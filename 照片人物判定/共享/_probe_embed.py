"""嵌入模型评测 v2：按人脸像素层评测候选嵌入模型的判别力。

为什么不只看全局指标：本项目 07 库人脸中位仅 19px，v1 探针抽到的全是 260px 大脸，
看上去各模型差不多，但真正在线的脸大量是小脸 —— 必须分层才知道涨点落在哪。

候选模型：
  mbf       = w600k_mbf   MobileFaceNet            —— 现役轻量
  r50       = w600k_r50   ArcFace ResNet50         —— 现役主力
  glintr100 = glintr100   antelopev2 更深骨干      —— InsightFace 官方更强 CPU 包
  adaface   = AdaFace IR-101 WebFace12M            —— CVPR22 Oral，质量自适应 margin，
                                                      专为低质量/模糊人脸设计

评测链路（所有模型共享同一批 112x112 裁剪，保证只比较嵌入本身）：
  SCRFD-10G 重检测 -> 与库内旧框做 IOU 匹配 -> norm_crop(112) -> 各模型提嵌入

用法：python _probe_embed.py [每个人取样数]
"""
import sys
import os
import sqlite3
import collections
import numpy as np
import onnxruntime as ort
from sklearn.metrics import roc_auc_score, roc_curve

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
LIB_DB = f"{ROOT}/19_统一相册库/library.db"
OLD_MODELS = f"{ROOT}/07_相册/相册&视频备份/_photo_index/models"
INSPATH = os.path.expanduser("~/.insightface/models")
ADAFACE = "/tmp/adaface_dl/adaface_ir_101.onnx"

PERSONS = ["乐仔", "艳艳", "妈妈", "我", "七月", "爸爸", "乐仔小时候"]
FOREIGN = "陌生人09"
BUCKETS = (25, 60, 140)
IOU_MIN = 0.3


