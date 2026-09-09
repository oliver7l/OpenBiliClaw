# 知乎合作的api 文档-9.25 · 提炼笔记

> 来源：`01_原始资料库/03_工作资料/工作资料_腾讯/2020年09月-内部资料/看点搜索/内容专项/知乎合作的api 文档-9.25.pdf` ｜ 15817字

## 一句话定位
关于通用推荐广告相关的技术资料（《知乎合作的api 文档-9.25》），含 21 条关键要点。

## 核心内容摘要
如果签名匹配，则处理请求；如果签名不匹配，则拒接请求

## 结构大纲
- 2. 下架内容查询接⼝
- 3. 话题相关接⼝
- 业务请求参数说明：⽆
- 4. 热⻔内容
- 5. 搜索
- 6. 获取回答详情接⼝
- 7. 获取⽂章详情接⼝
- 1. 创建⼀个规范请求
- 2. 使⽤规范请求和⼀些其他信息来创建要签名的字符串
- 3. 使⽤⾃⼰的知乎签名秘钥以及要签名的字符串来创建签名
- 4. 将⽣成的签名添加到  HTTP 请求中
- 5. ⼀个完整的  Python 签名认证示例
- 1. 增量内容  id 列表获取接⼝
- 备忘：
- 内容为正常状态
- 如果内容为限制流通、隔离、删除等特殊状态内容
- 1. 提供内容
- 2. 提供⽅式
- 3. 输出⽂件格式
- 4. ⽂件内容格式
- 5. 频率
- 2. CanonicalURI 是添加规范  URI 参数，后跟换⾏符。
- 3. CanonicalQueryString 是规范查询字符串，后跟换⾏符。
- 4. CanonicalHeaders 添加规范请求头部参数，后跟换⾏符。
- 5. SignedHeaders 添加已签名的头部参数，后跟换⾏符。

## 关键方法 · 模型 · 指标
- 如果签名匹配，则处理请求
- 如果签名不匹配，则拒接请求
- 您在前⾯所有步骤中使⽤的值匹配
- return kSigning
- 序列化后的  json , 例如：
- a. 该值⽤于计算规范请求摘要的哈希算法
- c. 根据参数名称的  ASCII 码表的顺序排序
- c. 其中头部参数（⼩写）按照  ASCII 码表排序
- thumbnail (string): 回答图⽚地址 ,
- image_url (string): ⽂章题图地址 ,
- 1. Algorithm 使⽤的签名算法名称，后跟换⾏符
- kSecret = your secret access key
- 对于  SHA256 ，算法名称是： ZH-HMAC-SHA256
- kDate = HMAC("ZH" + kSecret, Date)
- print 'No access key is available.'
- kSigning = HMAC(kDate, "zh_request")
- kSigning = sign(kDate, 'zh_request')
- is_editor_recommendation (boolean): 是否被编辑推荐 ,
- author_avatar_url: 作者头像图⽚地址（开启隐私保护及匿名⽤户返回空） ,
- # Create the signing key using the function defined above.
- # Read zhihu access key from env. variables or configuration file. Best pract

## 与推荐/广告/数据科学面试的关联
- 排序模型（Wide&Deep/DIN/ESMM/树模型）与重排

## 关键术语
ADD、AKIDEXAMPLE、ASCII、CALCULATE、CANONICAL、CREATE、GET、HHMMSS、HMAC、HTTP、HTTPRequestMethod、INFORMATION、KS、MUST、PLE、POST、PUT、REQUEST、SEND、SHA、SIGN、SIGNATURE、SIGNING、STRING、SZ、TASK、THE、TO、URI、URL

## 与本人项目的关联
通用推荐广告（文中出现相关关键词）

## 价值评估
**高** —— 与本人项目（通用推荐广告）直接关联，且含方法/指标/术语

