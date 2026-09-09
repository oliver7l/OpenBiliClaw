# 视频号行为Embedding召回项目说明文档 · 提炼笔记

> 来源：`01_原始资料库/03_工作资料/工作资料_腾讯/2020年09月-内部资料/视频号/视频号行为Embedding召回项目说明文档.doc` ｜ 4329字

## 一句话定位
关于视频号、通用推荐广告相关的技术资料（《视频号行为Embedding召回项目说明文档》），含 30 条关键要点。

## 核心内容摘要
如下图所示经过优化后的UU embedding召回效果提升显著：；工业界的推荐系统在召回阶段中一般会采用多路召回的策略，包括标签召回、协同过来召回、LBS召回、热门召回、embedding召回等

## 结构大纲
- 一、项目背景
- 二、业界方案及项目方案对比
- 三、项目方案：
- 行为embedding生成：
- 1、行为异构网络构造：根据以下四种类型的边构造异构网络：
- 2、通过以下五种metapath进行随机游走生成训练语料：
- 3、训练word2vec得到各节点的embedding
- 基于行为embedding的召回：
- 排序：
- 难点亮点：
- 项目难点主要有以下两个：
- 落地效果：

## 关键方法 · 模型 · 指标
- 如下图所示经过优化后的UU embedding召回效果提升显著：
- 工业界的推荐系统在召回阶段中一般会采用多路召回的策略，包括标签召回、协同过来召回、LBS召回、热门召回、embedding召回等
- 83路是基于行为embedding的user2user2feed召回（局部点赞率+局部完播率+局部人均曝光时长排序），点赞率及完播率均比欧拉召回总体要高
- 如下图所示，工业界的推荐系统通常涉及召回层、排序层两大模块，召回模块是推荐系统的第一阶段，其主要功能是根据user及item的特征、user历史观看记录，从海量的物品库中为用户初筛出用户潜在感兴趣的item，交给下一层的排序层，是推荐系统中重要的一环
- 如下图所示，81路是基于行为embedding的user2user2feed召回（局部点赞率排序），30路是基于行为embedding的feed2feed召回，均比欧拉召回总体的点赞率高60%以上（相对）
- 1、视频号行为embedding已经落地应用到视频号欧拉推荐系统的召回模块中，并衍生出13路不同的召回，成为欧拉推荐系统的主力召回
- 本人在项目中负责视频号域内行为embedding召回的embedding数据产出、优化以及运维监控、部分基于embedding召回策略的实现
- 视频号微信出品的短视频流推荐产品，欧拉推荐系统负责为进入视频号的用户推荐视频号中的高质量、用户感兴趣的feed，以提升用户的互动率、停留时长以及产品日活、次留等指标
- 基于embedding召回已经成为业界主流方式，其中包括YoutubeDNN、DSSM（谷歌、微软）等端到端的embedding方法，也包括把user-item互动历史构造一个二部图，来训练Graph Embedding得到user和物品的embedding，再通过Faiss、hnsw等近邻搜索的方法来为user召回相似的item（淘宝、知乎等）
- 主流的基于user-item二部图的Graph Embedding方法是通过把user-item的交互历史记录构造成一个二部图，然后通过随机游走得到user-item序列，最后通过word2vec训练得到user及item的embedding，但是这种方法的缺陷是一些被交互次数很少的item或者没有被交互过的item难以训练出质量较好的embedding
- 基于行为embedding召回总体架构如下：
- 加入这一策略后embedding近邻质量可获得肉眼可见的提升
- 1、在视频号项目初期只对少部分微信用户灰度时期，视频号的行为数据是极其稀疏的，如何对稀疏的行为网络进行数据增强，缓解冷启动问题、增强embedding质量是一个难点
- 基于行为embedding衍生出来的召回包括feed2feed召回、user2user2feed召回、user2finder2feed召回等等总共13种不同策略的召回，以下只详述最重要的user2user2feed召回的计算逻辑（ 提交的git code的具体逻辑 ）：
- 针对这个问题，我们利用视频号上的用户观看、完播、点赞、关注feed等行为数据、用户关注号主状态、号主发表feed、feed被打上的tag四种边构造出user-feed、user-finder、finder-feed、feed-tag多节点多种异构边的异构网络，并且构造多种丰富的metapath（异构游走路径）来生成序列，最终通过word2vec训练出user、finder、feed的embedding
- 基于行为embedding的召回：
- 视频号行为Embedding召回项目说明文档
- 本项目的embedding召回属于基于Graph Embedding的召回
- 2、基于视频号行为embedding召回效果远优于欧拉召回的平均效果，其中几路的点赞率、完播率分别是欧拉召回中效果最好的召回之一
- 经过数据分析得知，热门item由于连边很多，经常被作为另一个item的正样本去优化，导致热门item的embedding的模会比较大，算出来的cosine也更容易偏大
- 有别于传统的graph embedding方法，在user-feed的metapath之外通过增加user-finder、finder-feed、user-feed-finder-feed、feed-tag四种不同的metapath去游走，生成更多更丰富的语料，并且通过finder、feed对应的tag来连接稀疏连边的feed
- 行为embedding生成：
- 3、训练word2vec得到各节点的embedding
- 如何在海量的物料库中筛选出高质量、用户感兴趣的候选feed，是召回层要解决的问题
- 2、推荐系统中经常会出现“哈利波特热点问题”，即经常会协同出一些不太相关的热门item
- a) user-feed 正向行为边（包括点赞、关注、完播且曝光时长>5s、曝光时长>30s四种行为构成user-feed边，边权分别为2：2：2：1）
- 在视频号推荐中也有类似的问题
- 本项目通过以下方法解决问题1：
- 本项目通过以下方法解决问题2：
- user2user2feed召回逻辑：

## 与推荐/广告/数据科学面试的关联
- 召回策略（向量/图/协同过滤/多路）
- 排序模型（Wide&Deep/DIN/ESMM/树模型）与重排
- 冷启动（EE/元学习/内容特征）
- 用户画像/分层/生命周期
- 特征工程/embedding

## 关键术语
DIN、DSSM、Embedding、Word2Vec

## 与本人项目的关联
视频号、通用推荐广告（文中出现相关关键词）

## 价值评估
**高** —— 与本人项目（视频号、通用推荐广告）直接关联，且含方法/指标/术语

