#!/usr/bin/env python3
"""阅读库正文统一补抓（v2：覆盖 zhihu / xiaohongshu / youtube / bilibili / 其他）。

扫描 articles 中 content_text 为空、url 非空、重试未超限的行，按 source_type
分通道抓取正文写回 content_text：

- **zhihu**：`zhihu answer <id> --json` / `zhihu article <id> --json`（本机已登录
  pyzhihu-cli；URL 含 answer 走 answer，zhuanlan/p 走 article，question 取标题）。
- **xiaohongshu**：`xhs read <url> --xsec-token <token> --json`（URL 必须带
  xsec_token，否则跳过；正文取 data.note_desc / desc）。
- **youtube**：`yt-dlp --write-subs` 抓字幕（优先中文，走本机代理），解析 vtt。
- **bilibili**：`bili video <BV> -s --ai --json`，优先字幕口播稿，AI 总结+简介兜底。
- **其他**：`autocli read <url>`（Readability）。

设计要点（与 refill_article_bodies.py 一致）：
- content_text 为空即"待补"隐形队列；body_fetch_attempts 记录重试次数，
  >= MAX_ATTEMPTS 的行不再尝试。
- 区分「调用失败→保留重试」与「确认无正文→记满次数跳过」。
- 绝不覆盖已有正文；写入截断 20000 字符；只 UPDATE 不删行。

用法:
    python3 scripts/refill_library_bodies_v2.py [limit] [--source=zhihu]
"""
import json
import os
import re
import shutil
import sqlite3
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlparse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "data", "openbiliclaw.db")
AUTOCLI = shutil.which("autocli") or "/Users/imac/bin/autocli"
BILI_CLI = shutil.which("bili") or "/Users/imac/.local/bin/bili"
ZHIHU_CLI = shutil.which("zhihu") or "/opt/homebrew/bin/zhihu"
# 知乎正文走直连接口（zhihu CLI 新版已无 answer/article 子命令）
ZHIHU_PY = "/Users/imac/.local/share/uv/tools/zhihu-toolkit/bin/python3"
ZHIHU_HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zhihu_api_body.py")
XHS_CLI = shutil.which("xhs") or "/Users/imac/.local/bin/xhs"
YTDLP = shutil.which("yt-dlp") or "/opt/homebrew/bin/yt-dlp"
YT_PROXY = "http://127.0.0.1:7890"
# 登录态 Cookie（Netscape）：data/youtube_cookies.txt，见 refill_youtube_subtitles.py 同名常量
YT_COOKIE_FILE = os.path.join(BASE, "data", "youtube_cookies.txt")
MAX_ATTEMPTS = 3
TIMEOUT = 150
MIN_BODY = 50
BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
ZH_ANSWER_RE = re.compile(r"zhihu\.com/(?:question/\d+/)?answer/(\d+)")
ZH_ARTICLE_RE = re.compile(r"zhuanlan\.zhihu\.com/p/(\d+)")
ZH_QUESTION_RE = re.compile(r"zhihu\.com/question/(\d+)")
YT_VID_RE = re.compile(r"(?:v=|youtu\.be/|embed/|shorts/)([A-Za-z0-9_-]{11})")
YT_LANG_PRIORITY = ["zh-Hans", "zh-CN", "zh", "zh-TW", "zh-Hant", "en"]


def _run(cmd: list[str], timeout: int = TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _run_clean(cmd: list[str], timeout: int = TIMEOUT) -> subprocess.CompletedProcess:
    """去掉 PYTHONPATH 后运行子进程。

    在某些宿主环境（如带 sitecustomize shim 的沙箱）下，PYTHONPATH 会注入一个
    sitecustomize，它拦截 ``Path.mkdir`` 且**不认 exist_ok=True**，导致
    zhihu_cli 初始化缓存目录时抛 ``PermissionError: EEXIST``（目录已存在时）。
    清掉 PYTHONPATH 即可正常导入。
    """
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)


def fetch_autocli(url: str) -> str:
    for _ in range(2):
        try:
            r = _run([AUTOCLI, "read", url])
            out = (r.stdout or "").strip()
            if len(out) > MIN_BODY:
                return out
        except Exception:
            pass
        time.sleep(2)
    return ""


