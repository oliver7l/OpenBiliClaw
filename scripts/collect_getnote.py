#!/usr/bin/env python3
"""把 GetNote 本地网页剪藏(离线 HTML)收进阅读库(articles)。

数据来源: mindback2 的 getnote 本地剪藏目录, 每个 HTML 是一篇用户在浏览器里
"剪藏"的网页(含 标题 / 创建于 / 标签 / 正文)。注意: 真正的原网页 URL 在
`<div id="jsonData" data-json="...">` 加密字段里, 前端脚本并不解密, 因此绝大多数
剪藏**没有可追溯的原 URL**。为保持幂等且不与其他源冲突, 我们用合成 URL
`getnote://<file_id>`(file_id = 文件名去 .html) 作为唯一键; 若正文里带
"原文：<a href>" 链接则把原链接补到正文顶部, 方便回溯。

用法:
  python3 scripts/collect_getnote.py                 # 全量灌库(按 file_id 去重)
  python3 scripts/collect_getnote.py --dry-run       # 只数数不写库
  python3 scripts/collect_getnote.py --src /path      # 指定 getnote 目录

行为:
  - 遍历 src 目录下所有 *.html(跳过非剪藏文件)
  - 解析每篇: <h1>标题 / 创建于时间 / <span class="tag">标签 / <hr>后正文
  - 按 url=getnote://<file_id> 去重 upsert:
      * 不存在 -> INSERT (published_at 用 创建于 时间, 历史按序排)
      * 已存在且 content_text 为空 -> 补填正文(保护已有值)
      * 已存在且 content_text 非空 -> 跳过(绝不覆盖用户/已有数据)
  - source_type='getnote', source_name='GetNote', tags=["GetNote"]+剪藏标签
幂等: 重复运行安全; 不删数据、不改已有正文。

依赖: 仅标准库。
"""
from __future__ import annotations

import argparse
import datetime as dt

# 中国本地时间(UTC+8)。articles 表所有时间字段统一存北京时间字符串。
CN_TZ = dt.timezone(dt.timedelta(hours=8))
import html
import json
import os
import re
import sqlite3
import sys
from html.parser import HTMLParser

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE, "data", "openbiliclaw.db")
DEFAULT_SRC = (
    "/Volumes/未命名/未命名文件夹/2026年04月27日-备份项目/"
    "2026年05月09日-SQLiteDB/mindback_data/getnote"
)

PLATFORM = "getnote"
SOURCE_NAME = "GetNote"

