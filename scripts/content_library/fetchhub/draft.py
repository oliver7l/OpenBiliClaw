"""归档草稿生成：把 UnifiedDoc 写成五段式骨架 md（解读/批注留 TODO 由人写）。"""
from __future__ import annotations

import re
from pathlib import Path

from .core import PROJECT_ROOT, Reply, UnifiedDoc


def _slug(title: str, limit: int = 40) -> str:
    s = re.sub(r"[\\/:*?\"<>|\s#]+", "-", (title or "").strip()).strip("-")
    return s[:limit] or "untitled"


def write_draft(doc: UnifiedDoc, project_root: Path | None = None) -> Path:
    """写入 notes/阅读收藏库/.drafts/（不占用正式编号、不动索引，归档时人工搬运）。"""
    root = Path(project_root) if project_root else PROJECT_ROOT
    drafts = root / "notes" / "阅读收藏库" / ".drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    date_part = (doc.published_at or doc.fetched_at or "")[:10].replace("/", "-")
    base = f"{date_part}-{doc.platform}-{_slug(doc.title)}"

    replies_md = "\n".join(
        f"> **{r.author or '匿名'}**（{r.time or '时间未知'}）：{r.content.strip()}"
        for r in doc.replies
        if isinstance(r, Reply) and r.content.strip()
    ) or "（无回复或未抓取）"

    # 注意：f-string 表达式内不能含反斜杠（Python < 3.12 SyntaxError），先在表达式外拼好
    extra_lines = [f"- {k}: {v}" for k, v in doc.extra.items() if v not in ("", None)]
    extra_md = ("## 平台附加信息\n\n" + "\n".join(extra_lines) + "\n\n") if extra_lines else ""

    body = f"""# {doc.title}

> 来源：{doc.platform}
> 作者：{doc.author or '未知'}
> 发布时间：{doc.published_at or '未知'}
> 原帖：{doc.url}
> 抓取：{doc.fetched_via} @ {doc.fetched_at}（confidence={doc.confidence}）
> 类型：链接原文
> 一句话：TODO（人写，一句话概括核心观点）

{extra_md}---

## 我的解读

TODO（必须人写：这篇讲了什么、为什么值得存、和库内哪些条目互证）

---

## 对话摘录

{replies_md}

---

## 原文

{doc.content_md}

---

## 批注

TODO（必须人写：可操作点 / 与求职线或项目的关系 / 存疑处）
"""
    path = drafts / f"{base}.md"
    i = 2
    while path.exists():
        path = drafts / f"{base}-{i}.md"
        i += 1
    path.write_text(body, encoding="utf-8")
    return path
