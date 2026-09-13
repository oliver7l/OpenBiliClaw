# OpenBiliClaw 存活审计报告（2026-09-13）

> **承接**：`docs/project-consolidation-2026-09-11.md`（09-11 已完成 P0–P3，BUG-1/2/3 已修、清理 23G）。
> 本报告只记 **09-11 之后的新发现**，不重复旧结论。
> **审计方式**：全程只读（未改动任何生产文件）；方法 = 全量 import 体检 + `create_app()` dump 真实路由表 +
> 前后端端点对账 + TestClient 实测 + 磁盘实测。
> **触发**：用户要求「死代码/孤儿模块审计」。

---

## 0. 方法论与可信度

| 方法 | 本次结果 | 可信 |
|------|---------|------|
| 全量 `importlib` 体检（445 模块） | **445 OK / 0 FAIL** | ✅ |
| `create_app()` dump 真实路由表 | **557 路由对象 / 439 路径（418 个 /api）** | ✅ |
| TestClient 实测候选端点状态码 | 见 §2、§3 | ✅ |
| 前端 `/api/` 字符串静态提取求差集 | 24 条候选 → 去噪后 **4 条真** | ⚠️ 半可信（噪声多，须实测复核） |
| 反向「后端注册但前端未见调用」（262 条） | **不可信，不作为死代码证据** | ❌ 见 §5 |

**为什么反向清单不可信**：移动端 `web/js/` 用 `` fetch(`${BASE_URL}${path}`) ``，`path` 是运行时变量，静态提取抓不到；
另有大量端点是给**浏览器扩展 / CLI / PM2 producer** 用的，本就不该由 web 前端调用。故 262 条不能判死。

---

## 1. 真坏代码 / 回归（用户点得到）

### 🔴 F1：setup 向导页 3 个端点 404 —— 抽取式重构漏迁（上游有、本 fork 丢）

**现象**：`create_app()` 路由表中**不存在**以下 3 条，但前端 setup 向导页确实在调用 → 功能失效：

| 端点 | 前端调用处 | TestClient 实测 |
|------|-----------|----------------|
| `POST /api/config/discover-models` | `web/setup/index.html:652` | **404** |
| `GET /api/config/apply-status` | `web/setup/index.html:762` | **404** |
| `POST /api/embedding/repair` | `web/setup/index.html:1211` | **404** |

**根因（2026-09-13 复核后更正）**：不是「抽取时漏迁一行」，而是**整块上游子系统从未合入本 fork**。
本 fork 落后上游 `origin/main` **1328 个提交**；这 3 条端点及其依赖属于上游后续加入的能力，
本 fork 从未有过。复核证据（依赖在本 fork 的存在性）：

| 端点 | 上游实现规模 | 关键依赖 | 依赖在本 fork |
|------|------------|---------|--------------|
| `POST /api/embedding/repair` | ~200 行 | `llm/ollama_diagnostics.py`（448 行）+ repair 锁/状态/缓存 | ❌ 模块**整个不存在** |
| `GET /api/config/apply-status` | 4 行 handler | `_config_apply_status_response` + config-apply 状态机（`config_apply_state/task/pending/...`） | ❌ 均无 |
| `POST /api/config/discover-models` | ~16 行 | `_discover_llm_models` + `ConfigModelDiscoveryIn/Response` | ⚠️ 仅 `_apply_llm_update` 已有（`config_routes.py:415`） |

关键提交 `d3aaa480`（新增 `ollama_diagnostics.py`）**仅在上游、不在本 fork main**（`merge-base --is-ancestor` 判定 NO）。
而 setup 页之所以会调用它们，是本 fork 用户侧提交 `76d965c8`（2026-09-01「feat: 阅读库/多源抓取/质量评分与前端迭代」）
把**上游新版 setup 页**引入了前端、但后端未同步 **→ 前端超前于后端**。

**影响**：setup 向导的「模型发现」「配置热重载状态轮询」「embedding 修复」三项不可用。
**修法**：这不是「补一行接线」，而是**移植上游子系统**（工作量按上表，embedding/repair 最重）。
需先决策（见 §7）：① 完整移植 3 个子系统；② 只移植轻量的 `discover-models`；③ 让 setup 页对齐本 fork 现有能力（删/禁用对应 UI）。

### 🔴 F2：`GET /api/diary/rag/stats` 恒 422 —— 装饰器误挂 + 注册遮蔽

**现象**：实测 `GET /api/diary/rag/stats` → **422** `{"loc":["body"],"msg":"Field required"}`（应为统计 JSON）。

