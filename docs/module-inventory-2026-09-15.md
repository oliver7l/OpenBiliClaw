# 全量模块清单（2026-09-15）

> 回答「**到底有哪些模块、哪些还没梳理**」。
> 姊妹文档：`docs/architecture-map.md`（框架怎么跑）、`docs/module-review-2026-09-15.md`（六模块深挖）、`docs/module-cleanup-inventory-2026-09-14.md`（全局底账）。
> 全部数字为 2026-09-15 实测；**本文只诊断，未改代码**。

---

## 0. 一句话结论

`src/openbiliclaw/` 下有 **35 个源码包**（另 3 个已抽成独立 Python 包）。按「行数 × 测试覆盖」排优先级，**最该先看的 5 个**是：

| 排序 | 模块 | 行数 | 测试文件数 | 为什么排这 |
|---|---|---|---|---|
| 1 | **`self_evolution`** | 9,284 | **0** | 零测试的定时任务中枢；本次新发现的「聊天分析数据被静默毒化」源头就在这里 |
| 2 | **`chat_analysis`** | 2,355 | **0** | 你点名的模块；**219 个会话被标记「已分析」但零产出**，且每晚继续污染 |
| 3 | **`sources`** | 11,263 | 40 | 采集线核心、56 个文件，**但没有模块文档** |
| 4 | **`storage`** | 11,173 | 66 | 主库抽象层，34 个表结构在这里；同样**没有模块文档** |
| 5 | **`eval`** | 7,193 | 6 | 7k 行只有 6 个测试文件提及，且无模块文档 |

---

## 1. 全部 35 个源码包

「测试」列 = `tests/` 中提及该包的**文件数**（0 = 完全没有针对它的测试）。

| 模块 | 行数 | 文件 | 测试 | 模块文档 | 代码最后提交 | 梳理状态 |
|---|---:|---:|---:|---|---|---|
| `api` | 24,105 | 37 | 40 | ❌ 无（只有 `api-auth.md`） | 09-15 | 🔶 部分（路由注册已收口） |
| `runtime` | 20,338 | 53 | 38 | ✅ `runtime.md` | 09-09 | ⬜ 未梳理 |
| `diary` | 13,837 | 25 | 4 | ✅ `diary.md`（**过期**） | 09-13 | ✅ 已梳理（§六模块） |
| `sources` | 11,263 | 56 | 40 | ✅ `sources.md`（09-15 新建） | 09-13 | ✅ 已梳理 |
| `storage` | 11,173 | 35 | 66 | ✅ `storage.md`（09-15 增补） | 09-11 | ✅ 已梳理 |
| `self_evolution` | 9,296 | 15 | 3 | ✅ `self_evolution.md`（09-16 新建） | 09-14 | 🔶 已梳理（3/15 文件有测试；修掉 2 个真 bug） |
| `cli` | 7,478 | 12 | 15 | ✅ `cli.md` | 09-14 | ⬜ 未梳理 |
| `eval` | 7,193 | 24 | 6 | ❌ **无** | 09-14 | ⬜ 未梳理 |
| `recommendation` | 6,419 | 9 | 11 | ✅ `recommendation.md` | 09-14 | ⬜ 未梳理 |
| `knowledge_forge` | 6,230 | 21 | 2 | ✅ `knowledge_forge.md` | 09-08 | ⬜ 未梳理 |
| `interview` | 5,000 | 28 | 3 | ✅ `interview.md` + `-overview.md` | 09-14 | ✅ 已梳理 |
| `health` | 4,005 | 4 | **0** | ✅ `health.md`（**多处写错**） | 09-14 | ✅ 已梳理（**你点名的**） |
| **`chat_analysis`** | 2,355 | 5 | **0** | ✅ `chat_analysis.md`（**过期**） | 09-14 | ✅ **已梳理（本次）** |
| `notes` | 2,110 | 13 | 1 | ✅ `notes.md` | 09-11 | ✅ 已梳理（归入阅读库） |
| `integrations` | 1,751 | 8 | 4 | ✅（06-26，**滞后 79 天**） | 09-14 | ⬜ 未梳理 |
| `bilibili` | 1,628 | 5 | 16 | ✅（06-26，滞后 74 天） | 09-08 | ⬜ 未梳理 |
| `saved_sync` | 1,107 | 5 | 1 | ✅ `saved_sync.md`（09-15 新建） | 09-11 | ✅ 已梳理（原生保存已接线） |
| `weekend` | 1,063 | 6 | 1 | ✅ `weekend.md`（**两处写错**） | 09-13 | ✅ 已梳理 |
| `memory` | 1,012 | 3 | 12 | ✅（06-26，滞后 79 天） | 09-14 | ⬜ 未梳理 |
| `youtube` | 992 | 3 | 2 | ✅（06-26，滞后 79 天） | 09-08 | ⬜ 未梳理 |
| `clone` | 931 | 6 | **0** | ✅ `clone.md` | 09-09 | ⬜ 未梳理 |
| `synthesis` | 855 | 5 | **0** | ❌ **无** | 09-14 | ⬜ 未梳理 |
| `topics` | 765 | 2 | **0** | ❌ **无** | 09-04 | ⬜ 未梳理 |
| `media` | 722 | 4 | 1 | ✅ `media.md` | 09-11 | ⬜ 未梳理 |
| `douban` | 654 | 7 | 1 | ✅ `douban.md` | 09-11 | ⬜ 未梳理 |
| `llm` | 600 | 19 | 14 | ✅ `llm.md` | 09-13 | 🔶 薄转发层（已抽取完毕） |
| `travel` | 465 | 2 | **0** | ❌ **无** | 09-13 | ✅ 已梳理 |
| `rag` | 400 | 2 | 1 | ❌ **无** | 09-14 | ⬜ 未梳理 |
| `ed2k` | 287 | 3 | **0** | ✅ `ed2k.md` | 09-11 | ⬜ 未梳理 |
| `conversation_archive` | 274 | 2 | 1 | ✅ `conversation_archive.md` | 09-14 | ✅ 已梳理（归入阅读库） |
| `agent` | 240 | 3 | **0** | ✅ `agent.md`（**取证：零引用死包**） | 09-14 | ✅ 已梳理（待拍板删除） |
| `core` | 204 | 3 | 47 | ✅ `core.md`（09-15 新建） | 09-13 | ✅ 已梳理 |
| `reading` | 197 | 2 | 1 | ❌ **无** | 09-02 | ✅ 已梳理（归入阅读库） |
| `cycle` | 148 | 3 | 1 | ✅ `cycle.md` | 09-11 | ⬜ 未梳理 |
| `web` | 静态 | — | 1 | ❌ 无 | 09-14 | ⬜ 未梳理（两个前端） |

