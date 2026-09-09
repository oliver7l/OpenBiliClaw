"""Tests for scripts/save_xhs_note.py 的时间戳解析(小红书 time 字段是毫秒)。"""

from __future__ import annotations

import datetime
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "save_xhs_note.py"


def _load():
    spec = importlib.util.spec_from_file_location("save_xhs_note_under_test", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load()


# 1786700848 秒 == 1786700848000 毫秒 == 1786700848000000 微秒 == 2026-08-14 09:47:28 UTC
# 入库约定: 所有时间字段统一存北京时间(UTC+8)字符串 -> 09:47:28 UTC = 17:47:28 北京
@pytest.mark.parametrize(
    "ts",
    [1786700848, 1786700848000, 1786700848000000, "1786700848000"],
)
def test_parse_ts_normalizes_units(mod, ts):
    dt = mod._parse_ts(ts)
    assert dt is not None
    assert dt == datetime.datetime(2026, 8, 14, 9, 47, 28, tzinfo=datetime.UTC)
    assert mod._ts_to_str(ts) == "2026-08-14 17:47:28"


@pytest.mark.parametrize("ts", [0, -1, "", None, "abc", [], {}])
def test_parse_ts_invalid_returns_none(mod, ts):
    assert mod._parse_ts(ts) is None


def test_ts_to_str_falls_back_to_now(mod):
    before = datetime.datetime.now(mod.CN_TZ).replace(microsecond=0)
    out = mod._ts_to_str(0)
    got = datetime.datetime.strptime(out, mod.TIME_FMT).replace(tzinfo=mod.CN_TZ)
    assert before <= got <= before + datetime.timedelta(minutes=5)


def test_now_local_is_beijing_time(mod):
    """_now_local 返回北京时间字符串, 与 UTC now 差 8 小时。"""
    utc_now = datetime.datetime.now(datetime.UTC).replace(microsecond=0)
    out = mod._now_local()
    got = datetime.datetime.strptime(out, mod.TIME_FMT).replace(tzinfo=mod.CN_TZ)
    delta = abs((got - utc_now).total_seconds())
    assert delta <= 5


def test_to_local_str_converts_utc(mod):
    dt = datetime.datetime(2026, 8, 14, 9, 47, 28, tzinfo=datetime.UTC)
    assert mod._to_local_str(dt) == "2026-08-14 17:47:28"


def test_is_fallback_ts(mod):
    assert mod._is_fallback_ts(None) is True
    assert mod._is_fallback_ts("") is True
    assert mod._is_fallback_ts("not-a-date") is True
    # published_at 与 created_at 几乎相同 -> 当初时间戳解析失败写进去的回退值
    assert mod._is_fallback_ts("2026-09-08 02:11:30", "2026-09-08 02:11:32") is True
    # 正常发布时间远早于入库时间
    assert mod._is_fallback_ts("2026-08-14 09:47:28", "2026-09-08 02:11:32") is False
