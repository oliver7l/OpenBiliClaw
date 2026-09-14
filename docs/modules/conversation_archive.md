# 对话归档模块（Conversation Archive）

> 把「用户提问 → 外部原文 → 我的分析」这类对话沉淀成可全文检索的归档库，并在桌面端 / 移动端提供浏览页。

## 概述

`conversation_archive/` 用于归档**有长期价值的对话内容**：一条记录保存用户当时的问题、外部内容原文
（如知乎回答 / 专栏）、以及自己写下的分析。典型用法是把知乎上值得反复看的回答连同评论一起存下来，
日后按关键词回查。

模块刻意做轻：**单表 + FTS5 全文索引**，不做 pydantic 模型、不依赖 LLM、不参与推荐流。

> **⚠️ 内容库 v2 后的定位（2026-09-13 起）**：阅读收藏库的 md 文件（`notes/阅读收藏库/`）是
> **内容的单一数据源**，本模块的 DB 表降位为**派生镜像**——由 `scripts/content_library/sync_library_to_db.py`
> 从 md 单向同步，服务于前端的浏览/全文检索/状态互通。**不要直接往 DB 手写条目**（会被下次
> 同步覆盖语义），新内容一律先进 md。详见下文「与阅读收藏库的三件套闭环」。

| 组件 | 职责 | 核心文件 |
|------|------|----------|
| 存储层 | `ConversationArchiveStore`：建表、CRUD、FTS5 搜索、统计 | `src/openbiliclaw/conversation_archive/store.py` |
| API | 6 个端点（列表 / 详情 / 统计 / 原始 md / 写入 / 批量导入） | `src/openbiliclaw/api/conversation_archive_routes.py` |
| 前端（移动） | `/m` 的「对话归档」tab | `src/openbiliclaw/web/js/views/conversation.js` |
| 前端（桌面） | `/web/conversation-archive` 页面（类型筛选 + 状态互通） | `src/openbiliclaw/web/desktop/`（index.html + assets/js/app.js） |
| 同步脚本 | **md → DB** 单向派生镜像（幂等，按 md 文件名匹配既有行） | `scripts/content_library/sync_library_to_db.py` |
| 导入脚本 | 早期一次性搬迁脚本（剥离原文 HTML 注释） | `scripts/content_library/legacy/import_conversation_archive.py` |

> **与其他模块的关系**：表与主库共存（`ctx.database`），**不新建独立 db 文件**；
> 与 `chat_analysis`（微信聊天分析）定位不同 —— 后者分析聊天关系与人格，本模块只做内容归档与检索。

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| 单表归档 | ✅ | `conversation_archive` 表，按 `seq` 唯一约束，写入天然幂等 |
| FTS5 全文检索 | ✅ | external content 表 + trigram 分词，覆盖 用户问题 / 标题 / 作者 / 原文 / 分析 五列，搜索按 `bm25` 排序 |
| 索引同步 | ✅ | INSERT / DELETE / UPDATE 三个 trigger 维护 FTS 与外部内容表一致 |
| v2 派生字段 | ✅ | `entry_num`（收藏库编号）/ `group_name`（看板分组）/ `dialog_excerpt`（对话摘录）/ `annotations`（批注）/ `md_file`（原始文件名），由 sync 脚本回填 |
| 列表分页与排序 | ✅ | `limit`（上限 200）、`offset`、`sort_by`（seq / id / created_at / updated_at / voteup_count / published_at）、`sort_order` |
| 统计 | ✅ | 总数、按 kind 分布、按作者分布、有原文数、有分析数 |
| 单条写入 / 批量导入 | ✅ | 按 `seq` 幂等 upsert，重复导入不会翻倍 |
| 原始 md 导出 | ✅ | `GET /{id}/raw-md` 经 `FileResponse` 回吐收藏库源文件（三件套闭环的「回看原文」一环） |
| 双前端浏览 | ✅ | 移动端卡片 + `<details>` 展开原文/分析、搜索 250ms 防抖；桌面端搜索框 + 卡片列表 + **类型筛选条** |
| 阅读状态互通 | ✅ | 桌面端卡片的三态按钮与阅读计划看板共用 `localStorage` 键 `obc_reading_plan_v1`（按其 `entry_num` 对齐） |
| 中文与长文 | ✅ | 原文/分析为 Markdown 全文，实测单条原文近 2 万字符 |

## 与阅读收藏库的三件套闭环

内容库 v2 把「原始文件（md）→ 数据库（DB）→ 前端页」串成单向链路，各司其职：

```
notes/阅读收藏库/*.md              内容真源（人可读、可 git、看板直接解析）
        │  scripts/content_library/sync_library_to_db.py   （单向、幂等）
        ▼
data/openbiliclaw.db · conversation_archive     派生镜像（前端数据源 + FTS5 检索）
        │  /api/conversation-archive...
        ▼
/web/conversation-archive 页面               浏览/搜索/筛选/状态/回看原始 md
```

- **同步是单向且幂等的**：以 md 文件名（`md_file`）作为既有行的匹配键，反复跑只更新不新增
  （`updated N / inserted 0`）；新增条目 `inserted` 才 > 0。
- **状态只有一份**：阅读状态存在看板的 `localStorage`（键 `obc_reading_plan_v1` 的 `status[编号]`），
  前端页直接读同一份，点卡片上的三态按钮等于在看板上切换。
