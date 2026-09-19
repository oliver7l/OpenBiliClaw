# Hy4 Preview 实测：腾讯这次把大模型重点押在了 Agent 上

- 链接: https://zhuanlan.zhihu.com/p/2076815818369798398
- 发布: 2026-08-28 23:38:14
- 赞同: 2 | 评论: 0

---

这一次，腾讯HY4确实真的上桌了，真正 **进入国内开源旗舰第一梯队了。从官方的演示视频来看，效果确实可以，特别是在Coding Agent、工具调用、长任务执行和办公生产力。**

![](https://pic2.zhimg.com/v2-072f3aca499648bb90da22829aab6a79_1440w.jpg)

## 先看模型本身，有明显的效果提升

Hy4 preview 是一个 **770B 总参数、49B 激活参数的 MoE** ，上下文直接做到 **1M Token** 。相比 Hy3 的 295B / 21B / 256K，基本上是有一个成倍数的增加。目前官方没有给出架构图，所以我用大模型画了一个

![](https://pica.zhimg.com/v2-62f3096ffb1f9e4fdc0a91b65eafad32_1440w.jpg)

这个架构也挺有意思的

- 78 层，77 层采用 MoE
- 每层 256 个 Routed Experts + 1 个 Shared Expert
- 每 Token 激活 Top-8 Expert
- **Gated DeepSeek Sparse Attention**
- **IndexCache** 跨层复用稀疏索引
- **iHC / Hyper-Connections**
- 原生一层 **MTP** ，专门服务推测解码

也就是说，它明显吸收了过去一年 DeepSeek、GLM 这条路线里已经被验证有效的东西： **大 MoE + 稀疏 Attention + Hyper-Connection + MTP。**

所以 Hy4 给我的感觉是把开源模型的优势给结合起来， **重新组合、放大，然后狠狠干了一轮预训练和后训练。**

这种路线其实挺腾讯的。

它未必一定要在某个架构创新上做到“全世界第一个”，但腾讯手里真正有优势的，是大量真实的研发、办公、游戏、安全和 Agent 场景。只要底座能力足够强，后面就可以不断拿真实任务去打磨模型。

## 模型Agent能力明显提升

官方公布的数据里，我觉得有几个结果比普通数学 benchmark 更值得关注：

![](https://pic4.zhimg.com/v2-83cf853af4c98e7b562aaccb1402256f_1440w.jpg)

尤其 **DeepSWE 从 Hy3 的 28.0 涨到 64.3** ，这个提升非常夸张。还有几个比较关键的点：

- **Terminal Bench 2.1：70.8 → 85.4** ，已经进入 GPT-5.6 Sol、GLM 5.3、Claude Opus 5 这一档。
- **SWE Atlas Refactoring：32.9 → 53.3** ，甚至超过 GPT-5.6 Sol 的 52.4、GLM 5.3 的 51.9 和 Qwen3.8 Max 的 51.0，仅次于 Claude Opus 5 的 60.0。
- **Toolathlon-Verified：56.2 → 74.1** ，与 Kimi K3 74.7、Claude 76.5 基本已经处于同一区间。
- **PostTrainBench：14.5 → 35.6** ，超过 Claude Opus 5 的 35.0，和 GPT-5.6 Sol 的 36.2 几乎贴着。
- **HorizonMath：3.5 → 8.8** ，仅次于 GPT-5.6 Sol 的 10.6，数学推理也有明显补强。

但也能看到 Hy4 还不是全面 SOTA。比如 **ProgramBench 只有 17.5** ，Claude Opus 5 达到 39.5；DeepSWE 上也明显落后 Kimi K3 和 Claude。纯文本 HLE 上 Hy4 是 43.4，而 Claude / GPT-5.6 Sol 分别达到 53.2 / 49.6。

腾讯内部还找了 163 名内部专家，对 203 个真实工程任务做盲测：

- Hy4 preview： **2.99 / 4**
- Kimi K3： **2.94 / 4**
- GLM 5.3： **2.92 / 4**

差距只有 0.05～0.07 分，而且面对 GLM 5.3，Hy4 依然有 **40.4% 的任务输掉** 。 **Hy4 已经成功进入 GLM 5.3、Kimi K3 这一档，但是还是有一定的差距。** 不过这本身已经是一个很大的变化。

在 Code Arena：WebDev 中排名 ~#5，获得 1633 分（AutoEval）。

![](https://picx.zhimg.com/v2-f96424a1d5a5c5e1ce54b6437814ce3b_1440w.jpg)

这是从 Hy3 的整体 #31 位（+115 分）的一个显著改进！在开源模型中，Hy4 预览版排名 ~#3，而 Hy3 则为 #7。

## 实际体验反而比 Benchmark 更有意思

目前比较完整的一轮提前实测是在 WorkBuddy 里做的。

一个很典型的例子是做 **Art Deco 风格网站** 。HY4有明显的先计划再做的思考在里面

**检查环境 → 做任务规划 → 开发 → 运行 → 看结果 → 找 JS Bug → 修改 → 再验证 → 最后交付。**

![](https://pic3.zhimg.com/v2-822079a5478427015e6106ff2047b22a_1440w.jpg)

实测中它自己发现并修复了高亮显示错误和 JS 崩溃，而且最终前端的视觉一致性明显比 Hy3 好。

更夸张的是有人直接让它做了一个 **低多边形 3D 开放世界游戏** 。

![](https://pic2.zhimg.com/v2-ac5026f59969fd33d0ff6205e79409b1_1440w.jpg)

模型连续跑了约 **1 小时** ，最终交付了一个约 10MB 的多文件项目，包含：

- 资源采集
- 进食
- 建造
- 水面效果
- 昼夜变化
- 晴雨雾天气
- 环境音等

而且过程中会自己操作角色、截图，然后通过视觉工具检查采集机制到底有没有生效。

![](https://pic4.zhimg.com/v2-a1f1135fcd41983a7ff47dd306286e65_1440w.jpg)

![](https://picx.zhimg.com/v2-822d3771ade0b366e3d54845168f7bcb_1440w.jpg)

施瓦西黑洞的测试：Hy4 preview表现非常不错，而且这个测评顺利完成的模型不多，svg 也能有动态效果

![](https://picx.zhimg.com/v2-fcae6d58d5b06ff4681404ace44ec5d9_1440w.jpg)

展示一个经典的3D 飞机发动机引擎Case

*使用Three.js开发一款类似JigSpace的3D交互式展示应用，主要用于展示大爆炸理论和机械机构的运作原理。核心场景为飞机发动机引擎的可拆解展示，用户能够通过鼠标拖拽、滚轮缩放等交互方式逐层拆解引擎各部件并查看内部结构，同时配有工作原理的动态演示效果。* 要求：模型设计高度还原真实引擎结构，支持部件高亮标注与信息提示；交互流畅自然，具备相机自动聚焦、爆炸视图、单部件隔离查看等功能；界面UI简洁现代，适合技术展示与教学场景。

![](https://pic4.zhimg.com/v2-91f9a2fb7cec95dcbd1edd106b94676d_1440w.jpg)

## 还有一个非常现实的问题：770B 太大了

虽然每 Token 只激活 49B，但： **MoE 的显存占用看的是 770B 权重，不是 49B 激活参数。**

BF16 权重粗略就是 **1.5TB 级别** 。即便 FP8，也接近 **0.8TB 权重规模** 。

官方已经直接发布了 FP8 版本，同时 vLLM / SGLang 官方部署方案默认给出了 **TP=8 + MTP speculative decoding** 。这会限制 Hy4 开源权重的实际普及程度。

目前 OpenRouter 给出的价格是：

**$0.834 / M 输入，$2.501 / M 输出，缓存 $0.042 / M。**

![](https://pica.zhimg.com/v2-a99d9299c9f62df97905d2cd97afdd7a_1440w.jpg)

国内公开价格则是：

**6 元 / M 输入，18 元 / M 输出，缓存命中 0.3 元 / M。**

对于一个 **770B / 49B active / 1M Context** 的旗舰模型来说，这个价格还是有点贵，对比刚刚出的glm-5.3-flash模型确实贵了一点

![](https://pic1.zhimg.com/v2-c560deb33b12707745a2cd72506aaa5a_1440w.jpg)

## 写在最后

**混元终于开始形成自己的打法** ： **不卷单轮 Chatbot 第一，而是围绕腾讯内部真实的工程、游戏、安全、金融、办公场景，把模型往 Agent 生产力上训。**

这条路线其实很适合腾讯。腾讯最不缺的就是大量真实业务场景、工程师和专家反馈。

Hy4 真正可能形成壁垒的地方，也许不是某个架构创新，主要是它背靠腾讯。 **每天有大量真实用户拿模型干活，再把这些工作流反过来用于后训练。**

**Hy4 Preview、GLM 5.3、Kimi K3、DeepSeek V4 Pro 大致处于同一梯队，在不同 Agent 任务上各有优势。**

**如果正式版能够解决过度思考、自我验证循环和执行效率问题** 。会比现在这个 Preview 更值得关注。