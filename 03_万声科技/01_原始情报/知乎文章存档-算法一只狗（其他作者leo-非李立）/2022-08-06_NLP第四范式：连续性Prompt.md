# NLP第四范式：连续性Prompt

- 链接: https://zhuanlan.zhihu.com/p/550705038
- 发布: 2022-08-06 21:59:48
- 赞同: 10 | 评论: 0

---

前面一篇文章主要讲到NLP领域上的四种演变范式，同时引入了第四范式“Prompt”的概念。具体可以会看这里：

[withYou：NLP范式新变化：Prompt](https://zhuanlan.zhihu.com/p/548467223)

Prompt不仅仅使用特定的模板构建输入，同时也能够使用连续性Prompt进行输入。下面介绍两篇连续性领域的Prompt论文。

## 《GPT Understands, Too》

![](https://pic2.zhimg.com/v2-f5b206f33818764cf11a66ff157a2c97_1440w.jpg)

### 1.摘要

在自然语言理解（NLU）任务上，GPT模型一般来说效果不怎么好。但本文使用了新提出的 **P-tuning** 方法后，在GPT模型效果能够媲美BERT模型效果。同时也发现，P-tuning也能够在few-shot上提升BERT模型的效果。

### 2.Introduction

PLM预训练模型有三种不同的类别：

（1）无方向语言模型：GPT

（2）双向语言模型：BERT

（3）混合语言模型：XLNET

在很长一段时间，研究者观察到GPT模型相比其他两种类别模型，在自然语言理解（NLU）任务上表现比较差，因此不太适合进行fine-tuning任务。一些研究者在GPT-3模型上使用 **Prompt方法** ，few-shot和zero-shot任务都得到了巨大的提升。

但是传统的Prompt方法有很明显的缺点：

- 需要 **人工制定Prompt模板** ，因此需要大量的验证集数据进行验证。
- 近期的Prompt方法聚焦在 **自动寻找离散prompts** （discrete prompts），但是神经网络是内在连续型的，离散的prompts只能找到局部最优解。

这篇论文中，主要提出P-tuning去自动搜索prompts模板。使用P-tuning在两个NLU任务上：

（1）LAMA：固定模型参数后，GPT能够提升26.2%~41.1%

（2）SuperGLUE：GPT模型比BERT模型效果要好。

**论文贡献：**

（1）GPT在使用了P-tuning下，在自然语言理解中效果要比BERT好。这证明了，GPT结构在自然语言理解上具有较大的潜在能力

（2）提出来的P-tuning 能够在few-shot和监督学习中提升GPT和BERT模型效果。

### 3.Method：P-tuning

P-tuning主要应用在预训练模型的输入中，对模型是无伤害的。 换句话说，P-tuning代替预训练时的输入词语，把输入词语embeddings变为输入隐藏层参数$h$，然后进行连续性的搜索prompt模板。

![](https://pic2.zhimg.com/v2-676bbb9a7a70e81ead90998984c1cae9_1440w.jpg)

- 给定一个句子： **“The capital of Britain is [Mask]”** ，其中主要内容"Britain"，目标词语为"[MASK]"。然后让模型填空，预测[MASK]词语。
- 左图：传统的离散搜索prompt，主要更新输入词语的embedding  $h_i$ ，用来代替真实词语$e_i$
- 实际上公式就变成这样：

${e[P_{0:i}], e(x), e[P_{i+1:m}, e(y)]}  \rightarrow {h_0,h_i, e(x), h_{i+1}, ... , e(y)]}$

在优化方法上，论文认为存在两个挑战：

- 1） **离散性** ：经过预训练后，原来的单词embedding已经变得高度离散。如果用随机分布进行初始化，然后用随机梯度下降（SGD）寻找prompt模板，那么优化器很容易陷入局部极小。
- 2） **关联性** ：另一个问题是，embedding $h$应该相互依赖，而不是相互独立。需要一些机制来将提示embedding相互关联。

为了解决上面两个问题，论文中使用了LSTM模型更新embedding  $h$ 参数。同时添加一些额外的词语作为提示，比如句子“The capital of Britain is [Mask]”，“capital”也是一个关键词语，因此保留下来。

### 4.Experiments

![](https://picx.zhimg.com/v2-555b829205bd906aaa0ca6b1a2328cd3_1440w.jpg)

- MP：手工prompt
- MP+FT：手工prompt + fine-tuning
- PT： P-tuning
- 从实验结果上，使用了P-tuning方法都比MP方法效果要好，在Bert模型上，P@1能够提升14.

也对比了一些手工Prompt模板，P-tuning方法表现得更好：

![](https://pic1.zhimg.com/v2-8dd773830a1c94a42d167197e571a754_1440w.jpg)

## 《P-Tuning v2: Prompt Tuning Can Be Comparable to Fine-tuning Universally Across Scales and Tasks》

### 1.摘要

Prompt tuning，是一种在固定语言模型上进行连续性模板微调的方法，它能够减少训练时每项任务的存储和内存使用。但是以前的工作认为，在正常大小的 **预训练模型（模型比较大的情况下）** 上，prompt方法不能够起到很好的作用。

同时该论文认为，目前存在的prompt方法上，在序列标注任务上缺乏有效性和全局性。因此本论文在Prompt tuning基础上提出了V2版本。

在新的方法上，发现适当优化prompt能够在模型规模和NLU任务中达到平衡，而这种方法仅仅需要调整0.1%~3%的参数，就能够媲美fine-tuning的效果。

### 2.Introduction

对于NLU任务来说，fine-tuning训练更新大量的模型参数。虽然它能够获得很好的效果，但是在训练的过程中需要耗费内存，这是因为fine-tuning方法需要计算梯度和保存更新模型时需要的参数。

近期新起的Promp方法，会冻结所有的预训练任务，同时使用自然语言模版来调整语言模型。例如，对于一个情感分类任务来说，预测词语 `[MASK]` 为："Amazing movie！"，手工模版为"The movie is [MASK]"，这样做的目的可以提示预训练模型去预测该电影的情感倾向。

Prompt不需要训练和保存所有的参数，只需要加入手工模板就可以。然而，现有的离散型prompt比目前的fine-tuning要差。

Prompt tuning是一种在连续性prompts进行微调的算法。只有连续性的prompts才需要进行参数更新。但是prompt tuning只能在大模型下起作用，如果模型参数小于1千万，则模型效果还是会比fine-tuning要差。如下图所示：

![](https://pic4.zhimg.com/v2-cbf9d98f43e761374dc827c679e65fcd_1440w.jpg)

从图上可以看出，该论文提出的P-tuning v2版本，在小模型和大模型下都能够媲美fine-tuning。

**论文主要贡献：**

- **改进了P-tuning方法，提出了v2版本，在每一层神经网络下嵌入连续性prompt**
- **P-tuning v2版本在小模型下，效果能够达到和fine-tuning一样**

### 3.模型

现有的prompt有一定缺点：

**（1）不能有效使用在小模型上：超过1千万的参数模型，使用prompt能够达到fine-tuning的水准。但是如果在100M或1B的模型上使用，则会变得较差。**

**（2）在一些任务上没有提升效果：比如在序列标注任务上**

![](https://pic3.zhimg.com/v2-e2f91b55b8b0ecea14479264b01aba7c_1440w.jpg)

如上图所示，P-tuning只用在输入层的embedding中，这会有两个问题： **首先，由于序列长度的限制，可调参数的数量受到限制。其次，输入embedding对模型预测有相对间接的影响**

为了解决上面的问题，v2版本主要在每一层增加前缀tokens。这样，在可调参数上增加到0.1%~3%，而且可以在每一层直接影响模型

### 4.实验数据

使用数据集SuperGLUE，来验证P-tuning v2在NLU任务上的能力。 同时还使用了其他的序列标注任务，来验证v2版本在这些任务上的能力：

（1）命名实体识别

（2）QA

（3）语义角色标记

![](https://pic1.zhimg.com/v2-a0ae7b1517297c05fa048db465d1922a_1440w.jpg)

从图上可以看到，在小模型下（参数量在335M左右），PT(prompt tuning)方法效果要比fine-tuning差很多，但是PT2方法明显能够接近fine-tuning方法， **而这仅仅使用了少量参数进行微调。**

![](https://pic2.zhimg.com/v2-1fe78cf0bc6486d766afabbf121540f7_1440w.jpg)

同时还验证了两种微调方法：一种是自顶层到底层开始，加入prompt，另一种则是从底层开始到顶层加入prompt。从上图可以发现， **自顶层到底层的方法效果要更好** 。