def fetch_bilibili_body(url: str) -> tuple[str, bool]:
    """返回 (正文, 调用是否成功)。"""
    m = BV_RE.search(url or "")
    if not m:
        return "", True
    try:
        r = _run([BILI_CLI, "video", m.group(1), "-s", "--ai", "--json"])
        d = json.loads(r.stdout or "{}").get("data") or {}
    except Exception:
        return "", False
    if not d:
        return "", False
    st = d.get("subtitle") or {}
    if st.get("available") and (st.get("text") or "").strip():
        return (st.get("text") or "").strip(), True
    ai = d.get("ai_summary") or ""
    desc = ((d.get("video") or {}).get("description") or "").strip()
    parts = [p.strip() for p in (ai, desc) if isinstance(p, str) and p.strip()]
    combined = "\n\n".join(parts)
    return (combined if len(combined) >= MIN_BODY else ""), True


def _html_to_text(html: str) -> str:
    """知乎正文是 HTML：换行标签转 \\n、去其余标签、反转义。"""
    import html as _html

    s = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    s = re.sub(r"</(p|div|h\d|li|blockquote)>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", "", s)
    return _html.unescape(s)


def fetch_zhihu_body(url: str) -> tuple[str, bool]:
    """知乎正文：answer / article / question 标题。

    v2.1: zhihu CLI 已移除 `answer`/`article` 子命令（现在只有 download/browse），
    改为调用 scripts/zhihu_api_body.py —— 复用 CLI 登录态直连 api.zhihu.com，
    直接拿 content 并转 Markdown，比 CLI 更快也更稳。
    """
    m = ZH_ANSWER_RE.search(url or "")
    kind, oid = ("answer", m.group(1)) if m else ("", "")
    if not oid:
        m = ZH_ARTICLE_RE.search(url or "")
        kind, oid = ("article", m.group(1)) if m else ("", "")
    if oid:
        try:
            r = _run_clean([ZHIHU_PY, ZHIHU_HELPER, kind, oid], timeout=60)
            if r.returncode != 0 and not (r.stdout or "").strip():
                return "", False  # 调用失败 → 保留重试
            text = (r.stdout or "").strip()
            if len(text) > MIN_BODY:
                return text, True
            return "", True  # 调用成功但确实无正文
        except Exception:
            return "", False
    m = ZH_QUESTION_RE.search(url or "")
    if m:
        # 问题页无正文，返回问题标题作为最小可用正文
        try:
            r = _run([ZHIHU_CLI, "question", m.group(1), "--json"])
            d = json.loads(r.stdout or "{}")
            question = (d.get("data") or {}).get("question") or {}
            title = question.get("title") or ""
            if isinstance(title, str) and title.strip():
                return f"【知乎问题】{title.strip()}", True
            return "", True
        except Exception:
            return "", False
    return "", True  # 无法识别 URL 形态，视为永久跳过


def fetch_xhs_body(url: str) -> tuple[str, bool]:
    """小红书正文：URL 必须带 xsec_token。

    注意语义（v2.1）：小红书风控（CAPTCHA / noteDetailMap 为空）是**临时**故障，
    绝不能判成"确认无正文"把重试次数记满——那样会永久废掉 4 万条队列。
    只有真正解析到笔记、而 desc 确实为空时才算"确认无正文"。
    """
    parsed = urlparse(url or "")
    params = parse_qs(parsed.query)
    token = (params.get("xsec_token") or [""])[0]
    if not token:
        return "", True  # 无 token：main() 里单列，不消耗重试次数
    try:
        r = _run([XHS_CLI, "read", url, "--xsec-token", token, "--json"])
        d = json.loads(r.stdout or "{}")
    except Exception:
        return "", False
    if not d.get("ok", True):
        return "", False  # api_error / captcha → 临时故障，保留重试
    data = d.get("data") or d
    desc = (data.get("note_desc") or data.get("desc") or "").strip()
    if isinstance(desc, str) and len(desc) > MIN_BODY:
        return desc, True
    return "", True


