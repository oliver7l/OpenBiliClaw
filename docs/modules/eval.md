# 评估框架（eval）

> 自迭代评估框架：为目标系统（灵魂画像、内容发现、兴趣探针等）提供 personae 生成、
> 多维打分、参数优化、批量训练循环与可复现报告。代码位于 `src/openbiliclaw/eval/`。

## 概述

`eval` 是一个**跨目标复用的离线评估与优化框架**。它不绑定单一业务，而是抽象出
「生成候选（personae）→ 施加干预 → 打分 → 归因 → 优化参数 → 迭代」的标准循环，
供灵魂画像、内容发现策略、兴趣探针等通过各自的 `check()` / `tick()` 入口挂接使用。

核心价值是**归因闭环**：通过 `PromptOptimizer` 把评估偏差映射回具体 prompt / 参数，
生成定向修改（exploit）或随机扰动（explore），在缓解幻觉的前提下让系统持续变好。

## 已实现功能

| 功能 | 说明 | 入口 |
|------|------|------|
| Persona 池 | 按任务签名管理独立 persona 集，支持保存 / 匹配 / 计数 | `persona_pool.PersonaPool` |
| Persona 生成 | LLM（或 Agent SDK）按约束生成多样化 persona | `persona_generator.PersonaGenerator` |
| 画像评估 | 灵魂画像多维打分（MBTI / 兴趣树 / 特质字段），含字段级严重度 | `evaluator.EvalReport` |
| 画像判别 | 对 persona 生成结果做人格合理性裁决 | `persona_judge.PersonaJudgment` |
| Discovery 评估 | 内容发现策略多维打分（相关性 / 多样性 / 新颖性 / 回声避免） | `discovery_evaluator.DiscoveryEvaluator` |
| Speculation 评估 | 兴趣探针质量（无幻觉 / 确认率 / 多样性 / 域距离） | `speculation_evaluator.SpeculationEvaluator` |
| 场景生成 | 离线 mock 场景（B 站客户端 + 记忆）供无真实流量评估 | `discovery_scenario.*` |
| 事件模拟 | 用户行为事件序列生成（含 Agent SDK 模式） | `event_simulator.EventSimulator` |
| 参数优化器 | continuous / prompt 参数归因变更，支持测试验证 / 提交 / 回滚 | `optimizer.PromptOptimizer` |
| 训练循环 | 多 epoch 批量评估：选最差报告 → 归因 → 变更 → 复测 | `loop.OptimizationLoop` |
| 反馈采集 | 人类反馈收集与转成优化报告 | `human_feedback.*` |
| 报告渲染 | 训练小结 / 评估报告 / 曲线导出（ASCII / 文件） | `report.*` |
| 运行日志 | 分 epoch / persona 的运行目录与步骤追踪 | `run_logger.RunLogger` |

## 公开 API

```python
from openbiliclaw.eval.optimizer import PromptOptimizer, ContinuousParam, PromptParam
from openbiliclaw.eval.loop import OptimizationLoop, OptimizationConfig
from openbiliclaw.eval.persona_pool import PersonaPool
from openbiliclaw.eval.persona_generator import PersonaGenerator
from openbiliclaw.eval.discovery_evaluator import DiscoveryEvaluator
from openbiliclaw.eval.discovery_optimizer import create_discovery_optimizer
from openbiliclaw.eval.speculation_evaluator import SpeculationEvaluator

# 优化器三态：apply(变更) → validate_with_tests → commit/rollback
opt = PromptOptimizer(params=[ContinuousParam("min_score", 0.6), PromptParam("prompt", "...")])
opt.apply(changes)                      # 0 = 成功
ok, msg = opt.validate_with_tests()     # 用单测快速校验
opt.commit()                            # 或 opt.rollback()
```

关键对象：

- `EvalReport`（`evaluator.py`）：画像评估结果，`to_dict()` 供落盘 / 渲染。
- `DiscoveryEvalReport` / `StrategyEvalReport`（`discovery_evaluator.py`）：发现策略评估维度分。
- `SpeculationEvalReport`（`speculation_evaluator.py`）：探针质量评估。
- `OptimizationLoop.run(...)`（`loop.py`）：跑一轮多 epoch 优化，返回 `OptimizationResult`。
- `ScenarioGenerator`（`discovery_scenario.py`）：离线场景 / `MockBilibiliClient`，评估不依赖在线服务。

## 配置项

`eval` 无自身 `[eval]` 配置段。相关联的配置：

| 键 | 用途 |
|----|------|
| `[llm.embedding]` | discovery/speculation 评估里的语义维度 dep（记分前的嵌入） |
| `[llm]` provider 血缘 | 评估走 `obc_llm`，`model` 覆盖来自 `[llm.evaluation]` |

优化器内部用 `ContinuousParam` / `PromptParam` 描述参数，阈值与 prompt 内容由各
`OptimizationConfig` 实例提供，不写死进 config.toml。

## 设计决策

- **框架而非功能**：`eval` 刻意与具体业务解耦，通过「生成→打分→归因→优化」抽象复用，
  避免为 soul / discovery / speculation 各写一套评估。
- **归因驱动优化**（`PromptOptimizer`）：把偏差映射回 prompt / 参数，支持 exploit + explore，
  缓解纯随机调参的低效与幻觉放大。
- **离线优先**：`discovery_scenario` / `event_simulator` 提供 mock，评估不阻塞真实服务。
- **可复现**：`run_logger` 按 epoch / persona 落目录，`report` 生成曲线与小结，支持大跑批次收敛观察。

## 关联

- 消费方：`soul`（画像评估 / personae）、`discovery`（策略评估 / 优化）、`speculation`（探针评估）。
- 依赖：`obc_llm`（LLM 服务）、`[llm.embedding]`（语义维度）。
- 文档：`docs/modules/discovery.md`（策略评估章节）、`docs/modules/soul.md`（画像评估章节）。