#!/usr/bin/env python3
"""身份锚点体检：每个身份的"正样本种子"里，**脸**到底属于哪个簇；该身份有没有"本人锚点"。

为什么这是第 04 轮的核心诊断：
  归档是按**照片**标注的（"与这个名字相关的照片"），而训练用的是**脸**。
  对一个"长辈手机里全是孙子"的归档，它的单脸照片 = 孙子本人。
  ⇒ 该身份的检测器会被训练成**孙子的检测器**，却挂着长辈的名字。
  这类错误不会让任何指标报警（AUC 照样漂亮），只能靠"看脸是谁的"发现。

`--emit` 产出 `_audit/身份锚点.json`（{人: {archive_photos, seed_faces, own_share,
has_anchor}}），部署层（apply_per_person.py）读它来**拦掉零锚点身份**。

用法:
  python diag_identity_anchor.py
  python diag_identity_anchor.py --emit
"""
import os
import sys
import json
import argparse
import sqlite3
import collections

os.nice(10)
import numpy as np

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
LIB_DB = f"{ROOT}/19_统一相册库/library.db"
OUTDIR = f"{ROOT}/照片人物判定/_audit"
LABELS = f"{OUTDIR}/簇探针_k60.labels.npz"
SEEDS = f"{OUTDIR}/cluster_seeds.jsonl"
OUT = f"{OUTDIR}/身份锚点.json"

# 人工命名（来自第 04 轮的可视化判别；与 簇命名.json 必须一致）。
# CLUSTER_PERSON 把簇映射到**身份名**（用于算身份纯度），
# CLUSTER_LABEL 是给人看的标签（可以带说明）。两者分开是因为
# "艳艳(眼镜女)" 这种带注释的标签不能直接和身份名比较（会算成 0% 纯度）。
CLUSTER_PERSON = {37: "乐仔", 9: "乐仔", 1: "乐仔", 58: "乐仔", 26: "乐仔",
                  27: "乐仔", 42: "乐仔", 35: "七月", 3: "我", 38: "爸爸",
                  45: "艳艳", 36: "艳艳", 13: "艳艳"}
CLUSTER_LABEL = dict(CLUSTER_PERSON)
CLUSTER_LABEL.update({45: "艳艳(眼镜女)", 36: "艳艳(眼镜女)", 13: "艳艳/乐仔(混)"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ident", default=None)
    ap.add_argument("--emit", action="store_true")
    args = ap.parse_args()
    idents = args.ident.split(",") if args.ident else \
        ["乐仔", "艳艳", "妈妈", "我", "七月", "爸爸", "乐仔小时候"]

    if not os.path.exists(LABELS):
        print(f"缺簇标签 {LABELS}，先跑 cluster_probe.py"); return
    z = np.load(LABELS, allow_pickle=True)
    ck, lab = z["ck"], z["lab"]
    lab_of = {}
    for c, l in zip(ck, lab):
        if l >= 0:
            lab_of.setdefault(c, collections.Counter())[int(l)] += 1

    con = sqlite3.connect(LIB_DB)
    arch = collections.defaultdict(set)
    for c, pg in con.execute("SELECT DISTINCT content_key, person_group FROM files "
                             "WHERE person_group IS NOT NULL"):
        arch[pg].add(c)
    con.close()

    seed_cnt = collections.Counter()
    if os.path.exists(SEEDS):
        for l in open(SEEDS, encoding="utf-8"):
            if l.strip():
                seed_cnt[json.loads(l)["person"]] += 1

    print(f"簇标签覆盖 {len(lab_of)} 张照片的内容键；"
          f"簇种子 {sum(seed_cnt.values())} 条\n")
    report = {}
    for person in idents:
        cks = arch.get(person, set())
        hit = [c for c in cks if c in lab_of]
        cnt = collections.Counter()
        for c in hit:
            cnt.update(lab_of[c])
        tot = sum(cnt.values())
        top = "  ".join(f"C{c}({CLUSTER_LABEL.get(c, '?')}):{n}({n/tot:.0%})"
                        for c, n in cnt.most_common(5)) if tot else "（无）"
        own = sum(n for c, n in cnt.items()
                  if CLUSTER_PERSON.get(c) == person) / tot if tot else 0.0
        # 「乐仔小时候」是乐仔的同一个人（ALIAS），纯度按乐仔算
        if person == "乐仔小时候":
            own = sum(n for c, n in cnt.items()
                      if CLUSTER_PERSON.get(c) == "乐仔") / tot if tot else 0.0
        print(f"== {person}：归档 {len(cks)} 张照片（{len(hit)} 张有簇标签，{tot} 张脸）")
        print(f"   脸归属：{top}")
        print(f"   ▶ 身份纯度 {own:.1%}   簇种子 {seed_cnt.get(person, 0)} 条")
        report[person] = dict(archive_photos=len(cks), labelled_photos=len(hit),
                              labelled_faces=tot, own_cluster_share=round(own, 4),
                              seed_faces=int(seed_cnt.get(person, 0)),
                              has_anchor=bool(seed_cnt.get(person, 0) > 0 or own >= 0.15))
    if args.emit:
        json.dump(report, open(OUT, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"\n→ {OUT}")
        print("   has_anchor=False 的身份会被 apply_per_person.py 默认跳过（防污染）")


if __name__ == "__main__":
    main()
