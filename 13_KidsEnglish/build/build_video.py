#!/usr/bin/env python
"""小火车英语课堂 · 视频加工脚本
把单词卡（PIL 彩色emoji）+ TTS 发音（macOS say）烧进 Chuggington 视频。
用法: build_video.py <input.mp4> <output.mp4>
"""
import subprocess, sys, os, json
from PIL import Image, ImageFont, ImageDraw

BUILD = "/tmp/kidsenglish"  # 中间文件放内盘（外接盘删临时文件会触发安全钩子）
EMOJI_FONT = "/System/Library/Fonts/Apple Color Emoji.ttc"
EN_FONT = "/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf"
ZH_FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"
VOICE = "Samantha"

# (时间点秒, 英文, 中文, emoji, TTS台词)
WORDS = [
    (75,   "train",   "火车",   "🚂", "Train! Train! The train goes chugga chugga choo choo! Train!"),
    (215,  "tunnel",  "隧道",   "🕳️", "Tunnel! Through the tunnel! Whoosh! Tunnel!"),
    (355,  "bridge",  "桥",     "🌉", "Bridge! Across the bridge! Careful! Bridge!"),
    (495,  "wheels",  "车轮",   "🛞", "Wheels! The wheels go round and round! Wheels!"),
    (635,  "whistle", "汽笛",   "📢", "Whistle! Toot toot! Hear the whistle! Whistle!"),
    (775,  "fast",    "快",     "💨", "Fast! Go go go, so fast! Fast!"),
    (915,  "stop",    "停",     "✋", "Stop! Stop! Red light, stop! Stop!"),
    (1055, "friend",  "朋友",   "🤝", "Friend! Wilson and Brewster are good friends! Friend!"),
]
INTRO = (1.5, "🚂", "Let's Learn English!", "Welcome to Chuggington! Let's learn English! Choo choo!")
CARD_DUR = 8  # 每张卡停留秒数

def emoji_img(emoji, px):
    """Apple Color Emoji 只支持固定位图字号，按160渲染再缩放。"""
    fe = ImageFont.truetype(EMOJI_FONT, 160)
    tmp = Image.new("RGBA", (320, 320), (0, 0, 0, 0))
    d = ImageDraw.Draw(tmp)
    d.text((160, 160), emoji, font=fe, embedded_color=True, anchor="mm")
    bbox = tmp.getbbox()
    if bbox:
        tmp = tmp.crop(bbox)
    h = px
    w = int(tmp.width * px / tmp.height)
    return tmp.resize((w, h), Image.LANCZOS)

def paste_emoji(canvas, emoji, cx, cy, px):
    im = emoji_img(emoji, px)
    canvas.alpha_composite(im, (int(cx - im.width / 2), int(cy - im.height / 2)))

def rounded(draw, xy, r, fill):
    draw.rounded_rectangle(xy, radius=r, fill=fill)

def make_card(path, emoji, en, zh, sent, W, H):
    cw, ch = int(W*0.56), int(H*0.72)
    scale = cw/1200
    card = Image.new("RGBA", (cw, ch), (0,0,0,0))
    d = ImageDraw.Draw(card)
    rounded(d, (0,0,cw-1,ch-1), int(46*scale), (255,179,71,255))       # 橙色边框底
    rounded(d, (int(14*scale),)*2 + (cw-int(14*scale), ch-int(14*scale)), int(38*scale), (255,255,255,255))
    cx = cw/2
    # emoji
    paste_emoji(card, emoji, cx, int(ch*0.26), int(300*scale))
    # 英文
    fen = ImageFont.truetype(EN_FONT, int(132*scale))
    d.text((cx, int(ch*0.55)), en, font=fen, fill=(30,107,184,255), anchor="mm")
    # 中文
    fzh = ImageFont.truetype(ZH_FONT, int(64*scale))
    d.text((cx, int(ch*0.70)), zh, font=fzh, fill=(68,68,68,255), anchor="mm")
    # 例句
    fs = ImageFont.truetype(EN_FONT, int(46*scale))
    d.text((cx, int(ch*0.85)), sent, font=fs, fill=(122,138,153,255), anchor="mm")
    card.save(path)
    return cw, ch

