"""兼容垫片：本模块已迁移至 ``openbiliclaw.interview.study.cli``。

``python -m openbiliclaw.interview.questions.cli <子命令>`` 仍可用（转发到新模块）。
下一个大版本摘除。
"""

from __future__ import annotations

from openbiliclaw.interview.study.cli import main

__all__ = ["main"]

if __name__ == "__main__":  # pragma: no cover
    main()
