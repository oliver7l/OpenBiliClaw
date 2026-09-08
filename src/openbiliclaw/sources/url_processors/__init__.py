"""URL content processors — single URL content extraction for multiple platforms.

Inspired by Agent-SaveMark's processor pattern. Each platform has a
dedicated processor that extracts structured content from a single URL.
Processors register themselves via URL patterns and priorities.

Usage:
    from openbiliclaw.sources.url_processors import match_processor, list_processors

    # Match a URL to the best processor
    processor = match_processor("https://www.zhihu.com/question/123/answer/456")
    result = await processor.process("https://www.zhihu.com/question/123/answer/456")

    # List all registered processors
    processors = list_processors()

Supported platforms:
    - Zhihu (知乎): answers, articles, questions
    - V2EX: topics with replies
    - Hupu (虎扑): BBS posts
    - Bilibili (哔哩哔哩): videos (with subtitles), articles
    - Xiaohongshu (小红书): notes
    - YouTube: videos (with transcripts)
    - Xiaoyuzhou (小宇宙): podcast episodes
    - Generic URL: any web page (fallback)
"""

# Import all processors to trigger registration
from openbiliclaw.sources.url_processors import (
    bilibili_processor,
    csdn_processor,
    douban_processor,
    gcores_processor,
    generic_url_processor,
    hupu_processor,
    huxiu_processor,
    jianshu_processor,
    juejin_processor,
    kr36_processor,
    sspai_processor,
    v2ex_processor,
    wechat_processor,
    weibo_processor,
    xiaohongshu_processor,
    xiaoyuzhou_processor,
    youtube_processor,
    zhihu_processor,
)
from openbiliclaw.sources.url_processors.base import (
    BaseProcessor,
    ProcessorResult,
    ProcessorStatus,
    is_safe_url,
)
from openbiliclaw.sources.url_processors.registry import (
    get_all_source_types,
    get_processor,
    list_processors,
    match_processor,
    register_processor,
)

__all__ = [
    # Base classes
    "BaseProcessor",
    "ProcessorResult",
    "ProcessorStatus",
    "is_safe_url",
    # Registry functions
    "match_processor",
    "get_processor",
    "list_processors",
    "get_all_source_types",
    "register_processor",
]
