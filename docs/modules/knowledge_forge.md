# Knowledge Forge（知识锻造炉）

> 把 87,000+ 篇文章的阅读库升级为自进化知识网络的模块（对应 `docs/knowledge-forge-design.md` 阶段一实现）。覆盖正文清理、分层摘要、实体/概念聚合、知识 Wiki 构建、缺口分析和质量审计六条管线，CLI 子命令组 `knowledge-forge` 接入主 CLI。

## 概述

`knowledge_forge/` 包实现知识锻造炉的核心管线。数据库表结构通过 `Database.initialize()` 幂等补齐（`_ensure_knowledge_forge_tables()`），并在 `migrations/001_knowledge_forge.py` 提供独立迁移脚本。

| 组件 | 职责 | 核心文件 |
|------|------|----------|
| 配置 | KF 配置模型 + 默认值 | `config.py` |
| 数据模型 | 各管线返回结构 | `models.py` |
| 提示词 | LLM 分层摘要/实体/概念提示词 | `prompts.py` |
| 工具 | 文本清洗、simhash、批量切分 | `utils.py` |
| LLM 客户端 | 主 provider + fallback + 熔断 | `utils.py`（KFLlmClient） |
| 正文清理 | 平台特异清理 + 质量评分 + 验证 | `content_cleaner.py` |
| 分层摘要 | 详细/精简/极简三层摘要 | `summary_engine.py` |
| 实体提取 | 作者确定性提取 + 主题/概念 LLM | `entity_extractor.py` |
| 质量审计 | 全量只读扫描 + 问题报告 | `quality_auditor.py` |
| 知识 Wiki | embedding 语义关联 + 标签降级 | `wiki_builder.py` |
| 观点矛盾 | LLM 判定同主题观点冲突 | `contradiction_detector.py` |
| 死链检测 | 分批异步 HEAD 检查 URL 可用性 | `dead_link_checker.py` |
| 低质量检测 | LLM 抽样判定广告/垃圾/标题不符 | `low_quality_detector.py` |
| 自动修复 | 审计问题自动修复（污染/哈希/标签/摘要/重复） | `auto_fixer.py` |
| 批量处理 | 历史文章分批补全五步管线 | `batch_processor.py` |
| 自动补充闭环 | 缺口→B站搜索→提取入库→标记 | `gap_filler.py` |
| 实体间关联 | 共现计算 + entity_relations 持久化 | `entity_relation_builder.py` |
| 定时任务 | 审计+缺口+实体共现+（可选）死链/自动补充组合与 crontab 指南 | `schedule.py` |
| 缺口分析 | 主题/概念/时间/平台四维分析 | `gap_analyst.py` |
| CLI | `knowledge-forge` 子命令组 | `cli.py` |
| API 路由 | §4 端点（摘要/实体/Wiki/缺口/审计） | `api/knowledge_forge_routes.py` |
| 知识图谱页 | ECharts 关系图独立页 | `web/knowledge-graph/index.html` |
| 实体浏览页 | 列表+详情双视图（类型注入） | `web/entity-browser/index.html` |
| 审计/缺口/矛盾页 | 独立静态页 | `web/audit|gap-analysis|contradictions/index.html` |

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| 正文清理 | ✅ | HTML 剥离、空白压缩、平台特异截断（知乎评论区/推荐位、小红书广告、B 站弹幕）、simhash 质量分 0-100、验证标记；**已接入 `upsert_article` 入库流程**（插入/更新路径均同步生成清理结果，清理失败降级不阻断入库） |
| 分层摘要 | ✅ | `detailed`（≤5000 字）/ `compact`（≤1000）/ `ultra_compact`（≤200）三层写入新列，质量分与版本号追踪 |
| 实体提取 | ✅ | 作者确定性提取；主题（tags 校验 + LLM 合并）、概念（LLM）写入 `entities`/`article_entities` |
| 质量审计 | ✅ | 全量只读扫描：基础统计、URL/hash 重复、逐行缺失/格式/污染检测，质量分写入 `article_quality_scores`，报告入 `audit_tasks`；快照式——新一轮审计自动将上一轮 open 问题标记 `superseded` |
| 知识 Wiki | ✅ | 按标签分组 → 组内两两：共享标签 `same_topic`（确定性）+ embedding 相似度 `similar`（≥0.85） |
| 观点矛盾检测 | ✅ | LLM 判定同主题文章观点冲突（输入 compact 摘要，confidence>0.7 标矛盾），增量跳过已检测对，写 `article_relations`（contradiction），可生成矛盾报告 |
| 死链检测 | ✅ | 分批异步 HEAD 请求（批量+并发受配置限制）、平台差异延迟/UA（知乎/小红书更谨慎）、404/410 死链、超时待确认、7 天有效结果跳过避免重复请求 |
| 低质量内容检测 | ✅ | LLM 抽样判定广告/垃圾/无意义/正文与标题不符（优先抽样疑似问题文章，默认 5% 比例），命中写 `audit_issues(low_quality)` |
| 自动修复 | ✅ | 按审计问题自动修复：污染重清理（ContentCleaner）、补 content_hash、LLM 补标签/摘要、重复标记；dead_link/too_short/low_quality/missing_author 生成处理建议写回 fix_suggestion（不修改数据）；默认关闭需显式开启 |
| 缺口分析 | ✅ | 主题覆盖/概念覆盖/时间衰减/跨平台四维度，报告入 `gap_records` |
| 批量处理 | ✅ | 历史文章分批补全 清理/摘要/标签/实体/质量分，高价值优先，步骤级容错，进度落 `audit_tasks` 支持断点续跑 |
| 自动补充闭环 | ✅ | 高危主题缺口 → B 站搜索 → url_processors 提取 → 入库（含清理钩子）→ 缺口标记 resolved；候选去重/冷却尊重/缺口级容错 |
| 实体描述自动更新 | ✅ | LLM 基于关联文章生成/更新实体简介，幂等（描述为空或超期才处理），单实体失败不阻断 |
| 实体间关联持久化 | ✅ | 从文章-实体关联计算共现写入 `entity_relations`（co_occur + confidence），API 优先读持久化、无则实时回退 |
| 知识图谱可视化 | ✅ | 独立页 `/knowledge-graph`（ECharts 力导向关系图），作者/主题/概念类型筛选，节点详情（类型/文章数/关联文章列表） |
| 前端页面 | ✅ | `/authors` `/topics` `/concepts` 实体浏览（列表+详情：简介/统计/主题分布/时间线[近8月/全部切换]/相关实体/文章）；`/audit` 质量审计（含处理建议列）；`/gap-analysis` 缺口分析；`/contradictions` 矛盾报告（确认属实/标记误报） |
| 定时任务 | ✅ | 组合任务 质量审计→缺口分析→实体共现刷新→（可选）死链检查→（可选）自动补充，报告落库，`schedule-show` 输出推荐 crontab |
| LLM 降级 | ✅ | KFLlmClient：主 provider 连续失败 5 次熔断 300s，自动 fallback（文档 7.1/7.2） |
| CLI 接入 | ✅ | `knowledge-forge summary/entities/audit/wiki/gap-analysis/clean/contradiction/dead-link/low-quality/auto-fix/backfill/run-scheduled/schedule-show/gap-fill/entity-describe/entity-relations` 十六条子命令 |
| API 路由 | ✅ | `api/knowledge_forge_routes.py` 注册 §4 端点：摘要查询/生成、实体列表/详情/文章、相关文章、矛盾列表、知识图谱、缺口记录、审计任务/问题/质量分（`/api/authors`、`/api/topics` 因与既有端点重名，统一走 `/api/entities?type=`） |

