"""日记多来源导入的单元测试。

苹果备忘录部分用**合成 NoteStore**（自造 gzip + protobuf 正文）跑端到端，
因此不依赖 macOS 完全磁盘访问权限，CI 上也能跑。
"""

from __future__ import annotations

import gzip
import sqlite3
from pathlib import Path

import pytest

from openbiliclaw.diary.service import DiaryService
from openbiliclaw.diary.sources.apple_notes import AppleNotesSource, apple_date, connect
from openbiliclaw.diary.sources.base import (
    RawNote,
    dedup_key,
    find_date,
    split_monthly_summary,
)
from openbiliclaw.diary.sources.upsert import import_notes

APPLE_EPOCH_OFFSET = 978307200  # 2001-01-01 相对 Unix epoch 的秒数


# ── 构造合成 Apple Notes 库 ──────────────────────────────────────


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _field(number: int, payload: bytes) -> bytes:
    return _varint((number << 3) | 2) + _varint(len(payload)) + payload


def _zdata(text: str, *, compress: bool = True) -> bytes:
    """按 Apple Notes 的嵌套结构生成 ZDATA：{2:{3:{2:text}}}。"""
    content = _field(2, text.encode("utf-8"))
    document = _field(3, content)
    raw = _field(2, document)
    return gzip.compress(raw) if compress else raw


def _unix_to_apple(iso_date: str) -> float:
    import datetime as dt

    moment = dt.datetime.fromisoformat(iso_date).replace(tzinfo=dt.UTC)
    return moment.timestamp() - APPLE_EPOCH_OFFSET


