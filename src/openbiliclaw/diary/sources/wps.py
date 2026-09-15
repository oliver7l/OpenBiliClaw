"""WPS 笔记（note.wps.cn）日记来源适配器。

机制：Cookie 注入 + 浏览器自动化。官方 WPS V7 开放平台**没有云盘文件列表
接口**（只有内容抽取），所以列表只能从页面 DOM 拿，与 2026-09-06 的历史导入一致。

⚠️ 需要 ``data/cookies/wps.json``（登录态）。**未经实测**：DOM 选择器来自历史
导入记录，首次实跑需用 ``--debug-dump`` 校对。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ._browser import (
    CookieExpiredError,
    assert_logged_in,
    browser_context,
    load_cookies,
    page_text,
)
from .base import RawNote, find_date, split_monthly_summary

LOGIN_URL = "https://note.wps.cn/"
DOMAIN = ".wps.cn"
LOGIN_MARKERS = ("/login", "account.wps.cn", "passport.wps.cn")

# 分组（左侧）与笔记项（中间列表）的候选选择器
GROUP_SELECTORS = (".group-item", ".folder-item", "li[data-group-id]")
NOTE_ITEM_SELECTORS = (".note-item", ".note-list-item", "li[data-note-id]")


class WpsNoteSource:
    """把 WPS 笔记里的日记读成 ``RawNote``。"""

    name = "wps_note"

    def __init__(self, folder: str | None = None, debug_dump: Path | None = None) -> None:
        self.folder = folder
        self.debug_dump = debug_dump

    # ── 内部步骤 ────────────────────────────────────────────────

    def _open_and_read(self, page: Any, item: Any) -> str:
        item.click()
        page.wait_for_timeout(1200)
        return page_text(page)

    def _find(self, page: Any, selectors: tuple[str, ...]) -> list[Any]:
        for selector in selectors:
            found = page.query_selector_all(selector)
            if found:
                return found
        return []

    # ── 协议实现 ────────────────────────────────────────────────

    def fetch(self, since: str | None = None) -> Iterator[RawNote]:
        """抓取 WPS 笔记中的日记。"""
        cookies = load_cookies("wps", DOMAIN, LOGIN_URL)
        with browser_context("wps", cookies) as context:
            page = context.new_page()
            page.goto(LOGIN_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            assert_logged_in(page, LOGIN_MARKERS)
            if self.debug_dump:
                self.debug_dump.write_text(page.content(), encoding="utf-8")

            groups = self._find(page, GROUP_SELECTORS)
            if not groups:
                groups = [None]
            index = 0
            for group in groups:
                if group is not None:
                    label = (group.inner_text() or "").strip().splitlines()[0]
                    if self.folder and self.folder not in label:
                        continue
                    group.click()
                    page.wait_for_timeout(1200)
                for item in self._find(page, NOTE_ITEM_SELECTORS):
                    label = (item.inner_text() or "").strip()
                    title = label.splitlines()[0] if label else ""
                    index += 1
                    try:
                        body = self._open_and_read(page, item)
                    except CookieExpiredError:
                        raise
                    except Exception:  # noqa: BLE001 - 单条失败跳过
                        continue
                    if not body:
                        continue
                    note_day = find_date(title) or find_date(body[:120])
                    if since and note_day and note_day < since:
                        continue
                    parts = split_monthly_summary(body, fallback_date=note_day)
                    if len(parts) <= 1:
                        yield RawNote(
                            title=title or "WPS 笔记",
                            body=parts[0][1],
                            note_date=parts[0][0] or note_day,
                            source=self.name,
                            raw_id=f"wps#{index}",
                        )
                        continue
                    for date_str, part in parts:
                        yield RawNote(
                            title=f"{title}（{date_str}）" if date_str else title,
                            body=part,
                            note_date=date_str or note_day,
                            source=self.name,
                            raw_id=f"wps#{index}#{date_str}",
                        )


__all__ = ["WpsNoteSource"]
