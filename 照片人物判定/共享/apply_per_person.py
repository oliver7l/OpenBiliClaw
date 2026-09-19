#!/usr/bin/env python3
"""用「每人一个二分类器」对全库人脸做开集判定。

与之前 7 类 softmax 的区别：
  * 每张脸独立过 N 个二分类器，可以一个都不命中（陌生人），也可以命中多个（取分高者）
  * 没有闭集硬判，不会出现「陌生幼童被判成妈妈 0.99」这种事故
  * 阈值三档（strict/normal/loose）按「全库误判率」标定，默认用 normal

产出
----
1) library.db 的 photo_person_tags：source='model'（会先清掉旧的 model/clf 标签）
2) 19_统一相册库/_review/二分类判定审核.html：每人抽命中图 + 边界图，人工肉眼验收
"""
import argparse
import base64
import io
import json
import os
import pickle
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
LIB_DB = ROOT / "19_统一相册库" / "library.db"
MODEL_JSON = ROOT / "照片人物判定" / "per_person_models.json"
ENS_PKL = ROOT / "照片人物判定" / "per_person_ens.pkl"
REVIEW_HTML = ROOT / "19_统一相册库" / "_review" / "二分类判定审核.html"

# 18 归档目录名 → 身份（照片级标注；乐仔小时候 就是 乐仔）
ALIAS = {"乐仔小时候": "乐仔", "妈妈小时候": "妈妈", "艳艳小时候": "艳艳"}
# 配对仲裁：全库最相似的一对（余弦 0.626），同时过阈值时只能留一个
ARB_PAIRS = [("乐仔", "七月")]

MIN_DET = 0.60
MIN_SIDE_ALL = 10
DEFAULT_LEVEL = "normal"
SHOW_N = 10          # 每人展示的命中数
SHOW_EDGE = 8        # 每人展示的边界数
TARGETS = {"strict": 0.002, "normal": 0.005, "loose": 0.01}
# 标定负样本构成：
#  * 他人归档脸 —— 家人之间的混淆
#  * 09 班级库全量 —— 陌生幼童/家长（误判重灾区，训练时只抽了 5000，这里用全量 2.1 万）
#  * 成人模型再加 07/08 随机 —— 家庭库里的亲戚朋友；乐仔/七月/乐仔小时候不能加
#    （08 就是他们的照片库，加了会把本人当负样本，阈值被抬飞）
KIDS = {"乐仔", "乐仔小时候", "七月"}
# 09 班级库专用严格阈值：这些人在 09 里几乎没有真照片（18 归档真值 0~5 张），
# normal 档 0.5% FPR 在 2 万张班级库上就是上百张误判，必须单独收紧
STRICT09 = {"艳艳", "妈妈", "我", "七月", "爸爸", "乐仔小时候"}
FPR09 = 0.0003
N_HOME_RANDOM = 3000
# 人眼分档阈值的准入门槛（判读张数下限 / 允许判错数）。
# 为什么设闸门：第 05 轮那次事故（乐仔阈值 5.661 → −5.085、写库 12194 条）的根因不是
# "放宽阈值"本身，而是**放宽没有任何证据支持**。分档阈值的全部正当性来自"我判读过这些名次"，
# 所以没有判读记录就一律拒用，宁可退回保守阈值。
BAND_MIN_N = 48


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def l2n(X, axis=1):
    X = np.asarray(X, dtype=np.float32)
    n = np.linalg.norm(X, axis=axis, keepdims=True)
    n[n < 1e-9] = 1.0
    return X / n


