# AI绘图：Stable Diffusion 2.0它来了！

- 链接: https://zhuanlan.zhihu.com/p/589544263
- 发布: 2022-12-06 10:03:51
- 赞同: 1 | 评论: 0

---

自从Stable Diffusion 1.0模型发布以来，“AI文本图片生成”真正的变成普通人也能使用的技术。

同时各种国内外AI绘图软件，也不断频繁更新，像比较出名的 **文心一格，盗梦师，6open等生成工具，** 生成的图片已经达到了以假乱真的地步。想看详细介绍的，可以回看这篇文章：

[算法一只狗：国内外免费的AI作图软件](https://zhuanlan.zhihu.com/p/583842111)

而在这次Stable Diffusion 2.0的发布中，有几个比较重要的更新。

![](https://pic4.zhimg.com/v2-bf06c1a2c13ecff62d33fc0acfeb9415_1440w.jpg)

**第一，** 新的模型能够生成 768 * 768 高分辨率图片，在利用相同参数的U-Net模型结构下，文本编码器主要使用了OpenCLIP-ViT

![](https://pica.zhimg.com/v2-aeffcdafd8af860ca816b830aaf0d702_1440w.jpg)

**第二，** 在下游任务微调过程中，在去噪模型利用 512*512图片进行训练

![](https://pica.zhimg.com/v2-0f2e33fe8b8bd7939d552b878e7b17b4_1440w.jpg)

**第三，** 分辨率对比以前模型可以放大，对于同一张图，可以进行高清*4倍放大：

![](https://pica.zhimg.com/v2-7151bd7dee2b6a95eacbb53c17ddddca_1440w.jpg)

**第四，** 新增depth2image推理功能，利用深度信息推理生成图片。 比如通过从原始图片得到的深度信息图，然后可以利用stable diffusion模型进行推理，依赖于文本、图片和深度信息来生成新的图片。

![](https://pic3.zhimg.com/v2-b27c159a49a9a6557f19f05ac0dd32a4_1440w.gif)

新的生成图片，会保持原来图片的形状和结构。

这种新的功能，能够生成更多有创意的图片。比如你可以利用这个技术，不断替换不同的风格，就可以生成很多有意思的图片

**第五，** 图像重绘 模型在重新绘画方面，也保留了不错的效果：

![](https://picx.zhimg.com/v2-f41747631af64ef6188298ec8fd45cad_1440w.jpg)

具体模型的Github仓库地址可以看这里：

[https://github.com/Stability-AI/stablediffusion](https://github.com/Stability-AI/stablediffusion)

读者也可以自行下载模型，试跑一下具体的运行效果。

从Stable Diffusion发布以来，它让人们便捷绘画的同时，也造成了绘画艺术家的抵制。不过就像AlphaGo在围棋领域带来的革新风气一样，AI文本图像生成也会给更多的从业者提供创作思路，从而不断提升自己。

以上就是本期的内容了，我是leo，欢迎关注我的知乎/公众号“算法一只狗”，我们下期再见。