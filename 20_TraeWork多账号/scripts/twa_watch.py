#!/usr/bin/env python3
"""登录态自动入库守护 —— 让「添加账号」变成 wb-switch 级的无感体验：

1. 捕获：任何账号只要在本机任一 TRAE SOLO CN* 副本里登录过，自动存进档案仓；
2. 合成：accounts.json 里新出现的账号（如助手浏览器授权登录加进来的）若没有档案，
   自动用「现成档案模板 + token」合成一份可切换档案 —— 不需要客户端登录、不需要验证码。

为什么需要它：客户端的 storage.json 同一时刻只保存【一个】账号的登录态，登下一个
账号就把上一个顶掉；而浏览器授权登录的账号从来只有 token、没有客户端登录态。
这个守护每 15s 扫一遍，两条路都自动补齐。

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
from twa_synth import load_accounts, pick_template, profile_uids, synth_one  # noqa: E402

PID_FILE = ASSISTANT_DATA / "twa_watch.pid"
LOG_FILE = ASSISTANT_DATA / "twa_watch.log"
INTERVAL = 15
SYNTH_RETRY_AFTER = 30 * 60  # 合成失败的账号，30 分钟后再试（避免每 15s 撞同一个错）
_failed: dict[str, float] = {}  # uid -> 最早重试时间戳


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


def synth_missing(verbose: bool = True) -> int:
    """accounts.json 里有、档案仓没有的账号 → 自动合成档案。返回本次合成数量。

    只写 traework_profiles/，不碰 accounts.json（避免和助手 App 回写打架）。
    合成失败的账号进 30 分钟冷却，不反复撞。
    """
    n = 0
    try:
        accs = load_accounts()
    except Exception as e:  # noqa: BLE001
        if verbose:
            log(f"[!] accounts.json 读取失败，跳过合成轮：{e}")
        return 0
    missing = [a for a in accs if (a.get("user_id") not in profile_uids()) and a.get("token")]
    if not missing:
        return 0
    try:
        store, tpl_src = pick_template()
    except SystemExit as e:  # 模板缺失等致命情况
        if verbose:
            log(f"[!] 无法合成（{e}）")
        return 0
    now = time.time()
    for acc in missing:
        uid = acc["user_id"]
        if _failed.get(uid, 0) > now:
            continue
        name = acc.get("phone") or acc.get("name") or uid
        try:
            synth_one(acc, store)
        except Exception as e:  # noqa: BLE001
            _failed[uid] = now + SYNTH_RETRY_AFTER
            if verbose:
                log(f"[!] {name} 合成失败（{e}），{SYNTH_RETRY_AFTER // 60} 分钟后重试")
            continue
        _failed.pop(uid, None)
        n += 1
        log(f"[合成] {name} uid={uid} → 新档案（模板 {tpl_src}）。"
            f"菜单里已可选；签到若报 9090，在客户端登录激活一次即可")
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
        n = scan_once() + synth_missing()
        print(f"本轮入库 {n} 份（捕获 + 合成）")
        return
    if running_pid():
        print(f"守护已在运行 pid={running_pid()}，不重复启动。")
        return
    PID_FILE.write_text(str(os.getpid()))
    log(f"守护启动 pid={os.getpid()}，每 {INTERVAL}s 扫一次（捕获+合成），目录："
        + ", ".join(r.name for r in roots()))
    try:
        while True:
            scan_once()
            synth_missing()
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        log("收到中断，退出。")
    finally:
        PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
