#!/usr/bin/env python3
"""把 mindback2 的调度缓存(历史抓取)并入 OpenBiliClaw 阅读库(articles)。

覆盖源(均为 mindback_data 下的离线缓存，与当前项目已有的实时源去重合并):
  - bilibili-recommend      -> bilibili      (78 个每日推荐快照 JSON)
  - xhs_data                -> xiaohongshu   (1269 个发现页 feed JSON, 无 xsec_token)
  - xiaohongshu-feed-2026   -> xiaohongshu   (104 个调度 feed JSON, 含 note_card+xsec_token)
  - youtube-fetcher         -> youtube       (439 个观看历史 .txt: 标题|频道|NA|NA|url)
  - xiaoyuzhou-articles     -> xiaoyuzhou    (干净列表 775 集)
  - zhihu-recommend-cache   -> zhihu         (1329 个推荐流 JSON, api url -> web url)

设计:
  - 按 url 去重(articles.url UNIQUE + INSERT OR IGNORE)；xhs 归一化(去 query)以跨快照/跨源合重。
  - 只新增, 不覆盖已有正文、不删任何数据。
  - v2ex 系列不在此处理: 当前库已有 5732 条(v2ex-hot-hub git clone), 且 mindback 的
    v2ex_data/topics.json 文件损坏(转义错误)无法解析, 故跳过并提示。

用法:
  python3 scripts/collect_mindback_caches.py [--dry-run] [--source bilibili|xhs|youtube|xiaoyuzhou|zhihu]

源数据目录可用环境变量 MINDBACK_DATA 覆盖(默认指向备份盘上的 mindback_data)。
"""
import argparse
import datetime
import glob
import json
import os
import re
import sqlite3
from pathlib import Path

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE, "data", "openbiliclaw.db")
MIND = os.environ.get(
    "MINDBACK_DATA",
    "/Volumes/未命名/未命名文件夹/2026年04月27日-备份项目/2026年05月09日-SQLiteDB/mindback_data",
)

URL_RE = re.compile(r"https?://[^\s|]+")
ZHIHU_API_RE = re.compile(r"api\.zhihu\.com/(answers|articles|pins)/(\d+)")
XHS_ID_RE = re.compile(r"/explore/([0-9a-zA-Z]+)")
YT_ID_RE = re.compile(r"[?&]v=([0-9A-Za-z_-]{11})")
PUBDATE_RE = re.compile(r"youtube_history_(\d{8})")


# 中国本地时间(UTC+8)。articles 表所有时间字段统一存北京时间字符串。
CN_TZ = datetime.timezone(datetime.timedelta(hours=8))


def _now():
    return datetime.datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _ts_to_iso(ts, fallback=None):
    try:
        iv = int(ts)
        if iv > 0:
            return datetime.datetime.fromtimestamp(iv, CN_TZ).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    return fallback or _now()


def _norm_xhs(url):
    m = XHS_ID_RE.search(url)
    return f"https://www.xiaohongshu.com/explore/{m.group(1)}" if m else url


# ---------- 各源解析器: 返回 list[dict] ----------
def parse_bilibili():
    out = []
    for fp in sorted(glob.glob(os.path.join(MIND, "bilibili-recommend", "*.json"))):
        with open(fp, encoding="utf-8", errors="replace") as f:
            d = json.load(f)
        items = d.get("data", {})
        items = items.get("item", []) if isinstance(items, dict) else (items if isinstance(items, list) else [])
        for it in items:
            uri = it.get("uri") or ""
            if "bilibili.com/video" not in uri:
                continue
            owner = it.get("owner", {}) or {}
            out.append({
                "source_type": "bilibili", "url": uri,
                "title": (it.get("title") or "").strip(),
                "author": owner.get("name", "").strip(),
                "content": (it.get("desc") or "").strip(),
                "published_at": _ts_to_iso(it.get("pubdate")),
                "tags": ["B站推荐", "mindback"],
                "summary": f"{owner.get('name','')} · 推荐流",
            })
    return out


