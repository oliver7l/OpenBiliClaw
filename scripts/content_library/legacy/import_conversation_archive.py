#!/usr/bin/env python3
"""把 2026-09-11 与用户的对话内容归档进 conversation_archive 表。

包含：
  - 用户发来的 11 条知乎链接（用户问题 + 链接 + 作者/赞/评论/日期等元数据）
  - 用 zhihu_cli 登录态提取的原文（markdown，存 extracted_original_md，逐字保留）
  - 我（AI）对每条的评析（my_analysis_md，按 洞见 / 可商榷 / 与你·时代 三栏）
  - 第 12 条：PD/Decode/yoco 架构讲解（concept_explain 类）

原文来自 /tmp 下本次会话提取的 md；缺失的 2 条（苏剑林、爱思考的大学生）已用同一登录态重新抓取。
分析文本为本会话复盘后重新撰写的结构化评析（原文口径逐字保留，分析为当次思路的忠实还原）。

用法:
  python3 scripts/import_conversation_archive.py
"""

import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DB_PATH = BASE / "data" / "openbiliclaw.db"
sys.path.insert(0, str(BASE / "src"))

from openbiliclaw.conversation_archive.store import ConversationArchiveStore  # noqa: E402


def load_original(path: Path, *, strip_html_comment: bool = False) -> str:
    text = path.read_text(encoding="utf-8")
    if strip_html_comment:
        text = re.sub(r"^<!--.*?-->\s*", "", text, flags=re.DOTALL)
    return text.strip()


