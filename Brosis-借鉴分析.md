# brosis 借鉴分析

> 仓库：https://github.com/AllenBall/brosis ｜ 形态：**macOS 原生菜单栏应用（Swift）** ｜ 许可证 MIT
> 一句话：*"Your entire Mac, as context for your agent"* —— 本地活动记录器，数据不离机、理解层零模型调用，经 MCP 暴露给 AI 助手。

---

## 1. 总览

| 维度 | 内容 |
|---|---|
| **类型** | macOS 桌面菜单栏 App（**不是**浏览器扩展 / Web 应用 / CLI；虽含 `brosis` CLI 与 `brosis-mcp`） |
| **语言/构建** | Swift + SwiftPM（两个独立包 `app` + `core`）、Xcode 26、Metal Toolchain（编 mlx 着色器） |
| **存储** | SQLCipher（密钥存 data-protection 钥匙串）+ FTS5（bigram 中文分词）+ sqlite-vec（int8[1024] 余弦） |
| **本地模型** | mlx-swift 跑 Qwen3-Embedding 0.6B/4B/8B（截断为统一 1024 维），**可选二级层** |
| **系统接入** | macOS Accessibility (AX) API、Screen Recording（OCR 回退） |
| **对外协议** | MCP (Model Context Protocol) **只读**服务 |
| **更新/签名** | Sparkle 2（签名校验失败即拒）+ Developer ID + 苹果公证 |

**四大不可关闭的硬约束**：① 默认不离机（无云/无账号/无遥测）② 存储前脱敏 ③ 存储上限（~30MB/天，默认 10GiB）④ 自包含（静态链接、无 Homebrew、无后台 daemon）。

> **和 OpenBiliClaw 的关系**：它是桌面 App、且核心在做「桌面 AX/OCR 采集」，这部分与你的 Chrome 扩展**架构不重叠**。但它在**「把本地数据变成 AI 可消费上下文」**这件事上的四套机制（MCP 出口、混合检索、确定性规则层、入库前脱敏）正是 OpenBiliClaw 要继续强化 agentic 能力时最该抄的——而且 OpenBiliClaw 已经有 Python API 和 sqlite-vec，迁移成本极低。

---

## 2. 架构分层

```
app/       采集端（T4）：AX 探针 / 适配器规则 / 局部 OCR / 锁定状态机 / 菜单栏 / MCP 集成动作层
  │ 唯一出口：Recorder → 经 IPC 把写请求交给 core 的持钥进程
core/      单一存储服务（T2/T3/T5）：唯一持钥者，开库/写/索引/删除级联/配额/夜间维护/三通道检索/会话化/台账/MCP gate
  ├─ BrosisCore   SQLCipher + FTS5 + sqlite-vec + 规则层（timeline/ledger/patterns 全确定性）
  ├─ BrosisIPC    本地 socket 服务端（不依赖 Core，对端同 Team ID 校验 + 限流）
  └─ brosis-mcp    stdio 上的 MCP 薄服务（只链接 BrosisIPC，连库都不开）
brosis-mcp → IPC → 持钥进程(core 的 Store) → 按 grant 裁剪 → 写审计 → 返回
```

关键设计：**唯一持钥者**，采集端自己不开库；MCP 子进程连库都不开，只经本地 socket 向持钥进程要数据。这意味着「密钥只在进程内存一处」「权限闸门（locked/paused）由持钥进程统一裁决」。

---

## 3. 核心子系统（精读后）

### 3.1 MCP 只读服务暴露本地数据 —— **最该抄**
- 暴露 **10 个只读工具**：`search` / `get_evidence` / `get_context` / `get_timeline` / `get_day_ledger` / `get_week_ledger` / `get_patterns` / `get_item` / `recent_activity` / `list_activity`，时间工具统一 `period` 语法（`today`/`yesterday`/`this_week`/`YYYY-MM-DD`/`a..b`/`<N>h|m|d`）。
- 授权模型：`grants` 表按 client 细粒度授权（mode / apps / timeWindowDays / fields）。**没有 grant 一律全拒**（`MCPIntegration.swift:228-240` 开关顺序：先动配置再动 grant，绝不留下「有授权没入口」的悬空态）。
- 自动集成主流 harness（Claude Code / Codex / Cursor）：探测安装 → 优先官方 CLI 写配置 → 否则直接改配置文件（**原子写 tmp + `replaceItemAt`**，改前留时间戳备份）→ 发 grant（`MCPIntegration.swift:247-291`）。
- 「学习模式」：读 `mcp_audit` 里被 `no_grant` 拒掉的 client 名，列出待授权项（`MCPIntegration.swift:306-319`）。

