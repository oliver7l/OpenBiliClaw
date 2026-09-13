"""把拼多多岗位登记进 01_岗位表.csv，并用 InterviewEngine 追加一面日志。

只做两件事（符合模块「只追加写入」原则）：
1. 岗位表 CSV 追加一行（幂等：公司+岗位已存在则跳过）
2. engine.add_log 追加一面面试日志
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openbiliclaw.interview import InterviewEngine, resolve_root

ROOT = resolve_root("")
CSV_PATH = Path(ROOT) / "_系统_知识库引擎" / "数据" / "01_岗位表.csv"

JOB_ROW = {
    "公司": "拼多多",
    "岗位": "AI算法工程师(电商推荐)",
    "面试时间": "2026-09-11 一面 / 待二面",
    "状态": "进行中",
    "主打方向": "电商推荐/重排生成/精排价值LTV",
    "备战目录": "03_岗位弹药库/拼多多-面试准备/",
    "简历版本": "简历-腾讯版",
    "备注": "团队约20人(精排价值含LTV 3-4/重排生成4-5/全链路排查2-3/新场景发券3-5);一面已问发券场景题+Coding数正方形+实验管理;二面预测见03_速成包/拼多多_二面预测题库.md",
}

LOG_POINTS = (
    "面试官负责精排价值(含LTV)/重排生成/全链路;"
    "问发券场景题+Coding数正方形+实验管理;"
    "面试官提搜索不做纯销量排序走约束排序规划;"
    "Gap=生成式重排+购买链路建模"
)


def upsert_job_row() -> bool:
    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        if r.get("公司") == "拼多多":
            print("岗位表已存在拼多多，跳过")
            return False
    with CSV_PATH.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writerow(JOB_ROW)
    print(f"已追加岗位表: {CSV_PATH}")
    return True


def main() -> None:
    print(f"知识库根目录: {ROOT}")
    upsert_job_row()
    eng = InterviewEngine(ROOT)
    seq = eng.add_log("拼多多", "一面", LOG_POINTS)
    print(f"已追加面试日志，序号 {seq}")


if __name__ == "__main__":
    main()
