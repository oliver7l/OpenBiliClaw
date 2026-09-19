# -*- coding: utf-8 -*-
"""乐仔专项：模型组(综合+kmeans2子模型) x 双空间(mbf/r50) x 同照互斥，多轮入桶。
接收桶：#1 乐仔(成年) / #2500 乐仔小时候；其他人只做 margin 基线，不接收。
"""
import sqlite3, numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.cluster import KMeans

RS = 42
ROUND_MAX = 6
MARGIN = 0.05
FPR_GATE = 0.02
RECEIVERS = (1, 2500)

def l2(v):
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else v

PERSONS = {1: '乐仔(成年)', 2500: '乐仔小时候', 2: '艳艳', 3: '我', 4: '妈妈',
           6: '七月', 10: '爸爸', 21: '豆包', 24: '马斯克', 35: '彭超',
           4669: '南灵栩', 4278: '王阅熹', 4342: '刘峰'}
PLIST = list(PERSONS)

con = sqlite3.connect('_photo_index/photo_index.db')
cur = con.cursor()
rows = con.execute('SELECT id, cluster, det_score, embedding, file_key FROM faces').fetchall()
CLS = {r[0]: int(r[1]) for r in rows}
DET = {r[0]: float(r[2]) for r in rows}
E1 = {r[0]: l2(np.frombuffer(r[3], dtype=np.float32).copy()) for r in rows}
FILE_OF = {r[0]: r[4] for r in rows}
d = np.load('_photo_index/r50_sample_emb.npz')
E2 = {int(f): l2(np.asarray(e, dtype=np.float32)) for f, e in zip(d['ids'], d['emb'])}
ids_all = sorted(E1)

def core_seeds(c, cap=300):
    fs = [f for f in ids_all if CLS[f] == c and DET[f] >= 0.6]
    if len(fs) < 15:
        fs = [f for f in ids_all if CLS[f] == c]
    if not fs:
        return []
    prov = l2(np.mean([E1[f] for f in fs], axis=0))
    sims = sorted(((float(E1[f] @ prov), f) for f in fs), reverse=True)
    return [f for _, f in sims[:min(cap, max(10, int(len(sims) * 0.8)))]]

def build_group(E, pos_tr, pos_te, neg_tr, neg_te):
    Xtr = np.vstack([E[f] for f in pos_tr])
    groups = [(pos_tr, 'all')]
    if len(pos_tr) >= 40:
        km = KMeans(n_clusters=2, n_init=5, random_state=RS).fit(Xtr)
        g0 = [f for f, lb in zip(pos_tr, km.labels_) if lb == 0]
        g1 = [f for f, lb in zip(pos_tr, km.labels_) if lb == 1]
        if len(g0) >= 15 and len(g1) >= 15:
            groups = [(g0, 'sub0'), (g1, 'sub1'), (pos_tr, 'all')]
    models = []
    for gpos, tag in groups:
        clf = LogisticRegression(max_iter=2000, class_weight='balanced', C=1.0)
        clf.fit(np.vstack([E[f] for f in gpos + neg_tr]),
                np.array([1] * len(gpos) + [0] * len(neg_tr)))
        s = clf.predict_proba(np.vstack([E[f] for f in pos_te + neg_te]))[:, 1]
        y = np.array([1] * len(pos_te) + [0] * len(neg_te))
        th = float(np.percentile(s[y == 0], 99.5)) + 1e-6
        models.append((tag, clf, th))
    hp = np.zeros(len(pos_te), dtype=bool)
    hn = np.zeros(len(neg_te), dtype=bool)
    for tag, clf, th in models:
        hp |= clf.predict_proba(np.vstack([E[f] for f in pos_te]))[:, 1] >= th
        hn |= clf.predict_proba(np.vstack([E[f] for f in neg_te]))[:, 1] >= th
    tp, fp = int(hp.sum()), int(hn.sum())
    fpr = fp / max(1, len(neg_te))
    if tp < 5 or fpr > FPR_GATE:
        return None
    return (models, tp, fpr)

