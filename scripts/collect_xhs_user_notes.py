#!/usr/bin/env python3
"""整批归档某小红书用户发布的笔记到阅读库(articles)。

用法:
  python3 scripts/collect_xhs_user_notes.py <user_id> [--delay 4] [--limit N]
  python3 scripts/collect_xhs_user_notes.py <用户主页URL> [--delay 4]

行为:
  - 分页调 `xhs user-posts <user_id> --json` 拿到该用户全部笔记(note_id + xsec_token)
  - 对每篇构造 explore URL, 复用 scripts/save_xhs_note.py 归档(已含 note_id 去重 + 空壳补强)
  - 每篇之间 sleep --delay 秒, 避免触发小红书限流(用户要求: 很慢很慢)
  - 抓取失败(限流/登录过期)自动退避 --backoff 秒后跳过该篇, 不中断整体
  - 幂等: 已归档的同篇会 [跳过], 可安全重跑

依赖: 本机 xhs CLI 已登录; 依赖 scripts/save_xhs_note.py
"""
import argparse
import json
import re
import subprocess
import sys
import time

BASE = __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
SAVE_SCRIPT = __import__("os").path.join(BASE, "scripts", "save_xhs_note.py")


def _user_id_from(arg):
    m = re.search(r"/user/profile/([0-9a-zA-Z]+)", arg)
    if m:
        return m.group(1)
    # 可能直接给了 user_id
    if re.fullmatch(r"[0-9a-zA-Z]+", arg):
        return arg
    return None


def _fetch_user_posts(user_id, cursor=None):
    cmd = ["xhs", "user-posts", user_id, "--json"]
    if cursor:
        cmd += ["--cursor", cursor]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    try:
        return json.loads(out.stdout)
    except Exception:  # noqa: BLE001
        return None


def _parse_notes(page):
    if not page or not page.get("ok"):
        return [], None
    data = page.get("data", {})
    notes = data.get("notes") or data.get("items") or []
    nxt = data.get("cursor") or data.get("next_cursor")
    has_more = data.get("has_more")
    if has_more is False and nxt is None:
        nxt = None
    return notes, nxt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("user", help="user_id 或用户主页 URL")
    ap.add_argument("--delay", type=float, default=4.0, help="每篇之间的间隔秒数(默认4, 越慢越不易限流)")
    ap.add_argument("--backoff", type=float, default=20.0, help="单篇失败后的退避秒数(默认20)")
    ap.add_argument("--limit", type=int, default=0, help="最多处理 N 篇(测试用, 0=不限制)")
    args = ap.parse_args()

    user_id = _user_id_from(args.user)
    if not user_id:
        sys.exit(f"无法识别 user_id: {args.user}")

    # 1) 分页收集全部笔记
    collected = []
    cursor = None
    page_no = 0
    while True:
        page_no += 1
        page = _fetch_user_posts(user_id, cursor)
        if not page:
            print(f"[警告] 第 {page_no} 页 user-posts 获取失败, 停止翻页")
            break
        notes, cursor = _parse_notes(page)
        if not notes:
            print(f"[信息] 第 {page_no} 页无笔记, 停止")
            break
        collected.extend(notes)
        print(f"[翻页] 第 {page_no} 页 +{len(notes)} 篇, 累计 {len(collected)} 篇"
              + (f", cursor={cursor[:12]}..." if cursor else ", 已到末页"))
        if not cursor:
            break
        time.sleep(args.delay)

    if args.limit:
        collected = collected[: args.limit]
        print(f"[限制] 仅处理前 {len(collected)} 篇")

    # 2) 逐篇归档(复用 save_xhs_note, 自带去重)
    added = enriched = skipped = failed = 0
    total = len(collected)
    for i, n in enumerate(collected, 1):
        note_id = n.get("note_id") or n.get("id")
        xsec = n.get("xsec_token")
        title = n.get("display_title") or n.get("title") or ""
        if not note_id:
            print(f"  ({i}/{total}) 跳过: 无 note_id")
            failed += 1
            continue
        url = f"https://www.xiaohongshu.com/explore/{note_id}"
        if xsec:
            # user-posts 返回的 token 来源是 pc_user, 必须同源, 否则 xhs read 会因来源不符拿空/拿错
            url += f"?xsec_token={xsec}&xsec_source=pc_user"
        try:
            r = subprocess.run(
                ["python3", SAVE_SCRIPT, url],
                capture_output=True, text=True, timeout=150,
            )
            out = r.stdout.strip()
            if r.returncode != 0:
                print(f"  ({i}/{total}) 失败: {out[:120] or r.stderr[:120]}")
                failed += 1
                time.sleep(args.backoff)
                continue
            if "[完成]" in out:
                added += 1
                print(f"  ({i}/{total}) 新增: {title[:30]}")
            elif "[补全]" in out:
                enriched += 1
                print(f"  ({i}/{total}) 补强: {title[:30]}")
            else:
                skipped += 1
                print(f"  ({i}/{total}) 跳过(已存在): {title[:30]}")
        except Exception as e:  # noqa: BLE001
            print(f"  ({i}/{total}) 异常: {e}")
            failed += 1
            time.sleep(args.backoff)
            continue
        time.sleep(args.delay)

    print("\n==== 归档完成 ====")
    print(f"  新增: {added} | 补强: {enriched} | 已存在跳过: {skipped} | 失败: {failed}")
    print(f"  合计处理: {total} 篇 (用户: {user_id})")


if __name__ == "__main__":
    main()