def make_intro(path, W, H):
    cw, ch = int(W*0.56), int(H*0.72)
    scale = cw/1200
    card = Image.new("RGBA", (cw, ch), (0,0,0,0))
    d = ImageDraw.Draw(card)
    rounded(d, (0,0,cw-1,ch-1), int(46*scale), (76,175,80,255))
    rounded(d, (int(14*scale),)*2 + (cw-int(14*scale), ch-int(14*scale)), int(38*scale), (255,255,255,255))
    cx = cw/2
    paste_emoji(card, "🚂📚", cx, int(ch*0.28), int(280*scale))
    d.text((cx, int(ch*0.56)), "Let's Learn English!", font=ImageFont.truetype(EN_FONT, int(96*scale)), fill=(56,142,60,255), anchor="mm")
    d.text((cx, int(ch*0.74)), "小火车英语课堂 · 上车喽！", font=ImageFont.truetype(ZH_FONT, int(64*scale)), fill=(68,68,68,255), anchor="mm")
    card.save(path)
    return cw, ch

def tts(text, path_wav):
    aiff = path_wav.replace(".wav", ".aiff")
    subprocess.run(["say", "-v", VOICE, "-r", "140", "-o", aiff, text], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", aiff,
                    "-ar", "44100", "-ac", "2", path_wav], check=True)
    os.remove(aiff)

def main(inp, outp):
    probe = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json",
                            "-show_streams", "-select_streams", "v:0", inp],
                           capture_output=True, text=True, check=True)
    meta = json.loads(probe.stdout)["streams"][0]
    W, H = meta["width"], meta["height"]
    dur = float(meta.get("duration", 0))
    print(f"input: {W}x{H}, {dur:.0f}s")

    cards = []  # (t, png, wav)
    # 开场卡
    p = os.path.join(BUILD, "card_00.png"); make_intro(p, W, H)
    w = os.path.join(BUILD, "tts_00.wav"); tts(INTRO[3], w)
    cards.append((INTRO[0], p, w))
    for i, (t, en, zh, emo, line) in enumerate(WORDS):
        sent = line.split("! ", 1)[1].rsplit("! ", 1)[0] if "! " in line else en
        sent = {"train": "The train goes chugga chugga!", "tunnel": "Through the tunnel!",
                "bridge": "Across the bridge!", "wheels": "The wheels go round and round!",
                "whistle": "Toot toot! Hear the whistle!", "fast": "Go, go, go — so fast!",
                "stop": "Red light — stop!", "friend": "Wilson and Brewster are friends!"}[en]
        p = os.path.join(BUILD, f"card_{i+1:02d}.png"); make_card(p, emo, en, zh, sent, W, H)
        w = os.path.join(BUILD, f"tts_{i+1:02d}.wav"); tts(line, w)
        cards.append((t, p, w))
        print(f"  card {i+1}/8 ready: {en} @ {t}s")

    # ---- ffmpeg filtergraph ----
    n = len(cards)
    inputs = ["-i", inp]
    for _, p, _w in cards: inputs += ["-i", p]
    for _, _p, w in cards: inputs += ["-i", w]
    # idx: 0=video, 1..n=png, n+1..2n=wav
    windows = "+".join(f"between(t,{t:.1f},{t+CARD_DUR:.1f})" for t, *_ in cards)
    fg = []
    fg.append(f"[0:v]drawbox=x=0:y=0:w=iw:h=ih:color=black@0.38:t=fill:enable='{windows}'[bv]")
    prev = "bv"
    for i, (t, _p, _w) in enumerate(cards):
        out = f"v{i+1}"
        fg.append(f"[{prev}][{i+1}:v]overlay=(W-w)/2:(H-h)/2:enable='between(t,{t:.1f},{t+CARD_DUR:.1f})'[{out}]")
        prev = out
    fg.append(f"[{prev}]format=yuv420p[vout]")
    duck = f"1-0.72*({windows})"
    fg.append(f"[0:a]volume='{duck}':eval=frame[duck]")
    aidx = []
    for i, (t, _p, _w) in enumerate(cards):
        ms = int(t*1000)
        fg.append(f"[{n+1+i}:a]adelay={ms}|{ms}[a{i}]")
        aidx.append(f"[a{i}]")
    fg.append("[duck]" + "".join(aidx) + f"amix=inputs={n+1}:duration=first:normalize=0[aout]")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-stats"] + inputs + [
        "-filter_complex", ";".join(fg), "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-crf", "20", "-preset", "medium",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", outp]
    print("encoding...")
    subprocess.run(cmd, check=True)
    print("DONE:", outp)

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
