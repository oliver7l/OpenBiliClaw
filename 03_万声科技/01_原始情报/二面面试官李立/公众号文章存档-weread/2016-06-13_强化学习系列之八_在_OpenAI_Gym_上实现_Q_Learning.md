# 强化学习系列之八:在 OpenAI Gym 上实现 Q Learning

- 链接: https://mp.weixin.qq.com/s/c5wt14Ft0Xr3lH0g6nYO_w
- 发布: 2016-06-13
- 来源: 微信公众号 AlgorithmDog

---

这周我试用了下 OpenAI Gym。OpenAI Gym是一款用于研发和比较强化学习算法的工具包。强化学习和有监督学习的评测不一样。有监督学习的评测工具是数据。只要提供一批有标注的数据就能进行有监督学习的评测。强化学习的评测工具是环境。需要提供一个环境给 Agent 运行，才能评测 Agent 的策略的优劣。OpenAI Gym 是提供各种环境的开源工具包。

![](http://mmbiz.qpic.cn/mmbiz/Q3H1TCddfvMpLnhUs0FkFRgcb7KbZAWfyNeW6WuVWptSxe0Z7JbgL5A3ZoyPakyKiafYceaa9bP1ZUsQU9k7x1w/0?wx_fmt=png)

1. OpenAI Gym 的基本知识

      下面 OpenAI Gym 是一个示例。

![](http://mmbiz.qpic.cn/mmbiz/Q3H1TCddfvNzwLk8sOCscfmkpEAMKk0P88fusKOQPcZyvwZibwE6p4AaDsibPXc3yV4TTM7EVRBiaaurp6v8hrajQ/0?wx_fmt=png)

      OpenAI Gym 的最重要的功能就是提供各种强化学习环境。上面的代码 env = gym.make('CartPole-v0') 是实例化一个 CartPole 环境。CartPole 环境要求平衡一辆车上的一根棍子，如下图的第一个环境表示。下图是 OpenAI Gym 提供的部分自动控制方面的环境。除此之外 OpenAI Gym 还提供了算法、文本和游戏方面的环境，具体可以查看 https://gym.openai.com/envs。

![](http://mmbiz.qpic.cn/mmbiz/Q3H1TCddfvNzwLk8sOCscfmkpEAMKk0PFSib89ficn0HwFUomro82QMZZV1HJPnbVcyOvgicvYOrKuyDGUv20iaKhw/0?wx_fmt=png)

      强化学习环境其实是马尔科夫决策过程，马尔科夫决策过程的四个基本元素：状态、动作、转移概率和奖励函数。

1.1 状态

      代码中的 observation 就是马尔科夫决策过程的状态。更正确地说是，状态的特征。CartPole-v0 的状态特征是一维数组，比如array([-0.01377819, -0.01291427, 0.02268009, -0.0380999 ])。有些环境提供的状态特征是二维数组，比如 AirRaid-ram-v0 环境提供的是二维数组表示的游戏画面。

      observation = env.reset() 是初始化环境，设置一个随机或者固定的初始状态。env.step(a1) 是环境接受动作 a1，返回的第一个结果是接受动作 a1 之后的状态特征。

1.2 动作

      代码中的 action 就是马尔科夫决策过程中的动作。CartPole-v0 的动作是离散型特征。在 OpenAI Gym 中，离散型动作是用从 0 开始的整数集合表示，比如 CartPole-v0 的动作有 0 和 1。另一种动作是连续型，用实数表示。

1.3 转移概率和奖励函数

      在 OpenAI Gym 中，转移概率并没有显式表示出来，而是通过 env.step(a1) 的结果表示。env.step(a1) 返回的 observation 满足转移概率。

    代码中的 reward 就是马尔科夫决策过程中的动作，用实数表示。在 OpenAI Gym 中，奖励函数也没有显式表示出来，也是通过 env.step(a1) 的结果表示。env.step(a1) 返回的 reward 满足奖励函数。

      值得一提的是，env.step 返回的第四个结果 info 是系统信息，给开发人员调试用，不允许学习过程使用。本文只介绍在 OpenAI Gym 上实现 Q Learning 算法需要的知识。想了解更多 OpenAI Gym 知识，可以参考 https://gym.openai.com/docs

2. 实验Q Learning 算法

      我们在 OpenAI Gym 的 CartPole-v0 环境上实现 Q Learning 算法。Q Learning 目标是学习状态动作价值。QLearning 让 Agent 按照策略进行探索，在探索每一步都进行状态价值的更新，更新公式如下。

![](http://mmbiz.qpic.cn/mmbiz/Q3H1TCddfvNzwLk8sOCscfmkpEAMKk0PgDH9UFltWwtm5cdFPnujT4toicVOQesAmPJpuk33s70mptmCFMdDtfw/0?wx_fmt=png)

由于 OpenAI Gym 提供的状态特征，因此我们要用价值函数近似，参数更新的代码如下所示。

![](http://mmbiz.qpic.cn/mmbiz/Q3H1TCddfvNzwLk8sOCscfmkpEAMKk0P7aKvjynDcBiaLnN6SZX3VuDx7iaRTib5guoWKTWibfAy5l2wV3Sjia0OKIw/0?wx_fmt=png)

Q Learning 的代码如下。

![](http://mmbiz.qpic.cn/mmbiz/Q3H1TCddfvNzwLk8sOCscfmkpEAMKk0PXL4sgluOSNz34MsAbBnDaX4NdyGTLeeB5PnbOGk5OqpQWa5B0DL1Ig/0?wx_fmt=png)

      想了解更多代码，可以参见Github。实现的 Q Learning 算法的效果如下。

3. 总结

      结果好烂啊。基本的强化学习算法还是无法解决 OpenAI Gym 里面的问题啊。本文的代码可以在 https://github.com/algorithmdog/Reinforcement_Learning_Blog/tree/master/8.强化学习系列之八:OpenAi_Gym_试用 上找到，欢迎有兴趣的同学帮我挑挑毛病。

      现在毕业季准备好好玩下，公众号停更到8月份，希望大家见谅。欢迎关注 AlgorithmDog。

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