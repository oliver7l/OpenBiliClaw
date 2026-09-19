# Claude Opus 4.8 发布：补上 4.7 的短板，押注 Agent 工作流

- 链接: https://zhuanlan.zhihu.com/p/2043662085779894941
- 发布: 2026-05-29 11:57:09
- 赞同: 1 | 评论: 0

---

Opus 4.7 是 **2026 年 4 月 16 日** 发布，Opus 4.8 是 **2026 年 5 月 28 日** 发布，中间大约 6 周。说实话，这种小版本更新速度这么快，只能说明Anthropic确实有点着急。最近GPT和Codex更新的速度有点快，抢了Anthropic很多的用户。而且大家发现Opus 4.7效果有点拉，所以Anthropic只能快速迭代一个新的版本。

![](https://pic4.zhimg.com/v2-e70593dc3dc239651b621bda743bf6a9_1440w.jpg)

Anthropic 官方其实也说是了“modest but tangible improvement”，也就是这次的版本更新幅度不大，主要是为了把Opus4.7版本的一些缺点优化一下。

### 整体模型主要提升点

新模型超过GPT-5.5，终于重回第一的宝座。

![](https://picx.zhimg.com/v2-03923e6a7531c69543c2a1bb844649f1_1440w.jpg)

整体提升的话，肯定会比上一个版本强。在好久个数据集上，都有小幅度的提升。

![](https://pic1.zhimg.com/v2-6e43bf1737a85c2e2ba0ba2148e15096_1440w.jpg)

**1）Agentic coding 更强**

官方对外披露的 benchmark 里，Agentic coding 从 **64.3% 提升到 69.2%** 。这说明它更擅长在代码任务里规划、调用工具、修改、验证等任务的构建。

**2）长任务协作更稳**

Anthropic 强调 Opus 4.8 在 agentic task 里判断力更好：会问更关键的问题。它在官方客户反馈里也提到，它在 CursorBench 上超过旧版 Opus，工具调用更高效，用更少步骤完成同等任务。

![](https://pic4.zhimg.com/v2-fd3c5ecfa8362b5d6300a6fca5189fcd_1440w.jpg)

**3）更诚实，少硬编**

这是 4.8 这次最被强调的变化。Opus 4.8 更容易标记不确定性，不会做一些没有依据的断言；在代码评估里，它的偏离预期的行为概率约为前代的四分之一。

![](https://pic3.zhimg.com/v2-fc8c1486246efb67d7b9a9a46d581d0a_1440w.jpg)

换句话说，4.7 更像“很强但有时太自信”，4.8 更像“强，同时更愿意承认哪里没把握”。

但是它还是在一个数据集上没有超过GPT-5.5，也就是Terminal-Bench 2.1。该Agentic基准旨在评估Agent在真实命令行环境中的操作能力。其核心方法是将模型部署于沙盒终端内，使其自主执行文件查询、命令输入、错误分析及调试等操作，以检验其能否通过多个步骤完整完成指定任务。

## 其他的一些亮点

![](https://pic1.zhimg.com/v2-a4eff91a4007a86c28b82e5b13ad6cc2_1440w.jpg)

**Dynamic workflows：Claude Code 更像多 Agent 执行器**

这次伴随 Opus 4.8 一起推出的 Dynamic workflows 很值得关注。它允许 Claude Code 规划任务，并在单个 session 里运行数百个并行 subagents，最后再验证结果后汇报。官方举例是代码库级迁移，覆盖数十万行代码，从启动到 merge，以测试集作为验收标准。

![](https://pic1.zhimg.com/v2-cdc94c95858993ff3c7955fd01a7cfb6_1440w.jpg)

这其实就是把 Claude Code 往“工程级多 Agent 编排”方向推了一步。对大规模重构、批量修复、跨模块迁移，这比单 Agent 顺序执行更有想象力。

目前这个新的功能已在 Claude Code CLI、桌面版和 VS Code 扩展程序上了。

**Effort control 更清晰**

Opus 4.8 默认是 high effort。官方说在 coding task 上，这个档位消耗的 token 大致接近 Opus 4.7 默认档，但性能更好；难任务和长时间异步工作流建议用 extra / xhigh。(Anthropic)

这对于重度用户来说，用户不仅需要选择模型，还需选择思考强度。针对简单任务，可配置较低的思考强度；而对于重构任务、Agent任务或长时间任务，则建议采用较高的思考强度。此外，成本和质量均可实现更精细的调控。

**价格基本不涨，Fast mode 变便宜**

常规价格保持和 Opus 4.7 一样： **输入 $5 / 百万 token，输出 $25 / 百万 token** 。Fast mode 价格是 **输入 $10 / 百万 token，输出 $50 / 百万 token** ，官方称相比此前 fast mode 便宜了三倍，并且速度可到 2.5 倍。

## 为什么这一次这么快发布了Opus 4.8？

![](https://pic3.zhimg.com/v2-def5bf9097e9a9cb5dcaddc4a50a83c8_1440w.jpg)

**4.8 很明显是在补 4.7 的真实使用问题。**

官方和早期客户反馈里反复提到几个关键词：更少 unsupported claims、更会标记不确定性、更少让自己写的代码缺陷蒙混过关、工具调用更高效、修复 Opus 4.7 的 comment-verbosity 和 tool-calling 问题。

这说明 4.8 很可能不是“重新造一个模型”，而是基于 4.7 线上反馈、企业 eval、Claude Code 场景做了一轮对齐、后训练和推理策略优化。

**Claude Code / Agent 场景需要更快迭代。**

这次 4.8 同步推出 Dynamic workflows：Claude Code 可以规划任务，并在单个 session 里跑数百个并行 subagents，再验证输出后汇报。官方举例是代码库级迁移，覆盖数十万行代码。

这类功能不是普通聊天模型能直接撑住的，需要模型在长任务、工具调用、自检、协作判断上更稳定。4.8 的发布节奏，本质上是在配合 Claude Code 从“代码助手”升级成“工程 Agent 执行器”。