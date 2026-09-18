# 携程机票 API 逆向研究报告

## 1. 目标 API

```
POST https://flights.ctrip.com/international/search/api/search/batchSearch?v=<random>
```

请求 JSON 体包含航班搜索参数，响应包含机票价格数据。

---

## 2. 验证方式

### 2.1 数据采集

1. 在浏览器中打开 `https://flights.ctrip.com/online/list/oneway-ckg-yty?depdate=2026-09-01`
2. 通过 Playwright 路由拦截（`page.route('**/batchSearch**')`）在浏览器级别实时捕获真实请求
3. 同时通过 `performance.getEntriesByType('resource')` 确认所有 API 调用

### 2.2 签名验证

用捕获的真实数据验证签名算法：

```
transactionID = df2c11f032f54cb8b82a88258b12e317
sign 实际值    = 99ed7af1b7851b18173aaec79043118a

计算: md5("df2c11f032f54cb8b82a88258b12e317" + "CKG" + "YTY" + "2026-09-01")
     = 99ed7af1b7851b18173aaec79043118a  ✅ 匹配
```

### 2.3 参数行为观察

通过多次页面加载，观察各参数变化规律：
- 每次加载都触发 `batchSearch` 请求
- 记录 `v` 参数和 `transactionID` 的变化模式

---

## 3. 验证结论

### 3.1 各参数来源分析

| 参数 | 来源 | 纯 requests 可生成? |
|------|------|-------------------|
| `v` (URL 查询参数) | `Math.random()` 生成的 0~1 随机浮点数 | ✅ 可以 |
| `transactionID` (请求体 + 请求头) | `uuid.uuid4().hex` (32位十六进制 UUID4) | ✅ 可以 |
| `sign` (请求头) | `md5(transactionID + depCity + arrCity + depDate)` | ✅ 可以 |
| JSON 请求体 | 固定结构，字段含义明确 | ✅ 可以 |
| `token` (请求头) | WhaleGuard 反爬 JS 动态计算 | ❌ 不可以 |
| `w-payload-source` (请求头) | WhaleGuard 反爬 JS 动态计算 | ❌ 不可以 |
| 部分 Cookie | 由 JavaScript 在页面加载时设置 | ❌ 不可以 |

### 3.2 签名算法（已验证）

```python
import hashlib

def compute_sign(transaction_id: str, dep: str, arr: str, dep_date: str) -> str:
    src = transaction_id + dep + arr + dep_date
    return hashlib.md5(src.encode("utf-8")).hexdigest()
```

### 3.3 `v` 参数作用

- **作用**：缓存破坏（cache-busting），确保每次请求 URL 唯一
- **生成方式**：`Math.random()`，一个 0 到 1 之间的 16 位小数
- **服务端校验**：无。服务端忽略该参数，不参与签名计算
- **爬虫处理**：可随机生成，也可写死任意值

### 3.4 `transactionID` 参数作用

- **作用**：客户端生成的唯一请求标识，同时作为签名输入防止请求体篡改
- **生成方式**：`uuid.uuid4().hex`（标准 UUID4，32 位十六进制）
- **出现位置**：
  - JSON 请求体：`"transactionID": "..."`
  - HTTP 请求头：`"transactionid": "..."`（小写）
- **服务端校验**：校验 `sign` 是否匹配，不校验 transactionID 本身格式
- **爬虫处理**：每次请求用 `uuid.uuid4().hex` 生成新的即可

---

## 4. 纯 requests 爬取需要哪些请求头

### 4.1 完整请求头（来自真实浏览器）

```http
accept: application/json
cache-control: no-cache
content-type: application/json;charset=utf-8
cookie: <多个cookie值>
origin: https://flights.ctrip.com
referer: https://flights.ctrip.com/online/list/oneway-ckg-yty?depdate=2026-09-01
scope: d
sec-ch-ua: "Not=A?Brand";v="99", "Google Chrome";v="151", "Chromium";v="151"
sec-ch-ua-mobile: ?0
sec-ch-ua-platform: "Windows"
sign: <md5签名>
token: <WhaleGuard反爬token>
transactionid: <UUID4 hex>
user-agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ...
w-payload-source: <WhaleGuard反爬载荷>
x-ctx-fvpc: <加密值>
x-ctx-ubt-pageid: 10320673302
x-ctx-ubt-pvid: <递增>
x-ctx-ubt-sid: 2
x-ctx-ubt-vid: <UUID>
rms-token: <可空>
sessionid: <数字>
cookieorigin: https://flights.ctrip.com
```

### 4.2 按必要性分类

