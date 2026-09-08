#!/usr/bin/env python3
"""抓取一篇知乎文章/回答并归档进阅读库(articles)。

用法:
  python3 scripts/save_zhihu_article.py 2058874412229833701
  python3 scripts/save_zhihu_article.py https://zhuanlan.zhihu.com/p/2058874412229833701
  python3 scripts/save_zhihu_article.py https://www.zhihu.com/question/xxxx/answer/yyyy

约定:
  - 走本机 zhihu CLI(已登录); 专栏 p/ 用 `zhihu article`, 回答用 `zhihu answer`
  - 按 url 去重, 已存在则跳过; 只新增, 不覆盖已有正文、不删任何数据
  - 所有入库时间字段(published_at/created_at/updated_at)一律存北京时间(UTC+8)字符串
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

# 中国本地时间(UTC+8)。本脚本所有入库时间字段一律按"北京时间"字符串存储。
CN_TZ = datetime.timezone(datetime.timedelta(hours=8))
TIME_FMT = "%Y-%m-%d %H:%M:%S"


def _now_local() -> str:
    return datetime.datetime.now(CN_TZ).strftime(TIME_FMT)


def _to_local_str(dt: datetime.datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)
    return dt.astimezone(CN_TZ).strftime(TIME_FMT)


def _article_id_from(url_or_id):
    """从链接或 id 中提取知乎对象类型与 id。

    支持: zhuanlan.zhihu.com/p/<id>, www.zhihu.com/question/<q>/answer/<a>
    """
    s = url_or_id.strip()
    m = re.search(r"/p/(\d+)", s)
    if m:
        return "article", m.group(1)
    m = re.search(r"/answer/(\d+)", s)
    if m:
        return "answer", m.group(1)
    m = re.search(r"/question/(\d+)", s)
    if m:
        return "question", m.group(1)
    # 纯数字当作文章 id
    if re.fullmatch(r"\d+", s):
        return "article", s
    return None, None


def _ts_to_str(ts):
    try:
        dt = datetime.datetime.fromtimestamp(int(ts), datetime.UTC)
        return _to_local_str(dt)
    except Exception:  # noqa: BLE001
        return _now_local()


def _fetch(kind, aid):
    cmd = ["zhihu", kind, aid, "--json"]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    try:
        return json.loads(out.stdout)
    except Exception:  # noqa: BLE001
        return None


def main():
    if len(sys.argv) < 2:
        sys.exit("用法: save_zhihu_article.py <知乎链接或id>")
    kind, aid = _article_id_from(sys.argv[1])
    if not kind:
        sys.exit(f"无法识别知乎链接: {sys.argv[1]}")
    print(f"[知乎] 抓取 {kind} {aid} ...")
    data = _fetch(kind, aid)
    if not data or not data.get("ok"):
        sys.exit(f"[错误] 抓取失败或登录过期: {data}")
    art = data["data"].get("article") or data["data"].get("answer") or data["data"]
    if not art:
        sys.exit("[错误] 返回数据缺少 article/answer 字段")

    url = (art.get("url") or "").strip()
    # 回答对象无 title 字段, 用问题标题 question_title 兜底
    title = (art.get("title") or art.get("question_title") or "").strip()
    if not url:
        sys.exit("[错误] 无 url, 无法归档")
    content = (art.get("content") or "").strip()
    author = (art.get("author") or {}).get("name", "").strip() if isinstance(art.get("author"), dict) else ""
    voteup = art.get("voteup_count") or art.get("voteup") or 0
    comment = art.get("comment_count") or 0
    created = art.get("created_time") or art.get("created") or 0
    published_at = _ts_to_str(created)

    summary = f"{author} · 赞同 {voteup} · 评论 {comment}"
    tags = json.dumps(["知乎", "专栏" if kind == "article" else "回答"], ensure_ascii=False)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    row = cur.execute("SELECT id, title FROM articles WHERE url=?", (url,)).fetchone()
    if row:
        # 已存在: 若库内标题为空而本次抓到了, 仅补全标题(不覆盖正文)
        if (row[1] in (None, "")) and title:
            now_iso = _now_local()
            cur.execute("UPDATE articles SET title=?, updated_at=? WHERE id=?",
                        (title, now_iso, row[0]))
            conn.commit()
            print(f"[补全标题] {title} (id={row[0]})")
        else:
            print(f"[跳过] 已存在: {row[1]}")
        conn.close()
        return
    now_iso = _now_local()
    cur.execute(
        """INSERT INTO articles
           (source_type, source_name, title, url, author, summary, content_text,
            published_at, tags, status, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (PLATFORM, author or PLATFORM, title, url, author, summary,
         content, published_at, tags, "unread", now_iso, now_iso),
    )
    conn.commit()
    conn.close()
    print(f"[完成] 已归档 ({len(content)} 字): {title}")
    print(f"        url: {url}")


if __name__ == "__main__":
    main()
