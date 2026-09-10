# Plato调研
> 来源：01_原始资料库/03_工作资料/工作资料_腾讯/2020年09月-内部资料/看点搜索/搜索推荐组/Plato调研.doc ｜ 10496字

## 一句话定位
图embedding框架Plato的使用与调参调研。

## 核心内容摘要
该文档详细记录了使用Plato进行图embedding的研究，包括deepwalk、node2vec等算法的应用，以及针对不同维度和参数的实验效果。通过充分热词打压和调整embedding维度及参数，优化推荐效果。

## 关键方法 · 模型 · 指标
- 图embedding框架：Plato
- 算法：deepwalk, node2vec, line, metapath2vec
- 方法与调参：
  - 随机游走（将图节点数据转化成序列数据）
  - 训练（将序列数据训练成向量数据）
  - faiss召回
- 参数调整：embedding维度从128降低到64维，node2vec的p和q值调整

## 与推荐/广告/数据科学面试的关联
- 推荐系统中的图embedding技术
- 深度学习在推荐系统的应用
- 算法调参技巧及经验分享
- faiss召回机制的理解与优化

## 关键术语
- Plato
- deepwalk, node2vec, line, metapath2vec
- 随机游走
- 训练
- faiss召回
- embedding维度
- 参数p和q值调整

## 与本人项目的关联
无明显关联。资料中的项目主要涉及腾讯微视/QQ看点图集/视频号等，而候选人的项目则包括OPPO(国内+海外)/百度/中信信用卡的推荐与广告项目。

## 价值评估
中 —— 资料提供了图embedding框架的具体使用案例和调参经验，对于理解算法在实际项目中的应用有一定帮助。
