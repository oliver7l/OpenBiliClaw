# query中核心mention识别 · 提炼笔记

> 来源：`01_原始资料库/03_工作资料/工作资料_腾讯/2020年09月-内部资料/看点搜索/内容技术组/知识图谱组/query中核心mention识别.doc` ｜ 24419字

## 一句话定位
关于百度、通用推荐广告相关的技术资料（《query中核心mention识别》），含 30 条关键要点。

## 核心内容摘要
方法概览的模型图中TagEmbedding和BERT编码的相加细节如下：；mention识别任务使用经典的Bert+BiLSTM+CRF模型来做，具体对应模型图中Tag Embedding 下方的整块内容

## 结构大纲
- 登入接口机
- 进入项目所在目录
- 1.1. 方法概览
- 1.1.1. mention识别
- 1.1.1.1. 数据集
- 1.1.1.2. 模型细节
- 模型结构与训练：
- 1.1.2. 核心mention识别
- 1.1.2.1. 数据集
- 1.1.2.2. 模型细节

## 关键方法 · 模型 · 指标
- 方法概览的模型图中TagEmbedding和BERT编码的相加细节如下：
- mention识别任务使用经典的Bert+BiLSTM+CRF模型来做，具体对应模型图中Tag Embedding 下方的整块内容
- 训练过程中以预测结果的f1值作为效果评估方式，f1值基于准确率和召回率计算
- 召回率定义为：预测mention和标注mention的最长公共子序列长度在标注mention中的长度占比
- 训练过程给bert更小的学习率，给下游序列标注任务涉及的模型更大的学习率（1.加速收敛，2.提升学习质量）
- roberta+单层双向LSTM+转移概率受限的CRF来训练模型（用双层LSTM效果应该还会有所提升，训练时长也会有所增加）
- 其中融入了mention_data提供的标签信息，把每个标签通过embedding层进行编码，把每个字通过bert层进行编码，将它们经过线性层统一到相同维度，然后进行element-wise的加法合并标签与字语义两种信息
- I5cvlwkip38En2qh+4CvprmyiOJEmZxEJL/mDnCgvKsBZgAYcTnBlxIw53vQJc1uABrBl+xbl1x6
- 这个任务中包含了9万标注数据，标注数据来自 真实的互联网网页标题数据，是用户检索Query对应的有展现及点击的网页， 短文本平均长度为21.73中文字符，覆盖了不同领域的实体（包括各垂类的实例、概念），如人物、电影、电视、小说、软件、组织机构、事件等垂类，以及通用概念
- 这两个阶段的任务都可以通过序列标注模型进行建模
- 核心mention识别模型依然沿用了bert+bilstm+crf的序列标注模型结构（单独训练）
- 准确率定义为：预测mention和标注mention的最长公共子序列长度在预测mention中的长度占比
- 最后筛选掉2000条样例，剩余9700条训练数据，格式如最前面模型图中样例所示，每个sample包含三部分内容：
- 当前模型以batch_size=16，12G单卡GPU训练，一个epoch约1.5小时，通常到7，8个epoch就会收敛（设置了提前停止策略）
- 首先利用已经训练好的mention识别模型对这1.2万标注数据中的文本标注其mention（记为mention_data）
- elEeG0NSIqGbla8jny9ieHgQKSY/qBZxwvFvwtonn8Vv7rsf217dgnK+gve9+1w8++yzSCeSMC0P
- a1wKbR2dAtLRs4CZ3Jgxy9It6IYpYClJmDX2B02WRxOi97f++fstW7ejp3c6Nm56Fff++je46eab
- 4vGy7oX1AiKTmJvhLyPjgyhWCujsbhNCYgKsjcf+6k/5bW1tcBwffYND4tHB8BpeY/r2fD6Pcomp
- M95/3gdk4ZZuz2KsOIKa76JqV+HrGmwSjE869lM+l0waU9MPSRhe77RpePvbz8eFF74Lc+cdJNf6
- cVXHFWoughYMh6nabFMt1D8gAN0eux9PP7cFN9y4RsCD8fFx7N69Gye8dgK4QG3vasehixZg9kGz
- aq5qBM3J97Bp80toy3Uh25nDpo2v4IabrsPll38TV1xxBXTDQ76kbP3c809j2ZHLQEBteGxIPG56
- xJFFPGFhV98AFsw7SDK5NKbtlVsHu7qT56P1KS25k2Qe63tIJGI4++z3oFDMo7OrHf0De5BOZmRH
- t6Y5wlmUSMfgeSQIHYVhMRTPFw+DlMEFu9JN1BdNwzrlFS4dAvuEl4Mz3f+5oqA3V2GsJKSiuVw7
- cTz39DP4wt/9M5au6pIwvLeufgs+8YlPCGDieh5cu4LFixdj27Yt0r3pVUT+qBnTpsMvV4MFWPPy
- gZGhQUyfPk3I8Ale0sPItW1ksznodhgGt//jD9vf73//EH772wfw1pNOxtlnvQe9PdMFiDjtXW8X
- L6ORoWGsOnElzjnnvVhw8KEYHBiWTJabNm7CjN5pENL/ptu/hljcwle+/DUZd958wom46uorFWgW
- OK5bwuVEnhPT46K3RfnETqmuw4HChwFTgCABASTzoqn6Ar0btWCxTR4w4nYkwdb0luo/ZujwyA3m
- CEhZSBhZ2E5JFh68t9qprkthiYK1pIrs4kSWoQdCGq0kyAKn4qIj16X4kMoOqlYV8VhceUOBi1wp
- IPyBGv/2Nf5MaB+MP2wXHId4FnwhHP+Dawde/7Dyw/5WP/s+9Q8flxy3dbglDx3ZbqlBu2zDiFeR
- gFlAG8qP+J2ZHAQ0YnQR48A8IQ4KSLAZkyUUC2oeSNHESCqcsNUAw6arQHMhIYz3IuM1w9EsHbau

## 与推荐/广告/数据科学面试的关联
- 召回策略（向量/图/协同过滤/多路）
- 用户画像/分层/生命周期

## 关键术语
AArrjiSvzoRz、ACAZU、AG、BERT、BN、BOYjD、BRyJbUi、CDsHjJ、CSocauolsZx、DID、DIN、DIyOS、DLu、EFkgskBkgcgCkQX、EHvv、EQDOl、Embedding、FYnnok、GCN、GRU、HP、IPyBGv、KKcZ、KPD、KS、LA、LSTM、NVn、ORqYjIcC、PD

## 与本人项目的关联
百度、通用推荐广告（文中出现相关关键词）

## 价值评估
**高** —— 与本人项目（百度、通用推荐广告）直接关联，且含方法/指标/术语

