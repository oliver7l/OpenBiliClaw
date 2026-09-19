"""A/B：官方 insightface Attribute vs 本地复刻实现，验证 genderage 真实准确度。"""
import sqlite3, os, tempfile, subprocess
import numpy as np
from types import SimpleNamespace

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
LIB = f"{ROOT}/19_统一相册库/library.db"
GA = "/Users/imac/.insightface/models/buffalo_l/genderage.onnx"

import onnx
from insightface.model_zoo.attribute import Attribute


class Face:
    """同时支持属性访问(bbox)与下标赋值(face['gender'])。"""

    def __init__(self, x, y, w, h):
        self.bbox = np.array([x, y, x + w, y + h], dtype=np.float32)
        self._d = {}

    def __setitem__(self, k, v):
        self._d[k] = v

    def __getitem__(self, k):
        return self._d[k]


def imread(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".heic", ".heif"):
        tmp = tempfile.mktemp(suffix=".jpg")
        subprocess.run(["sips", "-s", "format", "jpeg", path, "--out", tmp],
                       capture_output=True)
        path = tmp
    from PIL import Image
    im = Image.open(path).convert("RGB")
    return np.asarray(im)[:, :, ::-1].copy()


def main():
    off = Attribute(GA)
    off.prepare(-1)
    print("官方 Attribute: input_size=", off.input_size, "mean=", off.input_mean,
          "std=", off.input_std, "task=", off.taskname)
    g = onnx.load(GA).graph
    print("前 8 节点名:", [n.name for n in list(g.node)[:8]])

    con = sqlite3.connect(LIB)
    paths = {}
    for ck, p in con.execute("select content_key,path from files where is_primary=1"):
        paths.setdefault(ck, p)
    ck_person = {}
    for rel, ck in con.execute("select rel,content_key from files where lib='18'"):
        ck_person.setdefault(ck, rel.split("/")[0])
    ck09 = {ck for ck, in con.execute(
        "select content_key from files where lib='09' and is_primary=1")}

    # 取：乐仔(幼童真值) 5 张、成人(妈妈/我) 5 张、09 幼童 5 张 —— 都选大脸
    rows = list(con.execute(
        "select rowid,content_key,x,y,w,h from faces where x is not null"))
    pick = []
    for want, n in [(("乐仔", "乐仔小时候"), 5), (("妈妈", "我", "爸爸"), 5), (None, 5)]:
        cand = []
        for rid, ck, x, y, w, h in rows:
            if ck not in paths:
                continue
            if want is None:
                if ck not in ck09:
                    continue
            else:
                if ck_person.get(ck) not in want:
                    continue
            cand.append((max(w, h), rid, ck, x, y, w, h))
        cand.sort(reverse=True)
        for it in cand[:n]:
            pick.append(it[1:])
    print(f"\n{'来源':<8}{'side':>5}{'官方age':>8}{'官方性别':>8}")
    for rid, ck, x, y, w, h in pick:
        img = imread(paths[ck])
        if img is None:
            continue
        f = Face(x, y, w, h)
        gender, age = off.get(img, f)
        src = ck_person.get(ck, "09班级")
        print(f"{src:<8}{int(max(w,h)):>5}{age:>8}{'男' if gender==1 else '女':>8}  "
              f"{os.path.basename(paths[ck])[:30]}")


if __name__ == "__main__":
    main()
