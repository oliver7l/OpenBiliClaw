# 自带cache的RedisWrapper开发及应用

- 链接: https://mp.weixin.qq.com/s/kwASlMcZB4a2N89vsxG3MQ
- 发布: 2019-04-09
- 来源: 微信公众号 AlgorithmDog

---

我们中心内部的在线 golang 框架有一个有 emma 同学实现的自带  cache 的 RedisWrapper，今天给大家介绍下这个 API 实现的细节和思考。

背景

在商业化活动中，redis作为数据存储经常用到，而在这些活动中，经常会有一些数据不会频繁修改，如一些默认数据等等，除了商业化活动外，一些实时预测的item特征也是属于这种特性（不会频繁修改但可能会修改），如果我们在每次取默认数据的时候都需要连接redis，在qps较高的情况下将极大的影响线上效率。基于这个问题，我们开发了一个自带cache的RedisWrapper——WrapperCache，让具有这种特性的数据加载到cache中，且实现在redis中数据更新后cache能达到一段时间内立即更新的效果，同时在此基础上实现线上定时生效数据和立即生效数据加载cache的API。

WrapperCache实现

本文的WrapperCache是基于我们的在线go服务mixture框架实现的，不愿意透露姓名的shemliang(梁晓湛)在框架上实现了一套redis异步机制如下图所示，该套异步机制在redis连接数为2的情况下可以达到2w的QPS，也正是因为有这样的异步机制，我们的WrapperCache得以完全放心开发，不用担心缓存和redis的连接数的问题。

