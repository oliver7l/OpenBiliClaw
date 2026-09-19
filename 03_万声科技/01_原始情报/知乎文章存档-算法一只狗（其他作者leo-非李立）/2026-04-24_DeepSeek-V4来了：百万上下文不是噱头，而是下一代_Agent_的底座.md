# DeepSeek-V4来了：百万上下文不是噱头，而是下一代 Agent 的底座

- 链接: https://zhuanlan.zhihu.com/p/2030977976515437334
- 发布: 2026-04-24 11:54:50
- 赞同: 2 | 评论: 0

---

DeepSeek-V4来了：百万上下文不是噱头，而是下一代 Agent 的底座

千呼万唤，DeepSeek-V4终于出来了。从整体来看，V4版本很明显围绕“超长上下文效率”重构出来的新架构。

![](https://pic1.zhimg.com/v2-77eaf827433f41bec5f11b3a430e0ec8_1440w.jpg)

这一次新的版本拥有百万字超长上下文，在 Agent 能力、世界知识和推理性能上均实现国内与开源领域的领先。模型按大小分为两个版本，两个版本的上下文长度都在1M。而且已经发布直接开源

- Pro版本整体的参数量级在1.6T，激活参数在49B
- flash版本参数量级在284B，激活参数在13B

![](https://pica.zhimg.com/v2-6ea94161bcf98036b5659d4eaf642c0a_1440w.jpg)

即日起，用户可登录 DeepSeek 官网 [http://chat.deepseek.com](http://chat.deepseek.com) 或官方 App，直接体验最新 **DeepSeek-V4** ，感受 **1M 超长上下文记忆** 带来的全新对话能力。同时，API 服务也已同步升级。开发者只需将 model_name 修改为 deepseek-v4-pro 或 deepseek-v4-flash，即可快速接入并调用 DeepSeek-V4。

![](https://pic1.zhimg.com/v2-af3cf542043130279488125310d1f578_1440w.jpg)

## 模型跑分怎么样？

首先，V4版本性能比肩顶级闭源模型

![](https://picx.zhimg.com/v2-0d1cc88a3f855c0b39cba25ae75f1d8d_1440w.jpg)

- **Agent 能力大幅提高：** 相比前代模型，DeepSeek-V4-Pro 的 Agent 能力显著增强。在 Agentic Coding 评测中，V4-Pro 已达到当前开源模型最佳水平，并在其他 Agent 相关评测中同样表现优异。目前 DeepSeek-V4 已成为公司内部员工使用的 Agentic Coding 模型，据评测反馈使用体验优于 Sonnet 4.5，交付质量接近 Opus 4.6 非思考模式，但仍与 Opus 4.6 思考模式存在一定差距。
- **丰富的世界知识：** DeepSeek-V4-Pro 在世界知识测评中，大幅领先其他开源模型，仅稍逊于顶尖闭源模型 Gemini-Pro-3.1。
- **世界顶级推理性能：** 在数学、STEM、竞赛型代码的测评中，DeepSeek-V4-Pro 超越当前所有已公开评测的开源模型，取得了比肩世界顶级闭源模型的优异成绩。

![](https://pic2.zhimg.com/v2-93469bf906fdf8dc90798601c8bc803b_1440w.jpg)

**结构创新和超高上下文效率**

DeepSeek-V4 开创了一种全新的注意力机制，在 token 维度进行压缩，结合 DSA 稀疏注意力（DeepSeek Sparse Attention），实现了全球领先的长上下文能力，并且相比于传统方法大幅降低了对计算和显存的需求。 **从现在开始，1M（一百万）上下文将是 DeepSeek 所有官方服务的标配。**

![](https://picx.zhimg.com/v2-34f420aaa755a7e6c19b830b8c8d703f_1440w.jpg)

DeepSeek-V4 和 DeepSeek-V3.2 的计算量和显存容量随上下文长度的变化

**Agent 能力专项优化**

DeepSeek-V4 针对 Claude Code 、OpenClaw、OpenCode、CodeBuddy 等主流的 Agent 产品进行了适配和优化，在代码任务、文档生成任务等方面表现均有提升。下图为 V4-Pro 在某 Agent 框架下生成的 PPT 内页示例：

![](https://pic1.zhimg.com/v2-963ecce8c1bd0ea598d6f6b2f8c90c12_1440w.jpg)

## 新版本下模型架构

这一次，DeepSeek官方直接放出了技术论文，具体可以看这里：

[https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro/blob/main/DeepSeek_V4.pdf](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro/blob/main/DeepSeek_V4.pdf)

我在这里总结一下，论文开篇就讲得很明确：

现在 reasoning model 很依赖 test-time scaling，但传统 attention 的二次复杂度会让超长上下文越来越贵，最后变成推理和长链条任务的瓶颈。DeepSeek-V4 的目标，就是打掉这个瓶颈，让 **1M context** 真正可行。

这背后其实有两个层面：

第一层是 **产品层** 。未来很多任务不是“问一个问题，答一句话”，而是长文档、多文档、复杂 Agent 工作流、超长链路推理。这些场景对上下文长度和推理成本都很敏感。

第二层是 **研究层** 。如果长上下文推理太贵，那 test-time scaling 的收益就很快撞墙。V4 实际上是在给“更长推理、更长轨迹任务”铺底座。

## 1）CSA + HCA：V4 真正的王牌

这是整篇论文最关键的部分。

V4 没继续走原始 dense attention 那条路，而是做了一个 **混合注意力架构** ：

![](https://pic4.zhimg.com/v2-29056af29603fabc86db86bea3191b63_1440w.jpg)

- **CSA（Compressed Sparse Attention）** ：先把 KV 沿序列压缩，再做稀疏选择，只让 query 看 top-k 的压缩块。
- **HCA（Heavily Compressed Attention）** ：压得更狠，但保留 dense attention。

你可以把它理解成：

- CSA 更像“ **压缩后再检索** ”，偏向高效找重点；
- HCA 更像“ **极限摘要后整体看** ”，偏向把全局成本压下去。

这两个交替使用，目的不是只做一个近似 attention，而是做一个 **兼顾局部细节、全局覆盖、推理成本** 的折中设计。论文还额外加了滑动窗口分支，防止压缩后丢掉近邻细粒度依赖。

这一个做法其实听高明的，因为很多长上下文方案要么偏理论，要么压缩后语义损失太大；V4 这里的思路明显更工程化： **远处的信息便宜看，近处的信息精细看，重要的块再稀疏挑出来重点看。**

这很像在做一个多级记忆系统，而不是死磕全量原始 token。

## 2）mHC：训练更加稳定

V4 另一个重要升级是 **mHC（Manifold-Constrained Hyper-Connections）** 。

这个技术就是为了解决三个问题：

- **Degradation problem：** 深层网络不是过拟合，是根本训不好
- **Residual explosion：** 残差叠加后范数不可控
- **表示空间塌缩 / 扭曲：** 深层特征不再可解释

mHC 的改进的核心点在于： **把每层的 residual mixing 矩阵 (H^{res}_l) 约束为“双随机矩阵（doubly stochastic）”** ，也就是落在 **Birkhoff polytope（双随机矩阵集合/置换矩阵凸包）** 这个流形/多面体上

![](https://pic1.zhimg.com/v2-db4b076dcd70b4b00422f8397cf1fec0_1440w.jpg)

DS研究团队通过实验观察到，由于缺乏有效约束机制，HC系统在训练过程中会出现控制参数无序波动的现象。为解决这一问题，研究团队引入了 **Birkhoff polytope** 这一特定流形结构作为优化空间。选择该流形的主要依据在于其具备多重优良特性：

- **范数不扩张（Non-expansive）** 双随机矩阵的谱范数有界，因此能抑制梯度爆炸风险
- **连乘闭包（Compositional Closure）** 双随机矩阵集合对乘法封闭：多层连乘 仍是双随机，因此“跨很多层”的直通项也保持同样的守恒/稳定属性
- **几何解释：置换的凸组合** Birkhoff polytope 是置换矩阵的凸包，所以 可视作“对多种置换混合方式的加权平均”；反复作用会带来更强的跨流混合，但仍是 **单调增强的融合** 而非失控放大

此外，mHC 还加了 **非负性约束** ，避免正负系数叠加造成信号抵消（也可理解为一种简单的流形/可行域约束）

HC 到底是“哪里不稳”？mHC 又是“怎么把它稳住的”呢？从实验上看，在前期训练过程中，HC loss 波动巨大，甚至有负值，说明训练初期 residual mixing 还没“学稳”。中后期的时候，HC 的 loss gap **稳定停在一个非零区间，** 且有明显的长期抖动。

![](https://picx.zhimg.com/v2-34bb467c466c6631eda923349eeb09a5_1440w.jpg)

而 mHC作为 reference，整体的loss 基本单调、平滑，而且没有长期偏移

在前向和后向传播上，多层连乘后，梯度链路被放大成“指数塔”。 **这就是 HC 训练不稳的根因**

不是某一层“坏”，而是 **可学习 residual mixing 的“连乘”失控导致的** 。

![](https://pic1.zhimg.com/v2-89bafcc7f23899ec1f08e14ae253b67c_1440w.jpg)

## 3）Muon：V4 里重点使用的一个 **优化器**

论文里把 **Muon** 放得很重。作用类似熟悉的 AdamW：都是拿来更新模型参数的。区别在于，论文认为 **Muon 在大模型训练里收敛更快、训练更稳** ，所以把它用在了 DeepSeek-V4 的大部分模块上。

![](https://pica.zhimg.com/v2-db67215b16c7feabb322f13851aa9a14_1440w.jpg)

它和普通 SGD / AdamW 最大的不同，会对更新矩阵做一次特殊处理，让更新方向更规整、更稳定。

论文里的核心流程大概是：

- 先算梯度
- 累积 momentum
- 对“动量 + 当前梯度”这个更新矩阵，做一次 **Hybrid Newton-Schulz** 正交化处理
- 再做缩放和权重衰减，最后更新参数。

## 4）V4 的效率提升到底有多狠

这篇论文最有冲击力的数据，是首页右边那两张效率图。

![](https://pica.zhimg.com/v2-c55178fe906f9918bcea08934e38fce6_1440w.jpg)

在 **1M token context** 下：

- **DeepSeek-V4-Pro** 的单 token 推理 FLOPs 只有 DeepSeek-V3.2 的 **27%**
- KV cache 只有 V3.2 的 **10%**
- **DeepSeek-V4-Flash** 更激进，单 token FLOPs 只有 **10%** ，KV cache 只有 **7%** 。

这个意义非常大。因为长上下文模型最大的问题在于贵，V4 这套设计的价值就在于，它试图把“百万上下文”从展示能力，变成可落地能力。这也是我觉得它比很多“长上下文号称支持 1M”模型更有说服力的地方。

## 写在最后

过去很多模型也说自己支持长上下文，但实际用起来经常是两个问题：一是太贵，二是长了以后不一定真能用。V4 这次的核心价值优化的地方在于： **它是从注意力机制、KV cache、训练稳定性和优化器上都围绕“长上下文可用”重新做了一遍工程化设计。**

这一次V4 预览版的出现，其实很强了，大家赶紧用起来，一起测试一下~