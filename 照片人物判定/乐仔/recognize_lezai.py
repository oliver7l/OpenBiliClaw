#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""乐仔识别器：判断一批照片里"有没有乐仔"。

模型 = lezai_model_v2.json（原型 proto + gallery 近邻库）
判据 = 照片内所有人脸与 gallery 的「top-5 平均余弦相似度」的最大值
      （LOO：排除与该照片同源的 gallery 项，避免自己匹配自己）

用法:
  # 用已有的扫描结果
  .venv-face/bin/python recognize_lezai.py --scan faces_all --src "<照片根目录>" \
      --model /Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/照片人物判定/乐仔/lezai_model_v2.json --out result_all --threshold 0.55

  # 顺便生成可点击核对页
  .venv-face/bin/python recognize_lezai.py ... --html
"""
import argparse, glob, html, json, os
import numpy as np


def l2(x):
    return x / (np.linalg.norm(x) + 1e-9)


def load_scan(scan):
    embs, metas = [], []
    for f in sorted(glob.glob(os.path.join(scan, 'p*.npz'))):
        embs.append(np.load(f)['embs'])
        metas += [json.loads(l) for l in open(f[:-4] + '.jsonl', encoding='utf-8')]
    if not embs:
        return np.zeros((0, 512), dtype=np.float32), []
    return np.vstack(embs), metas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--scan', required=True)
    ap.add_argument('--src', required=True)
    ap.add_argument('--model', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--threshold', type=float, default=0.55)
    ap.add_argument('--strict', type=float, default=0.60)
    ap.add_argument('--topk', type=int, default=5)
    ap.add_argument('--aux', default='',
                    help='辅助近邻库（婴儿期样本），与主库分别算分后取 max，避免互相稀释')
    ap.add_argument('--html', action='store_true')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    M = json.load(open(a.model, encoding='utf-8'))
    gal = np.array(M['gallery'], dtype=np.float32)
    keys = M.get('gallery_keys', [''] * len(gal))
    proto = l2(np.array(M['proto'], dtype=np.float32))

    gal_aux, keys_aux = None, []
    if a.aux:
        MA = json.load(open(a.aux, encoding='utf-8'))
        gal_aux = np.array(MA['gallery'], dtype=np.float32)
        keys_aux = MA.get('gallery_keys', [''] * len(gal_aux))
        print('辅助近邻库: %d 个样本（%s）' % (len(gal_aux), a.aux))

    embs, metas = load_scan(a.scan)
    print('待识别: %d 张脸 / %d 张照片' % (len(embs), len({m['photo'] for m in metas})))

    # gallery 按照片分块，便于 LOO
    g_base = np.array([os.path.basename(k.split(':', 1)[-1]) for k in keys])
    g_base_aux = np.array([os.path.basename(k.split(':', 1)[-1]) for k in keys_aux])
    sims_proto = embs @ proto if len(embs) else np.zeros(0)

    results = []
    by_photo = {}
    for i, m in enumerate(metas):
        by_photo.setdefault(m['photo'], []).append(i)

    for p, idx in by_photo.items():
        keep = np.array([os.path.basename(p) != b for b in g_base])
        gg = gal[keep]
        if len(gg) == 0:
            continue
        S = embs[idx] @ gg.T
        S = np.sort(S, axis=1)[:, ::-1]
        k = min(a.topk, S.shape[1])
        top5 = S[:, :k].mean(axis=1)
        max1 = S[:, 0].copy()
        # 辅助近邻库（婴儿期）：与主库「分别算分再取 max」，避免合并成一个大库后 top-5 被稀释
        if gal_aux is not None:
            keep_a = np.array([os.path.basename(p) != b for b in g_base_aux])
            ga = gal_aux[keep_a]
            if len(ga):
                SA = np.sort(embs[idx] @ ga.T, axis=1)[:, ::-1]
                kA = min(a.topk, SA.shape[1])
                top5 = np.maximum(top5, SA[:, :kA].mean(axis=1))
                max1 = np.maximum(max1, SA[:, 0])
        bi = int(np.argmax(top5))
        results.append({
            'photo': p,
            'top5': round(float(top5[bi]), 4),
            'max1': round(float(max1[bi]), 4),
            'n50': int((S[bi] >= 0.50).sum()),
            'proto_max1': round(float(sims_proto[idx].max()), 4),
            'n_faces': len(idx),
            'face_bbox': metas[idx[bi]]['bbox'],
            'face_size': metas[idx[bi]]['size'],
            'hit': bool(top5[bi] >= a.threshold),
            'level': ('高' if top5[bi] >= a.strict else
                      '中' if top5[bi] >= a.threshold else
                      '低'),
        })
    results.sort(key=lambda r: -r['top5'])
    n_hit = sum(1 for r in results if r['hit'])
    print('判定含乐仔: %d / %d 张照片 (阈值 top5>=%.2f)' % (n_hit, len(results), a.threshold))
    for lv in ('高', '中', '低'):
        c = [r for r in results if r['level'] == lv]
        print('  %s置信: %d 张 (top5 %.3f~%.3f)' % (
            lv, len(c), min([r['top5'] for r in c], default=0), max([r['top5'] for r in c], default=0)))
    json.dump(results, open(os.path.join(a.out, 'results.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)

    if a.html:
        rows = []
        for r in results[:4000]:
            color = {'高': '#0a7', '中': '#b80', '低': '#999'}[r['level']]
            rows.append(
                '<figure><img loading="lazy" src="file://%s" />'
                '<figcaption style="color:%s">%.3f %s<br><small>%s</small></figcaption></figure>'
                % (html.escape(os.path.join(a.src, r['photo'])), color, r['top5'], r['level'],
                   html.escape(os.path.basename(r['photo'])[:24])))
        doc = """<!doctype html><meta charset="utf-8"><title>乐仔识别结果</title>
<style>body{font-family:-apple-system,sans-serif;background:#fafafa;margin:16px}
h1{font-size:18px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
figure{margin:0;background:#fff;border:1px solid #eee;border-radius:6px;overflow:hidden}
img{width:100%%;height:130px;object-fit:cover;display:block}
figcaption{font-size:11px;padding:3px 4px;line-height:1.25}</style>
<h1>乐仔识别结果：%d / %d 张照片判定包含乐仔（阈值 %.2f）</h1>
<div class="grid">%s</div>""" % (n_hit, len(results), a.threshold, ''.join(rows))
        with open(os.path.join(a.out, 'report.html'), 'w', encoding='utf-8') as f:
            f.write(doc)
        print('核对页: %s/report.html' % a.out)


if __name__ == '__main__':
    main()