def load_faces():
    con = sqlite3.connect(LIB_DB)
    rows = con.execute("""
        select f.rowid, f.content_key, f.x, f.y, f.w, f.h, f.det_score,
               f.emb_mbf, f.emb_r50, a.p_child
        from faces f
        left join face_attrs a on a.face_rid = f.rowid""").fetchall()
    path_of = {}
    for ck, p in con.execute("select content_key, path from files where is_primary=1"):
        path_of.setdefault(ck, p)
    con.close()
    rid, cks, box, pch, Xm, Xr, detv = [], [], [], [], [], [], []
    for r, ck, x, y, w, h, det, mb, r5, pc in rows:
        if mb is None or r5 is None:
            continue
        if det is not None and det < MIN_DET:
            continue
        if w is not None and h is not None and min(w, h) < MIN_SIDE_ALL:
            continue
        rid.append(r); cks.append(ck); box.append((x, y, w, h))
        pch.append(pc if pc is not None else np.nan)
        detv.append(float(det) if det is not None else 0.0)
        Xm.append(np.frombuffer(mb, dtype=np.float32))
        Xr.append(np.frombuffer(r5, dtype=np.float32))
    Xm, Xr = l2n(np.vstack(Xm)), l2n(np.vstack(Xr))
    return (np.array(rid), np.array(cks, dtype=object), box, np.array(pch),
            {"mbf": Xm, "r50": Xr, "fused": l2n(np.hstack([Xm, Xr]))}, path_of,
            np.array(detv, dtype=np.float32))


def pick_threshold(pos_p, neg_p, target_fpr):
    grid = np.arange(0.02, 0.9901, 0.005)
    best = None
    for th in grid:
        fpr = float((neg_p >= th).mean())
        if fpr <= target_fpr:
            rec = float((pos_p >= th).mean())
            if best is None or rec > best[1]:
                best = (float(th), rec, fpr)
    if best is None:
        th = float(np.quantile(neg_p, 1 - target_fpr))
        best = (th, float((pos_p >= th).mean()), float((neg_p >= th).mean()))
    return best


MUTEX = {"乐仔": "乐仔小时候", "乐仔小时候": "乐仔"}


def predict(models, feats):
    """返回 {person: 每张脸的概率数组}"""
    out = {}
    for p, m in models.items():
        X = feats[m["feature"]]
        if m["kind"] == "lr":
            w = np.asarray(m["coef"], dtype=np.float32)
            out[p] = sigmoid(X @ w + m["intercept"])
        else:
            c = np.asarray(m["center"], dtype=np.float32)
            out[p] = sigmoid((X @ c) * m["platt_a"] + m["platt_b"])
    return out


def ens_models_stub(bundle):
    """把集成包包装成与 per_person_models.json 同形的 dict，好让下游标定/审核复用。"""
    out = {}
    for p, b in bundle.items():
        best = max(b["rows"], key=lambda r: r["tpr01"]) if b.get("rows") else {}
        out[p] = {"kind": "ens", "feature": "ens",
                  "n_seed": b["n_pos"], "cv_auc": round(best.get("auc", 0.0), 4),
                  "ens_tpr01": round(best.get("tpr01", 0.0), 4),
                  "thresholds": {"strict": 100.0, "normal": 50.0, "loose": 10.0},
                  "metrics": {"n_pos": b["n_pos"], "n_neg": b["n_neg"],
                              "trained_at": b.get("trained_at")}}
    return out


def predict_ens(feats, cks, rid, det, box, log):
    """集成引擎：每人 11 个子模型 → Platt → stacker。

    与 predict()（单 LR）的差异全部封装在这里，下游（幼童门控 / 阈值标定 /
    照片级聚合 / 写库 / 审核页）完全复用，不重复实现。
    """
    import ens_models as EM
    if not ENS_PKL.exists():
        raise SystemExit(f"未找到 {ENS_PKL}，请先跑 train_ens_prod.py")
    bundle = pickle.load(open(ENS_PKL, "rb"))
    con = sqlite3.connect(LIB_DB)
    ck18 = defaultdict(set)
    for rel, c in con.execute("select rel, content_key from files where lib='18'"):
        ck18[c].add(ALIAS.get(rel.split("/")[0], rel.split("/")[0]))
    con.close()
    prep = EM.ens_prep(cks, feats, ck18, EM.IDENT)
    out = {}
    for p, b in bundle.items():
        z, pos_idx = EM.ens_score_one(b, prep, cks, p, feats, det, box, EM.IDENT)
        # 保留 logit 原始尺度：下游标定只看排序，但 sigmoid 后大量正样本会挤在
        # 0.98~0.999 的高原上、并列排名按任意顺序断开，标定点会抖。
        out[p] = z
        if len(pos_idx) == 0:
            log(f"  ⚠️ {p} 归档单脸为 0 —— ctx/跨身份特征退化（数据没扫全？），"
                f"本人在该批数据上不可信")
        if len(b["meta_keys"]) - len(EM.META_KEY_BASE) != len(EM.IDENT) - 1:
            log(f"  ⚠️ {p} 训练时的跨身份特征列数="
                f"{len(b['meta_keys'])-len(EM.META_KEY_BASE)}，当前身份表="
                f"{len(EM.IDENT)-1} —— 列会错位，请重训 train_ens_prod.py")
        log(f"  {p:<8} {len(b['models'])} 子模型 · 正 {b['n_pos']} 负 {b['n_neg']} · "
            f"归档单脸 {len(pos_idx)} · 跨身份 {len(EM.IDENT)-1} 列")
    return bundle, out


