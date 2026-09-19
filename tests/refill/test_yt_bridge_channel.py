"""yt_bridge 通道单测：monkeypatch 桥接 evaljs / navigate，规避真实浏览器。

页面 JS 编排在通道内嵌 JS 常量里，单测只覆盖 Python 侧决策链：
字幕收割 → 简介兜底 → PERMANENT 判定 → 桥接不可用传播。
"""

from __future__ import annotations

from openbiliclaw.refill.channels import yt_bridge as yt_mod
from openbiliclaw.refill.channels.base import PERMANENT, route_channels
from openbiliclaw.refill.channels.yt_bridge import YtBridgeChannel


class _FakeBridge:
    def __init__(self) -> None:
        self.navigated: list[str] = []

    def _navigate(self, url: str) -> None:
        self.navigated.append(url)


_BODY = "这是字幕正文，超过五十个字符的长度要求，用来通过通道的最小正文判定阈值。" * 3


def _patch(monkeypatch, responses: list[dict | None], navigated_ok: bool = True):
    """按顺序回放 _eval_json 响应；navigate 记录调用。"""
    calls = {"i": 0, "navigate": 0}

    def fake_eval_json(expression, timeout=60):
        if not responses:
            return None
        idx = min(calls["i"], len(responses) - 1)
        calls["i"] += 1
        return responses[idx]

    monkeypatch.setattr(yt_mod, "_eval_json", fake_eval_json)

    bridge = _FakeBridge()

    def fake_navigate(url):
        calls["navigate"] += 1
        if not navigated_ok:
            raise RuntimeError("navigate failed")
        bridge.navigated.append(url)

    monkeypatch.setattr(bridge, "_navigate", fake_navigate)
    return bridge, calls


def _item(url="https://www.youtube.com/watch?v=dQw4w9WgXcQ"):
    return {"id": 1, "source_type": "youtube", "url": url, "title": "t", "attempts": 0, "max_attempts": 3}


_META_CAPS = {"playability": "OK", "title": "T", "vid": "dQw4w9WgXcQ", "desc": "short", "trackCount": 2}
_META_NO_CAPS = {"playability": "OK", "title": "T", "vid": "dQw4w9WgXcQ", "desc": "short", "trackCount": 0}


def test_captions_harvest_success(monkeypatch):
    bridge, calls = _patch(
        monkeypatch,
        [
            _META_CAPS,  # meta
            {"ok": True, "lang": "en", "kind": "manual"},  # drive
            {"events": 10, "body": _BODY},  # harvest #1
        ],
    )
    ch = YtBridgeChannel(bridge)
    ok, body, detail = ch.fetch(_item())
    assert ok is True
    assert body == _BODY
    assert "lang=en" in detail
    assert bridge.navigated == ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"]


def test_description_fallback_when_no_tracks(monkeypatch):
    meta = dict(_META_NO_CAPS, desc="很长的简介" * 30)
    bridge, calls = _patch(monkeypatch, [meta])
    ch = YtBridgeChannel(bridge)
    ok, body, detail = ch.fetch(_item())
    assert ok is True
    assert body.startswith("【视频简介】")
    assert detail == "fallback=description"
    # 无字幕轨时不应触碰播放器：仅 meta 一次求值
    assert calls["i"] == 1


def test_permanent_when_no_tracks_no_desc(monkeypatch):
    bridge, _ = _patch(monkeypatch, [_META_NO_CAPS])
    ch = YtBridgeChannel(bridge)
    ok, body, detail = ch.fetch(_item())
    assert ok is False
    assert detail.startswith(PERMANENT)


def test_permanent_when_playability_terminal(monkeypatch):
    meta = dict(_META_CAPS, playability="LOGIN_REQUIRED", trackCount=0)
    bridge, _ = _patch(monkeypatch, [meta])
    ch = YtBridgeChannel(bridge)
    ok, body, detail = ch.fetch(_item())
    assert ok is False and detail.startswith(PERMANENT)


def test_tracks_exist_but_harvest_fails_is_retryable(monkeypatch):
    bridge, _ = _patch(
        monkeypatch,
        [
            _META_CAPS,
            {"ok": True, "lang": "en", "kind": "manual"},
            {"err": "no timedtext request", "body": ""},  # harvest #1 miss
            {"err": "no timedtext request", "body": ""},  # harvest #2 miss
        ],
    )
    ch = YtBridgeChannel(bridge)
    ok, body, detail = ch.fetch(_item())
    assert ok is False
    assert not detail.startswith(PERMANENT)


def test_harvest_short_body_falls_back_to_retryable(monkeypatch):
    bridge, _ = _patch(
        monkeypatch,
        [
            _META_CAPS,
            {"ok": True, "lang": "en", "kind": "manual"},
            {"events": 1, "body": "短"},
            {"events": 1, "body": "短"},
        ],
    )
    ch = YtBridgeChannel(bridge)
    ok, body, detail = ch.fetch(_item())
    assert ok is False and not detail.startswith(PERMANENT)


def test_meta_unready_is_retryable(monkeypatch):
    bridge, _ = _patch(monkeypatch, [None])
    ch = YtBridgeChannel(bridge)
    ok, body, detail = ch.fetch(_item())
    assert ok is False and not detail.startswith(PERMANENT)


def test_url_without_vid_is_permanent(monkeypatch):
    bridge, _ = _patch(monkeypatch, [])
    ch = YtBridgeChannel(bridge)
    ok, body, detail = ch.fetch(_item("https://www.youtube.com/channel/abc"))
    assert ok is False and detail.startswith(PERMANENT)


def test_supports_only_youtube_with_vid():
    ch = YtBridgeChannel(_FakeBridge())
    assert ch.supports("youtube", "https://youtu.be/dQw4w9WgXcQ")
    assert ch.supports("youtube", "https://www.youtube.com/shorts/abcdefghijk")
    assert not ch.supports("bilibili", "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert not ch.supports("youtube", "https://www.youtube.com/feed/subscriptions")


def test_route_puts_yt_bridge_first():
    assert route_channels("youtube", "https://youtu.be/dQw4w9WgXcQ", "t")[0] == "yt_bridge"


def test_build_channels_registers_yt_bridge():
    from openbiliclaw.refill.channels import build_channels

    channels = build_channels(_FakeBridge())
    assert isinstance(channels["yt_bridge"], YtBridgeChannel)
    assert channels["yt_bridge"].requires_bridge is True
    # bridge=None（纯子进程调度）时不注册
    assert "yt_bridge" not in build_channels(None)
