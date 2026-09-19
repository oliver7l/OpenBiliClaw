"""探针：验证 genderage 属性模型在本库小脸上的可用性 + 测速。"""
import sqlite3, time, subprocess, tempfile, os, sys
import numpy as np, cv2
from PIL import Image

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
LIB = f"{ROOT}/19_统一相册库/library.db"
GA = "/Users/imac/.insightface/models/buffalo_l/genderage.onnx"


def imread(path):
    """读图为 BGR ndarray，HEIC 走 sips 转码。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".heic", ".heif"):
        tmp = tempfile.mktemp(suffix=".jpg")
        r = subprocess.run(["sips", "-s", "format", "jpeg", path, "--out", tmp],
                           capture_output=True)
        if r.returncode != 0:
            return None
        path = tmp
    try:
        im = Image.open(path)
        im = im.convert("RGB")
        a = np.asarray(im)[:, :, ::-1].copy()   # RGB->BGR
    except Exception as e:
        print("  imread fail", e)
        return None
    return a


class GenderAge:
    """复刻 insightface Attribute 的 genderage 前处理（只用 bbox center+scale，不需 kps）。"""

    def __init__(self, model_file):
        import onnxruntime
        import onnx
        graph = onnx.load(model_file).graph
        sub = any(n.name.startswith("Sub") or n.name.startswith("_minus")
                  or n.name == "bn_data" for n in list(graph.node)[:8])
        mul = any(n.name.startswith("Mul") or n.name.startswith("_mul")
                  or n.name == "bn_data" for n in list(graph.node)[:8])
        self.mean, self.std = (0.0, 1.0) if (sub and mul) else (127.5, 128.0)
        self.sess = onnxruntime.InferenceSession(model_file,
                                                 providers=["CPUExecutionProvider"])
        self.name = self.sess.get_inputs()[0].name
        self.size = 96

    def get(self, img, box):
        x, y, w, h = box
        cx, cy = x + w / 2, y + h / 2
        scale = self.size / (max(w, h) * 1.5)
        M = np.array([[scale, 0, self.size / 2 - cx * scale],
                      [0, scale, self.size / 2 - cy * scale]], dtype=np.float64)
        aimg = cv2.warpAffine(img, M, (self.size, self.size), flags=cv2.INTER_CUBIC,
                              borderValue=0.0)
        blob = cv2.dnn.blobFromImage(aimg, 1.0 / self.std, (self.size, self.size),
                                     (self.mean,) * 3, swapRB=True)
        pred = self.sess.run(None, {self.name: blob})[0][0]
        return int(np.argmax(pred[:2])), int(np.round(pred[2] * 100)), float(pred[0] - pred[1])


def main():
    con = sqlite3.connect(LIB)
    ck_person = {}
    for rel, ck in con.execute("select rel,content_key from files where lib='18'"):
        ck_person.setdefault(ck, rel.split("/")[0])
    paths = {}
    for ck, p in con.execute("select content_key,path from files where is_primary=1"):
        paths.setdefault(ck, p)
    # 抽各人的脸（带 box）
    rows = con.execute(
        "select rowid,content_key,x,y,w,h,det_score from faces").fetchall()
    by_p = {}
    for rid, ck, x, y, w, h, ds in rows:
        p = ck_person.get(ck)
        if p is None or ck not in paths:
            continue
        if x is None or w is None:
            continue
        by_p.setdefault(p, []).append((rid, ck, x, y, w, h, ds))
    ga = GenderAge(GA)
    print(f"{'人':<10}{'age':>5}{'gender':>7}{'side':>6}{'det':>6}  文件")
    t0 = time.time()
    n = 0
    for p in ["乐仔", "乐仔小时候", "妈妈", "艳艳", "我", "七月", "爸爸"]:
        lst = sorted(by_p.get(p, []), key=lambda r: -(r[5] or 0))[:6]
        for rid, ck, x, y, w, h, ds in lst:
            path = paths[ck]
            img = imread(path)
            if img is None:
                continue
            t1 = time.time()
            g, a, gs = ga.get(img, (x, y, w, h))
            n += 1
            print(f"{p:<10}{a:>5}{['女','男'][g]:>7}{int(max(w,h)):>6}"
                  f"{ds:>6.2f}  {os.path.basename(path)[:34]}  [{time.time()-t1:.2f}s]")
    print(f"\n共 {n} 张，总耗时 {time.time()-t0:.1f}s")
    # 09 班级库抽样（陌生幼童）
    print("\n=== 09 班级库抽样（应多为幼童）===")
    idx09 = [r for r in rows if r[1] in
             {ck for ck, in con.execute(
                 "select content_key from files where lib='09' and is_primary=1")}]
    import random
    random.seed(0)
    for rid, ck, x, y, w, h, ds in random.sample(idx09, 8):
        if ck not in paths or x is None:
            continue
        img = imread(paths[ck])
        if img is None:
            continue
        g, a, gs = ga.get(img, (x, y, w, h))
        print(f"  age={a:>3} gender={['女','男'][g]} side={int(max(w,h)):>4} "
              f"{os.path.basename(paths[ck])[:34]}")


if __name__ == "__main__":
    main()