![](https://mmbiz.qpic.cn/mmbiz_png/Q3H1TCddfvMDvyO8gcaxs6aO90OF2kYngMz6HiaOOzQK1XGsW7AWUOgH36kRcJS3ZE69bFfR17WSXMSs5JabgAg/640?wx_fmt=png)

在Go语言嵌入式Cache选择时，基于 shem 关于go嵌入式纯内存cache的调研，加上该种情况下，我们存储在cache的数据量一般不会很大，所以WrapperCache是基于go-cache实现的，虽然expire的性能低，但是缓存的数据量不大的情况下影响可以忽略不计。

WrapperCache数据结构

在WrapperCache的数据结构中，我们主要有两部分，一部分是需要连接redis的成员变量，另一部分就是cache相关的成员变量，这里我们定义了两个，一个是go-cache自带的Cache类型，另一个就是我们定义的允许存储的Cache的最大条数。

type WrapperCache struct { 
 // redis相关成员变量
 ... 
 localCache *cache.Cache 
 localCacheMaximum int 
}
// go-cache缓存框架自带cache的结构体
type Cache struct {
 *cache
 // If this is confusing, see the comment at the bottom of New()
}
type cache struct {
 defaultExpiration time.Duration
 items map[string]Item
 mu sync.RWMutex
 onEvicted func(string, interface{})
 janitor *janitor
}

 从背景可以知道，我们应用场景是存储在cache的量级不大的情况，如果不限制写入cache的条数，可能存在cache中的数据同时失效，然后同时查询redis的情况，这种情况会直接造成redis雪崩，考虑到这种情况，所以加入了cache允许写入最大条数，这个参数允许用户自己定义，一旦超过了最大存储条数，就不允许再写入，只能等cache中的数据失效再进行写入。

var FLAG_redis_cache_maximum = flag.Int("redis_cache_maximum",10000,"cache_maximum")

 同时，我们还考虑到如果用户对该参数不是特别了解，对该参数随意传参，如定义称1kw，并且一直往cache灌入数据，这样也会出现expire慢以及redis崩掉的情况，所以我们写死了一个“真正”的最大存储条数5w，在用户传参大于这个条数的时候，我们直接将成员变量localCacheMaximum置为5w

w := &WrapperCache{
 ...
 }
// cache New 
w.localCache = cache.New(cache_expiration_time,cache_cleanup_time)

// “真正”最大存储条数 
var maximum = *config.FLAG_redis_cache_maximum
if maximum > 50000{
 maximum = 50000
}
w.localCacheMaximum = maximum

WrapperCache相关函数

在介绍相关函数之前，我们首先讨论一下如何保持redis和缓存的一致性，我们可以将不一致分为以下三种情况：

redis中有数据，cache中没有数据：当从cache中获取不到数据的时候，可以从redis中拉取数据，然后存储一份到cache中，达到一致性；

redis中有数据，cache中也有数据，但是redis和cache中数据不一致：这种情况下，说明之前redis中有数据，且和cache中数据是一致的，当redis数据更新达到这种效果，所以可以当redis更新时我们清空缓存来达到一致性。但redis更新我们无法判断，所以可以对cache设置一个失效期来达到可以在一定时间内达到同步，失效期越短，数据一致性越高，查redis越频繁，可以自己取舍；

redis中没有数据，cache中有数据：这种情况和情况2类似，只有redis中数据更新（删除了一些数据）的时候才会达到这种效果，而解决方法可以参照方法2，对cache设置失效期来达到一致性

简单来说，我们的缓存策略就是

首先尝试从cache中读取，如果读取不到则从redis中读取

redis更新时（更新 OR 删除数据），使用设置失效期来达到一致

WrapperCache相关操作有很多，如get、set等，下面我们介绍经常用的Get函数和Del函数。

1. Get & Hgetall

Get：我们先从cache中进行查找，如果没有则从redis中进行get并将对应的key-value存储在cache中，同时要判断cache中的存储的条数是不是小于我们的最大存储条数，只有在小于最大存储条数的时候，我们才进行存储

func (w *WrapperCache) Get(ctx context.Context, key string) (string, error) {
 if data, ok := w.localCache.Get(key); ok == true {
 return redis.String(data, nil)
 }else {
 data, err := w.Do(ctx, "GET", key) // 从redis中get相应的value
 if err != nil {
 logs.Error("Get Data With Key Failed.", err.Error())
 return "", err
 }
 // 是否满足最大条数
 if w.localCache.ItemCount() < w.localCacheMaximum {
 w.localCache.SetDefault(key, data)
 }
 return redis.String(data, err)
 }
}

Hgetall：与Get类似，只不过在redis中get数据的时候用的指令是hgetall，得到的data类型是Map类型

2. Del & HDel

删除数据的时候，我们有两种删除方式

先删除cache，再删除redis

先删除redis，再删除cache，

两者有区别么？其实两者有很大的区别。如果先删除cache再删除redis的话，我们可能在删除cache后马上有请求来get缓存中的数据，发现没有get到，则会从redis中get相应key的value，这个时候我们redis的数据如果刚好还没有删除的话，是可以从redis中get到的，然后缓存到cache中，无法保证数据被真正删除，所以真正安全的删除策略是先删除redis再删除cache。

Del和HDel的区别在于，HDel需要通过key和field才能定位到redis和cache中的数据进行删除。

func (w *WrapperCache) Del(ctx context.Context, key string) (int, error) {
 data, err := w.Do(ctx, "DEL", key)
 w.localCache.Delete(key)
 return redis.Int(data, err)
}

func (w *WrapperCache) HDel(ctx context.Context, key string, field string) (int, error) {
 data, err := w.Do(ctx, "HDEL", key, field)
 key_field := keyvalue(key,field) // 拼接key&field
 if _,ok := w.localCache.Get(key_field); ok == true {
 w.localCache.Delete(key_field)
 }
 return redis.Int(data, err)
}

立即生效 & 定时生效实现与使用方法

在介绍相关函数之前，我们需要介绍@xueminsi(司雪敏)[1]之前灌入redis的基本思想，在商业化活动中，一般数据会分为两种：立即生效数据和定时生效的数据，而设计的难点就在于定时生效的数据。对于定时生效的数据，xueminsi的设计在redis会维护一些meta信息，用于生效时间的校验和生效数据的检查，同时基于redis的hash数据结构设计了如下用于数据存储的数据结构（appid和domin用于指定唯一商业化活动不同domin的数据）：

// 定时生效数据的meta_list信息
appid_domain_meta_enabletime_with_data -> (writetime1_enabletime1, writetime2_enabletime2)
// 定时生效数据的redis的hash数据结构
appid_domain_key -> ( (enabletime1 -> value1) (enabletime2 -> value2) )

为了方便用户直接使用，我们的基于WrapperCache封装立即生效和定时生效功能，是基于xueminsi的设计来开展的，具体的实现方式可以参考上述km链接。 

1. 立即生效

我们设计立即生效cache的数据结构ImmediateDaoCache如下，应用的appid、domin以及我们设计的WrapperCache数据结构，同时为了方便使用，我们在数据结构中也定义appid和domin拼接后的变量。

type ImmediateDaoCache struct{
 appid string
 domain string
 appid_domain string
 w * async_redis.WrapperCache
}

我们提供两个API供用户使用，一个是NewImmediateDaoCache，另一个就是请求来时，我们需要从cache中get的API。

func NewImmediateDaoCache(addr string, password string, appid string, domain string) (*ImmediateDaoCache, error) {
 dao := &ImmediateDaoCache{
 w: async_redis.NewWrapperCache(addr,password),
 appid:appid,
 domain:domain,
 appid_domain:fmt.Sprintf("%s_%s",realappid,realdomain),
 }
 return dao, nil
}
func (dao *ImmediateDaoCache) Get(ctx context.Context, key string)(string, error){
 real_key := fmt.Sprintf("%s_%s", dao.appid_domain, key)
 res, error := dao.w.Get(ctx, real_key)
 return res, error
}

 而使用方法也一目了然，如下所示

import (
 "git.code.oa.com/ieg_dm_proscenium/mixture.git/util/async_redis"
)
var testData * busi_redis.ImmediateDaoCache

testData, _ = busi_redis.NewImmediateDaoCache(addr,password,appid, domin)
result,_ := testData.Get(ctx, userid)

2. 定时生效

定时生效相对于立即生效的场景就要复杂很多，我们知道在定时生效的场景下，我们通过WrapperCache的Hgetall是得到enabletime -> value的Map类型，但是并不知道应该取哪个enabletime对应的value，所以需要一个定时器来定时从redis中来获取meta_list，来获取合法的离当前时间最近的enabletime对应的value作为当前的value。我们可以通过WrapperCache的Get方法将meta_list存储到cache中，然后将从meta_list中获取当前生效的enabletime存储到我们的PeriodDaoCache数据结构中，而enabletime可能会被同时读取和写，所以结构体中需要有锁变量。所以开始考虑的我们的数据结构和定时器如下

type PeriodDaoCache struct {
 wcache* async_redis.WrapperCache
 appid string
 domain string
 appid_domain string
 leftNearestEnableTime string
 lock *sync.RWMutex
}
func PeriodDaoCacheTicker(ctx context.Context, daocache *PeriodDaoCache) {
 ExecuteCache(ctx, daocache) // 计算当前enabletime，从cache中get meta_list
 go func() {
 var ticker = time.NewTicker( * time.Minute) // 周期5分钟
 for range ticker.C {
 ExecuteCache(ctx, daocache)
 }
 }()
}
func ExecuteCache(ctx context.Context, daocache *PeriodDaocache) {
 ...
 // 得到包含两个生效时间
 enabletime_with_data_infos_cache, err := daocache.wcache.Get(ctx, meta_enabletime_with_data_key)
 ...
}

 这个时候则开始考虑一个问题，在WrapperCache相关函数介绍的时候，我们知道cache有一个失效时间，通过失效时间我们来达到redis和cache的一致性。我们将meta_list读入cache，则meta_list也有一个失效时间，而我们的定时器也是每5分钟从redis中更新一次，假设我们设置cache的失效时间是5分钟，那么redis中数据更新后，最晚可能10分钟（cache失效时间+period定时器时间）后cache中的数据才能更新。这个时间差太大，无法在需要的时间内真正的达到cache和redis的一致性，所以我们在定时生效的情况下对这个一致性方案进行了改进。

后面考虑到，如果减少period的周期时间和cache的失效时间也可行，例如都设置成1分钟，则最多有2分钟的cache数据更新延迟，但是也存在这种情况，如果有些定时生效场景cache数据长时间不更新（如天更新），开发为了减少从redis读取数据的频率，可能直接将cache的失效时间设置成10min或者半小时更久，这样也无法达到cache和redis的一致性。这个时候我们可以直接从源头解决问题，我们的定时器可以改为从redis中直接拉取meta_list，这样每当redis更新之后，定时器周期过后，就会从redis拉取最新的enabletime，而这个时间外部并不会使用到，我们写死在代码中，设置成1分钟，同时结构体中需要加入一个Wrapper变量连接redis，这样数据更新1分钟后cache中的数据就一定会更新。

type PeriodDaoCache struct {
 w* async_redis.Wrapper
 ...
}
// 得到当前enabletime
func PeriodDaoCacheTicker(ctx context.Context, daocache *PeriodDaoCache) {
 ...
}
func ExecuteCache(ctx context.Context, daocache *PeriodDaoCache){
 ...
 // 得到包含两个生效时间
 enabletime_with_data_infos_cache, err := daocache.w.Get(ctx, meta_enabletime_with_data_key)
 ...
}

 和立即生效实现一样，我们提供两个API供用户使用，一个是NewPeriodDaoCache，而另一个就是Get方法，NewPeriodDaoCache与NewImmediateDaoCache不同的是有一个Wrapper成员变量以及需要定时获取最新的mate_list来获取enabletime。

func NewPeriodDaoCache(addr string, password string, appid string, domain string,) (*PeriodDaoCache, error) {
 ...
 periodDaoCache := &PeriodDaoCache{
 w: async_redis.NewWrapper(addr, password),
 ... // 和立即生效一致
 lock: new(sync.RWMutex),
 leftNearestEnableTime:"-1",
 }
 // 定时获取meta_list
 PeriodDaoCacheTicker(context.Background(), periodDaoCache)
 return periodDaoCache, nil
}

 Get方法相对于立即生效就要复杂得多，我们需要首先判断我们定时器从redis中得到的enabletime是否合理，如果合理则根据enabletime得到我们对应的value。

func (daocache *PeriodDaoCache) Get(ctx context.Context, key string)(string, error){
 // 判断leftNearestEnableTime是否合理
 ...

 real_key := fmt.Sprintf("%s_%s", daocache.appid_domain, key)
 data, err:= daocache.wcache.HGetall(ctx, real_key)
 if err != nil {
 logs.Error("Get Data From RedisCache Failed.", err.Error())
 return "", err
 }
 // get对应的value
 if result,ok := data[ leftNearestEnableTime ]; ok == true {
 return result, nil
 }else {
 return "", fmt.Errorf("EnableTime Is not in Data.")
 }
}

 使用方法和立即生效类似，如下所示

import (
 "git.code.oa.com/ieg_dm_proscenium/mixture.git/busi/busi_redis"
)
var testData * busi_redis.PeriodDaoCache

testData, _ = busi_redis.NewPeriodDaoCache(addr, password, credid, domin)
result,_ := testData.Get(ctx, userid)

总结

本文根据我们现有的应用场景，实现了WrapperCache的封装，让不频繁更新但仍可能更新的小数据集存储到cache中，并封装了立即生效和定时生效数据的相关API方便用户直接调用。

这个功能是自己在商业化活动中，发现自己每次都需要写协程来加载不频繁更新但可能更新的数据集，是十分不方便的，所以想到WrapperCache的实现，“需求是最大动力”。虽然是一个小小的功能，但是在实现过程中仍然有许多需要考虑的地方，如wrapperCache中的删除顺序、定时生效数据的cache存储实现等，在实现过程中考虑到自己应用的各个场景然后进行改进补充真的是一个十分有意思的事情。

[1] http://www.algorithmdog.com/period_data

预览时标签不可点

Scan to Follow

 Got It

 Scan with Weixin to 
use this Mini Program

 Cancel
 Allow

 Cancel
 Allow

 Cancel
 Allow

 ×
 分析

![](http://mmbiz.qpic.cn/mmbiz_png/AVt8qQ587W9b7ZXDWm3wtwn8N2k5wDkxSuZLLhibf8BiaWG4ibdEjFB2xzLHH8vSuODG7xvVyqan0f7VY3QS6dSkHSzibTtZOzicKASp2Ubeypgs/0?wx_fmt=png)

微信扫一扫可打开此内容，
使用完整服务

 <script