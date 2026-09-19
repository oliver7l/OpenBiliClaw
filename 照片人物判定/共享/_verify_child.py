"""幼童判别器人工验证：按组抽人脸裁剪，拼成 3x2 联系表供肉眼确认。"""
import sqlite3, os, tempfile, subprocess, json, sys
import numpy as np
from PIL import Image, ImageDraw

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
LIB = f"{ROOT}/19_统一相册库/library.db"
OUT = "/tmp/child_verify"


def load_img(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".heic", ".heif"):
        tmp = tempfile.mktemp(suffix=".jpg")
        subprocess.run(["sips", "-s", "format", "jpeg", path, "--out", tmp],
                       capture_output=True)
        path = tmp
    from PIL import ImageOps
    return ImageOps.exif_transpose(Image.open(path)).convert("RGB")


def crop(path, box, size=170, pad=0.4):
    try:
        im = load_img(path)
    except Exception:
        return None
    W, H = im.size
    x, y, w, h = box
    px = int(w * pad)
    x0, y0 = max(0, x - px), max(0, y - px)
    x1, y1 = min(W, x + w + px), min(H, y + h + px)
    if x1 <= x0 or y1 <= y0:
        return None
    c = im.crop((x0, y0, x1, y1)).resize((size, size), Image.LANCZOS)
    return c


def grid(items, path, cols=3):
    """items: [(img, label)]"""
    size = items[0][0].size[0]
    rows = (len(items) + cols - 1) // cols
    pad = 26
    g = Image.new("RGB", (cols * size, rows * (size + pad)), (25, 25, 28))
    d = ImageDraw.Draw(g)
    for k, (im, lb) in enumerate(items):
        r, c = divmod(k, cols)
        g.paste(im, (c * size, r * (size + pad) + pad))
        d.text((c * size + 4, r * (size + pad) + 6), lb, fill=(235, 235, 240))
    g.save(path, quality=88)
    print(path)


def main():
    os.makedirs(OUT, exist_ok=True)
    con = sqlite3.connect(LIB)
    paths, ck_lib = {}, {}
    for ck, p, lib in con.execute(
            "select content_key,path,lib from files where is_primary=1"):
        paths.setdefault(ck, p)
        ck_lib.setdefault(ck, lib)
    rows = list(con.execute("""
        select f.rowid, f.content_key, f.x, f.y, f.w, f.h, a.p_child
        from faces f join face_attrs a on a.face_rid=f.rowid
        where f.x is not null"""))
    print("可验证脸数", len(rows))

    def sample(cond, n, reverse=True, tag=""):
        cand = [r for r in rows if cond(r)]
        cand.sort(key=lambda r: r[6], reverse=reverse)
        out = []
        for rid, ck, x, y, w, h, p in cand[:n]:
            if ck not in paths or w < 12:
                continue
            c = crop(paths[ck], (x, y, w, h))
            if c is None:
                continue
            out.append((c, f"{tag} p={p:.2f} side={int(max(w,h))}"))
        return out

    is09 = lambda r: ck_lib.get(r[1]) == "09"
    ishome = lambda r: ck_lib.get(r[1]) in ("07", "08")

    g1 = sample(lambda r: is09(r), 6, True, "09-kid?")
    g2 = sample(lambda r: is09(r), 6, False, "09-adult?")
    g3 = sample(lambda r: ishome(r), 6, True, "home-kid?")
    g4 = sample(lambda r: ishome(r), 6, False, "home-adult?")
    g5 = sample(lambda r: 0.40 <= r[6] <= 0.60, 6, True, "edge")

    for name, g in (("09_high", g1), ("09_low", g2), ("home_high", g3),
                    ("home_low", g4), ("edge", g5)):
        if g:
            grid(g, f"{OUT}/{name}.jpg")


if __name__ == "__main__":
    main()
