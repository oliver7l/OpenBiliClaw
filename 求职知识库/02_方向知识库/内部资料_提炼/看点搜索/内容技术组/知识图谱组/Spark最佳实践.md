# Spark最佳实践 · 提炼笔记

> 来源：`01_原始资料库/03_工作资料/工作资料_腾讯/2020年09月-内部资料/看点搜索/内容技术组/知识图谱组/Spark最佳实践.pdf` ｜ 16378字

## 一句话定位
内部资料《Spark最佳实践》，属技术文档，提取 30 条关键要点供复习索引。

## 核心内容摘要
• 充分利用SparkSQL的优化执行策略，用户可省去复杂的优化过程；• 充分利用SparkSQL的二进制编码、内存管理以及Codegen等优化技术

## 结构大纲
- 举例：WordCount简单实例(scala)
- DAG：以Shuffle为界，划分Stage单元，生成有向无环图
- RPC：lanuch task
- 缺点：小表会膨胀，整体运行较慢
- RPC: launch task
- 9.3g 4g 10 2 100 6 10
- 注意：需要做
- streamIter: 流式遍历表 buildIter: 查找表
- 1、遍历表，取出一条记录
- 2、根据join条件得到keyA
- 3、根据join条件查找 keyB=keyA 的记录
- 5、根据join过滤条件判断是否符合要求
- 4、join rowA 和 rowBs中每条记录
- streamIter: 大表
- buildIter: 小表
- 6、根据join过滤条件判断是否符合要求
- 4、查找成功，得到所有满足条件的记录
- 5、查找失败
- streamIter: 左表
- buildIter: 右表
- 2、根据join条件得到keyB
- 3、根据join条件查找 keyA=keyB 的记录
- buildIter: Hash查找
- 1.定义batch
- 2.指定数据源

## 关键方法 · 模型 · 指标
- • 充分利用SparkSQL的优化执行策略，用户可省去复杂的优化过程
- • 充分利用SparkSQL的二进制编码、内存管理以及Codegen等优化技术
- • Dataset/DataFrame依赖图会被转化为SQL算子，通过SparkSQL引擎执行
- RDD编程模型
- TaskSet
- Table A
- Table B
- 程模型及最佳实战
- RDD依赖关系图
- • 优化数据结构
- 数据源：文件系统
- • 序列化：CPU
- New Table
- Spark分布式运行架构
- DStream 编程模型
- TaskScheduler
- TaskSetManager
- TaskScheduler上
- • 完全类似于RDD的编程模型
- Table A (IterA)
- Table B (IterB)
- (可以理解为一张分布式表/视图)
- 一个高效通用的内存型分布式计算框架
- Master-Slave 主从架构
- Spark on Yarn运行架构
- Wide Dependencies
- • Job：以action方法为界
- Kmeans算法Cache性能测试
- Spark  Core编程模型及最佳实战
- Driver TaskScheduler

## 与推荐/广告/数据科学面试的关联
- 用户画像/分层/生命周期
- 特征工程/embedding

## 关键术语
AM、BY、COMPRESS、CPU、DAG、DAGScheduler、DStream、FROM、GROUP、HDFS、INNER、IO、JOIN、KS、MLLib、NM、ON、OOM、PV、RDD、RDDs、RPC、SELECT、SIGKILL、SIGTREM、SQL、TDBank、TDW、WHERE

## 与本人项目的关联
无明显关联

## 价值评估
**中** —— 含部分方法/指标要点，可按需查阅

