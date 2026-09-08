# OpenBiliClaw 重构规划（2026-09）

> 编写日期：2026-09-08
> 基线：现状以 [development.md](development.md)（as-is baseline，已知问题 K1–K11）为准
> 关联方案：[module-extraction-plan.md](module-extraction-plan.md)（4 包抽取，进行中）、[handoff-20260907.md](handoff-20260907.md)（app.py 拆分批次表，部分已失效）
> 本规划只做治理排序与做法设计；**不包含在本文档内执行代码改动**，每阶段实施时另行开工。

---

## 0. 目标与原则

### 目标
把"功能增长快、重构半途"的仓库收敛回**可信任基线**：CI 全绿 → 一份事实（无重复实现/死代码）→ 模块可独立开发 → 分层单向依赖 → 抽取收口 → 技术债清零。

### 核心原则（每条都对应一次踩坑教训）

| 原则 | 对应教训 |
|---|---|
| 每阶段独立成 PR、可回滚、以测试为证 | 重构期间功能并行，冲突面要小 |
| **先接后删**：先接线新模块/新包，验证后再删旧实现 | K3：路由"先拆后不接"，留下 1 万行死代码 |
| 抽取包必须**零反向依赖**主项目 | 实测 obc-discovery 仍含 45 处 `openbiliclaw.*` 运行时引用 |
| 抽取必须先做**配置适配层**再切主项目 | K1：obc-llm 已建 `_config.py` 协议，但 `build_llm_registry` 仍直读旧 `Config.openai`，171 测试失败 |
| 等价性验证是合入门禁（route audit / 依赖矩阵 / pytest） | CI 三闸（ruff/mypy/pytest）当前全红，无门禁则重构无保护 |
| 文档与代码同步合入（AGENTS.md 强制） | — |

### 当前基线（实测，2026-09-08）
- pytest：3,359 通过 / 171 失败 / 20 错误（含收集错误）
- ruff：622 errors（143 可自动修复）
- mypy：被 `web/clone/` 阻断无法跑全量
- CI（`.github/workflows/ci.yml`）：跑 ruff + mypy + pytest + Firefox 构建 + guided-init E2E，**当前三个闸全红**

---

## 1. 阶段总览

```
阶段0 止血(CI绿) → 阶段1 一份事实(双份/死代码/卫生) → 阶段2 拆巨文件 → 阶段3 修分层 → 阶段4 抽取收口 → 阶段5 技术债(持续)
        ↑ 无依赖         ↑ 依赖0              ↑ 依赖1        ↑ 依赖2(部分并行)  ↑ 依赖0/1        ↑ 可与2/3并行
```

| 阶段 | 主题 | 解决问题 | 预计 | 依赖 |
|---|---|---|---|---|
| 0 | 止血：恢复 CI 与回归基线 | K1、K9 | 1 天 | 无 |
| 1 | 一份事实：双份实现 / 死代码 / 仓库卫生 | K2、K3、K4、K11 | 1.5–2 天 | 0 |
| 2 | 拆巨型文件 | app.py、database.py、cli.py、refresh.py | 2–3 天 | 1 |
| 3 | 修分层倒挂 | K5、K6、K7 | 1.5–2 天 | 2（部分可并行） |
| 4 | 收口模块抽取 | obc-discovery 反向依赖、obc-runtime 决策 | 1–1.5 天 | 0、1 |
| 5 | 技术债与质量（持续） | K8、K10、K9、测试分层 | 持续 | 可并行 |

主线合计约 **7–9.5 个工作日**（不含阶段 5 持续项）。

---

## 2. 阶段 0：止血 —— 恢复 CI 与回归基线（P0）

### 2.1 K1：抽取包与主项目配置的适配层
- **根因**：`packages/obc_llm/obc_llm/registry.py:454` 直接访问 `config.openai`（旧主项目 Config 形态）；`obc_llm/_config.py` 已定义 `LLMConfig` 协议但无人做**主项目 Config → LLMConfig** 的转换。
- **做法**：
  1. 在 `src/openbiliclaw/llm/`（或 `api/runtime_context.py`）提供 `to_llm_config(main_config) -> obc_llm._config.LLMConfig` 适配函数；`build_llm_registry` 收 LLMConfig，不再依赖主项目 Config 形状。
  2. `src/openbiliclaw/llm/prompts.py` stub 显式 re-export `_AWARENESS_SYSTEM_PROMPT` 等私有名（对齐 `registry.py` stub 对 `_ollama_is_chat_capable` 的处理）；`test_llm_prompts.py` 收集错误即消。
  3. 同法核查 obc-discovery 是否有 Config 直读（grep `openbiliclaw.config`）。
