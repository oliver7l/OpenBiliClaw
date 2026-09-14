# 模块整理清单（2026-09-14）

> 目的：为「一个模块一个模块整理清楚」提供底账。
> 本文只回答两件事：**现在还有哪些模块没理清**、**每一项的证据是什么**。
> 不含实施方案——每个模块单独立方案后再动。

## 0. 方法论

所有结论来自**当天实测**，不引用二手描述：

| 手段 | 用途 |
|---|---|
| `pm2 jlist` 全量导出 vs `ecosystem.config.json` 解析 | 部署拓扑对账 |
| 精确正则对账 `api/*_routes.py` × 3 条注册路径 | 孤儿路由识别（**不能用子串匹配**：`probe_routes` 会误命中 `chat_probe_routes`） |
| `curl /openapi.json` | 现网真实路由面（441 路径 / 524 操作） |
| 遍历 `tests/**/*.py` 统计模块名提及 | 测试覆盖信号 |
| `git log -1 --format=%ct -- <path>` 对比代码与文档 | 文档漂移 |
| `.venv/bin/ruff` / `.venv/bin/mypy` | 质量门禁 |

**更正说明**：`docs/project-audit-2026-09-11.md` §4 的数字已过期（该文写 `cli/__init__.py` 8,795 行、`api/app.py` 6,933 行；实为 673 / 4,119 行——上帝文件拆分已完成），其「质量门禁 ruff 279 项 / mypy 73 项」亦已下降至 3 / 55。引用该文时须以此处为准。

---

## 1. 全局规模

`src/openbiliclaw/` 下 **35 个顶层包 / 约 14.4 万行**，另有 3 个已抽取的独立包（`packages/`：`obc_llm`、`obc_soul`、`obc_discovery`）。

体量前 10：

| 模块 | 行数 | 文件 | 模块文档 | 测试提及 |
|---|---|---|---|---|
| `api` | 24,054 | 37 | ❌ 无（仅有 `api-auth.md`） | 1,235 |
| `runtime` | 20,338 | 53 | ✅ `runtime.md` | 344 |
| `diary` | 11,987 | 15 | ✅ | 36 |
| `sources` | 11,263 | 56 | ❌ 无 | 655 |
| `storage` | 11,173 | 35 | ✅ | 146 |
| `self_evolution` | 9,284 | 15 | ❌ 无 | **1** |
| `cli` | 7,478 | 12 | ✅ | 254 |
| `eval` | 7,193 | 24 | ❌ 无 | 44 |
| `recommendation` | 6,419 | 9 | ✅ | 126 |
| `knowledge_forge` | 6,230 | 21 | ✅ | 47 |

---

## 2. 六类「没理清」

### A. 部署拓扑漂移（🔴 最确凿，且是「已有结论未执行」）

| 项 | 实测 |
|---|---|
| `ecosystem.config.json` 声明 | **9 个** app |
| `pm2` 实际运行（openbiliclaw/pool 系） | **21 个** |

**14 个进程不在任何版本控制文件里**：

`bili-favorites`、`douyin-favorites`、`douyin-feed`、`douyin-likes`、`hupu-bxj`、`hupu-feed`、
`inbox-merger`、`toutiao-feed`、`v2ex-rss`、`x-favorites`、`xhs-favorites`、
`xiaoyuzhou-favorites`、`zhihu-favorites`、**`pool-feed-api`**

另有 2 个「声明了但没跑」：`openbiliclaw-v2ex-archive`、`openbiliclaw-v2ex-cli`。

**风险**：换机 / PM2 重装 / `pm2 resurrect` / 按 ecosystem 重部署后，这些采集进程**不会被恢复**，`pool-feed-api` 一挂，桌面端 6 个 feed 标签（`web/desktop/assets/js/app.js:39` 硬编码 `http://127.0.0.1:8421/api/pool/feed`）**直接不可用**。

**取证**：`docs/project-audit-2026-09-11.md:80-89` 已记录此漂移（当时 9 vs 22），修复项写的是「从 `pm2 jlist` 导出真实配置，回写 `ecosystem.config.json`」——**至今未执行**。

### B. 路由注册入口不唯一（3 处）——「孤儿端点」的结构性根因

| 注册点 | 内容 |
|---|---|
| `api/_route_registry.py` | `register_all_routes()`，30+ 模块显式白名单 |
| `api/app.py` | 15 个内联 `@app.*` 装饰器 + `register_web_ui_routes` |
| `cli/_build.py:187` | **仅此一处**注册 `chat_analysis_routes` |

