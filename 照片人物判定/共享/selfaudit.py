#!/usr/bin/env python3
"""自审闭环：出图 → 我看图判真值 → 落库 → 反推阈值/看精度 → 迭代。

为什么不用 18_照片分组 当真值：那是**照片级**标注（合影归在某个人名下），
一张合影里的旁人也被算成"正样本"，当评测真值会系统性失真。
唯一可靠的**脸级**真值是：我自己把脸裁出来一张张看。

所以本工具的三步：
  sheet   给出「锚点行 + 待判行」的对比拼图（锚点=单脸照片，必是本人）
  label   我读完图后，按编号把判断写进去（1=是 / 0=不是 / 2=存疑）
  eval    把 verdicts 与模型分对齐 → 精度随排名的曲线 + 建议阈值 + 漏在哪

verdicts 的稳定键 = content_key + 归一化人脸中心(cx,cy)。
content_key 是 md5(size:头部64K)，检测器重扫也不变；归一化中心只随检测框微动，
eval 时用最近邻(容差 0.06)重新吸附，跨重扫可用。

用法:
  python selfaudit.py sheet 乐仔 --pool 09 --band 0 24          # 09库排名0-24
  python selfaudit.py sheet 乐仔 --pool 09 --band 24 72 --cols 8
  python selfaudit.py label "_audit/pending_乐仔_09_0-24.jsonl" --labels "1,1,0,2,1,1,1,0"
  python selfaudit.py eval 乐仔
  python selfaudit.py eval                                     # 全部人
"""
import os
import sys
import json
import time
import sqlite3
import argparse
import collections

os.nice(10)
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench_ens as B
from audit import crop_face, make_sheet, full_scores

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
LIB_DB = f"{ROOT}/19_统一相册库/library.db"
OUTDIR = f"{ROOT}/照片人物判定/_audit"
VERDICTS = f"{OUTDIR}/verdicts.jsonl"
# render_tagged 批量人眼确认批次（**只有正样本**，统计时必须与逐张审核分开）
EYE_SRC = "eye_20260919"
FONT = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"
TOL = 0.06          # 归一化中心匹配容差（跨重扫吸附）
# 18 归档里的目录名 ≠ 身份：乐仔小时候就是乐仔本人（幼年期）
ALIAS = {"乐仔小时候": "乐仔", "妈妈小时候": "妈妈", "艳艳小时候": "艳艳"}


def canon(who):
    return ALIAS.get(who, who)


def canon_set(s):
    return {canon(w) for w in s}


def font(sz):
    try:
        return ImageFont.truetype(FONT, sz)
    except Exception:
        return ImageFont.load_default()


# ------------------------------------------------------------------ 缓存模型分
ENS_PKL = f"{ROOT}/照片人物判定/per_person_ens.pkl"


def prod_ens_scores(person, D):
    """用**即将部署的那套集成模型**打分（per_person_ens.pkl）。

    为什么要跟生产共用：如果审核用的是 bench_ens 的实验版、上线的是另一个模型，
    我肉眼审出来的结论就不能代表线上行为（"审的不是要发货的那个"）。
    走 ens_models.ens_prep/ens_score_one，与 apply_per_person 完全同路径。
    """
    import pickle
    import sqlite3
    import ens_models as EM
    if not os.path.exists(ENS_PKL):
        return None
    bundle = pickle.load(open(ENS_PKL, "rb"))
    if person not in bundle:
        return None
    con = sqlite3.connect(f"{ROOT}/19_统一相册库/library.db")
    ck18 = {}
    for rel, c in con.execute("select rel, content_key from files where lib='18'"):
        ck18.setdefault(c, set()).add(canon(rel.split("/")[0]))
    con.close()
    prep = EM.ens_prep(D["ck"], D["feats"], ck18, EM.IDENT)
    z, pos_idx = EM.ens_score_one(bundle[person], prep, D["ck"], person,
                                  D["feats"], D["det"], D["box"], EM.IDENT)
    print(f"  [ens] {person}: 归档单脸 {len(pos_idx)} · 子模型 {len(bundle[person]['models'])}")
    return z


