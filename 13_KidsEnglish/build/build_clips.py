#!/usr/bin/env python
"""小火车英语课堂 · 三分钟切片版
把正片切成 ~3 分钟一小集，每集按节奏弹单词卡。
默认模式：每 1 分钟一张（60s/120s）。
dense 模式（第二个参数传 dense）：每 15 秒一张新卡，闪卡轮播。
词表循环使用（自动复习前面的词）。
用法: build_clips.py <input.mp4> <output_dir> [dense]
"""
import subprocess, sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_video import BUILD, WORDS, INTRO, CARD_DUR, make_card, make_intro, tts
import build_subs

_flags = sys.argv[3].split() if len(sys.argv) > 3 else []
SUBS = "sub" in _flags    # 参数含 sub 则烧双语字幕
DENSE = "dense" in _flags  # 参数含 dense 则每15秒弹卡
# 系统 ffmpeg 8.1 无 libass，字幕版必须用带 libass 的静态版
FFMPEG = "/tmp/kidsenglish/ffmpeg" if SUBS else "ffmpeg"
SUB_STYLE = "FontName=Hiragino Sans GB,FontSize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=1.2,Shadow=0.6,MarginV=18"

CLIP_LEN = 180          # 每集 3 分钟
CARD_TIMES = [60, 120]  # 默认：每集第 1、2 分钟各一张卡
if DENSE:
    CARD_TIMES = list(range(0, CLIP_LEN, 15))  # 每 15 秒一张
INTRO_ONLY_FIRST = True

