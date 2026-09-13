# 对话归档模块（Conversation Archive）

> 把「用户提问 → 外部原文 → 我的分析」这类对话沉淀成可全文检索的归档库，并在桌面端 / 移动端提供浏览页。

## 概述

`conversation_archive/` 用于归档**有长期价值的对话内容**：一条记录保存用户当时的问题、外部内容原文
（如知乎回答 / 专栏）、以及自己写下的分析。典型用法是把知乎上值得反复看的回答连同评论一起存下来，
日后按关键词回查。

模块刻意做轻：**单表 + FTS5 全文索引**，不做 pydantic 模型、不依赖 LLM、不参与推荐流。

| 组件 | 职责 | 核心文件 |
|------|------|----------|
| 存储层 | `ConversationArchiveStore`：建表、CRUD、FTS5 搜索、统计 | `src/openbiliclaw/conversation_archive/store.py` |
| API | 5 个端点（列表 / 详情 / 统计 / 写入 / 批量导入） | `src/openbiliclaw/api/conversation_archive_routes.py` |
| 前端（移动） | `/m` 的「对话归档」tab | `src/openbiliclaw/web/js/views/conversation.js` |
| 前端（桌面） | `/web/conversation-archive` 页面 | `src/openbiliclaw/web/desktop/`（index.html + assets/js/app.js） |
| 导入脚本 | 批量写入脚本（剥离原文 HTML 注释） | `scripts/import_conversation_archive.py` |

> **与其他模块的关系**：表与主库共存（`ctx.database`），**不新建独立 db 文件**；
> 与 `chat_analysis`（微信聊天分析）定位不同 —— 后者分析聊天关系与人格，本模块只做内容归档与检索。

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| 单表归档 | ✅ | `conversation_archive` 表，按 `seq` 唯一约束，写入天然幂等 |
| FTS5 全文检索 | ✅ | external content 表 + trigram 分词，覆盖 用户问题 / 标题 / 作者 / 原文 / 分析 五列，搜索按 `bm25` 排序 |
| 索引同步 | ✅ | INSERT / DELETE / UPDATE 三个 trigger 维护 FTS 与外部内容表一致 |
| 列表分页与排序 | ✅ | `limit`（上限 200）、`offset`、`sort_by`（seq / id / created_at / updated_at / voteup_count / published_at）、`sort_order` |
| 统计 | ✅ | 总数、按 kind 分布、按作者分布、有原文数、有分析数 |
| 单条写入 / 批量导入 | ✅ | 按 `seq` 幂等 upsert，重复导入不会翻倍 |
| 双前端浏览 | ✅ | 移动端卡片 + `<details>` 展开原文/分析、搜索 250ms 防抖；桌面端搜索框 + 卡片列表 |
| 中文与长文 | ✅ | 原文/分析为 Markdown 全文，实测单条原文近 2 万字符 |

## 公开 API

### Python

```python
from pathlib import Path

from openbiliclaw.conversation_archive.store import ConversationArchiveStore

store = ConversationArchiveStore(db_path=Path("data/openbiliclaw.db"))

item_id = store.upsert_item(
    {
        "seq": 13,
        "kind": "zhihu_eval",           # 或 concept_explain
        "user_question": "这个问题怎么看？",
        "question_title": "原文标题",
        "source_url": "https://...",
        "source_type": "answer",        # answer / article / pin
        "author": "作者名",
        "headline": "作者签名",
        "voteup_count": 116,
        "comment_count": 7,
        "published_at": "2026-09-10",
        "tags": ["大模型", "推理"],
        "extracted_original_md": "# 原文 ...",
        "my_analysis_md": "# 分析 ...",
    }
)

items = store.list_items(limit=50, search="DeepSeek")   # 走 FTS5 + bm25
item = store.get_item(item_id)
stats = store.get_stats()   # {total, by_kind, by_author, with_original, with_analysis}
```

也可接入运行时主库（API 内部即这种方式）：`ConversationArchiveStore(database=ctx.database)`。

