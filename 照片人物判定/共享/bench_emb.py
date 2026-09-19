"""嵌入模型基准：分进程评测，避免四个大模型同时驻留内存被杀。

背景：本库 07 子库人脸中位仅 19px，用户要求"用更高级的深度模型"，
      但换模型必须拿数据说话 —— 这里在同一批严格对齐的裁剪上横评：
        mbf       w600k_mbf  MobileFaceNet          现役轻量
        r50       w600k_r50  ArcFace R50            现役主力
        glintr100 glintr100  antelopev2             InsightFace 更强 CPU 包
        adaface   AdaFace IR-101 WebFace12M         CVPR22 Oral，质量自适应 margin
      另测多模型拼接融合（误差去相关，常比单模型稳）。

三个子命令（必须按序跑，因为每个模型单独进程）：
    prep   检测+对齐，缓存 112x112 裁剪
    run    对指定模型提嵌入（原始 + 降质 16/32）
    eval   汇总对比表

用法：
    python bench_emb.py prep
    python bench_emb.py run mbf / r50 / glintr100 / adaface / all
    python bench_emb.py eval
"""
import os
import sys
import json
import sqlite3
import collections
import numpy as np

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
LIB_DB = f"{ROOT}/19_统一相册库/library.db"
OLD_MODELS = f"{ROOT}/07_相册/相册&视频备份/_photo_index/models"
INSPATH = os.path.expanduser("~/.insightface/models")
OUT = os.path.join(ROOT, "照片人物判定", "_bench")
CACHE = os.path.join(OUT, "crops.npy")
META = os.path.join(OUT, "meta.json")

MODELS = {
    "mbf": f"{OLD_MODELS}/buffalo_s/w600k_mbf.onnx",
    "r50": f"{OLD_MODELS}/buffalo_l/w600k_r50.onnx",
    "glintr100": os.path.join(INSPATH, "antelopev2", "glintr100.onnx"),
    "adaface": os.path.join(ROOT, "照片人物判定", "models", "adaface_ir_101.onnx"),
}
PERSONS = ["乐仔", "艳艳", "妈妈", "我", "七月", "爸爸", "乐仔小时候"]
FOREIGN = "陌生人09"
BUCKETS = (25, 60, 140)
IOU_MIN = 0.3
N_EACH = 30
DOWNSCALE = 1280        # 检测前把长边压到这个尺寸，原图 4000px 会让 SCRFD 慢到跑不完
DEGRADES = (0, 16, 32)  # 0 = 不降质


def bucket_of(side):
    for b in BUCKETS:
        if side < b:
            return f"<{b}px"
    return f">={BUCKETS[-1]}px"


def l2n(v):
    v = np.asarray(v, dtype=np.float32)
    if v.ndim == 1:
        n = np.linalg.norm(v)
        return v / n if n > 1e-9 else v
    return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)


def iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    return inter / (aw * ah + bw * bh - inter)


