#!/usr/bin/env python3
"""
wb_sync_wechat.py — WorkBuddy 全账号微信通道（ClawBot）统一

背景：
  settings.json 的 claw.users.<uid>.channels.weixinClawBot 是每个账号
  独立绑定的微信 bot（各自的 botToken）。没配的账号（如周英/19942328729）
  会回退到 claw.channels.weixinClawBot 的旧全局 bot——通常 token 已失效，
  表现为"切号后微信通道失效"。

  botToken 属于微信侧的 bot，与 WorkBuddy 账号无关，且所有 bot 绑定的是
  同一个个人微信 ⇒ 把「最新活跃的 bot 配置」统一写入所有账号名下，
  即可实现切号后微信通道无感。

用法：
  python3 wb_sync_wechat.py plan     # 显示各账号当前 bot 与将采用的源 bot
  python3 wb_sync_wechat.py apply    # 备份 settings.json 后统一写入
  python3 wb_sync_wechat.py apply --bot <channelId>   # 指定源 bot

源 bot 选择规则：
  在所有候选（各账号 per-user 配置 + 旧全局配置）中，
  取 claw-state/weixin/<botId>_im.bot.cursor.json 修改时间最新的那个
  （cursor 越新 = 这个 bot 最近真的在收发消息）。

注意：
  - 多个账号共用同一个 bot 后，微信消息会路由到「当前登录的账号」。
  - WorkBuddy 运行中可能用内存态覆写 settings.json，建议退出后执行
    （apply 会检测并提示；--force 越过）。改完重启 WorkBuddy 生效。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime

HOME = os.path.expanduser("~")
SETTINGS = os.path.join(HOME, ".workbuddy", "settings.json")
CURSOR_DIR = os.path.join(HOME, ".workbuddy", "claw-state", "weixin")
BACKUP_DIR = os.path.join(HOME, ".workbuddy", "automation-backups")


def now_str():
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def cursor_mtime(bot_id):
    """cursor 文件名是 botId 的 @ 前缀，如 a05ebd7f574b_im.bot.cursor.json"""
    if not bot_id:
        return 0
    prefix = bot_id.split("@")[0]
    p = os.path.join(CURSOR_DIR, f"{prefix}_im.bot.cursor.json")
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0


def fmt_ts(ms_or_s):
    if not ms_or_s:
        return "无 cursor"
    return datetime.fromtimestamp(ms_or_s).strftime("%m-%d %H:%M")


def check_app_running():
    try:
        out = subprocess.run(["pgrep", "-fl", r"WorkBuddy\.app"],
                             capture_output=True, text=True, timeout=10)
        lines = [l for l in out.stdout.splitlines() if "pgrep" not in l]
        return len(lines) > 0
    except Exception:
        return False


def collect(settings):
    """返回 [(位置标签, 配置dict)]。位置形如 user:<uid8> 或 legacy-global。"""
    out = []
    claw = settings.get("claw", {})
    for uid, uv in claw.get("users", {}).items():
        w = (uv.get("channels", {}) or {}).get("weixinClawBot")
        if w and w.get("botToken"):
            out.append((f"user:{uid[:8]}", w))
    g = claw.get("channels", {}).get("weixinClawBot")
    if g and g.get("botToken"):
        out.append(("legacy-global", g))
    return out


def pick_best(cands):
    return max(cands, key=lambda x: cursor_mtime(x[1].get("channelId", "")))


def cmd_plan(_args):
    settings = json.load(open(SETTINGS, encoding="utf-8"))
    cands = collect(settings)
    print("== 现有微信 bot 配置 ==")
    for loc, w in cands:
        print(f"  {loc:<16} {w.get('channelId')}  "
              f"enabled={w.get('enabled')}  cursor={fmt_ts(cursor_mtime(w.get('channelId','')))}")
    best_loc, best = pick_best(cands)
    print(f"\n将统一采用：{best_loc} 的 {best.get('channelId')}（cursor 最新）")
    claw = settings.get("claw", {})
    print("\n== 将写入的位置 ==")
    for uid in claw.get("users", {}):
        has = (claw["users"][uid].get("channels", {}) or {}).get("weixinClawBot")
        print(f"  user:{uid[:8]}  {'覆盖' if has else '新增'}")
    print("  legacy-global 兜底：覆盖")


def cmd_apply(args):
    if check_app_running() and not args.force:
        print("✗ WorkBuddy 正在运行，可能用内存态覆写 settings.json。"
              "请退出后执行，或加 --force。")
        sys.exit(2)
    settings = json.load(open(SETTINGS, encoding="utf-8"))
    cands = collect(settings)
    if args.bot:
        match = [(l, w) for l, w in cands if w.get("channelId") == args.bot]
        if not match:
            print(f"✗ 找不到 bot {args.bot}")
            sys.exit(1)
        src_loc, src = match[0]
    else:
        src_loc, src = pick_best(cands)
    print(f"源 bot：{src_loc} → {src.get('channelId')}")

    os.makedirs(BACKUP_DIR, exist_ok=True)
    dst = os.path.join(BACKUP_DIR, f"settings.json.pre-wechat-{now_str()}")
    shutil.copy2(SETTINGS, dst)
    print(f"  ✓ settings.json 已备份 → {dst}")

    claw = settings.setdefault("claw", {})
    n = 0
    for uid, uv in claw.get("users", {}).items():
        ch = uv.setdefault("channels", {})
        old = ch.get("weixinClawBot", {}).get("channelId")
        ch["weixinClawBot"] = dict(src)
        n += 1
        print(f"  ✓ user:{uid[:8]}  {old or '(无)'} → {src.get('channelId')}")
    old_g = claw.setdefault("channels", {}).get("weixinClawBot", {}).get("channelId")
    claw["channels"]["weixinClawBot"] = dict(src)
    print(f"  ✓ legacy-global  {old_g or '(无)'} → {src.get('channelId')}")

    tmp = SETTINGS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SETTINGS)
    os.chmod(SETTINGS, 0o600)
    print(f"完成：共更新 {n} 个账号 + 全局兜底。重启 WorkBuddy 后生效。")
    print("提示：切号后微信消息将路由到当前登录账号；多个实例同时在线会争抢同一 bot 的消息。")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["plan", "apply"])
    ap.add_argument("--bot", help="指定源 bot channelId（如 a05ebd7f574b@im.bot）")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    {"plan": cmd_plan, "apply": cmd_apply}[args.command](args)


if __name__ == "__main__":
    main()
