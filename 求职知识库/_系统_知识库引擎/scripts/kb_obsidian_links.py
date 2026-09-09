#!/usr/bin/env python3
"""把「简历技术专题」改造成 Obsidian 友好结构。

三件事：
1) 为专题 README 注入 frontmatter 元数据（tags/topic/type/updated）。
2) 将「资料索引」段里反引号包裹的硬编码相对路径 改成 Obsidian wikilink
   （[[...]]），这样在 Obsidian 里移动/重命名文件时链接自动跟随，永不断裂。
3) 在每个专题「资料索引」段头部插入 Dataview 动态聚合块，
   新增素材直接放进专题目录即自动收录，无需任何手动登记。

用法：python3 kb_obsidian_links.py   （仅作用于 简历技术专题 目录）
"""
from __future__ import annotations

import re
from pathlib import Path

BASE = Path(__file__).resolve().parents[1].parent  # _系统_知识库引擎 -> 求职知识库
TOPIC_DIR = BASE / "02_方向知识库" / "简历技术专题"

TOPICS = {
    "01_多目标推荐系统": "多目标推荐系统",
    "02_对比学习": "对比学习",
    "03_端云协同": "端云协同",
    "04_多场景统一建模": "多场景统一建模",
}

# 反引号包裹、以 0X_ 开头、含路径分隔符的文件/文件夹引用
FILE_TOKEN = re.compile(r"`((?:\d\d_)[^`\n]*?\.(?:md|txt|ppt|pptx|pdf|doc|docx|xlsx|xls))`")
DIR_TOKEN = re.compile(r"`((?:\d\d_)[^`\n]*?/)`")


def inject_frontmatter(text: str, tags: list[str], topic: str, ftype: str,
                       fmt: str = "markdown") -> str:
    if text.startswith("---"):
        # 已有 frontmatter：仅确保 tags 含目标标签（务实做法，不重写正文）
        end = text.find("---", 3)
        block = text[: end + 3]
        rest = text[end + 3:]
        if f"{topic}" not in block and "# {topic}".format(topic=topic) not in block:
            block = block.rstrip() + "\ntags:\n  - 简历技术专题\n  - " + topic + "\n"
        return block + rest
    fm = (
        "---\n"
        f"tags: [{', '.join(tags)}]\n"
        f"topic: {topic}\n"
        f"type: {ftype}\n"
        f"fields: [{fmt}]\n"
        "updated: 2026-09-09\n"
        "---\n\n"
    )
    return fm + text


def convert_paths(text: str) -> str:
    text = FILE_TOKEN.sub(lambda m: "[[" + m.group(1) + "]]", text)
    text = DIR_TOKEN.sub(lambda m: "[[" + m.group(1) + "]]", text)
    return text


def dataview_block(topic_dir: str) -> str:
    return (
        "> **本专题自动聚合（Obsidian）**：新素材 .md 直接放进 `"
        + topic_dir
        + "` 目录，下面列表自动收录，无需任何手动登记。\n"
        "\n"
        "```dataview\n"
        'LIST file.name\n'
        f'FROM "{topic_dir}"\n'
        'WHERE file.name != "README"\n'
        'SORT file.name ASC\n'
        "```\n"
    )


def main() -> None:
    changed: list[str] = []
    # 1) 单条索引文件注入 frontmatter
    nav = TOPIC_DIR / "README.md"
    if nav.exists():
        nav.write_text(inject_frontmatter(nav.read_text(encoding="utf-8"),
                                          ["简历技术专题"], "简历技术专题",
                                          "导航"), encoding="utf-8")
        changed.append("README.md (frontmatter)")

    # 2) 每个专题改 frontmatter + wikilink + dataview
    rel_dir = "02_方向知识库/简历技术专题"
    for sub, topic in TOPICS.items():
        f = TOPIC_DIR / sub / "README.md"
        if not f.exists():
            continue
        text = f.read_text(encoding="utf-8")
        text = inject_frontmatter(text, ["简历技术专题", topic], topic, "专题")
        # 仅转换「资料索引」段；若已含聚合块则跳过，保证幂等可重跑
        if "本专题自动聚合" in text:
            changed.append(f"{sub}/README.md (已聚合，跳过)")
            f.write_text(text, encoding="utf-8")
            continue
        idx_header = re.search(r"^## .*资料索引.*$", text, flags=re.M)
        if idx_header:
            sec_beg = idx_header.end()
            sec_end_m = re.search(r"^## ", text[sec_beg:], flags=re.M)
            sec_end = sec_beg + (sec_end_m.start() if sec_end_m else len(text))
            sec = convert_paths(text[sec_beg:sec_end])
            full_dir = f"{rel_dir}/{sub}"
            sec = "\n\n" + dataview_block(full_dir) + "\n" + sec
            text = text[:sec_beg] + sec + text[sec_end:]
        f.write_text(text, encoding="utf-8")
        changed.append(f"{sub}/README.md")

    print("已完成：")
    for c in changed:
        print("  -", c)


if __name__ == "__main__":
    main()