| 请求头 | 必要性 | 说明 |
|--------|--------|------|
| `Content-Type` | **必须** | `application/json;charset=utf-8` |
| `sign` | **必须** | 服务端校验，不匹配则拒绝 |
| `token` | **必须** | WhaleGuard 校验，缺失返回 432 |
| `w-payload-source` | **必须** | WhaleGuard 校验 |
| `transactionid` | 推荐 | 与请求体保持一致 |
| `User-Agent` | 推荐 | 固定一个常见浏览器 UA |
| `Referer` | 推荐 | 固定为携程机票搜索页 |
| `Origin` | 推荐 | `https://flights.ctrip.com` |
| `Cookie` | 推荐 | 部分 cookie 需要 |
| 其他 UBT 头 | 可选 | 用于追踪，服务端不强制校验 |

---

## 5. 哪些参数生成不了 & 为什么

### 5.1 `token`（WhaleGuard 反爬 Token）

**为什么生成不了：**
- 由 `overlock.js`（WhaleGuard SDK）在浏览器端通过混淆的 JavaScript 动态计算
- 计算过程依赖浏览器指纹、环境检测、时间戳、页面会话信息等
- JS 代码经过重度混淆（变量名替换、控制流平坦化、字符串加密），逆向难度极高
- 携程会定期更新算法，需要持续跟进

**作用：**
- 证明请求来自"真实的浏览器环境"而非脚本
- 每个 token 与当前会话绑定，有效期短（通常几分钟到几十分钟）
- 服务端无 token 或 token 无效时返回 HTTP 432

### 5.2 `w-payload-source`（WhaleGuard 反爬载荷）

**为什么生成不了：**
- 同样由 WhaleGuard SDK 计算
- 包含请求的"环境证明"数据
- 算法与 `token` 类似，混淆实现

**作用：**
- 与 `token` 配合使用，提供额外的反爬验证层
- 可能包含请求签名、环境指纹等信息

### 5.3 `x-ctx-fvpc`

**为什么生成不了：**
- 值为加密字符串（`U2FsdGVkX1...` 是 Base64 编码的 `Salted__` 前缀，符合 OpenSSL 加密格式）
- 可能包含会话的加密指纹

**作用：**
- 加密的客户端环境证明

### 5.4 部分 Cookie

**为什么生成不了：**
- 部分 cookie（如 `_bfa`、`FVP` 等）由 JavaScript 在页面加载时动态计算
- 简单的 cookie（如 `_ga`）可以从首次 GET 响应中获取

---

## 6. 纯 requests 请求的实际结果

```
HTTP/1.1 432 
content-type: text/plain
body: whaleguard block
```

**结论**：没有 `token` 和 `w-payload-source`，纯 requests 请求会被 WhaleGuard 直接拦截。

---

## 7. 是否可以先请求页面获取反爬参数？

**结论：不可以。** 无法通过纯 requests 先请求页面来获取 `token` 等反爬参数。

### 7.1 测试结果

| 请求目标 | HTTP 状态 | 返回内容 | 设置 Cookie |
|---------|----------|---------|------------|
| `flights.ctrip.com/` | 432 | `whaleguard block` | 无 |
| `flights.ctrip.com/online/list/...` | 432 | `whaleguard block` | 无 |
| `www.ctrip.com/` | 200 | HTML 页面 | 无 |
| `s.c-ctrip.com/bee/collect` | 200 | `{"status":0,...}` | `uid`, `suid`（仅限 s.c-ctrip.com 域） |
| `cdid.c-ctrip.com/chloro-device/v4/d` | 200 | 空（X-Original-Status: 1101） | 无 |

### 7.2 关键发现

1. **`flights.ctrip.com` 全站被 WhaleGuard 保护**：即使是首页 HTML，纯 requests 也拿不到（返回 432）。必须先有有效的 `token` 才能访问。

2. **Cookie 全部由 JavaScript 设置**：通过 Playwright 清除 cookie 后重新加载页面，监听所有响应头，`Set-Cookie` 数量为 0。所有 cookie（`UBT_VID`、`_bfa`、`FVP`、`FlightIntl` 等）都是由页面 JS 在浏览器中生成和设置的。

3. **部分端点可访问但无助于获取 token**：
   - `bee/collect` 可以拿到 `uid`/`suid`，但这些 cookie 仅限 `s.c-ctrip.com` 域
   - `cdid` 端点返回 `X-Original-Status: 1101`（可能需要 JS 计算的挑战响应）
   - 即使把这些 cookie 手动设置到 `.ctrip.com` 域，`flights.ctrip.com` 仍然返回 432

4. **`token` 必须由 JS 计算**：`token` 由 WhaleGuard SDK（`overlock.js`）在浏览器端通过混淆的 JavaScript 动态计算，依赖浏览器指纹、环境检测等信息。这不是"请求页面返回的"，而是"页面 JS 执行的产物"。

