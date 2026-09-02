#!/usr/bin/env python3
"""归档知乎文章/回答到 notes/已读库 文件存档(四件套)。

用法:
  python3 scripts/archive_zhihu_readlib.py "<知乎链接或id>" --folder "主题文件夹名"
  python3 scripts/archive_zhihu_readlib.py https://www.zhihu.com/question/xxx/answer/yyy --folder "把话说开"
  python3 scripts/archive_zhihu_readlib.py https://zhuanlan.zhihu.com/p/693029773 --folder "香港电影记忆"

约定:
  - 走本机 zhihu-cli(已登录, 凭证在 ~/.zhihu-cli/config.json):
      answer 用 `zhihu-cli answer show <id>`, 专栏用 `zhihu-cli article show <id>`
      (输出为 camelCase: questionTitle/voteupCount/createdTime, 与 save_zhihu_answer.py
       用的 `zhihu answer --json` 是不同 CLI, 不要混用)
  - 在 notes/已读库/<folder>/ 生成四件套:
      meta.json   元数据
      raw.html    原始 HTML 存档
      content.md  Markdown 正文(图片引用已去重)
      reading.html 离线阅读版(排版优化)
  - 知乎回答的图片是 <noscript>双 img 结构 + svg 占位符, 转换时已处理
  - 结构化笔记(notes/YYYY-MM-主题.md)与已读库 README 索引需人工补充,
    脚本结尾会打印建议的表格行和笔记文件名
幂等: 目标文件夹已存在且含 meta.json 时拒绝覆盖。
"""
import argparse
import datetime
import html as html_mod
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
READLIB = BASE / "notes" / "已读库"

CLI_CANDIDATES = [
    shutil.which("zhihu-cli"),
    "/opt/homebrew/bin/zhihu-cli",
    str(Path.home() / "Library/Application Support/TRAE SOLO CN/ModularData/ai-agent/vm/tools/npm-global/bin/zhihu-cli"),
]

READING_CSS = """
  :root { --bg: #faf9f7; --card: #fff; --text: #2c2c2c; --muted: #8a8a8a; --accent: #c0392b; --border: #ebe8e4; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: var(--bg); color: var(--text); font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", -apple-system, sans-serif; line-height: 1.9; padding: 48px 20px 80px; }
  .page { max-width: 720px; margin: 0 auto; background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 56px 60px; }
  .kicker { color: var(--accent); font-size: 13px; letter-spacing: 2px; margin-bottom: 14px; }
  h1 { font-size: 26px; font-weight: 600; line-height: 1.5; margin-bottom: 20px; }
  .meta { color: var(--muted); font-size: 14px; padding-bottom: 28px; border-bottom: 1px solid var(--border); margin-bottom: 36px; }
  .meta b { color: var(--text); font-weight: 500; }
  .content p { margin-bottom: 18px; font-size: 17px; }
  .content img { max-width: 100%; border-radius: 8px; margin: 10px 0; }
  .content figure { margin: 24px 0; }
  .content b, .content strong { font-weight: 600; }
  .content hr { border: none; border-top: 1px solid var(--border); margin: 36px 0; }
  .source { margin-top: 44px; padding-top: 24px; border-top: 1px solid var(--border); color: var(--muted); font-size: 13px; }
  .source a { color: #6b8e23; text-decoration: none; }
  @media print { body { padding: 0; background: #fff; } .page { border: none; padding: 0; } }
"""


def locate_cli() -> str:
    for c in CLI_CANDIDATES:
        if c and Path(c).exists():
            return c
    sys.exit("[错误] 找不到 zhihu-cli, 请确认已安装")


def parse_arg(arg: str) -> tuple[str, str]:
    s = arg.strip()
    m = re.search(r"/answer/(\d+)", s)
    if m:
        return "answer", m.group(1)
    m = re.search(r"/p/(\d+)", s)
    if m:
        return "article", m.group(1)
    if re.fullmatch(r"\d+", s):
        return "answer", s
    sys.exit(f"[错误] 无法识别知乎链接: {arg}")


def fetch(cli: str, kind: str, aid: str) -> dict:
    print(f"[知乎] 抓取 {kind} {aid} ...")
    out = subprocess.run(
        [cli, kind, "show", aid], capture_output=True, text=True, timeout=120
    )
    try:
        data = json.loads(out.stdout)
    except Exception:  # noqa: BLE001
        sys.exit(f"[错误] CLI 输出非 JSON: {out.stdout[:200]} {out.stderr[:200]}")
    if not data.get("ok"):
        sys.exit(f"[错误] 抓取失败(登录过期? 用 `zhihu-cli auth import` 导入含 z_c0 的 Cookie): {json.dumps(data.get('error', {}), ensure_ascii=False)[:300]}")
    return data["data"]


def html_to_md(h: str) -> str:
    # noscript 内是真实 img, 展开后删除外层 svg 占位 img, 再统一去重
    h = re.sub(r"<noscript>(.*?)</noscript>", r"\1", h, flags=re.S)
    h = re.sub(r'<img[^>]*src="data:image/svg[^"]*"[^>]*/?>', "", h)
    h = re.sub(r"<br\s*/?>", "\n", h)
    h = re.sub(r"<hr\s*/?>", "\n---\n", h)
    h = re.sub(r"<b>(.*?)</b>", r"**\1**", h, flags=re.S)
    h = re.sub(r"<strong>(.*?)</strong>", r"**\1**", h, flags=re.S)
    h = re.sub(r'<img[^>]*data-original="([^"]+)"[^>]*/?>', r"![](\1)", h)
    h = re.sub(r'<img[^>]*src="([^"]+)"[^>]*/?>', r"![](\1)", h)
    h = re.sub(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r"[\2](\1)", h, flags=re.S)
    h = re.sub(r"</p>\s*<p[^>]*>", "\n\n", h)
    h = re.sub(r"<[^>]+>", "", h)
    h = re.sub(r"(!\[\]\(([^)]+)\))(\1)+", r"\1", h)
    return html_mod.unescape(h).strip()


