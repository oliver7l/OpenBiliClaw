# 10 技术前沿弹药：图神经网络 & 大语言模型（调研沉淀）

> 来源：`01_原始资料库/腾讯文档资料/02_图神经网络/`、`01_LLM调研/`（腾讯文档 MCP 提取）
> 定位：面试被问"你还了解哪些新技术/前沿方向"时的弹药。可包装成"我在腾讯做了 GNN 预研、持续跟进 LLM"。
> 用法：挑 2-3 个能讲透的点，不要全背。每个都能用"动机-思路-为什么能落地/不能落地"讲出来。

---

## 一、图神经网络（GNN）在推荐中的应用 —— "我做过 GNN 预研"

### 一句话开场
> "我在腾讯负责看点推荐的时候，对图神经网络在推荐里的应用做过一轮系统的预研，重点看了它在协同过滤、社交推荐、大规模工业落地这几个方向的边界——哪些是真能用，哪些只是论文效果。"

### 为什么推荐要用 GNN（动机）
传统协同过滤只利用 user-item 二部图的一阶信息；真实场景里还有 user-user 社交关系、item-item 关联、多模态语义关联。GNN 通过在图上的邻居聚合/消息传递，把**高阶连通性**显式编码进 embedding，能学到纯向量内积学不到的协同信号。

### 关键技术模型速览（挑重点讲）
| 模型 | 来源 | 一句话思路 | 能不能工业落地 |
|---|---|---|---|
| **PinSAGE** | Pinterest+Stanford, KDD'18 | GraphSAGE 的工业落地版：L1 随机游走聚合邻居、重要性池化、生产者-消费者 minibatch、多 GPU 训练、负采样优化 | ✅ 唯一百万级落地的代表作，把图模型推到 web-scale |
| **LightGCN** | 快手+中科大, SIGIR'20 | 对 NGCF 做消融，发现 GCN 的特征转换+非线性激活对 CF 没用，只保留邻居聚合；更快更好 | ⚠️ 简单轻快，但要求 user/item 共享 embedding 空间，矩阵构造方式限制大规模应用 |
| **NGCF** | NUS+中科大, SIGIR'19 | 在 user-item 二部图上做 embedding 传播，显式编码 CF 信号 | ⚠️ 没考虑 user-user/item-item 关系 |
| **GATNE** | 阿里+清华, KDD'19 | 属性复用异质图嵌入：base embedding + 各边关系下的 edge embedding（GraphSAGE+attention），支持直推/归纳 | ✅ 宣称可处理上亿节点十亿边，泛化强，但落地需按场景改 |
| **GraphRec** | 京东+港城大, WWW'19 | 类 DSSM：user latent = item aggregation + social aggregation，用分值 embedding 融合用户网络和打分表 | ⚠️ 和微信 social influence 类似；分值 embedding 可解释性差、初始化影响大 |
| **DANSER** | 腾讯微信+上交, WWW'19 | 双图注意力：用户同质性+用户间影响+物品同质性+物品间影响四种 embedding，权重计算类似 MMoE 的 gate | ✅ 可解释、可移植性强，微信场景验证过 |
| **MEIRec** | 阿里+北邮, KDD'19 | 异构图做隐式 query 推荐：邻居聚合+语义聚合，统一 term embedding 减少参数 | ✅ 淘宝离线 +3% AUC、在线 CTR +2.66% |

### 工业界落地视角（加分观点）
- **哪些能上**：PinSAGE 证明了"邻居采样 + 局部图 minibatch + 工业特征注入"这条路走得通；GATNE 的归纳式学习适合动态图。
- **哪些难上**：大量论文只在 Gowalla/Yelp/Amazon-Book 小数据集上验证，矩阵构造方式决定了不能直接搬到大规模场景（LightGCN 的局限）；Cluster-GCN 本质是 graph embedding 的工程优化而非推荐模型。
- **我的判断**：GNN 在推荐的增量主要来自"把更丰富的边关系（社交、多模态、时序）建模进去"，但要在稀疏性、冷启动和线上延迟之间权衡，落地更多是工程问题不是模型问题。

### 面试话术（STAR-lite）
> "我系统调研过 GNN 在推荐的应用（MEIRec/GraphRec/LightGCN/NGCF/PinSAGE/GATNE/DANSER），结论是：纯 CF 场景用 LightGCN 这种轻量聚合就够；要引入社交/多关系就用 GATNE 这类异质图；工业落地最关键的是邻居采样和 minibatch 训练（PinSAGE 的思路），否则内存和计算撑不住。我在看点也评估过把社交关系建模进来提升账号推荐的可行性，但因为数据量和线上成本暂缓，这让我更清楚 GNN 在工业界的真实边界。"

