# RED（exoticknight/red）借鉴分析

> 仓库：`exoticknight/red` · Apache-2.0 · 克隆：`_ref_red`（约 90 文件）
> 研究日期：2026-09-14 · 背景：为 OpenBiliClaw 寻找可复用的工程方法论与工具形态

---

## 1. 一句话定位

**给「AI 辅助工程中的项目知识」建立状态机的方法论 + 协议 + 工具链：把项目内容按 R（Research 未决调查）/ E（Evolve 推进中改动）/ D（Document 已接受共识）三态组织，并规定 AI 在每态里能做什么、推进到下一态必须经过谁的授权。**

它要解决的问题直击 AI 协作四大痛点（方法论长文 §一）：**AI 记不住项目根基、混淆讨论和决定、你难以检查它据以行动的理解、对话无法收口**。根源是：我们给了 AI 大量内容，却没给内容的**知识状态**——「先查一下 / 按这个方向试 / 这个定了」三种允许的行动级别被压平成了同样的文本。

## 2. 项目形态（工程完整度罕见地高）

| 层 | 内容 |
|---|---|
| 方法论 | `methodology/introducing-red.md`（194 行中文长文 + 英文/公众号版 + 多套 SVG 状态图） |
| 协议规范 | `spec/protocol.md`（normative）+ **三个 JSON Schema**：config-schema / artifact-schema / output-schema + exit-codes.md |
| CLI 双实现 | Node（733 行，npm）+ Python（810 行，PyPI），**共享同一份 conformance fixtures** |
| AI Skill | `plugins/red/skills/red/SKILL.md` + 7 个按需加载的 references |
| 自举 | 本仓库自己用 RED 维护（red.toml + CI 跑 `red check --json`）——"RED runs on RED" |

**R/E/D 三态核心语义**：
- **R**：未决问题、观测、假设、冲突。它的主张不是需求。得到可行方案 ≠ 获得实施方案的授权。
- **E**：提议/进行中的改动——理由、影响面、**验收条件**、备选方案、`based_on` 引用链。
- **D**：已接受的基线。**不要求新目录**——就是现有 README/docs/**，由 `red.toml` 的 `document.paths` 声明。代码/测试/运行结果是「实现证据」而非 D；**D 与证据冲突 → 显式记入 R，等有权者裁决**。

**两个检查点**：R→E、E→D 都需要明确的人类决定（或项目授权的决策源，如 issue 状态/PR review）。协议明文：「宽泛的早期请求不构成后面的转换授权」；CLI 的 `--accepted/--verified` 是**记录**权威而非**创造**权威。

## 3. 可迁移手法 × OpenBiliClaw 落点

| # | 手法 | 源码位置 | OpenBiliClaw 落点 |
|---|---|---|---|
| 1 | **知识状态显式化（R/E/D）**：同一份材料，状态不同→AI 允许的行动不同 | `spec/protocol.md` + 方法论 §二 | 你已有同构工作流：`docs/plans/`（方案待确认）≈E、`docs/modules`+AGENTS.md≈D、借鉴分析≈R。**不必引入 CLI**，把三态词汇写进 AGENTS.md 即可让每个会话的我自动分清「待查/推进中/已定」 |
| 2 | **冲突显式化**：文档与实现冲突 → 记入 Research 待裁决，而不是让文档静默腐烂 | `spec/protocol.md` 知识状态节 | AGENTS.md「文档强制同步」规则的最大漏洞是没人发现文档过期。可在文档规则里加一条：发现 docs 与实现不符 → 必须记录（如 changelog 或 drift_reports）而非默默绕过 |
| 3 | **检查点授权语义**：「给出方案」和「获准实施」是两件事；宽泛早期请求不构成授权 | `spec/protocol.md` Transitions 节 | 正是你「先出完整方案确认后再写代码」的协议化表述——可原句写进 AGENTS.md 约束所有 Agent |
| 4 | **Skill 路由化按需加载**：SKILL.md 只留路由表，references 是「查找目标不是递归阅读清单」，操作需要时才读对应文件 | `SKILL.md:20-34` | 写任何 SKILL.md 的范本：主文件极短 + 分路由 references，省 context 且不遗漏 |
| 5 | **双实现 + 共享 conformance fixtures**：`cases.json` 就是 args+exitCode 序列，Node/Python 跑同一组用例保证行为一致 | `cli/conformance/cases.json` + `run.py` | 任何「同一逻辑两种语言/两处实现」的一致性保障轻量方案（如扩展 vs 后端的同构逻辑） |
| 6 | **机器可读契约三件套**：config/artifact/output 各配 JSON Schema + 独立 exit-codes 文档 | `spec/*.json` | API 路由（如 oss-research）可补 response schema；「CLI 输出有 schema + 固定退出码」值得全线推广 |
| 7 | **TOML front matter Markdown 工件**（`+++` 分隔）：人可读 + 机器可校验，`id/state/status/title/created` 必填 | `spec/artifact-schema.json` | diary/interview 等由 md 迁移的结构化内容可用同款形态；比纯 JSON 适合长期手写维护 |
| 8 | **受管理指令区块**：`instructions install` 往 AGENTS.md 写带标记区块，只管理自己的块、保留其余内容 | `cli/python/src/red_cli/cli.py`（instructions 子命令） | 往 AGENTS.md/README 自动注入生成内容又不破坏手写部分的标准做法 |
| 9 | **policy 可配授权严格度**：`document_requires_approval` / `report_document_implementation_conflicts` 开关 | `red.toml:12-14` | 「协议留开关、项目自选严格度」——写项目规范时比一刀切更可持续 |

## 4. 不建议照搬

- **完整 CLI + red.toml 对 OpenBiliClaw 偏重**：单人 + 单主力 Agent 的规模下，R/E 记录的创建/晋升/校验收益低于成本。**取其神（三态区分 + 检查点纪律 + 冲突显式化），舍其形（双 CLI、conformance、自举 CI）**。
- **方法论长文可直接给 Agent 读**：194 行中文，密度高。若要吸收，摘 §二/§三 十几行进 AGENTS.md 即可，不必整篇挂载。

## 5. 与既有收录项目的关联

- 与 **brosis**（数据→AI 上下文）互补：brosis 解决「AI 看得到什么数据」，RED 解决「AI 如何理解这些数据的状态」。两者合起来正好是「MCP 只读服务（方案已写）+ R/E/D 工作流约定」的组合。
- 与你的 **MEMORY.md/工作日志体系**同源：日志≈R（Append-only 观测），MEMORY.md≈D（curated 共识）——你的记忆分层其实已经在实践 RED，缺的只是「E 态的显式承载」（目前 plans 文档兼职）。

## 6. 精读文件清单

1. `methodology/introducing-red.md` — 四大困境 + 三态设计论证（194 行，值得整篇读）
2. `spec/protocol.md` — normative 状态/工件/转换/持久化语义
3. `plugins/red/skills/red/SKILL.md` — 路由化 Skill 的极简范本
4. `red.toml` + `spec/config-schema.json` — 声明式项目配置形态
5. `cli/conformance/cases.json` — 跨实现一致性的轻量用例形态
