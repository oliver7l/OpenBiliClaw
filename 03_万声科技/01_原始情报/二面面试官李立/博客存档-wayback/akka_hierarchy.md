# Akka 使用系列之三: 层次结构 | AlgorithmDog

> 原文: http://www.algorithmdog.com/akka-hierarchy
> 快照: 20170228070842

Akka 是用层次结构组织 Actors 的。

      

      我们需要实现一个翻译模块，其功能是输入中文输出多国语言。我们可以让一个 Master Actor 负责接收外界输入，多个 Worker Actor 负责将输入翻译成特定语言，Master Actor 和 Worker Actor 之间是上下级层次关系。下图展示了这种层级结构。

具体代码实现如下所示。

```

class Master extends Actor with ActorLogging{
 val english2chinese 
 = context.actorOf(Props[English2Chinese],"English2Chinese")
 val english2cat 
 = context.actorOf(Props[English2Cat],"English2Cat")

 def receive = {
 case eng1:String =>{
 english2chinese ! eng1
 english2cat ! eng1
 }
 }
 
}

class English2Chinese extends Actor with ActorLogging{
 def receive = {
 case eng:String => {
 println("我翻译不出来!")
 }
 }
}

class English2Cat extends Actor with ActorLogging{
 def receive = {
 case eng:String =>{
 println( "喵喵喵!")
 }
 }
}

object Main{
 def main(args:Array[String]):Unit = {
 val sys = ActorSystem("system")
 val master = sys.actorOf(Props[Master],"Master")
 master ! "Hello,world!"
 }
}

```

我们在 Master Actor 中使用 context.actorOf 实例化 English2Chinese 和 English2Cat，便可以在它们之间形成层次关系。这点通过它们的 actor 地址得到证实。

      

      上面的 Actors 层次结构是我们程序里 Actor 的层次结构。这个层次结构是 Actor System 层次结构的一部分。Actor System 层次结构从根节点出来有两个子节点：UserGuardian 和 SystemGuardian。用户程序产生的所有 Actor 都在 UserGuardian 节点下，SystemGuardian 节点则包含系统中的一些 Actor，比如 deadLetterListener。如果一个 Actor 已经 stop 了，发送给这个 Actor 的消息就会被转送到 deadLetterListener。因此完整的 Actor 层次结构如下所示。

      

       我们使用 Akka 开发并行程序时，可以使用层级结构组织 Actors。层次结构不仅比较符合人类直觉，还为容错提供了机制保障。我们将会在下一篇文章介绍容错机制。本文的所有代码已经上传到 GitHub 。欢迎关注 AlgorithmDog 公众号，每两周的更新会有推送哦。

      

### Akka 系列系列文章

- Akka 使用系列之一: 快速入门

- Akka 使用系列之二: 测试

- Akka 使用系列之三: 层次结构

 

 
 此条目发表在编程开发分类目录，贴了Akka标签。将固定链接加入收藏夹。 

 

 
 ← 动态图计算：Tensorflow 第一次清晰地在设计理念上领先

 

 

 

 

 
 
 

 
 

 

 

 
 

- 
 
 搜索：
 
 
 

 
- 

### 每周日更新，不关注下么？

 

 
- 

### 分类目录

 
 - 大局洞察 (2)

 - 数学基础 (7)

 - 假设检验 (3)

 - 算法荟萃 (28)

 - 强化学习 (7)

 - 游戏人工智能 (3)

 - 遗传算法 (5)

 - 编程开发 (10)

 

 - 

### 近期文章

 
 - 
 Akka 使用系列之三: 层次结构
 

 - 
 动态图计算：Tensorflow 第一次清晰地在设计理念上领先
 

 - 
 广告和推荐系统部署机器学习模型的两种架构
 

 - 
 Akka 使用系列之二: 测试
 

 - 
 Akka 使用系列之一: 快速入门
 

 - 
 不平衡数据的数据处理方法
 

 - 
 游戏智能系列之三:有限状态自动机
 

 - 
 如果人工智能泡沫破灭了
 

 - 
 在 Spark 中实现单例模式的技巧
 

 - 
 游戏智能系列之二:再次进行准备
 

 - 
 强化学习系列之九:Deep Q Network (DQN)
 

 - 
 游戏智能系列之一:一些准备工作
 

 - 
 Metropolis-Hastings 和 Gibbs sampling
 

 - 
 超快的 fastText
 

 - 
 强化学习系列之六:策略梯度
 

 
 
 - 

### 标签云

Actor
Actor 模型
Akka
Akka-testkit
AlphaGo
clash
CNN
DQN
EM
Gibbs sampling
Javascript
k-means
left-pad
mapreduce
Metropolis-Hasting
npm
Spark
Tensorflow
不平衡
人工智能
假设检验
典型关联分析
分类
前端
单例模式
单元测试
后端
强化学习
文本分类
有限状态机
框架
概率
泛化
泡沫
深度学习
深度学习框架
游戏
词嵌入
贝叶斯
遗传算法
采样算法

- 

### 近期评论

- 

### 访问图谱

 

 
- 

### 友情链接

 
- 我爱计算机
- 小土刀
- wuli涛涛
- Dr Dragon

- 石三石

- isnowfy
- 五道口摩羯宅男
- chaozh


 
- 

### 功能

 
 - 登录

 - 文章RSS

 - 评论RSS

 - WordPress.org
 
 
- 








 
 
 

 
 
 
 
 

 
 

 
 
 AlgorithmDog 
 

 
 自豪地采用WordPress。
