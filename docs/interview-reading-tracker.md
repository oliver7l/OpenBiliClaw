# 面试题阅读追踪系统

> ⚠️ **本文只描述「面试域」三子系统中的 B · 题目研习**。三子系统总览见
> [`modules/interview-overview.md`](./modules/interview-overview.md)（另有 A · 岗位备战、
> C · 面试复盘，期 2 URL 分区后 B 独占前缀 `/api/interview/study/*`，旧 `/api/interview/*` 仍兼容）。

## 概述

帮助你系统地管理、阅读和消化面试题，追踪掌握进度。

## 数据库

- 路径：`data/interview_questions.db`
- 表结构：
  - `iq_questions`：面试题库（题目、答案、分类、难度、来源、标签）
  - `iq_queue`：待看队列（优先级、计划日期）
  - `iq_records`：阅读记录（掌握程度、复习次数、笔记、花费时间）
  - `iq_plans`：阅读计划（每日目标、分类筛选、难度范围）
  - `iq_daily`：每日完成情况

## 快速开始

### 1. 查看今日待读
```bash
.venv/bin/python -m openbiliclaw.interview.study.cli today
```

### 2. 查看题库统计
```bash
.venv/bin/python -m openbiliclaw.interview.study.cli stats
```

### 3. 标记已读
```bash
# 标记为阅读中
.venv/bin/python -m openbiliclaw.interview.study.cli read <题目ID>

# 标记为已理解
.venv/bin/python -m openbiliclaw.interview.study.cli read <题目ID> --mastery understood

# 标记为已掌握
.venv/bin/python -m openbiliclaw.interview.study.cli master <题目ID>

# 标记为需要复习
.venv/bin/python -m openbiliclaw.interview.study.cli review <题目ID>

# 带笔记和时间
.venv/bin/python -m openbiliclaw.interview.study.cli read <题目ID> --notes "核心要点..." --time 15
```

### 4. 查看题目详情
```bash
.venv/bin/python -m openbiliclaw.interview.study.cli show <题目ID>
```

### 5. 查看待看队列
```bash
.venv/bin/python -m openbiliclaw.interview.study.cli queue
```

### 6. 创建阅读计划
```bash
# 默认计划：每天5题，全部分类，全部难度
.venv/bin/python -m openbiliclaw.interview.study.cli plan --name "秋招冲刺" --daily 5

# 只看推荐算法，每天3题
.venv/bin/python -m openbiliclaw.interview.study.cli plan --name "推荐专项" --daily 3 --categories recommendation

# 只看高难度题
.venv/bin/python -m openbiliclaw.interview.study.cli plan --name "难题攻坚" --daily 3 --min-diff 4 --max-diff 5
```

### 7. 查看阅读进度
```bash
.venv/bin/python -m openbiliclaw.interview.study.cli progress
```

## 掌握程度说明

| 状态 | 图标 | 说明 |
|------|------|------|
| not_started | ⬜ | 未开始 |
| reading | 📖 | 阅读中 |
| understood | ✅ | 理解了 |
| mastered | 🏆 | 掌握了 |
| need_review | 🔄 | 需要复习 |

## 题目分类

- `recommendation`：推荐算法（10题）
- `agent`：Agent（15题）
- `llm_engineering`：大模型工程（7题）
- `machine_learning`：机器学习基础（3题）
- `coding`：手撕代码（1题）
- `other`：其他（2题）

## 难度分布

- ⭐：1题（简单）
- ⭐⭐：6题
- ⭐⭐⭐：16题（中等）
- ⭐⭐⭐⭐：14题（较难）
- ⭐⭐⭐⭐⭐：1题（困难）

## 数据来源

1. **淘天生成式推荐面试记录**（15题）- 小红书笔记
   原始笔记：`求职知识库/02_方向知识库/大模型推荐专题/_资料副本/淘天生成式推荐面试记录.md`
2. **ReAct 高频面试题**（12题）- 小红书笔记
3. **作业帮大模型一面八股**（11题）- 小红书笔记

## 导入新题目

编辑 `src/openbiliclaw/interview/study/seed_iq_questions.py`，在对应的列表中添加题目，然后运行：

```bash
.venv/bin/python -m openbiliclaw.interview.study.seed_iq_questions
```

> 期 3「代码分层」后本子系统位于 `interview/study/`；旧路径 `interview/questions/`
> （含 `store` / `models` / `cli` / `seed_iq_questions`）保留 re-export 垫片一版，
> `python -m openbiliclaw.interview.questions.cli` 与 `...questions.seed_iq_questions`
> 仍可用，下一个大版本摘除。

## 阅读建议

1. **每天 5 题**，按难度从高到低（难题优先攻克）
2. **先理解后掌握**：第一遍标记 `understood`，复习时能脱口而出再标记 `mastered`
3. **定期复习**：标记 `need_review` 的题目会优先出现在今日待读中
4. **做笔记**：每道题写自己的理解和答题要点，不要只看标准答案
5. **模拟面试**：掌握后尝试口述答案，计时 3-5 分钟