def recalibrate_archive(models, prob, cks, log, tgt=0.03, cap_mult=6.0):
    """按「18 人工归档的冲突率」选阈值 —— 比 FPR-分位数更贴近真实目标。

    为什么换：FPR 分位数只回答"允许多少误报"，完全不看召回，于是
      · 乐仔（正样本 900+）阈值被抬到 0.969 → 全库只出 205 张
      · 七月 / 爸爸 阈值却落到 0.012 / 0.012（负样本分布不同）
    跨人差 80 倍，同一个目标 FPR 反而各自失准。

    本方法直接优化"召回尽量大、且（人类归档说不是本人的）冲突率 ≤ tgt"：
      · 冲突 = 模型判正、但 18 把该照片归在别人名下 —— 干净且非循环的误报信号
      · 冲突是**上界**：18 是照片级标注，合影归在一人名下，另一人出现不算错
      · 护栏：命中照片数 ≤ cap_mult × 归档规模，防小样本人一路滑到全库判正
    注意 18 归档的目录名 ≠ 身份：乐仔小时候 就是 乐仔。
    """
    con = sqlite3.connect(LIB_DB)
    ck18 = {}
    for rel, ck in con.execute("select rel, content_key from files where lib='18'"):
        ck18.setdefault(ck, set()).add(ALIAS.get(rel.split("/")[0], rel.split("/")[0]))
    con.close()
    log(f"归档标定：18 覆盖 {len(ck18)} 张唯一照片")
    for p, m in models.items():
        pp = ALIAS.get(p, p)
        u18 = sum(1 for w in ck18.values() if w == {pp})
        order = np.argsort(-prob[p])
        seen, pos, neg = set(), 0, 0
        best = None
        # ⚠️ 冲突判定：**归档不含本人即算冲突**（保持严格）。
        #
        # 曾试过「合影豁免」——只把单脸照里的别人算冲突，理由很充分：
        # 18 是照片级单人归档，一张"爸爸+我+乐仔"的合影只能归一个人，
        # 于是 艳艳/我 的冲突率被虚高到 46%/49%，标定直接无解、退化成 99.95 分位。
        # 渲染冲突批次肉眼复核也确认：那些"冲突"里的脸**全是本人**（同一位女性/男性），
        # 是归档口径的问题、不是模型错。
        #
        # **但豁免是错的**（2026-09-19 实测翻车）：把冲突从 673 压到 50 后，
        # "冲突率 ≤ 3%"就再也起不到约束作用（50/1692 = 2.95% 仍然达标），
        # 标定沿分数一路滑到 cap —— 乐仔阈值从 5.66 掉到 **-5.08**、命中 8657 张、
        # 全库写入 12194 条标签。**放宽"证据"等于拆掉唯一的刹车。**
        # 正确做法：保持严格标定（宁可少发），缺的召回交给运维手段解决——
        # 由人眼金标定第二档阈值（见 _迭代报告_第05轮），而不是靠放宽反证。
        for i in order:
            ck = cks[i]
            if ck in seen:
                continue
            seen.add(ck)
            w = ck18.get(ck)
            if w is not None:
                if w == {pp}:
                    pos += 1
                elif pp not in w:
                    neg += 1
            if len(seen) > cap_mult * max(u18, 20):
                break
            if pos >= 3 and neg / max(1, pos + neg) <= tgt:
                best = (float(prob[p][i]), len(seen), pos, neg)
        if best is None:
            # 回退必须**数据驱动且偏保守**：桩阈值（0.0）在 logit 尺度上等于"过半",
            # 一次标定失败就能把全库灌满标签（实测艳艳 1370 张）。
            # 取该人分数的全库 99.95 分位 ≈ 最多出 10 张，宁少不错。
            th = float(np.quantile(prob[p], 0.9995))
            m["thresholds"] = {"strict": round(th + 3.0, 4), "normal": round(th, 4),
                               "loose": round(th - 3.0, 4)}
            m["metrics"]["calib_archive"] = {"u18": u18, "fallback": "quantile_99.95",
                                             "thr": round(th, 4)}
            log(f"  ⚠️ {p:<10} 归档标定无解（归档 {u18} 张）→ 回退到全库 99.95 分位 "
                f"{th:.3f}（预计 ≤{int((prob[p] >= th).sum())} 张）")
            continue
        th, n_ph, pos, neg = best
        old = m["thresholds"].get("normal")
        # 分数是 logit，档位偏移用 logit 单位（≈ 概率 0.95 / 0.05 的间距）
        m["thresholds"] = {"strict": round(th + 3.0, 4),
                           "normal": round(th, 4),
                           "loose": round(th - 3.0, 4)}
        m["thresholds"].pop("09strict", None)   # 归档标定已兼顾 09 误报
        m["metrics"]["calib_archive"] = {"u18": u18, "hit_photos": n_ph,
                                         "confirmed": pos, "conflict": neg,
                                         "proof_prec": round(pos / max(1, pos + neg), 4)}
        log(f"  {p:<10} 阈值 {old} → {th:.4f}"
            f"（命中 {n_ph} 张，归档确证 {pos} / 冲突 {neg}，"
            f"证明精度 {pos/max(1,pos+neg):.1%}）")
    return models