def parse_xhs_data():
    # xhs_data: data.items[]  -> id/title/desc/userName (无 xsec_token)
    out = []
    for fp in sorted(glob.glob(os.path.join(MIND, "xhs_data", "*.json"))):
        try:
            with open(fp, encoding="utf-8", errors="replace") as f:
                d = json.load(f)
        except Exception:
            continue
        data = d.get("data", {})
        items = data.get("items", []) if isinstance(data, dict) else []
        for it in items:
            nid = it.get("id") or ""
            if not nid:
                continue
            url = f"https://www.xiaohongshu.com/explore/{nid}"
            out.append({
                "source_type": "xiaohongshu", "url": url,
                "title": (it.get("title") or "").strip() or (it.get("desc") or "").strip()[:40],
                "author": (it.get("userName") or "").strip(),
                "content": (it.get("desc") or "").strip(),
                "published_at": _now(),
                "tags": ["小红书", "mindback"],
                "summary": f"{it.get('userName','')} · 发现页",
            })
    return out


def parse_xhs_feed():
    # xiaohongshu-scheduler-feed-2026: items[].note_card (同 save_xhs_note 结构) + id/xsec_token
    out = []
    for fp in sorted(glob.glob(os.path.join(MIND, "xiaohongshu-scheduler-feed-2026", "*.json"))):
        try:
            with open(fp, encoding="utf-8", errors="replace") as f:
                d = json.load(f)
        except Exception:
            continue
        items = d.get("items", []) if isinstance(d, dict) else []
        for it in items:
            nid = it.get("id") or ""
            if not nid:
                continue
            nc = it.get("note_card", {}) or {}
            title = (nc.get("title") or "").strip() or (nc.get("desc") or "").strip()[:40]
            desc = (nc.get("desc") or "").strip()
            user = (nc.get("user") or {}).get("nickname", "") or ""
            xsec = it.get("xsec_token") or ""
            url = f"https://www.xiaohongshu.com/explore/{nid}"
            if xsec:
                url += f"?xsec_token={xsec}"
            out.append({
                "source_type": "xiaohongshu", "url": url,
                "title": title, "author": user, "content": desc,
                "published_at": _ts_to_iso(nc.get("time") or nc.get("last_update_time")),
                "tags": ["小红书", "mindback"],
                "summary": f"{user} · 调度feed",
            })
    return out


def parse_youtube():
    out = []
    for fp in sorted(glob.glob(os.path.join(MIND, "youtube-fetcher", "*.txt"))):
        mdate = PUBDATE_RE.search(os.path.basename(fp))
        fdate = mdate.group(1)[:4] + "-" + mdate.group(1)[4:6] + "-" + mdate.group(1)[6:8] if mdate else None
        with open(fp, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split("|")]
            url = parts[-1] if parts else ""
            if "youtube.com/watch" not in url and "youtu.be/" not in url:
                continue
            title = parts[0] if parts else ""
            chan = parts[1] if len(parts) > 1 else ""
            out.append({
                "source_type": "youtube", "url": url,
                "title": title, "author": chan,
                "content": "", "published_at": (fdate + " 00:00:00") if fdate else _now(),
                "tags": ["YouTube历史", "mindback"],
                "summary": f"{chan} · 观看历史",
            })
    return out


def parse_xiaoyuzhou():
    fp = os.path.join(MIND, "xiaoyuzhou-fetch", "data", "xiaoyuzhou-articles.json")
    if not os.path.exists(fp):
        return []
    with open(fp, encoding="utf-8", errors="replace") as f:
        arr = json.load(f)
    out = []
    for it in arr:
        link = it.get("link") or ""
        if "xiaoyuzhou" not in link:
            continue
        desc = re.sub(r"<[^>]+>", " ", it.get("description") or "")
        desc = re.sub(r"\s+", " ", desc).strip()
        pd = it.get("pubDate") or ""
        try:
            pdt = datetime.datetime.strptime(pd, "%a, %d %b %Y %H:%M:%S %z") if pd else None
            pub = pdt.strftime("%Y-%m-%d %H:%M:%S") if pdt else _now()
        except Exception:
            pub = _now()
        out.append({
            "source_type": "xiaoyuzhou", "url": link,
            "title": (it.get("title") or "").strip(),
            "author": (it.get("author") or "").strip(),
            "content": desc, "published_at": pub,
            "tags": ["小宇宙", "mindback"],
            "summary": f"{it.get('author','')} · 播客",
        })
    return out


