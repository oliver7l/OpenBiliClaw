# 模型pipeline流程 · 提炼笔记

> 来源：`01_原始资料库/03_工作资料/工作资料_腾讯/2020年09月-内部资料/腾讯视频/模型pipeline流程.doc` ｜ 19203字

## 一句话定位
关于通用推荐广告相关的技术资料（《模型pipeline流程》），含 30 条关键要点。

## 核心内容摘要
重载自定义network的_serve_fn_opt，定义模型的inference 图，这里的原则：用户+context特征这些只需要计算一次的tensor在和item特征进行交互时，先调用_tile_tensors_with_batch_size方法扩展batch_size份，这里需要开发者自己清楚交互的边界，然后保证这部分准确性；不同于之前的VideoResysDataGenerator对曝光数据进行抽样（10%），VideoResysSampleTable直接采用全量曝光数据和引擎日志进行join，导致任务直接增大了将近10倍，这里采取了一些优化策略：

## 结构大纲
- 1.1. 数据配置化
- 1.1.1. 数据流视图
- 1.1.2. 优化VideoResysSampleTable任务
- 1.1.3. 已有特征流程
- 指定中间表
- features:
- cid指定tfrecords中特征名称，from指定来自中间表哪个字段
- cid: {from: item_id}
- age: {from: face_age}
- gender: {from: face_sex}
- prof: {from: face_profession}
- edu: {from: face_education}
- province: {from: province}
- city: {from: city}
- 指定dtype会进行类型转换，否则继承中间表的字段类型
- 支持转化成Array类型，中间表的原字段必须是;连接的字符串
- seqnum: {from: seqnum}
- 支持过滤条件
- filter: >
- 支持按照条件采样
- sample:
- 1.1.4. 新增特征流程
- 1.1.5. 新增label流程
- 一. 新增短期label
- 2. 新建一张中间表，在短期中间表的基础上增加join生成中间表；

## 关键方法 · 模型 · 指标
- 重载自定义network的_serve_fn_opt，定义模型的inference 图，这里的原则：用户+context特征这些只需要计算一次的tensor在和item特征进行交互时，先调用_tile_tensors_with_batch_size方法扩展batch_size份，这里需要开发者自己清楚交互的边界，然后保证这部分准确性
- 不同于之前的VideoResysDataGenerator对曝光数据进行抽样（10%），VideoResysSampleTable直接采用全量曝光数据和引擎日志进行join，导致任务直接增大了将近10倍，这里采取了一些优化策略：
- VideoResysSampleTable：负责解析特征，比如解析insightdnn特征，join 引擎日志表和曝光点击表生成样本中间表.
- map_shared_embedding: {cid_playlist: cid}
- 数据配置化的目的：由于排序侧特征实验不断增加，大家都各自生成训练和测试的tfrecord，这样会来带来几个问题：
- 算法so的开发统一在 https://git.code.oa.com/omg_real_compute/algorithm_plugins 中的pctr_tf_dnn_dlrm_config对应的so上开发，新建分支，提交mr时增加roygan，owenhe作为review, 避免影响线上其他模型
- 短期样本生成视图： https://us.oa.com/#/?dirId=0&viewId=20200509200045796&locale=zh_CN ，比如ctr, vv, vt模型，中间表omg_video_resys:: t_rcmd_video_app_cid_tfdnn_sample_h
- 二. 新增长期label
- 1.2.2. 优化模型流程
- 1.1.2. 优化VideoResysSampleTable任务
- engine表和曝光点击表切小点 repartition 5000
- 在长/短期中间表中增加新特征字段，在VideoResysSampleTable中增加新特征逻辑
- 新增特征，需要引擎传给pctr，如果不需要特殊处理，直接配置化，如果需要特殊处理，需要修改so
- # 以下三个按照相同的命名, 代码里依靠idx_score, rank_score, exp_id进行pctr过滤和实验id过滤
- 长期样本生成视图： https://us.oa.com/#/?dirId=0&viewId=20200521165405118 ， 比如ltvv模型，中间表omg_video_resys:: t_rcmd_video_app_resys_cover_retention_sample_h
- 在自定义network中实现_get_serve_inputs方法，包含所有模型的输入tensor，用于OptimizedModelSaver生成signature，一般在线上加载so的时候出现 "You must feed a value for placeholder tensor 'example' with string and shape [?] " 的错误时，大部分都是因为_get_serve_inputs方法没有包含所有的输入tensors，导致tf会回溯到最开始的example tensor报错
- type: auc
- 1.2. 模型配置化
- main_table:
- - name: AUC
- 模型pipeline流程
- 1.1.1. 数据流视图
- 一. 新增短期label
- - name: GAUC
- 1.1.3. 已有特征流程
- 1.1.4. 新增特征流程
- 1.3.1. 已有特征流程
- 1.3.2. 新增特征流程
- - name: VV-AUC
- - name: VT-AUC

## 与推荐/广告/数据科学面试的关联
- 排序模型（Wide&Deep/DIN/ESMM/树模型）与重排
- CTR/CVR 预估与校准
- 用户画像/分层/生命周期
- 特征工程/embedding
- AB 实验与评估框架

## 关键术语
AUC、CTR、DIN、Embedding、GAUC、PLE、UNK、VT、VV

## 与本人项目的关联
通用推荐广告（文中出现相关关键词）

## 价值评估
**高** —— 与本人项目（通用推荐广告）直接关联，且含方法/指标/术语

