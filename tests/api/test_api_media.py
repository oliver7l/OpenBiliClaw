"""API tests for the media browsing module routes (/api/media)."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openbiliclaw.config import Config, MediaConfig
from openbiliclaw.media.routes import build_media_router

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture()
def media_root(tmp_path: Path) -> Path:
    root = tmp_path / "media"
    root.mkdir()
    (root / "movie_a.mp4").write_bytes(b"\x00\x01\x02\x03video-a" * 4)
    (root / "photo_b.jpg").write_bytes(b"\xff\xd8\xff\xe0photo-b")
    sub = root / "subdir"
    sub.mkdir()
    (sub / "nested_c.png").write_bytes(b"\x89PNG\r\n\x1a\nnested")
    (root / "readme.txt").write_text("not media", encoding="utf-8")
    (root / "clip.mkv").write_bytes(b"matroska")
    return root


@pytest.fixture()
def client(media_root: Path, tmp_path: Path) -> TestClient:
    config = Config(data_dir=str(tmp_path), media=MediaConfig(roots=[str(media_root)]))
    app = FastAPI()
    app.include_router(
        build_media_router(
            config=config,
            config_save_lock=threading.Lock(),
            config_path=tmp_path / "config.toml",
        )
    )
    return TestClient(app)


def test_roots_meta(client: TestClient, media_root: Path) -> None:
    resp = client.get("/api/media/roots")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["roots"]) == 1
    meta = body["roots"][0]
    assert meta["path"] == str(media_root)
    assert meta["exists"] is True
    assert meta["video_count"] == 2  # movie_a.mp4 + clip.mkv
    assert meta["image_count"] == 1  # photo_b.jpg


def test_list_all_includes_only_media(client: TestClient) -> None:
    resp = client.get("/api/media/list", params={"root": client_config_root(client)})
    items = resp.json()["items"]
    names = {i["name"] for i in items}
    assert names == {"movie_a.mp4", "photo_b.jpg", "clip.mkv", "subdir"}
    assert not any(i["name"] == "readme.txt" for i in items)
    dirs = [i for i in items if i["is_dir"]]
    assert [d["name"] for d in dirs] == ["subdir"]
    assert dirs[0]["kind"] == "dir"


def test_list_type_filter(client: TestClient) -> None:
    root = client_config_root(client)
    video = client.get("/api/media/list", params={"root": root, "kind": "video"}).json()["items"]
    # 目录始终展示（便于逐层导航），媒体文件需命中类型
    assert {i["name"] for i in video if not i["is_dir"]} == {"movie_a.mp4", "clip.mkv"}
    image = client.get("/api/media/list", params={"root": root, "kind": "image"}).json()["items"]
    assert {i["name"] for i in image if not i["is_dir"]} == {"photo_b.jpg"}


def test_list_search(client: TestClient) -> None:
    root = client_config_root(client)
    items = client.get("/api/media/list", params={"root": root, "q": "photo"}).json()["items"]
    assert [i["name"] for i in items] == ["photo_b.jpg"]
    assert client.get("/api/media/list", params={"root": root, "q": "不存在"}).json()["items"] == []


def test_list_pagination(client: TestClient) -> None:
    root = client_config_root(client)
    body = client.get("/api/media/list", params={"root": root, "limit": 2}).json()
    assert len(body["items"]) == 2
    assert body["has_more"] is True
    second = client.get(
        "/api/media/list", params={"root": root, "limit": 2, "offset": body["offset"] + 2}
    ).json()
    assert len(second["items"]) == 2
    assert second["has_more"] is False


def test_list_subdir(client: TestClient) -> None:
    root = client_config_root(client)
    items = client.get("/api/media/list", params={"root": root, "sub": "subdir"}).json()["items"]
    assert [i["name"] for i in items] == ["nested_c.png"]
    assert client.get(
        "/api/media/list", params={"root": root, "sub": "不存在的子目录"}
    ).status_code == 404


def test_file_serves_with_content_type(client: TestClient) -> None:
    root = client_config_root(client)
    resp = client.get("/api/media/file", params={"root": root, "path": "photo_b.jpg"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/")
    assert resp.content.startswith(b"\xff\xd8")


def test_file_range_support(client: TestClient) -> None:
    root = client_config_root(client)
    resp = client.get(
        "/api/media/file", params={"root": root, "path": "movie_a.mp4"}, headers={"Range": "bytes=4-9"}
    )
    assert resp.status_code in (200, 206)
    # 文件内容字节序列: \x00\x01\x02\x03 \x63('v') \x69('i') \x64('d') ...
    # 第 4~9 字节 = b"video-"
    assert resp.content == b"video-"


def test_file_rejects_path_traversal(client: TestClient) -> None:
    root = client_config_root(client)
    # ../ 越过根目录（指向根目录的父级），必须被安全校验拒绝
    assert client.get(
        "/api/media/file", params={"root": root, "path": "../secret.txt"}
    ).status_code == 404
    # 多级穿越同样被拒
    assert client.get(
        "/api/media/file", params={"root": root, "path": "../../etc/passwd"}
    ).status_code == 404


def test_file_rejects_unconfigured_root(client: TestClient, tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    (other / "x.png").write_bytes(b"xx")
    resp = client.get("/api/media/file", params={"root": str(other), "path": "x.png"})
    assert resp.status_code == 404


def test_add_root_persists(client: TestClient, tmp_path: Path) -> None:
    new_root = tmp_path / "newdir"
    new_root.mkdir()
    resp = client.post("/api/media/roots", json={"path": str(new_root)})
    assert resp.status_code == 200
    assert resp.json()["added"] is True
    paths = [r["path"] for r in resp.json()["roots"]]
    assert str(new_root.resolve()) in paths
    # 重复添加返回 added False
    dup = client.post("/api/media/roots", json={"path": str(new_root)})
    assert dup.json()["added"] is False
    # 已写入配置文件
    rendered = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert "[media]" in rendered
    assert "newdir" in rendered


def test_poster_falls_back_on_undecodable(client: TestClient) -> None:
    """无法解码的"视频"（假字节）应返回 404，而不是崩溃或返回坏图。"""
    root = client_config_root(client)
    resp = client.get("/api/media/poster", params={"root": root, "path": "clip.mkv"})
    assert resp.status_code == 404
    # 路径穿越/不存在同样被安全校验拒绝
    assert client.get(
        "/api/media/poster", params={"root": root, "path": "../secret.jpg"}
    ).status_code == 404
    assert client.get(
        "/api/media/poster", params={"root": root, "path": "不存在的.mp4"}
    ).status_code == 404


def test_item_state_post_get(client: TestClient) -> None:
    root = client_config_root(client)
    # 收藏 + 评级
    r = client.post("/api/media/item", json={"root": root, "path": "movie_a.mp4", "favorite": 1, "rating": 4})
    assert r.status_code == 200
    assert r.json() == {"favorite": 1, "rating": 4}
    # 读回
    got = client.get("/api/media/item", params={"root": root, "path": "movie_a.mp4"}).json()
    assert got == {"favorite": 1, "rating": 4}
    # 取消收藏但保留评级
    r2 = client.post("/api/media/item", json={"root": root, "path": "movie_a.mp4", "favorite": 0})
    assert r2.json() == {"favorite": 0, "rating": 4}
    # 两者皆空 → 400
    assert client.post("/api/media/item", json={"root": root, "path": "movie_a.mp4"}).status_code == 400
    # 越界/不存在 → 404
    assert client.post(
        "/api/media/item", json={"root": root, "path": "../x.mp4", "favorite": 1}
    ).status_code == 404


def test_list_rich_with_state(client: TestClient) -> None:
    root = client_config_root(client)
    client.post("/api/media/item", json={"root": root, "path": "photo_b.jpg", "favorite": 1, "rating": 5})
    items = client.get("/api/media/list", params={"root": root}).json()["items"]
    by_name = {i["name"]: i for i in items}
    assert by_name["photo_b.jpg"]["favorite"] == 1
    assert by_name["photo_b.jpg"]["rating"] == 5
    assert by_name["movie_a.mp4"]["favorite"] == 0
    assert by_name["movie_a.mp4"]["rating"] == 0


def test_favorites_list(client: TestClient, media_root: Path) -> None:
    root = client_config_root(client)
    client.post("/api/media/item", json={"root": root, "path": "photo_b.jpg", "favorite": 1})
    client.post("/api/media/item", json={"root": root, "path": "movie_a.mp4", "favorite": 1, "rating": 3})
    favs = client.get("/api/media/favorites").json()["items"]
    names = {f["name"] for f in favs}
    assert names == {"photo_b.jpg", "movie_a.mp4"}
    assert {f["kind"] for f in favs} == {"image", "video"}
    # kind 过滤视频
    vids = client.get("/api/media/favorites", params={"kind": "video"}).json()["items"]
    assert [f["name"] for f in vids] == ["movie_a.mp4"]
    # 移除收藏后不在列表
    client.post("/api/media/item", json={"root": root, "path": "photo_b.jpg", "favorite": 0})
    favs2 = client.get("/api/media/favorites").json()["items"]
    assert "photo_b.jpg" not in {f["name"] for f in favs2}


def test_delete_moves_to_trash(client: TestClient, media_root: Path, tmp_path: Path) -> None:
    root = client_config_root(client)
    client.post("/api/media/item", json={"root": root, "path": "movie_a.mp4", "favorite": 1})
    resp = client.delete("/api/media/item", params={"root": root, "path": "movie_a.mp4"})
    assert resp.status_code == 200
    assert resp.json()["deleted"] == "movie_a.mp4"
    # 原文件已不在根目录
    assert not (media_root / "movie_a.mp4").exists()
    # 状态已清除（文件不存在 → 404）
    assert client.get("/api/media/item", params={"root": root, "path": "movie_a.mp4"}).status_code == 404
    # 收藏列表不再包含它
    assert "movie_a.mp4" not in {f["name"] for f in client.get("/api/media/favorites").json()["items"]}
    # 列表不再显示
    names = {i["name"] for i in client.get("/api/media/list", params={"root": root}).json()["items"]}
    assert "movie_a.mp4" not in names
    # 进入回收站（tmp_path/media_trash/...）可找回
    assert any(p for p in (tmp_path / "media_trash").rglob("movie_a.mp4"))


def test_delete_rejects_traversal_and_missing(client: TestClient) -> None:
    root = client_config_root(client)
    assert client.delete("/api/media/item", params={"root": root, "path": "../x.mp4"}).status_code == 404
    assert client.delete("/api/media/item", params={"root": root, "path": "不存在.mp4"}).status_code == 404


def client_config_root(client: TestClient) -> str:
    """取第一个已配置根目录的绝对路径，用于 list/file 参数。"""
    return client.get("/api/media/roots").json()["roots"][0]["path"]
