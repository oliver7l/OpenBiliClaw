"""移动端补全端点测试（B1 体验包 + B3 content-history + B4 chat-stream）。

契约对齐 OpenBiliClaw-mobile v0.3.158 的 dart 客户端；全部零真实请求。
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.bilibili.api import BilibiliAPIClient
from openbiliclaw.config import Config
from openbiliclaw.storage.database import Database


class _FakeBiliClient:
    DEFAULT_USER_AGENT = BilibiliAPIClient.DEFAULT_USER_AGENT

    def __init__(self, cookie: str = "", **_kwargs: Any) -> None:
        self.cookie = cookie
        self.calls: list[str] = []

    async def get_nav_info(self) -> Any:
        self.calls.append("nav")
        if not self.cookie:
            return SimpleNamespace(is_login=False, uname="", mid=0)
        return SimpleNamespace(is_login=True, uname="测试用户", mid=123)

    async def get_video_relation_state(self, bvid: str) -> dict[str, Any]:
        self.calls.append(f"relation:{bvid}")
        return {"like": True, "coin": 2, "favorite": False, "watch_later": True}

    async def generate_qrcode(self) -> dict[str, Any]:
        self.calls.append("qr-generate")
        return {"qrcode_key": "qr-key-1", "url": "https://passport.bilibili.com/h2-app-mobileng"}

    async def poll_qrcode(self, qrcode_key: str) -> dict[str, Any]:
        self.calls.append(f"qr-poll:{qrcode_key}")
        return {
            "status": "confirmed",
            "message": "",
            "url": "https://passport.biligame.com/crossDomain?DedeUserID=123&SESSDATA=abc&bili_jct=csrf",
            "qrcode_key": qrcode_key,
            "raw_code": 0,
        }

    async def aclose(self) -> None:
        return None

    async def close(self) -> None:
        return None


def _build(monkeypatch: Any, tmp_path: Any, *, cookie: str = "SESSDATA=x; bili_jct=y") -> TestClient:
    cfg = Config()
    cfg.data_dir = str(tmp_path)
    cfg.bilibili.cookie = cookie
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *a, **kw: cfg)
    monkeypatch.setattr(
        "openbiliclaw.config.load_config_with_diagnostics",
        lambda *a, **kw: (cfg, SimpleNamespace(config_path=tmp_path / "config.toml")),
    )
    monkeypatch.setattr("openbiliclaw.bilibili.api.BilibiliAPIClient", _FakeBiliClient)
    # AuthManager 走真实实现（写 tmp 下的 cookie 文件）

    database = Database(tmp_path / "parity.db")
    database.initialize()
    return TestClient(
        create_app(
            memory_manager=object(),
            database=database,
            soul_engine=object(),
        )
    )


class TestAuthStatus:
    def test_logged_in_with_cookie(self, monkeypatch: Any, tmp_path: Any) -> None:
        with _build(monkeypatch, tmp_path) as client:
            resp = client.get("/api/bilibili/auth/status")
        assert resp.status_code == 200
        payload = resp.json()
        # ⚠️ 契约：字段名是 status（mobile fromJson 只读 status）
        assert payload["status"] == "logged_in"
        assert payload["user"]["mid"] == 123
        assert payload["user"]["name"] == "测试用户"
        assert payload["scopes"] == []

    def test_anonymous_without_cookie(self, monkeypatch: Any, tmp_path: Any) -> None:
        with _build(monkeypatch, tmp_path, cookie="") as client:
            resp = client.get("/api/bilibili/auth/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "anonymous"


class TestAuthImportAndSession:
    def test_import_persists_and_reports_logged_in(self, monkeypatch: Any, tmp_path: Any) -> None:
        with _build(monkeypatch, tmp_path, cookie="") as client:
            resp = client.post(
                "/api/bilibili/auth/import",
                json={"cookies": {"SESSDATA": "abc", "bili_jct": "csrf"}, "source": "mobile_webview"},
            )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["status"] == "logged_in"

    def test_import_rejects_empty_cookies(self, monkeypatch: Any, tmp_path: Any) -> None:
        with _build(monkeypatch, tmp_path) as client:
            resp = client.post("/api/bilibili/auth/import", json={"cookies": {}})
        assert resp.status_code == 422

    def test_session_delete_returns_ok(self, monkeypatch: Any, tmp_path: Any) -> None:
        with _build(monkeypatch, tmp_path) as client:
            resp = client.delete("/api/bilibili/auth/session")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


class TestQrcodeLogin:
    def test_create_returns_key_and_url(self, monkeypatch: Any, tmp_path: Any) -> None:
        with _build(monkeypatch, tmp_path) as client:
            resp = client.post("/api/bilibili/auth/qrcode", json={})
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["ok"] is True
        assert payload["qrcode_key"] == "qr-key-1"
        assert payload["qrcode_url"].startswith("https://passport.bilibili.com")
        assert payload["expires_in"] == 180

    def test_poll_confirmed_persists_cookie(self, monkeypatch: Any, tmp_path: Any) -> None:
        """⚠️ 契约陷阱：poll 必须回 `confirmed`——枚举里没有 logged_in。"""
        with _build(monkeypatch, tmp_path, cookie="") as client:
            resp = client.get("/api/bilibili/auth/qrcode/poll", params={"qrcode_key": "qr-key-1"})
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["status"] == "confirmed"
        assert payload["user"]["mid"] == 123


class TestVideoRelation:
    def test_relation_shape(self, monkeypatch: Any, tmp_path: Any) -> None:
        with _build(monkeypatch, tmp_path) as client:
            resp = client.get("/api/bilibili/video/relation", params={"bvid": "BV1mobile01"})
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["ok"] is True
        assert payload["like"] is True
        assert payload["coin"] == 2
        assert payload["watch_later"] is True


class TestPlatformAvailability:
    def test_counts_fresh_candidates_by_platform(
        self, monkeypatch: Any, tmp_path: Any
    ) -> None:
        with _build(monkeypatch, tmp_path) as client:
            # 直接往该实例的 pool 库插三类状态
            db_path = tmp_path / "parity.db"
            conn = sqlite3.connect(str(db_path))
            # pool.db 与主库同目录同名规则：Database 用 with_name("pool.db")
            pool_path = tmp_path / "pool.db"
            conn = sqlite3.connect(str(pool_path))
            for i, (platform, status) in enumerate(
                [
                    ("bilibili", "fresh"),
                    ("bilibili", "fresh"),
                    ("xiaohongshu", "fresh"),
                    ("bilibili", "shown"),  # 非 fresh → 不计
                    ("youtube", "suppressed"),  # 非 fresh → 不计
                ]
            ):
                conn.execute(
                    "INSERT INTO content_cache (bvid, source_platform, pool_status, title)"
                    " VALUES (?, ?, ?, ?)",
                    (f"BV{i:09d}", platform, status, f"t{i}"),
                )
            conn.commit()
            conn.close()

            resp = client.get("/api/recommendations/platform-availability")

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["total_available"] == 3
        assert payload["by_platform"]["bilibili"] == 2
        assert payload["by_platform"]["xiaohongshu"] == 1
        assert "youtube" not in payload["by_platform"]
        assert payload["version"] > 0


class TestContentHistory:
    def _seed_recommendations(self, tmp_path: Any, count: int = 3) -> None:
        """⚠️ 必须在 `_build` 之后调用：pool.db 的 schema 由 Database.initialize 创建。"""
        pool_path = tmp_path / "pool.db"
        conn = sqlite3.connect(str(pool_path))
        for i in range(count):
            conn.execute(
                "INSERT INTO recommendations (bvid, presented_at) VALUES (?, ?)",
                (f"BV1hist{i:04d}", f"2026-09-{16 - i:02d}T10:00:00"),
            )
            conn.execute(
                "INSERT INTO content_cache (bvid, source_platform, pool_status, title,"
                " up_name, cover_url, content_url)"
                " VALUES (?, 'bilibili', 'shown', ?, 'UP主', '//i0.hdslb.com/c.png',"
                " 'https://www.bilibili.com/video/' || ?)",
                (f"BV1hist{i:04d}", f"视频{i}", f"BV1hist{i:04d}"),
            )
        conn.commit()
        conn.close()

    def test_shown_history_maps_to_mobile_contract(
        self, monkeypatch: Any, tmp_path: Any
    ) -> None:
        with _build(monkeypatch, tmp_path) as client:
            self._seed_recommendations(tmp_path, 3)
            resp = client.get("/api/content-history", params={"category": "shown", "limit": 2})

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["category"] == "shown"
        assert payload["total"] == 3
        assert payload["retention_days"] == 30
        assert payload["has_more"] is True
        assert payload["next_cursor"]
        first = payload["items"][0]
        assert first["item_key"].startswith("bilibili:BV1hist")
        assert first["source_platform"] == "bilibili"
        assert first["content_type"] == "video"
        assert first["occurred_at"].startswith("2026-09-")
        assert first["contexts"] == []

    def test_cursor_pagination_walks_all_items(self, monkeypatch: Any, tmp_path: Any) -> None:
        with _build(monkeypatch, tmp_path) as client:
            self._seed_recommendations(tmp_path, 3)
            seen: list[str] = []
            cursor = ""
            for _ in range(5):
                params: dict[str, Any] = {"category": "shown", "limit": 2}
                if cursor:
                    params["cursor"] = cursor
                resp = client.get("/api/content-history", params=params)
                payload = resp.json()
                seen.extend(item["content_id"] for item in payload["items"])
                if not payload["has_more"]:
                    break
                cursor = payload["next_cursor"] or ""
        assert len(seen) == 3
        assert len(set(seen)) == 3  # 游标分页不重复不遗漏

    def test_unknown_category_is_422(self, monkeypatch: Any, tmp_path: Any) -> None:
        with _build(monkeypatch, tmp_path) as client:
            resp = client.get("/api/content-history", params={"category": "bogus"})
        assert resp.status_code == 422

    def test_bad_cursor_is_422(self, monkeypatch: Any, tmp_path: Any) -> None:
        with _build(monkeypatch, tmp_path) as client:
            resp = client.get("/api/content-history", params={"category": "shown", "cursor": "!!!"})
        assert resp.status_code == 422


class TestChatStream:
    def test_stream_emits_done_even_when_completion_fails(
        self, monkeypatch: Any, tmp_path: Any
    ) -> None:
        """测试环境没有可用 LLM → 补完任务失败 → SSE 仍必须发出 done 事件
        （mobile 的 chat 标签依赖 done 收尾，不能吊死在 pending）。"""
        with _build(monkeypatch, tmp_path) as client, client.stream(
            "POST",
            "/api/chat/stream",
            json={"turn_id": "", "message": "你好", "session": "popup", "streaming": True},
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            events: list[str] = []
            lines = []
            for line in resp.iter_lines():
                lines.append(line)
                if line.startswith("event:"):
                    events.append(line.split(":", 1)[1].strip())
                if "done" in events:
                    break
        assert "done" in events, f"事件序列里没有 done：{events[:10]}（lines={lines[:10]}）"
