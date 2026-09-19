#!/usr/bin/env python3
"""统一相册库编目（Stage A，只读扫描，不移动任何文件）。

功能：
1. 扫描 07/08/09/18 四个来源的媒体文件（排除缩略图/裁剪/拼图等派生目录）
2. 计算 content_key（size+头部64K md5）用于内容去重
3. 导入 07 photo_index.db / 08 faces08.db 的既有人脸、人物、EXIF、嵌入
4. 生成人物标签 photo_person_tags（07聚类真值 + kNN模型补扫；08 lezai扫描 + v1模型）
5. 生成搬迁预案 plan 表（originals/<来源>/... 同卷 mv，可整体还原）

产出：19_统一相册库/library.db
"""
import os
import re
import json
import hashlib
import sqlite3
import datetime

import numpy as np

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
LIB = f"{ROOT}/19_统一相册库"
DB_PATH = f"{LIB}/library.db"

PI07 = f"{ROOT}/07_相册/相册&视频备份"
PI08 = f"{ROOT}/08_乐仔相册"
PI09 = f"{ROOT}/09_QQ相册/中三班照片库"
PI18 = f"{ROOT}/18_照片分组"
DB07 = f"{PI07}/_photo_index/photo_index.db"
DB08 = f"{PI08}/_face_index/faces08.db"
MOVED_LOG = f"{PI18}/_moved_log.json"

MEDIA_EXT = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".bmp", ".gif", ".tiff",
             ".mp4", ".mov", ".avi", ".m4v"}
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".bmp", ".tiff"}
# 派生/工作台目录：整目录排除
EXCLUDE_DIRS = {"thumbs", ".thumbs", "_thumbs", ".kid_faces", "_face_index",
                "_photo_index", "00-总览拼图", "乐仔-全库照片清单",
                "乐仔-人脸模型", "乐仔的照片-判定清单", "_heic_jpg", "tools",
                "下载工具", "模型工作台"}
EXIF_COLS = ["make", "model", "lens", "focal", "fnum", "exposure", "iso",
             "dt_orig", "software", "gps_lat", "gps_lon", "gps_alt",
             "width", "height", "orientation"]

# v1 模型阈值（thresholds.recommended，FPR<=1%）；乐仔 v4 为经验值
MODEL_THRESH = {"妈妈": 0.55, "艳艳": 0.30, "我": 0.40, "爸爸": 0.35,
                "七月": 0.45, "乐仔": 0.55}


def l2n(v):
    v = np.asarray(v, dtype=np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else v


def md5(s):
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def head_hash(path, size):
    h = hashlib.md5()
    with open(path, "rb") as f:
        h.update(f.read(65536))
    return hashlib.md5(f"{size}:{h.hexdigest()}".encode()).hexdigest()


def scan_media(base, lib, source_fn):
    """扫描 base 下的媒体文件 -> list[dict]"""
    rows = []
    for root, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs
                         if not d.startswith((".", "_")) and d not in EXCLUDE_DIRS)
        for fn in sorted(files):
            ext = os.path.splitext(fn)[1].lower()
            if ext not in MEDIA_EXT or fn.startswith("."):
                continue
            path = os.path.join(root, fn)
            try:
                st = os.stat(path)
            except OSError:
                continue
            rel = os.path.relpath(path, base)
            rows.append({
                "lib": lib, "path": path, "rel": rel,
                "ext": ext.lstrip("."), "size": st.st_size,
                "mtime": st.st_mtime,
                "kind": "photo" if ext in PHOTO_EXT else "video",
                "source": None, "source_detail": None,
                "origin_lib": None, "origin_rel": None,
            })
    for r in rows:
        source_fn(r)
    return rows


def src07(r):
    r["source"] = "夸克网盘-手机备份"
    r["source_detail"] = r["rel"].split("/")[0]