**具体缺陷**：`chat_analysis_routes` 只在 CLI 启动路径（`openbiliclaw serve-api` → `_run_api_server`）注册。现网走 CLI 所以 15 个 `/api/chat-analysis/*` 端点正常；但**任何裸 `create_app()`（测试、嵌入式、改用 uvicorn 直启）都会缺这 15 个端点，且无任何告警**。

这与刚修完的 `POST /api/delight/sent` 404（`8a9dffb6`）是**同一族缺陷**：模块存在、能力存在，但注册路径分叉 → 端点静默消失。

**非缺陷**：`api/_interview_routes.py`（11 行）是期 3 有意留的兼容垫片，不接线是对的。

### C. 文档缺口：14 个模块有代码、无 `docs/modules/*.md`

按体量：`api`(24,054)、`sources`(11,263)、`self_evolution`(9,284)、`eval`(7,193)、
`saved_sync`(1,107)、`scripts`(1,029)、`synthesis`(855)、`topics`(765)、`travel`(465)、
`rag`(400)、`agent`(240)、`core`(204)、`reading`(197)、`web`(静态)

**另有 6 个文档明显滞后**（按最后提交日期）：

| 模块 | 代码最后提交 | 文档最后提交 | 落后 |
|---|---|---|---|
| `integrations` | 2026-09-14 | 2026-06-26 | 79 天 |
| `memory` | 2026-09-14 | 2026-06-26 | 79 天 |
| `youtube` | 2026-09-14 | 2026-06-26 | 79 天 |
| `extension` | 2026-09-13 | 2026-06-26 | 78 天 |
| `bilibili` | 2026-09-08 | 2026-06-26 | 74 天 |
| `recommendation` | 2026-09-14 | 2026-09-07 | 7 天 |

### D. 测试缺口：4 个模块在 `tests/` 中零提及

| 模块 | 行数 | 说明 |
|---|---|---|
| `chat_analysis` | 2,355 | 且只在 CLI 路径注册（见 B），双重风险 |
| `saved_sync` | 1,107 | 09-11 审计删过它引用不存在类的适配器，属历史高发区 |
| `clone` | 931 | |
| `travel` | 465 | 新疆行程 API |

**近乎空白**：`self_evolution`（9,284 行 / 提及 **1** 次）、`conversation_archive`(3)、`weekend`(6)、`synthesis`(6)。

### E. 质量门禁：mypy 是唯一未绿项

| 检查 | 当前 | 09-11 时 |
|---|---|---|
| `ruff check src packages` | **3 errors** | 279 |
| `mypy src/openbiliclaw` | **55 errors / 34 files** | 73 / 40 |

`ruff` 已近清零；`mypy` 仍差 55 项。09-13 那次审计**未复测 mypy**，故这项一直没人收口。

### F. 抽取式重构的收尾状态（数字已复核，与审计文档不同）

- `src/openbiliclaw/llm/` 现已是**薄转发层**（19 文件 / 600 行，其中 14 个模块 ≤20 行，转发到 `packages/obc_llm`）。
- 仍走旧 import 路径（`openbiliclaw.llm|soul|discovery`）：**21 处**（审计文档写 224 处，已大幅收敛）。
- `obc_runtime` **未创建**（`docs/module-extraction-plan.md` §5 计划项）。**已判定：关闭，不再抽取**（2026-09-14，依据见下）。

#### obc-runtime 抽取的评估结论（实测数据）

