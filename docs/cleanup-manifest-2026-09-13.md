# 清理候选清单（2026-09-13）

> **状态**：⏸ **待用户拍板**（本清单仅登记候选，**尚未执行任何删除/移动**）。
> 全部候选项位于 `.gitignore` 覆盖的 `data/` 内 —— 清理属**本地磁盘整理**，**不影响仓库内容**。
> 执行纪律（项目约定 + personal_files_safety）：**先出清单 → 用户确认 → 移入废纸篓（非永久删除）→ 分批 ≤10 → 逐批验证**。

---

## A. 建议清理（有明确依据）

| # | 目标 | 大小 | 依据 | 判定 |
|---|------|------|------|------|
| A1 | `data/v2ex-hot-hub/`（连字符版） | 28M | 外部仓库 git clone 旧版（8-31）；活跃脚本用的是 `data/v2ex_hot_hub`（下划线），见 `scripts/collect_v2ex_archive.py:59` 的 `DEFAULT_REPO_DIR` | ✅ 陈旧重复，无引用 |

## B. 待用户确认（疑似孤儿，全仓零代码引用）

| # | 目标 | 大小 | 说明 | 判定 |
|---|------|------|------|------|
| B1 | `data/tax_frames/` | 2.8M | 全仓 grep（py/json/yaml/toml/md）**零命中** | ⚠️ 疑似孤儿 |
| B2 | `data/tax_frames2/` | 4.6M | 同上 | ⚠️ 疑似孤儿 |
| B3 | `data/tax_frames2_check/` | 2.3M | 同上（名字含 `_check`，疑似临时校验产物） | ⚠️ 疑似孤儿 |
| B4 | `data/backups/`（09-11 保留的回滚点，如 `rollback-20260909/`） | 1.9G | 拆库已在 09-11 验证通过 | 🟡 可清，但非紧急；保留与否由用户定 |

## C. 明确不动（有活跃引用 / 用户数据）

| 目标 | 大小 | 原因 |
|------|------|------|
| `data/v2ex_browser_profile/` | — | 被 `runtime/v2ex_producer.py:74` 引用（活跃） |
| `data/hister/` | 37M | 第三方工具 hister 自身数据（含其 `config.yml`），非本项目代码数据 |
| `data/clone-sites/` | 3.0G | 克隆站点，可能有产品功能在用 |
| `data/embedding_cache.db` | 1.2G | 向量缓存，删会触发全量重算 |
| `data/douyin_profile/` | 1.2G | 抖音画像**数据** |
| `data/image-cache/` | 396M | 图片缓存，收益低 |

---

## 核实方法（可复现）

```bash
# 引用核对（对 A/B 两组逐项）
grep -rnE "v2ex-hot-hub|v2ex_hot_hub|tax_frames" src scripts *.toml *.md
# 跟踪/忽略状态
git check-ignore data/<dir>     # 期望 yes（均被忽略）
git ls-files data/<dir>         # 期望空（未被跟踪）
# 大小
du -sh data/<dir>
```

**结论**：A1 依据充分可清；B1–B4 需用户确认；C 组一律不动。