## 公开 API

### 正文清理（同步）

```python
from openbiliclaw.knowledge_forge.content_cleaner import ContentCleaner

cleaner = ContentCleaner()
result = cleaner.clean(body, title="标题", source_type="zhihu")
# result.cleaned_text / result.clean_score / result.verified / result.operations
```

### 分层摘要（异步）

```python
import asyncio
from openbiliclaw.knowledge_forge.summary_engine import SummaryEngine

async def main():
    engine = SummaryEngine()
    row = engine._fetch_row(article_id)
    result = await engine.generate(row, force=False)
    # result.detailed / result.compact / result.ultra_compact / result.quality

asyncio.run(main())
```

### 实体提取（异步）

```python
import asyncio
from openbiliclaw.knowledge_forge.entity_extractor import EntityExtractor

async def main():
    engine = EntityExtractor()
    row = engine._fetch_row(article_id)
    result = await engine.extract_article(row)
    # result["author"] / result["topics"] / result["concepts"]

asyncio.run(main())
```

### 质量审计（同步）

```python
from openbiliclaw.knowledge_forge.quality_auditor import QualityAuditor

stats = QualityAuditor().run_full_audit()
report = QualityAuditor().generate_report(stats["task_id"])
```

### 知识 Wiki（异步）

```python
import asyncio
from openbiliclaw.knowledge_forge.wiki_builder import WikiBuilder

async def main():
    stats = await WikiBuilder().detect_relations(limit=100)

asyncio.run(main())
```