def _build_store(path: Path, notes: list[dict[str, object]], folders: list[tuple[int, str, int | None]] = ()) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE ZICCLOUDSYNCINGOBJECT (
            Z_PK INTEGER PRIMARY KEY,
            ZTITLE1 TEXT, ZTITLE2 TEXT, ZPARENT INTEGER,
            ZFOLDER INTEGER, ZFOLDERTYPE INTEGER,
            ZCREATIONDATE REAL, ZCREATIONDATE1 REAL, ZMODIFICATIONDATE1 REAL,
            ZMARKEDFORDELETION INTEGER
        );
        CREATE TABLE ZICNOTEDATA (
            Z_PK INTEGER PRIMARY KEY, ZNOTE INTEGER, ZDATA BLOB
        );
        """
    )
    for pk, title, parent in folders:
        conn.execute(
            "INSERT INTO ZICCLOUDSYNCINGOBJECT (Z_PK, ZTITLE2, ZPARENT, ZFOLDERTYPE) VALUES (?,?,?,2)",
            (pk, title, parent),
        )
    for index, note in enumerate(notes):
        pk = int(note.get("pk", 100 + index))
        conn.execute(
            """INSERT INTO ZICCLOUDSYNCINGOBJECT
               (Z_PK, ZTITLE1, ZFOLDER, ZCREATIONDATE1, ZMODIFICATIONDATE1, ZMARKEDFORDELETION)
               VALUES (?,?,?,?,?,?)""",
            (
                pk,
                note.get("title"),
                note.get("folder"),
                _unix_to_apple(str(note.get("created", "2026-09-12"))),
                _unix_to_apple(str(note.get("created", "2026-09-12"))),
                note.get("deleted", 0),
            ),
        )
        conn.execute(
            "INSERT INTO ZICNOTEDATA (Z_PK, ZNOTE, ZDATA) VALUES (?,?,?)",
            (pk, pk, _zdata(str(note["body"]), compress=bool(note.get("gzip", True)))),
        )
    conn.commit()
    conn.close()
    return path


# ── base 纯函数 ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026年9月3日", "2026-09-03"),
        ("日记 2026-09-03 晴", "2026-09-03"),
        ("202026年09月03日", "2026-09-03"),
        ("没有日期", None),
    ],
)
def test_find_date(text: str, expected: str | None) -> None:
    assert find_date(text) == expected


def test_dedup_key_normalizes_whitespace_and_truncates() -> None:
    body = "  今天   带乐乐\n去上英语课。" + "x" * 100
    key = dedup_key(body)
    assert len(key) == 50
    assert key.startswith("今天 带乐乐 去上英语课。")


def test_split_monthly_summary_splits_on_date_lines() -> None:
    text = "2022年06月07日\n今天第一天。\n2022年06月08日\n今天第二天。"
    parts = split_monthly_summary(text)
    assert [date for date, _ in parts] == ["2022-06-07", "2022-06-08"]
    assert parts[0][1] == "今天第一天。"


def test_split_monthly_summary_keeps_single_section() -> None:
    text = "普通的一篇日记，正文里提到 2022年06月07日 但不是分隔行。"
    parts = split_monthly_summary(text, fallback_date="2026-09-12")
    assert parts == [("2026-09-12", text)]


# ── apple_notes 端到端（合成库） ─────────────────────────────────


def test_apple_source_reads_notes(tmp_path: Path) -> None:
    db = _build_store(
        tmp_path / "NoteStore.sqlite",
        [
            {"title": "2026年09月12日", "body": "2026年09月12日\n今天带乐乐上英语课。", "folder": 1},
            {"title": "无日期标题", "body": "随手写的一篇。", "folder": 1, "created": "2026-08-01"},
        ],
        folders=[(1, "每日记录", 2), (2, "生活", None)],
    )
    source = AppleNotesSource(database=db)
    notes = list(source.fetch())
    assert len(notes) == 2
    by_body = {note.body: note for note in notes}
    dated = next(note for body, note in by_body.items() if "带乐乐上英语课" in body)
    assert dated.note_date == "2026-09-12"
    assert dated.source == "apple_notes"
    # 无日期笔记回退到创建日期
    undated = next(note for body, note in by_body.items() if "随手写的一篇" in body)
    assert undated.note_date == "2026-08-01"


def test_apple_source_folder_filter_and_since(tmp_path: Path) -> None:
    db = _build_store(
        tmp_path / "NoteStore.sqlite",
        [
            {"title": "A", "body": "2026年01月01日\n甲。", "folder": 1},
            {"title": "B", "body": "2026年09月12日\n乙。", "folder": 2},
        ],
        folders=[(1, "每日记录", None), (2, "别的目录", None)],
    )
    only_daily = list(AppleNotesSource(database=db, folder="每日记录").fetch())
    assert [note.body for note in only_daily] == ["甲。"]
    recent = list(AppleNotesSource(database=db).fetch(since="2026-06-01"))
    assert [note.body for note in recent] == ["乙。"]


def test_apple_source_handles_plain_zdata(tmp_path: Path) -> None:
    """非 gzip 的 ZDATA 也要能解（历史库中两种情况都有）。"""
    db = _build_store(
        tmp_path / "NoteStore.sqlite",
        [{"title": "2026年09月12日", "body": "2026年09月12日\n未压缩正文。", "gzip": False}],
    )
    notes = list(AppleNotesSource(database=db).fetch())
    assert "未压缩正文" in notes[0].body


def test_connect_missing_database_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        connect(tmp_path / "nope.sqlite")


def test_apple_date_converts_timestamp() -> None:
    assert apple_date(0) is None
    assert apple_date(None) is None
    assert str(apple_date(_unix_to_apple("2026-09-12")))[:10] == "2026-09-12"


# ── upsert 幂等 ─────────────────────────────────────────────────


def _note(body: str, date: str = "2026-09-12") -> RawNote:
    return RawNote(title="t", body=body, note_date=date, source="apple_notes", raw_id="1")


def test_import_notes_dry_run_writes_nothing(tmp_path: Path) -> None:
    service = DiaryService(db_path=str(tmp_path / "diary.db"))
    stats = import_notes(service, [_note("2026年09月12日\n新内容。")], source="apple_notes", dry_run=True)
    assert stats.imported == 1
    assert service.store.list_entries(limit=10) == []


def test_import_notes_is_idempotent(tmp_path: Path) -> None:
    service = DiaryService(db_path=str(tmp_path / "diary.db"))
    payload = [_note("2026年09月12日\n同一条内容，重复导入应被跳过。")]
    first = import_notes(service, payload, source="apple_notes", dry_run=False)
    assert first.imported == 1
    second = import_notes(service, payload, source="apple_notes", dry_run=False)
    assert second.imported == 0
    assert second.skipped == 1
    assert len(service.store.list_entries(limit=10)) == 1


def test_import_notes_dedupes_within_batch(tmp_path: Path) -> None:
    service = DiaryService(db_path=str(tmp_path / "diary.db"))
    payload = [_note("2026年09月12日\n批内重复。"), _note("2026年09月12日\n批内重复。")]
    stats = import_notes(service, payload, source="apple_notes", dry_run=False)
    assert stats.imported == 1
    assert stats.skipped == 1