# ── 11 条知乎 + 第 12 条架构讲解 的结构化数据 ──────────────────────
RECORDS = [
    {
        "seq": 1,
        "kind": "zhihu_eval",
        "user_question": "如何评价正式发布的 DeepSeek V4.1 Flash？ - 苏剑林的回答 - 知乎 https://www.zhihu.com/question/2081380378493961583/answer/2081768662713754653",
        "question_title": "如何评价正式发布的 DeepSeek V4.1 Flash？",
        "source_url": "https://www.zhihu.com/question/2081380378493961583/answer/2081768662713754653",
        "source_type": "answer",
        "author": "苏剑林",
        "headline": "科学空间博主 / 大模型研究者",
        "voteup_count": 197,
        "comment_count": 4,
        "published_at": "2026-09-11",
        "tags": ["DeepSeek", "大模型架构", "PD不对称", "KV Cache"],
        "orig_file": "/tmp/zhihu_ans1_sujianlin.md",
        "strip_comment": True,
        "my_analysis_md": """**【洞见】** 苏剑林一句话点透了 V4.1 Flash 真正的杠杆：PD（Prefill/Decode）不对称不是 DeepSeek 首创，而是所有 GPT 类模型天然就有的——Prefill 要算 Cache + 预测首 token，而后几层 MLP/LM Head 只为产出 token，跟 Cache 无关，所以 Prefill 天然可以少算。常规架构下这加速微乎其微（因为每层都还要算自己的 Cache）。

**【可商榷】** "微乎其微"是相对值。对超长 Prefill（百万 token）而言，即使每层只省一点，绝对延迟也相当可观；苏剑林的重点不在否认收益，而在指出收益的结构性来源——这点论述很克制，没有夸大。

**【与你·时代】** 这正是你当天追问的"行家看架构"范本：营销话术讲"KV Cache 降 75%"，行家讲"yoco 把本就存在的 PD 不对称放大成结构级优势"。做推荐/广告系统同理——真正的杠杆往往不是新发明，而是把已被忽视的不对称（如召回与排序的目标错位）做成结构性改造。面试讲腾讯微视时，也可以用这种"先点破常识、再讲结构性放大"的叙事。""",
    },
    {
        "seq": 2,
        "kind": "zhihu_eval",
        "user_question": "深圳为什么没能留住梁文峰、王兴兴、冯骥等人？是什么原因导致了他们的离开？为什么选择杭州？ - 爱思考的大学生的回答 - 知乎 https://www.zhihu.com/question/14820548710/answer/2078608756355625787",
        "question_title": "深圳为什么没能留住梁文峰、王兴兴、冯骥等人？",
        "source_url": "https://www.zhihu.com/question/14820548710/answer/2078608756355625787",
        "source_type": "answer",
        "author": "爱思考的大学生",
        "headline": "",
        "voteup_count": 3112,
        "comment_count": 200,
        "published_at": "2026-09-02",
        "tags": ["城市", "深圳", "杭州", "人才流动", "创业生态"],
        "orig_file": "/tmp/zhihu_ans2_shenzhen.md",
        "strip_comment": True,
        "my_analysis_md": """**【洞见】** 高赞回答把"留不住人"拆成可迁移成本问题：顶尖技术创业者要的是**耐心资本 + 容忍失败的氛围 + 产业链互补**，而不是补贴。深圳强在硬件供应链与速度，弱在"长期主义"的创投文化与高校科研腹地；杭州强在政府引导基金 + 浙大系 + 电商场景的闭环。

**【可商榷】** 把个体出走归因于城市"性格"有归因简化之嫌——梁/王/冯的离开更多是公司层面（大厂组织摩擦、融资节奏、个人志向），未必是"深圳留不住"。城市是必要条件非充分条件。

**【与你·时代】** 你正在深圳找工作（广告/推荐算法岗）。这篇的价值不在城市优劣，而在提醒：选平台看"它能否容忍你长期做难而正确的事"。深圳的硬科技速度适合落地型选手，但若你想深耕推荐系统的结构性创新（如 RecSys 那三条改进），要评估目标团队是否有耐心。城市叙事背后，是"组织是否给得起长期主义"。""",
    },
    {
        "seq": 3,
        "kind": "zhihu_eval",
        "user_question": "菽陌松囿的想法 - 知乎 https://www.zhihu.com/pin/2081527485318035411",
        "question_title": "v4.1 flash 把 1M 上下文 KV Cache 做到 1G",
        "source_url": "https://www.zhihu.com/pin/2081527485318035411",
        "source_type": "pin",
        "author": "菽陌松囿",
        "headline": "往来无白丁，谈笑有鸿儒。",
        "voteup_count": 78,
        "comment_count": 13,
        "published_at": "2026-09-11",
        "tags": ["KV Cache", "长上下文", "显存"],
        "orig_file": "/tmp/zhihu_pin.md",
        "strip_comment": False,
        "my_analysis_md": """**【洞见】** 一条想法把抽象数字落到体感：1M 上下文 KV Cache 压到 1G，意味着长文档/长代码库推理的显存门槛从"要 A100 集群"降到"单卡可跑"。这是 yoco（共享 Cache）最直接的工程红利——和当天苏剑林、绝密伏击的两条形成三角印证。

**【可商榷】** 1G 是 Prefill 后的 Cache 占用，Decode 阶段仍随生成增长；"1M→1G"是理想上限，实际 batch 推理要乘并发。想法没有给出吞吐数字，不能据此推断线上成本。

**【与你·时代】** 你维护的 OpenBiliClaw 若未来接长上下文（如整本已读库做 RAG），这类"Cache 占用量级"就是选型依据。做推荐系统的"用户长期兴趣画像"也类似——把长序列压成共享表征，正是 yoco 思想在召回侧的投影。""",
    },
    {
        "seq": 4,
        "kind": "zhihu_eval",
        "user_question": "Quant 未来会成为非常内卷的职业吗？ - oliver的回答 - 知乎 https://www.zhihu.com/question/399459312/answer/2030913502873957339",
        "question_title": "Quant 未来会成为非常内卷的职业吗？",
        "source_url": "https://www.zhihu.com/question/399459312/answer/2030913502873957339",
        "source_type": "answer",
        "author": "oliver",
        "headline": "Calpers 量化工程师，擅长量化多因子模型、高频算法",
        "voteup_count": 387,
        "comment_count": 0,
        "published_at": "2026-04-24",
        "tags": ["Quant", "量化", "职业", "内卷"],
        "orig_file": "/tmp/zhihu_ans4.md",
        "strip_comment": False,
        "my_analysis_md": """**【洞见】** 一线 Quant（Calpers 级别）的回答最冷静：卷的是"门槛低的量化岗"，真正稀缺的是**能把研究、工程、市场微观结构打通**的人。量化的护城河不是学历，是"实盘存活率"——回测漂亮、上线亏钱的人大量被淘汰。

**【可商榷】** 答主站在买方头部视角，对中小私募/私募量化的生存压力着墨少；整体行业"马太效应"比他描述的更残酷，新人入行窗口确实在收窄。

**【与你·时代】** 你走广告/推荐算法岗，和 Quant 是同源赛道（都是"用数据+优化赚 money"）。这篇的提醒直接可迁移：**别卷调参，卷"业务闭环理解"**——推荐系统里能说清"为什么这个特征涨了 GMV"的人，远比会堆模型的人稀缺。面试讲微视 AB 指标时，正是这种"实盘思维"的体现。""",
    },
    {
        "seq": 5,
        "kind": "zhihu_eval",
        "user_question": "一位中国父亲写给远在德国的儿子的一封信《儿子，别回来》，为何会被那么多人关注？ - 诗与星空的回答 - 知乎 https://www.zhihu.com/question/2080928445358401256/answer/2081386897713243511",
        "question_title": "《儿子，别回来》为何被那么多人关注？",
        "source_url": "https://www.zhihu.com/question/2080928445358401256/answer/2081386897713243511",
        "source_type": "answer",
        "author": "诗与星空",
        "headline": "以财报为核心的上市公司分析者",
        "voteup_count": 695,
        "comment_count": 0,
        "published_at": "2026-09-10",
        "tags": ["家庭", "教育", "海外", "时代情绪"],
        "orig_file": "/tmp/zhihu_ans5.md",
        "strip_comment": False,
        "my_analysis_md": """**【洞见】** 诗与星空用"财报视角"解构这封信的爆点：它戳中了一代父母与子女的**预期错位**——父辈的"别回来"不是不爱，是承认自己那套上升路径已失效，不愿子女重复自己的沉没成本。关注度高，是因为它替无数家庭说出了不敢说的真话。

**【可商榷】** 把个体家书上升为"时代缩影"有样本偏差——能写出、被转发的，本就是有条件送子女出国的家庭；对更广大的普通家庭，"别回来"是奢侈品式焦虑，未必普适。

**【与你·时代】** 你四段大厂经历后在深圳找工作，正是这封信暗面的同龄人：父辈路径失效、自己要在不确定中重选方向。它的价值是情绪样本——做内容推荐时，"代际预期错位"是高频共鸣主题；做心理类推荐（积极心理学早安推送）也可借这种"被说中"的钩子。""",
    },
    {
        "seq": 6,
        "kind": "zhihu_eval",
        "user_question": "如果人人都可以通过 AI 写代码，程序员还需要存在吗？未来的程序员的工作会是什么？ - Rainchester的回答 - 知乎 https://www.zhihu.com/question/2077824745589028563/answer/2079669249254150636",
        "question_title": "AI 写代码后，程序员还需要存在吗？",
        "source_url": "https://www.zhihu.com/question/2077824745589028563/answer/2079669249254150636",
        "source_type": "answer",
        "author": "Rainchester",
        "headline": "中央对手方 Risk quant｜统计博",
        "voteup_count": 12,
        "comment_count": 0,
        "published_at": "2026-09-05",
        "tags": ["AI编程", "程序员", "职业", "LLM"],
        "orig_file": "/tmp/zhihu_ans6.md",
        "strip_comment": False,
        "my_analysis_md": """**【洞见】** Rainchester 的视角很"风控"：AI 把"写代码"降级为商品，程序员的价值上移到**定义问题 + 验收正确性 + 对系统负责**。未来稀缺的是能区分"代码能跑"和"代码对该跑的事负责"的人——这正是 quant/Risk 思维的通用的方。

**【可商榷】** 答主赞数低（12），说明立场偏"精英程序员"叙事，对大量 CRUD 岗被替代的阵痛着墨少；现实中"会提问 + 会验收"的门槛也不低，中间层程序员仍会被挤压。

**【与你·时代】** 你用 WorkBuddy/agent 写代码已是常态。这篇印证你的实践方向：**把自己当"问题定义者+验收者"而非"打字员"**。你做的 OpenBiliClaw 多批次迭代、根因分析不表面修补，正是"对系统负责"的体现——这是 AI 替代不了的那层。""",
    },
    {
        "seq": 7,
        "kind": "zhihu_eval",
        "user_question": "给大领导汇报工作，领导经常性抓到一个点就使劲往细节问，直到问答不上，该如何应对呢？这种现象正常吗？ - 成子崖的回答 - 知乎 https://www.zhihu.com/question/2046602314325268215/answer/2062613943391196512",
        "question_title": "领导抓细节问到答不上，如何应付？",
        "source_url": "https://www.zhihu.com/question/2046602314325268215/answer/2062613943391196512",
        "source_type": "answer",
        "author": "成子崖",
        "headline": "何必见，见已构成",
        "voteup_count": 3094,
        "comment_count": 0,
        "published_at": "2026-07-20",
        "tags": ["职场", "汇报", "沟通", "向上管理"],
        "orig_file": "/tmp/zhihu_ans7.md",
        "strip_comment": False,
        "my_analysis_md": """**【洞见】** 高赞（3094）点破：领导抓细节不是"为难"，是**验证你到底懂不懂**——他用一个点穿透你的认知边界，判断这份工作是你做的还是别人做的。应对不是"答上来"，是"承认边界 + 给出补齐路径"，把审问变成信任建立。

**【可商榷】** 这套适用于"善意但严格"的领导；对恶意 PUA 式追问，答主的"坦诚+补齐"未必奏效，需区分领导动机。把一切归为"正常考察"会误判毒性环境。

**【与你·时代】** 你面试讲腾讯微视推荐系统时，本质上也在被"抓细节问"——AB 指标怎么算、图嵌入为何选、Thompson Sampling 怎么平衡。成子崖的框架直接可用：**边界处坦诚 + 立刻给方法**，比硬编更赢得信任。你"看代码最实在"的偏好，正是经得起这种穿透的底气。""",
    },
    {
        "seq": 8,
        "kind": "zhihu_eval",
        "user_question": "KV Cache 降低 75%：DeepSeek-V4.1-Flash 全新架构解读 - 绝密伏击的文章 - 知乎 https://zhuanlan.zhihu.com/p/2081403774740980801",
        "question_title": "KV Cache 降低 75%：DeepSeek-V4.1-Flash 全新架构解读",
        "source_url": "https://zhuanlan.zhihu.com/p/2081403774740980801",
        "source_type": "article",
        "author": "绝密伏击",
        "headline": "《揭秘大模型：从原理到实战》《推荐系统技术原理与实践》作者",
        "voteup_count": 50,
        "comment_count": 4,
        "published_at": "2026-09-10",
        "tags": ["DeepSeek", "KV Cache", "架构解读", "yoco"],
        "orig_file": "/tmp/zhihu_article.md",
        "strip_comment": False,
        "my_analysis_md": """**【洞见】** 绝密伏击做了最完整的工程拆解：KV Cache 降 75% 来自 yoco（You Only Cache Once）——各层共享同一份 Cache，Prefill 主计算只需算到最后一层 Cache 即止。配合当天苏剑林（PD 不对称原理）与菽陌松囿（1M→1G 体感），三条拼成完整技术图谱。

**【可商榷】** 专栏文章偏"技术布道"，对 yoco 的代价（共享 Cache 是否损失层间表达差异、长程依赖是否退化）讨论不足；"降 75%"是特定配置下的数字，非普适结论。

**【与你·时代】** 你正把 OpenBiliClaw 的 soul 模块切到本地 Ollama。这类架构解读告诉你：**端侧推理的瓶颈在 Cache 不在算力**。若未来让 soul 跑长上下文（整库偏好），yoco 式共享表征就是必须关注的优化方向。技术阅读要连成网，不能孤立看单篇。""",
    },
    {
        "seq": 9,
        "kind": "zhihu_eval",
        "user_question": "如何看待红果短剧日活1.68亿已超「爱优腾芒」四家总和？为啥大众会在影视娱乐上出现这么强烈的偏好转变？ - 曹多鱼的回答 - 知乎 https://www.zhihu.com/question/2081408366807442163/answer/2081686124305576061",
        "question_title": "红果短剧日活超爱优腾芒总和，偏好为何剧变？",
        "source_url": "https://www.zhihu.com/question/2081408366807442163/answer/2081686124305576061",
        "source_type": "answer",
        "author": "曹多鱼",
        "headline": "知识星球博主",
        "voteup_count": 517,
        "comment_count": 0,
        "published_at": "2026-09-11",
        "tags": ["红果短剧", "消费", "影视", "注意力经济"],
        "orig_file": "/tmp/zhihu_ans9.md",
        "strip_comment": False,
        "my_analysis_md": """**【洞见】** 曹多鱼把"1.68 亿日活"拆成供给侧革命：短剧不是内容升级，是**分发效率 + 单集成本**的降维——每分钟一个钩子、算法精准投喂，单位注意力的获取成本远低于长视频。传统长视频的"会员+广告"模型在"极短反馈循环"面前结构性失利。

**【可商榷】** 日活超四家总和有"口径游戏"嫌疑（短剧免登录、小程序即点即看，DAU 统计更宽松）；且"偏好转变"可能含短期新鲜感，未必是永久迁移。

**【与你·时代】** 你做推荐系统，这篇是活教材：**反馈循环的快慢决定分发效率**。红果赢在"秒级完播信号"喂给推荐模型，而长视频的"看完一集"信号太稀疏。你给 OpenBiliClaw 设计的"兴趣双重任务探索分支"，正是要在稀疏信号里补探索——短剧的胜利反衬出你问题的价值。""",
    },
    {
        "seq": 10,
        "kind": "zhihu_eval",
        "user_question": "人与人相处什么最重要？ - 马超的回答 - 知乎 https://www.zhihu.com/question/2041921645321917318/answer/2081477453915149991",
        "question_title": "人与人相处什么最重要？",
        "source_url": "https://www.zhihu.com/question/2041921645321917318/answer/2081477453915149991",
        "source_type": "answer",
        "author": "马超",
        "headline": "九紫离火",
        "voteup_count": 961,
        "comment_count": 0,
        "published_at": "2026-09-10",
        "tags": ["人际", "相处", "心智", "边界"],
        "orig_file": "/tmp/zhihu_ans10.md",
        "strip_comment": False,
        "my_analysis_md": """**【洞见】** 马超（961 赞）的核心就一句：**不轻易向任何人输出观点，先听懂对方要什么**。相处最重要不是"真诚表达"，是"克制表达 + 精准回应"。这和成子崖的"汇报被抓细节"同源——都是"先确认对方坐标系，再开口"。

**【可商榷】** "不输出观点"若推极端会变冷漠/油滑；健康相处需要适度自我暴露建立信任。答主偏"防御性相处"，对亲密关系未必全适用。

**【与你·时代】** 你性格内向、追求自信与成长。这篇的"先听后说"恰好是内向者的优势打法——不必强行外向，靠"听懂需求"建立关系。你做的积极心理学早安推送、和我的对话风格（结论先行、结构化），也是"先确认对方要什么再给"的实践。""",
    },
    {
        "seq": 11,
        "kind": "zhihu_eval",
        "user_question": "那些老干部为啥都长寿？ - 海风的回答 - 知乎 https://www.zhihu.com/question/24992247/answer/2081388209511076603",
        "question_title": "那些老干部为啥都长寿？",
        "source_url": "https://www.zhihu.com/question/24992247/answer/2081388209511076603",
        "source_type": "answer",
        "author": "海风",
        "headline": "忍把浮名，换了浅斟低唱",
        "voteup_count": 788,
        "comment_count": 0,
        "published_at": "2026-09-10",
        "tags": ["健康", "长寿", "身体", "生活方式"],
        "orig_file": "/tmp/zhihu_ans11.md",
        "strip_comment": False,
        "my_analysis_md": """**【洞见】** 海风（788 赞）把长寿归因到"可控的低应激"：老干部群体普遍有**规律作息 + 社会角色带来的秩序感 + 不过度消耗情绪**的生活习惯。长寿不是玄学，是"长期低波动"的生理复利。

**【可商榷】** 样本有强选择性偏差——能活到被讨论的"老干部"本就是健康幸存者；且医疗/经济资源是混淆变量，不能全归给"心态"。

**【与你·时代】** 你关注心理学/情绪管理（28 本），这篇是身体版注脚：**情绪消耗是隐形成本**。你做的冥想空间 App、积极心理学推送，和"低应激长寿"是同一目标的不同入口。求职焦虑期尤其适用——把"长期低波动"当工程指标来管理，比临时鸡血更可持续。""",
    },
    {
        "seq": 12,
        "kind": "concept_explain",
        "user_question": "Prefill是什么，Decode是什么，他的核心洞察很准：PD（Prefill/Decode）不对称不是 DeepSeek 首创，而是所有 GPT 类模型天然就有的——Prefill 要\"算 Cache + 预测首 token\"，后几层 MLP/LM Head 只为产出 token，跟 Cache 无关，所以 Prefill 天然可以少算。这点常规架构下加速微乎其微，因为每层都还要算自己的 Cache。V4.1 Flash 的巧思在于 yoco（You Only Cache Once，Meta 2024 的 Cache 共享思路）：让各层共享同一份 Cache，Prefill 主计算只需截止到最后一层 Cache 就够，把那个\"微乎其微\"放大成了结构级优势。这是行家看架构，一句点透了营销话术背后的真实杠杆。，这个能讲清楚一些吗，还挺感兴趣的",
        "question_title": "Prefill / Decode 与 yoco：PD 不对称为何是结构级杠杆",
        "source_url": "",
        "source_type": "",
        "author": "OpenBiliClaw（讲解）",
        "headline": "架构讲解",
        "voteup_count": 0,
        "comment_count": 0,
        "published_at": "2026-09-11",
        "tags": ["PD不对称", "yoco", "KV Cache", "推理架构", "Prefill", "Decode"],
        "orig_file": None,
        "strip_comment": False,
        "my_analysis_md": """## Prefill 与 Decode：Transformer 推理的两个阶段

自回归生成一句话要跑两遍不同的活：

- **Prefill（预填充）**：把整段输入 prompt 一次性喂进模型，算出每个 token 的隐藏状态，并**生成 KV Cache**（每层注意力键/值）。这一步是"算 Cache + 预测出第一个 token"，计算密集、可并行。
- **Decode（解码）**：拿着 Prefill 产出的 KV Cache，一个个吐出后续 token。每吐一个，只需算**当前这个新 token 对自己那层的计算 + 一次注意力读取**——后面几层 MLP/LM Head 只为"产出这一个 token"，和"为后续 token 算 Cache"无关。

## PD 不对称：本就存在，只是被忽略

关键洞察：**Prefill 阶段，模型其实"顺便"把后续 Decode 要用的 Cache 也算好了**。严格说，Prefill 里"为产出后续 token 而算的 Cache"是必要成本，但"Prefill 末尾几层只为生成首 token 的那点额外计算"跟 Cache 无关——这部分在 Prefill 里可以少算。

问题在于：常规架构**每一层都有自己的独立 KV Cache**，所以"少算"只能省末尾几层的微薄计算，加速微乎其微（苏剑林说的"微乎其微"）。

## yoco：把"微乎其微"放大成结构级优势

Meta 2024 的 **yoco（You Only Cache Once）** 思路：不让每层各存一份 Cache，而是**让所有层共享同一份 Cache**（只在最后一层生成一次）。这样：

- Prefill 主计算**只需算到"生成那唯一一份共享 Cache"就结束**，不必为每一层都重复算 Cache；
- 后续层直接读这份共享 Cache 做注意力，省掉了"每层各算各的 Cache"的重复开销。

于是原本"每层各算 Cache → 省不掉"的死结，变成"只算一次共享 Cache → 整段 Prefill 大幅瘦身"。**KV Cache 占用从"层数 × 单层"降到"单层"量级**（绝密伏击说的"降 75%"、菽陌松囿说的"1M→1G"正是这个量级）。

## 一句话收束

> PD 不对称不是 DeepSeek 发明的新物理，是所有 GPT 类模型天生的"可优化不对称"；V4.1 Flash 的巧思是用 yoco 把这份"天生的、却一直被每层独立 Cache 稀释掉"的红利，做成了**结构级**的加速。

这正对应你当天读到的三篇：苏剑林点破原理、绝密伏击给工程拆解、菽陌松囿给体感数字——合起来才是"行家看架构"。""",
    },
]


