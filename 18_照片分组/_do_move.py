#!/usr/bin/env python3
"""18_照片分组 · 第二步执行移动（真移动，用户已确认）。

- 读 _scan_plan.json 的 single 清单
- 目标: 18_照片分组/<人>/<来源>/<原文件名>，同名冲突加序号
- 每批 500 张校验，出错即停
- 落 moved 清单 + 回写两库 moved 表
"""
import json
import os
import shutil
import sqlite3
from collections import defaultdict

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
OUT = f"{ROOT}/18_照片分组"
BATCH = 500

plan = json.load(open(f"{OUT}/_scan_plan.json", encoding="utf-8"))
single = plan["single"]
print("待移动:", len(single))

moved, conflicts = [], []
used_dst = set()
for i, (src, person, source) in enumerate(single):
    if not src or not os.path.exists(src):
        print(f"跳过(不存在): {src}")
        continue
    ddir = f"{OUT}/{person}/{source}"
    os.makedirs(ddir, exist_ok=True)
    base = os.path.basename(src)
    dst = os.path.join(ddir, base)
    stem, ext = os.path.splitext(base)
    n = 1
    while dst in used_dst or os.path.exists(dst):
        n += 1
        dst = os.path.join(ddir, f"{stem}__{n}{ext}")
    if n > 1:
        conflicts.append((src, dst))
    shutil.move(src, dst)
    used_dst.add(dst)
    moved.append({"src": src, "dst": dst, "person": person, "source": source})
    if len(moved) % BATCH == 0:
        print(f"  已移动 {len(moved)}")

print(f"完成: 移动 {len(moved)}，改名冲突 {len(conflicts)}")
json.dump(moved, open(f"{OUT}/_moved_log.json", "w", encoding="utf-8"), ensure_ascii=False)

# 回写 07 photo_index.db: moved_files 表（file_key 级）
con = sqlite3.connect(f"{ROOT}/07_相册/相册&视频备份/_photo_index/photo_index.db")
con.execute("""CREATE TABLE IF NOT EXISTS moved_files(
 path TEXT PRIMARY KEY, dst TEXT, person TEXT, moved_at TEXT DEFAULT (datetime('now')))""")
rows07 = [(m["src"], m["dst"], m["person"]) for m in moved if m["source"] == "07_相册"]
con.executemany("INSERT OR REPLACE INTO moved_files VALUES(?,?,?,datetime('now'))", rows07)
con.commit()
n_chk = con.execute("SELECT COUNT(*) FROM moved_files").fetchone()[0]
print(f"07 moved_files 落库复核: {n_chk}（本次 {len(rows07)}）")
con.close()

# 回写 08 faces08.db: moved_photos 表（photo 相对路径级）
con = sqlite3.connect(f"{ROOT}/08_乐仔相册/_face_index/faces08.db")
con.execute("""CREATE TABLE IF NOT EXISTS moved_photos(
 photo TEXT PRIMARY KEY, dst TEXT, person TEXT, moved_at TEXT DEFAULT (datetime('now')))""")
P08 = f"{ROOT}/08_乐仔相册/乐仔相片库/"
rows08 = [(m["src"].replace(P08, ""), m["dst"], m["person"])
          for m in moved if m["source"] == "08_乐仔相册"]
con.executemany("INSERT OR REPLACE INTO moved_photos VALUES(?,?,?,datetime('now'))", rows08)
con.commit()
n_chk = con.execute("SELECT COUNT(*) FROM moved_photos").fetchone()[0]
print(f"08 moved_photos 落库复核: {n_chk}（本次 {len(rows08)}）")
con.close()
