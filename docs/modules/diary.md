# 日记系统

> 个人日记的记录、存储、AI 分析与回顾，支持多格式导入与时间线浏览。

## 概述

`diary/` 包实现了完整的个人日记系统，提供从数据导入、存储管理、AI 智能分析到前端可视化的全链路能力。

| 组件 | 职责 | 核心文件 |
|------|------|----------|
| 数据模型 | 日记条目、分析结果、统计信息的 Pydantic 模型 | `models.py` |
| 存储层 | SQLite 表管理、CRUD、检索、统计 | `store.py` |
| 业务层 | 日记服务、LLM 分析、时间线、搜索 | `service.py` |
| 导入器 | 乐乐日记格式、纯文本、Markdown 导入 | `importer.py` |
| API 层 | RESTful 接口（在 `api/app.py` 中） | `api/app.py` |
| 前端页面 | 桌面端日记浏览与编辑界面 | `web/desktop/index.html` |

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| 日记 CRUD | ✅ | 创建、读取、更新、删除日记条目 |
| 多条件筛选 | ✅ | 按日期范围、情绪、来源、标签筛选 |
| 全文搜索 | ✅ | 按标题和内容关键词搜索 |
| 统计概览 | ✅ | 总数、字数、情绪分布、热门标签、按月统计 |
| 时间线视图 | ✅ | 按年/月浏览日记时间线 |
| AI 智能分析 | ✅ | LLM 生成摘要、关键要点、情绪分布、主题、人物、成长洞察 |
| 批量分析 | ✅ | 批量分析所有未分析的日记，支持并发控制 |
| 乐乐日记导入 | ✅ | 导入 `lele-diary.txt` 格式的成长日记 |
| 纯文本导入 | ✅ | 按段落导入通用文本日记 |
| Markdown 导入 | ✅ | 按一级标题分段导入 Markdown 日记 |
| 导入幂等性 | ✅ | 重复导入同日期同来源日记不会产生重复 |
| 多来源导入 | ✅ | 支持 MindBack、有道云、苹果备忘录、WPS、乐乐成长等 5 个来源 |
| 跨来源去重 | ✅ | 自动识别并合并跨来源重复日记 |
| 前端页面 | ✅ | 桌面端完整的日记浏览、编辑、分析界面 |
| 单元测试 | ✅ | 39 个测试用例覆盖核心功能（19 基础 + 20 知识图谱） |
| **RAG 语义搜索** | ✅ | 基于 bge-m3 向量的语义搜索，支持自然语言查询 |
| **日记对话** | ✅ | 基于 RAG 的日记问答，AI 基于你的日记回答问题 |
| **碎片化快速记录** | ✅ | 随手记功能，支持类型/媒体/标签，AI 自动标注 |
| **证据驱动日记生成** | ✅ | 从碎片记录自动生成日记，参考 echolog 设计 |
| **月度/年度自动反思** | ✅ | 周报、月度反思、年度回顾、人生里程碑识别 |
| **个人知识网络** | ✅ | 标签关联网络、人物关系图谱、混合知识网络、节点详情 |
| **人物关系分析** | ✅ | 自动推断人物关系类型（家人/朋友/同事/伴侣） |
| **接口性能优化** | ✅ | 统计类接口 30s TTL 内存缓存（写操作自动失效）；日记模块 JS 按需加载，首页不再加载 9 个日记脚本 |

## 数据模型

### DiaryEntry（日记条目）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int | 唯一 ID |
| `entry_date` | str | 日记日期（YYYY-MM-DD） |
| `title` | str | 标题（可选） |
| `content` | str | 正文内容 |
| `source` | str | 来源：manual / import_lele / import_text / import_markdown / api |
| `tags` | list[str] | 标签列表 |
| `mood` | MoodLevel | 情绪等级 |
| `mood_score` | float | 情绪分值（-1.0 ~ 1.0） |
| `word_count` | int | 字数统计 |
| `analysis_id` | int \| None | 关联的分析记录 ID |
| `created_at` / `updated_at` | datetime | 创建/更新时间 |

### MoodLevel（情绪等级）

```python
class MoodLevel(str, Enum):
    VERY_HAPPY = "very_happy"   # 非常开心
    HAPPY = "happy"             # 开心
    NEUTRAL = "neutral"         # 平静
    SAD = "sad"                 # 低落
    VERY_SAD = "very_sad"       # 非常低落
    ANGRY = "angry"             # 生气
    ANXIOUS = "anxious"         # 焦虑
    UNKNOWN = "unknown"         # 未标注
```

### DiaryAnalysis（AI 分析结果）