- **验证**：`pytest tests/test_llm_prompts.py tests/test_llm_registry.py tests/test_llm_routing.py` 全过。

### 2.2 测试环境类失败
- `test_source_recipe` 等 `sqlite3.OperationalError: unknown database pool`：tmp 库缺少 `pool.db`。做法：测试 fixture 统一创建（或连接 helper 对缺失 pool.db 容忍）。
- `tests/test_api_xhs_ingest.py` / `test_x_creators.py` / `test_xhs_tasks.py` 收集错误：逐一分类（抽取 stub 缺名 or 测试代码过期）。
- `notes/synthesis/generator.py:104` await MagicMock：修正测试 mock 或代码返回 awaitable，二选一后固定契约。

### 2.3 质量闸临时放行
- mypy：pyproject 加 `exclude = ["src/openbiliclaw/web/clone/"]`（临时，阶段 1 移走 sites 后移除）。
- ruff：`ruff check --fix` 先修 143 个自动修复项；其余按文件分批清零；**新增文件不豁免 E501**。

### 2.4 回归护栏
- 新增本地脚本 `scripts/ci-check.sh`：ruff check + mypy + `pytest -q --continue-on-collection-errors`，作为提交前门禁；CI 保持现有三个闸。
- 验收口径：pytest 失败收敛到 **<10 且全部有解释**（可挂已知失败清单），随后阶段 1 内归零。

---

## 3. 阶段 1：一份事实 —— 双份实现 / 死代码 / 仓库卫生（P1）

### 3.1 K2：soul 双份实现收口（决策点）
- **现状**：`src/openbiliclaw/soul/` 26 文件全量真实实现（runtime_context 直接 import），`packages/obc-soul/obc_soul/` 第二份拷贝，`__init__` re-export 指向包；两边已漂移。
- **推荐**：完成抽取（与 llm/discovery 一致，pyproject 已声明 `obc-soul` 依赖）。
  1. `diff -r src/openbiliclaw/soul packages/obc-soul/obc_soul` 收敛差异：只允许 import 路径差异（`openbiliclaw.*` ↔ `obc_*`）；实质差异逐项合并。
  2. `src/openbiliclaw/soul/*.py` 全部转 stub re-export（同 llm/discovery 模式）。
  3. 全量测试 + grep 确认无代码再引用 `openbiliclaw.soul.X` 的真实实现。
  4. 删除 src 全量副本，提交。
- **回滚条件**：若 diff 显示实质差异过大（> 数个文件），反向收口——撤销 `obc-soul` 依赖、`packages/obc-soul` 移出仓库、src/soul 保持唯一实现，并更新 module-extraction-plan 说明推迟。
- **硬规则**：双份并存不允许进入下一阶段。

### 3.2 K3：路由死代码（先接后删）
- **现状**：app.py 内联 ~297 路由（含 diary 87 / health 69 / source 33）；13 个 `*_routes.py`（~1 万行 / 319 路由定义）未被注册；`chat_analysis_routes` 由 cli.py 侧信道注册。
- **做法**（复用 handoff 批次表与 route audit 思路）：
  1. 函数级 diff 校验 `*_routes.py` 与 app.py 内联是否一致；不一致的以 **app.py 为准**更新 `*_routes.py`（当前生效的是内联版）。
  2. 按依赖复杂度逐批：app.py 删除该批内联路由 → 调用 `register_*_routes(app, ctx, ...)`；每批跑 route audit（提取前后路由集合 diff，无丢失无重复）+ pytest。
  3. `chat_analysis_routes` 并入 `create_app()` 正常注册，删除 `cli.py::_run_api_server` 的侧信道注册。
  4. 收尾 grep：每个 `register_*` 都有调用方；app.py 内联路由 ≤10 条（仅保留壳层与 auth 门禁）。
- **防再犯**：新功能路由一律新增 `api/*_routes.py` 并在 `create_app` 末尾接线，禁止内联（写进 AGENTS.md）。

### 3.3 K4 / K11：仓库卫生
- `web/clone/sites/`（3.0GB 克隆整站数据）移出 `src/` → `data/clone-sites/`（`data/` 已在 .gitignore）；`web/clone` 的代码文件（sync/scrape/generate）保留在 src，剔除 sites 数据；同步调整 mypy exclude。
- `.git` 清理：确认无并发 git 进程后 `git gc --prune=now`；`git count-objects -vH` 前后对比（当前 9133 loose objects + 大量 tmp_pack/tmp_obj）。
- 删除根目录游离 `openbiliclaw.db`（0B）/ `pool.db`（确认非运行中残留）；`config.toml.bak*` 确认不需要后删除。

