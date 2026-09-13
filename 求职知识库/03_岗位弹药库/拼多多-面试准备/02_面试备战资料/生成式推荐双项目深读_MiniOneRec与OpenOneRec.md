# 生成式推荐两个开源项目深度对比：MiniOneRec × OpenOneRec

> 一句话：**MiniOneRec（中科大+新国立，何向南组）回答"最小可复现的生成式推荐怎么做"；OpenOneRec（快手 OneRec 团队）回答"推荐基础模型怎么做"**。两者共用「**item 即 token**」的核心范式，但一个深耕**后训练配方**（对齐+约束解码+GRPO 奖励），一个主攻**预训练体系**（协同预训练+抗遗忘蒸馏+Scaling Law）。

| | MiniOneRec | OpenOneRec |
| --- | --- | --- |
| 论文 | arXiv 2510.24431（2025-10-28） | arXiv 2512.24762（2025-12-31, v2 2026-02-04） |
| 团队 | 中科大 LDS AlphaLab NExT + 新国立 | 快手 OneRec Team（Guorui Zhou、Kun Gai 等 50 人） |
| 定位 | 首个全开源生成式推荐框架，**最小复现 OneRec** | 推荐基础模型 + 指令跟随**评测基准** + 全栈管线 |
| Backbone | Qwen2.5-Instruct 0.5B~7B | Qwen3 1.7B / 8B（Standard + Pro） |
| 数据 | Amazon Review（Industrial / Office，各约 3.5k item） | 快手三域 1.2 亿交互 / 20 万用户 + 通用语料 |
| 算力 | 4-8 张 A100/H100 80GB | 工业级（130B tokens 语料） |
| 开源 | 代码+数据+权重（Apache-2.0） | 代码+数据+权重（Apache-2.0），Code 未完全发布 |
| 核心贡献 | Scaling Law 验证 + 全链路 SID 对齐 + 推荐 RL | RecIF-Bench + 抗遗忘协同预训练 + 推荐 Scaling Law 公式 |

---

## 一、共同范式：Item as Token（SID / Itemic Token）

两家都把"商品/内容"压成离散 token 序列，让 LLM 像预测下一个词一样预测下一个 item。都强调**层级结构**——相似 item 共享前缀，知识可迁移。

**但量化方案不同（这是面试好谈资）**：

| | MiniOneRec | OpenOneRec |
| --- | --- | --- |
| 量化器 | RQ-VAE（可学习，端到端训） | RQ-Kmeans（聚类，无需训练） |
| 层数 L | 3 | 3 |
| 码本 K | 256/层 → 256³ = 2²⁴ ≈ 1677 万 | 8192/层 → 8192³ ≈ 5.5×10¹¹ |
| 输入 | title+description → 冻结 Qwen3-Embedding-4B | 视频文本描述 → Qwen3-8B-Embedding（另提供 4096 维文本+5 帧视觉 embedding） |
| token 形态 | `<a_xx><b_xx><c_xx>` | `<\|item_begin\|><item_a_5028><item_b_6733><item_c_2559><\|item_end\|>` |

**为什么 K 差 32 倍**：MiniOneRec 跑 Amazon 单域 3.5k item，256³ 绰绰有余；OpenOneRec 跑快手 1534 万 item 三域，需要超大码本避免碰撞——但即便如此，迁移到 Amazon 时碰撞率仍 >30%（见 §4.3）。

**RQ-VAE 的量化与损失**（MiniOneRec 原文）：
```
c_l = argmin_k ||r_l − e_k^(l)||₂ ,  r_{l+1} = r_l − e_{c_l}^(l)
L(x) = ||x − x̂||₂²  +  Σ_l ( ||sg[r_l] − e_{c_l}^(l)||₂² + β||r_l − sg[e_{c_l}^(l)]||₂² )
```
- 文本编码器**冻结**；codebook / encoder / decoder 联合训练；
- 防 codebook collapse：用**第一个 batch 的 k-means 质心**初始化码本；
- 训练配置：单卡，batch 20480，lr 1e-3，**10000 epoch**。

---

## 二、MiniOneRec 深读：后训练配方是核心