def get_scores(person, D, engine=None):
    """全库模型分（带磁盘缓存：重扫期间反复出图不必重训）。

    engine: 'ens'=生产集成（默认，与 apply_per_person --engine ens 同路径）
            'bench'=bench_ens 实验版（旧行为）
    """
    engine = engine or os.environ.get("SELFAUDIT_ENGINE", "ens")
    cf = f"{OUTDIR}/_scores_{person}_{engine}.npz"
    if os.path.exists(cf):
        z = np.load(cf, allow_pickle=True)
        if len(z["ens"]) == len(D["ck"]) and str(z["sig"]) == B.NPZ:
            return z["ens"], z["lr"]
    lr = None
    if engine == "ens":
        ens = prod_ens_scores(person, D)
    else:
        ens = None
    if ens is None:
        if engine == "ens":
            print(f"  [ens] 回退到 bench 实验版（{person} 不在 {ENS_PKL}？）")
        R = full_scores(person, D)
        ens, lr = R["ens"], R["lr"]
    if lr is None:
        # 单 LR 对照列不可得时复制一份，保证下游（少数按 lr 排序的诊断）不炸
        lr = np.asarray(ens)
    np.savez_compressed(cf, ens=ens, lr=lr, sig=B.NPZ)
    return ens, lr


def _cxy_for(D, file_of, dims, ck, lib, box):
    """单个脸的归一化中心（跨重扫稳定的身份键的一半）。尺寸读一次就缓存。"""
    if ck not in dims:
        p = file_of.get((lib, ck))
        dims[ck] = None
        if p:
            try:
                im = cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)
                dims[ck] = list(im.shape[:2]) if im is not None else None
            except Exception:
                dims[ck] = None
    sh = dims.get(ck)
    if not sh:
        return None, None
    ih, iw = sh
    return (round(float((box[0] + box[2] / 2) / iw), 4),
            round(float((box[1] + box[3] / 2) / ih), 4))


def norm_centers(D, file_of):
    """（旧版，保留兼容）每个脸在所属照片里的归一化中心。"""
    cf = f"{OUTDIR}/_dims.json"
    dims = {}
    if os.path.exists(cf):
        try:
            dims = json.load(open(cf))
        except Exception:
            dims = {}
    cx = np.full(len(D["ck"]), np.nan, dtype=np.float64)
    cy = np.full(len(D["ck"]), np.nan, dtype=np.float64)
    dirty = False
    for i, (l, c) in enumerate(zip(D["lib"], D["ck"])):
        if c in dims:
            sh = dims[c]
        else:
            p = file_of.get((l, c))
            if not p:
                dims[c] = None; dirty = True
                continue
            try:
                im = cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)
                dims[c] = list(im.shape[:2]) if im is not None else None
            except Exception:
                dims[c] = None
            dirty = True
            if len(dims) % 500 == 0:
                json.dump(dims, open(cf, "w"))
        sh = dims.get(c)
        if not sh:
            continue
        ih, iw = sh
        bx = D["box"][i]
        cx[i] = (bx[0] + bx[2] / 2) / iw
        cy[i] = (bx[1] + bx[3] / 2) / ih
    if dirty:
        json.dump(dims, open(cf, "w"))
    return cx, cy


