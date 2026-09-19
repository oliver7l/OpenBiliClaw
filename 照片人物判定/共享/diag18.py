#!/usr/bin/env python3
"""用「18_照片分组」的人工归档当独立真值，验证 09 幼儿园库高分命中到底是谁。

18 是照片级标注（合影也归在某个人名下），所以：
  · 命中的 ck 出现在 18/乐仔 下 → 强正证据（人类归档说是乐仔）
  · 命中的 ck 出现在 18/<别人> 下 → 强负证据（人类归档说是别人）
  · 没出现在 18 → 未标注，靠我看图（selfaudit）
这一步是为了**在不动模型的前提下**，给阈值定一个可信的落点。
"""
import os, sys, json, sqlite3, collections
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench_ens as B
from selfaudit import get_scores, canon_set

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
LIB_DB = f"{ROOT}/19_统一相册库/library.db"
PERSON = sys.argv[1] if len(sys.argv) > 1 else "乐仔"


def main():
    D = B.load()
    ens, lr = get_scores(PERSON, D)
    con = sqlite3.connect(LIB_DB)
    ck18 = collections.defaultdict(set)          # ck -> {归档人名}
    for rel, c in con.execute("select rel, content_key from files where lib='18'"):
        ck18[c].add(rel.split("/")[0])
    con.close()

    print(f"== {PERSON} · 用 18 人工归档作独立真值 ==")
    lib = np.array(D["lib"], dtype=object)
    for t in (2.0, 1.5, 1.0, 0.5, 0.3, 0.15, 0.10, 0.05, 0.0, -0.5):
        sel = np.where(ens > t)[0]
        s09 = sel[lib[sel] == "09"]
        cks = {str(D["ck"][i]) for i in sel}
        pos = sum(1 for c in cks if c in ck18 and canon_set(ck18[c]) == {PERSON})
        neg = sum(1 for c in cks if c in ck18 and PERSON not in canon_set(ck18[c]))
        unk = len(cks) - pos - neg
        print(f"  E>{t:>5.2f}: 命中 {len(sel):>5} 张 / {len(cks):>4} 张唯一照片"
              f"（09库 {len(s09):>4}） | 18归还正 {pos:>3} 归还负 {neg:>3} 未标 {unk:>4}")

    # 09 库内部：命中照片是否与 18 归档同一张
    print("\n-- 09 库命中 vs 18 归档明细（E>0.10）--")
    sel = np.where((ens > 0.10) & (lib == "09"))[0]
    hit = collections.Counter()
    for i in sel:
        c = str(D["ck"][i])
        who = canon_set(ck18[c]) if c in ck18 else None
        hit[",".join(sorted(who)) if who else "(未标)"] += 1
    for k, v in hit.most_common(12):
        print(f"  {k:<16}{v:>5}")


if __name__ == "__main__":
    main()
