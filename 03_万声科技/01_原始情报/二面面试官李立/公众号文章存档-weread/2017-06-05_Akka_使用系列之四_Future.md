# Akka 使用系列之四: Future

- 链接: https://mp.weixin.qq.com/s/E_I662b1JIbGNwFY_1XkeQ
- 发布: 2017-06-05
- 来源: 微信公众号 AlgorithmDog

---

这篇文章介绍 Akka 的同步机制，以及 Spark 和 Akka 的恩怨情仇。

![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMib9vUyOLqTYQ6oUswibiae9Sqmvr06OokdhdwZm9ULyBzVfVR9e87AWyNheGlO41FKHlC3FZOBtTXg/0?wx_fmt=png)

1 Akka 中的 Future

       Akka 中的 Actor 发送和接收消息默认都是异步的。为了说明异步性，我们实行下面的数学老师和历史老师的 Actor:

class MathTeacherActor extends Actor with ActorLogging {
    def receive = {
        case "1+1等于多少?"           => {
        Thread.sleep(1)
        sender ! "1+1等于2"
        }
    }
}
class HistoryTeacherActor extends Actor with ActorLogging {
    def receive = {
        case "历史上规模最大的众筹行动是什么？" => {
            Thread.sleep(1)
            sender ! "历史上规模最大的众筹行动是 +1s"
        }
    }
}

如果我们在询问历史老师之后访问答案(如下面代码所示)，我们发现并不能获取正确答案。原因就在于 Akka 是异步非阻塞的。

val res = historyteacher ? "历史上规模最大的众筹行动是什么？"
println(res)

      实质上, historyteacher ? "历史上规模最大的众筹行动是什么？" 返回的根本不是答案，而是一个 Future。在Akka中, 一个Future是用来获取某个并发操作的结果的数据结构。有了 Future,我们可以以同步（阻塞）或异步（非阻塞）的方式访问结果。下面是简单地以同步（阻塞）方式访问结果的示例。

class StudentActor(mathteacher:ActorRef,historyteacher:ActorRef)
 extends Actor with ActorLogging{
  def receive = {
    case res:String => {
        val future1 = historyteacher ? "历史上规模最大的众筹行动是什么？"
        val future2 = mathteacher ? "1+1等于多少?"
        val res1    = Await.result(future1,10 second)
        val res2    = Await.result(future2,10 second)
        println(res1)
        println(res2)
    }
 }
}

2 Akka 和 Spark

      Spark 一开始使用 Akka 作为内部通信部件。在 Spark 1.3 年代，为了解决大块数据（如Shuffle）的传输问题，Spark引入了Netty通信框架。到了 Spark 1.6, Spark 可以配置使用 Akka 或者 Netty 了，这意味着 Netty 可以完全替代 Akka 了。再到 Spark 2, Spark 已经完全抛弃 Akka 了，全部使用 Netty 了。Sad。

![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMib9vUyOLqTYQ6oUswibiae9SGuxlfUlOxiaia7DnEP86Hg8D79MdmaHDAL1LA8ywHaNnJGCtVnPFlxHg/0?wx_fmt=png)

      为什么 Spark 无情地有步骤有预谋地抛弃 Akka 呢？Spark 官方倒是给了一个说法:https://issues.apache.org/jira/browse/SPARK-5293。

A lot of Spark user applications are using (or want to use) Akka. Akka as a whole can contribute great architectural simplicity and uniformity. However, because Spark depends on Akka, it is not possible for users to rely on different versions, and we have received many requests in the past asking for help about this specific issue. For example, Spark Streaming might be used as the receiver of Akka messages - but our dependency on Akka requires the upstream Akka actors to also use the identical version of Akka.

Since our usage of Akka is limited (mainly for RPC and single-threaded event loop), we can replace it with alternative RPC implementations and a common event loop in Spark.

大意就是很多 Spark 用户希望同时使用 Spark 和 Akka ，但他们必须使用 Spark 依赖的那个版本的 Akka。Spark 主要用了 Akka 的 RPC 和 单线程 event-loop，因此 Spark 没有必要依赖完全的 Akka。最终，对 Akka 心心念念的 Spark 用 netty 实现下简易版本的 Akka。真爱啊。

3 总结

      到这里，Akka 使用系列就结束了。这个系列简单地过了一下 Akka 的基础知识，介绍其梗概。如果需要深入了解，还需要详细阅读官方文档。完整代码已经上传至 Github (https://github.com/algorithmdog/AkkaUsageLearner)。

      最近在 GitHub 上开发了一个 Side Project - RoomAI (https://github.com/roomai/RoomAI)。这个 Side Project 目标是提供一些非完整信息游戏环境，让算法人员开发非完整信息游戏 AI, 目前已经支持德州和梭哈。欢迎大家使用和反馈。我后续会基于这个项目写一些文章介绍非完整信息游戏的算法。

![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMib9vUyOLqTYQ6oUswibiae9S6VVVy8CUXKYCZowCwdk4Vmb6Ww0U9V8dicFvEW3lwrXNQicuX8kBdnEQ/0?wx_fmt=png)

Akka 使用系列之一: 快速入门

Akka 使用系列之二: 测试

Akka 使用系列之三: 层次结构和容错机制

预览时标签不可点

 Read more

Scan to Follow

 Got It

 Scan with Weixin to 
use this Mini Program

 Cancel
 Allow

 Cancel
 Allow

 Cancel
 Allow

 ×
 分析

![](http://mmbiz.qpic.cn/mmbiz_png/AVt8qQ587W9b7ZXDWm3wtwn8N2k5wDkxSuZLLhibf8BiaWG4ibdEjFB2xzLHH8vSuODG7xvVyqan0f7VY3QS6dSkHSzibTtZOzicKASp2Ubeypgs/0?wx_fmt=png)

微信扫一扫可打开此内容，
使用完整服务

 <script