### 2.1 任务形式化

历史序列 `H_u = [i1, i2, ..., iT]`，每个 item 编成三级 SID `{c₀,c₁,c₂}`；策略 `π_θ` 自回归生成下一个 item 的 SID；推理时用 beam search 取 top-k 作为推荐列表。

### 2.2 关键创新①：全流程 SID-Text 对齐（Alignment Tasks）

普通生成式推荐只做 SID→SID 的 next-token prediction，LLM 的世界知识用不上。MiniOneRec 加了一组**双向桥接任务**，且**贯穿 SFT 与 RL 两个阶段**（关键！）：

**推荐任务**
1. **Generative Retrieval**：SID 序列历史 → 下一个 item 的 SID
2. **Asymmetric Item Prediction**（非对称）：(a) 文本历史 → 下一个 item 的 SID；(b) SID 历史 → 下一个 item 的文本标题

**对齐任务**
3. **SID–Text 语义对齐**：SID → 标题（反向映射）；标题 → SID
4. **Item Description 重建**：单个 SID → 生成 item 描述；描述 → SID（**仅 SFT 阶段**，因描述空间太大）
5. **用户偏好摘要**：SID 序列 → 自然语言用户画像（**仅 SFT**；无标签，用 **DeepSeek 抽取伪标签**）

**消融结论（Figure 4a）**：
- `w/o Align`（纯 SID→SID）**最差**；
- `w/ SFTAlign`（只在 SFT 对齐）和 `w/ RLAlign`（只在 RL 对齐）居中；
- **全流程对齐（SFT+RL 都做）最高** → 证明"世界知识接地"必须全程。

### 2.3 关键创新②：约束解码 + Beam Search 采样

RLVR 用在推荐上有两个障碍：

**障碍 1：动作空间是封闭的 item 集合**，比自然词表小几个数量级 → 重复采样经常采出同样的 item，多样性差。他们定义多样性指标：
```
Div({e_k}) = |Unique({e_1..e_G})| / G
```
试了两种补救：
- **Dynamic Sampling**（DAPO 思路）：超采样 1.5 倍，再挑"含 ground-truth 且内部多样性最大"的子集 → 有效但需额外前向，且随训练退化；
- **Beam Search（最终采用，width=16）**：束内天然互不相同 → **零重复**，效率最高。消融显示：达到同等精度只用 dynamic 变体约 **2/3 的样本**。

**障碍 2：必须生成合法 item** → 用 **LogitProcessor 约束解码**（掩掉非法 token），保证每步只产出词表内合法 SID。

> 工程坑（README 2026-01-04 公告）：用 Instruct 模型复现时若评估日志里 **CC 指标非零**，说明仍在生成大量非法 item、约束解码没生效——与 transformers 版本相关，可换 base 模型（如 Qwen2.5-base）规避。

### 2.4 关键创新③：混合奖励（Rule + Rank），以及一次失败的 reward hacking

纯二元奖励（命中=1，否则=0）不区分"差一点"和"完全不相关"，对排序质量指导弱。引入 **rank-aware 奖励**：负样本在自己排序中越靠前（ρ 越小），惩罚越重：
```
R̃_rank(e_k, e_t) = 0                        若 e_k = e_t
                  = −1 / log(ρ_k + 1)        否则
R_rank(e_k, e_t) = − R̃_rank(e_k,e_t) / Σ_j R̃_rank(e_j,e_t)     （组内归一化）
R(e_k, e_t) = R_rule + R_rank ,   R_rule = 1 若命中 else 0
```
**GRPO 目标**（组内标准化优势 + token 级重要性比 + KL 惩罚）：
```
Â_i = (S_i − μ_{1:G}) / σ_{1:G}
J_GRPO = E[ 1/G Σ_i 1/|y_i| Σ_t ( min(w_{i,t}Â_i , clip(w_{i,t},1−ε,1+ε)Â_i ) − β·KL[π_θ||π_ref] ) ]
```

