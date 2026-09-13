#!/usr/bin/env python3
"""用得到大脑（Get笔记）的服务端抓取能力给阅读库补正文。

适用场景：本机通道抓不到的条目（小红书裸链/风控、抖音、YouTube 被 bot 检测等）。
把 URL 交给得到大脑保存，由**服务端**去抓正文，再读回来写进 articles.content_text。

配额（getnote quota）：write_note 1000/天、read 20000/天，每天 00:00 重置。
本脚本遇到配额耗尽会立刻停止，不浪费调用。

流程（幂等，可重复运行）：
  1) submit : 选 N 条待补条目 → `getnote save <url>` → 记 task_id
  2) poll   : `getnote task <task_id>` 轮询 → 拿 note_id
  3) collect: `getnote note <note_id> --field content` → 写回 articles.content_text

用法:
    python3 scripts/refill_via_getnote.py                    # 跑完整一轮（默认 900 条）
    python3 scripts/refill_via_getnote.py --limit 50 --source xiaohongshu
    python3 scripts/refill_via_getnote.py --collect-only     # 只回收已完成的任务
    python3 scripts/refill_via_getnote.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DB_MAIN = BASE / "data" / "openbiliclaw.db"
DB_CONTENT = BASE / "data" / "content.db"
GETNOTE = "getnote"
MIN_BODY = 30
MAX_BODY = 20000

# 本机通道抓不动、优先交给得到大脑的源。
# 注意：youtube / bilibili / zhihu 本机通道已可用，不要占用宝贵的 write_note 配额。
PRIORITY_SOURCES = ["xiaohongshu", "douyin", "wechat", "xiaoyuzhou"]


def _run(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        # 服务端偶发慢到超过 timeout（并发/排队时更明显）。旧版这里会直接抛异常
        # 把整个进程打死，改成返回 124 让调用方按「可重试」处理。
        return subprocess.CompletedProcess(args, 124, "", f"timeout after {timeout}s")


def _json_of(proc: subprocess.CompletedProcess) -> dict:
    """解析 CLI 输出；table 格式或报错时返回 {}。

    注意 getnote CLI 报错时会输出形如
    ``Error: rate limited after 3 retries: {...}\n{...}``（JSON 出现两次），
    必须用 raw_decode 只取第一段，否则 json.loads 会因 extra data 失败。
    """
    out = (proc.stdout or "").strip()
    if not out:
        return {}
    try:
        return json.loads(out)
    except Exception:  # noqa: BLE001
        start = out.find("{")
        if start >= 0:
            try:
                obj, _end = json.JSONDecoder().raw_decode(out[start:])
                if isinstance(obj, dict):
                    return obj
            except Exception:  # noqa: BLE001
                return {}
    return {}


def _rate_limit_of(payload: dict) -> dict:
    """从 quota/错误响应里取出 rate_limit 明细。"""
    err = payload.get("error") or {}
    rl = err.get("rate_limit") or {}
    if not rl:
        data = payload.get("data")
        if isinstance(data, dict):
            rl = data.get("rate_limit") or {}
            # getnote quota 直接把 read/write/write_note 三个桶放在 data 下，
            # 没有 "rate_limit" 这一层（旧 CLI 才有），这里兼容新格式。
            if not rl and any(k in data for k in ("read", "write", "write_note")):
                rl = data
    return rl if isinstance(rl, dict) else {}


def quota_report() -> tuple[bool, str]:
    """检查得到大脑配额。返回 (是否可用, 说明文字)。

    得到大脑的网关是**按最紧的那个桶**放行的：只要 read 桶打满，
    整个 API（包括 save / write_note）都会返回 10203 quota_daily_exceeded。
    所以必须先看 read，而不是只看 write_note。
    """
    proc = _run([GETNOTE, "quota", "-o", "json"], timeout=60)
    payload = _json_of(proc)
    rl = _rate_limit_of(payload)
    if not rl:
        return True, "（未能读取配额明细，继续尝试）"
    parts, blocked = [], False
    for bucket in ("read", "write", "write_note"):
        daily = (rl.get(bucket) or {}).get("daily") or {}
        limit, used = daily.get("limit"), daily.get("used")
        if limit is None:
            continue
        remaining = max(0, int(limit) - int(used or 0))
        parts.append(f"{bucket} {used}/{limit}")
        if remaining <= 0:
            blocked = True
    detail = "，".join(parts) if parts else "（无明细）"
    if blocked:
        return False, f"配额已打满（{detail}）——网关会拒绝所有调用，稍后/次日再试"
    return True, detail


def _quota_exhausted(payload: dict, raw: str) -> bool:
    if "quota_daily_exceeded" in raw or "请求配额已用尽" in raw:
        return True
    err = (payload or {}).get("error") or {}
    return err.get("reason") == "quota_daily_exceeded" or err.get("code") == 10203


def _dig(payload: dict, *keys: str):
    """在 data.note / data / 顶层里找第一个非空字段。

    新版 getnote CLI 的 save 返回 ``data.note.{id,note_id,content,...}``，
    比旧版的 ``data.task_id`` 多嵌套一层，必须递归到 note 里才取得到。
    """
    data = payload.get("data") or {}
    note = data.get("note") if isinstance(data, dict) else None
    for scope in (note if isinstance(note, dict) else {}, data, payload):
        if not isinstance(scope, dict):
            continue
        for k in keys:
            v = scope.get(k)
            if v not in (None, "", 0):
                return v
    return None


def body_of_payload(payload: dict) -> str:
    """从 getnote 响应里挑出最合适的正文。

    新版 getnote 的 `save` 是**同步**的：一次调用就把服务端抓好的正文带回来，
    放在 ``data.note.web_page.content``（原始正文，保真）与
    ``data.note.content``（AI 提炼稿，通常更长）。原始正文够长就用原始的，
    否则退到最长的那份。
    """
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return ""
    note = data.get("note") if isinstance(data.get("note"), dict) else {}
    web = note.get("web_page") if isinstance(note.get("web_page"), dict) else {}
    if not web and isinstance(data.get("web_page"), dict):
        web = data["web_page"]
    cands: list[str] = []
    for c in (web.get("content"), note.get("content"), note.get("ref_content"), data.get("content")):
        if isinstance(c, str) and c.strip():
            cands.append(c.strip())
    if not cands:
        return ""
    raw = cands[0]
    return raw if len(raw) >= MIN_BODY else max(cands, key=len)


class Store:
    def __init__(self, dry: bool = False, shard: int = 0, shard_total: int = 1) -> None:
        self.db = sqlite3.connect(str(DB_MAIN))
        if DB_CONTENT.exists():
            self.db.execute("ATTACH DATABASE ? AS content", (str(DB_CONTENT),))
        self.db.execute("PRAGMA busy_timeout=30000")
        self.dry = dry
        self.shard = shard
        self.shard_total = shard_total
        self._init()

    def _init(self) -> None:
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS getnote_body_task (
                url TEXT PRIMARY KEY,
                article_id INTEGER,
                source_type TEXT DEFAULT '',
                task_id TEXT DEFAULT '',
                note_id TEXT DEFAULT '',
                status TEXT DEFAULT 'submitted',
                error TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        self.db.commit()

    def candidates(self, limit: int, source: str | None) -> list[tuple[int, str, str]]:
        sql = """SELECT a.id, a.url, a.source_type FROM articles a
                 WHERE (a.content_text IS NULL OR a.content_text = '')
                   AND a.url IS NOT NULL AND a.url <> ''
                   AND a.url NOT LIKE 'getnote://%'
                   AND a.url NOT IN (SELECT url FROM getnote_body_task)"""
        params: list = []
        if source:
            sql += " AND a.source_type = ?"
            params.append(source)
        if self.shard and self.shard_total > 1:
            # 多进程并行：按 id 取模分片，保证各 shard 候选集互不相交，
            # 不会重复提交同一条 URL（重复提交会白白吃掉 write_note 配额）。
            sql += " AND a.id % ? = ?"
            params += [self.shard_total, self.shard]
        # 优先处理本机抓不动的源，其次按 id 倒序（新内容优先）
        sql += """ ORDER BY CASE a.source_type
                     WHEN 'xiaohongshu' THEN 0
                     WHEN 'douyin' THEN 1
                     WHEN 'youtube' THEN 2
                     WHEN 'bilibili' THEN 3
                     WHEN 'zhihu' THEN 4
                     ELSE 5 END, a.id DESC LIMIT ?"""
        params.append(limit)
        return self.db.execute(sql, params).fetchall()

    def add_task(self, url: str, article_id: int, source: str, task_id: str, note_id: str = "",
                 status: str = "submitted", error: str = "") -> None:
        if self.dry:
            return
        self.db.execute(
            """INSERT INTO getnote_body_task(url, article_id, source_type, task_id, note_id, status, error)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(url) DO UPDATE SET
                 task_id=excluded.task_id, note_id=excluded.note_id,
                 status=excluded.status, error=excluded.error,
                 updated_at=CURRENT_TIMESTAMP""",
            (url, article_id, source, task_id, note_id, status, error),
        )
        self.db.commit()

    def pending(self, status: str, limit: int = 2000) -> list[tuple[str, str, int]]:
        return self.db.execute(
            "SELECT url, task_id, article_id FROM getnote_body_task WHERE status = ? LIMIT ?",
            (status, limit),
        ).fetchall()

    def set_status(self, url: str, status: str, note_id: str = "", error: str = "") -> None:
        if self.dry:
            return
        self.db.execute(
            "UPDATE getnote_body_task SET status=?, note_id=?, error=?, updated_at=CURRENT_TIMESTAMP WHERE url=?",
            (status, note_id, error, url),
        )
        self.db.commit()

    def write_body(self, article_id: int, body: str) -> None:
        if self.dry:
            return
        self.db.execute(
            "UPDATE articles SET content_text=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (body[:MAX_BODY], article_id),
        )
        self.db.commit()


def phase_submit(st: Store, limit: int, source: str | None) -> tuple[int, int, bool]:
    """提交保存任务。返回 (提交数, 失败数, 是否配额耗尽)。"""
    rows = st.candidates(limit, source)
    print(f"[submit] 候选 {len(rows)} 条", flush=True)
    submitted = failed = 0
    for idx, (aid, url, src) in enumerate(rows, 1):
        proc = _run([GETNOTE, "save", url, "-o", "json"], timeout=180)
        payload = _json_of(proc)
        if proc.returncode == 124:
            # 服务端超时：不写任务表，这条 URL 留给下一轮再试，避免误判成"永久失败"
            print(f"[{idx}/{len(rows)}] TIMEOUT id={aid} {src} 服务端超时，留给下轮", flush=True)
            continue
        raw = (proc.stdout or "") + (proc.stderr or "")
        if _quota_exhausted(payload, raw):
            print("[submit] 配额已用尽，停止提交（明天额度重置后继续）", flush=True)
            return submitted, failed, True
        task_id = str(_dig(payload, "task_id", "taskId", "id", "note_id", "noteId") or "")
        note_id = str(_dig(payload, "note_id", "noteId") or "")
        ok = bool(payload.get("ok", True)) and bool(task_id or note_id)
        if ok:
            # 新版 save 同步返回正文：直接落库，省掉 poll/collect 两趟往返。
            body = body_of_payload(payload)
            if len(body) >= MIN_BODY:
                st.write_body(aid, body)
                st.add_task(url, aid, src or "", task_id, note_id, "fetched")
                submitted += 1
                print(f"[{idx}/{len(rows)}] OK    id={aid} {src} len={len(body)}", flush=True)
                time.sleep(0.4)
                continue
            st.add_task(url, aid, src or "", task_id, note_id,
                        "done" if note_id else "submitted")
            submitted += 1
            print(f"[{idx}/{len(rows)}] SUBMIT id={aid} {src} task={task_id or note_id[:12]}", flush=True)
        else:
            failed += 1
            err = (payload.get("error") or {}).get("message") or raw[:120]
            st.add_task(url, aid, src or "", "", "", "failed", str(err)[:200])
            print(f"[{idx}/{len(rows)}] FAIL   id={aid} {src} {str(err)[:80]}", flush=True)
        time.sleep(0.4)
    return submitted, failed, False


def phase_poll(st: Store) -> int:
    """轮询任务进度，拿到 note_id 后转 done。返回转为 done 的条数。"""
    rows = st.pending("submitted")
    if not rows:
        return 0
    done = 0
    for url, task_id, _aid in rows:
        if not task_id:
            st.set_status(url, "failed", error="no task_id")
            continue
        proc = _run([GETNOTE, "task", task_id, "-o", "json"], timeout=60)
        payload = _json_of(proc)
        raw = (proc.stdout or "") + (proc.stderr or "")
        if _quota_exhausted(payload, raw):
            print("[poll] 配额已用尽，停止轮询", flush=True)
            break
        note_id = str(_dig(payload, "note_id", "noteId", "id") or "")
        status = str(_dig(payload, "status", "state") or "").lower()
        if note_id:
            st.set_status(url, "done", note_id=note_id)
            done += 1
        elif status in {"failed", "error", "failed_permanent"}:
            st.set_status(url, "failed", error=status)
        else:
            st.set_status(url, "submitted")
        time.sleep(0.3)
    print(f"[poll] 已就绪 {done} / 轮询 {len(rows)}", flush=True)
    return done


def phase_collect(st: Store) -> tuple[int, int]:
    """读取已完成任务的正文字段，写回 articles。"""
    rows = st.pending("done")
    ok = empty = 0
    for url, _task_id, aid in rows:
        note_id_row = st.db.execute(
            "SELECT note_id FROM getnote_body_task WHERE url = ?", (url,)
        ).fetchone()
        note_id = (note_id_row[0] if note_id_row else "") or ""
        if not note_id:
            st.set_status(url, "failed", error="no note_id")
            continue
        proc = _run([GETNOTE, "note", note_id, "--field", "content", "-o", "json"], timeout=60)
        payload = _json_of(proc)
        raw = (proc.stdout or "") + (proc.stderr or "")
        if _quota_exhausted(payload, raw):
            print("[collect] 配额已用尽，停止回收", flush=True)
            break
        body = _dig(payload, "content", "text", "data") or ""
        if not isinstance(body, str):
            body = ""
        body = body.strip()
        if len(body) >= MIN_BODY:
            st.write_body(aid, body)
            st.set_status(url, "fetched")
            ok += 1
            print(f"[collect] OK   id={aid} len={len(body)}", flush=True)
        else:
            st.set_status(url, "fetched_empty")
            empty += 1
            print(f"[collect] EMPTY id={aid} 服务端也没抓到正文", flush=True)
        time.sleep(0.3)
    print(f"[collect] 写入 {ok} 条，空 {empty} 条", flush=True)
    return ok, empty


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=900, help="本轮最多提交多少条（受 daily 配额限制）")
    ap.add_argument("--source", default=None, help="只处理指定来源")
    ap.add_argument("--poll-rounds", type=int, default=5, help="轮询轮数")
    ap.add_argument("--poll-interval", type=float, default=90.0, help="每轮轮询间隔秒数")
    ap.add_argument("--collect-only", action="store_true", help="只回收已完成任务")
    ap.add_argument("--shard", default="0/1",
                    help="并行分片，形如 1/4：第 1 片（0 基）共 4 片，按 article id 取模划分，"
                         "各片候选互不重叠。服务端单条约 30s，靠多片并行可以把吞吐翻倍。")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    try:
        shard_i, shard_n = (int(x) for x in args.shard.split("/", 1))
    except Exception:  # noqa: BLE001
        shard_i, shard_n = 0, 1
    if shard_n < 1 or not (0 <= shard_i < shard_n):
        print(f"[shard] 分片参数非法: {args.shard}", flush=True)
        return 1

    st = Store(dry=args.dry_run, shard=shard_i, shard_total=shard_n)
    available, detail = quota_report()
    print(f"[quota] {detail}", flush=True)
    if not available and not args.dry_run:
        print("[quota] 跳过本轮（配额打满时 save 也会被网关拒绝，避免刷无用请求）", flush=True)
        return 0
    if not args.collect_only:
        submitted, failed, exhausted = phase_submit(st, args.limit, args.source)
        print(f"[submit] 提交 {submitted} 条，失败 {failed} 条", flush=True)
        if submitted and not st.pending("submitted", 1):
            print("[poll] 本轮全部同步返回正文，跳过轮询", flush=True)
        elif submitted:
            for rnd in range(1, args.poll_rounds + 1):
                time.sleep(args.poll_interval)
                ready = phase_poll(st)
                print(f"[poll] 第 {rnd}/{args.poll_rounds} 轮就绪 {ready}", flush=True)
    ok, empty = phase_collect(st)
    print(f"DONE getnote ok={ok} empty={empty}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