### REST

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/conversation-archive` | 列表；参数 `limit` `offset` `search` `sort_by` `sort_order` |
| GET | `/api/conversation-archive/stats` | 统计 |
| GET | `/api/conversation-archive/{item_id}` | 单条详情（404 表示不存在） |
| POST | `/api/conversation-archive` | 写入单条，body 必须含 `seq` |
| POST | `/api/conversation-archive/import` | 批量导入，body 为 `{"items": [...]}` |

数据库不可用时统一返回 `503 {"ok": false, "error": "database unavailable"}`。

### 数据表

```sql
CREATE TABLE IF NOT EXISTS conversation_archive (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    seq                   INTEGER NOT NULL UNIQUE,
    kind                  TEXT NOT NULL DEFAULT 'zhihu_eval',
    user_question         TEXT NOT NULL,
    question_title        TEXT DEFAULT '',
    source_url            TEXT DEFAULT '',
    source_type           TEXT DEFAULT '',
    author                TEXT DEFAULT '',
    headline              TEXT DEFAULT '',
    voteup_count          INTEGER DEFAULT 0,
    comment_count         INTEGER DEFAULT 0,
    published_at          TEXT DEFAULT '',
    tags                  TEXT DEFAULT '[]',          -- JSON 数组字符串，读取时反序列化
    extracted_original_md TEXT DEFAULT '',
    my_analysis_md        TEXT DEFAULT '',
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## 配置项

| 字段 | 默认 | 说明 |
|------|------|------|
| （无独立配置） | — | 跟随主库：API 侧通过 `ctx.database` 建表；脚本侧直接传 `db_path` |

## 设计决策

1. **复用主库而非独立 db**：归档量级很小（十余条、单条最长 2 万字符），不值得为它再开一个锁域；
   与 `chat_analysis`（360 万消息、独立 969 MB 库）的体量差异决定了两种不同选择。
2. **trigram 分词而非默认 unicode61**：归档以中文长文为主，`unicode61` 对中文按整段切分几乎无法检索，
   trigram 支持任意子串匹配（实测 `search=yoco`、`search=DeepSeek` 均可命中）。
3. **external content 表 + trigger**：FTS 不额外存一份正文，靠 trigger 同步，避免正文双份导致库体积翻倍。
4. **按 `seq` 幂等**：`seq` 是人工维护的序号（当前 1–13），导入脚本反复跑不会产生重复条目。
5. **直接返回 dict，不引入 pydantic**：字段以展示为主、结构稳定，省一层模型转换。

## 已知问题

- **`tags` 反序列化容错**：JSON 解析失败时静默降级为空列表，脏数据不会报错但也不会暴露。
- **导入脚本内含数据**：`scripts/import_conversation_archive.py` 的 `RECORDS` 直接写死了 1–13 条的
  元数据与原文路径，属于一次性搬迁脚本；新增条目建议直接调 API 或另写脚本。

## 测试

`tests/conversation_archive/test_conversation_archive.py` 覆盖两层，不依赖真实主库 / LLM：

- **存储层 `ConversationArchiveStore`**：建表（db_path 模式触发）、`upsert_item` 幂等（同 `seq` 复用行）、
  `upsert_many` 返回导入条数、列表默认排序、按 `voteup_count` 倒序、分页、FTS5 全文搜索命中、
  `get_item` 命中 / 缺失、tags JSON 往返、统计聚合（total / by_kind / by_author / with_original / with_analysis）。
- **API 路由**：列表、详情（命中 / 404）、stats、创建（缺 `seq` → 400 / 正常 → 201）、
  批量导入（非 list → 400 / 正常）、数据库不可用时统一 503。

### 历史上修复的两个缺陷（与测试同批）

1. **`upsert_many` 返回值错误**：旧实现 `return sum(self.upsert_item(r) for r in records)`，
   而 `upsert_item` 返回的是**行 id**，导致批量导入实际返回「id 之和」而非导入条数
   （如 seq=1→id1、seq=2→id2，返回 3 而非 2）。已改为 `return len(records)`，import 端点的
   `imported` 字段现在报告真实条数。
2. **`database=` 模式不建表**：旧 `conn` 在 `database=` 下直接 `return self._database.conn`，
   跳过 `_initialize_tables`，导致**若未跑过 import 脚本，API 会报 no such table**。已对齐
   `chat_analysis/store.py` 的同源模式——`database=` 下首次访问也懒建表（DDL 幂等），API 自愈。