# ----------------------------- 样本计划 -----------------------------
def load_plan(n_each):
    con = sqlite3.connect(LIB_DB)
    ck_person = {}
    for rel, ck in con.execute("select rel, content_key from files where lib='18'"):
        ck_person.setdefault(ck, rel.split("/")[0])
    rows = con.execute(
        "select rowid, content_key, x, y, w, h, lib, det_score from faces").fetchall()
    # ck/lib -> 可读图路径（跳过 heic；必须与 faces.lib 匹配，否则分辨率不同导致 IOU 失配）
    paths = {}
    for lib, ck, pa in con.execute("select lib, content_key, path from files"):
        k = (lib, ck)
        if k in paths:
            continue
        if pa.lower().endswith((".heic", ".heif")):
            continue
        paths[k] = pa
    con.close()

    # 每张照片只贡献「最大的那张脸」：否则合影里旁人的脸会被误标成归档人，
    # 这正是 v2 第一版各模型 AUC 全线塌到 0.59（近乎随机）的根因。
    fam = collections.defaultdict(list)    # person -> [(side, ck, lib, x,y,w,h)]
    alien = []
    rows.sort(key=lambda r: -(max(r[4], r[5])))
    seen_ck = set()
    for rid, ck, x, y, w, h, lib, ds in rows:
        k = (lib, ck)
        if k not in paths or k in seen_ck:
            continue
        p = ck_person.get(ck)
        side = max(w, h)
        item = (side, ck, lib, x, y, w, h)
        if p in PERSONS:
            if ck in seen_ck:            # 同一内容只取一次（跨 lib 副本是同一张照片）
                continue
            seen_ck.add(ck)
            fam[p].append(item)
        elif lib == "09" and p is None:
            if ck in seen_ck:
                continue
            seen_ck.add(ck)
            alien.append(item)

    def take(items, n):
        """按像素层均衡取样，保证小脸层不被大脸淹没。"""
        if not items:
            return []
        by_b = collections.defaultdict(list)
        for it in items:
            by_b[bucket_of(it[0])].append(it)
        per_layer = {}
        for b, lst in by_b.items():
            per_layer[b] = len(lst)
        n_b = max(1, n // len(by_b))
        out = []
        rng = np.random.default_rng(7)
        for b, lst in by_b.items():
            lst = sorted(lst)
            k = min(n_b, len(lst))
            if k:
                idx = rng.choice(len(lst), size=k, replace=False)
                out += [lst[i] for i in idx]
        # 若某层不足，从其它层补齐到 n
        if len(out) < n:
            rest = [it for it in items if it not in out]
            k = min(n - len(out), len(rest))
            if k:
                pick = rng.choice(len(rest), size=k, replace=False)
                out += [rest[i] for i in pick]
        return out

    plan = []
    print("各家属可用样本（18 归档，限有 jpg 且已参与人脸扫描的）:")
    for p in PERSONS:
        got = take(fam[p], n_each)
        layers = dict(collections.Counter(bucket_of(it[0]) for it in fam[p]))
        print(f"  {p:<10} 可用 {len(fam[p]):>5} 层={layers} 取样 {len(got)}")
        for it in got:
            plan.append((p,) + it)
    print(f"  09陌生人   可用 {len(alien)}")
    for it in take(alien, 3 * n_each):
        plan.append((FOREIGN,) + it)
    return plan, paths


def bucket_of(side):
    for b in BUCKETS:
        if side < b:
            return f"<{b}px"
    return f">={BUCKETS[-1]}px"


# ----------------------------- 检测与对齐 -----------------------------
def iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    return inter / (aw * ah + bw * bh - inter)


def build_crops(plan, paths):
    """SCRFD 重检测 + IOU 匹配旧框 -> 112x112 对齐裁剪。"""
    import cv2
    from insightface.app import FaceAnalysis
    from insightface.utils import face_align

    app = FaceAnalysis(name="buffalo_l", allowed_modules=["detection"],
                       providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_size=(640, 640))

    crops, metas = [], []
    cache = {}
    for lab, side, ck, lib, x, y, w, h in plan:
        pa = paths.get((lib, ck))          # 必须与 faces.lib 匹配，否则分辨率不同 IOU 会失配
        if not pa:
            continue
        if ck not in cache:
            img = cv2.imread(pa)
            if img is None:
                cache[ck] = None
                continue
            # 先降到 1280 再检测：原图 4000px 级会让 SCRFD 慢到跑不完（实测卡住 >15min），
            # 而 1280 足够检出人脸。旧框按同一比例缩放后再做 IOU 匹配。
            H, W = img.shape[:2]
            sc = min(1.0, 1280.0 / max(H, W))
            small = cv2.resize(img, (int(W * sc), int(H * sc))) if sc < 1.0 else img
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
        best, bi = None, 0.0
        for bbox, kps in dets:
            b = (float(bbox[0]), float(bbox[1]),
                 float(bbox[2] - bbox[0]), float(bbox[3] - bbox[1]))
            v = iou(ob, b)
            if v > bi:
                best, bi = bbox, v
                best_kps = kps
        if best is None or bi < IOU_MIN:
            continue
        try:
            aimg = face_align.norm_crop(img, landmark=best_kps, image_size=112)
        except Exception:
            continue
        crops.append(aimg)
        metas.append(dict(label=lab, ck=ck, lib=lib,
                          side_new=int(max(best[2] - best[0], best[3] - best[1]) / sc)))
    return crops, metas


# ----------------------------- 嵌入 -----------------------------
def l2n(v):
    """逐行 L2 归一化。对 2D 必须用 axis=1，否则会退化成整体 Frobenius 范数，
    让各行的范数不一致 —— cosine 相似度随之失真。"""
    v = np.asarray(v, dtype=np.float32)
    if v.ndim == 1:
        n = np.linalg.norm(v)
        return v / n if n > 1e-9 else v
    n = np.linalg.norm(v, axis=1, keepdims=True)
    return v / np.maximum(n, 1e-9)


class InsRec:
    def __init__(self, path):
        from insightface.model_zoo import get_model
        self.m = get_model(path, providers=["CPUExecutionProvider"])

    def __call__(self, crops):
        return np.vstack([l2n(self.m.get_feat(c).ravel()) for c in crops])


class AdaRec:
    """批量推理，逐张跑 260MB 的 IR-101 太慢，会被超时杀掉。"""

    def __init__(self, path, rgb=True):
        self.s = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        self.name = self.s.get_inputs()[0].name
        self.rgb = rgb

    def __call__(self, crops, rgb=None):
        use = self.rgb if rgb is None else rgb
        out = []
        B = 32
        for i in range(0, len(crops), B):
            chunk = crops[i:i + B]
            arr = np.stack([
                np.transpose((c[:, :, ::-1] if use else c).astype(np.float32) / 127.5 - 1.0,
                             (2, 0, 1)) for c in chunk]).astype(np.float32)
            e = self.s.run(None, {self.name: arr})[0]
            out.append(e)
        e = np.vstack(out)
        return np.vstack([l2n(v) for v in e])


# ----------------------------- 评测 -----------------------------
def pair_stats(mat, labels, keys=None):
    """返回 (sims, ys)。同人对=同一且非陌生人；不同人对=两个不同的人（可含陌生人）。"""
    labels = np.asarray(labels)
    n = len(labels)
    sims, ys, ks = [], [], []
    for i in range(n):
        for j in range(i + 1, n):
            li, lj = labels[i], labels[j]
            if li == FOREIGN and lj == FOREIGN:
                continue                      # 09 内部是不同孩子，不是同人对 —— v1 在这里踩过坑
            if li == lj:
                ys.append(1)
            elif li == FOREIGN or lj == FOREIGN:
                ys.append(0)
            else:
                ys.append(0)
            sims.append(float(mat[i] @ mat[j]))
            ks.append(None if keys is None else keys[i])
    return np.array(sims), np.array(ys), ks


def auc_tpr(sims, ys):
    if ys.sum() < 3 or (ys == 0).sum() < 3:
        return None
    auc = roc_auc_score(ys, sims)
    fpr, tpr, _ = roc_curve(ys, sims)
    i = np.searchsorted(fpr, 0.01) - 1
    return auc, (tpr[i] if i >= 0 else 0.0), float(sims[ys == 1].mean()), \
        float(sims[ys == 0].mean())


def main():
    n_each = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    plan, paths = load_plan(n_each)
    print("计划样本:", len(plan), dict(collections.Counter(p[0] for p in plan)))
    print("尺寸层分布:", dict(collections.Counter(bucket_of(p[1]) for p in plan)))

    crops, metas = build_crops(plan, paths)
    print(f"成功对齐 {len(crops)} 张（IOU>{IOU_MIN}）")
    print("按人:", dict(collections.Counter(m['label'] for m in metas)))
    print("按层:", dict(collections.Counter(bucket_of(m['side_new']) for m in metas)))

    labels = [m["label"] for m in metas]
    keys = [bucket_of(m["side_new"]) for m in metas]
    if len(set(labels)) < 3:
        print("样本不足，评测中止。")
        return

    models = {}
    for tag, path in [("mbf", f"{OLD_MODELS}/buffalo_s/w600k_mbf.onnx"),
                      ("r50", f"{OLD_MODELS}/buffalo_l/w600k_r50.onnx"),
                      ("r50_ins", os.path.join(INSPATH, "buffalo_l", "w600k_r50.onnx")),
                      ("glintr100", os.path.join(INSPATH, "antelopev2", "glintr100.onnx"))]:
        if os.path.exists(path):
            models[tag] = InsRec(path)
        else:
            print(f"  跳过 {tag}（缺 {path}）")

    mats = {tag: m(crops) for tag, m in models.items()}

    # AdaFace 通道诊断：选 AUC 高的那个，避免想当然
    recs = {}
    if os.path.exists(ADAFACE):
        best = None
        for ch, rgb in (("RGB", True), ("BGR", False)):
            mtry = AdaRec(ADAFACE, rgb=rgb)(crops)
            s, y, _ = pair_stats(mtry, labels)
            r = auc_tpr(s, y)
            print(f"  AdaFace[{ch}] 同人均值 {r[2]:+.3f} 不同人 {r[3]:+.3f} AUC {r[0]:.4f}")
            if best is None or r[0] > best[0]:
                best = (r[0], ch, mtry)
        print(f"  -> 采用通道 {best[1]}")
        recs["adaface"] = AdaRec(ADAFACE, rgb=(best[1] == "RGB"))
        mats[f"adaface_{best[1]}"] = best[2]
    recs["r50"] = InsRec(os.path.join(INSPATH, "buffalo_l", "w600k_r50.onnx"))
    if os.path.exists(os.path.join(INSPATH, "antelopev2", "glintr100.onnx")):
        recs["glintr100"] = InsRec(os.path.join(INSPATH, "antelopev2", "glintr100.onnx"))
    mbf_path = f"{OLD_MODELS}/buffalo_s/w600k_mbf.onnx"
    if os.path.exists(mbf_path):
        recs["mbf"] = InsRec(mbf_path)

    base = {k: v for k, v in mats.items() if k.startswith(("mbf", "r50", "glintr100"))}
    base = {("r50" if k == "r50_ins" else k): v for k, v in base.items()}
    if "adaface" in recs:
        base["adaface"] = mats[[k for k in mats if k.startswith("adaface_")][0]]

    order = [o for o in ("mbf", "r50", "glintr100", "adaface") if o in base]
    if len(order) >= 2:
        mats[f"fuse({'+'.join(order)})"] = l2n(np.hstack([base[o] for o in order]))

    report("原始清晰度（无降质）", mats, labels, keys)

    # ---- 合成降质：缩到 KxK 再放大回 112，干净地压测低质量鲁棒性 ----
    import cv2
    for K in (16, 32):
        deg = []
        for c in crops:
            small = cv2.resize(c, (K, K), interpolation=cv2.INTER_AREA)
            deg.append(cv2.resize(small, (112, 112), interpolation=cv2.INTER_CUBIC))
        dm = {tag: rec(crops if False else deg) for tag, rec in recs.items()}
        if len(dm) >= 2:
            dm["fuse"] = l2n(np.hstack([dm[o] for o in order if o in dm]))
        report(f"降质到 {K}x{K} 再放大回 112", dm, labels, keys, deep=False)


def report(title, mats, labels, keys, deep=True):
    print(f"\n=== {title}：全局核验 ===")
    print(f"{'模型':<26}{'AUC':>9}{'TPR@FPR1%':>11}{'同人':>9}{'不同人':>9}{'间隔':>8}")
    for tag, m in mats.items():
        s, y, _ = pair_stats(m, labels)
        r = auc_tpr(s, y)
        if r is None:
            continue
        print(f"{tag:<26}{r[0]:>9.4f}{r[1]:>11.3f}{r[2]:>9.3f}{r[3]:>9.3f}{r[2]-r[3]:>8.3f}")
    if deep:
        print("\n-- 分层（像素层）--")
        for b in [f"<{x}px" for x in BUCKETS] + [f">={BUCKETS[-1]}px"]:
            idx = [k for k, kk in enumerate(keys) if kk == b]
            if len(idx) < 8:
                continue
            labs = [labels[i] for i in idx]
            if collections.Counter(labs).most_common(1)[0][1] < 3:
                continue
            line = f"{b:<12}"
            cells = []
            for tag, m in mats.items():
                s, y, _ = pair_stats(m[idx], labs)
                r = auc_tpr(s, y)
                cells.append(f"{tag}={r[0]:.3f}" if r else f"{tag}=n/a")
            print(f"{b:<12}" + "  ".join(cells))


if __name__ == "__main__":
    main()