# HTML -> 文本 时, 这些块级标签前后插入换行
_BLOCK_TAGS = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "blockquote", "section"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _BLOCK_TAGS or tag in ("br", "hr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def _html_to_text(s: str) -> str:
    p = _TextExtractor()
    try:
        p.feed(s)
    except Exception:  # noqa: BLE001
        pass
    raw = "".join(p.parts)
    lines = [ln.rstrip() for ln in raw.split("\n")]
    out: list[str] = []
    blank = 0
    for ln in lines:
        if ln.strip() == "":
            blank += 1
            if blank <= 1:
                out.append("")
        else:
            blank = 0
            out.append(ln)
    return "\n".join(out).strip()


def _parse_note(raw: str, file_id: str) -> dict | None:
    """从单篇 HTML 提取结构化字段; 非剪藏文件返回 None。"""
    # 标题: <div class="note"> 内首个 <h1>
    m = re.search(r'<div class="note">\s*<h1>(.*?)</h1>', raw, re.S)
    title = html.unescape(m.group(1).strip()) if m else ""

    # 创建于时间
    m = re.search(r"创建于：\s*([0-9]{4}-[0-9]{2}-[0-9]{2}[ 0-9:]*)", raw)
    published = m.group(1).strip() if m else ""
    # 没有 创建于 的大概率是模板/索引页, 跳过
    if not published:
        return None

    # 标签
    tags = [html.unescape(t).strip() for t in re.findall(r'<span class="tag">(.*?)</span>', raw)]
    tags = [t for t in tags if t]

    # 原文链接(部分剪藏带 原文：<a href>)
    m = re.search(r'原文：\s*<a\s+href="([^"]+)"', raw)
    src_url = html.unescape(m.group(1).strip()) if m else ""

    # 正文: 取 <hr> 之后的内容(已含 原文链接 / 正文), 剥离头部 meta
    idx = raw.find("<hr")
    body_html = raw[idx:] if idx != -1 else raw
    content = _html_to_text(body_html)

    # 标题兜底: 用正文首句
    if not title:
        first = next((ln.strip() for ln in content.split("\n") if ln.strip()), "")
        title = first[:80] or file_id

    # 把真实原文链接补到正文顶部, 便于回溯(没有就留空)
    if src_url:
        content = f"原文链接：{src_url}\n\n{content}"

    # 摘要: 正文前 200 字
    summary = content[:200].replace("\n", " ").strip()

    all_tags = [SOURCE_NAME] + tags

    return {
        "source_type": PLATFORM,
        "source_name": SOURCE_NAME,
        "title": title,
        "url": f"getnote://{file_id}",
        "author": "",
        "summary": summary,
        "content_text": content,
        "published_at": published,
        "tags": json.dumps(all_tags, ensure_ascii=False),
    }


def _iter_notes(src_dir: str):
    if not os.path.isdir(src_dir):
        print(f"! getnote 目录不存在: {src_dir}")
        return
    files = sorted(f for f in os.listdir(src_dir) if f.endswith(".html"))
    for fname in files:
        fpath = os.path.join(src_dir, fname)
        file_id = fname[: -len(".html")]
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
                raw = fh.read()
        except Exception as e:  # noqa: BLE001
            print(f"  ! 读取失败 {fname}: {e}")
            continue
        art = _parse_note(raw, file_id)
        if art is None:
            continue
        yield art


def collect(src_dir: str, *, dry_run: bool) -> dict:
    total = inserted = filled = skipped = ignored = 0
    conn = None if dry_run else sqlite3.connect(DB_PATH)
    # v0.4.0+: articles 表迁移到 content.db，ATTACH 以便跨库查询
    try:
        from pathlib import Path as _Path
        _content_db = _Path(__file__).parent.parent / "data" / "content.db"
        if _content_db.exists():
            conn.execute("ATTACH DATABASE ? AS content", (str(_content_db),))
    except Exception:
        pass


    cur = None if dry_run else conn.cursor()
    now_iso = dt.datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")

    for art in _iter_notes(src_dir):
        total += 1
        if dry_run:
            inserted += 1
            continue

        row = cur.execute(
            "SELECT id, content_text FROM articles WHERE url=?", (art["url"],)
        ).fetchone()
        if row is None:
            cur.execute(
                """INSERT INTO articles
                   (source_type, source_name, title, url, author, summary, content_text,
                    published_at, tags, status, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (art["source_type"], art["source_name"], art["title"], art["url"],
                 art["author"], art["summary"], art["content_text"], art["published_at"],
                 art["tags"], "unread", now_iso, now_iso),
            )
            inserted += 1
        else:
            existing_body = (row[1] or "") if row[1] is not None else ""
            if not existing_body.strip() and art["content_text"]:
                cur.execute(
                    """UPDATE articles
                       SET content_text = CASE WHEN content_text IS NULL OR TRIM(content_text)='' THEN ? ELSE content_text END,
                           summary = CASE WHEN summary IS NULL OR TRIM(summary)='' THEN ? ELSE summary END,
                           tags = CASE WHEN tags IS NULL OR TRIM(tags)='' THEN ? ELSE tags END,
                           updated_at = ?
                       WHERE id = ?""",
                    (art["content_text"], art["summary"], art["tags"], now_iso, row[0]),
                )
                filled += 1
            else:
                skipped += 1

    if not dry_run:
        conn.commit()
        conn.close()

    return {
        "total": total,
        "inserted": inserted,
        "filled": filled,
        "skipped": skipped,
        "ignored": ignored,
        "dry_run": dry_run,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="把 GetNote 本地剪藏收进阅读库")
    ap.add_argument("--src", default=DEFAULT_SRC, help="getnote 剪藏目录")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = ap.parse_args()

    print(f"[解析] 遍历 {args.src}/*.html ...")
    stats = collect(args.src, dry_run=args.dry_run)

    mode = "DRY-RUN" if stats["dry_run"] else "写入"
    print(
        f"[{mode}] 候选 {stats['total']} | 新增 {stats['inserted']} | "
        f"补空正文 {stats['filled']} | 跳过(已有) {stats['skipped']} | "
        f"忽略(非剪藏) {stats['ignored']}"
    )
    if stats["dry_run"]:
        print("(dry-run 模式未写库)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
