"""Xiaoyuzhou FM (小宇宙) recommendation feed scheduler.

Calls the ``xyz`` CLI to fetch subscribed podcasts and their latest episodes
every 24 hours, and inserts them into ``content_cache``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
import time
from datetime import datetime

logger = logging.getLogger(__name__)

DB_PATH = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliclaw/data/openbiliclaw.db"
INTERVAL_HOURS = 24
# 白名单而非 os.environ.copy()：这里刻意不继承 HTTP(S)_PROXY 等代理变量——
# 本机代理（Clash 类）挂掉时子进程走代理会静默失败。请勿改成 os.environ.copy()。
CLEAN_ENV = {k: v for k, v in os.environ.items() if k in ("HOME", "PATH", "USER", "SHELL", "TMPDIR")}
CLEAN_ENV["PYTHONHOME"] = ""
CLEAN_ENV["PYTHONPATH"] = ""

XYZ_CMD = ["xyz", "subs", "--jsonl"]
EP_CMD_TEMPLATE = ["xyz", "episodes", "{}", "--limit", "3", "--jsonl", "--fields", "eid,pid,podcast_title,title,duration_seconds,pub_date,audio_url,image_url"]


def _run_cmd(cmd: list[str]) -> list[dict]:
    """Run a CLI command and return parsed JSON objects."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, env=CLEAN_ENV, timeout=60)
    except subprocess.TimeoutExpired:
        logger.error("command timed out: %s", cmd[:2])
        return []
    except FileNotFoundError:
        logger.error("command not found: %s", cmd[0])
        return []
    if result.returncode != 0:
        logger.error("command failed (rc=%d): %s", result.returncode, result.stderr[:500])
        return []
    items: list[dict] = []
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return items


def _fetch_subscriptions() -> list[dict]:
    """Get all subscribed podcasts."""
    return _run_cmd(XYZ_CMD)


def _fetch_episodes(pid: str) -> list[dict]:
    """Get latest episodes for a podcast."""
    cmd = [a.format(pid) if "{}" in a else a for a in EP_CMD_TEMPLATE]
    return _run_cmd(cmd)


def _strip_html(text: str) -> str:
    """Remove HTML tags from shownotes."""
    return re.sub(r"<[^>]+>", "", text).strip()


def _parse_items(subscriptions: list[dict], episodes_by_pid: dict[str, list[dict]]) -> list[dict]:
    """Build content_cache-compatible rows from subscriptions + episodes."""
    now = datetime.now()
    rows: list[dict] = []
    seen: set[str] = set()

    # Build a podcast title map for fallback
    podcast_titles: dict[str, str] = {}
    for sub in subscriptions:
        pid = sub.get("pid", "")
        if pid:
            podcast_titles[pid] = sub.get("title", "") or ""

    for pid, eps in episodes_by_pid.items():
        for ep in eps:
            eid = ep.get("eid", "").strip()
            if not eid or eid in seen:
                continue
            seen.add(eid)

            title = ep.get("title", "") or ""
            if not title:
                continue

            podcast_title = ep.get("podcast_title", "") or podcast_titles.get(pid, "")
            content_url = f"https://www.xiaoyuzhoufm.com/episode/{eid}"
            duration = int(ep.get("duration_seconds", 0) or 0)
            image_url = ep.get("image_url", "") or ""
            audio_url = ep.get("audio_url", "") or ""

            rows.append({
                "bvid": eid,
                "title": title,
                "up_name": podcast_title,
                "author_name": podcast_title,
                "content_url": content_url,
                "source_platform": "xiaoyuzhou",
                "source": "xiaoyuzhou-feed",
                "content_type": "podcast",
                "pool_status": "fresh",
                "like_count": duration,
                "body_text": "",
                "topic_group": "",
                "discovered_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            })
    return rows


def _insert_rows(conn: sqlite3.Connection, rows: list[dict]) -> int:
    """Insert new rows, skip duplicates by bvid."""
    inserted = 0
    for row in rows:
        try:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO content_cache (
                    bvid, title, up_name, author_name, content_url,
                    source_platform, source, content_type, pool_status,
                    like_count, body_text, topic_group, discovered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["bvid"], row["title"], row["up_name"], row["author_name"],
                    row["content_url"], row["source_platform"], row["source"],
                    row["content_type"], row["pool_status"], row["like_count"],
                    row["body_text"], row["topic_group"], row["discovered_at"],
                ),
            )
            if cursor.rowcount > 0:
                inserted += 1
        except sqlite3.IntegrityError:
            continue
    return inserted


def _run_once() -> dict:
    """One full fetch cycle. Returns a summary dict."""
    subscriptions = _fetch_subscriptions()
    if not subscriptions:
        return {"ok": False, "reason": "no_subscriptions", "items_fetched": 0, "inserted": 0}

    # Fetch episodes for each subscription
    episodes_by_pid: dict[str, list[dict]] = {}
    for sub in subscriptions:
        pid = sub.get("pid", "")
        if not pid:
            continue
        eps = _fetch_episodes(pid)
        if eps:
            episodes_by_pid[pid] = eps
        time.sleep(0.5)  # be gentle to the API

    all_episode_count = sum(len(eps) for eps in episodes_by_pid.values())
    if all_episode_count == 0:
        return {"ok": False, "reason": "no_episodes", "items_fetched": 0, "inserted": 0}

    rows = _parse_items(subscriptions, episodes_by_pid)
    if not rows:
        return {"ok": False, "reason": "no_valid_items", "items_fetched": all_episode_count, "inserted": 0}

    conn = sqlite3.connect(DB_PATH)
    try:
        inserted = _insert_rows(conn, rows)
        conn.commit()
        return {
            "ok": True,
            "subscriptions": len(subscriptions),
            "episodes_fetched": all_episode_count,
            "valid_items": len(rows),
            "inserted": inserted,
            "skipped_duplicates": len(rows) - inserted,
        }
    finally:
        conn.close()


def run_forever() -> None:
    """Main loop: fetch every 24 hours."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logger.info("xiaoyuzhou feed producer started (interval=%dh)", INTERVAL_HOURS)

    while True:
        logger.info("fetching xiaoyuzhou episodes...")
        result = _run_once()
        if result["ok"]:
            logger.info(
                "feed ok: %d subscriptions, %d episodes, %d new, %d duplicate",
                result["subscriptions"],
                result["episodes_fetched"],
                result["inserted"],
                result["skipped_duplicates"],
            )
        else:
            logger.warning("feed skipped: %s", result.get("reason", "unknown"))

        time.sleep(INTERVAL_HOURS * 3600)


if __name__ == "__main__":
    run_forever()