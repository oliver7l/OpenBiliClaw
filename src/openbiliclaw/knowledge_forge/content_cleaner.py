"""3.0 正文清理器（Content Cleaner）。

解决的问题（文档 3.0.1）：抓取的正文常混入评论、推荐内容、广告等无关内容。
典型案例：知乎文章正文后混入"一看就是t0的老哥"等评论和"一、价值链分析…"等推荐文章。

设计原则：
    - **规则优先，LLM 兜底**：确定性规则清理 80% 常见污染，疑难案例 LLM 抽样
    - **保守截断**：宁可少清理也不误删正文；不确定时标记待人工审核
    - **可追溯**：记录清理前后差异与每一步操作
    - **平台适配**：知乎/小红书/B站/YouTube 分别适配

用法：
    from openbiliclaw.knowledge_forge.content_cleaner import ContentCleaner
    cleaner = ContentCleaner()
    result = cleaner.clean(text, title=title, source_type="zhihu")
"""

from __future__ import annotations

import re
from typing import Any

from .config import CleanerConfig
from .models import CleanResult, VerifyResult
from .utils import (
    compress_whitespace,
    count_effective_sentences,
    decode_html_entities,
    has_encoding_error,
    strip_html_tags,
)

# 知乎评论特征：行首出现的口语化短语（文档 3.0.2）
ZHIHU_COMMENT_MARKERS = (
    "一看就是",
    "原以为",
    "我觉得吧",
    "楼上",
    "谢邀",
    "泻药",
    "利益相关",
    "评论区",
    "赞同了",
    "收藏了",
    "已关注",
    "作者加油",
    "写得真好",
    "受教了",
    "不敢苟同",
    "实名反对",
    "补充一下",
    "插个眼",
    "马克一下",
    "占个坑",
)

# 知乎推荐内容：正文之后出现的新文章标题格式
ZHIHU_RECOMMEND_PATTERNS = (
    re.compile(r"^一、[^\n]{4,40}$", re.M),  # "一、价值链分析"
    re.compile(r"^#{1,2}\s*[^#\n]{4,40}$", re.M),  # 新标题
    re.compile(r"^【[^】]{2,30}】\s*$", re.M),
)

# 小红书营销关键词
XHS_AD_KEYWORDS = (
    "点击链接",
    "购买链接",
    "优惠券",
    "私信我",
    "评论区扣",
    "同款链接",
    "限时优惠",
    "扫码下单",
    "进店",
    "直播间",
    "福利来了",
)

# B站弹幕/评论时间戳（行首 [00:01:23] 之类）
BILIBILI_TS_RE = re.compile(r"^\s*\[?\d{1,2}:\d{2}(?::\d{2})?\]?\s*.{0,30}$", re.M)

# YouTube 描述区链接行
YT_LINK_LINE_RE = re.compile(
    r"^\s*(?:https?://\S+|(?:Twitter|Instagram|Facebook|Patreon|Discord|Telegram)[^\n]*)\s*$",
    re.M | re.I,
)

# 通用广告特征（标记而非直接删，保守策略）
GENERAL_AD_KEYWORDS = ("扫码关注", "公众号", "微信号", "加微信", "商务合作", "广告合作")


