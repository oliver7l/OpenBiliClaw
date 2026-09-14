"""Fetch Hub 核心：UnifiedDoc 规格化输出、通道注册与降级编排、健康度记录。

设计见 docs/plans/2026-09-14-fetch-hub-unification-design.md。
"""
from __future__ import annotations

import dataclasses
import datetime
import json
import os
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

BEIJING = datetime.timezone(datetime.timedelta(hours=8))
PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONTENT_DB = Path(os.environ.get("OBC_CONTENT_DB") or (PROJECT_ROOT / "data" / "content.db"))


def now_bj() -> str:
    return datetime.datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M:%S")


def bj_ts(ts, fmt: str = "%Y-%m-%d %H:%M") -> str | None:
    """Unix 时间戳 → 北京时间字符串；空值/非法值返回 None，绝不瞎编。"""
    if not ts:
        return None
    try:
        ts = float(ts)
        if ts > 1e12:  # 毫秒
            ts /= 1000
        return datetime.datetime.fromtimestamp(int(ts), BEIJING).strftime(fmt)
    except (ValueError, OSError, OverflowError):
        return None


@dataclasses.dataclass
class Reply:
    author: str | None
    time: str | None
    content: str


@dataclasses.dataclass
class UnifiedDoc:
    """所有平台通道的统一输出形状。"""

    url: str
    platform: str
    title: str
    author: str | None
    published_at: str | None
    content_md: str
    replies: list  # list[Reply]
    images: list  # list[str]
    fetched_via: str
    fetched_at: str
    confidence: str = "full"  # full | partial（缺元数据等场景）
    extra: dict = dataclasses.field(default_factory=dict)  # 平台特有元数据（节点/点赞数等）

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["replies"] = [dataclasses.asdict(r) if isinstance(r, Reply) else r for r in self.replies]
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)




class ChannelError(Exception):
    """单通道失败。kind: challenge|parse|not_found|auth|network|config|error"""

    def __init__(self, kind: str, message: str, channel: str = ""):
        super().__init__(f"[{channel or '?'}:{kind}] {message}")
        self.kind = kind
        self.channel = channel
        self.message = message


class AllChannelsFailed(Exception):  # noqa: N818
    def __init__(self, url: str, platform: str, attempts: list):
        self.url = url
        self.platform = platform
        self.attempts = attempts
        kinds = "; ".join(f"{a['channel']}:{a['error_kind']}" for a in attempts)
        super().__init__(f"所有通道失败（{platform}）: {kinds}")


def detect_platform(url: str) -> str:
    u = (url or "").lower()
    if "v2ex.com" in u:
        return "v2ex"
    if "zhihu.com" in u:
        return "zhihu"
    if "xiaohongshu.com" in u or "xhslink" in u:
        return "xhs"
    if "bilibili.com" in u or "b23.tv" in u:
        return "bilibili"
    return "generic"


# 每个平台的声明式降级链；AgentLimb（真 Chrome 桥）是所有平台的最终兜底。
CHANNEL_CHAINS: dict[str, list[str]] = {
    "v2ex": ["v2ex-mindback", "v2ex-api-proxy", "agentlimb-v2ex"],
    "zhihu": ["zhihu-cli", "agentlimb-text"],
    "xhs": ["xhs-cli", "agentlimb-text"],
    "bilibili": ["bili-cli", "agentlimb-text"],
    "generic": ["generic-webfetch", "agentlimb-text"],
}


def _dispatch(channel: str, url: str) -> UnifiedDoc:
    """通道名 → 具体实现。懒加载避免循环依赖。"""
    from . import agentlimb, bilibili, generic, v2ex, xhs, zhihu

    table: dict[str, Callable[[str], UnifiedDoc]] = {
        "v2ex-mindback": v2ex.mindback_fetch,
        "v2ex-api-proxy": v2ex.api_proxy_fetch,
        "agentlimb-v2ex": agentlimb.v2ex_fetch,
        "zhihu-cli": zhihu.cli_fetch,
        "xhs-cli": xhs.cli_fetch,
        "bili-cli": bilibili.cli_fetch,
        "generic-webfetch": generic.webfetch,
        "agentlimb-text": agentlimb.text_fetch,
    }
    fn = table.get(channel)
    if fn is None:
        raise ChannelError("config", f"未知通道: {channel}", channel)
    return fn(url)


