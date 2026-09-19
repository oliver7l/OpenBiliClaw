#!/usr/bin/env python3
"""
wb_sync_automations.py — WorkBuddy 全账号定时任务同步

原理：
  ~/.workbuddy/workbuddy.db 是所有账号共用的本地库，
  automations 表靠 owner_user_id 区分归属。
  本脚本把「全账号任务的并集（按 name 去重，最新 updated_at 优先）」
  作为主任务集，同步写入每个账号名下：
    - 主集里有、该账号没有的  → 插入（新 id，owner=目标账号）
    - 主集里有、该账号也有的  → 用主集版本覆盖（保留该账号行的 id）
    - 主集里没有、该账号独有  → 删除（apply 前自动备份整个 db）
  同步完成后，任何账号登录看到的都是同一份定时任务，
  切号不影响调度（同一时刻只有当前账号的任务在跑）。

用法：
  python3 wb_sync_automations.py status        # 查看当前账号与各账号任务数
  python3 wb_sync_automations.py plan          # dry-run：预览将要发生的变更
  python3 wb_sync_automations.py apply         # 备份 db 后执行同步
  python3 wb_sync_automations.py plan --uid <uuid> [--uid ...]   # 只看/只同步部分账号
  python3 wb_sync_automations.py apply --include-paused/--exclude-paused
      # 默认 include：暂停的任务也随主集同步（保持原状态）

安全机制：
  1. apply 前自动备份 workbuddy.db（含 -wal/-shm）到 ~/.workbuddy/automation-backups/
  2. 检测 WorkBuddy 是否在运行：运行中默认拒绝写库（--force 可越过，
     但建议退出 WorkBuddy 后执行，避免内存缓存覆盖写入结果）
  3. 一次性(scheduled_at 已过期)任务视为过期垃圾，不入主集
"""
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime

HOME = os.path.expanduser("~")
DB_PATH = os.path.join(HOME, ".workbuddy", "workbuddy.db")
AUTH_PATH = os.path.join(HOME, "Library", "Application Support",
                         "CodeBuddyExtension", "Data", "Public", "auth",
                         "workbuddy-desktop.info")
SWITCH_ACCOUNTS = os.path.join(HOME, ".wb-switch", "accounts.json")
BACKUP_DIR = os.path.join(HOME, ".workbuddy", "automation-backups")
WAL_PATHS = [DB_PATH + "-wal", DB_PATH + "-shm"]

# 同步行不搬这些列（身份/调度运行态由目标行自己决定）
SKIP_COPY_COLS = {"id", "owner_user_id", "owner_status", "owner_source",
                  "next_run_at", "last_run_at", "deleted_at",
                  "created_at", "updated_at"}


def now_ms():
    return int(time.time() * 1000)


def check_app_running():
    """返回 (是否在运行, 进程描述)。匹配 WorkBuddy 主应用。"""
    try:
        out = subprocess.run(
            ["pgrep", "-fl", r"WorkBuddy\.app"],
            capture_output=True, text=True, timeout=10)
        lines = [l for l in out.stdout.splitlines()
                 if "pgrep" not in l and "/wb_sync_automations" not in l]
        return (len(lines) > 0, "; ".join(lines[:3]))
    except Exception:
        return (False, "")


