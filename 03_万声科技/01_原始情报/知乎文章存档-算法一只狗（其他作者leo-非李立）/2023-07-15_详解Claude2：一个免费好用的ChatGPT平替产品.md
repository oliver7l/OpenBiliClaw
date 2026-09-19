# 详解Claude2：一个免费好用的ChatGPT平替产品

- 链接: https://zhuanlan.zhihu.com/p/643809759
- 发布: 2023-07-15 21:42:42
- 赞同: 6 | 评论: 0

---

> **公众号：算法一只狗**

还记得上星期OpenAI终于公布了GPT4 API接口和Code Interpreter插件，这一波的开放让大量用户体会到AI最新模型的强大能力。详情可以回看我之前写的文章：

[算法一只狗：GPT4开放API和Code Interpreter！如何利用它们来提升你的工作效率](https://zhuanlan.zhihu.com/p/642481981)

看到AI大模型大有OpenAI统一天下的趋势，各大厂商终于还是坐不住了。这不， **Anthropic推出了ChatGPT的最强平替产品——Claude2。** 而且最重要的是该软件免费申请，只要有账号就可以直接在国内使用。

![](https://pic1.zhimg.com/v2-2e58794d3e3e2f2c521cc9686009545a_1440w.gif)

Claude2和ChatGPT一样，也是一个大语言模型，对比于ChatGPT来说，有以下几个主要的优点：

- **免费注册使用（这里需要魔法上网）**
- Claude2支持100K 上下文，从价格上来看 **比GPT4-4K便宜了4-5倍**
- 可以直接导入文档进行总结。目前ChatGPT只能解析网页内容，使用插件应用才能支持文档导入总结。 **而Claude2本身就已经支持多个文档的导入，并可以概括文档之间的关系**
- 知识库对比ChatGPT更新， **知识截止时间是2023年初**
- 支持更长的上下文输入， **目前开放了10万tokens进行输入。**

当然也可以利用API接口调用其服务，从官网价格来看Claude2每1百万tokens仅需要花费$11：

![](https://pic3.zhimg.com/v2-b31e0516998406f2818d4a432853d856_1440w.jpg)

## 申请注册

目前要用到Claude2，可以登陆官网直接注册。最好用到美国IP进行注册就可以了，我这里用了谷歌浏览器的插件FreeVPN进行申请就可以了。

![](https://picx.zhimg.com/v2-951be88c33b54a34d8e81324b9f795bf_1440w.jpg)

## 1.Claude2优化了哪些方面的能力

### 1.1 多语言和实用性能力提升

对比于1代Claude模型来说，Claude2主要提升在：

- 提升了模型的有用性和诚实度
- 对于无害性上，与1.3版本的效果类似

![](https://pic3.zhimg.com/v2-39d313579d55284657aed2126a20c068_1440w.jpg)

- 同时，在多语言能力的处理上，使用Claude2将每个Flores 200数据集中的句子从英语翻译成目标语言，然后进行评估。实验中发现最新模型针对小语种Kannada、Lao等语言上有大幅度提升。

![](https://pic3.zhimg.com/v2-1432c8d2ff2d9bf7bee56d864ad353b4_1440w.jpg)

- 对于 **GRE考试** 来说，不同部分得分也有相应能力提升：

![](https://pic2.zhimg.com/v2-fee57fb6740ad0d26ffb2e8c8f8c014b_1440w.jpg)

### 1.2 更长的上下文处理

Claude在一代的时候，处理上下文已经达到了9K，而2代更是扩展到了200K个tokens，也就是相当于可以一次性处理15万个词语。目前在开放的对话中，暂时支持100K的tokens，以后会逐步开放

![](https://pic4.zhimg.com/v2-203f25499cf9af200721027eff2e175f_1440w.jpg)

## 2.有趣的使用技巧

### 2.1 文档对话能力

我们知道，目前有一众收费的ChatPDF等文档问答网站，这些网站或者插件都是基于ChatGPT的功能去做的。

而Claude2直接可以说秒杀这些大部分的网站插件。它本身就支持文档对话，而且原生就支持100K的上下文进行输入。

可以看到官网的上就可以直接上传文档进行对话了：

![](https://pic4.zhimg.com/v2-fcba443f16920933f4e904eda382721f_1440w.jpg)

而且最大可以上传5个文件，每个文件最大可以10MB.

比如我这里把Claude2的技术文档上传上去，让它进行总结。它能够在几秒内把PDF进行总结：

![](https://pic1.zhimg.com/v2-e8a4235acb78a5445d4fa43b1df382b0_1440w.jpg)

同时也可以不断询问文档中的细节，这个能力和ChatPDF相当：

![](https://pic2.zhimg.com/v2-84586cf960b1a74c783b216fa5a061b1_1440w.jpg)

当然，我们也能够利用它的能力，进行excel数据分析：

![](https://pic1.zhimg.com/v2-6835b2ac92499722a15cbf3eb9e25f80_1440w.jpg)

### 2.2 文档联系功能

而且，只要我们上传更多的文档，就可以让它总结其中之间的联系，省去了我们看多个文档写总结的时间：

![](https://pic1.zhimg.com/v2-fefc35cd7e0eaa84860fe2f2798d0b96_1440w.jpg)

## 3.Anthropic公司趣闻

说起这家Anthropic公司，其目标是成为一家研究人工智能安全和有益发展的公司，且由Dario Amodei和Daniela Amodei兄妹两于2021年创立。而且Dario Amodei曾在Open AI担任研究副总裁，领导了GPT-2和GPT-3等重要项目的开发。

![](https://pic1.zhimg.com/v2-8856fd443ada4e054c8bda2e464a27b0_1440w.jpg)

所有说这家公司和OpenAI还是有一定的渊源的。只是因为后来，由于微软对OpenAI的投资，使其变成了专属于微软的CloseAI，Dario对其心存不满，因此就自立门户，创建了这家公司。

后续谷歌也对Anthropic公司进行了投资，因此这两家公司的竞争，也成为AI界的一大亮点。

好了，以上就是本期的所有内容了，我是leo，我们下期再见~

**