### 3.2 三通道混合检索 + RRF 融合 —— **与 OpenBiliClaw 高度同构**
- `QueryRouter`（`Retrieval+Support.swift:12-57`）按查询串长相路由：**精确字段通道**（前缀 `url:`/`host:`/`path:`/`app:`/`title:` 走两步式小表查 id，空集直接返回，p50 0.00ms）、**FTS 通道**、**1–2 字扫描通道**。
- 三条通道是**并集**，多跑的精确字段查询是两步式、近乎免费。
- 向量召回（sqlite-vec int8[1024] 余弦）+ FTS 后做 **RRF 融合（k=60）**。OpenBiliClaw 已有 `embedding_cache.db` + sqlite-vec，这套「字段路由 + FTS + 向量 + RRF」可直接套到 `conversation_archive` / 内容库检索。
- `LIKE` 转义做了严格 `escape`（防 `%`/`_` 注入，`Retrieval+Support.swift:63-72`）。

### 3.3 确定性规则层（理解层零模型调用）—— **效率范式**
- 会话化 → 日/周台账 → 活动模式（热力/常用时段/会话/切换对/工作块）全部由**确定性规则**生成，不调任何模型（`Store+Sessions.swift` / `Store+Ledger.swift` / `Store+Patterns.swift`）。
- 向量搜索只是**可选二级层**，且模型在本地跑。原则：**能用规则做的绝不用 LLM**，LLM 仅做可缺失的增强。
- 时间口径极严谨：`DayCalendar`（显式时区、ISO 周一、半开区间 `[start,end)`、`bucketStart` 对齐桶）、`IntervalMath.unionMilliseconds` 合并重叠区间算「总在线」不重复计、`TimeBucketKind` 把 `source_state` 分成 dwell/active/unknown 三类、三者分列不相加（`Retrieval+Support.swift:80-239`）。这套时间聚合口径值得任何本地活动/日志系统借鉴。

### 3.4 入库前规则脱敏 —— **隐私范本（可直接移植思路）**
- `Redaction.swift`：纯函数 `redact(_:)`，按 **gitleaks 规则子集 + Luhn 卡号校验 + 验证码启发式** 替换成 `[REDACTED:<类型>]`，**只删不改语义**（不留前后几位）。
- 7 类规则：PEM 私钥 / AWS AK / GitHub 5 种 token / Slack token / 通用 `key=value` / 过 Luhn 的卡号 / 验证码。规则按优先级排序、重叠时靠前赢、从后往前替换。
- **标题、URL、`kAXDocument` 文件路径也过同一套规则**（不只是正文）。
- **33 条测试向量**（21 正 / 12 反），反例里刻意放了 13 位毫秒时间戳、16 位订单号、11 位手机号——只有 Luhn 能区分，防误伤。
- 命中只留「类型+条数」事件，**绝不写被删内容本身**。
- 若 OpenBiliClaw 未来存用户可见内容（日记/截图/网页正文），这套「入库前一道 + 查询时一道」的脱敏两层设计可直接用 Python 重实现。

### 3.5 适配器规则引擎（采集描述与执行分离）—— **映射你现有的抽取适配器**
- `AdapterRule`（纯数据）+ `AdapterEngine`（执行），每个常用 App 一条规则：`bundleIDs` / 区域定位 `ElementLocator` / 读取方式 `ReadMethod` / 视口裁剪 / 气泡归属 / Chromium 私有属性开关（`AdapterRule.swift`）。
- 定位器种类极全：`role` / `identifier` / `rolePath` / `relativeRect` / `insetRect`（定点内缩，三栏布局不能用比例！）/ `domClass`（Chromium 唯一稳定语义锚点）/ `webArea`（多 web area 按标题挑，飞书 `messenger-chat` 而非最富的 `messenger`）/ `primaryWebArea`（Chrome 排除 `chrome://`/`devtools://`）。
- **关键经验**：① 多 AXWebArea 窗口「取最富」会读错（飞书侧栏永远比正文富）→ 按 AXTitle 挑；② Chromium 私有 `AXEnhancedUserInterface` 开启时若客户端异常断开会**重放最近按键到焦点字段**（screenpipe #3884）→ 默认关闭；③ 固定宽度侧栏用定点 `WindowInset` 而非比例，比例在另一台屏就整体偏；④ 导航后空读需重试（订阅 `AXLoadComplete` 作就绪触发）。
- 映射到 OpenBiliClaw：你现有的 `content/zhihu.ts`、`content/xhs/` 本质也是「按站点声明抽取规则」，brosis 的「规则纯数据 + 引擎执行 + 合成树单测」范式比当前内联抽取更可维护。

