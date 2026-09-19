# 简单上手~用Stable Diffusion实现“妙鸭”相机的写真照

- 链接: https://zhuanlan.zhihu.com/p/648425215
- 发布: 2023-08-07 10:32:30
- 赞同: 3 | 评论: 4

---

> **公众号：算法一只狗**

上周，一款应用火遍了朋友圈。这款应用便是“妙鸭”相机，它以简单的操作步骤，就可以帮你生成自己的精修写真照片。

用户只需要上传21张图片，在1个小时内就可以制作完美的写真照片了。

![](https://pic3.zhimg.com/v2-d7e1230af7a2fc33999c760c9721511c_1440w.jpg)

看到这里，你可以想到这款应用很像以前的一键换脸技术。但是以前的换脸工具，不能够生成这么逼真的图片，只能是一眼就看到是假的图片。

![](https://pic4.zhimg.com/v2-b495d94aec93e01e95f7fcdde58ac44f_1440w.jpg)

但最近半年，得益于AIGC的发展，特别是midjourney和stable diffusion的出现，让普通人也可以直接生成自己的AI写真照。

接下来，我将用Stable Diffusion软件，来简单实现自己的AI写真照。

## 用Stable Diffusion做自己的写真照

### 1.安装roop插件

roop插件是Stable Diffusion中一个换脸插件，他可以根据不同的照片，把你的脸P上去。因此先需要安装roop插件。 但是要安装对应的插件，首先需要先安装相应的环境才可以：

- **安装“Microsoft C++ 生成工具”：** 打开地址， [https://visualstudio.microsoft.com/zh-hans/visual-cpp-build-tools/](https://visualstudio.microsoft.com/zh-hans/visual-cpp-build-tools/) ，然后下载对应的生成工具

![](https://pic4.zhimg.com/v2-9ce4d049d563336373bb4cd4e2e65f9d_1440w.jpg)

- **安装C++桌面开发**

![](https://pic1.zhimg.com/v2-da97fe10034886f9e4bc76f71aa65a1e_1440w.jpg)

![](https://picx.zhimg.com/v2-bdfefbcad8bda063465ea8f1ebbe58a7_1440w.jpg)

- 安装 **insightface环境：进入你的python环境，安装对应的包就可以**

```python
python -m pip install insightface
```

- 最后进入到界面中，利用github仓库地址进行安装，把这个地址“ [https://github.com/s0md3v/sd-webui-roop](https://github.com/s0md3v/sd-webui-roop) ”填入下面方框，安装后进行重启

![](https://picx.zhimg.com/v2-8455ba3c5d9d617222f69d50cc3cca01_1440w.jpg)

- 重启后，可以看到页面上有 **roop插件了** ：

![](https://picx.zhimg.com/v2-db8b48ce25f3920c3bac139ee873f863_1440w.jpg)

### **2.AI写真内容生成**

这里我在网上找了一张比较好看的写真照进行实验。

![](https://pic1.zhimg.com/v2-ada0106302a9fe31580cd889bd343c9e_1440w.jpg)

打开Stable Diffusion的图生图功能，然后进行局部重绘：

![](https://pica.zhimg.com/v2-901618dd7f71193751b6d73e419d06b4_1440w.jpg)

选择对应的采样方式：

![](https://pica.zhimg.com/v2-ae20296f35c66d63365665b2ce6a9e46_1440w.jpg)

利用ControlNet的OpenPose功能，固定脸的朝向：

![](https://pic1.zhimg.com/v2-fb8d0989c2a6865685d63bcddd2d40d6_1440w.jpg)

最后启用Roop功能进行人脸替换，这里面用了“赵今麦”的图片进行人脸替换：

![](https://picx.zhimg.com/v2-035862f4ebf1d663941a7ac3a366b0f3_1440w.jpg)

最后我们就可以看到生成的图片，这里面我抽了多次卡，生成了一个比较满意的图片，可以说脸还真的有几分相似的：

![](https://pic3.zhimg.com/v2-305b9d8d343a50fe9a6780f8b461ef50_1440w.jpg)

上面内容就是简单的实现AI写真照的方法，当然更复杂的方法我们也可以训练自己的lora模型，然后再生成自己的图片，如果读者感兴趣的我可以之后单独出一期教程。

好了，以上就是本期的所有内容了，我是leo，我们下期再见~