def main() -> None:
    store = ConversationArchiveStore(db_path=DB_PATH)
    ok = 0
    for rec in RECORDS:
        orig = ""
        if rec.get("orig_file"):
            p = Path(rec["orig_file"])
            if p.exists():
                orig = load_original(p, strip_html_comment=rec.get("strip_comment", False))
        payload = {
            "seq": rec["seq"],
            "kind": rec["kind"],
            "user_question": rec["user_question"],
            "question_title": rec.get("question_title", ""),
            "source_url": rec.get("source_url", ""),
            "source_type": rec.get("source_type", ""),
            "author": rec.get("author", ""),
            "headline": rec.get("headline", ""),
            "voteup_count": rec.get("voteup_count", 0),
            "comment_count": rec.get("comment_count", 0),
            "published_at": rec.get("published_at", ""),
            "tags": rec.get("tags", []),
            "extracted_original_md": orig,
            "my_analysis_md": rec.get("my_analysis_md", ""),
        }
        item_id = store.upsert_item(payload)
        print(f"  seq={rec['seq']:>2} id={item_id} author={rec.get('author',''):<14} original_chars={len(orig):>6} analysis_chars={len(payload['my_analysis_md']):>5}")
        ok += 1
    stats = store.get_stats()
    print(f"\n导入完成：{ok} 条；库内总计 {stats['total']} 条；含原文 {stats['with_original']} / 含分析 {stats['with_analysis']}")
    print("by_kind:", json.dumps(stats["by_kind"], ensure_ascii=False))


if __name__ == "__main__":
    main()
