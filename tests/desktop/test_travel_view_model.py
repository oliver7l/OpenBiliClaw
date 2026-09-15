"""旅行（✈️ 旅行 tab）视图模型回归测试（node 实跑，非 grep 源码）。

覆盖 2026-09-15 实测出的用户可见缺陷：桌面端 ``app.js`` 的
``loadTravelFlights()`` 读的字段名与后端 ``travel/routes.py`` 的
``get_flights()`` **完全对不上** —

| 前端读              | 后端实际返回                    | 后果                     |
|---------------------|---------------------------------|--------------------------|
| ``a.price/a.drop``  | ``lowest_price``/``vs_baseline.diff`` | 降价提醒渲染 ``¥undefined`` |
| ``a.flight``        | ``lowest_flight``               | 航班号恒空               |
| ``r.departure_time``| ``lowest_departure``            | 航班时刻恒空             |
| ``r.child_price``   | ``child_fare``                  | 儿童价恒不显示           |

另外 ``lowest_departure`` 存的是**完整时间戳**（``'2026-10-02 21:00:00'``），
原样拼进卡片会把整串日期时间糊上去，故 ``clockOf()`` 必须压成 ``'21:00'``。

这些断言在修复前会红：``travel-view.js`` 当时并不存在（那段逻辑埋在
``app.js`` 的 IIFE 里，只能靠 grep 源码去"测"，而 grep 对字段名漂移没有鉴别力）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from textwrap import dedent

import pytest

_NODE = shutil.which("node")
_MODULE = "./src/openbiliclaw/web/desktop/assets/js/travel-view.js"

# 取自 data/travel/ctrip-ticket-crawler/our_routes_results.json 的真实形状
# （经后端 get_flights() 加工后的 route_info，字段名以此为契约）。
_ROUTE = {
    "route": "TYN-URC",
    "dep": "TYN",
    "arr": "URC",
    "dep_city": "太原",
    "arr_city": "乌鲁木齐",
    "date": "2026-10-02",
    "success": True,
    "baseline": 670,
    "flight_count": 3,
    "lowest_price": 620,
    "lowest_flight": "HU7836",
    "lowest_departure": "2026-10-02 21:00:00",
    "lowest_arrival": "2026-10-03 00:50:00",
    "lowest_airline": "海南航空",
    "adult_fare": 500,
    "child_fare": 500.0,
    "baggage_kg": 20,
    "seat_count": 0,
    "vs_baseline": {"diff": 50, "pct": 7.5, "alert": False},
}

_ROUTE_ALERT = {**_ROUTE, "lowest_price": 410, "vs_baseline": {"diff": 260, "pct": 38.8, "alert": True}}


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
        const ROUTE = {json.dumps(_ROUTE, ensure_ascii=False)};
        const ALERT = {json.dumps(_ROUTE_ALERT, ensure_ascii=False)};
        const out = (() => ({expr}))();
        process.stdout.write(JSON.stringify(out === undefined ? null : out));
        """
    )
    result = _run_js(script)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _js_str(expr: str) -> str:
    """求值并期望返回字符串。"""
    value = _js_json(expr)
    assert isinstance(value, str), value
    return value


@pytest.mark.skipif(_NODE is None, reason="node is required for travel view-model tests")
class TestClockOf:
    """后端给的是完整时间戳，不能原样渲染。"""

    def test_full_timestamp_collapses_to_clock(self) -> None:
        assert _js_str("""VM.clockOf("2026-10-02 21:00:00")""") == "21:00"

    def test_iso_t_variant(self) -> None:
        assert _js_str("""VM.clockOf("2026-10-02T08:05:00")""") == "08:05"

    def test_non_string_and_garbage(self) -> None:
        """拿不到时刻返回空串，绝不返回 'undefined'/'Invalid Date'。"""
        assert _js_str("VM.clockOf(null)") == ""
        assert _js_str("VM.clockOf(undefined)") == ""
        assert _js_str("VM.clockOf(42)") == ""
        assert _js_str("""VM.clockOf("待定")""") == ""


