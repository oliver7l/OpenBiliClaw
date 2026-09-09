#!/usr/bin/env python3
"""归档单篇知乎回答到阅读库(articles)。

用法:
  python3 scripts/save_zhihu_answer.py "<知乎回答链接>"
  python3 scripts/save_zhihu_answer.py "<answer_id>"

约定:
  - 走本机 zhihu CLI(已登录) 的 session, 直接调知乎 API
    (/api/v4/answers/{id}), 避开 HTML 抓取的 zh-zse-ck 风控
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
from html import unescape

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE, "data", "openbiliclaw.db")
PLATFORM = "zhihu"

# 内联抓取脚本: 用 zhihu-toolkit 自带 python 导入其 session 单例,
# 直接调知乎回答 API. 输出 JSON 到 stdout.
_FETCH_INLINE = r"""
import json,sys
from zhihu_cli.content.handlers.requests import _get_session
aid=sys.argv[1]
s=_get_session()
r=s.get(f"https://www.zhihu.com/api/v4/answers/{aid}",
    params={"include":"data[*].is_normal,content,voteup_count,comment_count,created_time,updated_time,author,question"},
    timeout=20)
r.raise_for_status()
print(json.dumps(r.json(),ensure_ascii=False))
"""


def _find_zhihu_python():
    """定位 zhihu CLI 所属 uv tool 的 python 解释器。"""
    import shutil

    zhihu = shutil.which("zhihu")
    if not zhihu:
        return None
    real = os.path.realpath(zhihu)
    bin_dir = os.path.dirname(real)
    py = os.path.join(bin_dir, "python")
    return py if os.path.exists(py) else None


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
        return _now_local()


def _now_local():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _html_to_text(html):
    """知乎 API 返回的 content 是 HTML, 转纯文本。"""
    if not html:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"</p>", "\n\n", text)
    text = re.sub(r"</div>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _fetch(answer_id):
    """通过 zhihu CLI session 调 API 抓取回答, 返回解析后的 dict。"""
    py = _find_zhihu_python()
    if not py:
        print("[警告] 未找到 zhihu CLI 对应的 python, 请先安装 zhihu-toolkit", file=sys.stderr)
        return None
    cmd = [py, "-c", _FETCH_INLINE, answer_id]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    try:
        return json.loads(out.stdout)
    except Exception:  # noqa: BLE001
        if out.stderr:
            print(f"[错误] 抓取 stderr: {out.stderr[:500]}", file=sys.stderr)
        return None


def main():
    force = "--force" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--force"]
    if len(args) < 1:
        sys.exit("用法: save_zhihu_answer.py [--force] <知乎回答链接或 answer_id>")
    arg = args[0].strip()
    answer_id = _extract_answer_id(arg)
    if not answer_id:
        sys.exit(f"无法识别知乎回答: {arg}")

    print(f"[知乎] 抓取 answer {answer_id} ...")
    data = _fetch(answer_id)
    if not data or "content" not in data:
        sys.exit(f"[错误] 抓取失败或登录过期: {data}")

    q = data.get("question", {})
    a = data.get("author", {})
    question_id = q.get("id", "")
    url = f"https://www.zhihu.com/question/{question_id}/answer/{answer_id}"
    title = (q.get("title") or "").strip() or f"知乎回答 {answer_id}"
    content = _html_to_text(data.get("content") or "")
    author = (a.get("name") or "").strip()
    voteup = data.get("voteup_count", "?")
    created = data.get("created_time") or 0
    published_at = _ts_to_str(created)
    tags = ["知乎"]
    summary = f"{author} · 赞 {voteup}"

    conn = sqlite3.connect(DB_PATH)
    # v0.4.0+: articles 表迁移到 content.db，ATTACH 以便跨库查询
    try:
        from pathlib import Path as _Path
        _content_db = _Path(__file__).parent.parent / "data" / "content.db"
        if _content_db.exists():
            conn.execute("ATTACH DATABASE ? AS content", (str(_content_db),))
    except Exception:
        pass


    cur = conn.cursor()
    row = cur.execute(
        "SELECT id, title, content_text, tags FROM articles WHERE url = ?", (url,)
    ).fetchone()

    if row:
        rid, old_title, old_content, old_tags_json = row
        old_len = len(old_content or "")
        old_tags = json.loads(old_tags_json or "[]") if old_tags_json else []
        if old_len >= 50 and not force:
            print(f"[跳过] 已存在且含正文({old_len}字): {old_title!r} (id={rid}) (用 --force 强制补全)")
            conn.close()
            return
        merged = []
        for t in old_tags + tags:
            if t not in merged:
                merged.append(t)
        new_title = old_title or title
        now_iso = _now_local()
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

    now_iso = _now_local()
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
