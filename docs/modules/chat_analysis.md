# 聊天记录分析系统

> 聊天记录导入、存储、检索与分析：把微信聊天导出数据（含 DeepSeek AI 分析结果）结构化入库，支持全文搜索、发送者统计、话题分析。

## 概述

`chat_analysis/` 模块实现了独立的聊天记录分析系统，参考日记模块架构设计。支持从 `mindback_data` 导入原始聊天消息和已有的 AI 分析结果，提供完整的 RESTful API 供前端查询和展示。

数据存储在独立 SQLite 文件 `data/chat_analysis.db`，不影响主数据库。

| 组件 | 职责 | 核心文件 |
|------|------|----------|
| 数据模型 | 会话、消息、分析片段、标签的 Pydantic 模型 | `models.py` |
| 存储层 | SQLite 表创建、CRUD、搜索、统计 | `store.py` |
| 导入器 | 从 `chat_data_for_ai.db` / `chat_exports.db` / DeepSeek 分析目录 / 已有 articles 导入 | `importer.py` |
| 业务层 | 封装存储层，提供业务接口 | `service.py` |
| API 层 | RESTful 接口 | `../api/chat_analysis_routes.py` |

## 数据结构

### 表结构

| 表 | 用途 |
|----|------|
| `chat_sessions` | 聊天会话（一个微信群聊/私聊对应一个会话） |
| `chat_messages` | 单条聊天消息 |
| `chat_analysis_chunks` | AI 分析片段（DeepSeek 分块输出） |
| `chat_tags` | 标签表 |
| `chat_session_tags` | 会话-标签关联表 |

### 已导入数据统计

| 指标 | 数值 |
|------|------|
| 会话总数 | **801** |
| 消息总数 | **3,614,760** |
| AI 分析片段 | **832** |
| 总字数 | **~82 MB** |
| 群聊 | 274 |
| 私聊 | 526 |

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| 独立数据库 | ✅ | `data/chat_analysis.db` 独立存储，不侵入主库 |
| SQLite 表初始化 | ✅ | 自动创建表和索引 |
| 从 `chat_data_for_ai.db` 导入 | ✅ | 兼容 Mindback 导出格式 |
| 从 `chat_exports.db` 导入 | ✅ | 兼容另一种导出结构 |
| 从 DeepSeek 分析目录导入 | ✅ | 解析 `analysis_N_M` 文件，自动提取会话标题 |
| 从已有 articles 迁移 | ✅ | 将主库中 `source_type='chat-analysis'` 文章迁移为分析片段 |
| 会话 CRUD | ✅ | 列表、详情、删除 |
| 消息分页 | ✅ | 按会话查询、支持发送者/类型筛选 |
| 发送者统计 | ✅ | 获取会话中各发送者消息量/字数统计 |
| 全文搜索 | ✅ | 使用 `LIKE` 模糊搜索，支持全局/会话范围搜索 |
| AI 分析片段管理 | ✅ | 按会话列出、获取分组统计 |
| 标签系统 | ✅ | 标签列表（支持按分类筛选） |
| 全局统计 | ✅ | 总览总数、类型分布 |
| RESTful API | ✅ | 所有功能都开放 API 端点 |
| Python 兼容 | ✅ | Python 3.10+ 兼容（改用 `str, Enum` 代替 `StrEnum`） |

## 公开 API

### Python API

```python
from openbiliclaw.chat_analysis import ChatAnalysisService, ChatImporter, ChatAnalysisStore

# 方式一：使用独立数据库文件
svc = ChatAnalysisService(db_path=Path("data/chat_analysis.db"))

# 方式二：使用主数据库
svc = ChatAnalysisService(database=main_database)

# 查询统计
stats = svc.get_global_stats()
print(stats)

# 列出会话
sessions = svc.list_sessions(limit=50, offset=0, chat_type="group")

# 会话详情
session = svc.get_session(session_id)
messages = svc.get_messages(session_id, limit=100)

# 搜索消息
result = svc.search("关键词", session_id=None, limit=50)
for r in result.results:
    print(r.session_title, r.content)

# 发送者统计
senders = svc.get_top_senders(session_id, limit=20)

# 获取分析片段
chunks = svc.get_analysis_chunks(session_title="科学空间交流群8")

# 导入数据
from openbiliclaw.chat_analysis import ImportStats
stats = svc.import_from_sqlite("path/to/chat_data_for_ai.db")
stats = svc.import_from_deepseek_analysis("path/to/deepseek-analysis")
results = svc.import_all(mindback_root="/path/to/mindback_data")
```