| 字段 | 类型 | 说明 |
|------|------|------|
| `summary` | str | 内容摘要 |
| `key_points` | list[str] | 关键要点 |
| `emotions` | dict[str, float] | 情绪分布（情绪 -> 权重） |
| `themes` | list[str] | 主题标签 |
| `people_mentioned` | list[str] | 提到的人物 |
| `growth_insight` | str | 成长洞察 |
| `mood_score` | float | 情绪分值 |
| `model_used` | str | 使用的 LLM 模型 |

## 公开 API

### Python API

```python
from openbiliclaw.diary import DiaryService, DiaryEntryCreate, MoodLevel

# 初始化服务（复用项目数据库）
from openbiliclaw.storage.database import Database
db = Database("data/openbiliclaw.db")
db.initialize()
service = DiaryService(database=db, llm_service=llm_service)

# 创建日记
entry = service.create_entry(DiaryEntryCreate(
    entry_date="2026-09-06",
    title="今天的思考",
    content="今天学到了很多...",
    tags=["学习", "感悟"],
    mood=MoodLevel.HAPPY,
))

# 列出日记（支持筛选）
entries, total = service.list_entries(
    limit=20,
    start_date="2026-01-01",
    mood=MoodLevel.HAPPY,
    search="关键词",
)

# AI 分析
analysis = await service.analyze_entry(entry.id)

# 批量分析
results = await service.analyze_unanalyzed(limit=100, concurrency=3)

# 统计
stats = service.get_stats()

# 时间线
timeline = service.get_timeline(year=2026, month=9)

# 搜索
results = service.search_entries("关键词")
```

### 数据导入

```python
from openbiliclaw.diary.importer import DiaryImporter

importer = DiaryImporter(service)

# 乐乐日记格式
count, entries = importer.import_lele_diary("path/to/lele-diary.txt")

# 通用文本
count, entries = importer.import_text_file("path/to/diary.txt")

# Markdown
count, entries = importer.import_markdown_file("path/to/diary.md")
```

### REST API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/diary` | 列出日记（支持 limit/offset/start_date/end_date/mood/source/tag/search/sort_by/sort_order） |
| GET | `/api/diary/{id}` | 获取日记详情（含分析结果） |
| POST | `/api/diary` | 创建日记 |
| PUT | `/api/diary/{id}` | 更新日记 |
| DELETE | `/api/diary/{id}` | 删除日记 |
| GET | `/api/diary/stats` | 获取统计信息 |
| GET | `/api/diary/timeline` | 时间线视图（?year=2026&month=9） |
| GET | `/api/diary/search?q=关键词` | 全文搜索 |
| GET | `/api/diary/{id}/analysis` | 获取分析结果 |
| POST | `/api/diary/{id}/analyze?force=true` | 分析单篇日记 |
| POST | `/api/diary/analyze-batch?limit=100&concurrency=3` | 批量分析 |
| POST | `/api/diary/import` | 导入日记（body: {file_path, format, source}） |
| **RAG 语义搜索** | | |
| GET | `/api/diary/rag/stats` | RAG 统计（已生成 embedding 的日记数） |
| POST | `/api/diary/rag/generate-embeddings` | 批量生成 embedding |
| GET | `/api/diary/rag/search?q=查询&limit=10` | 语义搜索 |
| GET | `/api/diary/rag/similar/{entry_id}?limit=10` | 相似日记推荐 |
| POST | `/api/diary/rag/ask` | 日记对话（body: {question, limit}） |
| **碎片化记录** | | |
| GET | `/api/diary/fragments` | 列出碎片记录 |
| POST | `/api/diary/fragments` | 创建碎片记录 |
| POST | `/api/diary/fragments/{id}/auto-tag` | AI 自动标注碎片 |
| POST | `/api/diary/fragments/auto-tag-batch` | 批量自动标注 |
| POST | `/api/diary/fragments/generate-diary` | 从碎片生成日记 |
| **反思回顾** | | |
| GET | `/api/diary/reflection/weekly-stats?date=YYYY-MM-DD` | 周报统计 |
| POST | `/api/diary/reflection/weekly-report` | AI 生成周报 |
| GET | `/api/diary/reflection/monthly-stats?year=2026&month=9` | 月度统计 |
| POST | `/api/diary/reflection/monthly-reflection` | AI 生成月度反思 |
| GET | `/api/diary/reflection/yearly-stats?year=2026` | 年度统计 |
| POST | `/api/diary/reflection/yearly-review` | AI 生成年度回顾 |
| GET | `/api/diary/reflection/milestones` | 人生里程碑列表 |
| **知识网络** | | |
| GET | `/api/diary/knowledge-graph/tag-network` | 标签关联网络 |
| GET | `/api/diary/knowledge-graph/person-network` | 人物关系图谱 |
| GET | `/api/diary/knowledge-graph/mixed` | 混合知识网络 |
| GET | `/api/diary/knowledge-graph/stats` | 网络统计 |
| GET | `/api/diary/knowledge-graph/node/{node_id}` | 知识节点详情 |
| GET | `/api/diary/knowledge-graph/person/{name}/relations` | 人物关系分析 |

