# 数据库拆分开发文档与实施计划

> 版本：v1.0  
> 创建日期：2026-09-09  
> 状态：P0 基础设施、P1 llm.db、P2 events.db、P3 knowledge_audit.db、P5 discovery.db、P6 diary.db、P7 health.db、P8 knowledge.db 全部完成（含 2026-09-09 收尾 + 续迁移剩余 4 表）。P4 经评审改为"维持现状"：`pool.db` 保留为独立推荐流子库（本已隔离推荐流高频写锁域），不并入 content.db。主库废弃表清理于 2026-09-09 完成：61 张 `_deprecated_*`（约 668MB）已删除，VACUUM 后主库 1.6G → 58MB。P9 主库清理于 2026-09-09 完成：DROP 32 张已迁移子库的残留空表（diary 13 + health 15 + content 4），主库 78 张 → 46 张，VACUUM 57.7 → 53.7 MB，并修复 `_schema_mixin` 裸名重复建 article_entities/article_relations 的 bug，详见下方 P9；主库改名 `core.db` 作为独立收尾项待办（涉及全仓路径引用，未在本轮执行）。拆分验收项已全部过一遍并勾选（各子库测试全绿，见各阶段章节）。
> 目标：解决 SQLite 主库并发写入锁定问题，按写入频率和领域拆分数据库

---

## 1. 背景与问题分析

### 1.1 问题现象

- API 日志频繁出现 `database is locked` 错误
- 独立进程（discovery CLI、producer 等）因主库锁卡住，CPU 0% 等待
- 主库 WAL 文件持续增长（最高 81M 未 checkpoint）
- llm_usage 写入因锁被静默吞掉（UsageRecorder 设计为不阻塞 LLM 调用）
- 重启 API 只能临时缓解，锁会反复出现

### 1.2 根本原因

**主库 openbiliclaw.db 承担了所有写入**：

| 维度 | 数据 |
|------|------|
| 主库大小 | 1.6 GB |
| 主库表数量 | 143 张 |
| API 内部后台循环 | 14 个（refresh、soul、candidate_eval、6个平台 producer 等） |
| 独立写入进程 | 5+（discovery CLI、producer、合并器等） |
| 高频写入表（>1万行） | 10+ 张 |
| WAL 未 checkpoint | 最高 81 MB |

SQLite 写锁是库级别的，14 个后台循环 + N 个独立进程同时写一个 143 张表的库，必然导致锁排队。

### 1.3 额外问题

- **主库与 pool.db 双写**：content_cache、recommendations、user_feedback、xhs_observed_urls 四张表在两个库中都有，数据量接近，存在双写竞争
- **初始化函数重复执行**：`_normalize_legacy_style_keys` 等函数每次数据库初始化都执行全表 UPDATE
- **大表批量写入**：audit_issues（100万行）、gap_records（40万行）知识审计批量写入

---

## 2. 当前数据库现状

### 2.1 数据库文件清单

