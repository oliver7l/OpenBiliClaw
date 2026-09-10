# RL强化学习召回
> 来源：01_原始资料库/03_工作资料/工作资料_腾讯/2020年09月-内部资料/腾讯视频/RL+强化学习召回.doc ｜ 1480字

## 一句话定位
应用Top-K Off-Policy修正方案于腾讯视频长视频推荐系统。

## 核心内容摘要
该研究利用论文提出的Off-Policy修正方案，将Policy-Gradient类算法应用于腾讯视频的长视频推荐业务。通过离线实验验证了模型效果，并正在开展实时在线实验。主要挑战包括策略梯度计算和数据一致性问题。

## 关键方法 · 模型 · 指标
- Top-K Off-Policy修正方案
- Policy-Gradient类算法
- RNN生成用户状态State
- softmax层建模混合策略
- importance weighting
- 离线列表实验与实时在线实验

## 与推荐/广告/数据科学面试的关联
- 推荐系统中的RL应用
- Off-Policy修正方案在实际业务中的落地经验
- 复杂算法模型的设计与优化
- 实验验证方法及结果分析
- 数据处理和特征工程实践

## 关键术语
- RL (Reinforcement Learning)
- Policy-Gradient
- Off-Policy
- importance weighting
- RNN
- softmax层

## 与本人项目的关联
无明显关联。

## 价值评估
中 —— 资料提供了RL在推荐系统中的实际应用案例，对于理解算法模型的设计和优化有帮助。
