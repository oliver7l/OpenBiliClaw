# spark_tuning · 提炼笔记

> 来源：`01_原始资料库/03_工作资料/工作资料_腾讯/2020年09月-内部资料/看点搜索/内容技术组/知识图谱组/spark_tuning.pptx` ｜ 8590字

## 一句话定位
关于通用推荐广告相关的技术资料（《spark_tuning》），含 30 条关键要点。

## 核心内容摘要
用户自维护schema与优化代码；使用Kryo序列化代替原生Java序列化，提升性能

## 结构大纲
- RDD:
- 局限:
- Dataframe:
- Dataset:
- 3.   更好的二进制编码、更好的内存管理
- Driver:  维护运行环境，资源申请，任务分配与监控
- Worker：集群中任何可以运行任务的节点
- Executor：任务运行在worker节点上的一个进程
- Job:以action方法为界拆分
- Stage: Job子集，以宽依赖为界拆分（而不是shuffle）
- Task: Stage的子集，最小的任务粒度
- Partition:  RDD的分区数
- 序列化：CPU
- 跨机器传输：Network IO
- 读写文件： Disk IO
- https://github.com/EsotericSoftware/kryo
- 减少上传时间：
- 打印JVM运行状态：
- 官方文档：
- 美团技术博客：

## 关键方法 · 模型 · 指标
- 用户自维护schema与优化代码
- 使用Kryo序列化代替原生Java序列化，提升性能
- 编程模型简单
- 优化数据结构
- 序列化：CPU
- THANKS!
- （内存充足时推荐）
- （性能不佳，不推荐）
- （影响性能，不推荐）
- TaskScheduler
- 通过Catalyst自动优化程序
- Job:以action方法为界拆分
- Leader –Follower架构
- 列存储，可以减少io消耗, 提升性能
- 这种方式更加节省内存 （内存稀缺时推荐）
- 系统预留内存，会用来存储Spark内部对象
- 二进制文件，行存储，以KV形式序列化到文件中
- 同时也可以手动设定图中参数来改变各区内存大小
- 数据操作更简单，执行优化更智能，类型检查更安全
- 优化算子，减少shuffle时该key的数据条数
- 一个高效通用的内存型分布式计算框架 , 是对分布式计算的抽象
- MEMORY_ONLY | 使用未序列化的Java对象格式，将数据保存在内存中
- DISK_ONLY | 使用未序列化的Java对象格式，将数据全部写入磁盘文件中
- https://www.cnblogs.com/kpsmile/p/10434390.html
- MEMORY_AND_DISK | 使用未序列化的Java对象格式，优先尝试将数据保存在内存中
- 唯一的区别是，会将RDD中的数据进行序列化，RDD的每个partition会被序列化成一个字节数组
- --conf "spark.driver.extraJavaOptions=-Dfile.encoding=UTF-8  -Dcustom.file.encoding=UTF-8"
- --conf "spark.executor.extraJavaOptions=-Dfile.encoding=UTF-8  -Dcustom.file.encoding=UTF-8"
- MEMORY_ONLY_2 MEMORY_AND_DISK_2 等等. | 对于上述任意一种持久化策略，如果加上后缀_2，代表的是将每个持久化的数据，都复制一份副本，并将副本保存到其他节点上
- spark.shuffle.sort.bypassMergeThreshold | shuffle read task阈值，小于该值则shuffle write过程不进行排序 | 200 | 600

## 与推荐/广告/数据科学面试的关联
- 排序模型（Wide&Deep/DIN/ESMM/树模型）与重排
- 用户画像/分层/生命周期

## 关键术语
CPU、DAG、DAGScheduler、DIN、GB、GC、IO、KS、LZO、MB、ORCFile、PSM、RDD、SPARK、THANKS、TUNING、UTF、YARN、YARNcommands

## 与本人项目的关联
通用推荐广告（文中出现相关关键词）

## 价值评估
**高** —— 与本人项目（通用推荐广告）直接关联，且含方法/指标/术语

