# 挑战不写一行代码！基于ChatGPT搭建自己的AI新闻总结工具

- 链接: https://zhuanlan.zhihu.com/p/649971951
- 发布: 2023-08-14 17:30:31
- 赞同: 2 | 评论: 0

---

> **公众号：算法一只狗**

ChatGPT在生活中应用得最多的能力，当然要数其总结文章的能力。因此我们也不妨可以直接利用ChatGPT来实现一个自动AI新闻总结工具，把自己感兴趣的文章让它直接帮你总结。让ChatGPT快速帮你提炼要点，总结不同文章内容。

## 前期准备

1. 首先你当然需要有一个具体的ChatGPT账号，怎么申请可以看这篇文章：

[ChatGPT它还能玩出什么花来？](https://www.yuque.com/yuqueyonghumaryyq/vou788/olteqnxh37i8lno3?view=doc_embed) 如果实在申请不了，也可以去某宝买一个账号，几十块就有了。

2. 然后要准备一个可以用的chatgpt 的api key，怎么申请信用卡和绑定教程可以看这里：

[ChatGPT账号申请+充值API和Plus账号](https://www.yuque.com/yuqueyonghumaryyq/vou788/zbr028rgulk2fqme?view=doc_embed) 本质上其实是在软件上申请一个虚拟的信用卡，信用卡充值需要用到USDT货币，就在殴易平台上充值就可以了。

3.在官网上绑定信用卡后，就可以开始调用chatgpt的api key了

在个人页面上找到自己的API key：

![](https://pic1.zhimg.com/v2-7f49e87f13ef681c5b7cbaa8cd7c0f00_1440w.jpg)

## AI新闻爬虫

### 1.安装Selenium进行网页内容爬取

### 安装selenium

```powershell
pip install selenium
```

### 配置环境

（1）查看对应Chrome版本： 打开谷歌浏览器：浏览器输入地址

```js
chrome://version/

```

![](https://picx.zhimg.com/v2-882124415d338472bd34339030a4c22f_1440w.jpg)

可以看到，版本号未99.0.4844.51 （2）下载Chrome谷歌浏览器对应版本的驱动: Chrome Drive chromedriver下载网址： [http://chromedriver.storage.googleapis.com/index.html](http://chromedriver.storage.googleapis.com/index.html) 选择版本为99.0.4844.51:

![](https://pic4.zhimg.com/v2-199df34d78c993c083bcc867b5d91269_1440w.jpg)

解压后得到文件：chromedriver.exe，并把该文件放到python3中的Scripts中：

![](https://pic3.zhimg.com/v2-bc6da7e0f2dfbc45648f2c8706e5ea0c_1440w.jpg)

### 启动Selenium命令

利用chrome浏览器内核，就可以不启动窗口也可以登陆网站

```python
import time
from selenium import webdriver

# 1、创建Chrome实例 。
driver = webdriver.Chrome()
# 配置Chrome WebDriver的选项
options = Options()
options.add_argument("--headless")  # 以无头模式运行，即不显示浏览器窗口
# 添加不加载图片设置，提升速度
# options.add_argument('blink-settings=imagesEnabled=false')
# options.add_argument("interactive")
driver = webdriver.Chrome(options=options)
```

### 2.分析网站内容

这里主要爬取： [https://dataconomy.com/category/topics/data-science/artificial-intelligence/](https://dataconomy.com/category/topics/data-science/artificial-intelligence/)

![](https://pic3.zhimg.com/v2-1de7a8cdac888d2e7d4e8b111f724c26_1440w.jpg)

比如我们点击上面网页的《tiktok wants to know》这篇文章的内容，然后F12打开开发者工具，发现需要爬取的内容包裹在了：div.content-inner

![](https://pic4.zhimg.com/v2-e8ce5589961ec1de5736920328a4e603_1440w.jpg)

因此我们可以询问ChatGPT，怎么基于这个网页进行爬取：

![](https://pic4.zhimg.com/v2-5af23117bac590a11e2fce26ab946197_1440w.jpg)

然后ChatGPT就能给出相应的代码获取网页内容。

接着就可以调用ChatGPT的api_key，让他把刚刚那些内容进行总结：

![](https://pica.zhimg.com/v2-67789f6d94d2eae90a3517957370d378_1440w.jpg)

得到ChatGPT总结的内容：

![](https://pic3.zhimg.com/v2-6b3dc618144e69dcba71960c6f910a00_1440w.jpg)

## 总结

整体来看，利用ChatGPT就可以做到很好的闭环，不需要自己写大量的代码，就可以轻松实现一个爬虫+总结的工具。这个工具能够帮你一下子总结很多的内容文章，极大提高工作效率。所有代码存放github仓库中：

[https://github.com/llq20133100095/ChatGPTStartedGuide](https://github.com/llq20133100095/ChatGPTStartedGuide)

![](https://pic1.zhimg.com/v2-e5836bc3ac16523007659983fadafef0_1440w.jpg)

感兴趣的可以去下载来手动实现一下

好了，以上就是本期的内容了，我是leo，我们下期再见~