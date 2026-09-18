# pure_requests — 纯 Requests 携程机票爬虫

本文件夹包含使用纯 `requests` 库（不依赖浏览器自动化）爬取携程机票价格的脚本。

## 文件结构

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

## 文件说明

### ctrip_flight.py（核心模块）

提供以下功能：

- **`compute_sign(transaction_id, dep, arr, dep_date)`** — 计算 `sign` 请求头
  - 算法：`md5(transactionID + depCity + arrCity + depDate)`
  - 已用真实浏览器数据验证通过

- **`generate_transaction_id()`** — 生成 32 位十六进制 UUID4 作为 transactionID

- **`build_search_body(transaction_id, dep, arr, dep_date, ...)`** — 构建完整的 JSON 请求体

- **`build_headers(transaction_id, dep, arr, dep_date)`** — 构建所有可复现的 HTTP 请求头

- **`attempt_batch_search(dep, arr, dep_date, token=None)`** — 发起实际的 batchSearch 请求

- **`_CITY_INFO`** — 城市元数据字典（IATA 代码 → 城市名/ID/时区）

### run_check.py（测试入口）

运行方式：
```bash
# 从项目根目录运行
python pure_requests/run_check.py
```

功能：
1. 语法检查（`py_compile`）
2. 签名验证（对比真实浏览器捕获的签名）
3. 展示可复现的请求参数
4. 发起真实请求（预期返回 432）

### sign_verify.py

用 Playwright 捕获的真实浏览器数据验证签名算法：
- transactionID: `8992e1c46f7942da9066ad5a71ca5f0c`
- 预期签名: `e0415e976e9a3972fd0afd39a923897b`

### probe.py

探测 WhaleGuard 对纯 requests 请求的拦截行为（返回 432）。

### test_page_fetch.py

验证"先请求页面获取反爬参数"策略不可行性：
- 测试多个端点（homepage、bee/collect、cdid 等）
- 结论：无法通过纯 requests 获取 token

### RESEARCH.md

完整的逆向研究报告，包含：
- 目标 API 分析
- 验证方式与结论
- 各参数来源分析
- 纯 requests 的局限性说明
- 替代解决方案

## 与 doc/ 目录的关系

`doc/` 目录存放原始抓取数据，是本模块中参数和请求体结构的参考来源：

| doc/ 文件 | 内容 | 对应的脚本引用 |
|-----------|------|---------------|
| `doc/curltest.bat` | 浏览器原始 curl 命令 | `ctrip_flight.py` 的 URL 和参数来源 |
| `../doc/请求体.json` | batchSearch JSON 请求体 | `ctrip_flight.py` → `build_search_body()` 的结构参考 |
| `../doc/请求头.txt` | batchSearch HTTP 请求头 | `ctrip_flight.py` → `build_headers()` 的结构参考 |
| `../doc/响应体.json` | batchSearch JSON 响应 | 理解 API 返回数据结构 |
| `../doc/响应头.txt` | batchSearch HTTP 响应头 | 理解响应元数据 |
| `../doc/网页源码.html` | 携程搜索页 HTML 源码 | 包含 `GlobalSearchCriteria` JS 对象，是请求体结构的原始来源 |

## 签名算法（已验证）

```python
import hashlib

def compute_sign(transaction_id: str, dep: str, arr: str, dep_date: str) -> str:
    src = transaction_id + dep + arr + dep_date
    return hashlib.md5(src.encode("utf-8")).hexdigest()
```

**验证数据：**
```
transactionID: 8992e1c46f7942da9066ad5a71ca5f0c
dep: CKG, arr: YTY, date: 2026-09-01
computed sign: e0415e976e9a3972fd0afd39a923897b  ✅
```

## 纯 requests 的局限性

| 可生成 ✅ | 不可生成 ❌ | 原因 |
|----------|------------|------|
| `v` 参数（随机数） | `token` | WhaleGuard JS 动态计算 |
| `transactionID`（UUID4） | `w-payload-source` | WhaleGuard JS 动态计算 |
| `sign`（MD5） | 部分 Cookie | JS 在浏览器中设置 |
| JSON 请求体 | `x-ctx-fvpc` | 加密值 |
| 静态 HTTP 头 |  |  |

**实际请求结果：** HTTP 432 `whaleguard block`

## 运行要求

- Python 3.14+
- `requests` 库（已在 .venv 中安装）

## 运行方式

```bash
# 从项目根目录运行测试
python pure_requests/run_check.py

# 运行签名验证
python pure_requests/sign_verify.py

# 运行 432 探测
python pure_requests/probe.py

# 运行页面获取测试
python pure_requests/test_page_fetch.py
```
