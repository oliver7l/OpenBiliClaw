#!/usr/bin/env python3
"""TraeWork 单客户端多账号切换工具（档案式登录态交换）。

背景：本机靠多份 /Applications/TRAE SOLO CN*.app + 对应数据目录实现多账号多开，
每个数据目录 4-6GB。本工具把各账号的登录态（storage.json 整体，含 iCubeAuthInfo
加密 blob 与 telemetry 设备标识）捕获成档案，之后在主客户端 "TRAE SOLO CN" 里
一键换登录——设备标识随档案整体迁移，续签要求的「DeviceID 一致 + 签发时私钥」
不被破坏，token 不会 20403。

会话不丢的原理：
  - 会话本体在云端按账号（uid）存储，切回账号即从云端恢复；
  - 本地缓存（state.vscdb 的 local_conversation_share:<uid>: / icubeAiChat/.../<uid>::
    等键）按 uid 命名空间隔离，多账号在同一客户端内共存互不覆盖。

档案位置：~/Library/Application Support/cn.traework.assistant/traework_profiles/
  <uid>/storage.json   # 该账号最近一次的登录态快照
  <uid>/meta.json      # phone / uid / 来源目录 / 捕获时间

用法：
  python3 twa_switch_account.py capture          # 从所有 TRAE* 数据目录捕获/更新档案
  python3 twa_switch_account.py status           # 列出档案与当前登录的账号
  python3 twa_switch_account.py switch <phone>   # 切换到指定手机号账号（自动备份当前登录态）
"""
import base64
import hashlib
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from Crypto.Cipher import AES

# ---- 复用 twa_scan_accounts 的解密逻辑（同一套逆向常量） ----
sys.path.insert(0, str(Path(__file__).parent))
from twa_scan_accounts import AUTH_KEY, decrypt_blob  # noqa: E402

HOME = Path.home()
APP_SUPPORT = HOME / "Library" / "Application Support"
ASSISTANT_DATA = APP_SUPPORT / "cn.traework.assistant"
PROFILES = ASSISTANT_DATA / "traework_profiles"
MAIN_APP = "TRAE SOLO CN"
MAIN_DIR = APP_SUPPORT / "TRAE SOLO CN"
MAIN_STORAGE = MAIN_DIR / "User" / "globalStorage" / "storage.json"
CLIENT_PROCESS_HINT = ("TRAE SOLO CN.app/Contents/MacOS")  # 只匹配主客户端主进程（含 CN 2.app 也不会误匹配——.app 边界）


def current_uid_of(storage_path: Path) -> str | None:
    """解密 storage.json 取 uid；失败返回 None。"""
    try:
        data = json.loads(storage_path.read_text())
        blob = data.get(AUTH_KEY)
        if not isinstance(blob, str):
            return None
        auth = decrypt_blob(blob)
        uid = auth.get("userId")
        return uid.strip() if isinstance(uid, str) and uid.strip() else None
    except Exception:  # noqa: BLE001
        return None


def account_meta(storage_path: Path) -> dict:
    data = json.loads(storage_path.read_text())
    auth = decrypt_blob(data[AUTH_KEY])
    acc = auth.get("account") or {}
    phone = acc.get("nonPlainTextMobile") or acc.get("mobile")
    return {
        "uid": (auth.get("userId") or "").strip() or None,
        "phone": (phone or "").strip() or None,
        "nickname": (acc.get("nickname") or "").strip() or None,
    }


def client_running() -> list[int]:
    """返回主客户端主进程 PID 列表（只匹配主 binary，绝不匹配 AI 沙箱辅助进程）。"""
    r = subprocess.run(["pgrep", "-f", CLIENT_PROCESS_HINT], capture_output=True)
    return [int(x) for x in r.stdout.split() if x.strip().isdigit()]


def quit_client() -> bool:
    """优雅退出；失败返回 False（调用方中止切换）。顺序：osascript → SIGTERM 主进程。
    只有 --force 才在 SIGTERM 无效时 SIGKILL。"""
    pids = client_running()
    if not pids:
        return True
    subprocess.run(["osascript", "-e", f'quit app "{MAIN_APP}"'],
                   capture_output=True)  # 沙箱内常见 -10004 权限违例，失败则降级
    for _ in range(10):
        if not client_running():
            return True
        time.sleep(1)
    for pid in client_running():  # SIGTERM：Electron/VSCode 系会走 hot-exit 保存
        subprocess.run(["kill", "-TERM", str(pid)], capture_output=True)
    for _ in range(10):
        if not client_running():
            return True
        time.sleep(1)
    if "--force" in sys.argv:
        for pid in client_running():
            subprocess.run(["kill", "-9", str(pid)], capture_output=True)
        time.sleep(2)
        return not client_running()
    return False


