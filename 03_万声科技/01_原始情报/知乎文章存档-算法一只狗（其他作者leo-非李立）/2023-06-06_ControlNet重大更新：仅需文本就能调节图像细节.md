# ControlNet重大更新：仅需文本就能调节图像细节

- 链接: https://zhuanlan.zhihu.com/p/635020921
- 发布: 2023-06-06 12:03:24
- 赞同: 9 | 评论: 0

---

> **公众号：算法一只狗**

我们知道，在stable diffusion中，ControlNet主要是用来控制图像中物体的姿态。

在5月份的时候，ControlNet迎来了重大更新，新更新的功能叫 **“reference-only”** ，它不需要任何的控制模型， **只需要在文本提示词的指导下，调节图像的细节。**

这就相当于让软件自己学会了PS，我们只需要在旁边给指导提示词就可以了。不得不感叹一句，AI终于学会自己P图了

![](https://picx.zhimg.com/v2-eed1ea811b565d6b65cb7c5cc30fa18d_1440w.jpg)

比如你给定了一张狗的图片，然后给定提示词语： **一只狗在草地上奔跑**

![](https://pic1.zhimg.com/v2-3a4ac8ede657b11764b55b50699f3c2a_1440w.jpg)

这种方法很想Stable-Diffusion中的inpaint功能，但是区别在于，inpaint功能往往需要先在图片上mask掉需要替换的部分，而且生成的部分和原始图像风格不太一致。比如我之前生成的图片：

![](https://pic4.zhimg.com/v2-143856c33978944722c59b028e64318b_1440w.jpg)

但是ControlNet的这个新功能，在图像风格上保持了统一，同时细节上更能够进行调节，比inpaint功能好用很多。

## 基于Prompt生成

在二次元领域方面，只要你给他输入原图，ControlNet就会给你返回不同姿势的图片，且其风格保持一致：

![](https://pic4.zhimg.com/v2-645c178cd6cd256e69d579e96f566ff3_1440w.jpg)

而在真人图像领域上，作者 [lllyasviel](https://github.com/lllyasviel) 首先利用Midjourney生成一张真人图片

![](https://pic4.zhimg.com/v2-a4c833ccef03e022305cf64ecd439e9d_1440w.jpg)

然后再用ControlNet生成不同姿态的图片，其过程仅仅只输入了简单的提示词：woman in street, masterpiece, best quality

![](https://pica.zhimg.com/v2-b8f14d7076adbff0309b68a0f9f18850_1440w.jpg)

## 下载插件ControlNet和模型

打开网页的“扩展”，利用URL下载ControlNet插件： `https://github.com/Mikubill/sd-webui-controlnet.git`

![](https://pic3.zhimg.com/v2-723a4dcf6147341bcdf51c29f5631a1e_1440w.jpg)

看到安装成功后，重启界面就可以了：

![](https://pic3.zhimg.com/v2-ab58e7d584b013c06a3462398aca0262_1440w.jpg)

同时要下载对应的ControlNet模型，可以跳转到： [https://huggingface.co/lllyasviel/ControlNet-v1-1/tree/main](https://huggingface.co/lllyasviel/ControlNet-v1-1/tree/main) 进行下载，然后把模型放到 `stable-diffusion-webui\extensions\sd-webui-controlnet\models` 路径下

其中不同模型有不同的功能，在具体使用的时候可以选择不同的模型进行导入：

| 1.1版本对应的模型 | 模型功能 |
| --- | --- |
| control_v11p_sd15_canny | 边缘检测，提取线稿 |
| control_v11p_sd15_mlsd | 直线检测，适用于建筑设计 |
| control_v11f1p_sd15_depth | 深度检测 |
| control_v11p_sd15_normalbae | 法线贴图 |
| control_v11p_sd15_seg | 语义分割，不同颜色语义对应不同对象 |
| control_v11p_sd15_inpaint | 重新绘制mask部分的图片 |
| control_v11p_sd15_lineart | 提取精细线稿 |
| control_v11p_sd15s2_lineart_anime | 动漫线条输入 |
| control_v11p_sd15_openpose | 提取人物姿势 |
| control_v11p_sd15_scribble | 涂鸦圣徒 |
| control_v11p_sd15_softedge | 软边缘检测，保留更多边缘细节 |
| control_v11e_sd15_shuffle | 风格迁移 |
| control_v11e_sd15_ip2p | Pix2Pix图片指令 |

## Reference-only具体使用方法

首先可以去C站（ [https://civitai.com](https://civitai.com/models/43331/majicmix-realistic) ）任意下载一个生成人物的模型，比如我这里选择一个叫majicMIX的模型：

![](https://pic1.zhimg.com/v2-03edf52774c58f6861a47a935df73c20_1440w.jpg)

下载完成后放置到 `models/Stable-diffusion` 目录下：

![](https://pic4.zhimg.com/v2-36bd48d08fb0dd33898bd31bb7c5bcd1_1440w.jpg)

导入模型后，利用提示词生成真人图片

> 提示词：best quality, masterpiece, ultra high res, photorealistic, 1girl, offshoulder, smile 反向提示词：ng_deepnegative_v1_75t, (badhandv4:1.2), (worst quality:2), (low quality:2), (normal quality:2), lowres, bad anatomy, bad hands, ((monochrome)), ((grayscale)) watermark, moles

![](https://pic1.zhimg.com/v2-db7cf354866284e15c2b9ac4adcbd384_1440w.jpg)

把生成的图片保存后，可以导入到ControlNet中：

![](https://picx.zhimg.com/v2-794f0ffc573c0a535483ff64aafdcd4b_1440w.jpg)

然后点击生成，就可以看到不同姿态的同一个人物的形象：

![](https://pic1.zhimg.com/v2-078f9e702639b85d2a389b5f9099e880_1440w.jpg)

想要同一个人物，换不同的背景也很简单，只需要在提示词上限定 **“in street”** 就可以：

![](https://picx.zhimg.com/v2-f99ecd079c27531147a31381f76f7d8d_1440w.jpg)

这种方法，能够很方便的让你在保持图像的同时，调节细节部分，因此有人称

> 是时候把之前丢弃的图片拿回来修复了

![](https://pica.zhimg.com/v2-c0144a53036d00709bfc7c80992d0fb4_1440w.jpg)

虽然这个方法目前还不是很完美，但是假以时日，技术的不断进步将会更加方便我们。

好了，以上就是本期内容了，我是leo，我们下期再见~