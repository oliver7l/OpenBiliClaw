# ctrip-ticket-crawler

一个围绕携程机票列表页的价格爬取实验项目。当前已验证的可行方案是 `browser_automation/` 下的纯浏览器自动化：先让 Chromium 建立真实会话，再跳转到航班列表页并直接读取 `batchSearch` 响应中的数据。

已验证的入口 URL：

`https://flights.ctrip.com/online/list/oneway-ckg-yty?depdate=2026-09-01`

## 结论

- `pure_requests/`：纯 HTTP 请求会被 WhaleGuard 拦截，返回 432
- `playwright_token/`：只提取 token 后再转请求，仍然失败
- `browser_automation/`：当前唯一验证成功的方案

## 目录说明

- `browser_automation/`：最终可行方案，包含爬虫实现、研究记录和结果样本
- `pure_requests/`：纯 requests 方案的研究与验证记录
- `playwright_token/`：Playwright + requests 的中间方案
- `doc/`：抓包、响应体、网页源码等原始资料

## 快速开始

环境准备：

```bash
uv sync
pip install playwright
playwright install chromium
```

示例调用：

```python
from browser_automation.flight_crawler import FlightCrawler

with FlightCrawler(headless=True) as crawler:
    result = crawler.search("CKG", "YTY", "2026-09-01")
    print(result.success)
    for flight in result.flights:
        print(flight.itinerary_id, flight.adult_price)
```

命令行运行：

```powershell
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\python.exe browser_automation\flight_crawler.py
```

## 需要注意的点

- 首次查询需要先访问首页建立会话 cookies，关键是 `_bfa` 和 `FVP`
- 默认只保留直飞航班；如果要保留中转航班，需要在调用时显式开启
- 批量查询已实测稳定：连续 4 个查询（含跨航线）全部成功，无 `evicted from inspector cache` 报错
- Windows 控制台请保持 UTF-8 输出，否则容易出现编码错误

## 相关文档

- `browser_automation/AGENTS.md`
- `browser_automation/RESEARCH.md`
- `doc/响应体结构.md`