def log_fetch(url: str, platform: str, channel: str, ok: bool, error_kind, latency_ms: int, detail: str = "") -> None:
    """健康度记录：只 append，绝不因记录失败打断抓取。"""
    try:
        CONTENT_DB.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(CONTENT_DB, timeout=5)
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS fetch_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT, url TEXT, platform TEXT, channel TEXT,
                    ok INTEGER, error_kind TEXT, latency_ms INTEGER, detail TEXT)"""
            )
            conn.execute(
                "INSERT INTO fetch_log (ts,url,platform,channel,ok,error_kind,latency_ms,detail) VALUES (?,?,?,?,?,?,?,?)",
                (datetime.datetime.now(BEIJING).isoformat(timespec="seconds"), url, platform, channel, int(bool(ok)), error_kind, latency_ms, (detail or "")[:300]),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def fetch(url: str, *, forced_channel: str | None = None, no_fallback: bool = False, platform: str | None = None):
    """按降级链抓取。返回 (UnifiedDoc, attempts)；全失败抛 AllChannelsFailed。"""
    platform = platform or detect_platform(url)
    chain = [forced_channel] if forced_channel else list(CHANNEL_CHAINS.get(platform, ["generic-webfetch", "agentlimb-text"]))
    if no_fallback:
        chain = chain[:1]
    attempts: list[dict] = []
    for ch in chain:
        t0 = time.monotonic()
        try:
            doc = _dispatch(ch, url)
            if doc is None or not (doc.title or doc.content_md):
                raise ChannelError("parse", "空结果（无标题也无正文）", ch)
            doc.fetched_via = ch
            doc.fetched_at = now_bj()
            latency = int((time.monotonic() - t0) * 1000)
            attempts.append({"channel": ch, "ok": True, "latency_ms": latency, "error_kind": None, "detail": ""})
            log_fetch(url, platform, ch, True, None, latency)
            return doc, attempts
        except ChannelError as e:
            latency = int((time.monotonic() - t0) * 1000)
            attempts.append({"channel": ch or "unknown", "ok": False, "latency_ms": latency, "error_kind": e.kind, "detail": e.message})
            log_fetch(url, platform, ch, False, e.kind, latency, e.message)
        except Exception as e:  # 意外异常也降级，不让单通道崩溃拖死整条链
            latency = int((time.monotonic() - t0) * 1000)
            attempts.append({"channel": ch, "ok": False, "latency_ms": latency, "error_kind": "error", "detail": str(e)[:200]})
            log_fetch(url, platform, ch, False, "error", latency, str(e)[:300])
    raise AllChannelsFailed(url, platform, attempts)


def health(days: int = 7) -> list[dict]:
    """近 N 天各通道成功率（只读 fetch_log；表不存在返回空）。"""
    if not CONTENT_DB.exists():
        return []
    since = (datetime.datetime.now(BEIJING) - datetime.timedelta(days=days)).isoformat(timespec="seconds")
    try:
        conn = sqlite3.connect(CONTENT_DB, timeout=5)
        try:
            rows = conn.execute(
                """SELECT platform, channel,
                          COUNT(*) AS total, SUM(ok) AS ok_n,
                          AVG(latency_ms) AS avg_ms
                   FROM fetch_log WHERE ts >= ? GROUP BY platform, channel ORDER BY platform, channel""",
                (since,),
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return []
    return [
        {"platform": p, "channel": c, "total": t, "ok": o, "rate": (o / t) if t else 0.0, "avg_ms": int(m or 0)}
        for p, c, t, o, m in rows
    ]
