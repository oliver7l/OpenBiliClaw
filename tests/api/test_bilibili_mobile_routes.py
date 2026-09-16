"""移动端 B 站端点路由测试（play-url / video-info / auth-export）。

契约对齐上游 OpenBiliClaw-mobile 的 API 客户端；零真实请求：
- `load_config` monkeypatch 掉（cookie 配置在内存里）；
- `BilibiliAPIClient` 换成假类，只记录调用并返回固定载荷。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.bilibili.api import BilibiliAPIClient
from openbiliclaw.config import Config
from openbiliclaw.storage.database import Database

_COOKIE = "SESSDATA=secret; bili_jct=csrf; buvid3=buvid-value-3; DedeUserID=123"


class _FakeClient:
    DEFAULT_USER_AGENT = BilibiliAPIClient.DEFAULT_USER_AGENT

    def __init__(self, cookie: str = "", **_kwargs: Any) -> None:
        self.cookie = cookie
        self.calls: list[str] = []

    async def get_video_view_data(self, bvid: str) -> dict[str, Any]:
        self.calls.append(f"view:{bvid}")
        return {"bvid": bvid, "cid": 777, "title": "测试视频"}

    async def get_play_info(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append("play-info")
        return {
            "bvid": kwargs["bvid"],
            "cid": kwargs.get("cid") or 777,
            "qualities": [{"qn": 80, "label": "高清 1080P"}],
            "video": {"url": "https://upos.example/video.m4s", "codec": "avc1"},
            "audio": {"url": "https://upos.example/audio.m4s"},
            "subtitles": [],
        }

    async def aclose(self) -> None:  # pragma: no cover - 仅为接口对齐
        return None

    async def close(self) -> None:  # 旧别名，路由 finally 里用的是它
        return None


_FAKE_LAST: list[_FakeClient] = []


def _fake_client_cls() -> type[_FakeClient]:
    class _Tracking(_FakeClient):
        def __init__(self, cookie: str = "", **kwargs: Any) -> None:
            super().__init__(cookie=cookie, **kwargs)
            _FAKE_LAST.append(self)

    return _Tracking


def _build(
    monkeypatch: Any,
    tmp_path: Any,
    *,
    cookie: str = _COOKIE,
) -> TestClient:
    """注入路径起 app；load_config 与 B 站客户端全部换成假的。"""
    cfg = Config()
    cfg.data_dir = str(tmp_path)  # data_path 是只读派生属性，从这里间接指到 tmp
    cfg.bilibili.cookie = cookie
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *a, **kw: cfg)

    fake_cls = _fake_client_cls()
    monkeypatch.setattr("openbiliclaw.bilibili.api.BilibiliAPIClient", fake_cls)

    database = Database(tmp_path / "mobile.db")
    database.initialize()
    return TestClient(
        create_app(
            memory_manager=object(),
            database=database,
            soul_engine=object(),
        )
    )


class TestAuthExport:
    def test_returns_cookie_dict_and_meta(self, monkeypatch: Any, tmp_path: Any) -> None:
        with _build(monkeypatch, tmp_path) as client:
            resp = client.post("/api/bilibili/auth/export", json={})

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["ok"] is True
        assert payload["cookie"] == _COOKIE
        assert payload["cookies"]["SESSDATA"] == "secret"
        assert payload["cookies"]["bili_jct"] == "csrf"
        assert payload["buvid"] == "buvid-value-3"
        assert payload["user_agent"] == BilibiliAPIClient.DEFAULT_USER_AGENT
        assert payload["user"] is None

    def test_requires_cookie(self, monkeypatch: Any, tmp_path: Any) -> None:
        with _build(monkeypatch, tmp_path, cookie="") as client:
            resp = client.post("/api/bilibili/auth/export", json={})

        assert resp.status_code == 401
        assert resp.json()["detail"] == "B站 Cookie 未配置或已失效"


class TestVideoInfo:
    def test_returns_raw_view_data(self, monkeypatch: Any, tmp_path: Any) -> None:
        with _build(monkeypatch, tmp_path) as client:
            resp = client.get("/api/bilibili/video/info", params={"bvid": "BV1mobile01"})

        assert resp.status_code == 200
        assert resp.json() == {"bvid": "BV1mobile01", "cid": 777, "title": "测试视频"}
        assert _FAKE_LAST[-1].calls == ["view:BV1mobile01"]

    def test_requires_bvid(self, monkeypatch: Any, tmp_path: Any) -> None:
        with _build(monkeypatch, tmp_path) as client:
            resp = client.get("/api/bilibili/video/info")

        assert resp.status_code == 422  # FastAPI Query(...) 校验


class TestPlayUrl:
    def test_returns_flattened_playload(self, monkeypatch: Any, tmp_path: Any) -> None:
        with _build(monkeypatch, tmp_path) as client:
            resp = client.post(
                "/api/bilibili/player/play-url",
                json={"bvid": "BV1mobile01", "qn": 80, "preferred_codec": "avc"},
            )

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["ok"] is True
        assert payload["bvid"] == "BV1mobile01"
        assert payload["video"]["url"] == "https://upos.example/video.m4s"
        assert [q["qn"] for q in payload["qualities"]] == [80]

    def test_missing_bvid_is_400(self, monkeypatch: Any, tmp_path: Any) -> None:
        with _build(monkeypatch, tmp_path) as client:
            resp = client.post("/api/bilibili/player/play-url", json={})

        assert resp.status_code == 400
        assert resp.json()["detail"] == "missing bvid"
