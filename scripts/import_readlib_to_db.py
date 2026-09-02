#!/usr/bin/env python3
"""把 notes/已读库 的文件存档导入数据库 read_archive 表。

用法:
  python3 scripts/import_readlib_to_db.py            # 导入/补全

行为:
  - 24 个普通文件夹 + 轱天乐总结合集的 10 个子篇 = 34 条
  - source_type 固定 read-archive
  - tags = [已读库, 平台名, 主题词...] 便于标签筛选
  - content_text 取 content.md 正文(去掉 YAML frontmatter), 前端 renderMarkdown 渲染
  - summary 优先取笔记里的 导读/一句话核心/内容概要 引言行
  - 幂等: url 已存在且正文>=50字则跳过; 空壳则补全
  - 无原文链接的条目 url 用 local://readlib/<文件夹名> 占位(唯一性约束)
  - 喂画像: 每条入库条目同步发一条 article_finished 正向事件到 events
    表(按 URL 去重), soul 管道会异步把「读过这类内容」学进画像;
    已导入过的存量条目跑一次脚本也会补发(回填)
"""
import argparse
import datetime
import json
import re
import sqlite3
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
READLIB = BASE / "notes" / "已读库"
DB_PATH = BASE / "data" / "openbiliclaw.db"

# format_event_context 与 PATCH /api/articles/{id} 的 finished 分支共用,
# 保证已读库事件的 context 措辞和阅读库「标记读完」完全一致。event_format
# 是纯 stdlib 模块, 直连 src/ 即可导入; 导入失败则退回本地近似实现。
sys.path.insert(0, str(BASE / "src"))
try:
    from openbiliclaw.sources.event_format import format_event_context
except ImportError:  # pragma: no cover - 仅在包结构被破坏时触发
    def format_event_context(*, event_type: str, source_platform: str,
                             title: str, author: str = "", extra: str = "") -> str:
        parts = [f"在{source_platform}" if source_platform else "", "读完了"]
        parts.append(f"《{title}》" if title else "一条内容")
        if author:
            parts.append(f",作者:{author}")
        return "".join(parts)

PLATFORM_LABELS = {
    "zhihu": "知乎", "xiaohongshu": "小红书", "v2ex": "V2EX",
}
SUMMARY_PAT = re.compile(
    r"^>\s*\*{0,2}(导读|一句话核心|内容概要|核心观点)\*{0,2}[：:]\s*(.+)$", re.M
)


def strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:].lstrip("\n")
    return text


def extract_summary(md: str, meta: dict) -> str:
    m = SUMMARY_PAT.search(md)
    if m:
        return re.sub(r"[*`\[\]]", "", m.group(2)).strip()[:200]
    exc = (meta.get("excerpt") or "").strip()
    return exc[:200]


def parse_theme_tags(theme: str) -> list[str]:
    return [t.strip() for t in re.split(r"[/、·]", theme or "") if t.strip()][:3]


def normalize_url(url: str, folder: str) -> str:
    url = (url or "").strip()
    if url.startswith("http"):
        m = re.match(r"https?://api\.zhihu\.com/articles/(\d+)", url)
        if m:
            return f"https://zhuanlan.zhihu.com/p/{m.group(1)}"
        return url
    return f"local://readlib/{folder}"


def normalize_date(val: str, fallback: str = "") -> str:
    val = (val or "").strip()
    if re.match(r"^\d{4}-\d{2}(-\d{2})?(\s\d{2}:\d{2})?$", val):
        return val
    m = re.match(r"^(\d{4})-(\d{2})$", val)
    if m:
        return f"{val}-15"
    return fallback or datetime.datetime.now().strftime("%Y-%m-%d")


def collect_items() -> list[dict]:
    items = []
    for folder in sorted(READLIB.iterdir()):
        if not folder.is_dir() or folder.name.startswith("_"):
            continue
        meta_f = folder / "meta.json"
        if not meta_f.exists():
            continue
        meta = json.loads(meta_f.read_text(encoding="utf-8"))
        if meta.get("type") == "collection":
            for sub in sorted(folder.iterdir()):
                if not sub.is_dir():
                    continue
                jfs = list(sub.glob("*.json"))
                mds = list(sub.glob("*.md"))
                if not mds:
                    continue
                jd = json.loads(jfs[0].read_text(encoding="utf-8")) if jfs else {}
                ts = jd.get("created")
                date = (
                    datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
                    if ts else normalize_date(meta.get("created_at", ""))
                )
                md = mds[0].read_text(encoding="utf-8")
                items.append({
                    "folder": f"{folder.name}/{sub.name}",
                    "title": jd.get("title") or sub.name,
                    "author": (jd.get("author") or {}).get("name") or meta.get("author", ""),
                    "url": normalize_url(jd.get("url", ""), f"{folder.name}/{sub.name}"),
                    "platform": "zhihu",
                    "date": date,
                    "md": md,
                    "meta": {"excerpt": jd.get("excerpt", "")},
                    "theme": meta.get("theme", ""),
                    "extra_tags": ["总结合集"],
                })
            continue
        md_f = folder / "content.md"
        if not md_f.exists():
            continue
        items.append({
            "folder": folder.name,
            "title": meta.get("title") or meta.get("question_title") or folder.name,
            "author": meta.get("author", ""),
            "url": normalize_url(meta.get("url", ""), folder.name),
            "platform": meta.get("source", "other"),
            "date": normalize_date(meta.get("created_at", ""), meta.get("archived_at", "")),
            "md": md_f.read_text(encoding="utf-8"),
            "meta": meta,
            "theme": meta.get("theme", ""),
            "extra_tags": [],
        })
    return items


