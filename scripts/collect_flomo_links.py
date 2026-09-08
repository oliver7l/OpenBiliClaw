#!/usr/bin/env python3
"""把 flomo 笔记里"带链接"的 memo 收进阅读库(articles) —— 仅阅读类, 剔除敏感条目。

flomo 官方导出是 `html/keedor的笔记.html`(所有 memo 在一个 HTML 里), 每条 memo
含 时间 / 正文(可能带 #标签 与 URL) / 图片。本脚本只挑**带 URL 且属于阅读平台**的
memo 导入, 让它们和你已有的 zhihu/bilibili/xiaohongshu/xiaoyuzhou/wechat/douyin/douban
源**按真实 URL 合并去重**(已存在则跳过, 不新建重复)。

已确认可导入(阅读类)平台 -> 映射到现有 source_type:
  zhihu.com / zhuanlan.zhihu.com     -> zhihu   (知乎)
  bilibili.com / b23.tv              -> bilibili( B站)
  xiaohongshu.com / xhslink.com      -> xiaohongshu(小红书)
  xiaoyuzhoufm.com                   -> xiaoyuzhou(小宇宙)
  mp.weixin.qq.com                   -> wechat  (微信)
  douyin.com / v.douyin.com          -> douyin  (抖音)
  douban.com                         -> douban  (豆瓣)

强制剔除(非阅读 / 含敏感凭据):
  网盘: pan.quark.cn 115cdn.com www.kdocs.cn kdocs.cn
  机场: get.api-biubiu.sbs www.yfjc.xyz (subscribe?token=)
  账号: cloudsaver.wanglaohu.com.cn(含账号密码) mai.91kami.com(提卡)
  其他: help.flomoapp.com(推广) types.yuzeli.com(人格测试问卷)
其余未列平台(sohu/baidu/baijiahao 等)也跳过, 保持库干净。

用法:
  python3 scripts/collect_flomo_links.py              # 全量灌库(按 URL 去重)
  python3 scripts/collect_flomo_links.py --dry-run    # 只统计不写库
  python3 scripts/collect_flomo_links.py --src /path   # 指定 flomo HTML

行为: 每条 memo 取首个命中的阅读平台 URL 为主键; 同 URL 跨 memo 去重;
  SELECT by url -> 已存在则跳过(与现有源合并), 否则 INSERT(source_type=平台,
  source_name=平台中文名, tags=["flomo"]+平台标签+#标签, content_text=原 memo 全文)。
幂等: 重复运行安全。

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

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE, "data", "openbiliclaw.db")
DEFAULT_SRC = (
    "/Volumes/未命名/未命名文件夹/2026年04月27日-备份项目/"
    "2026年05月09日-SQLiteDB/mindback_data/flomo/html/keedor的笔记.html"
)

URL_RE = re.compile(r'https?://[^\s"<>\)\]】）\s]+', re.I)
TAG_RE = re.compile(r'#([^\s#@]+)')

# 平台域名 -> (source_type, source_name)
INCLUDE = [
    ("zhuanlan.zhihu.com", "zhihu", "知乎"),
    ("www.zhihu.com", "zhihu", "知乎"),
    ("zhihu.com", "zhihu", "知乎"),
    ("b23.tv", "bilibili", "B站"),
    ("bilibili.com", "bilibili", "B站"),
    ("xhslink.com", "xiaohongshu", "小红书"),
    ("xiaohongshu.com", "xiaohongshu", "小红书"),
    ("xiaoyuzhoufm.com", "xiaoyuzhou", "小宇宙"),
    ("mp.weixin.qq.com", "wechat", "微信"),
    ("v.douyin.com", "douyin", "抖音"),
    ("douyin.com", "douyin", "抖音"),
    ("douban.com", "douban", "豆瓣"),
]

# 强制剔除域名(含敏感凭据或非阅读)
EXCLUDE_DOMAINS = [
    "pan.quark.cn", "115cdn.com", "kdocs.cn", "cloudsaver.wanglaohu.com.cn",
    "mai.91kami.com", "get.api-biubiu.sbs", "www.yfjc.xyz",
    "help.flomoapp.com", "types.yuzeli.com",
]


def _domain_of(url: str) -> str:
    m = re.match(r'https?://([^/]+)/?', url, re.I)
    return m.group(1).lower() if m else ""


def _classify(url: str):
    """返回 (source_type, source_name) 或 None(剔除/不支持)。"""
    dom = _domain_of(url)
    if any(b in dom for b in EXCLUDE_DOMAINS):
        return None
    for needle, st, sn in INCLUDE:
        if needle in dom:
            return st, sn
    return None


def _clean_url(u: str) -> str:
    u = html.unescape(u)
    # 去掉尾部常见标点
    u = u.rstrip("，。、】）).,;；")
    return u


def _parse_memos(raw: str):
    """yield (time_str, full_text, urls)"""
    parts = re.split(r'<div class="memo">', raw)
    for blk in parts[1:]:
        mt = re.search(r'class="time">(.*?)</div>', blk, re.S)
        time = html.unescape(mt.group(1).strip()) if mt else ""
        mc = re.search(
            r'class="content">(.*?)(?:<div class="files">|</div>\s*</div>\s*</div>)',
            blk, re.S,
        )
        content_html = mc.group(1) if mc else blk
        text = re.sub(r'<[^>]+>', ' ', content_html)
        text = html.unescape(text)
        text = re.sub(r'\s+', ' ', text).strip()
        urls = [_clean_url(u) for u in URL_RE.findall(content_html)]
        if time or text:
            yield time, text, urls


def _build_article(time: str, text: str, urls: list[str]) -> dict | None:
    # 选首个命中的阅读平台 URL 作为主键
    primary = None
    klass = None
    for u in urls:
        k = _classify(u)
        if k is not None:
            primary, klass = u, k
            break
    if primary is None:
        return None

    source_type, source_name = klass
    # 标题: 去掉 URL 与 #标签 后的首句; 否则用 URL
    title_src = URL_RE.sub("", text)
    title_src = TAG_RE.sub("", title_src).strip()
    title = title_src.split("\n")[0][:80].strip() if title_src else ""
    if not title:
        title = f"{source_name}链接收藏"

    # 标签: flomo + 平台 + #标签(去#)
    hashtags = [t for t in TAG_RE.findall(text)]
    tags = ["flomo", source_name] + hashtags
    # 去重保序
    seen = set()
    tags = [t for t in tags if not (t in seen or seen.add(t))]

    published = time if re.match(r"\d{4}-\d{2}-\d{2}", time) else ""
    summary = text[:200]

    return {
        "source_type": source_type,
        "source_name": source_name,
        "title": title,
        "url": primary,
        "author": "",
        "summary": summary,
        "content_text": text,
        "published_at": published,
        "tags": json.dumps(tags, ensure_ascii=False),
    }


def collect(src: str, *, dry_run: bool) -> dict:
    with open(src, "r", encoding="utf-8", errors="replace") as fh:
        raw = fh.read()

    total = inserted = skipped = excluded = other = 0
    seen_urls: set[str] = set()
    conn = None if dry_run else sqlite3.connect(DB_PATH)
    cur = None if dry_run else conn.cursor()
    now_iso = dt.datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")

    for time, text, urls in _parse_memos(raw):
        # 该 memo 是否整体该剔除(含敏感域名且无阅读域名)
        has_include = any(_classify(u) is not None for u in urls)
        has_exclude = any(_domain_of(u) in EXCLUDE_DOMAINS for u in urls)
        if not has_include:
            if has_exclude:
                excluded += 1
            else:
                other += 1
            continue

        art = _build_article(time, text, urls)
        if art is None:
            other += 1
            continue
        if art["url"] in seen_urls:  # 同 URL 跨 memo 去重
            skipped += 1
            continue
        seen_urls.add(art["url"])
        total += 1

        if dry_run:
            inserted += 1
            continue

        row = cur.execute(
            "SELECT id FROM articles WHERE url=?", (art["url"],)
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
            skipped += 1

    if not dry_run:
        conn.commit()
        conn.close()

    return {
        "total": total, "inserted": inserted, "skipped": skipped,
        "excluded": excluded, "other": other, "dry_run": dry_run,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="把 flomo 链接型笔记收进阅读库(仅阅读类)")
    ap.add_argument("--src", default=DEFAULT_SRC, help="flomo 导出 HTML 路径")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = ap.parse_args()

    if not os.path.isfile(args.src):
        print(f"! flomo HTML 不存在: {args.src}")
        return 1

    print(f"[解析] {args.src}")
    stats = collect(args.src, dry_run=args.dry_run)

    mode = "DRY-RUN" if stats["dry_run"] else "写入"
    print(
        f"[{mode}] 待导入 {stats['total']} | 新增 {stats['inserted']} | "
        f"跳过(已存在/同URL) {stats['skipped']} | "
        f"剔除(网盘/机场/账号敏感) {stats['excluded']} | "
        f"跳过(其他平台) {stats['other']}"
    )
    if stats["dry_run"]:
        print("(dry-run 模式未写库)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
