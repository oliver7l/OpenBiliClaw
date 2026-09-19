# Metropolis-Hastings 和 Gibbs sampling

- 链接: https://mp.weixin.qq.com/s/gKGjSuK6TtD6hPq4Lw1Q7A
- 发布: 2016-08-22
- 来源: 微信公众号 AlgorithmDog

---

在科学研究中，如何生成服从某个概率分布的样本是一个重要的问题。 如果样本维度很低，只有一两维，我们可以用反切法、拒绝采样和重要性采样等方法。 但是对于高维样本，这些方法就不适用了。这时我们就要使用一些 “高档” 的算法，比如下面要介绍的 Metropolis-Hasting 算法和 Gibbs sampling 算法。

      Metropolis-Hasting 算法和 Gibbs sampling 算法是马尔科夫链蒙特卡洛（Markov Chain Mento Carlo, MCMC）方法。我们先介绍 MCMC 方法。

1. 马尔科夫蒙特卡洛方法

      MCMC 方法是用蒙特卡洛方法去体现马尔科夫链的方法。马尔科夫链是状态空间的转换关系，下一个状态只和当前的状态有关。比如下图就是一个马尔科夫链的示意图。

![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2PJ7xEox3abqiaVGHGhzZuqibX27ztnRsUSgCJb3TpPI7FJUaJH9dhXuuw/0?wx_fmt=png)

图中转移关系可以用一个概率转换矩阵 p 表示，

