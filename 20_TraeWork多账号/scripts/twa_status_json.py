#!/usr/bin/env python3
"""输出机器可读状态 JSON（供 TraeBar 托盘 App 调用）：
{
  "current_uid": "xxx" | null,          # 主客户端当前登录 uid
  "accounts": [ {"uid","name","phone","credits","has_profile"}, ... ]
}
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from twa_switch_account import (  # noqa: E402
    ASSISTANT_DATA,
    MAIN_STORAGE,
    current_uid_of,
    load_profiles,
)


def main() -> None:
    cur = current_uid_of(MAIN_STORAGE) if MAIN_STORAGE.exists() else None
    profiles = load_profiles()
    try:
        accounts = json.loads((ASSISTANT_DATA / "accounts.json").read_text())
    except Exception:
        accounts = []
    out = {
        "current_uid": cur,
        "accounts": [
            {
                "uid": a.get("user_id"),
                "name": a.get("name") or a.get("phone") or a.get("user_id"),
                "phone": a.get("phone"),
                "credits": (a.get("credit_snapshot") or {}).get("credits"),
                "has_profile": a.get("user_id") in profiles,
            }
            for a in accounts
        ],
    }
    json.dump(out, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
