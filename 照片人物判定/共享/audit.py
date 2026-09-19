#!/usr/bin/env python3
"""抽样审核台：把模型「最拿不准/最值得怀疑」的脸裁成拼图，供人工（我）看图定真值。

为什么必须有这一步：AUC/TPR 都是在「负样本池」上算的，而负样本池里混着大量
本该判正、只是没被归档的照片（归档不全）。数字会集体虚高或集体虚低，
只有看图才知道模型到底在对什么、错什么。

四类抽样（每类都是信息量最高的地方）:
  A 新增命中：集成判正、现役 LR 判负  —— 集成是不是真的多找对了人
  B 疑似误报：集成判正但照片不在本人归档里 —— 最可能把陌生人认成家人
  C 归档漏检：照片在本人归档里，集成却判负 —— 召回掉了多少
  D 高分随机：集成分数 top 区随机抽 —— 看精度上界

输出：
  _audit/<person>_<mode>.jpg   拼图（带大号编号）
  _audit/<person>_<mode>.json  每个编号的元数据（ck/face_idx/分数/lib/框）
看图后把结论写进 _audit/verdicts.jsonl，再跑 audit.py --report 汇总成指标。

用法:
  python audit.py 乐仔            # 四类各出一张拼图
  python audit.py 乐仔 --mode A --n 30
  python audit.py --report        # 汇总已标注的真值 vs 模型分
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
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench_ens as B

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
LIB_DB = f"{ROOT}/19_统一相册库/library.db"
OUTDIR = f"{ROOT}/照片人物判定/_audit"
# 坑：macOS 新版已无 /System/Library/Fonts/PingFang.ttc。写死它会静默回退到
# ImageFont.load_default()，中文标题全变乱码（拼图看上去"渲染成功"，其实没法读）。
FONT_CANDS = [
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",   # 有 CJK，实测可用
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]
_FONT_PATH = next((p for p in FONT_CANDS if os.path.exists(p)), None)


def font(sz):
    if _FONT_PATH:
        try:
            return ImageFont.truetype(_FONT_PATH, sz)
        except Exception:
            pass
    return ImageFont.load_default()


def imread_any(path):
    """读图：**必须兼容 HEIC**。踩过的坑：cv2.imdecode 对 .HEIC 返回 None，
    会把整个簇/样本静默丢空（08 乐仔库大量 HEIC，簇探针里 C18(762张) 整簇渲染失败才发现）。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".heic", ".heif"):
        try:
            import pillow_heif
            from PIL import Image as _I
            pillow_heif.register_heif_opener()
            with _I.open(path) as im:
                return cv2.cvtColor(np.array(im.convert("RGB")), cv2.COLOR_RGB2BGR)
        except Exception:
            return None
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


def crop_face(path, box, size=280, pad=0.5):
    """裁脸。三个踩过的坑：
       1. 必须用 (faces.lib, content_key) 对应的物理文件，primary 副本尺寸不同、坐标不通用
       2. 不能 exif_transpose —— 检测用 cv2.imread 原始方向，转正会错位
       3. HEIC 必须走 pillow_heif（见 imread_any）"""
    x, y, w, h = box
    img = imread_any(path)
    if img is None:
        return None
    H, W = img.shape[:2]
    px = int(max(w, h) * pad)
    x0, y0 = max(0, int(x - px)), max(0, int(y - px))
    x1, y1 = min(W, int(x + w + px)), min(H, int(y + h + px))
    c = img[y0:y1, x0:x1]
    if c.size == 0:
        return None
    c = cv2.resize(c, (size, size), interpolation=cv2.INTER_AREA)
    return Image.fromarray(cv2.cvtColor(c, cv2.COLOR_BGR2RGB))


def crop_ctx(path, box, size=420, thick_ratio=0.0025):
    """**原图 + 红框标出模型选中的那张脸**（不裁）。

    为什么必须有这个模式：`crop_face` 带 50% padding，一张小脸旁边的成人会挤进画面
    —— 于是"我看到一个成年男性"既可能是**模型选错了脸**，也可能只是**邻座被裁剪带进来**。
    第 06 轮在 七月/爸爸 的 bottom 档上正是被这个歧义卡住（p=0.79 那张到底是
    "把成年男性判成七月" 还是 "七月被抱起、成人在旁边"）。
    看原图 + 框能一眼分开这两种情况。
    ⚠️ 同样禁止 exif_transpose（检测坐标用的是原始方向）。"""
    img = imread_any(path)
    if img is None:
        return None
    H, W = img.shape[:2]
    x, y, w, h = box
    t = max(2, int(max(W, H) * thick_ratio))
    cv2.rectangle(img, (int(x), int(y)), (int(x + w), int(y + h)), (0, 0, 255), t)
    s = size / max(H, W)
    nw, nh = max(1, int(W * s)), max(1, int(H * s))
    im2 = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
    canvas = np.full((size, size, 3), 24, np.uint8)
    y0, x0 = (size - nh) // 2, (size - nw) // 2
    canvas[y0:y0 + nh, x0:x0 + nw] = im2
    return Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB))


