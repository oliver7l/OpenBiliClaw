#!/usr/bin/env python3
"""为「乐仔相册」微信小程序生成数据与配置（只读源库，不改动任何照片）。

小程序与网页版**共用同一份真值源**（源库里的「乐仔的照片-相册.html」索引），
但两者的图片取用方式完全不同：

* 网页版跑在浏览器里，session cookie 随请求自动携带 → 走 cookie 门禁；
* 小程序没有 cookie 概念，``<image src>`` 既带不了 cookie 也带不了自定义
  header，只能**把媒体签名放进 URL**（``?k=<签名>``，服务端校验见
  ``api/auth.py::_album_media_ok``）。

所以本脚本产出的 ``data/photos.js`` 里，每条图片路径都是「已 URL 编码的最终
路径 + 媒体签名」，小程序端直接用、不做任何拼接（编码规则与网页版保持一致：
目录段与文件名逐段 ``quote(safe="")``，因为源库里有中文目录名/括号/空格）。

产物（均写进 ``miniprogram-album/``，其中 ``data/photos.js`` 含签名与个人
照片路径，**不入库**）：

* ``data/photos.js`` —— 5206 条索引（按月分组）+ base + 媒体签名
* ``utils/config.js`` —— base 与签名，供页面拼接

用法::

    .venv/bin/python scripts/build_album_miniprogram.py
    .venv/bin/python scripts/build_album_miniprogram.py --base https://lezai.odn.cc
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_album_page as page_builder  # noqa: E402

from openbiliclaw import auth_core  # noqa: E402
from openbiliclaw.album import paths as album_paths  # noqa: E402
from openbiliclaw.config import load_config  # noqa: E402

# 源库里混进来的非照片文件（总览拼图等由整理脚本生成，不属于相册内容）。
_SKIP_PREFIXES = ("00-",)
_SKIP_NAMES = ("乐仔的照片-相册.html",)


def _enc_rel(rel: str) -> str:
    """把源库相对路径逐段编码，保留 ``/`` 作为分隔符。"""
    return "/".join(quote(unquote(seg), safe="") for seg in rel.split("/"))


def _media_path(rel_url: str) -> str:
    """把网页版用的 ``/album/...`` 路径逐段做 URL 编码。"""
    prefix, _, tail = rel_url.partition("/album/")
    if not tail:
        return rel_url
    # 网页版在拼接时已对文件名 quote 过一次，这里再规范化一遍（同分段编码）。
    parts = [quote(unquote(seg), safe="") for seg in tail.split("/")]
    return f"{prefix}/album/" + "/".join(parts)


def build(source_dir: Path, base: str, out_dir: Path) -> dict:
    index_file = source_dir / page_builder._INDEX_NAME
    if not index_file.exists():
        raise SystemExit(f"❌ 找不到源库索引: {index_file}")

    html = index_file.read_text(encoding="utf-8")
    groups, _photos = page_builder._parse_index(html)

    cfg = load_config()
    secret = cfg.api.auth.session_secret
    if not secret:
        raise SystemExit("❌ config.toml 里没有 api.auth.session_secret，无法派生媒体签名")
    media_key = auth_core.album_media_token(secret)
    if not media_key:
        raise SystemExit("❌ 媒体签名派生失败")

    months: list[dict] = []
    skipped: list[str] = []
    total = 0
    for group in groups:
        items: list[list[str]] = []
        for rec in group["items"]:
            name = rec["name"]
            if name.startswith(_SKIP_PREFIXES) or name in _SKIP_NAMES:
                skipped.append(name)
                continue
            thumb = _media_path(rec["thumb"])
            full = _media_path(rec["full"])
            items.append([thumb.rsplit("/", 1)[-1], full, rec["date"]])
        if not items:
            continue
        months.append({"label": group["label"].strip(), "meta": group["meta"], "items": items})
        total += len(items)

    payload = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "base": base.rstrip("/"),
        "key": media_key,
        "total": total,
        "months": months,
    }

    # 注意：这里刻意不用 ``mkdir(..., exist_ok=True)``——WorkBuddy 沙箱注入的
    # sitecustomize shim 会在目录已存在时把它误判成 PermissionError（EEXIST）
    # 直接崩掉，改成先判断存在性。
    for target in (out_dir, out_dir / "data", out_dir / "utils"):
        if not target.exists():
            target.mkdir(parents=True)

    data_js = (
        "// 由 scripts/build_album_miniprogram.py 生成，请勿手工编辑。\n"
        "// 含媒体签名与个人照片路径，不入库（见 .gitignore）。\n"
        "module.exports = " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n"
    )
    (out_dir / "data" / "photos.js").write_text(data_js, encoding="utf-8")

    config_js = (
        "// 由 scripts/build_album_miniprogram.py 生成，请勿手工编辑。\n"
        "module.exports = {\n"
        f"  base: {json.dumps(base.rstrip('/'))},\n"
        f"  mediaKey: {json.dumps(media_key)},\n"
        f"  generated: {json.dumps(payload['generated'])},\n"
        "};\n"
    )
    (out_dir / "utils" / "config.js").write_text(config_js, encoding="utf-8")

    return {
        "months": len(months),
        "photos": total,
        "skipped": skipped,
        "bytes": len(data_js.encode("utf-8")),
        "key": media_key,
        "out": out_dir,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="生成乐仔相册小程序的数据与配置")
    ap.add_argument("--source", default=None, help="源库目录，默认取 album 模块锚点")
    ap.add_argument("--base", default="https://lezai.odn.cc", help="后端公网基址")
    ap.add_argument(
        "--out",
        default=str(_REPO_ROOT / "miniprogram-album"),
        help="小程序工程目录",
    )
    args = ap.parse_args()

    source = Path(args.source).expanduser() if args.source else album_paths.source_library_dir()
    result = build(source, args.base, Path(args.out))

    print(f"✅ 已生成小程序数据 → {result['out']}")
    print(f"   分组 {result['months']} 个月 / 照片 {result['photos']} 张")
    print(f"   data/photos.js 体积 {result['bytes'] / 1024:.1f} KB")
    print(f"   媒体签名 {result['key']}（改 session_secret 即可整体作废）")
    if result["skipped"]:
        print(f"   已排除非照片条目 {len(result['skipped'])} 个：{result['skipped'][:5]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