**重要负面结论**：他们试过把**预训练 SASRec 的协同过滤 logit 当奖励**注入 RL，结果**性能显著下降**——因为出现了 **reward hacking**：推荐准确率在降，而协同奖励仍在涨。说明协同信号与真实目标错位。**最终采用 rule+rank 混合**。

> 这个"失败的实验"是面试里极好的谈资：多数人只知道加协同信号好，你能说出"协同奖励会被 hack，因为冻结模型的 logit 与在线策略分布漂移后不再是真目标"。

### 2.5 实验数字

**Table 1（Amazon Industrial / Office，HR / NDCG）**

| 类别 | 方法 | Industrial HR@10 | NDCG@10 | Office HR@10 | NDCG@10 |
| --- | --- | --- | --- | --- | --- |
| 传统 | GRU4Rec / Caser / **SASRec** | 0.0999 / 0.0942 / **0.1088** | 0.0669 / 0.0628 / **0.0806** | 0.1019 / 0.1093 / **0.1120** | 0.0669 / 0.0737 / **0.0858** |
| 生成式 | HSTU / TIGER / LC-Rec | 0.1163 / 0.1321 / 0.1332 | 0.0958 / 0.0908 / 0.0952 | 0.1400 / 0.1408 / 0.1237 | 0.1126 / 0.1002 / 0.0920 |
| LLM | BIGRec / D3 / S-DPO | 0.1370 / 0.1500 / 0.1524 | 0.0997 / 0.1082 / 0.1082 | 0.1434 / 0.1634 / 0.1587 | 0.1091 / 0.1213 / 0.1255 |
| **Ours** | **MiniOneRec** | **0.1586** | **0.1167** | **0.1634** | **0.1242** |

**其他关键结论**
- **Scaling**：0.5B→7B，训练 loss 与评估 loss **随规模单调下降**（首篇在公开数据上系统验证生成式推荐 Scaling Law）；
- **预训练权重的价值**（Table 3）：Industrial NDCG@10，**scratch 0.0804 vs 预训练 0.1139**（+41%）；Office **0.0941 vs 0.1242**（+32%）→ 世界知识确实有用；
- **OOD 跨域**（只在 Industrial 训，直接测 Office，HR@10）：Qwen-Text **0.0057** ≪ Qwen-SID **0.0733** ≪ MiniOneRec-w/ RL-OOD **0.0892**（接近域内训练的 GRU4Rec 0.1019）→ **结构化 SID 词汇比纯文本标题更容易被 LLM 利用**；
- **RL-only 变体迁移更好**（跳过 SFT 防过拟合源域），呼应"RL 比 SFT 更泛化"的近期共识。

---

## 三、OpenOneRec 深读：基础模型 + 抗遗忘 + 推荐 Scaling Law

### 3.1 RecIF-Bench 数据规模（Table 1）

| 域 | 用户 | Item | 交互 | 平均历史长度 | 平均目标 |
| --- | --- | --- | --- | --- | --- |
| 短视频 | 195,026 | 13,107,675 | 94,443,611 | 458.1 | 8.6 |
| 广告 | 151,259 | 177,548 | 5,341,911 | 29.9 | 5.5 |
| 商品 | 144,307 | 2,055,240 | 20,087,210 | 132.5 | 6.7 |
| **合计** | **202,359** | **15,340,463** | **119,872,722** | 574.9 | 17.5 |

- **严格 user-based split**：随机 20% 用户完全从训练中剔除（zero leakage）；每个用户再按时间戳切历史/目标。
- 元数据极其丰富：用户侧 **User Portrait**（自然语言 + itemic token 交织的叙事，含人口属性/搜索/关注/评论/直播/购物车/优惠券/广告曝光/商业意图）；item 侧 4096 维文本 embedding + 5 帧 × 1152 维视觉 embedding + 1300 万视频 dense caption；交互侧多标签（点赞/关注/评论/有效播放/不喜欢）。

### 3.2 八任务四层能力金字塔（Table 2）