def current_account():
    """从官方认证文件读当前登录账号。返回 (uid, nickname, phone) 或 None。"""
    try:
        with open(AUTH_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        acct = d.get("account", {})
        return (acct.get("uid"), acct.get("nickname"),
                acct.get("phoneNumber"))
    except Exception:
        return None


def switch_accounts():
    """从 wb-switch 账号清单读全部账号。返回 [(uid, nickname, phone)]。"""
    out = []
    try:
        with open(SWITCH_ACCOUNTS, "r", encoding="utf-8") as f:
            for a in json.load(f):
                acct = (a.get("auth_raw", {}) or {}).get("account", {}) or {}
                # uid 优先取顶层字段，其次 auth_raw.account.uid
                uid = a.get("uid") or acct.get("uid")
                if uid:
                    out.append((uid, a.get("nickname"),
                                acct.get("phoneNumber") or a.get("phone")))
    except Exception:
        pass
    return out


def open_db(readonly=True):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def load_all_automations(conn):
    rows = conn.execute(
        "SELECT * FROM automations WHERE deleted_at IS NULL").fetchall()
    return [dict(r) for r in rows]


def iso_to_ms(s):
    """scheduled_at ISO 字符串 → epoch ms；解析失败返回 0。"""
    if not s:
        return 0
    try:
        t = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:
            dt = dt.astimezone()
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0


def build_master(rows, include_paused=True):
    """全账号并集 → 主任务集（按 name 去重，最新 updated_at 优先）。
    跳过：已过期的一次性任务。返回 {name: row_dict}。"""
    cutoff = now_ms()
    master = {}
    for r in sorted(rows, key=lambda x: x.get("updated_at") or 0,
                    reverse=True):
        name = (r.get("name") or "").strip()
        if not name:
            continue
        if r.get("schedule_type") == "once":
            at_ms = iso_to_ms(r.get("scheduled_at"))
            if at_ms and at_ms < cutoff:
                continue  # 过期的一次性提醒不传播
        if not include_paused and r.get("status") != "ACTIVE":
            continue
        if name not in master:
            master[name] = r
    return master


def owner_label(owner_rows):
    """owner uid → 昵称。"""
    labels = {}
    for uid, nick, _phone in switch_accounts():
        labels[uid] = nick
    for r in owner_rows:
        labels.setdefault(r.get("owner_user_id") or "(无归属)",
                          r.get("owner_user_id") or "(无归属)")
    return labels


def plan(conn, include_paused=True, only_uids=None):
    rows = load_all_automations(conn)
    master = build_master(rows, include_paused)
    by_owner = {}
    for r in rows:
        by_owner.setdefault(r.get("owner_user_id"), []).append(r)

    uids = sorted(set(list(by_owner.keys())
                      + [u for u, _n, _p in switch_accounts()]),
                  key=lambda u: u or "")
    if only_uids:
        uids = [u for u in uids if u in only_uids]
    labels = owner_label(rows)

    changes = {}
    for uid in uids:
        existing = {r["name"].strip(): r for r in by_owner.get(uid, [])}
        ins, upd, dele = [], [], []
        for name, m in master.items():
            if name not in existing:
                ins.append(name)
            else:
                e = existing[name]
                if (e.get("prompt") != m.get("prompt")
                        or e.get("status") != m.get("status")
                        or e.get("rrule") != m.get("rrule")
                        or e.get("scheduled_at") != m.get("scheduled_at")):
                    upd.append(name)
        for name in existing:
            if name not in master:
                dele.append(name)
        changes[uid] = {"label": labels.get(uid, uid),
                        "insert": sorted(ins),
                        "update": sorted(upd),
                        "delete": sorted(dele)}
    return master, changes, labels


def apply(conn, master, changes):
    backup_db()
    n = now_ms()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(automations)")]
    total_i = total_u = total_d = 0
    for uid, ch in changes.items():
        existing = {r["name"].strip(): r
                    for r in load_all_automations(conn)
                    if r.get("owner_user_id") == uid}
        for name in ch["insert"]:
            m = master[name]
            vals = {}
            for c in cols:
                if c in SKIP_COPY_COLS:
                    continue
                vals[c] = m.get(c)
            vals["id"] = str(uuid.uuid4())
            vals["owner_user_id"] = uid
            vals["owner_status"] = "confirmed"
            vals["owner_source"] = "sync"
            vals["next_run_at"] = None
            vals["last_run_at"] = None
            vals["deleted_at"] = None
            vals["created_at"] = n
            vals["updated_at"] = n
            keys = ", ".join(vals.keys())
            ph = ", ".join(["?"] * len(vals))
            conn.execute(f"INSERT INTO automations ({keys}) VALUES ({ph})",
                         list(vals.values()))
            total_i += 1
        for name in ch["update"]:
            m = master[name]
            e = existing[name]
            sets, params = [], []
            for c in cols:
                if c in SKIP_COPY_COLS or c == "name":
                    continue
                sets.append(f"{c} = ?")
                params.append(m.get(c))
            sets.append("updated_at = ?")
            params.append(n)
            params.append(e["id"])
            conn.execute(
                f"UPDATE automations SET {', '.join(sets)} WHERE id = ?",
                params)
            total_u += 1
        for name in ch["delete"]:
            e = existing[name]
            conn.execute("DELETE FROM automations WHERE id = ?", (e["id"],))
            total_d += 1
    conn.commit()
    return total_i, total_u, total_d


def backup_db():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(BACKUP_DIR, f"workbuddy.db.pre-sync-{ts}")
    shutil.copy2(DB_PATH, dst)
    for p in WAL_PATHS:
        if os.path.exists(p):
            shutil.copy2(p, dst + os.path.basename(p).replace(DB_PATH, "-"))
    size = os.path.getsize(dst)
    print(f"  ✓ db 已备份 → {dst} ({size/1024:.0f} KB)")
    return dst


def cmd_status(_args):
    conn = open_db()
    rows = load_all_automations(conn)
    cur = current_account()
    print("== 当前登录账号 ==")
    if cur:
        print(f"  {cur[1]}  ({cur[2]})  uid={cur[0]}")
    else:
        print("  未能读取认证文件")
    print("\n== 各账号任务数（workbuddy.db automations 表）==")
    by_owner = {}
    for r in rows:
        by_owner.setdefault(r.get("owner_user_id"), []).append(r)
    labels = owner_label(rows)
    all_uids = sorted(set(list(by_owner)
                          + [u for u, _n, _p in switch_accounts()]),
                      key=lambda u: u or "")
    for uid in all_uids:
        lst = by_owner.get(uid, [])
        active = sum(1 for r in lst if r.get("status") == "ACTIVE")
        mark = " ← 当前" if cur and uid == cur[0] else ""
        print(f"  {labels.get(uid, uid):<16} {len(lst):>3} 条"
              f"（激活 {active}）{mark}")
    conn.close()


def cmd_plan(args):
    conn = open_db()
    only = set(args.uid) if args.uid else None
    master, changes, labels = plan(conn, args.include_paused, only)
    print(f"== 主任务集：{len(master)} 条（全账号并集，按 name 去重）==")
    for name in sorted(master):
        m = master[name]
        st = "激活" if m.get("status") == "ACTIVE" else "暂停"
        print(f"  [{st}] {name}")
    print(f"\n== 变更计划（{'含' if args.include_paused else '不含'}暂停）==")
    for uid, ch in changes.items():
        n = len(ch["insert"]) + len(ch["update"]) + len(ch["delete"])
        print(f"\n  ◆ {ch['label']}  (+{len(ch['insert'])} 新增"
              f" / ~{len(ch['update'])} 覆盖 / -{len(ch['delete'])} 删除)")
        for x in ch["insert"]:
            print(f"      + {x}")
        for x in ch["update"]:
            print(f"      ~ {x}")
        for x in ch["delete"]:
            print(f"      - {x}")
        if n == 0:
            print("      （无变化）")
    conn.close()


def cmd_apply(args):
    running, desc = check_app_running()
    if running and not args.force:
        print("✗ 检测到 WorkBuddy 正在运行，运行中写库可能被内存缓存覆盖。")
        print("  请先退出 WorkBuddy（本会话所在宿主也要退出），")
        print(f"  然后在终端执行本命令；或加 --force 强行写入。\n  {desc}")
        sys.exit(2)
    conn = open_db()
    only = set(args.uid) if args.uid else None
    master, changes, _ = plan(conn, args.include_paused, only)
    total = sum(len(c["insert"]) + len(c["update"]) + len(c["delete"])
                for c in changes.values())
    if total == 0:
        print("所有账号已是同一份任务集，无需同步。")
        conn.close()
        return
    print(f"开始同步：共 {total} 处变更…")
    i, u, d = apply(conn, master, changes)
    conn.close()
    print(f"  ✓ 完成：+{i} 新增 / ~{u} 覆盖 / -{d} 删除")
    print("  重启 WorkBuddy 后各账号看到的都是同一份定时任务。")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["status", "plan", "apply"])
    ap.add_argument("--uid", action="append", default=[],
                    help="只处理指定 owner_user_id（可多次）")
    ap.add_argument("--exclude-paused", dest="include_paused",
                    action="store_false", default=True,
                    help="主集不含暂停任务")
    ap.add_argument("--force", action="store_true",
                    help="WorkBuddy 运行中也强制写库（不推荐）")
    args = ap.parse_args()
    {"status": cmd_status, "plan": cmd_plan,
     "apply": cmd_apply}[args.command](args)


if __name__ == "__main__":
    main()
