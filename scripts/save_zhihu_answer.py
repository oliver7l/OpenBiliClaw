#!/usr/bin/env python3
"""归档单篇知乎回答到阅读库(articles)。

用法:
  python3 scripts/save_zhihu_answer.py "<知乎回答链接>"
  python3 scripts/save_zhihu_answer.py "<answer_id>"

约定:
  - 走本机 zhihu CLI(已登录); `zhihu answer <id> --json`
  - 知乎链接稳定(不含变化的 token), 直接用完整 URL 精确去重
  - 已存在且含正文(>=50字) -> 跳过; 空壳 -> 补强
  - published_at 用回答 created_time(UTC), 保证排序正确
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
PLATFORM = "zhihu"


def _extract_answer_id(arg):
    """从链接或裸 id 提取 answer id。"""
    m = re.search(r"/answer/(\d+)", arg)
    if m:
        return m.group(1)
    if re.fullmatch(r"\d+", arg.strip()):
        return arg.strip()
    return None


def _ts_to_str(ts):
    try:
        iv = int(ts)
        if iv <= 0:
            raise ValueError
        return datetime.datetime.fromtimestamp(iv, datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:  # noqa: BLE001
        return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")


def _fetch(answer_id):
    cmd = ["zhihu", "answer", answer_id, "--json"]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    try:
        return json.loads(out.stdout)
    except Exception:  # noqa: BLE001
        return None


def main():
    if len(sys.argv) < 2:
        sys.exit("用法: save_zhihu_answer.py <知乎回答链接或 answer_id>")
    arg = sys.argv[1].strip()
    answer_id = _extract_answer_id(arg)
    if not answer_id:
        sys.exit(f"无法识别知乎回答: {arg}")

    print(f"[知乎] 抓取 answer {answer_id} ...")
    data = _fetch(answer_id)
    if not data or not data.get("ok"):
        sys.exit(f"[错误] 抓取失败或登录过期: {data}")
    a = data["data"]["answer"]
    url = a.get("url") or f"https://www.zhihu.com/question/{a.get('question_id')}/answer/{answer_id}"
    title = (a.get("question_title") or "").strip() or f"知乎回答 {answer_id}"
    content = (a.get("content") or "").strip()
    author = (a.get("author") or {}).get("name", "").strip()
    voteup = a.get("voteup_count", "?")
    created = a.get("created_time") or 0
    published_at = _ts_to_str(created)
    tags = ["知乎"]
    summary = f"{author} · 赞 {voteup}"

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    row = cur.execute(
        "SELECT id, title, content_text, tags FROM articles WHERE url = ?", (url,)
    ).fetchone()

    if row:
        rid, old_title, old_content, old_tags_json = row
        old_len = len(old_content or "")
        old_tags = json.loads(old_tags_json or "[]") if old_tags_json else []
        if old_len >= 50:
            print(f"[跳过] 已存在且含正文({old_len}字): {old_title!r} (id={rid})")
            conn.close()
            return
        merged = []
        for t in old_tags + tags:
            if t not in merged:
                merged.append(t)
        new_title = old_title or title
        now_iso = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            """UPDATE articles
               SET title=?, content_text=?, tags=?, summary=?, author=?, published_at=?, updated_at=?
               WHERE id=?""",
            (new_title, content, json.dumps(merged, ensure_ascii=False), summary, author, published_at, now_iso, rid),
        )
        conn.commit()
        conn.close()
        print(f"[补全] 已写入正文({len(content)}字)到旧行 id={rid}: {new_title}")
        print(f"       作者: {author} | 赞: {voteup} | 发布: {published_at}")
        return

    now_iso = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        """INSERT INTO articles
           (source_type, source_name, title, url, author, summary, content_text,
            published_at, tags, status, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (PLATFORM, author or PLATFORM, title, url, author, summary,
         content, published_at, json.dumps(tags, ensure_ascii=False), "unread", now_iso, now_iso),
    )
    conn.commit()
    conn.close()
    print(f"[完成] 已归档 ({len(content)} 字): {title}")
    print(f"       作者: {author} | 赞: {voteup} | 发布: {published_at}")


if __name__ == "__main__":
    main()
