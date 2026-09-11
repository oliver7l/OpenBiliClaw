"""把已抓取的豆瓣 JSON 导入独立 douban.db。

读取 ``data/douban/douban_all.json``（含 categories：影视/书/音乐 ×
collect/wish/do → items），映射为结构化条目写入 ``douban_items`` 表（按 url
去重，重新导入前会先清空以保持幂等）。

用法：``python -m openbiliclaw.douban.import_data``（默认路径）或
``python -m openbiliclaw.douban.import_data --json <path> --db <path>``。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from openbiliclaw.douban.store import DoubanStore

# 分类名 -> category
CATEGORY_MAP = {"影视": "movie", "书": "book", "音乐": "music"}
# 动作名 -> status
STATUS_MAP = {"collect": "collect", "wish": "wish", "do": "do"}

DEFAULT_JSON = Path("data/douban/douban_all.json")
DEFAULT_DB = Path("data/douban.db")


def _items_from_json(data: dict) -> list[dict]:
    """把 douban_all.json 展开为平铺条目列表。"""
    out: list[dict] = []
    for cat_label, cat in data.get("categories", {}).items():
        category = CATEGORY_MAP.get(cat_label)
        if category is None:
            continue
        for act, val in cat.items():
            status = STATUS_MAP.get(act)
            if status is None:
                continue
            for it in val.get("items", []):
                item = {
                    "category": category,
                    "status": status,
                    "name": it.get("name", ""),
                    "url": it.get("url", ""),
                    "date": it.get("date", ""),
                    "comment": it.get("comment", ""),
                    "rating": it.get("rating", ""),
                    "pub": it.get("pub", ""),
                    "intro": it.get("intro", ""),
                }
                out.append(item)
    return out


def import_from_json(json_path: str | Path, db_path: str | Path) -> dict:
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    items = _items_from_json(data)
    store = DoubanStore(db_path)
    store.clear()
    result = store.import_items(items)
    result["total_in_json"] = len(items)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导入豆瓣书影音 JSON 到 douban.db")
    parser.add_argument("--json", default=str(DEFAULT_JSON), help="douban_all.json 路径")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="目标 douban.db 路径")
    args = parser.parse_args(argv)

    jp = Path(args.json)
    if not jp.exists():
        print(f"找不到 JSON 文件: {jp}", file=sys.stderr)
        return 1
    r = import_from_json(jp, args.db)
    print(
        f"导入完成: json={r['total_in_json']} 条 | "
        f"插入 {r['inserted']} / 更新 {r['updated']} / 跳过 {r['skipped']} -> {args.db}"
    )
    store = DoubanStore(args.db)
    print(f"douban.db 现有条目: {store.count()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
