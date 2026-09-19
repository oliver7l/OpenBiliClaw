#!/usr/bin/env python3
"""重扫完成后的全链路驱动（一键跑，逐步日志）。

顺序（不能换）：
  1. export_npz.py         faces 表 → npz（审核工具读 npz，不重导就会审旧坐标）
  2. train_child.py        重建 face_attrs/photo_attrs + 幼童判别器
                           （必须在 train_ens_prod 之前：成人正样本靠它剔"拍娃照里的孩子"）
  3. train_ens_prod.py     训练 6 身份集成（不加 --purify：它会被真本人难样本也剔掉，已证伪）
  4. apply_per_person.py   落库（--engine ens --calib archive）
  5. selfaudit.py eval     用人工脸级真值核阈值

用法:
  python run_chain.py             # 全跑（apply 不 dry）
  python run_chain.py --dry       # apply 只统计不写库
  python run_chain.py --from 3    # 从第 3 步开始
"""
import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = "/Users/imac/.workbuddy/binaries/python/envs/default/bin/python"

STEPS = [
    ("export_npz", [PY, "-u", "export_npz.py"]),
    ("train_child", [PY, "-u", "train_child.py"]),
    ("train_ens_prod", [PY, "-u", "train_ens_prod.py", "--src", "db"]),
    ("apply", [PY, "-u", "apply_per_person.py", "--engine", "ens",
               "--calib", "archive"]),
    ("eval", [PY, "-u", "selfaudit.py", "eval"]),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--from", dest="frm", type=int, default=1)
    ap.add_argument("--npz", default=None,
                    help="审核工具要读的 npz；默认取 export_npz 刚产出的那份")
    a = ap.parse_args()

    env = dict(os.environ)
    for i, (name, cmd) in enumerate(STEPS, 1):
        if i < a.frm:
            continue
        if name == "apply" and a.dry:
            cmd = cmd + ["--dry"]
        if i == 1:
            npz = a.npz or f"{os.path.dirname(HERE)}/../19_统一相册库/_faces_backup/" \
                           f"faces_scrfd_20260919.npz"
            env["OBC_FACES_NPZ"] = os.path.abspath(npz)
        log = f"_chain_{i}_{name}.log"
        print(f"\n===== [{i}/{len(STEPS)}] {name} → {log} =====", flush=True)
        t0 = time.time()
        with open(log, "w") as fh:
            p = subprocess.run(cmd, cwd=HERE, stdout=fh, stderr=subprocess.STDOUT, env=env)
        print(f"  退出码 {p.returncode}，用时 {time.time()-t0:.0f}s", flush=True)
        if p.returncode != 0:
            print(f"  ⚠️ {name} 失败，链路中止。看 {log}", flush=True)
            print("".join(open(log).readlines()[-25:]), flush=True)
            sys.exit(p.returncode)
        print("".join(open(log).readlines()[-12:]), flush=True)
    print("\n链路完成。审核图在 照片人物判定/_audit/", flush=True)


if __name__ == "__main__":
    main()
