# 模型线上serving相关文档、 · 提炼笔记

> 来源：`01_原始资料库/03_工作资料/工作资料_腾讯/2020年09月-内部资料/推荐系统/模型线上serving相关文档、.doc` ｜ 2998字

## 一句话定位
关于通用推荐广告相关的技术资料（《模型线上serving相关文档、》），含 30 条关键要点。

## 核心内容摘要
item_vec.dat ：模型item的embedding, 版本是v1是代表使用近似计算，版本是v2代表使用bruteforece计算(因为不同的方式索引加载的逻辑不同，且在算法插件初始化时进行加载，所以无法使用实验系统配置的方式进行，只能在模型文件中决定)；在生成模型时，item_vec.dat的版本设为v2，并将embedding的最后一项设为item的bias

## 结构大纲
- 主要步骤：
- ps：公司所有代码要求按照epc的规范进行，请参考wiki
- 模型生成流程：
- 多interest:
- 任务截图说明：
- Sumeru集群：
- 其他需要注意的点：
- 1、如果要使用bias+brute force方式

## 关键方法 · 模型 · 指标
- item_vec.dat ：模型item的embedding, 版本是v1是代表使用近似计算，版本是v2代表使用bruteforece计算(因为不同的方式索引加载的逻辑不同，且在算法插件初始化时进行加载，所以无法使用实验系统配置的方式进行，只能在模型文件中决定)
- 在生成模型时，item_vec.dat的版本设为v2，并将embedding的最后一项设为item的bias
- 每位算法同学需要维护自己的sumeru集群，每个集群上都运行自己的pctr引擎，引擎上可以加载自己负责的一类或多类模型(如mind不同策略的版本)
- 算法同学以引擎插件的形式开发自己所负责的排序/召回模型，算法插件需要负责模型、配置载入，实例构造，实例inference，inference结果封装等工作，引擎只提供输入输出的接口
- 带有bias的模型文件在item embedding的最后一列增加的对应item的bias值，不带有bias的模型文件在item enbedding的最后一列增加的是用于欧氏距离近似计算内积的增加项
- saved_model.pb和variables: 模型saved_model
- 2、多interest时不同的融合方式由实验系统配置项merge_method决定，
- tensorflow作为pCTR引擎的lib负责模型的载入和预测实例的inference
- 此外，要将serving时实验系统配置项的 search_method 项设为1(0为近似计算)
- 推荐引擎传递的特征key值列表wiki： http://tapd.oa.com/REC_ALG/markdown_wikis/view/#1010154631010063381
- 单元测试与集成测试的步骤与方法在wiki(点击链接后搜索算子调试，有详细步骤) http://tapd.oa.com/REC_ALG/markdown_wikis/show/#1210154631001041299 中
- 模型生成流程：
- 任务截图说明：
- 上图是一个配置例子
- 在骡马配置mind实验
- mdl文件截图所示的文件
- 模型线上serving相关文档、
- 配置实验时需要引流到自己的sumeru集群
- model_conf.conf：模型conf文件
- 3、配置参数keep_num决定多interest融合时每个interest保留的召回结果
- 另外，需要额外生成一份召回池子过滤后的item字典表video_id_mapping_new.dat：
- 分业务id集群监控（模型数据更新时间）： http://debug.pcg.com/tv/cluster_monitor
- 算法部署流程： http://tapd.oa.com/REC_ALG/markdown_wikis/#1010154631011031817
- 拉取代码(主project与submodule代码都要拉取)——>准备工具与环境(主要是blade)——>创建新的算法对应目录或者修改已有的算法代码
- 算法id申请，集群业务id查看： https://git.code.oa.com/omg_real_compute/rank_dnn/wikis/home/
- 算法so开发wiki： http://tapd.oa.com/REC_ALG/markdown_wikis/view/#1010154631010076773
- debug系统使用说明wiki： http://tapd.oa.com/REC_ALG/markdown_wikis/view/#1010154631010123981
- 测试所需单interest模型文件任务流： https://tesla.oa.com/ml/platform.html?projectId=16856&flowId=112524
- 测试所需多interest模型文件任务流： https://tesla.oa.com/ml/platform.html?projectId=16856&flowId=264833#
- video_id_mapping_new.dat：过滤后的视频及其对应的字典编码，用于bruteforce求最近邻(因为召回池子需要过滤，而bruteforce的faiss索引只支持从0开始编码且不可以跳码，所以需要另外新建一个字典)

## 与推荐/广告/数据科学面试的关联
- 召回策略（向量/图/协同过滤/多路）
- 排序模型（Wide&Deep/DIN/ESMM/树模型）与重排
- CTR/CVR 预估与校准
- 特征工程/embedding
- AB 实验与评估框架

## 关键术语
CTR、DIN、DNN、Embedding

## 与本人项目的关联
通用推荐广告（文中出现相关关键词）

## 价值评估
**高** —— 与本人项目（通用推荐广告）直接关联，且含方法/指标/术语