| 层 | 任务 | 输入 X | 目标 Y | 指标 |
| --- | --- | --- | --- | --- |
| L0 对齐 | Item Understanding | item i | item 描述 | LLM-as-Judge |
| L1 基础预测 | 短视频推荐 | H^video | next video | Pass@1/32, Recall@32 |
| | 广告推荐（跨域） | H^video + H^ad | next ad | 同上 |
| | 商品推荐（跨域） | H^video + H^product | next product | 同上 |
| | Label Prediction | H^video + item | Yes/No | AUC |
| L2 指令跟随 | Interactive Rec | User Portrait + query q | 用户会正向交互的 item | Pass@1/32, Recall@32 |
| | Label-Conditional Rec | H^video + 行为 a（点赞/分享…） | 该行为下的 item | 同上 |
| L3 推理 | Rec. Explanation | Portrait + H^video + item | 自然语言解释 | LLM-as-Judge（**Gemini-2.5-Pro 造 ground truth**） |

- 判别式 baseline（SASRec/BERT4Rec/GRU4Rec/HSTU）**每个任务要单独训一个模型**；生成式统一框架一个模型跑全部 → 这本身就是"基础模型 vs 专用模型"的论据。
- **通用能力 Sanity Check**：MATH-500 / GSM8K / AIME'24 / MMLU-Pro / GPQA-Diamond / IFEval / LiveCodeBench v5 —— 用来监测灾难性遗忘。

### 3.3 预训练：两阶段 + 数据混合

**数据三类（推荐域）**
1. **Itemic Dense Caption**：item token → 自然语言 caption（建立 item 感知）
2. **Sequential User Behavior**：长序列 next-item prediction（注入协同过滤信号）
3. **Interleaved User Persona Grounding**：用户画像叙事（静态属性+搜索+itemic 序列+兴趣摘要交织）→ 学"用户特征 ↔ 行为模式"的深层关联

**通用域语料**：多语言、以 **Coding / STEM / Medical** 为主，**优先推理密集型数据**（数学推导、逻辑谜题、代码）；用 **MinHash 模糊去重**过滤掉与评测集相似的样本，保证是真泛化。

**Stage 1 · Itemic-Text Alignment**
- 扩充词表，itemic token 的 embedding 用**已有 embedding 的均值与协方差**从多元正态采样初始化；
- **只训 itemic token 的 embedding**（其余冻结）；
- 工程细节：Qwen3 小模型（0.6B/1.7B/4B）是 **tied embedding**（embedding 与 output projection 共享），大模型（8B+）独立 → 大模型需**同时放开 itemic token 的 output projection**。
- lr 峰值 1e-3。

**Stage 2 · Full-Parameter Co-Pretraining**
- 全参解冻注入推荐知识，**同时维持相当比例的通用域数据防灾难性遗忘**；
- 上下文长度 **32K**（容纳超长用户行为序列）；
- lr 峰值 1e-4，AdamW β=(0.9, 0.95)，wd 0.1，warmup 10%，cosine 衰减。
- 数据量：Standard **32B tokens / 41.3M samples**；Pro **130B tokens / 179.1M samples**（Pro 覆盖约 2000 万用户、9800 万 item caption）。

### 3.4 推荐 Scaling Law（硬核，面试可甩公式）

在 N ∈ {0.6, 1.7, 4, 8, 14}B 上扫 token budget，用 `C ≈ 6ND`，取 loss 下包络拟合：
```
N_opt ∝ C^0.44 ,   D_opt ∝ C^0.56
L(N,D) = 0.4232 + 502.32/N^0.3325 + 7.02/D^0.1865
```
三点解读（论文原文）：
1. **数据密集型（b=0.56 > a=0.44）**：与 Chinchilla 的 0.5/0.5 均分不同 → **推荐域要把预算更多压在数据量而非参数量上**。原因：数据指数 β≈0.19 显著低于文本域的 ≈0.28（数据收益衰减更快），数学上必然 b>0.5。
2. **Warm-start 效应（A=502 极大，B=7.02 极小）**：B 小说明 Qwen3 backbone 的迁移学习大幅降低了初始分布熵；A 大是"模型容量"与"预训练质量"的耦合（更大模型往往配更多数据，增益被统计进 A）。
3. **推荐任务熵低（E=0.42 vs 文本 ≈1.69）**：结构化特征（dense caption）让任务更接近确定性 → **必须刻意提高推荐语料的多样性与质量**，否则容易饱和。

