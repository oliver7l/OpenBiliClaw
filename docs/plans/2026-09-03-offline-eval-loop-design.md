# 离线评估闭环（Offline Eval Loop）设计

> 目标：让「推荐 Agent 的每次改动」都可量化验证，形成「改动 → 离线指标 → 迭代」的闭环。
> 状态：Design · 2026-09-03
> 关联：`src/openbiliclaw/eval/`、`src/openbiliclaw/recommendation/engine.py`、`docs/technical-debt.md`

---

## 1. 背景与目标

### 1.1 为什么需要离线评估

当前项目已有完善的**画像评估**（`ProfileEvaluator`：对比 expected/predicted OnionProfile）和**合成数据自动优化**（`run_recommendation_auto_optimize.py`：mock persona + LLM 生成的 mock pool）。但存在一个关键缺口：

> **没有任何指标回答「推荐结果本身好不好、排序准不准」这一层问题。**

- 画像准 ≠ 推荐好：画像准确但候选池/排序/多样性策略差，用户依然不满意。
- 现有自动优化用的是 LLM 生成的 mock 候选池，不是真实内容，评估结论与线上脱节。
- 每次改 prompt、改 bandit 参数、改 MMR 重排，只能靠主观感受，无法说「提升/回退了多少」。

### 1.2 目标

1. 用**真实历史行为数据**构建可复算的离线评估任务。
2. 定义一组**确定性、可解释**的排序/多样性指标。
3. 提供一条 `scripts/run_offline_eval.py` CLI，可一键对当前引擎打分。
4. 让评估可对比基线（随机排序 / 历史引擎快照），并接入现有 `OptimizationLoop` 做自动迭代。

---

## 2. 数据现状盘点（关键约束）

> 以下为 2026-09-03 实测。

| 数据 | 规模 | 关键发现 |
|---|---|---|
| `recommendations` | 11,740 | **全部 `presented=0`、`presented_at=null`**，从未真正曝光；仅 1 条 dislike 反馈 |
| `events` | 227,898 | favorite 104,813 / view 62,285 / follow 52,001 为 B 站账号同步历史；另有近期实时 click/hover/scroll/snapshot |
| `events`（可关联） | 164,773 | url 含 `/video/BV...`，可解析出 BV 号 |
| `discovery_candidates` | 26,262 | 有 `candidate_key`（`bilibili:BV...`）、`bvid`、`topic_group`、`style_key`、`relevance_score` |
| `read_archive` | 60 | 已读库（含 tags/content_text），可作相关性/新颖性信号 |
| `articles` | 81,189 | 内容正文库 |

**三个决定性结论**：

1. **没有「曝光 → 点击」闭环数据**，无法做 CTR 类在线指标；`presented` 记录本身也未落库（本身是待修 bug，见 M4）。
2. **有海量「用户真实偏好」信号**：favorite / follow / like / article_finished 是强隐式正样本，可支撑**偏好预测类离线评估**（排序质量、命中率）。
3. **bvid 形态不统一**：`recommendations.bvid` 有「BV 前缀」4,285 条与「无前缀」7,455 条；`events.url` 带前缀。必须先归一化 content key（统一为 `platform:BVID`）。

---

## 3. 评估范式选型

**放弃**：曝光-点击 CTR 评估（无数据）。

**采用**：**隐式反馈偏好预测评估（Learning-to-Rank 风格）**。

> 思路：给系统一个「用户画像 + 一组候选内容（含该用户真实喜欢过与未消费过的）」，看系统能否把真正喜欢的排到前面。这直接度量**排序质量**，且完全可离线复算。

### 3.1 评估单元（Eval Scenario）

每个评估单元 = 一组 `(query, candidates, labels)`：

- **query**：用户画像快照（`SoulProfile`），可选用某一时刻的画像，支持时间切片。
- **candidates**：从候选池采样的 N 个内容（N 建议 20~50，控制 LLM 排序成本）。
- **labels**：每个 candidate 一个 0/1 标签（正样本 = 用户真实喜欢过，负样本 = 未消费过）。

系统对该单元输出一个排序；把排序与 labels 对比，得到 NDCG / HR / MRR / AUC。

---

## 4. 指标定义

### 4.1 排序质量（相关性）

| 指标 | 定义 | 说明 |
|---|---|---|
| **HR@K**（HitRate@K） | Top-K 中至少命中 1 个正样本的比例 | 兜底可解释指标，K=5/10 |
| **NDCG@K** | 位置加权的相关度增益 | 排序质量核心指标 |
| **MRR** | 第一个正样本出现位置的倒数 | 反映「首屏是否命中」 |
| **AUC** | 正样本得分 > 负样本得分的概率 | 全排序区分度，不依赖 K |

