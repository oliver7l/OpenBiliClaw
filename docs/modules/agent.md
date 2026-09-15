# Agent 编排与技能系统（agent）—— ⚠️ 不可达模块（待处置）

> **结论先说**：`src/openbiliclaw/agent/` 240 行、3 个文件，**全项目零消费者**，所有方法体
> 都是 `# TODO` 桩。它既不在 `docs/architecture-map.md` 的分层图里，也没有 CLI / 路由 / 运行时挂载点。
> 本文是取证记录，不是使用说明——目的是让下一个人不必再查一遍，并给出处置选项。

## 取证（2026-09-15）

| 判据 | 实测结果 |
|---|---|
| 文件 | `__init__.py`（1 行）、`skill.py`（116）、`orchestrator.py`（123） |
| 引用计数 | `AgentOrchestrator` / `SkillRegistry` / `openbiliclaw.agent` 在 `src/` `scripts/` `tests/` 中命中 **0** 次（含字符串形式的动态导入） |
| 测试 | 0 |
| 模块文档 | 无（`docs/architecture-map.md` 完全不提 agent） |
| 代码新鲜度 | 最后一次改动是 `1aeed497`（mypy 收口），上一次是 `e68369b7`（import 迁移）——**都是被动扫过，不是功能开发** |
| 命中「agent」字样的邻近物 | CLI 里的 `agent-browser`（浏览器自动化，**与本包无关**） |

## 包内到底有什么

### `orchestrator.py` —— `AgentOrchestrator`

| 方法 | 实际行为 |
|---|---|
| `__init__(config)` | 存 config、初始化 3 个空字典/None 字段 |
| `initialize()` | 只打日志 + 置 `_initialized = True`（**7 行 TODO，无任何真实初始化**） |
| `register_skill(skill)` | 真的写进 `self._skills` 字典，重名时 warning |
| `get_skill(name)` / `available_skills` | 真的能查/列出 |
| `run_discovery_cycle()` | 未初始化时 `raise RuntimeError`，否则打日志（**无实现**） |
| `process_feedback(feedback)` | 只打日志（**无实现**） |
| `chat(message)` | 打日志后**返回硬编码字符串** `"（对话功能开发中...）"` |
| `shutdown()` | 打日志 + 复位标志 |

即：只有一个「技能字典」是真的，其余是脚手架。

### `skill.py` —— `SkillMetadata` / `Skill` / `SkillRegistry`

`SkillMetadata` 与 `Skill`（`metadata` / `name` / `execute` / `describe`）本身是**完整可用**的抽象；
`SkillRegistry` 的 `register/get/all_skills/describe_all` 也都能用，`discover_skills(dir)` 会 glob
`*/SKILL.md`。

⚠️ 但要注意：**它不是本机 WorkBuddy 的 skill 体系**（那套在 `~/.workbuddy/skills/`，由宿主运行时加载），
两者除了「SKILL.md」这个名字没有任何关系。混为一谈会得出「技能系统已经有人用」的错误结论。

## 为什么它会留在这里

`orchestrator.py` 的 docstring 描述的是**早期设计愿景**：「协调 Soul Engine、Discovery Engine、
Recommendation Engine 的大脑」。项目后来选择了**反向结构**——三大引擎各自成包
（`obc_soul` / `obc_discovery` / `openbiliclaw.recommendation`），由 `runtime/` 的循环调度 +
`event_hub` 事件驱动，中间不需要一个「大脑」。于是这个中枢类的职责被架空了。

## 处置选项（需要拍板）

| 选项 | 代价 | 收益 | 风险 |
|---|---|---|---|
| **A. 删除整个包**（推荐） | 删 3 个文件、无引用需改 | 分层图少一个假模块；`SkillRegistry` 那点真代码若要用，20 行重写即可 | 极低（零消费者）；若将来真要做技能编排，从 WorkBuddy skills 协议起步更合理 |
| **B. 保留但标死** | 继续被 mypy/ruff 扫 | 保留意向 | 下一个人依然会花时间查「这东西谁在用」 |
| **C. 补实现并接线** | 大 | — | 与现架构（引擎 + runtime 事件）重复造中枢，方向可疑 |

> 我倾向 **A**：它在架构图里不存在、没有挂载点、没有任何调用方，属于「早期脚手架的残留」。
> 删除是一个 commit 的事，且随时可以从 git 历史取回。**未执行，等拍板。**

## 相关

- 同类「零引用/近零测试」小模块盘点：`docs/module-inventory-2026-09-15.md` §1（`agent` `core` `topics` `synthesis`）
- 分层契约（哪些依赖方向是被测试守住的）：`tests/test_layering_contracts.py`、`docs/modules/core.md`
