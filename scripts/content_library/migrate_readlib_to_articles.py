"""把「已读库」与「笔记」迁进 `content.db.articles`（阅读库归一第二步）。

## 背景

阅读库此前是**四套并存**（详见 `docs/module-review-2026-09-15.md` §4 R1）：

| 概念 | 表 | 行数（2026-09-15 实测） |
|---|---|---|
| 「阅读库」 | `content.db.articles` | 90,564 |
| 「已读库」 | `openbiliclaw.db.read_archive` | 108 |
| 「对话归档」 | `openbiliclaw.db.conversation_archive` | 140 |
| 「笔记」 | `openbiliclaw.db.notes` | **2** |

2026-09-15 用户拍板：**统一到 `content.db.articles`**；`conversation_archive` 语义不同
（对话归档）保持独立。本脚本负责把前两张表的行**搬**过去。

## 为什么搬得动

`read_archive` 的列和 `articles` **几乎逐列对应**（`source_type/source_name/title/
url/author/summary/content_text/published_at/tags` + 两个时间戳），且同样是
`url UNIQUE` —— 所以幂等键直接用 `url`，不必发明新的。

## 安全阀

- **默认 dry-run**：必须显式 `--apply` 才写库；先看 diff 再决定。
- **绝不覆盖已有文章**：`url` 命中已有行时默认**跳过**（那行可能已经过 AI 摘要 / 正文
  清洗，覆盖等于回退）。要补空字段得显式 `--fill-blank`，且只补空、不覆盖非空。
- **不伪造 URL**：`articles.url` 是 `NOT NULL UNIQUE`。笔记若没有合法 http(s) 链接
  （例如 `source_url = "original"`），**跳过并单独报出来**，而不是编一个 `notes://` 假
  链接——假链接会在前端渲染成死链，比不迁更难查。
- **不替用户断言「已读」**：迁移只搬数据，`status` 保持 `articles` 的默认值；
  想标已读要显式 `--mark-read`（写 `finished`）。筛选靠 `source_type='read-archive'`。

## 用法

```bash
python scripts/content_library/migrate_readlib_to_articles.py            # dry-run
python scripts/content_library/migrate_readlib_to_articles.py --json     # 机器可读摘要
cp data/content.db data/backups/content.db.bak                           # 落库前自己备份
python scripts/content_library/migrate_readlib_to_articles.py --apply
```

`read_archive` 与 `notes` 迁完后**保持原表冻结**（不再写），等前端切到统一入口后再谈
删表。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_SOURCE_DB = _PROJECT_ROOT / "data" / "openbiliclaw.db"
DEFAULT_TARGET_DB = _PROJECT_ROOT / "data" / "content.db"

#: `articles` 允许写入的列（其余列由库自身维护：id / 各 summary_* / content_clean* …）。
_ARTICLE_COLUMNS = (
    "source_type",
    "source_name",
    "title",
    "url",
    "author",
    "summary",
    "content_text",
    "published_at",
    "tags",
    "status",
    "created_at",
    "updated_at",
)

#: `read_archive` → `articles` 的同名列（逐列对应，直接搬）。
_READ_ARCHIVE_COLUMN_MAP = {
    "source_type": "source_type",
    "source_name": "source_name",
    "title": "title",
    "url": "url",
    "author": "author",
    "summary": "summary",
    "content_text": "content_text",
    "published_at": "published_at",
    "tags": "tags",
}

#: 迁进来的笔记统一用这个 `source_type`（`note_type` 追加进 tags，不丢信息）。
_NOTE_SOURCE_TYPE = "note"


class MigrationError(RuntimeError):
    """Raised when the databases do not look like what this script expects."""


def _is_migratable_url(value: str) -> bool:
    """任何**带 scheme + host 的 URI** 都算可迁移，不限于 http(s)。

    为什么不只放行 http(s)：`read_archive` 里本来就有 9 条 `local://readlib/<标题>`
    （旧导入管线为没抓到链接的条目造的稳定占位符），而 `articles` 里**已有 1,497 条
    非 http 的 url**。把它们挡在门外等于把「已读库」割裂成两半。
    真正的判据是「它是不是一个 URI」——`source_url = "original"` 那种才是不可迁移。
    """
    parts = urlsplit(value.strip())
    return bool(parts.scheme) and bool(parts.netloc)


def _is_clickable_url(value: str) -> bool:
    """前端能否点开（只有 http(s) 才算）。"""
    return urlsplit(value.strip()).scheme in {"http", "https"}


@dataclass(slots=True)
class RowPlan:
    """一条待迁移行的处置结果。"""

    origin: str  # "read_archive" | "notes"
    origin_id: int
    url: str
    action: str  # "create" | "fill_blank" | "skip_exists" | "unmigratable"
    reason: str = ""
    values: dict[str, Any] = field(default_factory=dict)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _existing_urls(conn: sqlite3.Connection, urls: Sequence[str]) -> dict[str, dict[str, Any]]:
    """返回 `url → 该文章当前值`（只取需要比对是否为空白的列）。"""
    found: dict[str, dict[str, Any]] = {}
    for chunk_start in range(0, len(urls), 400):
        chunk = urls[chunk_start : chunk_start + 400]
        placeholders = ", ".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT {', '.join(_ARTICLE_COLUMNS)} FROM articles WHERE url IN ({placeholders})",
            chunk,
        ).fetchall()
        for row in rows:
            found[str(row["url"])] = {key: row[key] for key in _ARTICLE_COLUMNS}
    return found


def plan_read_archive(
    source: sqlite3.Connection,
    existing: dict[str, dict[str, Any]],
    *,
    mark_read: bool,
    fill_blank: bool,
    seen_urls: set[str] | None = None,
) -> list[RowPlan]:
    """把 `read_archive` 的每一行算成一条处置。"""
    planned = seen_urls if seen_urls is not None else set()
    plans: list[RowPlan] = []
    rows = source.execute(
        "SELECT id, " + ", ".join(_READ_ARCHIVE_COLUMN_MAP) + " FROM read_archive ORDER BY id"
    ).fetchall()
    for row in rows:
        values: dict[str, Any] = {
            target: row[source_col] for source_col, target in _READ_ARCHIVE_COLUMN_MAP.items()
        }
        url = str(values.get("url") or "").strip()
        if not _is_migratable_url(url):
            plans.append(
                RowPlan("read_archive", int(row["id"]), url, "unmigratable", "url 不是合法链接")
            )
            continue
        if mark_read:
            values["status"] = "finished"
        if url in planned:
            plans.append(
                RowPlan(
                    "read_archive",
                    int(row["id"]),
                    url,
                    "skip_exists",
                    "同一批内重复（另一张表已计划同 URL）",
                )
            )
            continue
        current = existing.get(url)
        if current is None:
            planned.add(url)
            plans.append(RowPlan("read_archive", int(row["id"]), url, "create", values=values))
            continue
        missing = _blank_fields(current, values)
        if fill_blank and missing:
            plans.append(
                RowPlan(
                    "read_archive",
                    int(row["id"]),
                    url,
                    "fill_blank",
                    reason="、".join(sorted(missing)),
                    values={key: values[key] for key in missing},
                )
            )
        else:
            plans.append(
                RowPlan(
                    "read_archive",
                    int(row["id"]),
                    url,
                    "skip_exists",
                    "文章已存在（默认不覆盖，--fill-blank 可只补空）",
                )
            )
    return plans


def plan_notes(
    source: sqlite3.Connection,
    existing: dict[str, dict[str, Any]],
    *,
    fill_blank: bool,
    seen_urls: set[str] | None = None,
) -> list[RowPlan]:
    """把 `notes` 的每一行算成一条处置。"""
    planned = seen_urls if seen_urls is not None else set()
    plans: list[RowPlan] = []
    rows = source.execute(
        "SELECT id, title, content_md, note_type, source_platform, source_url, author, tags, "
        "created_at, updated_at FROM notes ORDER BY id"
    ).fetchall()
    for row in rows:
        url = str(row["source_url"] or "").strip()
        if not _is_migratable_url(url):
            plans.append(
                RowPlan(
                    "notes",
                    int(row["id"]),
                    url,
                    "unmigratable",
                    "没有合法链接（notes.source_url 不是 URI，例如 'original'）——不伪造 URL",
                )
            )
            continue
        values: dict[str, Any] = {
            "source_type": _NOTE_SOURCE_TYPE,
            "source_name": str(row["source_platform"] or ""),
            "title": str(row["title"] or ""),
            "url": url,
            "author": str(row["author"] or ""),
            "content_text": str(row["content_md"] or ""),
            "published_at": "",
            "tags": _note_tags(str(row["tags"] or "[]"), str(row["note_type"] or "")),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        if url in planned:
            plans.append(
                RowPlan("notes", int(row["id"]), url, "skip_exists", "同一批内重复（已读库已有同 URL）")
            )
            continue
        current = existing.get(url)
        if current is None:
            planned.add(url)
            plans.append(RowPlan("notes", int(row["id"]), url, "create", values=values))
            continue
        missing = _blank_fields(current, values)
        if fill_blank and missing:
            plans.append(
                RowPlan(
                    "notes",
                    int(row["id"]),
                    url,
                    "fill_blank",
                    reason="、".join(sorted(missing)),
                    values={key: values[key] for key in missing},
                )
            )
        else:
            plans.append(
                RowPlan("notes", int(row["id"]), url, "skip_exists", "文章已存在（默认不覆盖）")
            )
    return plans


def _note_tags(raw_tags: str, note_type: str) -> str:
    """把 `note_type` 追加进 tags —— 迁移后不能丢掉「这条是知识笔记还是导入内容」。"""
    try:
        parsed = json.loads(raw_tags or "[]")
    except json.JSONDecodeError:
        parsed = []
    tags = [str(item) for item in parsed] if isinstance(parsed, list) else []
    if note_type and note_type not in tags:
        tags.append(note_type)
    return json.dumps(tags, ensure_ascii=False)


def _blank_fields(current: dict[str, Any], incoming: dict[str, Any]) -> set[str]:
    """`incoming` 里比 `current` 更有内容、且 `current` 为空的列。"""
    missing: set[str] = set()
    for key, value in incoming.items():
        if key in {"created_at", "updated_at", "status"}:
            continue
        if not isinstance(value, str) or not value.strip():
            continue
        existing_value = current.get(key)
        if not isinstance(existing_value, str) or not existing_value.strip():
            missing.add(key)
    return missing


def _insert_article(conn: sqlite3.Connection, values: dict[str, Any]) -> None:
    columns = [key for key in _ARTICLE_COLUMNS if key in values]
    placeholders = ", ".join("?" for _ in columns)
    conn.execute(
        f"INSERT INTO articles ({', '.join(columns)}) VALUES ({placeholders})",
        [values[key] for key in columns],
    )


def _update_article(conn: sqlite3.Connection, url: str, values: dict[str, Any]) -> None:
    columns = [key for key in _ARTICLE_COLUMNS if key in values]
    assignments = ", ".join(f"{key} = ?" for key in columns)
    conn.execute(
        f"UPDATE articles SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE url = ?",
        [*[values[key] for key in columns], url],
    )


def apply_plans(conn: sqlite3.Connection, plans: Iterable[RowPlan]) -> dict[str, int]:
    stats = {"created": 0, "filled": 0}
    with conn:
        for plan in plans:
            if plan.action == "create":
                _insert_article(conn, plan.values)
                stats["created"] += 1
            elif plan.action == "fill_blank":
                _update_article(conn, plan.url, plan.values)
                stats["filled"] += 1
    return stats


def _counts(plans: Sequence[RowPlan]) -> dict[str, int]:
    out: dict[str, int] = {}
    for plan in plans:
        out[plan.action] = out.get(plan.action, 0) + 1
    return out


def _render(
    plans: Sequence[RowPlan],
    *,
    mode: str,
    applied: dict[str, int],
    non_clickable: Sequence[str],
) -> None:
    counts = _counts(plans)
    print(f"[{mode}] 计划 {len(plans)} 行：{counts}")
    for plan in plans:
        if plan.action in {"create", "fill_blank", "unmigratable"}:
            detail = f"（{plan.reason}）" if plan.reason else ""
            print(f"   {plan.action:<12} {plan.origin}#{plan.origin_id} {plan.url[:96]}{detail}")
    if non_clickable:
        print(
            f"   ⚠️ {len(non_clickable)} 条不是 http(s) 链接（如 {non_clickable[0][:60]}）："
            "能迁，但前端点不开——和它们在「已读库」里的表现一致"
        )
    if applied:
        print(f"   已写入：新增 {applied.get('created', 0)}，补空 {applied.get('filled', 0)}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把已读库/笔记迁入 content.db.articles")
    parser.add_argument("--source-db", default=str(DEFAULT_SOURCE_DB))
    parser.add_argument("--target-db", default=str(DEFAULT_TARGET_DB))
    parser.add_argument("--kind", choices=("read-archive", "notes", "all"), default="all")
    parser.add_argument("--apply", action="store_true", help="真的写库（默认 dry-run）")
    parser.add_argument("--fill-blank", action="store_true", help="已存在的文章只补空字段")
    parser.add_argument("--mark-read", action="store_true", help="把 read_archive 行标成 finished")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    source_path = Path(args.source_db)
    target_path = Path(args.target_db)
    if not source_path.exists():
        print(f"!! 源库不存在：{source_path}", file=sys.stderr)
        return 2
    if not target_path.exists():
        print(f"!! 目标库不存在：{target_path}", file=sys.stderr)
        return 2

    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    target = sqlite3.connect(str(target_path))
    target.row_factory = sqlite3.Row
    try:
        for kind, table in (("read-archive", "read_archive"), ("notes", "notes")):
            if args.kind in {kind, "all"} and not _table_exists(source, table):
                raise MigrationError(f"源库里没有表 {table}（--kind {kind}）：{source_path}")
        if not _table_exists(target, "articles"):
            raise MigrationError(f"目标库里没有 articles 表：{target_path}")

        plans: list[RowPlan] = []
        # 两张表之间**可能有同一个 URL**（实测：read_archive#167831 与 notes#1 是同一条
        # 小红书笔记）→ 同一批内必须去重，否则第二条 INSERT 直接撞 UNIQUE 约束。
        seen_urls: set[str] = set()
        if args.kind in {"read-archive", "all"}:
            urls = [
                str(row["url"]).strip()
                for row in source.execute("SELECT url FROM read_archive")
            ]
            existing = _existing_urls(target, urls)
            plans.extend(
                plan_read_archive(
                    source,
                    existing,
                    mark_read=args.mark_read,
                    fill_blank=args.fill_blank,
                    seen_urls=seen_urls,
                )
            )
        if args.kind in {"notes", "all"}:
            urls = [
                str(row["source_url"]).strip()
                for row in source.execute("SELECT source_url FROM notes")
            ]
            existing = _existing_urls(target, urls)
            plans.extend(
                plan_notes(source, existing, fill_blank=args.fill_blank, seen_urls=seen_urls)
            )

        non_clickable = [
            plan.url
            for plan in plans
            if plan.action == "create" and not _is_clickable_url(plan.url)
        ]

        applied: dict[str, int] = {}
        if args.apply:
            applied = apply_plans(target, plans)
    except MigrationError as exc:
        print(f"!! {exc}", file=sys.stderr)
        return 2
    finally:
        source.close()
        target.close()

    if args.json:
        print(
            json.dumps(
                {
                    "mode": "apply" if args.apply else "dry-run",
                    "counts": _counts(plans),
                    "applied": applied,
                    "non_clickable_urls": len(non_clickable),
                    "rows": [
                        {
                            "origin": plan.origin,
                            "origin_id": plan.origin_id,
                            "url": plan.url,
                            "action": plan.action,
                            "reason": plan.reason,
                        }
                        for plan in plans
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        _render(
            plans,
            mode="apply" if args.apply else "dry-run",
            applied=applied,
            non_clickable=non_clickable,
        )
        if not args.apply:
            print("（dry-run：未写库。确认无误后加 --apply；建议先备份 data/content.db）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
