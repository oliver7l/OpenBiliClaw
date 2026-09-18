# Data Agent（问数智能体）

个人数据仓库的 NL→SQL 问数 Agent。对应「微信支付-数据科学」JD 练手项目，面试对照见
`求职知识库/03_岗位弹药库/微信支付-数据科学-DataAgent练手对照.md`。

## 架构（对应弹药 §1 五点设计）

```
问题 ──► 口径层匹配（metrics.py，高频指标模板直达）
          │ 未命中
          ▼
        LLM 生成（llm.py，qwen2.5:7b 本地，schema 目录上下文 + few-shot）
          │
          ▼
        护栏（guard.py：意图前置拦截 → SQL 校验 → 只读执行）
          │
          ▼
        结果表格 + 自动解读（agent.py）
评测：data_agent_golden.json 黄金问答集（agent.py eval，执行结果一致性打分）
```

## 用法

```bash
cd scripts/data_agent
../../.venv/bin/python3.11 agent.py ask "最近7天采集成功率怎么样"   # 全自动
../../.venv/bin/python3.11 agent.py ask "最近一个月哪个平台失败最多" # 长尾→LLM
../../.venv/bin/python3.11 agent.py metric 投递漏斗                 # 直走口径层
../../.venv/bin/python3.11 agent.py sql "SELECT COUNT(*) FROM content.db.articles"
../../.venv/bin/python3.11 agent.py eval --verbose                 # 跑评测
../../.venv/bin/python3.11 catalog.py                              # 重建 schema 目录
```

## 设计要点

- **只读三重保险**：SQLite URI `mode=ro` + `PRAGMA query_only` + 危险词/表白名单校验
- **口径层优先**：高频指标模板化（≈100% 准确），LLM 只处理长尾——"text-to-SQL 失败多半是口径没定义清楚"
- **拒答协议**：模型低置信输出 `{"clarify": ...}` 反问，而不是硬编 SQL
- **评测即回归**：换模型/改 prompt 后跑 eval 防退化；FAIL 案例回流黄金集（自进化）

## 已知边界

- qwen2.5:7b 本地生成约 70s/条（Mac CPU）；生产换强模型 + API
- 跨库查询用 `库名.表名` 引用（如 content.db.articles），只读 attach
- 文本判空必须 `IS NULL OR = ''`（SQLite 教训，已写进生成规则和目录注释）
