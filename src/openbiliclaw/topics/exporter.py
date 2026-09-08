"""专题结构化导出、内容去重与跨平台融合。

功能：
1. 结构化导出：把数据库中的专题内容导出为 data/topics/<slug>/ 文件结构，
   可被外部 AI 直接读取（TOPIC.md + metadata.json + sources/<platform>/）。
2. SHA1 指纹去重：入库前计算内容指纹，避免重复入库。
3. 跨平台融合：对专题内容做关键词提取，找交叉主题锚点，生成融合草稿。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger("topics.exporter")

# ── 停用词（中文常见无意义词）────────────────────────────────────────────
_STOP_WORDS: set[str] = {
    "的",
    "了",
    "在",
    "是",
    "我",
    "有",
    "和",
    "就",
    "不",
    "人",
    "都",
    "一",
    "一个",
    "上",
    "也",
    "很",
    "到",
    "说",
    "要",
    "去",
    "你",
    "会",
    "着",
    "没有",
    "看",
    "好",
    "自己",
    "这",
    "那",
    "他",
    "她",
    "它",
    "们",
    "这个",
    "那个",
    "什么",
    "怎么",
    "为什么",
    "可以",
    "因为",
    "所以",
    "但是",
    "如果",
    "虽然",
    "然后",
    "就是",
    "还是",
    "或者",
    "以及",
    "并且",
    "而且",
    "不过",
    "只是",
    "已经",
    "正在",
    "将要",
    "可能",
    "应该",
    "需要",
    "知道",
    "觉得",
    "认为",
    "其实",
    "真的",
    "确实",
    "当然",
    "显然",
    "毕竟",
    "总之",
    "另外",
    "此外",
    "比如",
    "例如",
    "关于",
    "对于",
    "通过",
    "根据",
    "按照",
    "由于",
    "基于",
    "the",
    "a",
    "an",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "could",
    "should",
    "may",
    "might",
    "must",
    "shall",
    "can",
    "need",
    "dare",
    "to",
    "of",
    "in",
    "for",
    "on",
    "with",
    "at",
    "by",
    "from",
    "as",
    "into",
    "through",
    "during",
    "before",
    "after",
    "above",
    "below",
    "and",
    "but",
    "or",
    "nor",
    "not",
    "so",
    "yet",
    "both",
    "either",
    "this",
    "that",
    "these",
    "those",
    "it",
    "its",
    "they",
    "them",
    "i",
    "me",
    "my",
    "we",
    "us",
    "our",
    "you",
    "your",
    "he",
    "him",
    "she",
    "her",
    "what",
    "which",
    "who",
    "whom",
    "when",
    "where",
    "how",
    "all",
    "each",
    "every",
    "few",
    "more",
    "most",
    "other",
    "some",
    "such",
    "no",
    "only",
    "own",
    "same",
    "than",
    "too",
    "very",
    "just",
    "also",
    "now",
    "here",
    "there",
    "then",
}


# ── 数据结构 ──────────────────────────────────────────────────────────────


@dataclass
class TopicItem:
    """专题中的一条内容。"""

    content_key: str
    title: str
    url: str
    source_platform: str
    source_name: str = ""
    summary: str = ""
    content_text: str = ""
    topic_label: str = ""
    collected_at: str = ""


@dataclass
class FusionResult:
    """跨平台融合结果。"""

    topic_slug: str
    cross_platform_keywords: list[tuple[str, int]] = field(default_factory=list)
    per_platform_keywords: dict[str, list[tuple[str, int]]] = field(default_factory=dict)
    anchor_points: list[str] = field(default_factory=list)
    draft_markdown: str = ""


# ── SHA1 指纹 ─────────────────────────────────────────────────────────────


def compute_content_hash(text: str) -> str:
    """计算内容的 SHA1 指纹，用于去重。

    归一化处理：去除首尾空白、压缩连续空白、统一换行符，
    避免因格式差异导致同一内容被判定为不同。
    """
    if not text:
        return ""
    normalized = re.sub(r"\s+", " ", text.strip())
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def is_duplicate(conn: Any, content_hash: str) -> bool:
    """检查 articles 表中是否已存在相同指纹的内容。

    conn 是 sqlite3 连接对象。需要 articles 表已有 content_hash 列
    （由 ensure_content_hash_column 确保）。
    """
    if not content_hash:
        return False
    try:
        row = conn.execute(
            "SELECT id FROM articles WHERE content_hash = ? LIMIT 1",
            (content_hash,),
        ).fetchone()
        return row is not None
    except Exception:
        # 如果列不存在或查询失败，不阻断入库，返回 False
        logger.debug("content_hash check failed, proceeding without dedup")
        return False


def ensure_content_hash_column(conn: Any) -> bool:
    """确保 articles 表有 content_hash 列，不存在则添加。

    conn 是 sqlite3 连接对象。返回 True 表示列已存在或刚添加成功。
    """
    try:
        cols = conn.execute("PRAGMA table_info(articles)").fetchall()
        has_col = any(col[1] == "content_hash" for col in cols)
        if not has_col:
            conn.execute("ALTER TABLE articles ADD COLUMN content_hash TEXT DEFAULT ''")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_articles_content_hash ON articles(content_hash)"
            )
            conn.commit()
            logger.info("Added content_hash column and index to articles table")
        return True
    except Exception as e:
        logger.error("Failed to ensure content_hash column: %s", e)
        return False


# ── 关键词提取 ─────────────────────────────────────────────────────────────


def extract_keywords(text: str, top_k: int = 20) -> list[tuple[str, int]]:
    """从文本中提取关键词（简单词频统计 + 停用词过滤）。

    支持中英文混合：中文按 2-4 字滑窗，英文按单词。
    返回 [(keyword, count), ...] 按词频降序。
    """
    if not text:
        return []

    counter: Counter[str] = Counter()

    # 英文单词
    for word in re.findall(r"[a-zA-Z][a-zA-Z0-9+#.-]{1,}", text.lower()):
        if word not in _STOP_WORDS and len(word) > 1:
            counter[word] += 1

    # 中文 2-4 字词组（简单滑窗）
    chinese_segments = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    for seg in chinese_segments:
        for n in (2, 3, 4):
            for i in range(len(seg) - n + 1):
                word = seg[i : i + n]
                if word not in _STOP_WORDS:
                    counter[word] += 1

    # 过滤掉只出现一次且长度为2的中文词（噪声太多）
    filtered = [(w, c) for w, c in counter.most_common(top_k * 3) if c >= 2 or len(w) >= 3]
    return filtered[:top_k]


# ── 跨平台融合 ─────────────────────────────────────────────────────────────


def fuse_topic_content(
    topic_slug: str,
    items: list[TopicItem],
    top_keywords: int = 30,
    min_platforms_for_anchor: int = 2,
) -> FusionResult:
    """对专题内容做跨平台融合分析。

    步骤：
    1. 按平台分组，分别提取关键词
    2. 找交叉平台关键词（出现在 >= min_platforms_for_anchor 个平台）
    3. 生成融合草稿 markdown
    """
    result = FusionResult(topic_slug=topic_slug)

    if not items:
        result.draft_markdown = f"# {topic_slug} 跨平台融合草稿\n\n暂无内容。\n"
        return result

    # 按平台分组
    by_platform: dict[str, list[TopicItem]] = defaultdict(list)
    for item in items:
        platform = item.source_platform or "unknown"
        by_platform[platform].append(item)

    # 各平台关键词
    platform_keyword_counter: dict[str, Counter[str]] = {}
    for platform, platform_items in by_platform.items():
        combined_text = " ".join(
            f"{item.title} {item.summary} {item.content_text[:2000]}" for item in platform_items
        )
        kws = extract_keywords(combined_text, top_k=top_keywords)
        result.per_platform_keywords[platform] = kws
        platform_keyword_counter[platform] = Counter(dict(kws))

    # 交叉平台关键词（出现在多个平台）
    all_keyword_platforms: dict[str, set[str]] = defaultdict(set)
    all_keyword_score: dict[str, int] = defaultdict(int)
    for platform, counter in platform_keyword_counter.items():
        for keyword, count in counter.items():
            all_keyword_platforms[keyword].add(platform)
            all_keyword_score[keyword] += count

    cross_platform = [
        (kw, all_keyword_score[kw])
        for kw in all_keyword_platforms
        if len(all_keyword_platforms[kw]) >= min_platforms_for_anchor
    ]
    cross_platform.sort(key=lambda x: x[1], reverse=True)
    result.cross_platform_keywords = cross_platform[:20]

    # 锚点：交叉关键词中出现平台数最多的
    anchors = sorted(
        all_keyword_platforms.items(),
        key=lambda x: (len(x[1]), all_keyword_score[x[0]]),
        reverse=True,
    )
    result.anchor_points = [kw for kw, _ in anchors[:10]]

    # 生成融合草稿
    result.draft_markdown = _build_fusion_draft(topic_slug, by_platform, result)

    return result


def _build_fusion_draft(
    topic_slug: str,
    by_platform: dict[str, list[TopicItem]],
    fusion: FusionResult,
) -> str:
    """生成融合草稿 markdown。"""
    lines: list[str] = []
    lines.append(f"# {topic_slug} 跨平台融合草稿")
    lines.append("")
    lines.append(
        f"> 自动生成于跨平台内容融合分析，覆盖 {len(by_platform)} 个平台，"
        f"共 {sum(len(v) for v in by_platform.values())} 条内容。"
    )
    lines.append("")

    # 交叉主题锚点
    if fusion.anchor_points:
        lines.append("## 🔗 交叉主题锚点")
        lines.append("")
        lines.append("以下概念在多个平台的内容中都被高频提及，是跨平台融合的核心锚点：")
        lines.append("")
        for i, anchor in enumerate(fusion.anchor_points[:10], 1):
            lines.append(f"{i}. **{anchor}**")
        lines.append("")

    # 交叉平台关键词
    if fusion.cross_platform_keywords:
        lines.append("## 📊 跨平台高频关键词")
        lines.append("")
        lines.append("| 关键词 | 累计出现次数 |")
        lines.append("|---|---|")
        for kw, count in fusion.cross_platform_keywords[:15]:
            lines.append(f"| {kw} | {count} |")
        lines.append("")

    # 各平台视角
    lines.append("## 👁️ 各平台视角")
    lines.append("")
    for platform, items in sorted(by_platform.items()):
        lines.append(f"### {platform}（{len(items)} 条）")
        lines.append("")
        kws = fusion.per_platform_keywords.get(platform, [])
        if kws:
            kw_str = "、".join(f"**{kw}**({cnt})" for kw, cnt in kws[:8])
            lines.append(f"**高频关键词**：{kw_str}")
            lines.append("")
        # 列出该平台的内容标题
        lines.append("**内容清单**：")
        lines.append("")
        for item in items[:10]:
            title = item.title.replace("|", "\\|")[:80]
            lines.append(f"- [{title}]({item.url})")
        if len(items) > 10:
            lines.append(f"- ……还有 {len(items) - 10} 条")
        lines.append("")

    # 融合建议
    lines.append("## 💡 融合建议")
    lines.append("")
    lines.append("1. 以交叉主题锚点为骨架，整合各平台对同一概念的不同视角")
    lines.append("2. 优先融合高频关键词对应的内容，这些是专题的核心议题")
    lines.append(
        "3. 注意各平台的内容风格差异：知乎偏深度长文、小红书偏实用笔记、"
        "B站偏视频教程、V2EX偏技术讨论"
    )
    lines.append("4. 融合后的内容可进一步蒸馏为方法论 Skill 或专题速查手册")
    lines.append("")

    return "\n".join(lines)


# ── 结构化导出 ─────────────────────────────────────────────────────────────


def export_topic_to_files(
    topic: dict[str, Any],
    items: list[TopicItem],
    output_root: Path,
    *,
    include_content: bool = True,
    run_fusion: bool = True,
) -> dict[str, Any]:
    """把专题内容导出为结构化文件目录。

    目录结构：
        output_root/
        ├── INDEX.md                          # 所有专题的主目录
        ├── AGENTS.md                         # 外部 AI 读取指南
        └── <slug>/
            ├── TOPIC.md                      # 专题概览（自动生成）
            ├── metadata.json                 # 机器可读元数据
            ├── fusion_draft.md               # 跨平台融合草稿（如 run_fusion=True）
            └── sources/
                ├── <platform1>/
                │   ├── <content_key>.md      # 单条内容存档
                │   └── ...
                └── <platform2>/
                    └── ...

    返回导出统计信息。
    """
    slug = topic.get("slug", "unknown")
    name = topic.get("name", slug)
    topic_dir = output_root / slug
    sources_dir = topic_dir / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)

    stats = {
        "slug": slug,
        "name": name,
        "total_items": len(items),
        "exported_items": 0,
        "skipped_empty": 0,
        "platforms": {},
    }

    # 按平台分组导出单条内容
    by_platform: dict[str, list[TopicItem]] = defaultdict(list)
    for item in items:
        platform = item.source_platform or "unknown"
        by_platform[platform].append(item)

        platform_dir = sources_dir / _safe_dirname(platform)
        platform_dir.mkdir(parents=True, exist_ok=True)

        if include_content and (item.content_text or item.summary):
            content_md = _build_item_markdown(item)
            safe_key = _safe_filename(item.content_key)
            content_path = platform_dir / f"{safe_key}.md"
            content_path.write_text(content_md, encoding="utf-8")
            stats["exported_items"] += 1
        else:
            stats["skipped_empty"] += 1

    for platform, platform_items in by_platform.items():
        stats["platforms"][platform] = len(platform_items)

    # 跨平台融合
    fusion: FusionResult | None = None
    if run_fusion and items:
        fusion = fuse_topic_content(slug, items)
        fusion_path = topic_dir / "fusion_draft.md"
        fusion_path.write_text(fusion.draft_markdown, encoding="utf-8")

    # metadata.json
    metadata = {
        "slug": slug,
        "name": name,
        "description": topic.get("description", ""),
        "keywords": topic.get("keywords", []),
        "platforms": list(by_platform.keys()),
        "item_count": len(items),
        "exported_items": stats["exported_items"],
        "created_at": topic.get("created_at", ""),
        "updated_at": topic.get("updated_at", ""),
        "last_collected_at": topic.get("last_collected_at", ""),
        "per_platform_counts": stats["platforms"],
        "cross_platform_keywords": fusion.cross_platform_keywords if fusion else [],
        "anchor_points": fusion.anchor_points if fusion else [],
    }
    (topic_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # TOPIC.md
    topic_md = _build_topic_overview(name, topic, items, by_platform, fusion)
    (topic_dir / "TOPIC.md").write_text(topic_md, encoding="utf-8")

    # 更新全局 INDEX.md 和 AGENTS.md
    _update_global_index(output_root)
    _update_agents_guide(output_root)

    return stats


def _build_item_markdown(item: TopicItem) -> str:
    """把单条内容转为 markdown 存档。"""
    lines = [
        f"# {item.title}",
        "",
        f"- **来源平台**: {item.source_platform}",
        f"- **来源名称**: {item.source_name}",
        f"- **URL**: {item.url}",
        f"- **内容标识**: {item.content_key}",
        f"- **专题标签**: {item.topic_label}",
        f"- **收录时间**: {item.collected_at}",
        "",
    ]
    if item.summary:
        lines.append("## 摘要")
        lines.append("")
        lines.append(item.summary)
        lines.append("")
    if item.content_text:
        lines.append("## 正文")
        lines.append("")
        lines.append(item.content_text)
        lines.append("")
    return "\n".join(lines)


def _build_topic_overview(
    name: str,
    topic: dict[str, Any],
    items: list[TopicItem],
    by_platform: dict[str, list[TopicItem]],
    fusion: FusionResult | None,
) -> str:
    """生成专题概览 TOPIC.md。"""
    lines = [
        f"# {name}",
        "",
        f"> {topic.get('description', '暂无描述')}",
        "",
        f"**内容总数**: {len(items)} 条  ",
        f"**覆盖平台**: {', '.join(sorted(by_platform.keys()))}  ",
        f"**最后收录**: {topic.get('last_collected_at', '未知')}",
        "",
    ]

    # 各平台统计
    lines.append("## 📊 平台分布")
    lines.append("")
    lines.append("| 平台 | 内容数 |")
    lines.append("|---|---|")
    for platform, count in sorted(by_platform.items(), key=lambda x: len(x[1]), reverse=True):
        lines.append(f"| {platform} | {count} |")
    lines.append("")

    # 交叉锚点
    if fusion and fusion.anchor_points:
        lines.append("## 🔗 交叉主题锚点")
        lines.append("")
        lines.append("在多个平台内容中被高频提及的核心概念：")
        lines.append("")
        for i, anchor in enumerate(fusion.anchor_points[:8], 1):
            lines.append(f"{i}. **{anchor}**")
        lines.append("")

    # 最新内容
    lines.append("## 🆕 最新内容")
    lines.append("")
    sorted_items = sorted(items, key=lambda x: x.collected_at or "", reverse=True)
    for item in sorted_items[:15]:
        title = item.title[:70]
        lines.append(f"- [{title}]({item.url}) — {item.source_platform}")
    lines.append("")

    # 目录指引
    lines.append("## 📁 目录结构")
    lines.append("")
    lines.append("```")
    lines.append(f"{topic.get('slug', '')}/")
    lines.append("├── TOPIC.md           # 本文件：专题概览")
    lines.append("├── metadata.json      # 机器可读元数据")
    lines.append("├── fusion_draft.md    # 跨平台融合草稿")
    lines.append("└── sources/           # 按平台分目录的内容存档")
    lines.append("    ├── zhihu/")
    lines.append("    ├── xiaohongshu/")
    lines.append("    └── ...")
    lines.append("```")
    lines.append("")

    return "\n".join(lines)


def _update_global_index(output_root: Path) -> None:
    """更新全局 INDEX.md。"""
    output_root.mkdir(parents=True, exist_ok=True)
    topics = []
    for topic_dir in sorted(output_root.iterdir()):
        if topic_dir.is_dir() and (topic_dir / "metadata.json").exists():
            try:
                meta = json.loads((topic_dir / "metadata.json").read_text(encoding="utf-8"))
                topics.append(meta)
            except Exception:
                continue

    lines = [
        "# 专题知识库索引",
        "",
        f"> 共 {len(topics)} 个专题，自动生成。每个专题包含结构化内容存档、"
        "元数据和跨平台融合草稿。",
        "",
        "| 专题 | 内容数 | 覆盖平台 | 最后更新 |",
        "|---|---|---|---|",
    ]
    for meta in topics:
        name = meta.get("name", meta.get("slug", ""))
        slug = meta.get("slug", "")
        count = meta.get("item_count", 0)
        platforms = ", ".join(meta.get("platforms", []))
        updated = meta.get("updated_at", "")[:10]
        lines.append(f"| [{name}]({slug}/TOPIC.md) | {count} | {platforms} | {updated} |")
    lines.append("")
    lines.append("## 使用说明")
    lines.append("")
    lines.append("1. 点击专题名进入 TOPIC.md 查看概览")
    lines.append("2. sources/ 目录下按平台分目录存放原始内容存档")
    lines.append("3. fusion_draft.md 是跨平台内容融合分析草稿")
    lines.append("4. metadata.json 是机器可读的元数据，可用于程序化处理")
    lines.append("")

    (output_root / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")


def _update_agents_guide(output_root: Path) -> None:
    """更新 AGENTS.md（外部 AI 读取指南）。"""
    content = """# 专题知识库 — AI 读取指南