def build_space(E, pool, tag):
    rng = np.random.RandomState(RS)
    neg_base = []
    for c in PLIST:
        neg_base += core_seeds(c)
    pl = list(pool); rng.shuffle(pl); neg_base += pl[:2000]
    out = {}
    for c in PLIST:
        seeds = core_seeds(c)
        if len(seeds) < 20:
            continue
        neg = [f for f in neg_base if CLS[f] != c]
        rng.shuffle(seeds); rng.shuffle(neg)
        nh = max(10, int(len(seeds) * 0.2))
        pos_te, pos_tr = seeds[:nh], seeds[nh:]
        ng = max(40, int(len(neg) * 0.2))
        neg_te, neg_tr = neg[:ng], neg[ng:]
        g = build_group(E, pos_tr, pos_te, neg_tr, neg_te)
        if g:
            out[c] = g
        elif c in RECEIVERS:
            print('    [%s] %s 模型组未达标(tp=%s)' % (tag, PERSONS[c], '低'))
    return out

def score_space(groups, E, fs):
    X = np.vstack([E[f] for f in fs])
    S, H = {}, {}
    for c, (models, _, _) in groups.items():
        P = np.column_stack([m[1].predict_proba(X)[:, 1] for m in models])
        TH = np.array([m[2] for m in models])
        S[c] = P.max(1)
        H[c] = (P >= TH).any(1)
    return S, H

def run_round(rnd):
    pool = [f for f in ids_all if CLS[f] not in set(PERSONS)]
    if not pool:
        return {}
    G1 = build_space(E1, pool, 'mbf')
    G2 = build_space(E2, pool, 'r50')
    base = sorted(set(G1) & set(G2))
    recv = [c for c in RECEIVERS if c in base]
    if not recv:
        print('  [round %d] 乐仔模型组双空间未同时达标，停' % rnd)
        return {}
    fs = sorted(pool)
    S1, H1 = score_space(G1, E1, fs)
    S2, H2 = score_space(G2, E2, fs)
    # 同照互斥：照片已含该桶确认脸 -> 该候选不可能再入该桶
    veto = {c: set() for c in recv}
    for f in pool:
        fk = FILE_OF.get(f)
        for c in recv:
            if fk in leizai_files[c]:
                veto[c].add(f)
    n_mv = 0
    per = {}
    for i, f in enumerate(fs):
        for c in recv:
            if f in veto[c]:
                continue
            if not (H1[c][i] and H2[c][i]):
                continue
            # margin：该人得分须领先其他所有人（双空间）
            o1 = max((S1[cc][i], cc) for cc in base)
            o2 = max((S2[cc][i], cc) for cc in base)
            if o1[1] != c or o2[1] != c:
                continue
            r1 = max((S1[cc][i] for cc in base if cc != c), default=0)
            r2 = max((S2[cc][i] for cc in base if cc != c), default=0)
            if o1[0] - r1 >= MARGIN and o2[0] - r2 >= MARGIN:
                cur.execute('UPDATE faces SET cluster=? WHERE id=?', (c, f))
                cur.execute('INSERT OR REPLACE INTO lib_tiers VALUES(?,?)', (f, 'B'))
                CLS[f] = c
                per[PERSONS[c]] = per.get(PERSONS[c], 0) + 1
                n_mv += 1
    con.commit()
    print('  [round %d] 候选 %d -> 入桶 %d  %s' % (rnd, len(pool), n_mv, per))
    return per

leizai_files = {}
for c in RECEIVERS:
    leizai_files[c] = {FILE_OF[f] for f in ids_all if CLS[f] == c}

total = {}
for r in range(1, ROUND_MAX + 1):
    mv = run_round(r)
    if not mv:
        break
    for k, v in mv.items():
        total[k] = total.get(k, 0) + v

print('\n=== 乐仔专项累计入桶 ===')
for k, v in sorted(total.items(), key=lambda x: -x[1]):
    print('  %s: +%d' % (k, v))
print('  合计: +%d' % sum(total.values()))

cur.execute('CREATE TABLE IF NOT EXISTS face_locks(face_id INTEGER PRIMARY KEY)')
cur.executemany('INSERT OR IGNORE INTO face_locks VALUES(?)',
                [(f,) for f in ids_all if CLS[f] in set(PERSONS)])
con.commit()
print('锁表:', cur.execute('SELECT COUNT(*) FROM face_locks').fetchone()[0])
for r in cur.execute('''SELECT p.cluster,p.name,COUNT(f.id) FROM persons p
        LEFT JOIN faces f ON f.cluster=p.cluster GROUP BY p.cluster ORDER BY COUNT(f.id) DESC'''):
    print('  #%-5d %s: %d' % (r[0], r[1], r[2]))
con.close()
