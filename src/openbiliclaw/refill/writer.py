"""把回补抓到的正文写入阅读库 ``articles.content_text``。

复用 ``storage.Database.upsert_article``（挂 cleaner 生成 content_cleaned），对既有
缺口行只补正文：upsert_article 命中已存在行时不更新 author、tags 仅在空时覆盖，
维持既有语义。回补只动正文，不破坏原行元数据。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def build_content_writer(db_path: str | Path) -> Any:
    """返回 ``(item, body) -> None`` 的写回调（挂 Database 上报正文）。

    ``Database`` 懒初始化并复用同一连接，供一轮调度内多次写入。
    参数复用自 ``web_capture._ingest``：summary 取正文前 200 字作为摘要。
    """
    database = None

    def write(item: dict[str, Any], body: str) -> None:
        nonlocal database
        if database is None:
            from openbiliclaw.storage.database import Database

            database = Database(str(db_path))
            database.initialize()
        url = str(item.get("url") or "")
        title = str(item.get("title") or "") or url
        database.upsert_article(
            source_type=str(item.get("source_type") or ""),
            source_name="",
            title=title,
            url=url,
            summary=body[:200],
            content_text=body,
        )

    return write