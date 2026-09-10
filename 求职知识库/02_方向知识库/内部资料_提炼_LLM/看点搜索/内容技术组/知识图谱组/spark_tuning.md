# Spark调优
> 来源：01_原始资料库/03_工作资料/工作资料_腾讯/2020年09月-内部资料/看点搜索/内容技术组/知识图谱组/spark_tuning.pptx ｜ 8590字

## 一句话定位
Spark调优实践与策略

## 核心内容摘要
该PPT详细介绍了Spark的基本概念、运行架构及常用算子，重点讲解了资源调优（如num-executors, executor-cores等）、并行度调优、存储调优、代码调优等方面的内容，并提供了常见问题的解决方法。

## 关键方法 · 模型 · 指标
- 资源调优：设置合理`num-executors`, `executor-cores`, `executor-memory`, `driver-memory`
- 并行度调优：调整RDD分区数，使用`repartition`算子
- 存储调优：选择合适的存储格式如ORCFile、Parquet等
- 代码调优：减少shuffle操作，使用Kryo序列化，数据持久化（persist）
- 常见问题处理：内存溢出、任务失败重试

## 与推荐/广告/数据科学面试的关联
- 资源管理与配置优化
- 并行计算与调度策略
- 数据存储与读写性能调优
- 算子选择与代码优化技巧

## 关键术语
- RDD, Dataframe, Dataset
- DAG, Stage, Task
- BroadCast, Shuffle
- Memory Level (MEMORY_ONLY, MEMORY_AND_DISK等)
- Spark SQL, Hive

## 与本人项目的关联
无明显关联

## 价值评估
中 —— 资料提供了全面的Spark调优方法，虽然本人项目主要涉及推荐和广告系统，但其中关于资源管理和代码优化的部分仍有一定参考价值。
