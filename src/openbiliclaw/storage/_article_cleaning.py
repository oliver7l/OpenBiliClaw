"""正文清洗器注册点 — K5 storage 去领域依赖。

``cache_content`` 入库时调用 ContentCleaner 生成清洗字段，但
ContentCleaner 属于 knowledge_forge（领域），storage 直接 import 形成
storage→knowledge_forge 反向依赖。改为注册表模式：

- storage 只认 ``Callable[..., ContentCleanResult-like]``，不 import 领域模块
- ``knowledge_forge`` 包导入时自注册（领域→基础设施方向，合法）
- 生产路径（api routes / cli）必然导入 knowledge_forge，行为与原懒加载一致；
  若确实未注册（极端独立场景），清洗字段留空由批量管线后补，不阻断入库
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

CleanerCallable = Callable[..., Any]

_cleaner: CleanerCallable | None = None


def register_content_cleaner(cleaner: CleanerCallable) -> None:
    """注册正文清洗器（knowledge_forge 导入时调用，幂等，后注册覆盖）。"""
    global _cleaner
    _cleaner = cleaner


def has_content_cleaner() -> bool:
    return _cleaner is not None


def clean_article_content(
    content_text: str, *, title: str, source_type: str
) -> dict[str, Any] | None:
    """调用已注册的清洗器，返回入库字段 dict；未注册或失败返回 None。

    返回键：content_cleaned / content_clean_score / content_clean_log /
    content_verified / content_verify_result。
    """
    if _cleaner is None:
        return None
    try:
        cr = _cleaner().clean(content_text, title=title, source_type=source_type)
        return {
            "content_cleaned": cr.cleaned_text or None,
            "content_clean_score": cr.clean_score,
            "content_clean_log": json.dumps(cr.operations, ensure_ascii=False, default=str),
            "content_verified": 1 if cr.verified else 0,
            "content_verify_result": json.dumps(cr.verify_issues, ensure_ascii=False, default=str),
        }
    except Exception:
        # 清理失败不阻断入库，仅降级为新列留空，由批量清理管线后补。
        logger.exception("Knowledge Forge clean failed for article: %s", title)
        return None
