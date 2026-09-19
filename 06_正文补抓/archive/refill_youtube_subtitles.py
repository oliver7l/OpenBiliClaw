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
                                               [--proxy-pool "p1,p2,..."]
limit 默认 100；--dry-run 只统计不写库；--desc 按 id 倒序（新视频优先，
适合"只补最近 90 天"的慢速批量场景，默认升序补老视频）；
--sleep N 每篇间隔秒数（默认 1.5，防 429 限流；慢速场景可设 600）；
--proxy-pool "p1,p2,..." 多出口 IP 池（逗号分隔），每条视频轮询不同出口，
命中 YouTube bot 检测时自动换下一个出口重试。等价于环境变量 YT_PROXY_POOL。
不设则退化为单代理 PROXY（http://127.0.0.1:7890）。
--cookies-from-browser chrome 改用浏览器实时 Cookie（含完整鉴权项），绕过
"YouTube bot" 风控；等价于 YT_COOKIES_FROM_BROWSER。导出的
data/youtube_cookies.txt 若残缺（只剩分区 Cookie），必须走这条才能过风控。
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
YTDLP = shutil.which("yt-dlp") or "/opt/homebrew/bin/yt-dlp"
PROXY = "http://127.0.0.1:7890"  # 本机代理（Clash 等），YouTube 需走代理才能稳定访问
# 登录态 Cookie（Netscape 格式）。YouTube 会周期性判定出口 IP 为 bot，
# 光靠 --cookies-from-browser 会报 "cookies are no longer valid"，
# 因此固定用导出的 cookie 文件：data/youtube_cookies.txt（gitignore，600 权限）。
COOKIE_FILE = os.environ.get("YT_COOKIE_FILE") or os.path.join(
    BASE, "data", "youtube_cookies.txt"
)
# 若设了 YT_COOKIES_FROM_BROWSER（如 chrome），改用浏览器实时 Cookie。
# 浏览器 Cookie 含完整鉴权项（SID/HSID/SAPISID/LOGIN_INFO 等），能稳定绕过
# YouTube 的 "Sign in to confirm you're not a bot" 风控；而导出的
# data/youtube_cookies.txt 往往只剩分区 Cookie（残缺），会被判匿名 bot。
# 注意：--cookies-from-browser 需要对应浏览器（Chrome）当前在运行。
COOKIES_FROM_BROWSER = os.environ.get("YT_COOKIES_FROM_BROWSER") or ""


def _cookie_args() -> list[str]:
    """构造 yt-dlp 的 Cookie 参数：浏览器实时 Cookie 优先，文件兜底。"""
    if COOKIES_FROM_BROWSER:
        return ["--cookies-from-browser", COOKIES_FROM_BROWSER]
    if os.path.exists(COOKIE_FILE):
        return ["--cookies", COOKIE_FILE]
    return []
MAX_ATTEMPTS = 3
TIMEOUT = 180
MIN_BODY = 50
# 字幕语言优先级：中文在前，英文兜底
LANG_PRIORITY = ["zh-Hans", "zh-CN", "zh", "zh-TW", "zh-Hant", "en"]
VID_RE = re.compile(r"(?:v=|youtu\.be/|embed/|shorts/)([A-Za-z0-9_-]{11})")


def get_proxy_pool() -> list[str]:
    """读取代理 IP 池（多出口轮换，防单 IP 被 YouTube 风控）。

    优先级：--proxy-pool 命令行 > 环境变量 YT_PROXY_POOL > 单代理 PROXY 兜底。
    多个出口用逗号分隔，例如：
        YT_PROXY_POOL="socks5://127.0.0.1:7890,socks5://127.0.0.1:7891,http://127.0.0.1:7892"
    若 Clash 已配置 load-balance 组、只想走单个混合端口，则不设该变量即可（退化为单代理）。
    """
    raw = getattr(get_proxy_pool, "_override", "") or os.environ.get("YT_PROXY_POOL", "").strip()
    if raw:
        pool = [p.strip() for p in raw.split(",") if p.strip()]
        if pool:
            return pool
    return [PROXY]


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


