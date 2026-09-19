# 翻译：SmFace: Pre-training Encoder and Decoder with a Semantic Interface for Neural Machine Translation

- 链接: https://zhuanlan.zhihu.com/p/421846257
- 发布: 2021-10-15 15:28:38
- 赞同: 0 | 评论: 0

---

## 1.背景

![](https://pica.zhimg.com/v2-9112daa13287a10557e6185849b049f6_1440w.jpg)

这篇文章是北航和亚洲微软研究院共同发表的。目前主流的NLP任务，都需要先预训练模型，例如Bert和ALBert。本文章也主要集中在机器翻译（NMT）上的预训练任务。

本文指出，以前的预训练任务，并没有训练encoder和decoder之间的 **cross-attention** ，这会导致在fine-tuning阶段并没有巨大的提升。针对预训练任务上，cross-attention的训练，本文提出了两个语义交互（semantic interface）方法：

- **CL-SemFace** ：使用交互语言embeddings，训练attention的参数
- **VQ-SemFace** ：使用量化embedding，把encoder output和decoder inputs限制在同一语言独立空间中

实验中，用到了6个有监督翻译语言对，3个无监督翻译语言对

## 2. 引入

以前，预训练通常方法是在encoder和decoder上，利用大数据集独立进行训练，这种做法忽略了attention层的参数训练。论文中提到，通过语义接口（semantic interface），编码器经过预训练以将特征提取到该空间，解码器经过预训练以生成encoder提供的内容。

- **CL-SemFace** ：使用cross-lingual embeddings（跨语言）无监督训练
- **VQ-SemFace** ：同时映射encoder outputs 和decoder inputs 到同一VQ空间中

## 3.方法介绍

![](https://pic1.zhimg.com/v2-8ada7031ad417fb9bfe9e3f6c78001b0_1440w.jpg)

首先整个预训练阶段如上图所示。

（1）首先，使用单语数据分别预训练编码器和解码器，它们之间有语义接口。其中 $x_1$ 输入到encoder中， $x_2$ 输入到docoder中，两个输入用到的是两种不同的语言。

（2）编码器经过预训练得到Semantic Interface，而解码器经过预训练通过cross-attention内容完成解码。

具体的执行算法如下：

![](https://pica.zhimg.com/v2-95905083a81bde69ebae39d9cb070018_1440w.jpg)

- 输入：语料库 $D_x$ 和 $D_y$ ，输出：更新参数 $M_\theta$
- （1）随机初始化encoder和decoder的参数 $\theta_{enc}$ 和 $\theta_{dec}$ ，还有semantic interface 的参数 $\theta_{sf}$
- （2）针对CL-SemFace，初始化 $\theta_{sf}$ 作为预训练embeddings
- （3）从两个语料库中随机选择batch  $B$ ；输入  $B$ 到encoder和SemFace中，更新参数 $\theta_{enc}$ 和 $\theta_{sf}$ ；在输入 $B$ 到decoder中，更新参数 $\theta_{dec}$

## 3.1 CL-SemFace

![](https://pica.zhimg.com/v2-1d89e5412e949f33cf1eed288914fa80_1440w.jpg)

（1）encoder：输入 $x_1$ ，利用MLM任务和MSE任务，进行与训练

![](https://pica.zhimg.com/v2-bd2ab3d347c22c7c8e82f15b016b1aa2_1440w.jpg)

（2）cross-attention：在 $x_2$ 中添加噪声，得到 $C(x_2)$ 。把ecoder中得到的BPE embedding拿出来，然后用第二个样本 $x_2$ 进行输入编码，得到 $E$ 。把 $E$ 和 $x_2$ 进行相乘，用来训练decoder

（3）这种做法就可以同时训练attention层

## 3.2 VQ-SemFace

![](https://picx.zhimg.com/v2-52b197b29d67a0518d0a6688c42ce897_1440w.jpg)

CL-SemFace主要是用来约束word embedding，意味着不同的单词可能有同样的embedding，同时网络的units需要和词典的大小一致。

因此VQ-SemFace主要用来学习上下文独立的语义，它主要参考了VQ-VAE模型，设定了一个潜在空间。

VQ的定义可以参考这个网址： [https://zhuanlan.zhihu.com/p/91434658](https://zhuanlan.zhihu.com/p/91434658)

![](https://pica.zhimg.com/v2-b9c13746c28295869aa5c7f919ad4a7c_1440w.jpg)

VQ方法：把 $x_1$ 输入到encoder得到 $h$ ，然后在code-book（前在语义空间）找到最相似的$z$

## 4.实验

在fine-tuning阶段，去掉了semantic interface，直接使用cross-attention进行解码和编码。

![](https://pica.zhimg.com/v2-70d1f76975003f245e29a8950ab91000_1440w.jpg)

- 在多个数据集上，效果比Transformer要好。