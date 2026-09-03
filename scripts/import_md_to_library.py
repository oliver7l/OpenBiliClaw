#!/usr/bin/env python3
"""把本地 Markdown 笔记(离线抓好的正文/OCR/解读)导入阅读库(articles)。

适用场景:
  之前用 xhs / zhihu CLI 抓好、手工整理成 md 放在本地目录的笔记,
  想直接入库而**不再联网重抓**(避开小红书验证码风控红线)。

用法:
  python3 scripts/import_md_to_library.py <md目录> [选项]

选项:
  --attach "<note_id>=<文件名.md>"   把该 md 作为「延伸研究」附录合并进对应笔记(可多次)
  --dry-run                          只打印不写库

md 约定(尽量宽松, 缺就跳过该字段):
  - 首个 `# ` 标题       -> title
  - `**作者**：X`        -> author / source_name
  - `**平台**：小红书`   -> source_type=xiaohongshu
  - `**发布时间**：...`  -> published_at (解析不出用文件 mtime)
  - 结尾 `ID: <24位hex>` -> note_id, 拼出 https://www.xiaohongshu.com/explore/<id>
  - 没有 ID 的文件默认跳过(除非被 --attach 指定为附录)

图片:
  若 images/xhs/<note_id>/ 存在, 正文末尾自动追加「本地图片」清单(相对路径, 长期可用)。

去重(幂等):
  按 note_id 匹配已有 url。已存在且正文更长 -> 跳过(不覆盖更长版);
  已存在但更短/空壳 -> 用本次内容补强。只增/只改, 绝不删数据。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sqlite3
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE, "data", "openbiliclaw.db")
IMG_ROOT = os.path.join(BASE, "images", "xhs")

ID_RE = re.compile(r"ID[:：]\s*([0-9a-fA-F]{16,32})")
H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")


def _meta(text: str, key: str) -> str:
    m = re.search(rf"\*\*{key}\*\*[:：]\s*(.+)", text)
    return m.group(1).strip().rstrip("　 ") if m else ""


def _parse_published(text: str, path: str) -> str:
    raw = _meta(text, "发布时间")
    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})\D*(?:(\d{1,2})[:：](\d{2}))?", raw)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh = int(m.group(4) or 0)
        mm = int(m.group(5) or 0)
        try:
            return datetime.datetime(y, mo, d, hh, mm).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    ts = os.path.getmtime(path)
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _topics(text: str) -> list[str]:
    return sorted({t for t in re.findall(r"#([^\s#\[\]]{2,20})\[话题\]", text)})


def _image_block(note_id: str) -> str:
    d = os.path.join(IMG_ROOT, note_id)
    if not os.path.isdir(d):
        return ""
    files = sorted(f for f in os.listdir(d) if not f.startswith("."))
    if not files:
        return ""
    lines = [f"- images/xhs/{note_id}/{f}" for f in files]
    return "\n\n---\n\n## 本地图片\n\n" + "\n".join(lines) + "\n"


def _load(path: str) -> dict | None:
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    h1 = H1_RE.search(text)
    if not h1:
        return None
    note_id_m = ID_RE.search(text)
    platform = _meta(text, "平台")
    return {
        "path": path,
        "file": os.path.basename(path),
        "text": text,
        "title": h1.group(1).strip(),
        "author": _meta(text, "作者"),
        "platform": platform,
        "note_id": note_id_m.group(1) if note_id_m else "",
        "published_at": _parse_published(text, path),
        "topics": _topics(text),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--attach", action="append", default=[],
                    help='"<note_id>=<文件名.md>" 作为附录合并进该笔记')
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    folder = os.path.abspath(args.folder)
    if not os.path.isdir(folder):
        sys.exit(f"目录不存在: {folder}")

    attach_map: dict[str, list[str]] = {}
    for spec in args.attach:
        if "=" not in spec:
            sys.exit(f"--attach 格式应为 '<note_id>=<文件名.md>', 收到: {spec}")
        nid, fname = spec.split("=", 1)
        attach_map.setdefault(nid.strip(), []).append(fname.strip())
    attached_files = {f for lst in attach_map.values() for f in lst}

    docs = []
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith(".md"):
            continue
        d = _load(os.path.join(folder, name))
        if d is None:
            print(f"[跳过] 无 H1 标题: {name}")
            continue
        docs.append(d)

    by_file = {d["file"]: d for d in docs}
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    stats = {"insert": 0, "update": 0, "skip": 0}

    for d in docs:
        if d["file"] in attached_files:
            continue
        if not d["note_id"]:
            print(f"[跳过] 无笔记 ID(未被 --attach 指定): {d['file']}")
            continue

        nid = d["note_id"]
        body = d["text"].rstrip()
        for fname in attach_map.get(nid, []):
            extra = by_file.get(fname)
            if not extra:
                print(f"[警告] --attach 找不到文件: {fname}")
                continue
            body += "\n\n---\n\n## 延伸研究（" + extra["title"] + "）\n\n" + extra["text"].strip()
            print(f"       + 附录合并: {fname} ({len(extra['text'])} 字)")
        body += _image_block(nid)

        is_xhs = "小红书" in (d["platform"] or "")
        source_type = "xiaohongshu" if is_xhs else "local"
        url = f"https://www.xiaohongshu.com/explore/{nid}" if is_xhs else f"local://md/{nid}"
        tags = (["小红书"] if is_xhs else ["本地笔记"]) + d["topics"] + ["本地导入"]
        summary = f"{d['author'] or '未知作者'} · {d['platform'] or '本地'}"
        for key in ("点赞", "收藏"):
            v = _meta(d["text"], key)
            if v:
                summary += f" · {key} {v.split('|')[0].strip()}"

        row = cur.execute(
            "SELECT id, title, content_text, tags FROM articles WHERE url LIKE ?",
            (f"%{nid}%",),
        ).fetchone()

        if row:
            rid, old_title, old_content, old_tags_json = row
            old_len = len(old_content or "")
            if old_len >= len(body):
                stats["skip"] += 1
                print(f"[跳过] 已存在且不更短({old_len}字 >= {len(body)}字): {old_title!r} id={rid}")
                continue
            merged = []
            for t in (json.loads(old_tags_json or "[]") or []) + tags:
                if t not in merged:
                    merged.append(t)
            if not args.dry_run:
                cur.execute(
                    """UPDATE articles
                       SET title=?, content_text=?, tags=?, summary=?, published_at=?, updated_at=?
                       WHERE id=?""",
                    (old_title or d["title"], body, json.dumps(merged, ensure_ascii=False),
                     summary, d["published_at"], _now(), rid),
                )
            stats["update"] += 1
            print(f"[补强] id={rid} {old_len} -> {len(body)} 字: {d['title']}")
            continue

        if not args.dry_run:
            cur.execute(
                """INSERT INTO articles
                   (source_type, source_name, title, url, author, summary, content_text,
                    published_at, tags, status, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (source_type, d["author"] or source_type, d["title"], url, d["author"],
                 summary, body, d["published_at"],
                 json.dumps(tags, ensure_ascii=False), "unread", _now(), _now()),
            )
        stats["insert"] += 1
        print(f"[入库] {len(body)} 字 | {d['author']} | {d['published_at']} | {d['title']}")
        print(f"       url={url}")
        print(f"       tags={tags}")

    if args.dry_run:
        conn.rollback()
        print("\n[dry-run] 未写库")
    else:
        conn.commit()
    conn.close()
    print(f"\n汇总: 新增 {stats['insert']} / 补强 {stats['update']} / 跳过 {stats['skip']}")


if __name__ == "__main__":
    main()
