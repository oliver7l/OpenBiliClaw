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

### ✅ F1：setup 向导与后端整体失配 —— 已修（2026-09-13，含一个初稿漏掉的更大缺陷）

**现象（初稿）**：`create_app()` 路由表中**不存在**以下 3 条，但前端 setup 向导页确实在调用：

| 端点 | 前端调用处 | 初稿判定 | 复核后定性 |
|------|-----------|---------|-----------|
| `POST /api/config/discover-models` | `setup/index.html:652` | 404 | 🔴 **真实可达缺陷**（按钮无条件渲染） |
| `GET /api/config/apply-status` | `setup/index.html:762` | 404 | ⚪ **不可达死分支** |
| `POST /api/embedding/repair` | `setup/index.html:1211` | 404 | ⚪ **不可达死分支** |

**复核更正（可达性实证，而非静态推断）**：

- `apply-status` 不可达：`waitForConfigApply()` 仅在 `result.apply_state ∈ {queued, applying}` 时被调用，
  而本 fork 的 `ConfigUpdateResponse`（`models.py:1413`）**没有 `apply_state` / `apply_revision` 字段** → 该分支永不执行。
- `embedding/repair` 不可达：修复按钮仅在 `prereq.embedding_check` 非空时渲染，而本 fork 的
  `InitPrerequisitesOut`（`models.py:57`）只有 `bilibili_logged_in/bilibili_check/llm_ready/embedding_ready/
  embedding_required/enabled_platforms` **六个字段**，从不发 `embedding_check` / `embedding_repair_*` /
  `embedding_pull_status` / `ollama_phase` → 按钮永不出现。移植 448 行的 `ollama_diagnostics` 子系统
  **对终端用户零可感知收益**。
- 端点级对账（新增脚本化检查，已固化为测试）：向导页引用的 9 个 `/api` 路径中，**只有**上述 3 个缺失，
  其余 6 个均存在 → 问题范围就此收敛，不存在其它漏迁端点。

**🔴 初稿漏掉的更大缺陷：向导第 0 步「保存并继续」自 2026-09-01 起完全无法落盘。**

- 向导页把 LLM 配置构造成**上游 routing v2** 形状：`llm: {routing_version: 2, instances: {...},
  default_chain: [...], routes: {...}}`（`setup/index.html:847`）。
- 本 fork 后端只认 **provider-name** 形状：`_apply_llm_update()`（`config_routes.py:415`）遍历的是
  `default_provider` / `openai` / `claude` / `gemini` / `deepseek` / `ollama` / `openrouter` /
  `openai_compatible` / `embedding` / 4 个 module 段。`instances` 被整块忽略 → `api_key` 从未写入。
- **实测**：同 payload 下 `PUT /api/config` → **400**，`message = "配置校验失败，未写入 config.toml。"`，
  issues 为 `blocking llm: LLM registry would fail to build` + `warning llm.deepseek.api_key: 缺少 api_key`，
  `config.toml` 中确实没有该 key。即：所有新装用户卡在向导第一步。
- **归因证据**：`git log -S "routing_version"` / `-S "default_chain"` 均只命中 `76d965c8`
  （2026-09-01）；全仓 `src/**/*.py` 检索 `"instances"` **零命中** → 后端从未支持过 routing v2，
  属既有缺陷，与 2026-09-13 的路由收敛无关。
- **对照组**：桌面设置页（`profile.js:2131` `buildConfigUpdate`）用的是后端真实形状，因此一直正常；
  坏的只有首启动向导。

**修法（用户选定：实现 discover-models + 修复保存链路 + 删死分支）**：

| # | 动作 | 位置 |
|---|------|------|
| 1 | 新增模型发现纯逻辑层：OpenAI 兼容 `/models`、Ollama `/api/tags`、Anthropic `/v1/models`、Gemini `v1beta/models`；错误一律软失败（`ok=False` + 内联文案），绝不猜官方域名 | `llm/model_discovery.py`（新增） |
| 2 | 新增 `POST /api/config/discover-models`；空 `api_key`/`base_url` 回退到已保存的 `[llm.<provider>]`（兑现向导「留空则沿用当前 Key」） | `api/config_routes.py` |
| 3 | 向导保存改回 provider-name 形状；删除 routing v2 脚手架（`buildSetupLlmRouting`/`savedLlmInstances`/…）、`apply-status` 轮询、embedding 修复按钮链路 | `web/setup/index.html`（1813 → 1701 行） |
| 4 | 移除 `orcarouter`（后端无该 provider 配置段；全仓仅向导页 4 处引用） | `web/setup/index.html` |
| 5 | **降级模式白名单放行新端点** —— 首次运行无可用 LLM 时后端**按定义**处于降级模式，degraded 中间件默认拒绝一切未列白名单路径，不放行则「获取模型」照样 503 | `api/app.py:1305` `_degraded_mode_guard` |