**已梳理 = 9 个**（日记 / 面试 / 健康 / 旅游 / 周末 / 阅读库 4 个包 / 聊天分析），**未梳理 = 26 个**。

### 其它不属于 `src/` 但同样是模块的部分

| 部分 | 说明 | 状态 |
|---|---|---|
| `packages/obc_llm` / `obc_soul` / `obc_discovery` | 已抽成独立 Python 包（llm / soul / discovery 三模块的新家） | ✅ 抽取完成（obc_runtime 已判定不做） |
| `scripts/` | 一次性脚本堆（采集、迁移、导入、回填），**没有 README 之外的索引** | ⬜ 未梳理 |
| `extension/` | Chrome 扩展（轮询 dy/zhihu/yt/bili/xhs 的 next-task） | ⬜ 文档滞后 78 天 |
| `src/openbiliclaw/web/` | 两个前端（桌面 `/web`、移动 `/m`） | ⬜ 未梳理 |

---

## 2. 你点名的「聊天分析模块」（本次深挖，3 个 🔴）

规模：2,355 行 / 5 文件；库 `data/chat_analysis.db` **924 MB**（全项目第二大）；15 条端点；**零测试**。

### 🔴 C1. 219 个会话被标记「已分析」，但一条产出都没有（**已修复** `5d31f193`，数据未重置）

```
self_evolution/loop_engine.py:857  svc = ChatAnalysisService(db_path=Path("data/chat_analysis.db"))
                                   ↑ 构造时没传 llm_service，且是 CWD 相对路径
service.py:375-380                 _get_llm() → None（没有 llm_service）
service.py:542-543                 await self.analyze_session(...)  → self.store.mark_session_analyzed(...)
                                   ↑ 只要不抛异常就无条件标记 analyzed=1
```

实测数据（只读查询）：

```
analyzed=0  →  581 个会话          ← 还在队列里
analyzed=1  →  220 个会话
  其中「零 topics 且零 insights」 → 219 个
chat_topics 25 行 / chat_insights 20 行 / chat_embeddings 0 行 —— 全部属于 session_id=1
```

