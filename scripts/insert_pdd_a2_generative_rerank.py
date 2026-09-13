"""把「生成式重排」业界调研写成一条拼多多二面话术，插入 interview_scripts 表。

直接写 SQLite，不依赖 LLM（避开频率限制）。
如需撤销：DELETE FROM interview_scripts WHERE source='业界调研-生成式重排-2026-09-12';
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "interview.db"

CONTENT = """## 标准回答（面试直接说）
传统重排是「打分-约束」范式（MMR/DPP/ListNet）：先对精排 top-N 逐条打分，再做多样性/规则调整，列表收益不等于单点之和。
生成式重排把列表生成当成序列生成任务：自回归逐个生成 item（类似 NLG），模型直接以整个列表为优化目标，天然建模 item 间依赖和全局收益。
难点（一面面试官也提过）：① 线上耗时——自回归逐个生成，序列长则延迟高，需高效解码（缓存/并行/蒸馏/两阶段）；② 样本不丰富——训练样本是打散后的好结果，存在选择偏差。
坦诚答：没直接做过，但精排打分 + 列表约束熟；落地会先两阶段（规则重排保底 + 生成式小流量）。

## 业界真实做法（加分，面试官爱听）
1. 两阶段「生成-评估」(G-E) 框架 —— 阿里每平每屋(淘宝)、腾讯社区重排的主流落地
   - 生成阶段：MMR/DPP/beam search 并行产出若干候选列表（也可上生成式模型）
   - 评估阶段：上下文感知模型（双向 LSTM / Multi-head Self Attention）对整列打分，选全局最优
   - 解决核心痛点：打分上下文不等于展示上下文（传统 listwise 重排的硬伤，打完分再排上下文已变）
   - 阿里每平每屋线上 A/B（base=DPP）：pctr +2.17%、浏览深度 +0.25%、人均点击 +2.43%、pctcvr +1.86%

2. 自回归 vs 非自回归（腾讯社区重排双阶段框架）
   - 自回归：序列依赖强、质量高，但 L 次前向传播、延迟高、早期错误无法回溯修正
   - 非自回归：1 次前向传播出整列，快但条件独立假设过强、难建模复杂依赖
   - 腾讯上线了非自回归（Candidates Encoder + Position Encoder 带 Cross Attention，输出 n×L 位置-物品得分矩阵）
   - 质量-延迟-多样性「不可能三角」：beam width 增大质量提升但耗时边际递减

3. 端到端生成式推荐（快手 OneRec，直接替代整个级联）
   - 单一 Encoder-Decoder 自回归生成 session 列表，语义 ID（残差 K-Means 量化）替代 item ID
   - 推理：Beam Search 解码 + 约束解码（trie 前缀树防止生成非法 ID）
   - 偏好对齐：Reward Model + DPO（迭代偏好对齐 IPA）让生成列表对业务指标负责
   - 线上：人均观看时长 +1.68%，OPEX 降到传统架构的 10.6%

4. 序列生成重排先驱（可随口引用）
   - Google Seq2Slate (ICML'19)：seq2seq + Pointer Network 做重排
   - 阿里 miRNN (IJCAI'18)：RNN + Beam Search 做 GMV 最大化
   - 阿里 PRM (RecSys'19)：Transformer 个性化重排
   - Meta HSTU (ICML'24)：生成式推荐框架，pointwise aggregated attention，1.5T 参数，ranking A/B +12.4%，验证推荐 Scaling Law

## 追问应对
- 为什么不用纯生成式？ → 延迟/样本是硬约束，两阶段先保底再小流量最稳；或引用腾讯非自回归折中
- 样本不丰富怎么训？ → 曝光日志含正负样本 + IPS/DR 去偏 + off-policy correction / CQL 学可泛化的列表价值函数
- 和你的 Gap 怎么补？ → 从打分-约束到序列生成的转变我理解，落地点先两阶段

## 参考资料
- 阿里每平每屋生成式重排实践（阿里云开发者社区）
- 腾讯社区推荐重排双阶段框架演进（腾讯云开发者社区）
- 快手 OneRec 技术报告 (arXiv:2502.18965)
- Meta HSTU (arXiv:2402.17152)
"""

KEY_POINTS = "生成式重排,序列生成,两阶段G-E框架,自回归vs非自回归,OneRec端到端,不可能三角,样本去偏,拼多多二面A2"

NOW = datetime.now().isoformat(timespec="seconds")


def main() -> None:
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    # 幂等：同 source 已存在则跳过
    cur.execute(
        "SELECT id FROM interview_scripts WHERE source=?",
        ("业界调研-生成式重排-2026-09-12",),
    )
    if cur.fetchone():
        print("已存在，跳过")
        conn.close()
        return
    cur.execute(
        """INSERT INTO interview_scripts
           (type, company, position, title, content, key_points, priority, tags, used_count, note, source, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "技术问答",
            "拼多多",
            "AI算法工程师(电商推荐)",
            "生成式重排是什么（拼多多二面 A2 · 附业界做法）",
            CONTENT,
            KEY_POINTS,
            "高",
            "生成式重排,重排,拼多多二面,推荐系统,序列生成,OneRec,HSTU",
            0,
            "基于一面情报 A2 必背题，补充业界真实落地（阿里/腾讯两阶段、快手OneRec端到端）",
            "业界调研-生成式重排-2026-09-12",
            NOW,
            NOW,
        ),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    print(f"已插入 interview_scripts id={new_id}")


if __name__ == "__main__":
    main()
