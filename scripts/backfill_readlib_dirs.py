#!/usr/bin/env python3
"""补齐「README 有条目但没有已读库目录」的历史欠账。

背景（2026-09-08 盘点发现）：
  notes/已读库/README.md 有 88 条索引，但其中 34 条指向的 <目录>/content.md 不存在
  （多为 2026-09-01 从 mindback2 旧存档并入时只写了索引、未建目录）。
  这 34 条分两类：
    A 类（6 条）：连 read_archive 里都没有 —— 只在 notes/阅读收藏库/ 有 md/html 源文件
    B 类（26 条）：内容在 read_archive 里（有正文），只是文件目录缺失
  另有 2 条（59/65）实际在库中，只是 README 标题与库内标题不同，本脚本按"标题模糊匹配"
  命中后归入 B 类处理。

做法（只增不删，不覆盖任何已存在目录）：
    A 类：从 notes/阅读收藏库/<n>-<平台>-<标题>.md 生成四件套
    B 类：从 read_archive 行反向生成四件套

用法：
    python3 scripts/backfill_readlib_dirs.py            # 预演
    python3 scripts/backfill_readlib_dirs.py --apply    # 实际写入
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
READLIB = os.path.join(BASE, "notes", "已读库")
SESSLIB = os.path.join(BASE, "notes", "阅读收藏库")
DB_PATH = os.path.join(BASE, "data", "openbiliclaw.db")

# A 类：README 标题 -> 会话库源文件名（手工映射，保证准确）
A_MAP = {
    "Agent50道面试题（公开部分Q1-24）": "10-小红书-Agent50道面试题.md",
    "广告策略PM学习记录1：调价是什么？": "32-小红书-广告策略PM调价学习.md",
    "目前全网最全的查理·芒格思维模型知识库": "33-公众号-芒格思维模型知识库.md",
    "分享下我摸索了半年的 Vibe Coding 工作流": "34-公众号-VibeCoding工作流iPad加UU远程.md",
    "最近挖到一个还挺适合「记录控」的小工具": "35-小红书-MarkTimes记录控小工具.md",
    "开源项目Utopia：不会失忆的知识底座": "36-小红书-Utopia开源项目不会失忆的知识底座.md",
}

READING_TPL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;max-width:760px;margin:2em auto;padding:0 1em;line-height:1.7;color:#222}}
  h1,h2,h3{{color:#111}} h1{{border-bottom:2px solid #ddd;padding-bottom:.3em}}
  blockquote{{background:#f7f7f7;border-left:4px solid #bbb;padding:.8em 1em;margin:1em 0;color:#444}}
  .meta{{color:#888;font-size:.9em}}
</style>
</head>
<body>
<h1>{title}</h1>
<p class="meta">{metaline}</p>
<p class="meta">原文：<a href="{url}">{url}</a></p>
{body}
</body>
</html>
"""


def parse_readme():
    """返回 [(编号, 标题, 相对路径)]，仅保留路径不存在的条目。"""
    rd = open(os.path.join(READLIB, "README.md"), encoding="utf-8").read()
    rows = re.findall(r"^\|\s*(\d+)\s*\|\s*\[([^\]]+)\]\(([^)]+)\)", rd, re.M)
    out = []
    for num, title, path in rows:
        if not os.path.exists(os.path.join(READLIB, path)):
            out.append((num, title.strip(), path))
    return out


def parse_md_meta(text):
    """从会话库 md 头部抽取元信息（来源/作者/发布时间/互动/链接）。"""
    head = "\n".join(text.split("\n")[:20])
    meta = {"url": "", "author": "", "published_at": "", "stats": "", "platform": ""}
    m = re.search(r"来源\*?\*?:?\s*\[?[^\(]*\((https?://[^)\s]+)\)", head)
    if m:
        meta["url"] = m.group(1)
    if not meta["url"]:
        m = re.search(r"原链接\*?\*?:?\s*(https?://\S+)", head)
        if m:
            meta["url"] = m.group(1).rstrip("）)")
    m = re.search(r"作者\*?\*?:?\s*(.+)", head)
    if m:
        meta["author"] = m.group(1).strip().strip("*").strip()
    m = re.search(r"发布时间\*?\*?:?\s*([\d\-]+\s*[\d:]*|\d{4}-\d{2}-\d{2})", head)
    if m:
        meta["published_at"] = m.group(1).strip()
    m = re.search(r"互动数据\*?\*?:?\s*(.+)", head)
    if m:
        meta["stats"] = m.group(1).strip()
    m = re.search(r"平台\*?\*?:?\s*(.+)", head)
    if m:
        meta["platform"] = m.group(1).strip().strip("*").strip()
    return meta