# ------------------------------------------------------------------ 1. 出图
def cmd_sheet(a):
    D = B.load(a.src)
    con = sqlite3.connect(LIB_DB)
    file_of = {(l, c): p for l, c, p in
               con.execute("select lib, content_key, path from files")}
    con.close()
    ens, lr = get_scores(a.person, D)
    dims = {}
    df = f"{OUTDIR}/_dims.json"
    if os.path.exists(df):
        try:
            dims = json.load(open(df))
        except Exception:
            dims = {}

    pool = {"09": [i for i in range(len(D["ck"])) if D["lib"][i] == "09"],
            "home": [i for i in range(len(D["ck"])) if D["lib"][i] in ("07", "08")],
            "18": [i for i in range(len(D["ck"])) if D["lib"][i] == "18"],
            "all": list(range(len(D["ck"])))}
    pool = np.array([i for i in pool[a.pool] if np.isfinite(ens[i])], dtype=int)
    order = pool[np.argsort(-ens[pool])]                  # 池内按分数降序
    lo, hi = a.band
    sel = order[lo:min(hi, len(order))]

    # 锚点：本人归档里的单脸照片（整张只有一个脸 → 必是本人）
    nface = collections.Counter(D["ck"])
    mine = {c for c, p in D["ck_person"].items() if p == a.person}
    anchors = [i for i, c in enumerate(D["ck"])
               if c in mine and nface[c] == 1]
    rng = np.random.default_rng(5)
    na = min(a.na, len(anchors))
    anch = [anchors[i] for i in rng.choice(len(anchors), size=na, replace=False)] if na else []

    items, recs = [], []
    for i in anch:
        p = file_of.get((D["lib"][i], D["ck"][i]))
        if not p:
            continue
        im = crop_face(p, D["box"][i], size=300, pad=0.6)
        if im is not None:
            items.append((im, f"锚{len(items)}"))
    nanc = len(items)
    for k, i in enumerate(sel):
        p = file_of.get((D["lib"][i], D["ck"][i]))
        if not p:
            continue
        im = crop_face(p, D["box"][i], size=300, pad=0.6)
        if im is None:
            continue
        j = len(items) - nanc                            # 待判编号从 0 起
        items.append((im, f"#{j} E={ens[i]:.2f} {D['lib'][i]} r{lo + k}"))
        ncx, ncy = _cxy_for(D, file_of, dims, str(D["ck"][i]), str(D["lib"][i]),
                            D["box"][i])
        recs.append(dict(no=j, person=a.person, ck=str(D["ck"][i]), lib=str(D["lib"][i]),
                         ens=float(ens[i]), lr=float(lr[i]),
                         box=[float(v) for v in D["box"][i]],
                         cx=ncx, cy=ncy,
                         rank=int(lo + k), src=os.path.basename(B.NPZ)))

    tag = f"{a.person}_{a.pool}_{lo}-{lo + len(recs)}"
    g = Image.new("RGB", (a.cols * 300, ((len(items) + a.cols - 1) // a.cols) * 330 + 0),
                  (24, 24, 28))
    d = ImageDraw.Draw(g)
    f = font(20)
    for k, (im, cap) in enumerate(items):
        r, c = divmod(k, a.cols)
        X, Y = c * 300, r * 330
        g.paste(im, (X, Y + 30))
        d.rectangle([X, Y, X + 300, Y + 29],
                    fill=(58, 44, 20) if k < nanc else (40, 40, 48))
        d.text((X + 4, Y + 4), cap, fill=(255, 214, 102) if k < nanc else (240, 240, 248),
               font=f)
    sheet = f"{OUTDIR}/cmp_{tag}.jpg"
    g.save(sheet, quality=93)
    pend = f"{OUTDIR}/pending_{tag}.jsonl"
    with open(pend, "w") as fh:
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    try:
        json.dump(dims, open(f"{OUTDIR}/_dims.json", "w"))
    except Exception:
        pass
    print(f"锚点 {nanc} 张 + 待判 {len(recs)} 张  →  {sheet}")
    print(f"待判清单 → {pend}")
    print("判完后: python selfaudit.py label \"%s\" --labels \"1,0,2,...\"" % pend)


# ------------------------------------------------------------------ 1b. 带上下文的图
def cmd_ctx(a):
    """每个候选一行：左=大裁脸，右=整张原图（红框标出该脸）。
    只看 300px 裁脸，在「全班同款黄园服」下不足以判同一个人；
    看原图能知道这张脸在照片里的位置、旁边站着谁、是不是孤立的单脸照。"""
    D = B.load(a.src)
    con = sqlite3.connect(LIB_DB)
    file_of = {(l, c): p for l, c, p in
               con.execute("select lib, content_key, path from files")}
    con.close()
    ens, lr = get_scores(a.person, D)
    pool = np.array([i for i in range(len(D["ck"]))
                     if D["lib"][i] == a.pool and np.isfinite(ens[i])], dtype=int)
    order = pool[np.argsort(-ens[pool])]
    lo, hi = a.band
    sel = order[lo:min(hi, len(order))]

    CS, PS, H = 260, 620, 268
    g = Image.new("RGB", (CS + PS, H * len(sel) + 34), (24, 24, 28))
    d = ImageDraw.Draw(g)
    f = font(19)
    d.text((6, 6), f"{a.person} · {a.pool} 库 rank {lo}-{lo+len(sel)}："
                   f"左=裁脸 右=原图(红框)", fill=(255, 214, 102), font=font(21))
    recs = []
    for k, i in enumerate(sel):
        p = file_of.get((D["lib"][i], D["ck"][i]))
        if not p:
            continue
        Y = 34 + k * H
        face = crop_face(p, D["box"][i], size=CS, pad=0.7)
        if face is not None:
            g.paste(face, (0, Y))
        try:
            im = cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)
        except Exception:
            im = None
        if im is not None:
            ih, iw = im.shape[:2]
            sc = PS / iw
            th = cv2.resize(im, (PS, max(1, int(ih * sc))), interpolation=cv2.INTER_AREA)
            th = th[:H] if th.shape[0] > H else th
            bx = D["box"][i]
            x0, y0 = int(bx[0] * sc), int(bx[1] * sc)
            x1, y1 = int((bx[0] + bx[2]) * sc), int((bx[1] + bx[3]) * sc)
            cv2.rectangle(th, (x0, y0), (x1, y1), (60, 60, 255), 3)
            g.paste(Image.fromarray(cv2.cvtColor(th, cv2.COLOR_BGR2RGB)), (CS, Y))
        d.rectangle([0, Y, CS + PS, Y + 27], fill=(40, 40, 48))
        d.text((CS + 6, Y + 4), f"r{lo+k}  E={ens[i]:.2f}",
               fill=(240, 240, 248), font=f)
        bx = D["box"][i]
        ncx = ncy = None
        if im is not None:
            ncx = round(float((bx[0] + bx[2] / 2) / iw), 4)
            ncy = round(float((bx[1] + bx[3] / 2) / ih), 4)
        recs.append(dict(no=k, person=a.person, ck=str(D["ck"][i]), lib=str(D["lib"][i]),
                         ens=float(ens[i]), lr=float(lr[i]),
                         box=[float(v) for v in bx],
                         cx=ncx, cy=ncy, rank=int(lo + k),
                         src=os.path.basename(B.NPZ)))
    tag = f"{a.person}_{a.pool}_ctx_{lo}-{lo+len(recs)}"
    out = f"{OUTDIR}/ctx_{tag}.jpg"
    g.save(out, quality=90)
    pend = f"{OUTDIR}/pending_{tag}.jsonl"
    with open(pend, "w") as fh:
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(recs)} 行 → {out}\n待判清单 → {pend}")


