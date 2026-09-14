"""兼容垫片：本模块已迁移至 ``openbiliclaw.interview.study.seed_iq_questions``。

``python -m openbiliclaw.interview.questions.seed_iq_questions`` 仍可用（转发到新模块）。
下一个大版本摘除。
"""

from __future__ import annotations

from openbiliclaw.interview.study.seed_iq_questions import main

__all__ = ["main"]

if __name__ == "__main__":  # pragma: no cover
    main()