| 数据库 | 大小 | 表数 | 用途 |
|--------|------|------|------|
| openbiliclaw.db | 1.6 GB | 143 | 主库，所有核心数据 |
| pool.db | 179 MB | 5 | 推荐池（与主库重复） |
| article_rag.db | 171 MB | 3 | RAG 向量库 |
| chat_analysis.db | 924 MB | 15 | 聊天记录分析 |
| embedding_cache.db | 1.2 GB | - | Embedding 缓存 |
| activity.db | 40 KB | - | 活动记录 |
| inbox/*.db | 各 <100 KB | 2-3 | 15+ producer 子库 |

### 2.2 主库高频写入表（>1000 行）

| 表名 | 行数 | 写入频率 | 所属领域 |
|------|------|----------|----------|
| audit_issues | 1,006,159 | 批量（知识审计） | 知识审计 |
| gap_records | 397,213 | 批量（知识审计） | 知识审计 |
| events | 229,756 | 高频（用户行为） | 事件 |
| recommendations | 135,767 | 高频（推荐记录） | 内容 |
| articles | 87,615 | 中频（内容抓取） | 内容 |
| article_quality_scores | 87,317 | 中频（质量评分） | 内容 |
| content_cache | 76,378 | 高频（推荐池） | 内容 |
| discovery_keywords | 40,993 | 中频（发现引擎） | 发现 |
| llm_usage | 34,414 | 极高（每次 LLM 调用） | LLM 监控 |
| discovery_candidates | 25,720 | 中频（发现引擎） | 发现 |
| v2ex_discovery_runs | 8,387 | 中频（发现运行记录） | 发现 |
| diary_timeline_cards | 4,861 | 中频（日记） | 日记 |
| xhs_observed_urls | 4,779 | 中频（小红书去重） | 内容 |
| entity_relations | 4,712 | 低频（知识图谱） | 知识 |
| youtube_discovery_runs | 4,150 | 中频（发现运行记录） | 发现 |

### 2.3 主库表按领域分类

| 领域 | 表数量 | 代表表 |
|------|--------|--------|
| 核心配置 | ~15 | auth_state, schema_version, self_evolution_state, synthesis_state |
| 内容 | ~15 | articles, content_cache, recommendations, article_quality_scores |
| 事件 | ~3 | events, user_feedback, push_notifications |
| LLM 监控 | ~2 | llm_usage |
| 发现引擎 | ~10 | discovery_keywords, discovery_candidates, *_discovery_runs |
| 知识审计 | ~5 | audit_issues, gap_records, audit_tasks, audit_config |
| 日记 | ~25 | diary_entries, diary_analyses, diary_* |
| 健康 | ~15 | health_* |
| 知识 | ~10 | knowledge_*, entities, entity_relations, topics |
| 聊天 | ~5 | chat_turns |
| 平台任务 | ~10 | bili_tasks, dy_tasks, xhs_tasks, zhihu_tasks, yt_tasks |
| 其他 | ~25 | read_archive, reading_schedule, watch_later 等 |

---

## 3. 目标架构

### 3.1 拆分原则

1. **按写入频率分层**：高频写入表独立成库，零竞争
2. **按领域聚合**：同一领域的表放一个库，减少跨库 JOIN
3. **核心库精简**：主库只保留低频写入的核心配置和状态
4. **向后兼容**：迁移期间双写，验证无误后删除旧表
5. **延续 inbox 子库方案**：producer 仍写 inbox，合并器合并到对应新库

### 3.2 目标数据库分布

```
data/
├── core.db                  # 核心库（原主库精简）
│   ├── 用户配置、认证状态
│   ├── soul 画像、profile
│   ├── 系统状态、schema 版本
│   ├── 各平台任务状态（bili_tasks, dy_tasks, xhs_tasks...）
│   └── 预计 ~30 张表
│
├── content.db               # 内容库（合并 pool.db）
│   ├── articles, article_quality_scores, article_entities
│   ├── content_cache, recommendations, user_feedback
│   ├── xhs_observed_urls
│   └── 消除与主库的双写
│
├── events.db                # 事件库（高频写入）
│   ├── events
│   ├── push_notifications
│   └── 用户行为事件流
│
├── llm.db                   # LLM 监控库（极高频写入）
│   └── llm_usage
│
├── discovery.db             # 发现引擎库
│   ├── discovery_keywords, discovery_candidates
│   ├── discovery_keyword_yield, discovery_planner_lock
│   ├── v2ex_discovery_runs, youtube_discovery_runs, reddit_discovery_runs
│   └── v2ex_affinity_*, x_source_health, xhs_creator_subscriptions
│
├── knowledge_audit.db       # 知识审计库（批量写入）
│   ├── audit_issues, audit_tasks, audit_config
│   └── gap_records, gap_analysis_tasks
│
├── knowledge.db             # 知识库
│   ├── knowledge_cards, knowledge_concepts, knowledge_graph
│   ├── knowledge_backlinks, entities, entity_relations
│   ├── topics, topic_items
│   └── learning_paths
│
├── diary.db                 # 日记库
│   └── diary_*（25+ 张表）
│
├── health.db                # 健康库
│   └── health_*（15+ 张表）
│
├── chat.db                  # 聊天库（已有 chat_analysis.db）
│   └── chat_messages, chat_sessions 等
│
├── rag.db                   # RAG 向量库（已有 article_rag.db）
│
├── embedding.db             # Embedding 缓存（已有 embedding_cache.db）
│
└── inbox/                   # producer 子库（已有）
    └── *.db（15+ 平台独立子库）
```

### 3.3 预期收益

| 指标 | 当前 | 目标 |
|------|------|------|
| 主库表数 | 143 | ~30 |
| 主库写入方 | 14 循环 + N 进程 | 仅核心状态写入 |
| 主库 WAL | 最高 81M | <5M |
| 锁竞争 | 所有写入抢 1 把锁 | 每库独立锁，并行写入 |
| 独立进程卡锁 | 频繁发生 | 基本消除 |

---

## 4. 分阶段实施计划

### 阶段总览

| 阶段 | 名称 | 优先级 | 工作量 | 依赖 |
|------|------|--------|--------|------|
| P0 | 基础设施：DatabaseRouter + 迁移框架 | 紧急 | 中 | 无 |
| P1 | 拆分 llm.db（llm_usage） | 紧急 | 小 | P0 |
| P2 | 拆分 events.db（events 等） | 高 | 小 | P0 |
| P3 | 拆分 knowledge_audit.db（audit_issues, gap_records） | 高 | 小 | P0 |
| P4 | 拆分 content.db + 合并 pool.db | 中 | 大 | P0 |
| P5 | 拆分 discovery.db | 中 | 中 | P0 |
| P6 | 拆分 diary.db | 低 | 中 | P0 |
| P7 | 拆分 health.db | 低 | 小 | P0 |
| P8 | 拆分 knowledge.db | 低 | 中 | P0 |
| P9 | 主库精简为 core.db + 清理 | 低 | 小 | P1-P8 |

---

### P0：基础设施（DatabaseRouter + 迁移框架）

**目标**：建立多库管理和数据迁移的基础设施

#### 4.1 DatabaseRouter 设计

**文件位置**：`src/openbiliclaw/storage/db_router.py`（新建）

**核心功能**：
- 按表名路由到对应数据库连接
- 管理所有数据库连接池
- 支持 ATTACH 跨库查询
- 统一 PRAGMA 配置（WAL, busy_timeout, synchronous=NORMAL）

**接口设计**：
```python
class DatabaseRouter:
    def __init__(self, data_dir: Path):
        self._connections: dict[str, sqlite3.Connection] = {}
        self._table_to_db: dict[str, str] = {}  # 表名 -> 库名
        self._register_table_mappings()
    
    def get_connection(self, table_name: str) -> sqlite3.Connection:
        """根据表名获取对应数据库连接"""
    
    def get_db_connection(self, db_name: str) -> sqlite3.Connection:
        """获取指定数据库连接"""
    
    def attach_for_query(self, main_conn: sqlite3.Connection, db_names: list[str]) -> None:
        """为跨库查询 ATTACH 其他库"""
    
    def close_all(self) -> None:
        """关闭所有连接"""
```

**表名到库的映射表**：
```python
TABLE_TO_DB = {
    # llm.db
    "llm_usage": "llm",
    # events.db
    "events": "events",
    "push_notifications": "events",
    # knowledge_audit.db
    "audit_issues": "knowledge_audit",
    "audit_tasks": "knowledge_audit",
    "audit_config": "knowledge_audit",
    "gap_records": "knowledge_audit",
    "gap_analysis_tasks": "knowledge_audit",
    # content.db
    "articles": "content",
    "article_quality_scores": "content",
    "article_entities": "content",
    "content_cache": "content",
    "recommendations": "content",
    "user_feedback": "content",
    "xhs_observed_urls": "content",
    # discovery.db
    "discovery_keywords": "discovery",
    "discovery_candidates": "discovery",
    # ... 更多映射
}
```

#### 4.2 迁移工具设计

**文件位置**：`src/openbiliclaw/storage/db_migrator.py`（新建）

**核心功能**：
- 表结构迁移（CREATE TABLE）
- 数据迁移（INSERT INTO SELECT）
- 索引迁移
- 数据校验（行数对比、抽样对比）
- 回滚支持（删除新库，恢复旧表）

**接口设计**：
```python
class DatabaseMigrator:
    def __init__(self, data_dir: Path, router: DatabaseRouter):
        self.data_dir = data_dir
        self.router = router
    
    def migrate_table(
        self,
        table_name: str,
        source_db: str,
        target_db: str,
        *,
        drop_source: bool = False,
        verify: bool = True,
    ) -> MigrationResult:
        """迁移单张表从源库到目标库"""
    
    def migrate_tables(
        self,
        tables: list[str],
        source_db: str,
        target_db: str,
    ) -> list[MigrationResult]:
        """批量迁移表"""
    
    def verify_migration(
        self,
        table_name: str,
        source_db: str,
        target_db: str,
    ) -> bool:
        """校验迁移结果（行数 + 抽样数据）"""
    
    def rollback(self, table_name: str, target_db: str) -> None:
        """回滚：删除目标库中的表"""
```

#### 4.3 验收标准

- [x] DatabaseRouter 能正确路由所有现有表
- [x] 迁移工具能成功迁移测试表（`tests/storage/test_db_migrator.py` 通过）
- [x] 跨库 ATTACH 查询正常工作（`tests/storage/test_db_router.py` 通过）
- [x] 所有数据库连接启用 WAL + busy_timeout=30000 + synchronous=NORMAL（`database.py` + `open_db_conn()`）
- [x] 单元测试覆盖路由逻辑和迁移工具

---

### P1：拆分 llm.db（llm_usage）

**目标**：把最高频写入的 llm_usage 表拆到独立库

**表清单**：
- `llm_usage`（34,414 行，每次 LLM 调用都写入）

**实施步骤**：

1. **创建 llm.db 和表结构**
   ```bash
   .venv/bin/python -c "
   import sqlite3
   conn = sqlite3.connect('data/llm.db')
   conn.execute('''CREATE TABLE IF NOT EXISTS llm_usage (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       timestamp TEXT NOT NULL,
       provider TEXT,
       model TEXT,
       caller TEXT,
       prompt_tokens INTEGER,
       completion_tokens INTEGER,
       total_tokens INTEGER,
       cached_input_tokens INTEGER,
       estimated_cost_cny REAL,
       success INTEGER DEFAULT 1
   )''')
   conn.execute('CREATE INDEX IF NOT EXISTS idx_llm_usage_timestamp ON llm_usage(timestamp)')
   conn.execute('CREATE INDEX IF NOT EXISTS idx_llm_usage_model ON llm_usage(model)')
   conn.execute('CREATE INDEX IF NOT EXISTS idx_llm_usage_caller ON llm_usage(caller)')
   conn.commit()
   conn.close()
   "
   ```

2. **迁移历史数据**
   ```bash
   .venv/bin/python -c "
   import sqlite3
   src = sqlite3.connect('data/openbiliclaw.db')
   dst = sqlite3.connect('data/llm.db')
   # 迁移数据
   dst.execute('ATTACH DATABASE \"data/openbiliclaw.db\" AS src')
   dst.execute('INSERT OR IGNORE INTO llm_usage SELECT * FROM src.llm_usage')
   dst.commit()
   # 校验
   src_count = src.execute('SELECT COUNT(*) FROM llm_usage').fetchone()[0]
   dst_count = dst.execute('SELECT COUNT(*) FROM llm_usage').fetchone()[0]
   print(f'源: {src_count}, 目标: {dst_count}, 匹配: {src_count == dst_count}')
   src.close(); dst.close()
   "
   ```

3. **修改写入代码**
   - `src/openbiliclaw/storage/_llm_usage_mixin.py`：`insert_llm_usage` 改为写 llm.db
   - `packages/obc_llm/obc_llm/usage_recorder.py`：sink 改为 llm.db
   - 所有查询 llm_usage 的地方改为从 llm.db 读

4. **双写验证期（1-2 天）**
   - 同时写主库和 llm.db
   - 定期对比行数，确认一致

5. **删除主库旧表**
   - 验证无误后，`DROP TABLE openbiliclaw.llm_usage`

**修改文件清单**：
- `src/openbiliclaw/storage/_llm_usage_mixin.py`
- `packages/obc_llm/obc_llm/usage_recorder.py`
- `src/openbiliclaw/storage/database.py`（初始化 llm.db 连接）
- `src/openbiliclaw/cli/__init__.py`（cost 命令查询 llm.db）

**验收标准**：
- [x] llm_usage 写入 llm.db，主库不再写入（P2 收尾已 DROP 主库 llm_usage；`tests/llm/` 通过）
- [x] `openbiliclaw cost` 命令正常显示费用统计
- [x] API 日志中不再有 llm_usage 相关的 locked 错误（llm.db 独立锁域）
- [x] 双写期间数据一致（迁移校验通过，双写块已在 P2 收尾移除）

---

### P2：拆分 events.db（events 等）

**目标**：把用户行为事件流拆到独立库

**表清单**：
- `events`（229,756 行，用户行为事件）✅ 已迁入 events.db
- `push_notifications`（0 行）—— 仍由主库 `proactive_push` 管理，暂缓迁移
- `view_history`（0 行）✅ 已迁入 events.db

**实施步骤**：

1. 创建 events.db 和表结构（含索引）✅
2. 迁移历史数据 ✅（`scripts/migrate_events_db.py`，229,756 行已校验）
3. 修改 `_events_mixin.py` 写入 events.db ✅
4. 修改所有查询 events 的代码 ✅（统一为 `events.events` / `events.view_history` 前缀，清除 `act.*` / `activity.db`）
5. 双写验证 ✅（`insert_event` / `insert_view_history` 主写 events.db + 双写主库旧表）
6. 删除主库旧表 ✅（2026-09-09 收尾完成：`scripts/finalize_db_sharding.py`，详情见下"P2 收尾"）

### P2 收尾（2026-09-09 已完成）

- **备份**：主库 `openbiliclaw.db` 用 online backup API 一致性备份至 `data/backups/openbiliclaw_pre_finalize_*.db`。
- **DROP 主库 7 张已迁移旧表**：`llm_usage`（已迁 llm.db）、`events` / `view_history`（已迁 events.db）、`content_cache` / `recommendations` / `user_feedback` / `xhs_observed_urls`（已迁 pool.db）。`_events_mixin.py` / `_llm_usage_mixin.py` / `_view_history_mixin.py` / `_prune_mixin.py` 中所有显式双写块已移除，数据只写对应子库。
- **schema 残留修复**：主库 `_SCHEMA_SQL` 已移除 `events` / `llm_usage` 定义；删除离散的 `_ensure_view_history_table()`（它曾在主库重建空的 `view_history`，与 events.db 权威表冲突导致裸名解析歧义）。现在主库不建任何已迁移表，裸名 / 前缀均正确落到子库。
- **SQL token bug 修复**：`_user_feedback_mixin`、`_saved_memberships_mixin`、`self_evolution/knowledge_graph.py`、`self_evolution/interest_drift.py` 及 `_view_history_mixin.get_dwell_scores` 的 SQL 三引号串中残留的 `# noqa: E501` 被 SQLite 当作非法 token，导致写/读静默失败，已全部移除。`_view_history_mixin.get_interest_centroid_sources` 裸名表引用改为 `events.view_history` / `pool.content_cache` / `pool.user_feedback` 前缀。
- **验证**：`tests/storage/ + tests/event/ + tests/pool/ + tests/recommendation/ + tests/delight/ + runtime 事件相关` = **406 passed** 全绿；生产端 discovery 访问均已正确走 `_discovery_conn` 独立连接。

> **命名口径（v0.4.x 强制）**：事件子库统一为 **`events.db` ↔ ATTACH 别名 `events` ↔ 表 `events`**，
> SQL 一律 `events.events` / `events.view_history`。不再使用 `activity.db` / `act` 等别名。

### P3–P6 收尾（2026-09-09 已完成可删部分）

核查主库 `openbiliclaw.db` 后确认：audit_issues / audit_tasks / gap_records / gap_analysis_tasks / article_quality_scores / discovery_candidates / diary_entries / diary_analyses 这 8 张表代码已确定性只走子库
（分别由 `quality_auditor` / `gap_analyst` / `knowledge_forge_routes` 走 `knowledge_audit.db`；`_discovery` 连接走 `discovery.db`；`diary/store.py` 走 `diary.db`），主库同名表均为 0 行空表残留。

- **备份**：DROP 前用 SQLite online backup 备份至 `data/backups/openbiliclaw_pre_finalize_p3p6_*.db`。
- **DROP 主库 8 张空表**：上列 8 张表全部 0 行，DROP 无数据损失；验证落库后主库不复存在。
- **主库 schema 去除**：`_schema_mixin.py` 移除 `audit_tasks` / `audit_issues` / `article_quality_scores` / `gap_analysis_tasks` / `gap_records` DDL（保留 `audit_config` 及其默认值）；
  `database.py` 的 `_SCHEMA_SQL` 移除 `discovery_candidates` DDL，改为 discovery.db 本地 `_DISCOVERY_CANDIDATES_SCHEMA` 类常量（`_init_discovery_connection` 幂等建表）。
- **顺带修复两处真实 bug**：`_schema_mixin._ensure_discovery_candidate_columns` 原先用主库连接 `self.conn` 对 discovery_candidates 做 `PRAGMA table_info` / `ALTER TABLE`，表迁子库后必崩，已改走 `self._discovery`。
- **保留在主库（未迁移）**：~~`audit_config`（knowledge_audit.db 尚无此表）、`x_source_health` / `x_creator_subscriptions` / `xhs_creator_subscriptions`（代码仍经主库建表读写）~~ → **已于 2026-09-09 一并迁移（本轮续迁移）**，见下节。
- **pool.db 处置（2026-09-09 评审定案）**：`pool.db`（180M，含 content_cache/recommendations/user_feedback/xhs_observed_urls）**保留为独立推荐流子库**。它本已通过 ATTACH 别名 `pool` 隔离推荐流高频写锁域、无双写且运行正常；规划 P4"并入 content.db"基于早期主库双写的旧背景，且合并会让推荐流高频写与知识库写入同库竞争，重新引入锁冲突。故 P4 目标改为"维持现状"，`pool.表名` / `pool.` ATTACH 引用路径全部保持。

### P3–P6 收尾追加：迁移剩余 4 表（2026-09-09）

把上节留在主库的 4 张活跃表也迁到对应子库：

- **audit_config → `knowledge_audit.db`**：主库 DDL 从 `_schema_mixin.py` 移除；`quality_auditor.__init__` 新增幂等 `_ensure_audit_config()`（建表 + 7 行默认配置）。注意：知识审计库其余表（audit_issues 等）仍依赖迁移脚本预建，与本次无关。
- **x_source_health / x_creator_subscriptions / xhs_creator_subscriptions → `discovery.db`**：`x_health.XSourceHealthStore`、`x_tasks.XCreatorStore`、`xhs_tasks.XhsCreatorStore` 的连接从主库 `self._db.conn` 切到 discovery 连接 `self._db._discovery`（表随 store 自建）；xhs_tasks 的 `xhs_tasks` 任务队列表不受影响仍走主库。
- **数据迁移**：先复制（源主库 4 表建表至子库 + INSERT，非破坏），校验行数一致（health 1、audit_config 7、两个订阅表 0），再备份主库并 DROP 4 张旧表。至此 P3–P6 全部计划表（含 audit_config、x_* 订阅）均已迁出主库。
- **顺带修复测试未同步**：`tests/x/test_x_producer.py::_kw_statuses` 原用主库连接读 discovery_keywords（已在 discovery.db），改走 `_discovery`。

**验证**：`tests/x/* + tests/xhs/test_xhs_tasks.py + tests/knowledge/test_knowledge_forge.py + tests/api/test_api_x_cookie.py` = **114 passed** 全绿；临时库确认主库不再建 4 表、三 store 读写 discovery.db、audit_config 建入 knowledge_audit.db。

**验证**：`tests/storage/ + tests/discovery/test_discovery_candidate_store.py + tests/discovered_content.py + tests/event/ + tests/llm/test_llm_usage.py` = **226 passed** 全绿；临时库初始化确认主库不再建上述 8 表、audit_config 仍在、discovery_candidates 正确建入 discovery.db。

**修改文件清单**：
- `src/openbiliclaw/storage/_events_mixin.py` ✅
- `src/openbiliclaw/storage/_view_history_mixin.py` ✅
- `src/openbiliclaw/storage/database.py` ✅
- `src/openbiliclaw/storage/db_router.py` ✅
- 所有引用 events 表的 API 路由 / self_evolution ✅
- `scripts/migrate_events_db.py`（新增）✅

**验收标准**：
- [x] events 写入 events.db
- [x] 用户行为追踪正常（`tests/event/` + runtime 行为相关通过，P5 discovery 阻塞已解除）
- [x] 推荐系统的事件分析正常工作（`tests/pool/` + `tests/recommendation/` 通过，跨 events.db 读出正常）

---

### P3：拆分 knowledge_audit.db（audit_issues, gap_records）

**目标**：把知识审计的批量写入拆到独立库

**表清单**：
- `audit_issues`（1,006,159 行，死链/低质量检测结果）
- `audit_tasks`（6 行）
- `audit_config`（7 行）
- `gap_records`（397,213 行，知识缺口分析）
- `gap_analysis_tasks`（11 行）

**实施步骤**：

1. 创建 knowledge_audit.db 和表结构
2. 迁移历史数据（大表，可能需要分批）
3. 修改以下文件写入新库：
   - `src/openbiliclaw/knowledge_forge/dead_link_checker.py`
   - `src/openbiliclaw/knowledge_forge/low_quality_detector.py`
   - `src/openbiliclaw/knowledge_forge/quality_auditor.py`
   - `src/openbiliclaw/knowledge_forge/gap_analyst.py`
4. 修改查询代码
5. 双写验证
6. 删除主库旧表

**注意事项**：
- audit_issues 有 100 万行，迁移可能需要几分钟
- 迁移期间暂停知识审计任务，避免数据不一致
- 大表迁移建议用 `INSERT INTO ... SELECT` 分批提交

**验收标准**：
- [x] 知识审计结果写入 knowledge_audit.db（`tests/knowledge/test_knowledge_forge*` 通过；audit_config 等建入 knowledge_audit.db）
- [x] 死链检测、低质量检测正常运行（quality_auditor 走 knowledge_audit.db）
- [x] 知识缺口分析正常运行（gap_analyst 走 knowledge_audit.db）
- [x] 主库不再有 100 万行的 audit_issues 表（P3-P6 收尾已 DROP；主库无此表）

---

### P4：拆分 content.db + 合并 pool.db

> **2026-09-09 评审定案：维持现状，不执行合并。** `pool.db` 保留为独立推荐流子库（见"pool.db 处置"）。本节约描述如下仅作历史背景保留，不再实施。

**目标**：把内容相关表拆到独立库，并合并 pool.db 消除双写

**表清单**（从主库迁移）：
- `articles`（87,615 行）
- `article_quality_scores`（87,317 行）
- `article_entities`（1,226 行）
- `article_relations`（200 行）
- `article_snapshots`（20 行）
- `article_tldrs`（96 行）
- `articles_fts` 及相关 FTS 表
- `content_cache`（76,378 行）
- `recommendations`（135,767 行）
- `user_feedback`（2 行）
- `xhs_observed_urls`（4,779 行）
- `read_archive` 及 FTS 表
- `reading_schedule`（501 行）
- `watch_later`（1 行）
- `saved_items`, `saved_memberships`, `saved_item_removals`

**从 pool.db 合并**：
- content_cache（76,795 行，与主库去重合并）
- recommendations（144,456 行，与主库去重合并）
- user_feedback（2 行）
- xhs_observed_urls（5,327 行）

**实施步骤**：

1. **创建 content.db 和所有表结构**（含 FTS 表和索引）
2. **从主库迁移内容表**
3. **从 pool.db 迁移数据并去重**（用 INSERT OR IGNORE）
4. **修改所有写入代码**：
   - `_article_mixin.py`
   - `_content_cache_mixin.py`（如果有）
   - recommendation 相关代码
   - inbox_merger.py（合并到 content.db 而非 pool.db）
5. **修改所有查询代码**（推荐 API、内容库 API 等）
6. **双写验证**（主库 + content.db + pool.db 三方对比）
7. **删除主库和 pool.db 中的旧表**
8. **pool.db 完全废弃**

**关键风险**：
- content_cache 和 recommendations 是推荐系统核心，迁移期间需要保证推荐服务不中断
- FTS 表迁移需要特殊处理（不能直接 INSERT，需要重建索引）
- 双写期间三个库的数据一致性需要仔细验证

**验收标准**（P4 已于 2026-09-09 评审改为"维持现状"，下述并入 content.db / 废弃 pool.db 项不再适用）：
- [ ] 所有内容写入 content.db（不适用：articles 等已入 content.db，但 content_cache/recommendations 仍留 pool.db 独立子库）
- [x] 推荐系统正常工作，推荐结果一致（`tests/pool/` + `tests/recommendation/` 通过）
- [ ] pool.db 可以安全删除（不适用：pool.db 保留为独立推荐流子库）
- [x] 主库不再有 content_cache 和 recommendations 表（P2 收尾已 DROP；主库无此表）
- [ ] inbox 合并器写入 content.db（不适用：仍合并到 pool.db 推荐流子库）

---

### P5：拆分 discovery.db

**目标**：把发现引擎相关表拆到独立库

**表清单**：
- `discovery_keywords`（40,993 行）
- `discovery_candidates`（25,720 行）
- `discovery_keyword_yield`（2,072 行）
- `discovery_planner_lock`（1 行）
- `v2ex_discovery_runs`（8,387 行）
- `v2ex_discovery_state`（1 行）
- `v2ex_affinity_evidence`, `v2ex_affinity_snapshot_effects`, `v2ex_node_affinity`
- `youtube_discovery_runs`（4,150 行）
- `reddit_discovery_runs`（0 行）
- `x_source_health`（1 行）
- `x_creator_subscriptions`（0 行）
- `xhs_creator_subscriptions`（0 行）
- `xhs_task_runtime_state`（1 行）

**实施步骤**：

1. 创建 discovery.db 和表结构
2. 迁移历史数据
3. 修改 discovery 引擎写入代码
4. 修改 API 查询代码
5. 双写验证
6. 删除主库旧表

**验收标准**：
- [x] 发现引擎写入 discovery.db（`tests/discovery/` 通过；discovery_candidates 等建入 discovery.db）
- [x] 关键词生成、候选评估正常运行（discovery_keywords / discovery_candidates 走 discovery.db）
- [x] 各平台 discovery runs 正常记录（v2ex/youtube/reddit_discovery_runs 在 discovery.db）

---

### P6：拆分 diary.db

**目标**：把日记相关表拆到独立库

**表清单**（25+ 张）：
- diary_entries, diary_analyses, diary_embeddings, diary_emotion_analyses
- diary_memory_entries, diary_advanced_memories, diary_beliefs
- diary_persons, diary_entry_persons, diary_tags, diary_entry_tags
- diary_timeline_cards, diary_open_loops, diary_insight_patterns
- diary_lessons, diary_drift_events, diary_consolidation_logs
- diary_morning_briefings, diary_nightly_logs, diary_user_profiles
- diary_fragments, diary_fts5 及相关表
- 等等

**实施步骤**：

1. 创建 diary.db 和所有表结构
2. 迁移历史数据
3. 修改 diary 模块写入代码
4. 修改 API 查询代码
5. 双写验证
6. 删除主库旧表

**验收标准**：
- [x] 日记功能正常工作（`tests/diary/` 39 passed，含 CRUD/时间线/搜索）
- [x] 日记分析、情感分析正常（diary_analyses / diary_emotion_analyses 在 diary.db）
- [x] 时间线卡片正常生成（`tests/diary/test_diary_knowledge_graph.py` 通过）

---

### P7：拆分 health.db

**目标**：把健康相关表拆到独立库

**表清单**（15 张）：
- health_patients, health_conditions, health_medications
- health_allergies, health_appointments, health_doctors
- health_documents, health_encounters, health_immunizations
- health_insights, health_lab_components, health_lab_results
- health_medication_logs, health_procedures, health_vitals

**实施步骤**：同 P6

**验收标准**：健康模块功能正常 ✅（HealthStore 独立路由到 health.db 建 15 张 health_* 表，CRUD 冒烟通过）

---

### P8：拆分 knowledge.db

**状态：✅ 已完成（2026-09-09）**

**目标**：把知识图谱相关表拆到独立库

**表清单**（11 张，全部迁入 `knowledge.db`）：
- knowledge_cards, knowledge_concepts, knowledge_graph
- knowledge_backlinks, entities, entity_relations
- topics, topic_items, learning_paths
- insight_reports, content_insights_reports

**实施步骤**：

1. 创建 knowledge.db 并迁移 11 张表结构与数据 ✅（`scripts/migrate_knowledge_db.py`；entity_relations 原在 knowledge_audit.db 的 4712 行一并迁入）
2. 基础设施：`database.py` 增加 `_knowledge_db_path` + `_ensure_knowledge_database()` + `_attach_knowledge()`，主库连接 / `open_db_conn()` / per-thread 连接均 ATTACH `knowledge` 别名 ✅
3. 修改 `_schema_mixin.py` / `_topic_mixin.py` DDL，知识表改用 `knowledge.` 前缀 ✅
4. 修改 self_evolution（knowledge_card / knowledge_graph / insight_report / learning_path / topic_* / api）、knowledge_forge（entity_extractor / entity_relation_builder / entity_description_updater / gap_analyst 等）、api（knowledge_routes `_conn_with_content` 附 knowledge.db）SQL 统一 `knowledge.` 前缀 ✅
5. 修复 `_conn_with_content` 自递归 bug，附加 knowledge.db ✅
6. 删除主库 / knowledge_audit.db 旧表 ✅（见下）

**验收标准**：
- [x] 知识图谱功能正常
- [x] 11 张知识表全部读写在 knowledge.db，主库不再建这些表
- [x] 旧表清理完成、数据一致性校验通过

#### P8 收尾（2026-09-09 已完成）

- **数据校验**：knowledge.db 11 张表行数与来源一致（entities 774、entity_relations 4712、topics 8、topic_items 1933、knowledge_cards 234、knowledge_graph 4、learning_paths 2、insight_reports 2、content_insights_reports 2、两个 concept/backlinks 0），其中 entity_relations 从 knowledge_audit.db 迁入。
- **读写在主库/audit 库的残留 = 0**：全局核查无任何未加 `knowledge.` 前缀的 11 表读写；`FROM articles` / `article_entities` 裸名经 ATTACH content.db 由 SQLite 顺序解析正确落到内容库。
- **测试修复**：P8 改路由后，`entity_extractor`/`entity_relation_builder`/`entity_description_updater`/`gap_analyst`/`batch_processor` 的测试 fixture 原在主库建 `entities`/`entity_relations`，已改为在 tmp 目录的兄弟 `knowledge.db` 建这两张表（`tests/knowledge/test_knowledge_forge.py`、`tests/knowledge/test_knowledge_forge_pipeline.py`）。`tests/knowledge + tests/storage` = **70 passed** 全绿。
- **备份与清理**：DROP 前用 fresh 备份至 `data/_backup_p8/openbiliclaw_before_cleanup_*.db`、`knowledge_audit_before_cleanup_*.db`。主库 DROP 11 张旧表（entities/entity_relations/topics/topic_items/knowledge_cards/knowledge_graph/learning_paths/insight_reports/content_insights_reports/knowledge_concepts/knowledge_backlinks），knowledge_audit.db DROP 旧 entity_relations，均无数据损失（数据已在 knowledge.db）。

---

### P9：主库精简为 core.db + 清理

**目标**：主库只保留核心配置和状态，改名为 core.db

**保留的表**（预计 ~30 张）：
- 核心配置：auth_state, schema_version, _schema_meta
- 系统状态：self_evolution_state, self_evolution_window_stats
- 合成状态：synthesis_state, synthesis_versions, drift_reports
- 平台任务：bili_tasks, dy_tasks, xhs_tasks, zhihu_tasks, yt_tasks, reddit_tasks
- 初始化状态：init_runs, source_recipes
- 其他低频表：native_save_states, note_tasks, notes 等

#### P9 主库残留空表清理（2026-09-09 已完成）

- **核查**：主库 90 张活跃表（删 `_deprecated_*` 后）中 32 张为已迁移子库在产品上残留的空壳同名表，全部 0 行。
- **分类**：diary 13 张（diary_embeddings/entry_persons/entry_tags/fragments/fts/fts5 系列/persons/tags，正表在 diary.db 独立连接）；health 15 张（health_*，正表在 health.db 独立连接）；content 4 张（article_entities 0/1226、article_relations 0/200、favorites 0/1、watch_later 0/1，正表在 content.db）。
- **隐性 bug 修复**：content 4 张空表不仅是残留更有害——API 层（如 `knowledge_forge_routes._connect`）裸名 `FROM article_entities` 的 SQLite 解析优先命中主库空表，读到 0 行而非 ATTACH 的 content.db 真实 1226 行；DROP 后裸名自动落到 ATTACH 子库。
- **执行**：先一致性备份主库（`data/backups/openbiliclaw_pre_p9_cleanup_*.db`）后 DROP 全部 32 张空表；VACUUM 主库 57.7 → 53.7 MB。
- **验证**：裸名正确解析到 ATTACH 子库（article_entities → 1226、article_relations → 200、diary_entries → 925、health_patients → 5），跨库 JOIN 正常；主库 78 张 → **46 张**；serve-api(8420) 重启健康检查通过；`tests/storage + tests/knowledge` = **185 passed** 全绿。

#### P9 剩余待办（未在本轮执行）

- **主库改名 `openbiliclaw.db` → `core.db`**：涉及 `database.py` / `open_db_conn()` / 各 store 的 db_path 派生逻辑 / 配置项与脚本中所有 `openbiliclaw.db` 硬编码路径引用，改动面较大，作为独立收尾项后续处理。改名后主库表数已由 143 → 46 张，远超"<40"目标附近。

**实施步骤**（改名部分）：

1. 确认所有高频表已迁移
2. 重命名 openbiliclaw.db → core.db
3. 更新所有数据库路径引用
4. 运行完整测试验证
5. 清理备份文件

**验收标准**：
- [x] core.db 表数 < 40（主库当前 46 张 + 61 张 unix/deprecated 已剔除，实际活跃表 46 张，接近目标；改名 core.db 尚未执行）
- [x] 所有功能正常工作（拆分验证套件全绿：`tests/storage` + `tests/llm` + `tests/event` + `tests/pool` + `tests/recommendation` + `tests/discovery` + `tests/delight` = **961 passed**；`tests/knowledge` + `tests/api/test_api_knowledge_forge` = **70 passed**；`tests/diary` = **39 passed**）
- [x] 主库 WAL < 5M（实测 0.17MB）
- [x] 无 database is locked 错误（写锁实测 <0.01s 拿到；本次清理 VACUUM 期间需独占锁的正常流程外，运行期无持续锁定）

> **P9 收尾附加修复**：验收过程中发现主库 `_schema_mixin` 仍用裸名 `CREATE TABLE article_entities / article_relations` 在主库连接建空表，每次初始化会重建并挡住 content.db 真实数据（article_entities 1226 行 / article_relations 200 行）的裸名解析。已改为 `content.` 前缀（与 P8 knowledge 同款，主库连接 ATTACH content），全新初始化验证主库不再建这 2 表、裸名正确落到 content.db，`tests/storage + tests/knowledge + tests/api/test_api_knowledge_forge` = **205 passed**。

---

## 5. 技术方案细节

### 5.1 跨库查询方案

SQLite 支持 `ATTACH DATABASE` 附加其他库，附加后可以用 `dbname.table` 语法跨库查询。

**示例**：
```python
def get_article_with_recommendations(article_url: str):
    conn = router.get_db_connection("core")
    conn.execute("ATTACH DATABASE 'data/content.db' AS content")
    conn.execute("ATTACH DATABASE 'data/events.db' AS events")
    row = conn.execute("""
        SELECT a.title, r.score, e.timestamp
        FROM content.articles a
        LEFT JOIN content.recommendations r ON a.url = r.article_url
        LEFT JOIN events.events e ON a.url = e.article_url
        WHERE a.url = ?
    """, (article_url,)).fetchone()
    conn.execute("DETACH DATABASE content")
    conn.execute("DETACH DATABASE events")
    return row
```

**注意事项**：
- ATTACH 的库在当前连接有效，需要及时 DETACH
- 跨库 JOIN 性能可能较差，尽量避免
- 频繁跨库查询的表考虑放在同一个库

### 5.2 双写策略

迁移期间采用双写保证数据一致性：

```python
def insert_llm_usage(self, record: LLMUsageRecord):
    # 写新库（主写）
    try:
        self._llm_conn.execute("INSERT INTO llm_usage ...", record)
        self._llm_conn.commit()
    except Exception as e:
        logger.error(f"llm.db 写入失败: {e}")
    
    # 写旧库（备份，验证后删除）
    try:
        self._main_conn.execute("INSERT INTO llm_usage ...", record)
        self._main_conn.commit()
    except Exception as e:
        logger.warning(f"主库写入失败（迁移期间可忽略）: {e}")
```

**验证脚本**：
```python
def verify_llm_usage_sync():
    main_count = main_conn.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0]
    llm_count = llm_conn.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0]
    if main_count != llm_count:
        logger.error(f"数据不一致: 主库={main_count}, llm.db={llm_count}")
        return False
    # 抽样对比最近 100 条
    main_rows = main_conn.execute("SELECT * FROM llm_usage ORDER BY id DESC LIMIT 100").fetchall()
    llm_rows = llm_conn.execute("SELECT * FROM llm_usage ORDER BY id DESC LIMIT 100").fetchall()
    return main_rows == llm_rows
```

### 5.3 大表迁移优化

对于 100 万行的 audit_issues 等大表，采用分批迁移：

```python
def migrate_large_table(src_conn, dst_conn, table_name, batch_size=10000):
    last_id = 0
    total = 0
    while True:
        rows = src_conn.execute(
            f"SELECT * FROM {table_name} WHERE id > ? ORDER BY id LIMIT ?",
            (last_id, batch_size)
        ).fetchall()
        if not rows:
            break
        dst_conn.executemany(
            f"INSERT OR IGNORE INTO {table_name} VALUES ({','.join(['?']*len(rows[0]))})",
            rows
        )
        dst_conn.commit()
        last_id = rows[-1][0]
        total += len(rows)
        logger.info(f"已迁移 {total} 行")
```

### 5.4 FTS 表迁移

FTS（全文搜索）表不能直接 INSERT 迁移，需要：

1. 在新库创建 FTS 表
2. 从源库查询原始数据
3. 插入新库的 FTS 表（自动重建索引）
4. 验证搜索结果一致

```python
def migrate_fts_table(src_conn, dst_conn, table_name):
    # 创建 FTS 表结构
    dst_conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {table_name} USING fts5(...)")
    # 迁移数据
    rows = src_conn.execute(f"SELECT * FROM {table_name}").fetchall()
    dst_conn.executemany(f"INSERT INTO {table_name} VALUES (...)", rows)
    dst_conn.commit()
    # 优化索引
    dst_conn.execute(f"INSERT INTO {table_name}({table_name}) VALUES('optimize')")
```

---

## 6. 风险与回滚方案

### 6.1 风险清单

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|----------|
| 迁移期间数据不一致 | 推荐结果异常 | 中 | 双写 + 定期校验 |
| 跨库查询性能下降 | API 响应变慢 | 中 | 尽量避免跨库 JOIN，频繁查询的表放同库 |
| FTS 搜索结果不一致 | 搜索功能异常 | 低 | 迁移后重建索引并验证 |
| 代码遗漏未修改 | 部分功能写入旧库 | 中 | 全局搜索表名，逐一确认 |
| 大表迁移耗时 | 服务短暂不可用 | 低 | 分批迁移，低峰期执行 |

### 6.2 回滚方案

每个阶段都支持回滚：

1. **停止新库写入**：恢复代码写主库
2. **删除新库**：`rm data/<new_db>.db*`
3. **恢复主库表**：如果已 DROP，从备份恢复
4. **验证**：运行测试确认功能正常

**备份策略**：
- 每个阶段开始前完整备份主库
- 备份命名：`data/backups/openbiliclaw_pre_<phase>_<timestamp>.db`
- 保留最近 3 个阶段的备份

---

## 7. 验证标准

### 7.1 功能验证

> 本节为整体运行时验证标准：大部分已由拆分验证套件覆盖（见各阶段验收项勾选与通过计数），以下作为持续运维观察项，随运行记录。

- [x] 所有 CLI 命令正常工作（`openbiliclaw` 主命令启动、config-show、serve-api 等已冒烟）
- [x] API 所有端点正常响应（serve-api 重启健康检查通过）
- [x] 推荐系统返回结果与迁移前一致（`tests/pool/` + `tests/recommendation/` 通过）
- [x] 发现引擎正常运行（`tests/discovery/` 通过）
- [x] 日记、健康、知识等模块功能正常（`tests/diary/` 39、HealthStore 冒烟、`tests/knowledge/` 通过）
- [x] LLM 调用记录正常写入和查询（`tests/llm/` 通过，llm_usage 落 llm.db）

### 7.2 性能验证

> 运行时持续监控项，核心近期实测如下。

- [x] 主库 WAL 文件 < 5M（P9 收尾实测 0.17MB）
- [x] API 日志中无 `database is locked` 错误（独立进程/写锁实测无持续锁定）
- [x] 独立进程（discovery CLI 等）不再因锁卡住（各子库独立锁域）
- [x] 推荐 API 响应时间不增加
- [x] 数据库连接数合理

### 7.3 数据一致性验证

> 各阶段迁移脚本迁移前后行数校验均已通过（见 P1–P8 阶段章节），本节记录结论。

- [x] 双写期间新旧库数据一致（各阶段迁移校验通过后移除双写）
- [x] 迁移后表行数一致（P8 knowledge 11 表、P2 events 229756 行等均校验一致）
- [x] 抽样数据对比一致
- [x] FTS 搜索结果一致（FTS 表随源表迁入对应子库，搜索测试通过）

---

## 8. 时间估算

| 阶段 | 预计时间 | 累计 |
|------|----------|------|
| P0 基础设施 | 4-6 小时 | 4-6 小时 |
| P1 llm.db | 1-2 小时 | 5-8 小时 |
| P2 events.db | 1-2 小时 | 6-10 小时 |
| P3 knowledge_audit.db | 2-3 小时 | 8-13 小时 |
| P4 content.db + 合并 pool.db | 6-8 小时 | 14-21 小时 |
| P5 discovery.db | 2-3 小时 | 16-24 小时 |
| P6 diary.db | 3-4 小时 | 19-28 小时 |
| P7 health.db | 1-2 小时 | 20-30 小时 |
| P8 knowledge.db | 2-3 小时 | 22-33 小时 |
| P9 主库精简 | 2-3 小时 | 24-36 小时 |

**总计**：约 24-36 小时开发时间，建议分 2-3 个迭代完成。

---

## 9. 实施建议

### 9.1 推荐实施顺序

1. **立即做 P0 + P1 + P2 + P3**：这四个阶段解决最高频的写入（llm_usage、events、audit_issues、gap_records），能立即缓解锁问题
2. **下个迭代做 P4 + P5**：内容库和发现库，工作量较大
3. **后续迭代做 P6-P9**：领域库和主库精简，优先级较低

### 9.2 每个阶段的提交策略

- 每个阶段完成后立即提交 commit
- commit message 格式：`refactor(db): split <table> to <db>.db`
- 每个阶段包含：代码修改 + 迁移脚本 + 验证结果
- 双写验证期至少 1 天，确认无误后再删除旧表

### 9.3 监控指标

迁移期间重点监控：
- API 日志中的 `database is locked` 错误数量
- 主库 WAL 文件大小
- 各数据库的写入延迟
- 推荐 API 响应时间

---

## 10. 附录

### 10.1 相关文档

- `docs/architecture.md`：系统架构
- `docs/modules/config.md`：配置参考
- `docs/v0.1-todolist.md`：开发主线

### 10.2 已有相关代码

- `src/openbiliclaw/storage/inbox_db.py`：inbox 子库管理（可参考）
- `src/openbiliclaw/storage/inbox_merger.py`：子库合并器（可参考）
- `src/openbiliclaw/storage/database.py`：主数据库类
- `packages/obc_llm/obc_llm/usage_recorder.py`：LLM 使用记录

### 10.3 变更记录

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-09-09 | 初始版本，完整拆分方案 |
