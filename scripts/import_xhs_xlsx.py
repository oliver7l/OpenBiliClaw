#!/usr/bin/env python3
"""把「社媒助手」导出的小红书笔记 xlsx(含正文/作者/发布时间/媒体)离线导入阅读库(articles)。

适用场景:
  用「社媒助手」类工具批量导出的小红书笔记 xlsx, 已含完整正文与带 xsec_token 的链接,
  想直接入库而**不再联网重抓**(避开小红书验证码风控红线)。

xlsx 列(20 列):
  笔记ID, 笔记链接, 笔记类型, 笔记标题, 笔记内容, 点赞量, 收藏量, 评论量, 分享量,
  发布时间, 更新时间, IP地址, 博主ID, 博主链接, 博主昵称, 图片数量,
  笔记封面链接, 笔记图片链接, 笔记视频时长, 笔记视频链接

用法:
  python3 scripts/import_xhs_xlsx.py <xlsx文件|目录> [选项]

选项:
  --media-root <dir>   本地媒体根目录(默认=首个 xlsx 所在目录), 结构 <作者>/<笔记ID>/<文件>
  --no-copy-media      不复制本地媒体(只入正文+链接)
  --dry-run            只打印不写库
  --limit N            最多处理 N 条(调试)

媒体:
  <media-root> 下所有 24 位 hex 文件夹名 = 笔记ID, 其内 jpg/mp4 复制到 images/xhs/<笔记ID>/,
  正文末尾追加「本地媒体」清单(相对路径, 长期可用)。

去重(幂等):
  按 note_id 在 url 中匹配。已存在 -> 用本次 xlsx 内容补强(UPDATE); 不存在 -> 插入。
  只增/只改, 绝不删数据。
"""
from __future__ import annotations

import argparse
import datetime

# 中国本地时间(UTC+8)。articles 表所有时间字段统一存北京时间字符串。
CN_TZ = datetime.timezone(datetime.timedelta(hours=8))
import glob
import json
import os
import re
import shutil
import sqlite3
import sys

try:
    import openpyxl
except ImportError:
    sys.exit("需要先安装 openpyxl: pip install openpyxl")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE, "data", "openbiliclaw.db")
IMG_ROOT = os.path.join(BASE, "images", "xhs")
NOTE_RE = re.compile(r"^[0-9a-f]{24}$")
TOPIC_RE = re.compile(r"#([^\s#\[\]]{2,20})\[话题\]")


def _now() -> str:
    return datetime.datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _build_media_map(media_root: str) -> tuple[dict[str, list[str]], dict[str, str]]:
    """返回 (note_id -> [媒体文件], note_id -> 媒体所在目录)。"""
    m: dict[str, list[str]] = {}
    src_dir: dict[str, str] = {}
    if not media_root or not os.path.isdir(media_root):
        return m, src_dir
    for root, _dirs, files in os.walk(media_root):
        leaf = os.path.basename(root)
        if NOTE_RE.match(leaf):
            media = [f for f in files if not f.startswith(".")]
            if media:
                m[leaf] = sorted(media)
                src_dir[leaf] = root
    return m, src_dir


def _topics(text: str) -> list[str]:
    return sorted({t for t in TOPIC_RE.findall(text or "")})


def _media_block(note_id: str, media: list[str]) -> str:
    d = os.path.join(IMG_ROOT, note_id)
    if not os.path.isdir(d):
        return ""
    files = sorted(f for f in os.listdir(d) if not f.startswith("."))
    if not files:
        return ""
    lines = []
    for f in files:
        kind = "（视频）" if f.lower().endswith(".mp4") else ""
        lines.append(f"- images/xhs/{note_id}/{f}{kind}")
    return "\n\n---\n\n## 本地媒体\n\n" + "\n".join(lines) + "\n"


def _published_str(val) -> str:
    if isinstance(val, datetime.datetime):
        return val.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(val, str) and val.strip():
        return val.strip()
    return _now()


