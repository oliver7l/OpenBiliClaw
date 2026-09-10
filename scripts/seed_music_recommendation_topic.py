"""Seed 音乐推荐学习专题（幂等，可重复运行）。

把整理好的音乐推荐学习资料写入 knowledge.topics / knowledge.topic_items，
前端「📚 专题」页即可直接展示。
"""

from __future__ import annotations

from pathlib import Path

from openbiliclaw.storage.database import Database

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "openbiliclaw.db"

TOPIC = {
    "name": "音乐推荐",
    "slug": "music-recommendation",
    "description": "音乐推荐系统学习专题：综述、工业实践、冷启动、歌单生成、面试链路。面向万声音乐推荐算法面试准备（精排/多目标/序列建模/重排）。",
    "keywords": ["音乐推荐", "精排", "多目标", "冷启动", "歌单生成", "序列建模"],
    "platforms": ["zhihu", "csdn"],
}

ITEMS = [
    # ── 综述与基础 ─────────────────────────────────────────────
    {
        "title": "Content Filtering Methods for Music Recommendation: A Review（2025）",
        "url": "https://arxiv.org/html/2507.02282v1",
        "summary": "内容过滤综述：音频信号分析（情绪识别/曲风分类/乐器检测）+ 歌词 LLM 分析 + 上下文感知，解决协同过滤在音乐场景 99.9% 稀疏性问题。",
        "source_platform": "arxiv",
        "source_name": "arXiv 2507.02282",
        "topic_label": "综述",
    },
    {
        "title": "Deep Learning Based Music Recommendation Systems: A Review",
        "url": "https://ace.ewapub.com/article/view/17400.pdf",
        "summary": "深度学习音乐推荐综述：CNN（音频频谱特征）/RNN-LSTM（时序行为）/自编码器（冷启动）+ 混合模型，指标 precision/recall/F1/MRR。",
        "source_platform": "ewapub",
        "source_name": "ACEW",
        "topic_label": "综述",
    },
    {
        "title": "Music Recommendation with LLMs: Challenges, Opportunities, and Evaluation",
        "url": "https://arxiv.org/html/2511.16478",
        "summary": "Deezer 视角：LLM 生成式推荐颠覆传统 IR 评估范式（准确性指标失效），梳理用户建模/物品建模/自然语言交互与评测方法学。",
        "source_platform": "arxiv",
        "source_name": "arXiv 2511.16478",
        "topic_label": "综述",
    },
    {
        "title": "A Review of the Emotion-Induced Music Recommendation Systems",
        "url": "https://dline.info/ojs/index.php/jdim/article/view/541",
        "summary": "情绪诱导音乐推荐综述（2011-2025 共 32 项研究）：表情/生理信号驱动，按内容过滤/序列推荐/情绪检测分类，指出冷启动与实时处理挑战。",
        "source_platform": "dline",
        "source_name": "JDIM 2025",
        "topic_label": "综述",
    },
    # ── 工业实践 ─────────────────────────────────────────────
    {
        "title": "推荐系统在音乐领域的实践：网易云音乐推荐算法解析",
        "url": "https://blog.csdn.net/universsky2015/article/details/152194224",
        "summary": "混合推荐策略（协同过滤+内容+实时行为）：音乐 vs 电商差异（决策成本低/兴趣多变/价值难量化）、冷启动三类、多维度特征融合。",
        "source_platform": "csdn",
        "source_name": "CSDN",
        "topic_label": "工业实践",
    },
    {
        "title": "网易云音乐推荐中的用户行为序列深度建模",
        "url": "https://blog.csdn.net/jxq0816/article/details/109902699",
        "summary": "精排模型迭代历程：多兴趣点挖掘、多空间长短期兴趣、兴趣演化网络；音乐可重复消费 vs 电商不可重复，正向/负向行为累积修正偏好。",
        "source_platform": "csdn",
        "source_name": "CSDN",
        "topic_label": "工业实践",
    },
    {
        "title": "解码智能推荐：多模态大模型在网易云音乐的创新应用",
        "url": "https://www.51cto.com/article/800204.html",
        "summary": "统一多场景精排模型 + 多模态对齐模块（歌词文本/专辑封面/音频）：多模态表征缓解马太效应，人均播放时长+3%、歌单分发+50%。",
        "source_platform": "51cto",
        "source_name": "51CTO",
        "topic_label": "工业实践",
    },
    {
        "title": "网易云音乐算法备案公示：召回→排序→人工干预链路",
        "url": "https://y.music.163.com/m/at/661f2af6e36f7c50ead8994b",
        "summary": "官方算法机制：数据收集→风控→特征引擎→召回引擎（千万级→千级）→排序引擎（十级歌曲）→线上服务→人工干预。",
        "source_platform": "netease",
        "source_name": "网易云音乐",
        "topic_label": "工业实践",
    },
    {
        "title": "Spotify: Generalized User Representations for Large-Scale Recommendations",
        "url": "https://research.atspotify.com/2025/9/generalized-user-representations-for-large-scale-recommendations",
        "summary": "用户表征框架：音频+协同双编码器 → 三时间尺度聚合（6个月/1月/1周）→ 自编码器压缩，下游召回/排序/搜索共享，NRT 近实时更新。",
        "source_platform": "spotify",
        "source_name": "Spotify Research",
        "topic_label": "工业实践",
    },
    {
        "title": "Spotify: Calibrated Recommendations with Contextual Bandits on Homepage",
        "url": "https://research.atspotify.com/2025/9/calibrated-recommendations-with-contextual-bandits-on-spotify-homepage",
        "summary": "内容类型比例校准（音乐/播客/有声书）：contextual bandit 预测目标分布 + KL 散度惩罚逐条构建列表，比静态历史比例准确度+35%。",
        "source_platform": "spotify",
        "source_name": "Spotify Research",
        "topic_label": "工业实践",
    },
    {
        "title": "Mostra: A Flexible Balancing Framework for Music Sequencing（WWW'22）",
        "url": "https://dl.acm.org/doi/fullHtml/10.1145/3485447.3512014",
        "summary": "音乐序列排序多利益方优化：Set Transformer 编码 + submodular 多目标 beam search 解码，动态平衡用户满意度/新歌发现/艺人曝光/平台目标。",
        "source_platform": "acm",
        "source_name": "ACM WWW",
        "topic_label": "工业实践",
    },
    {
        "title": "PIANO: Personalized Reranking via Information Aggregation Node for Music Search",
        "url": "https://arxiv.org/pdf/2606.16641",
        "summary": "网易云搜索列表级重排：Query 驱动兴趣精炼器对齐历史查询 + [CLS] 信息聚合节点做 listwise 多目标，在线 CTR+0.62%、CVR+4.45%。",
        "source_platform": "arxiv",
        "source_name": "arXiv 2606.16641",
        "topic_label": "工业实践",
    },
    {
        "title": "Scaling Recommender Transformers to One Billion Parameters（KDD'26）",
        "url": "https://arxiv.org/html/2507.15994v2",
        "summary": "Yandex 音乐平台：生成式推荐 Transformer 1B 参数，自回归分解为反馈预测+下一物品预测，总收听时长+2.26%、点赞+6.37%。",
        "source_platform": "arxiv",
        "source_name": "arXiv 2507.15994",
        "topic_label": "工业实践",
    },
    {
        "title": "工业级推荐系统进阶：从多级流水线到生成式新范式",
        "url": "https://juejin.cn/post/7610325062705168426",
        "summary": "分层漏斗架构（数据→召回→粗排→精排→重排→服务）+ 快手 OneRec 端到端生成式推荐：运营成本降至 10.6%、GMV+21%。",
        "source_platform": "juejin",
        "source_name": "掘金",
        "topic_label": "工业实践",
    },
    # ── 冷启动 ─────────────────────────────────────────────
    {
        "title": "冷启动算法系列：云音乐歌曲冷启动初探",
        "url": "https://juejin.cn/post/7086063980006866952",
        "summary": "冷启动歌曲排序：导出非冷启动模型中的曲风/语种/艺人 embedding 与统计特征，按用户对这三维的偏好序列打分分发。",
        "source_platform": "juejin",
        "source_name": "网易云音乐技术团队",
        "topic_label": "冷启动",
    },
    {
        "title": "网易云音乐推荐系统的冷启动技术",
        "url": "https://www.51cto.com/article/773316.html",
        "summary": "CLIP 预训练多模态表征（音频 Transformer + 歌词 BERT）；两种建模：I2I2U 间接建模 + 多模态 DSSM 用户兴趣边界建模。",
        "source_platform": "51cto",
        "source_name": "51CTO",
        "topic_label": "冷启动",
    },
    {
        "title": "CIKM 2021 | 云音乐与模型无关的冷启动推荐框架：MAIL",
        "url": "https://juejin.cn/post/7091216412030566413",
        "summary": "双塔零样本兴趣学习：双自编码器跨模态重建生成虚拟行为偏好，排序塔与模型无关，直播推荐 CTR+13~15%。",
        "source_platform": "juejin",
        "source_name": "网易云音乐技术团队",
        "topic_label": "冷启动",
    },
    {
        "title": "网易云音乐：如何通过数据发掘音乐品鉴家，找到宝藏小众音乐",
        "url": "https://www.woshipm.com/evaluating/5482749.html",
        "summary": "音乐鉴赏人机制：按发现时间/升级幅度定义垂类品鉴人，用其收藏信号捞长尾，冷启动成功率从人工打分 3% 提升到 40%+。",
        "source_platform": "woshipm",
        "source_name": "人人都是产品经理",
        "topic_label": "冷启动",
    },
    # ── 歌单 / 播放列表 ─────────────────────────────────────
    {
        "title": "A Scalable Framework for Automatic Playlist Continuation（SIGIR'23）",
        "url": "https://arxiv.org/pdf/2304.09061v1.pdf",
        "summary": "Deezer 自动歌单续播（APC）框架：represent-then-aggregate 策略保证可扩展性，兼容 Transformer 等序列模型，上线 A/B 验证。",
        "source_platform": "arxiv",
        "source_name": "arXiv 2304.09061",
        "topic_label": "歌单生成",
    },
    {
        "title": "TalkPlay: Multimodal Music Recommendation with Large Language Models",
        "url": "https://arxiv.org/html/2502.13713v3/",
        "summary": "把音乐推荐重构成 LLM 的 next-token prediction：音乐用多模态 token 词汇（音频/歌词/元数据/标签/歌单共现）表示，端到端对话式推荐。",
        "source_platform": "arxiv",
        "source_name": "arXiv 2502.13713",
        "topic_label": "歌单生成",
    },
    {
        "title": "Learning When to Personalize: LLM Based Playlist Generation via Query Taxonomy",
        "url": "https://aclanthology.org/anthology-files/anthology-files/pdf/nlp4musa/2026.nlp4musa-1.8.pdf",
        "summary": "Zvuk 音乐平台：19 类查询分类器动态调节个性化强度，融合 LLM 语义打分与协同打分，盲测 ELO 显著优于固定个性化基线。",
        "source_platform": "aclanthology",
        "source_name": "NLP4MusA 2026",
        "topic_label": "歌单生成",
    },
    {
        "title": "Automatic, Personalized, and Flexible Playlist Generation Using RL",
        "url": "https://scispace.com/pdf/automatic-personalized-and-flexible-playlist-generation-1745v1blpb.pdf",
        "summary": "把播放列表生成当语言建模：attention 语言模型 + 策略梯度（RL）生成连贯有序歌单，兼顾个性化与可调节性。",
        "source_platform": "scispace",
        "source_name": "SciSpace",
        "topic_label": "歌单生成",
    },
    # ── 面试链路 ─────────────────────────────────────────────
    {
        "title": "推荐系统的召回、排序和重排链路如何理解（面经解析）",
        "url": "https://mianshidashi.cn/interview-questions/alibaba/algorithm-engineer/alibaba-algorithm-data-structures-c9353c21",
        "summary": "面试题解析：多路召回→粗排（轻模型）→精排（多任务深度模型）→重排（多样性/去重/频控/业务配额），60 秒回答模板+易错点。",
        "source_platform": "mianshidashi",
        "source_name": "面试大师",
        "topic_label": "面试链路",
    },
    {
        "title": "推荐策略产品经理必知必会：粗排、精排、重排模型",
        "url": "https://www.woshipm.com/share/6055285.html",
        "summary": "精排多目标排序公式 RankScore=a·P播+b·P赞+…；单点/成对/序列优化三种目标；内容场景以时长与互动为正反馈核心。",
        "source_platform": "woshipm",
        "source_name": "人人都是产品经理",
        "topic_label": "面试链路",
    },
]


def main() -> None:
    db = Database(DB_PATH)
    db.initialize()
    topic = db.get_topic_by_slug(TOPIC["slug"])
    if topic is None:
        topic_id = db.create_topic(**TOPIC)
    else:
        topic_id = topic["id"]
    new = dup = 0
    for item in ITEMS:
        item = {**item, "content_key": item["url"]}
        if db.add_topic_item(topic_id, item):
            new += 1
        else:
            dup += 1
    db.mark_topic_collected(topic_id)
    print(
        f"topic={TOPIC['slug']} id={topic_id} new={new} dup={dup} "
        f"total={db.count_topic_items(topic_id)}"
    )


if __name__ == "__main__":
    main()
