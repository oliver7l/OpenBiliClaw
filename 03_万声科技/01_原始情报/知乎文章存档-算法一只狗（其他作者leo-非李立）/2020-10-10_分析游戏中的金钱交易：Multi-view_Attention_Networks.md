# 分析游戏中的金钱交易：Multi-view Attention Networks

- 链接: https://zhuanlan.zhihu.com/p/264407771
- 发布: 2020-10-10 17:30:59
- 赞同: 1 | 评论: 0

---

## 1.摘要

**论文：MVAN: Multi-view attention networks for real money trading detection in online games** 里分析了在网游中存在的真实金钱交易行为。（Real money trading）这种交易行为，用真实世界的货币交换虚拟世界中的资产，导致游戏经济的不平衡和贫富不均。

论文中主要提出了一种新的模型MVAN（Multi-view Attention Networks），通过利用多种数据源检测真实的金钱交易。

这个模型主要包括以下几个部分：

- **multi-graph attention** ：基于图结构
- **behaviour attention network** ：基于顶点内容（vertex content），也即是角色的行为序列
- **portrait attention network** ：基于角色属性（vertex attribute），也即是角色的性别，等级等等
- **data source attention network** ：多种不同的数据进行结合

这个模型主要用来分析游戏《逆水寒》里面的交易行为。

## 2.引入

在MMORPG游戏中，不同的角色会有不同的特征。例如外貌，性别，名字，等级等等。在这些游戏当中，玩家会在虚拟世界中追逐富有。这样一种需求，会催生出真实的金钱交易行为 **(real money trading， RMT)** 在RMT行为中，通常有三种不同的角色：

- **Gold Farmers（金农）** ：通过使用自动化程序或雇佣低成本劳动力以获取虚拟商品。金农们作为一个团队一起工作来完成挑战性的任务，比如完成一个复杂的任务。
- **Gold Bankers（金币商）** ：聚集了从金农中获得的虚拟物品，然后交易给金币购买者
- **Gold Buyers（金币购买者）** ：从金币商中用真实的金钱购买大量物品。

这三种RMT行为的角色，被称为RMTers。

这种RMT行为，会危害游戏内的正常交易行为，同时会损害MMORPG游戏的声誉。如果要检测RMT的存在，有如下几个挑战点：

- **需要大量人力** ：传统的检测方法需要大量的人力来标记该行为是否属于RMT行为
- **标签不确定性** ：难以利用规则来确认禁止交易行为、不能观察的角色是否是RMTers
- **组识别** ：大多数的方法目标是对不同的Gold Farmers（金农）进行独立检测
- **不能快速反应**

下图为交易行为的发生情况：

