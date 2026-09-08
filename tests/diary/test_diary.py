"""日记系统单元测试。

覆盖数据模型、存储层、业务逻辑和导入器的核心功能。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openbiliclaw.diary import (
    DiaryEntryCreate,
    DiaryEntryUpdate,
    DiaryService,
    DiaryStats,
    MoodLevel,
)
from openbiliclaw.diary.importer import DiaryImporter
from openbiliclaw.diary.store import DiaryStore


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> str:
    """提供临时数据库路径。"""
    return str(tmp_path / "test_diary.db")


@pytest.fixture
def store(tmp_db_path: str) -> DiaryStore:
    """提供已初始化的 DiaryStore。"""
    s = DiaryStore(db_path=tmp_db_path)
    s.initialize()
    return s


@pytest.fixture
def service(tmp_db_path: str) -> DiaryService:
    """提供已初始化的 DiaryService。"""
    return DiaryService(db_path=tmp_db_path)


class TestModels:
    """数据模型测试。"""

    def test_diary_entry_create(self):
        data = DiaryEntryCreate(
            entry_date="2026-09-06",
            title="测试",
            content="内容",
            source="manual",
            tags=["a", "b"],
            mood=MoodLevel.HAPPY,
        )
        assert data.entry_date == "2026-09-06"
        assert data.mood == MoodLevel.HAPPY

    def test_mood_level_values(self):
        assert MoodLevel.VERY_HAPPY.value == "very_happy"
        assert MoodLevel.UNKNOWN.value == "unknown"


class TestDiaryStore:
    """存储层测试。"""

    def test_create_and_get_entry(self, store: DiaryStore):
        data = DiaryEntryCreate(
            entry_date="2026-09-06",
            title="测试日记",
            content="这是测试内容",
            source="manual",
            tags=["测试"],
            mood=MoodLevel.HAPPY,
        )
        entry = store.create_entry(data)
        assert entry.id > 0
        assert entry.title == "测试日记"
        assert entry.word_count == 6

        got = store.get_entry(entry.id)
        assert got.id == entry.id
        assert got.content == "这是测试内容"

    def test_update_entry(self, store: DiaryStore):
        entry = store.create_entry(DiaryEntryCreate(entry_date="2026-09-06", content="原始内容"))
        updated = store.update_entry(
            entry.id,
            DiaryEntryUpdate(title="新标题", content="更新后的内容", mood=MoodLevel.SAD),
        )
        assert updated.title == "新标题"
        assert updated.content == "更新后的内容"
        assert updated.mood == MoodLevel.SAD
        assert updated.word_count == 6

    def test_delete_entry(self, store: DiaryStore):
        entry = store.create_entry(DiaryEntryCreate(entry_date="2026-09-06", content="待删除"))
        store.delete_entry(entry.id)
        with pytest.raises(ValueError):
            store.get_entry(entry.id)

    def test_list_entries_with_filters(self, store: DiaryStore):
        store.create_entry(
            DiaryEntryCreate(
                entry_date="2026-09-01", content="第一篇", mood=MoodLevel.HAPPY, source="manual"
            )
        )
        store.create_entry(
            DiaryEntryCreate(
                entry_date="2026-09-02", content="第二篇", mood=MoodLevel.SAD, source="import_lele"
            )
        )
        store.create_entry(
            DiaryEntryCreate(
                entry_date="2026-09-03", content="第三篇", mood=MoodLevel.HAPPY, source="manual"
            )
        )

        # 全部
        all_entries = store.list_entries(limit=10)
        assert len(all_entries) == 3

        # 按情绪筛选
        happy = store.list_entries(mood=MoodLevel.HAPPY)
        assert len(happy) == 2

        # 按来源筛选
        lele = store.list_entries(source="import_lele")
        assert len(lele) == 1

        # 按日期范围
        date_range = store.list_entries(start_date="2026-09-02", end_date="2026-09-02")
        assert len(date_range) == 1

        # 搜索
        search = store.list_entries(search="第一篇")
        assert len(search) == 1

    def test_count_entries(self, store: DiaryStore):
        store.create_entry(DiaryEntryCreate(entry_date="2026-09-01", content="a"))
        store.create_entry(DiaryEntryCreate(entry_date="2026-09-02", content="b"))
        assert store.count_entries() == 2
        assert store.count_entries(start_date="2026-09-02") == 1

    def test_get_stats(self, store: DiaryStore):
        store.create_entry(
            DiaryEntryCreate(
                entry_date="2026-09-01", content="第一篇日记", tags=["生活"], mood=MoodLevel.HAPPY
            )
        )
        store.create_entry(
            DiaryEntryCreate(
                entry_date="2026-09-02", content="第二篇日记内容", tags=["工作"], mood=MoodLevel.SAD
            )
        )
        stats = store.get_stats()
        assert isinstance(stats, DiaryStats)
        assert stats.total_entries == 2
        assert stats.total_words > 0
        assert stats.earliest_date == "2026-09-01"
        assert stats.latest_date == "2026-09-02"
        assert "happy" in stats.mood_distribution
        assert len(stats.top_tags) == 2

    def test_create_and_get_analysis(self, store: DiaryStore):
        entry = store.create_entry(DiaryEntryCreate(entry_date="2026-09-06", content="测试"))
        analysis_data = {
            "summary": "测试摘要",
            "key_points": ["要点1", "要点2"],
            "emotions": {"开心": 0.8},
            "themes": ["测试"],
            "people_mentioned": ["小明"],
            "growth_insight": "成长洞察",
            "mood_score": 0.5,
        }
        analysis = store.create_analysis(entry.id, analysis_data, model_used="test-model")
        assert analysis.id > 0
        assert analysis.summary == "测试摘要"
        assert len(analysis.key_points) == 2

        # 通过日记 ID 获取
        got = store.get_analysis_by_diary(entry.id)
        assert got is not None
        assert got.id == analysis.id

        # 日记的 analysis_id 应被更新
        updated_entry = store.get_entry(entry.id)
        assert updated_entry.analysis_id == analysis.id

    def test_get_unanalyzed_entries(self, store: DiaryStore):
        e1 = store.create_entry(DiaryEntryCreate(entry_date="2026-09-01", content="a"))
        e2 = store.create_entry(DiaryEntryCreate(entry_date="2026-09-02", content="b"))
        store.create_analysis(e1.id, {"summary": "test"}, model_used="test")

        unanalyzed = store.get_unanalyzed_entries()
        assert len(unanalyzed) == 1
        assert unanalyzed[0].id == e2.id


class TestDiaryService:
    """业务逻辑层测试。"""

    def test_create_and_list(self, service: DiaryService):
        service.create_entry(
            DiaryEntryCreate(entry_date="2026-09-06", title="测试", content="内容")
        )
        entries, total = service.list_entries()
        assert total == 1
        assert len(entries) == 1

    def test_get_stats(self, service: DiaryService):
        service.create_entry(DiaryEntryCreate(entry_date="2026-09-06", content="测试内容"))
        stats = service.get_stats()
        assert stats.total_entries == 1

    def test_search_entries(self, service: DiaryService):
        service.create_entry(DiaryEntryCreate(entry_date="2026-09-06", content="今天天气很好"))
        service.create_entry(DiaryEntryCreate(entry_date="2026-09-07", content="工作很忙"))
        results = service.search_entries("天气")
        assert len(results) == 1

    def test_get_timeline(self, service: DiaryService):
        service.create_entry(DiaryEntryCreate(entry_date="2026-09-01", content="a"))
        service.create_entry(DiaryEntryCreate(entry_date="2026-08-15", content="b"))
        service.create_entry(DiaryEntryCreate(entry_date="2025-12-01", content="c"))

        # 按年
        year_2026 = service.get_timeline(year=2026)
        assert len(year_2026) == 2

        # 按月
        month_sep = service.get_timeline(year=2026, month=9)
        assert len(month_sep) == 1


class TestDiaryImporter:
    """导入器测试。"""

    def test_import_lele_format(self, service: DiaryService, tmp_path: Path):
        content = """生活 - 乐乐日记

