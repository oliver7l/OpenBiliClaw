"""有道云笔记（note.youdao.com）日记来源适配器。

机制：Cookie 注入 + 浏览器自动化（新版编辑器正文在 ``bulb-editor`` iframe 内，
必须连 iframe 一起取）。官方旧版下载 API 已不可用（``request not valid``），
所以这里走浏览器路线，与 2026-09-06 的历史导入一致。

⚠️ 需要 ``data/cookies/youdao.json``（登录态）。**未经实测**：DOM 选择器来自
历史导入记录，首次实跑需用 ``--debug-dump`` 校对。
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

LOGIN_URL = "https://note.youdao.com/web/"
DOMAIN = ".youdao.com"
LOGIN_MARKERS = ("/login", "login.youdao.com", "account.youdao.com")

# 候选选择器：左侧目录树 -> 文件项。以历史导入记录为准，实跑时按需增补。
FILE_ITEM_SELECTORS = (
    "#file-list li.file-item",
    ".file-list .file-item",
    ".file-tree-item",
    "li[data-id]",
)


class YoudaoNoteSource:
    """把有道云笔记里的日记读成 ``RawNote``。"""

    name = "youdao_note"

    def __init__(self, folder: str | None = None, debug_dump: Path | None = None) -> None:
        self.folder = folder
        self.debug_dump = debug_dump

    # ── 内部步骤 ────────────────────────────────────────────────

    def _iter_file_items(self, page: Any) -> Iterator[Any]:
        for selector in FILE_ITEM_SELECTORS:
            items = page.query_selector_all(selector)
            if items:
                yield from items
                return

    def _open_and_read(self, page: Any, item: Any) -> str:
        item.click()
        page.wait_for_timeout(1200)  # SPA 切笔记没有可靠事件，稍等渲染
        return page_text(page)

    # ── 协议实现 ────────────────────────────────────────────────

    def fetch(self, since: str | None = None) -> Iterator[RawNote]:
        """抓取有道云笔记中的日记。"""
        cookies = load_cookies("youdao", DOMAIN, LOGIN_URL)
        with browser_context("youdao", cookies) as context:
            page = context.new_page()
            page.goto(LOGIN_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            assert_logged_in(page, LOGIN_MARKERS)
            if self.debug_dump:
                self.debug_dump.write_text(page.content(), encoding="utf-8")
            for index, item in enumerate(self._iter_file_items(page)):
                label = (item.inner_text() or "").strip()
                title = label.splitlines()[0] if label else ""
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
                        title=title or "有道云笔记",
                        body=parts[0][1],
                        note_date=parts[0][0] or note_day,
                        source=self.name,
                        raw_id=f"youdao#{index}",
                    )
                    continue
                for date_str, part in parts:
                    yield RawNote(
                        title=f"{title}（{date_str}）" if date_str else title,
                        body=part,
                        note_date=date_str or note_day,
                        source=self.name,
                        raw_id=f"youdao#{index}#{date_str}",
                    )


__all__ = ["YoudaoNoteSource"]
