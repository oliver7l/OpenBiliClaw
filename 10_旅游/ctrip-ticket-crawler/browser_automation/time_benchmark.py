"""
time_benchmark.py - 测量当前爬虫（事件等待版）单次查询各阶段耗时

测量 FlightCrawler 实际实现的性能：首次查询（含会话建立）与后续查询的耗时。
"""
import logging
import sys
import time
import os

sys.path.insert(0, os.path.dirname(__file__))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

from flight_crawler import FlightCrawler

QUERIES = [
    {"dep": "PEK", "arr": "SHA", "date": "2026-09-01"},
    {"dep": "SHA", "arr": "PEK", "date": "2026-09-05"},
]


def main():
    print("=== 事件等待版爬虫耗时基准测试 ===\n")
    timings = []

    with FlightCrawler(headless=True) as crawler:
        for i, q in enumerate(QUERIES):
            t0 = time.time()
            result = crawler.search(dep=q["dep"], arr=q["arr"], date=q["date"])
            dt = time.time() - t0
            timings.append(dt)
            tag = "首次查询(含会话建立)" if i == 0 else "后续查询(会话已建立)"
            print(
                f"{tag}: {dt:5.2f} 秒 | {q['dep']}->{q['arr']} "
                f"{'OK' if result.success else 'FAIL: ' + result.error} "
                f"({len(result.flights)} 个航班)"
            )

    print("\n=== 汇总 ===")
    print(f"首次查询: {timings[0]:.2f} 秒")
    if len(timings) > 1:
        print(f"后续查询: {timings[1]:.2f} 秒/次（会话复用，无首页开销）")


if __name__ == "__main__":
    main()
