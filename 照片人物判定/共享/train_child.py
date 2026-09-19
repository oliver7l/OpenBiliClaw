#!/usr/bin/env python3
"""训练「幼童 vs 成人」判别器 —— 特征体系里的一个「小模型」。

为什么要自训而不直接用现成年龄模型
-----------------------------------
insightface buffalo_l 自带的 genderage.onnx 在本库实测**不可用**：
  09 班级幼童(真值 3~6 岁)判中位 28 岁、18 归档幼童判中位 36 岁、成人判 35 岁，
  幼童/成人 AUC 仅 0.625（≈无区分力）。已弃用，不落库。
所以改用在**本域数据**上自训练的判别器，种子来自 18_人物分组 的人工归档。

难点与对策
----------
幼童类如果只用「乐仔」一个孩子当种子，模型会学到「乐仔这张脸」而不是「幼童」。
对策是两轮自举：轮 0 用乐仔/乐仔小时候 建初版 → 给 09 班级库打分 →
把得分最高的脸（**每张照片最多取 1 张**，强制个体多样性）加入幼童正样本，
得分最低的加入成人负样本 → 重训。这样幼童类覆盖几十上百个不同孩子。

验证（未参与训练的 held-out）
----------------------------
「七月」全程不进训练集：若模型把它判成幼童，说明学到的是年龄特征而非身份特征。

用法
----
    python train_child.py
"""
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import KFold

sys.path.insert(0, str(Path(__file__).resolve().parent))
from train_per_person import LIB_DB, OUT_DIR, l2n, sigmoid  # noqa: E402

MODEL_JSON = OUT_DIR / "child_model.json"

KID_ARCH = {"乐仔", "乐仔小时候"}          # 幼童种子（人工归档）
ADULT_ARCH = {"艳艳", "我", "妈妈", "爸爸"}  # 成人种子
HOLDOUT = "七月"                          # 全程不进训练，用于泛化验证

MIN_SIDE_SEED = 16       # 幼童判定需要更清晰的脸
BOOT_ROUNDS = 2
N_09_TOP = 2500          # 每轮从 09 取的高分幼童数
N_09_BOT = 2500          # 每轮从 09 取的低分成人数
C_GRID = [0.03, 0.1, 0.3, 1.0, 3.0]
SEED = 42
rng = np.random.default_rng(SEED)


def log(*a):
    print(f"[{datetime.now():%H:%M:%S}]", *a, flush=True)


