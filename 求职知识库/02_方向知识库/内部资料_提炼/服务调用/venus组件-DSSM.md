# venus组件-DSSM · 提炼笔记

> 来源：`01_原始资料库/03_工作资料/工作资料_腾讯/2020年09月-内部资料/服务调用/venus组件-DSSM.doc` ｜ 7367字

## 一句话定位
关于通用推荐广告相关的技术资料（《venus组件-DSSM》），含 30 条关键要点。

## 核心内容摘要
item与user类似，两个塔底层的向量长度应当严格等于sum(embedding_dim(slot(i)))；按照无量0.3.0版本的标准，使用 numerous.framework.SparseEmbedding 注册，embedding_dim代表了最终进入塔的单个slot向量长度

## 结构大纲
- 分别介绍：
- 1.无量数据构造
- 2.DSSM模型脚本编写
- -*- coding: UTF-8 -*-
- 这里给出绝大多数可以使用的无量api接口
- 训练任务的配置（.i.e 任务名字，worker和server中的线程数等）
- 模型导出的配置（.i.e 模型导出间隔等）
- 设置全局的优化器和参数初始化方式
- Note: 我们同样允许用户自定义设置各个参数的优化和初始化方式，
- 全局优化器
- 全局参数初始化方式
- 开始构建计算图
- embedding
- slotid不能乱设置，要与训练数据对应，切记！
- 定义dnn weights
- 需要通过无量的ControlPlaceholder Api来进行控制
- 记得训练阶段使用bn层的时候，设置 training=True
- 修改item分支的请求名称，召回上线组件的请求对象（item）需要此名称
- predict value
- loss
- 3.服务上线调用
- 调用方式如下：

## 关键方法 · 模型 · 指标
- item与user类似，两个塔底层的向量长度应当严格等于sum(embedding_dim(slot(i)))
- 按照无量0.3.0版本的标准，使用 numerous.framework.SparseEmbedding 注册，embedding_dim代表了最终进入塔的单个slot向量长度
- # embedding
- embedding_dim = 32,
- embedding_dim = 64,
- DSSM双塔模型需要item+user+label的数据
- doc_embedding_sum_layers = {}
- user_embedding_sum_layers = {}
- embedding = tf.matmul(x_data, embedding_w)
- doc_embedding_sum_layers[slot_id] = embedding
- user_embedding_sum_layers[slot_id] = embedding
- embedding_w, x_data = numerous.framework.SparseEmbedding(
- doc_merge_layers = tf.concat(doc_embedding_sum_layers.values(), axis=1)
- user_merge_layers = tf.concat(user_embedding_sum_layers.values(), axis=1)
- 以上简单举例了itemEmbedding的获取方式，user Embedding类似，构造slot feature value的结构体之后可以将req_type填写为"user_tower"进行调用
- 在venus上使用dssm双塔模型主要包含以下三个方面：
- 虽然相关模型是双塔模型，但实际上无量提供的服务是根据输入返回相应的层
- # Note: 我们同样允许用户自定义设置各个参数的优化和初始化方式，
- 无量数据构造可以参考 预估服务接入帮助文档 中的离线样本生成和特征抽取逻辑代码进行，流程和逻辑基本一样
- 比如usertower底层有12个槽位，没个槽位设置的都是64纬的向量，则最终用户侧的塔输入为12*64=768纬的向量
- 为了节省时间，在离线样本生成时可以先将item和user与label的关联存储下来，然后分别计算item、user的特征，最后统一拼接放入hdfs
- 无量数据单个特征的表现形式为【key:slot:value】，训练中会把单个slot打平成一个向量，slot下的key：value对为稀疏存储的数据，其中key是不超过int48的整型，value为浮点数
- # 全局优化器
- dssm模型脚本编写
- ## 开始构建计算图
- 2.DSSM模型脚本编写
- labels = y_data))
- # 设置全局的优化器和参数初始化方式
- req = PredictRequest()
- # -*- coding: UTF-8 -*-

## 与推荐/广告/数据科学面试的关联
- 召回策略（向量/图/协同过滤/多路）
- CTR/CVR 预估与校准
- 用户画像/分层/生命周期
- 特征工程/embedding

## 关键术语
AUC、CTR、DIN、DSSM、Embedding、FTRL、PLE、SGD、UTF

## 与本人项目的关联
通用推荐广告（文中出现相关关键词）

## 价值评估
**高** —— 与本人项目（通用推荐广告）直接关联，且含方法/指标/术语

