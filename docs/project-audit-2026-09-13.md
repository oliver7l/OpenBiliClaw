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

**根因**：上游 `origin/main` 的 god-file `src/openbiliclaw/api/app.py` **有**这些端点
（`apply-status` L17540、`discover-models` L18430、`embedding/repair` L6201）。本 fork 把 `app.py`
的路由抽取到 `config_routes.py` 时**只迁了 `GET/PUT /api/config`**，这 3 条**没跟着迁、也没在内联保留**
→ 「提取后没接线」的典型重构半成品。setup 页（沿用上游/已更新）因此调用到不存在的端点。

**影响**：setup 向导的「模型发现」「配置热重载状态轮询」「embedding 修复」三项不可用。
**修法（低风险）**：把上游这 3 个 handler 迁到 `config_routes.py` / 对应模块（或内联补回）。

### 🔴 F2：`GET /api/diary/rag/stats` 恒 422 —— 装饰器误挂 + 注册遮蔽

**现象**：实测 `GET /api/diary/rag/stats` → **422** `{"loc":["body"],"msg":"Field required"}`（应为统计 JSON）。

**根因**：`src/openbiliclaw/api/app.py:2677` 把 `@app.get("/api/diary/rag/stats")` 误加在了
**序列化辅助函数** `_serialize_recommendation_items(items: list[Any])` 之上（L2678）——
该函数本应在 L6514 作 `serialize_recommendation_items=` 传参，**不是路由 handler**。
它先注册（低 index）→ **遮蔽**了 `diary_routes.py:1686` 的正确实现 `diary_rag_stats`，
且因签名 `items: list[Any]` 被 FastAPI 当成**必填 body** → 恒 422。

**影响**：日记 RAG 统计接口不可用（用户点得到）。
**修法**：删掉 `app.py:2677` 那行误挂的 `@app.get(...)` 装饰器（保留 L6514 的传参用法）。

### 🟠 F3：`GET /api/diary/people` 405 —— 前后端命名漂移

**现象**：`GET /api/diary/people` → **405**（因被 `/api/diary/{entry_id}` 的 PUT/DELETE 兜底命中，返回 405 而非 404，掩盖了问题）。
**证据**：`web/desktop/assets/js/diary-insights.js:211` 调 `/api/diary/people`，期望 `{persons,tags,processed}`；
但后端只有 `/api/diary/persons`（`app.py:6416` + `diary_routes.py:1556`），且**同项目**另一个文件
`diary-people.js:78` 用的就是正确的 `persons`。→ 前端内部不一致 + 命名漂移。
**修法**：把 `diary-insights.js:211` 改为 `/api/diary/persons`（并核对返回字段名）。

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

| 优先级 | 动作 | 风险 | 说明 |
|--------|------|------|------|
| **P0** | 修 F2：删 `app.py:2677` 误挂装饰器 | 极低 | 一处删除，恢复 `/api/diary/rag/stats` |
| **P0** | 修 F3：`diary-insights.js:211` 改用 `persons` | 极低 | 单行 |
| **P1** | 修 F1：迁回 setup 3 端点（discover-models / apply-status / embedding-repair） | 低 | 参照上游 `origin/main` 的 app.py 实现 |
| **P1** | 清 F1 的 39 条重复路由：确认后删 `app.py` 内联副本 | 中 | 逐条验证再删，跑全量测试回归 |
| **P2** | `data/` 孤儿/重复目录清理（v2ex-hot-hub + tax_frames*） | 低 | 先出 `cleanup-manifest-2026-09-13.md` 再移废纸篓 |

---

## 7. 待用户拍板

1. **F1 的 3 个 setup 端点**：是**补回实现**（推荐，上游有现成实现可参照），还是**删掉 setup 页对应 UI**？
2. **39 条重复路由**：是否启动「删 app.py 内联副本、模块单点负责」的收敛（需配套回归测试）？
3. **P2 磁盘清理**（v2ex 重复 + tax_frames）是否执行？
4. **P4 大重构**（obc_runtime 抽取收口、224 处旧 import、上帝文件 `cli.py`/`app.py`）——本次仍未启动，是否另立专项？

---

*报告完成于 2026-09-13 · 全程只读 · 发现来源：import 体检 + 路由 dump + 前后端对账 + TestClient 实测*
