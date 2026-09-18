#!/usr/bin/env python3
"""TraeWork 账号切换交互菜单（双击 .command 或终端运行）。

逻辑复用 twa_switch_account：档案式登录态交换，会话云端按账号存、切回即恢复。
"""
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from twa_switch_account import (  # noqa: E402
    MAIN_STORAGE,
    cmd_capture,
    client_running,
    current_uid_of,
    load_profiles,
    quit_client,
    save_profile,
)
import shutil  # noqa: E402
import subprocess  # noqa: E402

MAIN_APP = "TRAE SOLO CN"


def do_switch(target_uid: str, meta: dict) -> None:
    phone = meta.get("phone") or target_uid
    if not MAIN_STORAGE.exists():
        raise SystemExit("主客户端登录态不存在")
    cur_uid = current_uid_of(MAIN_STORAGE)
    if cur_uid == target_uid:
        print(f"当前已登录 {phone}，无需切换。")
        return
    print(f"\n切换 → {phone}（客户端会重启一次）")
    if not quit_client():
        print("⚠️ 客户端未能退出（可能有未保存对话框）。请手动保存并退出 TraeWork 后重试。")
        return
    if cur_uid:
        save_profile(cur_uid, MAIN_STORAGE, "TRAE SOLO CN(切换前回存)")
        print(f"  [1/3] 原账号登录态已回存档案")
    profiles_dir = Path.home() / "Library/Application Support/cn.traework.assistant/traework_profiles"
    shutil.copy2(profiles_dir / target_uid / "storage.json", MAIN_STORAGE)
    if current_uid_of(MAIN_STORAGE) != target_uid:
        print("⚠️ 写入校验失败，已中止（未重启客户端）。")
        return
    print("  [2/3] 目标登录态已写入并校验通过")
    subprocess.run(["open", "-a", MAIN_APP], check=True)
    print(f"  [3/3] 已重启，当前登录 = {phone}")


def main() -> None:
    while True:
        cur = current_uid_of(MAIN_STORAGE) if MAIN_STORAGE.exists() else None
        profiles = load_profiles()
        print("\n========== TraeWork 账号切换 ==========")
        items = list(profiles.items())
        for i, (uid, m) in enumerate(items, 1):
            mark = "  ← 当前登录" if uid == cur else ""
            print(f"  {i}. {m.get('phone') or '-':<14} 来源={m.get('source_dir','?')}{mark}")
        print("  r. 重新捕获全部账号档案（多开副本还在时可刷新最新 token）")
        print("  q. 退出")
        try:
            choice = input("选择: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if choice == "q":
            return
        if choice == "r":
            cmd_capture()
            continue
        if choice.isdigit() and 1 <= int(choice) <= len(items):
            uid, m = items[int(choice) - 1]
            if uid == cur:
                print("已是当前账号。")
                continue
            do_switch(uid, m)
        else:
            print("无效输入。")


if __name__ == "__main__":
    main()
