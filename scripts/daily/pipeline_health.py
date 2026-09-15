#!/usr/bin/env python3
"""采集线体检（D1）：各源状态 + 任务积压 + 子系统上次运行 + pm2 + 库体积。

只读。快照写到 data/daily_snapshots/pipeline_health_YYYY-MM-DD.json，用于算「比昨天多了多少」。

用法：
    .venv/bin/python scripts/daily/pipeline_health.py
    .venv/bin/python scripts/daily/pipeline_health.py --quiet   # 只打印问题行 + JSON

输出：体检简报 + 最后一行 JSON（issues 列表 / ok 布尔），供自动化判断是否要报警。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import urllib.request
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_BASE = os.environ.get("OBC_API_BASE", "http://127.0.0.1:8420").rstrip("/")
SNAP_DIR = ROOT / "data" / "daily_snapshots"
DATA_DIR = ROOT / "data"

# 积压任务表：openbiliclaw.db 里的任务队列
BACKLOG_TABLES = ("zhihu_tasks", "dy_tasks", "bili_tasks", "xhs_tasks", "getnote_pending", "getnote_body_task")
BACKLOG_LABEL = {
    "zhihu_tasks": "知乎任务",
    "dy_tasks": "抖音任务",
    "bili_tasks": "B站任务",
    "xhs_tasks": "小红书任务",
    "getnote_pending": "得到待处理",
    "getnote_body_task": "得到正文任务",
}
# 各子系统的「上次运行」键与允许的最大间隔（小时）
STALE_LIMITS = {
    "last_content_filler_body": 30,
    "last_content_filler_yt": 30,
    "last_content_filler_bili": 30,
    "last_content_filler_getnote": 30,
    "last_chat_analysis": 30,
    "last_insight_report": 30,
    "last_synthesis": 30,
    "last_diary_analysis": 30,
    "last_topic_miner": 36,
    "last_knowledge_graph": 48,
    "last_drift": 36,
}
STALE_LABEL = {
    "last_content_filler_body": "正文补抓",
    "last_content_filler_yt": "YouTube字幕",
    "last_content_filler_bili": "B站字幕",
    "last_content_filler_getnote": "得到通道",
    "last_chat_analysis": "聊天分析",
    "last_insight_report": "洞察报告",
    "last_synthesis": "日记综合",
    "last_diary_analysis": "日记分析",
    "last_topic_miner": "话题挖掘",
    "last_knowledge_graph": "知识图谱",
    "last_drift": "漂移检测",
}
# 已知高频重启但属于「可接受」的进程（避免天天刷屏）——留空表示都报警
RESTART_NOISE_OK = {"openbiliclaw-api": 0}
DISK_TOTAL_WARN_GB = 5.0
DISK_GROWTH_WARN_MB = 300.0


def read_json_snapshot(prefix: str, before: str) -> dict | None:
    files = sorted(SNAP_DIR.glob(f"{prefix}_*.json"))
    older = [f for f in files if f.stem.replace(f"{prefix}_", "") < before]
    if older:
        return json.loads(older[-1].read_text())
    same = [f for f in files if f.stem == f"{prefix}_{before}"]
    return json.loads(same[-1].read_text()) if same else None


def api_get(path: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(f"{API_BASE}{path}", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def collect_sources(issues: list[str]) -> dict:
    try:
        payload = api_get("/api/sources/status")
    except Exception as exc:  # noqa: BLE001
        issues.append(f"❌ 源状态接口不可用：{exc}")
        return {}
    out = {}
    for name, info in payload.items():
        if not isinstance(info, dict):
            continue
        out[name] = {
            "enabled": info.get("enabled"),
            "state": info.get("state"),
            "logged_in": info.get("logged_in"),
            "feed_paused": info.get("feed_paused"),
            "detail": (info.get("detail") or "")[:120],
        }
        if info.get("feed_paused"):
            issues.append(f"⚠️ {name}：feed 已暂停（{info.get('detail','')[:60]}）")
        if info.get("enabled") and not info.get("logged_in") and info.get("state") not in ("ok", "ready"):
            issues.append(f"⚠️ {name}：登录态异常（state={info.get('state')}）")
    return out


def collect_backlog(prev: dict | None, issues: list[str]) -> dict:
    db = DATA_DIR / "openbiliclaw.db"
    out: dict[str, int] = {}
    if not db.exists():
        issues.append("❌ 主库不存在")
        return out
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    for table in BACKLOG_TABLES:
        try:
            out[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except Exception:  # noqa: BLE001
            continue
    conn.close()
    prev_backlog = (prev or {}).get("backlog", {})
    for table, count in out.items():
        label = BACKLOG_LABEL.get(table, table)
        old = prev_backlog.get(table)
        delta = "" if old is None else f"（昨日 {old}，{'±' if count != old else ''}{count - old:+d}）" if count != old else "（昨日持平）"
        if table in ("getnote_pending", "getnote_body_task") and count > 500:
            issues.append(f"⚠️ {label} 积压 {count} 条{delta}")
        if table in ("zhihu_tasks", "dy_tasks", "bili_tasks", "xhs_tasks") and old is not None and count - old > 300:
            issues.append(f"⚠️ {label} 单日新增 {count - old} 条（总 {count}）")
    return out


def collect_subsystems(prev: dict | None, issues: list[str]) -> dict:
    db = DATA_DIR / "openbiliclaw.db"
    state: dict[str, str] = {}
    if db.exists():
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            for key, value in conn.execute("SELECT key, value FROM self_evolution_state"):
                state[key] = value
        finally:
            conn.close()
    now = datetime.now()
    out = {}
    for key, limit in STALE_LIMITS.items():
        raw = state.get(key)
        if not raw:
            continue
        try:
            ts = datetime.fromisoformat(raw)
        except ValueError:
            continue
        hours = (now - ts).total_seconds() / 3600
        out[key] = {"at": raw[:19], "hours_ago": round(hours, 1)}
        if hours > limit:
            issues.append(f"⏰ {STALE_LABEL.get(key, key)} 已 {hours:.0f} 小时未运行（上限 {limit}h）")
    return out


def collect_pm2(prev: dict | None, issues: list[str]) -> dict:
    try:
        raw = subprocess.run(["pm2", "jlist"], capture_output=True, text=True, timeout=30).stdout
        procs = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        issues.append(f"❌ pm2 读取失败：{exc}")
        return {}
    out: dict[str, dict] = {}
    for proc in procs:
        name = proc.get("name", "")
        if not (name.startswith("openbiliclaw") or name == "pool-feed-api"):
            continue
        env = proc.get("pm2_env", {})
        out[name] = {
            "status": env.get("status"),
            "restarts": env.get("restart_time", 0),
            "memory": (proc.get("monit") or {}).get("memory", 0),
        }
        if env.get("status") != "online":
            issues.append(f"❌ {name} 状态异常：{env.get('status')}")
    prev_procs = (prev or {}).get("pm2", {})
    for name, info in out.items():
        old = prev_procs.get(name, {}).get("restarts")
        if old is not None and info["restarts"] - old >= 5:
            issues.append(f"🔁 {name} 昨日以来重启 {info['restarts'] - old} 次（累计 {info['restarts']}）")
    return out


def collect_disk(prev: dict | None, issues: list[str]) -> dict:
    out: dict[str, float] = {}
    if not DATA_DIR.exists():
        return out
    for f in sorted(DATA_DIR.glob("*.db")):
        try:
            out[f.name] = round(f.stat().st_size / 1024 / 1024, 1)
        except OSError:
            continue
    total = sum(out.values())
    out["_total_mb"] = round(total, 1)
    prev_disk = (prev or {}).get("disk_mb", {})
    prev_total = prev_disk.get("_total_mb")
    if prev_total and total - prev_total > DISK_GROWTH_WARN_MB:
        issues.append(f"💾 库体积一日内增长 {total - prev_total:.0f} MB（现共 {total/1024:.2f} GB）")
    if total / 1024 > DISK_TOTAL_WARN_GB:
        issues.append(f"💾 data/ 库总体积 {total/1024:.2f} GB，已超 {DISK_TOTAL_WARN_GB} GB")
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quiet", action="store_true", help="只打印问题行")
    args = parser.parse_args()

    today = date.today().isoformat()
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    prev = read_json_snapshot("pipeline_health", today)

    issues: list[str] = []
    sources = collect_sources(issues)
    backlog = collect_backlog(prev, issues)
    subsystems = collect_subsystems(prev, issues)
    pm2 = collect_pm2(prev, issues)
    disk = collect_disk(prev, issues)

    if not args.quiet:
        online = sum(1 for p in pm2.values() if p["status"] == "online")
        enabled = [n for n, i in sources.items() if i.get("enabled")]
        print(f"🩺 采集线体检 · {today}")
        print(f"源：{len(sources)} 个（启用 {len(enabled)}）｜pm2：{online}/{len(pm2)} online")
        print("\n积压：")
        for table, count in sorted(backlog.items(), key=lambda kv: -kv[1]):
            old = ((prev or {}).get("backlog") or {}).get(table)
            delta = "" if old is None or old == count else f"（{count - old:+d}）"
            print(f"  · {BACKLOG_LABEL.get(table, table)}: {count} {delta}")
        if subsystems:
            print("\n子系统上次运行（小时前）：")
            for key, info in sorted(subsystems.items(), key=lambda kv: -kv[1]["hours_ago"]):
                print(f"  · {STALE_LABEL.get(key, key)}: {info['hours_ago']:.0f}h")
        top = sorted(((k, v) for k, v in disk.items() if not k.startswith("_")), key=lambda kv: -kv[1])[:5]
        print("\n最大的库：")
        for name, mb in top:
            print(f"  · {name}: {mb/1024:.2f} GB" if mb > 1024 else f"  · {name}: {mb:.0f} MB")
        print(f"  （data/ 下 .db 合计 {disk.get('_total_mb', 0)/1024:.2f} GB）")

    if issues:
        print(f"\n🚨 需要关注 {len(issues)} 项：")
        for line in issues:
            print(f"  {line}")
    else:
        print("\n✅ 全部正常，无异常项。")

    (SNAP_DIR / f"pipeline_health_{today}.json").write_text(json.dumps({
        "date": today,
        "sources": sources,
        "backlog": backlog,
        "subsystems": subsystems,
        "pm2": pm2,
        "disk_mb": disk,
        "issues": issues,
    }, ensure_ascii=False, indent=1))

    print("\n" + json.dumps({"date": today, "ok": not issues, "issue_count": len(issues),
                             "issues": issues[:10]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