def recalibrate(models, prob, cks, log, save=True):
    """用干净基准集重标阈值（不重训）。

    训练时 09 只抽 5000 张，导致 2 万张班级库上误判集中爆发（实测妈妈 111 张）。
    这里用 09 全量 + 他人归档脸 + （成人）07/08 随机重新标定三档阈值，
    并把新阈值写回 per_person_models.json。
    """
    con = sqlite3.connect(LIB_DB)
    ck_person = {}
    for rel, ck in con.execute("select rel, content_key from files where lib='18'"):
        ck_person.setdefault(ck, rel.split("/")[0])
    ck_lib = {}
    for ck, lib in con.execute("select content_key, lib from files where is_primary=1"):
        ck_lib.setdefault(ck, lib)
    con.close()
    home_pool = np.array([i for i in range(len(cks))
                          if ck_lib.get(cks[i]) in ("07", "08")], dtype=int)
    idx09 = np.array([i for i in range(len(cks)) if ck_lib.get(cks[i]) == "09"], dtype=int)
    log(f"重标定：09 全量 {len(idx09)} 张，07/08 池 {len(home_pool)} 张")
    rng = np.random.default_rng(42)
    for p, m in models.items():
        other_ck = {ck for ck, pp in ck_person.items()
                    if pp != p and pp != MUTEX.get(p)}
        other_idx = np.array([i for i in range(len(cks)) if cks[i] in other_ck], dtype=int)
        parts = [idx09, other_idx]
        if p not in KIDS and len(home_pool):
            parts.append(rng.choice(home_pool, size=min(N_HOME_RANDOM, len(home_pool)),
                                    replace=False))
        neg_idx = np.unique(np.concatenate(parts))
        s_neg = prob[p][neg_idx]
        thr_new = {}
        for level, tgt in TARGETS.items():
            thr_new[level] = round(float(np.quantile(s_neg, 1 - tgt)), 4)
        old = m["thresholds"]["normal"]
        m["thresholds"] = thr_new
        if p in STRICT09 and len(idx09):
            s09 = prob[p][idx09]
            m["thresholds"]["09strict"] = round(float(np.quantile(s09, 1 - FPR09)), 4)
        m["metrics"]["recal"] = {"n_neg": int(len(neg_idx)),
                                 "fpr_normal": round(float((s_neg >= thr_new["normal"]).mean()), 5)}
        t09 = m["thresholds"].get("09strict")
        log(f"  {p}: 阈值 {old} → {thr_new['normal']}"
            + (f"，09 严格 {t09}" if t09 else "") + f"（基准负样本 {len(neg_idx)}）")
    if save:      # 集成引擎的阈值不回写 per_person_models.json（那是单 LR 的档）
        MODEL_JSON.write_text(json.dumps(models, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    return models


def thumb(path, box=None, width=300):
    """缩放 + 画人脸框，返回 base64 jpeg。失败返回 None。"""
    try:
        im = Image.open(path)
        im = im.convert("RGB")
    except Exception:
        return None
    W, H = im.size
    if box:
        d = ImageDraw.Draw(im)
        for (x, y, w, h) in box:
            if x is None:
                continue
            d.rectangle([x, y, x + w, y + h], outline=(255, 60, 60), width=max(2, W // 200))
    im = im.resize((width, int(H * width / W)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=68)
    return base64.b64encode(buf.getvalue()).decode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", default=DEFAULT_LEVEL, choices=["strict", "normal", "loose"])
    ap.add_argument("--dry", action="store_true", help="只统计不写库")
    ap.add_argument("--no-recal", action="store_true", help="跳过阈值重标定")
    ap.add_argument("--calib", default="archive", choices=["fpr", "archive"],
                    help="fpr=负样本池 FPR 分位（旧）；archive=按 18 归档冲突率（推荐）")
    ap.add_argument("--engine", default="lr", choices=["lr", "ens"],
                    help="lr=单 LR/中心NN（per_person_models.json）；"
                         "ens=多子模型集成（per_person_ens.pkl）")
    ap.add_argument("--no-html", action="store_true")
    ap.add_argument("--no-gold", action="store_true",
                    help="不用人眼金标第二档阈值（默认启用：archive 只管「别发错」，"
                         "金标负责「别太抠」）")
    ap.add_argument("--allow-no-anchor", action="store_true",
                    help="允许给「零视觉锚点」身份发标签。⚠️ 默认禁止："
                         "第 04 轮实测 `妈妈` 的归档里没有一张本人脸（身份纯度 0%），"
                         "其正样本实际是乐仔 ⇒ 发出来的标签是挂着妈妈名字的乐仔。"
                         "要放行必须自己确认锚点已修好（_audit/身份锚点.json）")
    ap.add_argument("--no-band", action="store_true",
                    help="不用人眼分档实测阈值（_audit/分档阈值.json）。"
                         "⚠️ 默认启用且优先级最高，因为它是唯一**逐档出图、我亲手判读**的阈值："
                         "金标 5% 分位被自己的取样名次封顶（循环），archive 对配角身份失效，"
                         "只有分档阈值能真正抬召回。闸门：判读张数 ≥" + str(BAND_MIN_N)
                         + " 且 0 错，否则拒用。")
    args = ap.parse_args()

    def log(*a):
        print(*a, flush=True)

    rid, cks, box, p_child, feats, path_of, det = load_faces()
    log(f"全库人脸 {len(rid)} 张（有幼童属性 {int((~np.isnan(p_child)).sum())} 张）")

    bundle = None
    if args.engine == "ens":
        log("引擎 ens：多子模型集成")
        bundle, prob = predict_ens(feats, cks, rid, det, box, log)
        models = ens_models_stub(bundle)
    else:
        models = json.loads(MODEL_JSON.read_text(encoding="utf-8"))
        log(f"引擎 lr：{len(models)} 个二分类模型（{', '.join(models)}）")
        prob = predict(models, feats)

    # ---- 无锚点身份护栏（第 04 轮新增）----
    # 归档目录名是"与这个名字相关的照片"，不是"这个人的脸"。实测 `妈妈` 归档里
    # 475 张脸中 0 张是本人（26% 是乐仔）⇒ 它的"正样本"是乐仔的脸，
    # 训练出的模型挂着"妈妈"的名字却是个乐仔检测器。这类身份发标签会污染整库。
    # 锚点体检表由 diag_identity_anchor.py --emit 生成。
    anchor_p = ROOT / "照片人物判定" / "_audit" / "身份锚点.json"
    if anchor_p.exists() and not args.allow_no_anchor:
        try:
            anc = json.loads(anchor_p.read_text(encoding="utf-8"))
        except Exception:
            anc = {}
        drop = [p for p in list(models)
                if p in anc and not anc[p].get("has_anchor", True)]
        if drop:
            for p in drop:
                models.pop(p, None)
                prob.pop(p, None)
            log(f"⛔ 跳过 {len(drop)} 个「零视觉锚点」身份：{', '.join(drop)}\n"
                f"   依据 {anchor_p.name}：这些身份的归档里没有本人脸，"
                f"发标签等于把别人的人认成它（详见 _迭代报告_第04轮_20260919.md）\n"
                f"   确认锚点已修好后，用 --allow-no-anchor 放行")
    elif anchor_p.exists() and args.allow_no_anchor:
        log("⚠️ --allow-no-anchor：零锚点身份也会发标签，请自行承担污染风险")

    # ---- 幼童/成人属性门控：用 train_child.py 的 p_child 否决跨类误判 ----
    # 成人模型被「明确幼童」脸否决；幼童模型被「明确成人」脸否决；
    # 属性未知（NaN / 中间带）不否决。只在高置信区间生效，保证门控自身不引入新错。
    cm = json.loads((ROOT / "照片人物判定" / "child_model.json").read_text(encoding="utf-8"))
    child_hi = cm["thresholds"]["child"]     # >= 视为明确幼童
    adult_lo = cm["thresholds"]["adult"]     # <= 视为明确成人
    n_veto = 0
    for p in models:
        if p in KIDS:
            veto = p_child <= adult_lo
        else:
            veto = p_child >= child_hi
        veto = veto & ~np.isnan(p_child)
        n_veto += int(veto.sum())
        # 否决要打到「任何阈值以下」：分数是 logit，0.0 可能仍高于该人的阈值
        prob[p] = np.where(veto, -1e9, prob[p])
    log(f"属性门控否决 {n_veto} 个（人,脸）组合")
    if not args.no_recal:
        if args.calib == "archive":
            models = recalibrate_archive(models, prob, cks, log)
        else:
            models = recalibrate(models, prob, cks, log,
                                 save=(args.engine == "lr"))
    # ---- 人眼金标第二档阈值（第 05 轮新增）----
    # 为什么需要：archive 标定对**配角身份**系统性保守甚至无解 ——
    # 艳艳/我 的归档照片 2/3 是合影，冲突率被虚高到 46%/49% → 判无解 →
    # 回退 99.95 分位（只发 18 张）。而"放宽反证"（合影豁免）会让唯一的刹车
    # 失效（实测乐仔阈值掉到 -5.08、写库 12194 条，见第 05 轮报告第六节）。
    # 人眼金标是**唯一非循环**的监督：用它给每人定"我亲眼确认过的最低分"，
    # 与 archive 阈值取 min —— archive 负责别发错，金标负责别太抠。
    gold_path = f"{ROOT}/照片人物判定/_audit/金标阈值.json"
    if os.path.exists(gold_path) and not args.no_gold:
        gold = json.load(open(gold_path, encoding="utf-8"))
        for p, m in models.items():
            g = gold.get(p)
            if not g:
                continue
            cur = float(m["thresholds"]["normal"])
            if g["thr"] < cur - 1e-6:
                log(f"  ↓ 金标放宽 {p:<8} {cur:.3f} → {g['thr']:.3f}"
                    f"（人眼 {g['n']} 张，最低 {g['lo']:.3f}）")
                m["thresholds"] = {"strict": round(g["thr"] + 3.0, 4),
                                   "normal": round(g["thr"], 4),
                                   "loose": round(g["thr"] - 3.0, 4)}
                m.setdefault("metrics", {})["calib_gold"] = g
            else:
                log(f"  · 金标未放宽 {p:<8}（金标 {g['thr']:.3f} ≥ archive {cur:.3f}）")
    else:
        log("（未用人眼金标第二档）")

    # ---- 人眼分档实测阈值（第 06 轮新增，优先级最高）----
    # 为什么必须有这一档（第 05 轮遗留的结构性缺陷）：
    #   archive 阈值 = 按归档冲突率扫出来的位置；金标阈值 = **已发货批次分数的最低 5% 分位**。
    #   两者的取样都发生在 top-N 之内 ⇒ 金标阈值**永远不可能低于取样名次**，是循环的，
    #   召回被自己的取样点封顶。实测后果：七月只发 22 张，而归档口径下它前 200 名有 89 张确证。
    # 分档阈值的不同之处：它不是算出来的，是**逐档出图、由我判读**的，并配三类独立证据
    #   （来源库 / 18 归属 / 同框伙伴，见 diag_band_evidence.py）。
    # 与「合影豁免」那次事故的区别：那边是把**反证主动豁免**（拆刹车），
    #   这边是把**验证样本扩大**（加刹车）——放宽的是我们已有证据的范围，不是证据本身。
    band_path = f"{ROOT}/照片人物判定/_audit/分档阈值.json"
    if os.path.exists(band_path) and not args.no_band:
        band = json.load(open(band_path, encoding="utf-8"))
        for p, m in models.items():
            b = band.get(p)
            if not isinstance(b, dict) or "thr" not in b:
                continue
            n, err = int(b.get("n_judged", 0)), int(b.get("n_err", 99))
            if n < BAND_MIN_N or err > 0:
                log(f"  ⛔ 分档阈值被拒 {p:<8}（判读 {n} 张 / 判错 {err} 张，"
                    f"闸门要求 ≥{BAND_MIN_N} 张且 0 错）")
                continue
            cur = float(m["thresholds"]["normal"])
            if b["thr"] < cur - 1e-6:
                log(f"  ↓ 分档放宽 {p:<8} {cur:.3f} → {b['thr']:.3f}"
                    f"（人眼判读 {n} 张 0 错；最深验证 #{b.get('deepest_rank')}"
                    f" @ {b.get('deepest_score')}）")
                m["thresholds"] = {"strict": round(b["thr"] + 3.0, 4),
                                   "normal": round(b["thr"], 4),
                                   "loose": round(b["thr"] - 3.0, 4)}
                m.setdefault("metrics", {})["calib_band"] = b
            else:
                log(f"  · 分档未放宽 {p:<8}（分档 {b['thr']:.3f} ≥ 现行 {cur:.3f}）")
    else:
        log("（未用人眼分档阈值）")

    thr = {p: m["thresholds"][args.level] for p, m in models.items()}

    # ---- 照片级聚合：一张照片只要有一张脸过阈值就算命中 ----
    # 09 班级库照片对 STRICT09 成员用更严的阈值
    ck_lib = {}
    con = sqlite3.connect(LIB_DB)
    for ck, lib in con.execute("select content_key, lib from files where is_primary=1"):
        ck_lib.setdefault(ck, lib)
    con.close()
    is09 = np.array([ck_lib.get(ck) == "09" for ck in cks])
    face_hit = {}
    for p in models:
        t09 = models[p]["thresholds"].get("09strict")
        if t09 is not None:
            face_hit[p] = np.where(is09, prob[p] >= t09, prob[p] >= thr[p])
        else:
            face_hit[p] = prob[p] >= thr[p]

    # ---- 配对仲裁：全库最相似的一对（乐仔↔七月，余弦 0.626）同时命中时只留分高者 ----
    # 为什么必要：两张脸都过阈值说明不了身份，只有"互相比较"才给得出答案；
    # 不仲裁的结果是两个模型各打一部分，用户看到的是"同一张脸挂了两个人"。
    for x, y in ARB_PAIRS:
        if x not in prob or y not in prob:
            continue
        both = face_hit[x] & face_hit[y]
        if not both.any():
            continue
        drop_x = both & (prob[x] < prob[y])
        drop_y = both & (prob[y] <= prob[x])
        face_hit[x] = face_hit[x] & ~drop_x
        face_hit[y] = face_hit[y] & ~drop_y
        log(f"配对仲裁 {x}↔{y}：双命中 {int(both.sum())} 张 → "
            f"判给{x} {int(drop_y.sum())} / 判给{y} {int(drop_x.sum())}")

    # ---- 照片级聚合：一张照片只要有一张脸过阈值就算命中 ----
    ck_best = {p: defaultdict(lambda: 0.0) for p in models}
    for p in models:
        for i in np.where(face_hit[p])[0]:
            ck = cks[i]
            if prob[p][i] > ck_best[p][ck]:
                ck_best[p][ck] = float(prob[p][i])
    for p in models:
        ck_best[p] = dict(ck_best[p])

    log(f"\n=== 判定结果（阈值档位 {args.level}）===")
    log(f"{'人':<10}{'阈值':>8}{'命中照片':>10}{'命中脸':>9}{'其中18已归档':>14}")
    for p, m in models.items():
        n_ck = len(ck_best[p])
        con = sqlite3.connect(LIB_DB)
        known = con.execute(
            f"select count(*) from files where lib='18' and content_key in ({','.join(['?']*max(1,n_ck))})",
            list(ck_best[p].keys()) if n_ck else ["__none__"]).fetchone()[0] if n_ck else 0
        con.close()
        log(f"{p:<10}{thr[p]:>8.3f}{n_ck:>10}{int(face_hit[p].sum()):>9}{known:>14}")

    # ---- 写库 ----
    if not args.dry:
        con = sqlite3.connect(LIB_DB)
        n0 = con.execute("select count(*) from photo_person_tags").fetchone()[0]
        con.execute("delete from photo_person_tags where source in ('model','clf')")
        rows = []
        for p in models:
            for ck, s in ck_best[p].items():
                rows.append((ck, p, "model", round(s, 4)))
        con.executemany(
            "insert or replace into photo_person_tags(content_key,person,source,score) "
            "values(?,?,?,?)", rows)
        con.commit()
        n1 = con.execute("select count(*) from photo_person_tags").fetchone()[0]
        con.close()
        log(f"\n已写入 {len(rows)} 条 model 标签（库内标签 {n0} → {n1}）")

    if args.no_html:
        return

    # ---- 审核页 ----
    log("生成审核页 ...")
    rng = np.random.default_rng(7)
    cards = []
    for p, m in models.items():
        hits = sorted(ck_best[p].items(), key=lambda kv: -kv[1])
        pick = hits[:SHOW_N]
        # 边界：分数刚好在阈值附近 ±0.06
        edge = [kv for kv in hits if thr[p] <= kv[1] < thr[p] + 0.06]
        rng.shuffle(edge)
        edge = edge[:SHOW_EDGE]
        blocks = []
        for title, items in (("高置信命中", pick), ("边界样本（最易错）", edge)):
            if not items:
                continue
            imgs = []
            for ck, s in items:
                path = path_of.get(ck)
                if not path or not Path(path).exists():
                    continue
                ii = np.where(cks == ck)[0]
                bx = [box[i] for i in ii if prob[p][i] >= thr[p] - 0.12]
                b = thumb(path, bx)
                if not b:
                    continue
                imgs.append(
                    f'<div class="card"><img src="data:image/jpeg;base64,{b}">'
                    f'<div class="cap">{s:.3f}</div></div>')
            if imgs:
                blocks.append(f"<h3>{title}（{len(imgs)}）</h3><div class='grid'>{''.join(imgs)}</div>")
        if blocks:
            cards.append(
                f"<section><h2>{p} <span class=m>阈值 {thr[p]:.3f} · AUC {m['cv_auc']} · "
                f"种子 {m['n_seed']} · 命中 {len(ck_best[p])} 张</span></h2>"
                + "".join(blocks) + "</section>")

    html = """<!doctype html><meta charset="utf-8"><title>二分类判定审核</title>
<style>
body{font-family:-apple-system,PingFang SC,sans-serif;background:#f6f7f9;color:#222;margin:0;padding:20px}
h2{font-size:18px;margin:26px 0 8px;border-left:4px solid #4a76d4;padding-left:10px}
h3{font-size:14px;color:#666;margin:14px 0 6px;font-weight:600}
.m{font-size:12px;color:#888;font-weight:400}
.grid{display:flex;flex-wrap:wrap;gap:8px}
.card{width:150px;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px #0002}
.card img{width:150px;height:150px;object-fit:cover;display:block}
.cap{font-size:11px;text-align:center;padding:3px;color:#555}
</style>
<h1>二分类判定审核 <span class=m>每人一个二分类器（开集）· 阈值档位 LEVEL · 生成 GEN</span></h1>
<p class=m>「高置信命中」是本档位分数最高的照片；「边界样本」是刚过阈值的，最容易判错，重点看这些。</p>
CARDS
""".replace("CARDS", "\n".join(cards)).replace("LEVEL", args.level) \
       .replace("GEN", datetime.now().strftime("%Y-%m-%d %H:%M"))
    REVIEW_HTML.write_text(html, encoding="utf-8")
    log(f"审核页: {REVIEW_HTML} ({REVIEW_HTML.stat().st_size/1e6:.1f} MB)")


if __name__ == "__main__":
    main()