def make_sheet(items, cols, out, title=""):
    """items: [(PIL.Image, caption)]。编号画在左上角，大号白字+黑描边，保证可读。"""
    S = items[0][0].size[0]
    rows = (len(items) + cols - 1) // cols
    g = Image.new("RGB", (cols * S, rows * (S + 30) + (40 if title else 0)), (24, 24, 28))
    d = ImageDraw.Draw(g)
    f = font(22)
    if title:
        d.text((8, 8), title, fill=(255, 214, 102), font=font(26))
    off = 40 if title else 0
    for k, (im, cap) in enumerate(items):
        r, c = divmod(k, cols)
        X, Y = c * S, r * (S + 30) + off
        g.paste(im, (X, Y + 30))
        d.rectangle([X, Y, X + S, Y + 29], fill=(40, 40, 48))
        txt = f"#{k} {cap}"
        d.text((X + 4, Y + 3), txt, fill=(240, 240, 248), font=f)
    g.save(out, quality=92)
    return out


# ------------------------------------------------------------------ 打全库分
def full_scores(person, D, specs=None):
    """在全量正/负集上训练每个子模型，然后对全库打分。返回 (子模型分数矩阵, idx, y)。"""
    specs = specs or B.base_specs()
    pos, neg = B.build_set(person, D)
    idx = np.concatenate([pos, neg])
    y = np.concatenate([np.ones(len(pos)), np.zeros(len(neg))])
    meta = np.hstack([D["meta_static"][idx], B.ctx_features(person, D, idx)])

    n = len(D["ck"])
    Sold = np.zeros((len(specs), n), dtype=np.float32)   # 全库（全量模型）
    Soof = np.zeros((len(specs), len(idx)), dtype=np.float64)  # 训练集 OOF
    skf = StratifiedKFold(B.N_FOLD, shuffle=True, random_state=0)
    ab = []
    for k, (nm, fkey, mk) in enumerate(specs):
        Xtr = D["feats"][fkey][idx]
        m = mk(Xtr.shape[1]); m.fit(Xtr, y)
        Sold[k] = B.score(m, D["feats"][fkey]).astype(np.float32)
        for tr, te in skf.split(Xtr, y):
            mm = mk(Xtr.shape[1]); mm.fit(Xtr[tr], y[tr])
            Soof[k, te] = B.score(mm, Xtr[te])
        ab.append(B.platt_fit(Soof[k], y))       # Platt 系数：训练/部署共用一套
    good = ~np.isnan(Soof[:, 0])
    # stacker 在「校准后」的 OOF 上训练
    Xs = np.nan_to_num(np.column_stack([np.vstack(
        [B.platt_apply(Soof[k], ab[k]) for k in np.where(good)[0]]).T, meta]), nan=0.0)
    st = LogisticRegression(C=1.0, max_iter=5000).fit(Xs, y)
    meta_all = np.hstack([D["meta_static"], B.ctx_features(person, D, np.arange(n))])
    xall = np.nan_to_num(np.column_stack([np.vstack(
        [B.platt_apply(Sold[k], ab[k]) for k in np.where(good)[0]]).T, meta_all]), nan=0.0)
    ens_full = st.decision_function(xall)
    lr = specs[2][2](D["feats"]["fused"].shape[1]).fit(D["feats"]["fused"][idx], y)
    lr_full = B.score(lr, D["feats"]["fused"])
    return dict(ens=ens_full, lr=lr_full, Sold=Sold, idx=idx, y=y,
                pos=pos, neg=neg, specs=specs, stacker=st, ab=ab)


