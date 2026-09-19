# LongCat-Next 架构拆解：原生离散多模态，正在重写统一多模态模型范式

- 链接: https://zhuanlan.zhihu.com/p/2022440247321994235
- 发布: 2026-03-31 22:29:16
- 赞同: 0 | 评论: 0

---

**如何评价美团的LongCat-Next模型？**

我看下来，LongCat-Next 的整体架构可以概括成三个优点：

**原生离散多模态、单一自回归骨干、模态专属 tokenizer/detokenizer** 。

它抛弃了传统的“LLM + 视觉塔 + 语音塔 + 扩散头”那种拼接式路线，把文本、图像、音频都尽量离散成同一种 token 范式，然后统一塞进一个 next-token prediction 框架里做。官方把这套方法叫 **DiNA（Discrete Native Autoregression）** ，并明确强调这是建立在 **LongCat-Flash-Lite MoE A3B backbone** 上的统一多模态模型。

![](https://picx.zhimg.com/v2-06bd6329f89fb11c2565f50c94a3ee31_1440w.jpg)

下面我结合具体的代码，分析一下这个模型的输入和输出。

**输入侧** ：

文本直接进文本 tokenizer；图像先走视觉 tokenizer；音频先走音频 tokenizer。图像和音频都不是输出连续特征给 LLM，而是先变成 **离散 code** 。这些 code 再通过偏移量映射进统一 token 空间，与文本 token 共存。代码里可以直接看到它分别维护了visual_offset、audio_offset，并在前向时把visual_ids、audio_ids转成 embedding 后塞进同一个骨干。

![](https://pic3.zhimg.com/v2-e18391b2f9a3c4358c64fbb79bcceb28_1440w.jpg)

**中间骨干** ：

核心仍是一个 **decoder-only 自回归 Transformer/MoE** 。从公开配置看，LongCat-Next 当前开源版主干隐藏维度是 **3072** ，num_layers是 **14** ，注意力头数 **32** ，并带有 **256 个 routed experts、128 个 zero experts、top-k=12** 的 MoE 配置；

同时主干沿用了 LongCat-Flash 系列的 **MLA / LoRA-style q-kv compression** 参数形态，比如q_lora_rank=1536、kv_lora_rank=512。

![](https://pic4.zhimg.com/v2-74d5b915647ff3027c12c4a7722cc37b_1440w.jpg)

**输出侧** ：

不仅仅只有一个 lm head。文本走lm_head，图像走visual_head，音频走audio_head。但三者共享同一个主干隐藏状态，再由不同 head 预测各自的离散 code。代码里这点很明确：LongcatNextForCausalLM同时挂了lm_head、visual_head、audio_head，而生成状态机会在text / visual / audio三种模式间切换。也就是说，它表面上是“一套统一模型”，本质上是“ **共享 backbone + 分模态离散预测头** ”。

![](https://pica.zhimg.com/v2-1784b3d02715daf1d09536274465ad06_1440w.jpg)

模型架构层面上的创新有三个：

**第一，视觉架构是这篇里最核心的创新**

官方在 README 里把视觉部分拆成两层意思：

一层是 **SAE + RVQ** ，另一层是 **dNaViT** 。前者解决“图像怎么离散得足够有语义”，后者解决“视觉 token 怎么以更自然的方式接进 LLM”。

![](https://pica.zhimg.com/v2-1aec3d62f81f39ba1a05680edd4cdf9c_1440w.jpg)

具体看代码和配置，视觉侧大概是：

**图像 → VisualEncoder → VisualVQBridge → RVQ codebooks → visual ids** 。

公开代码里有LongcatNextVisualTokenizer，内部包含VisualEncoder、VisualVQBridge、VisualQuantizer；而VisualQuantizer用的是 **Residual Quantization / RQ bottleneck** 。配置里视觉量化器是 **8 个 codebook** ，每个大小 **16384** ，codebook_dim=3584，并且是shared_codebook=true。这意味着一张图压成了多级 residual token 组合。

LongCat-Next 想解决的是通过“ **Semantic-and-Aligned Encoders + RVQ** ”突破了过去离散视觉表征在理解任务上的天花板。换句话说，它不是只想把图像压缩成能生成的 token，而是想把图像压成 **既能理解、又能生成** 的 token。

**第二，音频路线和视觉一样，也是“先离散化，再并入统一 token 空间”**

音频侧并不是外挂 ASR/TTS 模块，而是也走 **audio tokenizer → audio ids → backbone → audio head → detokenizer/vocoder** 这一套。README 里把 LongCat-Next 定义为同时处理 text、vision、audio 的 native multimodal model；代码里也明确有 LongcatNextAudioTokenizer、audio_head、decode_audio_ids_and_save()。

代码里除了audio_ids，还有audio_text_ids，以及audiotext_start_token_id / audiotext_pad_token_id。也就是说它在语音生成里， **文本内容和音频 token 之间是协同建模的** ，不是完全割裂的两条支路。

**第三，理解和生成尽量收敛成同一种预测问题**

README 里有一句很核心：在 DiNA 框架下， **visual understanding 和 visual generation 被重写成同一个预测过程的两种表现** 。这和主流路线差别很大：

- 传统 VLM：图像理解靠视觉 encoder 对齐到语言空间；
- 传统 T2I：图像生成靠 diffusion / rectified flow；
- 传统语音：ASR、TTS、speech LM 往往各训各的。

LongCat-Next 想做的是把这些都往“ **统一离散 token 预测** ”上收敛。所以它的架构在 **系统层面上进行了范式统一** ：

- 模态先被词法化成离散 token；
- 主干只做一件事：自回归预测下一个 token；
- 不同模态在输入和输出两边由 tokenizer / detokenizer 编解码。

## 模型效果

![](https://pic4.zhimg.com/v2-9fd4fa6903410a4d550335a5cab2d7d7_1440w.jpg)

**LongCat-Next 的整体效果很强，而且强项非常集中** ，

**1）STEM 与 OCR 侧，LongCat-Next 基本是第一梯队，且稳定领先。**

- **MMMU-Pro：60.3** ，比 Qwen3-Omni-7B-Instruct（57.0）高，也接近 GPT-5-mini（62.7）和 Gemini 2.5 Flash-Lite-preview（64.1），说明它在综合多学科视觉推理上已经很能打。
- **MathVision：64.7** ，超过图里所有对比模型，说明它在 **数学图像理解、公式/图形推理** 上是强项。
- **OmniDocBench-EN：0.152** ，这个指标旁边有下降箭头，通常代表 **越低越好** 。LongCat-Next 最低，优于 GPT-5-mini 的 0.174、Gemini 的 0.240，说明它在 **英文文档解析/阅读/版面理解** 上表现最好。
- **CharXivRQ：60.1** ，也排第一，说明它在 **科研图表、字符密集型视觉问答** 上很强。

这意味着它在图表、文档、数学、科研图这种更难的任务更占优。

**2）视觉生成能力是它最突出的板块之一。**

- **TIIF-LONG：84.38** ，虽然不是第一，但已经很高，仅次于 Gemini 2.5 Flash-Image 的 90.8。
- **LongText-EN：93.15** ，这个非常强，超过 Gemini 2.5 Flash-Image（86.04）、FLUX.1-dev（60.7），但略低于 Qwen-Image-2507（94.3）。

整体来看，LongCat-Next 的模型效果属于当前多模态第一梯队，尤其强在 STEM、OCR 文档理解、科研图解析和长文本图像生成；它最有价值的地方不是单点刷榜，而是通用多模态能力比较均衡，且在高难业务场景上优势明显。