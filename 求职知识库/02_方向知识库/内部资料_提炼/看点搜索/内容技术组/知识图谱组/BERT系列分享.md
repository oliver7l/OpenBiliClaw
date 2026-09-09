# BERT系列分享 · 提炼笔记

> 来源：`01_原始资料库/03_工作资料/工作资料_腾讯/2020年09月-内部资料/看点搜索/内容技术组/知识图谱组/BERT系列分享.pdf` ｜ 10490字

## 一句话定位
关于百度、通用推荐广告相关的技术资料（《BERT系列分享》），含 30 条关键要点。

## 核心内容摘要
取 sentence embedding 训练逻辑回归模型；A: token 级别的特征可以增加 embedding table，但不要加在首层，应当加在输出层，且优先使用合并

## 结构大纲
- 1. 引用量过低/知名榜单以外的冷门模型
- 2. BERT 体系之外的模型 (e.g. XLNet)
- 3. 与音频/视频相结合的多模态模型 (e.g. VisualBERT)
- 第一步：准备带有标签的监督数据 (需要与业务所需的分布完全一致)
- 第二步：选择模型，调整超参数，反复试验
- 第三步：Bad Case 分析，调整训练数据，重新试验
- 第四步：整理规则，应对数据调整过后模型依然无法解决的情形
- 第五步：模型蒸馏，轻量上线
- 1. 优先使用提供最佳中文预训练参数的 BERT
- 2. 充分利用多任务学习的各项 tricks
- 2. 我们的 query 大多是不完整的短语句，不适合加入中文分词信息
- 1. 如果复杂问题比例不大，优先使用规则和基于 LSTM 的 Seq2Seq
- 1. 规则足以满足业务需求时，优先使用规则
- 99.2 (↑1.7)
- GLUE: BERT-Large + 0.3%
- MRC: BERT-Large + 1.6~2.2%
- 预训练：分两步。第一步单独训练 G；第
- 微调：抛弃 G，使用 D

## 关键方法 · 模型 · 指标
- 取 sentence embedding 训练逻辑回归模型
- A: token 级别的特征可以增加 embedding table，但不要加在首层，应当加在输出层，且优先使用合并
- Sentence-BERT: Sentence Embeddings using Siamese BERT-Networks (Reimers et al., 2019) quote: 61
- Embedding
- token embeddings
- segment embeddings
- position embeddings
- Evaluate Sentence Embedding (SentEval)
- Multi-Task Deep Neural Networks for Natural Language Understanding (Liu et al., 2019) quote: 179
- BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding (Devlin et al., 2018) quote: 5,440
- 模型预训练时是否关注我们需要的特征
- 不推荐使用目前所有大型预训练语言模型
- Labeled-data Tasks (STILTs)
- Query 分类 (Classification)  e.g. QQ匹配、完整性、问题分类
- 2. 当问题变得复杂，优先考虑优化规则和数据，由于推理过慢，大模型作为最后的下下策(MASS, UniLM)
- 如果决定自行预训练，NLG/MT 任务优先 UniLM，其他则优先轻量级模型 (ALBERT ELECTRA)
- StructBERT: Incorporating Language Structures into Pre-training for Deep Language Understanding (Wang et al., 2019) quote: 2
- MLM 策略 (15%)
- ELECTRA 15%: D 只根据 15% 被 [MASK] 的词计算损失
- NSP 策略
- MLM 策略
- 如何选择模型
- MTL 策略
- ELECTRA
- Finding
- 系列模型BERT
- 模型不是越大越好
- Q: 如何增加特征
- 语言分类和机器翻译)
- Masking 策略

## 与推荐/广告/数据科学面试的关联
- CTR/CVR 预估与校准
- 特征工程/embedding

## 关键术语
AFS、ALBERT、BERT、BERTSP、BPE、BQ、CJRC、CLM、CLS、CMRC、CNN、CRF、CTR、DAAF、DBQA、DIN、DLM、DNN、DRCD、ELECTRA、ELMo、ERNIE、Embedding、FFN、GAN、GD、GLUE、GPT、GPU、GRU

## 与本人项目的关联
百度、通用推荐广告（文中出现相关关键词）

## 价值评估
**高** —— 与本人项目（百度、通用推荐广告）直接关联，且含方法/指标/术语

