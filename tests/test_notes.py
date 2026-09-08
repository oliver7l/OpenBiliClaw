"""笔记系统单元测试。

覆盖数据模型、存储层、业务逻辑、转写模块、合成模块和管线。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openbiliclaw.notes import (
    Note,
    NoteCreate,
    NoteListParams,
    NoteService,
    NoteStats,
    NoteStore,
    NoteTaskCreate,
    NoteUpdate,
    VideoToNotePipeline,
    VideoToNoteResult,
)
from openbiliclaw.notes.synthesis.generator import NoteGenerator
from openbiliclaw.notes.synthesis.prompts import (
    ARTICLE_LEARNING_PROMPT,
    ASR_RECTIFY_PROMPT,
    NOTE_GENERAL_PROMPT,
    NOTE_NEWS_PROMPT,
    NOTE_STUDY_PROMPT,
    get_prompt_for_content_type,
)
from openbiliclaw.notes.transcribe.cleaner import TextCleaner
from openbiliclaw.notes.transcribe.fetcher import AudioDownloader

# ── Fixtures ──


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> str:
    return str(tmp_path / "test_notes.db")


@pytest.fixture
def store(tmp_db_path: str) -> NoteStore:
    s = NoteStore(db_path=tmp_db_path)
    s.initialize()
    return s


@pytest.fixture
def service(tmp_db_path: str) -> NoteService:
    return NoteService(db_path=tmp_db_path)


# ── 数据模型 ──


class TestModels:
    """数据模型测试。"""

    def test_note_create(self):
        """测试 NoteCreate 模型创建。"""
        data = NoteCreate(
            title="测试笔记",
            content_md="# 测试\n\n这是笔记内容",
            note_type="video",
            source_platform="bilibili",
            source_ref="BV1xx",
            author="测试UP主",
            tags=["机器学习", "AI"],
            metadata={"duration": 120},
        )
        assert data.title == "测试笔记"
        assert data.note_type == "video"
        assert data.source_platform == "bilibili"
        assert len(data.tags) == 2
        assert data.metadata["duration"] == 120

    def test_note_create_defaults(self):
        """测试 NoteCreate 默认值。"""
        data = NoteCreate(title="仅标题")
        assert data.content_md == ""
        assert data.note_type == "manual"
        assert data.source_platform == ""
        assert data.tags == []
        assert data.metadata == {}

    def test_note_model(self):
        """测试 Note 完整模型。"""
        from datetime import datetime

        now = datetime.now()
        note = Note(
            id=1,
            title="测试",
            content_md="内容",
            note_type="video",
            source_platform="bilibili",
            tags=["标签"],
            metadata={"k": "v"},
            created_at=now,
            updated_at=now,
        )
        assert note.id == 1
        assert note.tags == ["标签"]
        assert note.metadata["k"] == "v"
        assert note.note_type == "video"

    def test_note_update(self):
        """测试 NoteUpdate 部分更新。"""
        update = NoteUpdate(title="新标题", tags=["新标签"])
        assert update.title == "新标题"
        assert update.tags == ["新标签"]
        assert update.content_md is None  # 未传的字段为 None

    def test_note_task_create(self):
        """测试 NoteTaskCreate 模型。"""
        data = NoteTaskCreate(
            task_id="abc12345",
            source_platform="bilibili",
            source_ref="BV1xx",
            resume_key="bilibili:BV1xx:v1",
        )
        assert data.task_id == "abc12345"
        assert data.resume_key == "bilibili:BV1xx:v1"

    def test_note_list_params(self):
        """测试 NoteListParams 查询参数。"""
        params = NoteListParams(
            limit=20,
            offset=10,
            note_type="video",
            search="机器学习",
            sort_by="updated_at",
            sort_order="ASC",
        )
        assert params.limit == 20
        assert params.offset == 10
        assert params.search == "机器学习"
        assert params.sort_by == "updated_at"

    def test_note_list_params_defaults(self):
        """测试 NoteListParams 默认值边界。"""
        params = NoteListParams()
        assert params.limit == 50
        assert params.offset == 0
        assert params.sort_by == "created_at"
        assert params.sort_order == "DESC"

    def test_note_stats(self):
        """测试 NoteStats 统计模型。"""
        stats = NoteStats(
            total=10,
            by_type={"video": 5, "manual": 5},
            by_platform={"bilibili": 8, "zhihu": 2},
            top_tags=[{"name": "AI", "count": 3}, {"name": "ML", "count": 2}],
        )
        assert stats.total == 10
        assert stats.by_type["video"] == 5
        assert len(stats.top_tags) == 2

    def test_note_model_serialization(self):
        """测试 Note 序列化为 dict。"""
        from datetime import datetime

        now = datetime.now()
        note = Note(
            id=1,
            title="测试",
            content_md="# 内容",
            source_platform="bilibili",
            created_at=now,
            updated_at=now,
        )
        d = note.model_dump(mode="json")
        assert d["id"] == 1
        assert d["title"] == "测试"
        assert d["source_platform"] == "bilibili"
        # tags 默认空列表
        assert d["tags"] == []


# ── 存储层 ──


class TestNoteStore:
    """存储层测试。"""

    def test_create_and_get_note(self, store: NoteStore):
        """测试创建和获取笔记。"""
        note = store.create_note(
            NoteCreate(
                title="测试笔记",
                content_md="# 测试\n\n这是笔记内容",
                note_type="video",
                source_platform="bilibili",
                source_ref="BV1xx",
                tags=["测试"],
            )
        )
        assert note.id > 0
        assert note.title == "测试笔记"
        assert note.note_type == "video"

        fetched = store.get_note(note.id)
        assert fetched is not None
        assert fetched.title == "测试笔记"
        assert fetched.content_md == "# 测试\n\n这是笔记内容"
        assert fetched.tags == ["测试"]

    def test_get_nonexistent_note(self, store: NoteStore):
        """测试获取不存在的笔记返回 None。"""
        assert store.get_note(99999) is None

    def test_update_note(self, store: NoteStore):
        """测试更新笔记。"""
        note = store.create_note(NoteCreate(title="原始标题", content_md="原始内容"))

        # 全字段更新
        updated = store.update_note(
            note.id,
            NoteUpdate(
                title="新标题",
                content_md="新内容",
                note_type="article",
                source_platform="zhihu",
                tags=["新标签"],
            ),
        )
        assert updated is not None
        assert updated.title == "新标题"
        assert updated.content_md == "新内容"
        assert updated.note_type == "article"
        assert updated.tags == ["新标签"]

    def test_update_partial(self, store: NoteStore):
        """测试部分更新（只更新标题）。"""
        note = store.create_note(NoteCreate(title="原始标题", content_md="原始内容", tags=["a"]))
        updated = store.update_note(note.id, NoteUpdate(title="仅改标题"))
        assert updated.title == "仅改标题"
        assert updated.content_md == "原始内容"  # 不变
        assert updated.tags == ["a"]  # 不变

    def test_update_nonexistent(self, store: NoteStore):
        """测试更新不存在的笔记返回 None。"""
        result = store.update_note(99999, NoteUpdate(title="新标题"))
        assert result is None

    def test_delete_note(self, store: NoteStore):
        """测试删除笔记。"""
        note = store.create_note(NoteCreate(title="待删除"))
        assert store.delete_note(note.id) is True
        assert store.get_note(note.id) is None

    def test_delete_nonexistent(self, store: NoteStore):
        """测试删除不存在的笔记返回 False。"""
        assert store.delete_note(99999) is False

    def test_list_notes(self, store: NoteStore):
        """测试列出笔记。"""
        for i in range(3):
            store.create_note(NoteCreate(title=f"笔记{i}", note_type="manual"))

        notes = store.list_notes(limit=10)
        assert len(notes) == 3

    def test_list_notes_filter_by_type(self, store: NoteStore):
        """测试按类型筛选。"""
        store.create_note(NoteCreate(title="视频笔记", note_type="video"))
        store.create_note(NoteCreate(title="文章笔记", note_type="article"))
        store.create_note(NoteCreate(title="手动笔记", note_type="manual"))

        videos = store.list_notes(note_type="video")
        assert len(videos) == 1
        assert videos[0].title == "视频笔记"

    def test_list_notes_filter_by_platform(self, store: NoteStore):
        """测试按平台筛选。"""
        store.create_note(NoteCreate(title="B站笔记", source_platform="bilibili"))
        store.create_note(NoteCreate(title="知乎笔记", source_platform="zhihu"))

        bilibili = store.list_notes(source_platform="bilibili")
        assert len(bilibili) == 1
        assert bilibili[0].title == "B站笔记"

    def test_list_notes_filter_by_tag(self, store: NoteStore):
        """测试按标签筛选。"""
        store.create_note(NoteCreate(title="机器学习", tags=["AI", "ML"]))
        store.create_note(NoteCreate(title="前端开发", tags=["前端", "JS"]))

        ai_notes = store.list_notes(tag="AI")
        assert len(ai_notes) == 1
        assert ai_notes[0].title == "机器学习"

    def test_list_notes_pagination(self, store: NoteStore):
        """测试分页。"""
        for i in range(5):
            store.create_note(NoteCreate(title=f"笔记{i}"))

        page1 = store.list_notes(limit=2, offset=0)
        assert len(page1) == 2

        page2 = store.list_notes(limit=2, offset=2)
        assert len(page2) == 2

        page3 = store.list_notes(limit=2, offset=4)
        assert len(page3) == 1

    def test_list_notes_sorting(self, store: NoteStore):
        """测试排序。"""
        store.create_note(NoteCreate(title="B笔记"))
        store.create_note(NoteCreate(title="A笔记"))
        store.create_note(NoteCreate(title="C笔记"))

        asc = store.list_notes(sort_by="title", sort_order="ASC")
        assert asc[0].title == "A笔记"
        assert asc[2].title == "C笔记"

        desc = store.list_notes(sort_by="title", sort_order="DESC")
        assert desc[0].title == "C笔记"
        assert desc[2].title == "A笔记"

    def test_fts_search(self, store: NoteStore):
        """测试 FTS5 全文搜索（trigram 分词）。"""
        store.create_note(
            NoteCreate(
                title="这是机器学习笔记",
                content_md="深度学习是人工智能的核心",
                tags=["AI技术"],
            )
        )
        store.create_note(
            NoteCreate(
                title="前端开发技巧",
                content_md="CSS Grid 布局的使用方法",
                tags=["前端技术"],
            )
        )

        # trigram 搜索，短语会自动分拆为 3-gram 匹配
        results = store.list_notes(search="深度学习")
        assert len(results) == 1
        assert results[0].title == "这是机器学习笔记"

        results = store.list_notes(search="人工智能")
        assert len(results) == 1

    def test_fts_search_content(self, store: NoteStore):
        """测试 FTS5 按内容搜索。"""
        store.create_note(NoteCreate(title="深度学习", content_md="Transformer 架构详解"))
        results = store.list_notes(search="Transformer")
        assert len(results) == 1

    def test_fts_search_tags(self, store: NoteStore):
        """测试 FTS5 按标签搜索。"""
        store.create_note(NoteCreate(title="测试", tags=["AI技术", "深度学习"]))
        results = store.list_notes(search="AI技术")
        assert len(results) == 1

    def test_count_notes(self, store: NoteStore):
        """测试统计笔记数量。"""
        store.create_note(NoteCreate(title="A", note_type="video"))
        store.create_note(NoteCreate(title="B", note_type="video"))
        store.create_note(NoteCreate(title="C", note_type="article"))

        assert store.count_notes() == 3
        assert store.count_notes(note_type="video") == 2
        assert store.count_notes(note_type="article") == 1

    def test_get_stats(self, store: NoteStore):
        """测试获取统计信息。"""
        store.create_note(
            NoteCreate(title="A", note_type="video", source_platform="bilibili", tags=["AI"])
        )
        store.create_note(
            NoteCreate(title="B", note_type="video", source_platform="bilibili", tags=["ML"])
        )
        store.create_note(
            NoteCreate(title="C", note_type="article", source_platform="zhihu", tags=["AI"])
        )

        stats = store.get_stats()
        assert stats["total"] == 3
        assert stats["by_type"]["video"] == 2
        assert stats["by_type"]["article"] == 1
        assert stats["by_platform"]["bilibili"] == 2
        assert stats["by_platform"]["zhihu"] == 1
        assert len(stats["top_tags"]) == 2  # AI:2, ML:1

    def test_get_stats_empty(self, store: NoteStore):
        """测试空库统计。"""
        stats = store.get_stats()
        assert stats["total"] == 0
        assert stats["by_type"] == {}
        assert stats["by_platform"] == {}
        assert stats["top_tags"] == []

    # ── 任务管理 ──

    def test_create_task(self, store: NoteStore):
        """测试创建任务。"""
        task = store.create_task(
            NoteTaskCreate(
                task_id="task001",
                source_platform="bilibili",
                source_ref="BV1xx",
                resume_key="bilibili:BV1xx:v1",
            )
        )
        assert task.task_id == "task001"
        assert task.status == "pending"
        assert task.current_stage == ""

    def test_create_duplicate_task(self, store: NoteStore):
        """测试创建重复任务（INSERT OR IGNORE）。"""
        data = NoteTaskCreate(
            task_id="task001",
            source_platform="bilibili",
            source_ref="BV1xx",
            resume_key="bilibili:BV1xx:v1",
        )
        store.create_task(data)
        # 同样的 resume_key 重复创建
        store.create_task(data)
        tasks = store.list_tasks()
        assert len(tasks) == 1

    def test_get_task(self, store: NoteStore):
        """测试获取任务。"""
        store.create_task(
            NoteTaskCreate(
                task_id="task001",
                source_platform="bilibili",
                source_ref="BV1xx",
                resume_key="bilibili:BV1xx:v1",
            )
        )
        task = store.get_task("task001")
        assert task is not None
        assert task.task_id == "task001"

    def test_get_task_by_resume_key(self, store: NoteStore):
        """测试按恢复键获取任务。"""
        store.create_task(
            NoteTaskCreate(
                task_id="task001",
                source_platform="bilibili",
                source_ref="BV1xx",
                resume_key="bilibili:BV1xx:v1",
            )
        )
        task = store.get_task_by_resume_key("bilibili:BV1xx:v1")
        assert task is not None
        assert task.task_id == "task001"

    def test_update_task_status(self, store: NoteStore):
        """测试更新任务状态。"""
        store.create_task(
            NoteTaskCreate(
                task_id="task001",
                source_platform="bilibili",
                source_ref="BV1xx",
                resume_key="bilibili:BV1xx:v1",
            )
        )
        store.update_task_status("task001", "running", "fetching")
        task = store.get_task("task001")
        assert task.status == "running"
        assert task.current_stage == "fetching"

        store.update_task_status("task001", "done", "", "全部完成")
        task = store.get_task("task001")
        assert task.status == "done"
        assert task.error_summary == "全部完成"

    def test_list_tasks_filter_by_status(self, store: NoteStore):
        """测试按状态筛选任务。"""
        store.create_task(
            NoteTaskCreate(
                task_id="t1",
                source_platform="bilibili",
                source_ref="BV1",
                resume_key="bilibili:BV1:v1",
            )
        )
        store.create_task(
            NoteTaskCreate(
                task_id="t2",
                source_platform="bilibili",
                source_ref="BV2",
                resume_key="bilibili:BV2:v1",
            )
        )
        store.update_task_status("t1", "done")

        running_tasks = store.list_tasks(status="pending")
        assert len(running_tasks) == 1
        assert running_tasks[0].task_id == "t2"

        done_tasks = store.list_tasks(status="done")
        assert len(done_tasks) == 1
        assert done_tasks[0].task_id == "t1"

    # ── 边界情况 ──

    def test_empty_title(self, store: NoteStore):
        """测试空标题也能创建。"""
        note = store.create_note(NoteCreate(title=""))
        assert note.id > 0

    def test_long_content(self, store: NoteStore):
        """测试长内容。"""
        long_md = "# 长文\n\n" + "这是内容。\n" * 1000
        note = store.create_note(NoteCreate(title="长文笔记", content_md=long_md))
        assert len(note.content_md) > 5000

    def test_many_tags(self, store: NoteStore):
        """测试大量标签。"""
        tags = [f"标签{i}" for i in range(50)]
        note = store.create_note(NoteCreate(title="多标签", tags=tags))
        assert len(note.tags) == 50


# ── 业务逻辑层 ──


class TestNoteService:
    """业务逻辑层测试。"""

    def test_create_and_list(self, service: NoteService):
        """测试创建和列出笔记。"""
        note = service.create_note(NoteCreate(title="测试服务", content_md="内容"))
        assert note.id > 0
        assert note.title == "测试服务"

        notes = service.list_notes(NoteListParams())
        assert len(notes) == 1

    def test_get_stats(self, service: NoteService):
        """测试统计。"""
        service.create_note(NoteCreate(title="A", note_type="video"))
        service.create_note(NoteCreate(title="B", note_type="article"))
        stats = service.get_stats()
        assert isinstance(stats, NoteStats)
        assert stats.total == 2
        assert stats.by_type["video"] == 1

    def test_search_notes(self, service: NoteService):
        """测试搜索。"""
        service.create_note(
            NoteCreate(
                title="机器学习基础",
                content_md="本文介绍监督学习的核心概念",
            )
        )
        service.create_note(
            NoteCreate(
                title="前端开发入门",
                content_md="HTML CSS JavaScript 基础教程",
            )
        )
        results = service.list_notes(NoteListParams(search="机器学习"))
        assert len(results) == 1

    def test_delete_and_count(self, service: NoteService):
        """测试删除和计数方法。"""
        note = service.create_note(NoteCreate(title="待删除"))
        assert service.count_notes(NoteListParams()) == 1

        service.delete_note(note.id)
        assert service.count_notes(NoteListParams()) == 0

    def test_create_task(self, service: NoteService):
        """测试创建任务（自动生成 task_id）。"""
        task = service.create_task("bilibili", "BV1xx")
        assert task.task_id is not None
        assert len(task.task_id) == 8
        assert task.source_platform == "bilibili"
        assert task.source_ref == "BV1xx"
        assert task.status == "pending"

    def test_create_task_custom_resume_key(self, service: NoteService):
        """测试自定义 resume_key。"""
        task = service.create_task("bilibili", "BV1xx", resume_key="custom:v1")
        assert task.resume_key == "custom:v1"

    def test_list_tasks(self, service: NoteService):
        """测试列出任务。"""
        service.create_task("bilibili", "BV1")
        service.create_task("zhihu", "ZH1")
        tasks = service.list_tasks()
        assert len(tasks) == 2

    def test_import_read_archive(self, service: NoteService, tmp_path: Path):
        """测试从已读库目录导入。"""
        # 创建模拟的已读库目录结构
        item_dir = tmp_path / "测试文章"
        item_dir.mkdir(parents=True)
        meta = {
            "title": "测试文章",
            "url": "https://example.com/article",
            "platform": "web",
            "author": "作者",
            "tags": ["测试", "导入"],
            "source_ref": "ref123",
        }
        (item_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        (item_dir / "content.md").write_text("# 测试文章\n\n这是导入内容", encoding="utf-8")
        (item_dir / "raw.html").write_text("<html></html>", encoding="utf-8")

        result = service.import_from_read_archive(str(tmp_path))
        assert result["imported"] == 1
        assert result["errors"] == 0

        # 验证入库
        notes = service.list_notes(NoteListParams())
        assert len(notes) == 1
        assert notes[0].title == "测试文章"
        assert notes[0].note_type == "import"

    def test_import_read_archive_skip_no_meta(self, service: NoteService, tmp_path: Path):
        """测试跳过缺少 meta.json 的目录。"""
        item_dir = tmp_path / "无元数据"
        item_dir.mkdir(parents=True)
        (item_dir / "content.md").write_text("内容", encoding="utf-8")

        result = service.import_from_read_archive(str(tmp_path))
        assert result["imported"] == 0
        assert result["skipped"] == 1

    def test_import_read_archive_not_found(self, service: NoteService):
        """测试不存在的目录。"""
        result = service.import_from_read_archive("/nonexistent")
        assert result["imported"] == 0
        assert result["message"] == "目录不存在"


# ── 文本清洗 ──


class TestTextCleaner:
    """文本清洗测试。"""

    def test_basic_cleaning(self):
        """测试基础清洗。"""
        result = TextCleaner.clean("你好，，，这是  一个  测试。。。真的吗？？？")
        assert result["cleaned_text"] == "你好，这是一个测试。真的吗？"

    def test_preserve_chinese_idioms(self):
        """测试保留中文成语（不破坏重复字）。"""
        result = TextCleaner.clean("代代相传，天天向上，常常学习")
        # 中文之间的空格被移除，但成语本身的重复字保留
        assert "代代相传" in result["cleaned_text"]
        assert "天天向上" in result["cleaned_text"]
        assert "常常学习" in result["cleaned_text"]

    def test_preserve_grammar_words(self):
        """测试保留语法动词。"""
        result = TextCleaner.clean("你是不是 是不是 这个 那个 什么")
        # 中文之间的空格被移除
        assert "你是不是" in result["cleaned_text"]
        assert "这个" in result["cleaned_text"]
        assert "那个" in result["cleaned_text"]

    def test_empty_text(self):
        """测试空文本。"""
        result = TextCleaner.clean("")
        assert result["cleaned_text"] == ""
        assert result["original_length"] == 0
        assert result["compression_ratio"] == 0.0

    def test_no_change_needed(self):
        """测试无需修改的文本。"""
        text = "这是一个正常的句子。"
        result = TextCleaner.clean(text)
        assert result["cleaned_text"] == text

    def test_multiple_newlines(self):
        """测试折叠多余空行。"""
        result = TextCleaner.clean("第一行\n\n\n\n第二行")
        assert result["cleaned_text"] == "第一行\n\n第二行"

    def test_line_whitespace(self):
        """测试行级空白去除。"""
        result = TextCleaner.clean("  第一行  \n  第二行  ")
        assert result["cleaned_text"] == "第一行\n第二行"

    def test_mixed_punctuation(self):
        """测试混合标点折叠。"""
        result = TextCleaner.clean("!!!测试？？？，，，。。。")
        assert "!" in result["cleaned_text"]
        # 折叠后只保留一个
        assert result["cleaned_text"].count("！") <= 1
        assert result["cleaned_text"].count("？") <= 1
        assert result["cleaned_text"].count("，") <= 1

    def test_compression_ratio(self):
        """测试压缩率计算。"""
        result = TextCleaner.clean("你好，，，世界")
        assert result["compression_ratio"] > 0
        assert result["original_length"] > result["cleaned_length"]

    def test_whitespace_between_chinese(self):
        """测试中文之间的空格被移除。"""
        result = TextCleaner.clean("机器 学习 是 人工 智能 的 子集")
        assert "机器 学习" not in result["cleaned_text"]
        assert "机器学习" in result["cleaned_text"]

    def test_only_whitespace(self):
        """测试纯空白字符。"""
        result = TextCleaner.clean("   \n  \n  ")
        assert result["cleaned_text"] == ""
        assert result["original_length"] == 9


# ── 音频下载器 ──


class TestAudioDownloader:
    """音频下载器测试。"""

    @pytest.mark.asyncio
    async def test_context_manager(self):
        """测试上下文管理器。"""
        async with AudioDownloader(cookie="test_cookie") as downloader:
            assert downloader._client is not None
            assert downloader._cookie == "test_cookie"
        # 退出后 client 应关闭
        assert downloader._client is None

    @pytest.mark.asyncio
    async def test_headers_with_cookie(self):
        """测试携带 Cookie 的请求头。"""
        async with AudioDownloader(cookie="test_cookie") as downloader:
            headers = dict(downloader._client.headers)
            # httpx 内部将请求头名统一转为小写
            assert "cookie" in headers
            assert headers["cookie"] == "test_cookie"
            assert "referer" in headers
            assert headers["referer"] == "https://www.bilibili.com/"

    @pytest.mark.asyncio
    async def test_download_http_error(self, tmp_path: Path):
        """测试下载时 HTTP 错误。"""
        mock_response = AsyncMock()
        mock_response.raise_for_status.side_effect = Exception("HTTP 404")
        mock_response.aiter_bytes.return_value = AsyncIterator([b"x"])

        class MockStreamContext:
            async def __aenter__(self):
                return mock_response

            async def __aexit__(self, *args):
                pass

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=MockStreamContext())

        with patch(
            "openbiliclaw.notes.transcribe.fetcher.httpx.AsyncClient", return_value=mock_client
        ):
            async with AudioDownloader() as downloader:
                with pytest.raises(RuntimeError, match="音频下载失败"):
                    await downloader.download_audio(
                        "https://example.com/audio",
                        str(tmp_path / "test"),
                    )

    @pytest.mark.asyncio
    async def test_max_bytes_limit(self, tmp_path: Path):
        """测试下载限速。"""

        class MockResponse:
            """模拟 httpx 响应，aiter_bytes 返回异步迭代器。"""

            raise_for_status = MagicMock(return_value=None)

            async def aiter_bytes(self, chunk_size: int):
                async for chunk in AsyncIterator([b"x" * 128]):
                    yield chunk

        class MockStreamContext:
            async def __aenter__(self):
                return MockResponse()

            async def __aexit__(self, *args):
                pass

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=MockStreamContext())

        with patch(
            "openbiliclaw.notes.transcribe.fetcher.httpx.AsyncClient", return_value=mock_client
        ):
            async with AudioDownloader() as downloader:
                with patch("shutil.which", return_value=None):
                    with patch.object(Path, "exists", return_value=True):
                        path = await downloader.download_audio(
                            "https://example.com/audio",
                            str(tmp_path / "test"),
                            max_bytes=1024,
                        )
                        assert path.endswith(".m4a")


class AsyncIterator:
    """将可迭代对象包装为异步迭代器。"""

    def __init__(self, items):
        self._items = items
        self._idx = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._idx >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._idx]
        self._idx += 1
        return item


# ── Prompt 模板 ──


class TestPrompts:
    """Prompt 模板测试。"""

    def test_get_prompt_for_study(self):
        """测试获取学习类 prompt。"""
        prompt = get_prompt_for_content_type("study")
        assert prompt == NOTE_STUDY_PROMPT
        assert "学习与复习笔记" in prompt

    def test_get_prompt_for_news(self):
        """测试获取资讯类 prompt。"""
        prompt = get_prompt_for_content_type("news")
        assert prompt == NOTE_NEWS_PROMPT
        assert "前沿资讯" in prompt

    def test_get_prompt_for_general(self):
        """测试获取通用类 prompt。"""
        prompt = get_prompt_for_content_type("general")
        assert prompt == NOTE_GENERAL_PROMPT
        assert "通用知识笔记" in prompt

    def test_get_prompt_for_article(self):
        """测试获取精读类 prompt。"""
        prompt = get_prompt_for_content_type("article")
        assert prompt == ARTICLE_LEARNING_PROMPT
        assert "系统化长文" in prompt

    def test_get_prompt_fallback(self):
        """测试未知类型回退到 article。"""
        prompt = get_prompt_for_content_type("unknown_type")
        assert prompt == ARTICLE_LEARNING_PROMPT

    def test_asr_rectify_prompt_format(self):
        """测试 ASR 校对 prompt 模板格式化。"""
        formatted = ASR_RECTIFY_PROMPT.format(
            domain_hint="计算机科学",
            raw_text="这是一个测试文本",
        )
        assert "计算机科学" in formatted
        assert "这是一个测试文本" in formatted

    def test_study_prompt_format(self):
        """测试学习类 prompt 模板格式化。"""
        formatted = NOTE_STUDY_PROMPT.format(
            title="机器学习入门",
            part_title="P1",
            content="这是课程内容",
        )
        assert "机器学习入门" in formatted
        assert "P1" in formatted
        assert "这是课程内容" in formatted


# ── 笔记生成器 ──


class TestNoteGenerator:
    """笔记生成器测试。"""

    @pytest.mark.asyncio
    async def test_generate_note(self):
        """测试生成笔记。"""
        mock_llm = MagicMock()
        mock_llm.complete_structured_task = AsyncMock(return_value=SimpleNamespace(content="# 生成的笔记\n\n这是内容"))

        generator = NoteGenerator(mock_llm)
        result = await generator.generate_note(
            title="测试视频",
            content="转录文本内容",
            content_type="article",
        )
        assert result == "# 生成的笔记\n\n这是内容"
        mock_llm.complete_structured_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_note_empty_content(self):
        """测试空内容不调用 LLM。"""
        mock_llm = MagicMock()
        generator = NoteGenerator(mock_llm)
        result = await generator.generate_note(title="测试", content="")
        assert result == ""
        mock_llm.complete_structured_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_rectify_asr(self):
        """测试 ASR 校对。"""
        mock_llm = MagicMock()
        mock_llm.complete_structured_task = AsyncMock(return_value=SimpleNamespace(content="校对后的文本"))

        generator = NoteGenerator(mock_llm)
        result = await generator.rectify_asr(raw_text="原始转录文本", domain_hint="计算机科学")
        assert result == "校对后的文本"

    @pytest.mark.asyncio
    async def test_rectify_asr_empty(self):
        """测试空文本不校对。"""
        mock_llm = MagicMock()
        generator = NoteGenerator(mock_llm)
        result = await generator.rectify_asr(raw_text="")
        assert result == ""
        mock_llm.complete_structured_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_rectify_asr_fallback_on_error(self):
        """测试 ASR 校对失败时回退到原始文本。"""
        mock_llm = MagicMock()
        mock_llm.complete_structured_task = AsyncMock(side_effect=Exception("LLM不可用"))

        generator = NoteGenerator(mock_llm)
        result = await generator.rectify_asr(raw_text="原始文本", domain_hint="测试")
        assert result == "原始文本"

    @pytest.mark.asyncio
    async def test_generate_note_raises_on_error(self):
        """测试笔记生成失败时向上抛出异常。"""
        mock_llm = MagicMock()
        mock_llm.complete_structured_task = AsyncMock(side_effect=Exception("LLM不可用"))

        generator = NoteGenerator(mock_llm)
        with pytest.raises(Exception, match="LLM不可用"):
            await generator.generate_note(title="测试", content="内容")


# ── 管线 ──


class TestVideoToNoteResult:
    """VideoToNoteResult 数据类测试。"""

    def test_default_values(self):
        """测试默认值。"""
        result = VideoToNoteResult()
        assert result.success is False
        assert result.note is None
        assert result.source == ""
        assert result.error == ""
        assert result.stages == {}

    def test_with_values(self):
        """测试赋值。"""
        note = MagicMock(spec=Note)
        result = VideoToNoteResult(
            success=True,
            note=note,
            source="subtitle",
            stages={"clean": {"length": 100}},
        )
        assert result.success is True
        assert result.note is note
        assert result.source == "subtitle"
        assert result.stages["clean"]["length"] == 100


class TestVideoToNotePipeline:
    """视频转笔记管线测试。"""

    @pytest.mark.asyncio
    async def test_pipeline_with_subtitle(self):
        """测试字幕路径。"""
        mock_bilibili = AsyncMock()
        mock_bilibili.get_video_info = AsyncMock(
            return_value=MagicMock(
                title="测试视频",
                up_name="测试UP主",
                duration=300,
                cid=12345,
            )
        )

        mock_llm = MagicMock()
        mock_llm.complete_structured_task = AsyncMock(return_value=SimpleNamespace(content="# 生成的笔记"))

        mock_note_service = MagicMock()
        mock_note_service.create_note.return_value = Note(
            id=1,
            title="测试视频",
            content_md="# 生成的笔记",
            note_type="video",
            source_platform="bilibili",
            source_ref="BV1xx",
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
        )

        pipeline = VideoToNotePipeline(
            bilibili_client=mock_bilibili,
            llm_service=mock_llm,
            note_service=mock_note_service,
            prefer_subtitle=True,
            content_type="article",
        )

        with patch("openbiliclaw.notes.pipeline.BilibiliSubtitleFetcher") as mock_fetcher:
            mock_fetcher_instance = AsyncMock()
            mock_fetcher_instance.__aenter__.return_value = mock_fetcher_instance
            mock_fetcher_instance.get_cid.return_value = 12345
            mock_fetcher_instance.fetch_subtitle_text.return_value = "这是字幕文本内容"
            mock_fetcher.return_value = mock_fetcher_instance

            result = await pipeline.run("BV1xx", save_note=True)

        assert result.success is True
        assert result.source == "subtitle"
        assert result.note is not None
        assert result.note.id == 1
        assert "video_info" in result.stages
        assert "subtitle" in result.stages
        assert "clean" in result.stages
        assert "note_generation" in result.stages
        assert "save" in result.stages

    @pytest.mark.asyncio
    async def test_pipeline_no_subtitle_fallback(self):
        """测试字幕失败走音频路径（但音频路径也失败时返回错误）。"""
        mock_bilibili = AsyncMock()
        mock_bilibili.get_video_info = AsyncMock(
            return_value=MagicMock(
                title="测试视频",
                up_name="UP主",
                duration=300,
                cid=12345,
            )
        )
        mock_bilibili.get_audio_streams = AsyncMock(return_value={"best_stream_url": ""})

        mock_llm = MagicMock()
        mock_llm.complete_structured_task = AsyncMock(return_value=SimpleNamespace(content="# 笔记"))

        pipeline = VideoToNotePipeline(
            bilibili_client=mock_bilibili,
            llm_service=mock_llm,
            prefer_subtitle=True,
            content_type="article",
        )

        with patch("openbiliclaw.notes.pipeline.BilibiliSubtitleFetcher") as mock_fetcher:
            mock_fetcher_instance = AsyncMock()
            mock_fetcher_instance.__aenter__.return_value = mock_fetcher_instance
            mock_fetcher_instance.get_cid.return_value = 0
            mock_fetcher.return_value = mock_fetcher_instance

            result = await pipeline.run("BV1xx", save_note=False)

        # 字幕失败（cid=0）且音频路径也失败（无 get_audio_streams 返回）
        assert result.success is False
        assert result.error != ""

    @pytest.mark.asyncio
    async def test_pipeline_no_llm_raw_text(self):
        """测试无 LLM 时直接用清洗文本。"""
        mock_bilibili = AsyncMock()
        mock_bilibili.get_video_info = AsyncMock(
            return_value=MagicMock(title="测试视频", up_name="UP主", duration=300, cid=12345)
        )

        pipeline = VideoToNotePipeline(
            bilibili_client=mock_bilibili,
            llm_service=None,
            prefer_subtitle=True,
            content_type="article",
        )

        with patch("openbiliclaw.notes.pipeline.BilibiliSubtitleFetcher") as mock_fetcher:
            mock_fetcher_instance = AsyncMock()
            mock_fetcher_instance.__aenter__.return_value = mock_fetcher_instance
            mock_fetcher_instance.get_cid.return_value = 12345
            mock_fetcher_instance.fetch_subtitle_text.return_value = (
                "  字幕文本  ，，有空格和标点。。。"
            )
            mock_fetcher.return_value = mock_fetcher_instance

            result = await pipeline.run("BV1xx", save_note=False)

        assert result.success is True
        assert result.source == "subtitle"
        assert result.stages["note_generation"]["mode"] == "raw_text_no_llm"

    @pytest.mark.asyncio
    async def test_pipeline_bilibili_api_error(self):
        """测试 B 站接口错误。"""
        mock_bilibili = AsyncMock()
        mock_bilibili.get_video_info = AsyncMock(side_effect=Exception("B站API错误"))

        pipeline = VideoToNotePipeline(
            bilibili_client=mock_bilibili,
            llm_service=None,
            prefer_subtitle=False,
        )

        result = await pipeline.run("BV1xx")

        assert result.success is False
        assert "B站API错误" in result.error

    @pytest.mark.asyncio
    async def test_pipeline_empty_after_clean(self):
        """测试清洗后文本为空。"""
        mock_bilibili = AsyncMock()
        mock_bilibili.get_video_info = AsyncMock(
            return_value=MagicMock(title="测试", up_name="", duration=0, cid=12345)
        )

        pipeline = VideoToNotePipeline(
            bilibili_client=mock_bilibili,
            llm_service=None,
            prefer_subtitle=True,
        )

        with patch("openbiliclaw.notes.pipeline.BilibiliSubtitleFetcher") as mock_fetcher:
            mock_fetcher_instance = AsyncMock()
            mock_fetcher_instance.__aenter__.return_value = mock_fetcher_instance
            mock_fetcher_instance.get_cid.return_value = 12345
            mock_fetcher_instance.fetch_subtitle_text.return_value = "   "
            mock_fetcher.return_value = mock_fetcher_instance

            result = await pipeline.run("BV1xx")

        assert result.success is False
        assert result.error == "清洗后文本为空"

    @pytest.mark.asyncio
    async def test_pipeline_subtitle_no_bilibili_client(self):
        """测试无 bilibili_client 时字幕路径仍可用。"""
        mock_llm = MagicMock()
        mock_llm.complete_structured_task = AsyncMock(return_value=SimpleNamespace(content="# 笔记"))

        pipeline = VideoToNotePipeline(
            bilibili_client=None,
            cookie="test_cookie",
            llm_service=mock_llm,
            prefer_subtitle=True,
            content_type="article",
        )

        with patch("openbiliclaw.notes.pipeline.BilibiliSubtitleFetcher") as mock_fetcher:
            mock_fetcher_instance = AsyncMock()
            mock_fetcher_instance.__aenter__.return_value = mock_fetcher_instance
            mock_fetcher_instance.get_cid.return_value = 12345
            mock_fetcher_instance.fetch_subtitle_text.return_value = "字幕文本内容"
            mock_fetcher.return_value = mock_fetcher_instance

            result = await pipeline.run("BV1xx", save_note=False)

        assert result.success is True
        assert result.source == "subtitle"

    def test_video_to_note_result_dataclass(self):
        """测试结果数据类。"""
        result = VideoToNoteResult()
        assert result.success is False
        result.success = True
        result.source = "audio"
        assert result.success is True
        assert result.source == "audio"


# ── Imports 完整性 ──


class TestModuleImports:
    """模块导入测试。"""

    def test_all_imports(self):
        """验证 __init__.py 声明的所有导出类可导入。"""
        from openbiliclaw.notes import VideoToNotePipeline  # noqa: F401

    def test_transcribe_imports(self):
        """验证 transcribe 子模块可导入。"""
        from openbiliclaw.notes.transcribe.chunker import AudioChunker
        from openbiliclaw.notes.transcribe.cleaner import TextCleaner
        from openbiliclaw.notes.transcribe.fetcher import AudioDownloader
        from openbiliclaw.notes.transcribe.whisper import AudioTranscriber

        assert TextCleaner is not None
        assert AudioChunker is not None
        assert AudioDownloader is not None
        assert AudioTranscriber is not None

    def test_synthesis_imports(self):
        """验证 synthesis 子模块可导入。"""
        from openbiliclaw.notes.synthesis.generator import NoteGenerator
        from openbiliclaw.notes.synthesis.prompts import (
            get_prompt_for_content_type,
        )

        assert NoteGenerator is not None
        assert get_prompt_for_content_type is not None
