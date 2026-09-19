# ControlNet生成视频方法：Mov2Mov插件（第一期）

- 链接: https://zhuanlan.zhihu.com/p/638992401
- 发布: 2023-06-23 10:26:44
- 赞同: 8 | 评论: 1

---

> **公众号：算法一只狗**

ControlNet本质上不是用来生成视频的，而是对于图像进行细节优化，又或者对任务进行姿态控制等等。可以参考我上周写的一篇文章，里面有具体介绍的方法：

[算法一只狗：ControlNet重大更新：仅需文本就能调节图像细节](https://zhuanlan.zhihu.com/p/635020921)

但是聪明的你肯定想到，既然ControlNet可以进行图像的控制，那么我们也就可以把图片进行叠加，一帧一帧的把图片生成出来后再还原就可以形成视频了。

## 1.安装Mov2Mov+Tagger插件

首先需要安装两个插件，分别是Mov2Mov插件和Tagger插件。

- Mov2Mov插件：用来替换原始视频，基于每一帧生成对应的图片
- Tagger插件：主要用于解析对应图片的提示词

首先我们可以在网上找一个跳舞小姐姐的视频，比如：

![](https://pic3.zhimg.com/v2-bb70319c8ae58e45fd0e8eaa65eb7bde_1440w.jpg)

我们可以把视频随机截取一张图片，用来作为前期调试。

在调试之前，需要描述该图片的一些基础信息，同时调试是为了能够确定使用什么参数对图片进行生成。

## 2.利用Tagger插件生成图片描述词语

安装好Tagger插件后，可以看到Tagger方框。

点击之后上传图片，就可以生成对应的图片提示词，这样我们就可以方便的得到图片的Prompt用来进行后续的生成。

![](https://picx.zhimg.com/v2-4a021af84c20880a11b7c7d92e4735e3_1440w.jpg)

点击图生图功能，把刚刚生成的提示词语填入，同时填入反向提示词语：

> 提示词：1girl, solo, long hair, black hair, blue dress, sandals, hairband, arms behind back, realistic, standing, full body, lips, looking at viewer, curtains, brown eyes 反向提示词：ng_deepnegative_v1_75t, (badhandv4:1.2), (worst quality:2), (low quality:2), (normal quality:2), lowres, bad anatomy, bad hands, ((monochrome)), ((grayscale)) watermark, moles

![](https://pic3.zhimg.com/v2-2099cb4c4de8d3692f9f0b2448f03350_1440w.jpg)

## 3.ControlNet风格化调试+Mov2Mov生成视频

上面步骤我们已经得到对应的提示词语。这里就可以根据提示词和ControlNet功能对图片进行风格迁移。

风格化调试的目的是为了得到稳定可控的参数，为后续Mov2Mov插件的使用提供帮助

比如可以类似的执行以下步骤：

- 设置其生成图片大小
- 固定重绘幅度，值越小，生成的图片与原始图片越类似
- 插入需要调试的图片
- 启用ControlNet插件
- 选择不同的功能，这里主要使用了最新的Reference功能

![](https://pica.zhimg.com/v2-f3df3d6157e5c031fe8cae9ef178f104_1440w.jpg)

可以不断调试参数，选择自己比较满意的参数。

然后点击Mov2Mov插件，把刚刚设置的参数填入，等待生成结果

![](https://pic4.zhimg.com/v2-f7c32ffe5c7dc8c0ebba45d97cd425b5_1440w.jpg)

这是我自己生成的，脸部细节还不是太好，但是基本能够还原视频的动作信息：

![](https://pica.zhimg.com/v2-4e0a2c8ea1fadf2d5d020d30634c0136_1440w.gif)

我们可以注意到，上面生成的视频有一个重大的缺点就是画面会闪烁。造成闪烁的原因很简单，是因为AI是一帧一帧生成图片然后再合成的，所有每张图片本质上不太连贯，所以就会造成闪烁的原因。

所以下一期，将介绍一种稳定生成的方法。欢迎大家关注~

好了，以上就是本期的内容了，我是leo，我们下期再见。