def reading_html(title: str, author: str, date_str: str, stats: str,
                 content_html: str, url: str, archived_at: str, kicker: str) -> str:
    esc = html_mod.escape
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} — 阅读版</title>
<style>{READING_CSS}</style>
</head>
<body>
<div class="page">
  <div class="kicker">{kicker}</div>
  <h1>{esc(title)}</h1>
  <div class="meta"><b>{esc(author)}</b> · {date_str} · {stats}</div>
  <div class="content">
{content_html}
  </div>
  <div class="source">
    原文：<a href="{url}">{url}</a><br>
    存档时间：{archived_at} · 本文件为离线阅读版，配套存档见同目录 raw.html / content.md / meta.json
  </div>
</div>
</body>
</html>
"""


def main() -> None:
    ap = argparse.ArgumentParser(description="归档知乎文章/回答到 notes/已读库")
    ap.add_argument("url", help="知乎回答/专栏链接或裸 id")
    ap.add_argument("--folder", required=True, help="已读库下的文件夹名, 如 '把话说开'")
    ap.add_argument("--theme", default="", help="主题标签, 写入 meta.json, 如 '人际沟通 / 齐泽克哲学'")
    args = ap.parse_args()

    cli = locate_cli()
    kind, aid = parse_arg(args.url)
    data = fetch(cli, kind, aid)

    title = (data.get("questionTitle") or data.get("title") or "").strip()
    if not title:
        sys.exit("[错误] 返回数据缺少标题")
    author_obj = data.get("author") or {}
    author = (author_obj.get("name") or "").strip()
    content_html = (data.get("content") or "").strip()
    voteup = data.get("voteupCount", 0)
    comments = data.get("commentCount", 0)
    url = data.get("url") or (
        f"https://www.zhihu.com/question/{data['questionId']}/answer/{aid}"
        if kind == "answer" else f"https://zhuanlan.zhihu.com/p/{aid}"
    )

    created_ts = data.get("createdTime")
    date_str = (
        datetime.datetime.fromtimestamp(int(created_ts)).strftime("%Y-%m-%d %H:%M")
        if created_ts else ""
    )
    archived_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    stats = f"{voteup} 赞同 · {comments} 评论" if comments else f"{voteup} 赞同"

    folder = READLIB / args.folder
    if (folder / "meta.json").exists():
        sys.exit(f"[跳过] {folder} 已存在(幂等保护), 如需重抓请先删除该文件夹")
    folder.mkdir(parents=True, exist_ok=True)

    meta = {
        "source": "zhihu", "type": kind, "id": aid,
        "title": title, "author": author,
        "author_token": author_obj.get("urlToken", ""), "url": url,
        "created_at": date_str, "archived_at": archived_at,
        "voteup_count": voteup,
        "excerpt": data.get("excerpt", ""), "theme": args.theme,
    }
    if kind == "answer":
        meta = {
            "source": "zhihu", "type": "answer",
            "question_id": data.get("questionId"), "answer_id": aid,
            "question_title": title, "author": author,
            "author_token": author_obj.get("urlToken", ""), "url": url,
            "voteup_count": voteup, "comment_count": comments,
            "created_at": date_str, "archived_at": archived_at,
            "excerpt": data.get("excerpt", ""), "theme": args.theme,
        }

    (folder / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    esc = html_mod.escape
    (folder / "raw.html").write_text(f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{esc(title)} - {esc(author)}{'的回答' if kind == 'answer' else ''} - 知乎</title>
<meta name="source" content="{url}">
<meta name="author" content="{esc(author)}">
</head>
<body>
<article>
<h1>{esc(title)}</h1>
<div class="meta">作者：{esc(author)} | {date_str} | {stats}</div>
{content_html}
</article>
</body>
</html>
""", encoding="utf-8")

    md_body = html_to_md(content_html)
    md_meta_line = " · ".join(x for x in [f"**{author}**", date_str, stats] if x)
    (folder / "content.md").write_text(f"""---
title: "{title}"
author: "{author}"
date: "{date_str}"
source: "{url}"
votes: {voteup}
---

# {title}

{md_meta_line}

{md_body}
""", encoding="utf-8")

    kicker = "知乎 · 回答存档" if kind == "answer" else "知乎 · 专栏存档"
    (folder / "reading.html").write_text(
        reading_html(title, author, date_str, stats, content_html, url, archived_at, kicker),
        encoding="utf-8",
    )

    n_imgs = len(re.findall(r"!\[\]", md_body))
    print(f"\n[完成] {folder}/ 四件套已生成")
    print(f"  标题: {title}")
    print(f"  作者: {author} | 赞 {voteup} | 评论 {comments} | 发布 {date_str or '(未知)'}")
    print(f"  正文 {len(md_body)} 字符 | 图片 {n_imgs} 张")
    month = date_str[:7] if date_str else datetime.datetime.now().strftime("%Y-%m")
    print(f"\n[下一步·人工] 结构化笔记: notes/{month}-<主题>.md")
    print(f"[下一步·人工] README 索引表追加行:")
    print(f"| N | [{title}](../{month}-<主题>.md) | {author} | {date_str[:7] if date_str else '?'} | 赞{voteup} | {args.theme or '<主题>'} |")


if __name__ == "__main__":
    main()
