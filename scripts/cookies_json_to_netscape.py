#!/usr/bin/env python3
"""把浏览器扩展导出的 Cookie JSON 转成 yt-dlp 用的 Netscape cookie 文件。

背景：YouTube 会周期性把出口 IP 判为 bot，yt-dlp 的 `--cookies-from-browser`
经常报 "cookies are no longer valid"（浏览器端 cookie 已轮换）。可靠做法是
从浏览器导出 .youtube.com 的 Cookie JSON，转成 Netscape 格式固定给 yt-dlp 用。

用法:
    python3 scripts/cookies_json_to_netscape.py cookies.json [输出路径]

默认输出: data/youtube_cookies.txt（已在 .gitignore 内，权限 600）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DEFAULT_OUT = BASE / "data" / "youtube_cookies.txt"


def convert(src: Path, dst: Path) -> int:
    cookies = json.loads(src.read_text(encoding="utf-8"))
    if isinstance(cookies, dict):  # 有些扩展包一层 {"cookies": [...]}
        cookies = cookies.get("cookies") or cookies.get("data") or []
    lines = [
        "# Netscape HTTP Cookie File",
        f"# converted from {src.name} by scripts/cookies_json_to_netscape.py",
        "",
    ]
    for c in cookies:
        domain = c.get("domain", "")
        if not domain or not c.get("name"):
            continue
        include_sub = "TRUE" if domain.startswith(".") else "FALSE"
        secure = "TRUE" if c.get("secure") else "FALSE"
        try:
            expires = str(int(float(c.get("expirationDate") or 0)))
        except (TypeError, ValueError):
            expires = "0"
        lines.append(
            "\t".join([domain, include_sub, c.get("path", "/"), secure, expires,
                       c["name"], c.get("value", "")])
        )
    dst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(dst, 0o600)
    return len(cookies)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUT
    n = convert(src, dst)
    print(f"已写入 {n} 条 cookie → {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
