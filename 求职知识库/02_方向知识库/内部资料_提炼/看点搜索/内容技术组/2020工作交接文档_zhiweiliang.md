# 2020工作交接文档_zhiweiliang · 提炼笔记

> 来源：`01_原始资料库/03_工作资料/工作资料_腾讯/2020年09月-内部资料/看点搜索/内容技术组/2020工作交接文档_zhiweiliang.doc` ｜ 5876字

## 一句话定位
内部资料《2020工作交接文档_zhiweiliang》，属技术文档，提取 22 条关键要点供复习索引。

## 核心内容摘要
3.1. 短文本广告识别模型；> Note：目前暂不支持同时使用ALBERT和特征网络，因为特征网络中使用的 MultiheadAttention 与ALBERT中的层同名，在模型加载时会报错

## 结构大纲
- 1. 代码及数据目录
- 代码根目录： /data/ceph/zhiweiliang
- 数据目录：模型的训练及测试数据均在对应的代码目录下
- 2. 代码运行环境
- 3. 工作详情
- 3.1. 短文本广告识别模型
- 具体的模型训练超参数设置在main.py
- 3.2. 时效性新闻识别模型
- 3.3. 优质文章识别模型
- 3.3.1. 模型训练
- 3.3.1. 模型测试
- 4. Sumeru模块
- 时效性新闻识别模型： SearchDuNews
- 优质文章识别模型： SearchCqArticleScore
- 5. MDB
- 实例： article36kr.mdb.mig:17350
- 数据库： db_36kr_article
- 读数据表： url_data （存放爬取URL）
- 写数据表： long_article_data （存放爬取数据）

## 关键方法 · 模型 · 指标
- 3.1. 短文本广告识别模型
- > Note：目前暂不支持同时使用ALBERT和特征网络，因为特征网络中使用的 MultiheadAttention 与ALBERT中的层同名，在模型加载时会报错
- 输入数据及特征
- 3.3.1. 模型训练
- 3.3.1. 模型测试
- 3.3. 优质文章识别模型
- 3.2. 时效性新闻识别模型
- > id title label
- --label_smoothing=0 \
- # 具体的模型训练超参数设置在main.py
- 时效性新闻识别模型： SearchDuNews
- 数据目录：模型的训练及测试数据均在对应的代码目录下
- keras-transformer==0.31.0
- 优质文章识别模型： SearchCqArticleScore
- 特征与与内容中心字段的对应关系（左：输入数据，右：内容中心对应字段）
- --vocab_file=run4online/news/dict \
- --vocab_file=bert/albert_tiny_zh_google/vocab.txt \
- 在语义子网络中，首先会加载阶段一的ALBERT权重，然后再与其他两个子网络（排版网络和特征网络）联合训练
- 工作归纳及模型的详细结构可以参考： https://docs.qq.com/slide/DWUZqVVVOeHlpQ3Jo
- 模型训练： /data/ceph/zhiweiliang/SearchCqArticleScore/data/article
- --vocab_file=run4Online/ArticleScore/0813_021831140/vocab.txt \
- --test_file=data/news/20200623_testdata_xiaoka_with_labeled.txt # 可选

## 与推荐/广告/数据科学面试的关联
- 特征工程/embedding

## 关键术语
BERT、CNN、HTML、MDB、PLE、SEP、Transformer

## 与本人项目的关联
无明显关联

## 价值评估
**中** —— 含部分方法/指标要点，可按需查阅