### 3.5 后训练三阶段

**① Multi-Task SFT**
- 混合"推荐对话数据"（由 16 万用户元数据合成）+ 开源通用推理/指令数据；
- lr 降到 **2e-5 → 5e-6**；
- 意外收获：**通用域推理能力会"交叉授粉"到推荐任务**——模型自发对复杂推荐 query 生成连贯推理链，尽管推荐样本里没显式监督推理。

**② On-Policy Distillation（恢复通用能力）** ← 最有工程启发的一节
- 问题：预训练+SFT 后，通用推理能力**仍持续掉**。
- 做法：学生（当前策略）自己采样轨迹，老师（**同规模原版 Qwen3**）逐 token 给反馈；目标 **per-token reverse KL**：
```
D_KL(π_θ || π_teacher) = E_{x∼π_θ}[ log π_θ(x_{t+1}|x_{1..t}) − log π_teacher(x_{t+1}|x_{1..t}) ]
R_KL(o,x) = clip( −D_KL(π_θ || π_teacher), α, β )
∇_θ J = E[ Σ_t ∇_θ log π_θ(x_t|o,x_{<t}) · R_KL(o,x) ]
```
用**策略梯度**直接优化（不是静态数据集上的 off-policy 蒸馏）。
- **词表不一致的坑**：老师不认识新增的 itemic token。朴素做法是丢弃含 itemic token 的轨迹，但 reverse KL 本身是有偏估计，直接丢会引入**采样偏差**，随训练模型会逐渐抬高 itemic token 概率 → **训练崩溃**。他们的解法三条：
  1. **Prompt 只从通用域采**（这些 prompt 下策略本就不该生成 itemic token）；
  2. **Itemic Token 惩罚与截断**：轨迹第 t 步一旦出现 itemic token，把 `log π_teacher` 设为 **−1e9**（视为零概率）并**截断轨迹**，配合 reward clipping 给强负信号；
  3. **高温采样**增强探索，主动把 itemic token "钓"出来再纠正。
- 规模：20 万通用题；按 Qwen3 报告的做法随机追加 `/think`、`/no_think` 或空后缀，对齐强制思考/不思考/自动思考三种范式。

**③ Rec-RL（推荐强化学习）**
- GRPO，参考模型 = 蒸馏后的模型（保持 KL 惩罚，防止通用能力再次掉）；
- **规则奖励（稀疏）**：5 个核心推荐任务（短视频/广告/商品/交互式/标签条件）统一：
```
r(R_i) = +1.0 若目标 itemic token s ∈ R_i ；否则 0.0
```
→ 组内采样多候选，等价于在生成空间做 "**Soft Ranking**"。

### 3.6 灾难性遗忘实测（Table 5/6，这是最值得记的数字）

Thinking 模式，Qwen3-8B → OneRec-8B：

| 能力 | 任务 | Qwen3-8B | OneRec-8B | 变化 |
| --- | --- | --- | --- | --- |
| 数学 | MATH-500 | 0.9520 | 0.9460 | **−0.6%**（几乎不掉） |
| | GSM8K | 0.9568 | 0.9575 | +0.07% |
| | AIME'24 | 0.7917 | 0.7250 | **−8.4%** |
| 通用知识 | **MMLU-Pro** | 0.7235 | **0.5342** | **−26.2%** |
| | GPQA-Diamond | 0.5606 | 0.5000 | −10.8% |
| 对齐 | IFEval | 0.8577 | 0.7893 | −8.0% |
| 代码 | LiveCodeBench v5 | 0.5484 | 0.4910 | −10.5% |

**结论**：**推理/数学能力基本保住（蒸馏有效），但世界知识（MMLU-Pro）掉得最狠 −26%**——论文自己也承认"通用数据的多样性不足"是瓶颈。这是"通用能力 vs 推荐能力跷跷板"最硬的实证。

### 3.7 RecIF-Bench 主结果（Table 4 节选，Recall@32）

