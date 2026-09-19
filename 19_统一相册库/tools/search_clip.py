#!/usr/bin/env python3
"""用自然语言搜图（CLIP 语义检索）。

可与 query.py 的人物过滤组合：先按人物/SQL 缩小候选，再按语义排序；
不指定候选时全库暴力余弦（1.8 万条 512 维，毫秒级）。

用法:
  python tools/search_clip.py "小孩在草地上奔跑"
  python tools/search_clip.py "生日蛋糕吹蜡烛" --person 乐仔 --lib 08
  python tools/search_clip.py "小狗" --top 20 --html
  python tools/search_clip.py "a child riding a bike" --en     # 英文查询更准
"""
import os
import sys
import html
import sqlite3
import argparse
from urllib.parse import quote

os.nice(10)
import numpy as np
import torch
import open_clip

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
DB = f"{ROOT}/19_统一相册库/library.db"
OUT_DIR = f"{ROOT}/19_统一相册库/_review"
MODEL_NAME, PRETRAINED = "ViT-B-32-quickgelu", "openai"
LIB_NAMES = {"07": "夸克手机备份", "08": "夸克乐仔照片", "09": "QQ群相册", "18": "人物分组"}

# openai CLIP 的词表是英文 BPE，中文 prompt 效果很差 —— 内置常用场景映射，
# 命中即自动替换为对应英文 prompt（多条会取均值）。可用 --raw 强制原样查询。
ZH2EN = {
    "生日": "birthday cake with candles and a happy child",
    "蛋糕": "birthday cake with candles",
    "蛋糕吹蜡烛": "a child blowing out candles on a birthday cake",
    "雪": "snowy outdoor scene in winter",
    "雪景": "a snowy landscape covered in white snow",
    "下雨": "rainy street with umbrellas",
    "沙滩": "a beach with sand and sea",
    "海边": "seaside with ocean waves",
    "游泳": "a person swimming in a swimming pool",
    "骑车": "a child riding a bicycle",
    "自行车": "a bicycle outdoors",
    "滑板车": "a child riding a scooter",
    "跑步": "people running outdoors",
    "踢球": "children playing soccer on a field",
    "足球": "a soccer ball on grass",
    "画画": "a child drawing or painting with crayons",
    "读书": "a child reading a book",
    "看书": "a child reading a picture book",
    "吃饭": "a child eating a meal at a table",
    "睡觉": "a child sleeping in bed",
    "哭": "a crying child",
    "笑": "a smiling happy child",
    "拥抱": "people hugging each other warmly",
    "全家福": "a family portrait with parents and children",
    "合影": "a group photo of people smiling",
    "毕业": "graduation ceremony with gowns and caps",
    "表演": "children performing on a stage",
    "舞台": "a stage performance with lights",
    "运动会": "a sports day event at school with children running",
    "教室": "a classroom with desks and children",
    "幼儿园": "a kindergarten classroom with young children",
    "校车": "a yellow school bus",
    "游乐场": "a playground with slides and swings",
    "滑梯": "a child playing on a slide in a playground",
    "秋千": "a child on a swing",
    "小狗": "a cute dog",
    "狗": "a dog",
    "猫": "a cat",
    "兔子": "a rabbit",
    "花": "colorful flowers blooming",
    "树": "a green tree outdoors",
    "烟花": "colorful fireworks in the night sky",
    "夜景": "a night scene with city lights",
    "霓虹灯": "neon lights at night",
    "山": "mountains and hiking scenery",
    "湖": "a lake with calm water",
    "车内": "inside a car, driving",
    "地铁": "a subway train interior",
    "飞机": "an airplane flying or on the tarmac",
    "火车": "a train on railway tracks",
    "医院": "a hospital room or corridor",
    "超市": "a supermarket aisle with shelves",
    "电脑": "a computer screen displaying text",
    "手机截图": "a screenshot of a mobile phone app",
    "截屏": "a screenshot with text and user interface",
    "聊天记录": "a screenshot of a chat conversation with text messages",
    "文档": "a document page full of text",
    "二维码": "a QR code",
    "ppt": "a slide presentation with bullet points",
    "地图": "a map with streets",
    "快递": "cardboard packages and delivery boxes",
    "发票": "a receipt or invoice with text",
    "食物": "delicious food on a plate",
    "火锅": "a hot pot meal on a table",
    "面条": "a bowl of noodles",
    "水果": "fresh fruit on a table",
    "车": "a car parked on the street",
    "礼物": "wrapped gift boxes",
    "玩具": "colorful children toys",
    "积木": "a child playing with building blocks",
    "拼图": "a jigsaw puzzle on a table",
    "书": "a book cover",
    "作业": "a child doing homework, writing on paper",
    "洗手": "a child washing hands at a sink",
    "刷牙": "a person brushing teeth",
    "理发": "a haircut at a barber shop",
    "踢被子": "a child sleeping in bed with blanket",
    "游泳课": "children swimming in a pool during a lesson",
}


def pick_device():
    return "mps" if torch.backends.mps.is_available() else "cpu"


