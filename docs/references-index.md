# 参考项目索引

> 本目录列出的项目为 OpenBiliClaw 开发过程中参考、学习或借鉴的第三方开源项目。
> 代码不在版本控制内，仅保留本地副本供离线查阅。
> 各项目版权归原作者所有，许可证见各项目内 LICENSE 文件。

---

## 参考项目清单

### 记忆系统

| 项目 | 目录 | 说明 |
|---|---|---|
| **LycheeMem** | `references/LycheeMem/` | 轻量级 LLM Agent 长期记忆系统，Apache 2.0 许可 |
| **agent-memory-architecture** | `references/agent-memory-architecture/` | 扁平文件 + 混合搜索的 Agent 记忆架构 |
| **deep-memory** | `references/deep-memory/` | 自进化知识积累与混合检索系统，含 ChromaDB + BGE-Reranker |
| **sleep-memory** | `references/sleep-memory/` | 睡眠记忆系统，模拟人类睡眠进行记忆巩固与整合 |
| **reor** | `references/reor/` | AI 驱动的笔记工具，本地 LLM 与语义搜索 |
| **memos** | `references/memos/` | 开源轻量级笔记中心，支持 Markdown 与社交协作 |

### 日记系统

| 项目 | 目录 | 说明 |
|---|---|---|
| **MyOwnDiaryRag** | `references/MyOwnDiaryRag/` | 本地日记管理与检索系统，支持全文搜索、AI 摘要、统计分析 |
| **nightDiary** | `references/nightDiary/` | 夜间日记系统，前端参考 |
| **diary-projects** | `references/diary-projects/` | 多个日记类项目合集（cube-diary、journiv-app、nightDiary 等） |

### 内容处理

| 项目 | 目录 | 说明 |
|---|---|---|
| **bili-video2book** | `references/bili-video2book/` | B 站视频转结构化笔记/教材，OpenBiliClaw 笔记系统 `transcribe/` 和 `synthesis/prompts.py` 改编自本项目（MIT License） |
| **wandao** | `references/wandao/` | 万能导 Wandao，笔记同步与管理工具，笔记系统任务管理设计参考其 checkpoint 模型 |

### 其他

| 项目 | 目录 | 说明 |
|---|---|---|
| **anything-llm** | `references/anything-llm/` | 全功能 LLM 应用平台，含文档管理、Agent 系统、Open Computer |
| **obsidian-second-brain** | `references/obsidian-second-brain/` | Obsidian 第二大脑跨平台技能，支持 Claude Code / Codex 等 8 个平台 |
| **youdaonote-pull** | `references/youdaonote-pull/` | 有道云笔记导出脚本，参考了其导入逻辑 |

---

## 外部包（`packages/`）

| 包 | 说明 |
|---|---|
| `obc-llm` | 多模型 LLM 适配层，从 `src/openbiliclaw/llm/` 提取的独立包 |
| `obc-discovery` | 内容发现引擎，从 `src/openbiliclaw/discovery/` 提取的独立包 |
| `obc-soul` | 用户画像引擎，从 `src/openbiliclaw/soul/` 提取的独立包 |

## 辅助脚本（`scripts/`）

| 脚本 | 说明 |
|---|---|
| `optimize_diary_html.py` | 日记 HTML 优化脚本 |
| `optimize_diary_html_v2.py` | 日记 HTML 优化脚本 v2 |

---

## 许可证说明

所有参考项目的代码版权归原作者所有。OpenBiliClaw 中改编自参考项目的代码在对应文件 docstring 中保留了原版权声明和许可证信息。