### 3.4 阶段 1 验收
- pytest 全量绿（0 错误 / 0 失败）；route 集合与拆分前完全一致；`du` 确认 web/clone 不再占 src；grep 无孤儿 register。

---

## 4. 阶段 2：拆巨型文件（P1）

> 阶段 1 完成后 app.py 应已 ≤6k 行（仅剩 create_app 组装，合理）。本节拆其余三个。

| 文件 | 现状 | 拆分方案 |
|---|---|---|
| `storage/database.py` | 9,047 行 / 单类 274 方法 | 按功能域拆 Mixin：`pool` / `events` / `saved_sync` / `health` / `discovery_candidates` / `knowledge_forge` / `auth`；`Database(*mixins)` 组装。机械搬迁，不改任何方法行为 |
| `cli.py` | 8,769 行 / 183 函数 / ~32 命令 | `cli/` 子包：`cli/fetch.py`、`cli/discover.py`、`cli/profile.py`、`cli/init.py`、`cli/maintenance.py`、`cli/notes.py` 等；typer app 在 `cli/__init__.py` 组装 |
| `runtime/refresh.py` | 3,243 行 | 按 replenishment / pool trim / eval drain / runtime-state 四个职责拆 |

- 每个文件拆分：机械搬迁 → pytest 全绿 → ruff/mypy 通过 → grep 无断链 → 独立 PR。
- 期间顺手处理 `api ↔ cli` 双向依赖：`run_guided_init` 抽到 `runtime/init_flow.py`，api 与 cli 共同依赖（详见阶段 3）。

---

## 5. 阶段 3：修分层倒挂（P2）

### 5.1 目标依赖方向（单向）
```
api / cli (入口)
   ↓
runtime (编排)
   ↓
soul / discovery / recommendation / sources (领域)
   ↓
llm / memory / storage (基础设施)
```
包间不允许反向：`storage` 不依赖领域模块；`sources`（适配层）不依赖 `soul`/`discovery`；`api` 不依赖 `cli`。

### 5.2 具体动作
- **K5 storage 去领域依赖**：`Database` 移除对 `sources` / `saved_sync` / `knowledge_forge` 的 import；领域相关查询改由对应 service 组装参数调用（或回调注入）。
- **K6 api↔cli**：`run_guided_init` 迁至 `runtime/init_flow.py`；api 依赖它，cli 也依赖它；`cli.py::_run_api_server` 只 import `create_app`（单向可接受）。
- **K6b sources 解耦**：各 adapter/strategy 只依赖 `sources/event_format.py` 值对象 + 注入协议，不再直接 import `soul` / `discovery`。
- **K7 统一 DB 连接**：新增 `storage/connection.py::connect_main(pool_attach=True)`；14 份 `_obc_connect` 收敛为引用该 helper（一行函数替换，行为不变）；60 处裸 `sqlite3.connect` 分批替换。
- **验证**：复用 AST 依赖矩阵脚本（本次体检同款），输出确认上述反向依赖清零；pytest 全绿。

---

## 6. 阶段 4：收口模块抽取（P2）

- **obc-discovery 反向依赖清零（45 处）**：`strategies/douyin_direct.py` / `youtube.py` 对 `openbiliclaw.soul.profile` / `openbiliclaw.storage.database` / `openbiliclaw.sources.douyin_direct` / `openbiliclaw.youtube.client` 的懒引用 → 改为注入接口 / 协议，或把平台专属 normalize 函数移回主项目 adapter 后由策略接收回调。
- **obc-llm**：阶段 0 完成 Config 适配后复查 `_protocols.py` 覆盖完整性。
- **obc-soul**：阶段 1 已收口，此处只做包内测试/文档核对。
- **obc-runtime（决策项）**：**建议暂停**。runtime 39 文件依赖全部模块，抽取收益低、成本高；先完成 producer 去重（阶段 5）与分层修正（阶段 3）后再评估。若决定继续，按 module-extraction-plan §5 执行并补 `RuntimeConfig` 协议。
- 包测试归属：三个包的测试保留在主项目 `tests/`（单一测试入口），不强制搬入包内。

---

## 7. 阶段 5：技术债与质量（P3，持续）

