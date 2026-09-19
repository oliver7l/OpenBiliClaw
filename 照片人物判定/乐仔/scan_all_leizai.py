# -*- coding: utf-8 -*-
"""全库扫描：用乐仔模型组过所有非乐仔桶的脸（含妈妈/艳艳等已命名桶、复核堆、单脸）。
- 命中且当前是 B级/复核/未命名 -> 直接转移（入桶仍标 B 待察）
- 命中但当前是 A级（已判准） -> 只报告不移动，等用户指认
"""
import sqlite3, numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.cluster import KMeans

RS = 42
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
TIERS = {r[0]: r[1] for r in cur.execute('SELECT face_id, tier FROM lib_tiers')}

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
    if tp < 5 or fp / max(1, len(neg_te)) > FPR_GATE:
        return None
    return models

def build_space(E, tag):
    rng = np.random.RandomState(RS)
    neg_base = []
    for c in PLIST:
        neg_base += core_seeds(c)
    pool = [f for f in ids_all if CLS[f] not in set(PERSONS)]
    rng.shuffle(pool)
    neg_base += pool[:2000]
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
        else:
            print('  [%s] %s 模型组未达标' % (tag, PERSONS[c]))
    return out

G1 = build_space(E1, 'mbf')
G2 = build_space(E2, 'r50')
base = sorted(set(G1) & set(G2))
recv = [c for c in RECEIVERS if c in base]
print('双空间达标人物:', ', '.join(PERSONS[c] for c in base))
print('乐仔接收桶:', ', '.join(PERSONS[c] for c in recv))

# 同照互斥
leizai_files = {c: {FILE_OF[f] for f in ids_all if CLS[f] == c} for c in RECEIVERS}

# 候选 = 所有非乐仔桶的脸
cand = [f for f in ids_all if CLS[f] not in RECEIVERS]
print('扫描范围: %d 张（全部非乐仔桶，含已命名桶/复核堆/未命名/单脸）' % len(cand))

def score_space(groups, E, fs):
    X = np.vstack([E[f] for f in fs])
    S, H = {}, {}
    for c, models in groups.items():
        P = np.column_stack([m[1].predict_proba(X)[:, 1] for m in models])
        TH = np.array([m[2] for m in models])
        S[c] = P.max(1)
        H[c] = (P >= TH).any(1)
    return S, H

S1, H1 = score_space(G1, E1, cand)
S2, H2 = score_space(G2, E2, cand)

moved, report_a = {}, []
n_veto = 0
for i, f in enumerate(cand):
    src = CLS[f]
    for c in recv:
        if FILE_OF.get(f) in leizai_files[c]:
            n_veto += 1
            continue
        if not (H1[c][i] and H2[c][i]):
            continue
        o1 = max((S1[cc][i], cc) for cc in base)
        o2 = max((S2[cc][i], cc) for cc in base)
        if o1[1] != c or o2[1] != c:
            continue
        r1 = max((S1[cc][i] for cc in base if cc != c), default=0)
        r2 = max((S2[cc][i] for cc in base if cc != c), default=0)
        if o1[0] - r1 < MARGIN or o2[0] - r2 < MARGIN:
            continue
        if src in PERSONS and TIERS.get(f) == 'A':
            report_a.append((f, src, PERSONS[src], round(float(min(o1[0]-r1, o2[0]-r2)), 3)))
        else:
            cur.execute('UPDATE faces SET cluster=? WHERE id=?', (c, f))
            cur.execute('INSERT OR REPLACE INTO lib_tiers VALUES(?,?)', (f, 'B'))
            CLS[f] = c
            key = PERSONS[src] if src in PERSONS else ('#%d' % src if src != -1 else '单脸')
            moved[key] = moved.get(key, 0) + 1
        break

con.commit()
print('\n同照互斥拦截:', n_veto)
print('=== 从其他桶/复核/未命名 转入乐仔 ===')
for k, v in sorted(moved.items(), key=lambda x: -x[1]):
    print('  来自 %s: %d 张' % (k, v))
print('  合计:', sum(moved.values()))
print('=== A级冲突（已判准但强烈指向乐仔，只报告未动）===')
for f, src, nm, mg in report_a[:15]:
    print('  face %d: %s(%s) margin=%s' % (f, nm, src, mg))
print('  合计:', len(report_a))

cur.execute('CREATE TABLE IF NOT EXISTS face_locks(face_id INTEGER PRIMARY KEY)')
cur.executemany('INSERT OR IGNORE INTO face_locks VALUES(?)',
                [(f,) for f in ids_all if CLS[f] in set(PERSONS)])
con.commit()
print('锁表:', cur.execute('SELECT COUNT(*) FROM face_locks').fetchone()[0])
for r in cur.execute('''SELECT p.cluster,p.name,COUNT(f.id) FROM persons p
        LEFT JOIN faces f ON f.cluster=p.cluster GROUP BY p.cluster ORDER BY COUNT(f.id) DESC'''):
    print('  #%-5d %s: %d' % (r[0], r[1], r[2]))
con.close()
