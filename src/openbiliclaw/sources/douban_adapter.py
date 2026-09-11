"""豆瓣（Douban）source adapter — 从 douban.db 回放书影音清单供阅读/推荐。

与小红书 stub 不同，这个 adapter 的 ``fetch()`` 从独立 ``douban.db`` 读取用户
已抓取的书影音条目（看过/想看/在看），转成 ``DiscoveredContent`` 返回，供
discovery / 阅读链路消费。默认 ``[sources.douban].enabled=false`` 不注入推荐
流；仅当用户显式启用后才会注册进 ``AdapterRegistry``。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openbiliclaw.core.contracts import DiscoveredContent
    from openbiliclaw.sources.protocol import SourceRecipe

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "data/douban.db"


class DoubanAdapter:
    """从 douban.db 回放书影音清单的 source adapter。"""

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self._db_path = db_path

    @property
    def source_type(self) -> str:
        return "douban"

    @property
    def source_name(self) -> str:
        return "豆瓣"

    def _load_items(self, limit: int = 20) -> list[dict]:
        from openbiliclaw.douban.store import DoubanStore

        store = DoubanStore(self._db_path)
        return store.list_items(limit=limit)

    async def fetch(
        self,
        recipe: SourceRecipe | None,
        profile: Any,
        limit: int = 20,
    ) -> list[DiscoveredContent]:
        """从 douban.db 返回最近的书影音条目。"""
        from openbiliclaw.core.contracts import DiscoveredContent

        items = self._load_items(limit=limit)
        out: list[DiscoveredContent] = []
        for it in items:
            category = it.get("category", "")
            status = it.get("status", "")
            name = it.get("name", "") or ""
            url = it.get("url", "") or ""
            pub = it.get("pub", "") or it.get("intro", "") or ""
            out.append(
                DiscoveredContent(
                    content_id=url or name,
                    content_url=url,
                    source_platform="douban",
                    source_strategy="douban_replay",
                    author_name="豆瓣",
                    title=name,
                    description=f"[{category}/{status}] {pub}".strip(),
                    body_text=(it.get("comment", "") or ""),
                    content_type="note",
                    tags=["豆瓣", category, status],
                )
            )
        logger.debug("DoubanAdapter.fetch() -> %d items", len(out))
        return out
