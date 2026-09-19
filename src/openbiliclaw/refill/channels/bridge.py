"""AgentLimb 桥接客户端（HTTP 直连）。

内核镜像 ``二创/xhs_refill/agentlimb_xhs_batch.py``（raw urllib → Bridge
``http://127.0.0.1:7791/api/mvp/browser/call``），比 ``web_capture.py`` 的 Node
CLI 启动更快、不依赖侧边栏握手。provides：

- ``probe()``：探测桥接是否可达（排水口卸载，Startup 跳过整轮）。
- ``grab(url)``：登录态 Chrome 直接访问并抽取标题/正文/作者/发布时间/关键词
  （镜像 ``web_capture.py::_grab/_READ_JS``）。
- ``xhs_search_click(nid, title)``：小红书标题搜索 → CDP 真实点击 → 读
  ``noteDetailMap`` 取正文（镜像 ``agentlimb_xhs_batch.py::fetch_one``）。

基础设施故障（桥接 403 / 连接拒绝）统一抛 :class:`BridgeUnavailableError`。
本模块独立于 ``16_浏览器自动化/cli/后端``，仅剩 HTTP 这一个依赖。
"""

from __future__ import annotations

import json
import random
import time
import urllib.parse
import urllib.request

from openbiliclaw.refill.channels.base import BridgeUnavailableError

BRIDGE = "http://127.0.0.1:7791"

# 关键词最多截取长度（搜索用）。
_MAX_KEYWORD_CHARS = 20


def _patch_no_proxy() -> None:
    # 本机系统代理（127.0.0.1:58753）会劫持 localhost → 强制 urllib 全程不走代理。
    urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))


_patch_no_proxy()


