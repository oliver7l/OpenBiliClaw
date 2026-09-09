#!/usr/bin/env python3
"""把 02_方向知识库 批量改造成 Obsidian 友好结构（与 简历技术专题 同套机制）。

1) 遍历全部 .md，把「反引号包裹的跨层路径」（01_/02_/03_ 开头、含斜杠的文件/目录）
   转成 Obsidian wikilink [[...]] -> 移动/重命名自动跟随，正文断链消失。
2) 给每个含 README.md 的目录注入 frontmatter（方向标签元数据），
   并在 README 末尾追加该目录的 Dataview 自动聚合块 -> 新素材零登记。

幂等：frontmatter 已存在不重注入；含「自动聚合」关键词则不重复插入。
用法：python3 kb_obsidian_links_dir.py
"""
from __future__ import annotations

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIR = os.path.join(ROOT, "02_方向知识库")

FILE_TOKEN = re.compile(r"`((?:\d\d_)[^`\n]*?\.(?:md|txt|ppt|pptx|pdf|doc|docx|xlsx|xls))`")
DIR_TOKEN = re.compile(r"`((?:\d\d_)[^`\n]*?/)`")

SKIP_TOKENS = ("简历技术专题", "本专题自动聚合")  # 已单批改造的目录不再动其自身结构


def convert(text: str) -> tuple[str, int]:
    text, n1 = FILE_TOKEN.subn(lambda m: "[[" + m.group(1) + "]]", text)
    text, n2 = DIR_TOKEN.subn(lambda m: "[[" + m.group(1) + "]]", text)
    return text, n1 + n2


def inject_frontmatter(text: str, tags: str, topic: str, ftype: str) -> str:
    if text.lstrip().startswith("---"):
        return text
    return f"---\ntags: [{tags}]\ntopic: {topic}\ntype: {ftype}\nupdated: 2026-09-09\n---\n\n" + text


def dataview_block(rel_dir: str) -> str:
    return (
        "\n---\n"
        "\n### 本方向自动聚合（Obsidian 行为）\n"
        "\n> 新增素材.md 直接放进本目录即自动收录，无需手动登记。\n"
        "\n```dataview\n"
        "LIST file.name\n"
        f'FROM "{rel_dir}"\n'
        'WHERE file.name != "README"\n'
        "SORT file.name ASC\n"
        "```\n"
    )


def main() -> None:
    changed_links, changed_fm, changed_dv = 0, 0, 0
    # 收集所有 md 文件
    md_files = []
    for root, _, files in os.walk(DIR):
        if root.startswith("."):
            continue
        for fn in files:
            if fn.endswith(".md"):
                md_files.append(os.path.join(root, fn))

    for fp in md_files:
        rel = os.path.relpath(fp, ROOT)
        if any(sk in fp for sk in SKIP_TOKENS):
            continue
        with open(fp, encoding="utf-8") as f:
            text = f.read()
        text, n = convert(text)
        if n:
            changed_links += n
            with open(fp, "w", encoding="utf-8") as f:
                f.write(text)
        # 仅对「README.md」注入 frontmatter + Dataview
        if os.path.basename(fp) == "README.md" and "自动聚合" not in text:
            d = os.path.basename(os.path.dirname(fp))
            new = inject_frontmatter(text, f"方向知识库, {d}", d, "方向")
            rel_dir = os.path.relpath(os.path.dirname(fp), ROOT).replace(os.sep, "/")
            new += dataview_block(rel_dir)
            with open(fp, "w", encoding="utf-8") as f:
                f.write(new)
            changed_fm += 1
            changed_dv += 1

    print(f"路径转wikilink: {changed_links} 处")
    print(f"注入frontmatter+dataview的README: {changed_fm} 个")
    print(f"文件总数扫描: {len(md_files)}")


if __name__ == "__main__":
    main()