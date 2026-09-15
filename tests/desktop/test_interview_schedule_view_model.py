"""面试安排视图模型回归测试（node 实跑，非 grep 源码）。

覆盖的是 2026-09-15 实测出的两个用户可见缺陷：

1. 前端把 ``interview_at`` 按空白切分当时间用 —— ``'2026-08下旬~09初'`` /
   ``'未约面'`` 这类自由文本切出来是垃圾（day 显示成 "下旬~09初"）。
   现在必须先吃后端回填的 ``interview_start_at``，抠不到就显示「待定」。
2. 卡片完全不显示阶段与场次，且「待进行」判定依赖自由文本 ``status`` ——
   真实值形如 ``'已确认参加(9/17周四 19:00 视频面)'``，硬匹配恒不命中。

这些断言在重构前会红：``scheduleDateParts`` / ``countdown`` /
``buildScheduleViewModel`` 当时并不存在（逻辑埋在 interview.js 的 IIFE 里）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from textwrap import dedent

import pytest

_NODE = shutil.which("node")
_MODULE = "./src/openbiliclaw/web/desktop/assets/js/interview-schedule-view.js"


def _run_js(script: str) -> subprocess.CompletedProcess[str]:
    assert _NODE, "node is required"
    return subprocess.run(
        [_NODE, "-e", script],
        cwd=".",
        text=True,
        capture_output=True,
        check=False,
    )


def _js_json(expr: str) -> object:
    """在 node 里求值一段表达式并把结果 JSON 回来（便于 Python 侧断言）。"""
    script = dedent(
        f"""
        const VM = require("{_MODULE}");
        const out = (() => ({expr}))();
        process.stdout.write(JSON.stringify(out === undefined ? null : out));
        """
    )
    result = _run_js(script)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(_NODE is None, reason="node is required for interview schedule view-model tests")
class TestScheduleDateParts:
    """结构化时间优先，自由文本只在结构化列缺失时兜底。"""

    def test_structured_column_wins(self) -> None:
        assert _js_json('VM.scheduleDateParts("2026-09-17 19:00", "2026-09-17 19:00 视频面试")') == {
            "datePart": "2026-09-17",
            "timePart": "19:00",
        }
        assert _js_json('VM.scheduleDateParts("2026-09-04", "2026-09-04 HR面")') == {
            "datePart": "2026-09-04",
            "timePart": "",
        }

    def test_free_text_fallback_recovers_date_prefix(self) -> None:
        """`interview_at` 里的 'YYYY-MM-DD HH:MM 备注' 形态仍要能取到日期。"""
        assert _js_json('VM.scheduleDateParts("", "2026-09-04 HR面")') == {
            "datePart": "2026-09-04",
            "timePart": "",
        }
        assert _js_json('VM.scheduleDateParts("", "2026-09-15 15:00 二面+HR面(一次性走完)")') == {
            "datePart": "2026-09-15",
            "timePart": "15:00",
        }

    def test_unparseable_free_text_yields_no_date(self) -> None:
        """'2026-08下旬~09初' / '未约面' 抠不出日期，必须留空而不是瞎猜。

        旧实现用 split(" ") 切分，会把 '2026-08下旬~09初' 当成一天使用。
        """
        for raw in ("2026-08下旬~09初", "未约面", "", "下周三"):
            assert _js_json(f'VM.scheduleDateParts("", {json.dumps(raw)})') == {
                "datePart": "",
                "timePart": "",
            }, raw


@pytest.mark.skipif(_NODE is None, reason="node is required for interview schedule view-model tests")
class TestCountdown:
    """倒计时必须能注入「今天」——否则测试永远非确定性。"""

    def test_today_tomorrow_and_future(self) -> None:
        assert _js_json('VM.countdown("2026-09-17", "2026-09-17")') == {"text": "今天", "tone": "soon"}
        assert _js_json('VM.countdown("2026-09-17", "2026-09-16")') == {"text": "明天", "tone": "soon"}
        assert _js_json('VM.countdown("2026-09-20", "2026-09-17")') == {"text": "3 天后", "tone": "future"}

    def test_past_or_missing_date_has_no_countdown(self) -> None:
        assert _js_json('VM.countdown("2026-09-10", "2026-09-17")') is None
        assert _js_json('VM.countdown("", "2026-09-17")') is None
        assert _js_json('VM.countdown(undefined, "2026-09-17")') is None

    def test_month_boundary_crossing(self) -> None:
        """跨月/跨年按日历算，不是按 30 天算。"""
        assert _js_json('VM.daysBetween("2026-09-30", "2026-10-01")') == 1
        assert _js_json('VM.daysBetween("2026-12-31", "2027-01-01")') == 1
        assert _js_json('VM.daysBetween("2026-09-17", "2026-09-17")') == 0


@pytest.mark.skipif(_NODE is None, reason="node is required for interview schedule view-model tests")
class TestScheduleViewModel:
    """后端响应 → 渲染用 VM。"""

    _PAYLOAD = {
        "total": 4,
        "upcoming_count": 2,
        "structured": True,
        "stage_counts": {"待面": 2, "面试中": 1, "已结束": 1},
        "upcoming": [
            {
                "company": "HungryStudio",
                "role": "算法工程师",
                "interview_at": "2026-09-17 16:00",
                "interview_start_at": "2026-09-17 16:00",
                "round_note": "视频面试",
                "stage": "待面",
                "status": "已约面(周四16:00)",
                "is_upcoming": True,
            },
            {
                "company": "深圳灵动",
                "role": "(Sr.) Data Scientist-Modeling",
                "interview_at": "2026-09-17 19:00 视频面试",
                "interview_start_at": "2026-09-17 19:00",
                "round_note": "视频面试",
                "stage": "待面",
                "status": "已确认参加(9/17周四 19:00 视频面)",
                "is_upcoming": True,
            },
        ],
        "history": [
            {
                "company": "百度",
                "role": "数据分析",
                "interview_at": "2026-08下旬~09初",
                "interview_start_at": "",
                "stage": "已结束",
                "status": "已结束(输给内转,HR留门:新增HC可直接推进谈薪)",
                "is_upcoming": False,
            },
            {
                "company": "比亚迪",
                "role": "高级算法工程师",
                "interview_at": "2026-09-10 15:00 HR面",
                "interview_start_at": "2026-09-10 15:00",
                "stage": "谈薪中",
                "status": "谈薪中(R1已完成·D1/43K方案)",
                "is_upcoming": False,
            },
        ],
    }

    def _vm(self) -> dict:
        return _js_json(f'VM.buildScheduleViewModel({json.dumps(self._PAYLOAD)}, "2026-09-16")')

    def test_sections_split_and_titles(self) -> None:
        vm = self._vm()
        assert vm["total"] == 4
        assert vm["upcomingCount"] == 2
        assert [s["key"] for s in vm["sections"]] == ["upcoming", "history"]
        assert vm["sections"][0]["title"] == "⏳ 待进行"
        assert [len(s["items"]) for s in vm["sections"]] == [2, 2]
        assert vm["isEmpty"] is False

    def test_upcoming_items_render_date_and_countdown(self) -> None:
        items = self._vm()["sections"][0]["items"]
        assert [i["company"] for i in items] == ["HungryStudio", "深圳灵动"]
        # 9/17 相对注入的今天 9/16 → 「明天」
        assert all(i["countdown"] == {"text": "明天", "tone": "soon"} for i in items)
        assert [i["time"] for i in items] == ["16:00", "19:00"]
        assert [i["day"] for i in items] == ["17", "17"]
        assert all(i["month"] == "9月" for i in items)
        assert all(i["isUpcoming"] is True for i in items)

    def test_unparseable_row_degrades_to_pending_label(self) -> None:
        """'2026-08下旬~09初' 必须显示「待定」且不出现任何伪造日期。"""
        baidu = self._vm()["sections"][1]["items"][0]
        assert baidu["company"] == "百度"
        assert baidu["month"] == "待定"
        assert baidu["day"] == "?"
        assert baidu["datePart"] == ""
        assert baidu["countdown"] is None
        assert baidu["daysUntil"] is None

    def test_stage_and_round_note_surface_to_ui(self) -> None:
        """阶段/场次此前完全没进前端——现在必须带色调地暴露出来。"""
        items = self._vm()["sections"][0]["items"]
        assert items[0]["stage"] == "待面"
        assert items[0]["stageTone"] == "upcoming"
        assert items[0]["roundNote"] == "视频面试"
        # 自由文本 status 仍原样透出（不丢信息，只是不再参与判定）
        assert items[1]["status"] == "已确认参加(9/17周四 19:00 视频面)"
        assert self._vm()["sections"][1]["items"][1]["stageTone"] == "offer"

    def test_chips_follow_canonical_stage_order(self) -> None:
        chips = self._vm()["chips"]
        assert [c["stage"] for c in chips] == ["待面", "面试中", "已结束"]
        assert [c["count"] for c in chips] == [2, 1, 1]

    def test_legacy_payload_without_structured_columns(self) -> None:
        """未跑迁移 001 的老库：无 stage_counts/structured，不能崩。"""
        vm = _js_json(
            'VM.buildScheduleViewModel({total: 1, upcoming: [{company: "X", role: "Y", '
            'interview_at: "2026-09-20 10:00", is_upcoming: true}]}, "2026-09-16")'
        )
        assert vm["chips"] == []
        assert vm["structured"] is False
        assert vm["sections"][0]["items"][0]["month"] == "9月"
        assert vm["sections"][0]["items"][0]["stage"] == ""

    def test_empty_payload(self) -> None:
        vm = _js_json("VM.buildScheduleViewModel({}, '2026-09-16')")
        assert vm["isEmpty"] is True
        assert vm["sections"] == []
        assert vm["total"] == 0