### 缺口分析（同步）

```python
from openbiliclaw.knowledge_forge.gap_analyst import GapAnalyst

result = GapAnalyst().run()  # {"gaps_found": N, "high": ..., "report": markdown}
```

### 观点矛盾检测（异步）

```python
import asyncio
from openbiliclaw.knowledge_forge.contradiction_detector import ContradictionDetector

async def main():
    stats = await ContradictionDetector().detect(limit=100)
    # stats["pairs_scanned"] / stats["contradictions"]
    report = ContradictionDetector().generate_report()

asyncio.run(main())
```

### 死链检测（异步）

```python
import asyncio
from openbiliclaw.knowledge_forge.dead_link_checker import DeadLinkChecker

async def main():
    stats = await DeadLinkChecker().check(limit=50)
    # stats["alive"] / stats["dead"] / stats["pending"] / stats["redirect"]

asyncio.run(main())
```

### 低质量内容检测（异步）

```python
import asyncio
from openbiliclaw.knowledge_forge.low_quality_detector import LowQualityDetector

async def main():
    stats = await LowQualityDetector().detect(limit=50)
    # stats["low_quality"] / stats["normal"]

asyncio.run(main())
```

### 自动修复（异步）

```python
import asyncio
from openbiliclaw.knowledge_forge.auto_fixer import IssueFixer

async def main():
    stats = await IssueFixer().fix_all(enable=True, limit=20)
    # stats["fixed"] / stats["skipped_manual"] / stats["failed"]

asyncio.run(main())
```

### 批量处理（异步）

```python
import asyncio
from openbiliclaw.knowledge_forge.batch_processor import BatchProcessor

async def main():
    stats = await BatchProcessor().backfill(limit=50, priority="high")
    # stats["processed"] / stats["cleaned"] / stats["summarized"] / stats["last_id"]

asyncio.run(main())
```

### 自动补充闭环（异步）

```python
import asyncio
from openbiliclaw.knowledge_forge.gap_filler import GapFiller

async def main():
    stats = await GapFiller().fill(limit_gaps=5, fill_per_gap=3)
    # stats["gaps_filled"] / stats["articles_filled"] / stats["filled"]

asyncio.run(main())
```

### 实体间关联持久化

```python
from openbiliclaw.knowledge_forge.entity_relation_builder import run_entity_relations

stats = run_entity_relations(min_co_occur=2)  # 只保留共现 ≥2 篇文章的实体对
```

CLI：`openbiliclaw knowledge-forge entity-relations`。API `/api/entities/{id}` 的 related 优先读 `entity_relations`（无数据时回退实时共现计算）。

### 实体描述自动更新（异步）