| 维度 | 实测 | 含义 |
|---|---|---|
| 前 3 阶段 | `obc-llm` 7,844 行 ／ `obc-soul` 13,883 行 ／ `obc-discovery` 8,610 行 —— **均已提取** | 只剩最后一块，不是新战线 |
| 连带迁移 | `runtime/` **已切到新包**：`from obc_discovery` ×11、`obc_soul` ×6、`obc_llm` ×5 | 跨界适配层工作**已完成大部分** |
| 循环依赖 | 已解：`obc_discovery` 用 `ImageCache` Protocol + `set_image_cache()` 注入，不反向依赖 runtime | 方案风险 1 有现成解法 |
| 剩余解耦面 | **75 处越界 import**：`sources` 35 ／ `config` 19 ／ `storage` 6 ／ `self_evolution` 5 ／ `weekend` 4 ／ `recommendation` 4 ／ `llm` 1 ／ `bilibili` 1 | 方案 §5.3 只规划了 5 类，**实测多出 4 类未预见**（self_evolution/weekend/recommendation/bilibili）→ 方案不完整 |
| 解耦对象规模 | 最大的 `sources/` = 56 文件 / 11,263 行，方案只给一句「定义 `SourceAdapter` Protocol」 | 单这一项就是大工程 |
| 爆炸半径 | **20 个 pm2 进程**（`api` + `inbox-merger` + 全系 producer/favorites）都跑在 runtime 上 | 抽错 = 全量采集停摆 |
| 测试保护 | 45 个测试文件提及 runtime；`tests/feed_producers/` 整目录 + 各 producer + `source_policy` + `init_prereqs/coordinator` | 覆盖较厚，但涉及 20 进程的运行时行为难以在单测中完全覆盖 |
| **复用价值** | runtime = 刷新控制器 + 生产者调度，依赖 `sources/self_evolution/weekend/recommendation`，**全是本项目业务概念** | **四个模块里唯一几乎不可能被第三方复用的** |

**结论**：`obc_runtime` 是四个模块中**成本最高（解耦面最大、方案未覆盖、20 进程爆炸半径）、收益最低（无复用价值）**的一个。方案自身也把它排在「第四 —— 依赖前三者，且与主项目耦合最深」，即排序本就意味着最低优先级。**决定不做**。

代价（明确放弃什么）：若将来要把本项目开源为可复用 SDK，runtime 无法作为独立包发布——但该项目定位是**个人应用**，runtime 作为其调度核心本就不该被外部复用，故无实际损失。

---

## 3. 已收口（不必再做，避免重复劳动）

| 项 | 状态 |
|---|---|
| 上帝文件 `cli/__init__.py`（8,795 行） | ✅ 已拆，现 673 行 |
| 39 对重复路由 | ✅ 已收敛至 0 对 |
| `data/` 孤儿目录 `v2ex-hot-hub`、`tax_frames*` | ✅ 已清 |
| 仓库根游离 `.db`、`config.toml.bak`、`.DS_Store` | ✅ 已清（本会话） |
| 面试模块 期 0–期 4 + 3 个路由契约修复 | ✅ 已完 |
| `_delight_routes.py` 孤儿模块 / `/api/delight/sent` 404 | ✅ 已修（`8a9dffb6`） |

---

## 4. 建议的整理顺序

| 批次 | 模块 / 主题 | 风险 | 为什么排这里 |
|---|---|---|---|
| **①** | **部署拓扑**：重建 `ecosystem.config.json` + 加一致性检查 | 低 | 结论已有、证据确凿、改动纯声明文件，一轮可收口 |
| **②** | **路由注册单一化**：`chat_analysis` 归位 + 「注册表 ⟺ 所有 `*_routes.py`」一致性测试 | 低 | 与刚修完的重构线同源，可根治「孤儿端点」这一族 |
| **③** | **质量门禁**：`mypy` 55 → 0、`ruff` 3 → 0 | 低 | 纯机械修复，且是 AGENTS.md 要求的门禁 |
| **④** | **`api/` 总览文档**：37 文件 / 441 路径的归属表 | 低 | 最大模块、最缺文档；后续所有路由改动都靠它 |
| **⑤** | **`sources/` + `runtime/` 生产者矩阵**：20 个进程 ↔ 脚本 ↔ 库 ↔ 频率 | 低 | 31.6K 行 / 109 文件 / 零文档，是「黑盒」最大的一块 |
| **⑥** | 其余缺文档模块（`self_evolution` / `eval` / `saved_sync` …） | 低 | 补课 |
| **⑦** | 零测试模块补测（`chat_analysis` / `saved_sync` / `clone` / `travel`） | 中 | 需先有行为契约才好写断言 |
| **⑧** | ~~判定 `obc_runtime` 抽取：待办 or 过期~~ → **已判定：关闭**（见 §2.F） | — | 已收口，不占后续批次；结论与依据已写入 §2.F 与 `module-extraction-plan.md` 顶部批注 |

---

## 5. 待你拍板

1. 从哪一批开始？（建议 ①，最低风险且已有结论）
2. ~~`obc_runtime` 抽取：继续做，还是判定为「计划已过期」正式关闭？~~ → **已决策：正式关闭**（2026-09-14，依据见 §2.F）
3. `mypy` 55 项：一次性收口，还是按模块随修随清？