## 数据库表

### diary_entries

```sql
CREATE TABLE diary_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_date TEXT NOT NULL,
    title TEXT DEFAULT '',
    content TEXT NOT NULL,
    source TEXT DEFAULT 'manual',
    tags TEXT DEFAULT '[]',
    mood TEXT DEFAULT 'unknown',
    mood_score REAL DEFAULT 0.0,
    word_count INTEGER DEFAULT 0,
    analysis_id INTEGER DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_diary_entries_date ON diary_entries(entry_date);
CREATE INDEX idx_diary_entries_mood ON diary_entries(mood);
CREATE INDEX idx_diary_entries_source ON diary_entries(source);
```

### diary_analyses

```sql
CREATE TABLE diary_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    diary_id INTEGER NOT NULL,
    summary TEXT DEFAULT '',
    key_points TEXT DEFAULT '[]',
    emotions TEXT DEFAULT '{}',
    themes TEXT DEFAULT '[]',
    people_mentioned TEXT DEFAULT '[]',
    growth_insight TEXT DEFAULT '',
    mood_score REAL DEFAULT 0.0,
    model_used TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (diary_id) REFERENCES diary_entries(id) ON DELETE CASCADE
);
CREATE INDEX idx_diary_analyses_diary ON diary_analyses(diary_id);
```

### diary_fragments（碎片记录）

```sql
CREATE TABLE diary_fragments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fragment_date TEXT NOT NULL,
    content TEXT NOT NULL,
    fragment_type TEXT DEFAULT 'text',  -- text/thought/mood/quote/photo/link
    mood TEXT DEFAULT 'unknown',
    tags TEXT DEFAULT '[]',
    media_path TEXT DEFAULT '',
    media_description TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_diary_fragments_date ON diary_fragments(fragment_date);
```

### diary_embeddings（向量嵌入）

```sql
CREATE TABLE diary_embeddings (
    entry_id INTEGER PRIMARY KEY,
    vector BLOB NOT NULL,           -- 1024 维 float32 向量
    model TEXT DEFAULT 'bge-m3',
    dimension INTEGER DEFAULT 1024,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (entry_id) REFERENCES diary_entries(id) ON DELETE CASCADE
);
```

### diary_tags（标签表）

```sql
CREATE TABLE diary_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    type TEXT DEFAULT 'other',      -- emotion/topic/event/location/work/family/health/finance/other
    count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_diary_tags_name ON diary_tags(name);
```

### diary_entry_tags（日记-标签关联）

```sql
CREATE TABLE diary_entry_tags (
    entry_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    confidence REAL DEFAULT 1.0,
    PRIMARY KEY (entry_id, tag_id),
    FOREIGN KEY (entry_id) REFERENCES diary_entries(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES diary_tags(id) ON DELETE CASCADE
);
```

### diary_persons（人物表）

```sql
CREATE TABLE diary_persons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    relation TEXT DEFAULT '',        -- family/friend/colleague/partner/other
    count INTEGER DEFAULT 0,
    first_appeared TEXT DEFAULT '',
    last_appeared TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_diary_persons_name ON diary_persons(name);
```

### diary_entry_persons（日记-人物关联）

```sql
CREATE TABLE diary_entry_persons (
    entry_id INTEGER NOT NULL,
    person_id INTEGER NOT NULL,
    context TEXT DEFAULT '',         -- 人物出现的上下文片段
    PRIMARY KEY (entry_id, person_id),
    FOREIGN KEY (entry_id) REFERENCES diary_entries(id) ON DELETE CASCADE,
    FOREIGN KEY (person_id) REFERENCES diary_persons(id) ON DELETE CASCADE
);
```

### diary_fts（全文搜索虚拟表）

```sql
CREATE VIRTUAL TABLE diary_fts USING fts5(
    title,
    content,
    content='diary_entries',
    content_rowid='id'
);
```

## 配置项

日记系统当前无需额外配置项，复用项目主数据库和 LLM 服务。

## 设计决策

### 1. 独立存储层 vs 集成到主 Database

日记系统采用独立的 `DiaryStore` 类管理数据表，而非将所有方法塞进主 `Database` 类。这样做的好处：
- 模块边界清晰，日记相关的 SQL 集中在一个文件
- 可以独立初始化和测试，不依赖项目其他模块
- 复用主数据库连接，数据仍然存储在 `openbiliclaw.db` 中

### 2. AI 分析的异步设计

