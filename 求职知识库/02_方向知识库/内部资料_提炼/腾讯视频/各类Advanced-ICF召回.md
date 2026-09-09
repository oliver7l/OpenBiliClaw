# 各类Advanced-ICF召回 · 提炼笔记

> 来源：`01_原始资料库/03_工作资料/工作资料_腾讯/2020年09月-内部资料/腾讯视频/各类Advanced-ICF召回.doc` ｜ 1450字

## 一句话定位
关于通用推荐广告相关的技术资料（《各类Advanced-ICF召回》），含 16 条关键要点。

## 核心内容摘要
实验桶(2772, ctr重排) vs base(2773，原始fasttext)，录得vv提升1.8%，时长提升1.3%，目前已经全量，日曝光量6亿次以上；中低频item通过共享的subword互相可以“交流信息”，而对于没有用户行为的item，可以直接使用其subword的embedding合成其embedding，解决了冷启动的问题

## 结构大纲
- 上述方案存在几个问题：
- 线上效果：
- VV：
- 时长：
- 后续展望：

## 关键方法 · 模型 · 指标
- 实验桶(2772, ctr重排) vs base(2773，原始fasttext)，录得vv提升1.8%，时长提升1.3%，目前已经全量，日曝光量6亿次以上
- 中低频item通过共享的subword互相可以“交流信息”，而对于没有用户行为的item，可以直接使用其subword的embedding合成其embedding，解决了冷启动的问题
- 目前腾讯视频ICF主要技术方案是NLP领域的word2vec及其后续的改进方案——将单个用户一定时间窗口内的播放序列视为doc，取若干用户的播放序列作为corpus，使用word2vec学习item(cid或者vid)的embedding
- 通过控制每个节点作为起点的次数和游走路径的长度，可以调节训练语料中item出现的次数和语料数量，从而使得以前因为出现次数过少学习不到embedding的item可以被学习到embedding，另外，随机游走可能会出现播放中从未出现过的序列，从而发掘item之间的隐含关系
- 随机游走策略没有录得正面效果可能是囿于当时对算法ID映射的影响认识不足，可以重新进行实验
- 尾部item学习到的embedding质量不高，强制计算最相似的top 100时，会引入体验上的bad case.
- 得到每个item的embedding之后，为每个item以cosine similarity作为测度计算相似度top 100的item，并将结果存入索引，供线上调用
- 选取item的标题分词和标签，将其当做fastText中的subword，训练的过程中不仅学习item的embedding，亦学习subword的embedding，整个item的embedding为其本身embedding与其subword的加权和，其中，权重亦是可学习的参数
- 对于没有用户行为的item无能无力，无法做到对新内容和长尾冷门内容的冷启动.
- ICF，即item based collaborative filtering，其intuition是根据item在用户行为中的共现关系(或打分等行为衍生的其他关系)，挖掘出item之间关联(看过A的也爱看B或给A打过高分的也会给B打高分)
- 此改进称之为ctr重排
- 各类Advanced-ICF召回
- 引入更多行为信息的icf重排，例如时长、付费率等
- 只考虑用户行为的ICF没有利用腾讯视频丰富的媒资信息.
- 根据用户播放记录构建item之间的关联graph，graph的node是item，当用户播放过item A后继续播放item B，则在A与B之间构建一条无向的edge，A与B在用户播放中的共现次数越多，其edge的权重越大
- 对于每个item的top n相似，我们引入更多行为信息，具体而言，value item与key item的相似分又原先的cosine similarity 变为 （cosine similiarity）* sigmoid(ctr_本身/ctr_所有item平均)

## 与推荐/广告/数据科学面试的关联
- 召回策略（向量/图/协同过滤/多路）
- 冷启动（EE/元学习/内容特征）
- CTR/CVR 预估与校准
- 用户画像/分层/生命周期
- AB 实验与评估框架

## 关键术语
CTR、DIN、Embedding、ICF、VV、Word2Vec

## 与本人项目的关联
通用推荐广告（文中出现相关关键词）

## 价值评估
**高** —— 与本人项目（通用推荐广告）直接关联，且含方法/指标/术语

