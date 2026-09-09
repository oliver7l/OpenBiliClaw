# tdw踩坑记 · 提炼笔记

> 来源：`01_原始资料库/03_工作资料/工作资料_腾讯/2020年09月-内部资料/看点搜索/数据平台组/tdw踩坑记.doc` ｜ 2601字

## 一句话定位
内部资料《tdw踩坑记》，属技术文档，提取 4 条关键要点供复习索引。

## 核心内容摘要
venus能兼容这种问题，tdw解析到连续两个等号就会丢弃后续所有的序列；CREATE TABLE ods_search_page_work_behavior(

## 结构大纲
- 年月日时分：${YYYYMMDDHHFF}
- 异常情况：qua2%3DQV%3D3，urldecode后是qua2=QV=2
- 改用spark同步： Spark-2.4.4-tq全互通使用指南

## 关键方法 · 模型 · 指标
- venus能兼容这种问题，tdw解析到连续两个等号就会丢弃后续所有的序列
- CREATE TABLE ods_search_page_work_behavior(
- 这里表名填的是venus_inter_db中的表名，是用来检测分区用的，venus用户id是用来检测venus任务是否完成的
- 需要在idex上写语句或者在us上创建任务建表，且建表后需要到 http://tdwhelper.oa.com/storage_stat/storage_path_info.php?dbName=pcg_kd_search_data_platform&tableName=t_sh_mtt_smartbox_search_res 设置表的生命周期，否则7天后表的生命周期将被设置为7天，到时如果需要修改生命周期需要审批

## 与推荐/广告/数据科学面试的关联
- 用户画像/分层/生命周期

## 关键术语
BA、BD、BF、BIGINT、BY、COMMENT、CREATE、IN、PARTITION、PARTITIONED、QV、SUBPARTITION、TABLE、VALUES、YYYYMMDDHHFF

## 与本人项目的关联
无明显关联

## 价值评估
**中** —— 含部分方法/指标要点，可按需查阅

