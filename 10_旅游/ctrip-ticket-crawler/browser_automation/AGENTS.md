# browser_automation 使用说明

这个目录包含当前已验证成功的方案：用真实浏览器完成会话建立，再直接读取 `batchSearch` 的响应 JSON。

## 这个方案做了什么

- 先访问 `https://flights.ctrip.com/`，等待关键 cookies 出现，主要是 `_bfa` 和 `FVP`
- 再跳转到航班列表页，例如 `https://flights.ctrip.com/online/list/oneway-ckg-yty?depdate=2026-09-01`
- 通过 `page.expect_response()` 等待 `batchsearch` 响应
- 直接解析响应 JSON，不依赖 DOM 抓取
- 默认只保留直飞航班，除非显式要求保留中转（`include_transfer=True`）
- 默认剔除共享航班（任一航段存在执飞航班号即视为共享），除非显式设置 `include_shared=True`

## 主要文件

- `flight_crawler.py`：核心爬虫实现
- `results.json`：运行样本输出
- `time_benchmark.py`：耗时基准脚本
- `RESEARCH.md`：验证过程与结论

## 核心类和方法

- `SegmentInfo`：单个航段的信息封装（只含航班信息，不含价格）
- `FlightInfo`：单个行程的信息封装，包含价格、舱位、行李、折扣等字段
  （**所有价格与产品字段均为行程级**，见下文验证结论）

### 价格选取规则（`_parse_itinerary`）

1. 先剔除带付费服务包的档位：`priceList` 中含 `servicePackage` 且其 `price > 0` 的档位不参与比较（此类为选座/餐食/行李升级包等增值产品，其 `adultPrice` 不是裸票价）
2. 在剩余档位中依次比较：成人价最低 → 儿童价最低 → 婴儿价最低（婴儿价缺失按 +∞ 处理）
3. 全部并列时取先出现的档位

### 价格字段归属（实测结论）

`adult_price`、`child_price`、`infant_price`、`cabin`、`seat_class`、`baggage_kg`、
`free_airport_fee`、`free_fuel_fee`、`discount_rate`、`ticket_count`、`penalty_tag`
**全部是 flight/行程级别**，不是 segment 级别。证据：

- 中转行程（如 CZ2339-CZ3957）两个航段的 priceList 值完全相同，
  adultPrice=610 是整个行程的总价（若是段级价格，总价应为 610×2=1220）
- 中转时 `seatClass` 本身就是拼接码格式（如 `@Y-Y/T`），说明该字段按行程生成

因此 `SegmentInfo` 不含任何价格字段，价格统一存放在 `FlightInfo` 上；
`penalty_tag` 取自 `flightSeatList[0].penalty.defaultPenaltyTag`（如 "退改¥335起"）。
- `SearchResult`：一次查询的返回对象
- `FlightCrawler.start()` / `stop()`：启动和关闭浏览器
- `FlightCrawler._ensure_session(timeout_ms=30000)`：访问首页并等待会话 cookies
- `FlightCrawler.search(dep, arr, date, include_transfer=False, include_shared=False, timeout_ms=30000)`：单次查询
- `FlightCrawler.batch_search(queries, include_transfer=False, include_shared=False, delay_range=(2, 5))`：批量顺序查询

## 使用方式

```python
from browser_automation.flight_crawler import FlightCrawler

with FlightCrawler(headless=True) as crawler:
    result = crawler.search("CKG", "YTY", "2026-09-01")
    print(result.success)
    for flight in result.flights:
        print(flight.itinerary_id, flight.adult_price)
```

批量查询：

```python
with FlightCrawler(headless=True) as crawler:
    results = crawler.batch_search([
        {"dep": "CKG", "arr": "YTY", "date": "2026-09-01"},
        {"dep": "PEK", "arr": "SHA", "date": "2026-09-05"},
    ])
```

命令行运行：

```powershell
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\python.exe browser_automation\flight_crawler.py
```

## 已知注意事项

- `page.route()` 不适合拦截 `batchSearch`，容易破坏浏览器侧的签名和上下文
- Windows 控制台需要 UTF-8 输出，否则容易出现编码错误
- 结果落盘在 `results.json`
