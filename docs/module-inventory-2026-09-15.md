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
| `sources` | 11,263 | 56 | 40 | ❌ **无** | 09-13 | ⬜ 未梳理 |
| `storage` | 11,173 | 35 | 66 | ❌ **无** | 09-11 | ⬜ 未梳理 |
| `self_evolution` | 9,284 | 15 | **0** | ❌ **无** | 09-14 | 🔴 **最该先梳理** |
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
| `saved_sync` | 1,107 | 5 | **0** | ❌ **无** | 09-11 | ✅ 已梳理（有已知缺陷待修） |
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
| `agent` | 240 | 3 | **0** | ❌ **无** | 09-14 | ⬜ 未梳理 |
| `core` | 204 | 3 | **0** | ❌ **无** | 09-13 | ⬜ 未梳理 |
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
> ⚠️ **存量 219 个会话未重置**（重置 = 800 会话 × 3 次 LLM 真实消耗，需你授权）。

### 🔴 C2. 导入不幂等：832 条 chunk 里 419 条是重复（**已亲自复核**）

`importer.py:236`（deepseek）、`:339`（articles）无条件 INSERT；`chat_analysis_chunks` 无 UNIQUE 约束。
实测：413 组重复、419 行多余 / 总 832 行 —— **重复率约 50%**。消息表 3,614,760 行里大概率同样有重复。

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
| **⑮** | 小模块补课：`clone` `synthesis` `topics` `agent` `core` `rag` `ed2k` | 都是 200–1,000 行、零或近零测试，适合批量补文档 + 补测 |
| **⑯** | `scripts/` + `extension/` + 两个前端 | 索引与文档建设，风险最低 |

## 5. 需要你拍板的（承接上一轮）

1. **存量 219 个假已分析会话**：要不要把它们的 `analyzed` 重置为 0？（代码已修，不会**再**污染；但存量需要重置才能被重新分析，代价是约 800 会话 × 3 次 LLM 真实消耗）
2. **重复的 419 条 chunk / 可能成倍的消息重复**：要不要加 UNIQUE 约束 + `INSERT OR IGNORE`，并清洗存量？（会动 924 MB 的库，建议先备份）
3. 上一轮那 4 项仍待定：旅游真值源 / 阅读库真值源 / saved_sync 去留 / 面试 12 个垫片。
