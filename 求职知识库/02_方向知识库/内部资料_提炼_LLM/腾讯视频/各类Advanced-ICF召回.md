# 各类Advanced-ICF召回
> 来源：01_原始资料库/03_工作资料/工作资料_腾讯/2020年09月-内部资料/腾讯视频/各类Advanced-ICF召回.doc ｜ 1450字

## 一句话定位
改进传统ICF算法，提升尾部和冷门内容的推荐效果。

## 核心内容摘要
针对传统ICF算法存在的问题，通过引入随机游走、subword嵌入以及CTR重排等方法，优化了item之间的关联图构建及相似度计算。实验结果显示VV和时长均有显著提升。

## 关键方法 · 模型 · 指标
- 随机游走（random walk）
- subword嵌入（fastText中的subword）
- CTR重排（cosine similarity * sigmoid(ctr_本身/ctr_所有item平均)）

## 与推荐/广告/数据科学面试的关联
- 推荐系统中相似度计算和冷启动问题解决方案
- 基于行为序列的学习方法
- 随机游走算法在图结构中的应用
- CTR优化及嵌入表示学习

## 关键术语
- ICF（item based collaborative filtering）
- word2vec
- random walk
- subword
- fastText
- embedding
- cosine similarity
- sigmoid
- ctr

## 与本人项目的关联
有明显关联。候选人曾参与腾讯微视/QQ看点图集/视频号等推荐系统的开发，这些项目中也涉及到相似度计算和冷启动问题的解决方法。

## 价值评估
高 —— 提供了针对ICF算法改进的具体案例和技术细节，对面试准备非常有用。
