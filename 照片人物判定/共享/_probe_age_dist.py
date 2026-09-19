"""量化验证：genderage 在本库数据上到底有没有区分力。
三组对照：09 班级库幼童 / 18 归档幼童(乐仔,乐仔小时候,七月) / 18 归档成人(艳艳,我,妈妈,爸爸)。
若三组年龄分布完全重叠 → 该模型不可用，改用自训练幼童判别器。
"""
import sqlite3, os, tempfile, subprocess, random, time
import numpy as np

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
LIB = f"{ROOT}/19_统一相册库/library.db"
GA = "/Users/imac/.insightface/models/buffalo_l/genderage.onnx"
N_PER = 150


class Face:
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
    return np.asarray(Image.open(path).convert("RGB"))[:, :, ::-1].copy()


def main():
    from insightface.model_zoo.attribute import Attribute
    ga = Attribute(GA)
    ga.prepare(-1)

    con = sqlite3.connect(LIB)
    paths = {}
    for ck, p in con.execute("select content_key,path from files where is_primary=1"):
        paths.setdefault(ck, p)
    ck_person = {}
    for rel, ck in con.execute("select rel,content_key from files where lib='18'"):
        ck_person.setdefault(ck, rel.split("/")[0])
    ck09 = {ck for ck, in con.execute(
        "select content_key from files where lib='09' and is_primary=1")}
    rows = list(con.execute(
        "select rowid,content_key,x,y,w,h from faces where x is not null and w>=30"))

    groups = {"09幼童": [], "归档幼童": [], "归档成人": []}
    kid_p = {"乐仔", "乐仔小时候", "七月"}
    adult_p = {"艳艳", "我", "妈妈", "爸爸"}
    for rid, ck, x, y, w, h in rows:
        if ck not in paths:
            continue
        if ck in ck09:
            groups["09幼童"].append((rid, ck, x, y, w, h))
        elif ck_person.get(ck) in kid_p:
            groups["归档幼童"].append((rid, ck, x, y, w, h))
        elif ck_person.get(ck) in adult_p:
            groups["归档成人"].append((rid, ck, x, y, w, h))

    rng = random.Random(0)
    t0 = time.time()
    print(f"{'组':<10}{'n':>5}{'age中位':>8}{'age均值':>8}{'P10':>6}{'P90':>6}{'判<13岁占比':>12}{'男占比':>8}")
    res = {}
    for gname, lst in groups.items():
        sample = rng.sample(lst, min(N_PER, len(lst)))
        ages, gens = [], []
        for rid, ck, x, y, w, h in sample:
            try:
                img = imread(paths[ck])
                if img is None:
                    continue
                gd, ag = ga.get(img, Face(x, y, w, h))
            except Exception:
                continue
            ages.append(ag)
            gens.append(gd)
        a = np.array(ages)
        res[gname] = a
        print(f"{gname:<10}{len(a):>5}{np.median(a):>8.0f}{a.mean():>8.1f}"
              f"{np.percentile(a,10):>6.0f}{np.percentile(a,90):>6.0f}"
              f"{float((a<13).mean()):>12.2%}{np.mean(gens):>8.2f}")
    print(f"\n耗时 {time.time()-t0:.0f}s")
    # 区分力：幼童 vs 成人 的 AUC（用 age 反向排序）
    try:
        from sklearn.metrics import roc_auc_score
        kid = np.concatenate([res["09幼童"], res["归档幼童"]])
        ad = res["归档成人"]
        y = np.concatenate([np.zeros(len(kid)), np.ones(len(ad))])
        s = np.concatenate([kid, ad])
        print(f"\nage 区分幼童/成人 AUC = {roc_auc_score(y, s):.3f}  "
              f"(1.0=完美区分, 0.5=无区分力)")
    except Exception as e:
        print("AUC 计算失败", e)


if __name__ == "__main__":
    main()
