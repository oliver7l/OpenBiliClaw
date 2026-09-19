# 开源的Stable Diffusion 3 Medium效果如何？

- 链接: https://zhuanlan.zhihu.com/p/703790864
- 发布: 2024-06-17 10:43:04
- 赞同: 1 | 评论: 0

---

> 公众号：算法一只狗

在2月份的时候，Stable Diffusion 3曾经公布过其强大的能力，由于其在细节生成上更加可控，不少人就一直期待着这个模型的开源。

这不，在大众千呼万唤下，目前网上已经可以下载Stable Diffusion 3 Medium免费开源模型。

具体说来，这次开源的版本属于Stable Diffusion 3 Medium，其具有20亿参数量级，有以下几个特性：

- **模型变得更大了** ：从之前的800M参数升级到20亿参数量。这意味着，新的模型能够为用户提供更多扩展性选择，同时生成的图片效果更加惊人。模型变得更大，意味着生成的图片能够提供卓越的细节，实现照片级逼真的输出以及灵活风格的高质量输出。

![](https://pic3.zhimg.com/v2-ab5781fbe5e40c3ea53695c2967599b4_1440w.jpg)

- **prompt控制更强** ：能够根据给定的主题词，限定模型生成更加符合条件的图片，比如下图中给定的prompt：“史诗般的动画艺术作品，一位巫师在夜间在山顶上向黑暗的天空施放宇宙咒语，上面写着由彩色能量制成的“stable diffusion 3””

![](https://pica.zhimg.com/v2-dd983d8b7351ec6109cd481e3be07876_1440w.jpg)

从上图中可以看到，新模型在prompt控制上更上一层楼，氛围感方面也有明显的提升。

- **使用Diffusion transformer模型结构** ：这个模型结构可以说和Sora使用的基础模型一致，被称为DiT架构。主要是使用Transformers替换扩散模型中U-Net主干网络。这样做的效果不仅速度更快，而且在不同任务上都取得了很好的效果。

![](https://pica.zhimg.com/v2-d732d28662b5efb825d8d61894c1cb3a_1440w.jpg)

- **运行时可以高效使用资源** ：得益于其低显存占用，在标准消费级GPU上运行时无需性能降低，是理想的解决方案。
- **适合个性化微调** ：新版本的SD3能够在微调的时候，充分学习小数据集的细节，使其更容易还原数据集的真实细节

## Stable Diffusion 3 Medium vs DALL.E

说了这么多Stable Diffusion 3版本的优点，那么在同样prompt下，它的效果和Dall.E有什么区别呢？让我们一起来对比一下。

![](https://pic4.zhimg.com/v2-a2d5021662c2398eecd5f0ecd67ce97f_1440w.jpg)

> prompt：狗狗穿外套

![](https://pic4.zhimg.com/v2-67094051dbc7d11c40c82f26a5e97f11_1440w.jpg)

两个模型生成的效果都还可以。但是SD3生成的狗狗更为真实，而Dalle.E生成的图片有一点点假。在明亮度方面，Dalle.E在生成动物方面会打光严重，所以个人还是喜欢SD3生成的照片。

> prompt：古老废弃药店中三个古董龙形玻璃魔法药水的照片：第一个是蓝色的，标签为“1.5”，第二个是红色的，标签为“SDXL”，第三个是绿色的，标签为“SD3”

![](https://pica.zhimg.com/v2-649d7758eb9bbbdcdce6e39b643950ce_1440w.jpg)

在细节控制方面，两个模型都很好的完成了prompt提出的要求。不过Dall.E模型，在生成的一个蓝色瓶子的字体时，有一点点偏差，字体上多了一个“5”。在细节完成度上，SD3还是更胜一筹。

> prompt: 烤盘上有形状字母的饼干照片，拼成单词“Fresh from the oven”。照片是在有人工照明的面包房拍摄的。

![](https://picx.zhimg.com/v2-ef70307911269f38447bfec4787fd619_1440w.jpg)

这两张图的对比更加明显，SD3完美的生成了饼干字体，而Dall.E并没有还原出给出的单词。

综合来看，SD3在文字细节控制和图片的和谐角度来看，都比Dall.E要好很多，但毕竟是把最新技术和一年多以前的技术进行了比较，所以SD3强一点也是正常的。

## 初体验与安装

目前最快上手的体验方法，可以去到huggingface提供的网站

> [https://huggingface.co/spaces/stabilityai/stable-diffusion-3-medium](https://huggingface.co/spaces/stabilityai/stable-diffusion-3-medium)

再来看看几组SD3生成的人物照片：生成的国内国外的人物效果都比较好，人物细节上也相当出色

![](https://picx.zhimg.com/v2-0dbf8c995e2a5950702c16edffd96b69_1440w.jpg)

生成在水中的人物细节效果也很好：

![](https://pic3.zhimg.com/v2-6cb275b272eb69bc4d958ce23381231a_1440w.jpg)

也能很好的hold得住漫画风格和同时生成多种动物：

![](https://pic2.zhimg.com/v2-62d8ba66695cb991deb1d54f7d76aec1_1440w.jpg)

但有一个问题在于，让SD3生成在草地中的人物时，效果便会大打折扣。往往在人体姿势或者人体结构上有明显的错误：

![](https://pic1.zhimg.com/v2-d64aa0db663440fe59eaf07e6336dd94_1440w.jpg)

这是因为，官方把很多不太适宜的训练数据剔除掉，导致目前SD3 medium对人体结构有理解上的问题。

目前如果想要本地进行运行，可以使用“ComfyUI”进行安装。stability AI官方已经做出了一个Webui，叫做StableSwarmUI。

> [https://github.com/Stability-AI/StableSwarmUI](https://github.com/Stability-AI/StableSwarmUI)

实测在GPU 4060下，显存占用8G左右。

如果想要更加方便的安装包进行一件部署的， **可以在公众号回复“SD3”去到百度网盘上下载** 。里面已经包含了模型，只需要启动对应工作流即可

![](https://pic4.zhimg.com/v2-908b216ee46b6225e0b8a492afd00189_1440w.jpg)

尽管SD3 medium模型对在“地上的人物”理解存在偏差，但这并不影响SD3作为一款优秀的文本生成图像模型的事实。SD3在生成图像的精细程度上有了很大的提升。相较于以往的文本生成图像模型，SD3能够生成更加逼真的图像细节，包括纹理、光影、色彩等方面。这使得生成的图像在视觉上更加接近于真实照片，为用户带来更好的体验。

除了精细程度外，SD3在生成效率方面也有显著的提升。相较于传统方法，SD3能够在更短的时间内生成高质量的图像。这使得它在实际应用中更加实用，例如在快速设计、广告创意等领域，SD3可以大大缩短创作周期，提高工作效率。

总之，作为一款优秀的文本生成图像模型，SD3在生成图像的精细程度和生成效率方面都有显著的优势。对此感兴趣的小伙伴，可以亲自尝试使用SD3，感受它带来的惊艳效果。

以上就是本期的所有内容了，我是leo，我们下期再见~