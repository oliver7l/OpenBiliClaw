#!/usr/bin/env python3
"""从简历底座派生定制版（底座 → 派生，杜绝 46 个手抄副本的维护灾难）。

用法（单个）：
    .venv/bin/python scripts/resume_library/derive_resume.py \
        --intent "算法工程师（搜索/推荐/广告方向）" \
        --promote 中信银行信用卡中心,腾讯 \
        --out "简历库/20_公司定制版/腾讯/童力-腾讯-微信支付数据科学-深圳版.md"

用法（批量，按派生清单）：
    .venv/bin/python scripts/resume_library/derive_resume.py --batch
    # 派生清单：简历库/20_公司定制版/_派生清单.md，每行一条：
    #   相对路径 | 求职意向 | promote公司列表(逗号分隔，可空)

派生规则（均对 10_母版/童力-简历-底座.md 施加）：
1. 意向行：在基本信息块后插入 `**求职意向**：…`
2. 出处剥离：删除全部 `〔出处：…〕`（投递版不带内审标注）
3. 章节重排：--promote 的公司段在「工作经历」内按给定顺序提前
生成 md 后自动调用 build_resume_files.py 出 docx/pdf（除非 --no-docx）。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BASE_MD = REPO / "简历库/10_母版/童力-简历-底座.md"
BUILD = REPO / "scripts/resume_library/build_resume_files.py"
MANIFEST = REPO / "简历库/20_公司定制版/_派生清单.md"

_PROVENANCE_RE = re.compile(r"〔出处：[^〕]*〕")


def load_base() -> str:
    text = BASE_MD.read_text(encoding="utf-8")
    # 去掉底座头部的改动规则引用块（投递版不需要）
    return re.sub(r"> \*\*改动规则\*\*.*?\n(?:>.*\n)*", "", text, count=1)


def split_work_experience(base_text: str) -> tuple[str, list[tuple[str, str]], str]:
    """把「## 工作经历」段拆成 (前缀, [公司段], 后缀)。

    公司段以 `### ` 开头，直到下一个 `## ` 或文末。
    """
    lines = base_text.splitlines(keepends=True)
    start = next(i for i, l in enumerate(lines) if l.startswith("## 工作经历"))
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    prefix = "".join(lines[: start + 1])
    body = lines[start + 1 : end]
    suffix = "".join(lines[end:])

    sections: list[tuple[str, list[str]]] = []
    for line in body:
        if line.startswith("### "):
            sections.append((line, [line]))
        elif sections:
            sections[-1][1].append(line)
        else:
            sections.append(("__preamble__", [line]))

    preamble = ""
    company_sections: list[tuple[str, str]] = []
    for header, chunk in sections:
        text = "".join(chunk)
        if header == "__preamble__":
            preamble = text
        else:
            company = header.removeprefix("### ").split("|")[0].strip()
            company_sections.append((company, text))
    return prefix, company_sections, suffix


def reorder_companies(
    base_text: str, promote: list[str]
) -> str:
    """把 --promote 里的公司段按顺序提到工作经历最前（其余保持原序）。"""
    prefix, company_sections, suffix = split_work_experience(base_text)
    if not promote:
        return base_text

    by_company: dict[str, str] = {}
    order: list[str] = []
    for company, text in company_sections:
        if company not in by_company:
            order.append(company)
        by_company[company] = text  # 同名公司保留最后一段（不应出现）

    promoted: list[str] = []
    for want in promote:
        matches = [c for c in order if want in c]
        if not matches:
            print(f"  ⚠️ promote 未匹配到公司段：{want}", file=sys.stderr)
            continue
        promoted.append(matches[0])

    rest = [c for c in order if c not in promoted]
    new_order = promoted + rest
    rebuilt = prefix + (preamble or "") + "".join(by_company[c] for c in new_order) + suffix
    return rebuilt


def derive(base_text: str, *, intent: str, promote: list[str], title: str) -> str:
    text = reorder_companies(base_text, promote)
    text = _PROVENANCE_RE.sub("", text)  # 投递版不带出处标注
    # H1：投递版一律「# 童力」，可选后缀（--title）
    text = text.replace("# 童力（简历底座 · 唯一真值母版）", f"# 童力（{title}）" if title else "# 童力", 1)
    if intent:
        # 意向行插在分隔线 `---` 之前（即基本信息块之后）
        text = text.replace("\n---\n", f"\n**求职意向**：{intent}\n\n---\n", 1)
    return text


def build_docx_pdf(md_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(BUILD), str(md_path)],
        cwd=str(REPO),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(result.stdout[-2000:], result.stderr[-2000:], sep="\n", file=sys.stderr)
        raise SystemExit(f"build_resume_files.py 失败：{md_path}")


def derive_one(
    out_path: Path, *, intent: str, promote: list[str], title: str, no_docx: bool
) -> bool:
    base_text = load_base()
    text = derive(base_text, intent=intent, promote=promote, title=title)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    try:
        display = out_path.relative_to(REPO)
    except ValueError:
        display = out_path
    print(f"  ✓ {display}")
    if no_docx:
        return True
    try:
        build_docx_pdf(out_path)
    except SystemExit:
        # 校验告警（如 WXG 专用数字清单对其它版本的误报）不中断批量；
        # docx/pdf 已产出，末尾汇总失败清单人工复核。
        print(f"  ⚠️ 构建校验告警：{display}", file=sys.stderr)
        return False
    return True


def parse_manifest() -> list[tuple[Path, str, list[str], str]]:
    """派生清单格式：`相对路径 | 求职意向 | promote公司 | 标题`（后两列可空）。"""
    rows: list[tuple[Path, str, list[str], str]] = []
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ">", "```", "|--", "|--")) or line.startswith("|:"):
            continue
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2 or cells[0] in ("相对路径", "---", ""):
            continue
        rel = cells[0]
        intent = cells[1] if len(cells) > 1 else ""
        promote = [c for c in (cells[2].split(",") if len(cells) > 2 and cells[2] else []) if c]
        title = cells[3] if len(cells) > 3 else ""
        rows.append((REPO / "简历库" / rel, intent, promote, title))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intent", default="", help="求职意向（插入基本信息后）")
    parser.add_argument("--promote", default="", help="提前的公司段，逗号分隔")
    parser.add_argument("--title", default="", help="H1 标题（默认保留底座标题）")
    parser.add_argument("--out", default="", help="输出 md 路径")
    parser.add_argument("--batch", action="store_true", help="按派生清单批量生成")
    parser.add_argument("--no-docx", action="store_true", help="只生成 md")
    args = parser.parse_args()

    if args.batch:
        rows = parse_manifest()
        if not rows:
            raise SystemExit(f"派生清单为空或解析失败：{MANIFEST}")
        print(f"派生清单共 {len(rows)} 个版本：")
        for out_path, intent, promote, title in rows:
            derive_one(out_path, intent=intent, promote=promote, title=title, no_docx=args.no_docx)
        print("全部完成。")
        return

    if not args.out:
        raise SystemExit("单个派生需要 --out；批量用 --batch")
    promote = [c for c in args.promote.split(",") if c]
    derive_one(
        Path(args.out),
        intent=args.intent,
        promote=promote,
        title=args.title,
        no_docx=args.no_docx,
    )


if __name__ == "__main__":
    main()