### 3.6 本地 IPC 安全 — 工程范本
- `BrosisIPC/PeerIdentity.swift`：`getpeereid` + audit token + `SecCodeCheckValidity`，校验对端同 Team ID；`RateLimiter.swift` 按客户端滑动窗口（单调时钟，时钟回拨不放大配额）。任何「本地服务↔子进程」组合都该照此做。

---

## 4. 可迁移手法清单（按 OpenBiliClaw 价值排序）

| # | 手法 | 源码位置 | 在 OpenBiliClaw 的落点 |
|---|---|---|---|
| 1 | **MCP 只读服务**暴露本地数据，grant 细粒度授权 | `MCPIntegration.swift` / `core/StoreMCPService.swift` | 给 OpenBiliClaw 的 `openbiliclaw.db`/`conversation_archive`/`content.db` 加一个 MCP server（read-only），让 Claude Code/Cursor 直接查你的数据 |
| 2 | **字段路由 + FTS5 + sqlite-vec + RRF(k=60)** 混合检索 | `Retrieval+Support.swift` / `Store+Search.swift` | 套到内容库 / 对话归档检索，复用已有的 sqlite-vec |
| 3 | **确定性规则层**替代 LLM 做理解/聚合 | `Store+Sessions/Ledger/Patterns.swift` | 时间线/统计/分类先用规则，LLM 仅做可选增强，省 token 又稳 |
| 4 | **入库前规则脱敏**（gitleaks+Luhn+验证码，33 向量） | `Redaction.swift` | 任何存用户可见内容的场景（日记/网页正文）加 Python 版脱敏 |
| 5 | **严格时间口径**（半开区间/桶对齐/区间并集/三类时间） | `Retrieval+Support.swift:80-239` | 日记/活动/看板的时长与日界统计 |
| 6 | **规则纯数据 + 引擎执行 + 合成树单测** | `AdapterRule.swift` / `AdapterEngine.swift` / `AdapterVectors.swift` | 重构 `content/*.ts` 抽取层，按站点拆规则、可单测 |
| 7 | **原子写配置**（tmp+`replaceItemAt`+时间戳备份） | `MCPIntegration.swift:247-291` | 任何写本地配置/JSON 的健壮性 primitive |
| 8 | **本地 IPC 对端校验 + 限流** | `BrosisIPC/PeerIdentity.swift` / `RateLimiter.swift` | 若加 native companion / 子进程 |
| 9 | **硬性 fail-closed 约束**（不离机/必脱敏/固定上限） | `app/README.md` 四大约束 | 隐私/安全设计原则 |

---

## 5. 不建议照搬

- **AX/OCR 桌面采集**：Swift/Carbon/ScreenCaptureKit 专属，Chrome 扩展无法做（扩展只能 content script + `chrome.webRequest`）。
- **SQLCipher + Metal/mlx 构建链**：重且平台绑定（Xcode/macOS），OpenBiliClaw 用 Python + 现有 SQLite 即可；加密若需要可换 SQLCipher 的 Python 绑定（但不急）。
- **Sparkle/公证/DMG 分发**：只在你要发 macOS 原生 app 时才需要。
- **macOS 菜单栏形态**：OpenBiliClaw 的主交互是浏览器扩展 + Web UI，不必做菜单栏 App。

---

## 6. 与已分析三个项目的对比

| 项目 | 形态 | 对 OpenBiliClaw 最大价值 |
|---|---|---|
| TraeWorkAssistant-mac | Tauri 桌面 App | 工程健壮性 primitives（原子写/冷却状态机/代理回环） |
| wikitok | 纯前端卡片流 | TikTok 式竖向卡片 feed（你知乎扩展的视觉形态） |
| lushu | 网页+Chrome 扩展 | MAIN world 驱动 + LLM 抽取（与扩展架构同构） |
| **brosis** | macOS 原生 App | **MCP 出口 + 混合检索 + 确定性规则层 + 入库前脱敏**（agentic 数据层） |

---

## 7. 精读文件清单（按优先级）

1. `core/Sources/BrosisCore/Retrieval+Support.swift` —— 查询路由 / LIKE 转义 / 日历 / 区间并集 / 三类时间归属（**检索与时间口径范本**）
2. `app/Sources/brosis/MCP/MCPIntegration.swift` —— MCP 集成动作层、grant、原子写配置（**MCP 出口范本**）
3. `app/Sources/brosis/Redaction.swift` —— 入库前规则脱敏 + 33 测试向量（**隐私范本**）
4. `app/Sources/brosis/Adapters/AdapterRule.swift` —— 适配器规则纯数据结构（**抽取层范式**）
5. `core/README.md` / `app/README.md` —— 完整架构、schema、决策记录（D16/D22/D25 等）
6. `core/Sources/BrosisCore/Store+Search.swift` / `Store+Sessions.swift` —— 三通道检索与确定性规则层实现