def ensure_tables(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS face_attrs (
            face_rid   INTEGER PRIMARY KEY,   -- faces.rowid（**唯一键，不能用 face_id**）
            content_key TEXT,
            side       INTEGER,
            det        REAL,
            p_child    REAL
        )""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_face_attrs_ck ON face_attrs(content_key)")
    con.execute("""
        CREATE TABLE IF NOT EXISTS photo_attrs (
            content_key TEXT PRIMARY KEY,
            lib         TEXT,
            face_count  INTEGER,
            n_big       INTEGER,
            max_side    INTEGER,
            n_child     INTEGER,
            n_adult     INTEGER,
            n_unknown   INTEGER,
            has_kid     INTEGER,
            group_size  TEXT
        )""")
    con.commit()


def load():
    """复用人物模型的加载逻辑：rowid 唯一键 + 三种特征变体。"""
    con = sqlite3.connect(LIB_DB)
    rows = con.execute(
        "select rowid, content_key, det_score, w, h, emb_mbf, emb_r50 from faces").fetchall()
    ck_person = {}
    for rel, ck in con.execute("select rel, content_key from files where lib='18'"):
        ck_person.setdefault(ck, rel.split("/")[0])
    nface = dict(con.execute("select content_key, count(*) from faces group by content_key"))
    ck_lib = {}
    for ck, lib in con.execute("select content_key, lib from files where is_primary=1"):
        ck_lib.setdefault(ck, lib)
    con.close()

    rid, cks, side, Xm, Xr = [], [], [], [], []
    for r, ck, det, w, h, mb, r5 in rows:
        if mb is None or r5 is None:
            continue
        if det is not None and det < 0.60:
            continue
        if w is None or h is None or min(w, h) < 10:
            continue
        rid.append(r)
        cks.append(ck)
        side.append(min(w, h))
        Xm.append(np.frombuffer(mb, dtype=np.float32))
        Xr.append(np.frombuffer(r5, dtype=np.float32))
    Xm, Xr = l2n(np.vstack(Xm)), l2n(np.vstack(Xr))
    feats = {"mbf": Xm, "r50": Xr, "fused": l2n(np.hstack([Xm, Xr]))}
    return (np.array(rid), np.array(cks, dtype=object), np.array(side), feats,
            ck_person, nface, ck_lib)


def single_face_idx(cks, side, nface, ck_person, persons):
    """人工归档的**单脸照片**的脸作种子（纯度最高）。"""
    want = {ck for ck, p in ck_person.items() if p in persons}
    return np.array([i for i in range(len(cks))
                     if cks[i] in want and nface.get(cks[i]) == 1
                     and side[i] >= MIN_SIDE_SEED], dtype=int)


def cv_auc(Xp, Xn):
    """5 折 CV 的 OOF AUC（衡量模型真实判别力，不是训练集分数）。"""
    X = np.vstack([Xp, Xn])
    y = np.concatenate([np.ones(len(Xp)), np.zeros(len(Xn))])
    oof = np.zeros(len(y))
    for tr, te in KFold(5, shuffle=True, random_state=SEED).split(X):
        if len(np.unique(y[tr])) < 2:
            continue
        clf = LogisticRegression(C=1.0, max_iter=3000, class_weight="balanced")
        clf.fit(X[tr], y[tr])
        oof[te] = clf.decision_function(X[te])
    return roc_auc_score(y, oof)


def fit_best(Xp, Xn):
    """在 C 网格上挑 CV AUC 最好的 LR。"""
    best = None
    for C in C_GRID:
        a = cv_auc(Xp, Xn)
        clf = LogisticRegression(C=C, max_iter=5000, class_weight="balanced")
        clf.fit(np.vstack([Xp, Xn]),
                np.concatenate([np.ones(len(Xp)), np.zeros(len(Xn))]))
        if best is None or a > best[0]:
            best = (a, C, clf)
    return best


def main():
    t0 = datetime.now()
    rid, cks, side, feats, ck_person, nface, ck_lib = load()
    log(f"加载 {len(rid)} 张合格脸")
    idx09 = np.array([i for i in range(len(cks)) if ck_lib.get(cks[i]) == "09"], dtype=int)

    kid0 = single_face_idx(cks, side, nface, ck_person, KID_ARCH)
    adu0 = single_face_idx(cks, side, nface, ck_person, ADULT_ARCH)
    hold = single_face_idx(cks, side, nface, ck_person, {HOLDOUT})
    log(f"种子：幼童 {len(kid0)}（乐仔/小时候单脸） 成人 {len(adu0)} "
        f"held-out {HOLDOUT} {len(hold)}")

    # -------- 特征变体择优 --------
    fname, auc = None, -1
    for fn, X in feats.items():
        a = cv_auc(X[kid0], X[adu0])
        log(f"  特征 {fn:<6} 种子 CV AUC = {a:.4f}")
        if a > auc:
            fname, auc = fn, a
    X = feats[fname]
    log(f"选用特征：{fname}")

    pos, neg = set(kid0.tolist()), set(adu0.tolist())
    for r in range(BOOT_ROUNDS):
        log(f"\n--- 自举第 {r + 1}/{BOOT_ROUNDS} 轮 ---")
        pi, ni = np.array(sorted(pos)), np.array(sorted(neg))
        a, C, clf = fit_best(X[pi], X[ni])
        log(f"  正 {len(pi)} 负 {len(ni)} → CV AUC {a:.4f} (C={C})")
        if r == BOOT_ROUNDS - 1:
            break
        # 给 09 班级库打分：高分=幼童，低分=成人（老师/家长）
        s09 = clf.decision_function(X[idx09])
        order = idx09[np.argsort(-s09)]
        seen, top = set(), []
        for i in order:                       # 每张照片最多 1 张 → 强制个体多样性
            if cks[i] in seen:
                continue
            seen.add(cks[i])
            top.append(i)
            if len(top) >= N_09_TOP:
                break
        bot = list(idx09[np.argsort(s09)][:N_09_BOT])
        pos |= set(int(i) for i in top)
        neg |= set(int(i) for i in bot)
        log(f"  09 取高分幼童 {len(top)} + 低分成人 {len(bot)}")

    # -------- 定稿 --------
    pi, ni = np.array(sorted(pos)), np.array(sorted(neg))
    a, C, clf = fit_best(X[pi], X[ni])
    p_all = clf.predict_proba(X)[:, 1]

    log(f"\n最终模型 CV AUC = {a:.4f}  正 {len(pi)} 负 {len(ni)}")
    # held-out 泛化验证
    if len(hold):
        ph = p_all[hold]
        log(f"  held-out「{HOLDOUT}」判为幼童的比例 = {float((ph >= 0.5).mean()):.1%} "
            f"（中位概率 {np.median(ph):.2f}）—— 未在训练中出现过")
    pk = p_all[list(kid0)]
    pa = p_all[list(adu0)]
    log(f"  种子自洽：幼童 {float((pk >= 0.5).mean()):.1%} / 成人判幼童 {float((pa >= 0.5).mean()):.1%}")
    for name, idx in (("09 班级库", idx09),
                      ("07/08 家庭库", np.array([i for i in range(len(cks))
                                                 if ck_lib.get(cks[i]) in ("07", "08")]))):
        if len(idx):
            log(f"  {name}：幼童占比 {float((p_all[idx] >= 0.5).mean()):.1%}")

    # -------- 落库 --------
    hi, lo = 0.85, 0.15     # 只在高置信区间给出确定标签，中间为「未知」
    con = sqlite3.connect(LIB_DB)
    ensure_tables(con)
    con.execute("DELETE FROM face_attrs")
    con.executemany(
        "INSERT INTO face_attrs(face_rid,content_key,side,det,p_child) VALUES(?,?,?,?,?)",
        [(int(rid[i]), str(cks[i]), int(side[i]), None, float(p_all[i]))
         for i in range(len(rid))])
    # 照片级聚合特征
    con.execute("DELETE FROM photo_attrs")
    by_ck = {}
    for i in range(len(rid)):
        by_ck.setdefault(cks[i], []).append(i)
    rows = []
    for ck, lst in by_ck.items():
        ps = [p_all[i] for i in lst]
        nc = sum(1 for p in ps if p >= hi)
        na = sum(1 for p in ps if p <= lo)
        nu = len(ps) - nc - na
        ms = max(int(side[i]) for i in lst)
        nb = sum(1 for i in lst if side[i] >= 64)
        n = len(lst)
        rows.append((str(ck), ck_lib.get(ck), n, nb, ms, nc, na, nu,
                     1 if nc > 0 else 0,
                     "single" if n == 1 else ("couple" if n == 2 else "group")))
    con.executemany(
        "INSERT INTO photo_attrs(content_key,lib,face_count,n_big,max_side,"
        "n_child,n_adult,n_unknown,has_kid,group_size) VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    log(f"  落库 face_attrs {len(rid)} 行 / photo_attrs {len(rows)} 行")
    con.close()

    MODEL_JSON.write_text(json.dumps({
        "kind": "lr", "feature": fname, "C": C, "cv_auc": round(float(a), 4),
        "coef": clf.coef_[0].astype(float).tolist(),
        "intercept": float(clf.intercept_[0]),
        "thresholds": {"child": hi, "adult": lo},
        "n_pos": int(len(pi)), "n_neg": int(len(ni)),
        "note": "genderage.onnx 在本库验证不可用(幼童/成人AUC 0.625)，故自训",
        "trained_at": datetime.now().isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"模型写入 {MODEL_JSON}  用时 {datetime.now() - t0}")


if __name__ == "__main__":
    main()
