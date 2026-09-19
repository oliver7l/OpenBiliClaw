#!/usr/bin/env python3
"""为云端应用生成相册公开索引（snapshot + 预签名缩略图 URL）。

**v3（2026-09-19）：真值源切换到统一相册库 library.db。**

旧版读源库 HTML（``乐仔的照片-相册.html``）解析条目，两大缺陷：
1. 1877/5206 条（36%）堆进「未标注日期」——HTML 分组期丢日期；
2. 3415 条是夸克下载的重复副本（同 md5 的 ``(1)(2)`` 连拍副本），
   其原图在分月整理回滚后已搬进 ``_重复/``，点开大图 404。

新版口径：
- 照片集合 = library.db ``files``（source=夸克网盘-乐仔的照片，kind=photo，
  is_primary=1，排除生成用的总览拼图）→ **1790 张真实照片**；
- 月份 = 库内 ``ym`` → HTML 条目日期 → 文件名 ``IMGyyyymmdd`` 模式 → mtime；
- 缩略图名 / 日期字符串：源 HTML 仍是 thumb 命名的唯一映射源
  （``_thumbs/`` 文件名 = 原名或 ``<8hex>_原名.jpg``），按 (月目录, 原名) join；
- 原图 URL：HEIC → ``/album/heic/<stem>.jpg``（预转目录，平铺）；
  其余 → ``/album/full/<名>``（**平铺快照目录**，无月份子目录——
  月目录已不存在，继续拼 ``<ym>/<name>`` 会 404）。

URL 编码铁律：thumb 名含括号/中文，构造 URL 时 **恰好 quote 一次**
（``quote(name, safe='')``），绝不能二次编码（``(`` → ``%2528`` 全 404）。

产物（8420 挂载到 ``/public-album/``，完全公开，缩略图/原图仍带 k 签名）：
- ``manifest.json`` —— 月份元信息（~几 KB，首屏）
- ``months/<ym>.json`` —— 当月条目（切月才拉）

用法::

    .venv/bin/python scripts/build_album_public_index.py
    .venv/bin/python scripts/build_album_public_index.py --base https://lezai.odn.cc
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_album_page as page_builder  # noqa: E402  (source_library_dir 锚点)

from openbiliclaw import auth_core  # noqa: E402
from openbiliclaw.config import load_config  # noqa: E402

_DEFAULT_BASE = "https://lezai.odn.cc"
_OUTPUT_DIRNAME = "public_album"
_LIBRARY_DB = _REPO_ROOT / "19_统一相册库" / "library.db"
_NODATE = "未标注日期"
# 生成的拼图不是照片，不进相册
_EXCLUDE_NAMES = {"00-总览拼图.jpg", "00-总览拼图-5206张.jpg"}
# 人物 tab 白名单：只发 model 源（第 8 轮现役发货口径）。
# 妈妈 = 零视觉锚点身份，apply 护栏本就不发 model 标签 ⇒ 不上架（勿凑数）。
_PEOPLE_MODEL_SOURCES = ("乐仔", "艳艳", "我", "七月", "爸爸")

# 源 HTML 条目：data-full="月目录/原名"、src="_thumbs/缩略名"、data-d 日期串
_CELL_RE = re.compile(
    r'<div class="cell" data-n="[^"]*" data-d="([^"]*)" data-s="(\d+)">'
    r'\s*<img[^>]*?src="([^"]*)"[^>]*?data-full="([^"]*)"'
)
_FILENAME_YM = re.compile(r"(?:IMG|DSC|PXL|20)\D*?(20\d{2})(\d{2})\d{2}", re.I)


def _signed(base: str, k: str, path: str) -> str:
    """``path`` 是未编码的服务器路径，逐段 quote 后拼 base。

    ``safe='/'``——目录分隔符必须保留（safe='' 会把 ``/`` 编成 ``%2F``，
    整条 URL 变畸形）；文件名里的括号/空格/中文照常编码。
    """
    return f"{base}{quote(path, safe='/')}?k={k}"


def _load_html_mapping() -> dict[tuple[str, str], tuple[str, str]]:
    """源 HTML → {(月目录, 原名): (缩略图磁盘名, 日期字符串)}。"""
    index_path = page_builder.source_library_dir() / page_builder._INDEX_NAME
    html = index_path.read_text(encoding="utf-8")
    mapping: dict[tuple[str, str], tuple[str, str]] = {}
    for m in _CELL_RE.finditer(html):
        date_str, _size, thumb_src, full_rel = m.groups()
        ym_dir, _, name = unquote(full_rel).rpartition("/")
        thumb_name = unquote(thumb_src.rsplit("/", 1)[-1])
        mapping[(ym_dir, name)] = (thumb_name, date_str.strip())
    return mapping


def _derive_ym(
    db_ym: str | None, date_str: str, name: str, mtime: float
) -> str:
    """月份四级兜底：库 ym → HTML 日期 → 文件名模式 → mtime。"""
    if db_ym:
        return db_ym
    m = re.match(r"(\d{4}-\d{2})", date_str)
    if m:
        return m.group(1)
    fm = _FILENAME_YM.search(name)
    if fm:
        return f"{fm.group(1)}-{fm.group(2)}"
    return datetime.fromtimestamp(mtime).strftime("%Y-%m")


def _display_date(date_str: str, mtime: float) -> str:
    """展示用日期串：优先 HTML 条目日期，缺失时用 mtime。"""
    if date_str:
        return date_str
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")


def build_payload(base: str, k: str) -> dict:
    """library.db 真值 + HTML 映射 → 紧凑 payload。"""
    if not _LIBRARY_DB.is_file():
        raise SystemExit(f"统一相册库不存在: {_LIBRARY_DB}")
    html_map = _load_html_mapping()
    snap_dir = page_builder.source_library_dir() / "_整理前_硬链接快照"
    snap_names = {p.name for p in snap_dir.iterdir()} if snap_dir.is_dir() else set()

    db = sqlite3.connect(f"file:{_LIBRARY_DB}?mode=ro", uri=True)
    rows = db.execute(
        "SELECT rel, ym, taken, mtime, size, content_key FROM files "
        "WHERE source='夸克网盘-乐仔的照片' AND kind='photo' AND is_primary=1"
    ).fetchall()
    # 现役 model 源人脸标签 → content_key → 人名列表（人物 tab 数据面）
    person_of_ck: dict[str, list[str]] = {}
    for person, ck in db.execute(
        "SELECT pt.person, pt.content_key FROM photo_person_tags pt "
        "JOIN files f ON f.content_key = pt.content_key "
        "WHERE pt.source='model' AND f.source='夸克网盘-乐仔的照片' "
        "AND f.kind='photo' AND f.is_primary=1"
    ):
        person_of_ck.setdefault(ck, []).append(person)
    db.close()

    buckets: dict[str, list[dict]] = {}
    no_map: list[str] = []
    for rel, ym, _taken, mtime, size, ck in rows:
        album_rel = rel.removeprefix("乐仔的照片/")
        ym_dir, _, name = album_rel.rpartition("/")
        if name in _EXCLUDE_NAMES:
            continue
        hit = html_map.get((ym_dir, name))
        if hit is None:  # 兜底：仅按文件名（月目录口径不一致时）
            hit = next((v for (d, n), v in html_map.items() if n == name), None)
        if hit is None:
            no_map.append(rel)
            continue
        thumb_name, date_str = hit
        month = _derive_ym(ym, date_str, name, mtime)
        stem, ext = name.rsplit(".", 1) if "." in name else (name, "")
        if ext.lower() == "heic":
            full_path = f"/album/heic/{stem}.jpg"
        else:
            # 平铺快照：直接按名取（名字必须真实存在，否则原图 404）
            if snap_names and name not in snap_names:
                no_map.append(rel)
                continue
            full_path = f"/album/full/{name}"
        persons = sorted(set(person_of_ck.get(ck, [])))
        buckets.setdefault(month, []).append(
            {
                "n": name,
                "t": thumb_name,
                "d": _display_date(date_str, mtime),
                "s": size,
                "u": _signed(base, k, f"/album/thumbs/{thumb_name}"),
                "f": _signed(base, k, full_path),
                "_p": persons,
            }
        )

    if no_map:
        print(f"[warn] {len(no_map)} 条无缩略图映射/原图缺失，已跳过（首条: {no_map[0]}）")

    months = []
    dated = sorted(m for m in buckets if re.match(r"^\d{4}-\d{2}$", m))
    for month in dated + ([_NODATE] if _NODATE in buckets else []):
        items = buckets[month]
        total_mb = sum(it["s"] for it in items) / 1048576
        months.append(
            {
                "m": month,
                "n": f"{len(items)} 张 · {total_mb:.0f}MB",
                "c": items[0]["u"],
                "i": items,
            }
        )

    return {
        "v": 3,
        "base": base,
        "k": k,
        "total": sum(len(m["i"]) for m in months),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "19_统一相册库/library.db",
        "months": months,
        "_buckets": buckets,
    }


def build_people(payload: dict) -> dict:
    """按人聚合（仅 model 源白名单）→ 人物 tab 数据。

    people.json 是人物清单（头像墙），people/<person>.json 是该人
    按月分组的条目（结构复用 months/<ym>.json 的 item，前端零新组件）。
    """
    buckets: dict[str, list[dict]] = payload.pop("_buckets")
    per_person: dict[str, dict[str, list[dict]]] = {}
    for month, items in buckets.items():
        for it in items:
            for p in it.pop("_p"):
                if p in _PEOPLE_MODEL_SOURCES:
                    per_person.setdefault(p, {}).setdefault(month, []).append(it)

    people = []
    dated = sorted(m for m in buckets if re.match(r"^\d{4}-\d{2}$", m))
    for person, months_map in sorted(
        per_person.items(), key=lambda kv: -sum(len(v) for v in kv[1].values())
    ):
        p_months = []
        for month in [m for m in dated if m in months_map] + (
            [_NODATE] if _NODATE in months_map else []
        ):
            items = months_map[month]
            p_months.append(
                {"m": month, "count": len(items), "c": items[0]["u"]}
            )
        # 头像 = 最近一张（p_months 升序，取尾月首图）
        people.append(
            {
                "p": person,
                "count": sum(len(v) for v in months_map.values()),
                "c": p_months[-1]["c"] if p_months else None,
                "months": p_months,
            }
        )
    return {
        "v": 1,
        "base": payload["base"],
        "k": payload["k"],
        "generated_at": payload["generated_at"],
        "people": people,
        "_per_person": per_person,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="为云端应用生成相册公开索引")
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
    args = parser.parse_args()

    # 签名 token：从 session_secret 派生（HMAC-SHA256，32 字符，无状态）
    cfg = load_config()
    secret = (
        getattr(getattr(getattr(cfg, "api", None), "auth", None), "session_secret", "")
        or ""
    )
    if not secret:
        raise SystemExit("session_secret 未配置；请检查 config.toml 的 [api.auth] 段。")
    k = auth_core.album_media_token(secret)
    if not k:
        raise SystemExit("album_media_token 派生为空，请检查 sign 配置。")

    payload = build_payload(base=args.base, k=k)
    people = build_people(payload)

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # manifest.json（首屏几 KB）+ months/<ym>.json（切月才拉）
    manifest = {
        "v": payload["v"],
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
    for stale in months_dir.glob("*.json"):
        if stale.name not in keep:
            stale.unlink()
    # 旧版全量索引退役（v3 起前端只用 manifest，留着只会误导）
    old_full = out_dir / "public_index.json"
    if old_full.exists():
        old_full.unlink()

    # 人物 tab：people.json（清单）+ people/<person>.json（按月条目）
    per_person = people.pop("_per_person")
    people_path = out_dir / "people.json"
    people_path.write_text(
        json.dumps(people, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    people_dir = out_dir / "people"
    people_dir.mkdir(exist_ok=True)
    p_keep = set()
    for person, months_map in per_person.items():
        p_keep.add(f"{person}.json")
        dated_m = sorted(m for m in months_map if re.match(r"^\d{4}-\d{2}$", m))
        doc = {
            "p": person,
            "months": [
                {"m": m, "i": months_map[m]}
                for m in dated_m + ([_NODATE] if _NODATE in months_map else [])
            ],
        }
        (people_dir / f"{person}.json").write_text(
            json.dumps(doc, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    for stale in people_dir.glob("*.json"):
        if stale.name not in p_keep:
            stale.unlink()

    nodate = next((m for m in manifest["months"] if m["m"] == _NODATE), None)
    print(
        f"[ok] manifest → {man_path}（{man_path.stat().st_size/1024:.1f} KB）\n"
        f"     月份={len(keep)} 总数={payload['total']} "
        f"未标注={nodate['count'] if nodate else 0} "
        f"months/ 合计 {sum_kb:.1f} KB base={args.base}\n"
        f"[ok] people → {people_path.name} "
        f"({', '.join(p['p'] + ':' + str(p['count']) for p in people['people'])}) "
        f"people/×{len(p_keep)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
