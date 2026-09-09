#!/usr/bin/env python3
"""阅读库批量 AI 摘要生成（调 /api/articles/{id}/summarize，幂等）。

扫描 articles 中「有正文(>200字)且无 ai_summary」的行，逐个调 summarize
端点生成摘要并落库。端点内部已处理：LLM JSON 模式（sensenova 推理模型
max_tokens=3000）、缓存检查（已有 ai_summary 直接返回 cached）。

用法:
    python3 scripts/batch_ai_summarize.py            # dry-run（默认）
    python3 scripts/batch_ai_summarize.py --run      # 实际执行
    python3 scripts/batch_ai_summarize.py --run --limit 50
    python3 scripts/batch_ai_summarize.py --run --start-id 30000
    python3 scripts/batch_ai_summarize.py --run --source=zhihu
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "openbiliclaw.db"
API = "http://127.0.0.1:8420/api"


def _candidates(source: str | None, min_id: int) -> list[tuple[int, str, str]]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    # v0.4.0+: articles 表迁移到 content.db，ATTACH 以便跨库查询
    try:
        from pathlib import Path as _Path
        _content_db = _Path(__file__).parent.parent / "data" / "content.db"
        if _content_db.exists():
            con.execute("ATTACH DATABASE ? AS content", (str(_content_db),))
    except Exception:
        pass

    sql = """SELECT id, source_type, title FROM articles
             WHERE LENGTH(COALESCE(content_text,'')) > 200
               AND COALESCE(ai_summary,'') = ''
               AND id >= ?
               AND url IS NOT NULL AND url <> ''"""
    params: list = [min_id]
    if source:
        sql += " AND source_type = ?"
        params.append(source)
    sql += " ORDER BY id LIMIT 100000"
    rows = con.execute(sql, params).fetchall()
    con.close()
    return rows


def _summarize(article_id: int, retries: int = 1) -> tuple[bool, str]:
    """调 summarize 端点，502/网络类失败重试 ``retries`` 次。"""
    req = urllib.request.Request(
        f"{API}/articles/{article_id}/summarize",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    last: str = "unknown"
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                body = json.loads(resp.read() or b"{}")
            if body.get("ok"):
                return True, "cached" if body.get("cached") else "generated"
            last = str(body.get("error") or "unknown")
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
            if exc.code != 502 or attempt >= retries:
                break
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
            if attempt >= retries:
                break
        time.sleep(3)
    return False, last


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", action="store_true", help="实际执行（默认仅 dry-run）")
    ap.add_argument("--limit", type=int, default=0, help="最多处理 N 篇（0=全部）")
    ap.add_argument("--start-id", type=int, default=0, help="从 id >= N 开始")
    ap.add_argument("--source", default="", help="仅处理指定平台")
    ap.add_argument("--sleep", type=float, default=0.5, help="每篇间隔秒数")
    args = ap.parse_args()

    rows = _candidates(args.source or None, args.start_id)
    if args.limit > 0:
        rows = rows[: args.limit]
    print(f"候选 {len(rows)} 篇（{'dry-run，加 --run 执行' if not args.run else '执行中'}）"
          + (f"，平台={args.source}" if args.source else ""), flush=True)
    if not args.run:
        by_src: dict[str, int] = {}
        for _, src, _ in rows:
            by_src[src] = by_src.get(src, 0) + 1
        for src, n in sorted(by_src.items(), key=lambda kv: -kv[1])[:10]:
            print(f"  {src:<14} {n:>6,}")
        return 0

    ok = fail = 0
    by_result: dict[str, int] = {}
    t0 = time.time()
    for idx, (aid, src, title) in enumerate(rows, 1):
        success, detail = _summarize(aid)
        by_result[detail] = by_result.get(detail, 0) + 1
        if success:
            ok += 1
        else:
            fail += 1
        if idx % 10 == 0 or not success:
            elapsed = time.time() - t0
            print(
                f"[{idx}/{len(rows)}] {'OK' if success else 'FAIL'} id={aid} "
                f"{src} {detail} | 累计 ok={ok} fail={fail} "
                f"({elapsed:.0f}s, {(idx/elapsed) if elapsed else 0:.2f}/s)",
                flush=True,
            )
        time.sleep(args.sleep)
    print(f"DONE total={len(rows)} ok={ok} fail={fail} results={by_result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