---

## 二、大语言模型（LLM）调研 —— "我跟进过 LLM"

### 一句话开场
> "我持续跟进过大模型的训练范式和工程优化，包括 RLHF/PPO、涌现现象、transformer 并行和推理加速，能聊模型训练到落地的完整链路。"

### LLM 架构演进（四阶段）
- 统计语言模型 SLM（GMM+HMM）→ 神经网络语言模型 NLM（RNN/LSTM/GRU）→ 预训练语言模型 PLM（BERT/BART/T5）→ 大语言模型 LLM（涌现能力，ChatGPT/GPT4）
- **三种主流架构**：Encoder-decoder（T5）；Causal Decoder（GPT1-3/OPT/BLOOM，只能看过去）；Prefix Decoder（U-PaLM/GLM-130B，前缀双向+生成单向）

### 训练链路（重点讲 RLHF 这段）
预训练 → Instruction Tuning（指令对齐）→ Alignment Tuning（对齐人类偏好）：
- **SFT**：监督微调，用人工标注的 (prompt, response) 让模型学会"好回答长什么样"。
- **RM（Reward Model）**：训练奖励模型，对多个候选回复打分，建模人类偏好。
- **RLHF（PPO）**：用 RM 打分做奖励信号，PPO 更新策略；为了让模型不偏离 SFT 行为，会加 KL 惩罚约束。
- **关键点**：RM 打分不完美 → 需要 KL 约束；PPO 对超参敏感、容易崩 → 需要经验（这是能讲深度的点）。

### 涌现现象（可讲透的认知点）
- 三类任务表现：**伸缩法则**（知识密集型，规模越大越好）、**涌现能力**（多步骤复杂任务，过临界点才爆发，如 CoT）、**U 型曲线**（先降后升，用 CoT 可转成伸缩法则）。
- **涌现的两个可能原因**：① 评价指标不够平滑（改成多选题后涌现消失）；② 复杂任务由子任务构成，子任务平滑增长但宏观指标显涌现。
- **顿悟现象**：训练记忆期→平台期→泛化期，突然学会规律。
- ICL（Few-shot prompt）和 CoT（思维链）是两类典型涌现能力。

### 配套工程调研（体现工程嗅觉）
| 技术 | 解决什么 |
|---|---|
| Flash Attention | 让 attention 计算不落显存，线性/近似内存复杂度，大模型长序列训练提速 |
| DeepSpeed-Chat | 把 RLHF 三阶段（SFT/RM/PPO）的工程化训练管线铺好 |
| Constitutional AI | 用一组原则让模型自我批判、自我修正，减少人工 RLHF 依赖 |
| ToolFormer | 让 LLM 学会"调用工具"，扩展模型能力边界 |
| transformer 并行 | 数据并行/张量并行/流水线并行，大模型训练显存和吞吐的分配 |
| transformer 位置外推 | 训练时短、推理时长（如 RoPE 类），解决长度外推问题 |

### 面试话术
> "我调研过大模型从预训练到 RLHF 的完整链路，重点看两块：一是对齐技术，SFT 教模型格式、RM 教模型偏好、PPO 用 KL 约束在探索和稳定性之间权衡；二是工程，Flash Attention 解决 attention 显存、DeepSpeed 解决 RLHF 三阶段管线、并行策略解决吞吐。我理解 LLM 对推荐的影响主要在两个层面：一是作为内容理解/特征抽取的上游（比如用语义向量做召回和冷启动），二是生成式交互重构产品形态，这两条线我都有关注。"

---

## 来源映射
| 弹药 | 原始文档 |
|---|---|
| GNN 推荐模型调研表 | 02_图神经网络/图神经网络在推荐中的应用.md、图神经网络在工业界的应用.md |
| LLM 架构/涌现/RLHF | 01_LLM调研/大模型调研.md |
| PPO/RLHF 细节 | 01_LLM调研/PPO调研.md |
| 工程优化 | 01_LLM调研/Flash Attention.md、DeepSpeed-Chat调研.md、transformer并行.md、transformer位置外推.md |
| 对齐技术 | 01_LLM调研/Constitutional AI调研.md、ToolFormer调研.md |
| transformer 基础 | 01_LLM调研/transformer调研.md、transformer代码.md |