1、测试条目一

2025年04月08日
1、日期测试一
2、日期测试二

2024年12月06日
1、更早的日记
"""
        test_file = tmp_path / "test_lele.txt"
        test_file.write_text(content, encoding="utf-8")

        importer = DiaryImporter(service)
        count, entries = importer.import_lele_diary(str(test_file))
        assert count == 2  # 两个有日期的分组
        assert entries[0].entry_date == "2025-04-08"
        assert entries[1].entry_date == "2024-12-06"

    def test_import_generic_text(self, service: DiaryService, tmp_path: Path):
        content = """2026年09月01日

今天是美好的一天，记录一下生活。

2026年09月02日

工作很忙，但很充实。
"""
        test_file = tmp_path / "test.txt"
        test_file.write_text(content, encoding="utf-8")

        importer = DiaryImporter(service)
        count, entries = importer.import_text_file(str(test_file))
        assert count == 2

    def test_import_markdown(self, service: DiaryService, tmp_path: Path):
        content = """# 2026年09月01日 第一篇

这是第一篇日记的内容。

# 2026年09月02日 第二篇

这是第二篇日记的内容。
"""
        test_file = tmp_path / "test.md"
        test_file.write_text(content, encoding="utf-8")

        importer = DiaryImporter(service)
        count, entries = importer.import_markdown_file(str(test_file))
        assert count == 2

    def test_import_idempotent(self, service: DiaryService, tmp_path: Path):
        """测试重复导入不会产生重复数据。"""
        content = """2025年04月08日
1、测试条目
"""
        test_file = tmp_path / "test.txt"
        test_file.write_text(content, encoding="utf-8")

        importer = DiaryImporter(service)
        count1, _ = importer.import_lele_diary(str(test_file))
        count2, _ = importer.import_lele_diary(str(test_file))
        assert count1 == 1
        assert count2 == 0  # 第二次应跳过已存在的
        assert service.get_stats().total_entries == 1

    def test_import_nonexistent_file(self, service: DiaryService):
        importer = DiaryImporter(service)
        with pytest.raises(FileNotFoundError):
            importer.import_lele_diary("/nonexistent/path.txt")
