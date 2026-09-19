# NLP范式新变化：Prompt

- 链接: https://zhuanlan.zhihu.com/p/548467223
- 发布: 2022-08-01 10:39:53
- 赞同: 6 | 评论: 0

---

## 1.Prompt简介

最近，NLP上又开发出了一种新的范式：Prompt。它通过定义模板来提醒下游任务模型学习的特定目标，在更少的更新参数场景下达到了和fine-tuning方法一样的效果。 具体可以看一下这篇综述文章： **《Pre-train, Prompt, and Predict: A Systematic Survey of Prompting Methods in Natural Language Processing》**

![](https://pic3.zhimg.com/v2-3acbdea6180a271ad255b71339a310a6_1440w.jpg)

简单的来说，不同于fine-tuning方法，prompt范式需要给出一个定义好的模板，这个模板可以是离散的或者是连续的，来提醒模型在预训练的时候学习的知识。这是因为预训练的任务和下游任务往往差别较大，模型可能会存在特定性遗忘。

为了使用这些模型执行预测任务，使用未填充的文本字符串prompt $x'$ ，将原始输入 $x$ 进行修改。然后使用语言模型填充文本信息来获取最终字符串 $\tilde{x}$ 。 **它允许对语言模型进行大量原始文本的预训练，并通过定义新的模板函数，使得模型能够进行few-shot和zero-shot学习，以适应几乎没有或没有标记数据的场景。**

![](https://pic2.zhimg.com/v2-fe2147d09e4db79b11b72f0971e8dae1_1440w.jpg)

从上图可以看出，prompt方法本质上就是定义了 **不同的手工模板** ：“JDK is developed by **”，“This is a super long text. TLjDR: ”, “Birds can** ”。就可以使得预训练模型适应不用的任务场景。

## 2.NLP中的两次重大改变

一直以来，监督学习（supervised learning）使用在很多机器学习任务上，这其中也包括NLP任务。由于传统的机器学习模型，不能够很好的对特征进行提取，因此在NLP任务上往往需要 **特征工程（feature engineering）** 进行辅助。但神经网络的出现改变了这一现状，使得原始特征可以与模型本身的训练一起学习，因此研究重点转移到神经网络结构工程（architecture engineering）上。 **在结构工程中，通过设计有助于学习输入特征的合适网络架构来提供归纳偏差。**

| 年份 | NLP模型范式变化 |
| --- | --- |
| 2017年以前 | 传统机器学习模型、神经网络 |
| 2017-2019 | 预训练 + 微调（pre-train + fine-tune） |
| 2019-至今 | ”预训练，prompt和预测“（pre-train，prompt and predict）范式 |

- **NLP模型的第一次重大变化**

从2017-2019年开始，NLP模型引来了第一次重大的变化。从以前的监督学习，转变为 **“预训练 + 微调（pre-train + fine-tune）”** 范式。 **在这种范式下，模型提前预训练好一个language model(LM)，然后在下游任务中对文本数据进行微调预测。** 由于训练LM模型所需的原始文本数据非常丰富，因此可以在大型数据集上训练这些模型，在这个过程中，可以通过LM模型学习到文本之间的通用特征。而在下游任务中，则需要引入额外的参数进行fine-tuning，以此来适应特定的任务。目前这种范式称为NLP界的主流范式，它可以在不同任务上提升模型的效果。

- **NLP模型的第二次重大变化**

NLP范式从预训练+微调，已经变成了 **”预训练，prompt和预测“** （pre-train，prompt and predict）范式。在这种范式中，不是通过目标工程将预先训练的LM来适应下游任务，而是重新制定下游任务，使其看起来更像在文本提示的帮助下利用原始的LM模型接近特定任务。

**下面举几个例子：**

- 比如，但我们识别句子的情感时，”I missed the bus today.“，我们在后面接上手工模板"I felt so ___"。这样就可以让预训练好的LM模型填充情感词语。
- 又或者，可以制定下面模板”English：I missed the bus today。French：____“。让LM模型填充词语作为法语翻译。

通过这种方法，可以选择合适的prompts，让LM模型与预测对应的输出结果，不需要额外的特定任务训练。该方法的优点是，在给定一组适当prompts的情况下，以完全无监督方式训练的单个LM可以用于解决大量任务。然而，它还是会存在一定局限性，这种方法引入了即时工程的必要性，需要找到最合适的提示，让LM去解决任务。

下面这个图对比了NLP中的目前有的范式：

![](https://picx.zhimg.com/v2-00d01360e0303a63b2d4227ede4b053d_1440w.jpg)

- **传统的监督学习（不需要神经网络）**
- **神经网络-监督学习：不同NLP任务需要单独训练**
- **pre-train + fine-tune：目前流行的范式，可以适应不同的场景任务**
- **pre-train + prompt + predict：模板prompt范式，可以适应不同的场景任务**

## 3.Prompt的标注描述

让我们先来看一下，以前NLP中监督学习的流程。

我们有输入$x$ ，基于模型 $P(y|x;\theta)$ 得到预测值 $y$ 。由于要学习模型参数 $\theta$ ，因此在给定输入和输出数据下，训练模型来更新参数。例如：

- 在情感分类任务上，输入句子 $x=$ "I love this movie"，输出 $y$ 为 $+1,-1$
- 在机器翻译任务上，输入句子为Finnish $x=$ ”Hyv ̈a ̈a huomenta.“，输出句子为English $y=$ "Good morning"

接下来介绍具体的prompt流程。

### 3.1 添加Prompt

![](https://pic2.zhimg.com/v2-47a58581e341fadf4cebe841c76a1f41_1440w.jpg)

如上图所示，函数 $f_{prompt}(\centerdot)$ 应用在输入句子 $x$ 中得到Prompt $x'=f_{prompt}(\centerdot)$ 。这个函数包含以下两个步骤：

- 使用模板，这个模板有两个填充位，包括输入填充[x]，和对应的回应填充[z]。其中[x]表示为输入句子，[z]表示模型预测位，这在之后需要映射到 $y$ 标签中
- 把输入句子 $x$ 填充到[x]

在上面例子中， $x=$ "I love this movie"，模板为"[x] Overall, it was a [z] movie."。应用模板后会得到 $x'=$ “I love this movie. Overall, it was a [z] movie.”。当然，这种模板不一定是指固定某些词语，也可以是一些连续性的embedding空间。如果[z]在模板中间，则称为 *cloze prompt* ，如果[z]在模板最后，则称为 *prefix prompt* 。

### 3.2 搜索和映射[z]

我们可以首先定义 $Z$ 为所有 $z$ 值的集合。对于分类任务来说， $Z$ 可以是{excellent, good, OK, bad, horrible}。然后可以利用预训练的LM模型预测最合适的词语。

最后，从得到的最高分数的词语中，映射到 $y$ 值，就可以完成整体Prompt的流程。

下图贴了具体的Prompt在不同场景上，常用的手工模板，读者可以针对不同任务进行选择：

![](https://pic1.zhimg.com/v2-8932f1b32ffe0a2d59dcf607671bfefe_1440w.jpg)

## 4.后续

Prompt中，最主要的一般来说是设计模板，因此目前主流门派分为：离散型prompt和连续型prompt。在后续的文章中，主要介绍自己读到的连续型prompt论文。