def main(inp, outdir):
    os.makedirs(outdir, exist_ok=True)
    probe = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json",
                            "-show_streams", "-show_format", inp],
                           capture_output=True, text=True, check=True)
    meta = json.loads(probe.stdout)
    W = H = None
    for s in meta["streams"]:
        if s["codec_type"] == "video":
            W, H = s["width"], s["height"]; break
    dur = float(meta["format"]["duration"])
    n_clips = -(-int(dur) // CLIP_LEN)
    print(f"input: {W}x{H}, {dur:.0f}s -> {n_clips} clips")

    # ---- 生成卡片图 + 发音（每词一份，循环复用）----
    pngs, wavs = {}, {}
    p = os.path.join(BUILD, "clip_intro.png"); make_intro(p, W, H)
    w = os.path.join(BUILD, "clip_tts_intro.wav"); tts(INTRO[3], w)
    intro = (p, w)
    for i, (t, en, zh, emo, line) in enumerate(WORDS):
        sent = {"train": "The train goes chugga chugga!", "tunnel": "Through the tunnel!",
                "bridge": "Across the bridge!", "wheels": "The wheels go round and round!",
                "whistle": "Toot toot! Hear the whistle!", "fast": "Go, go, go — so fast!",
                "stop": "Red light — stop!", "friend": "Wilson and Brewster are friends!"}[en]
        p = os.path.join(BUILD, f"clip_card_{en}.png"); make_card(p, emo, en, zh, sent, W, H)
        w = os.path.join(BUILD, f"clip_tts_{en}.wav"); tts(line, w)
        pngs[en], wavs[en] = p, w
        print(f"  asset ready: {en}")

    words_cycle = [x[1] for x in WORDS]  # 英文名循环
    word_idx = [0]  # 全局词序号，跨集连续循环
    # 语音区间（用于把弹卡点挪到没人说话的位置）：不管是否烧字幕都加载
    cues = build_subs.parse_srt("/tmp/kidsenglish/audio.srt")
    # 每个 TTS 的实际朗读时长：卡出现后的这段时间里尽量不要有原声对白
    tts_dur = {}
    for en in set(list(wavs.keys()) + ["intro"]):
        w = os.path.join(BUILD, f"clip_tts_{en}.wav")
        r = subprocess.run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                            "-of", "csv=p=0", w], capture_output=True, text=True, check=True)
        tts_dur[en] = float(r.stdout.strip())
    SEARCH = 10.0   # 目标点前后各搜 10 秒找安静位置
    STEP = 0.5

    def speech_ivs(s0, d0):
        ivs = []
        for a, b, _t in cues:
            ra, rb = a - s0, b - s0
            if rb <= 0 or ra >= d0:
                continue
            ivs.append((max(0.0, ra), min(d0, rb)))
        return ivs

    def ov(ivs, a, b):
        o = 0.0
        for x, y in ivs:
            lo, hi = max(a, x), min(b, y)
            if hi > lo:
                o += hi - lo
        return o

    for ci in range(n_clips):
        start = ci * CLIP_LEN
        this_dur = min(CLIP_LEN, dur - start)
        ivs = speech_ivs(start, this_dur)
        cards = []  # (相对时间, png, wav)
        times = list(CARD_TIMES)
        if ci == 0 or not INTRO_ONLY_FIRST:
            cards.append((INTRO[0], intro[0], intro[1]))
            if DENSE and 0 in times:
                times.remove(0)  # 开场卡占 0s，词卡从 15s 开始
        placed = [(INTRO[0], INTRO[0] + CARD_DUR)] if cards else []
        for ct in times:
            if ct + CARD_DUR > this_dur:
                continue
            word = words_cycle[word_idx[0] % len(words_cycle)]
            word_idx[0] += 1
            # 在目标点附近找「TTS 朗读期间原声对白重合最少」的落卡位置
            need = tts_dur[word] + 0.6
            lo = max(0.0, ct - SEARCH)
            hi = min(this_dur - CARD_DUR, ct + SEARCH)
            best = None
            t0 = lo
            while t0 <= hi + 1e-9:
                ok = all(t0 >= e + 0.5 or t0 + CARD_DUR <= s - 0.5 for s, e in placed)
                if ok:
                    key = (ov(ivs, t0, t0 + need),   # 朗读段的人声重合（首要）
                           ov(ivs, t0, t0 + CARD_DUR),  # 整卡窗口的人声重合（次要）
                           abs(t0 - ct))                # 离目标点别太远（再次）
                    if best is None or key < best[0]:
                        best = (key, t0)
                t0 += STEP
            t0 = best[1] if best else float(ct)
            placed.append((t0, t0 + CARD_DUR))
            cards.append((t0, pngs[word], wavs[word]))

        outp = os.path.join(outdir, f"小火车英语课堂-EP1-{ci+1:02d}.mp4")
        n = len(cards)
        inputs = ["-ss", str(start), "-t", f"{this_dur:.2f}", "-i", inp]
        for _, p, _w in cards: inputs += ["-i", p]
        for _, _p, w in cards: inputs += ["-i", w]
        windows = "+".join(f"between(t,{t:.1f},{t+CARD_DUR:.1f})" for t, *_ in cards)
        fg = []
        fg.append(f"[0:v]drawbox=x=0:y=0:w=iw:h=ih:color=black@0.38:t=fill:enable='{windows}'[bv]")
        prev = "bv"
        for i, (t, _p, _w) in enumerate(cards):
            out = f"v{i+1}"
            fg.append(f"[{prev}][{i+1}:v]overlay=(W-w)/2:(H-h)/2:enable='between(t,{t:.1f},{t+CARD_DUR:.1f})'[{out}]")
            prev = out
        fg.append(f"[{prev}]format=yuv420p[vsub]")
        if SUBS:
            srt_path = os.path.join(BUILD, f"clip_sub_{ci+1:02d}.srt")
            build_subs.write_clip_srt(cues, start, this_dur, srt_path, skip_ivs=placed)
            fg.append(f"[vsub]subtitles={srt_path}:force_style='{SUB_STYLE}'[vout]")
        else:
            fg.append("[vsub]null[vout]")
        duck = f"1-0.85*({windows})"
        fg.append(f"[0:a]volume='{duck}':eval=frame[duck]")
        aidx = []
        for i, (t, _p, _w) in enumerate(cards):
            ms = int(t * 1000)
            fg.append(f"[{n+1+i}:a]adelay={ms}|{ms}[a{i}]")
            aidx.append(f"[a{i}]")
        fg.append("[duck]" + "".join(aidx) + f"amix=inputs={n+1}:duration=first:normalize=0[aout]")
        cmd = [FFMPEG, "-y", "-loglevel", "error", "-stats"] + inputs + [
            "-filter_complex", ";".join(fg), "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-crf", "20", "-preset", "medium",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", outp]
        print(f"encoding clip {ci+1}/{n_clips}: " + ", ".join(c[2].split("clip_tts_")[1].replace(".wav","") for c in cards))
        subprocess.run(cmd, check=True)
        print(f"  DONE: {outp}")
    print("ALL DONE")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