| 项 | 问题 | 做法 |
|---|---|---|
| TD-001 | 画像写无串行化（last-write-wins） | 统一 profile mutation queue 或提交前 rebase（读最新 → merge → 写回） |
| TD-004 | pipeline 非单入口 | `analyze_events` / `learn_from_dialogue` / account sync 收敛为 `ProfileSignal → pipeline.ingest` |
| TD-006 | `soul/dialogue.py:129` 裸 `asyncio.create_task` | 接入 `BackgroundTaskRegistry`，加任务状态记录 |
| K10 | 15 个 producer 重复骨架 | 提取 `BaseDiscoveryProducer`（enqueue→claim→wait→normalize→ingest→ledger/health），收敛 `_obc_connect` 与轮询逻辑 |
| K9 | ruff 622 / 新增文件豁免 | 清零 + 移除 per-file E501 豁免（app.py 拆分后） |
| K10b | tests/ 扁平 201 文件 | 按模块建子目录（`test_soul/`、`test_runtime/`…）逐步迁移 |
| 前端 | `web/desktop/assets/js/app.js`（5,132 行） | 继续按页面拆（handoff 已有先例） |
| 插件 CI | CI 只跑 Firefox 构建，未跑 `npm test` / typecheck | CI 增加 `npm run typecheck` + `npm test` |

---

## 8. 风险与对策

| 风险 | 对策 |
|---|---|
| A：路由接线时行为漂移 | 每批独立 PR + route audit + pytest；前端手动冒烟（/web、/m） |
| B：soul 收口暴露 obc_soul 与主项目隐式耦合 | 先 diff 收敛差异，全量测试后再删 src 副本；回滚条件明确（§3.1） |
| C：拆 database.py 改变事务/连接语义 | 机械搬迁不改行为；storage 相关测试全跑；每 Mixin 独立 PR |
| D：obc-discovery 反向依赖清理牵动 strategy 结构 | 先加协议/注入点再改实现；懒引用保持 lazy import 不扩大 |
| E：重构与功能开发并行冲突 | 阶段 0–4 每批 ≤1 天、高频合入；功能分支等阶段 0 完成后再开 |
| F：CI 与本地环境差异（如 web/clone 3GB 拖慢 checkout） | 阶段 1 移出 sites 后 CI checkout 体积大幅下降；在此之前 CI 加 paths-ignore |

---

## 9. 每阶段合入 main 前验收清单

- [ ] `pytest -q --continue-on-collection-errors`：0 错误；失败 0（或挂明确已知失败清单并逐步归零）
- [ ] `ruff check src/ tests/`：0；`mypy src/`：0（exclude 仅限 web/clone 临时项）
- [ ] 等价性验证：route audit 无丢失无重复；AST 依赖矩阵无新增反向依赖
- [ ] 新接入模块（如 `interview/`，v0.3.217）同样过闸：独立叶模块、无分层倒挂、路由走 `build_*_router` / `register_*_routes` 接线（非内联）、有测试与 `docs/modules/*.md`、`[config]` 段同步入 example——interview 已达标，剩余动作是**补齐提交**（changelog 条目 + 合入）
- [ ] grep 确认：无孤儿 register（全被调用）；`src/soul/` 全为 stub 或已删；packages 内无 `openbiliclaw.` 运行时 import
- [ ] 文档同步（AGENTS.md 强制）：`docs/modules/<模块>.md`、`docs/changelog.md`、`docs/architecture.md`（如架构变化）、`docs/index.md`
- [ ] CI（ci.yml）在 PR 上通过
- [ ] 未引入新行为：diff 中无逻辑改动混入

---

## 10. 工时与排期汇总

| 阶段 | 内容 | 预计 | 依赖 | 建议排期 |
|---|---|---|---|---|
| 0 | 止血：K1 + 测试环境 + 质量闸 + ci-check | 1 天 | 无 | 第 1 天 |
| 1 | 一份事实：K2/K3/K4/K11 | 1.5–2 天 | 0 | 第 2–3 天 |
| 2 | 拆巨文件：database/cli/refresh | 2–3 天 | 1 | 第 4–6 天 |
| 3 | 修分层：K5/K6/K7 | 1.5–2 天 | 2（部分并行） | 第 6–8 天 |
| 4 | 抽取收口：discovery 反向依赖 + runtime 决策 | 1–1.5 天 | 0、1 | 第 8–9 天 |
| 5 | 技术债 + 质量（持续） | 2–3 天/周 配额 | 可并行 | 第 2 周起持续 |

> 建议：阶段 0 完成后立即更新 [technical-debt.md](technical-debt.md)（同步 K1–K11 与 TD 状态），再进入阶段 1。