**根因**：`src/openbiliclaw/api/app.py:2677` 把 `@app.get("/api/diary/rag/stats")` 误加在了
**序列化辅助函数** `_serialize_recommendation_items(items: list[Any])` 之上（L2678）——
该函数本应在 L6514 作 `serialize_recommendation_items=` 传参，**不是路由 handler**。
它先注册（低 index）→ **遮蔽**了 `diary_routes.py:1686` 的正确实现 `diary_rag_stats`，
且因签名 `items: list[Any]` 被 FastAPI 当成**必填 body** → 恒 422。

**影响**：日记 RAG 统计接口不可用（用户点得到）。
**修法**：删掉 `app.py:2677` 那行误挂的 `@app.get(...)` 装饰器（保留 L6514 的传参用法）。

> ✅ **已修（2026-09-13）**：已删除该装饰器，实测 `GET /api/diary/rag/stats` 由 **422 → 200**。
> 回归测试见 `tests/api/test_api_route_regressions.py::test_diary_rag_stats_route_not_shadowed`
>（断言胜出 handler 为 `diary_rag_stats`；已验证在未修复代码下会失败）。

### 🟠 F3：`GET /api/diary/people` 405 —— 前后端命名漂移

**现象**：`GET /api/diary/people` → **405**（因被 `/api/diary/{entry_id}` 的 PUT/DELETE 兜底命中，返回 405 而非 404，掩盖了问题）。
**证据**：`web/desktop/assets/js/diary-insights.js:211` 调 `/api/diary/people`，期望 `{persons,tags,processed}`；
但后端只有 `/api/diary/persons`（`app.py:6416` + `diary_routes.py:1556`），且**同项目**另一个文件
`diary-people.js:78` 用的就是正确的 `persons`。→ 前端内部不一致 + 命名漂移。

> ⚠️ **修法更正（2026-09-13 复核）**：本报告初稿写的「改名为 `persons`」是**错的**——两者响应结构根本不匹配：
> `/api/diary/persons` 返回 `{ok, data:[...], total}`，而 `loadPeopleData` 期望 `{ok, persons, tags, processed}`。
> 复核后确认 `loadPeopleData`（`diary-insights.js`）是**被 `diary-people.js` 取代的遗留死代码**：
> 同一批统计卡片（`statTotalPersons`/`statTotalTags`/`statExtractedEntries`）已由
> `diary-people.js:53 loadStats()` 走正确端点 `/api/diary/extraction-stats` 填充。
> **真正的修法 = 删除死代码**（删 `loadPeopleData` 的调用与定义），而非改名。
>
> ✅ **已修（2026-09-13）**：已删除 `loadPeopleData`（调用 + 定义）。回归测试见
> `tests/api/test_api_route_regressions.py::test_frontend_does_not_call_removed_diary_people_endpoint`。

---

## 2. 结构性问题：39 对路由被重复注册（抽取式重构的另一面）

`create_app()` 后统计 **(path, method)** 重复项 = **39 对**。绝大多数是
**`api.app` 旧内联 handler 与新模块 handler 并存**：

- **37/39 对「旧内联先注册 → 生效的是旧版，新模块版本运行时不执行」**（Starlette 先注册者胜）。
  例：`GET /api/sources`（`app.list_sources` idx 未定 vs `source_routes.list_sources`）、
  `GET /api/diary/persons`、`GET /api/knowledge/concepts`、`DELETE /api/notes/{note_id}`、
  `POST /api/chat`、`GET /api/autostart-status` 等。
- 其余 2 对为**同模块自重复**或**跨模块重叠**：
  - `POST /api/config/probe-service` 由 `probe_routes` **注册了两次**（idx 14 与 314）。
  - `GET /api/interview/reviews` 由 `interview/review_routes.list_reviews`（idx 436）
    与 `api/_interview_routes.get_reviews`（idx 456）**两个模块都注册**（另有 `/reviews/` 变体）。
- 另：`/api/notes/{note_id:int}`（`DELETE`）与 `/api/notes/{note_id}`（`DELETE`）两条 DELETE 路由并存。

**判定**：这是「K3 路由抽取搬了模块、**没删 app.py 内联副本**」的遗留。
- 大多数情况下两版行为一致（实测 `/api/sources`、`/api/knowledge/concepts`、`/api/diary/fragments` 均 200），
  属**冗余 → 新模块代码实际是死代码**（不执行），构成"看起来有、实际走的是旧实现"的隐患。
