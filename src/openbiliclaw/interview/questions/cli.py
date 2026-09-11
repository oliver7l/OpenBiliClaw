"""面试题阅读追踪 CLI。

用法：
    # 查看今日待读
    python -m openbiliclaw.interview.questions.cli today

    # 查看题库统计
    python -m openbiliclaw.interview.questions.cli stats

    # 查看待看队列
    python -m openbiliclaw.interview.questions.cli queue

    # 标记题目已读
    python -m openbiliclaw.interview.questions.cli read <question_id> [--mastery understood] [--notes "笔记"]

    # 标记题目掌握
    python -m openbiliclaw.interview.questions.cli master <question_id>

    # 标记需要复习
    python -m openbiliclaw.interview.questions.cli review <question_id>

    # 查看题目详情
    python -m openbiliclaw.interview.questions.cli show <question_id>

    # 创建阅读计划
    python -m openbiliclaw.interview.questions.cli plan --name "秋招冲刺" --daily 5

    # 查看阅读进度
    python -m openbiliclaw.interview.questions.cli progress
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 需先插入 PROJECT_ROOT 到 sys.path，import 置后
from openbiliclaw.interview.questions.models import MasteryLevel  # noqa: E402
from openbiliclaw.interview.questions.store import InterviewQuestionStore  # noqa: E402

DB_PATH = PROJECT_ROOT / "data" / "interview_questions.db"


def _get_store() -> InterviewQuestionStore:
    return InterviewQuestionStore(DB_PATH)


def cmd_today(args: argparse.Namespace) -> None:
    """查看今日待读。"""
    store = _get_store()
    plan = store.get_active_plan()
    if not plan:
        print(
            "暂无激活的阅读计划，先创建一个："
            "python -m openbiliclaw.interview.questions.cli plan --name '秋招冲刺' --daily 5"
        )
        return
    questions = store.get_today_queue(plan)
    print(f"\n📅 今日待读（{date.today().isoformat()}）— 计划：{plan.name}，目标 {plan.daily_target} 题")
    print("=" * 70)
    for i, q in enumerate(questions, 1):
        mastery = store.get_question_mastery(q.id)
        mastery_icon = {
            MasteryLevel.NOT_STARTED: "⬜",
            MasteryLevel.READING: "📖",
            MasteryLevel.UNDERSTOOD: "✅",
            MasteryLevel.MASTERED: "🏆",
            MasteryLevel.NEED_REVIEW: "🔄",
        }.get(mastery, "⬜")
        diff_stars = "⭐" * q.difficulty
        print(f"{i:2d}. [{q.id:3d}] {mastery_icon} {diff_stars} [{q.category.value:15s}] {q.title[:50]}")
        if q.tags:
            print(f"     标签：{q.tags}")
    print(f"\n共 {len(questions)} 题")
    print("\n标记已读：python -m openbiliclaw.interview.questions.cli read <id>")
    print("标记掌握：python -m openbiliclaw.interview.questions.cli master <id>")


def cmd_stats(args: argparse.Namespace) -> None:
    """查看题库统计。"""
    store = _get_store()
    stats = store.stats()
    print("\n📊 题库统计")
    print("=" * 50)
    print(f"  总题数：{stats.total}")
    print(f"  未开始：{stats.not_started}")
    print(f"  阅读中：{stats.reading}")
    print(f"  已理解：{stats.understood}")
    print(f"  已掌握：{stats.mastered}")
    print(f"  需复习：{stats.need_review}")
    print("\n  按分类：")
    for cat, count in sorted(stats.by_category.items(), key=lambda x: -x[1]):
        print(f"    {cat:20s} {count:3d} 题")
    print("\n  按难度：")
    for diff in sorted(stats.by_difficulty.keys()):
        print(f"    {'⭐' * diff:10s} {stats.by_difficulty[diff]:3d} 题")
    mastery_pct = (stats.mastered + stats.understood) / stats.total * 100 if stats.total else 0
    print(f"\n  整体掌握率：{mastery_pct:.1f}%")


def cmd_queue(args: argparse.Namespace) -> None:
    """查看待看队列。"""
    store = _get_store()
    queue = store.get_queue(limit=100)
    print(f"\n📋 待看队列（共 {len(queue)} 题）")
    print("=" * 70)
    for q, priority, _planned in queue:
        p_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(priority.value, "⚪")
        print(f"  {p_icon} [{q.id:3d}] {'⭐' * q.difficulty} [{q.category.value:15s}] {q.title[:45]}")


def cmd_read(args: argparse.Namespace) -> None:
    """标记已读。"""
    store = _get_store()
    mastery = MasteryLevel(args.mastery) if args.mastery else MasteryLevel.READING
    record = store.mark_read(args.question_id, mastery=mastery, notes=args.notes or "", time_spent_min=args.time or 0)
    q = store.get_question(args.question_id)
    print(f"\n✅ 已标记阅读：[{q.id}] {q.title}")
    print(f"   掌握程度：{mastery.value}")
    print(f"   复习次数：{record.review_count}")
    if args.notes:
        print(f"   笔记：{args.notes}")


def cmd_master(args: argparse.Namespace) -> None:
    """标记掌握。"""
    store = _get_store()
    store.mark_read(args.question_id, mastery=MasteryLevel.MASTERED)
    q = store.get_question(args.question_id)
    print(f"\n🏆 已标记掌握：[{q.id}] {q.title}")


def cmd_review(args: argparse.Namespace) -> None:
    """标记需要复习。"""
    store = _get_store()
    store.mark_read(args.question_id, mastery=MasteryLevel.NEED_REVIEW)
    q = store.get_question(args.question_id)
    print(f"\n🔄 已标记需复习：[{q.id}] {q.title}")


def cmd_show(args: argparse.Namespace) -> None:
    """查看题目详情。"""
    store = _get_store()
    q = store.get_question(args.question_id)
    if not q:
        print(f"题目 {args.question_id} 不存在")
        return
    mastery = store.get_question_mastery(q.id)
    print(f"\n📝 题目详情 [{q.id}]")
    print("=" * 70)
    print(f"  标题：{q.title}")
    print(f"  分类：{q.category.value}")
    print(f"  难度：{'⭐' * q.difficulty}")
    print(f"  来源：{q.source}")
    print(f"  标签：{q.tags}")
    print(f"  掌握：{mastery.value}")
    if q.answer:
        print(f"\n  答案要点：\n{q.answer}")
    records = store.get_records(qid=q.id, limit=5)
    if records:
        print("\n  阅读记录：")
        for r in records:
            print(f"    {r.read_date} - {r.mastery.value}（复习{r.review_count}次）")


def cmd_plan(args: argparse.Namespace) -> None:
    """创建阅读计划。"""
    store = _get_store()
    plan = store.create_plan(
        name=args.name,
        daily_target=args.daily,
        categories=args.categories or "",
        min_difficulty=args.min_diff or 1,
        max_difficulty=args.max_diff or 5,
    )
    print("\n📅 阅读计划已创建")
    print("=" * 50)
    print(f"  名称：{plan.name}")
    print(f"  每日目标：{plan.daily_target} 题")
    print(f"  开始日期：{plan.start_date}")
    if plan.categories:
        print(f"  目标分类：{plan.categories}")
    print(f"  难度范围：{plan.min_difficulty}-{plan.max_difficulty}")
    stats = store.stats()
    total_days = (stats.total + plan.daily_target - 1) // plan.daily_target
    print(f"\n  预计完成：{total_days} 天（共 {stats.total} 题）")


def cmd_progress(args: argparse.Namespace) -> None:
    """查看阅读进度。"""
    store = _get_store()
    plan = store.get_active_plan()
    if not plan:
        print("暂无激活的阅读计划")
        return
    daily = store.get_daily_progress(plan.id, days=7)
    stats = store.stats()
    print(f"\n📈 阅读进度 — {plan.name}")
    print("=" * 60)
    print(f"  每日目标：{plan.daily_target} 题")
    print(f"  已理解/掌握：{stats.understood + stats.mastered} / {stats.total}")
    print("\n  最近 7 天：")
    for d in daily:
        bar_len = min(int(d.questions_read / max(plan.daily_target, 1) * 20), 20)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        status = "✅" if d.questions_read >= plan.daily_target else "⬜"
        print(f"    {d.progress_date} {status} {bar} {d.questions_read:2d}/{plan.daily_target} 题")
    if not daily:
        print("    暂无记录，开始阅读吧！")
    mastery_pct = (stats.mastered + stats.understood) / stats.total * 100 if stats.total else 0
    print(f"\n  整体掌握率：{mastery_pct:.1f}%")


def main() -> None:
    parser = argparse.ArgumentParser(description="面试题阅读追踪系统")
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # today
    p_today = subparsers.add_parser("today", help="查看今日待读")
    p_today.set_defaults(func=cmd_today)

    # stats
    p_stats = subparsers.add_parser("stats", help="查看题库统计")
    p_stats.set_defaults(func=cmd_stats)

    # queue
    p_queue = subparsers.add_parser("queue", help="查看待看队列")
    p_queue.set_defaults(func=cmd_queue)

    # read
    p_read = subparsers.add_parser("read", help="标记已读")
    p_read.add_argument("question_id", type=int, help="题目 ID")
    p_read.add_argument(
        "--mastery",
        choices=["reading", "understood", "mastered", "need_review"],
        default="reading",
        help="掌握程度",
    )
    p_read.add_argument("--notes", type=str, help="阅读笔记")
    p_read.add_argument("--time", type=int, help="花费时间（分钟）")
    p_read.set_defaults(func=cmd_read)

    # master
    p_master = subparsers.add_parser("master", help="标记掌握")
    p_master.add_argument("question_id", type=int, help="题目 ID")
    p_master.set_defaults(func=cmd_master)

    # review
    p_review = subparsers.add_parser("review", help="标记需要复习")
    p_review.add_argument("question_id", type=int, help="题目 ID")
    p_review.set_defaults(func=cmd_review)

    # show
    p_show = subparsers.add_parser("show", help="查看题目详情")
    p_show.add_argument("question_id", type=int, help="题目 ID")
    p_show.set_defaults(func=cmd_show)

    # plan
    p_plan = subparsers.add_parser("plan", help="创建阅读计划")
    p_plan.add_argument("--name", type=str, default="秋招冲刺", help="计划名称")
    p_plan.add_argument("--daily", type=int, default=5, help="每日目标题数")
    p_plan.add_argument("--categories", type=str, default="", help="目标分类（逗号分隔）")
    p_plan.add_argument("--min-diff", type=int, default=1, help="最低难度")
    p_plan.add_argument("--max-diff", type=int, default=5, help="最高难度")
    p_plan.set_defaults(func=cmd_plan)

    # progress
    p_progress = subparsers.add_parser("progress", help="查看阅读进度")
    p_progress.set_defaults(func=cmd_progress)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return
    args.func(args)


if __name__ == "__main__":
    main()
