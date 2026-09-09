#!/usr/bin/env python3
"""异步补抓阅读库正文的独立后台任务。

扫描 articles 中 content_text 为空、url 非空、且重试次数未超限的行，
按来源分通道抓取正文，写回 content_text：

- **bilibili**：`bili video <BV> -s --ai --json`。优先取字幕口播稿
  （data.subtitle.text，实测约 60% 的视频有，长度 600~7500 字）；
  无字幕的用 AI 总结 + 视频简介拼接兜底。B站视频页 HTML 里没有口播稿，
  原来的 autocli(Readability) 通道对 B站几乎无效（历史覆盖率仅 4.8%）。
- 其他来源：沿用本机 `autocli read <url>`（Readability，依赖 Chrome 扩展）。

设计要点：
- content_text 为空即代表"待补"，相当于一个隐形队列；新进来的 RSS 文章若
  feed 未带正文，会被下一轮自动补上，不拖慢实时 RSS 调度。
- body_fetch_attempts 记录重试次数，>= MAX_ATTEMPTS 的行不再尝试，避免对
  永久抓不到的 URL 反复无效请求。B站若字幕/AI/简介三者皆无，视为永久缺失，
  直接记满次数跳过（不再浪费 3 次重试）。
- 绝不覆盖已有正文；写入截断到 20000 字符。
- 只 UPDATE content_text/body_fetch_attempts，不删除任何行。

用法:
    python3 scripts/refill_article_bodies.py [limit] [--source=bilibili]
limit 默认为 100（每轮最多补多少条）；--source 可只补指定来源。
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "data", "openbiliclaw.db")
AUTOCLI = "/Users/imac/bin/autocli"
BILI_CLI = "/Users/imac/.local/bin/bili"
MAX_ATTEMPTS = 3
TIMEOUT = 120
MIN_BODY = 50
BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")


def fetch_body(url: str) -> str:
    """Return article body markdown, or '' on failure (after retries)."""
    for _ in range(2):
        try:
            r = subprocess.run(
                [AUTOCLI, "read", url],
                capture_output=True, text=True, timeout=TIMEOUT,
            )
            out = (r.stdout or "").strip()
            if len(out) > 50:
                return out
        except Exception:
            pass
        time.sleep(2)
    return ""


def _bili_json(args: list[str]) -> dict:
    """Run bili CLI and return its `data` object ({} on any failure)."""
    try:
        r = subprocess.run(
            [BILI_CLI, *args, "--json"], capture_output=True, text=True, timeout=TIMEOUT
        )
        return json.loads(r.stdout or "{}").get("data") or {}
    except Exception:
        return {}


def fetch_bilibili_body(url: str) -> tuple[str, bool]:
    """B站正文：优先字幕口播稿，否则用 AI 总结 + 简介兜底。

    单次调用 `-s --ai --json` 即可同时拿到 subtitle / ai_summary / description。

    返回 ``(正文, 调用是否成功)``。区分两者很关键：
    - 调用成功但字幕/AI/简介皆无 → 该视频永久无正文，可记满次数跳过；
    - 调用本身失败（超时/网络/B站限流）→ **必须重试**，若误判为永久跳过，
      一次限流就会让几百条视频再也没机会补上。
    无 BV 号（url 不合法）视为永久无法处理。
    """
    m = BV_RE.search(url or "")
    if not m:
        return "", True
    d = _bili_json(["video", m.group(1), "-s", "--ai"])
    if not d:
        return "", False  # 调用失败 → 重试

    st = d.get("subtitle") or {}
    if st.get("available") and (st.get("text") or "").strip():
        return (st.get("text") or "").strip(), True

    ai = d.get("ai_summary") or ""
    desc = ((d.get("video") or {}).get("description") or "").strip()
    parts = [p.strip() for p in (ai, desc) if isinstance(p, str) and p.strip()]
    combined = "\n\n".join(parts)
    return (combined if len(combined) >= MIN_BODY else ""), True


def main() -> None:
    limit, source = 100, None
    for arg in sys.argv[1:]:
        if arg.startswith("--source="):
            source = arg.split("=", 1)[1].strip()
        elif arg.isdigit():
            limit = int(arg)

    db = sqlite3.connect(DB)
# v0.4.0+: articles 表迁移到 content.db，ATTACH 以便跨库查询
try:
    from pathlib import Path as _Path
    _content_db = _Path(__file__).parent.parent / "data" / "content.db"
    if _content_db.exists():
        db.execute("ATTACH DATABASE ? AS content", (str(_content_db),))
except Exception:
    pass

    db.execute("PRAGMA busy_timeout=15000")
    sql = """SELECT id, url, source_type FROM articles
           WHERE (content_text IS NULL OR content_text = '')
             AND url IS NOT NULL AND url <> ''
             AND COALESCE(body_fetch_attempts, 0) < ?"""
    params: list = [MAX_ATTEMPTS]
    if source:
        sql += " AND source_type = ?"
        params.append(source)
    sql += " ORDER BY id LIMIT ?"
    params.append(limit)
    rows = db.execute(sql, params).fetchall()

    ok = fail = skipped = 0
    for idx, (aid, url, src) in enumerate(rows, 1):
        is_bili = (src or "") == "bilibili"
        if is_bili:
            body, call_ok = fetch_bilibili_body(url)
        else:
            body, call_ok = fetch_body(url), True
        attempts = db.execute(
            "SELECT COALESCE(body_fetch_attempts, 0) FROM articles WHERE id = ?", (aid,)
        ).fetchone()[0] + 1
        if body:
            db.execute(
                "UPDATE articles SET content_text = ?, body_fetch_attempts = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (body[:20000], attempts, aid),
            )
            print(f"[{idx}/{len(rows)}] OK   id={aid} len={len(body)}", flush=True)
            ok += 1
        elif is_bili and call_ok:
            # 调用成功但确实无字幕/无AI/无简介 → 永久跳过，不再浪费重试
            db.execute(
                "UPDATE articles SET body_fetch_attempts = ? WHERE id = ?",
                (MAX_ATTEMPTS, aid),
            )
            print(f"[{idx}/{len(rows)}] SKIP id={aid} 无字幕/无AI/无简介", flush=True)
            skipped += 1
        elif is_bili:
            # 调用失败（超时/限流）→ 正常累加次数，下轮再试
            db.execute(
                "UPDATE articles SET body_fetch_attempts = ? WHERE id = ?",
                (attempts, aid),
            )
            print(f"[{idx}/{len(rows)}] RETRY id={aid} 调用失败，保留重试", flush=True)
            fail += 1
        else:
            db.execute(
                "UPDATE articles SET body_fetch_attempts = ? WHERE id = ?", (attempts, aid)
            )
            print(f"[{idx}/{len(rows)}] FAIL id={aid} {url}", flush=True)
            fail += 1
        db.commit()
        time.sleep(1)
    print(
        f"DONE total={len(rows)} ok={ok} fail={fail} skipped={skipped}"
        + (f" source={source}" if source else ""),
        flush=True,
    )


if __name__ == "__main__":
    main()
