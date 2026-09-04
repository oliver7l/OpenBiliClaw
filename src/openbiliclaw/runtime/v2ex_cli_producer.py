"""V2EX feed producer built on the user's ``v2ex`` CLI.

Why this exists
---------------
The legacy ``v2ex_feed_producer.py`` and the official ``v2ex`` CLI's
``topics latest/hot`` commands both hit V2EX API 2.0
(``https://www.v2ex.com/api/v2/...``). On this machine that host is
blocked at the network layer (direct connections time out; the local
Clash proxy returns 403), so those paths silently yield nothing. That
is why ``v2ex_rss_producer.py`` was added — it reads a public RSSHub
mirror (bestblogs.dev) instead and works.

This script reuses the user's *authenticated* ``v2ex`` CLI for
**discovery** (honouring "use the v2ex CLI") and then enriches each
new topic with its **full body** via V2EX's legacy public API
(``https://www.v2ex.com/api/topics/show.json?id=<id>`` — the exact
endpoint ``v2ex topic show`` uses internally). The legacy API is a
separate, long-stable path that returns the topic text, author, node
and reply count, so V2EX threads land in the reading library WITH
正文 (RSSHub only gives title/author/url).

Outputs
-------
  * ``content_cache`` (recommendation pool) — with ``body_text`` filled
    in when enrichment succeeds, else title-only like the RSSHub path.
  * ``articles`` (reading library) — full ``content_text``, so V2EX
    threads are searchable and readable, not just titles.

Robustness
----------
  * The CLI is invoked as a subprocess with a hard timeout. Any failure
    (network, 404, 403, auth) is logged loudly and that cycle is
    skipped — never silent (unlike the old producer on 0-item replies).
  * Enrichment is best-effort: if the legacy API is also unreachable,
    we still insert the title-only row so the pool is never empty.
  * Dedupe is by topic id (``bvid``) / url, so re-runs are safe.

Platform risk-control (IMPORTANT)
---------------------------------
  V2EX sits behind Cloudflare. Bursty automated traffic gets a 403 whose body
  is a "Just a moment..." interstitial. That is NOT an IP ban and NOT a bad
  token, and retrying through it makes things worse. The cycle therefore runs
  at a deliberately low frequency (24h) and, on any sign of a challenge,
  aborts the remaining requests and backs off (interval x3 = 72h). Do not
  tighten this without checking the platform's rate-limit policy.

Usage
-----
  python3 -m openbiliclaw.runtime.v2ex_cli_producer            # loop forever
  python3 -m openbiliclaw.runtime.v2ex_cli_producer --dry-run  # one-shot, no DB writes
  python3 -m openbiliclaw.runtime.v2ex_cli_producer --discover-only  # skip body fetch
  python3 -m openbiliclaw.runtime.v2ex_cli_producer --limit 20 --interval 24
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sqlite3
import subprocess
import time
import urllib.request
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

DB_PATH = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/data/openbiliclaw.db"
INTERVAL_HOURS = 24
# Backoff multiplier applied to the interval when a Cloudflare challenge is
# detected (i.e. the platform is rate-limiting / bot-screening us). We must
# NEVER retry through a challenge — back off hard instead.
RISK_BACKOFF_MULTIPLIER = 3
# V2EX API 2.0 /nodes/{node}/topics returns a FIXED 20 topics per page and
# has NO size param, so asking for more than 20 only ever yields 20. To get
# more you must paginate via `--page` (one extra API call per page — weigh
# against the platform rate-limit red line). Keep this <= 20.
# NOTE: the v2ex CLI has a bug where BOTH `--limit` and `--node` claim the
# short flag `-n` (Click warns "parameter -n used more than once" on every
# call). Always pass `--limit` / `--node` LONG forms; never `-n`.
DISCOVER_LIMIT = 20
CLI_TIMEOUT = 40  # seconds per `v2ex topics` subprocess call

# Locate the v2ex executable. Prefer PATH, fall back to the known pipx bin.
_V2EX_BIN = shutil.which("v2ex") or "/Users/imac/.local/bin/v2ex"

# Legacy public API used by `v2ex topic show` under the hood. No token needed.
_LEGACY_SHOW_URL = "https://www.v2ex.com/api/topics/show.json?id={tid}"

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# --- Platform risk-control (Cloudflare) detection ---------------------------
# V2EX sits behind Cloudflare. Sustained automated requests get a 403 whose
# body is a "Just a moment..." interstitial — NOT an IP ban and NOT a token
# problem. Retrying through it makes things worse, so we detect it and back
# off hard instead (user red line: never trigger platform rate-limit/风控).
_CF_MARKERS = (
    "just a moment",
    "cf-browser-verification",
    "cf_chl_opt",
    "challenge-platform",
    "enable javascript and cookies to continue",
)
# The CLI maps ANY 403 to this message. With a valid token configured, a 403
# from V2EX means the Cloudflare challenge, so treat it as risk control.
_CLI_FORBIDDEN_MARKERS = ("access forbidden", "insufficient permissions")


def _looks_like_challenge(payload: bytes | str) -> bool:
    """True if the payload is a Cloudflare interstitial rather than JSON."""
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8", "replace")
        except Exception:
            return False
    head = payload[:2000].lower()
    return any(m in head for m in _CF_MARKERS)


# Toggled by --dry-run; when True we fetch/parse but skip all DB writes.
_DRY_RUN = False


# --------------------------------------------------------------------------- #
# Discovery (shell out to the user's v2ex CLI)
# --------------------------------------------------------------------------- #
def _run_cli_topics(command: str, limit: int) -> tuple[str | None, bool]:
    """Run ``v2ex topics <command> --limit <limit>``.

    Returns ``(stdout_or_None, risk_control)``. ``risk_control`` is True when
    the failure looks like a Cloudflare challenge (403) rather than a plain
    outage — callers must back off instead of retrying.
    """
    try:
        proc = subprocess.run(
            [_V2EX_BIN, "topics", command, "--limit", str(limit)],
            capture_output=True,
            text=True,
            timeout=CLI_TIMEOUT,
        )
    except FileNotFoundError:
        logger.error("v2ex CLI not found at %s", _V2EX_BIN)
        return None, False
    except subprocess.TimeoutExpired:
        logger.error("v2ex topics %s timed out after %ds", command, CLI_TIMEOUT)
        return None, False

    if proc.returncode != 0:
        # The CLI prints its error on stdout ("Error fetching topics: ...")
        # as well as stderr, so inspect both when classifying the failure.
        blob = f"{proc.stdout or ''}\n{proc.stderr or ''}"
        lines = [
            ln
            for ln in blob.strip().splitlines()
            if "UserWarning" not in ln and "parser =" not in ln and "self.parse_args" not in ln
        ]
        detail = " | ".join(lines[-3:]) or "no output"
        lowered = blob.lower()
        if any(m in lowered for m in _CLI_FORBIDDEN_MARKERS):
            # V2EX behind Cloudflare: 403 == bot challenge, not a bad token.
            logger.warning(
                "v2ex topics %s: platform risk-control detected "
                "(Cloudflare challenge) - backing off, NOT retrying: %s",
                command,
                detail,
            )
            return None, True
        logger.error("v2ex topics %s failed (rc=%d): %s", command, proc.returncode, detail)
        return None, False
    return proc.stdout or "", False


def _parse_topics_table(text: str) -> list[dict[str, Any]]:
    """Parse a rich table from ``v2ex topics latest/hot`` into row dicts.

    Robust to the real-world output, which differs from the source's
    assumptions:
      * Titles WRAP across multiple physical lines (rich wraps long cells),
        so a logical row may span several ``│...│`` lines — only the first
        carries the ID; continuation lines have an empty ID column and the
        title fragment in the Title column.
      * The Author column is EMPTY for node-topic listings (V2EX API 2.0
        ``/nodes/{node}/topics`` omits the member field), so we must accept
        a blank author and rely on legacy-API enrichment later.
    """
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        # Data rows start with the single vertical box char; header uses
        # the double char (┃) and separators use ┏┳┓ etc., so they're skipped.
        if not line.lstrip().startswith("│"):
            continue
        cells = [c.strip() for c in line.split("│")]
        # cells[0] is empty (before first │); the 5 columns are cells[1..5];
        # cells[-1] is empty (after last │). Need at least 6 parts.
        if len(cells) < 6:
            continue
        cid, title, author, replies, ttime = (cells[1], cells[2], cells[3], cells[4], cells[5])
        if cid and cid.isdigit():
            if current:
                rows.append(current)
            current = {
                "id": cid,
                "title": title,
                "author": author,
                "replies": replies,
                "time": ttime,
            }
        elif current is not None and title:
            # Continuation line of a wrapped title.
            current["title"] = (current["title"] + " " + title).strip()
        # else: stray/separator line, ignore

    if current:
        rows.append(current)

    out: list[dict[str, Any]] = []
    for r in rows:
        if not r["id"].isdigit():  # skip any non-data row
            continue
        try:
            reply_count = int(r["replies"]) if str(r["replies"]).isdigit() else 0
        except (ValueError, TypeError):
            reply_count = 0
        out.append(
            {
                "id": r["id"],
                "title": r["title"].replace("\n", " ").strip(),
                "author": (r["author"] or "").strip(),
                "replies": reply_count,
            }
        )
    return out


def _discover(limit: int) -> tuple[list[dict[str, Any]], str, bool]:
    """Discover new V2EX topics via the CLI.

    Returns ``(rows, status, risk_control)``.
    """
    all_rows: list[dict[str, Any]] = []
    ok_any = False
    risk_control = False
    for command, source in (("latest", "v2ex-cli-latest"), ("hot", "v2ex-cli-hot")):
        text, risky = _run_cli_topics(command, limit)
        if risky:
            risk_control = True
            break  # challenged — stop issuing further discovery calls
        if text is None:
            continue
        parsed = _parse_topics_table(text)
        if parsed:
            ok_any = True
            for r in parsed:
                r["source"] = source
            all_rows.extend(parsed)

    # Dedupe by topic id, keeping the first occurrence.
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for r in all_rows:
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        deduped.append(r)

    status = "risk_control" if risk_control else "ok" if ok_any else "cli_failed"
    return deduped, status, risk_control


# --------------------------------------------------------------------------- #
# Enrichment (legacy public API -> full body)
# --------------------------------------------------------------------------- #
def _fetch_topic_body(topic_id: str) -> tuple[dict[str, Any] | None, bool]:
    """Fetch full topic via legacy API.

    Returns ``(normalized_dict_or_None, risk_control)``. A Cloudflare
    challenge (403 / interstitial HTML) sets ``risk_control`` so callers can
    abort the remaining per-topic calls instead of hammering the platform.
    """
    url = _LEGACY_SHOW_URL.format(tid=topic_id)
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        # V2EX returns 403 for the Cloudflare challenge; any other HTTP error
        # is a normal per-topic failure (deleted topic, etc).
        if exc.code == 403:
            logger.warning(
                "v2ex legacy t/%s: 403 (Cloudflare challenge) - risk control, aborting enrichment",
                topic_id,
            )
            return None, True
        try:
            if _looks_like_challenge(exc.read(2000)):
                logger.warning(
                    "v2ex legacy t/%s: challenge page on HTTP %s - "
                    "risk control, aborting enrichment",
                    topic_id,
                    exc.code,
                )
                return None, True
        except Exception:
            pass
        logger.warning("v2ex legacy fetch failed for t/%s: HTTP %s", topic_id, exc.code)
        return None, False
    except Exception as exc:  # network errors -> degrade gracefully
        logger.warning("v2ex legacy fetch failed for t/%s: %s", topic_id, exc)
        return None, False

    if _looks_like_challenge(raw):
        logger.warning(
            "v2ex legacy t/%s: challenge page instead of JSON - risk control, aborting enrichment",
            topic_id,
        )
        return None, True

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("v2ex legacy JSON decode failed for t/%s: %s", topic_id, exc)
        return None, False

    # topics/show.json returns a list with one topic dict.
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        return None, False

    member = data.get("member") or {}
    node = data.get("node") or {}
    created = data.get("created") or data.get("last_modified") or 0
    try:
        published_at = datetime.fromtimestamp(int(created), tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        published_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return {
        "title": data.get("title", ""),
        "content": data.get("content") or "",
        "author": (member.get("username") or "").strip(),
        "node": node.get("title") or node.get("name") or "",
        "published_at": published_at,
        "replies": data.get("replies") or 0,
    }, False


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def _insert_rows(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> tuple[int, int, int]:
    """Insert rows into content_cache (pool) + articles (library).

    Returns (cache_inserted, cache_skipped, articles_inserted).
    """
    cache_ins = 0
    cache_skip = 0
    art_ins = 0

    for row in rows:
        tid = row["id"]
        title = row["title"]
        author = row.get("author") or ""
        body = row.get("body") or ""
        node = row.get("node") or ""
        published_at = row.get("published_at") or ""
        url = f"https://www.v2ex.com/t/{tid}"

        # --- content_cache (recommendation pool) ---
        try:
            cur = conn.execute(
                """INSERT OR IGNORE INTO content_cache (
                    bvid, title, up_name, author_name, content_url,
                    source_platform, source, content_type, pool_status,
                    discovered_at, body_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    tid,
                    title,
                    author,
                    author,
                    url,
                    "v2ex",
                    row.get("source", "v2ex-cli"),
                    "thread",
                    "fresh",
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    body,
                ),
            )
            if cur.rowcount > 0:
                cache_ins += 1
            else:
                cache_skip += 1
        except sqlite3.IntegrityError:
            cache_skip += 1

        # --- articles (reading library, only when we have a body) ---
        if body:
            try:
                cur = conn.execute(
                    """INSERT OR IGNORE INTO articles (
                        source_type, source_name, title, url, author,
                        content_text, published_at, tags
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        "v2ex",
                        "V2EX",
                        title,
                        url,
                        author,
                        body,
                        published_at,
                        json.dumps([node] if node else []),
                    ),
                )
                if cur.rowcount > 0:
                    art_ins += 1
            except sqlite3.IntegrityError:
                pass

    return cache_ins, cache_skip, art_ins


# --------------------------------------------------------------------------- #
# Cycle
# --------------------------------------------------------------------------- #
def _run_once(limit: int, discover_only: bool) -> dict[str, Any]:
    rows, status, risk_control = _discover(limit)
    if not rows:
        return {
            "ok": False,
            "reason": status,
            "risk_control": risk_control,
            "discovered": 0,
            "inserted": 0,
            "articles": 0,
        }

    # Enrich (best-effort) unless disabled. Abort the whole loop on the first
    # sign of platform risk-control so we never fan out a burst of requests
    # into a Cloudflare challenge.
    enriched = 0
    for r in rows:
        if discover_only:
            continue
        body_data, risky = _fetch_topic_body(r["id"])
        if risky:
            risk_control = True
            logger.warning(
                "risk-control hit on t/%s; aborting enrichment "
                "for the remaining %d topics this cycle",
                r["id"],
                len(rows) - enriched - 1,
            )
            break
        if body_data:
            r["body"] = body_data["content"]
            r["author"] = body_data["author"] or r["author"]
            r["node"] = body_data["node"]
            r["published_at"] = body_data["published_at"]
            enriched += 1

    if _DRY_RUN:
        return {
            "ok": True,
            "dry_run": True,
            "risk_control": risk_control,
            "discovered": len(rows),
            "would_have_body": enriched,
        }

    conn = sqlite3.connect(DB_PATH)
    try:
        cache_ins, cache_skip, art_ins = _insert_rows(conn, rows)
        conn.commit()
    finally:
        conn.close()

    return {
        "ok": True,
        "risk_control": risk_control,
        "discovered": len(rows),
        "with_body": enriched,
        "cache_inserted": cache_ins,
        "cache_skipped": cache_skip,
        "articles_inserted": art_ins,
    }


def _main() -> None:
    parser = argparse.ArgumentParser(description="V2EX CLI-based feed producer")
    parser.add_argument(
        "--dry-run", action="store_true", help="Discover + parse but skip DB writes."
    )
    parser.add_argument(
        "--discover-only",
        action="store_true",
        help="Skip legacy body enrichment (title-only rows).",
    )
    parser.add_argument(
        "--limit", type=int, default=DISCOVER_LIMIT, help="Topics per CLI command (latest/hot)."
    )
    parser.add_argument(
        "--interval", type=int, default=INTERVAL_HOURS, help="Hours between cycles when looping."
    )
    args = parser.parse_args()

    global _DRY_RUN
    _DRY_RUN = args.dry_run

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logger.info(
        "v2ex cli producer started (cli=%s, interval=%dh, limit=%d, dry_run=%s, discover_only=%s)",
        _V2EX_BIN,
        args.interval,
        args.limit,
        _DRY_RUN,
        args.discover_only,
    )

    if _DRY_RUN:
        result = _run_once(args.limit, args.discover_only)
        if result["ok"]:
            logger.info(
                "dry-run ok: %d discovered, %d would have body",
                result["discovered"],
                result.get("would_have_body", 0),
            )
        else:
            logger.warning("dry-run skipped: %s", result.get("reason", "unknown"))
        return

    while True:
        result = _run_once(args.limit, args.discover_only)
        if result["ok"]:
            logger.info(
                "feed ok: %d discovered, %d with body, %d pool new, %d pool dup, %d articles new",
                result["discovered"],
                result.get("with_body", 0),
                result.get("cache_inserted", 0),
                result.get("cache_skipped", 0),
                result.get("articles_inserted", 0),
            )
        else:
            logger.warning("feed skipped: %s", result.get("reason", "unknown"))

        if result.get("risk_control"):
            # Cloudflare challenged us: back off hard instead of retrying.
            sleep_hours = args.interval * RISK_BACKOFF_MULTIPLIER
            logger.warning(
                "platform risk-control detected - backing off %.0fh "
                "(%dh x%d). Do NOT tighten this without checking the "
                "platform rate-limit policy.",
                sleep_hours,
                args.interval,
                RISK_BACKOFF_MULTIPLIER,
            )
        else:
            sleep_hours = args.interval
        time.sleep(sleep_hours * 3600)


if __name__ == "__main__":
    _main()
