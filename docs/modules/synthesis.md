# 迭代合成引擎（synthesis）

> 把日记分析、聊天洞察、聊天话题等各模块的分析结果综合起来，通过迭代 LLM 合成，
> 增量更新个人认知画像。代码位于 `src/openbiliclaw/synthesis/`。

## 概述

`synthesis` 是一个**跨模块迭代合成器**。它周期性拉取其他模块新增的分析产出
（日记 `service` 的结果、聊天的 `insights` 与 `topics`），把「上次合成结果 + 新增数据」
喂给 LLM 做增量合成，产出新的洞察并写回，实现个人画像的持续进化。

每次运行都**从上次结果出发**，只处理新增数据，避免全量重算；合成结果**版本化**存储，
并同步回源模块的分析记录，形成闭环。

## 已实现功能

| 功能 | 说明 | 入口 |
|------|------|------|
| 增量合成 | 上次结果 + 新增分析 → 更新后洞察 | `engine.SynthesisEngine` |
| 多源汇入 | 拉取新日记分析 / 聊天洞察 / 聊天话题 | `store.get_new_diary_analyses / get_new_chat_insights / get_new_chat_topics` |
| 版本化存储 | 每次合成保存递增版本，可回溯 | `store.save_version / get_version / list_versions` |
| 状态游标 | 记录上次处理到的 ID，防重复处理 | `store.get_state / update_state` |
| 日记增强 | 由合成结果生成日记增强记录 | `store.save_diary_enhancement` |
| HTTP 路由 | 经 Web 管理路由暴露查询 / 触发 | `api.create_synthesis_router` |

## 公开 API

```python
from openbiliclaw.synthesis import SynthesisEngine, SynthesisConfig, SynthesisState, SynthesisVersion
from openbiliclaw.synthesis.models import DiarySynthesisResult, CrossModulePattern

engine = SynthesisEngine(llm_service, store)   # 组装引擎
engine.run()                                    # 跑一轮：拉新 → 合成 → 版本化 → 回写
```

对象模型（`models.py`，均为 Pydantic）：

- `SynthesisConfig`：合成开关 / 各源参与与否。
- `SynthesisState`：游标状态（已处理到的源 ID）。
- `SynthesisVersion`：单次合成产物（版本号 + 结果）。
- `DiarySynthesisResult` / `CrossModulePattern`：日记增强记录 / 跨模块模式。

存储（`store.py`，SQLite `data/openbiliclaw.db`）：

- `get_latest_version()` / `list_versions(limit)`：查询历史合成。
- `get_new_diary_analyses(since_id)` / `get_new_chat_insights` / `get_new_chat_topics`：增量拉取。

## 配置项

`synthesis` 无专有 `[synthesis]` 配置段。其 LLM 走 `obc_llm`，模型归属 `[llm]` 血缘；
数据入库路径由构造时传入的 `db_path` / `_self_evo_db_path` 决定（默认 `data/openbiliclaw.db`）。

## 设计决策

- **增量而非全量**：`since_id` 游标只拉新增，合成以上次结果为上下文，控制每次 LLM token 成本。
- **版本化**：每次合成存储版本与完整结果，画像可回溯、可审计。
- **回写闭环**：合成结果除落库外还同步回源模块分析记录，供仪表盘 / 下游模块读取。
- **跨模块归集**：日记 + 聊天的洞察归为同一画像迭代数据面，避免各模块自我封闭。

## 关联

- 上游：`diary`（分析结果）、`chat_analysis`（insights / topics）。
- 暴露：`api/_web_ui_routes.py` 通过 `create_synthesis_router` 挂到 Web 路由组。
- 依赖：`obc_llm`（LLM 合成）、`data/openbiliclaw.db`（存储）。
- 文档：`docs/modules/diary.md`、`docs/modules/chat_analysis.md`。