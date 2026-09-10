# Rank to Rerank序反馈
> 来源：01_原始资料库/03_工作资料/工作资料_腾讯/2020年09月-内部资料/推荐系统/Rank+to+Rerank序反馈.doc ｜ 13130字

## 一句话定位
（≤40字）
评估rank to rerank各特征对排序的影响，确保rerank后的视频顺序合理。

## 核心内容摘要
（3-5句，讲清主干）
本文介绍了如何通过序反馈机制来监控和调整推荐系统中从rank到rerank各个阶段的排序变化。主要方法包括计算Kendall tau、Pearson相关系数等统计指标，并针对top10视频及filter out的视频进行chi-square检验。

## 关键方法 · 模型 · 指标
（要点列表，抓方法/模型/算法/指标/数据，去重）
- 序反馈机制：Kendall tau, Pearson相关系数
- top 10视频序变化：Chi-square goodness of fit
- filter out 视频序变化：Wilcoxon ranksums

## 与推荐/广告/数据科学面试的关联
（这篇资料能支撑哪些面试话题？列出可迁移点）
- 推荐排序算法评估
- 排序模型的性能监控
- 数据分析和统计检验方法在推荐系统中的应用
- 个性化推荐系统的优化策略

## 关键术语
（列出文中重要术语/缩写，便于检索）
- Kendall tau
- Pearson correlation coefficient
- Chi-square goodness of fit
- Wilcoxon ranksums
- CatTagRescorer, DurationRescorer, NegFeedbackRescorer等rerank参数

## 与本人项目的关联
（若与微视/图集/视频号/OPPO/百度/中信项目相关，点出关联；无关写"无明显关联"）
有明显关联。文中提到的推荐系统排序优化方法和统计检验手段可以应用于腾讯微视、QQ看点等项目中。

## 价值评估
（高 / 中 / 低 —— 对面试准备的价值，并一句话理由）
中 - 这篇文章提供了实际的排序优化案例，对于理解如何监控和调整推荐系统的排序逻辑非常有帮助。
