# 自进化中枢（self_evolution）

> 定时任务中枢 + 内容加工厂：读行为、挖专题、生成卡片/图谱/TLDR/学习路径、主动推送。
> 15 个文件、9,296 行，**此前只有 1 个测试文件**——所以它同时是「正在产生脏数据」和
> 「坏了没人知道」的重灾区。

## ⚠️ 先分清四个「自进化」

| 名字 | 实际是什么 | 端点 |
|---|---|---|
| **`self_evolution/` 包**（本文） | 内容加工/洞察中枢，15 文件 9,296 行 | `/api/self-evolution/*` |
| `diary/self_evolution.py`（1,644 行） | **日记系统**的夜间自我改进循环（画像/漂移/夜间日志） | `/api/diary/self-evolution/*` |
| `runtime/` 里的 loop | 真正驱动定时跑的调度器 | 无 |
| `synthesis/` 包 | 迭代合成（复用同一个 `db_path`，但语义不同） | `/api/synthesis/*` |

两者都叫 self-evolution、都写 `openbiliclaw.db`，但**没有任何代码关系**。

## 组件（按行数）

| 文件 | 行数 | 职责 | 测试 |
|---|---:|---|---|
| `insight_report.py` | 1,014 | 周期性洞察报告（按时间窗分析阅读行为） | ❌ |
| `content_filler.py` | 1,010 | 离线内容填充（正文/字幕/AI 摘要），按配额、增量 | ❌ |
| `loop_engine.py` | 919 | **循环引擎**：持久化状态、跨库内容筛选、各步骤编排 | ✅ |
| `auto_topic_generator.py` | 720 | 从内容库自动发现主题并生成跨平台专题 | ❌ |
| `proactive_push.py` | 693 | 主动推送（监控内容库与兴趣，产出通知） | ❌ |
| `knowledge_graph.py` | 657 | 个人知识图谱（实体 + 关系抽取） | ❌ |
| `knowledge_card.py` | 646 | 知识卡片 + **SM-2 间隔重复调度** | ✅（SM-2 部分） |
| `api.py` | 599 | 全部 `/api/self-evolution/*` 端点 | ❌ |
| `tldr.py` | 540 | 按需 TL;DR（3–5 条要点） | ❌ |
| `learning_path.py` | 536 | 结构化学习路径 | ❌ |
| `reading_schedule.py` | 486 | **阅读调度器（简化 FSRS）** | ✅ |
| `insights.py` | 486 | 知识缺口 + 跨平台洞察 | ❌ |
| `interest_drift.py` | 456 | 兴趣漂移检测 | ❌ |
| `topic_miner.py` | 428 | 主题挖掘（新兴主题发现） | ❌ |
| `__init__.py` | 106 | 导出 | — |

挂载点：`api/_web_ui_routes.py:267` → `create_self_evolution_router(db_path, llm_service)`；
`db_path` 取 `config.storage.db_path`（回落 `data/openbiliclaw.db`）。

## 端点（`/api/self-evolution/*`，共 35 个）

| 分组 | 路径 |
|---|---|
| 洞察报告 | `GET /insight-reports`、`POST /insight-reports/generate`、`GET /insight-reports/{id}` |
| 兴趣漂移 / 主题 | `GET /drift`、`GET /topics`、`GET /auto-topic/candidates`、`POST /auto-topic/{generate,auto-generate}` |
| 知识卡片 | `GET /knowledge-cards`、`POST /knowledge-cards/generate`、`GET /knowledge-cards/due`、`POST /knowledge-cards/{id}/review` |
| 知识图谱 | `GET /knowledge-graph`、`GET /knowledge-graph/entity/{id}` |
| 通知 | `GET /notifications`、`POST /notifications/check`、`POST /notifications/{id}/read`、`/dismiss` |
| 学习路径 | `GET /learning-paths`、`POST /learning-paths/generate`、`PATCH /learning-paths/{id}/steps/{i}`、`DELETE /learning-paths/{id}` |
| TLDR | `GET /tldrs`、`GET /tldrs/{article_id}`、`POST /tldrs/{article_id}/regenerate`、`POST /tldrs/batch-generate`、`DELETE /tldrs/{article_id}` |
| 内容洞察 | `POST /insights/generate`、`GET /insights/latest` |
| **阅读排期** | `GET /reading-schedule/stats`、`GET /reading-schedule/daily`、`GET /reading-schedule/{article_id}`、`POST /reading-schedule/{article_id}/review`、`POST /reading-schedule/batch-register` |
| 其它 | `GET /status` |

