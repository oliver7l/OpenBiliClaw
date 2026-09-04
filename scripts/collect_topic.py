#!/usr/bin/env python3
"""持续搜集专题（topic）内容 —— 纯 CLI 通道，不占用浏览器。

通道设计（全部走 HTTP/API，绝不打开浏览器）：
1. ``bilibili`` —— 项目自带 B 站官方 WBI 搜索 API（``BilibiliAPIClient``，
   带用户 cookie + 反风控退避；这是 openbiliclaw 自己日常 discovery 用的通道）。
2. ``rss`` —— 公开 RSS 源（少数派 / InfoQ / 极客公园 / IT之家），
   按专题关键词匹配标题与摘要后收录。
3. ``pool`` —— 直接匹配项目内容池里**已有多来源内容**：
   ``content_cache``（推荐池：zhihu/youtube/bilibili/xiaohongshu/v2ex/douyin/
   xiaoyuzhou/wechat/reddit 等 11 来源）与 ``articles``（阅读库 8 万+ 条，
   含 youtube/zhihu/v2ex/xhs/虎嗅/小宇宙等）。零网络请求、零浏览器占用，
   一次跑完立即覆盖全平台。
4. ``csdn`` —— CSDN 搜索 API（``so.csdn.net/api/v3/search``，纯 HTTP，
   无签名无浏览器），覆盖 CSDN 博客与资源。
5. ``hot`` —— 知乎/微博实时热榜（60s.viki.moe 公开 API，用户本机
   ``008-zhihu`` 同源），按专题关键词匹配标题后收录——给专题提供
   「正在发生」的新鲜内容。

每个专题定义 keywords + platforms（bilibili / rss / pool / csdn / hot 组合）。
结果按 (topic_id, content_key) 幂等去重，可重复运行。
历史备注：曾用 autocli 的 douban/xiaohongshu/weibo 搜索（浏览器自动化通道），
用户明确反馈会抢占浏览器 —— 已全部移除，浏览器通道站点不再参与收集。
小红书 get-xiaohongshu-* 工具同样依赖 Chrome 扩展，未接入；
掘金（juejin）搜索 API 需要签名（返回空结果），暂未接入，留待后续。

用法:
    python scripts/collect_topic.py                     # 搜集全部 active 专题
    python scripts/collect_topic.py --slug advertising  # 只搜集指定专题
    python scripts/collect_topic.py --limit 6           # 每关键词/每源最多 6 条
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))
sys.path.insert(0, str(_PROJECT_ROOT))

from openbiliclaw.storage.database import Database  # noqa: E402

# ── RSS 源（稳定公开，纯 HTTP）──────────────────────────────────────────
DEFAULT_RSS_SOURCES: dict[str, str] = {
    "sspai": "https://sspai.com/feed",
    "infoq": "https://www.infoq.cn/feed",
    "geekpark": "https://www.geekpark.net/rss",
    "ithome": "https://www.ithome.com/rss/",
}

_BV_RE = re.compile(r"BV[a-zA-Z0-9]+")


# ── bilibili 通道 ─────────────────────────────────────────────────────────
def _build_bilibili_client():
    """Build the configured Bilibili API client (same as CLI's own)."""
    from openbiliclaw.bilibili.api import BilibiliAPIClient
    from openbiliclaw.bilibili.auth import resolve_runtime_cookie
    from openbiliclaw.config import load_config

    config = load_config()
    return BilibiliAPIClient(
        cookie=resolve_runtime_cookie(
            data_dir=config.data_path,
            configured_cookie=config.bilibili.cookie,
        ),
        min_request_interval=0.8,
    )


def _bili_item(raw: dict) -> dict | None:
    """Map one B站 search result to a topic_items row."""
    bvid = str(raw.get("bvid") or raw.get("content_id") or "").strip()
    title = str(raw.get("title") or "").strip()
    if not bvid and not title:
        return None
    title = re.sub(r"<[^>]+>", "", title)  # strip highlight tags
    summary = str(raw.get("description") or "").strip()[:400]
    cover = str(raw.get("pic") or "").strip()
    url = f"https://www.bilibili.com/video/{bvid}" if bvid else ""
    return {
        "content_key": f"bilibili:{bvid}" if bvid else f"bilibili:{title}",
        "title": title,
        "url": url,
        "source_platform": "bilibili",
        "source_name": str(raw.get("author") or "").strip(),
        "cover_url": cover,
        "summary": summary,
        "topic_label": "",
    }


async def collect_bilibili(db: Database, topic: dict, *, limit: int) -> dict:
    """Search Bilibili official API per keyword; returns per-topic stats delta."""
    stats: dict = {"new": 0, "dup": 0, "failed": 0, "searches": 0}
    keywords = json.loads(topic.get("keywords") or "[]")
    if not keywords:
        return stats
    client = _build_bilibili_client()
    try:
        for keyword in keywords:
            try:
                hits = await client.search(keyword, page=1, page_size=max(limit, 20))
            except Exception as exc:  # noqa: BLE001 - per-keyword tolerance
                stats["failed"] += 1
                print(f"    ⚠ bilibili×{keyword}: {exc}")
                continue
            stats["searches"] += 1
            for raw in hits[:limit]:
                item = _bili_item(raw)
                if item is None:
                    continue
                if db.add_topic_item(topic["id"], item):
                    stats["new"] += 1
                else:
                    stats["dup"] += 1
            await asyncio.sleep(0.5)  # be gentle with B站 rate limits
    finally:
        with _suppress():
            await client.close()
    return stats


def _suppress():
    from contextlib import suppress

    return suppress(Exception)


# ── rss 通道 ──────────────────────────────────────────────────────────────
def _fetch_rss(url: str, *, timeout: float = 15.0) -> list[dict]:
    """Fetch and parse one RSS feed; returns list of entry dicts."""
    import urllib.request

    import feedparser

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (OpenBiliClaw topic collector)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(1024 * 1024)  # cap at 1 MiB
    parsed = feedparser.parse(raw)
    entries: list[dict] = []
    for entry in parsed.entries[:40]:
        title = str(entry.get("title") or "").strip()
        if not title:
            continue
        link = str(entry.get("link") or "").strip()
        summary = ""
        for key in ("summary", "description"):
            value = str(entry.get(key) or "").strip()
            if value:
                summary = re.sub(r"<[^>]+>", "", value)[:400]
                break
        entries.append({"title": title, "link": link, "summary": summary})
    return entries


def _matches_keywords(title: str, summary: str, keywords: list[str]) -> bool:
    haystack = (title + " " + summary).lower()
    return any(k.lower() in haystack for k in keywords if k)


def collect_rss(db: Database, topic: dict, *, limit: int, sources: dict[str, str]) -> dict:
    """Fetch RSS sources and match entries against topic keywords."""
    stats: dict = {"new": 0, "dup": 0, "failed": 0, "searches": 0}
    keywords = json.loads(topic.get("keywords") or "[]")
    if not keywords:
        return stats
    for source_name, url in sources.items():
        try:
            entries = _fetch_rss(url)
        except Exception as exc:  # noqa: BLE001 - per-source tolerance
            stats["failed"] += 1
            print(f"    ⚠ rss[{source_name}]: {exc}")
            continue
        stats["searches"] += 1
        matched = 0
        for entry in entries:
            if not _matches_keywords(entry["title"], entry["summary"], keywords):
                continue
            matched += 1
            if matched > limit:
                break
            link = entry["link"]
            clean_link = re.sub(r"[?#].*$", "", link).rstrip("/")
            item = {
                "content_key": f"rss:{source_name}:{clean_link}" if clean_link
                else f"rss:{source_name}:{entry['title']}",
                "title": entry["title"],
                "url": link,
                "source_platform": f"rss:{source_name}",
                "source_name": source_name,
                "cover_url": "",
                "summary": entry["summary"],
                "topic_label": "",
            }
            if db.add_topic_item(topic["id"], item):
                stats["new"] += 1
            else:
                stats["dup"] += 1
        time.sleep(0.3)
    return stats


# ── pool 通道：匹配项目内容池（多来源，零网络） ─────────────────────────
def _like_clause(columns: list[str], keywords: list[str]) -> tuple[str, list[str]]:
    """Build a keyword LIKE OR-condition across the given columns."""
    conds: list[str] = []
    params: list[str] = []
    for col in columns:
        for kw in keywords:
            conds.append(
                f"(LOWER(COALESCE({col}, '')) LIKE '%' || LOWER(?) || '%')"
            )
            params.append(kw)
    return "(" + " OR ".join(conds) + ")", params


def collect_pool(db: Database, topic: dict, *, limit: int) -> dict:
    """Match topic keywords against the project's existing content pool.

    Covers content_cache (11+ sources) and articles (reading library):
    zhihu / youtube / bilibili / xiaohongshu / v2ex / douyin / xiaoyuzhou /
    wechat / reddit / rss / 虎嗅 etc. — everything already collected by the
    app's own discovery pipeline, no network, no browser.
    """
    stats: dict = {"new": 0, "dup": 0, "failed": 0, "searches": 0}
    keywords = [k for k in json.loads(topic.get("keywords") or "[]") if k]
    if not keywords:
        return stats

    # 1) content_cache (recommendation pool)
    cond, params = _like_clause(["title", "description", "topic_group"], keywords)
    rows = db.conn.execute(
        f"SELECT * FROM content_cache WHERE {cond} "
        "ORDER BY COALESCE(relevance_score, 0) DESC LIMIT ?",
        params + [limit * 5],
    ).fetchall()
    for row in rows:
        row = dict(row)
        content_id = str(row.get("content_id") or "").strip()
        bvid = str(row.get("bvid") or "").strip()
        platform = str(row.get("source_platform") or "").strip() or "pool"
        key = content_id or bvid or str(row.get("content_url") or "").strip()
        if not key:
            continue
        item = {
            "content_key": f"pool:{platform}:{key}",
            "title": str(row.get("title") or "").strip(),
            "url": str(row.get("content_url") or "").strip(),
            "source_platform": platform,
            "source_name": str(row.get("author_name") or row.get("up_name") or "").strip(),
            "cover_url": str(row.get("cover_url") or "").strip(),
            "summary": str(row.get("description") or "").strip()[:400],
            "topic_label": str(row.get("topic_group") or "").strip(),
        }
        if item["title"]:
            if db.add_topic_item(topic["id"], item):
                stats["new"] += 1
            else:
                stats["dup"] += 1
    stats["searches"] += 1

    # 2) articles (reading library)
    cond, params = _like_clause(["title", "summary", "tags"], keywords)
    rows = db.conn.execute(
        f"SELECT * FROM articles WHERE {cond} "
        "ORDER BY COALESCE(published_at, created_at) DESC LIMIT ?",
        params + [limit * 5],
    ).fetchall()
    for row in rows:
        row = dict(row)
        url = str(row.get("url") or "").strip()
        source_type = str(row.get("source_type") or "article").strip()
        key = url or f"{source_type}:{str(row.get('title') or '').strip()}"
        item = {
            "content_key": f"article:{key}",
            "title": str(row.get("title") or "").strip(),
            "url": url,
            "source_platform": f"article:{source_type}",
            "source_name": str(row.get("source_name") or row.get("author") or "").strip(),
            "cover_url": "",
            "summary": str(row.get("summary") or "").strip()[:400],
            "topic_label": str(row.get("tags") or "").strip()[:60],
        }
        if item["title"]:
            if db.add_topic_item(topic["id"], item):
                stats["new"] += 1
            else:
                stats["dup"] += 1
    stats["searches"] += 1
    return stats


# ── csdn 通道：CSDN 搜索 API（纯 HTTP） ─────────────────────────────────
def _fetch_csdn(keyword: str, *, timeout: float = 12.0) -> list[dict]:
    """Search CSDN via its public so.csdn.net JSON API (no auth needed)."""
    import json as _json
    import urllib.parse
    import urllib.request

    url = (
        "https://so.csdn.net/api/v3/search?t=all&p=1&q="
        + urllib.parse.quote(keyword)
    )
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (OpenBiliClaw topic collector)"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = _json.loads(resp.read(2 * 1024 * 1024))
    return payload.get("result_vos") or []


def collect_csdn(db: Database, topic: dict, *, limit: int) -> dict:
    """Search CSDN per keyword; keeps blog/article hits (drops downloads)."""
    stats: dict = {"new": 0, "dup": 0, "failed": 0, "searches": 0}
    keywords = [k for k in json.loads(topic.get("keywords") or "[]") if k]
    if not keywords:
        return stats
    for keyword in keywords:
        try:
            hits = _fetch_csdn(keyword)
        except Exception as exc:  # noqa: BLE001 - per-keyword tolerance
            stats["failed"] += 1
            print(f"    ⚠ csdn×{keyword}: {exc}")
            continue
        stats["searches"] += 1
        collected = 0
        for raw in hits:
            ftype = str(raw.get("type") or "").strip()
            if ftype not in {"blog", "article", "download"}:
                continue
            title = re.sub(r"<[^>]+>", "", str(raw.get("title") or "").strip())
            url = str(raw.get("url") or "").strip()
            if not title or not url:
                continue
            collected += 1
            if collected > limit:
                break
            clean_url = re.sub(r"[?#].*$", "", url).rstrip("/")
            item = {
                "content_key": f"csdn:{clean_url}",
                "title": title,
                "url": url,
                "source_platform": "csdn",
                "source_name": str(raw.get("author") or raw.get("nickname") or "").strip(),
                "cover_url": "",
                "summary": re.sub(
                    r"<[^>]+>", "", str(raw.get("description") or "").strip()
                )[:400],
                "topic_label": f"csdn:{ftype}",
            }
            if db.add_topic_item(topic["id"], item):
                stats["new"] += 1
            else:
                stats["dup"] += 1
        time.sleep(0.3)
    return stats


# ── hot 通道：知乎/微博实时热榜（60s API，纯 HTTP） ────────────────────
_HOT_ENDPOINTS: dict[str, str] = {
    "zhihu": "https://60s.viki.moe/v2/zhihu",
    "weibo": "https://60s.viki.moe/v2/weibo",
}


def _fetch_hot(platform: str, *, timeout: float = 12.0) -> list[dict]:
    """Fetch one platform's realtime hot list via the 60s API."""
    import json as _json
    import urllib.request

    req = urllib.request.Request(
        _HOT_ENDPOINTS[platform],
        headers={"User-Agent": "Mozilla/5.0 (OpenBiliClaw topic collector)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = _json.loads(resp.read(2 * 1024 * 1024))
    return payload.get("data") or []


def collect_hot(db: Database, topic: dict, *, limit: int) -> dict:
    """Match topic keywords against zhihu/weibo realtime hot lists.

    Hot lists are broad public topics, so keyword hits are sparse but give
    the topic fresh "what's happening now" items when they do match.
    """
    stats: dict = {"new": 0, "dup": 0, "failed": 0, "searches": 0}
    keywords = [k for k in json.loads(topic.get("keywords") or "[]") if k]
    if not keywords:
        return stats
    for platform in _HOT_ENDPOINTS:
        try:
            items = _fetch_hot(platform)
        except Exception as exc:  # noqa: BLE001 - per-source tolerance
            stats["failed"] += 1
            print(f"    ⚠ hot[{platform}]: {exc}")
            continue
        stats["searches"] += 1
        collected = 0
        for raw in items:
            title = re.sub(
                r"<[^>]+>", "", str(raw.get("title") or "").strip()
            )
            if not title or not _matches_keywords(title, "", keywords):
                continue
            collected += 1
            if collected > limit:
                break
            link = str(raw.get("link") or "").strip()
            item = {
                "content_key": f"hot:{platform}:{title}",
                "title": title,
                "url": link,
                "source_platform": f"hot:{platform}",
                "source_name": "",
                "cover_url": "",
                "summary": str(raw.get("detail") or "").strip()[:400],
                "topic_label": str(raw.get("hot_value_desc") or "").strip(),
            }
            if db.add_topic_item(topic["id"], item):
                stats["new"] += 1
            else:
                stats["dup"] += 1
        time.sleep(0.3)
    return stats


def seed_discovery_keywords(db: Database, topic: dict) -> dict:
    """Seed the topic's keywords into ``discovery_keywords`` so the project's
    own discovery scheduler (``discovery_cron``, every 8h) keeps searching
    them via its bilibili official-API channel. New content lands in
    content_cache, which the ``pool`` channel then matches into the topic —
    a fully-CLI loop with no extra browser usage.

    Idempotent: the partial unique index (platform, keyword, digest) with
    status IN ('pending','claimed','executing') makes re-seeding a no-op.
    """
    stats: dict = {"seeded": 0, "existing": 0}
    keywords = [k for k in json.loads(topic.get("keywords") or "[]") if k]
    if not keywords:
        return stats
    conn = db.conn
    for word in keywords:
        try:
            conn.execute(
                """
                INSERT INTO discovery_keywords (platform, keyword, status)
                VALUES ('bilibili', ?, 'pending')
                """,
                (word,),
            )
            stats["seeded"] += 1
        except Exception:  # noqa: BLE001 - unique in-flight index hit
            stats["existing"] += 1
    db.conn.commit()
    return stats


# ── zhihu-cli / xhs-cli 通道：用户专用 CLI（reverse-engineered API） ──────
# 用户 pipx 安装的专业 CLI（xiaohongshu-cli 0.6.4 / zhihu-cli），走官方
# 逆向 API，不操作浏览器、不抢占窗口。登录态（cookie）由 CLI 自己管理。
#
# 风控与频率（用户要求，小红书尤其注意）：
#   - 频道级冷却：所有用户 CLI 通道（zhihu-cli/xhs-cli/bili-cli/rdt-cli）
#     按自然日冷却——当天已跑过则该通道跳过（一天最多一次）。
#   - xhs-cli 每次搜索（每个关键词）之间至少间隔 12 小时：一次收集只搜
#     1 个关键词，搜完下一个要等 12 小时，绝不高频打小红书接口。
#   - xhs-cli 触发验证码时立即终止该通道本次剩余关键词，并冷却到次日。
#   - 冷却状态落在 data/.topic_cli_cooldown.json，与项目数据同库同目录。
_ZHIHU_BIN = "/Users/imac/.local/bin/zhihu"
_XHS_BIN = "/Users/imac/.local/bin/xhs"
_COOLDOWN_FILE = Path(__file__).resolve().parent.parent / "data" / ".topic_cli_cooldown.json"
_CLI_COOLDOWN_CHANNELS = ("zhihu-cli", "xhs-cli", "bili-cli", "rdt-cli")
_XHS_SEARCH_GAP_HOURS = 12.0  # 小红书两次搜索之间至少 12 小时


def _now_cst() -> datetime:
    """Asia/Shanghai 当前时间（无 pytz 依赖，用固定 +8 偏移）。"""
    return datetime.now(timezone(timedelta(hours=8)))


def _today_str() -> str:
    return _now_cst().strftime("%Y-%m-%d")


def _load_cooldown() -> dict:
    try:
        return json.loads(_COOLDOWN_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - missing/corrupt state is fine
        return {}


def _save_cooldown(state: dict) -> None:
    with contextlib.suppress(OSError):
        _COOLDOWN_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _cli_channel_due(channel: str) -> bool:
    """True if the CLI channel may run today (not yet run today)."""
    state = _load_cooldown()
    return state.get(channel, {}).get("last_run_date") != _today_str()


def _mark_cli_run(channel: str) -> None:
    state = _load_cooldown()
    state[channel] = {"last_run_date": _today_str()}
    _save_cooldown(state)


def _xhs_search_due(state: dict) -> bool:
    """True if another xhs search is allowed now (>=12h since last one)."""
    last = (state.get("xhs-cli") or {}).get("last_search_at")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except (TypeError, ValueError):
        return True
    now = _now_cst()
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=now.tzinfo)
    return (now - last_dt) >= timedelta(hours=_XHS_SEARCH_GAP_HOURS)


def _run_cli_json(cmd: list[str], *, timeout: float) -> dict | None:
    """Run a CLI expecting a JSON blob on stdout; None on any failure."""
    import subprocess

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except Exception:  # noqa: BLE001 - per-call tolerance
        return None
    if proc.returncode != 0:
        return None
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _zhihu_web_url(obj: dict) -> str:
    """Convert a zhihu search object into a user-openable web URL."""
    otype = obj.get("type")
    oid = str(obj.get("id") or "")
    raw = str(obj.get("url") or "")
    if otype == "article" and oid:
        return f"https://zhuanlan.zhihu.com/p/{oid}"
    if otype == "answer" and oid:
        return f"https://www.zhihu.com/answer/{oid}"
    if otype == "question":
        if "www.zhihu.com/question/" in raw:
            return raw
        if oid:
            return f"https://www.zhihu.com/question/{oid}"
    return raw or ""


def collect_zhihu_cli(db: Database, topic: dict, *, limit: int) -> dict:
    """Collect via the user's ``zhihu`` CLI search (reverse-engineered API)."""
    stats: dict = {"new": 0, "dup": 0, "failed": 0, "searches": 0, "skipped": 0}
    if not _cli_channel_due("zhihu-cli"):
        stats["skipped"] = 1
        print("    ⏭ zhihu-cli: 今日已收集，跳过（一天最多一次）")
        return stats
    keywords = [k for k in json.loads(topic.get("keywords") or "[]") if k]
    if not keywords or not os.path.exists(_ZHIHU_BIN):
        return stats
    for kw in keywords[:limit]:
        payload = _run_cli_json([_ZHIHU_BIN, "search", kw, "--json"], timeout=20)
        if payload is None:
            stats["failed"] += 1
            print(f"    ⚠ zhihu-cli[{kw}]: 调用失败/非 JSON")
            continue
        stats["searches"] += 1
        collected = 0
        for item in payload.get("data") or []:
            if item.get("type") != "search_result":
                continue
            obj = item.get("object") or {}
            otype = obj.get("type")
            if otype not in {"article", "answer", "question"}:
                continue
            hl = item.get("highlight") or {}
            title = re.sub(r"<[^>]+>", "", str(hl.get("title") or obj.get("title") or "")).strip()
            desc = re.sub(r"<[^>]+>", "", str(hl.get("description") or "")).strip()
            if not title:
                continue
            collected += 1
            if collected > limit:
                break
            item_row = {
                "content_key": f"zhihu-cli:{otype}:{obj.get('id')}",
                "title": title,
                "url": _zhihu_web_url(obj),
                "source_platform": "zhihu-cli",
                "source_name": "知乎",
                "cover_url": "",
                "summary": desc[:400],
                "topic_label": "",
            }
            if db.add_topic_item(topic["id"], item_row):
                stats["new"] += 1
            else:
                stats["dup"] += 1
        time.sleep(0.5)
    _mark_cli_run("zhihu-cli")
    return stats


def collect_xhs_cli(db: Database, topic: dict, *, limit: int) -> dict:
    """Collect via the user's ``xhs`` CLI search (reverse-engineered API).

    Extra care for risk control (user requirement): each xhs *search* must be
    at least 12h apart, so a run typically searches only the first keyword;
    a captcha response stops the whole channel for the day.
    """
    stats: dict = {"new": 0, "dup": 0, "failed": 0, "searches": 0, "skipped": 0}
    if not _cli_channel_due("xhs-cli"):
        stats["skipped"] = 1
        print("    ⏭ xhs-cli: 今日已收集，跳过（一天最多一次）")
        return stats
    keywords = [k for k in json.loads(topic.get("keywords") or "[]") if k]
    if not keywords or not os.path.exists(_XHS_BIN):
        return stats
    for kw in keywords[:limit]:
        if not _xhs_search_due(_load_cooldown()):
            print(f"    ⏭ xhs-cli[{kw}]: 距上次小红书搜索不足 12 小时，本次停止（风控保护）")
            break
        payload = _run_cli_json([_XHS_BIN, "search", kw, "--json"], timeout=30)
        if payload is None:
            stats["failed"] += 1
            print(f"    ⚠ xhs-cli[{kw}]: 调用失败/非 JSON")
            continue
        if not payload.get("ok"):
            # 风控：验证码等限流响应 → 冷却到次日并终止本通道
            err = str((payload.get("error") or {}).get("message") or "").lower()
            if "captcha" in err or "验证码" in err or "frequent" in err:
                print(f"    🛑 xhs-cli[{kw}]: 触发风控（{err[:60]}），当日不再尝试")
                _mark_cli_run("xhs-cli")
                stats["skipped"] = 1
                return stats
            stats["failed"] += 1
            print(f"    ⚠ xhs-cli[{kw}]: 搜索失败 {err[:80]}")
            continue
        # 搜索成功：记录本次搜索时间（下次搜索需等 12 小时）
        state = _load_cooldown()
        state.setdefault("xhs-cli", {})
        state["xhs-cli"]["last_search_at"] = _now_cst().isoformat()
        _save_cooldown(state)
        stats["searches"] += 1
        collected = 0
        for item in (payload.get("data") or {}).get("items") or []:
            card = item.get("note_card") or {}
            title = str(card.get("display_title") or "").strip()
            nid = str(item.get("id") or "").strip()
            token = str(item.get("xsec_token") or "").strip()
            if not title or not nid:
                continue
            collected += 1
            if collected > limit:
                break
            url = f"https://www.xiaohongshu.com/explore/{nid}"
            if token:
                url += f"?xsec_token={token}&xsec_source=pc_search"
            author = (card.get("user") or {}).get("nick_name") or ""
            item_row = {
                "content_key": f"xhs-cli:{nid}",
                "title": title,
                "url": url,
                "source_platform": "xhs-cli",
                "source_name": f"小红书·{author}" if author else "小红书",
                "cover_url": "",
                "summary": "",
                "topic_label": "",
            }
            if db.add_topic_item(topic["id"], item_row):
                stats["new"] += 1
            else:
                stats["dup"] += 1
        # 关键词间隔 12 小时，跑完 1 个关键词后本通道本次结束
        break
    _mark_cli_run("xhs-cli")
    return stats


# ── bili-cli / rdt-cli 通道：用户专用 CLI（jackwener 系列） ───────────────
_BILI_BIN = "/Users/imac/.local/bin/bili"
_RDT_BIN = "/Users/imac/.local/bin/rdt"


def collect_bili_cli(db: Database, topic: dict, *, limit: int) -> dict:
    """Collect via the user's ``bili`` (bilibili-cli) video search."""
    stats: dict = {"new": 0, "dup": 0, "failed": 0, "searches": 0, "skipped": 0}
    if not _cli_channel_due("bili-cli"):
        stats["skipped"] = 1
        print("    ⏭ bili-cli: 今日已收集，跳过（一天最多一次）")
        return stats
    keywords = [k for k in json.loads(topic.get("keywords") or "[]") if k]
    if not keywords or not os.path.exists(_BILI_BIN):
        return stats
    for kw in keywords[:limit]:
        payload = _run_cli_json([_BILI_BIN, "search", kw, "--type", "video", "--json"], timeout=25)
        if payload is None or not payload.get("ok"):
            stats["failed"] += 1
            print(f"    ⚠ bili-cli[{kw}]: 调用失败/非 JSON")
            continue
        stats["searches"] += 1
        collected = 0
        for row in payload.get("data") or []:
            bvid = str(row.get("bvid") or "").strip()
            title = str(row.get("title") or "").strip()
            if not bvid or not title:
                continue
            collected += 1
            if collected > limit:
                break
            item_row = {
                "content_key": f"bili-cli:{bvid}",
                "title": title,
                "url": f"https://www.bilibili.com/video/{bvid}",
                "source_platform": "bili-cli",
                "source_name": str(row.get("author") or "B站"),
                "cover_url": "",
                "summary": "",
                "topic_label": f"播放 {row.get('play') or ''}".strip(),
            }
            if db.add_topic_item(topic["id"], item_row):
                stats["new"] += 1
            else:
                stats["dup"] += 1
        time.sleep(0.4)
    _mark_cli_run("bili-cli")
    return stats


def collect_rdt_cli(db: Database, topic: dict, *, limit: int) -> dict:
    """Collect via the user's ``rdt`` (rdt-cli) Reddit search."""
    stats: dict = {"new": 0, "dup": 0, "failed": 0, "searches": 0, "skipped": 0}
    if not _cli_channel_due("rdt-cli"):
        stats["skipped"] = 1
        print("    ⏭ rdt-cli: 今日已收集，跳过（一天最多一次）")
        return stats
    keywords = [k for k in json.loads(topic.get("keywords") or "[]") if k]
    if not keywords or not os.path.exists(_RDT_BIN):
        return stats
    for kw in keywords[:limit]:
        payload = _run_cli_json([_RDT_BIN, "search", kw, "-n", str(max(limit, 5)), "--json"], timeout=30)
        if payload is None or not payload.get("ok"):
            stats["failed"] += 1
            print(f"    ⚠ rdt-cli[{kw}]: 调用失败/非 JSON")
            continue
        stats["searches"] += 1
        collected = 0
        children = ((payload.get("data") or {}).get("data") or {}).get("children") or []
        for child in children:
            row = child.get("data") or {}
            title = str(row.get("title") or "").strip()
            permalink = str(row.get("permalink") or "").strip()
            if not title or not permalink:
                continue
            collected += 1
            if collected > limit:
                break
            selftext = re.sub(r"\s+", " ", str(row.get("selftext") or "")).strip()
            item_row = {
                "content_key": f"rdt-cli:{permalink}",
                "title": title,
                "url": f"https://www.reddit.com{permalink}",
                "source_platform": "rdt-cli",
                "source_name": f"r/{row.get('subreddit') or ''}".strip(),
                "cover_url": "",
                "summary": selftext[:400],
                "topic_label": "",
            }
            if db.add_topic_item(topic["id"], item_row):
                stats["new"] += 1
            else:
                stats["dup"] += 1
        time.sleep(0.4)
    _mark_cli_run("rdt-cli")
    return stats


# ── 总入口 ────────────────────────────────────────────────────────────────
async def _collect_one(db: Database, topic: dict, *, limit: int, rss_sources: dict[str, str]) -> dict:
    platforms = json.loads(topic.get("platforms") or '["bilibili"]')
    platforms = [
        p
        for p in platforms
        if p
        in {
            "bilibili",
            "rss",
            "pool",
            "csdn",
            "hot",
            "zhihu-cli",
            "xhs-cli",
            "bili-cli",
            "rdt-cli",
        }
    ]
    merged: dict = {"new": 0, "dup": 0, "failed": 0, "searches": 0, "skipped": 0}
    for platform in platforms:
        if platform == "bilibili":
            stats = await collect_bilibili(db, topic, limit=limit)
        elif platform == "rss":
            stats = collect_rss(db, topic, limit=limit, sources=rss_sources)
        elif platform == "pool":
            stats = collect_pool(db, topic, limit=limit)
        elif platform == "csdn":
            stats = collect_csdn(db, topic, limit=limit)
        elif platform == "hot":
            stats = collect_hot(db, topic, limit=limit)
        elif platform == "zhihu-cli":
            stats = collect_zhihu_cli(db, topic, limit=limit)
        elif platform == "xhs-cli":
            stats = collect_xhs_cli(db, topic, limit=limit)
        elif platform == "bili-cli":
            stats = collect_bili_cli(db, topic, limit=limit)
        elif platform == "rdt-cli":
            stats = collect_rdt_cli(db, topic, limit=limit)
        else:
            continue
        for key in merged:
            merged[key] += stats.get(key, 0)
    # Keep the topic's keywords in the project's discovery queue so its own
    # every-8h scheduler keeps sourcing new content for this topic (CLI loop).
    seed_stats = seed_discovery_keywords(db, topic)
    merged["seeded"] = seed_stats["seeded"]
    merged["existing"] = seed_stats["existing"]
    db.mark_topic_collected(topic["id"])
    return merged


def main() -> int:
    parser = argparse.ArgumentParser(description="持续搜集专题内容（纯 CLI，B站官方 API + RSS）")
    parser.add_argument("--slug", help="只搜集指定 slug 的专题（默认全部 active）")
    parser.add_argument("--limit", type=int, default=8, help="每关键词/每源条数")
    parser.add_argument("--db", default=None, help="数据库路径（默认项目 data/openbiliclaw.db）")
    parser.add_argument(
        "--no-rss", action="store_true", help="跳过 RSS 通道（只跑 bilibili）"
    )
    args = parser.parse_args()

    db_path = args.db or str(_PROJECT_ROOT / "data" / "openbiliclaw.db")
    db = Database(db_path)
    db.initialize()

    if args.slug:
        topic = db.get_topic_by_slug(args.slug)
        topics = [topic] if topic else []
        if not topic:
            print(f"[collect_topic] 未找到专题: {args.slug}")
            return 1
    else:
        topics = db.list_topics(include_paused=False)

    if not topics:
        print("[collect_topic] 没有 active 专题，先用 API 或 create_topic 建一个。")
        return 0

    rss_sources = {} if args.no_rss else dict(DEFAULT_RSS_SOURCES)
    for topic in topics:
        stats = asyncio.run(_collect_one(db, topic, limit=args.limit, rss_sources=rss_sources))
        count = db.count_topic_items(topic["id"])
        skip_note = f" / 跳过(冷却) {stats['skipped']}" if stats.get("skipped") else ""
        print(
            f"[collect_topic] 「{topic['name']}」新增 {stats['new']} / 重复 {stats['dup']} "
            f"/ 抓取 {stats['searches']} / 失败 {stats['failed']}{skip_note} → 共 {count} 条"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