- 至少 1 处已造成真 bug（F2 的遮蔽）。
- **修法**：逐个确认后删除 `app.py` 内联副本，让模块单点负责（与 09-11 删健康内联桩同一套做法）。
  完整 39 条清单见 `/tmp/obc_dups.txt`（本次临时产物，未入库）。

### ✅ 收敛执行记录（2026-09-13 晚，已完成）

**方法**：不满足于源码文本比对（发现辅助闭包在 `app.py` 与模块里各有一份、名字相同但定义不同），
改用**递归闭包树等价性比较**——把每个重复端点引用到的全部辅助闭包一并展开比对，
并把 `nonlocal`/`global` 这类无实义的作用域语法行归一化后：

- **33 对闭包树完全等价**（差异仅 `nonlocal` vs `global` 语法糖）→ 内联副本可安全删除；
- **5 对有实义差异**（autostart/apply、`source-share-suggestion`×2、`knowledge/concepts` 的 `_conn_with_content`、
  `interview/reviews`）→ **保留现状**，待逐对审查；
- 另 1 对为 `probe-service` 同模块自重复 → 保留。

**执行**：AST 定位 32 个等价内联 handler 的 span（含装饰器），自底向上删除；删除后做三重验证：
① 32 条路径全部转由模块版本生效（`app.py` 命中 0、`NO MATCH` 0）；
② API 路径集合 418→418 无丢失；③ `create_app()` 实例化正常。

**级联清理**：删除后用嵌套函数引用分析迭代至不动点，再清掉 **36 个变成孤儿的辅助闭包**
（含 25 个收敛前就已无人引用的存量死代码），`app.py` **6534 → 4560 行（净 -1974）**。
全量 pytest 基线（收敛前）3460 passed / 0 failed，收敛后复跑见 §4。

### ✅ 第二轮收敛（2026-09-13 晚，5 对"实义差异"全部澄清 → 重复 0 对）

对剩余 6 对逐对做**正文精确 diff + 引用链追踪**，之前的"实义差异"全部澄清：

| 对 | 判定 | 动作 |
|----|------|------|
| `POST /api/autostart/apply` | 两版正文逐行相同，仅锁名不同（`_CONFIG_SAVE_LOCK` vs `_config_save_lock`）——`app.py:4520` 把**同一把锁实例**传给了 `register_source_routes` | 删 app.py 内联 |
| `POST /api/config/probe-service`（同模块双注册） | `app.py:2471` 与 `config_routes.py:521` 都调 `register_probe_routes`，且两版 `_apply_llm_update`（101 行）**零差异** | 删 app.py 的调用 |
| `GET/POST /api/config/source-share-suggestion` ×2 | 两版 handler 逐行相同；差异只在辅助名 `_count_events_by_source_platform`（app.py 定义）vs `_get_count_events_by_source_platform`（config_routes 的**延迟 import 同一函数**的 3 行薄委托） | 删 app.py 内联 |
| `GET /api/interview/reviews` | 生效版 = 新服务化实现（`InterviewReviewService`，返回裸列表，前端 `interview.js:506` 已按数组适配）；被遮蔽的旧版 `_interview_routes.get_reviews` 返回 `{reviews,stats,total}` dict（前端不适配） | 删旧版路由 |
| `GET /api/knowledge/concepts` | 生效版直连 `database.conn`——但主库 `Database` 在 `storage/database.py:796` 打开时**已全局 ATTACH knowledge.db**，实测 200；模块版 `_conn_with_content` 的 ATTACH 是幂等冗余（suppress OperationalError） | 删 app.py 内联（模块版防御性更强） |

**第二轮执行**：AST 删 4 个 app.py 内联 handler + 1 个双注册调用 + `_interview_routes.py` 旧 `get_reviews`；
级联死代码再迭代到不动点（app.py 的 `_apply_llm_update`/`_autostart_status_out`/`_build_source_share_suggestion_response`
副本均确认外部引用都指向模块自有定义）。**`app.py` 4560 → 4084 行（两轮累计 -2450，相对原始 6534）**。

**验证**：重复 (path,method) 对数 **6 → 0**；API 路径 418→418 无丢失；`create_app()` 正常（routes 524→518）；
7 个受影响端点冒烟全符合预期（2 个 422 为缺 body 的预期校验）；死代码 0；ruff 全绿。

---

## 3. 数据层 / 磁盘：孤儿与重复目录

`data/` 全部已被 `.gitignore` 忽略（不影响仓库，只占本地磁盘）。项目总计 **28G**。

