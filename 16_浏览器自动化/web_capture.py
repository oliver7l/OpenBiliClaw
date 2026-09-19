#!/usr/bin/env python3
"""用 AgentLimb（真实登录态 Chrome）抓任意网页正文并写入 OpenBiliClaw 阅读库。

适用：任何需要登录态/被反爬(如 Cloudflare)挡住的平台——给 URL 即可，
登录态 Chrome 里抓标题 + 可读正文，按域名自动判 source_type/source_name 入库。

为什么不直接 curl：很多平台全程人机验证登录墙（Linux.do/小红书/抖音等），
后端直连会被 403。这条 AgentLimb 桥在你已登录的真实 Chrome 里执行，Cookie/会话全复用。

前置（同 16_浏览器自动化/README.md）：Chrome 已启动 + AgentLimb 面板开着 + Bridge 7791 在线。

用法（项目 venv 下，一次可给多个 URL）：
    .venv/bin/python 16_浏览器自动化/web_capture.py <url> [<url> ...]
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for agentlimb_eval

from agentlimb_eval import AgentLimbBrowser  # noqa: E402
from openbiliclaw.storage.database import Database  # noqa: E402

DEFAULT_DB = _REPO / "data" / "openbiliclaw.db"

# 域名后缀 → (source_type, source_name)。默认 ("web", host)。
SUFFIX: dict[str, tuple[str, str]] = {
    "linux.do": ("linuxdo", "Linux.do"),
    "zhihu.com": ("zhihu", "知乎"),
    "xiaohongshu.com": ("xiaohongshu", "小红书"),
    "xhslink.com": ("xiaohongshu", "小红书"),
    "douyin.com": ("douyin", "抖音"),
    "youtube.com": ("youtube", "YouTube"),
    "youtu.be": ("youtube", "YouTube"),
    "bilibili.com": ("bilibili", "B站"),
    "b23.tv": ("bilibili", "B站"),
    "v2ex.com": ("v2ex", "V2EX"),
    "douban.com": ("douban", "豆瓣"),
    "mp.weixin.qq.com": ("wechat", "微信公众号"),
    "weread.qq.com": ("wechat", "微信读书"),
    "xiaoyuzhoufm.com": ("xiaoyuzhou", "小宇宙"),
    "reddit.com": ("reddit", "Reddit"),
    "getpocket.com": ("web", "Pocket"),
}


def _sniff(url: str) -> tuple[str, str]:
    host = (urlparse(url).netloc or "").lower()
    for suf, pair in SUFFIX.items():
        if host == suf or host.endswith("." + suf):
            return pair
    return ("web", host or "Web")


# token 失效/未登录时会被重定向到站点首页，抓到的是噪音而非正文 → 命中即拒收。
# 值可以是子串（标题含即判失效）。空列表 = 该源不设守卫。
FAKE_SIGNATURES: dict[str, list[str]] = {
    "xiaohongshu": ["小红书 - 你的生活兴趣社区", "小红书 - 送你一个年度好物集合"],
    "douyin": ["分享视频"],
    "zhihu": [],
    "linux.do": [],
}


# 复杂 JS 用单行 then 链（README 坑#2）。抓标题+可读正文+作者+发布时间+关键词。
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


def _grab(b: AgentLimbBrowser, url: str) -> dict:
    b.navigate(url)
    time.sleep(3)
    r = b.eval_js(_READ_JS)
    val = r if isinstance(r, dict) else {"raw": str(r)[:500]}
    if isinstance(val, dict) and "value" in val:
        val = val["value"]
    data = json.loads(json.loads(val)) if isinstance(val, str) else val
    if data.get("error"):
        raise RuntimeError(f"{url}: {data['error']}")
    return data


def _ingest(db: Database, url: str, a: dict, source_type: str, source_name: str) -> int:
    txt = a.get("text") or ""
    tags = [t for t in (a.get("tags") or []) if t] + [source_name if source_type != "web" else source_type]
    tags = list(dict.fromkeys(tags[:5]))
    author = (a.get("author") or "").strip()
    body = txt or (a.get("title") or url)
    db.upsert_article(
        source_type=source_type,
        source_name=source_name,
        title=a.get("title") or url,
        url=url,
        author=author,
        summary=txt[:200],
        content_text=body,
        published_at=a.get("pub") or "",
        tags=tags or [source_type],
    )
    # upsert_article 命中已存在行时不更新 author、tags 只在空时覆盖 → 手动补写
    if author or tags:
        db._content.execute(
            "UPDATE articles SET author=?, tags=?, "
            "updated_at=datetime('now','localtime') WHERE url=?",
            (author, json.dumps(tags or [source_type], ensure_ascii=False), url),
        )
    return len(body)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    db = Database(str(DEFAULT_DB))
    db.initialize()
    b = AgentLimbBrowser()
    b.start()

    fail = 0
    for url in [u.strip() for u in sys.argv[1:] if u.strip()]:
        try:
            if not url.startswith("http"):
                url = "https://" + url
            a = _grab(b, url)
            st, sn = _sniff(url)
            title = (a.get("title") or "").strip()
            # 防假命中：失效 token 重定向到首页，标题命中站点签名 → 拒收
            for bad in FAKE_SIGNATURES.get(st, []):
                if bad and bad in title:
                    raise RuntimeError(f"{st}: 疑似失效/重定向页(标题='{title[:36]}')，跳过")
            if title.endswith(f" - {sn}"):
                title = title[: -len(f" - {sn}")].rstrip()
                a["title"] = title
            n = _ingest(db, url, a, st, sn)
            print(f"[OK]   {url}\n       → {st}/{sn} | {title[:40]} @{a.get('author') or '-'} | body={n}字")
        except Exception as e:  # noqa: BLE001
            fail += 1
            print(f"[FAIL] {url}: {e}", file=sys.stderr)
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())