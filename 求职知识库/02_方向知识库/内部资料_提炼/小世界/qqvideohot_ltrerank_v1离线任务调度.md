# qqvideohot_ltrerank_v1离线任务调度 · 提炼笔记

> 来源：`01_原始资料库/03_工作资料/工作资料_腾讯/2020年09月-内部资料/小世界/qqvideohot_ltrerank_v1离线任务调度.doc` ｜ 828字

## 一句话定位
内部资料《qqvideohot_ltrerank_v1离线任务调度》，属业务/项目文档，提取 3 条关键要点供复习索引。

## 核心内容摘要
6383 热点曝光数据；8123 算法上报日志

## 结构大纲
- 1. streaming日志落地 【Spark Scala】
- 落地三份日志：曝光日志、播放日志、包含action value的日志，分钟级延迟
- code: StreamingSaveAll3WithUid.scala
- 2. 样本拼接 【Spark Scala】
- code: MakeSamples.scala
- 3. 训练和更新action value 【Tensorflow Python】
- code: qqvideohot_ltrerank_tfv100.py

## 关键方法 · 模型 · 指标
- 6383 热点曝光数据
- 8123 算法上报日志
- 落地三份日志：曝光日志、播放日志、包含action value的日志，分钟级延迟

## 与推荐/广告/数据科学面试的关联
- 无明显领域关键词，可能为通用业务/数据记录

## 关键术语
PLE

## 与本人项目的关联
无明显关联

## 价值评估
**中** —— 含部分方法/指标要点，可按需查阅