- **回看原文**：卡片的「原始 md ↗」打开 `/{id}/raw-md`，直接读到 `notes/阅读收藏库/` 里的源文件。
- **新内容一律先进 md**：链接/摘要/对话解读三类入口都落在 md，再跑一次 sync 让前端可见。

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
| GET | `/api/conversation-archive/{item_id:int}/raw-md` | 回吐该条目对应的收藏库源 md（`FileResponse`；校验路径在收藏库目录内，防越权） |
| GET | `/api/conversation-archive/{item_id}` | 单条详情（404 表示不存在） |
| POST | `/api/conversation-archive` | 写入单条，body 必须含 `seq` |
| POST | `/api/conversation-archive/import` | 批量导入，body 为 `{"items": [...]}` |

> `{item_id}` 支持 `int` 约束，`/raw-md` 后缀端点不会与详情端点冲突。
> 注意 `item_id` 是**表主键 `id`**，不是收藏库编号（`entry_num`）——前端从列表项拿 `item.id` 组装链接。

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
    -- v2 派生字段（由 sync_library_to_db.py 从收藏库 md 回填，非手工维护）
    entry_num             INTEGER DEFAULT 0,   -- 收藏库编号（看板/前端状态互通的对齐键）
    group_name            TEXT DEFAULT '',     -- 看板分组（算法·推荐广告 / 求职·面试 / …）
    dialog_excerpt        TEXT DEFAULT '',     -- 对话摘录节（用户提问 + 回答精华）
    annotations           TEXT DEFAULT '',     -- 批注节（关联条目 / 面试弹药 / 提醒）
    md_file               TEXT DEFAULT '',     -- 收藏库侧源文件名（sync 的幂等匹配键）
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

> 新列通过 `store.py` 的幂等补列逻辑（`_ensure_columns`）在旧库上自动 ALTER 补齐，
> 无需手工迁移；`sync_library_to_db.py` 亦会兜底补列以兼容 API 未启动的场景。

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
4. **按 `seq` 幂等**：`seq` 是人工维护的序号（v2 后由 sync 脚本按收藏库编号分配，当前 1–116），
   导入脚本反复跑不会产生重复条目。
5. **直接返回 dict，不引入 pydantic**：字段以展示为主、结构稳定，省一层模型转换。
6. **md 为单一数据源、DB 为派生镜像**（v2）：md 人可读、可 git、看板直接解析，适合做真源；
   DB 承担前端查询与 FTS5 检索这类 md 不擅长的活。两者由单向同步脚本连接，杜绝「两边各改一半」
   的不一致。**代价**是写路径唯一——所有新增都必须先落 md。
7. **sync 的幂等键是 `md_file`（源文件名）而非 `seq`**：v2 迁移时既有的 1–13 条 DB 行 seq 与收藏库
   编号并不一致（如对话归档 seq 1 对应收藏库 84 号），若按 seq 匹配会重复插入（曾实测 97→181 行）；
   改用「md 文件名」这一稳定身份后，重复跑只更新不新增。

## 已知问题

- **`tags` 反序列化容错**：JSON 解析失败时静默降级为空列表，脏数据不会报错但也不会暴露。
- **早期导入脚本内含数据**：`scripts/content_library/legacy/import_conversation_archive.py` 的 `RECORDS` 直接写死了最初的
  1–13 条元数据与原文路径，属于**一次性搬迁脚本**（v2 后已不再使用）；新增条目一律走
  「写 md → 跑 sync」，不要再往 DB 直写。
- **v2 派生字段依赖 sync**：若只改了 md 没跑 `scripts/content_library/sync_library_to_db.py`，前端页看到的仍是上一次
  同步的快照（md 侧始终是最新）。归档流程的最后一步固定为跑 sync。
- **`seq` 与 `entry_num` 在 1–13 号不相等**：历史对话归档行的 `seq` 是旧序号，`entry_num` 才是收藏库
  编号；前端一律用 `entry_num` 展示与对齐状态，`seq` 仅作稳定排序键。

## 测试

`tests/conversation_archive/test_conversation_archive.py` 覆盖两层，不依赖真实主库 / LLM：

- **存储层 `ConversationArchiveStore`**：建表（db_path 模式触发）、`upsert_item` 幂等（同 `seq` 复用行）、
  `upsert_many` 返回导入条数、列表默认排序、按 `voteup_count` 倒序、分页、FTS5 全文搜索命中、
  `get_item` 命中 / 缺失、tags JSON 往返、统计聚合（total / by_kind / by_author / with_original / with_analysis）。
- **API 路由**：列表、详情（命中 / 404）、stats、创建（缺 `seq` → 400 / 正常 → 201）、
  批量导入（非 list → 400 / 正常）、数据库不可用时统一 503。
- **原始 md 端点**（v2）：`/{id}/raw-md` 命中返回源文件内容、无 `md_file` → 404、
  文件不存在 → 404，路径穿越（`../`）→ 404（共 4 例）。
- **同步脚本**（v2）：走**手工验证**而非单测——首次跑 `inserted 84`，重复跑 `updated 116 / inserted 0`
  即为幂等通过；五段 md 的解析结果用 `sqlite3` 抽查 `entry_num`/`group_name`/`md_file`/`dialog_excerpt`/
  `annotations` 落库正确。

### 历史上修复的两个缺陷（与测试同批）

1. **`upsert_many` 返回值错误**：旧实现 `return sum(self.upsert_item(r) for r in records)`，
   而 `upsert_item` 返回的是**行 id**，导致批量导入实际返回「id 之和」而非导入条数
   （如 seq=1→id1、seq=2→id2，返回 3 而非 2）。已改为 `return len(records)`，import 端点的
   `imported` 字段现在报告真实条数。
2. **`database=` 模式不建表**：旧 `conn` 在 `database=` 下直接 `return self._database.conn`，
   跳过 `_initialize_tables`，导致**若未跑过 import 脚本，API 会报 no such table**。已对齐
   `chat_analysis/store.py` 的同源模式——`database=` 下首次访问也懒建表（DDL 幂等），API 自愈。
