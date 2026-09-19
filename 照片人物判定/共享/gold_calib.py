#!/usr/bin/env python3
"""从**人眼金标**（verdicts.jsonl）算"第二档阈值"。

为什么需要（_迭代报告_第05轮 第五/六节）：
  archive 标定只看"归档冲突率"，对**配角身份**（艳艳/我，归档照片 2/3 是合影）
  系统性失效 —— 冲突率被合影虚高到 46%/49% → 判无解 → 回退 99.95 分位（只发 ≤18 张）。
  而"放宽反证"（合影豁免）会被证明是拆刹车（乐仔阈值掉到 -5.08、写入 12194 条）。
  正确的第二档信号是**人眼金标**：我逐张看过、确认是本人的最低分。

用法:
  python gold_calib.py            # → _audit/金标阈值.json（默认 5% 分位）
  python gold_calib.py --q 0.10   # 更激进（覆盖 90% 金标样本）
"""
import argparse
import collections
import json

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
VERDICTS = f"{ROOT}/照片人物判定/_audit/verdicts.jsonl"
OUT = f"{ROOT}/照片人物判定/_audit/金标阈值.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--q", type=float, default=0.05,
                    help="分位：默认 0.05 = 取「5%% 分位」，即让 95%% 的金标样本都高于阈值")
    a = ap.parse_args()

    by = collections.defaultdict(list)
    n_noscore = n_notyes = n_other = 0
    for line in open(VERDICTS, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        # ⚠️ 只信**本轮批次**（source=eye_20260919）。旧批次金标（早期逐张审核）的 ens
        # 分数来自**旧模型/旧嵌入**，与当前部署模型的 logit 尺度不一致 —— 混进来会把
        # 阈值拖到 0.112（乐仔），等于没标定。
        if r.get("source") != "eye_20260919":
            n_other += 1
            continue
        if int(r.get("verdict", -1)) != 1:      # 只信"确认是本人"的
            n_notyes += 1
            continue
        s = r.get("ens", r.get("score"))   # 字段名与 selfaudit 统一为 ens，兼容老的 score
        if s is None:
            n_noscore += 1
            continue
        by[r["person"]].append(float(s))

    out = {}
    for p, ss in sorted(by.items()):
        ss.sort()
        k = max(0, min(len(ss) - 1, int(len(ss) * a.q)))
        out[p] = dict(n=len(ss), thr=round(ss[k], 4), lo=round(ss[0], 4),
                      hi=round(ss[-1], 4), q=a.q, source="verdicts.jsonl 人眼金标")
        print(f"  {p:<6} 金标 {len(ss):>3} 张  分数 {ss[0]:.3f}~{ss[-1]:.3f}"
              f"  → {int(a.q * 100)}% 分位阈值 {ss[k]:.3f}")

    if not out:
        print("⚠️ 没读金标到任何带分数的条目")
        return
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    extra = []
    if n_other:
        extra.append(f"跳过非本轮批次 {n_other}（旧模型分数尺度不同）")
    if n_notyes:
        extra.append(f"跳过非 1 标签 {n_notyes}")
    if n_noscore:
        extra.append(f"跳过无分数 {n_noscore}")
    print(f"→ {OUT}" + (f"（{'；'.join(extra)}）" if extra else ""))


if __name__ == "__main__":
    main()
