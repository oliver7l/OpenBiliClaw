"""导入面试题到阅读追踪数据库。

用法：
    python -m openbiliclaw.interview.questions.import_questions
"""

from __future__ import annotations

import sys
from pathlib import Path

# 确保项目根目录在 path 中
PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from openbiliclaw.interview.questions.models import (  # noqa: E402
    Priority,
    QuestionCategory,
    QuestionCreate,
)
from openbiliclaw.interview.questions.store import InterviewQuestionStore  # noqa: E402

DB_PATH = PROJECT_ROOT / "data" / "interview_questions.db"

# ── 淘天生成式推荐面试题（15题）──────────────────────────────
TAOTIAN_QUESTIONS = [
    ("简单讲下项目里的推荐框架，业务背景", QuestionCategory.RECOMMENDATION, 2, "淘天生成式推荐", "推荐框架,业务背景,架构"),
    ("多目标是怎么选择的，稀疏的问题怎么解决，目标之间跷跷板有做过优化吗", QuestionCategory.RECOMMENDATION, 4, "淘天生成式推荐", "多目标,MMoE,PLE,稀疏,跷跷板"),
    ("伪负样本消偏怎么做的，具体原理", QuestionCategory.RECOMMENDATION, 4, "淘天生成式推荐", "伪负样本,消偏,IPW,ESMM,因果推断"),
    ("对于国家行业有做过特别建模吗？最终为什么推特征侧改造？网络上有做过尝试吗", QuestionCategory.RECOMMENDATION, 3, "淘天生成式推荐", "多场景,特征工程,STAR,分场景建模"),
    ("DIEN这块怎么做的？为什么不直接在DIN上加时序特征？为什么不用transformer", QuestionCategory.RECOMMENDATION, 4, "淘天生成式推荐", "DIEN,DIN,GRU,序列建模,兴趣演化"),
    ("生成式推荐了解多少，OneRec的原理知道吗？RankMixer做Scaling Up", QuestionCategory.RECOMMENDATION, 5, "淘天生成式推荐", "生成式推荐,OneRec,RankMixer,Scaling Up,Seq2Seq"),
    ("召回怎么做的？几路召回，除了线上实验如何验证召回价值？衡量每路贡献度", QuestionCategory.RECOMMENDATION, 3, "淘天生成式推荐", "召回,多路召回,双塔,CF,归因分析"),
    ("Agent这块怎么做的，业务背景是什么", QuestionCategory.AGENT, 4, "淘天生成式推荐", "Agent,智能导购,LLM,工具调用,ReAct"),
    ("了解Codex这些Harness的框架吗？和你们实际业务使用的有什么不同", QuestionCategory.AGENT, 3, "淘天生成式推荐", "Codex,Harness,工程框架,Agent"),
    ("为什么Claude Code不用RAG而是用grep这种检索方式呢", QuestionCategory.AGENT, 3, "淘天生成式推荐", "Claude Code,RAG,grep,代码检索,AST"),
    ("推荐里AUC和GAUC的区别吗？为啥推荐里比较倾向于用GAUC", QuestionCategory.RECOMMENDATION, 2, "淘天生成式推荐", "AUC,GAUC,评估指标,分组AUC"),
    ("你们会用到PCOC吗？做校准之类的", QuestionCategory.RECOMMENDATION, 3, "淘天生成式推荐", "PCOC,校准,Calibration,Platt Scaling"),
    ("双塔里为什么要做负采样修正？负采样修正怎么做的", QuestionCategory.RECOMMENDATION, 4, "淘天生成式推荐", "双塔,负采样,InfoNCE,LogQ Correction,对比学习"),
    ("为什么在推荐里经常只训练一轮？CV就会训练很多轮，原因是什么", QuestionCategory.MACHINE_LEARNING, 3, "淘天生成式推荐", "训练轮次,推荐系统,CV,数据驱动,模型驱动"),
    ("RQVAE怎么做的，有和VQVAE做过对比吗？手撕动态规划", QuestionCategory.MACHINE_LEARNING, 4, "淘天生成式推荐", "RQVAE,VQVAE,向量量化,残差量化,动态规划"),
]

# ── ReAct 高频面试题（12题）──────────────────────────────────
REACT_QUESTIONS = [
    ("你如何理解 ReAct？它的核心思想是什么", QuestionCategory.AGENT, 2, "ReAct高频面试题", "ReAct,Reasoning,Acting,核心思想"),
    ("ReAct 的完整执行流程是怎样的", QuestionCategory.AGENT, 2, "ReAct高频面试题", "ReAct,执行流程,Thought,Action,Observation"),
    ("ReAct 中 Thought、Action 和 Observation 分别起什么作用", QuestionCategory.AGENT, 2, "ReAct高频面试题", "ReAct,Thought,Action,Observation,闭环"),
    ("ReAct 与传统 Chain-of-Thought 的核心区别是什么", QuestionCategory.AGENT, 3, "ReAct高频面试题", "ReAct,CoT,Chain-of-Thought,推理,行动"),
    ("ReAct Agent 的 Agent Loop 通常是如何实现的", QuestionCategory.AGENT, 3, "ReAct高频面试题", "ReAct,Agent Loop,状态驱动,工程实现"),
    ("ReAct 中模型是如何决定是否调用工具以及调用哪个工具的", QuestionCategory.AGENT, 3, "ReAct高频面试题", "ReAct,工具调用,Function Calling,Tool Use,Schema"),
    ("ReAct Agent 通常在什么条件下结束循环", QuestionCategory.AGENT, 2, "ReAct高频面试题", "ReAct,结束条件,max turns,超时,预算"),
    ("如何防止 ReAct Agent 陷入重复调用工具或死循环", QuestionCategory.AGENT, 4, "ReAct高频面试题", "ReAct,死循环,重复调用,检测,状态机"),
    ("ReAct 中某次工具调用失败后，Agent 应该如何进行错误恢复", QuestionCategory.AGENT, 3, "ReAct高频面试题", "ReAct,错误恢复,重试,指数退避,换策略"),
    ("ReAct 在长程、多步骤任务中容易出现哪些问题", QuestionCategory.AGENT, 4, "ReAct高频面试题", "ReAct,长程任务,上下文膨胀,目标漂移,规划"),
    ("ReAct 和 Plan-and-Execute 分别更适合什么类型的任务", QuestionCategory.AGENT, 3, "ReAct高频面试题", "ReAct,Plan-and-Execute,任务类型,混合模式"),
    ("为什么实际生产系统通常不会直接使用最原始的 ReAct，而会在外层增加 Harness", QuestionCategory.AGENT, 4, "ReAct高频面试题", "ReAct,Harness,生产系统,可靠性,安全性,可控性"),
]