```python
import asyncio
from openbiliclaw.knowledge_forge.entity_description_updater import EntityDescriptionUpdater

async def main():
    stats = await EntityDescriptionUpdater().run(limit=10, entity_type="concept")
    # stats["updated"] / stats["failed"]

asyncio.run(main())
```

### 知识图谱页

浏览器访问 `http://localhost:PORT/knowledge-graph`（独立可书签页面，ECharts 关系图 + 类型筛选 + 节点详情）。

### 前端页面

- `/authors` `/topics` `/concepts`：实体浏览（列表 + 详情：简介/统计/主题分布/时间线/相关实体/文章列表）
- `/audit`：审计问题列表（级别筛选/概要统计）
- `/gap-analysis`：缺口记录 + 重新分析
- `/contradictions`：观点矛盾文章对

数据端点：`/api/entities`（列表）、`/api/entities/{id}`（详情含 timeline/related）、`/api/entities/{id}/articles`、`/api/audit/issues`、`/api/audit/summary`、`/api/gap-records`、`/api/contradictions`、`/api/knowledge-graph`。

### 定时组合任务（异步）

```python
import asyncio
from openbiliclaw.knowledge_forge.schedule import Scheduler

async def main():
    stats = await Scheduler().run_scheduled(include_dead_link=False)
    print(stats["report"])

asyncio.run(main())
```

### 同步便捷入口

`summary_engine.summarize_article()`、`entity_extractor.extract_article()`（异步包装）、`gap_analyst.run_gap_analysis()`、`contradiction_detector.run_contradiction_detection()`、`dead_link_checker.run_dead_link_check()`、`low_quality_detector.run_low_quality_detection()`、`auto_fixer.run_auto_fix()`、`batch_processor.run_backfill()`、`schedule.run_scheduled()`、`gap_filler.run_gap_fill()` 提供模块级调用。

## 配置项

KF 配置位于 `config.toml` 的 `[knowledge_forge]` 段（默认值见 `knowledge_forge/config.py`），LLM provider 映射与摘要长度上限见 `docs/knowledge-forge-design.md` §7：

| 配置 | 默认 | 说明 |
|------|------|------|
| `summary.provider` | openai/zhipu | 分层摘要 provider 及 fallback |
| `entity.provider` | zhipu/siliconflow | 实体提取 provider 及 fallback |
| `gap.provider` | openai/zhipu | 缺口分析 provider 及 fallback |
| `audit.provider` | zhipu/zhipu | 质量审计 provider 及 fallback |
| `contradiction.provider` | zhipu/openai | 观点矛盾检测 provider 及 fallback |
| `contradiction.min_shared_tags` | 1 | 配对共享标签阈值（实际数据多为单标签） |
| `contradiction.confidence_threshold` | 0.7 | 矛盾置信度阈值 |
| `low_quality.sample_ratio` | 0.05 | 低质量检测抽样比例 |
| `low_quality.max_samples` | 100 | 低质量检测单次最多抽样数 |
| `low_quality.provider` | zhipu/openai | 低质量判定 provider 及 fallback |
| `audit.dead_link_timeout` | 10 | 死链检测超时（秒） |
| `audit.dead_link_concurrency` | 5 | 死链检测并发数 |
| `audit.batch_size` | 500 | 死链检测单批大小 |
| 摘要长度上限 | 5000/1000/200 | detailed/compact/ultra_compact |
| `wiki.similarity_threshold` | 0.85 | embedding 相似度阈值 |

## 设计决策

