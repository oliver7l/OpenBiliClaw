# Facebook的 Prophet 算法简介与使用

- 链接: https://mp.weixin.qq.com/s/pJTDJrMCfv5y4LQ2itt1tQ
- 发布: 2019-04-24
- 来源: 微信公众号 AlgorithmDog

---

中心 xueminsi 给大家带来的 Prophet 算法的简介和使用。

Prophet是Facebook开源的一款针对时间序列基于Python和R语言的数据预测工具，其工作发表在论文《Forecasting at scale》(Taylor S J, LethamB. Forecasting at scale[J]. The American Statistician, 2018, 72(1): 37-45)。这里的“at scale”并不是常说的数据存储和计算复杂度，而是指数据的复杂度（如数据中存在大量异常点）和预测结果的评估复杂度。用于预测的时间任务常具有以下特征：

对于历史在至少几个月（最好是一年）的每小时、每天或每周的观察

强大的多次的「人类规模级」的季节性：每周的一些天和每年的一些时候

事先知道的以不定期的间隔发生的重要节假日（如，双十一）

合理数量的缺失的观察或大量异常

历史趋势改变，比如因为产品发布或记录变化

非线性增长曲线的趋势，其中有的趋势达到了自然极限或饱和

Prophet算法的工作流如下所示：