def _call(tool: str, params: dict, timeout: float = 15) -> str:
    body = json.dumps({"tool": tool, "params": params}, ensure_ascii=False).encode()
    request = urllib.request.Request(
        BRIDGE + "/api/mvp/browser/call",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        raw = urllib.request.urlopen(request, timeout=timeout).read()
    except OSError as exc:  # 连接失败 / 超时 → 基础设施问题
        raise BridgeUnavailableError(f"AgentLimb 桥接不可用: {exc}") from exc
    try:
        return json.loads(raw)["call"]["id"]
    except (KeyError, ValueError) as exc:
        raise BridgeUnavailableError(f"AgentLimb 桥接响应异常: {raw[:120]!r}") from exc


def _wait(cid: str, timeout: float = 30) -> tuple[str, object]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = urllib.request.urlopen(BRIDGE + f"/api/mvp/browser/calls/{cid}", timeout=10).read()
            data = json.loads(raw)
            status = data["call"].get("status")
            if status == "completed":
                try:
                    return ("OK", data["call"]["result"]["result"]["value"])
                except Exception:  # noqa: BLE001
                    return ("OK", data["call"].get("result"))
            if status == "failed":
                return ("FAIL", data["call"].get("result"))
        except OSError as exc:
            return ("ERR", str(exc))
        except (KeyError, ValueError):
            time.sleep(0.6)
            continue
        time.sleep(0.6)
    return ("TIMEOUT", None)


def _evaljs(expression: str, timeout: float = 30) -> tuple[str, object]:
    return _wait(_call("javascript_eval", {"expression": expression}), timeout)


class AgentLimbBridge:
    """AgentLimb 登录态浏览器的薄封装（probe / grab / xhs 搜索点击）。"""

    def __init__(self, bridge: str = BRIDGE) -> None:
        self.bridge = bridge.rstrip("/")

    # -- 基础设施 ----------------------------------------------------------
    def probe(self) -> bool:
        """探测桥接可用性。不可用返回 False（不抛错，Startup 据此整轮跳过）。"""
        try:
            _call("tabs_context", {})
            return True
        except BridgeUnavailableError:
            return False

    # -- 直接访问（web_capture 内核） ---------------------------------------
    def grab(self, url: str) -> dict:
        """导航并抽取正文。镜像 ``web_capture.py::_grab/_READ_JS``。"""
        self._navigate(url)
        time.sleep(3)
        status, val = _evaljs(_READ_JS, 30)
        if status != "OK":
            raise BridgeUnavailableError(f"grab 执行失败({status})")
        data = val
        if isinstance(data, str):
            try:
                data = json.loads(json.loads(data)) if data.startswith('"') else json.loads(data)
            except ValueError as exc:
                raise BridgeUnavailableError(f"grab 解析失败: {str(data)[:160]!r}") from exc
        if not isinstance(data, dict):
            return {"title": "", "author": "", "pub": "", "text": "", "tags": [], "url": url}
        if data.get("error"):
            raise BridgeUnavailableError(f"grab 页内错误: {data['error']}")
        return data

    def _navigate(self, url: str) -> None:
        status, _ = _wait(
            _call("navigate", {"url": url, "waitForLoad": True, "timeout": 20000}), 30
        )
        if status == "ERR":
            raise BridgeUnavailableError(f"navigate 连接失败({url})")

    # -- 小红书标题搜索点击（agentlimb_xhs_batch 内核） ----------------------
    def xhs_search_click(self, nid: str, title: str) -> tuple[str, str]:
        """搜索→点击→读 noteDetailMap。返回 ``(status, body)``。

        status: ``OK`` 抓到正文；``NOT_FOUND`` 搜不到/404（真缺内容，可计数）；
        ``ERROR`` 桥接/抓取异常 → 抛 :class:`BridgeUnavailableError`。
        """
        keyword = urllib.parse.quote((title or "")[:_MAX_KEYWORD_CHARS])
        status, _ = _wait(
            _call(
                "navigate",
                {"url": f"https://www.xiaohongshu.com/search_result?keyword={keyword}",
                 "waitForLoad": True, "timeout": 20000},
            ),
            30,
        )
        if status == "ERR":
            raise BridgeUnavailableError("xhs 导航连接失败")
        time.sleep(random.uniform(6, 11))

        # 必须先观察再写（bridge 强制）。
        status, _ = _wait(_call("page_snapshot", {}), 30)

        selector = f"section.note-item:has(a[href*='{nid}'])"
        status, _ = _wait(_call("computer", {"type": "click", "selector": selector}, timeout=20), 30)
        if status != "OK":
            return "NOT_FOUND", ""
        time.sleep(random.uniform(6, 11))

        # 用 replace 注入 nid，避免 .format() 与 JS 字面大括号冲突。
        read_js = _XHS_READ_JS.replace("__NID__", nid)
        status, res = _evaljs(read_js, 30)
        if status != "OK" or not res:
            raise BridgeUnavailableError("xhs 读正文执行失败")
        try:
            d = json.loads(res)
        except ValueError:
            return "ERROR", ""
        if d.get("err"):
            return "ERROR", ""
        if not d.get("hit") or not d.get("desc"):
            return "NOT_FOUND", ""
        return "OK", (d.get("title", "") + "\n\n" + d["desc"]).strip()[:20000]


# 《web_capture.py::_READ_JS》：抓标题 + 可读正文 + 作者 + 发布时间 + 关键词。
_READ_JS = (
    "(async()=>{"
    "const q=s=>document.querySelector(s);"
    "const g=(s,a)=>{const e=q(s);return e?(e.getAttribute&&e.getAttribute(a))||(e.content)||'':'';};"
    "const node=['article','main','[role=main]','[itemprop=articleBody]',"
    "'.article-content','.post-content','#content','[class*=content]','.RichText']"
    ".map(q).find(el=>el&&(el.innerText||'').trim().length>150)||null;"
    "const raw=node?node.innerText:(document.body?document.body.innerText:'');"
    "const text=(raw||'').replace(/\\s+/g,' ').trim();"
    "const title=(q('meta[property=\"og:title\"]')&&q('meta[property=\"og:title\"]').content)||document.title||'';"
    "const author=(g('meta[property=\"og:article:author\"]','content')||"
    "g('meta[name=\"author\"]','content')||g('meta[name=\"og:author\"]','content')||'').trim();"
    "const pub=(g('meta[property=\"article:published_time\"]','content')||"
    "g('meta[name=\"publishdate\"]','content')||'').trim();"
    "const kw=((g('meta[name=\"keywords\"]','content')||'').split(/[,，]/).map(s=>s.trim())"
    ".filter(Boolean).slice(0,3));"
    "return JSON.stringify({title,author,pub,text,tags:kw,url:location.href});})()"
)

# 《agentlimb_xhs_batch.py::fetch_one》的读正文 JS。
_XHS_READ_JS = """(() => {
  try {
    const s = window.__INITIAL_STATE__;
    const ndm = (s && s.note) ? (s.note.noteDetailMap || {}) : {};
    const target = '__NID__';
    const n = ndm[target] && ndm[target].note;
    if (!n) return JSON.stringify({hit: false, loc: location.href.slice(0, 90)});
    let desc = n.desc || '';
    const imgs = (n.imageList || []).map(x => x.title || '').filter(Boolean).join('\\n');
    if (imgs) desc += '\\n' + imgs;
    return JSON.stringify({hit: true, title: (n.title || '').slice(0, 80),
                           descLen: desc.length, desc: desc});
  } catch (e) { return JSON.stringify({err: String(e).slice(0, 80)}); }
})()"""