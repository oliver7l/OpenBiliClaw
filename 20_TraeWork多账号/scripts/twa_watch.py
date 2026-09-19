#!/usr/bin/env python3
"""登录态自动捕获守护 —— 任何账号只要在本机任一 TRAE SOLO CN* 副本里登录过，
就自动存进档案仓，不再需要事后手动 capture。

为什么需要它：客户端的 storage.json 同一时刻只保存【一个】账号的登录态，登下一个
账号就把上一个顶掉；不当时捕获，那次登录态就永久丢了（账号能出现在 accounts.json
里，但切不到、签不了）。这个守护每 15s 扫一遍，登录即入库。

用法：
  python3 twa_watch.py            # 前台跑（Ctrl-C 退出）
  python3 twa_watch.py --once     # 只扫一轮，打印结果
  python3 twa_watch.py --status   # 看守护是否在跑 + 最近日志
  python3 twa_watch.py --stop     # 停掉守护
"""
import json
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from twa_switch_account import (  # noqa: E402
    APP_SUPPORT,
    ASSISTANT_DATA,
    PROFILES,
    account_meta,
    save_profile,
)

PID_FILE = ASSISTANT_DATA / "twa_watch.pid"
LOG_FILE = ASSISTANT_DATA / "twa_watch.log"
INTERVAL = 15


def roots() -> list[Path]:
    """所有可能承载客户端登录态的数据目录（含多开实例）。"""
    return sorted(APP_SUPPORT.glob("TRAE SOLO CN*")) + [
        p for p in [APP_SUPPORT / n for n in ("TRAE", "Trae TRAE", "TRAE CN")] if p.exists()
    ]


def log(msg: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    try:
        with LOG_FILE.open("a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def scan_once(verbose: bool = True) -> int:
    """扫一轮，把比档案更新的登录态写进档案仓。返回本次新捕获/更新的数量。"""
    n = 0
    for root in roots():
        sp = root / "User" / "globalStorage" / "storage.json"
        if not sp.exists():
            continue
        try:
            meta = account_meta(sp)
        except Exception as e:  # noqa: BLE001
            if verbose:
                log(f"[!] {root.name}: 解析失败 {e}")
            continue
        uid = meta.get("uid")
        if not uid:
            continue
        saved = PROFILES / uid / "storage.json"
        # copy2 保留 mtime ⇒ 捕获过之后源不比档案新，天然幂等、不会反复写
        if saved.exists() and sp.stat().st_mtime <= saved.stat().st_mtime:
            continue
        kind = "更新" if saved.exists() else "新增"
        save_profile(uid, sp, root.name)
        n += 1
        tag = "（首次进档案仓）" if kind == "新增" else ""
        log(f"[{kind}] {root.name} → {meta.get('phone') or uid} uid={uid}{tag}")
    return n


def running_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return None
    try:
        os.kill(pid, 0)  # 探活
        return pid
    except OSError:
        return None


def cmd_status() -> None:
    pid = running_pid()
    print(f"守护进程：{'运行中 pid=' + str(pid) if pid else '未运行'}")
    print(f"日志：{LOG_FILE}")
    if LOG_FILE.exists():
        lines = LOG_FILE.read_text().splitlines()[-8:]
        print("最近日志：")
        for line in lines:
            print("  " + line)
    profiles = [d.name for d in PROFILES.iterdir() if d.is_dir()] if PROFILES.exists() else []
    print(f"档案仓现有 {len(profiles)} 份档案")


def cmd_stop() -> None:
    pid = running_pid()
    if not pid:
        print("守护没在跑。")
        return
    os.kill(pid, signal.SIGTERM)
    time.sleep(1)
    print(f"已停止 pid={pid}" if not running_pid() else f"pid={pid} 还在，请手动 kill")


def main() -> None:
    if "--status" in sys.argv:
        cmd_status()
        return
    if "--stop" in sys.argv:
        cmd_stop()
        return
    if "--once" in sys.argv:
        n = scan_once()
        print(f"本轮捕获 {n} 份登录态")
        return
    if running_pid():
        print(f"守护已在运行 pid={running_pid()}，不重复启动。")
        return
    PID_FILE.write_text(str(os.getpid()))
    log(f"守护启动 pid={os.getpid()}，每 {INTERVAL}s 扫一次，目录："
        + ", ".join(r.name for r in roots()))
    try:
        while True:
            scan_once()
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        log("收到中断，退出。")
    finally:
        PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