![](https://mmbiz.qpic.cn/mmbiz_png/8trK3Rr2ExsbManrjN6flmx6kwuE9jJ6FamQ0ratwlLnK3ofZ9jUQYFA03Vt0o6LdRltPNdIUnoawGiaplQ9R2g/640?wx_fmt=png)

Prophet算法工作流

由建模和模型评估两部分组成。其优点在于可以结合领域专家的专业知识，在训练过程中不断对模型进行改进。后面会有详细的讲解。

Prophet预测模型

Prophet使用的是加性模型（additive model），分别用不同的部分来拟合时间序列不同的趋势，叠加起来则是整个时间序列模型：

![](https://mmbiz.qpic.cn/mmbiz_png/8trK3Rr2ExsbManrjN6flmx6kwuE9jJ6iclRTlIlDHx8eSwl0nwf5N7hG2amtQciaV5OAYHaZ2tkwA3r1lLN8RtA/640?wx_fmt=png)

趋势函数

g(t)是趋势函数，表示时间序列上的非周期变化，在Prophet中有以下两种形式：

![](https://mmbiz.qpic.cn/mmbiz_png/8trK3Rr2ExsbManrjN6flmx6kwuE9jJ6NnmFpSibH7OvNJwlpSKib1J1wCZPldwCOMpSOtVrjEXdsaia131iaXPmqw/640?wx_fmt=png)
   (1)

![](https://mmbiz.qpic.cn/mmbiz_png/8trK3Rr2ExsbManrjN6flmx6kwuE9jJ6ImfboicV9UAbVKEo8hjHIGI6vz9OFEUG03egBnlAfLxUM91uJXBJSqQ/640?wx_fmt=png)
  (2)

式(1)用于拟合非线性变化的增长趋势，C(t)表示增长的上限，是t的函数，认为在不同时间段序列增长的上限可能是不同的。

式(1)和式(2)里的
![](https://mmbiz.qpic.cn/mmbiz_png/8trK3Rr2ExsbManrjN6flmx6kwuE9jJ61hcCZ5Fiabfpg23licsEcygF1iaUL2udjctqcoOpuIzlTFfmPRs1LLAKA/640?wx_fmt=png)
表示增长速率，
![](https://mmbiz.qpic.cn/mmbiz_png/8trK3Rr2ExsbManrjN6flmx6kwuE9jJ6cm5f2zNkJFYMicOYXoIoGFWIXIlOibqTDnMtAHAjMGD2LCCR4qIzPGcg/640?wx_fmt=png)
表示线性的偏移。a(t)是一个指示函数，取值要么是0要么是1. 引入a(t)的目的是考虑到时间序列中可能的changepoint，这些changepoint会对趋势函数有所影响，\delta 和 \gamma 表示changepoint对趋势函数的斜率和偏移量影响的大小。算法中默认
![](https://mmbiz.qpic.cn/mmbiz_png/8trK3Rr2Exvy8YFm6jUJ47Egfj5gibOZR1lm22lbOSGu99bibgibMqfxA4kJp1icWd5ZC7LhVkeWf0z1wvbjASvrVw/640?wx_fmt=png)
，由参数 \tau 控制模型改变斜率的灵活度。Prophet算法可以自动检测出这些changepoint，也可以由我们手动传入这些点。

周期（季节）函数

s(t)用于刻画时间序列中的周期性（季节性）变化，其形式为傅立叶级数，如下所示：

![](https://mmbiz.qpic.cn/mmbiz_png/8trK3Rr2Exvy8YFm6jUJ47Egfj5gibOZRdnekZRuu8hz1vQicsIbSjfgpLdMr82V4WYe9t9TZlr9PjFvzj1uAMpA/640?wx_fmt=png)

P是时间周期，当P=7时刻画的是以周为周期，P=365.25则是年。a_n和b_n是需要学习的参数。由傅立叶级数的性质可知，N越大越能刻画变化多的周期性模式，默认使用N=10刻画以年为单位的周期性变化，N=3刻画以周为单位的周期性变化。

节假日函数

h(t)即为holday，其用于拟合节假日和特殊日期，比如中国的双十一、美国的黑五等，函数形式为：

![](https://mmbiz.qpic.cn/mmbiz_png/8trK3Rr2Exvy8YFm6jUJ47Egfj5gibOZRejPEZaicFticmicRiaYCCXE5ughyFgHWGUZZPyBicJSoK6IlUiboRWPMtywg/640?wx_fmt=png)

![](https://mmbiz.qpic.cn/mmbiz_png/8trK3Rr2Exvy8YFm6jUJ47Egfj5gibOZR3iaMMZ7P9ymp95dBSFZ33lnNIo1pGBgSibazZz491icygAiaAcH5sPFmqA/640?wx_fmt=png)
表示holiday的时长，\kappa刻画在整个趋势的改变程度。

最后是
![](https://mmbiz.qpic.cn/mmbiz_png/8trK3Rr2Exvy8YFm6jUJ47Egfj5gibOZReSMIGEcAXYy7Bn4DqzfGuHwnHntBIeJqnfEL8xJdvr0dSrnmNBWTIw/640?wx_fmt=png)
，它是拟合那些无法用模型描述的异常点。

优化

模型训练的过程其实就是一个优化求解的过程，Prophet采用了L-BGFS的方式进行优化求得各个参数的最大后验估计。

模型回顾

针对文章一开始提到的模型适用情况，每种特征都可以由其拟合函数解释，如“强大的多次的「人类规模级」的季节性”可以由周期性函数s(t)体现，“事先知道的以不定期的间隔发生的重要节假日”则是通过函数h(t)刻画，引入的
![](https://mmbiz.qpic.cn/mmbiz_png/8trK3Rr2Exvy8YFm6jUJ47Egfj5gibOZReSMIGEcAXYy7Bn4DqzfGuHwnHntBIeJqnfEL8xJdvr0dSrnmNBWTIw/640?wx_fmt=png)
可以保证模型不会因离群点产生较大的误差等。

Prophet算法使用加性模型的好处是可以刻画时间序列的不同变化部分：整体走势、周期性变化、节假日影响和异常点。下面两张图分别展现了模型整体拟合和单独每个函数部分的情况：

![](https://mmbiz.qpic.cn/mmbiz_png/8trK3Rr2ExsbManrjN6flmx6kwuE9jJ61QfaPibvfcInl8Qw0SibUKtE6QibFW5NarjYqqmTSK6iaGdkK8nicjhnNKw/640?wx_fmt=png)

模型拟合与预测

![](https://mmbiz.qpic.cn/mmbiz_png/8trK3Rr2Exvy8YFm6jUJ47Egfj5gibOZR5WpYxJf43cxwBrIL2xqbXtLGTqRtysI2vqsZoibfbUUkm7VFkI0Ro4w/640?wx_fmt=png)

Prophet模型中的每一部分

而且，因为Prophet的参数较多，可调性较大，也可以加入较多的先验和领域知识，如我们可以手动加入模型走势的上限和changepoint、holiday等，这些改变点的影响也可以由参数调整。

Prophet使用

Prophet现在已经开源，Facebook提供了R和Python语言版，这里以Python语言为例，介绍下Prophet的基本使用方法。更加详细用法参考https://facebook.github.io/prophet/docs/quick_start.html 。

基本使用

首先导入所需要的包

from fbprophet import Prophetimport pandas as pd

读需要拟合的数据，并创建模型和拟合

df = pd.read_csv('../examples/example_wp_log_peyton_manning.csv') # read data# create model and fit datam = Prophet()m.fit(df)

Prophet是单特征模型，输入只有两列，日期列必须被称为「ds」，数值列被称为「y」，如下图所示：

![](https://mmbiz.qpic.cn/mmbiz_png/8trK3Rr2Exvy8YFm6jUJ47Egfj5gibOZRjy8zPDBxYn91MmibN2sym2Y6t4yzhwic8E9e0ERu1tWvpMHhoTIS3rXw/640?wx_fmt=png)

 Prophet算法输入示例

接着指定需要预测的日期

future = m.make_future_dataframe(periods=365)

默认是以天为单位，这里是创建了接下来365天的「ds」列，即365条日期输入。

接下来就可以直接预测了

forecast = m.predict(future)forecast[['ds', 'yhat', 'yhat_lower', 'yhat_upper']].tail()

其中yhat为预测的结果，yhat_lower和yhat_upper分别是预测的下界和上界，给出了一个置信区间，输出示例如下：

![](https://mmbiz.qpic.cn/mmbiz_png/8trK3Rr2Exvy8YFm6jUJ47Egfj5gibOZRNCTA9GHIpRn8f3yJApwCiaDQmialBgJRM6oFYiamCaK2AuvOAibzQOORHw/640?wx_fmt=png)

Prophet预测结果输出示例

此外，Prophet还提供了API，可以画出整体拟合的情况和单独的每个函数

fig1 = m.plot(forecast) fig2 = m.plot_components(forecast)

![](https://mmbiz.qpic.cn/mmbiz_png/8trK3Rr2ExsbManrjN6flmx6kwuE9jJ6Rrh5icGCsAIFPKfWMp24JNNSS3hzzsoAr2UiaOsgmIQO1fq3k8J9Gs4A/640?wx_fmt=png)

预测结果整体拟合

黑色的点是真实数据，深蓝色是拟合的走向，浅蓝色是置信区间。

![](https://mmbiz.qpic.cn/mmbiz_png/8trK3Rr2Exvy8YFm6jUJ47Egfj5gibOZR5UalSSfFJaZhFeXT1vSoTNtO5fn4CvFc9OcadfAovIYK8u5VI0Rd7A/640?wx_fmt=png)

预测结果的每个部分

SHF交叉验证与异常点删除

看了Prophet的论文和使用方法下来，我对Prophet这部分特性印象深刻，但看网上的一些博客和参考资料很少有提及这部分，所以特地展开介绍一下。

SHF全称是Simulated Historical Forecasts，是时间序列的一种交叉验证方法。是以历史数据作为训练集，然后取紧接着后面的一段时间为验证集，用以判断模型的好坏。用滑动窗口的方式不断推近训练数据和验证数据的时间序列。

from fbprophet.diagnostics import cross_validationdf_cv = cross_validation(m, initial='730 days', period='180 days', horizon = '365 days')

这里的initial是指从开始传入的时间序列到多久作为训练集，period指多久为一个cutoff，horizon为预测的时间段长度。这里为取第1-730为训练集，731~(731+365)天为验证集，看模型效果如何，然后往后推延cufoff天，即再取第(1+180)~(730+180)天为训练集，(731+180)~(731+180+365)天的数据为验证集，依次往后推，直到整个时间序列的最后一天。一般cutoff为horizon的一半：

![](https://mmbiz.qpic.cn/mmbiz_png/8trK3Rr2Exvy8YFm6jUJ47Egfj5gibOZRSuKLsJU47rNyTTK7mhdQqFXPkibS5Ciaq0ia1y7RLPicN1aAfw5KCohknQ/640?wx_fmt=png)

Prophet的cross validation

这样做的好处是能检测出历史数据是否存在异常点：

如果验证结果有较大偏差，可以手动调整模型趋势或者周期性；

某个时间段的结果有问题，可能有离群点，手动删除（离群点的删除不影响预测结果）；

某个时间段error大，下个时间段小，可能需要手动添加changepoints。

Prophet的另一个优点是对缺失点不敏感，如果人工发现一些异常点，那么可以直接将其删除，不影响训练过程，如下图所示，左图是有异常点的模型拟合结果，右图是删除异常点后的结果：

![](https://mmbiz.qpic.cn/mmbiz_png/8trK3Rr2Exvy8YFm6jUJ47Egfj5gibOZRYkGSULdM0WCE2s2ibJyXf9oM1JWO2EtiahdmRI8W3a5kUZpbTWHKnz6g/640?wx_fmt=png)

删除异常点前后的拟合结果

总结

Prophet算法原理简单，多参数便于加入领域知识和进行数据分析，适用于周期性和趋势性较强的数据。数据只有两列，其中只有一列是特征，对特征的要求比较高。

参考资料

[1] Taylor S J, Letham B. Forecasting at scale[J]. The American Statistician, 2018, 72(1): 37-45

[2] https://facebook.github.io/prophet/

[3] https://github.com/facebook/prophet

[4] https://medium.com/open-machine-learning-course/open-machine-learning-course-topic-9-part-3-predicting-the-future-with-facebook-prophet-3f3af145cdc

附：微信公众号不能打公式，大多公式都是以插图替代，还有部分特殊符号只能用latex形式表示，阅读不变，还请见谅。

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