| 任务 | SASRec | HSTU | TIGER | LC-Rec-8B | OneRec-1.7B | OneRec-8B | **8B-Pro** |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 短视频 | 0.0119 | 0.0119 | 0.0132 | 0.0180 | 0.0272 | 0.0355 | **0.0369** |
| 广告 | 0.0293 | 0.0409 | 0.0581 | 0.0723 | 0.0707 | 0.0877 | **0.0964** |
| 商品 | 0.0175 | 0.0178 | 0.0283 | 0.0416 | 0.0360 | 0.0470 | **0.0538** |
| 标签条件 | 0.0140 | 0.0139 | 0.0123 | 0.0170 | 0.0184 | 0.0228 | **0.0235** |
| 交互式 | – | – | – | 0.2394 | 0.1941 | 0.3032 | **0.3458** |
| 推荐解释 | – | – | – | 3.935 | 3.354 | 3.677 | **4.038** |

两个 scaling 维度都成立：**数据 scaling**（Pro > Standard）、**模型 scaling**（8B > 1.7B）。

### 3.8 跨域迁移：三种适配策略（Table 8，很有工程价值）

预训练 tokenizer 在开放域上优化，迁移到 Amazon 垂类时**碰撞率 >30%**，直接套会灾难性丢信息。他们系统对比三招：

| 策略 | 做法 | 碰撞率 | 平均 R@10 提升 |
| --- | --- | --- | --- |
| ① Extended Residual Quantization | 在预训练第 3 层残差上再叠一层 **FSQ** 做第 4 层码 | 3.05% | 比 LC-Rec +10.0% |
| ② Text-Only Adaptation | 完全绕开 itemic token，用 metadata 抽 **5 个关键词**表示 item | 4.27% | 比 ① 再 +18.8% |
| ③ **Text-Augmented Itemic Tokens**（最优） | `[原三层 itemic token] + [关键词]`，**不改预训练结构** | **0.47%** | 几乎全数据集 SOTA |

**洞察**：③ 胜出的关键不是精度最高，而是**同时保住了预训练的协同信号（itemic token）和语言能力（关键词消歧），且严格保留了预训练的结构完整性**。①虽然降了碰撞，但非预训练的第 4 层破坏了原有层级语义。

最终 10 个 Amazon 数据集 Recall@10 **平均 +26.8%**（Baby +34.6%、Pet +36.3%、Tools +35.4%、Toys +35.0%）。

---

## 四、两份工作放一起看：技术演进脉络

```
TIGER(2023) RQ-VAE + 生成式检索
  → LC-Rec(2024) LLM 与 SID 多任务对齐
    → OneRec / OneRec-V2(2025) 工业端到端，替代级联（4 亿 DAU）
      ├→ MiniOneRec(2025.10) 开源最小复现 + 后训练配方（对齐/约束解码/混合奖励）
      └→ OpenOneRec(2025.12) 基础模型化 + 评测基准 + 抗遗忘 + Scaling Law
```
同期的工业旁支：**HSTU**（Meta，万亿参数流式）、**MTGR**（美团，保留 DLRM 特征 + 用户级压缩）、**OnePiece**（LLM 式上下文工程进级联排序）、**RankMixer**（抖音，排序模型规模化）。

---

## 五、对你拼多多面试的直接价值

拼多多二面 A2「生成式重排 / 生成式推荐」大概率会问"业界进展""怎么看端到端生成式替代级联""有什么坑"。下面这些是可直接引用的硬料：

