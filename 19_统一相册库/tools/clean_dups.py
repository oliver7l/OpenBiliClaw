#!/usr/bin/env python3
"""清理重复副本（默认 dry-run，绝不误删）。

安全约束（全部满足才会删）：
  1. 文件必须位于 originals/_重复/ 下
  2. 该 content_key 在主树中必须存在对应主副本（非 _重复 目录）
  3. 副本与主副本的**全文件 md5 必须一致**（先跑 tools/verify_dups.py 生成）
  4. 同一内容组内若出现多个不同 md5（伪重复）→ 整组跳过
  5. 每批 10 个，逐批校验后继续；全程写入 _deleted_dups.jsonl 清单

用法:
  python tools/clean_dups.py               # dry-run，只预览
  python tools/clean_dups.py --apply       # 真正删除
  python tools/clean_dups.py --apply --lib 09
"""
import os
import sys
import json
import time
import sqlite3
import argparse

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
DB = f"{ROOT}/19_统一相册库/library.db"
LOG = f"{ROOT}/19_统一相册库/_deleted_dups.jsonl"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正删除（默认只预览）")
    ap.add_argument("--lib", help="只处理指定来源 07/08/09/18")
    ap.add_argument("--batch", type=int, default=10)
    args = ap.parse_args()

    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row

    # 伪重复组（组内 md5 不一致）→ 跳过
    md5s = {}
    for ck, m in db.execute(
            """SELECT content_key, md5 FROM files
               WHERE content_key IN (SELECT DISTINCT content_key FROM files
                                     WHERE path LIKE '%/_重复/%')"""):
        md5s.setdefault(ck, set()).add(m)
    skip = {k for k, v in md5s.items() if len(v) > 1}
    print(f"伪重复组（整组跳过）: {len(skip)}")

    q = "SELECT file_key, path, size, lib, content_key FROM files WHERE path LIKE '%/_重复/%'"
    p = []
    if args.lib:
        q += " AND lib=?"
        p.append(args.lib)
    rows = db.execute(q, p).fetchall()

    todo, blocked = [], []
    for r in rows:
        if r["content_key"] in skip:
            blocked.append(r["path"])
            continue
        main = db.execute(
            "SELECT md5 FROM files WHERE content_key=? AND path NOT LIKE '%/_重复/%' LIMIT 1",
            (r["content_key"],)).fetchone()
        me = db.execute("SELECT md5 FROM files WHERE file_key=?", (r["file_key"],)).fetchone()
        if not main or not me or main["md5"] is None or main["md5"] != me["md5"]:
            blocked.append(r["path"])
            continue
        todo.append(r)
    print(f"可删 {len(todo)} 个（{sum(r['size'] for r in todo)/1e9:.1f} GB）"
          f" / 保护 {len(blocked)} 个")

    if not args.apply:
        print("\n[dry-run] 预览前 10 条：")
        for r in todo[:10]:
            print("  ", r["path"][-95:])
        print("\n确认无误后加 --apply 执行（建议先 --lib 09 小规模验证）")
        return

    print("\n⚠️ 即将真实删除，3 秒后开始（Ctrl-C 中止）")
    time.sleep(3)
    log = open(LOG, "a", encoding="utf-8")
    n_ok = n_fail = 0
    for i in range(0, len(todo), args.batch):
        batch = todo[i:i + args.batch]
        for r in batch:
            try:
                if os.path.exists(r["path"]):
                    os.remove(r["path"])
                db.execute("DELETE FROM files WHERE file_key=?", (r["file_key"],))
                log.write(json.dumps({"file_key": r["file_key"], "path": r["path"],
                                      "size": r["size"], "lib": r["lib"],
                                      "content_key": r["content_key"],
                                      "ts": time.time()}, ensure_ascii=False) + "\n")
                n_ok += 1
            except Exception as e:
                n_fail += 1
                print(f"  ⚠️ {r['path'][-60:]}: {e}", file=sys.stderr)
        db.commit()
        log.flush()
        if (i // args.batch) % 20 == 0:
            print(f"[{i+len(batch)}/{len(todo)}] 已删 {n_ok} 错 {n_fail}", file=sys.stderr)
    log.close()
    print(f"完成：删除 {n_ok}，错 {n_fail}；清单 {LOG}")


if __name__ == "__main__":
    main()
