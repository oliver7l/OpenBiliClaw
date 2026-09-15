"""Vendored Apple Notes 正文解码层（纯函数，不含 IO）。

来源：https://github.com/ingjieye/apple-notes-cli （MIT License，见同目录 LICENSE）
vendor 日期：2026-09-14 ｜ 仅取 ``parser.py`` + ``models.py``，未作逻辑改动。

用途：把 Apple Notes ``ZICNOTEDATA.ZDATA`` 字段（gzip + protobuf）解码成
Markdown 正文。本目录**不接受本地修改**，如需变更请在上层
``sources/apple_notes.py`` 做适配，保持与上游可对照。

上游设计纪律（值得保留）：``parser.py`` 不碰 SQLite、不碰文件系统，全部是
``bytes/text -> text`` 的纯函数，因此可以被单测直接覆盖。
"""

from .parser import extract_note_markdown, normalize_note_text

__all__ = ["extract_note_markdown", "normalize_note_text"]
