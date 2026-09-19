#!/usr/bin/env python3
"""检测器横评：YuNet(现役) vs SCRFD-10G。

不只看「谁检得多」——多检出的框可能是误检。三维度评判：
  1. 召回：在已知有人在的照片上，谁能检到脸（漏检率）
  2. 小脸：框边长分布，谁在 <40px 区间还有产出
  3. 精 度：IOU 匹配后「仅某方独有」的框，裁图人工看是不是真脸

用法:
  python bench_det.py prep            # 采样并重检测，落缓存
  python bench_det.py report          # 输出统计表
  python bench_det.py grid [N] [tag]  # 独有框拼图，人工验精度
"""
import os
import sys
import json
import time
import collections
import argparse

os.nice(10)
import cv2
import numpy as np

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
LIB_DB = f"{ROOT}/19_统一相册库/library.db"
OLD_MODELS = f"{ROOT}/07_相册/相册&视频备份/_photo_index/models"
YUNET = f"{OLD_MODELS}/face_detection_yunet_2023mar.onnx"
SCRFD = os.path.expanduser("~/.insightface/models/antelopev2/scrfd_10g_bnkps.onnx")
CACHE = f"{ROOT}/照片人物判定/_bench_det"
MAX_SIDE = 1280          # 检测前长边压到此值（原图 4000px 级太慢）
N_PER_LIB = 60


def load_image(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".heic", ".heif"):
        try:
            import pillow_heif
            from PIL import Image
            pillow_heif.register_heif_opener()
            with Image.open(path) as im:
                im = im.convert("RGB")
                bgr = cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)
            return bgr
        except Exception:
            return None
    buf = np.fromfile(path, dtype=np.uint8)
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def resize_keep(img, max_side=MAX_SIDE):
    h, w = img.shape[:2]
    s = max_side / max(h, w)
    if s >= 1.0:
        return img, 1.0
    return cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA), s


class YuNetDet:
    """现役检测器：OpenCV YuNet。"""
    name = "yunet"

    def __init__(self, path):
        self.path = path

    def detect(self, img):
        h, w = img.shape[:2]
        d = cv2.FaceDetectorYN.create(self.path, "", (w, h),
                                      score_threshold=0.6,
                                      nms_threshold=0.3, top_k=5000)
        _, faces = d.detect(img)
        if faces is None:
            return []
        out = []
        for f in faces:
            x, y, bw, bh = f[:4]
            kps = f[4:14].reshape(5, 2)
            out.append(dict(box=[float(x), float(y), float(bw), float(bh)],
                            score=float(f[14]),
                            kps=[[float(a), float(b)] for a, b in kps]))
        return out


class SCRFDDet:
    """候选检测器：InsightFace SCRFD-10G（5 点关键点）。"""

    def __init__(self, path, input_size=(640, 640)):
        import insightface
        from insightface.model_zoo import get_model
        # get_model 会按 onnx 文件名猜；这里显式构造
        from insightface.model_zoo.scrfd import SCRFD
        self.m = SCRFD(model_file=path)
        self.m.prepare(ctx_id=-1, input_size=input_size, det_thresh=0.5)

    @property
    def name(self):
        return f"scrfd{self.m.input_size[0]}"

    def detect(self, img):
        bboxes, kpss = self.m.detect(img, max_num=0, metric="max")
        out = []
        if bboxes is None:
            return out
        for i in range(bboxes.shape[0]):
            x1, y1, x2, y2, sc = bboxes[i]
            k = kpss[i] if kpss is not None else None
            out.append(dict(box=[float(x1), float(y1), float(x2 - x1), float(y2 - y1)],
                            score=float(sc),
                            kps=[[float(a), float(b)] for a, b in k] if k is not None else None))
        return out


def iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    i = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    u = aw * ah + bw * bh - i
    return i / u if u > 1e-9 else 0.0