def src08(r):
    if r["rel"].startswith("乐仔的照片/"):
        r["source"] = "夸克网盘-乐仔的照片"
        r["source_detail"] = "乐仔的照片/" + r["rel"].split("/")[1] if "/" in r["rel"][6:] else "乐仔的照片"
    elif r["rel"].startswith("乐仔相片库/"):
        r["source"] = "夸克网盘-乐仔相片库(分类)"
        r["source_detail"] = r["rel"].split("/")[1] if "/" in r["rel"][6:] else "乐仔相片库"
    else:
        r["source"] = "夸克网盘-乐仔的照片"


def src09(r):
    r["source"] = "QQ群相册-中三班"
    r["source_detail"] = r["rel"].split("/")[0]


def src18(r):
    r["source"] = "人物分组"


def guess_ym(r):
    m = re.search(r"(20\d{2}-\d{2})", r["rel"])
    if m:
        r["ym"] = m.group(1)
    else:
        m2 = re.search(r"(20\d{2})年?(\d{1,2})月", r["rel"])
        if m2:
            r["ym"] = f"{m2.group(1)}-{int(m2.group(2)):02d}"
        else:
            r["ym"] = None


def create_schema(con):
    con.executescript("""
    CREATE TABLE files(
      file_key TEXT PRIMARY KEY,
      content_key TEXT,
      lib TEXT, path TEXT UNIQUE, rel TEXT,
      kind TEXT, ext TEXT, size INTEGER, mtime REAL,
      taken TEXT, ym TEXT,
      source TEXT, source_detail TEXT,
      origin_lib TEXT, origin_rel TEXT,
      person_group TEXT,
      is_primary INTEGER DEFAULT 1,
      indexed_at TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX idx_files_content ON files(content_key);
    CREATE INDEX idx_files_source ON files(source);
    CREATE TABLE persons(cluster INTEGER PRIMARY KEY, name TEXT, note TEXT);
    CREATE TABLE faces(
      face_id INTEGER, lib TEXT, content_key TEXT,
      face_idx INTEGER, x INT, y INT, w INT, h INT, det_score REAL,
      cluster INTEGER, person TEXT,
      emb_mbf BLOB, emb_r50 BLOB,
      PRIMARY KEY(face_id, lib)
    );
    CREATE INDEX idx_faces_content ON faces(content_key);
    CREATE TABLE photo_person_tags(
      content_key TEXT, person TEXT, source TEXT, score REAL,
      PRIMARY KEY(content_key, person)
    );
    CREATE TABLE exif(
      content_key TEXT PRIMARY KEY,
      make TEXT, model TEXT, lens TEXT, focal REAL, fnum REAL, exposure TEXT,
      iso INTEGER, dt_orig TEXT, software TEXT,
      gps_lat REAL, gps_lon REAL, gps_alt REAL,
      width INTEGER, height INTEGER, orientation INTEGER
    );
    CREATE TABLE plan(
      file_key TEXT PRIMARY KEY,
      from_path TEXT, to_path TEXT, action TEXT
    );
    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
    """)


