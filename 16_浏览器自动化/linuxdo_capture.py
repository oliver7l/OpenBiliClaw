#!/usr/bin/env python3
"""用 AgentLimb（真实登录态 Chrome）抓取 Linux.do 帖子全文并写入阅读库。

为什么用这个：linux.do 全程在 Cloudflare 人机验证后面，后端直连 JSON 会被 403。
而 Linux.do 扩展通道又依赖"扩展在线 + 已构建"才能领任务。走这条 AgentLimb 桥，
直接在你已登录的真实 Chrome 里 fetch Discourse JSON，最稳、无额外依赖。

前置：Chrome 已启动、AgentLimb 侧边栏面板开着、Bridge 常驻 127.0.0.1:7791
      （离线时在 chrome://extensions 刷新 AgentLimb ⟳ + 重开侧边栏）。

用法（在项目 venv 下）：
    .venv/bin/python 16_浏览器自动化/linuxdo_capture.py <url|id> [<url|id> ...]

示例：
    .venv/bin/python 16_浏览器自动化/linuxdo_capture.py https://linux.do/t/topic/188854/11 2920473
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for agentlimb_eval

from agentlimb_eval import AgentLimbBrowser  # noqa: E402
from openbiliclaw.storage.database import Database  # noqa: E402

DEFAULT_DB = _REPO / "data" / "openbiliclaw.db"
ORIGIN = "https://linux.do"


def _grab(b: AgentLimbBrowser, topic_id: str) -> dict:
    """导航到帖子、fetch 首帖正文，返回 {title,author,tags,text}。"""
    b.navigate(f"{ORIGIN}/t/{topic_id}")
    time.sleep(3)
    js = (
        f"fetch('/t/{topic_id}.json').then(r=>r.json()).then(o=>{{"
        f"const p=(o.post_stream&&o.post_stream.posts&&o.post_stream.posts[0])||{{}};"
        f"const d=document.createElement('div');d.innerHTML=p.cooked||'';"
        f"const txt=(d.textContent||'').replace(/\\s+/g,' ').trim();"
        f"return JSON.stringify({{title:o.title,author:p.username,tags:o.tags||[],text:txt}});"
        f"}}).catch(e=>JSON.stringify({{error:String(e)}}))"
    )
    r = b.eval_js(js)
    val = r if isinstance(r, dict) else {"raw": str(r)[:500]}
    if isinstance(val, dict) and "value" in val:
        val = val["value"]
    data = json.loads(json.loads(val)) if isinstance(val, str) else val
    if data.get("error"):
        raise RuntimeError(f"topic {topic_id}: {data['error']}")
    return data


def _topic_id(url_or_id: str) -> str:
    s = (url_or_id or "").strip()
    if s.isdigit():
        return s
    # /t/topic/123  /t/slug/123  /t/topic/123/11
    import re

    m = re.search(r"/t/(?:[^/]+/)?(\d+)(?:/\d+)?$", s)
    if m:
        return m.group(1)
    raise ValueError(f"无法从 URL 解析 topic id: {url_or_id}")


def _ingest(db: Database, url: str, a: dict, extra_tags: list[str]) -> int:
    txt = (a.get("text") or "").strip()
    raw = a.get("tags") or []
    names = [t.get("name") for t in raw if isinstance(t, dict) and t.get("name")] if isinstance(raw, list) else []
    tags = list(dict.fromkeys(names[:3] + extra_tags))
    body = txt or a.get("title") or url
    db.upsert_article(
        source_type="linuxdo",
        source_name="Linux.do",
        title=a.get("title") or url,
        url=url,
        author=a.get("author") or "",
        summary=txt[:200],
        content_text=body,
        published_at="",
        tags=tags or ["linuxdo"],
    )
    # upsert 命中已存在行时不更新 author、tags 也只在空时覆盖，这里补上
    if a.get("author") or tags:
        db._content.execute(
            "UPDATE articles SET author=?, tags=?, "
            "updated_at=datetime('now','localtime') WHERE url=?",
            (a.get("author") or "", json.dumps(tags or ["linuxdo"], ensure_ascii=False), url),
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
    for arg in sys.argv[1:]:
        try:
            tid = _topic_id(arg)
            a = _grab(b, tid)
            # 给了 URL 就保留原始 URL（含楼层形如 .../188854/11）以便命中同一行；
            # 给了裸 id 就拼 canonical。
            url = arg.strip() if "/t/" in arg else f"{ORIGIN}/t/{tid}"
            n = _ingest(db, url, a, ["linuxdo"])
            print(f"[OK]   {url}\n       {a.get('title','')} @{a.get('author','-')} | tags={a.get('tags')} | body={n}字")
        except Exception as e:  # noqa: BLE001
            fail += 1
            print(f"[FAIL] {arg}: {e}", file=sys.stderr)
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())