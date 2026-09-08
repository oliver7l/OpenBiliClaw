# 手撕 SQL 速刷题单（字节数据科学・今日速成）

> 依据：上次备考 deck 明确 "字节 SQL 必考且难度高，LeetCode Medium-Hard，爱考窗口函数 / 连续行为 / 留存率 / TopN / 数据倾斜"。
> 用法：每题先想思路（3-5 秒），再默写 SQL，最后对照答案。
>
> **今天刷这 10 题就够**
>
> 。
> 语法基准：Hive/Spark SQL（JD 要求熟悉 Hadoop/Spark/Hive）。



***

## 一、连续行为类（最高频，必会）

### 1. 找出连续登录 ≥3 天的用户

**思路（背熟口诀）：** 先去重（一天可能多次登录）→ `日期 - 行号`，连续登录的差值相等 → 按 `uid+差值` 分组计数。



```
with dedup as (

&#x20; select distinct uid, dt from login\_log        -- 1. 去重

)

select uid

from (

&#x20; select uid, dt,

&#x20;        date\_sub(dt, row\_number() over(partition by uid order by dt)) as diff  -- 2. 日期减行号

&#x20; from dedup

) t

group by uid, diff                              -- 3. 差值相同=连续

having count(1) >= 3;
```

### 2. 计算每个用户最长连续登录天数



```
with dedup as (

&#x20; select distinct uid, dt from login\_log

), grp as (

&#x20; select uid, dt, date\_sub(dt, row\_number() over(partition by uid order by dt)) as diff

&#x20; from dedup

)

select uid, max(cnt) as max\_consecutive\_days

from (

&#x20; select uid, diff, count(1) as cnt from grp group by uid, diff

) t

group by uid;
```

**追问接得住：** 为什么先去重？—— 不先去重会导致同一天多条记录让 "行号" 错位，把不连续的日子也拼成连续。



***

## 二、留存率类（次高频）

### 3. 计算某天的次日 / 7 日 / 30 日留存率

**思路：** 自连接 —— 当日活跃用户 左连接 未来 N 天活跃用户，按间隔天数分别计数。



```
select

&#x20; a.dt,

&#x20; count(distinct a.uid) as active\_users,

&#x20; count(distinct if(datediff(b.dt, a.dt) = 1,  b.uid, null)) / count(distinct a.uid) as d1,

&#x20; count(distinct if(datediff(b.dt, a.dt) = 7,  b.uid, null)) / count(distinct a.uid) as d7,

&#x20; count(distinct if(datediff(b.dt, a.dt) = 30, b.uid, null)) / count(distinct a.uid) as d30

from act\_log a

left join act\_log b on a.uid = b.uid

&#x20; and datediff(b.dt, a.dt) in (1, 7, 30)

where a.dt = '2026-09-01'

group by a.dt;
```

### 4. 新用户（按首日分组）的次日留存

**思路：** 先取每用户最早活跃日 → 再和活跃表连接，按首日分组算留存。



```
select

&#x20; a.first\_dt,

&#x20; count(distinct a.uid) as new\_users,

&#x20; count(distinct if(datediff(b.dt, a.first\_dt) = 1, b.uid, null)) / count(distinct a.uid) as d1\_ret

from (

&#x20; select uid, min(dt) as first\_dt from act\_log group by uid

) a

left join act\_log b on a.uid = b.uid

&#x20; and datediff(b.dt, a.first\_dt) = 1

group by a.first\_dt;
```



***

## 三、TopN 类（必会）

### 5. 每个用户点击量 Top3 的内容品类



```
select uid, cat, cnt

from (

&#x20; select uid, cat, count(\*) as cnt,

&#x20;        row\_number() over(partition by uid order by count(\*) desc) as rn

&#x20; from click\_log

&#x20; group by uid, cat

) t

where rn <= 3;
```

**追问：** `row_number` vs `rank` vs `dense_rank`？—— 并列名次：`rank` 会跳过（1,1,3），`dense_rank` 不跳（1,1,2），`row_number` 不并列。TopN 一般用 `row_number`。



***

## 四、间隔 / 窗口类

### 6. 找出两次行为间隔 >30 天的用户（lag 版）



```
select distinct uid

from (

&#x20; select uid, dt,

&#x20;        datediff(dt, lag(dt) over(partition by uid order by dt)) as gap

&#x20; from act\_log

) t

where gap > 30;
```