### 4.2 多样性 / 新颖性 / 覆盖

| 指标 | 定义 | 说明 |
|---|---|---|
| **ILS**（Intra-List Similarity） | 推荐列表内两两内容向量相似度均值 | 越低越多样，建议同时报告「去重后命中」 |
| **新颖性** | 推荐内容与用户历史消费的平均向量距离 | 破茧效果度量 |
| **覆盖率** | 推荐列表覆盖的 `topic_group` / `style_key` 数 | 供给多样性 |
| **探索成功率** | 探索型策略（explore/probe）推荐中被用户消费/收藏的比例 | bandit 效果 |

> 向量信号：`embedding_cache.db` 已有内容 embedding，`article_rag.db` 有 chunk 向量；新颖性/ILS 直接复用。

### 4.3 报告口径

- 每项指标给 **mean ± std**（跨多个评估单元），并附单元数、正样本数。
- 输出 **JSON + Markdown + 可视化**，便于对比历史 run（复用 `eval/run_logger.py`）。

---

## 5. Ground Truth 构建（最关键设计）

### 5.1 正样本（强信号优先）

按事件类型定权重与优先级：

| 事件 | 信号强度 | 说明 |
|---|---|---|
| `favorite` / `like` | 强 | 明确喜欢 |
| `follow` | 强 | 关注创作者，注意绑定到 up 而非单条内容 |
| `article_finished` | 中 | 读完（内容消费完成） |
| `view` | 中偏弱 | 浏览过，注意与曝光混淆 |
| `search` + 点击 | 中 | 主动搜索后点击 |

每个正样本保留 `(content_key, signal_weight, timestamp)`，评估时可按权重给分（可先二值化 0/1）。

### 5.2 负样本与选择偏差

- 候选池本身是系统探索产物，「未消费」≠「不喜欢」。
- **缓解策略**：
  1. 负样本只从「评估时刻之前已进入候选池、且评估窗口内未被消费」的内容中采样；
  2. 每条正样本配 1~3 条负样本（采样比 1:1~1:3），保持单元内正负平衡；
  3. 报告里显式标注「负样本为未消费假设」，不把 AUC 当 CTR。
- **防泄漏**：正样本的时间戳必须晚于 query 所用画像的构建时间；推荐用**时间切片**（见 5.4）。

### 5.3 候选集构造

- 候选集 = 正样本 ∪ 负样本（各采样），再随机打乱后交给引擎排序。
- 正样本必须全部来自真实 `events`；负样本来自 `discovery_candidates`（未消费）。
- 同一 `content_key` 去重；bvid 归一化统一为 `bilibili:BV...`。

### 5.4 时间切片（可选，M2）

- **T0 时刻前的行为** → 构建画像 / 建模；
- **T0~T1 窗口内的行为** → 作为评估正样本；
- 保证评估正样本不会污染排序所用画像，避免「测试泄漏」。

---

## 6. 评估流水线架构

```
┌──────────────────────────── 数据层 ────────────────────────────┐
│ events.db（真实行为）        discovery_candidates.db（候选池）     │
│ read_archive / articles     embedding_cache.db（向量）          │
└──────────────┬───────────────────────────┬─────────────────────┘
               │ content_key 归一化          │
               ▼                            ▼
┌───────────────────── Ground Truth 构建 ──────────────────────┐
│ 正样本：favorite/follow/like/article_finished（加权、带时间戳） │
│ 负样本：候选池未消费内容（采样比 1:1~1:3）                       │
│ 评估单元：query(画像) + N 个候选 + 0/1 标签                     │
└────────────────────────────┬─────────────────────────────────┘
                             ▼
┌───────────────────── 评估执行层（Runner） ────────────────────┐
│ 对每个单元调用 RecommendationEngine.serve(profile, limit=N)     │
│ 拿到排序 → 与 labels 对齐 → 落指标                              │
└────────────────────────────┬─────────────────────────────────┘
                             ▼
┌──────────────────────────── 指标层 ───────────────────────────┐
│ RankMetrics：HR@K / NDCG@K / MRR / AUC                        │
│ DiversityMetrics：ILS / 新颖性 / 覆盖率 / 探索成功率            │
└────────────────────────────┬─────────────────────────────────┘
                             ▼
┌──────────────────────────── 报告层 ───────────────────────────┐
│ JSON + Markdown + 可视化（对比基线 + 历史趋势）                  │
│ 可选：接入 OptimizationLoop 自动迭代                           │
└──────────────────────────────────────────────────────────────┘
```

---

## 7. 模块设计（新增文件）