**后果**：这 219 个会话被永久跳过（`get_unanalyzed_sessions` 不会再看它们），而定时任务**每 6 小时跑一次、每次 10 个**——也就是说污染每晚继续，且不可自愈。
对照：同一个文件里紧挨着的 `_do_synthesis`（`loop_engine.py:872-876`）**是传了 `llm_service=self._llm_service` 的**，所以这是漏写，不是设计。

> **修复记录（`5d31f193`，2026-09-15）**：已止血——`loop_engine` 无 LLM 直接跳过并传 `llm_service`、路径改走 `_project_root()`；`service` 新增 `_analysis_has_output()`，分析为空**不**标记。
> 顺带修掉一个**掩盖本 bug 的读路径缺陷**：`store._row_to_session` 压根没映射 `analyzed` / `last_analyzed_at`，`ChatSession.analyzed` 恒为 False —— 服务层错标了，界面却一律显示「未分析」，两者互相掩盖。
> 回归 4 条（worktree 修复前 3 failed / 1 passed），**断言刻意直接查库而非读模型字段**（否则又会被掩盖成恒 False）。

> **存量数据已重置（2026-09-15 07:07，用户授权）**：
> - 脚本 `scripts/chat_analysis_reset_analyzed.py`（默认 dry-run，`--apply` 才写库）
> - 结果：`analyzed=1` 由 **220 → 2**（只剩真正有产出的 session 1 与 244）；`analyzed=0` **581 → 799**
> - 回滚清单（含 219 个 id + 原 `last_analyzed_at`）：`data/chat_analysis_backups/reset-analyzed-20260915-070729.json`
>   回滚命令：`.venv/bin/python scripts/chat_analysis_reset_analyzed.py --revert <该 JSON> --apply`
> - **冒烟验证**（重置前先证明管道可用，避免 800×3 次空转）：`POST /api/chat-analysis/sessions/244/analyze` → **8 个话题 + 10 条洞察 + 完整摘要**，消耗 3 次调用
> - 因该 API 路径本身不标记 analyzed，已用 `--mark-with-output` 把 244 补标为已分析，避免被定时任务重复分析产生重复行
> - 后续行为：定时任务每 6 小时分析 10 个、每天约 40 个 → 799 个约 **20 天**跑完；若 LLM 缺失则整轮跳过（不再毒化）

### ~~🔴 C2. 导入不幂等：832 条 chunk 里 419 条是重复~~ → **误判，已更正 + 已加护栏**

**⚠️ 更正说明（2026-09-15，重要）**：本节原写「413 组重复、419 行多余、重复率约 50%」，
**这个结论是错的**，错在用了错误的判重键（`session_title + start_line + end_line`）。

动手前逐列比对才发现：

```
组 ('人工智能交流群8', start_line=0, end_line=0) → 8 行
   组内不一致的列: analysis_content, analysis_file   ← 内容各不相同
按「全部内容列」分组统计 → 重复组 = 0，多余行 = 0（总 832）
analysis_file: 832 行 / 832 个不同值（真正的来源标识，唯一）
```

那 419 组只是**标题与行区间相同**（绝大多数落在默认区间 `(0,0)`，共 8 行是这种情况），
但 `analysis_content` / `analysis_file` 各不相同 —— 是**不同分析文件产出的不同内容**。
若按最初的结论删除，会**删掉 419 行真实数据**。

**真正的缺陷（确实存在，但形态不同）**：`importer.py:236/339` 无条件 INSERT，
而表上没有唯一约束 ⇒ **同一份分析文件被导入两次就会堆两行**（当前库里恰好没发生过，
因为没人重复导入过）。

**已修（本次）**：
- `store._initialize_tables()` 增加**部分唯一索引**
  `CREATE UNIQUE INDEX ... ON chat_analysis_chunks(analysis_file) WHERE analysis_file <> ''`
  （用部分索引是刻意的：`analysis_file` 默认值为 `''`，对空值也约束会误拒第二条空值行；
  老库若已有历史重复则只告警、不阻塞启动）
- `store.create_analysis_chunk()` 改 `INSERT OR IGNORE`，被挡下时**回读既有行**（不抛错）
- 新增 `store.has_analysis_chunk()`；`importer` 两个导入点先查后插，并如实计入
  `skipped`（新增 `DeepseekAnalysisImportResult.chunks_skipped` 字段）
- 回归＝`tests/chat_analysis/test_chunk_idempotency.py`（5 条，**含 3 条反向守卫**：
  不同来源都要保留 / 同标题同行区间但来源不同必须都留 / 空来源不去重）
  修复前 **2 failed / 3 passed**，修复后 5 passed

