# browser_automation 研究报告

> 结论：当前 `browser_automation/` 是唯一已经验证成功的方案。
> 研究日期：2026-08-30。

## 1. 研究目标

在纯 requests 和 Playwright + requests 两条路线都失败后，验证是否可以通过真实浏览器完成完整查询，并直接从浏览器侧拿到携程航班列表数据。

最终答案是可以，而且实现路径比前两条更稳定。

## 2. 为什么前两条方案失败

### 2.1 纯 requests

`pure_requests/` 方案会被 WhaleGuard 拦截，返回 HTTP 432。

根因不是签名本身，而是请求缺少浏览器内生成的会话上下文，包括页面侧动态生成的 cookie 和相关请求上下文。

### 2.2 Playwright + token + requests

`playwright_token/` 方案虽然能从浏览器里拿到部分动态信息，但 token 和 `w-payload-source` 都带有明显的会话和请求态特征，抽出来后再到浏览器外复用仍然不稳定，最终依然失败。

## 3. 当前方案的实现方式

`flight_crawler.py` 的做法不是逆向签名，也不是把浏览器只当成 token 提取器，而是直接让浏览器把整条链路跑完：

1. 启动 Chromium
2. 首次访问首页 `https://flights.ctrip.com/`
3. 等待关键 cookies 出现，当前代码里关注的是 `_bfa` 和 `FVP`
4. 跳转到航班列表页，例如 `https://flights.ctrip.com/online/list/oneway-ckg-yty?depdate=2026-09-01`
5. 用 `page.expect_response()` 等待 `batchsearch` 响应
6. 直接解析响应 JSON，映射成 `FlightInfo`
7. 默认过滤掉中转航班，只保留直飞

这条链路的关键点是：不在浏览器外重建会话，也不尝试复用浏览器内生成的签名参数。

## 4. 从 `flight_crawler.py` 固化下来的观察

### 4.1 会话建立是必须步骤

当前代码把首页访问当成会话建立步骤。没有 `_bfa` 和 `FVP`，后续列表页虽然看起来能打开，但返回结果会不完整，常见表现是“未找到符合条件的航班”。

### 4.2 不要拦截 `batchSearch`

`page.route()` + `route.fetch()` + `route.fulfill()` 这类拦截方式会破坏原始请求上下文，容易让返回数据变空。当前方案选择的是只监听响应，不修改请求。

### 4.3 响应到达后不能立刻取值

在部分页面上，`batchSearch` 响应到达时，页面 DOM 还没完全渲染。如果后续要从 DOM 取值，需要等待更长时间。

不过当前实现已经改为直接解析响应 JSON，所以这条风险已经基本消失。

### 4.4 价格字段是行程级而非航段级

为了确认 `adultPrice`、`childPrice`、`infantPrice`、`cabin`、`seatClass`、
`baggageTag`、`freeOilFeeAndTax`、折扣率、剩余票数、退改签标签这些字段
到底属于航段还是行程，做了一次实时抓取（`include_transfer=True`）检查中转行程：

- 中转行程 CZ2339-CZ3957（重庆-广州-扬州，2 个航段）的 priceList 中
  `adultPrice=610`。若该价格是航段级，行程总价应为 610×2=1220，
  但页面上该行程售价就是 610 —— 说明它是**整个行程的总价**
- 两个航段解析出的价格、舱位、行李、折扣完全相同，与"行程级"一致
- 中转时 `seatClass` 本身就是拼接码（`@Y-Y/T`），进一步说明该字段按行程生成

**结论**：所有价格与产品字段均为 flight/行程级别。`SegmentInfo` 只保存
航班时刻等纯航段信息，价格统一放在 `FlightInfo` 上，数据库层（`db_storage`）
也按此结构设计。

## 5. `results.json` 的样本结论

当前样本来自一次成功查询：

- 出发：`CKG`
- 到达：`YTY`
- 日期：`2026-09-01`

样本里可以确认的内容包括：

- 结果是直飞行程为主，`transfer_count` 为 `0`
- 存在大量共享航班，`flightNo` 和 `operateFlightNo` 不一致很常见
- 成人票价分布跨度较大，样本里能看到 `320` 到 `670` 之间的多个档位
- 部分档位带付费 `servicePackage`（如 `{"id": "180", "price": 48}`），解析时已剔除
  （`price > 0` 的服务包档位不参与最低价比较），确保取到的是裸票价
- `free_airport_fee`、`free_fuel_fee`、`discount_rate`、`ticket_count` 等字段都能稳定取到
- 航站楼、航班号、出发到达时间、机型、行李额、退改签规则都在响应里具备

这说明 `batchSearch` 返回的数据已经足够支撑完整的航班列表展示和价格比较。

## 6. 性能数据

`time_benchmark.py` 的当前观测值大致如下：

| 指标 | 结果 |
| --- | --- |
| 首次查询 | 约 7.57 秒 |
| 后续查询 | 约 6.09 秒 |
| 会话建立 | 约 1.3 秒 |
| 列表页导航 + `batchSearch` | 约 4 到 5 秒 |
| 响应解析 | 相对很快，几乎不是主要开销 |

这意味着当前方案的时间成本主要在浏览器启动、会话建立和列表页等待上，而不在解析逻辑本身。

## 7. 优点和局限

### 优点

- 完全遵循真实浏览器行为
- 不需要在浏览器外复现 WhaleGuard 的签名逻辑
- 数据来自响应 JSON，结构比 DOM 更完整
- 成功率和稳定性明显优于前两条路线

### 局限

- 启动 Chromium 有固定开销
- 每次查询仍然偏慢，不适合高并发
- 对页面和响应结构变化仍然敏感
- 批量查询为顺序执行，不是真正并行
- 响应体通过 `page.on("response")` 回调在回调内立即读取（`resp.json()`），避免了
  `Response.json: ... evicted from inspector cache` 问题（页面导航会清掉前一个
  batchSearch 的响应缓存，若在块外/延迟读取会触发该错误）。连续 4 个批量查询
  实测全部成功，无缓存淘汰报错

## 8. 后续方向

1. 如果继续做吞吐优化，可以尝试复用一个浏览器下的多个 context，但要控制并发上限。
2. 如果要扩展到更多航线，建议继续用 `results.json` 作为样本对照，逐步确认字段稳定性。