**验证**：`tests/llm/test_model_discovery.py`（19 例）+ `tests/api/test_config_setup_wizard.py`（11 例）。
其中 `test_wizard_llm_payload_shape_is_persisted` 断言新 payload 落盘且 `load_config` 回读一致；
`test_wizard_payload_does_not_use_upstream_routing_schema` 反向锁定 routing v2 仍是 400；
`test_setup_wizard_calls_no_unregistered_endpoint` 对向导页引用的全部 `/api` 路径做注册表对账；
`test_wizard_page_drops_dead_upstream_branches` 静态禁止 `apply-status` / `embedding/repair` /
`routing_version` / `default_chain` / `orcarouter` 回流。

**遗留（未在本轮动，避免越权改动可见 UI）**：
- 向导页 `#apiFlavor`（`responses` 协议）：该字段在 `LLMProviderConfig` 中**根本不存在**，
  `obc_llm` 也没有 `responses` 协议实现 → 选了不生效（payload 仍会带上，后端静默忽略）。
  修法二选一：**删掉该 UI** 对齐现实，或补后端协议支持。
- `[llm.<provider>].num_ctx`：后端**完整支持**（`config.py` → `registry.py` →
  `ollama_provider.py` 的 native `/api/chat`），只是没有前端入口、`LLMProviderConfigOut`
  未暴露 → 属**缺 UI，不是缺后端**（初稿把两者混为一谈，措辞已更正）。
- ~~`_apply_llm_update` 的 provider 白名单缺 `zhipu` / `modelscope` / `siliconflow`~~
  ✅ **已修复**（2026-09-13，见 §7 第 3 条）：根因不是「漏列三项」，而是 provider 集合
  散落 5 处、其中 3 处漏项，而**写盘路径的漏项会让用户手工配置被静默删除**。

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
| `data/v2ex-hot-hub/`（连字符） | 28M | 外部仓库 git clone，**8-31 旧版** | ⚠️ **陈旧重复**：脚本用的是 `data/v2ex_hot_hub`（下划线，`scripts/content_library/collect_v2ex_archive.py:59` 的 `DEFAULT_REPO_DIR`）→ 连字符版无引用，可清 |
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

1. ~~**F1 的 setup 端点**~~：✅ 已完成（2026-09-13）——实现 `discover-models` + 修复向导保存链路
   （routing v2 → provider-name，含降级模式白名单放行），删除 `apply-status` / `embedding/repair` 两处不可达死分支。
   复核结论：初稿的「移植 448 行 `ollama_diagnostics`」并非必要——那两个端点在本 fork 无任何可达路径。
   遗留的 `api_flavor` / `num_ctx` 两处缺口**已收口**（2026-09-13）：前者删除死承诺
   （实证上游 #72 的 `735e1c4c` 不在本 fork 历史中，属"上游 UI 超前"而非"后端缺实现"），
   后者打通 API 层（落盘 + 回传 + 整数校验）。
2. ~~**39 条重复路由**~~：✅ 全部收敛（两轮：32 对闭包树等价 + 6 对逐对澄清后删除，重复 0 对，见 §2）。
3. ~~**F1 遗留的「provider 白名单缺三家」**~~：✅ 已修复（2026-09-13）。
   排查后发现不是「白名单漏列三项」，而是 **provider 集合散落 5 处、其中 3 处漏项**：

   | 位置 | 漏项后果 |
   |------|---------|
   | `_apply_llm_update`（PUT 字段应用） | 提交被静默忽略 |
   | `_render_config_toml`（写盘） | **任何一次保存都删掉该段**（数据丢失） |
   | `LLMConfigOut` + `_config_to_response`（GET 回传） | 读不回来 |
   | `_collect_config_issues`（校验） | 误判「不支持的默认 provider」 |
   | `llm/_compat._PROVIDER_NAMES` | ✅ 正确（10 个，与 `obc_llm` registry 一致） |

   实测（worktree 对照 HEAD 版）：手工写入的 `[llm.zhipu]` 能被 `load_config` 读到，
   但一次 `save_config` 后该段与 `api_key` **全部消失**。已收敛为
   `config.LLM_PROVIDER_NAMES`（从 `LLMConfig` 数据类**派生**，新增字段自动纳入），
   四处引用点全部改用它；回归测试 8 例
   （`tests/config/test_llm_provider_sections.py` + `tests/api/test_config_provider_sections.py`）。
4. ~~**P2 磁盘清理**~~：✅ 已执行（2026-09-13）——`data/v2ex-hot-hub`（28M 陈旧重复 clone，
   活跃脚本用的是下划线版）与 `data/tax_frames{,2,2_check}`（~9.7M，09-10 一次性提取的
   PNG 帧，代码/文档/JSON 状态/全部 DB 均零引用）已移入废纸篓（可还原）。
   `data/backups/`（1.9G 回滚点）**未动**——非紧急，保留与否仍由用户定。
