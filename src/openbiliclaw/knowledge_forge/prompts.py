"""Knowledge Forge 提示词模板（文档附录 B）。

所有模板遵循同一条硬约束：**不要添加原文没有的信息**。
"""

from __future__ import annotations

DETAILED_SUMMARY_PROMPT = """请为以下文章生成详细摘要，要求：
1. 包含核心观点、关键数据、结构梳理
2. 按文章逻辑组织，分点列出
3. 保留重要的原文引用（用引号标注）
4. 字数控制在 2000-5000 字
5. 不要添加文章中没有的信息

文章标题：{title}
文章作者：{author}
文章正文：
{content}
"""

COMPACT_SUMMARY_PROMPT = """请将以下详细摘要压缩为精简版，要求：
1. 保留核心观点和关键数据
2. 列出 3-5 个要点
3. 字数控制在 500-1000 字
4. 不要添加新信息

详细摘要：
{detailed_summary}
"""

ULTRA_COMPACT_SUMMARY_PROMPT = """请将以下摘要压缩为超精简版，要求：
1. 一句话概括文章核心内容
2. 列出 3-5 个核心标签
3. 总字数控制在 50-200 字

摘要：
{compact_summary}
"""

SUMMARY_QUALITY_PROMPT = """请评估以下三层摘要是否准确覆盖原文核心，只输出一个 0-1 之间的浮点数（保留两位），不要输出其他内容。

原文前 1500 字：
{content_head}

详细摘要：
{detailed}

精简摘要：
{compact}
"""

TOPIC_EXTRACT_PROMPT = """请从以下文章中提取 2-5 个核心主题（词组，不要长句），
要求：
1. 主题应体现文章所属领域或讨论对象，如"推荐系统""广告出价""量化回测"
2. 优先复用已有主题列表中的写法（若语义相同则直接复用原词）：{existing_topics}
3. 只输出 JSON 数组，如 ["主题1","主题2"]，不要输出其他内容

文章标题：{title}
文章正文前 3000 字：
{content}
"""

CONCEPT_EXTRACT_PROMPT = """请从以下文章中提取 3-8 个技术概念/方法/框架（如 TokenFormer、Thompson Sampling、Point-in-Time），
要求：
1. 只提取具体的、可复用的技术概念，不要提取泛泛的领域词
2. 只输出 JSON 数组，如 ["概念1","概念2"]，不要输出其他内容

文章标题：{title}
文章正文前 3000 字：
{content}
"""

ENTITY_DESCRIPTION_PROMPT = """请为实体「{name}」（类型：{type}）写一段 100-200 字的简介，
基于以下引用它的文章摘要综合而成。不要编造摘要之外的信息。

摘要列表：
{summaries}
"""

CONTRADICTION_PROMPT = """判断以下两篇同主题文章的观点是否存在实质矛盾。
只输出 JSON：{{"contradiction": true/false, "confidence": 0-1, "description": "矛盾点说明"}}

主题：{topic}
文章A摘要：{summary_a}
文章B摘要：{summary_b}
"""

LOW_QUALITY_DETECT_PROMPT = """判断以下正文是否属于低质量内容（广告/垃圾/正文与标题不符/无意义文本）。
只输出 JSON：{{"low_quality": true/false, "reason": "原因"}}

标题：{title}
正文前 1500 字：
{content}
"""

TAG_SUPPLEMENT_PROMPT = """请为以下文章补充 3-6 个标签，只输出 JSON 数组，如 ["标签1","标签2"]。

文章标题：{title}
文章正文前 2000 字：
{content}
"""

GAP_SUGGESTION_PROMPT = """以下是知识库缺口分析结果，请为每个缺口生成具体的补充建议（补充什么内容、去哪个平台找、关键词是什么）。
输出 Markdown 列表。

缺口列表：
{gaps}
"""


def parse_json_array(raw: str) -> list[str]:
    """从 LLM 输出中解析出字符串数组（容忍 markdown 代码围栏与多余文字）。"""
    import json
    import re

    if not raw:
        return []
    text = raw.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    m = re.search(r"\[.*\]", text, flags=re.S)
    if m:
        text = m.group(0)
    try:
        data = json.loads(text)
    except Exception:  # noqa: BLE001
        return []
    if isinstance(data, list):
        return [str(x).strip() for x in data if str(x).strip()]
    return []
