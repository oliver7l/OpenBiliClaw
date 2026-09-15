#!/usr/bin/env python3
"""面试模块优化 · 002：扫描录音/转写资产入库 + 检测失效条目。

背景（2026-09-15 诊断）
--------------------
``interview_recordings`` 是一张**零引用死表**——全树没有任何代码读写它，
导致：

1. 比亚迪 HR 面那条（id=1）卡在 ``status='transcribing'`` 五天无人发现；
2. 文件系统里躺着 3 个音频 + 3 份转写从未被索引。

本脚本做两件事：

- **扫描入库**：遍历 ``求职知识库/03_岗位弹药库/*/`` 下的录音目录，
  把音频文件登记进 ``interview_recordings``，并自动关联同目录的转写文件。
- **失效检测**：列出 ``audio_path`` 在磁盘上已不存在的条目（孤儿记录），
  这类条目的转写永远等不到结果。

幂等：按 ``audio_path`` 唯一去重，已存在则跳过（除非 ``--force``）。

用法
----
    .venv/bin/python scripts/interview_scan_recordings.py            # 演练
    .venv/bin/python scripts/interview_scan_recordings.py --apply    # 落库
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "interview.db"
AMMO_DIR = PROJECT_ROOT / "求职知识库" / "03_岗位弹药库"

AUDIO_EXT = {".aac", ".m4a", ".mp3", ".wav", ".flac", ".ogg", ".opus"}
# 录音/转写可能所在的目录名。
# ⚠️ 必须与实际目录名保持同步：2026-09-15 巡检发现 GoodLuckStudio 的
# 「05_面试记录」早已改名为「05_面试复盘」，而库里 audio_path 还指向旧路径，
# 导致一条已转写完成的录音被判为"文件不存在"。改目录名时务必同步本常量。
RECORD_DIR_HINTS = (
    "03_沟通录音", "05_面试记录", "05_面试复盘",
    "录音", "沟通录音", "面试记录", "面试复盘",
)
# 转写文件关键词
TRANSCRIPT_HINTS = ("转写", "转录", "转文字", "全记录", "复盘", "逐字")
TRANSCRIPT_EXT = {".txt", ".md"}

# 衍生文件标记：同一场录音的降噪/分段/裁剪版本，不重复登记
DERIVED_HINTS = ("part", "16k", "8k", "44k", "-cut", "chunk", "seg", "split")

# 文件名里的日期：20260915103648 / 2026-09-15 / 20260915
DATE_PATTERNS = (
    re.compile(r"(?P<y>\d{4})(?P<m>\d{2})(?P<d>\d{2})[_]?(?P<hm>\d{4})?(?P<s>\d{2})?"),
    re.compile(r"(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})"),
)

ROUND_RULES: tuple[tuple[str, str], ...] = (
    ("一面", "一面"),
    ("二面", "二面"),
    ("三面", "三面"),
    ("hr", "HR沟通"),
    ("HR", "HR沟通"),
    ("谈薪", "谈薪沟通"),
    ("boss", "一面"),
)


def extract_date(text: str) -> str:
    """从文件名里抽日期，返回 YYYY-MM-DD；抽不到返回 ''。"""
    for pat in DATE_PATTERNS:
        m = pat.search(text)
        if m:
            y, mo, d = m.group("y"), m.group("m"), m.group("d")
            if 2000 <= int(y) <= 2100 and 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
                return f"{y}-{mo}-{d}"
    return ""


def infer_round(text: str) -> str:
    low = text.lower()
    for kw, rnd in ROUND_RULES:
        if kw.lower() in low:
            return rnd
    return ""


def find_transcript(audio: Path) -> str:
    """在同目录（及父目录）找与音频同日期/同关键词的转写文件。"""
    stem = audio.stem
    date = extract_date(stem)
    candidates: list[Path] = []
    for parent in (audio.parent, audio.parent.parent):
        for f in parent.iterdir() if parent.is_dir() else []:
            if not f.is_file() or f.suffix.lower() not in TRANSCRIPT_EXT:
                continue
            if not any(h in f.name for h in TRANSCRIPT_HINTS):
                continue
            candidates.append(f)
    if not candidates:
        return ""
    # 优先：同日期 > 文件名包含音频 stem 的前缀 > 最新修改
    # 注意：日期在文件名里可能是紧凑格式（20260915）也可能是横线格式（2026-09-15），
    # 两种都要比，否则会漏关联（比亚迪「薪酬沟通全记录-20260915.md」即此坑）。
    compact = date.replace("-", "") if date else ""

    def score(f: Path) -> tuple[int, int, float]:
        same_date = 1 if (date and (date in f.name or (compact and compact in f.name))) else 0
        prefix = stem[:8]
        name_match = 1 if (prefix and prefix in f.name) else 0
        return (same_date, name_match, f.stat().st_mtime)

    candidates.sort(key=score, reverse=True)
    best = candidates[0]
    if score(best)[0] == 0 and score(best)[1] == 0:
        return ""
    try:
        return str(best.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(best)


def scan_audio_files() -> list[dict[str, str]]:
    """扫描弹药库下的录音资产。

    跳过降噪/分段等**衍生版本**（如 ``余宁通话-16k.wav``、``xxx-part2.wav``）——
    它们是同一场录音的处理产物，重复登记会让同一场通话出现多条记录。
    """
    from datetime import datetime

    found: list[dict[str, str]] = []
    if not AMMO_DIR.is_dir():
        return found
    for company_dir in sorted(AMMO_DIR.iterdir()):
        if not company_dir.is_dir():
            continue
        company = company_dir.name.replace("-面试准备", "").strip()
        for sub in sorted(company_dir.rglob("*")):
            if not sub.is_file() or sub.suffix.lower() not in AUDIO_EXT:
                continue
            # 只收录音相关目录下的（避免误收素材音）
            rel_parts = set(sub.relative_to(company_dir).parts)
            if not (rel_parts & set(RECORD_DIR_HINTS)):
                continue
            if any(h in sub.stem.lower() for h in DERIVED_HINTS):
                continue
            date = extract_date(sub.name)
            if not date:
                # 文件名里没日期 → 用文件修改时间兜底
                date = datetime.fromtimestamp(sub.stat().st_mtime).strftime("%Y-%m-%d")
            found.append({
                "company": company,
                "round": infer_round(sub.name),
                "interview_date": date,
                "audio_path": str(sub.relative_to(PROJECT_ROOT)),
                "transcript_path": find_transcript(sub),
            })
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description="面试模块 002：扫描录音资产入库 + 失效检测")
    ap.add_argument("--apply", action="store_true", help="真正写库（默认只演练）")
    ap.add_argument("--force", action="store_true", help="已存在的条目也更新")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"❌ 找不到数据库：{DB_PATH}")
        return 1

    found = scan_audio_files()
    print(f"=== 扫描到 {len(found)} 个音频资产 ===")
    for f in found:
        print(f"  {f['company']:10s} | {f['interview_date']:12s} | {f['round']:8s} | {f['audio_path']}")
        if f["transcript_path"]:
            print(f"  {'':10s}   └ 转写: {f['transcript_path']}")

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    existing = {r["audio_path"] for r in conn.execute(
        "SELECT audio_path FROM interview_recordings"
    )}

    to_insert = [f for f in found if f["audio_path"] not in existing]
    to_update = [f for f in found if f["audio_path"] in existing] if args.force else []
    skipped = [f for f in found if f["audio_path"] in existing and not args.force]

    print(f"\n新增 {len(to_insert)} | 更新 {len(to_update)} | 跳过(已存在) {len(skipped)}")

    # ── 失效检测：audio_path 在磁盘上不存在的孤儿条目 ──
    print("\n=== 失效条目检测（audio_path 已不在磁盘）===")
    orphans = []
    for r in conn.execute("SELECT id, company, round, audio_path, status FROM interview_recordings"):
        p = r["audio_path"] or ""
        if not p:
            orphans.append((r["id"], r["company"], r["round"], "(空路径)", r["status"]))
            continue
        full = p if Path(p).is_absolute() else PROJECT_ROOT / p
        if not full.exists():
            orphans.append((r["id"], r["company"], r["round"], p, r["status"]))
    if orphans:
        for oid, comp, rnd, p, st in orphans:
            print(f"  ⚠️ #{oid} {comp} {rnd} | status={st} | {p}")
    else:
        print("  无")

    if not args.apply:
        conn.close()
        print("\n（演练模式，未写库。加 --apply 落库）")
        return 0

    from datetime import datetime

    now = datetime.now().isoformat(timespec="seconds")
    n = 0
    for f in to_insert:
        conn.execute(
            "INSERT INTO interview_recordings"
            " (company, round, interview_date, audio_path, transcript_path,"
            "  duration_sec, summary, key_points, status, created_at)"
            " VALUES (?,?,?,?,?,0,'','',?,?)",
            (
                f["company"], f["round"], f["interview_date"],
                f["audio_path"], f["transcript_path"],
                "completed" if f["transcript_path"] else "pending",
                now,
            ),
        )
        n += 1
    for f in to_update:
        conn.execute(
            "UPDATE interview_recordings SET round=?, interview_date=?,"
            " transcript_path=?, status=? WHERE audio_path=?",
            (
                f["round"], f["interview_date"], f["transcript_path"],
                "completed" if f["transcript_path"] else "pending",
                f["audio_path"],
            ),
        )
        n += 1
    conn.commit()

    print(f"\n✅ 已写入 {n} 条")
    print("\n=== 校验 ===")
    for r in conn.execute(
        "SELECT id, company, round, interview_date, status, transcript_path"
        " FROM interview_recordings ORDER BY interview_date DESC, id DESC"
    ):
        tp = r["transcript_path"] or "—"
        print(f"  #{r['id']} {r['company']:12s} {str(r['round']):8s} "
              f"{str(r['interview_date']):12s} {r['status']:12s} {tp[:52]}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