def feed_profile_event(
    cur: sqlite3.Cursor,
    *,
    article_id: int,
    url: str,
    title: str,
    platform: str,
    author: str,
    tags: list[str],
) -> bool:
    """把一条已读库条目写入 events 表作为画像正向信号。

    与 api.update_article 的 finished 分支同构：article_finished 在
    classify_event_satisfaction 里归为 (positive, explicit_engagement)，
    soul 管道会异步把它消费进画像。按 URL 幂等去重——重复跑脚本不会
    重复喂。

    Returns:
        True 表示本次新插入了一条事件。
    """
    existing = cur.execute(
        "SELECT id FROM events "
        "WHERE event_type = 'article_finished' AND url = ? LIMIT 1",
        (url,),
    ).fetchone()
    if existing:
        return False
    context = format_event_context(
        event_type="article_finished",
        source_platform=platform,
        title=title,
        author=author,
    )
    metadata = {
        "article_id": article_id,
        "source_type": "read-archive",
        "source_name": platform,
        "author": author,
        "tags": tags,
        "signal_strength": 0.8,
    }
    # inferred_satisfaction / satisfaction_reason 与 storage.insert_event
    # 的单一分类口径保持一致（single classification owner）。
    cur.execute(
        "INSERT INTO events "
        "(event_type, url, title, context, metadata, "
        " inferred_satisfaction, satisfaction_reason) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "article_finished",
            url,
            title,
            context,
            json.dumps(metadata, ensure_ascii=False),
            "positive",
            "explicit_engagement",
        ),
    )
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB_PATH))
    args = ap.parse_args()

    if not READLIB.is_dir():
        sys.exit(f"[错误] 找不到已读库目录: {READLIB}")
    items = collect_items()
    print(f"[已读库] 共发现 {len(items)} 条待导入")

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()
    inserted = updated = skipped = feeded = 0
    for it in items:
        row = cur.execute(
            "SELECT id, content_text FROM read_archive WHERE url = ?",
            (it["url"],),
        ).fetchone()
        body = strip_frontmatter(it["md"]).strip()
        summary = extract_summary(it["md"], it["meta"])
        platform = PLATFORM_LABELS.get(it["platform"], it["platform"] or "其他")
        tags = ["已读库", platform] + parse_theme_tags(it["theme"]) + it["extra_tags"]
        tags = [t for t in tags if t]
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        article_id = 0
        if row and len(row[1] or "") >= 50:
            skipped += 1
            article_id = row[0]
        elif row:
            cur.execute(
                """UPDATE read_archive SET title=?, author=?, summary=?, content_text=?,
                       tags=?, published_at=?, updated_at=? WHERE id=?""",
                (it["title"], it["author"], summary, body,
                 json.dumps(tags, ensure_ascii=False), it["date"], now, row[0]),
            )
            updated += 1
            article_id = row[0]
        else:
            cur.execute(
                """INSERT INTO read_archive (source_type, source_name, title, url, author,
                       summary, content_text, published_at, tags, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                ("read-archive", platform, it["title"], it["url"], it["author"],
                 summary, body, it["date"], json.dumps(tags, ensure_ascii=False),
                 now, now),
            )
            inserted += 1
            article_id = cur.lastrowid or 0
        # 存量条目（skipped）也补发事件，跑一次即完成全部回填。
        try:
            if feed_profile_event(
                cur,
                article_id=article_id,
                url=it["url"],
                title=it["title"],
                platform=platform,
                author=it["author"],
                tags=tags,
            ):
                feeded += 1
        except sqlite3.Error:
            print(f"[警告] 喂画像事件失败(不阻塞导入): {it['title']}")
    conn.commit()

    total = cur.execute(
        "SELECT COUNT(*) FROM read_archive"
    ).fetchone()[0]
    conn.close()
    print(f"[完成] 新增 {inserted} 条, 补全 {updated} 条, 跳过 {skipped} 条")
    print(f"[画像] 新喂入正向事件 {feeded} 条")
    print(f"[现状] read_archive 表共 {total} 条")


if __name__ == "__main__":
    main()