- **启动幂等补齐 + 独立迁移脚本双通道**：`Database.initialize()` 每次启动自动 `_ensure_knowledge_forge_tables()`（沿用项目既有 `_ensure_*` + `PRAGMA table_info` 模式）；`migrations/001_knowledge_forge.py` 复用同一份迁移逻辑避免 SQL 漂移。
- **LLM 降级在模块内自建**：obc_llm `complete_provider` 无 fallback，KF 用 KFLlmClient 自行实现主 provider → fallback → 熔断（5 次失败 / 300s 冷却），进程级单例复用 registry。
- **正文清洗以 content_cleaned 为唯一权威**：各管线优先读取 `content_cleaned`，回退 `content_text`，避免污染内容影响摘要与关联质量（设计文档 3.0.7）。
- **入库清理钩子降级不阻断**：`upsert_article` 内同步调用 ContentCleaner，清理失败仅留空新列，由批量清理管线后补（不破坏既有入库路径）。
- **quality_auditor 只读**：全量扫描不修改文章数据（`auto_fix` 默认关闭），问题只入 `audit_issues`，修复动作留待阶段二。
- **审计快照取代**：新一轮全量审计前自动将上一轮 open 问题标记 `superseded`（audit_issues 无 task_id 外键，按快照语义清理，避免重复堆积）。
- **Wiki 关联按「候选分组 + 组内两两」**：按标签分组控制 O(n²) 规模；same_topic（确定性）与 similar（embedding）为两个独立阶段，不互相阻塞。
- **同名实体按 name 唯一复用**：`entities.name` 有 UNIQUE 约束，`_upsert_entity` 按 name 查找复用、保留首个 type，避免同名不同 type 违反约束。
- **矛盾检测增量+降本**：已存在 contradiction 关系的配对跳过；摘要优先用 summary_compact 控制 token；LLM 失败整对跳过不阻断。
- **死链检测防风控**：平台差异延迟/UA（知乎/小红书更谨慎）、批次间冷却、7 天有效结果跳过，避免重复请求触发风控。
- **低质量检测优先抽样疑似**：先取已有 content_contamination/too_short/not_cleaned 审计问题的文章，不足随机补足，LLM 成本受 sample_ratio/max_samples 控制。
- **自动修复默认关闭**：设计 3.5.2 阶段四需用户确认，`audit.auto_fix_enabled=false` 时仅 dry-run/需 `--enable`；dead_link/too_short/low_quality/missing_author 不自动修改数据，但生成处理建议写回 `fix_suggestion`（`--dry-run` 只统计不落库）。
- **批量处理步骤级容错**：单篇某步失败（如摘要 content_too_short）不丢弃整篇，已完成部分（清理/标签/实体/质量分）照常落库计数。
- **定时组合不嵌套事件循环**：`Scheduler.run_scheduled` 直接调用各模块 async 方法（DeadLinkChecker.check），避免同步包装器在已运行 loop 内嵌套 asyncio.run。
- **自动补充闭环复用既有能力**：B站搜索（尊重项目级冷却/风控退避）+ url_processors 提取 + `upsert_article` 入库（自动正文清理），不引入新抓取链路；提取失败（如无字幕视频）不阻断，缺口级容错。
- **知识图谱页独立可书签**：自包含 HTML（无构建），沿用 `/web` 设计 token，ECharts 走 CDN（离线时页面给出加载失败提示而非白屏）。
- **前端路由避开既有 mount**：`/web` 被 desktop SPA mount 占用（先注册先匹配），KF 页面一律用顶级独立前缀（`/authors` `/audit` 等），与 `/knowledge-graph` 一致。
- **实体描述幂等生成**：仅处理 description 为空或超期未更新的实体，单条 LLM 失败（含 JSON 解析失败）不阻断，`refresh_days` 控制重生成频率。
- **实体间关联持久化优先**：related 查询先读 `entity_relations`（关系网络随 entity 增长而膨胀，避免每次实时自连接计算），无持久化数据时回退实时计算保证兼容；`co_occur` 列通过 `Database.initialize()` 幂等补齐。
- **API 与既有端点隔离**：`/api/authors`、`/api/topics` 已被既有话题管理端点占用，作者/主题/概念列表统一走 `/api/entities?type=`。

## 未实现（后续阶段）

- 自动补充闭环的多源扩展（非 B站来源）。
- API 路由（`api/routes/knowledge_forge.py`，设计 §4）与前端（§5）。
- `content_cleaner` 尚未接入文章入库流程（设计阶段一任务 1.0 的入库调用）。
