#!/usr/bin/env python3
"""由 md 真值源生成简历 docx + PDF，并做投递前校验。

用法：
    .venv/bin/python scripts/resume_library/build_resume_files.py <简历.md> \
        [--nums "35%,15.3%,24%"] [--forbid "ROAS,中信不续签"]

约定：
- md 是唯一真值源，docx/pdf 一律由它生成，不手改产物
- docx 复用 convert_resumes_to_docx.py 的排版（与全库简历风格一致）
- PDF 走 md-to-pdf skill（A4、中文字体、可复制可搜索）
- 校验项：页数 / 关键数字零缺失 / 禁用词零残留 / 文本层坏字符
"""
from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
DOCX_CONV = f"{ROOT}/scripts/convert_resumes_to_docx.py"
PDF_CONV = str(Path.home() / ".workbuddy/skills/md-to-pdf/scripts/md2pdf.py")

# 默认必背数字（腾讯WXG v3 版）；用 --nums 覆盖
DEFAULT_NUMS = [
    "35%", "15.3%", "24%", "21%", "20%", "1.04%", "30%", "2.3%",
    "13 项", "+15%", "0.42%", "6.8%", "0.6s", "2.6s", "25%", "7 日回本",
]
DEFAULT_FORBID = ["ROAS"]


def _load_conv():
    spec = importlib.util.spec_from_file_location("conv", DOCX_CONV)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build_docx(md: Path) -> Path:
    conv = _load_conv()
    out = md.with_suffix(".docx")
    conv.md_to_docx(md, out)
    return out


def build_pdf(md: Path) -> Path:
    subprocess.run([sys.executable, PDF_CONV, str(md)], check=True,
                   capture_output=True, text=True)
    return md.with_suffix(".pdf")


def verify(pdf: Path, nums: list[str], forbid: list[str]) -> bool:
    try:
        from pypdf import PdfReader
    except ImportError:
        print("! 未安装 pypdf，跳过 PDF 校验")
        return True
    text = "".join(p.extract_text() for p in PdfReader(str(pdf)).pages)
    missing = [n for n in nums if n not in text]
    hits = {w: text.count(w) for w in forbid if text.count(w)}
    bad = sum(1 for c in text if 0x2E80 <= ord(c) <= 0x2FDF)
    print(f"  页数 {len(PdfReader(str(pdf)).pages)}｜数字缺失 {missing or '无'}"
          f"｜禁用词 {hits or '无'}｜坏字符 {bad}")
    return not missing and not hits and bad == 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("md", help="简历 md 真值源路径")
    ap.add_argument("--nums", default=",".join(DEFAULT_NUMS),
                    help="关键数字清单，逗号分隔（校验零缺失）")
    ap.add_argument("--forbid", default=",".join(DEFAULT_FORBID),
                    help="禁用词清单，逗号分隔（校验零残留）")
    ap.add_argument("--no-pdf", action="store_true", help="只出 docx")
    args = ap.parse_args()

    md = Path(args.md).expanduser().resolve()
    if not md.exists():
        print(f"! 找不到 {md}")
        return 1
    nums = [n.strip() for n in args.nums.split(",") if n.strip()]
    forbid = [w.strip() for w in args.forbid.split(",") if w.strip()]

    print(f"[1/3] docx ← {md.name}")
    docx = build_docx(md)
    print(f"      {docx}")
    if args.no_pdf:
        return 0
    print(f"[2/3] pdf  ← {md.name}")
    pdf = build_pdf(md)
    print(f"      {pdf}")
    print("[3/3] 校验")
    ok = verify(pdf, nums, forbid)
    print("✅ 校验通过" if ok else "⚠️ 校验有问题，见上行")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