**未动数据**：全程只加索引与逻辑，**没有删除任何行**。

### 🔴 C3. `chat_fts` 是孤儿表

库里有 `chat_fts` 表（0 行），既不在 `store.py:41-169` 的建表 SQL 里，也全仓无引用。

### 🟡 C4 / C5 / C6

- **LLM 无缓存**：`POST /sessions/{id}/analyze` 固定 3 次 LLM 调用，无结果缓存；限流只是进程内计数（`service.py:42`，重启即清零）→ 重复点会重复烧钱、重复写行。
- **硬编码路径**：`loop_engine.py:858` 是 `Path("data/chat_analysis.db")`（CWD 相对）。注意 `routes.py:27-38` 已改成 `config._project_root()`——**同一个模块两条路径策略**。
- **文档失真**：`chat_analysis.md:24-29` 只列 5 张表（漏 `chat_topics/chat_insights/chat_embeddings`）；`:55` 说「用 LIKE 模糊搜索」已过期（代码已是 FTS5 + BM25）；端点表缺 `/health`；`:62` 说「3.10 兼容，不用 StrEnum」而 `models.py:10` 正是 StrEnum。
- **死代码**：`service.py` 的 `hybrid_search` / `rebuild_fts_index` / `get_topics` / `get_insights` / 嵌入三件套，以及 `store.py:update_session`、`importer.py:import_from_existing_articles` 均无调用方；三个 Pydantic 模型只被 `__init__.py` 导出。

---

## 3. 健康模块（速览，详见 `docs/module-review-2026-09-15.md` §6）

| 项 | 状态 |
|---|---|
| 规模 / 数据 | 4,005 行（其中 `store.py` 单文件 89KB、单类 1812 行）；`data/health.db` 16 表（patients=17） |
| 🔴 时间线分页 | `store.py:1699-1884`：offset 逐表应用 + 末尾二次截断 → 第 2 页起重复且丢条目 |
| 🔴 空壳表 | 主库仍有 15 张全 0 行 `health_*`，而 `health.md:55` 说「已清理」；`store.py:440` 仍可写主库 |
| 🟡 测试 | 全模块零单测（tests 里 33 处 `api/health` 全是系统探针，与医疗档案无关） |
| 🟡 文档 | 教的 `scripts/import_health_data.py` **不存在**；表数 13 vs 实为 15；端点 68 是装饰器数，唯一路径实为 35 |
| 🟡 命名 | `/api/health`（系统探针）与 `/api/health/*`（医疗档案）同名 |

---

## 4. 建议的下一波梳理顺序

| 批次 | 模块 | 为什么 |
|---|---|---|
| **⑪** | **`self_evolution`（+ chat_analysis 修复）** | 零测试的定时任务中枢，且正在**持续产生脏数据**（219 个假已分析、每 6 小时 +10）。这是唯一「不修会继续恶化」的项 |
| **⑫** | **`sources` + `storage`** | 采集线 + 库抽象，合计 2.2 万行、106 文件，都**没有模块文档**；改错影响 18 个 producer |
| **⑬** | **`eval`** | 7,193 行 / 只有 6 个测试文件提及 / 无文档 |
| **⑭** | **`cli` + `runtime`** | 有文档但滞后；`runtime` 是 20K 行的调度核心 |
| **⑮** | 小模块补课：`clone` `synthesis` `topics` `agent` `core` `rag` `ed2k` | 都是 200–1,000 行、零或近零测试，适合批量补文档 + 补测；**🔶 第一轮已完成（09-15 晚）**：`core` 补 47 例测试 + `core.md`，`agent` 出死包取证（`agent.md`），并新增分层依赖棘轮 `tests/test_layering_contracts.py`。剩余：`clone` `synthesis` `topics` `rag` `ed2k` |
| **⑯** | `scripts/` + `extension/` + 两个前端 | 索引与文档建设，风险最低 |

## 5. 需要你拍板的（承接上一轮）

1. ~~**存量 219 个假已分析会话**：要不要把它们的 `analyzed` 重置为 0？~~ → **已重置（2026-09-15，用户授权）**，见 §2 C1 修复记录。
2. ~~**重复的 419 条 chunk**：要不要加 UNIQUE 约束 + `INSERT OR IGNORE`，并清洗存量？~~ → **已处理**：核实为误判（无真实重复，见 §2 C2），改为给 `analysis_file` 加唯一索引 + `INSERT OR IGNORE` 护栏，**未删任何数据**。
3. 上一轮那 4 项仍待定：旅游真值源 / 阅读库真值源 / saved_sync 去留 / 面试 12 个垫片。