@pytest.mark.skipif(_NODE is None, reason="node is required for travel view-model tests")
class TestRouteViewModel:
    """字段名必须按后端契约取，而不是前端自己猜的那套。"""

    def test_reads_backend_lowest_fields(self) -> None:
        vm = _js_json("VM.routeViewModel(ROUTE)")
        assert isinstance(vm, dict)
        # 后端字段是 lowest_price，不是 price
        assert vm["priceText"] == "620"
        assert vm["priceValue"] == 620
        assert vm["hasPrice"] is True
        # 后端字段是 lowest_flight / lowest_departure
        assert vm["flightNo"] == "HU7836"
        assert vm["departure"] == "21:00"
        # 后端字段是 child_fare，不是 child_price
        assert vm["childText"] == "500"
        assert vm["hasChild"] is True

    def test_the_fields_old_frontend_read_do_not_exist(self) -> None:
        """锁死漂移本身：旧代码读的四个字段后端从未返回过。

        这是最有鉴别力的一条——只要有人再把 `a.price` 那套写法搬回来，
        routeViewModel 就该给出「用旧字段 = 全 undefined」的证据。
        """
        # 注意：用 typeof 而非直接取值——JSON.stringify 会把 undefined 键整个丢掉，
        # 反而让这条断言变成空对象比较（没有鉴别力）。
        old = _js_json(
            "(v => Object.fromEntries(Object.entries(v).map(([k, x]) => [k, typeof x])))"
            "({price: ROUTE.price, drop: ROUTE.drop, flight: ROUTE.flight})"
        )
        assert old == {"price": "undefined", "drop": "undefined", "flight": "undefined"}, old
        old_route = _js_json(
            "(v => Object.fromEntries(Object.entries(v).map(([k, x]) => [k, typeof x])))"
            "({departure_time: ROUTE.departure_time, child_price: ROUTE.child_price})"
        )
        assert old_route == {"departure_time": "undefined", "child_price": "undefined"}, old_route

    def test_missing_fields_yield_empty_not_undefined(self) -> None:
        """查询失败的航线（只有 error，没有报价）不能渲染出 'undefined'。"""
        vm = _js_json("""VM.routeViewModel({route: "SZX-URC", success: false, error: "查询失败"})""")
        assert isinstance(vm, dict)
        assert vm["hasPrice"] is False
        assert vm["priceText"] is None
        assert vm["flightNo"] == ""
        assert vm["departure"] == ""
        assert vm["error"] == "查询失败"

    def test_city_label_falls_back_to_code(self) -> None:
        """城市名缺失（未登记的机场码）时退回三字码，不渲染空标题。"""
        vm = _js_json("""VM.routeViewModel({route: "XYZ-URC", dep: "XYZ", arr: "URC", arr_city: "乌鲁木齐"})""")
        assert isinstance(vm, dict)
        assert vm["routeLabel"] == "XYZ → 乌鲁木齐"


@pytest.mark.skipif(_NODE is None, reason="node is required for travel view-model tests")
class TestAlertMessage:
    """降价提醒：旧实现整句是 '¥undefined（undefined），较基线降 ¥undefined'。"""

    def test_alert_message_has_real_numbers(self) -> None:
        msg = _js_str("VM.alertMessage(ALERT)")
        assert "undefined" not in msg, msg
        assert "太原 → 乌鲁木齐" in msg
        assert "2026-10-02" in msg
        assert "¥410" in msg
        assert "HU7836" in msg
        assert "¥260" in msg
        assert "38.8%" in msg

    def test_accepts_both_raw_route_and_view_model(self) -> None:
        """幂等：传原始 route_info 与传已归一化的 VM 必须得到同一句文案。"""
        assert _js_str("VM.alertMessage(ALERT)") == _js_str(
            "VM.alertMessage(VM.routeViewModel(ALERT))"
        )

    def test_non_alert_route_yields_empty(self) -> None:
        """只看 alert 标记（后端已按 diff/pct 阈值算好），前端不重算阈值。"""
        assert _js_str("""VM.alertMessage(ROUTE)""") == ""
        assert _js_str("VM.alertMessage(null)") == ""
        assert _js_str("VM.alertMessage({})") == ""


@pytest.mark.skipif(_NODE is None, reason="node is required for travel view-model tests")
class TestBuildFlightsViewModel:
    def test_groups_alerts_and_keeps_carrier_fields(self) -> None:
        data = _js_json(
            """VM.buildFlightsViewModel({routes: [ROUTE, ALERT], fee_note: "含税=票面+机建", updated_at: 1})"""
        )
        assert isinstance(data, dict)
        assert data["hasData"] is True
        assert len(data["routes"]) == 2
        assert [r["key"] for r in data["routes"]] == ["TYN-URC", "TYN-URC"]
        # alerts 元素是**完整 route_info**（含 lowest_price），不是扁平的价格对象
        assert len(data["alerts"]) == 1
        assert data["alerts"][0]["priceValue"] == 410
        assert data["feeNote"] == "含税=票面+机建"

    def test_empty_payload_is_safe(self) -> None:
        data = _js_json("VM.buildFlightsViewModel({})")
        assert isinstance(data, dict)
        assert data["hasData"] is False
        assert data["routes"] == []
        assert data["alerts"] == []
        assert _js_json("VM.buildFlightsViewModel(null)") == _js_json(
            "VM.buildFlightsViewModel({})"
        )
