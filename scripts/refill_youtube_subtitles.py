#!/usr/bin/env python3
"""YouTube 字幕补抓：用 yt-dlp 抓字幕/自动字幕，拼成正文写回 articles。

扫描 articles 中 source_type='youtube' 且 content_text 为空、重试未超限的行，
用本机 yt-dlp 抓字幕（优先中文，其次英文），解析 vtt 拼接为纯文本正文写回。

设计要点（与 refill_article_bodies.py 一致）：
- content_text 为空即"待补"隐形队列；新进 RSS 文章若无正文会被下一轮自动补上。
- body_fetch_attempts 记重试次数，>= MAX_ATTEMPTS 不再尝试，避免对永久抓不到的
  视频反复无效请求。
- 区分三类结果：
  * 调用失败（YouTube 429 限流 / 超时 / 网络）→ 必须重试，不记满次数；
  * 确认无字幕（视频本身无字幕 / 会员私有 / 需登录）→ 永久跳过，记满次数；
  * 成功拿到足够正文 → 写回。
- YouTube 对匿名抓取易 429，脚本内置指数退避与全局限速。
- 绝不覆盖已有正文；写入截断到 20000 字符。只 UPDATE，不删行。

用法:
    python3 scripts/refill_youtube_subtitles.py [limit] [--dry-run] [--desc] [--sleep N]
limit 默认 100；--dry-run 只统计不写库；--desc 按 id 倒序（新视频优先，
适合"只补最近 90 天"的慢速批量场景，默认升序补老视频）；
--sleep N 每篇间隔秒数（默认 1.5，防 429 限流；慢速场景可设 600）。
"""
import glob
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "data", "openbiliclaw.db")
YTDLP = "/opt/homebrew/bin/yt-dlp"
PROXY = "http://127.0.0.1:7890"  # 本机代理（Clash 等），YouTube 需走代理才能稳定访问
MAX_ATTEMPTS = 3
TIMEOUT = 180
MIN_BODY = 50
# 字幕语言优先级：中文在前，英文兜底
LANG_PRIORITY = ["zh-Hans", "zh-CN", "zh", "zh-TW", "zh-Hant", "en"]
VID_RE = re.compile(r"(?:v=|youtu\.be/|embed/|shorts/)([A-Za-z0-9_-]{11})")


def extract_vid(url: str) -> str | None:
    m = VID_RE.search(url or "")
    return m.group(1) if m else None


def parse_vtt(path: str) -> str:
    """解析 vtt 字幕为纯文本：去 WEBVTT 头、时间戳行、序号、行内标签、连续重复。"""
    out = []
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("WEBVTT") or "-->" in s or s.isdigit():
                continue
            s = re.sub(r"<[^>]+>", "", s)  # 去 <00:00:00.000> 等行内时间戳
            if s:
                out.append(s)
    dedup = []
    for l in out:
        if not dedup or l != dedup[-1]:
            dedup.append(l)
    return "\n".join(dedup)


def _yt_retryable(stderr: str) -> bool:
    """判断 yt-dlp 报错是否属于可重试的临时故障（限流/网络）。"""
    return any(k in stderr for k in [
        "429", "Too Many Requests", "rate", "Rate",
        "timed out", "Temporary", "Connection", "Internet", "reset",
    ])


def _yt_permanent(stderr: str) -> bool:
    """判断是否属于永久不可抓（视频无字幕/会员/私有/需登录）。"""
    return any(k in stderr for k in [
        "Subtitles are not available", "no subtitles", "No subtitles",
        "Sign in", "members", "Private", "unavailable", "not available",
    ])