## 已修的缺陷（2026-09-16，补测时发现）

### 🔴 `ReadingScheduler._ensure_table` 是一段不可达代码

它被写在 `_get_conn()` 的 `return conn` **之后**，而 `_get_conn` 又被它调用（挂回去会
无限递归）⇒ **全新库上 `reading_schedule` 永远建不出来**。后果不是报错而是静默：
`register_article` 吞掉 `no such table` 返回 `False`。真实库里表是历史遗留的
（501 行），所以一直没暴露。
**修法**：建表挪到 `__init__`（失败只 warning，不连累读路径）。

### 🔴 `ReadingScheduleItem.from_row` 用了 `sqlite3.Row.get()`

那是 dict 的方法，`sqlite3.Row` 没有 ⇒ `AttributeError`。三个调用方全中：
`review_article` / `get_daily_queue` / `get_article_schedule`——即**「复习」与「今日队列」
两条主路径在生产上是坏的**（线上表现是 500）。
**修法**：用 `row.keys()` 判断列是否存在（查询有两种形状：纯调度行 / JOIN `articles` 后多
三列），缺失列给默认值。

### 🟡 `register_article` 的返回值与 docstring 相反

`INSERT OR IGNORE` 撞 UNIQUE 不抛异常，于是重复注册也返回 `True`；docstring 却写
「False 如果已存在」。**修法**：按 `cursor.rowcount > 0` 返回。

### 未修 / 待观察

- **`SM2Scheduler` 写 naive 本地时间**（`datetime.now()`），而 `ReadingScheduler` 写
  aware UTC（`datetime.now(UTC)`）——两者相减会抛
  `TypeError: can't subtract offset-naive and offset-aware datetimes`。统一要连已落库的
  naive 旧行一起迁移，本轮只把事实钉进测试（`test_timestamps_are_naive_local_time`）。
- **`/reading-schedule/stats` 现在显示 `due_today: 501`（= 全部）**：501 篇是历史批量注册
  的，`next_review_at` 早已过期且从未复习过，于是「到期复习」桶永远吃满 `limit`，
  `new_count` 基本没机会生效。这是数据状态而非代码 bug，但要重建阅读节奏就得先
  `batch-register` + 实际复习几轮。

## 测试（`tests/self_evolution/`，58 例，2026-09-16 扩到 3 个文件）

| 文件 | 覆盖 |
|---|---|
| `test_loop_engine.py` | `SelfEvolutionState` 持久化/脏值容错、`ContentFilter` 跨库筛选、**无 LLM 必须跳过**（锁 `5d31f193` 的「空转毒化」修复） |
| `test_reading_schedule.py` | 建表引导（真 bug 回归）、注册幂等、FSRS 四档评级数学、难度/进度夹取、状态跃迁、`from_row` 容错、今日队列排序与上限、统计 |
| `test_knowledge_card_sm2.py` | SM-2 间隔阶梯（1→6→ceil(int×EF)）、**EF 用更新前的值**、EF 下限 1.3、评分夹取、naive 时间戳现状 |

全部 `tmp_path` 隔离；`reading_schedule` 的测试要**同时造 `openbiliclaw.db` 与同目录
`content.db`**（`_get_conn` 会 ATTACH `content.db` 去 JOIN `articles`）。

鉴别力校验：6 组故意改错各自变红（建表调用挪回 return 后 / `from_row` 改回 `row.get` /
register 恒 True / SM2 先更 EF 再算间隔 / 去掉 EF 下限 / 去掉 quality 夹取）。

## 已知缺口

- **12 个模块仍零测试**（`insight_report` `content_filler` `auto_topic_generator`
  `proactive_push` `knowledge_graph` `api` `tldr` `learning_path` `insights`
  `interest_drift` `topic_miner`）。优先建议：`interest_drift` / `topic_miner` /
  `insights`（纯算法、不需要 LLM 也能测）；`api.py` 可用 FastAPI TestClient 补端点级冒烟。
- **本模块没有模块文档之外的运维说明**：谁在什么时候调用哪个步骤（`loop_engine`）只在
  代码里；`runtime/` 侧还有一层调度。
- ⚠️ pm2 日志 `/tmp/openbiliclaw-out.log` 已 **2.96 GB**（无轮转）——排查本模块问题时
  别用 `grep` 全量扫，用 `tail -c` 取窗口。