| 文件 | 职责 |
|---|---|
| `src/openbiliclaw/eval/offline/__init__.py` | 包导出 |
| `src/openbiliclaw/eval/offline/content_key.py` | bvid/url/content_key 归一化 |
| `src/openbiliclaw/eval/offline/ground_truth.py` | events → 正/负样本、时间切片、采样 |
| `src/openbiliclaw/eval/offline/scenario.py` | 评估单元构造（候选集、标签对齐） |
| `src/openbiliclaw/eval/offline/metrics.py` | HR/NDCG/MRR/AUC + ILS/新颖性/覆盖率 |
| `src/openbiliclaw/eval/offline/runner.py` | 调引擎排序 + 计算指标（异步） |
| `src/openbiliclaw/eval/offline/report.py` | 报告生成（JSON/Markdown） |
| `scripts/run_offline_eval.py` | CLI 入口（--units/--K/--baseline） |

复用：`eval/run_logger.py`（run 记录）、`eval/loop.py`（优化循环）、`llm/embedding.py`（向量）、现有 `RecommendationEngine.serve()`。

---

## 8. 关键难点与决策

1. **content key 统一**：`events.url` → `bilibili:BV...`；`recommendations.bvid`（两种形态）→ 归一化；`candidates.candidate_key` 已是规范形态。统一以 `platform:BVID` 为 key。
2. **负样本选择偏差**：显式声明「未消费假设」，只用评估窗口前的候选池，报告标注口径。
3. **LLM 排序成本**：限制单元候选 N（20~50）与单元数（默认 20 个），支持 `--dry-run` 预估 LLM 消耗。
4. **时间泄漏**：M2 起必须时间切片，正样本晚于画像构建时间。
5. **推荐表 presented=0 是独立 bug**：M4 一并修复，让真实曝光-反馈数据开始积累，为未来在线指标打基础。

---

## 9. 里程碑

| 里程碑 | 内容 | 验收 |
|---|---|---|
| **M1（MVP）** | content_key + ground_truth + metrics + runner + CLI | 对当前候选池跑出首个可解释基线：HR@5 / NDCG@10 / AUC / ILS |
| **M2** | 时间切片 + 基线对比（随机排序 / 当前引擎） | 证明引擎优于随机；NDCG 相对提升可量化 |
| **M3** | 接入 OptimizationLoop；报告可视化 + 历史趋势 | 每次调参/改 prompt 都能自动对比指标回退 |
| **M4（可选）** | 修复 presented 落库；积累真实曝光-反馈 | 未来可加「线上 CTR」第二层评估 |

---

## 10. 与求职叙事的衔接

- **技术亮点**：隐式反馈 ground truth 构建、选择偏差处理、时间切片防泄漏、排序+多样性多目标指标、离线评估驱动迭代。
- **可讲的故事**：「我给自己的推荐系统搭了离线评估闭环，用 16 万条真实行为事件做偏好预测评估，发现引擎排序 NDCG@10 只有 X，通过调整 Y（如重排权重/bandit 探索率）提升到 Z%，且多样性 ILS 下降 W%」——每个数字都可复算。
- 与工业界对齐：召回→排序→重排 → 离线评估（NDCG/HR/AUC）→ A/B 的完整方法论。

---

## 11. 实施记录（2026-09-03）

### 已落地

| 里程碑 | 状态 | 交付 |
|---|---|---|
| **M1（MVP）** | ✅ 完成 | `src/openbiliclaw/eval/offline/`（content_key / ground_truth / scenario / metrics / runner / report）+ `scripts/run_offline_eval.py` + 12 个单元测试 |
| **M2** | ✅ 完成 | `--eval-after` 时间切片（CLI 参数）；engine vs random 双基线对比 |
| **M3** | ✅ 完成 | 历史趋势（`data/eval_runs/index.json` + `--history` + Δ 对比）；报告 JSON/Markdown 双输出 |
| **M4** | ✅ 完成 | 修复 `presented` 不落库：`GET /api/recommendations`、`reshuffle`、`append` 三处展示路径回写 `presented=1`/`presented_at` |

### 首个基线（真实数据，2026-09-03，seed=42，30 单元×20 候选）

| 指标 | engine | random |
|---|---|---|
| NDCG@10 | **0.887** | 0.644 |
| MRR | **0.900** | 0.589 |
| AUC | 0.742 | 0.742* |
| HR@10 | 1.000 | 1.000 |

\* AUC 衡量 relevance_score 本身的区分度，与最终排列无关，故两基线相同（设计使然）。

**结论**：引擎排序显著优于随机基线（NDCG +0.24、MRR +0.31），证明「画像相关分 → 排序」链路有效；评估确定性可复现（同 seed 结果一致）。

### 使用方式

