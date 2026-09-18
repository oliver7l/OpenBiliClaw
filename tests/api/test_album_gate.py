"""``/album`` 乐仔家庭相册的**门禁**回归。

为什么单独锁这一组：``api/auth.py::_is_public()`` 对「不以 ``/api`` 开头」的
路径一律放行，是给 SPA 壳、favicon 这类静态资源开的绿灯。但后端由 frpc 把
8420 **整个端口**直通公网（``infra/frpc/frpc-passnat4.toml``），相册一旦走
默认放行，5206 张家庭照片就是公网裸奔——而且不报任何错，只有"照片被谁看过"
才知道。故登记进 ``_PROTECTED_STATIC_PREFIXES``，本文件锁住四条契约：

1. 未登录取照片（缩略图 / 原图 / HEIC 预览）一律 401，不因「非 /api」放行；
2. 未登录的 **HTML 导航** 302 到公开登录页并带回跳地址（手机上别看到裸 JSON），
   而图片等子资源仍回 401（不连环跳转）；
3. 登录页 + PWA 图标/manifest 保持公开（否则未登录打不开登录页 → 死循环，
   iOS「添加到主屏幕」也取不到图标）；
4. 登录后带 session cookie 即可取图，且既有 ``/web``、``/lezai``、
   ``/api/health`` 不被误伤；
5. 无 Cookie 客户端（微信小程序 ``<image>``）走 ``?k=<媒体签名>`` 放行，但签名
   **只能换图片字节**——开不了相册页面、换不到 ``/api`` 会话、碰不到门禁管理端点
   （签名会随图片 URL 传播，越权面必须卡死）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openbiliclaw import auth_core as ac
from tests.api.test_api_auth import _build_app, _remote

_PASSWORD = "hunter2"

# 线上姿态：密码门开启且**不**信任 loopback（frpc 的入口注释明确要求如此，
# 否则公网流量经 127.0.0.1 进来会被当成"本机"绕过密码）。
_REMOTE_WITH_GATE = dict(enabled=True, password=_PASSWORD, trust_loopback=False)

_THUMB = "/album/thumbs/A_1.jpg"
_HEIC = "/album/heic/A_2.jpg"
_ORIGINAL = "/album/full/2024-05/A_3.jpg"

_FAKE_JPEG = b"\xff\xd8\xff\xe0fake-jpeg-bytes"


def _fake_source(tmp_path: Path) -> Path:
    """造一个最小源库（缩略图 / HEIC 预览 / 按月原图各一张）。"""
    src = tmp_path / "photos"
    for relative in ("_thumbs/A_1.jpg", "_heic_jpg/A_2.jpg", "2024-05/A_3.jpg"):
        path = src / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_FAKE_JPEG)
    return src


@pytest.fixture
def gated_remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """带门禁的远方客户端 + 已挂载的假源库。"""
    monkeypatch.setenv("OBC_ALBUM_SOURCE_DIR", str(_fake_source(tmp_path)))
    app, _db = _build_app(tmp_path, monkeypatch, **_REMOTE_WITH_GATE)
    return _remote(app)


def test_photos_are_blocked_without_session(gated_remote: TestClient) -> None:
    """未登录取照片必须 401——这是本模块存在的唯一理由。"""
    for url in (_THUMB, _HEIC, _ORIGINAL):
        response = gated_remote.get(url)
        assert response.status_code == 401, f"{url} 未过门禁：{response.status_code}"


def test_html_navigation_redirects_to_public_login(gated_remote: TestClient) -> None:
    """浏览器导航 → 302 到登录页，且带回跳地址。

    目标必须带**末尾斜杠**：登录页是 ``StaticFiles(html=True)`` 下的目录
    （``login/index.html``），缺斜杠时 Starlette 会先插一次 307「目录补斜杠」，
    那条重定向按请求 Host 拼绝对 URL，经 frp 反代（Host 可能是 127.0.0.1）会把
    浏览器送去 ``https://127.0.0.1/album/login/`` —— 手机端登录页直接打不开。
    """
    response = gated_remote.get(
        "/album/", headers={"accept": "text/html"}, follow_redirects=False
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/album/login/?next=/album/"


def test_subresource_is_not_redirected(gated_remote: TestClient) -> None:
    """图片等子资源不带 text/html → 保持 401，不跟风跳转。"""
    response = gated_remote.get(
        _THUMB, headers={"accept": "image/avif,image/webp,*/*"}, follow_redirects=False
    )
    assert response.status_code == 401


def test_login_surface_stays_public(gated_remote: TestClient) -> None:
    """登录页与 PWA 资产不能被门禁拦住（403/401 都算拦）。"""
    for url in ("/album/login", "/album/assets/icon-192.png", "/album/manifest.json"):
        response = gated_remote.get(url, follow_redirects=False)
        assert response.status_code not in (401, 403), f"{url} 被门禁拦了：{response.status_code}"


def test_session_cookie_unlocks_photos(gated_remote: TestClient) -> None:
    """登录后 cookie 生效 → 照片从 401 变成可取（同源子请求自动带 cookie）。"""
    assert gated_remote.get(_THUMB).status_code == 401

    login = gated_remote.post("/api/auth/login", json={"password": _PASSWORD})
    assert login.status_code == 200

    for url in (_THUMB, _HEIC, _ORIGINAL):
        response = gated_remote.get(url)
        assert response.status_code == 200, f"{url} 登录后仍不可取：{response.status_code}"
        assert response.content == _FAKE_JPEG


def test_existing_public_surfaces_unaffected(gated_remote: TestClient) -> None:
    """既有公开路径不能被这次收紧误伤。"""
    for url in ("/web", "/lezai/", "/api/health"):
        response = gated_remote.get(url, follow_redirects=False)
        assert response.status_code not in (401, 403), f"{url} 被误伤：{response.status_code}"


def test_gate_disabled_leaves_album_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """门禁关闭时相册回到公开（本地开发/桌面端不受影响）。"""
    monkeypatch.setenv("OBC_ALBUM_SOURCE_DIR", str(_fake_source(tmp_path)))
    app, _db = _build_app(tmp_path, monkeypatch, enabled=False)
    client = _remote(app)
    assert client.get(_THUMB).status_code == 200


# ── 小程序媒体签名通道（cookieless） ─────────────────────────────────────────
#
# 微信小程序的 <image> 既不带 Cookie 也带不了自定义 header，照片只能靠 URL 上
# 的 ``?k=<签名>`` 放行。这一组锁住三件事：签名能取图、错误签名取不到、以及
# **签名不能越权**（打不开页面、换不到 /api 会话）。

_MEDIA_SECRET = "test-media-secret"


def _media_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("OBC_ALBUM_SOURCE_DIR", str(_fake_source(tmp_path)))
    app, _db = _build_app(
        tmp_path,
        monkeypatch,
        enabled=True,
        password=_PASSWORD,
        session_secret=_MEDIA_SECRET,
        trust_loopback=False,
    )
    return _remote(app)


def _signed(url: str) -> str:
    return f"{url}?k={ac.album_media_token(_MEDIA_SECRET)}"


def test_media_signature_unlocks_photos_without_cookie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """带正确签名 → 三档图片都能取到，且字节与源库一致。"""
    client = _media_client(tmp_path, monkeypatch)
    assert client.get(_THUMB).status_code == 401  # 裸请求仍必须被拦

    for url in (_THUMB, _HEIC, _ORIGINAL):
        response = client.get(_signed(url))
        assert response.status_code == 200, f"{url} 签名后仍不可取：{response.status_code}"
        assert response.content == _FAKE_JPEG


def test_wrong_media_signature_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """错误/空签名必须 401——否则等于没门禁。"""
    client = _media_client(tmp_path, monkeypatch)
    bogus = ac.album_media_token("some-other-secret")
    for url in (_THUMB, f"{_THUMB}?k=", f"{_THUMB}?k={bogus}", f"{_THUMB}?k=short"):
        assert client.get(url).status_code == 401, f"{url} 竟被放行"


def test_media_signature_cannot_escalate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """签名只换图片字节：既开不了相册页面，也拿不到 /api。

    这是本方案的安全边界——媒体签名会随着图片 URL 传播（相册页、分享、日志），
    一旦它能开页面或调接口，泄露面就从「某几张照片」扩大到「整个服务」。
    """
    client = _media_client(tmp_path, monkeypatch)

    page = client.get(
        _signed("/album/"), headers={"accept": "text/html"}, follow_redirects=False
    )
    assert page.status_code == 302, "媒体签名竟然打开了相册页面"

    assert client.get(_signed("/api/interview/todos"), follow_redirects=False).status_code == 401

    # 门禁管理端点（可改密码/开关）尤其不能被媒体签名碰到。该路由自身也是
    # 公开的（让 handler 对非本地调用回具体的 403），所以这里断言的是
    # 「拿不到成功响应」而不是具体的 401。
    admin = client.post(_signed("/api/auth/admin"), json={}, follow_redirects=False)
    assert admin.status_code in (401, 403), f"媒体签名触碰到了门禁管理端点：{admin.status_code}"


def test_media_signature_disabled_when_gate_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """门禁关闭时媒体签名不再是必要条件（照片本来就公开）。"""
    monkeypatch.setenv("OBC_ALBUM_SOURCE_DIR", str(_fake_source(tmp_path)))
    app, _db = _build_app(tmp_path, monkeypatch, enabled=False)
    client = _remote(app)
    assert client.get(_THUMB).status_code == 200