# ------------------------------------------------------------------ 1c. 模型与人工归档冲突
def cmd_errs(a):
    """模型判正、但 18 人工归档把这张照片归在**别人**名下 —— 模型真错的候选。
    这是最该看的一批：既不是训练集内的重复，也不靠我的肉眼先验。"""
    D = B.load(a.src)
    con = sqlite3.connect(LIB_DB)
    file_of = {(l, c): p for l, c, p in
               con.execute("select lib, content_key, path from files")}
    ck18 = collections.defaultdict(set)
    for rel, c in con.execute("select rel, content_key from files where lib='18'"):
        ck18[c].add(rel.split("/")[0])
    con.close()
    ens, lr = get_scores(a.person, D)
    cand = []
    seen = set()
    for i in range(len(D["ck"])):
        c = str(D["ck"][i])
        if ens[i] <= a.t or c in seen:
            continue
        who = canon_set(ck18[c]) if c in ck18 else None
        if who and a.person not in who:
            seen.add(c)
            cand.append((i, ",".join(sorted(who))))
    cand.sort(key=lambda z: -ens[z[0]])
    cand = cand[:a.n]
    print(f"冲突样本 {len(cand)} 张（模型>{a.t}，18 归到别人）")
    if not cand:
        return
    CS, PS, H = 260, 620, 268
    g = Image.new("RGB", (CS + PS, 34 + H * len(cand)), (24, 24, 28))
    d = ImageDraw.Draw(g)
    d.text((6, 6), f"{a.person} · 模型判正×18归档他人（模型错的最可能处）"
                   f"  E>{a.t}", fill=(255, 120, 120), font=font(21))
    for k, (i, who) in enumerate(cand):
        Y = 34 + k * H
        p = file_of.get((D["lib"][i], D["ck"][i]))
        if not p:
            continue
        face = crop_face(p, D["box"][i], size=CS, pad=0.7)
        if face is not None:
            g.paste(face, (0, Y))
        try:
            im = cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)
        except Exception:
            im = None
        if im is not None:
            ih, iw = im.shape[:2]
            sc = PS / iw
            th = cv2.resize(im, (PS, max(1, int(ih * sc))), interpolation=cv2.INTER_AREA)[:H]
            bx = D["box"][i]
            cv2.rectangle(th, (int(bx[0] * sc), int(bx[1] * sc)),
                          (int((bx[0] + bx[2]) * sc), int((bx[1] + bx[3]) * sc)),
                          (60, 60, 255), 3)
            g.paste(Image.fromarray(cv2.cvtColor(th, cv2.COLOR_BGR2RGB)), (CS, Y))
        d.rectangle([0, Y, CS + PS, Y + 27], fill=(70, 24, 24))
        d.text((CS + 6, Y + 4), f"E={ens[i]:.2f} 归档={who} {D['lib'][i]}",
               fill=(255, 200, 200), font=font(19))
    out = f"{OUTDIR}/errs_{a.person}_t{a.t}.jpg"
    g.save(out, quality=90)
    print(f"→ {out}")