## REST API 端点

所有端点都在 `/api/chat-analysis/` 前缀下：

| 方法 | 端点 | 参数 | 说明 |
|------|------|------|------|
| GET | `/stats` | - | 获取全局统计 |
| GET | `/sessions` | `limit`, `offset`, `chat_type` | 列出会话 |
| GET | `/sessions/{id}` | - | 获取会话详情+统计 |
| DELETE | `/sessions/{id}` | - | 删除会话（级联删除消息） |
| GET | `/sessions/{id}/messages` | `limit`, `offset`, `sender`, `message_type` | 获取消息列表 |
| GET | `/sessions/{id}/senders` | - | 获取发送者统计 |
| GET | `/search` | `q`, `session_id`, `limit`, `offset` | 全文搜索 |
| GET | `/analysis` | `session_title`, `limit`, `offset` | 获取分析片段列表 |
| GET | `/analysis/groups` | - | 获取已分析分组统计 |
| POST | `/import/sqlite` | `db_path`, `max_sessions`, `max_messages` | 从 SQLite 导入 |
| POST | `/import/deepseek` | `analysis_dir` | 从 DeepSeek 分析目录导入 |
| POST | `/import/all` | `mindback_root`, `max_sessions`, `max_messages` | 批量从所有来源导入 |
| GET | `/tags` | `category` | 获取标签列表 |

### 示例响应（`GET /stats`）

```json
{
  "ok": true,
  "total_sessions": 801,
  "total_messages": 3614760,
  "total_chars": 82429790,
  "total_analysis_chunks": 832,
  "session_types": {
    "group": 274,
    "private": 526
  }
}
```

## 数据导入

### 目录结构要求

```
mindback_data/
├── chat_data_for_ai.db      # 聊天会话和消息数据库
├── chat_exports.db          # 另一种格式导出
└── deepseek-analysis/        # DeepSeek 分析结果
    ├── ChatLab交流群_分析结果/
    │   ├── analysis_0_3000.txt
    │   ├── analysis_3000_6000.txt
    │   └── ...
    ├── 科学空间交流群8_分析结果/
    │   └── ...
    └── ...
```

### 导入流程

1. 导入 `chat_data_for_ai.db` 中所有会话
2. 导入 `chat_exports.db` 中不存在的会话
3. 遍历 `deepseek-analysis/` 下每个分组导入分析片段
4. 从主库 `articles` 表迁移已有 `chat-analysis` 文章

### API 触发导入

```http
POST /api/chat-analysis/import/all
Content-Type: application/json

{
  "mindback_root": "/path/to/mindback_data",
  "max_sessions": 0,
  "max_messages": 0
}
```

`max_sessions=0` 表示导入全部，`>0` 表示限制条数。

## 设计决策

1. **独立数据库**：聊天数据体积大（560MB+）且与原项目核心功能（B站内容发现）正交，所以放在独立文件，避免膨胀主数据库。

2. **模糊搜索**：当前使用 SQLite `LIKE` 做模糊搜索。如果需要更精确的全文检索，可以后续启用 SQLite FTS5。

3. **Python 兼容**：项目要求 `>=3.10`，所以不使用 Python 3.11+ 引入的 `StrEnum`，改用 `class ChatType(str, Enum)`。

4. **自动初始化**：首次访问时自动创建表结构，不需要额外迁移脚本。

5. **跳过已存在**：导入时按标题去重，不会重复导入相同会话。

## 相关文档

- [CLI 命令参考](./cli.md)
- [笔记系统](./notes.md) — 本模块参考其架构设计
- [数据库存储](../storage/database.md)