1. **为什么要 SID 而不是文本标题**：MiniOneRec OOD 实验给出铁证——Qwen-Text HR@10 **0.0057** vs Qwen-SID **0.0733**（差 12 倍），结构化离散词汇远比长文本容易被 LLM 利用；且 SID 省 context token、推理更快。
2. **SID 容量怎么设计**：MiniOneRec 256³=2²⁴（够千万级），OpenOneRec 8192³（百亿级）；码本不是越大越好，要看 item 量与碰撞率——OpenOneRec 迁移时 8192³ 都撞了 30%。
3. **约束解码是工业落地的必需品**：不约束就会生成不存在的 item（MiniOneRec 用 LogitProcessor 掩非法 token；评估时监控 CC 指标是否非零）。
4. **RL 阶段的采样要用 beam search 而不是随机采样**：动作空间小 → 重复率高；beam width 16 天然零重复，样本效率是 dynamic sampling 的 1.5 倍。
5. **奖励设计要防 reward hacking**：协同过滤 logit 当奖励会让"奖励涨、准确率跌"（MiniOneRec 实证失败案例）；最终 rule(hit=1) + rank(−1/log(ρ+1)) 混合。
6. **通用能力 vs 推荐能力的跷跷板**：OpenOneRec 实测 MMLU-Pro **−26%**、AIME −8%、LiveCodeBench −10%，而 MATH-500 几乎不掉 → 推理能力靠 on-policy 蒸馏能救，世界知识救不回来。
7. **推荐 Scaling Law 与文本不同**：`N_opt ∝ C^0.44, D_opt ∝ C^0.56`——**推荐更吃数据**；且推荐任务熵 E=0.42 远低于文本 1.69，语料多样性是瓶颈。
8. **跨域迁移优先"文本增强 itemic token"**：保结构 + 关键词消歧，碰撞率 0.47%，优于扩展量化层。
9. **对齐要贯穿全流程**：只在 SFT 或只在 RL 做都不如两阶段都做（MiniOneRec 消融）。
10. **预训练权重是必需品**：scratch 训练 NDCG@10 掉 30~40%。

---

## 六、30 秒 / 2 分钟答题模板

**30 秒**：
> 生成式推荐现在有两条开源主线。MiniOneRec 是学术界的最小复现版，核心贡献三件事：RQ-VAE 把 item 压成三级 SID、全程做 SID-文本对齐把 LLM 世界知识接地、RL 阶段用约束 beam search + GRPO 混合奖励（命中 + 排序惩罚）。OpenOneRec 是快手的工业基础模型版，多了三件事：RecIF-Bench 八任务四层评测体系、推荐+通用数据协同预训练抗遗忘、以及 on-policy 蒸馏恢复推理能力。共同结论是——item 即 token，且 Scaling Law 在推荐域成立但**更偏数据密集**（D_opt ∝ C^0.56）。

**2 分钟**（加这三层）：
> ① **为什么必须离散化**：纯标题序列上下文太长且无法保证生成合法 item；离散 SID 让 LLM 能直接吃长历史，OOD 实验里 SID 比纯文本 HR@10 高一个数量级。
> ② **两个工业坑**：一是采样重复——动作空间是封闭 item 集合，随机采样多样性差，必须用束内互斥的 beam search；二是奖励 hack——把冻结模型的协同 logit 当奖励会出现"奖励涨准确率跌"，必须用规则命中 + 排序惩罚。
> ③ **推荐基础模型的核心矛盾是遗忘**：OpenOneRec 实测训完推荐后 MMLU-Pro 掉 26%，靠 on-policy 蒸馏（学生自采样、老师逐 token reverse KL 当 reward）+ itemic token 惩罚截断才把推理能力拉回来，但世界知识仍救不回。这也是我认为离真正"推荐基础模型"最大的 gap。

---

## 七、参考

- MiniOneRec: arXiv 2510.24431 · github.com/AkaliKong/MiniOneRec · huggingface.co/kkknight/MiniOneRec
- OpenOneRec: arXiv 2512.24762 · github.com/Kuaishou-OneRec/OpenOneRec · huggingface.co/OpenOneRec
- 关联文献：OneRec (2502.18965) / OneRec-V2 (2508.20900) / OneRec Technical Report (2506.13695) / TIGER (NeurIPS'23) / LC-Rec (ICDE'24) / HSTU (ICML'24) / MTGR (美团) / OnePiece (2509.18091) / ReRe (2510.12211) / RecZero (NeurIPS'25)

---

**归档**：`求职知识库/03_岗位弹药库/拼多多-面试准备/02_面试备战资料/`
**关联**：`notes/阅读收藏库/37-小红书-MiniOneRec生成式推荐项目入门.md`、`notes/阅读收藏库/49-小红书-快手开源OpenOneRec生成式推荐基础模型.md`