![](http://mmsns.qpic.cn/mmsns/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2PtttDiabxEul303xSfSytZsw/0)

如果当前状态分布为 
![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2PB0tDRBBGcfHsF1Sv5vocqUaS7qxLwDz5RvCbxaGGhIgqYqjwMIFlVA/0?wx_fmt=png)
, 那么下一个矩阵的状态就是 
![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2PIQSJ6MxZHAzwPnqb3A1hDTylXbibzVfGhhPY6coq40VnAASWS6Dn1zQ/0?wx_fmt=png)
, 再下一个就是
![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2PAbzb167XcmZdBiaibcibUQseJ9ricmUQwy4AWHiaPweIG9H7EviaW1NhhyiaQ/0?wx_fmt=png)
,... 最后会收敛到一个平稳分布 
![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2PUp9VSzEw0rtfu0FticmqRVRyTauEDCVqAuZdGNs2j5I1atEZ62bolZg/0?wx_fmt=png)
。这个平稳分布 
![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2PUp9VSzEw0rtfu0FticmqRVRyTauEDCVqAuZdGNs2j5I1atEZ62bolZg/0?wx_fmt=png)
 只和概率转移矩阵 p 有关，而和初始状态分布 u 是什么没有关系。

      如何判断一个马尔科夫链是否能收敛到平稳分布，以及如何判断一个状态分布是不是一个马尔科夫链的平稳分布呢？我们有下面定理。

细致平衡条件: 已知各态历经的的马尔科夫链有概率转移矩阵 
![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2PuyZMa407NoQz4x0RIoZWOicEEIXdt2uEXqOqWEaLIYAGx6NwIaIGo1A/0?wx_fmt=png)
，以及已知状态分布 
![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2PUp9VSzEw0rtfu0FticmqRVRyTauEDCVqAuZdGNs2j5I1atEZ62bolZg/0?wx_fmt=png)
。如果对于任意两个状态 i 和 j，下面公式成立，则马尔科夫链能够收敛到 
![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2PUp9VSzEw0rtfu0FticmqRVRyTauEDCVqAuZdGNs2j5I1atEZ62bolZg/0?wx_fmt=png)
。

![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2PTGxibmz9kKvY0mDeNOEMiaxIm8icKlkTQ0fYlBXjfEoXHZ5Ud4UVxndYA/0?wx_fmt=png)

这里的各态历经是指任意两个状态之间可以通过有限步到达。

怎么证明细致平衡条件呢？我也不知道啊。

      MCMC 方法的基本原理是利用细致平衡条件构建一个概率转移矩阵，使得目标概率就是概率转移矩阵的平稳分布。 Metropolis-Hasting 和 Gibbs sampling 算法本质上是构建概率转移矩阵的不同方法。

2. Metropolis-Hastings 算法

      Metropolis-Hastings 算法先提出一个可能不符合条件的概率转移矩阵 q, 然后再进行调整。比如我们提出的 q 是均匀概率，即从任意状态到任意状态的概率是相等的。显然在绝大部分情况下，q 的稳定概率不是目标概率 
![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2PUp9VSzEw0rtfu0FticmqRVRyTauEDCVqAuZdGNs2j5I1atEZ62bolZg/0?wx_fmt=png)
，即不满足细致平衡条件。

![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2P7TCruVxEvmuHfiaDOAtrIMB96Foc9Peib6slUqxyfXjejSzph2QpfsdQ/0?wx_fmt=png)

如何让这个不等式转变成等式呢？根据对称性，我们容易得到下面的等式。

![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2PX9zhFLOl1872icLY8gQEjewQ6YMoDia9P6byuBdyK4RujuCNl9AWuVdQ/0?wx_fmt=png)

这时整个概率转移矩阵满足细致平衡条件。从 i 状态转到 j 状态的概率是 
![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2PX19FwUjljh3w6NasagIH8UroibW4IwFjbmbAcxh1KzuEyyHlfCCK5RQ/0?wx_fmt=png)
，实现这个转移概率的方式是 i 状态以 q(j|i) 概率跳转到 j 状态，然后以 
![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2PycM9ENLgicia9up8ch0u2zEjQhdtQkqKNV5traq3XtDXFibTia9rQpT5kA/0?wx_fmt=png)
 接受跳转 (拒绝跳转就退回 i 状态)。这样整个 Metropolis-Hasting 算法的框架就建立起来了。

      这个原始的 Metropoli-Hasting 算法的有一个小问题。 跳转接受概率 
![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2PycM9ENLgicia9up8ch0u2zEjQhdtQkqKNV5traq3XtDXFibTia9rQpT5kA/0?wx_fmt=png)
 和 
![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2P0eFspbMkZuIvdsnGJgzBulXFzZ5OwFXNibT2SWI67pgxePsVDQm9b8A/0?wx_fmt=png)
的值很小，算法进行过程充斥着跳转拒绝。为了改进这点，Metropoli-Hasting 算法的方法是公式两边同时乘以一个系数，使得 
![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2PycM9ENLgicia9up8ch0u2zEjQhdtQkqKNV5traq3XtDXFibTia9rQpT5kA/0?wx_fmt=png)
 和 
![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2P0eFspbMkZuIvdsnGJgzBulXFzZ5OwFXNibT2SWI67pgxePsVDQm9b8A/0?wx_fmt=png)
 中大的一项 scale 到 1，得到下面的公式。

![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2P6scG5ETz5y4Jbxbt3yxd6X2apMFib3de7a37RJRXuibaTROsVKkoG4BQ/0?wx_fmt=png)

这个公式可以进一步简化为下面的公式

![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2PQa49GYHcdo6HTCYBJs4icX7CKJeQOrxtGd3P6kbRcUr7xqDtK64LAqw/0?wx_fmt=png)

      根据上面的推导，我们容易得到 Metropolis-Hasting 算法的流程。

![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2PJboa8qMTHRhsR56F2AWnCGO4uTvwJHAoQ6rIGraPt8CC7P6RItZR1w/0?wx_fmt=png)

3. Gibbs sampling 算法

      Gibbs sampling 算法是 Metropolis-Hasting 算法的一个特例。很鸡贼的一个特例。m 维的一个样本跳转到另一个样本的过程，可以拆解为 m 个子过程，每一个子过程对应一个维度。这时概率转移矩阵是 m 个子概率转移矩阵之积，即 
![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2PSNd3V0jnfOvnLmSm2LwAdvXkuZIhk6XugA3f11BicePPv6lBLQE92zQ/0?wx_fmt=png)

其中 
![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2PlNrXRjtfeP44wNicMentI9EL7VOvZ2JNx0Uu2s4T5KibknsC4E7BtfzQ/0?wx_fmt=png)
 表示第 k 维的变化概率。在 
![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2PlNrXRjtfeP44wNicMentI9EL7VOvZ2JNx0Uu2s4T5KibknsC4E7BtfzQ/0?wx_fmt=png)
 中，两个状态之间只有 k 维不同，其跳转概率如下所示；不然为 0。

![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2PWYo1yPHBXg84npqNh7rrdlXa65y3z638XdtVz0JHuW48ZtfPtxLeyA/0?wx_fmt=png)

其中 
![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2P80fIrQKWpTMOt40Lh0mZibjFRE0icXgfkcyibMgzVyA0u0Qy0EclWAqbQ/0?wx_fmt=png)
 表示样本第 k 维数据为 
![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2PTiav5R5q6pESu7CCWE3dzHII8oJR1vhEJWrOGkxrT0TIR0ZGm91InaA/0?wx_fmt=png)
，其它维度固定。这时候我们发现如下公式

![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2PslwiadiaN0Dt84GUzib9WatpBHs0rOhaAUEyQym8dpsSWW9qe92NOvFVA/0?wx_fmt=png)

即 
![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2PlNrXRjtfeP44wNicMentI9EL7VOvZ2JNx0Uu2s4T5KibknsC4E7BtfzQ/0?wx_fmt=png)
 和  
![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2PUp9VSzEw0rtfu0FticmqRVRyTauEDCVqAuZdGNs2j5I1atEZ62bolZg/0?wx_fmt=png)
 满足细致平衡条件的等式。那么 
![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2PlNrXRjtfeP44wNicMentI9EL7VOvZ2JNx0Uu2s4T5KibknsC4E7BtfzQ/0?wx_fmt=png)
 就是我们要构建的概率转移矩阵嘛？答案是否定的。因为完整的细致平衡条件需要各态历经。在概率转移矩阵 
![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2PlNrXRjtfeP44wNicMentI9EL7VOvZ2JNx0Uu2s4T5KibknsC4E7BtfzQ/0?wx_fmt=png)
 下, 只有 k 维数据子啊变化，因此一个状态永远不能到达和它第 k-1 维数据不同的状态。

      最终我们构建的概率转移矩阵是 m 个子概率转移矩阵之积

(9) 
![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2PwnQwviaD5ubjTXvZBf1WK5xriclBFn3S8gAOt2DALw4yNI5vYzsLtSSA/0?wx_fmt=png)

我们很容易证明 
![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2PuyZMa407NoQz4x0RIoZWOicEEIXdt2uEXqOqWEaLIYAGx6NwIaIGo1A/0?wx_fmt=png)
 依然满足细致平衡条件中的等式，同时还满足各态历经。根据上述推导，我们得到 Gibbs sampling 的算法过程。

![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMKdLNCEn6EVEdHloGmtD2P1QMwH8sibyqVI6oCWsGXd5F7arGYqrQfjpNXzdeqicwic8aAwTYQADrYg/0?wx_fmt=png)

      欢迎关注 AlgorithmDog

![](http://mmbiz.qpic.cn/mmbiz/Q3H1TCddfvPE2JpYR7tQ2eqqCLpF5ncuTx6MNCib7Qd3FF67BsxicqGoLo1Y44rRGAah5gUfUrV6QTjIVaanBsDA/0?wx_fmt=png)

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