def pick_samples(n_per_lib=N_PER_LIB):
    """每个来源库取样：优先取「已检出脸偏少」的照片，这类最能体现召回差异。"""
    import sqlite3
    con = sqlite3.connect(LIB_DB)
    nf = dict(con.execute("select content_key, count(*) from faces group by content_key"))
    rows = {}
    for ck, lib, path in con.execute(
            "select content_key, lib, path from files where is_primary=1"):
        if lib not in ("07", "08", "09"):
            continue
        if path.lower().endswith((".heic", ".heif")):
            continue       # HEIC 转换慢，单独处理
        rows.setdefault(lib, []).append((ck, path, nf.get(ck, 0)))
    con.close()
    out = []
    for lib, items in rows.items():
        # 一半取「已检出 0~1 张脸」（疑似漏检），一半随机
        few = [it for it in items if it[2] <= 1]
        rest = [it for it in items if it[2] > 1]
        rng = np.random.default_rng(7)
        a = [items[int(i)] for i in rng.choice(len(few), size=min(n_per_lib // 2, len(few)),
                                               replace=False)] if few else []
        b = [rest[int(i)] for i in rng.choice(len(rest), size=min(n_per_lib - len(a), len(rest)),
                                              replace=False)] if rest else []
        for ck, path, n in a + b:
            out.append(dict(ck=ck, path=path, lib=lib, n_old=n))
    return out


def cmd_prep():
    os.makedirs(CACHE, exist_ok=True)
    samples = pick_samples()
    print(f"采样 {len(samples)} 张："
          f"{collections.Counter(s['lib'] for s in samples).most_common()}")
    dets = [("yunet", YuNetDet(YUNET)),
            ("scrfd640", SCRFDDet(SCRFD, (640, 640))),
            ("scrfd1280", SCRFDDet(SCRFD, (1280, 1280)))]
    res = []
    t0 = time.time()
    for i, s in enumerate(samples):
        img = load_image(s["path"])
        if img is None:
            continue
        img, scale = resize_keep(img)
        rec = dict(s, scale=scale, img_size=list(img.shape[:2]))
        for tag, det in dets:
            try:
                faces = det.detect(img)
            except Exception as e:
                print("检测失败", tag, type(e).__name__, str(e)[:80])
                faces = []
            # 坐标还原回原图尺度
            for f in faces:
                f["box"] = [v / scale for v in f["box"]]
                if f["kps"]:
                    f["kps"] = [[a / scale, b / scale] for a, b in f["kps"]]
            rec[tag] = faces
        res.append(rec)
        if (i + 1) % 20 == 0:
            el = time.time() - t0
            print(f"  {i + 1}/{len(samples)}  用时 {el:.0f}s  预计剩余 "
                  f"{el / (i + 1) * (len(samples) - i - 1):.0f}s")
    json.dump(res, open(f"{CACHE}/det.json", "w"), ensure_ascii=False)
    print(f"已存 {len(res)} 张 → {CACHE}/det.json  用时 {time.time() - t0:.0f}s")


def cmd_report():
    res = json.load(open(f"{CACHE}/det.json", encoding="utf-8"))
    tags = ["yunet", "scrfd640", "scrfd1280"]
    print(f"样本 {len(res)} 张\n")

    print("=== 1. 召回：检出脸总数 / 有脸照片占比 ===")
    print(f"{'检测器':<12}{'脸总数':>8}{'张/照片':>9}{'有脸照片':>10}{'漏检照片(=0脸)':>16}")
    for t in tags:
        tot = sum(len(r[t]) for r in res)
        withface = sum(1 for r in res if r[t])
        zero = len(res) - withface
        print(f"{t:<12}{tot:>8}{tot / len(res):>9.2f}{withface:>10}{zero:>16}")

    print("\n=== 2. 小脸能力：检出框的边长分布 ===")
    bins = [(0, 25), (25, 40), (40, 80), (80, 160), (160, 9999)]
    print(f"{'检测器':<12}" + "".join(f"{'<25':>8}{'25-40':>8}{'40-80':>8}"
                                      f"{'80-160':>8}{'>160':>8}" if t == tags[0] else "" for t in tags[:1]))
    print(f"{'':<12}" + "".join(f"{b[0]}-{b[1] if b[1] < 9999 else '∞':>7}" for b in bins))
    for t in tags:
        sides = [max(f["box"][2], f["box"][3]) for r in res for f in r[t]]
        counts = []
        for lo, hi in bins:
            counts.append(sum(1 for s in sides if lo <= s < hi))
        print(f"{t:<12}" + "".join(f"{c:>8}" for c in counts))

    print("\n=== 3. 一致性：以 SCRFD1280 为参照的 IOU 匹配 ===")
    for t in tags[:2]:
        only_t, only_ref, both = 0, 0, 0
        for r in res:
            a, b = r[t], r["scrfd1280"]
            used = set()
            for f in a:
                m = max((iou(f["box"], g["box"]), j) for j, g in enumerate(b)) if b else (0, -1)
                if m[0] >= 0.3:
                    both += 1
                    used.add(m[1])
                else:
                    only_t += 1
            only_ref += len(b) - len(used)
        print(f"  {t:<10} vs scrfd1280：共有 {both}  |  仅{t}有 {only_t}  |  仅参照有 {only_ref}")
    print("\n（下一步用 grid 子命令把「仅某方独有」的框裁出来人工验精度）")


def cmd_grid(n=18, tag="scrfd1280"):
    """把「仅 tag 检出 / 仅 yunet 检出」的框裁图拼成对照图。"""
    from PIL import Image, ImageDraw
    res = json.load(open(f"{CACHE}/det.json", encoding="utf-8"))
    ref = "yunet" if tag != "yunet" else "scrfd1280"
    A, B = [], []     # A: 仅 tag 有；B: 仅 ref 有
    imgs = {}
    for r in res:
        a, b = r[tag], r[ref]
        used = set()
        for f in a:
            m = max((iou(f["box"], g["box"]), j) for j, g in enumerate(b)) if b else (0, -1)
            if m[0] >= 0.3:
                used.add(m[1])
            else:
                A.append((r, f))
        for j, g in enumerate(b):
            if j not in used:
                B.append((r, g))
    rng = np.random.default_rng(3)
    A = [A[i] for i in rng.choice(len(A), size=min(n, len(A)), replace=False)] if A else []
    B = [B[i] for i in rng.choice(len(B), size=min(n, len(B)), replace=False)] if B else []
    print(f"仅 {tag} 检出 {len(A)}（取样 {len(A)}） / 仅 {ref} 检出 {len(B)}（取样 {len(B)}）")

    S = 150
    cols = 6
    for name, items in ((f"only_{tag}", A), (f"only_{ref}", B)):
        if not items:
            continue
        rows = (len(items) + cols - 1) // cols
        g = Image.new("RGB", (cols * S, rows * (S + 22)), (30, 30, 34))
        d = ImageDraw.Draw(g)
        for k, (r, f) in enumerate(items):
            ck = r["ck"]
            if ck not in imgs:
                im = load_image(r["path"])
                if im is None:
                    continue
                imgs[ck] = im
            im = imgs[ck]
            x, y, w, h = f["box"]
            px = int(max(w, h) * 0.35)
            H, W = im.shape[:2]
            c = im[max(0, int(y - px)):min(H, int(y + h + px)),
                   max(0, int(x - px)):min(W, int(x + w + px))]
            if c.size == 0:
                continue
            c = cv2.resize(c, (S, S), interpolation=cv2.INTER_AREA)
            rr, cc = divmod(k, cols)
            g.paste(Image.fromarray(cv2.cvtColor(c, cv2.COLOR_BGR2RGB)),
                    (cc * S, rr * (S + 22) + 22))
            d.text((cc * S + 3, rr * (S + 22) + 5),
                   f"#{k} s={int(max(w, h))} p={f['score']:.2f}", fill=(230, 230, 236))
        p = f"{CACHE}/{name}.jpg"
        g.save(p, quality=88)
        print(f"  → {p}")
        for k, (r, f) in enumerate(items):
            x, y, w, h = f["box"]
            print(f"     #{k} side={int(max(w, h)):>4} score={f['score']:.2f} "
                  f"{r['lib']} {r['path'].split('/')[-1][:38]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["prep", "report", "grid"])
    ap.add_argument("n", nargs="?", default=18)
    ap.add_argument("tag", nargs="?", default="scrfd1280")
    a = ap.parse_args()
    if a.cmd == "prep":
        cmd_prep()
    elif a.cmd == "report":
        cmd_report()
    else:
        cmd_grid(int(a.n), a.tag)
