# TDM项目 · 提炼笔记

> 来源：`01_原始资料库/03_工作资料/工作资料_腾讯/2020年09月-内部资料/腾讯视频/TDM项目.doc` ｜ 4263字

## 一句话定位
内部资料《TDM项目》，属业务/项目文档，提取 13 条关键要点供复习索引。

## 核心内容摘要
任务流程 ：【tesla】ICF聚类初始化→ 【us】数据预处理→【113机器】调用建树脚本→ 【tesla】模型训练→ 【tesla】模型打包；聚类结果上传到：

## 结构大纲
- https://git.code.oa.com/jawiezhu/tdm-app
- 视图链接：
- 数据处理的代码：
- hdfs文件删除命令：
- 任务ID： 20191209163653908
- 任务ID：20191209172428133
- 任务ID：20200330173747981
- 任务ID：20200330184654308
- 聚类结果上传到：
- us任务ID：20200328112216380
- 9.180.5.113登录方式：（管理员carsonliang）
- 任务ID：20200311173009551
- TDM模型训练：
- TDM模型打包：
- hdfs同步到pub机：（上面那步路径下的模型文件 同步到 pub机）：
- sumeru管理平台链接：（管理人 carsonliang）
- 在线serving代码地址：
- 开发机器：（机器管理人员 carsonliang）
- 编译：
- 若出现：
- 解决方案：
- 注释掉BUILD中的：
- 执行：

## 关键方法 · 模型 · 指标
- 任务流程 ：【tesla】ICF聚类初始化→ 【us】数据预处理→【113机器】调用建树脚本→ 【tesla】模型训练→ 【tesla】模型打包
- 聚类结果上传到：
- 调用脚本进行聚类
- TDM模型训练：
- TDM模型打包：
- ICF聚类初始化 ：
- 若该路径有近期模型了，但存在该报警，联系carsonliang
- hdfs同步到pub机：（上面那步路径下的模型文件 同步到 pub机）：
- 若该路径近期没有模型，检查 打包任务 或者 训练任务 中间是否有卡住的情况
- 调用9.180.5.113的脚本，/data/workspace/jawiezhu_tdm_icf_cluster/tdm/run_step_1.sh
- /data/workspace/jawiezhu_tdm_icf_cluster/tdm/expand_sample 目录下 tdm_op.cc编译命令：
- 【算法so开发wiki】 http://tapd.oa.com/REC_ALG/markdown_wikis/show/#1210154631001041299
- 说明线上模型太久没更新，检查该hdfs路径上模型情况： hdfs://ss-omg-3-v2/stage/outface/omg/omg_video_resys/jawiezhu/tdm_icf_cluster/online_tdm_model/

## 与推荐/广告/数据科学面试的关联
- 无明显领域关键词，可能为通用业务/数据记录

## 关键术语
BB、BF、KS、PLE、YYYYMMDD

## 与本人项目的关联
无明显关联

## 价值评估
**中** —— 含部分方法/指标要点，可按需查阅

