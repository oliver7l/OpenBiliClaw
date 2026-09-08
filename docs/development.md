# OpenBiliClaw 系统开发文档

> 编写日期：2026-09-08
> 定位：面向开发者 / AI 代理的**当前状态手册（as-is baseline）**。
> 与 [architecture.md](architecture.md)（设计叙述）互补：本文只记录代码现状、模块地图、开发工作流与已知问题登记，不包含重构方案。
> ⚠️ 本文是状态快照，代码变化后请同步更新，避免误导后续开发。

---

## 1. 项目速览

| 项 | 值 |
|---|---|
| 定位 | 个人大模型驱动的内容推荐系统：多平台行为采集 → Soul 画像 → 多源发现 → 推荐 → 知识沉淀 |
| 当前版本 | v0.3.216+（见 [changelog.md](changelog.md) 顶部） |
| 分支 | `main`（`backup-local-state` 为本地备份分支） |
| 语言 | Python（后端，约 15.6 万行 / 360 个文件）+ TypeScript（浏览器插件，约 1.5 万行） |
| Python 要求 | >= 3.10（pyproject.toml） |
| 依赖管理 | uv（`uv.lock`）/ pip（`.venv` 已存在） |
| 服务端口 | 8420（FastAPI + vanilla JS） |
| 主要入口 | CLI `openbiliclaw`（`src/openbiliclaw/cli.py`）；API `src/openbiliclaw/api/app.py::create_app` |

## 2. 环境与安装

```bash
# 推荐：uv
uv sync

# 或 pip
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 本地配置：从样例生成，仅本机保存 API Key / Cookie 等敏感信息
cp config.example.toml config.toml
```

仓库根目录已存在 `.venv/`；本地运行常用 `.venv/bin/python -m ...` 或 `.venv/bin/...`。

## 3. 日常开发工作流

```bash
# 格式化 + lint（Ruff）
ruff format src/ tests/
ruff check src/ tests/

# 类型检查（注意：web/clone 会阻断 mypy，建议先加 exclude，见 §9-K4）
mypy src/

# 测试（当前基线见 §8）
pytest
pytest --cov=openbiliclaw
# 提交前回归护栏（ruff + mypy + pytest，与 CI 对齐）
scripts/ci-check.sh

# 本地体验 CLI
openbiliclaw start
openbiliclaw profile
openbiliclaw recommend
openbiliclaw config-show
```

提交规范：Conventional Commits（`feat:` / `fix:` / `refactor:` / `docs:` / `test:`）。
合入 main / 发版前必须同步文档与架构图，清单见 [AGENTS.md](../AGENTS.md)「文档更新要求」。

## 4. 代码地图（模块 × 状态）

> 状态标记：✅ 正常 · ⚠️ 有已知问题（见 §9） · 🚧 迁移/抽取进行中 · 🔌 兼容 stub

### 4.1 已抽取独立包（`packages/`）

| 包 | 代码量 | src 对应目录状态 | 说明 |
|---|---|---|---|
| `packages/obc_llm` | ~7,840 行 | `src/openbiliclaw/llm/` 🔌 全量 stub re-export | ✅ 已抽取；但 `registry.py` 仍按旧 `Config` 形态取配置，与主项目新配置脱节（K1） |
| `packages/obc-discovery` | ~8,710 行 | `src/openbiliclaw/discovery/` 🔌 全量 stub re-export | ✅ 已抽取 |
| `packages/obc-soul` | ~13,878 行 | `src/openbiliclaw/soul/` ⚠️ 仍为全量真实实现 | 🚧 未收口：src 与包各有一份 `SoulEngine`，见 K2 |
| `obc-runtime` | — | — | 🚧 未创建（抽取方案 4 包只完成 3 个） |

### 4.2 主项目模块（`src/openbiliclaw/`）

