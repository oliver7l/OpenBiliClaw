# 强化学习系列之九:Deep Q Network (DQN)

- 链接: https://mp.weixin.qq.com/s/EGzj67FkeqUhRD0QcF2HFA
- 发布: 2016-09-11
- 来源: 微信公众号 AlgorithmDog

---

我们终于来到了深度强化学习。

![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvPqOU7lTxHiavGqFpLhWEbUheodPu55vXXQibaDLsibxeibwr6X07YAej3JbkQicWYyw87hjHz5Gn6oegA/0?wx_fmt=png)

1. 强化学习和深度学习结合

机器学习=目标+表示+优化。目标层面的工作关心应该学习到什么样的模型，强化学习应该学习到使得激励函数最大的模型。表示方面的工作关心数据表示成什么样有利于学习，深度学习是最近几年兴起的表示方法，在图像和语音的表示方面有很好的效果。深度强化学习则是两者结合在一起，深度学习负责表示马尔科夫决策过程的状态，强化学习负责把控学习方向。

      深度强化学习有三条线：分别是基于价值的深度强化学习，基于策略的深度强化学习和基于模型的深度强化学习。这三种不同类型的深度强化学习用深度神经网络替代了强化学习的不同部件。基于价值的深度强化学习本质上是一个 Q Learning 算法，目标是估计最优策略的 Q 值。 不同的地方在于 Q Learning 中价值函数近似用了深度神经网络。比如 DQN 在 Atari 游戏任务中，输入是 Atari 的游戏画面，因此使用适合图像处理的卷积神经网络（Convolutional Neural Network，CNN）。下图就是 DQN 的框架图。

![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvPqOU7lTxHiavGqFpLhWEbUhIfUBaxwpqpYyqf25iaMWWfMzG1CYquIVmJ8cw9X9M0xq2KzWdp4xENA/0?wx_fmt=png)

2. Deep Q Network (DQN) 算法

      当然了基于价值的深度强化学习不仅仅是把 Q Learning 中的价值函数用深度神经网络近似，还做了其他改进。

      这个算法就是著名的 DQN 算法，由 DeepMind 在 2013 年在 NIPS 提出。DQN 算法的主要做法是 Experience Replay，其将系统探索环境得到的数据储存起来，然后随机采样样本更新深度神经网络的参数。

![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvPqOU7lTxHiavGqFpLhWEbUhIwg2whD3AShmqIIW9X38EMq5gSVMQ7Ey93KLvJoNQnUZCGKBEK11Hw/0?wx_fmt=png)

      Experience Replay 的动机是：1）深度神经网络作为有监督学习模型，要求数据满足独立同分布，2）但 Q Learning 算法得到的样本前后是有关系的。为了打破数据之间的关联性，Experience Replay 方法通过存储-采样的方法将这个关联性打破了。

      DeepMind 在 2015 年初在 Nature 上发布了文章，引入了 Target Q 的概念，进一步打破数据关联性。Target Q 的概念是用旧的深度神经网络 w− 去得到目标值，下面是带有 Target Q 的 Q Learning 的优化目标。

![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvPqOU7lTxHiavGqFpLhWEbUhR9V2FDlmLwTLbIj8bJLnzZxPcdFhPNh7KIbibykkT4BWWhkbXfulShw/0?wx_fmt=png)

      下图是 Nature 论文上的结果。可以看到，打破数据关联性确实很大程度地提高了效果。

![](http://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvPqOU7lTxHiavGqFpLhWEbUhH9ET4wpMjKiaLfUl0K35wBRL3yPj6y1Xe9udIVyiaOrjf8BzYZdjOKhg/0?wx_fmt=png)

3. 总结

      本来想把基于价值的深度强化学习的 Double DQN, Prioritised replay 和 Duelling network 也写了，但就这点东西写的晚上 2 点。先这样吧，中秋会补上。

      欢迎关注 AlgorithmDog~

![](http://mmbiz.qpic.cn/mmbiz/Q3H1TCddfvPE2JpYR7tQ2eqqCLpF5ncuTx6MNCib7Qd3FF67BsxicqGoLo1Y44rRGAah5gUfUrV6QTjIVaanBsDA/0?wx_fmt=png)

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