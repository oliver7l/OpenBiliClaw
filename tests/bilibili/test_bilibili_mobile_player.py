"""移动端播放器客户端层测试（WBI fallback + get_play_info 扁平化契约）。

全部走 httpx.MockTransport，零真实请求。移植自上游
8a1e98a4（view 412 → wbi/view）与 90a88262/9422c35e/a50ec617（播放载荷）。
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from openbiliclaw.bilibili.api import BilibiliAPIClient

_BVID = "BV1mobile01"

_NAV_PAYLOAD = {
    "code": 0,
    "data": {
        "wbi_img": {
            # 真实 key 是 32+32 字符；签名表索引最大到 63，短 key 会越界
            "img_url": "https://i0.hdslb.com/bfs/wbi/" + ("a" * 32) + ".png",
            "sub_url": "https://i0.hdslb.com/bfs/wbi/" + ("b" * 32) + ".png",
        }
    },
}

_VIEW_PAYLOAD = {
    "code": 0,
    "data": {
        "bvid": _BVID,
        "aid": 42,
        "cid": 777,
        "title": "测试视频",
        "desc": "简介",
        "pic": "//i2.hdslb.com/cover.png",
        "duration": 60,
        "pubdate": "2026-09-01",
        "owner": {"name": "UP主", "mid": 9},
        "stat": {"view": 1, "like": 2, "coin": 3, "favorite": 4, "share": 5, "danmaku": 6},
    },
}

_DASH_PAYLOAD = {
    "code": 0,
    "data": {
        "timelength": 61000,
        "quality": 80,
        "support_formats": [
            {"quality": 80, "new_description": "高清 1080P", "width": 1920, "height": 1080},
            {"quality": 64, "new_description": "高清 720P", "width": 1280, "height": 720},
        ],
        "dash": {
            "video": [
                {
                    "id": 80,
                    "codecs": "avc1.640032",
                    "baseUrl": "https://upos.example/video-avc.m4s",
                    "backupUrl": ["https://upos.example/video-avc-bak.m4s"],
                    "width": 1920,
                    "height": 1080,
                    "bandwidth": 2000000,
                    "mimeType": "video/mp4",
                },
                {
                    "id": 80,
                    "codecs": "hev01",
                    "baseUrl": "https://upos.example/video-hev.m4s",
                    "width": 1920,
                    "height": 1080,
                    "bandwidth": 1500000,
                    "mimeType": "video/mp4",
                },
            ],
            "audio": [
                {
                    "id": 30280,
                    "codecs": "mp4a.40.2",
                    "baseUrl": "https://upos.example/audio.m4s",
                    "bandwidth": 320000,
                    "mimeType": "audio/mp4",
                }
            ],
        },
    },
}

_PAGELIST_PAYLOAD = {
    "code": 0,
    "data": [
        {"cid": 777, "page": 1, "part": "P1", "duration": 61,
         "dimension": {"width": 1920, "height": 1080}},
    ],
}

_SUBTITLE_PAYLOAD = {
    "code": 0,
    "data": {
        "subtitle": {
            "subtitles": [
                {"lan": "zh-CN", "lan_doc": "中文（自动生成）", "subtitle_url": "//aisubtitle.hdslb.com/zhi.json"},
            ]
        }
    },
}


def _client_with(handler: Any) -> BilibiliAPIClient:
    return BilibiliAPIClient(
        cookie="SESSDATA=x; bili_jct=y",
        transport=httpx.MockTransport(handler),
    )


def _route_handler(
    *,
    view_status: int = 200,
    view_payload: dict[str, Any] | None = None,
    calls: list[str] | None = None,
) -> Any:
    """按路径分发；`calls` 记录命中的端点（含重复）。"""

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if calls is not None:
            calls.append(path)
        if path == "/x/web-interface/nav":
            return httpx.Response(200, json=_NAV_PAYLOAD)
        if path == "/x/web-interface/view":
            if view_payload is None:
                return httpx.Response(
                    view_status, json={"code": -412, "message": "请求被拦截"}
                )
            return httpx.Response(view_status, json=view_payload)
        if path == "/x/web-interface/wbi/view":
            return httpx.Response(200, json=_VIEW_PAYLOAD)
        if path == "/x/player/wbi/playurl":
            return httpx.Response(200, json=_DASH_PAYLOAD)
        if path == "/x/player/pagelist":
            return httpx.Response(200, json=_PAGELIST_PAYLOAD)
        if path == "/x/player/wbi/v2":
            return httpx.Response(200, json=_SUBTITLE_PAYLOAD)
        return httpx.Response(404, json={"code": -404, "message": "not found"})

    return handle


class TestVideoViewDataFallback:
    def test_plain_view_success_keeps_using_it(self) -> None:
        calls: list[str] = []
        client = _client_with(
            _route_handler(view_payload=_VIEW_PAYLOAD, calls=calls)
        )

        data = asyncio.run(client.get_video_view_data(_BVID))

        assert data["bvid"] == _BVID
        assert calls == ["/x/web-interface/view"]
        asyncio.run(client.aclose())

    def test_412_falls_back_to_wbi_view(self) -> None:
        """裸 /view 被风控(-412) → 重试 WBI 签名端点（移植自上游 8a1e98a4）。"""
        calls: list[str] = []
        client = _client_with(_route_handler(calls=calls))

        data = asyncio.run(client.get_video_view_data(_BVID))

        assert data["cid"] == 777
        assert calls[:3] == [
            "/x/web-interface/view",  # 裸端点，-412
            "/x/web-interface/nav",  # 取 WBI key
            "/x/web-interface/wbi/view",  # 签名重试
        ]
        asyncio.run(client.aclose())

    def test_other_codes_are_not_swallowed(self) -> None:
        """非 -412 的错误（如 -404）原样上抛，不做无谓重试。"""
        from openbiliclaw.bilibili.api import BilibiliAPIError

        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/x/web-interface/view":
                return httpx.Response(200, json={"code": -404, "message": "啥都查不到"})
            return httpx.Response(200, json=_NAV_PAYLOAD)

        client = _client_with(handle)
        try:
            asyncio.run(client.get_video_view_data(_BVID))
        except BilibiliAPIError as exc:
            assert exc.code == -404
        else:
            raise AssertionError("应当上抛 -404")
        finally:
            asyncio.run(client.aclose())

    def test_get_video_info_populates_cid(self) -> None:
        client = _client_with(_route_handler())

        info = asyncio.run(client.get_video_info(_BVID))

        assert info.cid == 777  # get_play_info 免传 cid 依赖这个字段
        assert info.cover_url == "//i2.hdslb.com/cover.png"  # 原样保留，归一化在契约层
        asyncio.run(client.aclose())


class TestGetPlayInfo:
    def test_flattened_payload_contract(self) -> None:
        """移动端契约：按首选编解码挑 dash 流 + 画质清单 + 字幕轨归一化。"""
        client = _client_with(_route_handler())

        payload = asyncio.run(client.get_play_info(_BVID, preferred_codec="avc"))

        assert payload["bvid"] == _BVID
        assert payload["cid"] == 777
        assert payload["duration"] == 61  # timelength 61000ms → 秒
        # avc 优先：第一个 hev 被跳过
        assert payload["video"]["codec"] == "avc1.640032"
        assert payload["video"]["url"] == "https://upos.example/video-avc.m4s"
        assert payload["video"]["backup_urls"] == ["https://upos.example/video-avc-bak.m4s"]
        assert payload["audio"]["url"] == "https://upos.example/audio.m4s"
        assert [q["qn"] for q in payload["qualities"]] == [80, 64]
        assert payload["subtitles"] == [
            {"lan": "zh-CN", "name": "中文（自动生成）", "url": "https://aisubtitle.hdslb.com/zhi.json"}
        ]
        asyncio.run(client.aclose())

    def test_preferred_codec_falls_back_to_first_stream(self) -> None:
        client = _client_with(_route_handler())

        payload = asyncio.run(client.get_play_info(_BVID, preferred_codec="av01"))

        assert payload["video"]["codec"] == "avc1.640032"  # 无 av01 → 取第一路
        asyncio.run(client.aclose())

    def test_missing_cid_resolves_from_video_info(self) -> None:
        """cid 缺省时先用 video/info 解析（走的还是可降级的 view 链路）。"""
        calls: list[str] = []
        client = _client_with(_route_handler(calls=calls))

        payload = asyncio.run(client.get_play_info(_BVID))

        assert payload["cid"] == 777
        assert "/x/web-interface/view" in calls
        asyncio.run(client.aclose())

    def test_subtitle_and_pagelist_failures_are_non_fatal(self) -> None:
        """字幕/分 P 拉不动时不影响主流程（播放地址才是硬需求）。"""
        calls: list[str] = []

        def handle(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            calls.append(path)
            if path == "/x/player/pagelist" or path == "/x/player/wbi/v2":
                return httpx.Response(200, json={"code": -412, "message": "blocked"})
            return _route_handler(calls=None)(request)

        client = _client_with(handle)
        payload = asyncio.run(client.get_play_info(_BVID))

        assert payload["pages"] == []
        assert payload["subtitles"] == []
        assert payload["video"] is not None
        asyncio.run(client.aclose())

    def test_durl_fallback_when_no_dash(self) -> None:
        """低清晰度 durl 形态：video 对象从 durl[0] 构造。"""
        durl_payload = {
            "code": 0,
            "data": {
                "timelength": 30000,
                "quality": 16,
                "support_formats": [{"quality": 16, "new_description": "流畅"}],
                "durl": [
                    {"url": "https://upos.example/low.mp4", "backup_url": ["https://bak.example/low.mp4"]}
                ],
            },
        }

        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/x/player/wbi/playurl":
                return httpx.Response(200, json=durl_payload)
            if request.url.path == "/x/player/pagelist":
                return httpx.Response(200, json=_PAGELIST_PAYLOAD)
            if request.url.path == "/x/player/wbi/v2":
                return httpx.Response(200, json=_SUBTITLE_PAYLOAD)
            return httpx.Response(200, json=_NAV_PAYLOAD)

        client = _client_with(handle)
        payload = asyncio.run(client.get_play_info(_BVID, cid=777))

        assert payload["video"]["url"] == "https://upos.example/low.mp4"
        assert payload["video"]["codec"] == "mp4"
        assert payload["audio"] is None
        asyncio.run(client.aclose())


class TestAuthExportClientSurface:
    def test_default_user_agent_constant(self) -> None:
        assert BilibiliAPIClient.DEFAULT_USER_AGENT.startswith("Mozilla/5.0")

    def test_signed_playurl_request_carries_w_rid(self) -> None:
        """playurl 请求必须带 WBI 签名参数（wts/w_rid）。"""
        seen: dict[str, Any] = {}

        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/x/player/wbi/playurl":
                seen["params"] = dict(request.url.params)
                return httpx.Response(200, json=_DASH_PAYLOAD)
            if request.url.path == "/x/web-interface/nav":
                return httpx.Response(200, json=_NAV_PAYLOAD)
            if request.url.path == "/x/player/pagelist":
                return httpx.Response(200, json=_PAGELIST_PAYLOAD)
            if request.url.path == "/x/player/wbi/v2":
                return httpx.Response(200, json=_SUBTITLE_PAYLOAD)
            return httpx.Response(200, json=_VIEW_PAYLOAD)

        client = _client_with(handle)
        asyncio.run(client.get_play_info(_BVID, cid=777))

        params = seen["params"]
        assert params.get("bvid") == _BVID
        assert params.get("cid") == "777"
        assert "wts" in params and "w_rid" in params
        assert params.get("fnval") == "4048"
        asyncio.run(client.aclose())