def _yt_bot_blocked(stderr: str) -> bool:
    """YouTube 机器人检测（本机 IP/出口被判定为 bot）。

    这是**全局**故障，不是单条视频的属性：一旦出现，本轮后续全部会失败，
    必须熔断停止，且**绝不能**把条目记满重试次数（否则一次性废掉整个队列）。
    """
    return "Sign in to confirm" in stderr or "not a bot" in stderr


def _yt_permanent(stderr: str) -> bool:
    """判断是否属于永久不可抓（视频无字幕/会员/私有/需登录）。

    注意：不含 "Sign in" —— 那会把 "Sign in to confirm you're not a bot"
    误判成"该视频需登录"，导致整批条目被永久跳过（历史上一天废掉 200 条）。
    """
    return any(k in stderr for k in [
        "Subtitles are not available", "no subtitles", "No subtitles",
        "members-only", "Private video", "Video unavailable", "not available",
    ])


def fetch_youtube_subtitle(url: str, proxy: str | None = None) -> tuple[str, bool, bool]:
    """返回 (正文, 是否应重试, 是否被 bot 检测熔断)。

    - 拿到 >= MIN_BODY 的正文 → (正文, 任意, False) 视为成功；
    - 视频无字幕/不可抓 → ("", False, False) 永久跳过；
    - 限流/网络失败 → ("", True, False) 下轮重试；
    - 命中 bot 检测 → ("", True, True) 熔断（调用方应换下一个出口或停止本轮）。

    proxy: 本次调用使用的出口（来自 IP 池）；为空则用默认 PROXY。
    """
    proxy = proxy or PROXY
    vid = extract_vid(url)
    if not vid:
        return "", False, False  # url 不合法，永久无法处理
    tmp = tempfile.mkdtemp(prefix="yt_subs_")
    env = os.environ.copy()
    env["HTTP_PROXY"] = proxy
    env["HTTPS_PROXY"] = proxy
    try:
        for attempt in range(3):
            try:
                r = subprocess.run(
                    [YTDLP, "--proxy", proxy]
                    + _cookie_args()
                    # 人工字幕与自动字幕都抓：只写 --write-auto-subs 会漏掉
                    # 只有人工字幕的视频。
                    # 顺带写 description：YouTube 自动字幕现在要 PO token，
                    # 拿不到字幕时用视频简介兜底（比整条空着强）。
                    + ["--skip-download", "--write-subs", "--write-auto-subs",
                     "--write-description",
                     "--sub-langs", ",".join(LANG_PRIORITY),
                     "--sub-format", "vtt", "--no-progress", "--no-warnings",
                     "--output", f"{tmp}/%(id)s", url],
                    capture_output=True, text=True, timeout=TIMEOUT, env=env,
                )
                stderr = r.stderr or ""
                if r.returncode != 0:
                    if _yt_bot_blocked(stderr):
                        return "", True, True  # 全局熔断
                    if _yt_permanent(stderr):
                        return "", False, False
                    if _yt_retryable(stderr):
                        time.sleep(8 * (attempt + 1))  # 指数退避
                        continue
                    # 未知错误：退避一次再试
                    time.sleep(5)
                    continue
                files = glob.glob(os.path.join(tmp, "*.vtt"))
                if not files:
                    # 无字幕：用视频简介兜底（YouTube 自动字幕需 PO token，命中率有限）
                    desc_files = glob.glob(os.path.join(tmp, "*.description"))
                    for df in desc_files:
                        try:
                            with open(df, encoding="utf-8", errors="ignore") as fh:
                                desc = fh.read().strip()
                        except Exception:  # noqa: BLE001
                            continue
                        if len(desc) >= MIN_BODY:
                            return f"【视频简介】{desc}", True, False
                    return "", False, False  # 无字幕也无简介 → 永久跳过
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
                    return body, True, False
                return "", False, False  # 字幕过短，视为无
            except subprocess.TimeoutExpired:
                time.sleep(8 * (attempt + 1))
                continue
        return "", True, False  # 重试耗尽 → 下轮再试
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("limit", type=int, nargs="?", default=100, help="最多处理 N 篇")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写库")
    ap.add_argument("--desc", action="store_true", help="按 id 倒序，新视频优先")
    ap.add_argument("--sleep", type=float, default=1.5, help="每篇间隔秒数")
    ap.add_argument("--proxy-pool", default=None,
                    help="多出口 IP 池（逗号分隔），轮换防封；等价于 YT_PROXY_POOL")
    ap.add_argument("--cookies-from-browser", default=None,
                    help="用浏览器实时 Cookie（如 chrome）绕过 YouTube bot 风控；"
                         "等价于 YT_COOKIES_FROM_BROWSER")
    args = ap.parse_args()
    limit, dry, desc, sleep_s = args.limit, args.dry_run, args.desc, args.sleep
    if args.proxy_pool:
        get_proxy_pool._override = args.proxy_pool  # type: ignore[attr-defined]
    if args.cookies_from_browser:
        globals()["COOKIES_FROM_BROWSER"] = args.cookies_from_browser

    db = sqlite3.connect(DB)
    # v0.4.0+: articles 表迁移到 content.db，ATTACH 以便跨库查询
    try:
        from pathlib import Path as _Path
        _content_db = _Path(__file__).parent.parent / "data" / "content.db"
        if _content_db.exists():
            db.execute("ATTACH DATABASE ? AS content", (str(_content_db),))
    except Exception:
        pass


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

    pool = get_proxy_pool()
    if len(pool) <= 6:
        pool_repr = ", ".join(pool)
    else:
        pool_repr = ", ".join(pool[:3]) + f" ... (共 {len(pool)} 个)"
    print(f"[proxy-pool] 启用 {len(pool)} 个出口 IP 轮换: {pool_repr}", flush=True)
    max_rot = min(len(pool), 3)  # 单条约最多尝试几个不同出口（bot 检测时换出口重试）
    ok = fail = skipped = 0
    for idx, (aid, url) in enumerate(rows, 1):
        body = retry = bot = False
        used_proxy = pool[0]
        # 以 idx 锚定轮询：相邻视频分散到不同出口，避免单 IP 高频命中风控。
        for ti in range(max_rot):
            proxy = pool[(idx + ti) % len(pool)]
            used_proxy = proxy
            b, r, bt = fetch_youtube_subtitle(url, proxy)
            if bt:
                # 该出口被 YouTube 判为 bot：换下一个出口再试，不立即熔断。
                continue
            # 非 bot 的判定（成功 / 永久无字幕 / 可重试故障）即采纳该出口结果。
            body, retry, bot = b, r, bt
            break
        else:
            # 所有尝试的出口都返回 bot 检测 → 视为整段出口被封，熔断本轮。
            bot = True
        if bot:
            # 全局被判定为 bot：立刻熔断，不消耗任何条目的重试次数。
            print(
                f"[ABORT] {idx}/{len(rows)} 命中 YouTube bot 检测"
                f"（已试 {max_rot} 个出口均失败），本轮停止（未消耗重试次数）。"
                f"请切换 Clash 节点 / 接入住宅代理 / 修复 Cookie。",
                flush=True,
            )
            break
        attempts = db.execute(
            "SELECT COALESCE(body_fetch_attempts, 0) FROM articles WHERE id = ?", (aid,)
        ).fetchone()[0] + 1
        if body:
            db.execute(
                "UPDATE articles SET content_text = ?, body_fetch_attempts = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (body[:20000], attempts, aid),
            )
            print(f"[{idx}/{len(rows)}] OK   id={aid} proxy={used_proxy} len={len(body)}", flush=True)
            ok += 1
        elif retry:
            db.execute(
                "UPDATE articles SET body_fetch_attempts = ? WHERE id = ?", (attempts, aid)
            )
            print(f"[{idx}/{len(rows)}] RETRY id={aid} proxy={used_proxy} 限流/失败，保留重试", flush=True)
            fail += 1
        else:
            db.execute(
                "UPDATE articles SET body_fetch_attempts = ? WHERE id = ?",
                (MAX_ATTEMPTS, aid),
            )
            print(f"[{idx}/{len(rows)}] SKIP id={aid} proxy={used_proxy} 无字幕/不可抓", flush=True)
            skipped += 1
        db.commit()
        time.sleep(sleep_s)  # 每篇间隔，防 429 限流（--sleep 可调）
    print(
        f"DONE total={len(rows)} ok={ok} retry={fail} skipped={skipped}",
        flush=True,
    )


if __name__ == "__main__":
    main()
