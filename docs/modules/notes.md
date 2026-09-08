# 笔记系统

> 内容消费的知识沉淀层：把看过的好内容（B 站视频、专栏、知乎等）转化为结构化笔记入库，成为可检索、可回顾、可影响画像的私有知识库。

## 概述

`notes/` 包实现了统一的笔记系统，覆盖从内容转写、结构化生成到存储检索的完整链路。

| 组件 | 职责 | 核心文件 |
|------|------|----------|
| 数据模型 | 笔记条目、笔记任务的 Pydantic 模型 | `models.py` |
| 存储层 | SQLite 表管理、CRUD、FTS5 全文搜索、统计 | `store.py` |
| 业务层 | 笔记 CRUD、导入、视频转笔记编排 | `service.py` |
| 转写子模块 | 音频下载、切片、本地转录、文本清洗 | `transcribe/` |
| 合成子模块 | Prompt 模板、LLM 笔记生成 | `synthesis/` |
| 管线编排 | 字幕优先 → 音频兜底 → 清洗 → LLM 合成 → 入库 | `pipeline.py` |
| API 层 | RESTful 接口 | `api/notes_routes.py` |
| CLI | `openbiliclaw note` 命令组 | `cli.py` |

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| 笔记 CRUD | ✅ | 创建、读取、更新、删除笔记条目 |
| 全文搜索 | ✅ | FTS5 trigram 分词，支持标题/内容/标签/作者搜索 |
| 多条件筛选 | ✅ | 按类型、平台、标签筛选 |
| 统计概览 | ✅ | 总数、按类型分布、按平台分布、热门标签 |
| 笔记任务管理 | ✅ | 生成任务的创建、状态更新、列表查询 |
| 已读库导入 | ✅ | 从 `notes/已读库/` 四件套目录批量导入 |
| **视频转笔记（P2）** | ✅ | 字幕优先 → 音频兜底 → 清洗 → LLM 生成结构化笔记 |
| **CC 字幕获取** | ✅ | 优先走 B 站 CC 字幕，零下载零风控 |
| **音频流下载** | ✅ | playurl 接口 + 防盗链头 + ffmpeg 转封装 m4a |
| **音频切片** | ✅ | FFmpeg stream copy 无损 10 分钟均衡切片 |
| **本地转录** | ✅ | faster-whisper 离线转录（可选依赖） |
| **非破坏性清洗** | ✅ | 保留成语/叠词，仅折叠标点空白与多余换行 |
| **ASR 校对** | ✅ | LLM 字面级校对（只改字不改话） |
| **结构化笔记生成** | ✅ | 4 套模板：精读长文 / 学习笔记 / 资讯速报 / 通用笔记 |
| **临时文件管理** | ✅ | 音频/切片等中间产物走系统临时目录，用完即删 |
| API 路由 | ✅ | CRUD + 搜索 + 统计 + 任务 + 导入 + 视频转笔记 |
| CLI 命令 | ✅ | `note list/get/create/delete/search/stats/import/tasks/video` |

## 模块结构

```
src/openbiliclaw/notes/
├── __init__.py           # 导出所有公开类
├── models.py             # Pydantic 数据模型
├── store.py              # SQLite 存储层（notes + note_tasks + FTS5）
├── service.py            # 业务逻辑层
├── pipeline.py           # 视频转笔记管线编排
├── transcribe/           # 转写子模块
│   ├── __init__.py
│   ├── cleaner.py        # 非破坏性文本清洗
│   ├── chunker.py        # FFmpeg 无损音频切片
│   ├── fetcher.py        # B 站音频流下载器
│   └── whisper.py        # faster-whisper 本地转录（可选依赖）
└── synthesis/            # 合成子模块
    ├── __init__.py
    ├── prompts.py        # Prompt 模板库
    └── generator.py      # LLM 笔记生成器
```

## 数据模型

### Note（笔记条目）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int | 唯一 ID |
| `title` | str | 笔记标题 |
| `content_md` | str | 结构化笔记正文（Markdown） |
| `note_type` | str | 类型：manual / video / article / essay / import |
| `source_platform` | str | 来源平台：bilibili / zhihu / xiaohongshu / ... |
| `source_url` | str | 来源 URL |
| `source_ref` | str | 来源 ID（BV 号、专栏 ID 等） |
| `author` | str | 来源作者/UP主 |
| `tags` | list[str] | 标签列表 |
| `metadata` | dict | JSON 扩展字段（时长、集数、转录方式等） |
| `raw_ref` | str | 关联清洗后转录文本/原文路径 |
| `task_id` | str | 关联生成任务 ID |
| `created_at` / `updated_at` | datetime | 创建/更新时间 |

**唯一约束**：`(source_platform, source_ref)` — 同一来源的笔记不会重复创建。

### NoteTask（笔记生成任务）