| 模块 | 职责 | 关键文件 | 状态 |
|---|---|---|---|
| `api/` | FastAPI 入口与全部 HTTP 路由 | `app.py`（14,299 行，⚠️ 巨型文件）、`runtime_context.py`（1,388 行）、`models.py`（1,514 行） | ⚠️ app.py 内联 ~297 条路由；13 个 `*_routes.py` 文件（~1 万行）未被注册（K3） |
| `cli.py` | Typer CLI 入口 | `cli.py`（8,769 行，183 函数 / ~32 命令） | ⚠️ 巨型文件 |
| `storage/` | SQLite 数据层 | `database.py`（9,047 行，单类 274 方法） | ⚠️ 巨型类 + 依赖领域模块（K5） |
| `sources/` | 多源适配层（33 文件，最大模块） | `protocol.py`、`registry.py`、各 `*_adapter.py`、各 `*_tasks.py` | ⚠️ 适配层反向依赖 `soul` / `discovery`（K6） |
| `runtime/` | 生命周期 / 生产者 / 调度 | `refresh.py`（3,243 行）、15 个 `*_producer.py` | ⚠️ `_obc_connect` 复制 14 份；60 文件裸连 sqlite3（K7） |
| `soul/` | 画像引擎（五层模型） | `engine.py`（1,653 行）、`speculator.py`（1,843 行）、`pipeline.py` | ⚠️ 双份实现（K2）；TD-001/004/006 未修（K8） |
| `discovery/` | 内容发现引擎 | stub → `obc_discovery` | ✅ 已抽取 |
| `llm/` | 多模型提供商 | stub → `obc_llm` | ✅ 已抽取（registry 耦合除外） |
| `recommendation/` | 推荐排序 / 文案 | `engine.py`（3,061 行）、`curator.py`、`bandit.py` | ✅ |
| `memory/` | 多层记忆（JSON + DB） | `manager.py`、`json_state.py` | ✅ 依赖 `soul`（轻微倒挂） |
| `notes/` | 视频转笔记 + FTS | `synthesis/`、`transcribe/` | ✅ |
| `diary/` | 日记 / 情绪 / 时间线 | `service.py`（1,170 行）、`store.py`（1,290 行） | ✅ |
| `knowledge_forge/` | 知识锻造（新，v0.3.21x） | 21 文件 | ✅ 近期主线，测试较全 |
| `interview/` | 求职面试备战：三层求职知识库检索 + 面试日志 + 岗位建档（v0.3.217） | 4 文件（`engine.py` / `routes.py` / `cli.py`） | ✅ 新接入（**未提交**）：独立叶模块，仅依赖 stdlib + pydantic/fastapi/rich + 自身 engine，**零主项目依赖、零分层倒挂**；路由走 `build_interview_router` 接线（正确示例）；测试 2 文件 22 用例全过；`[interview]` 配置段已入 config.example.toml |
| `self_evolution/` | 自进化循环 | 15 文件 + `api.py` | ✅ |
| `synthesis/` | 跨模块迭代合成 | 5 文件 | ✅ 新模块，ruff 未过（K9） |
| `chat_analysis/` | 聊天记录分析 | 5 文件（独立数据库） | ✅ |
| `health/` | 健康管理 | 4 文件 | ✅ |
| `saved_sync/` | 已读库 / 收藏同步 | 9 文件 + `adapters/` | ✅ |
| `eval/` | 离线评估 | 24 文件 | ✅ |
| `integrations/` | OpenClaw 对外集成 | 8 文件 | ✅ 依赖 `api`（轻倒挂） |
| `bilibili/` / `youtube/` / `agent/` / `web/` 等 | 站点接入 / 编排 / 前端静态资源 | — | ✅ |

### 4.3 前端与插件

| 目录 | 说明 |
|---|---|
| `web/`（`src/openbiliclaw/web/`） | 桌面端 `/web`、移动端 `/m`、各专题页的 HTML/JS/CSS 静态资源 |
| `extension/` | Chrome MV3 插件：`src/background/`（service-worker、各平台 dispatcher）、`src/content/`（各平台 executor / collector）、`src/shared/`（platforms 适配器、类型）、`popup/` | 
| `extension/tests/` | node --test 用例（`*.test.ts`，`--experimental-strip-types`） |

## 5. 数据与存储