日记分析采用异步设计，支持批量并发分析：
- 单篇分析：`analyze_entry(id, force=False)`
- 批量分析：`analyze_batch(ids, concurrency=3)`
- 自动分析未分析：`analyze_unanalyzed(limit=50, concurrency=3)`

使用 `asyncio.Semaphore` 控制并发数，避免对 LLM API 造成过大压力。

### 3. 导入器的幂等性设计

所有导入操作都具有幂等性：
- 乐乐日记导入：按 `entry_date + source` 去重，已存在的日期跳过
- 通用文本/Markdown 导入：每次导入都会创建新条目（因为无法可靠去重）

### 4. 情绪模型的设计

采用双维度情绪表示：
- `mood`：离散的情绪等级（8 种），便于筛选和展示
- `mood_score`：连续的情绪分值（-1.0 ~ 1.0），便于趋势分析

AI 分析会同时输出这两个维度，用户也可以手动设置。

## 前端页面

桌面端日记页面（`/web/diary`）提供以下功能视图：

### 基础功能
- **统计概览**：日记总数、总字数、平均字数、已分析数、时间跨度
- **筛选搜索**：关键词搜索、情绪筛选、来源筛选、重置
- **日记列表**：左侧时间序列列表，显示日期、预览、情绪、来源、字数
- **详情面板**：右侧显示完整内容、标签、AI 分析结果
- **写日记**：弹窗编辑器，支持日期、标题、内容、标签、情绪
- **AI 分析**：单篇分析和批量分析按钮
- **导入功能**：支持本地文件路径导入

### 数据洞察
- **情绪趋势图**：按月/年展示情绪分值变化
- **字数统计**：按月/年展示字数分布
- **写作连续天数**：连续写作天数统计
- **热门标签**：标签云展示
- **人物统计**：人物出现频次排行

### 人物与标签
- **标签管理**：所有标签列表，支持按类型筛选
- **人物管理**：所有人物列表，显示关系类型和出现次数
- **人物详情**：点击人物查看相关日记和时间线

### 随手记（碎片化记录）
- **快速记录**：一键创建碎片记录，支持类型选择
- **类型分类**：text/thought/mood/quote/photo/link
- **媒体支持**：图片路径和描述
- **AI 自动标注**：自动识别情绪和标签
- **证据驱动日记生成**：从一天的碎片自动生成完整日记

### 反思回顾
- **周报**：一周的统计概览 + AI 生成的周报
- **月度反思**：月度统计 + AI 生成的深度反思
- **年度回顾**：年度统计 + AI 生成的年度总结
- **人生里程碑**：自动识别职业、感情、健康、财务、家庭、旅行、学习等重大事件

### 知识网络
- **混合网络**：标签 + 人物 + 标签-人物关联的完整知识图谱
- **标签网络**：标签之间的共现关系
- **人物网络**：人物之间的共现关系
- **网络统计**：节点数、边数、热门标签、热门人物
- **力导向布局**：Canvas 实现的物理模拟，支持拖拽、缩放、高亮
- **节点详情**：点击节点查看出现次数、关联节点、相关日记、时间线

### RAG 语义搜索
- **语义搜索**：用自然语言描述你想找的内容，AI 理解语义后匹配
- **相似日记推荐**：基于向量相似度推荐相关日记
- **日记对话**：基于 RAG 的问答系统，AI 基于你的日记回答问题

## 测试

单元测试覆盖：

### `tests/test_diary.py`（19 个基础测试）
- 数据模型测试（2 个）
- 存储层测试（10 个）：CRUD、筛选、统计、分析、未分析查询
- 业务层测试（4 个）：列表、统计、搜索、时间线
- 导入器测试（5 个）：乐乐格式、通用文本、Markdown、幂等性、文件不存在

### `tests/test_diary_knowledge_graph.py`（20 个知识图谱测试）
- 数据结构测试（4 个）：GraphNode、GraphEdge、KnowledgeGraph、PersonRelation
- 服务功能测试（13 个）：标签网络、人物网络、混合网络、关系分析、节点详情、网络统计、日期筛选、空数据库
- 边界情况测试（3 个）：最大节点数限制、节点大小缩放、关系类型推断

运行测试：
```bash
pytest tests/test_diary.py -v
```

## 后续规划

- [ ] 日记导出功能（导出为 Markdown/PDF）
- [ ] 情绪趋势可视化（月度/年度情绪曲线图）
- [ ] 词云生成（基于日记内容的关键词云）
- [ ] 日记与灵魂画像联动（将日记分析结果注入用户画像）
- [ ] 日记提醒功能（定时提醒写日记）
- [ ] 移动端日记页面适配
- [ ] 日记加密存储（敏感内容保护）