def parse_zhihu():
    out = []
    for fp in sorted(glob.glob(os.path.join(MIND, "zhihu", "recommend-cache", "*.json"))):
        try:
            with open(fp, encoding="utf-8", errors="replace") as f:
                d = json.load(f)
        except Exception:
            continue
        data = d.get("data", {})
        items = data.get("data", []) if isinstance(data, dict) else []
        if not isinstance(items, list):
            continue
        for it in items:
            tgt = it.get("target", {}) or {}
            api_url = tgt.get("url") or ""
            m = ZHIHU_API_RE.search(api_url)
            if not m:
                continue
            kind, zid = m.group(1), m.group(2)
            web = {"answers": f"https://www.zhihu.com/answer/{zid}",
                   "articles": f"https://zhuanlan.zhihu.com/p/{zid}",
                   "pins": f"https://www.zhihu.com/pin/{zid}"}.get(kind)
            if not web:
                continue
            title = (tgt.get("title") or (tgt.get("question") or {}).get("title")
                     or it.get("brief") or "").strip()
            if not title:
                title = (tgt.get("excerpt") or "")[:50].strip()
            content = (tgt.get("excerpt") or tgt.get("content") or "").strip()
            pub = _ts_to_iso(tgt.get("created_time") or it.get("created_time"))
            out.append({
                "source_type": "zhihu", "url": web,
                "title": title, "author": "",
                "content": content, "published_at": pub,
                "tags": ["知乎推荐", "mindback"],
                "summary": f"知乎{kind} · 推荐流",
            })
    return out


SOURCES = {
    "bilibili": parse_bilibili,
    "xhs": None,          # 合并 xhs_data + xhs_feed
    "youtube": parse_youtube,
    "xiaoyuzhou": parse_xiaoyuzhou,
    "zhihu": parse_zhihu,
}


def collect(source):
    if source == "xhs":
        return parse_xhs_data() + parse_xhs_feed()
    return SOURCES[source]()


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="把 mindback2 的调度缓存并入阅读库(articles)")
    ap.add_argument("--source", choices=sorted(SOURCES), help="只导入指定来源(默认全部)")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写库")
    return ap


def main() -> None:
    args = build_parser().parse_args()
    dry = args.dry_run
    srcs = [args.source] if args.source else list(SOURCES.keys())
    conn = sqlite3.connect(DB_PATH)
    # v0.4.0+: articles 表迁移到 content.db，ATTACH 以便跨库查询
    _content_db = Path(__file__).resolve().parent.parent / "data" / "content.db"
    if _content_db.exists():
        conn.execute("ATTACH DATABASE ? AS content", (str(_content_db),))

    cur = conn.cursor()
    # 已存在 url 集合(按源), xhs 归一化去 query
    exist = {}
    for st in ("bilibili", "xiaohongshu", "youtube", "xiaoyuzhou", "zhihu"):
        rows = cur.execute("SELECT url FROM articles WHERE source_type=?", (st,)).fetchall()
        exist[st] = {_norm_xhs(r[0]) if st == "xiaohongshu" else r[0] for r in rows}

    total_new = total_skip = 0
    for st in srcs:
        recs = collect(st)
        # 本次已见(同一次运行内去重)
        seen = set()
        new = skip = 0
        for r in recs:
            key = _norm_xhs(r["url"]) if r["source_type"] == "xiaohongshu" else r["url"]
            if key in exist[r["source_type"]] or key in seen:
                skip += 1
                continue
            seen.add(key)
            new += 1
            if not dry:
                cur.execute(
                    """INSERT OR IGNORE INTO articles
                       (source_type, source_name, title, url, author, summary, content_text,
                        published_at, tags, status, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (r["source_type"], r["author"] or r["source_type"], r["title"], r["url"],
                     r["author"], r["summary"], r["content"], r["published_at"],
                     json.dumps(r["tags"], ensure_ascii=False), "unread", _now(), _now()),
                )
        exist[r["source_type"]].update(seen)
        total_new += new
        total_skip += skip
        print(f"  [{st}] 解析 {len(recs)} 条 | 新增 {new} | 跳过(重复) {skip}")
    if not dry:
        conn.commit()
    conn.close()
    print(f"\n合计: 新增 {total_new} | 跳过 {total_skip}" + ("  (dry-run, 未写入)" if dry else "  (已写入)"))


if __name__ == "__main__":
    main()