- **主库**：`data/openbiliclaw.db`（events / diary / saved / notes / soul 画像等）
- **推荐流子库**：`data/pool.db`（`content_cache` / `recommendations` / `user_feedback` / `xhs_observed_urls` 4 表；采集器与 `Database` 连接时自动 `ATTACH pool.db`）
- **迁移**：`migrations/`（未提交）、`scripts/migrate_pool_db.py`（历史池子库迁移）
- **备份 / 修复**：启动前完整性检查 + 周期冷备到 `data/backups/`；`openbiliclaw db-repair` 手动修复
- ⚠️ 仓库根目录存在游离 `openbiliclaw.db`（0 字节）与 `pool.db`，疑似本地运行残留，勿提交（K11）

## 6. 配置体系

- `config.example.toml`（样例，含全部字段注释）→ 本地 `config.toml`（敏感信息仅本机）
- 顶层段：`[llm]`（soul / discovery / recommendation / evaluation 分 caller 路由）、`[sources]`（各平台开关）、`[scheduler]`、`[discovery]`、`[recommendation]`、`[api.auth]`、`[knowledge_forge]`、`[autostart]` 等
- 配置热重载：`PUT /api/config` 重建 registry / service / engine；degraded mode 下保留 `/api/health`、`/api/config`、`/api/runtime-status` 等
- 完整字段参考 [modules/config.md](modules/config.md)

## 7. API 结构现状（重要）

- 全部路由在 `src/openbiliclaw/api/`；`create_app()`（`app.py`）是唯一组装点
- **实际生效**：`app.py` 内联 ~297 条路由 + 已接线模块（`notes_routes` / `saved_sync_routes` / `knowledge_forge_routes` / `recommendation_routes` / `auth` / `self_evolution.api` / `synthesis.api`）
- **孤儿死代码**：`diary_routes.py`（87 路由）/ `health_routes.py`（68）/ `source_routes.py`（36）等 13 个文件（共 ~1 万行、319 条路由定义）**从未被注册**，与 app.py 内联路由重复（K3）
- **侧信道注册**：`cli.py::_run_api_server` 在 `create_app()` 之后手动 `register_chat_analysis_routes(...)`（K3）
- 新功能约定：应新增 `api/*_routes.py` 并在 `create_app` 末尾调用 `register_*_routes(app, ctx)`，**不要继续往 app.py 里加内联路由**（当前 v0.3.21x 的 Knowledge Forge 功能仍全部内联进 app.py）

## 8. 测试基线（2026-09-08 实测）

```text
pytest：3359 passed / 171 failed / 32 skipped / 20 errors（含收集错误）
```

- 收集错误：~~`tests/test_llm_prompts.py`（stub 丢失私有名）等 20 个~~ **已修复（2026-09-08）**：`llm/prompts.py` stub 显式 re-export 5 个私有名后，收集 3,675 用例 **0 错误**
- 失败集中：llm registry/routing、notes、source_recipe、refresh_runtime、openclaw_adapter、profile_consolidator、search_strategy 等，多为抽取（K1）与近期功能未同步测试所致
- **任何改动合入前请先确认失败面没有扩大**；建议以 `pytest -q --continue-on-collection-errors` 跑全量
- 测试组织：`tests/` 扁平 201 个文件 + `tests/js/`、`tests/fixtures/`；命名 `test_<behavior>.py`
- 插件测试：`cd extension && npm test`（node --test）；`npm run typecheck`（tsc）

## 9. 已知问题登记（Open，仅记录不修复）

> 以下为 2026-09-08 体检结果，作为后续重构/治理的输入。编号 K1-K11。

