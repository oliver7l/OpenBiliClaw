#!/usr/bin/env python3
"""每日自动签到守护（交给 pm2 托管，常驻）：
- 启动时先跑一轮（幂等：已签到就返回已签，无副作用）；
- 之后每天 09:00 自动跑全部账号的签到 + 余额刷新；
- 报告写 logs/checkin_latest.txt，并按日期留档 logs/checkin_YYYY-MM-DD.txt；
- 精简结果打进 stdout（= pm2 logs trae-checkin）。

用法：
  python3 twa_checkin_daily.py           # 常驻
  python3 twa_checkin_daily.py --once    # 手动立即跑一轮（不常驻）
"""
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent
LOGS = HERE.parent / "logs"
CHECKIN = HERE / "twa_checkin.py"
DAILY_AT = (9, 0)  # 每天几点几分执行


def run_once(trigger: str) -> None:
    ts = datetime.now().strftime("%F %T")
    p = subprocess.run(
        [sys.executable, str(CHECKIN), "all"],
        capture_output=True, text=True, timeout=600,
    )
    out = (p.stdout + p.stderr).strip()
    LOGS.mkdir(parents=True, exist_ok=True)
    report = f"# TraeWork 自动签到 · {ts}（触发：{trigger}）\n\n{out or '（无输出）'}\n"
    (LOGS / "checkin_latest.txt").write_text(report)
    (LOGS / f"checkin_{datetime.now():%F}.txt").write_text(report)
    bad = ("鉴权失败" in out) or ("签到失败" in out) or (p.returncode != 0)
    print(f"[{ts}] trigger={trigger} 结果={'有异常，看报告文件' if bad else 'OK'}", flush=True)
    for line in out.splitlines():
        s = line.strip()
        if ("签到" in s or "→" in s) and s:
            print("   " + s[:100], flush=True)


def next_run() -> float:
    now = datetime.now()
    target = now.replace(hour=DAILY_AT[0], minute=DAILY_AT[1], second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target.timestamp()


if __name__ == "__main__":
    if "--once" in sys.argv:
        run_once("手动 --once")
        sys.exit(0)
    print(f"[{datetime.now():%T}] TraeWork 签到守护启动：每天 {DAILY_AT[0]:02d}:{DAILY_AT[1]:02d} 执行，"
          f"报告 → {LOGS}/checkin_latest.txt", flush=True)
    run_once("守护启动")
    while True:
        t = next_run()
        print(f"[{datetime.now():%T}] 下次执行 {datetime.fromtimestamp(t):%F %T}"
              f"（等待 {(t - time.time()) / 3600:.1f}h）", flush=True)
        time.sleep(max(t - time.time(), 1))
        run_once(f"定时 {DAILY_AT[0]:02d}:{DAILY_AT[1]:02d}")
