# Claude Fable 5.1 来了：Agent 暴涨、缓存降价，还有新的反蒸馏机制

- 链接: https://zhuanlan.zhihu.com/p/2078589622142224026
- 发布: 2026-09-02 21:06:46
- 赞同: 1 | 评论: 0

---

Claude Fable 5.1 来了：Agent 暴涨、缓存降价，还有新的反蒸馏机制

目前 **Fable 5.1 是全面 GA（正式发布），Mythos 5.1 并没有全面开放。两者其实是同一个底层模型** ，Fable 5.1 面向普通用户和企业开放；Mythos 5.1 则使用更宽松的安全策略，目前主要通过 Project Glasswing 向经过审核的网络安全和生命科学机构开放。

![](https://pic1.zhimg.com/v2-a2885cbb25051bdd8b7d6397ab65269e_1440w.jpg)

这一次发布的新模型，其实总结起来就几个优点：

- 这次Anthropic新模型主要集中在Agent任务上的提升
- cache降价了，也就是便宜了一点
- Fable / Mythos 这种“双版本模型”可能会成为一种新范式
- 自身的科研能力更强

### 第一，Agent任务的能力提升

几个数字很有意思，新模型的能力基本把GPT-5.6吊打了。

![](https://pic3.zhimg.com/v2-beb506acb348df4452fc658b7f984428_1440w.jpg)

其中 Mythos 5.1 在 Terminal-Bench 4.0 甚至达到 **60.9%** 。具体来说， **Terminal-Bench 4.0** 不是普通 Coding Benchmark 那种“给你一道题，输出一段代码”。它就是给 Agent 一个 **真实的沙箱环境 + Terminal** ，让它自己执行命令、读文件、改代码、装依赖、运行测试、定位报错，直到最终环境达到要求。官方最后会直接运行验证脚本检查机器的最终状态，而不是看模型回答得像不像。

所以它会比普通的任务更加难，以前的模型也就最高能够达到20%的效果。而Mythos 5.1 直接突破到55.8%，确实很强了。

也就是说Terminal、科研 Agent、Automation 这种需要持续调用工具、排查错误、验证结果的任务涨幅远高于传统知识推理 Benchmark。

**目前单轮 Chatbot 已经不是主战场，真正拉开差距的是 Agent 能不能连续工作几小时甚至几十小时。**

Anthropic 给出的早期案例里甚至出现了 38 小时无人值守的机器学习实验：模型自己发现原实验存在标签问题、修正后启动 6 个并行实验，再给出最终结果。当然这些属于厂商/早期客户案例，还不能等同于独立评测。

### 第二，这次真正的大招可能反而是 Cache 降价

Fable 5.1 的普通 API 价格 **没有降** ： **输入 $10 / M Token，输出 $50 / M Token。**

甚至仍然明显比 Opus 5 的 $5 / $25 贵。

但它把 **Cache Read 从 $1 直接降到了 $0.25 / M Token，下降 75%** 。Anthropic 称典型任务总成本能下降约 25%，复杂 Agent 任务最高可以下降约 **45%** 。

![](https://pic3.zhimg.com/v2-5bdabbf8d8d2a24d2f2bc30a44e32e3a_1440w.jpg)

如果你目前在研究一些比较难得问题，需要用到fable得话，用它最新得这个5.1模型确实会便宜一点。

### 第三，Fable / Mythos 这种“双版本模型”可能会成为一种新范式

这点其实挺有意思。Anthropic 现在把最新的模型都包装成两个不同的版本。 **同一个模型能力 → 根据用户身份和应用领域挂不同 Safeguard。**

普通用户使用 Fable 5.1；经过审核的科研机构、安全机构，可以使用限制更少的 Mythos 5.1。而这直接反映到了 Benchmark 上：

Terminal-Bench 4.0：

**Fable 5.1：55.8%**

**Mythos 5.1：60.9%**

![](https://pic2.zhimg.com/v2-6b602d9c9e318c178581909290641fad_1440w.jpg)

底层模型完全一样，差的这 5.1 个百分点，很大程度就是安全策略造成的。所以有了安全的限制之后， **未来前沿模型的“能力”不一定等于普通用户实际能使用到的“能力”。**

但同时，还有一个新的“反蒸馏机制”上线。它的核心其实是 **Preserved Thinking（保留式 Thinking）** 。

Anthropic 现在会把 Claude 产生的 thinking block 和它当时对应的 **system prompt、tools、历史 messages** 绑定起来。后续请求如果你修改了这些上下文，再把原 thinking block 塞回去，API 会校验不一致，并把这段 thinking 丢掉，不再让模型看到。Anthropic 明确说，这么做就是为了防止通过“修改上下文 → 诱导 Claude 解密/复述 thinking → 批量收集 CoT”来做非法蒸馏。

![](https://pic4.zhimg.com/v2-1cf5a9329dca0ccca3e2d2ac9876c393_1440w.jpg)

可以简单理解成：

**以前：**

Prompt A → Claude Thinking → 修改 Prompt A → 套出 Thinking → 批量训练学生模型

**现在：**

Prompt A + Tools A + History A ↔ Thinking A

只要后续变成Prompt B / Tools B / History B， **Thinking A 就失效** 。

### 第四，科研能力可能比 Coding Benchmark 更值得关注

这次 Anthropic 特别强调了 Scientific Agent。

例如 Mythos 5.1 被拿去做蛋白质 binder 设计，在 12 个靶点上的有效命中率接近 **50%** ，而 Anthropic 给出的行业典型水平是约 10%–15%；在三个靶点上，其设计的结合亲和力达到相关蛋白设计竞赛最佳方案的约 **10 倍** 。这些设计还进行了外部实验验证。

![](https://pic4.zhimg.com/v2-f6743827b29eaec828afe05b18cdfa25_1440w.jpg)

另外还有一个我觉得很典型的例子：模型自己写 CUDA Kernel、缓存中间结果，把 7 个开源蛋白质/基因组深度学习模型加速最高 **2.5 倍** ，Anthropic 估算一些全基因组分析的 GPU 成本可以下降 **30%–60%** 。

在天文学领域，Fable 5.1基于美国国家航空航天局（NASA）麦哲伦号探测器于三十余年前获取的雷达影像训练了神经网络模型，为金星表面约三分之一的区域生成了新型高分辨率地形图。既往地形图仅覆盖金星表面约五分之一的面积，其空间分辨率介于10至20公里之间。Fable 5.1将分辨率提升至2至3公里，高程精度较既往提高25%。该地图已依据知识共享（CC）许可协议开源发布，以供即将实施的NASA VERITAS与ESA EnVision任务参考，从而辅助明确未来观测的重点地质特征。

### 我的总体判断

如果只看模型分数，Fable 5.1 当然算一次不错的升级，但还没有出现那种“传统 Benchmark 全面代际领先”的感觉。

**Fable 5 → 证明超强长程 Agent 可以做出来。**

**Fable 5.1 → 开始解决这种 Agent 能不能真正大规模用起来的问题。**

所以这次 Anthropic 同时解决了四件事情：

**更长程的 Agent 能力 + 更低的 Agent 实际成本 + 更少的安全误杀 + 针对科研/安全领域的权限分级。**

这也是为什么我觉得， **Fable 5.1 最应该拿来对比的可能不是 GPT-5.6 Sol 的单轮推理，而是“让 Codex / Claude Code / Devin 连续跑几个小时之后，谁能以更低的成本真正把任务做完”。**

如果这个趋势继续下去，下一阶段大模型的“斩杀线”可能也会发生变化：

**不是 Benchmark 能不能到 60 分，而是能不能在无人值守情况下，连续工作 10 小时以后依然不跑偏。**

这可能才是 Fable 5.1 最重要的信号。