![](https://pica.zhimg.com/v2-76ca0d2deb304d8568806f939d396916_1440w.jpg)

- 图1（b）中，通常金币商-金币购买者中，存在真实金钱交易；金币商-金农，存在虚拟金钱收集 - 图1（c）中，通过网络结构（network structure）、vertex attribute和vertex content，最后输出节点的label。

本论文的贡献有四个方面：

- **multi-view data sources** ：利用多种不同的数据结构来预测RMTers。
- **The MVAN** ：论文中提出的新模型，结合了多种不同的网络结构，同时引入了多个attention机制，最后把不同的loss进行相加，使得模型结构有所提升。
- **real evaluation** ：利用真实世界中的数据集来评估论文中的模型
- **工业应用** ：把论文的模型应用到不同的游戏中，从而验证模型效果

## 3.游戏数据描述

## 3.1 逆水寒中的游戏日志

该游戏中有以下信息：

- **时间戳** ：定义了事件发生的时间
- **角色信息** ：包含角色的id，等级，金钱等等
- **log ID** ：定义活动的ID号，包括打猎ID，交易ID等
- **目标角色的信息** ：即当前角色与其他角色有交互时，其他角色的信息
- **其他细节信息**

使用了逆水寒中共 **227,148** 个角色（时间跨度为11月1日到12月31日），其中： |gold farmers| gold bankers  | gold buyers| |--|--|--| | 12,529 | 1,549 | 2,737|

## 3.2 社交图分析

构建了5个图：交易图、设备分享图、交友图、团队图、聊天图

![](https://pic2.zhimg.com/v2-f075a30f771dbbf7c457ccbef42ead51_1440w.jpg)

- 图（a）：交易行为，有连接的点表示已经建立了交易行为
- 图（b）：有一部分角色进行过设备共享

同时，论文中把相邻1个节点的相关性图画出来：

![](https://pic4.zhimg.com/v2-85ddc144055f744302a446dfeefb239b_1440w.jpg)

- 图（a）：金农中，与金币商交易的行为相关性比较高 - 图（b）：金币商，与金币购买者聊天相关性较高 - 图（c）：金币购买者，与其他金农在共享设备方面相关性较高

## 3.3 行为序列

每一个角色的行为都包含一系列的事件信息，具体包含三个主要的特征：

- event ID：事件ID，角色当前发生的事件，比如使用一个技能，获取一个物品
- interval：间隔，事件之间的间隔
- level：该角色的等级

下图展示了三个不同的RMTers的行为序列：

![](https://pic1.zhimg.com/v2-fa935d584f2bb0df21ede4d675d25746_1440w.jpg)

每个slot代表不同的事件，红色代表交易事件

- 金农的行为是比较相似和简单的，因为金农通过继续接收和完成任务并定期将其转移给黄金银行家来获取虚拟资产。
- 金币商和金币购买者的行为比金农的行为更复杂和更难以预测

## 3.4 角色属性构造

角色的属性之前就提过，包括等级，性别，游戏分数等等。

下图展示了RMTers在不同属性中的表现：

![](https://pic4.zhimg.com/v2-3f8cf743b3ba869341901ab3fdb68ee9_1440w.jpg)

- 金币购买者会花费更多的时间去捏脸
- 金币商的虚拟资金会比较多
- 金币购买者在等级上会比其他两个类型要高

## 4.MVAN模型

该模型主要由四个部分所构成，模型结构图如下：

![](https://pic2.zhimg.com/v2-625163be68258bf1c445460e52f88e8b_1440w.jpg)

这四个不同的结构包括：

- **multi-graph attention network** ：GPT网络图结构，同时加上attention mechanism
- **behaviour attention network** ：利用了Bi-LSTM，同时加上attention mechanism
- **portrait attention network** ：使用了CNN模型，同时加上attention mechanism
- **data source attention network** ：把上面三个网络进行结构，构造输出

## 4.1 multi-graph attention network

在GAT网络中，使用embedding矩阵 $M_{emd}^c$ 来对节点 $c_i$ 进行向量化。同时使用5个维度的向量来表征边状态和权重。这5个维度就是上面所说的交易图（ta）、设备分享图（ds）、交友图（fr）、团队图（tm）、聊天图（ct），从而得到每个节点的表征：

![](https://pic4.zhimg.com/v2-d8ed9f59ed6cdaa8df1c97fa59097761_1440w.jpg)

其中 $i$ 和 $j$ 表示不同的角色节点。

![](https://pic2.zhimg.com/v2-d776a3adfcd0bf9763c3f2f8d0f8cecf_1440w.jpg)

看上面这张图已经比较明显可以解释过程了。这部分首先把每个节点特征向量化为 $\vec{h_i}$ ，同时计算节点 $i$ 和节点 $j$ 之间的关系，即 $W_g\vec{h_i}||W_g\vec{h_j}$ 。然后再利用 $softmax$ 求出对应的注意力权重 $a_{ij}^t$ 。唯一和GAT不同的地方在于，这篇论文引入了新的5维度向量 $\vec{e_{ij}^t}$ ，使得模型学习到更多的信息。

## 4.2 behaviour attention network

这里主要使用了两个Bi-LSTM模型，其实也就是一个 **双重Attention mechanism** 。

第一个Bi-LSTM称为Event Encoder，它的输入为 $e_{ij},j \in E_i$ ，其中 $i$ 为某个任务（quest）， $j$ 为该任务中发生的事件（event）。具体的任务和事件如下图：

![](https://pica.zhimg.com/v2-18d443ad5b952cd186d6070005d5d9ee_1440w.jpg)

之后通过Bi-LSTM进行转换，然后利用attention转换得到基于事件的输出。

![](https://pic1.zhimg.com/v2-c5be9bf9e879dc260b122cfd63038df6_1440w.jpg)

第二个Bi-LSTM主要针对的是任务（quest）的输入，把第一个Bi-LSTM得到的事件输出进行输入后，再次通过一个attention mechanism进行转换得到最后的输出。

![](https://picx.zhimg.com/v2-45f1a1efb4c8fa42f3c7aa4c72c526df_1440w.jpg)

假设有任务Q个，每个任务就有E个事件，这样就构成了两个Bi-LSTM网络了。

综上所述，这两个attention都比较简单。

## 4.3 behaviour attention network

这里主要使用了CNN模型，输入的是角色的属性，输入构成成 $T * P$ 的形式。 $T$ 为时间， $P$ 为对应的角色属性。

![](https://pic4.zhimg.com/v2-de4be7fc9b59179f26be9fc509bee5e1_1440w.png)

## 4.4 Data Source Attention Network

最后把三种输出进行拼接：

![](https://pic2.zhimg.com/v2-330a31386723ee82e6c4c0f06bbf351d_1440w.jpg)

其中 $ns$ 、 $vc$ 、 $va$ 分别对应于三个网络结构的输出，然后再引入一次attention mechanism形成三个不同的注意力权重 $a$ ，最后把注意力权重进行拼接得到 $\vec{V_{ds}}$

然后利用交叉熵进行输出：

![](https://pic3.zhimg.com/v2-3e33e7345dfcd134c975b2d8f396ba9e_1440w.jpg)

## 5.模型效果

## 5.1 baseline 对比

![](https://pic4.zhimg.com/v2-c53ebe0dee6fe1aab81c887e93f557d9_1440w.jpg)

该模型在三个不同的RMTers识别中，都达到了最优的效果。

## 5.2 注意力权重的内在本质

论文中展示了几个attention中的权重：

![](https://pic3.zhimg.com/v2-a0aa8d611de098e1b8d8997a862fec4e_1440w.jpg)

- 图a中展示：虚拟金钱Virtual money这个特征可以有效挖掘RMTers
- 图b中展示Quest：金币农获得更多的物品（item）和杀更多怪兽（monster）
- 图b中展示Event：PVE这个事件在金农中比较明显

下图为在图网络中的权重注意力矩阵：

![](https://pic4.zhimg.com/v2-569f59ad6e5724172e1355e05c51ea3b_1440w.jpg)

它们分别有以下几个高相关性的特征： - 金农；设备共享 - 金币商：聊天关系 - 金币购买者：朋友关系