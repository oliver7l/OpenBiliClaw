# GPT-6 Astra 发布：AGI 时代真的来了吗？OpenAI总裁都直接说GPT-6进入 AGI 时代了。这个是不是

- 链接: https://zhuanlan.zhihu.com/p/2079347765595870063
- 发布: 2026-09-04 23:19:27
- 赞同: 0 | 评论: 0

---

GPT-6 Astra 发布：AGI 时代真的来了吗？

OpenAI总裁都直接说GPT-6进入 AGI 时代了。这个是不是有点太夸张。他在发布会上表示，自己认为“我们已经处于 AGI 时代”是一个合理判断，未来回看时，Astra 可能会被视为那个分界点；但是如果回看官方发布的模型效果来看，目前没有证据说它具有AGI的特征。

![](https://pic3.zhimg.com/v2-7ef48d086b7b55bcecb075bf889eebda_1440w.jpg)

## 为什么 Astra 这次确实不太一样？

最新的Astra 在 Agents' Last Exam 上达到 **59.3%** ，GPT-5.6 Sol 是 53.6%；OSWorld 2.0 达到 **72.6%** ；更明显的是 AutomationBench，从 Sol 的 **18.1% 直接到了 41.4%** 。这说明提升最大的地方其实是：跨软件、长流程、真实工作任务。

![](https://pic3.zhimg.com/v2-0ca84220a6762200313f9423c0557758_1440w.jpg)

这三个 Benchmark 可以理解成在测 **三个不同层级的 Agent 能力。本质就是为了衡量模型在控制电脑、专业工作上的能力。**

![](https://pic3.zhimg.com/v2-031b93df8033db014a3d256d5a39fc9e_1440w.jpg)

任务通常横跨多个系统，比如一个很典型的任务可能是：

找到所有符合某些条件的客户 → 查看 CRM 信息 → 根据公司规则筛选 → 发邮件 → 创建 Calendar 跟进 → 更新 CRM 状态 → 通知 Slack 里的销售负责人。

而 Agent 不会直接拿到所有 API 怎么调用。它还需要自己发现API，然后判定哪些是可以用的，然后阅读业务规则之后，正确找到数据，调用不同的系统进行输出。

## 第二个非常夸张的地方，是 ARC-AGI-3

OpenAI 官方公布 Astra 在 ARC-AGI-3 上达到 **99.9%** ，而 GPT-5.6 Sol 只有 **7.8%** ；同时称 Astra 在 96% 的关卡上超过了人类 action-efficiency baseline。

![](https://pic1.zhimg.com/v2-56377fd8c1342955a10935f2ff83c2d0_1440w.jpg)

ARC-AGI-3 可以理解成： **不给说明书的陌生小游戏，让 AI 一边玩、一边猜规则、一边建立世界模型，最后自己找到通关方法。**

它和 ARC-AGI-1/2 最大区别是：以前主要是“看几个网格例子 → 推断变换规则 → 输出答案”，ARC-AGI-3 则变成了 **真正可交互的动态环境** 。官方把它称为首个 interactive reasoning benchmark。

![](https://picx.zhimg.com/v2-a6822ea39feb973622a3f2f36e88ae21_1440w.jpg)

它会把模型放到一个从来没有见过的环境下，然后让它通过少量的交互信息去通关游戏。

这个跨度甚至不像正常的“下一代模型升级”。但这里我会保持一点谨慎。因为 OpenAI 自己也明确注明，不同模型运行配置并不完全一致。 **Astra 本身的世界建模能力暴涨了；同时长期记忆 / Harness 对 Agent 能力的影响也已经非常巨大。**

## 第三个信号，我觉得甚至比 ARC-AGI 更重要：Cyber 已经进入 Critical

Astra 是 OpenAI 第一个达到 Preparedness Framework **Critical cybersecurity capability** 的模型。

OpenAI 对这个等级的描述已经非常激进：在具备合适工具和权限的情况下，它能够发现此前未知的安全漏洞，并针对防护良好的系统设计新的利用方法，且不需要人类一步步指导。

![](https://pic2.zhimg.com/v2-7cd7c4219d665004e1eea53129e595e7_1440w.jpg)

按照 OpenAI Preparedness Framework，只要达到下面两个条件之一，就会被判定为 Cyber Critical：

- **无需人工介入，在大量经过强化防护的真实关键系统中，发现此前未知的漏洞，并开发可工作的 zero-day exploit。**
- 用户只给一个高层目标，模型就能 **自主设计并执行端到端的新型攻击策略** ，对 hardened target 发起攻击。

可以把 OpenAI 的 **Critical cybersecurity capability** 理解成：

**Astra 已经不只是“会写安全代码、解释漏洞”，而是开始具备“自主发现漏洞 → 构造利用 → 连续推进攻击链”的能力。**

## 还有一个现实问题，新模型还是贵

Astra API 定价是： **输入 $10 / M Token，输出 $50 / M Token** 。

而 GPT-5.6 Sol 是 $4 / $20，相当于 Astra Token 单价大约 **2.5 倍** 。上下文依然是约 **1.05M** 。

![](https://pic2.zhimg.com/v2-f9bda75c8b6ad862c87dafc967cc1339_1440w.jpg)

而且在artificialanalysis上，它的价格还是不低，不过好在比Opus的模型要便宜一点吧

![](https://pic2.zhimg.com/v2-f8d362520ced6efc3aba687249c20eb7_1440w.jpg)

## 写在最后

我觉得 OpenAI 直接说“进入 AGI 时代”还是有点激进。Astra 还远没到“什么都能做”的程度，Agents' Last Exam 只有 59.3%，AutomationBench 也只有 41.4%，距离稳定替代大量专业工作还有明显差距。

它能操作电脑、跨软件执行长流程、在陌生环境中探索规则、调用工具，甚至已经具备很强的网络安全 Agent 能力。

所以与其说， **AGI 已经实现了。** 不如说， **AI 正在从 Chatbot 真正进入 Agent 时代。**

未来回头看，AGI 的分界点可能不是某个模型突然拥有“超级智能”，而是从某一天开始，越来越多原本需要人坐在电脑前完成的工作，可以直接交给 AI。

从这个角度看，Astra 确实有可能成为那个分界点。