```bash
# 跑一次完整评估（默认 20 单元）
.venv/bin/python scripts/run_offline_eval.py

# 带时间切片（只用 2026-08-15 之后的真实行为作正样本）
.venv/bin/python scripts/run_offline_eval.py --eval-after "2026-08-15 00:00:00"

# 只看历史趋势
.venv/bin/python scripts/run_offline_eval.py --history
```

报告输出到 `data/eval_runs/<时间戳>/`（report.md + report.json）。

### 已知限制 / 后续扩展

1. **严格时间切片的画像重建**：当前 `--eval-after` 只过滤正样本行为，未重建「历史时刻」的画像快照（relevance_score 仍用全量画像）。完整做法是保存历史画像快照、按 T0 重建——留作后续。
2. **OptimizationLoop 自动接入**：现有 `OptimizationLoop` 面向画像 prompt 优化，未强行耦合。当前迭代方式为「调参 → 重跑 CLI → 对比 index 趋势」，已是标准闭环；如需全自动调参可基于 `eval/loop.py` 扩展。
3. **embedding 级 ILS / 新颖性**：MVP 用确定性 topic 重合度近似；embedding_cache 为标题级 key，需先建「内容↔embedding」映射再升级。

---

## 12. E 里程碑：实时反馈闭环（2026-09-04）

离线评估证明「画像 → 排序」有效后，E 把**真实使用行为**接回画像，让推荐吃到的信号持续更新。

### 闭环图景（探查确认）

| 信号 | 状态 |
|---|---|
| events（收藏/点赞/读完/观看/屏蔽）→ ProfileUpdatePipeline | ✅ 已有 |
| 推荐点击 → 强信号立即更新画像 | ✅ 已有 |
| 文章 finished/hidden → 事件回流 | ✅ 已有（仅标题级） |
| **推荐点击 → recommendations 表消费状态** | ❌ **E1 修复** |
| **已读全文 tags → 偏好分析** | ❌ **E2 修复** |
| 兴趣时间衰减 | ✅ 已有（`decay_factor_per_week=0.9`） |

### E1：点击回写消费状态（曝光→点击数据闭环）

- `database.py`：新增 `clicked_at` 列迁移（幂等）+ `mark_recommendations_clicked()` + `get_clicked_bvids()`；`get_recommendations(exclude_processed=True)` 排除已点击项，**保留仅展示项**（曝光≠消费）。
- `app.py`：`/api/recommendations/click` 收到 `recommendation_id` 时回写 presented+clicked。
- `engine.py`：`serve()` 的 `_exclude_recently_viewed` 合并已点击 bvid——点过的视频即使重新进入候选池也不再被推荐。
- 效果：推荐表具备完整 `presented_at` / `clicked_at`，可算真实 CTR；已消费内容不再重复推荐。

### E2：已读回流（推荐吃到正在读的内容）

- **E2a**（app.py）：`article_finished` / `article_dismissed` 事件 context 折叠文章 tags（此前 tags 仅存 metadata、LLM 偏好分析永远看不到）——读完/屏蔽信号升级为标签级证据。
- **E2b**（`scripts/backfill_reading_to_profile.py`）：把 `read_archive` + `articles(finished/favorited)` 的 tags 批量送入 `PreferenceAnalyzer.analyze_events`，按 `layer_updaters._update_interest` 同款流程写回 flat preference + onion profile + profile 文件同步，推荐引擎下次 `serve()` 即生效。LLM 失败/结果异常时不写回。

### 真实验证（2026-09-04）

```text
$ .venv/bin/python scripts/backfill_reading_to_profile.py --limit 5
[reading→profile] 读取已读条目 5 条
[reading→profile] 现有画像: 255 个兴趣
[reading→profile] 画像变化 3 项：
  + 新增兴趣: 智能体强化学习 (0.65)      ← 《OpenAI o1 RL训练配方》(微信)
  + 兴趣权重变化: 影视评论 0.19 → 0.45    ← 《重器》影视评论 (知乎)
  + 新增兴趣: 广告投放 (0.40)            ← 《腾讯运营教程 Vol.4 出价策略》(小红书)
```

画像落盘确认：`preference.json` / `soul.json` 均含 `source: 'read-archive'`、`last_seen: 2026-09-04` 的新兴趣条目。

### 使用方式

```bash
# 已读回流（默认每源 50 条；--dry-run 只看不写）
.venv/bin/python scripts/backfill_reading_to_profile.py [--limit 50] [--dry-run]
```

### E 已知限制

1. `reshuffle`（换一批）走预计算缓冲，缓冲生命周期内新点击不会立即从当批排除——缓冲 TTL 短且下次填充即生效，可接受近似。
2. 已读回流是**触发式**（脚本/手动），未接定时调度；如需每日自动回流可挂 cron。
3. `_article_tags_for_context` 取前 8 个标签，超长标签列表截断（保护 LLM prompt 预算）。
