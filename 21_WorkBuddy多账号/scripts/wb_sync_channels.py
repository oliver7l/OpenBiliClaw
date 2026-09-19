#!/usr/bin/env python3
"""
wb_sync_channels.py — WorkBuddy 全账号消息通道统一（通用版，含微信 ClawBot）

背景：
  settings.json 有两层通道配置：
    claw.channels.*                 全局层：所有账号共用（feishu / yuanbao / wechatmp 无凭据态）
    claw.users.<uid>.channels.*     账号层：per-user 覆盖（weixinClawBot 各账号独立 bot、
                                    wecomaibot 仅个别账号有）
  账号层覆盖全局层。凡 per-user 配置不一致的通道，切号后表现就不同。

  通道凭据（botToken/appSecret/botSecret）都属于**外部应用的 bot**，与 WorkBuddy
  账号无关 ⇒ 把每个通道「最完整的配置」统一写入所有账号名下，切号即无感。

  通道选择规则：enabled=True 优先，其次取字段最多（含凭据）的那份。

用法：
  python3 wb_sync_channels.py plan     # 预览各通道在各账号的现状与统一方案
  python3 wb_sync_channels.py apply    # 备份 settings.json 后统一
  python3 wb_sync_channels.py apply --channel weixinClawBot   # 只统一某个通道

注意：
  - per-user 的 conversationBindings / requestDeliveries（会话路由）不动。
  - WorkBuddy 运行中可能用内存态覆写 settings.json，建议退出后执行（--force 越过）。
  - 多实例同跑会争抢同一 bot 的消息轮询，同时只开一个 WorkBuddy。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime

HOME = os.path.expanduser("~")
SETTINGS = os.path.join(HOME, ".workbuddy", "settings.json")
BACKUP_DIR = os.path.join(HOME, ".workbuddy", "automation-backups")


def check_app_running():
    try:
        out = subprocess.run(["pgrep", "-fl", r"WorkBuddy\.app"],
                             capture_output=True, text=True, timeout=10)
        return len([l for l in out.stdout.splitlines() if "pgrep" not in l]) > 0
    except Exception:
        return False


def cred_score(cfg):
    """凭据丰富度：字段越多越可能含凭据。"""
    cred_keys = {"botToken", "appSecret", "botSecret", "appId", "botId",
                 "channelId", "appKey", "encryptKey", "verificationToken"}
    return sum(1 for k in cfg if k in cred_keys)


def cursor_mtime(cfg):
    """轮询型 bot 的活跃度：claw-state/weixin/<botId前缀>_im.bot.cursor.json mtime。"""
    bot_id = cfg.get("channelId", "")
    if not bot_id:
        return 0.0
    prefix = bot_id.split("@")[0]
    p = os.path.join(HOME, ".workbuddy", "claw-state", "weixin",
                     f"{prefix}_im.bot.cursor.json")
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0.0


def best_of(cands):
    """cands: [(uid, cfg)] → enabled 优先 → 凭据最全 → cursor 最新（越活跃越好）。"""
    return max(cands, key=lambda x: (bool(x[1].get("enabled")),
                                     cred_score(x[1]),
                                     cursor_mtime(x[1])))


def analyze(settings):
    claw = settings.get("claw", {})
    users = claw.get("users", {})
    per_chan = {}   # name -> [(uid, cfg)]
    for uid, uv in users.items():
        for name, cfg in (uv.get("channels", {}) or {}).items():
            if isinstance(cfg, dict):
                per_chan.setdefault(name, []).append((uid, cfg))
    global_chan = {k: v for k, v in (claw.get("channels", {}) or {}).items()
                   if isinstance(v, dict)}
    return per_chan, global_chan


def cmd_status(_args):
    settings = json.load(open(SETTINGS, encoding="utf-8"))
    claw = settings.get("claw", {})
    # 当前登录账号
    auth = os.path.join(HOME, "Library", "Application Support",
                        "CodeBuddyExtension", "Data", "Public", "auth",
                        "workbuddy-desktop.info")
    cur = None
    try:
        acct = json.load(open(auth, encoding="utf-8")).get("account", {})
        cur = acct.get("uid")
        print(f"当前登录：{acct.get('nickname')} ({acct.get('phoneNumber')})")
    except Exception:
        print("当前登录：未能读取认证文件")
    print("\n== 账号层通道矩阵 ==")
    chans = sorted({c for uv in claw.get("users", {}).values()
                    for c in (uv.get("channels", {}) or {})})
    users = claw.get("users", {})
    print(f"  {'账号':<14}", *[f"{c[:12]:<14}" for c in chans])
    for uid, uv in users.items():
        ch = uv.get("channels", {})
        mark = " ←当前" if cur and uid == cur else ""
        cells = [("✓" if c in ch else "-") for c in chans]
        print(f"  {uid[:8]:<14}", *[f"{x:<14}" for x in cells], mark)
    print("\n== 全局层 ==", sorted((claw.get("channels", {}) or {}).keys()))


def cmd_plan(args):
    settings = json.load(open(SETTINGS, encoding="utf-8"))
    per_chan, global_chan = analyze(settings)
    print("== 各通道现状（账号层）==")
    for name in sorted(per_chan):
        cands = per_chan[name]
        cfgs = {json.dumps(c, sort_keys=True) for _u, c in cands}
        mark = "（各账号一致）" if len(cfgs) == 1 else "（有差异 → 需统一）"
        print(f"  [{name}] {len(cands)} 个账号有配置 {mark}")
        if len(cfgs) > 1:
            for uid, c in cands:
                print(f"      {uid[:8]}: channelId={c.get('channelId','-')} "
                      f"enabled={c.get('enabled')} 字段{len(c)}个")
    print(f"\n== 全局层 == {sorted(global_chan.keys())}")
    print("（全局层通道可能只对 legacyOwnerUid 生效；"
          "apply --include-global 会把它们写进每个账号层）")


def cmd_apply(args):
    if check_app_running() and not args.force:
        print("✗ WorkBuddy 正在运行，请退出后执行或加 --force。")
        sys.exit(2)
    settings = json.load(open(SETTINGS, encoding="utf-8"))
    per_chan, global_chan = analyze(settings)
    users = settings["claw"]["users"]
    n_users = len(users)

    todo = {}
    targets = [args.channel] if args.channel else sorted(per_chan)
    for name in targets:
        cands = per_chan.get(name, [])
        if not cands:
            print(f"  - {name}: 没有任何账号配置，跳过")
            continue
        if len(cands) == n_users and len(
                {json.dumps(c, sort_keys=True) for _u, c in cands}) == 1:
            print(f"  - {name}: 全部账号已一致，跳过")
            continue
        todo[name] = best_of(cands)

    # 全局层通道 → 铺进每个账号层（保证任何账号登录都显式启用）
    if args.include_global:
        for name, cfg in global_chan.items():
            holders = per_chan.get(name, [])
            if len(holders) == n_users:
                continue  # 账号层已全覆盖
            todo[name] = ("global", cfg)

    if not todo:
        print("所有通道都已一致，无需同步。")
        return

    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(BACKUP_DIR, f"settings.json.pre-channels-{ts}")
    shutil.copy2(SETTINGS, dst)
    print(f"  ✓ settings.json 已备份 → {dst}")

    for name, (src_uid, src_cfg) in todo.items():
        for uid, uv in users.items():
            uv.setdefault("channels", {})[name] = dict(src_cfg)
        print(f"  ✓ [{name}] 来源 {src_uid} → 写入全部 {n_users} 个账号"
              f"（channelId={src_cfg.get('channelId','-')}）")

    tmp = SETTINGS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SETTINGS)
    os.chmod(SETTINGS, 0o600)
    print("完成。重启 WorkBuddy 后生效。")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["status", "plan", "apply"])
    ap.add_argument("--channel", help="只统一指定通道（如 weixinClawBot）")
    ap.add_argument("--include-global", action="store_true",
                    help="把全局层通道（feishu/yuanbao 等）也写进每个账号层")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    {"status": cmd_status, "plan": cmd_plan, "apply": cmd_apply}[args.command](args)


if __name__ == "__main__":
    main()
