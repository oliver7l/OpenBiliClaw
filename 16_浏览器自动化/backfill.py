#!/usr/bin/env python3
"""低密度回填：从阅读库批量补「缺正文」的文章（默认小红书）。

用法（项目 venv 下）：
    .venv/bin/python 16_浏览器自动化/backfill.py [--source xiaohongshu] [--n 40] [--pacing 7]

- 从 articles 里找 content_text 为空、缺正文的条目（默认 source=xiaohongshu），最旧…最新序，
  每条约 5-7 秒 + pacing 节流，逐一登录态抓正文入库；结果追加到 data/backfill_<source>.log。
- 内置「防假命中」守卫：token 失效被重定向回首页（标题命中站点签名）则拒收、不计成功。
- 低密度设计用于避免平台风控：默认 40 条/次，单次跑完 min(---:n, 当日剩余)。
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentlimb_eval import AgentLimbBrowser  # noqa: E402
from openbiliclaw.storage.database import Database  # noqa: E402
from web_capture import (  # noqa: E402
    DEFAULT_DB,
    FAKE_SIGNATURES,
    _grab,
    _ingest,
    _sniff,
)


def _pending_urls(db, source: str, n: int) -> list[str]:
    where = "content_text IS NULL OR length(content_text)=0"
    if source == "xiaohongshu":
        # 与 二创/xhs_refill/agentlimb_xhs_batch.py(hourly)错开：hourly 只抓
        # 无 xsec_token 且标题非空的裸 id 笔记(走搜索点击),Backfill 只补它
        # 覆盖不到的：带 xsec_token(直接访问)或标题为空(搜索用不了)。
        where += (
            " AND url LIKE '%xiaohongshu.com/explore/%'"
            " AND (url LIKE '%xsec_token=%' OR COALESCE(title,'')='')"
        )
    rows = db.conn.execute(
        f"SELECT url FROM articles WHERE source_type=? AND ({where}) ORDER BY RANDOM() LIMIT ?",
        (source, n),
    ).fetchall()
    return [r["url"] for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser(description="低密度回填缺正文文章")
    ap.add_argument("--source", default="xiaohongshu")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--pacing", type=float, default=7.0, help="条间节流秒数（防风控）")
    ap.add_argument("--jitter", type=float, default=0.0, help="首段随机憩志上限(分钟)，随机化触发时刻")
    args = ap.parse_args()

    db = Database(str(DEFAULT_DB))
    db.initialize()
    urls = _pending_urls(db, args.source, args.n)
    if not urls:
        print(f"[none] source={args.source} 无缺正文条目")
        return 0

    if args.jitter > 0:
        d = random.uniform(0, args.jitter) * 60
        print(f"[jitter] sleep {d:.0f}s (<= {args.jitter}min) 随机化触发点")
        time.sleep(d)

    log = _REPO / "data" / f"backfill_{args.source}.log"
    b = AgentLimbBrowser()
    b.start()

    ok = fake = err = 0
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"== {stamp} backfill source={args.source} n={len(urls)} =="]
    for i, url in enumerate(urls, 1):
        try:
            a = _grab(b, url)
            st, sn = _sniff(url)
            title = (a.get("title") or "").strip()
            bad = any(bad_sig and bad_sig in title for bad_sig in FAKE_SIGNATURES.get(st, []))
            if bad:
                fake += 1
                lines.append(f"[FAKE] {url}  (标题='{title[:36]}')")
            else:
                n = _ingest(db, url, a, st, sn)
                ok += 1
                lines.append(f"[OK]   {url}  body={n}字")
            print(f"[{i}/{len(urls)}] {'FAKE-skip' if bad else 'OK'} {url[:70]}", flush=True)
        except Exception as e:  # noqa: BLE001
            err += 1
            lines.append(f"[ERR]  {url}  {e}")
            print(f"[{i}/{len(urls)}] ERR {url[:70]}  {e}", flush=True)
        time.sleep(args.pacing)

    summary = f"== done ok={ok} fake={fake} err={err} => {log} =="
    print(summary)
    lines.append(summary)
    with Path(log).open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return 1 if (fake + err) == len(urls) else 0


if __name__ == "__main__":
    raise SystemExit(main())