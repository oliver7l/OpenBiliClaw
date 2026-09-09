#!/usr/bin/env python3
"""
旁路锁压力诊断 db_lock_probe.py（零风险：只读 + 瞬时写锁回滚，不写任何业务数据）

用途：系统"经常锁"时，量化 SQLite 写锁竞争压力，定位是否有连接长时间持锁。
原理：周期性对目标库执行 `BEGIN IMMEDIATE … ROLLBACK`（秒级瞬时写锁测试），
统计每次拿到写锁的等待时长 / 是否超过阈值 → 判断库此时是否处于锁竞争高峰。

用法：
  python3 scripts/db_lock_probe.py                 # 探测全部已知库，跑 60s，每 2s 一次
  python3 scripts/db_lock_probe.py --duration 300   # 跑 5 分钟
  python3 scripts/db_lock_probe.py --interval 1 --max-wait 0.5 --db data/openbiliclaw.db
  python3 scripts/db_lock_probe.py --paths data/openbiliclaw.db,data/pool.db
"""
import argparse
import os
import sqlite3
import sys
import time


def _known_paths():
    cands = []
    for rel in ("data/openbiliclaw.db", "data/pool.db"):
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), rel)
        if os.path.exists(p):
            cands.append(p)
    return cands


def probe(path: str, duration: float, interval: float, max_wait: float) -> int:
    conn = sqlite3.connect(path, timeout=max_wait * 2, check_same_thread=False)
    try:
        conn.execute(f"PRAGMA busy_timeout = {int(max_wait * 1000)}")
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        # 若库目前被独占锁着，连读 journal_mode 都可能立刻失败，单独处理
    except sqlite3.OperationalError as e:
        print(f"\n[{path}] 无法读取（数据库文件可能被独占锁定）: {e}")
        return 1

    ok = slow = locked = 0
    total_wait = 0.0
    max_observed = (0.0, 0.0)  # (最长等待ms, 该次发生时刻)
    print(f"\n[{path}] journal_mode={mode}  duration={duration:.0f}s  "
          f"interval={interval:.0f}s  max_wait={max_wait*1000:.0f}ms")
    print("=" * 62)
    start = time.time()
    while time.time() - start < duration:
        t0 = time.perf_counter()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("ROLLBACK")
            wait_ms = (time.perf_counter() - t0) * 1000
            total_wait += wait_ms
            ok += 1
            if wait_ms > max_observed[0]:
                max_observed = (wait_ms, time.time())
            if wait_ms > max_wait * 1000:
                slow += 1
                ts = time.strftime("%H:%M:%S", time.localtime())
                print(f"  [{ts}] 拿写锁偏慢: {wait_ms:.0f}ms > 阈值")
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() or "busy" in str(e).lower():
                locked += 1
                ts = time.strftime("%H:%M:%S", time.localtime())
                print(f"  [{ts}] 写锁获取失败(busy_timeout内未拿到): {e}")
        time.sleep(interval)

    if ok == 0:
        print(f"[{path}] 本轮完全无法取得写锁，锁竞争极严重")
        return 1
    print("-" * 62)
    print(f"[{path}] 采样 {ok} 次 | 平均等待 {total_wait/ok:.1f}ms | "
          f"最大等待 {max_observed[0]:.0f}ms | 超阈 {slow} 次 | 获取失败 {locked} 次")
    if locked or slow:
        print(f"[{path}] 结论: 存在锁竞争 → 期间有连接长时间持锁或写频率过高")
    else:
        print(f"[{path}] 结论: 当前时段写锁顺畅，未见明显竞争")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="SQLite 写锁压力旁路诊断")
    ap.add_argument("--paths", default="", help="逗号分隔的 db 路径；缺省自动探测已知库")
    ap.add_argument("--duration", type=float, default=60, help="探测时长(秒)")
    ap.add_argument("--interval", type=float, default=2, help="每次探测间隔(秒)")
    ap.add_argument("--max-wait", type=float, default=1.5, help="写锁等待报警阈值(秒)")
    args = ap.parse_args()

    paths = [p.strip() for p in args.paths.split(",") if p.strip()]
    if not paths:
        paths = _known_paths()
    if not paths:
        print("未找到已知数据库，请用 --paths <db1,db2> 指定")
        return 1

    rc = 0
    for p in paths:
        if not os.path.exists(p):
            print(f"[跳过] 不存在: {p}")
            continue
        rc |= probe(p, args.duration, args.interval, args.max_wait)
    return rc


if __name__ == "__main__":
    sys.exit(main())
