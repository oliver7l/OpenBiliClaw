#!/usr/bin/env python3
"""按当前阈值重算「scan」来源的人物标签（不重新检测，秒级）。

嵌入已在 faces.emb_r50 中，改阈值/改模型后跑本脚本即可刷新标签，
只影响 face_id >= 9000000 的扫描记录与 source='scan' 的标签。

用法:
  python tools/relabel.py                     # 用默认阈值重算
  python tools/relabel.py --thresh 乐仔=0.6 妈妈=0.6
  python tools/relabel.py --min-score 0.5     # 全局下限
"""
import os
import sys
import json
import sqlite3
import argparse

import numpy as np

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
DB = f"{ROOT}/19_统一相册库/library.db"
DEFAULT_THRESH = {"妈妈": 0.55, "艳艳": 0.30, "我": 0.40, "爸爸": 0.35,
                  "七月": 0.45, "乐仔": 0.55}
NEW_FACE_ID_BASE = 9_000_000

# 质量规则（2026-09-19 抽图核验后定）：
# 1) 09=QQ班级相册，场景里是幼童+老师，成人标签（妈妈/艳艳/我/爸爸）一律禁用
#    —— 实测"戴眼镜的幼童"会被妈妈模型以 0.80 高分误判。
# 2) 09 中幼童密度高（全是同龄小朋友），乐仔/七月阈值收紧到 0.65。
# 3) 全局要求 top1-top2 分差 >= MIN_MARGIN，压掉人物间嵌入重叠的边缘样本。
LIB_BLOCK = {"09": {"妈妈", "艳艳", "我", "爸爸"}}
PER_LIB_THRESH = {("09", "乐仔"): 0.65, ("09", "七月"): 0.65}
MIN_MARGIN = 0.03


def l2n(v):
    v = np.asarray(v, dtype=np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else v


def load_models():
    models = {}
    for p in ["妈妈", "艳艳", "我", "爸爸", "七月"]:
        m = json.load(open(f"{ROOT}/照片人物判定/{p}/{p}_model_v1.json", encoding="utf-8"))
        G = np.array([g["embedding"] for g in m["gallery"]], dtype=np.float32)
        models[p] = G / np.linalg.norm(G, axis=1, keepdims=True)
    m4 = json.load(open(f"{ROOT}/照片人物判定/乐仔/lezai_model_v4.json", encoding="utf-8"))
    G4 = np.array(m4["gallery"], dtype=np.float32)
    models["乐仔"] = G4 / np.linalg.norm(G4, axis=1, keepdims=True)
    return models


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--thresh", nargs="*", default=[], help="覆盖阈值，如 乐仔=0.6")
    ap.add_argument("--min-score", type=float, default=0.0)
    args = ap.parse_args()

    th_map = dict(DEFAULT_THRESH)
    for kv in args.thresh:
        k, _, v = kv.partition("=")
        th_map[k.strip()] = float(v)
    if args.min_score:
        th_map = {k: max(v, args.min_score) for k, v in th_map.items()}
    print("阈值:", th_map, "| margin>=", MIN_MARGIN, "| 09 屏蔽:", LIB_BLOCK["09"])

    db = sqlite3.connect(DB)
    models = load_models()
    rows = db.execute(
        "SELECT face_id, lib, content_key, emb_r50 FROM faces "
        "WHERE face_id >= ? AND emb_r50 IS NOT NULL", (NEW_FACE_ID_BASE,)).fetchall()
    print("待重算人脸:", len(rows))

    by_person = {}
    for name, G in models.items():
        by_person[name] = G

    face_upd, tags = [], {}
    for fid, lib, ck, blob in rows:
        v = l2n(np.frombuffer(blob, dtype=np.float32))
        cand = {p: G for p, G in by_person.items()
                if p not in LIB_BLOCK.get(lib, set())}
        s = sorted(((float((G @ v).max()), p) for p, G in cand.items()), reverse=True)
        if not s:
            face_upd.append((None, fid))
            continue
        best_s, best_p = s[0]
        second = s[1][0] if len(s) > 1 else -1.0
        th = PER_LIB_THRESH.get((lib, best_p), th_map.get(best_p, DEFAULT_THRESH[best_p]))
        person = best_p if (best_s >= th and best_s - second >= MIN_MARGIN) else None
        face_upd.append((person, fid))
        if person:
            prev = tags.get(ck, {}).get(person)
            if prev is None or best_s > prev:
                tags.setdefault(ck, {})[person] = best_s

    db.executemany("UPDATE faces SET person=? WHERE face_id=?", face_upd)
    db.execute("DELETE FROM photo_person_tags WHERE source='scan'")
    db.executemany(
        "INSERT OR REPLACE INTO photo_person_tags VALUES(?,?,?,?)",
        [(ck, p, "scan", s) for ck, d in tags.items() for p, s in d.items()])
    db.commit()
    print("标签写入:", db.execute(
        "SELECT COUNT(*) FROM photo_person_tags WHERE source='scan'").fetchone()[0])
    for r in db.execute("SELECT person, COUNT(*) FROM photo_person_tags "
                        "WHERE source='scan' GROUP BY person ORDER BY 2 DESC"):
        print("  ", r)


if __name__ == "__main__":
    main()
