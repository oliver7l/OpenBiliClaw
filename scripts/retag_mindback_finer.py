#!/usr/bin/env python3
"""给 mindback 调度缓存导入的文章补「更细」标签(只追加, 不删原有标签)。

背景: collect_mindback_caches.py 导入时只打了 [平台, mindback] 粗标签。
这里回捞更细的来源/分类元数据, 按 url 合并进现有 tags:
  - bilibili       -> UP主(articles.author 列, 导入时已存 owner.name)
  - xiaohongshu    -> 作者(articles.author 列, 导入时已存 userName/nickname)
  - youtube        -> 频道名(channel, 从观看历史 txt 回捞)
  - xiaoyuzhou     -> 播客名(source 字段, 注意不在 author)
  - zhihu          -> 内容类型(回答/文章/想法, 从推荐流回捞)

注: bilibili/xiaohongshu 的推荐缓存本身不含分区/话题元数据, 故用"作者/UP主"
    这一可离线获取的更细维度; 想拿分区/话题需另调详情接口(本脚本不联网)。

安全:
  - 只更新带 'mindback' 标签的行(即本次导入批次), 不动手工精选/实时源文章。
  - 合并用集合去重, 保留原顺序, 重复运行幂等。
  - 不修改正文/url/作者等任何其它字段。

用法:
  python3 scripts/retag_mindback_finer.py [--dry-run]
"""
import datetime
import glob
import json
import os
import re
import sqlite3
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE, "data", "openbiliclaw.db")
MIND = "/Volumes/未命名/未命名文件夹/2026年04月27日-备份项目/2026年05月09日-SQLiteDB/mindback_data"

XHS_ID_RE = re.compile(r"/explore/([0-9a-zA-Z]+)")
ZHIHU_API_RE = re.compile(r"api\.zhihu\.com/(answers|articles|pins)/(\d+)")
KIND_CN = {"answers": "回答", "articles": "文章", "pins": "想法"}


def _norm_xhs(url):
    m = XHS_ID_RE.search(url or "")
    return f"https://www.xiaohongshu.com/explore/{m.group(1)}" if m else url


def _clean(s):
    return re.sub(r"\s+", " ", (s or "").strip())


# ---------- 各源: 返回 {normalized_url: [finer_tags]} ----------
def finer_from_db_author(source_type):
    """bilibili / xiaohongshu: 作者已存在 articles.author 列, 直接作更细标签。"""
    out = {}
    conn = sqlite3.connect(DB_PATH)
    # v0.4.0+: articles 表迁移到 content.db，ATTACH 以便跨库查询
    try:
        from pathlib import Path as _Path
        _content_db = _Path(__file__).parent.parent / "data" / "content.db"
        if _content_db.exists():
            conn.execute("ATTACH DATABASE ? AS content", (str(_content_db),))
    except Exception:
        pass


    for url, author in conn.execute(
        "SELECT url, author FROM articles WHERE source_type=? AND tags LIKE '%mindback%'",
        (source_type,),
    ):
        a = _clean(author)
        if a:
            out[_norm_xhs(url) if source_type == "xiaohongshu" else url] = [a]
    conn.close()
    return out


def finer_youtube():
    out = {}
    for fp in sorted(glob.glob(os.path.join(MIND, "youtube-fetcher", "*.txt"))):
        for line in open(fp, encoding="utf-8", errors="replace").read().splitlines():
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split("|")]
            url = parts[-1] if parts else ""
            if "youtube.com/watch" not in url and "youtu.be/" not in url:
                continue
            chan = _clean(parts[1]) if len(parts) > 1 else ""
            if chan:
                out[url] = [chan]
    return out


def finer_xiaoyuzhou():
    out = {}
    fp = os.path.join(MIND, "xiaoyuzhou-fetch", "data", "xiaoyuzhou-articles.json")
    if not os.path.exists(fp):
        return out
    try:
        arr = json.load(open(fp, encoding="utf-8", errors="replace"))
    except Exception:
        return out
    for it in arr:
        link = it.get("link") or ""
        if "xiaoyuzhou" not in link:
            continue
        # 播客名在顶层 source 字段(author 为空)
        pod = _clean(it.get("source") or it.get("author") or it.get("podcastName") or "")
        if pod:
            out[link] = [pod]
    return out


def finer_zhihu():
    out = {}
    for fp in sorted(glob.glob(os.path.join(MIND, "zhihu", "recommend-cache", "*.json"))):
        try:
            d = json.load(open(fp, encoding="utf-8", errors="replace"))
        except Exception:
            continue
        data = d.get("data", {})
        items = data.get("data", []) if isinstance(data, dict) else []
        if not isinstance(items, list):
            continue
        for it in items:
            tgt = it.get("target", {}) or {}
            m = ZHIHU_API_RE.search(tgt.get("url") or "")
            if not m:
                continue
            kind, zid = m.group(1), m.group(2)
            web = {"answers": f"https://www.zhihu.com/answer/{zid}",
                   "articles": f"https://zhuanlan.zhihu.com/p/{zid}",
                   "pins": f"https://www.zhihu.com/pin/{zid}"}.get(kind)
            if web and kind in KIND_CN:
                out[web] = [KIND_CN[kind]]
    return out


SOURCES = {
    "bilibili": ("bilibili", finer_from_db_author),
    "xiaohongshu": ("xiaohongshu", finer_from_db_author),
    "youtube": ("youtube", finer_youtube),
    "xiaoyuzhou": ("xiaoyuzhou", finer_xiaoyuzhou),
    "zhihu": ("zhihu", finer_zhihu),
}


def main():
    dry = "--dry-run" in sys.argv
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    grand_updated = 0
    for st, (col, fn) in SOURCES.items():
        url2extra = fn(st) if col in ("bilibili", "xiaohongshu") else fn()
        rows = cur.execute(
            "SELECT url, tags FROM articles WHERE source_type=? AND tags LIKE '%mindback%'",
            (st,),
        ).fetchall()
        updated = skipped = 0
        batch = []
        for url, tags_json in rows:
            key = _norm_xhs(url) if st == "xiaohongshu" else url
            extra = url2extra.get(key)
            if not extra:
                skipped += 1
                continue
            cur_tags = json.loads(tags_json) if tags_json else []
            merged = list(dict.fromkeys(cur_tags + extra))
            if merged == cur_tags:
                skipped += 1
                continue
            batch.append((json.dumps(merged, ensure_ascii=False), url))
            updated += 1
        if batch and not dry:
            cur.executemany("UPDATE articles SET tags=? WHERE url=?", batch)
            conn.commit()
        grand_updated += updated
        added = set()
        for _, e in url2extra.items():
            added.update(e)
        print(f"  [{st}] 匹配 {len(rows)} 行 | 补细标签 {updated} | 无细标签跳过 {skipped}")
        if dry:
            print(f"       可补细标签样例: {sorted(added)[:15]}")
    conn.close()
    print(f"\n合计补细标签: {grand_updated} 行" + ("  (dry-run, 未写入)" if dry else "  (已写入)"))


if __name__ == "__main__":
    main()