---

## 6. 接续点（下次从这里开始）

本轮（2026-09-15）**已收尾**。下次继续时直接看这张表：

| 待办 | 现状 | 起点 |
|---|---|---|
| ~~**聊天分析重复数据**~~ | ✅ 已处理（误判→改加幂等护栏，未删数据） | 本文 §2 C2 |
| **旅游真值源** | 待你拍板 | `module-review-2026-09-15.md` §2 T1 |
| **阅读库真值源（稍后读双写）** | 待你拍板 | 同上 §4 R2 |
| **saved_sync 原生保存：补 adapter 还是删** | 待你拍板 | 同上 §4 R3 |
| **面试 12 个兼容垫片** | 待你拍板（我建议现在摘） | 同上 §5 I3 |
| **健康模块三修**（时间线分页 / 空壳表 / 文档） | 未开始，**风险低** | 同上 §6 H1/H2/H6 |
| **路径统一**（34 处） | ✅ **已清零（2026-09-15，`bc933c16`）**：26 处 CWD + 7 处 parents[N] + weekend 字符串路径 5 处；棘轮基线已清空，`tests/test_architecture_contracts.py` 守住新增 |
| ~~**`sources` + `storage` 模块文档**~~ | ✅ **已完成（2026-09-15）**：`docs/modules/sources.md` 新建、`storage.md` 增补摸底（含原生保存 12 个缺失方法清单） |
| **`self_evolution` 零测试补课** | 🔶 首批 6 条已落地（`cf9ba22c`，State/ContentFilter/空转跳过）；**09-16 第二轮**：+52 例（`reading_schedule` FSRS 全量 + SM-2 算法），并修掉两个真 bug（`_ensure_table` 不可达 / `from_row` 用 `sqlite3.Row.get`）+ 模块文档 `self_evolution.md`。剩余 12 个文件仍零测试 | 同上 |
| ~~**`core` 小模块补课 + 分层闸门**~~ | ✅ **已完成（2026-09-15 晚）**：`tests/core/` 47 例（落库映射完整性 / X 异常类型身份 / 跨平台派生）+ `docs/modules/core.md`；新增 `tests/test_layering_contracts.py` 用 **AST** 守 K5/K6b（区分模块级 import、`TYPE_CHECKING`、函数内延迟 import） | 本文 §4 批次⑮ |
| **`agent` 死包处置** | 待拍板（**建议删除**）：零引用 + 全 TODO 桩，取证见 `docs/modules/agent.md` | 同上 |
| **`clone` `synthesis` `topics` `rag` `ed2k` 小模块补课** | 未开始（批次⑮ 剩余） | 本文 §4 批次⑮ |
| **`eval` 模块文档 + 补测** | 未开始（7,193 行 / 6 个测试文件提及 / 无文档） | 本文 §4 批次⑬ |
| **质量门禁 mypy 55 → 0** | ✅ **已完成（2026-09-15）**：`1aeed497` 收到 70 → 16（剩余全是 `saved_sync/service.py` 原生保存死代码族），`31ef4fdd` 补实现 12 个 Database 方法 + `native_save_tasks` 表后 **全量归零** —— 复核实测 `mypy src/` = `Success: no issues found in 427 source files`；ruff 维持基线 3 条既有 N806。⚠️ mypy 2.3.1 冷缓存全量会撞 pydantic INTERNAL ERROR、TypedDict 结构兼容显著收紧（连 Mapping 都拒）——按文件核对才可靠 | `docs/module-cleanup-inventory-2026-09-14.md` §4 批次③ |

**每批次的固定动作**（本轮已验证有效，照做即可）：

1. 先只读取证（读码 + `sqlite3 ?mode=ro` + curl openapi），**关键结论自己复核一遍**
2. 有缺陷就写回归测试，**先在 worktree@修复前跑一遍证明它会失败**（没失败＝零鉴别力，要改断言口径）
3. 改完跑目标测试 + ruff + mypy（只比对该文件是否引入新错）
4. Python 改动 → `pm2 restart openbiliclaw-api`；前端改动**不用**重启
5. 提交只 add 自己的路径（工作树里常年有另一条工作线的改动）
6. 落 `docs/` + 日志 + 必要时更新本文与 `architecture-map.md`