def save_profile(uid: str, storage_path: Path, source: str) -> Path:
    d = PROFILES / uid
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy2(storage_path, d / "storage.json")
    meta = account_meta(storage_path)
    meta.update({"source_dir": source,
                 "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    return d


def cmd_capture() -> None:
    # 先扫 SOLO 系（切换目标是 SOLO 客户端，快照必须同源）；
    # TRAE CN 是另一个产品（Trae CN IDE），只用来补缺、不覆盖已有 SOLO 快照
    solo_roots = sorted(APP_SUPPORT.glob("TRAE SOLO CN*"))
    other_roots = [p for p in [APP_SUPPORT / n for n in ("TRAE", "Trae TRAE", "TRAE CN")] if p.exists()]
    seen_uids: set = set()
    for root in solo_roots + other_roots:
        sp = root / "User" / "globalStorage" / "storage.json"
        if not sp.exists():
            continue
        try:
            meta = account_meta(sp)
        except Exception as e:  # noqa: BLE001
            print(f"  [!] {root.name}: 解析失败 {e}")
            continue
        uid = meta["uid"]
        if not uid:
            print(f"  [!] {root.name}: 无 uid，跳过")
            continue
        if uid in seen_uids:
            if root in other_roots:
                print(f"  [-] {root.name} → {meta['phone'] or uid}（同账号已有 SOLO 快照，跳过非 SOLO 来源）")
                continue
            print(f"  [✓] {root.name} → 档案 {meta['phone'] or uid}（重复，更新）")
        else:
            print(f"  [✓] {root.name} → 档案 {meta['phone'] or uid}")
        seen_uids.add(uid)
        save_profile(uid, sp, root.name)
    print(f"\n共 {len(seen_uids)} 个账号档案 → {PROFILES}")


def load_profiles() -> dict[str, dict]:
    out = {}
    if not PROFILES.exists():
        return out
    for d in sorted(PROFILES.iterdir()):
        m = d / "meta.json"
        if d.is_dir() and m.exists():
            out[d.name] = json.loads(m.read_text())
    return out


def cmd_status() -> None:
    cur = current_uid_of(MAIN_STORAGE) if MAIN_STORAGE.exists() else None
    profiles = load_profiles()
    print(f"当前主客户端登录 uid：{cur or '（未登录/解析失败）'}")
    print(f"档案数：{len(profiles)}")
    for uid, m in profiles.items():
        mark = " ← 当前登录" if uid == cur else ""
        print(f"  {m.get('phone') or '-':<14} uid={uid}  来源={m.get('source_dir')}  "
              f"捕获={m.get('captured_at')}{mark}")


def clear_stale_cache(main_dir: Path) -> list[str]:
    """切号后清理可能残留旧账号态的缓存（traehop 同款清单）。
    返回实际删除的路径（相对名）。"""
    removed = []
    for rel in ("User/globalStorage/state.vscdb.backup",
                "Cookies-journal", "Network/Cookies-journal"):
        p = main_dir / rel
        if p.exists():
            p.unlink()
            removed.append(rel)
    return removed


def cmd_switch(phone: str) -> None:
    profiles = load_profiles()
    hits = {u: m for u, m in profiles.items() if m.get("phone") == phone}
    if len(hits) != 1:
        avail = ", ".join(sorted((m.get("phone") or u) for m in profiles.values())) or "（无）"
        raise SystemExit(f"目标手机号 {phone} 匹配到 {len(hits)} 个档案；可用：{avail}\n"
                         f"先跑 capture 更新档案。")
    target_uid, target_meta = next(iter(hits.items()))
    if not MAIN_STORAGE.exists():
        raise SystemExit(f"主客户端登录态不存在：{MAIN_STORAGE}")
    cur_uid = current_uid_of(MAIN_STORAGE)
    if cur_uid == target_uid:
        print(f"当前已登录 {phone}，无需切换。")
        return
    print(f"切换：{cur_uid} → {target_uid}（{phone}）")
    if not quit_client():
        raise SystemExit("  客户端未能优雅退出（可能有未保存对话框）——已中止，未改动任何文件。\n"
                         "  请手动保存并退出 TraeWork 后重试；确认无未保存内容可用 --force 强退。")
    # ① 当前登录态存回其档案（保证下次切回它时是最新 token）
    if cur_uid:
        save_profile(cur_uid, MAIN_STORAGE, "TRAE SOLO CN(切换前回存)")
        print(f"  [1/3] 原账号 {cur_uid} 登录态已回存档案")
    # ② 写入目标档案（storage.json 整体替换：auth blob + 设备标识同步迁移）
    shutil.copy2(PROFILES / target_uid / "storage.json", MAIN_STORAGE)
    # 防 iCubeAuthInfo://usertag 残留指向旧账号（traehop 实测需删）
    try:
        data = json.loads(MAIN_STORAGE.read_text())
        if data.pop("iCubeAuthInfo://usertag", None) is not None:
            MAIN_STORAGE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception:  # noqa: BLE001
        pass
    removed = clear_stale_cache(MAIN_DIR)
    ok = current_uid_of(MAIN_STORAGE) == target_uid
    if not ok:
        raise SystemExit("  写入后校验失败：主 storage.json 的 uid 不是目标账号（已中止，未重启客户端）")
    print(f"  [2/3] 目标登录态已写入并校验通过" + (f"（清理缓存：{', '.join(removed)}）" if removed else ""))
    # ③ 重启客户端
    subprocess.run(["open", "-a", MAIN_APP], check=True)
    print(f"  [3/3] {MAIN_APP} 已重启，登录账号 = {phone}")
    print("会话说明：该账号的云端会话登录后自动恢复；此前其它账号的会话在各自账号下，切回即回。")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "capture":
        cmd_capture()
    elif cmd == "status":
        cmd_status()
    elif cmd == "switch":
        if len(sys.argv) < 3:
            raise SystemExit("用法：switch <手机号>（先 status 看可用账号）")
        cmd_switch(sys.argv[2])
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
