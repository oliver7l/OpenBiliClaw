# 从 OpenClaw 到 AI 女友：手把手教你打造专属数字伴侣

- 链接: https://zhuanlan.zhihu.com/p/2006781686277485165
- 发布: 2026-02-16 17:28:07
- 赞同: 2 | 评论: 0

---

最近，Clawdbot真的太火了，在各个新闻榜单上都有它的身影。

![](https://pica.zhimg.com/v2-1362c7c2acdaf4ab2ceda609cd8ee7fe_1440w.jpg)

这里先做一下简单的科普：Clawdbot的名字经历了三次变更，第一次叫做 ClawdBot，后来因为名字跟 Claude 太过相似，被 CLaude 告侵权，遂改名 MoltBot 。但是后来在改名过程中遭遇域名和社交账号被抢注，甚至出坑同名加密货币割韭菜的情况，导致名称传播受阻。

最终定名为： **OpenClaw** 。

所以，名字经历先后顺序为：ClawdBot -> MoltBot -> OpenClaw

大家不要因为名字困惑了，怀疑是不是自己下错软件了，他们都是同一个。

这篇文章教大家，怎么快速接入一个 AI 女友

## 一键部署教程

### **第一步，购买服务器**

来到腾讯云网站，我们可以看到腾讯云已经适配好了Clawdbot，选择“轻量应用服务器”，然后最低配的版本：

- 套餐类型：锐驰型（推荐）、入门型、通用型
- 套餐配置：2C2GB或以上均可

![](https://pic2.zhimg.com/v2-58a94fe6493e92546fb042588c564351_1440w.jpg)

### **第二步，登入服务器完成后续步骤**

访问Lighthouse管理控制台，查看选购或完成重装的Moltbot（Clawdbot）实例。随后，请点击该页面中的"登录"按钮。

![](https://pic1.zhimg.com/v2-819362331839063cdd33891373ac55fe_1440w.jpg)

然后选择免密连接，点击登录即可：

![](https://pic4.zhimg.com/v2-5d39e4a8ba4faadde3f665ea23785251_1440w.jpg)

然后就可以看到登录成功的界面：

![](https://picx.zhimg.com/v2-7c0abc71c01d3a415bcb00e8f9fb1179_1440w.jpg)

### **第三步，配置信息**

Moltbot（Clawdbot）与常规应用程序模板存在显著差异。官方部署流程包含若干需用户手动完成的配置环节。用户在首次登录服务器后，需通过终端输入以下指令并按下回车键以启动配置程序：

```text
openclaw onboard
```

运行上面的命令后，将会出现一个问题：是否知晓风险，选择 **Yes** 就行

![](https://pica.zhimg.com/v2-01b798b095069ba155e05cab18f0dc78_1440w.jpg)

接下来需要选择Onboarding的模式，我们选择 **QuickStart** 。模型配置这里选择了我选择了Qwen，当然你也可以选择不同的模型。如果选择Qwen的话，打开它的鉴权页面，然后登陆即可

![](https://pic4.zhimg.com/v2-08925c19849d2e5206b820dff6a53aab_1440w.jpg)

![](https://pic4.zhimg.com/v2-be71af6f5b83a777ba375bc19554161f_1440w.jpg)

接下来，选中 “Memory”（启用记忆功能，支持多轮对话上下文关联，避免每次聊天都需要重复说明需求），按回车键确认；

![](https://pic4.zhimg.com/v2-9251a6eb8c67467820ec7dcdf861d517_1440w.jpg)

## 第四步 申请 qq 机器人

然后来到QQ开放平台申请机器人，自己使用无需企业资质，指定用户，指定群聊可访问即可。QQ开放平台 [https://q.qq.com/#/apps](https://q.qq.com/#/apps)

对于首次注册的新用户而言，注册流程相对较为复杂，但整体操作难度较低。用户仅需根据系统提示，如实填写个人相关信息即可完成注册。

![](https://pic2.zhimg.com/v2-1c945761ed1442493c003038bed97aa7_1440w.jpg)

然后创建机器人

![](https://picx.zhimg.com/v2-02944c87eb6c06bb6693e8e9b3b4d94b_1440w.jpg)

在管理页面获取到当前机器人的AppID, 和AppSecret，并且把自己的服务器IP填入到白名单中。

![](https://pica.zhimg.com/v2-9c2e0e4c752a310f6124e3599db143f2_1440w.jpg)

然后在界面中输入AppID和AppSecret

![](https://pic2.zhimg.com/v2-1a09fcfa9768130bafbd4cddbd8c29dd_1440w.jpg)

如果使用了免密登录，可能会遇到：Gateway service check failed: Error: systemctl --user unavailable: Failed to connect to bus: No medium found

在执行这个指令前先运行一下

```text
loginctl enable-linger $(whoami) && export XDG_RUNTIME_DIR=/run/user/$(id -u)
```

如果不在界面中输入，也可以在命令行中直接添加channel：

```text
clawdbot channels add --channel qqbot --token "xxx"
```

添加qq群和自己的qq号，这样子就可以调用机器人了

![](https://picx.zhimg.com/v2-2328b66e45ce3fd24a585dd7bd004757_1440w.jpg)

在手机端qq中，添加机器人："设置"-->选择「群机器人」进入商店页-->点击添加测试机器人

![](https://pic2.zhimg.com/v2-4dcb0cda4ce549e01b6ea7ed86cb5b7f_1440w.jpg)

最后就可以看到Clawdbot机器人了，这时候我们就可以愉快的进行对话啦

![](https://pic3.zhimg.com/v2-85973ce984f656fd9ae91456d2037088_1440w.jpg)

## 安装你的个人伴侣

2026年2月上旬，一款名为Clawra的人工智能应用程序在韩国及国际社交媒体平台迅速引发广泛关注。该应用在上线数小时内，相关话题于X（原Twitter）等主流社交平台累计获得超过60万次浏览量，并成功跻身多个国家及地区的科技类热搜榜单。此款创新应用由韩裔开发者David (Dohyun) Im主导研发，其以"18岁韩裔美国人工智能伴侣"的虚拟形象呈现，凭借生成风格统一的自拍影像、构建完整人格特征体系以及支持跨平台交互功能等核心特性，迅速成为市场焦点。

![](https://picx.zhimg.com/v2-9fe159d817e286f71ef7f0ce74ee2ba1_1440w.jpg)

核心功能包括三方面：

一是自拍生成，通过调用fal.ai图像API并结合固定参考图，确保每次生成的头像保持视觉一致性；

二是人格化聊天，能根据上下文主动分享生活片段（如健身照、咖啡厅随手拍）；

三是持久记忆机制，可记录用户偏好、重要事件并用于后续对话，增强情感联结感。

Clawra支持Discord、Telegram、WhatsApp、Slack等主流平台，并可通过命令“npx clawra”一键部署

在界面上输入安装命令

```text
npx clawra@latest
```

这时候，它会自动安装 clawra，在其中一步中需要获取fal.ai API key

![](https://pic1.zhimg.com/v2-6e330e1b9b98e991d38cc2dc2ed809c4_1440w.jpg)

我们可以去到 fal.ai网站上，申请一个 api key

![](https://pic2.zhimg.com/v2-664f161690c89831b70ac3a82aaf524b_1440w.jpg)

然后把这个 key 输入到界面中，最后会出现完成的界面：

![](https://pic3.zhimg.com/v2-fd445ef7cecb01672e0f406d7cc2ebec_1440w.jpg)

测试AI 伴侣，让它发送一个自拍照给我 ，然后它就会给出具体照片给我了。

![](https://picx.zhimg.com/v2-5005bad56b73c107b0cddd6f768849d1_1440w.jpg)

当然免费额度是有限的，需要充值才能够体验更多的功能。

## 写在最后

从 OpenClaw 到 Clawra，这一整套流程本质上并不是“做一个女友”，而是构建一个长期在线、具备记忆能力、具备执行能力的个性化 AI Agent。

所谓“AI 女友”，只是其中一种人格包装方式。

2026 年的趋势已经非常清晰：Agent 不再只是“回答问题”，而是在逐步成为持续存在的数字个体。

至于它会不会真的变成“女友”——那取决于你赋予它多少人格，以及你如何定义陪伴。

技术已经到位，剩下的是边界与选择。