## 这是什么

本目录是 OpenBiliClaw 项目的专题知识库，包含从多个平台
（B站、知乎、小红书、V2EX、抖音等）收集的结构化内容。

## 目录结构

```
topics/
├── INDEX.md              # 主索引：所有专题的一览表
├── AGENTS.md             # 本文件：AI 读取指南
└── <slug>/               # 每个专题一个目录
    ├── TOPIC.md          # 专题概览：平台分布、交叉锚点、最新内容
    ├── metadata.json     # 机器可读元数据
    ├── fusion_draft.md   # 跨平台融合草稿：多视角整合分析
    └── sources/          # 按平台分目录的原始内容存档
        ├── zhihu/        # 知乎内容（深度长文）
        ├── xiaohongshu/  # 小红书内容（实用笔记）
        ├── bilibili/     # B站内容（视频教程）
        └── ...
```

## 读取建议

1. **先读 INDEX.md**：了解有哪些专题，快速定位
2. **再读 TOPIC.md**：获取专题概览、平台分布和交叉主题锚点
3. **需要深度内容时**：进入 sources/<platform>/ 读取具体内容存档
4. **需要跨平台整合视角时**：读 fusion_draft.md

## 内容特点

- 知乎：深度长文，适合系统性学习
- 小红书：实用笔记，适合快速获取技巧
- B站：视频教程，适合跟练
- V2EX：技术讨论，适合了解社区观点
- 抖音：短视频，适合快速了解热点

## 注意事项

- 内容版权归原作者所有，仅供个人学习参考
- fusion_draft.md 是 AI 自动生成的融合分析，可能存在偏差，请结合原始内容判断
- metadata.json 中的 cross_platform_keywords 和 anchor_points 可用于程序化检索
"""
    (output_root / "AGENTS.md").write_text(content, encoding="utf-8")


# ── 工具函数 ───────────────────────────────────────────────────────────────


def _safe_filename(name: str) -> str:
    """把任意字符串转为安全的文件名。"""
    safe = re.sub(r"[^\w\-.]", "_", name)
    return safe[:100] or "untitled"


def _safe_dirname(name: str) -> str:
    """把平台名转为安全的目录名。"""
    return _safe_filename(name).lower()