# ----------------------------- prep -----------------------------
def cmd_prep():
    os.makedirs(OUT, exist_ok=True)
    con = sqlite3.connect(LIB_DB)
    ck_person = {}
    for rel, ck in con.execute("select rel, content_key from files where lib='18'"):
        ck_person.setdefault(ck, rel.split("/")[0])
    rows = con.execute(
        "select rowid, content_key, x, y, w, h, lib, det_score from faces").fetchall()
    paths = {}
    for lib, ck, pa in con.execute("select lib, content_key, path from files"):
        k = (lib, ck)
        if k in paths:
            continue
        if pa.lower().endswith((".heic", ".heif")):
            continue
        paths[k] = pa
    con.close()

    # 只取「整张照片仅 1 张脸」的照片作真值。
    # 放宽到"合影里取最大脸"会把旁人误标成归档人 —— 实测同人相似度只剩 0.32
    # （正常应 0.5+），噪声盖过模型差异，评测失去意义。
    nface = collections.Counter()
    for rid, ck, x, y, w, h, lib, ds in rows:
        nface[(lib, ck)] += 1

    fam = collections.defaultdict(list)
    alien = []
    rows.sort(key=lambda r: -max(r[4], r[5]))
    seen = set()
    for rid, ck, x, y, w, h, lib, ds in rows:
        if (lib, ck) not in paths or ck in seen:
            continue
        if nface[(lib, ck)] != 1:
            continue
        p = ck_person.get(ck)
        it = (max(w, h), ck, lib, x, y, w, h)
        if p in PERSONS:
            seen.add(ck)
            fam[p].append(it)
        elif lib == "09" and p is None:
            seen.add(ck)
            alien.append(it)

    def take(items, n):
        if not items:
            return []
        by_b = collections.defaultdict(list)
        for it in items:
            by_b[bucket_of(it[0])].append(it)
        per = max(1, n // len(by_b))
        rng = np.random.default_rng(7)
        out = []
        for b, lst in by_b.items():
            k = min(per, len(lst))
            if k:
                out += [lst[i] for i in rng.choice(len(lst), size=k, replace=False)]
        if len(out) < n:
            rest = [it for it in items if it not in out]
            k = min(n - len(out), len(rest))
            if k:
                out += [rest[i] for i in rng.choice(len(rest), size=k, replace=False)]
        return out

    plan = []
    print("各家属可用样本:")
    for p in PERSONS:
        got = take(fam[p], N_EACH)
        print(f"  {p:<10} 可用 {len(fam[p]):>5} 取样 {len(got)} "
              f"层={dict(collections.Counter(bucket_of(i[0]) for i in fam[p]))}")
        plan += [(p,) + it for it in got]
    got = take(alien, 3 * N_EACH)
    print(f"  09陌生人   可用 {len(alien)} 取样 {len(got)}")
    plan += [(FOREIGN,) + it for it in got]

    import cv2
    from insightface.app import FaceAnalysis
    from insightface.utils import face_align
    app = FaceAnalysis(name="buffalo_l", allowed_modules=["detection"],
                       providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_size=(640, 640))

    crops, metas, cache = [], [], {}
    for lab, side, ck, lib, x, y, w, h in plan:
        pa = paths.get((lib, ck))
        if not pa:
            continue
        if ck not in cache:
            img = cv2.imread(pa)
            if img is None:
                cache[ck] = None
                continue
            H, W = img.shape[:2]
            sc = min(1.0, DOWNSCALE / max(H, W))
            small = cv2.resize(img, (int(W * sc), int(H * sc))) if sc < 1 else img
            try:
                fs = app.get(small)
            except Exception:
                fs = []
            cache[ck] = (small, [(f.bbox, f.kps) for f in fs], sc)
        c = cache[ck]
        if not c:
            continue
        img, dets, sc = c
        ob = (x * sc, y * sc, w * sc, h * sc)
        best, bi, bkps = None, 0.0, None
        for bbox, kps in dets:
            b = (float(bbox[0]), float(bbox[1]),
                 float(bbox[2] - bbox[0]), float(bbox[3] - bbox[1]))
            v = iou(ob, b)
            if v > bi:
                best, bi, bkps = bbox, v, kps
        if best is None or bi < IOU_MIN:
            continue
        try:
            aimg = face_align.norm_crop(img, landmark=bkps, image_size=112)
        except Exception:
            continue
        crops.append(aimg)
        metas.append(dict(label=lab, ck=ck, lib=lib,
                          side=int(max(best[2] - best[0], best[3] - best[1]) / sc)))
    np.save(CACHE, np.stack(crops))
    json.dump(metas, open(META, "w"), ensure_ascii=False)
    print(f"\n缓存 {len(crops)} 张裁剪 -> {CACHE}")
    print("按人:", dict(collections.Counter(m["label"] for m in metas)))
    print("按层:", dict(collections.Counter(bucket_of(m["side"]) for m in metas)))


# ----------------------------- run -----------------------------
def _degrade(crops, k):
    import cv2
    if k == 0:
        return crops
    out = []
    for c in crops:
        s = cv2.resize(c, (k, k), interpolation=cv2.INTER_AREA)
        out.append(cv2.resize(s, (112, 112), interpolation=cv2.INTER_CUBIC))
    return out


def cmd_run(tag):
    path = MODELS[tag]
    if not os.path.exists(path):
        print(f"缺模型 {path}")
        return
    crops = list(np.load(CACHE))
    print(f"[{tag}] 加载 {path}")
    if tag == "adaface":
        sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        name = sess.get_inputs()[0].name
        saved = {}
        for rgb in (True, False):
            for k in DEGRADES:
                arr = _degrade(crops, k)
                outs = []
                for i in range(0, len(arr), 32):
                    ch = arr[i:i + 32]
                    x = np.stack([
                        np.transpose((c[:, :, ::-1] if rgb else c).astype(np.float32)
                                     / 127.5 - 1.0, (2, 0, 1)) for c in ch])
                    outs.append(sess.run(None, {name: x})[0])
                saved[("RGB" if rgb else "BGR", k)] = l2n(np.vstack(outs))
            # 通道诊断只做一次：用未降质的挑
        pick = None
        for ch in ("RGB", "BGR"):
            m = saved[(ch, 0)]
            labs = [x["label"] for x in json.load(open(META))]
            sims, ys = pairs(m, np.array(labs))
            from sklearn.metrics import roc_auc_score
            auc = roc_auc_score(ys, sims)
            print(f"   [{ch}] AUC={auc:.4f} 同人={sims[ys==1].mean():.3f}")
            if pick is None or auc > pick[0]:
                pick = (auc, ch)
        print(f"   -> 采用 {pick[1]}")
        for k in DEGRADES:
            np.save(os.path.join(OUT, f"emb_{tag}_{k}.npy"), saved[(pick[1], k)])
    else:
        from insightface.model_zoo import get_model
        rec = get_model(path, providers=["CPUExecutionProvider"])
        for k in DEGRADES:
            arr = _degrade(crops, k)
            m = l2n(np.vstack([rec.get_feat(c).ravel() for c in arr]))
            np.save(os.path.join(OUT, f"emb_{tag}_{k}.npy"), m)
            print(f"   k={k}: {m.shape}")


MUTEX = {"乐仔": "乐仔小时候", "乐仔小时候": "乐仔"}


def pairs(mat, labels):
    n = len(labels)
    sims, ys = [], []
    for i in range(n):
        for j in range(i + 1, n):
            li, lj = labels[i], labels[j]
            if li == FOREIGN and lj == FOREIGN:
                continue   # 09 内部是不同孩子，不是同人对 —— 第一版在这里踩过坑
            if MUTEX.get(li) == lj:
                continue   # 乐仔与乐仔小时候是同一人，不能算"不同人"，否则 AUC 被系统性压低
            ys.append(1 if li == lj else 0)
            sims.append(float(mat[i] @ mat[j]))
    return np.array(sims), np.array(ys)


def cmd_eval():
    from sklearn.metrics import roc_auc_score, roc_curve
    metas = json.load(open(META))
    labels = np.array([m["label"] for m in metas])
    keys = np.array([bucket_of(m["side"]) for m in metas])

    def auc_of(m):
        s, y = pairs(m, labels)
        if y.sum() < 3 or (y == 0).sum() < 3:
            return None
        fpr, tpr, _ = roc_curve(y, s)
        i = np.searchsorted(fpr, 0.01) - 1
        return roc_auc_score(y, s), (tpr[i] if i >= 0 else 0), \
            float(s[y == 1].mean()), float(s[y == 0].mean())

    for k in DEGRADES:
        mats = {}
        for tag in MODELS:
            p = os.path.join(OUT, f"emb_{tag}_{k}.npy")
            if os.path.exists(p):
                mats[tag] = np.load(p)
        # 融合
        order = [t for t in ("mbf", "r50", "glintr100", "adaface") if t in mats]
        if len(order) >= 2:
            mats["fuse(" + "+".join(order) + ")"] = l2n(np.hstack([mats[t] for t in order]))
        title = "原始清晰度" if k == 0 else f"降质 {k}x{k} 后放大回 112"
        print(f"\n=== {title} ===")
        print(f"{'模型':<34}{'AUC':>9}{'TPR@F1%':>10}{'同人':>9}{'不同人':>9}")
        for tag, m in mats.items():
            r = auc_of(m)
            if r is None:
                continue
            print(f"{tag:<34}{r[0]:>9.4f}{r[1]:>10.3f}{r[2]:>9.3f}{r[3]:>9.3f}")
        if k == 0:
            print("\n-- 分层（人脸像素）--")
            for b in [f"<{x}px" for x in BUCKETS] + [f">={BUCKETS[-1]}px"]:
                idx = np.where(keys == b)[0]
                if len(idx) < 10:
                    continue
                labs = labels[idx]
                if collections.Counter(labs).most_common(1)[0][1] < 3:
                    continue
                cells = []
                for tag, m in mats.items():
                    s, y = pairs(m[idx], labs)
                    if y.sum() >= 3 and (y == 0).sum() >= 3:
                        cells.append(f"{tag}={roc_auc_score(y, s):.3f}")
                if cells:
                    print(f"{b:<10} " + "  ".join(cells))


if __name__ == "__main__":
    import onnxruntime as ort
    cmd = sys.argv[1] if len(sys.argv) > 1 else "eval"
    if cmd == "prep":
        cmd_prep()
    elif cmd == "run":
        tags = sys.argv[2:] or list(MODELS)
        if tags == ["all"]:
            tags = list(MODELS)
        for t in tags:
            cmd_run(t)
    else:
        cmd_eval()