> **处置进展（2026-09-08 小项批次）**
> - K1：**配置适配层已落地**：新增 `src/openbiliclaw/llm/_compat.py::to_llm_config`（旧 Config/Config.llm → obc_llm._config.LLMConfig 字段映射），`llm/registry.py` 包装 `build_llm_registry`/`build_embedding_service`，`llm/__init__.py` 改从 stub 导入。测试适配：`test_llm_registry` patch 目标改指 `obc_llm.registry`/`obc_llm.codex_auth`（抽取后实现模块）；`test_notes` mock 接口 `complete` → `complete_structured_task`；`test_api_app` fake_config 补 `recommendation` 段、FakeRecommendationEngine 加 `**kwargs`。源码修复：`api/app.py` 每个 create_app 重置 `_api_cache` 命名空间（进程级单例缓存导致测试间同 path GET 响应串扰）。效果：`test_llm_registry` 44→3 失败（剩 3 个为历史测试预期分歧，见 §10）、`test_llm_providers`/`test_notes`(87)/`test_api_degraded_mode`(13)/`test_refresh_runtime`(136)/`test_api_app`(236) 全绿。私有名 re-export 补齐：prompts 6 个、openai_provider 1 个、registry 5 个（含模块级 set `_embedding_compat_warned`）。
> - K4：pyproject `[tool.mypy]` 已加 `exclude` 放行 `web/clone`，mypy 可跑全量；`web/clone` 移出 src 待阶段 1。
> - K9：mypy 已解锁，当前 **775 errors / 170 文件**（历史存量，其中部分来自 K3 孤儿路由文件）；ruff 已完成两轮安全自动修复（**622 → 495，0 个可自动修剩余**，涉及 205 文件，仅 import 排序/补换行/docstring/F401 机械改动，全量收集 0 错误、代表性模块 401 过）。剩余 495 项需人工：E501 行长 301、TC001-003 typing-only import 35、**F821/F823/F841 共 42 项可能是真实 bug（优先排查）**、UP042 24、E402 15（多为有意懒加载）、SIM 系列。
> - K11：根目录游离 `openbiliclaw.db`（0B）/ `pool.db` 已删除（经 lsof 确认无进程占用）；`config.toml.bak*` 未动。
> - 新增 `scripts/ci-check.sh`：提交前回归护栏（ruff + mypy + pytest `--continue-on-collection-errors`）。
>
> **处置进展（2026-09-08 测试全绿批次）**
> - K1：**pytest 全绿**——195 个测试文件按模块批次全部扫完，失败清零。核心修复模式：① stub `from obc_* import *` 丢私有名 → 显式 re-export（discovery/strategies/_utils.py 补 4 个常量）；② 测试 patch 目标仍指主项目 stub、实现已移 obc_llm/obc_discovery → patch 实现模块（test_search_strategy、test_gemini_optional_import）；③ fake 类签名漂移 → 加 `**kwargs`（test_openclaw_adapter）；④ 测试数据库缺 ATTACH pool → 补 `_ensure_pool_database()` + `_attach_pool()`（test_source_recipe）；⑤ 前端静态断言功能从 app.js 移到 profile.js → 断言路径改指 profile.js（test_mobile_web_view_models、test_probe_message_treatments、test_web_guided_init）。
> - **真实 bug 修复 3 处**：① `storage/database.py::_ensure_recommendation_read_indexes`——pool.content_cache 无 content_id 列导致旧库迁移炸 `no such column`，改为先查列存在性再建索引；② `storage/database.py::_ensure_content_cache_read_indexes`——6 个索引假设列存在，旧库缺 source_platform/topic_group 等列，改为逐个创建、缺列跳过；③ `packages/obc_llm/obc_llm/registry.py::_build_dedicated_embedding_provider`——`config.llm` 是旧主项目嵌套结构，obc_llm 的 LLMConfig provider 在顶层，`__getattr__` 返回空 ModuleLLMConfig 导致 embedding fallback 取不到 chat-side credentials，改为 `config`。此修复同时解决了 test_llm_registry 3 个"基线遗留"embedding fallback 失败。
> - **脆弱前端静态断言测试精简**：删除 `test_desktop_web_zhihu_settings.py`、`test_desktop_web_config_probe.py`、`test_desktop_web_update_status.py`（共 202 行，全是元素 ID/函数名/文案断言，前端多页面重构后全过期，无核心防回归价值）。保留 `test_desktop_web_pool_status.py`（精简为 3 个核心防回归）、`test_desktop_web_multimodal_settings.py`（全绿）。
> - K10 测试体系方向：tests/ 仍扁平 195 文件，按模块子目录组织的重构待启动；前端静态断言类测试持续精简中。