def pick(person, D, R, mode, n, thr):
    ck = D["ck"]
    mine = {c for c, p in D["ck_person"].items() if p == person}
    ens, lr = R["ens"], R["lr"]
    cand = np.arange(len(ck))
    rng = np.random.default_rng(11)
    if mode == "A":        # 集成判正、LR 判负（集成新捞回来的）
        sel = cand[(ens > thr) & (lr < np.quantile(lr, 0.99))]
        return sel[np.argsort(-ens[sel])][:n]
    if mode == "B":        # 判正却不在归档，且来自 09 幼儿园班级库 ← 最危险的误报区
        sel = cand[(ens > thr) & (~np.isin(ck, list(mine)))
                   & (D["lib"] == "09")]
        if len(sel) == 0:
            return np.array([], dtype=int)
        sel = rng.choice(sel, size=min(n, len(sel)), replace=False)
        return sel[np.argsort(-ens[sel])]
    if mode == "E":        # 判正却不在归档，来自 07/08 家庭库 ← 多半是没归档到的真照片
        sel = cand[(ens > thr) & (~np.isin(ck, list(mine)))
                   & np.isin(D["lib"], ["07", "08"])]
        if len(sel) == 0:
            return np.array([], dtype=int)
        sel = rng.choice(sel, size=min(n, len(sel)), replace=False)
        return sel[np.argsort(-ens[sel])]
    if mode == "F":        # 09 幼儿园库 top-N（不看阈值）← 检验泛化的主战场
        sel = cand[D["lib"] == "09"]
        return sel[np.argsort(-ens[sel])][:n]
    if mode == "G":        # 07/08 家庭库 top-N（不看阈值）← 未归档的真照片应该在这里
        sel = cand[np.isin(D["lib"], ["07", "08"])]
        return sel[np.argsort(-ens[sel])][:n]
    if mode == "C":        # 归档照片里却判负 ← 注意：归档是照片级，旁人判负是对的
        sel = cand[np.isin(ck, list(mine)) & (ens < thr)]
        return sel[np.argsort(ens[sel])][:n]
    # D 高分随机
    sel = cand[np.argsort(-ens)[:max(n * 3, n)]]
    sel = rng.choice(sel, size=min(n, len(sel)), replace=False)
    return sel[np.argsort(-ens[sel])]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("person", nargs="?", default=None)
    ap.add_argument("--mode", default="ABCD")
    ap.add_argument("--n", type=int, default=24)
    ap.add_argument("--q", type=float, default=0.999,
                    help="部署阈值分位：0.999→全库约 36 张；0.99→约 360 张；0.97→约 1100 张")
    ap.add_argument("--src", default=B.NPZ)
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()

    os.makedirs(OUTDIR, exist_ok=True)
    if a.report:
        return report()

    t0 = time.time()
    D = B.load(a.src)
    con = sqlite3.connect(LIB_DB)
    file_of = {(l, c): p for l, c, p in
               con.execute("select lib, content_key, path from files")}
    con.close()
    print(f"加载 {len(D['ck'])} 张脸（{time.time() - t0:.0f}s）")

    for person in (a.person.split(",") if a.person else B.FAMILY):
        print(f"\n===== {person} =====")
        R = full_scores(person, D)
        thr = float(np.quantile(R["ens"], a.q))
        print(f"  阈值分位 {a.q} → {thr:.2f}  全库判正 {int((R['ens'] > thr).sum())} 张")
        for mode in a.mode:
            sel = pick(person, D, R, mode, a.n, thr)
            items, meta = [], []
            for i in sel:
                path = file_of.get((D["lib"][i], D["ck"][i]))
                if not path:
                    continue
                im = crop_face(path, D["box"][i])
                if im is None:
                    continue
                j = len(items)
                items.append((im, f"E={R['ens'][i]:.1f} L={R['lr'][i]:.2f} "
                                  f"{D['lib'][i]} {int(max(D['box'][i][2], D['box'][i][3]))}px"))
                # 存归一化中心（cx,cy）：检测器一换 box 就变，只有相对位置能跨重扫复用
                iw = ih = 0
                try:
                    _im = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
                    if _im is not None:
                        ih, iw = _im.shape[:2]
                except Exception:
                    pass
                bx = D["box"][i]
                meta.append(dict(no=j, ck=str(D["ck"][i]), face_idx=int(i),
                                 lib=str(D["lib"][i]), ens=float(R["ens"][i]),
                                 lr=float(R["lr"][i]),
                                 box=[float(v) for v in bx],
                                 cx=float((bx[0] + bx[2] / 2) / iw) if iw else None,
                                 cy=float((bx[1] + bx[3] / 2) / ih) if ih else None,
                                 iw=int(iw), ih=int(ih),
                                 path=path))
            if not items:
                print(f"  [{mode}] 无样本")
                continue
            tag = {"A": "A_集成新增命中", "B": "B_09疑似误报",
                   "C": "C_归档内判负", "D": "D_高分随机",
                   "E": "E_家庭库未归档", "F": "F_09库topN",
                   "G": "G_家庭库topN"}[mode]
            p = make_sheet(items, 6, f"{OUTDIR}/{person}_{tag}_q{a.q}.jpg",
                           title=f"{person} · {tag}  q={a.q} 阈值≈{thr:.2f}")
            json.dump(dict(person=person, mode=mode, thr=thr, items=meta),
                      open(f"{OUTDIR}/{person}_{tag}.json", "w"), ensure_ascii=False, indent=1)
            print(f"  [{mode}] {len(items)} 张 → {p}")
    print(f"\n总用时 {time.time() - t0:.0f}s")


def report():
    """汇总人工标注：verdicts.jsonl 每行 {person, no, ck, face_idx, verdict: 1/0/?}"""
    f = f"{OUTDIR}/verdicts.jsonl"
    if not os.path.exists(f):
        print("还没有标注"); return
    rows = [json.loads(l) for l in open(f) if l.strip()]
    per = collections.defaultdict(lambda: [0, 0, 0])
    for r in rows:
        v = r.get("verdict")
        k = 0 if v == 1 else (1 if v == 0 else 2)
        per[r["person"]][k] += 1
    print(f"{'人':<10}{'判对':>7}{'判错':>7}{'存疑':>7}{'准确率':>9}")
    for p, (ok, bad, un) in per.items():
        tot = ok + bad
        print(f"{p:<10}{ok:>7}{bad:>7}{un:>7}{(ok / tot if tot else 0):>9.1%}")
    print(f"\n共 {len(rows)} 条标注 → {f}")


if __name__ == "__main__":
    main()
