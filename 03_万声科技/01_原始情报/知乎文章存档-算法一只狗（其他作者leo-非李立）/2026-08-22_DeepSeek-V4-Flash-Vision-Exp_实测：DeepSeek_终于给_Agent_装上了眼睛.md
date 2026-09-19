# DeepSeek-V4-Flash-Vision-Exp 实测：DeepSeek 终于给 Agent 装上了眼睛

- 链接: https://zhuanlan.zhihu.com/p/2074612571622135305
- 发布: 2026-08-22 21:43:20
- 赞同: 1 | 评论: 0

---

**DeepSeek 给 V4-Flash 补上了 Agent 最缺的一双“眼睛”，而且从首批结果看，这双眼睛并没有明显拖累原来的推理与 Coding 能力。**

首先就是最新的这个V4-Flash-Vision-Exp在多模态 Agent上确实的比V4-Flash要好，而且某些评测集上和Opus-4.8的效果差不多。

![](https://pic4.zhimg.com/v2-e8e0e8af2289c493f715842b7ff83d21_1440w.jpg)

纯文本 Agent、推理、世界知识能力基本保持 V4-Flash 水平，而需要视觉理解的 Agent Benchmark 出现了明显提升。其中比较有意思的是和Opus 4.8 对比。主要看这几个数据集：

| 多模态 Agent | Vision-Exp | Opus 4.8 |
| --- | --- | --- |
| Agents' Last Exam | 27.3 | 25.7 |
| ZeroBench | 35.0 | 34.0 |
| ApexBench | 36.5 | 39.4 |
| Chartography | 64.3 | 65.0 |

相当于是 **2 胜 2 负** ，Chartography 甚至只有 0.7 分差距。所以官方说“多模态 Agent 能力接近 Opus-4.8”，至少从它选出来的这些 Benchmark 看，并不算特别夸张。

给我的感觉，加视觉以后，文本能力没有塌。这个其实比视觉跑分本身更值得关注。

目前其实很多VLM都存在加了视觉编码器之后导致文本能力下降的问题，因为在对其视觉token得时候，往往会牺牲LLM的能力来强制对齐。但 Vision-Exp 的数据目前看没有出现这个问题。

比如在 **DeepSWE：54.4 → 59.3。** 而 DeepSeek 官方此前 V4-Flash 的 Terminal Bench 是 82.7，Vision 版本是 **83.9** 。

至于具体使用了什么 Vision Encoder、Connector、多模态训练配方，DeepSeek **目前没有公开架构细节。**

**至于模型的价格，1M 上下文、最大 384K 输出、2500 并发，价格与 V4-Flash 完全相同。**

高峰期百万输入 Token **3 元** ，空闲期 **1.5 元** 。

![](https://picx.zhimg.com/v2-d3c5bbbfca417264ccfde17753a7c679_1440w.jpg)

而且 DeepSeek 对图片做了非常激进的 Token 控制， **每张图片最多只有 384 tokens。**

大图在进入模型前会等比例缩小到大约相当于 **800×800 总像素量** ；因此 2000×2000 和 5000×5000 的图片最终视觉 Token 消耗可能一样。单请求最多甚至可以塞 **600 张图** 。

这个设计应该是是为了让Agent能够塞入更多的图而做的操作。它瞄准的是， **Screenshot Agent。**

一条 Agent trajectory 可能要消费几十甚至上百张截图。如果每张图都是几千视觉 Token，这类 Agent 的成本会迅速爆炸。DeepSeek 直接把图片压到最高 **384 Token** ，本质是在给未来的 Computer Use / Browser Agent / Coding Agent 大规模部署铺路。

## 官方测试

V4-Flash-Vision-Exp 在各类 Agent 框架内展现出了较好的多模态适配能力，能够有效支持大家借助多样化的 Agent 工具解锁更多实用的工作场景。

**示例 1：在 Agent 框架下生成商业定制西藏自驾游 PPT**

Prompt：帮我做个西藏攻略的 ppt，藏南 + 藏北，一个月，自驾游，我是高端定制游，只做私人服务，和别的旅行团不一样，核心调性：野性、原始、探索感——不是常规打卡路线，是深入无人区级别的体验。视觉风格要大气、粗粝、有力量感，拒绝小清新和网红风。ppt 要发送给高净值客户，要足够大气、野性、高端美观且最后给到三种真实定价方案，所有配图使用真实摄影图片风格（实拍质感）

![](https://pic4.zhimg.com/v2-e0cec7f51f7051506a5e0a640f5835cb_1440w.jpg)

**示例 2：对 DeepSeek Harness 官网进行二次创作**

![](https://pic1.zhimg.com/v2-d6473dd9d513ac651ed09f3ca607eed0_1440w.jpg)

要求模型用黑蓝深海、玻璃 UI 和 ASCII 原子像素等视觉要素，经过长多轮交互后重构得到的未来主义风格开发者网站

**示例 3：制作黏土怪物风格动态特效的前端 Mini Demo**

![](https://pic3.zhimg.com/v2-da36ef4e01cd53f6914396fefb224bf6_1440w.jpg)

提示模型在「一群可爱的 3D 黏土小怪物加入一个跃动的复古舞池派对」的灵感下进行创作

## 初步体验

目前已经在最新版本的DSH上可以接入这个模型。一开始我发现就算更新了也最新版本，好像也添加不了这个模型。

![](https://pica.zhimg.com/v2-8278b9c621f546d1e1926de305a74f9e_1440w.jpg)

看官方好像确实是支持的，只能让codex帮我修复一下就出来了

![](https://picx.zhimg.com/v2-f732e399574b969982a7a7fa787482cf_1440w.jpg)

目前把你想要的图片进行复制，然后就可以在输入框粘贴一下就会出现图片了。

![](https://pica.zhimg.com/v2-52969916ca9075ed9cf93cd70be3ece6_1440w.jpg)

**Q1：识别论文中的图片**

识别效果有点强。主要是它先识别到了我这个图片是出自那一篇论文的（应该是我之前的记录让它有记忆）。

![](https://picx.zhimg.com/v2-23fa579a4585bb1471ab40ebb37f1b3b_1440w.jpg)

然后把图片里面的每一个细节都能详细的描述出来

![](https://pic4.zhimg.com/v2-d773d3b4fb63ecd56f1ac44eb361ffc5_1440w.jpg)

**Q2：设置旅游宣传html页面**

帮我设计一个 **新疆高端私人定制自驾游 HTML 单页** ，面向高净值客户，展示一条 **30 天北疆 + 南疆深度自驾线路** 。

核心定位不是常规旅行团，而是 **私人、深度、稀缺、探索型旅行** ，深入雪山、草原、峡谷、戈壁、沙漠、边境公路和普通游客很少抵达的区域。

**核心调性：野性、原始、辽阔、探索、力量感、高级感。**

拒绝小清新、网红风、传统旅行社页面，整体参考：

**National Geographic × Patagonia × Land Rover 探险大片**

![](https://pic3.zhimg.com/v2-642c7e0f7755c5d7f06ad662dc1f88f4_1440w.jpg)

![](https://pica.zhimg.com/v2-273299c20794874b04da8dd82b23817c_1440w.jpg)

**Q3：复杂街景：视觉搜索 + 空间关系**

![](https://pic1.zhimg.com/v2-3b333b748397ba3ae487d3f15b73e33a_1440w.jpg)

直接用上面的街景图。 **小目标、OCR、计数、空间关系以及幻觉控制** 。

**Prompt：**

仔细观察图片，不要做泛泛描述。

图中一共有多少辆能够明确看到的汽车？

最靠近画面中央的白色汽车前方有什么？

找出所有能够辨认出的英文招牌文字。红绿灯当前分别显示什么状态？

根据行人和车辆的位置，判断此时哪些方向的交通更可能被放行。对不确定的信息明确标注“不确定”，不要猜测。

![](https://picx.zhimg.com/v2-8e38f126311df9a19209b2e998fa9e77_1440w.jpg)

![](https://picx.zhimg.com/v2-42f00f203b2551220c151c5cf2d14549_1440w.jpg)

对于汽车的数量数得有点不太对，对于比较大得字体识别得还可以。

**Q4：图表理解**

只根据图片回答，不使用外部知识：图中最低点大约发生在哪一年？2020 年附近出现的最大单次下跌幅度大约是多少？图中历史最高点大约是多少？从最低点到历史最高点，大约上涨了百分之多少？2020 年的大幅下跌之后，大概花了多久重新回到下跌前水平？每个答案给出你的读图依据，并区分“精确读数”和“估算值”。

![](https://pic1.zhimg.com/v2-17a4f19dddbe2822b79a73acce1f1f32_1440w.jpg)

![](https://picx.zhimg.com/v2-0b637880922bdf74625a946966e03c17_1440w.jpg)

整体回答还算可以，大致能够估算出来

## **写在最后**

过去 DeepSeek Agent 有个很明显的问题， **脑子很强，但是眼睛是瞎的。** 现在 Vision-Exp 等于把这个缺口直接补上。而且还是 **V4-Flash 级推理 + 原生视觉 + Tool Call + Responses API + Anthropic API + 1M Context + 极低视觉 Token 成本。**

目前来看，通用视觉能力“待验证”，多模态 Agent 能力“第一梯队候选”，成本和工程化能力则非常有竞争力。