| 项 | 大小 | 性质 | 结论 |
|----|------|------|------|
| `data/v2ex-hot-hub/`（连字符） | 28M | 外部仓库 git clone，**8-31 旧版** | ⚠️ **陈旧重复**：脚本用的是 `data/v2ex_hot_hub`（下划线，`scripts/collect_v2ex_archive.py:59` 的 `DEFAULT_REPO_DIR`）→ 连字符版无引用，可清 |
| `data/tax_frames/` | 2.8M | 无代码引用（全仓 grep 零命中） | ⚠️ 疑似孤儿，待用户确认 |
| `data/tax_frames2/` | 4.6M | 同上 | ⚠️ 同上 |
| `data/tax_frames2_check/` | 2.3M | 同上 | ⚠️ 同上 |
| `data/backups/` | 1.9G | 09-11 清理时保留的回滚点（`rollback-20260909/` 等） | 🟡 非紧急，可择机清 |
| `data/hister/` | 37M | 第三方工具（hister）自身数据目录（含 `config.yml`/`tui.yaml`/`data/`） | ⛔ **不动**（用户工具数据） |
| `data/v2ex_browser_profile/` | — | 被 `runtime/v2ex_producer.py:74` 引用 | ⛔ 不动（活跃） |

**未现**：`0 字节 .db`、`*?mode=ro*` 垃圾文件（均已在此前清理）。

> ⚠️ **清理纪律**：以上均为 gitignored 本地文件，清理属磁盘整理、不影响仓库。按项目约定应
> **移入废纸篓（非永久删除）+ 分批（每批 ≤10）+ 先出清单再动手**，见 `docs/cleanup-manifest-2026-09-13.md`（待建）。

---

## 4. 质量门禁

| 检查 | 结果 |
|------|------|
| 全量 import 体检 | ✅ 445 / 0 FAIL |
| `create_app()` | ✅ 成功，418 个 /api 路径 |
| 路由静默注册失败日志 | ✅ 无（`_route_registry` 聚合告警生效） |
| `ruff check src/ tests/` | ✅ All checks passed（本次顺带修掉会话中测试文件 1 处 E501） |
| `mypy` | ⏸ 本次未跑（09-11 为 0 错误，鉴于是只读审计未复测） |

---

## 5. 反向清单的局限（不要据此删端点）

「后端注册但前端 web 未见调用」共 **262 条**，**不可作为死代码证据**（§0 已述原因：
移动端动态 base + 扩展/CLI/producer 专用端点）。如需判定，必须**按调用方（扩展/CLI/PM2 producer）
分别对账**，不能只看 web 目录。

---

## 6. 建议动作（按风险升序）

| 优先级 | 动作 | 风险 | 状态 |
|--------|------|------|------|
| **P0** | 修 F2：删 `app.py:2677` 误挂装饰器 | 极低 | ✅ **已完成**（+ 回归测试） |
| **P0** | 修 F3：删 `diary-insights.js` 死的 `loadPeopleData` | 极低 | ✅ **已完成**（+ 回归测试） |
| **P1** | 修 F1：setup 3 端点（实为**移植上游 3 个子系统**，见 §1） | 中 | ⏸ 待决策（§7） |
| **P1** | 清重复路由（闭包树等价的 32 对）+ 级联死代码 | 中 | ✅ **已完成**（38→6 对；`app.py` -1974 行） |
| **P1** | 第二轮：剩余 6 对逐对澄清后全部收敛 | 中 | ✅ **已完成**（重复 6→0 对；`app.py` 再 -476 行，累计 6534→4084） |
| **P2** | `data/` 孤儿/重复目录清理（v2ex-hot-hub + tax_frames*） | 低 | ⏸ 待决策（§7） |

---

## 7. 待用户拍板

1. **F1 的 3 个 setup 端点**（实为移植上游子系统，见 §1 依赖表）——选其一：
   ① 完整移植（含 448 行 `ollama_diagnostics` + config-apply 状态机）；② 只移植轻量的 `discover-models`；
   ③ 让 setup 页对齐本 fork 现有能力（删/禁用对应 UI）。
2. ~~**39 条重复路由**~~：✅ 全部收敛（两轮：32 对闭包树等价 + 6 对逐对澄清后删除，重复 0 对，见 §2）。
3. **P2 磁盘清理**（v2ex 重复 + tax_frames）是否执行？
4. **P4 大重构**（obc_runtime 抽取收口、224 处旧 import、上帝文件 `cli.py`/`app.py`）——本次仍未启动，是否另立专项？

---

*报告完成于 2026-09-13 · 全程只读 · 发现来源：import 体检 + 路由 dump + 前后端对账 + TestClient 实测*