| 字段 | 类型 | 说明 |
|------|------|------|
| `task_id` | str | 任务唯一 ID（8 位 UUID） |
| `source_platform` | str | 来源平台 |
| `source_ref` | str | 来源 ID（BV 号或合集 ID） |
| `resume_key` | str | 恢复键（source_ref + pipeline_version），唯一 |
| `status` | str | 状态：pending / running / done / failed / partial |
| `current_stage` | str | 当前阶段 |
| `items_json` | str | 分集状态 JSON（wandao items 简化版） |
| `error_summary` | str | 错误摘要 |
| `created_at` / `updated_at` | datetime | 创建/更新时间 |

## 公开 API

### NoteService

```python
from openbiliclaw.notes import NoteService, NoteCreate

svc = NoteService(database=db)

# CRUD
note = svc.create_note(NoteCreate(title="...", content_md="..."))
note = svc.get_note(note_id)
svc.update_note(note_id, data)
svc.delete_note(note_id)

# 列表与搜索
from openbiliclaw.notes import NoteListParams
notes = svc.list_notes(NoteListParams(search="关键词", limit=50))
total = svc.count_notes(NoteListParams(note_type="video"))

# 统计
stats = svc.get_stats()  # NoteStats

# 导入已读库
result = svc.import_from_read_archive("notes/已读库")

# 视频转笔记（异步）
result = await svc.video_to_note("BV1xx", save_note=True)
```

### 视频转笔记管线

```python
from openbiliclaw.notes import VideoToNotePipeline

pipeline = VideoToNotePipeline(
    bilibili_client=bilibili_client,
    llm_service=llm_service,
    note_service=note_service,
    prefer_subtitle=True,       # 字幕优先
    enable_asr_rectify=True,   # ASR 校对
    content_type="article",    # 笔记类型
    whisper_model="base",      # whisper 模型大小
)

result = await pipeline.run("BV1xx", save_note=True)
# result.success, result.note, result.source, result.stages, result.error
```

### CLI 命令

```bash
# 基础操作
openbiliclaw note list                     # 列出笔记
openbiliclaw note get <id>                 # 查看笔记
openbiliclaw note create --title "..."     # 创建笔记
openbiliclaw note delete <id>              # 删除笔记
openbiliclaw note search <query>           # 全文搜索
openbiliclaw note stats                    # 统计概览

# 视频转笔记
openbiliclaw note video <BV号>             # 字幕优先 + ASR 校对 + 保存
openbiliclaw note video <BV号> -t study    # 指定内容类型
openbiliclaw note video <BV号> --no-subtitle   # 强制走音频
openbiliclaw note video <BV号> --no-save   # 仅生成不入库

# 导入
openbiliclaw note import-read-archive <目录>

# 任务
openbiliclaw note tasks                    # 列出生成任务
```

### API 端点

```
GET    /api/notes                     # 列表（支持筛选、搜索、分页）
GET    /api/notes/stats               # 统计
GET    /api/notes/{id}                # 详情
POST   /api/notes                     # 创建
PUT    /api/notes/{id}                # 更新
DELETE /api/notes/{id}                # 删除
GET    /api/notes/search?q=...        # 全文搜索
GET    /api/notes/tasks               # 任务列表
GET    /api/notes/tasks/{task_id}     # 任务详情
POST   /api/notes/tasks               # 创建任务
POST   /api/notes/from-video          # 视频转笔记
POST   /api/notes/import/read-archive # 已读库导入
```

## 设计决策

### 字幕优先策略

优先获取 B 站 CC 字幕——有字幕时完全跳过音频下载，是最大的风控规避手段。多数知识类视频都有 CC 字幕，这条路径既快又安全。

### 临时文件策略

音频下载、切片、转录中间产物均写入系统临时目录（`tempfile.gettempdir()`），管线完成后立即清理。只有最终结构化笔记正文和清洗后的转录文本会持久化。

这与项目"不保留原始音视频"的隐私取向一致。

### 可选依赖

faster-whisper 为可选依赖，未安装时调用 `AudioTranscriber.transcribe()` 会抛出明确错误提示。不破坏 `openbiliclaw init` 的开箱体验。

### FTS5 trigram 分词

使用 `tokenize='trigram'` 而非默认的 `unicode61`，与项目中 `read_archive_fts`、`articles_fts` 保持一致。trigram 以三字为单位分词，对中文搜索更友好。

### wandao 思想，不抄代码

`note_tasks` 表的 checkpoint 模型（resume_key、items_json、状态流转）借鉴了 wandao 的设计思想，但全部自行实现，不复制 AGPL-3.0 的代码。

### 许可证

- `transcribe/` 和 `synthesis/prompts.py` 改编自 bili-video2book（MIT License），文件头 docstring 中保留原版权声明。
- wandao（AGPL-3.0）只借鉴设计思想，不复制代码。
