#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backfill xsec_token for bare Xiaohongshu URLs in content_cache.

Safe, resumable, low-frequency runner that calls ``xhs search`` to obtain a
fresh tokenized URL for rows whose ``content_url`` lacks ``xsec_token=``.
Respects platform rate limits through fixed delays and exponential backoff.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "data" / "content.db"
STATE_PATH = PROJECT_ROOT / "data" / ".xhs_token_backfill_state.json"
DONE_MARKER = PROJECT_ROOT / "data" / ".xhs_token_backfill_done"
def _resolve_xhs_bin() -> str:
    env = os.environ.get("XHS_BIN", "").strip()
    if env:
        return env
    candidates = [
        Path.home() / ".local" / "bin" / "xhs",
        Path("/opt/homebrew/bin/xhs"),
        Path("/usr/local/bin/xhs"),
        Path("xhs"),
    ]
    for c in candidates:
        if str(c) == "xhs" or c.exists():
            return str(c)
    return "xhs"


XHS_BIN = _resolve_xhs_bin()
NOTE_ID_RE = re.compile(r"^[0-9a-f]{24}$")


def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"done": [], "failed": [], "skipped": []}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _norm_bvid(bvid: str) -> str:
    bvid = str(bvid or "").strip()
    if bvid.startswith("xhs_"):
        return bvid[4:]
    return bvid


def _extract_note_id(url: str) -> str | None:
    try:
        path = urlparse(str(url or "")).path.strip("/")
        if path.startswith("explore/"):
            note_id = path.split("/", 1)[1].split("?", 1)[0]
            if NOTE_ID_RE.match(note_id):
                return note_id
    except Exception:
        pass
    return None


def _search_xhs(title: str, delay: float) -> dict | None:
    """Run ``xhs search`` and return parsed JSON, or None on failure."""
    if not title:
        return None
    # Be gentle with the platform: sleep before each search call.
    if delay > 0:
        time.sleep(delay)
    cmd = [XHS_BIN, "search", title, "--json"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "PYTHONHOME": "", "PYTHONPATH": ""},
        )
    except subprocess.TimeoutExpired:
        return {"_error": "timeout"}
    except Exception as exc:
        return {"_error": f"subprocess:{exc}"}

    if result.returncode != 0:
        err = result.stderr.strip()[:500] if result.stderr else "unknown"
        return {"_error": f"rc={result.returncode}: {err}"}

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {"_error": f"json:{exc}"}


def _find_token(data: dict | None, target_note_id: str) -> str | None:
    if not isinstance(data, dict):
        return None
    items = data.get("data", {}).get("items", []) if isinstance(data.get("data"), dict) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        note_id = str(item.get("id", "")).strip()
        if note_id != target_note_id:
            continue
        token = str(item.get("xsec_token", "")).strip()
        if token:
            return token
    return None


