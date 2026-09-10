# qqvideohot_ltrerank_v1离线任务调度
> 来源：01_原始资料库/03_工作资料/工作资料_腾讯/2020年09月-内部资料/小世界/qqvideohot_ltrerank_v1离线任务调度.doc ｜ 828字

## 一句话定位
腾讯微视项目中用于训练推荐系统的离线任务调度方案。

## 核心内容摘要
该文档描述了从日志采集、样本拼接至模型训练的全流程。通过Spark Scala进行日志处理，TensorFlow实现深度学习模型训练，并在Venus调度平台上执行相关任务。

## 关键方法 · 模型 · 指标
- 数据处理：使用Spark Scala编写脚本处理曝光、播放及行为价值日志。
- 样本拼接：通过5分钟周期内join 15分钟前的日志数据生成样本集。
- 模型训练：采用TensorFlow实现CriticNet和ActorNet模型进行深度学习训练。

## 与推荐/广告/数据科学面试的关联
- 数据处理能力：展示如何使用Spark Scala处理大规模日志数据。
- 深度学习应用：涉及CriticNet和ActorNet模型，适用于讨论强化学习在推荐系统中的应用。
- 离线任务调度：可谈及Venus平台的任务管理经验。

## 关键术语
- Spark Scala
- Venu 调度画布
- StreamingSaveAll3WithUid.scala
- MakeSamples.scala
- qqvideohot_ltrerank_tfv100.py
- CriticNet
- ActorNet

## 与本人项目的关联
无明显关联。资料主要涉及腾讯微视项目，而候选人曾参与过多个不同公司的推荐及广告项目。

## 价值评估
中 —— 资料提供了处理大规模日志数据和使用TensorFlow进行模型训练的具体案例，对于理解离线任务调度流程有一定帮助。