# ------------------------------------------------------------------ 2. 落真值
def key_of(rec):
    return f"{rec['ck']}|{(rec.get('cx') or 0):.2f}|{(rec.get('cy') or 0):.2f}"


def cmd_label(a):
    recs = [json.loads(l) for l in open(a.pending) if l.strip()]
    labs = [int(x) for x in a.labels.replace(" ", "").split(",") if x != ""]
    if len(labs) < len(recs):
        print(f"！标签 {len(labs)} < 待判 {len(recs)}，只落前 {len(labs)} 条")
    old = {}
    if os.path.exists(VERDICTS):
        for l in open(VERDICTS):
            if l.strip():
                r = json.loads(l)
                old[r["key"]] = r
    n = 0
    with open(VERDICTS, "a") as fh:
        for r, v in zip(recs, labs):
            r = dict(r)
            r["verdict"] = v                     # 1=是本人 0=不是 2=存疑
            r["key"] = key_of(r)
            r["ts"] = time.strftime("%Y-%m-%d %H:%M")
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            old[r["key"]] = r
            n += 1
    print(f"写入 {n} 条 → {VERDICTS}（累计 {len(old)} 个唯一键）")


# ------------------------------------------------------------------ 3. 评估
def latest(a=None):
    """verdicts.jsonl 后写覆盖前写，返回 key→rec。"""
    out = {}
    if os.path.exists(VERDICTS):
        for l in open(VERDICTS):
            if l.strip():
                r = json.loads(l)
                out[r["key"]] = r
    return out


def load_any(a=None):
    """当前库的脸 → 模型分的对齐表（用于把 verdicts 吸附回当前 D）。"""
    return None