### 7.3 为什么"先请求页面"策略失效

```
传统反爬（如 Cloudflare）：
  请求首页 → 获得 Cookie/Token → 带 Cookie 请求 API ✅

携程 WhaleGuard：
  请求首页 → 432 被拦截 ❌
  原因：首页 HTML 本身就需要 token 才能访问
  
  可访问的端点（如 bee/collect）：
  → 获得的 cookie 不足以通过 WhaleGuard 校验
  → token 需要 JS 计算，不是服务端返回的
```

### 7.4 唯一的出路

**必须执行 JavaScript 才能获取 token。** 这意味着：
- 必须使用 Playwright / Puppeteer / Selenium 等浏览器自动化工具
- 或者逆向 WhaleGuard JS 用 Python 重写 token 生成（难度极高）

---

## 8. 解决方案

### 方案 A：Playwright 获取 token + requests 批量查询（推荐）

**原理**：
1. 用 Playwright 打开页面，让浏览器正常执行 JS 生成 token
2. 通过路由拦截捕获 `batchSearch` 请求，提取 `token`、`w-payload-source`、cookies
3. 在 token 有效期内，用 requests 携带这些参数批量发起查询

**优点**：
- 比纯浏览器自动化快（token 有效期内可发多个请求）
- token 复用减少浏览器启动次数

**缺点**:
- token 有有效期，过期需重新获取
- 仍需维护 Playwright 环境

### 方案 B：纯 Playwright 浏览器自动化

**原理**：
- 用 Playwright 打开搜索结果页，等待数据加载
- 从 DOM 或网络响应中提取价格数据

**优点**：
- 最稳定，完全模拟真实用户
- 不受 token 算法变更影响（浏览器自动处理）

**缺点**：
- 速度慢，每个页面需要加载时间
- 资源消耗大（每个实例占用内存）

### 方案 C：逆向 WhaleGuard JS 生成 token

**原理**：
- 分析 `overlock.js` 混淆代码
- 用 Python 重写 token 生成算法

**优点**：
- 完全摆脱浏览器，纯 requests 爬取
- 速度最快

**缺点**：
- 工作量极大（JS 重度混淆）
- 携程会定期更新算法，需持续维护
- 法律风险较高（违反网站 ToS 和反爬措施）

---

## 9. 项目文件结构

```
pure_requests/
├── __init__.py            # 包初始化（使 pure_requests 可作为 Python 包导入）
├── ctrip_flight.py        # 核心模块：签名计算、请求体构建、header 生成、请求尝试
├── sign_verify.py         # 签名算法验证（用真实浏览器捕获的数据验证签名正确性）
├── run_check.py           # 运行测试：语法检查 + 签名验证 + 真实请求尝试
├── probe.py               # WhaleGuard 432 拦截行为探测
├── test_page_fetch.py     # 验证"先请求页面获取反爬参数"策略不可行
├── AGENTS.md              # 开发说明
└── RESEARCH.md            # 逆向研究报告
```

---

## 10. 参考数据样例

### 真实捕获的请求体

```json
{
  "adultCount": 1,
  "childCount": 0,
  "infantCount": 0,
  "flightWay": "S",
  "cabin": "Y_S",
  "scope": "d",
  "segmentNo": 1,
  "transactionID": "df2c11f032f54cb8b82a88258b12e317",
  "flightSegments": [{
    "departureCityCode": "CKG",
    "arrivalCityCode": "YTY",
    "departureCityName": "重庆",
    "arrivalCityName": "扬州",
    "departureDate": "2026-09-01",
    "departureCountryId": 1,
    "departureCountryName": "中国",
    "departureCountryCode": "CN",
    "departureProvinceId": 4,
    "departureCityId": 4,
    "arrivalCountryId": 1,
    "arrivalCountryName": "中国",
    "arrivalCountryCode": "CN",
    "arrivalProvinceId": 15,
    "arrivalCityId": 15,
    "departureCityTimeZone": 480,
    "arrivalCityTimeZone": 480,
    "timeZone": 480
  }],
  "directFlight": false,
  "extGlobalSwitches": {
    "useAllRecommendSwitch": true,
    "unfoldPriceListSwitch": true
  },
  "noRecommend": false,
  "extensionAttributes": {
    "LoggingSampling": false,
    "isFlightIntlNewUser": false
  }
}
```

### 真实捕获的签名计算

```
transactionID: df2c11f032f54cb8b82a88258b12e317
dep: CKG
arr: YTY
date: 2026-09-01

sign = md5("df2c11f032f54cb8b82a88258b12e317CKGYTY2026-09-01")
     = 99ed7af1b7851b18173aaec79043118a  ✅
```
