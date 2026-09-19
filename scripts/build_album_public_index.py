#!/usr/bin/env python3
"""为云端应用生成相册公开索引（snapshot + 预签名缩略图 URL）。

数据真值源与 ``build_album_page.py`` 同源——读源库里的「乐仔的照片-相册.html」，
按月产出 26 组、5206 条照片索引，但本脚本的产物**专为云端应用消费**：

- base + 预签名缩略图 URL（HMAC token 派生自 session_secret，secret 仅在本机）
- 路径段逐段 percent-encode（源库有中文目录/括号/空格）
- 输出 ``public_index.json`` 到 ``src/openbiliclaw/web/public_album/``，由 8420
  挂载到 ``/public-album/public_index.json``，**完全公开**（不在 protected 前缀下）；
- 缩略图 URL 仍带 ``k=`` 签名，访问仍受 ``_album_media_ok`` 校验（签名 = 鉴权）
- 失败模式：源库新增照片 → 重跑本脚本（与 build_album_page 同款约定）

用法::

    .venv/bin/python scripts/build_album_public_index.py
    .venv/bin/python scripts/build_album_public_index.py --base https://lezai.odn.cc
    .venv/bin/python scripts/build_album_public_index.py --source /path/to/源库
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_album_page as page_builder  # noqa: E402

from openbiliclaw import auth_core  # noqa: E402
from openbiliclaw.album.paths import web_assets_dir as _web_assets_dir  # noqa: E402
from openbiliclaw.config import load_config  # noqa: E402

_DEFAULT_BASE = "https://lezai.odn.cc"
_OUTPUT_DIRNAME = "public_album"


def _signed_thumb_url(base: str, k: str, thumb_path: str) -> str:
    """``/album/thumbs/<名>`` 预签名 URL。

    ⚠️ ``thumb_path`` 来自 build_album_page 的 ``_thumb_url``，已经是逐段
    percent-encode 过一次的路径——绝不能再 quote（二次编码会让 ``(`` 变
    ``%2528``、中文变 ``%25E6..``，全部 404）。直接拼 base 即可。
    """
    return f"{base}{thumb_path}?k={k}"


def _signed_full_url(base: str, k: str, full_path: str) -> str:
    """``/album/heic/<stem>.jpg`` 或 ``/album/full/<ym>/<name>`` 预签名 URL。

    同上：``full_path`` 已由 build_album_page 编码过一次（HEIC→jpg 的 stem
    原样保留大小写），直接拼 base，禁止二次 quote。
    """
    return f"{base}{full_path}?k={k}"


def build_payload(source: Path, base: str, k: str) -> dict:
    """读源库 HTML → 解析 → 产出紧凑 payload dict。"""
    index_path = source / page_builder._INDEX_NAME
    if not index_path.is_file():
        raise SystemExit(
            f"源库索引不存在: {index_path}\n"
            "可通过 --source 指定，或确认源库已就位。"
        )

    html = index_path.read_text(encoding="utf-8")
    groups, _items = page_builder._parse_index(html)

    months = []
    for g in groups:
        items = []
        for it in g["items"]:
            # it["thumb"]/it["full"] 已是编码后的路径；t 存磁盘真实文件名
            thumb_name = unquote(it["thumb"].rsplit("/", 1)[-1])
            items.append(
                {
                    "n": it["name"],  # 原文件名（含扩展名）
                    "t": thumb_name,  # 缩略图名（不含扩展）
                    "d": it["date"],  # 拍摄时间
                    "s": it["size"],  # 原图字节
                    "u": _signed_thumb_url(base, k, it["thumb"]),
                    "f": _signed_full_url(base, k, it["full"]),
                }
            )
        cover_item = items[0] if items else None
        months.append(
            {
                "m": g["label"],  # 2024-05
                "n": g["meta"],  # "25 张 · 60MB"
                "c": cover_item["u"] if cover_item else None,
                "i": items,
            }
        )

    return {
        "v": 1,
        "base": base,
        "k": k,
        "total": sum(len(m["i"]) for m in months),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": str(source.relative_to(_REPO_ROOT)) if source.is_relative_to(_REPO_ROOT) else str(source),
        "months": months,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="为云端应用生成相册公开索引")
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="源库目录（默认取 album.paths 锚点）",
    )
    parser.add_argument(
        "--base",
        default=_DEFAULT_BASE,
        help=f"对外 base URL（默认 {_DEFAULT_BASE}，也支持局域网 IP）",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=_REPO_ROOT / "src/openbiliclaw/web/public_album",
        help="产物目录（默认 web/public_album/，由 8420 挂载到 /public-album/）",
    )
    parser.add_argument(
        "--also-web-assets",
        action="store_true",
        help="同时拷贝到 web/album/api/（不走公开 mount，调试用）",
    )
    args = parser.parse_args()

    source = args.source or page_builder.source_library_dir()
    if not source.is_dir():
        raise SystemExit(f"源库目录不存在: {source}")

    # 签名 token：从 session_secret 派生（HMAC-SHA256，32 字符，无状态）
    cfg = load_config()
    secret = (
        getattr(getattr(getattr(cfg, "api", None), "auth", None), "session_secret", "")
        or ""
    )
    if not secret:
        raise SystemExit(
            "session_secret 未配置；请检查 config.toml 的 [api.auth] 段。"
        )
    k = auth_core.album_media_token(secret)
    if not k:
        raise SystemExit("album_media_token 派生为空，请检查 sign 配置。")

    payload = build_payload(source=source, base=args.base, k=k)

    # 主输出：8420 静态托管目录
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "public_index.json"

    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    out_path.write_text(text, encoding="utf-8")

    months_n = len(payload["months"])
    total = payload["total"]
    size_kb = len(text.encode("utf-8")) / 1024
    print(
        f"[ok] 索引 → {out_path}\n"
        f"     月份={months_n} 总数={total} 大小={size_kb:.1f} KB base={args.base}"
    )

    # 按月拆分输出：manifest.json（元信息，~几十KB）+ months/<ym>.json（当月条目）。
    # 前端首屏只拉 manifest，切月才拉当月文件——首屏 1.5MB → 60KB。
    manifest = {
        "v": 2,
        "base": payload["base"],
        "k": payload["k"],
        "total": payload["total"],
        "generated_at": payload["generated_at"],
        "source": payload["source"],
        "months": [
            {"m": m["m"], "n": m["n"], "c": m["c"], "count": len(m["i"])}
            for m in payload["months"]
        ],
    }
    man_path = out_dir / "manifest.json"
    man_path.write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    months_dir = out_dir / "months"
    months_dir.mkdir(exist_ok=True)
    keep = set()
    sum_kb = 0.0
    for m in payload["months"]:
        if not m["i"]:
            continue
        keep.add(f"{m['m']}.json")
        mt = json.dumps(m, ensure_ascii=False, separators=(",", ":"))
        (months_dir / f"{m['m']}.json").write_text(mt, encoding="utf-8")
        sum_kb += len(mt.encode("utf-8")) / 1024
    # 清掉历史月份文件（源库重命名/删除后残留会 404 混入）
    for stale in months_dir.glob("*.json"):
        if stale.name not in keep:
            stale.unlink()
    print(
        f"[ok] 拆分 → {man_path.name}（{man_path.stat().st_size/1024:.1f} KB）"
        f" + months/×{len(keep)}（合计 {sum_kb:.1f} KB）"
    )

    # 兼容调试拷贝
    if args.also_web_assets:
        debug_dir = _web_assets_dir() / "api"
        debug_dir.mkdir(parents=True, exist_ok=True)
        debug_path = debug_dir / "public_index.json"
        debug_path.write_text(text, encoding="utf-8")
        print(f"[ok] 调试拷贝 → {debug_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())