# Personalized Re-ranking for Recommendation
> 来源：01_原始资料库/03_工作资料/工作资料_腾讯/2020年09月-内部资料/其他/Personalized+Re-ranking+for+Recommendation.doc ｜ 1597字

## 一句话定位
个性化重排序模型，解决精排后的混排问题。

## 核心内容摘要
该研究提出使用Transformer模型对当前列表中的每个item进行重新打分。通过个性化矩阵PV学习用户的编码函数以个性化地建模item之间的相互影响。实验表明这种方法能有效提升推荐效果。

## 关键方法 · 模型 · 指标
- 使用Transformer模型进行重排序
- 引入个性化矩阵PV
- 位置嵌入PE（Position Embedding）
- attention机制
- 输出层采用softmax计算点击率

## 与推荐/广告/数据科学面试的关联
- 推荐系统中的多样性问题
- Transformer在推荐系统的应用
- 个性化排序模型的设计思路
- 用户行为建模和特征工程
- 深度学习在推荐系统中的实践案例

## 关键术语
- Personalized Re-ranking
- Transformer
- Self-attention
- Position Embedding (PE)
- Personalized Vector (PV)

## 与本人项目的关联
有明显关联。项目中涉及的腾讯微视/QQ看点图集/视频号等推荐系统，以及OPPO、百度和中信信用卡的广告推荐项目都可能用到重排序技术来提升推荐效果。

## 价值评估
高 —— 这篇资料提供了个性化重排序的具体实现方法和技术细节，在面试中可以展示候选人的技术深度和广度。