def main():
    if not os.path.exists(LIB):
        os.makedirs(LIB)
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    create_schema(con)
    cur = con.cursor()

    print("[1/6] 扫描媒体文件 ...")
    rows = []
    rows += scan_media(PI07, "07", src07)
    rows += scan_media(PI08, "08", src08)
    rows += scan_media(PI09, "09", src09)
    rows += scan_media(PI18, "18", src18)
    print(f"  共 {len(rows)} 个媒体文件")

    # 18 的来源映射（_moved_log.json）
    moved = json.load(open(MOVED_LOG))
    dst2log = {}
    for e in moved:
        dst2log[e["dst"]] = e
    print(f"  _moved_log {len(moved)} 条")

    # 18 的 origin_lib/origin_rel
    for r in rows:
        if r["lib"] == "18":
            e = dst2log.get(r["path"])
            if e:
                src = e["src"]
                if "07_相册/相册&视频备份/" in src:
                    r["origin_lib"] = "07"
                    r["origin_rel"] = src.split("07_相册/相册&视频备份/", 1)[1]
                elif "08_乐仔相册/" in src:
                    r["origin_lib"] = "08"
                    r["origin_rel"] = src.split("08_乐仔相册/", 1)[1]
                r["source"] = f"人物分组(源:{r['origin_lib']})"
        guess_ym(r)
        r["person_group"] = r["rel"].split("/")[0] if r["lib"] == "18" else None

    print("[2/6] 计算 content_key（头部哈希，读 64K/文件）...")
    for i, r in enumerate(rows):
        try:
            r["content_key"] = head_hash(r["path"], r["size"])
        except OSError:
            r["content_key"] = None
        r["file_key"] = md5(r["path"])
        if (i + 1) % 5000 == 0:
            print(f"  {i+1}/{len(rows)}")
    ck = [r for r in rows if not r["content_key"]]
    print(f"  哈希失败 {len(ck)} 个")

    print("[3/6] 内容去重 ...")
    # 去重优先级：07 > 08乐仔的照片 > 08相片库 > 09 > 18
    prio = {"07": 0}
    seen = {}
    for r in rows:
        if not r["content_key"]:
            continue
        if r["content_key"] not in seen:
            seen[r["content_key"]] = r
            r["is_primary"] = 1
        else:
            first = seen[r["content_key"]]
            if prio.get(r["lib"], 5) < prio.get(first["lib"], 5):
                first["is_primary"] = 0
                seen[r["content_key"]] = r
                r["is_primary"] = 1
            else:
                r["is_primary"] = 0
    n_dup = sum(1 for r in rows if r["is_primary"] == 0)
    print(f"  重复副本 {n_dup} 个（保留原位，预案中标出）")

    print("[4/6] 写 files 表 ...")
    cur.executemany(
        """INSERT INTO files(file_key, content_key, lib, path, rel, kind, ext,
           size, mtime, ym, source, source_detail, origin_lib, origin_rel,
           person_group, is_primary)
           VALUES(:file_key,:content_key,:lib,:path,:rel,:kind,:ext,:size,
           :mtime,:ym,:source,:source_detail,:origin_lib,:origin_rel,
           :person_group,:is_primary)""", rows)

    path2row = {r["path"]: r for r in rows}
    # 07 legacy file_key(md5(rel)[:16]) -> 当前 row（含 18 中来自 07 的照片）
    fk07_2row = {}
    for r in rows:
        if r["lib"] == "07":
            fk07_2row[md5(r["rel"])[:16]] = r
        elif r["lib"] == "18" and r["origin_lib"] == "07" and r["origin_rel"]:
            fk07_2row[md5(r["origin_rel"])[:16]] = r
    rel08_lib_2row = {}
    for r in rows:
        if r["lib"] == "08" and r["rel"].startswith("乐仔相片库/"):
            rel08_lib_2row[r["rel"][len("乐仔相片库/"):]] = r
        elif r["lib"] == "18" and r["origin_lib"] == "08" and r["origin_rel"]:
            parts = r["origin_rel"].split("/", 1)
            if len(parts) == 2 and parts[0] == "乐仔相片库":
                rel08_lib_2row[parts[1]] = r

    print("[5/6] 导入既有人脸/人物/EXIF 与人物标签 ...")
    # ---- 07 ----
    db7 = sqlite3.connect(DB07)
    persons7 = dict(db7.execute("SELECT cluster, name FROM persons").fetchall())
    for cl, name in persons7.items():
        cur.execute("INSERT OR REPLACE INTO persons VALUES(?,?,?)", (cl, name, "07聚类真值"))

    d = np.load(f"{PI07}/_photo_index/r50_sample_emb.npz")
    E50 = {int(i): e for i, e in zip(d["ids"], d["emb"])}

    # 模型库（r50 空间）；v1 gallery 为 {face_id, photo, embedding} 列表
    models = {}
    for p in ["妈妈", "艳艳", "我", "爸爸", "七月"]:
        m = json.load(open(f"{ROOT}/照片人物判定/{p}/{p}_model_v1.json"))
        G = np.array([g["embedding"] for g in m["gallery"]], dtype=np.float32)
        models[p] = G / np.linalg.norm(G, axis=1, keepdims=True)
    m4 = json.load(open(f"{ROOT}/照片人物判定/乐仔/lezai_model_v4.json"))
    G4 = np.array(m4["gallery"], dtype=np.float32)
    models["乐仔"] = G4 / np.linalg.norm(G4, axis=1, keepdims=True)  # v4 兼容 r50 空间

    tags7 = {}   # content_key -> {person: (source, score)}
    n_cl = n_md = 0
    face_rows = []
    for fid, fk, ym7, emb_mbf, cl, x, y, w, h, ds in db7.execute(
            """SELECT f.id, f.file_key, f.ym, f.embedding, f.cluster,
               f.x, f.y, f.w, f.h, f.det_score FROM faces f"""):
        row = fk07_2row.get(fk)  # file_key 即 md5(rel)[:16]
        if row is None or not row.get("content_key"):
            continue
        ck = row["content_key"]
        r50 = E50.get(fid)
        person = persons7.get(cl)
        face_rows.append((fid, "07", ck, None, x, y, w, h, ds, cl,
                          person, emb_mbf, r50.tobytes() if r50 is not None else None))
        if person:
            t = tags7.setdefault(ck, {})
            if person not in t:
                t[person] = ("cluster", None)
            n_cl += 1
        elif r50 is not None:
            v = l2n(r50)
            best_p, best_s = None, -1.0
            scores = {}
            for p, G in models.items():
                s = float((G @ v).max())
                scores[p] = s
                if s > best_s:
                    best_p, best_s = p, s
            if best_p and best_s >= MODEL_THRESH.get(best_p, 1.0):
                tags7.setdefault(ck, {}).setdefault(best_p, ("model", best_s))
                n_md += 1
    print(f"  07: 聚类标签脸 {n_cl}, 模型补扫命中 {n_md}")

    # 07 exif（exif.rel 与 legacy files.rel 均为旧结构；现行位置以 new_path||path 为准）
    name2rel = {}
    for nm, p, np_ in db7.execute("SELECT lower(name), path, new_path FROM files"):
        cur_rel = (np_ or p).split("07_相册/相册&视频备份/", 1)[-1]
        name2rel.setdefault(nm, []).append(cur_rel)
    n_exif = 0
    for rel, *vals in db7.execute(
            f"SELECT rel, {','.join(EXIF_COLS)} FROM exif"):
        cands = name2rel.get(rel.split("/")[-1].lower(), [])
        if len(cands) == 1:
            row = fk07_2row.get(md5(cands[0])[:16])
            if row and row.get("content_key"):
                cur.execute(
                    f"INSERT OR REPLACE INTO exif VALUES(?{',?'*len(EXIF_COLS)})",
                    [row["content_key"]] + list(vals))
                n_exif += 1
    # 07 taken 时间回填（同样用现行 rel）
    for p, np_, taken, ym in db7.execute("SELECT path, new_path, taken, ym FROM files"):
        cur_rel = (np_ or p).split("07_相册/相册&视频备份/", 1)[-1]
        row = fk07_2row.get(md5(cur_rel)[:16])
        if row:
            cur.execute("UPDATE files SET taken=?, ym=COALESCE(ym,?) WHERE file_key=?",
                        (taken, ym, row["file_key"]))
    print(f"  07 exif 导入 {n_exif}")

    # ---- 08 ----
    db8 = sqlite3.connect(DB08)
    scan8 = dict(db8.execute("SELECT face_id, tier FROM lezai_scan08"))
    tags8 = {}
    n_a = n_b = n_md8 = 0
    for fid, photo, cat, emb_mbf, emb_r50, ds in db8.execute(
            """SELECT f.id, f.photo, f.category, f.emb_mbf, f.emb_r50,
               f.det_score FROM faces f"""):
        row = rel08_lib_2row.get(photo)
        if row is None or not row.get("content_key"):
            continue
        ck = row["content_key"]
        face_rows.append((fid, "08", ck, None, None, None, None, None, ds, None,
                          "乐仔" if scan8.get(fid) in ("A", "B") else None,
                          emb_mbf, emb_r50))
        tier = scan8.get(fid)
        if tier == "A":
            tags8.setdefault(ck, {}).setdefault("乐仔", ("scan_A", None))
            n_a += 1
        elif tier == "B":
            tags8.setdefault(ck, {}).setdefault("乐仔", ("scan_B", None))
            n_b += 1
        if tier not in ("A", "B") and emb_r50:
            v = l2n(np.frombuffer(emb_r50, dtype=np.float32))
            best_p, best_s = None, -1.0
            for p in ["妈妈", "艳艳", "我", "爸爸", "七月"]:
                s = float((models[p] @ v).max())
                if s > best_s:
                    best_p, best_s = p, s
            if best_p and best_s >= MODEL_THRESH[best_p]:
                tags8.setdefault(ck, {}).setdefault(best_p, ("model", best_s))
                n_md8 += 1
    print(f"  08: lezai scan A {n_a} / B {n_b}, v1模型命中 {n_md8}")

    for ck, t in tags7.items():
        for p, (src, sc) in t.items():
            cur.execute("INSERT OR REPLACE INTO photo_person_tags VALUES(?,?,?,?)",
                        (ck, p, src, sc))
    for ck, t in tags8.items():
        for p, (src, sc) in t.items():
            cur.execute("INSERT OR REPLACE INTO photo_person_tags VALUES(?,?,?,?)",
                        (ck, p, src, sc))

    cur.executemany("INSERT OR REPLACE INTO faces VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    face_rows)

    print("[6/6] 生成搬迁预案 ...")
    TARGET = {
        "07": "originals/07_夸克手机备份",
        "09": "originals/09_QQ群相册",
        "18": "originals/18_人物分组",
    }
    n_plan = 0
    for r in rows:
        if r["lib"] == "08":
            if r["rel"].startswith("乐仔的照片/"):
                tgt = "originals/08_夸克乐仔照片/" + r["rel"][len("乐仔的照片/"):]
            elif r["rel"].startswith("乐仔相片库/"):
                tgt = "originals/08_夸克乐仔相片库/" + r["rel"][len("乐仔相片库/"):]
            else:
                tgt = "originals/08_其他/" + r["rel"]
        else:
            tgt = TARGET[r["lib"]] + "/" + r["rel"]
        action = "keep" if r["is_primary"] else "dup"
        cur.execute("INSERT INTO plan VALUES(?,?,?,?)",
                    (r["file_key"], r["path"], f"{LIB}/{tgt}", action))
        n_plan += 1

    cur.executemany("INSERT INTO meta VALUES(?,?)", [
        ("built_at", datetime.datetime.now().isoformat(timespec="seconds")),
        ("n_files", str(len(rows))),
        ("n_dup", str(n_dup)),
        ("version", "1"),
    ])
    con.commit()

    # 汇总
    print("\n===== 汇总 =====")
    for row in cur.execute("""SELECT lib, source, kind, COUNT(*), SUM(size)/1e9
                             FROM files GROUP BY lib, source, kind ORDER BY lib"""):
        print(f"  {row[0]} | {row[1]} | {row[2]} | {row[3]} 个 | {row[4]:.1f} GB")
    for row in cur.execute("""SELECT person, source, COUNT(DISTINCT content_key)
                             FROM photo_person_tags GROUP BY person, source"""):
        print(f"  标签 {row[0]} ({row[1]}): {row[2]} 张照片")
    con.close()
    print(f"\n完成 -> {DB_PATH}")


if __name__ == "__main__":
    main()
