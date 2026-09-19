# Gemma 4 正式发布：Google 开源大模型的一次全面升级

- 链接: https://zhuanlan.zhihu.com/p/2023528332562559111
- 发布: 2026-04-03 22:32:35
- 赞同: 0 | 评论: 0

---

时隔一年，新版本的Gemma终于发布了。

官方把这一次的版本进行了全面的升级，试图把 **多模态、长上下文、函数调用、结构化输出、Agent 工作流适配、本地与边缘部署** 这些今天真正决定可用性的能力，一次性补齐。因此谷歌把它称为目前“最强的 Gemma 开放模型”，并在发布当天同步接入了 Hugging Face、vLLM、llama.cpp、MLX、Ollama、NVIDIA NIM 等一整套主流生态。

![](https://pic1.zhimg.com/v2-52016349bb08f4527f0debf223c700d4_1440w.jpg)

这一次发布的型号参数，小到只用一个手机的配置就可以跑起来。Gemma 4 这次一共放出了四个主要版本： **E2B、E4B、26B A4B 和 31B** 。其中 E2B 和 E4B 更偏边缘设备、本地部署和轻量多模态场景；31B 是更完整的高能力稠密模型；26B A4B 则采用 MoE 结构，试图在推理成本和性能之间做平衡。官方同时提供了预训练版和指令微调版，并强调 Gemma 4 支持超过 140 种语言，最长上下文窗口可到 256K。

可以把四个型号分成两类，一个是密集模型（Dense），另一个是MOE架构的模型。在密集模型上，最大参数量来到了307亿。

![](https://pica.zhimg.com/v2-db254eb9556fae7786e319c1fcbd9fa8_1440w.jpg)

而这一次的MOE架构中，反而参数量比密集模型的要少一点。

![](https://pic2.zhimg.com/v2-b2c01f442e731e2ed9fab139d782eb43_1440w.jpg)

在Arena AI权威评测榜单中，满血版Gemma 4模型以显著优势超越参数规模达其20倍的竞品模型，位列全球开源模型综合排名第三位。当前排名领先的两款模型均为中国研发成果，分别为智谱AI推出的GLM-5模型及月之暗面公司开发的Kimi K2.5模型。

![](https://pic4.zhimg.com/v2-014fe01d58b7c36ee8b0eb00f9bf9477_1440w.jpg)

在评估模型表现和模型大小上看，越接近左上角越好，因此可以看到Gemma4在开源模型上的优势。

![](https://picx.zhimg.com/v2-0e87ce62f17097332812e0f98a8f1649_1440w.jpg)

在具体的测评数据集上，效果确实有明显的提升。

**1）31B 和 26B A4B 已经进入“高质量开源主力模型”区间。**

官方模型卡里，Gemma 4 31B 在 MMLU Pro 上是 **85.2%** ，26B A4B 是 **82.6%** ；AIME 2026 分别是 **89.2%** 和 **88.3%** ；LiveCodeBench v6 分别是 **80.0%** 和 **77.1%** ；GPQA Diamond 分别是 **84.3%** 和 **82.3%** 。这些分数说明它在通用知识、数学推理、代码和科学问答上都已经很能打，不是只靠某一个单项拉分。

**2）和 Gemma 3 27B 相比，提升非常明显。**

同样看官方表，Gemma 3 27B 在 MMLU Pro 是 **67.6%** ，AIME 2026 是 **20.8%** ，LiveCodeBench v6 是 **29.1%** ，GPQA Diamond 是 **42.4%** ，而 Gemma 4 31B 分别拉到 **85.2% / 89.2% / 80.0% / 84.3%** 。

![](https://pic4.zhimg.com/v2-cc6e43bb598c529d3a1574fd99a43f95_1440w.jpg)

## 模型结构分析

## 1）整体还是 decoder-only 主干，但已经是原生多模态组合体

HF 的 Gemma 4 配置里，顶层 Gemma4Config 直接由 text_config、vision_config、audio_config 三部分组成，而且还定义了图像/音频相关的特殊 token，比如 boi/eoi、boa/eoa、image_token_id、audio_token_id、video_token_id。这说明它不仅仅是简单的把图像特征“外接一下”就结束，主要是从配置层面就按统一多模态输入来设计。

同时，HF 的模型输出里也专门保留了 image_hidden_states 和 audio_hidden_states

## 2）文本主干是“滑窗注意力 + 全注意力”混合结构

这是 Gemma 4 很值得注意的一点。

Gemma4TextConfig 里默认 sliding_window=512，如果没有手动指定 layer_types，代码会自动生成一个 **5:1 的层模式** ：大多数层是 sliding_attention，每隔 6 层插一个 full_attention，并且强制最后一层必须是 full_attention。

这意味着 Gemma 4 是典型的 **hybrid attention** 设计：

- 大部分层走局部滑窗，省显存、省算力；
- 少数层走全局注意力，用来补全长程依赖；
- 最后一层强制全局，避免最终表征过于局部化。

这个设计很像现在很多长上下文模型和高效推理模型的思路。它的目标很明确： **把长上下文能力和推理成本做平衡** 。

## 3）不同 attention 层，连 RoPE 配置都不一样

Gemma 4 不是全模型共用一套 RoPE 参数。Gemma4TextConfig 里给不同层型配置了不同的 RoPE：

- sliding_attention 用的是 default RoPE，rope_theta=10,000
- full_attention 用的是 proportional RoPE，partial_rotary_factor=0.25，rope_theta=1,000,000。

这点很有意思。我的理解是：

- **局部层** 更偏常规稳定建模；
- **全局层** 则更偏向长距离外推和更大范围的位置建模。

也就是说，Gemma 4在位置编码策略都分层做了差异化。这个设计挺工程化，也说明它不是简单从 Gemma 3 改个名字过来的。

## 写在最后

**Gemma 4 目前吧“把长上下文高效注意力、多模态统一 token 流、端侧数值稳定、Agent/工具调用适配”这几条线揉到了一起。**

它在结构上至少体现出这几个方向：

1. **长上下文效率优先** ：滑窗层为主，全局层兜底，RoPE 分层配置。
2. **多模态不是外挂** ：图像/音频 token、双向视觉注意力、独立 vision/audio tower 都是原生设计。
3. **推理部署导向很强** ：GQA、KV 共享开关、softcap、clippable linear 都是典型工程向设计。
4. **家族化扩展明显** ：dense、MoE、小模型、多模态模型共用一套较统一的抽象。