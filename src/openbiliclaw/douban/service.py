"""豆瓣书影音业务层：封装存储读写，供路由与 adapter 使用。"""

from __future__ import annotations

from .store import DoubanStore

CATEGORY_LABELS = {"movie": "影视", "book": "书", "music": "音乐"}
STATUS_LABELS = {"collect": "看过/读过/听过", "wish": "想看/想读/想听", "do": "在看/在读/在听"}


class DoubanService:
    """豆瓣清单业务。"""

    def __init__(self, db_path: str) -> None:
        self._store = DoubanStore(db_path)

    def items(
        self,
        category: str | None = None,
        status: str | None = None,
        search: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> dict:
        items = self._store.list_items(category, status, search, limit=limit, offset=offset)
        return {"items": items, "count": len(items)}

    def stats(self) -> dict:
        raw = self._store.stats()
        out: dict = {"total": raw["total"], "categories": {}}
        for cat, statuses in raw["categories"].items():
            out["categories"][cat] = {
                "label": CATEGORY_LABELS.get(cat, cat),
                "statuses": {
                    s: {"label": STATUS_LABELS.get(s, s), "count": c}
                    for s, c in statuses.items()
                },
            }
        return out