def write_kit(dirname, *, title, content_md, meta_obj):
    """写入四件套；目录已存在则跳过（只增不覆盖）。"""
    d = os.path.join(READLIB, dirname)
    if os.path.exists(d):
        return "skip_exists"
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "content.md"), "w", encoding="utf-8") as f:
        f.write(content_md)
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta_obj, f, ensure_ascii=False, indent=2)
    stats_raw = meta_obj.get("stats") or {}
    stats_txt = stats_raw.get("note", "") if isinstance(stats_raw, dict) else str(stats_raw)
    metaline = " · ".join(
        x for x in [meta_obj.get("author", ""), meta_obj.get("source", ""),
                    meta_obj.get("published_at", ""), stats_txt] if x
    )
    body = "\n".join(
        f"<p>{line}</p>" if line.strip() and not line.startswith("#") else line
        for line in content_md.split("\n")
    )
    with open(os.path.join(d, "reading.html"), "w", encoding="utf-8") as f:
        f.write(READING_TPL.format(title=title, metaline=metaline,
                                   url=meta_obj.get("url", ""), body=body))
    with open(os.path.join(d, "raw.html"), "w", encoding="utf-8") as f:
        f.write(f"<!-- 原始抓取内容缺失，本条目由补录脚本生成 -->\n{content_md}")
    return "created"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际写入（默认预演）")
    args = ap.parse_args()

    dead = parse_readme()
    print(f"README 中目录缺失的条目：{len(dead)} 条")

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT id, source_name, title, url, author, summary, content_text, "
        "published_at, created_at, tags FROM read_archive"
    ).fetchall()

    a_done = b_done = skipped = 0
    plan = []
    for num, title, path in dead:
        dirname = path.split("/")[0]
        if title in A_MAP:
            src = os.path.join(SESSLIB, A_MAP[title])
            if not os.path.exists(src):
                print(f"  [A-缺失源文件] {num} {title}")
                continue
            text = open(src, encoding="utf-8").read()
            m = parse_md_meta(text)
            plan.append(("A", num, title, dirname, src, m, text))
        else:
            hit = None
            for r in rows:
                if r[2].strip() == title:
                    hit = r
                    break
            if hit is None:  # 标题不同 -> 取前 8 字包含匹配
                for r in rows:
                    if title[:8] and title[:8] in r[2]:
                        hit = r
                        break
            if hit is None:
                print(f"  [B-库中无正文] {num} {title}")
                continue
            plan.append(("B", num, title, dirname, hit, None, None))

    print(f"\n可补录：{len(plan)} 条（A 类 {sum(1 for p in plan if p[0]=='A')} / "
          f"B 类 {sum(1 for p in plan if p[0]=='B')}）\n")

    for kind, num, title, dirname, payload, meta_m, text in plan:
        if os.path.exists(os.path.join(READLIB, dirname)):
            skipped += 1
            continue
        if kind == "A":
            m = meta_m
            meta_obj = {
                "id": f"backfill_{num}",
                "title": title,
                "author": m["author"],
                "source": m["platform"] or "未知",
                "url": m["url"],
                "published_at": m["published_at"],
                "collected_at": "2026-09-08",
                "stats": {"note": m["stats"]},
                "tags": ["补录"],
                "files": {"meta": "meta.json", "raw": "raw.html",
                          "content": "content.md", "reading": "reading.html"},
                "summary": "",
                "note": "由 scripts/backfill_readlib_dirs.py 从会话库补录",
            }
            if args.apply:
                write_kit(dirname, title=title, content_md=text, meta_obj=meta_obj)
            a_done += 1
            print(f"  [A] {num:>3} {dirname}")
        else:
            (rid, sname, rtitle, url, author, summary,
             ctext, published, created, tags) = payload
            try:
                taglist = json.loads(tags) if tags else []
            except Exception:
                taglist = []
            meta_obj = {
                "id": f"ra_{rid}",
                "title": rtitle,
                "author": author or "",
                "source": sname or "",
                "url": url or "",
                "published_at": published or "",
                "collected_at": created or "",
                "stats": {},
                "tags": taglist,
                "files": {"meta": "meta.json", "raw": "raw.html",
                          "content": "content.md", "reading": "reading.html"},
                "summary": summary or "",
                "note": "由 scripts/backfill_readlib_dirs.py 从 read_archive 反向补录",
            }
            body = ctext or summary or ""
            md = f"# {rtitle}\n\n> 来源：{sname} · 作者：{author}\n> 发布：{published}\n\n{body}\n"
            if args.apply:
                write_kit(dirname, title=rtitle, content_md=md, meta_obj=meta_obj)
            b_done += 1
            print(f"  [B] {num:>3} {dirname}  (ra_id={rid}, {len(body)}字)")

    conn.close()
    prefix = "已写入" if args.apply else "预演（加 --apply 实际写入）"
    print(f"\n{prefix}：A 类 {a_done} 条、B 类 {b_done} 条、跳过(已存在) {skipped} 条")


if __name__ == "__main__":
    main()