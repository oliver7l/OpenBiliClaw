"""本文件已废弃，请使用 scripts/import_kb_questions_to_interview_db.py。

此文件仅作兼容重定向，代码不再维护。
"""
import sys as _sys
from pathlib import Path as _Path

# 重定向到新脚本（使用相同参数）
_new_script = _Path(__file__).resolve().parent / "import_kb_questions_to_interview_db.py"
assert _new_script.exists(), f"找不到目标脚本: {_new_script}"

if __name__ == "__main__":
    _sys.argv[0] = str(_new_script)
    exec(_new_script.read_text(encoding="utf-8"))