def cmd_eval(a):
    V = latest()
    if a.person:
        V = {k: r for k, r in V.items() if r.get("person") == a.person}
    if not V:
        print("还没有 verdicts"); return
    # ⚠️ 金标批次（render_tagged 批量确认）**全是正样本**，必须排除：
    # 混进来会让 eval 输出"精度 100%"，而那是同义反复（分母里没有负样本）。
    # 金标的分位阈值由 gold_calib.py 单独负责。
    n_gold = sum(1 for r in V.values() if r.get("source") == EYE_SRC)
    V = {k: r for k, r in V.items() if r.get("source") != EYE_SRC}
    if n_gold:
        print(f"（已排除 {n_gold} 条批量金标 —— 只有正样本，见 gold_calib.py）")
    per = collections.defaultdict(list)
    for r in V.values():
        per[r.get("person") or "?"].append(r)
    print(f"{'人':<10}{'标注':>6}{'是':>5}{'否':>5}{'存疑':>6}"
          f"{'Top1%精度':>11}{'Top5%精度':>11}{'Top10%精度':>12}")
    for p, rows in sorted(per.items()):
        rows = [r for r in rows if r["verdict"] in (0, 1)]
        if not rows:
            continue
        rows.sort(key=lambda r: -r["ens"])
        n = len(rows)
        cells = []
        for frac in (0.01, 0.05, 0.10):
            k = max(1, int(round(n * frac)))
            top = rows[:k]
            prec = sum(r["verdict"] for r in top) / len(top)
            cells.append(prec)
        ny = sum(r["verdict"] for r in rows)
        print(f"{p:<10}{n:>6}{ny:>5}{n - ny:>5}"
              f"{sum(1 for r in rows if r['verdict'] == 2):>6}"
              f"{cells[0]:>10.1%}{cells[1]:>10.1%}{cells[2]:>11.1%}")
    # 阈值建议：在人工真值上找「精度≥目标」的最低分数
    rows = [r for r in V.values() if r["verdict"] in (0, 1)]
    if len(rows) >= 20:
        rows.sort(key=lambda r: -r["ens"])
        print("\n阈值-精度-召回（人工真值）：")
        for tgt in (0.99, 0.97, 0.95, 0.90):
            cut = None
            for k in range(1, len(rows) + 1):
                if sum(x["verdict"] for x in rows[:k]) / k >= tgt:
                    cut = k
            if cut:
                print(f"  精度≥{tgt:.0%}: 阈值={rows[cut-1]['ens']:+.3f}  "
                      f"命中 {sum(x['verdict'] for x in rows[:cut])} 张 / 前 {cut} 名")
            else:
                print(f"  精度≥{tgt:.0%}: 人工样本里达不到（正样本太少/分数重叠）")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sheet"); s.add_argument("person")
    s.add_argument("--pool", default="09", choices=["09", "home", "18", "all"])
    s.add_argument("--band", type=int, nargs=2, default=[0, 24], metavar=("LO", "HI"))
    s.add_argument("--cols", type=int, default=6)
    s.add_argument("--na", type=int, default=6)
    s.add_argument("--src", default=B.NPZ)
    s.set_defaults(fn=cmd_sheet)
    l = sub.add_parser("label"); l.add_argument("pending")
    l.add_argument("--labels", required=True); l.set_defaults(fn=cmd_label)
    c = sub.add_parser("ctx"); c.add_argument("person")
    c.add_argument("--pool", default="09", choices=["09", "home", "18", "all"])
    c.add_argument("--band", type=int, nargs=2, default=[0, 12], metavar=("LO", "HI"))
    c.add_argument("--src", default=B.NPZ); c.set_defaults(fn=cmd_ctx)
    r = sub.add_parser("errs"); r.add_argument("person")
    r.add_argument("--t", type=float, default=0.5)
    r.add_argument("--n", type=int, default=12)
    r.add_argument("--src", default=B.NPZ); r.set_defaults(fn=cmd_errs)
    e = sub.add_parser("eval"); e.add_argument("person", nargs="?", default=None)
    e.set_defaults(fn=cmd_eval)
    a = ap.parse_args()
    os.makedirs(OUTDIR, exist_ok=True)
    a.fn(a)


if __name__ == "__main__":
    main()
