"""把小红书 MiniOneRec 笔记提炼成面试题，导入 iq_questions + iq_queue。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openbiliclaw.interview.questions.models import Priority, QuestionCategory, QuestionCreate
from openbiliclaw.interview.questions.store import InterviewQuestionStore

DB = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/data/interview_questions.db"
SOURCE = "MiniOneRec生成式推荐(小红书)"
URL = "https://www.xiaohongshu.com/explore/6aa10ed0000000002502e203"
TAGS = "生成式推荐,MiniOneRec,SID,RQ-VAE,GRPO,推荐系统"

QUESTIONS = [
    (
        "传统推荐「召回→CTR预估→排序」范式的流程是什么？它的局限在哪？",
        "召回一批候选 item → 排序模型预测 CTR/CVR → 按分数排序。局限：① item 只是数据库 id，缺乏语义；② 排序分数与用户意图理解脱节；③ 冷启动 item 缺乏行为数据时难以表征。",
        3,
    ),
    (
        "生成式推荐如何把推荐问题改写成「大模型生成问题」？与打分排序的本质区别？",
        "不给商品打分，而是把商品变成 token，让 LLM 像预测下一个词一样预测用户下一个可能感兴趣的 item。本质区别：从判别式打分（discriminative ranking）变成自回归序列生成（generative ranking），推荐目标从「分数排序」变成「序列生成+合法解码」。",
        3,
    ),
    (
        "什么是 SID（Semantic ID）？MiniOneRec 是如何构造 SID 的？",
        "SID 是把 item 压缩成离散语义 token 的层级编码。MiniOneRec 构造：商品标题/描述等文本 → 冻结的文本编码器得到语义 embedding → RQ-VAE / RQ-Kmeans 量化成离散 token。商品从数据库 id 变成模型可生成、可对齐语义的「推荐 token」。",
        4,
    ),
    (
        "RQ-VAE / RQ-Kmeans 在生成式推荐中的作用？为什么要把 embedding 量化成离散 token？",
        "作用：把连续语义 embedding 逐级残差量化成层级离散码（多级 codebook，前缀共享语义、末层保身份）。原因：① LLM 的词表是离散的，item 必须离散化才能进 next-token 框架；② 层级编码支持从粗到细的 beam search；③ 离散 token 可与文本语义空间对齐。",
        4,
    ),
    (
        "为什么 SID 构造阶段用「冻结的文本编码器」？冻结有什么好处？",
        "好处：① 语义空间稳定，避免端到端训练中 embedding 漂移导致码本失效；② 省算力（编码器不参与训练）；③ 冻结的通用语义空间保留预训练知识，冷启动 item 仅凭文本即可获得合理 SID。",
        3,
    ),
    (
        "MiniOneRec 的 SFT 阶段如何把用户行为序列改造成 next-token prediction 任务？",
        "把用户历史交互 item 序列转成对应 SID token 序列：「User bought → [SID history] → predict next item SID」，训练 LLM 自回归预测下一个 item 的 SID token，与语言模型的训练目标完全同构。",
        3,
    ),
    (
        "生成式推荐为什么 SFT 之后还需要 RL 阶段？",
        "SFT 只做 teacher forcing 的 next-token 拟合，存在 exposure bias：训练时看真值前缀、推理时要自己 rollout；且 SFT 优化 token 级似然，不直接优化推荐指标（命中率、排名）。RL 用真实解码路径 + 推荐导向奖励，弥合训练目标与业务目标的 gap。",
        4,
    ),
    (
        "GRPO 的原理是什么？为什么比 PPO 更适合推荐场景？",
        "GRPO（Group Relative Policy Optimization）：同一 prompt 采样一组 G 个候选输出，用组内奖励的相对优势（减组均值/除组标准差）作为 advantage 更新策略，省去 PPO 的 value model。适合推荐：一次生成 G 个候选 item 天然成组；奖励可即时计算（命中/排名），不需要复杂价值网络。",
        4,
    ),
    (
        "MiniOneRec 的 ACC Reward 和 Rank Reward 分别是什么？为什么要两个结合？",
        "ACC Reward：生成的 item 命中用户真实交互记 1 否则 0（命中率导向）；Rank Reward：命中的 item 在生成列表中位置越靠前奖励越高（排序质量导向）。只用 ACC 模型学会「命中但不排序」，只用 Rank 可能偏向保守押注头部，两者结合同时优化命中与排序。",
        4,
    ),
    (
        "什么是 Constrained Beam Search？为什么生成式推荐必须要约束解码？",
        "在 beam search 的每层扩展时，只允许沿合法 SID 码本树路径展开（非法前缀直接剪枝），保证最终解码出的是真实存在的商品 SID。必要性：LLM 自由生成可能产出码本中不存在的 token 组合——生成了一个「不存在的商品」，约束解码把生成空间限制在商品库内。",
        4,
    ),
    (
        "生成式推荐 vs 传统 CTR 排序的优劣对比？各自适用场景？",
        "生成式优势：语义泛化/冷启动好、召回-排序统一、能端到端对齐用户意图；劣势：推理成本高（自回归解码）、毫秒级延迟要求下难当精排、评测与可解释性体系不成熟。适用：生成式适合召回/粗排/新用户冷启动；传统 CTR 排序在精排低延迟高 QPS 场景仍是主力（可参考腾讯 TGR 的分层生成式架构）。",
        3,
    ),
    (
        "复现 MiniOneRec 需要 4-8 张 A100/H100 80GB——从中怎么看待生成式推荐的落地现状？",
        "训练门槛高（SFT+RL 双阶段、LLM 主干）说明当前生成式推荐仍是大厂游戏；工业落地路径是蒸馏/小参数化（如 TGR 离线摊销推理）、只把生成式用在召回层、或像 GR4AD 用 LazyAR 降 QPS 成本。个人学习者价值在「读代码理解范式」而非复现。",
        3,
    ),
]


def main() -> None:
    store = InterviewQuestionStore(DB)
    existing = {
        row[0] for row in __import__("sqlite3").connect(DB).execute("SELECT title FROM iq_questions")
    }
    added = []
    for title, answer, diff in QUESTIONS:
        if title in existing:
            print(f"跳过（已存在）: {title}")
            continue
        q = store.add_question(
            QuestionCreate(
                title=title,
                answer=answer,
                category=QuestionCategory.RECOMMENDATION,
                difficulty=diff,
                source=SOURCE,
                tags=TAGS,
                url=URL,
            )
        )
        store.add_to_queue(q.id, Priority.HIGH)
        added.append(q.id)
        print(f"入库 id={q.id} 难度={diff} {title}")
    print(f"\n共新增 {len(added)} 题: {added}")


if __name__ == "__main__":
    main()