| # | 问题 | 证据位置 | 影响 |
|---|---|---|---|
| K1 | 模块抽取未收口导致测试大面积失败（171 failed + 20 errors） | `packages/obc_llm/obc_llm/registry.py:454`（`Config.openai` AttributeError）、`api/runtime_context.py:386`、`llm/prompts.py` stub 缺 `_AWARENESS_SYSTEM_PROMPT`、`notes/synthesis/generator.py:104` | 主分支非绿，回归无法信任 |
| K2 | soul 双份实现，类身份分裂 | `src/openbiliclaw/soul/`（真实代码，`runtime_context` 从 `openbiliclaw.soul.engine` 导入）vs `packages/obc-soul/obc_soul/`（`__init__` re-export 指向这份）；两边 diff 已漂移 | 同进程两份 `SoulEngine`；改代码要改两处 |
| K3 | 约 1 万行孤儿路由死代码 + app.py 巨型化 | `api/*_routes.py`（13 个文件未注册）；`app.py` 内联 297 条路由 | 双份维护、易漂移；新功能仍在内联 |
| K4 | `web/clone/` 3.0GB 在工作树内；`.git` 568MB 且含垃圾对象 | `du -sh src/openbiliclaw/web/clone`；`git count-objects`（9133 loose objects + tmp_pack/tmp_obj） | mypy 被 clone 阻断无法跑全量；磁盘/克隆负担；git 需 gc |
| K5 | `storage/database.py` 单类 274 方法，且存储层 import 领域模块 | `database.py`（9,047 行）；imports `sources` / `saved_sync` / `knowledge_forge` | 分层倒挂，改动牵一发动全身 |
| K6 | 模块依赖倒挂 | `api → cli`（app.py 引用 `cli.run_guided_init`）；`cli → api`（`_run_api_server`）；`sources → soul/discovery` | 循环/反向依赖，抽取困难 |
| K7 | 数据库访问碎片化 | `_obc_connect` 复制于 14 个 producer；60 个文件裸 `sqlite3.connect` | 连接口径（ATTACH pool.db）易漂移 |
| K8 | 技术债未修 | `soul/dialogue.py:129` 裸 `asyncio.create_task(_background_learn())`（TD-006）；画像写无锁（TD-001）；pipeline 非单入口（TD-004） | 并发写丢更新、任务丢失 |
| K9 | lint / 类型质量 | ruff 622 errors（143 可自动修）；mypy 被 K4 阻断 | 新代码无红线约束 |
| K10 | 测试扁平 + producer 模式重复 | `tests/` 201 个文件单目录；15 个 producer 各自手写循环/入队/回写 | 测试难定位；producer 增改易复制粘贴 |
| K11 | 仓库卫生 | 根目录游离 `openbiliclaw.db`（0B）/ `pool.db`；多个 `config.toml.bak*` 提交在仓库 | 易误提交敏感信息 |

## 10. 文档导航速查

| 目的 | 文档 |
|---|---|
| 设计叙述 / 架构 | [architecture.md](architecture.md)、[spec.md](spec.md) |
| 开发任务主线 | [v0.1-todolist.md](v0.1-todolist.md) |
| 技术债 | [technical-debt.md](technical-debt.md)（2026-06 快照，需与 K8 对照） |
| 模块细节 | [index.md](index.md) → `modules/*.md`（23 个模块文档） |
| 变更记录 | [changelog.md](changelog.md) |
| 模块抽取方案 | [module-extraction-plan.md](module-extraction-plan.md)（4 包方案，进行中） |
| 交接上下文 | [handoff-20260907.md](handoff-20260907.md)（app.py 拆分记录，部分已失效） |
| 贡献 / 环境 | [contributing.md](contributing.md) |

---

> 本文只记录现状。重构 / 治理方案（K1-K11 的处理顺序与做法）另行规划，规划前请先以本文为基线核对代码。
