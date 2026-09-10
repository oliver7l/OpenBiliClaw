# 视频号行为Embedding召回项目说明文档
> 来源：01_原始资料库/03_工作资料/工作资料_腾讯/2020年09月-内部资料/视频号/视频号行为Embedding召回项目说明文档.doc ｜ 4329字

## 一句话定位
基于Graph Embedding的视频号用户行为embedding召回方案及效果优化。

## 核心内容摘要
该项目通过构建异构网络并使用多种metapath进行随机游走，生成高质量的user、finder和feed embedding。最终应用于13种不同的召回策略中，并显著提升了推荐效果。

## 关键方法 · 模型 · 指标
- 异构网络构造：包括user-feed、user-finder、finder-feed及feed-tag四种边。
- 多种metapath生成训练语料，如user-feed、user-finder等五种路径。
- 使用word2vec进行embedding训练。
- 通过正则项优化热门item的embedding质量。
- 基于embedding召回策略的应用效果评估指标包括点赞率、完播率等。

## 与推荐/广告/数据科学面试的关联
1. **算法设计**：了解如何构建异构网络和使用多种metapath进行embedding训练。
2. **模型优化**：正则项在处理热门item时的应用。
3. **效果评估**：基于embedding召回策略的效果衡量方法。
4. **冷启动问题解决**：稀疏行为数据的增强方法。

## 关键术语
- Graph Embedding
- metapath
- word2vec
- embedding
- 正则项

## 与本人项目的关联
有明显关联。项目中涉及的embedding召回策略、异构网络构建及优化方法均与腾讯微视/QQ看点图集/视频号/OPPO(国内+海外)/百度/中信信用卡的相关推荐和广告项目中的算法设计和技术实现有相似之处。

## 价值评估
高 —— 提供了详细的基于Graph Embedding的embedding召回方案，对于面试中涉及的算法设计、模型优化及效果评估等话题非常有用。