### 7. 会话划分：同一用户相邻行为间隔 >30 分钟算新会话，求每个用户的会话数

**思路（字节常考）：** `lag` 算间隔 → 间隔超阈值记 1，否则 0 → 前缀和 = 会话 ID → 数会话 ID 种类数。



```
select uid, count(distinct session\_id) as session\_cnt

from (

&#x20; select uid, ts,

&#x20;        sum(if(ts - lag(ts) over(partition by uid order by ts) > 30\*60, 1, 0))

&#x20;            over(partition by uid order by ts) as session\_id

&#x20; from behavior\_log

) t

group by uid;
```



***

## 五、漏斗类

### 8. 曝光→点击→下载→激活 漏斗（广告 / 分发场景）



```
select

&#x20; sum(if(step >= 1, 1, 0)) as impress,

&#x20; sum(if(step >= 2, 1, 0)) as click,

&#x20; sum(if(step >= 3, 1, 0)) as download,

&#x20; sum(if(step >= 4, 1, 0)) as activate

from (

&#x20; select uid,

&#x20;        max(case when act = 'impress'  then 1 end) as step,

&#x20;        max(case when act in ('impress','click') then 2 end) as step,  -- 简化示意

&#x20;        -- 实际：把行为按顺序编码为 step 值

&#x20;        max(act\_step) as step

&#x20; from ad\_log

&#x20; group by uid

) t;
```

> 面试重点是
>
> **说清漏斗口径**
>
> ：按 "用户是否到达该环节" 而非 "行为次数"；每一步分母是上一环节到达人数（整体转化率）或该环节暴露人数（环节转化率），要说明。



***

## 六、数据倾斜（思路题，张嘴就能答）

### 9. 一个大 key（如头部大 V、爆款内容）导致 reduce 倾斜，怎么处理？

**先问一句（体现工程经验）：** "是 join 倾斜还是 group by 倾斜？"

**四板斧（背熟）：**



1. **加盐两阶段聚合**（group by 倾斜）：给 key 加随机前缀 → 局部聚合 → 去掉前缀 → 再全局聚合。

2. **大 key 单独处理**：把大 key 挑出来走单独链路，其余正常聚合，最后合并。

3. **Map 端聚合 / Combiner**：能在 map 端先合并就合并，减少 shuffle 数据量。

4. **Map Join 广播小表**：大表 join 小表时把小表 broadcast 到每个 map，避免 shuffle。



```
\-- 加盐示例：先加随机前缀局部聚合，再去前缀全局聚合

select split(k, '\_')\[1] as key, sum(c) as cnt

from (

&#x20; select concat(cast(rand()\*10 as int), '\_', key) as k, count(\*) as c

&#x20; from log group by concat(cast(rand()\*10 as int), '\_', key)

) t

group by split(k, '\_')\[1];
```



***

## 七、其他高频（快速过）

### 10. 每个用户的最新一条行为记录



```
select uid, dt, action

from (

&#x20; select \*, row\_number() over(partition by uid order by dt desc) as rn

&#x20; from log

) t

where rn = 1;
```

### 11. 找出共同关注同一作者 ≥10 个的用户对（自连接，对应 "好友推荐"）



```
select a.uid as u1, b.uid as u2, count(\*) as common\_follow

from follow a

join follow b on a.author\_id = b.author\_id and a.uid < b.uid   -- a.uid\<b.uid 去重

group by a.uid, b.uid

having count(\*) >= 10;
```

### 12. 中位数 / 分位数（时长、价格等）



```
select

&#x20; percentile\_approx(duration, 0.5) as median,

&#x20; percentile\_approx(duration, 0.9) as p90

from act\_log;
```



***

## 刷题自检（今天过一遍）



* [ ] 连续登录 ≥3 天（1）

* [ ] 最长连续天数（2）

* [ ] 次日 / 7 日留存（3）

* [ ] 新用户留存（4）

* [ ] TopN（5）+ rank 区别

* [ ] lag 间隔（6）+ 会话划分（7）

* [ ] 漏斗（8）口径说明

* [ ] 数据倾斜四板斧（9）能讲

* [ ] 最新一条记录（10）、共同关注（11）、中位数（12）