# unbiased-lambdamart
> 来源：01_原始资料库/03_工作资料/工作资料_腾讯/2020年09月-内部资料/看点搜索/上层排序组/unbiased-lambdamart.doc ｜ 9239字

## 一句话定位
unbiased-lambdamart算法及其在点击数据生成和模型训练中的应用。

## 核心内容摘要
该文档详细介绍了unbiased-lambdamart算法的实现步骤，包括初始化排序模型、点击模型生成模拟点击数据以及lambdamart模型的训练与评估。实验设计考虑了session信息，并通过前后端日志抽样进行数据处理和模型验证。

## 关键方法 · 模型 · 指标
- **方法/算法**: unbiased-lambdamart, pair-wise LTR, IPW位置bias估计
- **模型**: RankSVM初始化排序模型, Click Models (cascade_model, PositionBiasedModel, user_browsing_model)
- **指标**: NDCG@1, @3, @5, AUC, CTR

## 与推荐/广告/数据科学面试的关联
- 推荐系统中的LTR算法实现和优化
- 点击模型（Click Model）的设计与应用
- pair-wise排序学习方法的理解与实践
- NDCG、AUC等评估指标的应用
- 数据预处理及特征工程在推荐系统中的作用

## 关键术语
- unbiased-lambdamart
- RankSVM
- click model (cascade_model, PositionBiasedModel, user_browsing_model)
- IPW (Inverse Probability Weighting)
- NDCG@1, @3, @5
- AUC, CTR

## 与本人项目的关联
无明显关联。文档中的项目背景和具体应用场景与候选人过往的推荐系统项目（如腾讯微视/QQ看点图集/视频号/OPPO/百度/中信信用卡）不完全一致。

## 价值评估
中 —— 文档详细介绍了LTR算法的具体实现步骤，对理解pair-wise排序学习方法和点击模型设计有帮助。对于面试准备来说，可以作为参考案例来讨论相关技术点。