def _get_bare_rows(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    cursor = conn.execute(
        """
        SELECT id AS bvid, id AS content_id, title, url AS content_url
        FROM articles
        WHERE source_type = 'xiaohongshu'
          AND url LIKE '%xiaohongshu.com%'
          AND url NOT LIKE '%xsec_token=%'
          AND (content_text IS NULL OR content_text = '')
          AND body_fetch_attempts < 3
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    return cursor.fetchall()


def _update_row(conn: sqlite3.Connection, bvid: str, new_url: str) -> int:
    cursor = conn.execute(
        "UPDATE articles SET url = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND url NOT LIKE '%xsec_token=%'",
        (new_url, bvid),
    )
    conn.commit()
    return cursor.rowcount or 0


def _process_one(conn: sqlite3.Connection, row: sqlite3.Row, delay: float, max_retries: int) -> tuple[str, str | None]:
    """Process one row. Returns (status, detail). status in done/failed/skipped."""
    bvid = str(row["bvid"] or "")
    url = str(row["content_url"] or "")
    title = str(row["title"] or "").strip()
    note_id = _extract_note_id(url) or _norm_bvid(str(row["content_id"] or bvid))

    if not NOTE_ID_RE.match(note_id):
        return "skipped", f"invalid note_id: {note_id}"

    if not title:
        return "skipped", "empty title"

    for attempt in range(max_retries):
        data = _search_xhs(title, delay if attempt == 0 else 0)
        error = data.get("_error") if isinstance(data, dict) else "bad response"
        if error:
            # HARD STOP signal: a captcha/risk-control challenge means the
            # account is being flagged. Per project rule "绝不触发平台限流",
            # we must NOT keep hammering — abort the whole run and let the
            # caller wait (manually, for hours) before resuming.
            if "captcha" in error.lower() or "risk" in error.lower():
                return "captcha", error
            if "timeout" in error.lower() or "429" in error or "rate" in error.lower():
                backoff = min(2 ** attempt * 5, 60)
                time.sleep(backoff)
                continue
            return "failed", error

        token = _find_token(data, note_id)
        if token:
            new_url = f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token={token}&xsec_source=pc_feed"
            updated = _update_row(conn, bvid, new_url)
            return ("done", new_url) if updated else ("skipped", "no row updated")

        # Token not found in first page of search results; try a shorter title once.
        if attempt == 0 and len(title) > 12:
            short_title = title[:12].strip()
            if short_title and short_title != title:
                data = _search_xhs(short_title, delay)
                token = _find_token(data, note_id)
                if token:
                    new_url = f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token={token}&xsec_source=pc_feed"
                    updated = _update_row(conn, bvid, new_url)
                    return ("done", new_url) if updated else ("skipped", "no row updated")

        return "skipped", "token not found in search results"

    return "failed", "max retries exceeded"


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill xsec_token for bare xhs URLs")
    parser.add_argument("--limit", type=int, default=0, help="Max rows to process (0 = all)")
    parser.add_argument("--rows-per-run", type=int, default=1,
                        help="Max rows to actively process per invocation (default 1). "
                             "Pair with a scheduler (e.g. every 2h) for ultra-low-frequency backfill.")
    parser.add_argument("--delay", type=float, default=5.0, help="Seconds between xhs search calls")
    parser.add_argument("--max-retries", type=int, default=3, help="Retries per row")
    parser.add_argument("--dry-run", action="store_true", help="Do not update DB")
    parser.add_argument("--reset", action="store_true", help="Reset progress state")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"DB not found: {DB_PATH}", file=sys.stderr)
        return 1

    # If a prior run already finished all bare URLs, this is a no-op.
    # Exits immediately (no DB connection) so a still-scheduled recurring
    # task costs essentially nothing until it is auto-disabled.
    if DONE_MARKER.exists():
        print("ALREADY COMPLETE — all bare xhs URLs already have xsec_token. Nothing to do.")
        return 0

    state = _load_state()
    if args.reset:
        state = {"done": [], "failed": [], "skipped": []}
        _save_state(state)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = _get_bare_rows(conn, args.limit or 100000)
    print(f"bare xhs rows to process: {len(rows)}")

    done = set(state.get("done", []))
    failed = set(state.get("failed", []))
    skipped = set(state.get("skipped", []))

    counts = {"done": 0, "failed": 0, "skipped": 0, "captcha": 0}
    processed = 0
    try:
        for row in rows:
            bvid = str(row["bvid"] or "")
            if bvid in done or bvid in skipped:
                continue

            if args.dry_run:
                print(f"[dry-run] would process {bvid}: {row['title'][:40]}")
                continue

            status, detail = _process_one(conn, row, args.delay, args.max_retries)
            counts[status] += 1

            if status == "done":
                done.add(bvid)
                failed.discard(bvid)
                skipped.discard(bvid)
                print(f"[done] {bvid} -> {detail}")
            elif status == "captcha":
                # Account is being risk-controlled. Abort immediately so we
                # don't keep burning captcha challenges. Leave this row
                # unrecorded so a later (cooler) run can resume it.
                print(f"[CAPTCHA] {bvid}: {detail}")
                print(">>> Captcha triggered. Aborting run to avoid platform rate-limit ban.")
                print(">>> Resume later after a cooldown (the progress state is saved).")
                break
            elif status == "failed":
                failed.add(bvid)
                print(f"[failed] {bvid}: {detail}")
            else:
                skipped.add(bvid)
                print(f"[skipped] {bvid}: {detail}")

            state["done"] = sorted(done)
            state["failed"] = sorted(failed)
            state["skipped"] = sorted(skipped)
            _save_state(state)

            processed += 1
            if args.rows_per_run and processed >= args.rows_per_run:
                print(f"rows-per-run limit reached ({args.rows_per_run}); stopping. "
                      f"Resume next scheduled run.")
                break

        # Completion check (conn still open): if no bare URLs remain, write a
        # marker so future scheduled runs are free no-ops, and signal that this
        # task may disable itself.
        if not args.dry_run:
            try:
                remaining = conn.execute(
                    "SELECT COUNT(*) FROM articles "
                    "WHERE source_type='xiaohongshu' "
                    "  AND url LIKE '%xiaohongshu.com%' "
                    "  AND url NOT LIKE '%xsec_token=%' "
                    "  AND (content_text IS NULL OR content_text='') "
                    "  AND body_fetch_attempts < 3"
                ).fetchone()[0]
                if remaining == 0:
                    DONE_MARKER.write_text(
                        f"completed at {time.strftime('%Y-%m-%dT%H:%M:%S')}\n",
                        encoding="utf-8",
                    )
                    print("BACKFILL_COMPLETE — no bare xhs URLs remain. "
                          "This scheduled task can now disable itself.")
            except Exception:
                pass
    finally:
        conn.close()

    print(f"summary: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