# ── 作业帮大模型一面面试题（11题）────────────────────────────
ZUOYEBANG_QUESTIONS = [
    ("常规自我介绍", QuestionCategory.OTHER, 1, "作业帮大模型一面", "自我介绍,STAR法则"),
    ("针对简历上的全部项目做深度追问，深挖落地细节", QuestionCategory.OTHER, 3, "作业帮大模型一面", "项目深挖,落地细节,STAR"),
    ("是否使用过 DeepSpeed？讲解 ZeRO 不同阶段的实现逻辑和显存优化底层原理", QuestionCategory.LLM_ENGINEERING, 4, "作业帮大模型一面", "DeepSpeed,ZeRO,显存优化,分布式训练,数据并行"),
    ("对比 LayerNorm 与 BatchNorm 的差异，解释 Transformer 为何选择 LayerNorm", QuestionCategory.MACHINE_LEARNING, 3, "作业帮大模型一面", "LayerNorm,BatchNorm,Transformer,归一化,序列数据"),
    ("算法手撕：三数之和（排序+双指针，重点处理去重、剪枝）", QuestionCategory.CODING, 3, "作业帮大模型一面", "三数之和,双指针,排序,去重,剪枝"),
    ("大模型主流微调方案有哪些？全参数微调、LoRA、QLoRA 三者对应的落地场景", QuestionCategory.LLM_ENGINEERING, 3, "作业帮大模型一面", "微调,全参数,LoRA,QLoRA,PEFT"),
    ("讲清楚 PPO 算法里的 clip 裁剪机制；区分在线强化学习与离线强化学习的核心差异", QuestionCategory.LLM_ENGINEERING, 4, "作业帮大模型一面", "PPO,clip,强化学习,在线RL,离线RL,RLHF"),
    ("在 RLHF 流程中 KL 散度的出现位置与实际作用？KL 偏大偏小分别带来哪些问题", QuestionCategory.LLM_ENGINEERING, 4, "作业帮大模型一面", "RLHF,KL散度,奖励函数,对齐,正则化"),
    ("简述 RAG 完整工作链路，梳理现存痛点短板，如何改善检索结果不准确的问题", QuestionCategory.LLM_ENGINEERING, 3, "作业帮大模型一面", "RAG,检索增强,工作链路,痛点,检索优化,Rerank"),
    ("对比 DPO 和 SFT，分析二者各自的优势与不足；说明 DPO 偏好数据对的构建方式", QuestionCategory.LLM_ENGINEERING, 4, "作业帮大模型一面", "DPO,SFT,偏好对齐,RLHF,偏好数据"),
    ("大模型主流的位置编码方案有哪些？讲解 RoPE 的工作原理，以及它的优缺点", QuestionCategory.LLM_ENGINEERING, 4, "作业帮大模型一面", "位置编码,RoPE,旋转位置编码,正弦编码,ALiBi"),
]


def main() -> None:
    store = InterviewQuestionStore(DB_PATH)

    all_questions = []
    for title, cat, diff, source, tags in TAOTIAN_QUESTIONS:
        all_questions.append(QuestionCreate(title=title, category=cat, difficulty=diff, source=source, tags=tags))
    for title, cat, diff, source, tags in REACT_QUESTIONS:
        all_questions.append(QuestionCreate(title=title, category=cat, difficulty=diff, source=source, tags=tags))
    for title, cat, diff, source, tags in ZUOYEBANG_QUESTIONS:
        all_questions.append(QuestionCreate(title=title, category=cat, difficulty=diff, source=source, tags=tags))

    print(f"准备导入 {len(all_questions)} 道面试题...")
    count = store.bulk_add_questions(all_questions)
    print(f"成功导入 {count} 道面试题")

    # 全部加入待看队列
    for q in store.list_questions(limit=200):
        priority = Priority.HIGH if q.difficulty >= 4 else Priority.MEDIUM
        store.add_to_queue(q.id, priority=priority)
    print("已全部加入待看队列")

    # 统计
    stats = store.stats()
    print("\n题库统计：")
    print(f"  总数：{stats.total}")
    print(f"  按分类：{stats.by_category}")
    print(f"  按难度：{stats.by_difficulty}")
    print(f"  数据库：{DB_PATH}")


if __name__ == "__main__":
    main()
