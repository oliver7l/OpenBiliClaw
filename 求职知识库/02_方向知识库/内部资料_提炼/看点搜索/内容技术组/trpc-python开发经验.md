# trpc-python开发经验 · 提炼笔记

> 来源：`01_原始资料库/03_工作资料/工作资料_腾讯/2020年09月-内部资料/看点搜索/内容技术组/trpc-python开发经验.doc` ｜ 1096字

## 一句话定位
内部资料《trpc-python开发经验》，属业务/项目文档，提取 7 条关键要点供复习索引。

## 核心内容摘要
2、根据proto文件生成trpc-python框架代码，可以使用 trpc-go-cmdline 工具，也可以在123平台上添加协议文件生成（推荐）；如下图二，图三

## 结构大纲
- 1、官方开发流程 tRPC-Python快速上手
- 3、采坑问题记录。

## 关键方法 · 模型 · 指标
- 2、根据proto文件生成trpc-python框架代码，可以使用 trpc-go-cmdline 工具，也可以在123平台上添加协议文件生成（推荐）
- 如下图二，图三
- trpc框架建议自己定义的proto文件定义规则为 应用名.服务名 等
- a. 图谱组trpc-python开发环境已经配置好了，为CVM机器，直接在上面写逻辑代码，运行就可以了
- 该工程实现了本服务调用另一个trpc服务 search.search_proxy_deepqa_baike_doc_ir 的功能
- with_target( ' polaris://trpc.search.search_proxy_deepqa_baike_doc_ir.ProxyObj ' )，北极星名字为注册到北极星的名字，可以在123服务信息里看到
- 注意快速上手第3部（图一），生成虚拟环境后，需要启动虚拟环境（ source bin/activate ），在虚拟环境里安装包，保持包的独立干净，因为 123平台在发布时，会根据requirements.txt来确定服务所依赖的pypi打包（ pip3 freeze > requirements . txt），所以需要将必要配置加入到requirements . txt

## 与推荐/广告/数据科学面试的关联
- 无明显领域关键词，可能为通用业务/数据记录

## 与本人项目的关联
无明显关联

## 价值评估
**中** —— 含部分方法/指标要点，可按需查阅

