"""苹果备忘录（Apple Notes）日记来源适配器。

**只读 SQLite**，不用 AppleScript、不用 UI 自动化、不用云 API：

* ``mode=ro`` 打开 ``NoteStore.sqlite``，WAL 模式下允许并发读，Notes.app
  运行中也安全，且永远看到最新提交；
* **绝不用 ``immutable=1``** —— 该 flag 会跳过 ``-wal``，静默返回陈旧快照；
* 正文解码复用 vendor 的 ``_apple_notes.parser``（gzip + protobuf，纯函数）。

⚠️ ``~/Library/Group Containers/group.com.apple.notes/`` 受 macOS TCC 保护，
宿主进程需要「完全磁盘访问权限」，否则会抛 :class:`NotesAccessError`。
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

from ._apple_notes import extract_note_markdown
from .base import RawNote, find_date, split_monthly_summary

APPLE_EPOCH = dt.datetime(2001, 1, 1, tzinfo=dt.UTC)
DEFAULT_NOTES_DIR = Path.home() / "Library/Group Containers/group.com.apple.notes"

REMEDIATION = (
    "无法读取 Apple Notes 数据库（macOS TCC 保护）。\n"
    "  解决：系统设置 → 隐私与安全性 → 完全磁盘访问权限 → 勾选 WorkBuddy.app，\n"
    "  然后完全退出并重新打开该 App 再试。\n"
    "  数据库路径：{path}"
)


class NotesAccessError(RuntimeError):
    """Apple Notes 数据库存在但当前进程无权读取（TCC / 沙箱）。"""


def default_database(notes_dir: Path | None = None) -> Path:
    """返回 NoteStore.sqlite 的默认路径。"""
    return (notes_dir or DEFAULT_NOTES_DIR) / "NoteStore.sqlite"


def connect(database: Path) -> sqlite3.Connection:
    """以只读方式打开 Notes 数据库。

    Raises:
        FileNotFoundError: 数据库不存在（本机从未启动过备忘录）。
        NotesAccessError: 存在但无权读取（需授予完全磁盘访问权限）。

    """
    if not database.exists():
        raise FileNotFoundError(f"Apple Notes 数据库不存在：{database}（本机需先启动过「备忘录」App）")
    # 先做一次裸读取探测，把 TCC 拒绝翻译成人话（sqlite 只会报 unable to open database file）
    try:
        with database.open("rb") as handle:
            handle.read(16)
    except PermissionError as exc:
        raise NotesAccessError(REMEDIATION.format(path=database)) from exc
    try:
        conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    except sqlite3.OperationalError as exc:  # pragma: no cover - 平台相关
        raise NotesAccessError(REMEDIATION.format(path=database)) from exc
    conn.row_factory = sqlite3.Row
    return conn


def apple_date(value: object) -> str | None:
    """把 Apple/Core Data 时间戳（自 2001-01-01 起的秒）转成 ISO 字符串。"""
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric <= 0:
        return None
    return (APPLE_EPOCH + dt.timedelta(seconds=numeric)).isoformat()


def query_folder_paths(conn: sqlite3.Connection) -> dict[int, str]:
    """构建「文件夹主键 -> 完整路径」映射（形如 ``生活/每日记录``）。"""
    rows = conn.execute("SELECT Z_PK, ZTITLE2, ZPARENT FROM ZICCLOUDSYNCINGOBJECT WHERE ZTITLE2 IS NOT NULL").fetchall()
    titles = {int(row["Z_PK"]): (row["ZTITLE2"] or "Notes").strip() for row in rows}
    parents = {int(row["Z_PK"]): row["ZPARENT"] for row in rows}
    cache: dict[int, str] = {}

    def build(folder_id: int, seen: set[int] | None = None) -> str:
        if folder_id in cache:
            return cache[folder_id]
        seen = seen or set()
        if folder_id in seen or folder_id not in titles:
            return titles.get(folder_id, "Notes")
        seen.add(folder_id)
        parent = parents.get(folder_id)
        if parent is None or int(parent) not in titles:
            cache[folder_id] = titles[folder_id]
        else:
            cache[folder_id] = f"{build(int(parent), seen)}/{titles[folder_id]}"
        return cache[folder_id]

    for folder_id in titles:
        build(folder_id)
    return cache


def trashed_folder_pks(conn: sqlite3.Connection) -> set[int]:
    """「最近删除」文件夹的主键（含其下所有子文件夹）。

    ``ZFOLDERTYPE == 1`` 标记 Recently Deleted，与界面语言无关；仅凭
    ``ZMARKEDFORDELETION`` 过滤是拦不住垃圾箱里的笔记的。
    """
    rows = conn.execute(
        "SELECT Z_PK, ZPARENT, ZFOLDERTYPE FROM ZICCLOUDSYNCINGOBJECT WHERE ZTITLE2 IS NOT NULL"
    ).fetchall()
    parents = {int(row["Z_PK"]): row["ZPARENT"] for row in rows}
    roots = {int(row["Z_PK"]) for row in rows if row["ZFOLDERTYPE"] == 1}
    trashed = set(roots)
    for folder_pk in parents:
        chain: set[int] = set()
        current: int | None = folder_pk
        while current is not None and current not in chain:
            chain.add(current)
            if current in roots:
                trashed |= chain
                break
            parent = parents.get(current)
            current = int(parent) if parent is not None else None
    return trashed


def _day(iso_value: str | None) -> str | None:
    return iso_value[:10] if iso_value else None


class AppleNotesSource:
    """把 Apple Notes「每日记录」型文件夹里的笔记读成 ``RawNote``。"""

    name = "apple_notes"

    def __init__(
        self,
        database: Path | None = None,
        notes_dir: Path | None = None,
        folder: str | None = None,
        decode: Callable[[bytes], str] = extract_note_markdown,
    ) -> None:
        self.notes_dir = notes_dir or DEFAULT_NOTES_DIR
        self.database = database or default_database(self.notes_dir)
        self.folder = folder
        self._decode = decode

    # ── 内部查询 ────────────────────────────────────────────────

    def _iter_rows(self, conn: sqlite3.Connection) -> Iterator[sqlite3.Row]:
        where = ["d.ZDATA IS NOT NULL", "IFNULL(n.ZMARKEDFORDELETION, 0) = 0"]
        trashed = trashed_folder_pks(conn)
        if trashed:
            placeholders = ", ".join(str(int(pk)) for pk in sorted(trashed))
            where.append(f"IFNULL(n.ZFOLDER, 0) NOT IN ({placeholders})")
        sql = f"""
            SELECT n.Z_PK, n.ZTITLE1, n.ZFOLDER, n.ZCREATIONDATE, n.ZCREATIONDATE1,
                   n.ZMODIFICATIONDATE1, d.ZDATA
            FROM ZICCLOUDSYNCINGOBJECT n
            JOIN ZICNOTEDATA d ON n.Z_PK = d.ZNOTE
            WHERE {" AND ".join(where)}
            ORDER BY n.ZMODIFICATIONDATE1 ASC, n.Z_PK ASC
        """
        yield from conn.execute(sql)

    def _matches_folder(self, folder_path: str) -> bool:
        if not self.folder:
            return True
        if folder_path == self.folder:
            return True
        return self.folder in folder_path.split("/")

    # ── 协议实现 ────────────────────────────────────────────────

    def fetch(self, since: str | None = None) -> Iterator[RawNote]:
        """产出苹果备忘录中的日记条目（已按日期分隔符拆分月度汇总）。"""
        conn = connect(self.database)
        try:
            folder_paths = query_folder_paths(conn)
            for row in self._iter_rows(conn):
                folder_path = folder_paths.get(row["ZFOLDER"], "Notes") if row["ZFOLDER"] else "Notes"
                if not self._matches_folder(folder_path):
                    continue
                try:
                    body = self._decode(row["ZDATA"])
                except Exception:  # noqa: BLE001 - 单条解码失败不应中断整批导入
                    continue
                body = (body or "").strip()
                if not body:
                    continue
                title = (row["ZTITLE1"] or "").strip() or body.splitlines()[0].strip()
                created = (
                    _day(apple_date(row["ZCREATIONDATE1"]))
                    or _day(apple_date(row["ZCREATIONDATE"]))
                    or _day(apple_date(row["ZMODIFICATIONDATE1"]))
                )
                note_day = find_date(title) or find_date(body[:120]) or created
                if since and note_day and note_day < since:
                    continue
                raw_id = str(row["Z_PK"])

                parts = split_monthly_summary(body, fallback_date=note_day)
                if len(parts) == 1:
                    yield RawNote(
                        title=title,
                        body=parts[0][1],
                        note_date=parts[0][0] or note_day,
                        source=self.name,
                        raw_id=raw_id,
                    )
                    continue
                for date_str, part in parts:
                    yield RawNote(
                        title=f"{title}（{date_str}）" if date_str else title,
                        body=part,
                        note_date=date_str or note_day,
                        source=self.name,
                        raw_id=f"{raw_id}#{date_str}",
                    )
        finally:
            conn.close()


__all__ = [
    "DEFAULT_NOTES_DIR",
    "AppleNotesSource",
    "NotesAccessError",
    "apple_date",
    "connect",
    "default_database",
    "query_folder_paths",
    "trashed_folder_pks",
]