def _iter_rows(paths: list[str]):
    for p in paths:
        wb = openpyxl.load_workbook(p, read_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        header = [str(h).strip() if h is not None else "" for h in rows[0]]
        idx = {h: i for i, h in enumerate(header)}
        for row in rows[1:]:
            if not row or not row[0]:
                continue
            get = lambda k: (row[idx[k]] if idx.get(k, -1) >= 0 and idx[k] < len(row) else None)
            yield {
                "note_id": str(row[0]).strip(),
                "url": get("笔记链接") or "",
                "note_type": get("笔记类型") or "",
                "title": (get("笔记标题") or "") or "",
                "content": get("笔记内容"),
                "likes": get("点赞量"),
                "collects": get("收藏量"),
                "comments": get("评论量"),
                "shares": get("分享量"),
                "published": get("发布时间"),
                "author": (get("博主昵称") or "") or "",
                "img_links": get("笔记图片链接") or "",
                "video_links": get("笔记视频链接") or "",
            }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+", help="xlsx 文件或含 xlsx 的目录")
    ap.add_argument("--media-root", default=None, help="本地媒体根目录(默认=首个 xlsx 所在目录)")
    ap.add_argument("--no-copy-media", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    paths: list[str] = []
    for inp in args.inputs:
        if os.path.isdir(inp):
            paths += sorted(glob.glob(os.path.join(inp, "*.xlsx")))
        elif inp.lower().endswith(".xlsx"):
            paths.append(inp)
        else:
            print(f"[跳过] 非 xlsx: {inp}")
    if not paths:
        sys.exit("没有可处理的 xlsx")
    media_root = args.media_root or os.path.dirname(paths[0])
    print(f"=== xlsx: {len(paths)} 个 | media-root: {media_root} | copy_media={not args.no_copy_media}")

    media_map, src_dir_map = _build_media_map(media_root)
    print(f"=== 本地媒体映射: {len(media_map)} 个笔记ID")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    stats = {"insert": 0, "update": 0, "skip": 0, "media_copied": 0}
    n = 0

    for r in _iter_rows(paths):
        if args.limit and n >= args.limit:
            break
        n += 1
        nid = r["note_id"]
        title = (r["title"] or "").strip() or f"小红书笔记 {nid[:8]}"
        content = (r["content"] or "").strip()
        author = (r["author"] or "").strip()
        note_type = (r["note_type"] or "").strip()
        url = (r["url"] or "").strip()
        if not url:
            url = f"https://www.xiaohongshu.com/explore/{nid}"

        media = media_map.get(nid, [])
        # 复制本地媒体(dry-run 不复制, 保持只读)
        if media and not args.no_copy_media and not args.dry_run:
            dst = os.path.join(IMG_ROOT, nid)
            os.makedirs(dst, exist_ok=True)
            src_dir = src_dir_map.get(nid)
            for f in media:
                src = os.path.join(src_dir, f) if src_dir else None
                if src and os.path.exists(src) and not os.path.exists(os.path.join(dst, f)):
                    shutil.copy2(src, os.path.join(dst, f))
                    stats["media_copied"] += 1

        body = content
        if not body:
            body = title + "\n\n[本笔记无文字正文" + (f"；类型：{note_type}" if note_type else "") + "；见下方本地媒体]" if media else title + "\n\n[本笔记无文字正文]"
        body += _media_block(nid, media)

        topics = _topics(content)
        tags = ["小红书"]
        if author:
            tags.append(author)
        tags += topics
        tags.append("本地导入")
        tags = list(dict.fromkeys(tags))  # 去重保序

        eng = []
        for label, key in (("👍", "likes"), ("⭐", "collects"), ("💬", "comments"), ("🔁", "shares")):
            v = r[key]
            if v not in (None, ""):
                try:
                    eng.append(f"{label}{int(v)}")
                except (ValueError, TypeError):
                    eng.append(f"{label}{v}")
        summary = " · ".join(x for x in [author, note_type] + eng if x)
        published_at = _published_str(r["published"])

        # 去重: 按 note_id 匹配已有 url
        row = cur.execute(
            "SELECT id, content_text, url FROM articles WHERE url LIKE ?",
            (f"%{nid}%",),
        ).fetchone()

        if row:
            rid, old_content, old_url = row
            new_len = len(body)
            old_len = len(old_content or "")
            # 已存在: 用 xlsx 权威内容补强(尤其补上 xsec_token 链接 / 媒体)
            if not args.dry_run:
                cur.execute(
                    """UPDATE articles
                       SET url=?, title=?, content_text=?, tags=?, summary=?, published_at=?, updated_at=?
                       WHERE id=?""",
                    (url, title, body, json.dumps(tags, ensure_ascii=False), summary,
                     published_at, _now(), rid),
                )
            stats["update"] += 1
            tag = "补强" if (new_len > old_len or "xsec_token" not in (old_url or "")) else "已存在"
            print(f"[{tag}] id={rid} {old_len}->{new_len}字 | {author} | {title[:30]}")
            continue

        if not args.dry_run:
            try:
                cur.execute(
                    """INSERT INTO articles
                       (source_type, source_name, title, url, author, summary, content_text,
                        published_at, tags, status, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    ("xiaohongshu", author or "xiaohongshu", title, url, author,
                     summary, body, published_at,
                     json.dumps(tags, ensure_ascii=False), "unread", _now(), _now()),
                )
            except sqlite3.IntegrityError:
                # URL 已存在(不同 token) -> 跳过
                print(f"[跳过-URL冲突] {title[:30]} | {url[:50]}")
                stats["skip"] += 1
                continue
        stats["insert"] += 1
        print(f"[入库] {len(body)}字 | {author} | {published_at} | {title[:30]}")

    if args.dry_run:
        conn.rollback()
        print("\n[dry-run] 未写库")
    else:
        conn.commit()
    conn.close()
    print(f"\n汇总: 新增 {stats['insert']} / 补强 {stats['update']} / 跳过 {stats['skip']} / 复制媒体 {stats['media_copied']} 个文件")


if __name__ == "__main__":
    main()