class ContentCleaner:
    """正文清理器。"""

    def __init__(self, config: CleanerConfig | None = None) -> None:
        self.config = config or CleanerConfig()

    # ------------------------------------------------------------------ 主流程
    def clean(
        self,
        text: str,
        *,
        title: str = "",
        source_type: str = "",
        article_id: int = 0,
    ) -> CleanResult:
        """清理正文；返回 CleanResult（含清理后文本、评分、日志、验证结果）。"""
        cfg = self.config
        src = (source_type or "").lower()
        result = CleanResult(article_id=article_id, original_length=len(text or ""))

        if not text:
            result.add_op("empty", "正文为空")
            self._verify(result, "", title)
            return result

        cleaned = text

        # 1. HTML 解析与标签清理
        cleaned, removed = self._clean_html(cleaned, cfg)
        if removed:
            result.add_op("html_strip", "移除 HTML 标签/脚本/样式/导航", removed)

        # 2. 平台特定清理
        cleaned, removed, truncated_at = self._clean_by_platform(cleaned, src, cfg, result)
        if truncated_at is not None:
            result.truncated_at = truncated_at

        # 3. 通用清理
        before = len(cleaned)
        cleaned = compress_whitespace(cleaned, remove_empty_lines=cfg.remove_empty_lines)
        if len(cleaned) != before:
            result.add_op("whitespace", "压缩空白/空行", before - len(cleaned))

        # 4. 质量验证
        result.cleaned_text = cleaned
        result.removed_chars = max(0, result.original_length - len(cleaned))
        self._verify(result, cleaned, title)

        # 5. 清理质量评分（0-100）
        result.clean_score = self._score(result)
        return result

    # ------------------------------------------------------------------ 步骤实现
    def _clean_html(self, text: str, cfg: CleanerConfig) -> tuple[str, int]:
        before = len(text)
        out = strip_html_tags(text, remove_links=cfg.remove_links)
        if cfg.decode_html_entities:
            out = decode_html_entities(out)
        return out, before - len(out)

    def _clean_by_platform(
        self, text: str, src: str, cfg: CleanerConfig, result: CleanResult
    ) -> tuple[str, int, int | None]:
        """平台特定清理；返回 (文本, 移除字符数, 截断位置)。"""
        removed_total = 0
        truncated_at: int | None = None

        if "zhihu" in src:
            if cfg.zhihu_remove_comments:
                text, n, pos = self._truncate_at_comment(text)
                if pos is not None:
                    result.add_op("zhihu_comment", "截断知乎评论/跟帖", n)
                    removed_total += n
                    truncated_at = pos if truncated_at is None else min(truncated_at, pos)
            if cfg.zhihu_remove_recommendations:
                text, n, pos = self._truncate_at_recommendation(text)
                if pos is not None:
                    result.add_op("zhihu_recommend", "截断知乎推荐内容", n)
                    removed_total += n
                    truncated_at = pos if truncated_at is None else min(truncated_at, pos)

        elif "xiaohongshu" in src or "xhs" in src:
            if cfg.xhs_remove_ads:
                before = len(text)
                text = self._remove_ad_paragraphs(text, XHS_AD_KEYWORDS)
                if len(text) != before:
                    result.add_op("xhs_ad", "移除小红书营销段落", before - len(text))
                    removed_total += before - len(text)
            if cfg.xhs_remove_hashtags:
                before = len(text)
                text = re.sub(r"#[^\s#]{1,30}#?", "", text)
                removed_total += before - len(text)

        elif "bilibili" in src or "bili" in src:
            if cfg.bilibili_remove_danmaku:
                before = len(text)
                text = BILIBILI_TS_RE.sub("", text)
                if len(text) != before:
                    result.add_op("bili_danmaku", "移除B站弹幕/时间戳行", before - len(text))
                    removed_total += before - len(text)

        elif "youtube" in src or "yt" in src:
            if cfg.youtube_remove_description_links:
                before = len(text)
                text = YT_LINK_LINE_RE.sub("", text)
                if len(text) != before:
                    result.add_op("yt_links", "移除YouTube描述区链接", before - len(text))
                    removed_total += before - len(text)

        # 通用广告：只标记不删（保守）
        hits = [k for k in GENERAL_AD_KEYWORDS if k in text]
        if hits:
            result.add_op("ad_suspect", f"疑似广告特征词：{','.join(hits[:5])}", 0)

        return text, removed_total, truncated_at

    def _truncate_at_comment(self, text: str) -> tuple[str, int, int | None]:
        """命中评论特征则截断该行及之后内容。"""
        lines = text.split("\n")
        # 只在文章后半段开始找，避免误伤正文开头出现的"我觉得"
        start = max(1, int(len(lines) * 0.4))
        for i in range(start, len(lines)):
            line = lines[i].strip()
            if not line:
                continue
            if any(line.startswith(m) for m in ZHIHU_COMMENT_MARKERS):
                kept = "\n".join(lines[:i])
                return kept, len(text) - len(kept), len(kept)
        return text, 0, None

    def _truncate_at_recommendation(self, text: str) -> tuple[str, int, int | None]:
        """正文后出现新文章标题格式 → 截断。"""
        lines = text.split("\n")
        start = max(1, int(len(lines) * 0.5))
        for i in range(start, len(lines)):
            line = lines[i].strip()
            if not line:
                continue
            for pat in ZHIHU_RECOMMEND_PATTERNS:
                if pat.match(line):
                    # 排除正文自身的分节标题：若该行前后文高度相关则不截断
                    kept = "\n".join(lines[:i])
                    if len(kept) < 200:  # 截得太狠，放弃
                        return text, 0, None
                    return kept, len(text) - len(kept), len(kept)
        return text, 0, None

    def _remove_ad_paragraphs(self, text: str, keywords: tuple[str, ...]) -> str:
        """移除命中营销关键词的段落（整段移除，避免半截残留）。"""
        paragraphs = re.split(r"\n{1,}", text)
        kept = [p for p in paragraphs if not any(k in p for k in keywords)]
        return "\n".join(kept) if kept else text

    # ------------------------------------------------------------------ 验证与评分
    def _verify(self, result: CleanResult, text: str, title: str) -> None:
        """3.0.3 质量验证。"""
        cfg = self.config
        v = VerifyResult()
        v.effective_sentences = count_effective_sentences(text)

        if len(text) < cfg.min_content_length:
            v.fail(f"content_too_short:{len(text)}<{cfg.min_content_length}")
        if v.effective_sentences < cfg.min_effective_sentences:
            v.fail(f"low_quality:sentences={v.effective_sentences}")
        if has_encoding_error(text):
            v.fail("encoding_error")
        # 残留污染：清理后仍有评论特征词
        residual = [m for m in ZHIHU_COMMENT_MARKERS if text.strip().startswith(m)]
        if residual:
            v.fail(f"residual_contamination:{residual[:2]}")

        result.verified = v.passed
        result.verify_issues = v.issues

    def _score(self, result: CleanResult) -> float:
        """清理质量评分 0-100：基础分 100 - 问题扣分 + 清理收益。"""
        score = 100.0
        for issue in result.verify_issues:
            key = issue.split(":")[0]
            score -= {
                "content_too_short": 25,
                "low_quality": 30,
                "encoding_error": 40,
                "residual_contamination": 15,
                "title_mismatch": 20,
            }.get(key, 10)
        return max(0.0, min(100.0, round(score, 1)))

    # ------------------------------------------------------------------ 批量
    def clean_article_row(self, row: dict[str, Any]) -> CleanResult:
        """直接处理一行 articles 记录（dict，含 id/content_text/title/source_type）。"""
        return self.clean(
            row.get("content_text") or "",
            title=row.get("title") or "",
            source_type=row.get("source_type") or "",
            article_id=row.get("id") or 0,
        )
