"""聊天记录导入器。

支持从 mindback_data 目录导入数据：
1. chat_data_for_ai.db / chat_exports.db → 原始聊天消息
2. deepseek-analysis/ → DeepSeek AI 分析结果
3. 项目已有 articles (source_type='chat-analysis') → 迁移到新模块
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .models import (
    ChatAnalysisChunkCreate,
    ChatMessageCreate,
    ChatSessionCreate,
    ChatType,
    DeepseekAnalysisImportResult,
    ImportStats,
    MessageType,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .store import ChatAnalysisStore


class ChatImporter:
    """聊天记录导入器。

    从 mindback_data 的 SQLite 数据库和 DeepSeek 分析文件导入数据。
    """

    def __init__(self, store: ChatAnalysisStore):
        self.store = store

    # ── 从 SQLite 导入原始聊天消息 ──

    def import_from_sqlite(
        self,
        db_path: str | Path,
        max_sessions: int = 0,
        max_messages_per_session: int = 0,
    ) -> ImportStats:
        """从 chat_data_for_ai.db 或 chat_exports.db 导入聊天消息。

        Args:
            db_path: SQLite 数据库路径
            max_sessions: 最大导入会话数（0=全部）
            max_messages_per_session: 每会话最大消息数（0=全部）

        """
        db_path = Path(db_path)
        if not db_path.exists():
            raise FileNotFoundError(f"数据库文件不存在: {db_path}")

        stats = ImportStats(
            sessions_imported=0, messages_imported=0, analysis_chunks_imported=0, skipped=0
        )

        conn = sqlite3.connect(str(db_path), timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        # v0.4.0+: articles 表迁移到 content.db，ATTACH 以便跨库查询
        from pathlib import Path as _Path
        from contextlib import suppress as _suppress
        _content_path = _Path(str(db_path)).with_name("content.db")
        if _content_path.exists():
            with _suppress(Exception):
                conn.execute("ATTACH DATABASE ? AS content", (str(_content_path),))

        try:
            # 检测数据库结构
            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            ]
            has_sessions = "chat_sessions" in tables
            has_exports = "chat_exports" in tables

            if has_sessions or has_exports:
                session_table = "chat_sessions" if has_sessions else "chat_exports"
                id_col = "id"
                title_col = "title" if has_sessions else "chat_name"
                msg_table = "chat_messages"
                fk_col = "session_id" if has_sessions else "chat_id"
                type_col = "chat_type"
                count_col = "message_count"
                file_col = "file_path"
                file_size_col = "file_size"

                sessions_sql = f"SELECT * FROM {session_table} ORDER BY {count_col} DESC"
                if max_sessions > 0:
                    sessions_sql += f" LIMIT {max_sessions}"

                sessions = conn.execute(sessions_sql).fetchall()

                for s in sessions:
                    title = s[title_col] or "未命名会话"
                    existing = self.store.get_session_by_title(title)
                    if existing:
                        stats.skipped += 1
                        continue

                    ct = s[type_col] if s[type_col] else ""
                    chat_type = (
                        ChatType.GROUP
                        if ct == "群聊"
                        else (ChatType.PRIVATE if ct == "私聊" else None)
                    )

                    session_data = ChatSessionCreate(
                        title=title,
                        source_file=s["source_file"] or "",
                        time_range="最早 ~ 最新",
                        export_time=dict(s).get("export_time", ""),
                        message_count=s[count_col],
                        chat_type=chat_type,
                        file_path=s[file_col] if file_col else "",
                        file_size=s[file_size_col] if file_size_col else 0,
                    )
                    session = self.store.create_session(session_data)
                    stats.sessions_imported += 1

                    # 导入消息
                    msg_sql = f"SELECT * FROM {msg_table} WHERE {fk_col} = ? ORDER BY timestamp ASC"
                    if max_messages_per_session > 0:
                        msg_sql += f" LIMIT {max_messages_per_session}"

                    messages = conn.execute(msg_sql, (s[id_col],)).fetchall()
                    batch_size = 500
                    for i in range(0, len(messages), batch_size):
                        batch = messages[i : i + batch_size]
                        msg_creates = []
                        for m in batch:
                            mt = m.get("message_type", "text")
                            msg_creates.append(
                                ChatMessageCreate(
                                    session_id=session.id,
                                    timestamp=m["timestamp"] if m["timestamp"] else "",
                                    sender=m.get("sender", ""),
                                    content=m["content"] if m["content"] else "",
                                    message_type=self._map_message_type(mt),
                                )
                            )
                        self.store.create_messages_batch(msg_creates)
                        stats.messages_imported += len(msg_creates)

            # 也检查是否有 chat_messages 直接关联
            else:
                # 尝试直接读取 chat_messages 表（如果只有消息表）
                logger.warning("未知数据库结构: %s", db_path)
                stats.errors.append(f"未知数据库结构: {db_path}")

        finally:
            conn.close()

        return stats

    def _map_message_type(self, mt: str) -> MessageType:
        mt_lower = mt.lower().strip()
        if mt_lower in ("text", "文本"):
            return MessageType.TEXT
        elif mt_lower in ("media", "image", "video", "audio", "图片", "视频"):
            return MessageType.MEDIA
        elif mt_lower in ("self", "自己", "me"):
            return MessageType.SELF
        else:
            return MessageType.SYSTEM

    # ── 从 DeepSeek 分析目录导入 ──

    def import_from_deepseek_analysis(
        self,
        analysis_dir: str | Path | None = None,
    ) -> list[DeepseekAnalysisImportResult]:
        """从 deepseek-analysis/ 目录导入 AI 分析结果。

        Args:
            analysis_dir: deepseek-analysis 目录路径

        """
        if analysis_dir is None:
            raise ValueError("analysis_dir 不能为空，请指定 DeepSeek 分析目录路径")
        base_dir = Path(analysis_dir)
        if not base_dir.exists():
            raise FileNotFoundError(f"分析目录不存在: {base_dir}")

        results: list[DeepseekAnalysisImportResult] = []

        for group_dir in sorted(base_dir.iterdir()):
            if not group_dir.is_dir():
                continue
            session_title = self._extract_session_title(group_dir.name)
            if not session_title:
                continue

            # 解析分析文件
            txt_files = sorted(group_dir.glob("analysis_*.txt"), key=self._chunk_sort_key)
            if not txt_files:
                continue

            result = DeepseekAnalysisImportResult(
                session_title=session_title,
                chunks_imported=0,
                total_lines=0,
            )

            for txt_file in txt_files:
                start_line, end_line = self._parse_chunk_range(txt_file.stem)
                if start_line is None:
                    continue

                try:
                    content = txt_file.read_text(encoding="utf-8", errors="replace")
                    lines = content.strip().split("\n")
                    result.total_lines += len(lines)

                    chunk_data = ChatAnalysisChunkCreate(
                        session_title=session_title,
                        start_line=start_line,
                        end_line=end_line,
                        analysis_content=content,
                        analysis_file=str(txt_file),
                        model_used="deepseek",
                    )
                    self.store.create_analysis_chunk(chunk_data)
                    result.chunks_imported += 1
                except Exception as e:
                    result.errors.append(f"{txt_file.name}: {e}")

            results.append(result)

        return results

    def _extract_session_title(self, dir_name: str) -> str:
        """从目录名提取会话标题，去掉 _分析结果 后缀。"""
        name = dir_name.replace("_分析结果", "").replace("_分析", "")
        # 移除可能的多余后缀
        if name.endswith("_分析结果"):
            name = name[:-5]
        return name.strip()

    def _parse_chunk_range(self, stem: str) -> tuple[int | None, int | None]:
        """从文件名 analysis_0_3000 中提取行号范围。"""
        m = re.search(r"analysis_(\d+)_(\d+)", stem)
        if m:
            return int(m.group(1)), int(m.group(2))
        return None, None

    def _chunk_sort_key(self, path: Path) -> tuple[int, int]:
        """按 (起始行, 结束行) 排序。"""
        start, end = self._parse_chunk_range(path.stem)
        return (start or 0, end or 0)

    # ── 从项目已有 articles 导入 ──

    def import_from_existing_articles(
        self,
        db_path: str | Path,
    ) -> ImportStats:
        """从项目的 openbiliclaw.db 迁移已有的 chat-analysis 文章。

        Args:
            db_path: openbiliclaw.db 路径

        """
        db_path = Path(db_path)
        if not db_path.exists():
            raise FileNotFoundError(f"数据库不存在: {db_path}")

        stats = ImportStats(
            sessions_imported=0, messages_imported=0, analysis_chunks_imported=0, skipped=0
        )

        conn = sqlite3.connect(str(db_path), timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        # v0.4.0+: articles 表迁移到 content.db，ATTACH 以便跨库查询
        from pathlib import Path as _Path
        from contextlib import suppress as _suppress
        _content_path = _Path(str(db_path)).with_name("content.db")
        if _content_path.exists():
            with _suppress(Exception):
                conn.execute("ATTACH DATABASE ? AS content", (str(_content_path),))

        try:
            rows = conn.execute(
                "SELECT * FROM articles WHERE source_type = 'chat-analysis' ORDER BY id"
            ).fetchall()

            logger.info("找到 %d 条 chat-analysis 文章", len(rows))

            for row in rows:
                row["title"]
                content = row["content_text"] or ""
                url = row["url"]  # chat-analysis://groupName/range

                # 从 URL 提取会话标题和范围
                session_title, start_line, end_line = self._parse_article_url(url)

                if not session_title:
                    stats.errors.append(f"无法解析 URL: {url}")
                    stats.skipped += 1
                    continue

                # 检查是否已存在
                existing = self.store.get_session_by_title(session_title)
                if not existing:
                    session_data = ChatSessionCreate(
                        title=session_title,
                        source_file="",
                        message_count=0,
                        chat_type=ChatType.GROUP if "群" in session_title else ChatType.PRIVATE,
                    )
                    self.store.create_session(session_data)
                    stats.sessions_imported += 1

                # 导入分析片段
                chunk_data = ChatAnalysisChunkCreate(
                    session_title=session_title,
                    start_line=start_line or 0,
                    end_line=end_line or 0,
                    analysis_content=content,
                    analysis_file=url,
                    model_used="deepseek",
                )
                self.store.create_analysis_chunk(chunk_data)
                stats.analysis_chunks_imported += 1

        finally:
            conn.close()

        return stats

    def _parse_article_url(self, url: str) -> tuple[str | None, int | None, int | None]:
        """从文章 URL 解析会话标题和行范围。
        URL 格式: chat-analysis://groupName/第15000-18000行
        """
        if not url.startswith("chat-analysis://"):
            return None, None, None
        path = url[len("chat-analysis://") :]
        parts = path.split("/", 1)
        if len(parts) != 2:
            return parts[0] if parts else None, None, None

        session_title = parts[0]
        range_str = parts[1]
        m = re.search(r"(\d+)-(\d+)", range_str)
        if m:
            return session_title, int(m.group(1)), int(m.group(2))
        return session_title, None, None

    # ── 批量导入全部 ──

    def import_all(
        self,
        mindback_root: str | Path,
        openbiliclaw_db: str | Path,
        max_sessions: int = 0,
        max_messages: int = 0,
    ) -> dict[str, Any]:
        """从所有已知来源批量导入数据。

        Args:
            mindback_root: mindback_data 根目录（必填）
            openbiliclaw_db: openbiliclaw.db 路径（必填）
            max_sessions: 最大导入会话数（0=全部）
            max_messages: 每会话最大消息数（0=全部）

        """
        base = Path(mindback_root)
        results: dict[str, Any] = {}

        # 1. 导入 chat_data_for_ai.db
        db1 = base / "chat_data_for_ai.db"
        if db1.exists():
            logger.info("从 chat_data_for_ai.db 导入...")
            results["chat_data_for_ai"] = self.import_from_sqlite(
                db1,
                max_sessions=max_sessions,
                max_messages_per_session=max_messages,
            )

        # 2. 导入 chat_exports.db
        db2 = base / "chat_exports.db"
        if db2.exists():
            logger.info("从 chat_exports.db 导入...")
            results["chat_exports"] = self.import_from_sqlite(
                db2,
                max_sessions=max_sessions,
                max_messages_per_session=max_messages,
            )

        # 3. 导入 DeepSeek 分析
        analysis_dir = base / "deepseek-analysis"
        if analysis_dir.exists():
            logger.info("从 deepseek-analysis 导入...")
            results["deepseek_analysis"] = self.import_from_deepseek_analysis(analysis_dir)

        # 4. 从现有文章迁移
        if openbiliclaw_db:
            db_path = Path(openbiliclaw_db)
            if db_path.exists():
                logger.info("从现有文章迁移 chat-analysis...")
                results["existing_articles"] = self.import_from_existing_articles(db_path)

        return results
