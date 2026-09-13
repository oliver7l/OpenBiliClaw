"""把拼多多二面 B 组「购买链路建模 / LTV」两条话术写入 interview_scripts 表。

直接写 SQLite，不依赖 LLM（避开频率限制）。
如需撤销：DELETE FROM interview_scripts WHERE source LIKE '业界调研-拼多多B组-%';
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "interview.db"

NOW = datetime.now().isoformat(timespec="seconds")

# ---------------------------------------------------------------------------
# B2 购买链路建模（⭐⭐ 真实题）
# ---------------------------------------------------------------------------
B2_CONTENT = """## 标准回答（面试直接说）
电商购买是典型漏斗：曝光 → 点击 → 加购 → 下单(转化)。传统 CVR 只拟合「点击→购买」一步，但中间有大量信号（加购、收藏）被浪费，且转化样本极稀疏（<0.1%）。

业界从两段式走向多段式建模：
1. 阿里 ESMM (CIKM'18, 阿里妈妈)：全空间多任务。传统 CVR 两痛点——样本选择偏差(SSB，只在点击样本上训练、推理却在曝光空间) + 数据稀疏(DS，转化样本<0.1%)。ESMM 引入 CTR、CTCVR 两个辅助任务在**完整曝光空间**联合训练，pCTCVR = pCTR × pCVR，底层 embedding 共享。一举解决训练/预测空间不一致 + 利用点击数据全局优化。
2. 阿里 ESM² (SIGIR'2020)：把「点击→购买」之间的中间行为拆出来——DAction(加购/收藏，高意图) vs OAction(其他)。行为图：曝光→点击→D(O)Action→购买。三模块：SEM(共享embedding) + DPM(并行MLP估各步条件概率) + SCM(链式法则组合)，CVR = (1-y₂)×y₄ + y₂×y₃。阿里 3 亿样本离线 CVR AUC 0.8486 vs ESMM 0.8398，线上 CVR +3%。
3. 京东 DBMTL 等把多阶段「深度行为」做多任务。

我的实践套路：从 ESMM 两段扩展到三段漏斗——pOrder = pClick × pCart|Click × pOrder|Cart，加购这个中间信号把稀疏问题 densify。这是拼多多电商天然链路，面试官大概率想听你怎么把「两段扩三段」。

## 业界真实做法（加分）
- **ESMM/ESM² 是 CVR 预估工业事实标准**：全空间建模、共享 embedding 缓解稀疏、链式组合保概率一致。
- 拼多多电商主搜/推荐都是这套漏斗链路，团队（精排价值 3-4 人方向）一定关心你怎么把中间行为用起来。
- 腾讯 ESMM 实战（我参与）：点击 +24.14%、人均时长 +24.28%——可主动衔接。

## 关键坑
- 训练空间 ≠ 预测空间 → 必须在全曝光空间建模，别只在点击样本训
- 中间行为定义因类目而异：服饰「加购」重要、快消「立即购买」更直接，先漏斗分析定关键路径
- 多任务 loss 权重：从 1:1:1:1 起，逐步加大主任务权重

## 追问应对
- 为什么不直接单模型估 pOrder？ → 稀疏+偏差双重问题，多任务共享 embedding 用点击数据训出商品/用户表达
- 加购样本也不多？ → DAction 放宽(收藏也算) + OAction 路径兜底，ESM² 公式两路径都覆盖
- 你做过吗？ → OPPO 多目标 + 腾讯 ESMM 实战可直接衔接

## 参考资料
- 阿里 ESMM: Entire Space Multi-Task Model (CIKM'18)
- 阿里 ESM² (SIGIR'2020, arXiv:1910.07099)
- 京东 DBMTL 深度行为多任务
"""

B2_KEY_POINTS = "购买链路,ESMM,ESM²,全空间多任务,三段漏斗,样本选择偏差,链式组合,拼多多二面B2"

# ---------------------------------------------------------------------------
# B3 LTV 进排序（团队方向 + 你有 OPPO 经验）
# ---------------------------------------------------------------------------
B3_CONTENT = """## 标准回答（面试直接说）
LTV(生命周期价值)是用户/商品长期带来的价值总和。排序若只优化短期 CTR/GMV，会薅羊毛、损害长期健康。接法有三板斧：

1. 独立目标塔：多目标框架(MMoE/PLE)给 LTV 一个独立塔，用长窗口(14/30/120天)样本当监督。
2. 约束优化：以「GMV 最大」为目标、「CTR 不降」为约束（或反过来），**保短期不跌抬长期**——这是主流落地套路，比简单加权融分稳。
3. 分层：新客看 LTV(拉新质量)、老客看短期+复购；按 LTV 分层匹配资源。

我的实践：OPPO 把 LTV 接入排序——给 LTV 独立塔 + 长窗口样本，约束优化保短期 GMV 抬长期 LTV。这正是拼多多「精排价值/LTV」团队方向在做的事，可主动对齐。

## 业界真实做法（加分）
- **快手 Kuaishou (CIKM'2022, Billion-user Customer LTV)**：十亿级用户 LTV 预估。① ODMN(有序依赖单调网络)建模不同时间跨度 LTV 间有序依赖；② MDME(多分布多专家)按 LTV 分桶缓解不平衡分布；③ 相对基尼系数衡量拟合不平衡能力。
- **腾讯 大禹投放平台 (DataFun)**：投放场景 CLTV，选 LTV14 作短期代理目标(和 LTV32/120 高相关)做 RTA 实时流量优选；评估偏 Discrimination 而非 Calibration(头部筛选看 AUC / Normalized Gini)；零值膨胀参考 ESMM 级联建模「付费概率+金额」或快手 MDME。
- **阿里 WWW'2026 A Long-term Value Prediction Framework In Video Ranking (淘宝)**：不把 LTV 放重排(候选少且 RL 贵)，而在**精排阶段做 task augmentation**——PDQ(位置去偏分位) + 多维归因 + 跨时作者建模。线上 PDQ 提升 views +2.49%、归因提升 watch time +1.23%、作者建模高质作者 views +4.03%。
- **字节**：LTV 用于广告投放 ROI、内容分发排序权重，分层(高/中/低)匹配策略。

## 关键坑
- LTV 样本延迟 → 延迟反馈(Defer/ES-DFM 重要性加权) + 用短窗口代理(LTV14)做敏捷迭代
- 与短期目标冲突 → 约束优化(主-从)而非简单加权；分场景调权重
- 评估 ≠ 精确校准：投放偏 AUC/Gini(筛选)，线上用长窗口后验

## 追问应对
- LTV 样本延迟怎么解？ → 延迟反馈重要性加权 + 短窗口代理目标敏捷迭代
- 和短期冲突？ → 约束优化而非加权，保短期不跌
- 你做过吗？ → OPPO 把 LTV 接入排序，可展开

## 参考资料
- 快手 Billion-user Customer LTV (CIKM'2022, arXiv:2208.13358)
- 腾讯 大禹投放平台 CLTV (DataFun 分享)
- 阿里 A Long-term Value Prediction Framework In Video Ranking (WWW'2026)
"""

B3_KEY_POINTS = "LTV,生命周期价值,约束优化,独立目标塔,长窗口样本,快手MDME,阿里PDQ,拼多多二面B3"

SCRIPTS = [
    {
        "type": "技术问答",
        "company": "拼多多",
        "position": "AI算法工程师(电商推荐)",
        "title": "购买链路怎么建模（拼多多二面 B2 · 附业界做法）",
        "content": B2_CONTENT,
        "key_points": B2_KEY_POINTS,
        "priority": "高",
        "tags": "购买链路,ESMM,ESM²,多任务,漏斗,拼多多二面,推荐系统,CVR",
        "note": "基于一面情报 B2 必背题，补充业界真实落地（阿里ESMM/ESM²、京东DBMTL）",
        "source": "业界调研-拼多多B组-购买链路-2026-09-12",
    },
    {
        "type": "技术问答",
        "company": "拼多多",
        "position": "AI算法工程师(电商推荐)",
        "title": "LTV 怎么进排序（拼多多二面 B3 · 附业界做法）",
        "content": B3_CONTENT,
        "key_points": B3_KEY_POINTS,
        "priority": "高",
        "tags": "LTV,生命周期价值,多目标,约束优化,快手,阿里,拼多多二面,推荐系统",
        "note": "基于一面情报 B3 团队方向题，补充业界真实落地（快手MDME、腾讯大禹、阿里PDQ）",
        "source": "业界调研-拼多多B组-LTV-2026-09-12",
    },
]


def main() -> None:
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    inserted = []
    for s in SCRIPTS:
        cur.execute("SELECT id FROM interview_scripts WHERE source=?", (s["source"],))
        if cur.fetchone():
            print(f"已存在 source={s['source']}，跳过")
            continue
        cur.execute(
            """INSERT INTO interview_scripts
               (type, company, position, title, content, key_points, priority, tags, used_count, note, source, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                s["type"], s["company"], s["position"], s["title"], s["content"],
                s["key_points"], s["priority"], s["tags"], 0, s["note"],
                s["source"], NOW, NOW,
            ),
        )
        inserted.append((cur.lastrowid, s["title"]))
    conn.commit()
    conn.close()
    if inserted:
        for rid, t in inserted:
            print(f"已插入 interview_scripts id={rid} | {t}")
    else:
        print("无新增（均已存在）")


if __name__ == "__main__":
    main()
