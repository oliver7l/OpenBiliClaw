# XGBoost + LR 就是加特征而已

- 链接: https://mp.weixin.qq.com/s/IJpEXAw6dVs50nRGHq6g-w
- 发布: 2017-09-03
- 来源: 微信公众号 AlgorithmDog

---

LR (逻辑回归) 算法因其简单有效，成为工业界最常用的算法之一。但 LR 算法是线性模型，不能捕捉到非线性信息，需要大量特征工程找到特征组合。为了发现有效的特征组合，Facebook 在 2014年介绍了通过 GBDT （Gradient Boost Decision Tree）+ LR 的方案 [1] （XGBoost 是 GBDT 的后续发展）。随后 Kaggle 竞赛实践证明此思路的有效性 [2][3]。

1. XGBoost + LR 的原理

      XGBoost + LR 融合方式原理很简单。先用数据训练一个 XGBoost 模型，然后将训练数据中的实例给 XGBoost 模型得到实例的叶子节点，然后将叶子节点当做特征训练一个 LR 模型。XGBoost + LR 的结构如下所示。

![](https://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvPhO5ic9IwzauWSdAwDAxcWB6LWoIVKTqVXPCB1Y7lyOrCmlDvYK3G6ZZAMXGMkPcPEt5JYgTQPdog/0?wx_fmt=png)

      我第一接触到 XGBoost + LR 的时候，认为 XGBoost + LR 是尝试自动替代特征工程的方法。深度学习在 CTR 领域便是在讲述这样的故事和逻辑:只需人工对原始特征进行简单的变换，深度学习能取的比大量人工特征的 LR 好的效果。

2. XGBoost 叶子节点不能取代特征工程

      为了验证 XGBoost + LR 是尝试自动替代特征工程的方法，还只是一种特征工程的方法，我们在自己业务的数据上做了一些实验。下图便是实验结果，其中: “xgboost+lr1" 是 XGBoost 的叶子节点特征、原始属性特征和二阶交叉特征一起给 LR 进行训练；"xgboost+lr2" 则只有叶子节点特征给 LR；"lr1" 是原始属性特征和二阶交叉特征; "lr2" 只有原始属性特征。

![](https://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvPhO5ic9IwzauWSdAwDAxcWBb3zegHT8cE0z03jcOicnrsy437YQq428JewmrPHJ7iajpGVjDwa2Q8zA/0?wx_fmt=png)

从上面的实验来看：1) "xgboost+lr2" 明显弱于 "lr1" 方法，说明只用叶子节点特征的 XGBoost + LR 弱于有特征工程的 LR 算法。即 XGBoost 叶子节点不能取代特征工程，XGBoost + LR 无法取代传统的特征工程。2) "xgboost+lr1" 取得了所有方法中的最好效果，说明了保留原来的特征工程 XGBoost + LR 方法拥有比较好的效果。即 XGBoost 叶子节点特征是一种有效的特征，XGBoost + LR 是一种有效的特征工程手段。

      上面的实验结果和我同事二哥之前的实验结果一致。在他实验中没有进行二阶交叉的特征工程技巧，结果 XGBoost > XGBoost + LR > LR，其中 XGBoost +LR 类似我们的 "xgboost+lr2" 和 LR 类似于我们的 "lr2"。

3. 强大的 XGBoost

      只用 XGBoost 叶子节点特征， XGBoost + LR 接近或者弱于 XGBoost 。在下图中，我们发现 XGBoost 的每个叶子节点都有权重 w, 一个实例的预测值和这个实例落入的叶子节点的权重之和有关。

![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvPhO5ic9IwzauWSdAwDAxcWBvTZaUJtAuSfXdzQOmdeyp8voGflIRVUvO0Cs6n6vzxXXKXibiaQE3hNw/0?wx_fmt=png)

如果二分类 XGBoost 使用了 sgmoid 做激活函数, 即参数为 "binary:logistic", 则 XGBoost 的最终预测值等于 sgmoid(叶子节点的权重之和)。而 LR 的最终预测值等于 sgmoid (特征对应的权重之后)。因此 LR 只要学到叶子节点的权重，即可以将 XGBoost 模型复现出来。因此理论上，如果 LR 能学到更好的权重，即使只有叶子节点特征的 XGBoost + LR 效果应该好于 XGBoost。

      但是从上面的结果来看，XGBoost + LR 要接近或者弱于 XGBoost。XGBoost 赋予叶子节点的权重是很不错的，LR 学到的权重无法明显地超过它。

4. 总结

      XGBoost +　LR 在工业和竞赛实践中，都取得了不错的效果。但 XGBoost 的叶子节点不能完全替代人工特征， XGBoost + LR 并没有像深度学习那样试图带来自动特征工程的故事和逻辑。最终，XGBoost + LR 的格局没有超越特征工程。欢迎关注我的公众号。

![](https://mmbiz.qpic.cn/mmbiz/Q3H1TCddfvPE2JpYR7tQ2eqqCLpF5ncuTx6MNCib7Qd3FF67BsxicqGoLo1Y44rRGAah5gUfUrV6QTjIVaanBsDA/0?wx_fmt=png)

[1].He X, Pan J, Jin O, et al. Practical lessons from predicting clicks on ads at facebook[C]. Proceedings of 20th ACM SIGKDD Conference on Knowledge Discovery and Data Mining. ACM, 2014: 1-9.
[2].http://www.csie.ntu.edu.tw/~r01922136/Kaggle-2014-criteo.pdf
[3].https://github.com/guestwalk/Kaggle-2014-criteo

预览时标签不可点

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