def fetch_youtube_subtitle(url: str) -> tuple[str, bool]:
    """返回 (正文, 是否应重试)。

    - 拿到 >= MIN_BODY 的正文 → (正文, 任意) 视为成功；
    - 视频无字幕/不可抓 → ("", False) 永久跳过；
    - 限流/网络失败 → ("", True) 下轮重试。
    """
    vid = extract_vid(url)
    if not vid:
        return "", False  # url 不合法，永久无法处理
    tmp = tempfile.mkdtemp(prefix="yt_subs_")
    env = os.environ.copy()
    env["HTTP_PROXY"] = PROXY
    env["HTTPS_PROXY"] = PROXY
    try:
        for attempt in range(3):
            try:
                r = subprocess.run(
                    [YTDLP, "--proxy", PROXY, "--skip-download", "--write-auto-subs",
                     "--sub-langs", ",".join(LANG_PRIORITY),
                     "--sub-format", "vtt", "--no-progress", "--no-warnings",
                     "--output", f"{tmp}/%(id)s", url],
                    capture_output=True, text=True, timeout=TIMEOUT, env=env,
                )
                stderr = r.stderr or ""
                if r.returncode != 0:
                    if _yt_permanent(stderr):
                        return "", False
                    if _yt_retryable(stderr):
                        time.sleep(8 * (attempt + 1))  # 指数退避
                        continue
                    # 未知错误：退避一次再试
                    time.sleep(5)
                    continue
                files = glob.glob(os.path.join(tmp, "*.vtt"))
                if not files:
                    return "", False  # 无字幕文件 → 永久跳过
                chosen = None
                for lang in LANG_PRIORITY:
                    for f in files:
                        if f".{lang}.vtt" in f:
                            chosen = f
                            break
                    if chosen:
                        break
                if not chosen:  # 语言不在优先级列表，取最大的
                    chosen = max(files, key=os.path.getsize)
                body = parse_vtt(chosen)
                if len(body) >= MIN_BODY:
                    return body, True
                return "", False  # 字幕过短，视为无
            except subprocess.TimeoutExpired:
                time.sleep(8 * (attempt + 1))
                continue
        return "", True  # 重试耗尽 → 下轮再试
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("limit", type=int, nargs="?", default=100, help="最多处理 N 篇")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写库")
    ap.add_argument("--desc", action="store_true", help="按 id 倒序，新视频优先")
    ap.add_argument("--sleep", type=float, default=1.5, help="每篇间隔秒数")
    args = ap.parse_args()
    limit, dry, desc, sleep_s = args.limit, args.dry_run, args.desc, args.sleep

    db = sqlite3.connect(DB)
    db.execute("PRAGMA busy_timeout=15000")
    order = "ORDER BY id DESC" if desc else "ORDER BY id"
    rows = db.execute(
        f"""SELECT id, url FROM articles
           WHERE source_type = 'youtube'
             AND (content_text IS NULL OR content_text = '')
             AND url IS NOT NULL AND url <> ''
             AND COALESCE(body_fetch_attempts, 0) < ?
           {order} LIMIT ?""",
        (MAX_ATTEMPTS, limit),
    ).fetchall()

    if dry:
        print(f"[dry-run] youtube 待补候选: {len(rows)}")
        return

    ok = fail = skipped = 0
    for idx, (aid, url) in enumerate(rows, 1):
        body, retry = fetch_youtube_subtitle(url)
        attempts = db.execute(
            "SELECT COALESCE(body_fetch_attempts, 0) FROM articles WHERE id = ?", (aid,)
        ).fetchone()[0] + 1
        if body:
            db.execute(
                "UPDATE articles SET content_text = ?, body_fetch_attempts = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (body[:20000], attempts, aid),
            )
            print(f"[{idx}/{len(rows)}] OK   id={aid} len={len(body)}", flush=True)
            ok += 1
        elif retry:
            db.execute(
                "UPDATE articles SET body_fetch_attempts = ? WHERE id = ?", (attempts, aid)
            )
            print(f"[{idx}/{len(rows)}] RETRY id={aid} 限流/失败，保留重试", flush=True)
            fail += 1
        else:
            db.execute(
                "UPDATE articles SET body_fetch_attempts = ? WHERE id = ?",
                (MAX_ATTEMPTS, aid),
            )
            print(f"[{idx}/{len(rows)}] SKIP id={aid} 无字幕/不可抓", flush=True)
            skipped += 1
        db.commit()
        time.sleep(sleep_s)  # 每篇间隔，防 429 限流（--sleep 可调）
    print(
        f"DONE total={len(rows)} ok={ok} retry={fail} skipped={skipped}",
        flush=True,
    )


if __name__ == "__main__":
    main()
