"""浏览器型来源（有道云 / WPS）的共享底座。

只做三件事：

1. 从 ``data/cookies/<site>.json`` 读取 Cookie（兼容三种落地格式）；
2. 用持久化 profile 启动 Chromium（首次注入 Cookie，之后复用登录态）；
3. 把「打开页面→取正文」这段通用流程抽出来给各适配器复用。

⚠️ 这两个来源依赖登录态，Cookie 会过期；过期时抛
:class:`CookieMissingError`，由 CLI 打印续期指引并非 0 退出。
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from openbiliclaw.config import _project_root

PROJECT_ROOT = _project_root()
COOKIE_DIR = PROJECT_ROOT / "data" / "cookies"
PROFILE_DIR = PROJECT_ROOT / "data"

_COOKIE_GUIDE = """缺少 {site} 的 Cookie。请：
  1. 浏览器登录 {url}
  2. 开发者工具 → Application → Cookies，全选导出
  3. 存成 JSON 到 {path}
     （支持三种格式：Playwright cookie 数组 / {{"cookies":[...]}} / 简单的 {{"名字":"值"}}）
  文件会以 0600 权限保存，且 data/ 已在 .gitignore 内，不会入库。"""


class CookieMissingError(RuntimeError):
    """Cookie 文件缺失或为空。"""


class CookieExpiredError(RuntimeError):
    """Cookie 存在但已失效（页面被踢回登录页）。"""


def cookie_path(site: str) -> Path:
    """返回某来源的 Cookie 文件路径。"""
    return COOKIE_DIR / f"{site}.json"


def load_cookies(site: str, domain: str, url: str) -> list[dict[str, Any]]:
    """读取并归一化 Cookie。

    Args:
        site: ``youdao`` / ``wps``。
        domain: 注入用的域（如 ``.youdao.com``）。
        url: 用于生成续期指引的登录地址。

    Raises:
        CookieMissingError: 文件不存在或解析后为空。

    """
    path = cookie_path(site)
    if not path.exists():
        raise CookieMissingError(_COOKIE_GUIDE.format(site=site, url=url, path=path))
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CookieMissingError(f"Cookie 文件无法解析：{path}（{exc}）") from exc

    if isinstance(raw, dict) and "cookies" in raw:
        raw = raw["cookies"]
    if isinstance(raw, list):
        cookies = [item for item in raw if isinstance(item, dict) and "name" in item]
        for cookie in cookies:
            cookie.setdefault("domain", domain)
            cookie.setdefault("path", "/")
        if cookies:
            return cookies
    if isinstance(raw, dict):
        simple = [
            {"name": str(name), "value": str(value), "domain": domain, "path": "/"} for name, value in raw.items()
        ]
        if simple:
            return simple
    raise CookieMissingError(_COOKIE_GUIDE.format(site=site, url=url, path=path))


@contextlib.contextmanager
def browser_context(site: str, cookies: list[dict[str, Any]]) -> Iterator[Any]:
    """启动持久化 Chromium 上下文并注入 Cookie。

    使用 ``data/<site>_browser_profile`` 作为 profile，登录态可跨次复用，
    因此 Cookie 只是「首次播种」，之后过期了直接换新文件即可。
    """
    from playwright.sync_api import sync_playwright

    profile = PROFILE_DIR / f"{site}_browser_profile"
    profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            context.add_cookies(cookies)
            yield context
        finally:
            context.close()


def page_text(page: Any, include_frames: bool = True) -> str:
    """取页面可见正文；同源 iframe（如有道云新版编辑器）一并合并。"""
    parts: list[str] = []
    with contextlib.suppress(Exception):
        parts.append(page.evaluate("() => document.body ? document.body.innerText : ''") or "")
    if include_frames:
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            with contextlib.suppress(Exception):
                text = frame.evaluate("() => document.body ? document.body.innerText : ''") or ""
                if text.strip() and text not in parts:
                    parts.append(text)
    return "\n".join(part for part in parts if part.strip()).strip()


def assert_logged_in(page: Any, markers: tuple[str, ...]) -> None:
    """粗暴但有效的登录态判断：URL/正文命中登录特征即视为过期。"""
    url = (page.url or "").lower()
    if any(marker in url for marker in markers):
        raise CookieExpiredError(f"页面被重定向到登录页（{page.url}），Cookie 已失效，请重新导出。")


__all__ = [
    "COOKIE_DIR",
    "CookieExpiredError",
    "CookieMissingError",
    "assert_logged_in",
    "browser_context",
    "cookie_path",
    "load_cookies",
    "page_text",
]