def _yt_run(url: str) -> str:
    """yt-dlp 抓字幕到临时目录，返回解析后的纯文本。"""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        try:
            _run(
                # --cookies-from-browser chrome: 过 YouTube bot 检测
                # ("Sign in to confirm you're not a bot")；--remote-components
                # ejs:github + 本机 deno: 解 JS challenge（n challenge）。
                [YTDLP, "--proxy", YT_PROXY]
                + (["--cookies", YT_COOKIE_FILE] if os.path.exists(YT_COOKIE_FILE) else
                   ["--cookies-from-browser", "chrome", "--remote-components", "ejs:github"])
                + ["--skip-download", "--write-subs",
                 "--sub-langs", "all", "--sub-format", "vtt",
                 "-o", os.path.join(td, "sub.%(ext)s"), url],
                timeout=TIMEOUT,
            )
        except Exception:
            return ""
        best = ""
        for lang in YT_LANG_PRIORITY:
            for ext in ("vtt", "en.vtt"):
                p = os.path.join(td, f"sub.{lang}.{ext}")
                if os.path.exists(p):
                    best = p
                    break
            if best:
                break
        if not best:
            import glob
            vtts = sorted(glob.glob(os.path.join(td, "*.vtt")))
            best = vtts[0] if vtts else ""
        if not best:
            return ""
        out = []
        try:
            with open(best, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    s = line.strip()
                    if not s or s.startswith("WEBVTT") or "-->" in s or s.isdigit():
                        continue
                    s = re.sub(r"<[^>]+>", "", s)
                    if s:
                        out.append(s)
        except Exception:
            return ""
        # 去连续重复行
        dedup = []
        for s in out:
            if not dedup or dedup[-1] != s:
                dedup.append(s)
        text = "\n".join(dedup).strip()
        return text if len(text) >= MIN_BODY else ""


def fetch_youtube_body(url: str) -> tuple[str, bool]:
    m = YT_VID_RE.search(url or "")
    if not m:
        return "", True
    text = _yt_run(url)
    if text:
        return text, True
    return "", False  # 抓取失败/无字幕 → 重试


def main() -> None:
    limit, source = 100, None
    for arg in sys.argv[1:]:
        if arg.startswith("--source="):
            source = arg.split("=", 1)[1].strip()
        elif arg.isdigit():
            limit = int(arg)

    db = sqlite3.connect(DB)
    # v0.4.0+: articles 表迁移到 content.db，ATTACH 以便跨库查询
    _content_db = Path(__file__).resolve().parent.parent / "data" / "content.db"
    if _content_db.exists():
        db.execute("ATTACH DATABASE ? AS content", (str(_content_db),))

    db.execute("PRAGMA busy_timeout=15000")
    sql = """SELECT id, url, source_type FROM articles
           WHERE (content_text IS NULL OR content_text = '')
             AND url IS NOT NULL AND url <> ''
             AND COALESCE(body_fetch_attempts, 0) < ?"""
    params: list = [MAX_ATTEMPTS]
    if source:
        sql += " AND source_type = ?"
        params.append(source)
    sql += " ORDER BY id LIMIT ?"
    params.append(limit)
    rows = db.execute(sql, params).fetchall()

    ok = fail = skipped = no_token = 0
    for idx, (aid, url, src) in enumerate(rows, 1):
        src = (src or "").strip().lower()
        # 小红书裸链（无 xsec_token）本通道无法处理：不消耗重试次数，
        # 留给 token 回填任务 / 得到大脑通道。
        if src == "xiaohongshu" and "xsec_token=" not in (url or ""):
            print(f"[{idx}/{len(rows)}] NOTOKEN id={aid} {src} 无 token，留待其它通道", flush=True)
            no_token += 1
            continue
        if src == "bilibili":
            body, call_ok = fetch_bilibili_body(url)
        elif src == "zhihu":
            body, call_ok = fetch_zhihu_body(url)
        elif src == "xiaohongshu":
            body, call_ok = fetch_xhs_body(url)
        elif src == "youtube":
            body, call_ok = fetch_youtube_body(url)
        else:
            body, call_ok = fetch_autocli(url), True
        attempts = db.execute(
            "SELECT COALESCE(body_fetch_attempts, 0) FROM articles WHERE id = ?", (aid,)
        ).fetchone()[0] + 1
        if body:
            db.execute(
                "UPDATE articles SET content_text = ?, body_fetch_attempts = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (body[:20000], attempts, aid),
            )
            print(f"[{idx}/{len(rows)}] OK   id={aid} {src} len={len(body)}", flush=True)
            ok += 1
        elif call_ok:
            db.execute(
                "UPDATE articles SET body_fetch_attempts = ? WHERE id = ?",
                (MAX_ATTEMPTS, aid),
            )
            print(f"[{idx}/{len(rows)}] SKIP id={aid} {src} 确认无正文", flush=True)
            skipped += 1
        else:
            db.execute(
                "UPDATE articles SET body_fetch_attempts = ? WHERE id = ?", (attempts, aid)
            )
            print(f"[{idx}/{len(rows)}] RETRY id={aid} {src} 调用失败，保留重试", flush=True)
            fail += 1
        db.commit()
        time.sleep(1)
    print(
        f"DONE total={len(rows)} ok={ok} fail={fail} skipped={skipped} no_token={no_token}"
        + (f" source={source}" if source else ""),
        flush=True,
    )


if __name__ == "__main__":
    main()
