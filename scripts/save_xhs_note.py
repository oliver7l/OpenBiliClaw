#!/usr/bin/env python3
"""归档单篇小红书笔记到阅读库(articles)。

用法:
  python3 scripts/save_xhs_note.py "https://www.xiaohongshu.com/explore/<id>?xsec_token=...&xsec_source=pc_feed"

约定:
  - 走本机 xhs CLI(已登录); 链接需带 xsec_token, 自动从 URL 提取传给 --xsec-token
  - 按 note_id 去重(链接里 xsec_token 每次不同, 不能用完整 URL 精确匹配)
  - 已存在则:
      * 若已有正文为空/过短(<50字) -> 补强(写入新抓的正文/标题/话题标签), 不丢已有数据
      * 若已有正文完整 -> 跳过
  - 入库 URL 保留用户传入的带 xsec_token 链接(小红书详情页必须带 token 才能渲染, 裸链反而打不开)
  - 只增/只改, 不删任何数据
"""
import datetime
import json
import os
import re
import sqlite3
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE, "data", "openbiliclaw.db")
PLATFORM = "xiaohongshu"


def _extract(url):
    m = re.search(r"/explore/([0-9a-zA-Z]+)", url) or re.search(r"/discovery/item/([0-9a-zA-Z]+)", url)
    note_id = m.group(1) if m else None
    mt = re.search(r"xsec_token=([^&]+)", url)
    xsec = mt.group(1) if mt else None
    return note_id, xsec


def _bare_url(note_id):
    return f"https://www.xiaohongshu.com/explore/{note_id}"


def _ts_to_str(ts):
    try:
        iv = int(ts)
        if iv <= 0:
            raise ValueError
        return datetime.datetime.fromtimestamp(iv, datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:  # noqa: BLE001
        return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")


def _fetch(url, xsec):
    cmd = ["xhs", "read", url]
    if xsec:
        cmd += ["--xsec-token", xsec]
    cmd += ["--json"]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    try:
        return json.loads(out.stdout)
    except Exception:  # noqa: BLE001
        return None


def main():
    if len(sys.argv) < 2:
        sys.exit("用法: save_xhs_note.py <小红书链接>")
    url = sys.argv[1].strip()
    note_id, xsec = _extract(url)
    if not note_id:
        sys.exit(f"无法识别小红书链接: {url}")
    bare = _bare_url(note_id)
    print(f"[小红书] 抓取 {note_id} ...")
    data = _fetch(url, xsec)
    if not data or not data.get("ok"):
        sys.exit(f"[错误] 抓取失败或登录过期: {data}")
    it = data["data"]["items"][0]["note_card"]
    title = (it.get("title") or "").strip() or (it.get("desc") or "").strip()[:40]
    desc = (it.get("desc") or "").strip()
    author = (it.get("user") or {}).get("nickname", "").strip()
    topics = [t.get("name") for t in it.get("tag_list", []) if t.get("type") == "topic" and t.get("name")]
    tags = ["小红书"] + topics
    ts = it.get("time") or it.get("last_update_time") or 0
    published_at = _ts_to_str(ts)
    liked = (it.get("interact_info") or {}).get("liked_count", "?")
    summary = f"{author} · 话题 {len(topics)} · 赞 {liked}"

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # 按 note_id 去重(匹配裸链接或任意带 token 的同 id 链接)
    row = cur.execute(
        "SELECT id, title, content_text, tags FROM articles WHERE url LIKE ? OR url LIKE ?",
        (f"%{note_id}%", f"%/{note_id}%"),
    ).fetchone()

    if row:
        rid, old_title, old_content, old_tags_json = row
        old_len = len(old_content or "")
        old_tags = json.loads(old_tags_json or "[]") if old_tags_json else []
        # 已有完整正文 -> 跳过, 但用本次传入的链接刷新 token(保留最新可用的 xsec_token)
        if old_len >= 50:
            cur.execute("UPDATE articles SET url=? WHERE id=?", (url, rid))
            conn.commit()
            print(f"[跳过] 已存在且含正文({old_len}字): {old_title!r} (id={rid}) -> token 已刷新")
            conn.close()
            return
        # 已有但为空壳 -> 补强(合并标签, 标题为空则补, 正文补入)
        merged = []
        for t in old_tags + tags:
            if t not in merged:
                merged.append(t)
        new_title = old_title or title
        now_iso = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            """UPDATE articles
               SET title=?, content_text=?, tags=?, summary=?, url=?, updated_at=?
               WHERE id=?""",
            (new_title, desc, json.dumps(merged, ensure_ascii=False), summary, url, now_iso, rid),
        )
        conn.commit()
        conn.close()
        print(f"[补全] 已写入正文({len(desc)}字)到旧行 id={rid}: {new_title}")
        print(f"       作者: {author} | 话题: {', '.join(topics)} | 发布: {published_at}")
        return

    now_iso = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        """INSERT INTO articles
           (source_type, source_name, title, url, author, summary, content_text,
            published_at, tags, status, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (PLATFORM, author or PLATFORM, title, url, author, summary,
         desc, published_at, json.dumps(tags, ensure_ascii=False), "unread", now_iso, now_iso),
    )
    conn.commit()
    conn.close()
    print(f"[完成] 已归档 ({len(desc)} 字): {title}")
    print(f"       作者: {author} | 话题: {', '.join(topics)} | 发布: {published_at}")


if __name__ == "__main__":
    main()