def candidate_keys(db, person, lib, ym_from, ym_to):
    w, p = ["f.kind='photo'", "f.is_primary=1"], []
    if person:
        w.append("""EXISTS (SELECT 1 FROM photo_person_tags t
                    WHERE t.content_key=f.content_key AND t.person=?)""")
        p.append(person)
    if lib:
        w.append("f.lib=?")
        p.append(lib)
    if ym_from:
        w.append("COALESCE(f.ym,'')>=?")
        p.append(ym_from)
    if ym_to:
        w.append("COALESCE(f.ym,'')<=?")
        p.append(ym_to)
    rows = db.execute(f"""SELECT DISTINCT f.content_key FROM files f
                          WHERE {' AND '.join(w)}""", p).fetchall()
    return {r[0] for r in rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("text")
    ap.add_argument("--person")
    ap.add_argument("--lib")
    ap.add_argument("--from", dest="ym_from")
    ap.add_argument("--to", dest="ym_to")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--min", type=float, default=0.0, help="相似度下限")
    ap.add_argument("--html", action="store_true")
    ap.add_argument("--neg", default="", help="反向提示词，如 'screenshot text document'")
    ap.add_argument("--raw", action="store_true", help="不做中文→英文映射")
    ap.add_argument("--list-zh", action="store_true", help="列出内置中文关键词")
    args = ap.parse_args()

    if args.list_zh:
        for k in ZH2EN:
            print(f"{k}\t{ZH2EN[k]}")
        return

    query = args.text.strip()
    if not args.raw:
        mapped = ZH2EN.get(query)
        if mapped:
            print(f"中文映射: {query} -> {mapped}", file=sys.stderr)
            query = mapped

    db = sqlite3.connect(DB)
    rows = db.execute("SELECT content_key, vec FROM clips").fetchall()
    if not rows:
        print("clips 表为空，先跑 tools/clip_embed.py", file=sys.stderr)
        sys.exit(1)
    keys = [r[0] for r in rows]
    X = np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
    X = X / np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-9)

    if args.person or args.lib or args.ym_from or args.ym_to:
        allow = candidate_keys(db, args.person, args.lib, args.ym_from, args.ym_to)
        idx = [i for i, k in enumerate(keys) if k in allow]
        print(f"候选 {len(idx)} / {len(keys)}", file=sys.stderr)
    else:
        idx = list(range(len(keys)))
    if not idx:
        print("无候选照片")
        return
    Xi = X[idx]

    device = pick_device()
    model, _, _ = open_clip.create_model_and_transforms(
        MODEL_NAME, pretrained=PRETRAINED)
    model = model.to(device).eval()
    tok = open_clip.get_tokenizer(MODEL_NAME)
    with torch.no_grad():
        t = tok([query]).to(device)
        q = model.encode_text(t)
        q = q / q.norm(dim=-1, keepdim=True)
        if args.neg:
            n = tok([args.neg]).to(device)
            qn = model.encode_text(n)
            qn = qn / qn.norm(dim=-1, keepdim=True)
            q = q - qn
            q = q / q.norm(dim=-1, keepdim=True)
    sims = (Xi @ q.float().cpu().numpy().T).ravel()
    order = np.argsort(-sims)
    order = [i for i in order if sims[i] >= args.min][:args.top]

    results = []
    for i in order:
        ck = keys[idx[i]]
        row = db.execute(
            "SELECT path, lib, rel, ym FROM files WHERE content_key=? AND is_primary=1",
            (ck,)).fetchone()
        if row:
            results.append((float(sims[i]),) + row)
    print(f"「{args.text}」命中 {len(results)}", file=sys.stderr)
    for r in results:
        print(f"{r[0]:.3f}  {r[1]}")

    if args.html:
        cards = []
        for s, path, lib, rel, ym in results:
            if not os.path.exists(path):
                continue
            u = "file://" + quote(path)
            cards.append(
                f'<a class="card" href="{u}" target="_blank" '
                f'title="{html.escape(LIB_NAMES.get(lib,lib)+" | "+rel)}">'
                f'<img loading="lazy" src="{u}">'
                f'<span class="tag">{s:.3f} {html.escape(LIB_NAMES.get(lib,lib))}</span></a>')
        doc = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>%s</title><style>
body{font-family:-apple-system,"PingFang SC",sans-serif;background:#f6f7f9;color:#1c1e21;margin:0;padding:20px}
h1{font-size:18px}p.sub{color:#666;font-size:13px;margin:4px 0 16px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:10px}
.card{position:relative;display:block;height:170px;overflow:hidden;border-radius:8px;background:#eee}
.card img{width:100%%;height:100%%;object-fit:cover;display:block}
.tag{position:absolute;left:6px;bottom:6px;background:rgba(0,0,0,.62);color:#fff;font-size:11px;
padding:2px 6px;border-radius:4px}</style></head><body><h1>语义检索：%s</h1>
<p class="sub">%d 张 · 角标为相似度，点击看原图</p><div class="grid">%s</div></body></html>""" % (
            html.escape(args.text), html.escape(args.text), len(cards), "".join(cards))
        os.makedirs(OUT_DIR, exist_ok=True)
        out = f"{OUT_DIR}/语义检索_{args.text[:20].replace('/', '／')}.html"
        with open(out, "w", encoding="utf-8") as f:
            f.write(doc)
        print("画廊页:", out)


if __name__ == "__main__":
    main()
