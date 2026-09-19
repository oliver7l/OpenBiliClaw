# DeepSeek Harness 发布：Everything is a Plugin

- 链接: https://zhuanlan.zhihu.com/p/2071602143807776344
- 发布: 2026-08-14 14:21:36
- 赞同: 2 | 评论: 0

---

DeepSeek Harness终于发布了，官方地址在这里： [https://www.deepseek.com/harness/](https://www.deepseek.com/harness/)

![](https://pic4.zhimg.com/v2-47fe19650185c54158c46a54ad81f89f_1440w.jpg)

其中它的Github仓库已经来到了62.9K星星数了，一经发布即开源。

![](https://pic3.zhimg.com/v2-3a13cd6c3e8ef675e60ac234f6eba79a_1440w.jpg)

安装方式也比较简单，只需要一行命令就可以安装。如果你自己嫌麻烦，也可以把这个命令发送给任意一个Agent帮你安装

成功之后可以看到挂载的网页弹出来，然后输入自己的API Key就可以了

![](https://pic1.zhimg.com/v2-b7458a3bd9bccb94f05af0e453e0bd9c_1440w.jpg)

## 模型本身亮点

**第一，Everything is a Plugin，一切皆插件。**

这是它最核心的设计。模型、Tool、Skill、Session、Sandbox、Storage、Agent Loop、调度、UI，甚至模型适配器本身，都可以作为插件存在。底层的 Cordis Kernel 只负责插件加载、卸载和依赖关系，不承载具体 Agent 能力。开发者基本可以通过配置替换任意一层，而不需要魔改核心代码。

它不像现有的codex、claude code等产品，更像是一个 **Agent Runtime / Agent OS。**

![](https://pic3.zhimg.com/v2-f30101e403ef74ce2e914615efefcf1a_1440w.jpg)

而且这个插件目前已经做成了社区的形式： [https://github.com/topics/dsh-plugin](https://github.com/topics/dsh-plugin) 。任何人都可以上传自己的插件供别人使用。

因为人做的不是Agent产品，人做的是一个基建，是Harness系统。

它需要全世界开发者进来，来一起插拔，帮他做各种各样的插件出来，丰富整个DeepSeek Harness的生态，从而形成一个新时代的平台，同时完成Agent的自进化。

所以，整个社区，才会吵得不可开交。

一边说这个理念太牛逼了太好了，另一边说这玩意鬼才用，我为什么没事要插拔。

一个新事物诞生必然有他的争议，但是无所谓，我觉得对于大家来说，最好的方式，还是上手用一用。

再来说一下什么是Cordis，它是整个DeepSeek Harness的核心所在。Cordis 让插件共享上下文 **Service、类型化 Event 和可逆 Effect** ，而 Harness 里的模型适配器、Tool Registry、Session Log、Agent Loop 本身全部都是插件。

![](https://pic3.zhimg.com/v2-ff75504fbcc1916bb1a964a67ac56246_1440w.jpg)

更为具体的，它主要解决三件事：一是不同模块可以自由替换，比如 DeepSeek 换成 GPT、Local Sandbox 换成 Remote Sandbox；二是插件之间尽量解耦，通过 Service 和 Event 协作；三是插件可以动态挂载和卸载，卸载时相关能力也能自动清理。

所以 Cordis 本质上可以看成： **插件系统 + 依赖注入 + Event Bus + 生命周期管理。**

![](https://picx.zhimg.com/v2-0326bb94b4a776606588c5521266561b_1440w.jpg)

它让 DeepSeek Harness 不再是一个写死的 Agent，变成了一套可以自由拼装 Agent 的基础设施。

**第二，多种模式可以任你选择。**

目前官方给的4种模板预设，方便普通人开箱即用

![](https://pic3.zhimg.com/v2-da73bfc4d3bbcc9a0a7f80dac0ed4a7a_1440w.jpg)

然后我简单的说一下四种模式的区别。

| 模式 | 核心特点 | 适合场景 |
| --- | --- | --- |
| 标准模式 | 完整 Coding Agent，文件编辑、Shell、搜索、Skills、计划、目标、子代理等基本都开放 | 日常写代码，默认选这个 |
| PTC 模式 | 拥有标准模式能力，但允许模型通过 Code Mode SDK 生成一段 TypeScript，把多个工具调用组合起来执行 | 复杂、多步骤、工具调用很多的任务 |
| 极简模式 | 只提供 persistent bash + str_replace_editor 两个工具 | 测试模型“裸 Agent 能力”、Benchmark、研究 |
| 创造模式 | 完整能力 + Runtime 检查、插件实验、Preset 创建等开发能力 | 开发 Harness / 插件 / 自定义 Agent |

**这里可以比较新奇的是一个PTC模式。**

官方提供的 PTC 模式，会把工具通过 Code Mode SDK 暴露给模型，然后让模型生成一段 TypeScript 程序，把多个操作组合起来执行，而不是传统的一轮模型调用只能发几个 Function Call。(深度求索)

比如传统模式可能是：

**搜索文件 → 返回结果 → 调模型 → 读文件 → 调模型 → 修改文件**

PTC 更像：

**模型先写一段程序 → 搜索 → 过滤 → 读取 → 修改 → 一次执行**

对于长程 Agent 来说，这可能明显降低模型来回调用次数和上下文开销。

**当然，它还有其他亮点：**

- **减少模型往返次数：** 不再是LLM → Tool → LLM → Tool一步一步执行，而是让模型直接生成一段 TypeScript，把多步工具调用一次性编排起来。
- **更省 Token：** 大量中间结果可以留在代码运行环境里先做筛选、聚合、过滤，只把最终结果返回模型，避免上下文被 Tool Result 撑爆。
- **天然支持并行调用：** 可以直接用Promise.all之类的方式，并行读取文件、查询 API、搜索代码，比传统串行 Tool Calling 更高效。

**第三，Trajectory 做得非常完整。**

模型看到的内容都会写入 append-only Session Log。比如我这里让它做了一个“创建一个太阳系模拟器”

![](https://pica.zhimg.com/v2-1b2e159efe5016c38056f240971f6b62_1440w.jpg)

除了对话之外，它还可以有一个“轨迹”入口。包含了System Prompt、上下文注入、Reasoning、Tool Call、Tool Result、SubAgent 调度等。

![](https://pic3.zhimg.com/v2-a2fb1b28f71974e0cdc232813a043c6a_1440w.jpg)

而且点击进度条可以聚焦到某个命令的消耗token上，很容易就知道这个Agent具体干了什么事情。

![](https://pic3.zhimg.com/v2-4a6591e1a0489afc00befb986a9c7a84_1440w.jpg)

这个设计非常适合调试 Agent。以后出现“模型为什么第 50 步突然开始乱改代码”，可以真正往前追：

**到底是哪次 Tool Result、哪段 Context，或者哪个 SubAgent 把它带偏了。** 对后训练也很有价值，因为这些 Trajectory 本身天然就是 Agent SFT / RL 数据。

**第四，Agent Loop 本身都可以换。**

这个其实挺激进的。传统 Agent 框架一般会把“模型 → Tool Call → Tool Result → 再调用模型”这一套 Loop 写死。但 DeepSeek Harness 连 Agent Loop 都属于插件，因此理论上可以实验完全不同的执行范式，比如不同的规划器、并行 Agent、层级 Agent，甚至未来专门针对某个模型训练出来的一套 Loop。

![](https://picx.zhimg.com/v2-606a580f3ad617ec27566bde5e1a4279_1440w.jpg)

这对做 Agent RL、Harness 研究的人会非常有吸引力。因为在很多 Agent 产品里，这部分属于核心逻辑，基本是固定的。但 Cordis / DeepSeek Harness 的思路是： **Agent Loop 也只是一个插件。所以任何用户都可以随机更换即可。**

## 初步体验

**Q1：六边形中弹珠碰撞**

请生成一个完整的HTML文件(将HTML、 CSS和JavaScript均合并成一个文件)来模拟一个蓝色小球在顺时针缓慢旋转的正六边形内形成一个文件）来模拟一个彩色小球在针旋转的正字形内部弹跳的动画，要求如下: - 小球应受重力影响，并在碰到边界时发生反弹-小球与多边形之间的碰撞检测要真实-所有代码应包含在<html>文件内，不要引用外部库或文件-动画要平滑，页面布局适配

![](https://pic3.zhimg.com/v2-bb89e4f1df88cecf4529fcf300c3ff20_1440w.gif)

具体的动画效果：整体效果不错， **科技感、动态感都有，视觉上很像“模型能力在不断探索边界”** 。

**Q2: 生成一个宇航员再月球上骑马的动态svg**

![](https://picx.zhimg.com/v2-cf7abf891c92724e5fbbff7814de1941_1440w.jpg)

**Q3：用 Three.js 实现一款“我的世界风格”的3D飞机大战。**

这是我用最新的DS-V4-Pro模型做出来的效果

![](https://pic1.zhimg.com/v2-a72eac680e4c087b09be8ecf2a1d5cd2_1440w.jpg)

这是用的是之前的DeepSeek-V4 模型生成的效果

![](https://pic4.zhimg.com/v2-15b068145736cb7cae3e82f8d03cba6f_1440w.jpg)

**Q4：创建一个太阳系模拟器**

![](https://pica.zhimg.com/v2-287369a73ae5ee7ffb04a1ea6befeb4e_1440w.jpg)

## 插件系统

跟DeepSeek Harness一起上线的，还有社区插件入口。有很多开发者开发的三方插件。

![](https://pic3.zhimg.com/v2-12343133d2802184afaa87a23619265a_1440w.jpg)

我简单挑了一圈，发现里面确实有几个挺实用的，装上之后能明显提升使用体验，推荐大家试试。

**1. dsh-at-file**

[https://github.com/omdsh-dev/dsh-at-file](https://github.com/omdsh-dev/dsh-at-file)

这个插件很简单，但特别实用。

安装之后，可以直接在输入框里通过 @ 引用文件，不需要再手动输入文件路径，查文件、改代码的时候会方便很多。

![](https://picx.zhimg.com/v2-dce7071c69d39480b7c0db63082c2309_1440w.jpg)

**2. dsh-genui**

[https://github.com/omdsh-dev/dsh-genui](https://github.com/omdsh-dev/dsh-genui)

允许模型在回复里直接渲染图表、表格、表单、Diff、Mermaid、交互面板之类的，很有用。

![](https://pic2.zhimg.com/v2-ce6dadea4909d7351fadbd46d16bc383_1440w.jpg)

**3. dsh-automation**

[https://github.com/titanwings/dsh-automation](https://github.com/titanwings/dsh-automation)

给DeepSeek Harness补上了自动化的能力，刚需，就是这个交互感觉做的稍微有点问题= =

![](https://pica.zhimg.com/v2-ad0fc0a2f46d87114d2cff7a73166f7c_1440w.jpg)

**4. DSH-better-sidebar**

[https://github.com/omdsh-dev/DSH-better-sidebar](https://github.com/omdsh-dev/DSH-better-sidebar)

直接给 DSH 补上了一套类似 VS Code 的工作台，文件管理、代码编辑、真实终端、Git、Diff、内嵌浏览器、后台任务和子代理状态，全都塞进了侧边栏里，能让功能更多，也更好用一点。

![](https://pic3.zhimg.com/v2-7c770c02f2e26fa4a2498fb839ee064e_1440w.jpg)

**5. ModLens**

[https://github.com/liustack/modlens](https://github.com/liustack/modlens)

给纯文本的DeepSeek模型补上视觉能力，配置好视觉通道以后，直接往对话里粘贴图片，模型就可以读图了，刚需。

![](https://pic3.zhimg.com/v2-e571b87d5407b98cab4fb2feafd03bd8_1440w.jpg)

**2. dsh-genui**

[https://github.com/omdsh-dev/dsh-genui](https://github.com/omdsh-dev/dsh-genui)

这个插件我很推荐。

它允许模型直接在回复里渲染 **图表、表格、表单、Diff、Mermaid、交互面板** 等内容，不再只是纯文本输出。

对于数据分析、代码 Review、画流程图这些场景特别有用，能明显提升 DeepSeek Harness 的交互体验。

![](https://pic2.zhimg.com/v2-ce6dadea4909d7351fadbd46d16bc383_1440w.jpg)

**3. dsh-automation**

[https://github.com/titanwings/dsh-automation](https://github.com/titanwings/dsh-automation)

这个插件直接给 DeepSeek Harness 补上了 **自动化任务** 的能力，我觉得基本算刚需。

可以让 Agent 定时执行任务，或者把一些重复性的工作自动跑起来，让 Harness 从“你问一句它干一次”，变成可以持续帮你干活。

不过目前交互体验还有一点粗糙，有些地方用起来不是特别顺手，希望后面继续优化。

![](https://pica.zhimg.com/v2-ad0fc0a2f46d87114d2cff7a73166f7c_1440w.jpg)

**4. DSH-better-sidebar**

[https://github.com/omdsh-dev/DSH-better-sidebar](https://github.com/omdsh-dev/DSH-better-sidebar)

这个插件属于装上之后，整个 DeepSeek Harness 都会更像一个真正的开发工作台。

它直接在侧边栏里补上了一套类似 VS Code 的能力，包括：

**文件管理、代码编辑、真实终端、Git、Diff、内嵌浏览器、后台任务，以及子代理运行状态。**

原本很多需要来回切窗口才能完成的操作，现在基本都可以集中在一个界面里完成。

如果你准备长期把 DeepSeek Harness 当 Coding Agent 用，这个插件我觉得非常值得装。

![](https://pic3.zhimg.com/v2-7c770c02f2e26fa4a2498fb839ee064e_1440w.jpg)

**5. ModLens**

[https://github.com/liustack/modlens](https://github.com/liustack/modlens)

这个也属于我认为的刚需插件。@＆活￡下

DeepSeek 本身还是纯文本模型，而 ModLens 可以给它补上一条 **视觉通道** 。

配置好视觉模型之后，直接把图片粘贴到对话框里，DeepSeek Harness 就可以理解图片内容。

截图分析、报错截图、UI 检查、图表理解这些场景一下就都能用了。

![](https://pic3.zhimg.com/v2-e571b87d5407b98cab4fb2feafd03bd8_1440w.jpg)

相当于： **纯文本 DeepSeek + ModLens = 给 Harness 补上了“眼睛”。**

## 写在最后

目前DSH最大贡献，应该是把 Harness 这层真正做成了一个开放的平台。

以前大家主要比模型能力，后来开始比 Agent。再往后，真正影响体验的，其实就是模型、Harness 和插件生态一起决定。

DeepSeek 这次和 Codex、Claude Code 的路子不太一样。后两者更像是把产品直接做好给你用，DeepSeek 则是先把底座搭出来，模型、Tool、Agent Loop、Session、Sandbox、UI 都可以往里面接。

所以现在社区里缺什么，就有人补什么。当然现在还很早，很多插件也比较粗糙。但如果后面这个生态真能跑起来，那 DeepSeek Harness 就不只是一个 Coding Agent 了，而更像一个 Agent 平台。

所以我觉得没必要现在就争它到底好不好用，先装上跑几个真实任务最直接。

这东西最后能不能成，关键就看两件事： **官方能不能把底座稳定住，社区能不能持续长出好用的插件**