5. **P4 大重构**：🟡 已完成六刀（2026-09-13 ~ 09-14）——
   **第一刀**：`llm/registry.py` 与 `llm/_compat_registry.py` 的 **5 个逐字相同适配入口**
   （`build_llm_registry` / `build_embedding_service` / `summarize_registry` /
   `_maybe_openai_compatible_provider` / `_ollama_is_chat_capable`）已去重，唯一实现归
   `_compat_registry`，`registry.py` 只转发（77 → 37 行）。
   **第二刀**：note 命令组（9 命令 + 服务工厂，~270 行）抽至 `cli/_cmd_notes.py`；
   同刀修复 note 命令**静默 no-op**（`_APP_CONTEXT` 无 `data_dir`/`config` 写入点）与
   `Database` 未 `initialize()` 两个既有 bug，守门测试 `tests/cli/test_cli_notes_module.py`。
   **第三刀**：`cost` / `logs-prune`（~320 行）抽至 `cli/_cmd_usage.py`；patch 语义
   经「函数体内 `from openbiliclaw import cli as _cli` 动态取属性」保留（cost 的
   `_ensure_runtime_database_healthy` / `_get_runtime_database`），守门
   `tests/cli/test_cli_usage_module.py`。
   **第四刀**：autostart 组（3 命令 + 8 helper）抽至 `cli/_cmd_autostart.py`；三个渲染
   helper 抽至共享 `cli/_render.py`；同刀发现并修复第三刀引入的隐性回归——降级面板
   测试补丁点漂移（helper console 本体迁至 `_render`，测试仍 patch `cli.console` →
   空串断言，且曾被 `database is locked` 环境错误掩盖），修复=补丁点迁至本体模块。
   守门：`tests/cli/test_cli_autostart_module.py`（5 例，含本体补丁生效性）。
   **第五刀**：fetch-\*/search-\*/discover-\* 平铺组（13 命令）+ discovery runtime
   helpers + typer 参数常量（~1245 行）抽至 `cli/_cmd_fetch.py`（`register(app)`
   挂载，命令形状不变）；50+ 处共享符号调用统一 `_cli.X` 动态取（ruff F821
   逐轮兜底补漏，含无括号引用传值形态）；`_print_discovered_content_preview`
   迁入 `_render`。守门 `tests/cli/test_cli_fetch_module.py`（5 例）。
   **第六刀**：init 引导组（`init` 命令 372 行 + 11 个问询/落盘 helper，共 ~988 行）
   抽至 `cli/_cmd_init.py`；19 处共享符号调用 `_cli.X` 动态取（7 个外部被 patch
   符号 + 4 个本组 patch 点）；`init` 及 9 个测试直引/patch 符号在 cli 命名空间
   re-export（`cli_module.init` 签名检查等既有测试不变）。守门
   `tests/cli/test_cli_init_module.py`（6 例，含「patch 经 cli 命名空间可命中」
   的行为锁）。六刀累计 **7087 → 4039 行**（-3048，约 -43%）；顶层 42 命令
   worktree 对账零丢失。
   **第七刀**：soul 画像 / 推荐 / 对话组（9 个平铺命令，~945 行）抽至 `cli/_cmd_soul.py`
   （`register(app)` 挂载，命令名与形状不变）；12 个外部 patch 敏感符号统一
   `_cli.X` 动态取；`profile` 因与 cli 内既有形参
   `_run_init_discovery_backfill_async(profile=...)` 同名、re-export 会触发 ruff F811，
   **故意不 re-export**（命令本体仍由 `register()` 挂在 app 上）。同刀归位
   `_run_single_source_bootstrap` → `_cmd_fetch.py`、`_print_recommendation_card`
   → `_render.py`；顺带修 `tests/weekend` 的日期炸弹用例（`include_expired=True`）。
   守门 `tests/cli/test_cli_soul_module.py`（9 例，含 recommend / chat 两条行为锁）。
   七刀累计 **7087 → 3057 行**（-4030，约 -57%）；90 条命令路径 worktree 对账
   `diff` 为空。
   **obc_runtime 抽取收口维持暂停**（v0.3.235 评审：runtime 44 文件依赖全部模块，
   收益低成本高）。「旧 import」路径 776 处（llm 183 + soul 407 + discovery 186）迁移
   与上帝文件后续簇（probe 已随第七刀迁出；config 显示 / start / set-password /
   引导交互 _save_runtime_provider_config 家族与构建 helper 等约 25 命令）待续。

   | 项 | 实测规模 | 备注 |
   |----|---------|------|
   | `obc_runtime` 包 | **维持不建（暂停评审结论）** | runtime 44 文件依赖全部模块，收益低成本高 |
   | 「旧 import」路径 | **776 处**（llm 183 + soul 407 + discovery 186） | 迁移须与测试 monkeypatch 补丁点同步核查 |
   | 兼容垫片 | **25 个 `sys.modules[__name__]` 模块别名** + 一批 3 行 re-export | 别名家族用于保留 `monkeypatch.setattr` 补丁语义（类身份唯一），**不可按「零引用即删」处理** |
   | 上帝文件 | `cli/__init__.py` **4039 行**（已拆 note / cost+logs-prune / autostart / fetch-* / init 五簇）；`api/app.py` 4084 行 | 建议继续按自洽簇抽离（profile 系 / probe / config 显示等） |

---

*报告完成于 2026-09-13 · 全程只读 · 发现来源：import 体检 + 路由 dump + 前后端对账 + TestClient 实测*
