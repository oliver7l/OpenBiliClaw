# TraeWorkAssistant-mac 架构分析报告

> 分析对象：`github.com/ailogk/TraeWorkAssistant-mac`（v2.9.4，macOS 移植版）
> 分析目的：作为 OpenBiliClaw（知乎首页卡片化 Chrome 扩展）的参考，提炼可借鉴的架构手法
> 克隆位置：`/Volumes/固态硬盘1T/002-探索项目/_ref_TraeWorkAssistant-mac`
> 日期：2026-09-14

---

## 0. 一句话定位

一款 **Tauri 2 + React + Rust + Python** 的 **Windows/macOS 桌面端多账号管理工具**，针对字节的 AI IDE「Trae Work」，把
**签到 / 本地 MITM 代理捕获 JWT / 多账号登录态切换 / 设备指纹隔离 / 本地 OpenAI 兼容 API 网关** 收敛到一个界面里。
官方声明与 Trae 官方无关联、仅供学习研究、可能违反服务条款。

**与你项目的本质差异**：它是**独立桌面应用**（能调用系统 API、装系统证书、起本地代理），而 OpenBiliClaw 是**浏览器扩展**（运行在网页上下文，能力被浏览器沙箱限制）。
所以下面的「可迁移点」要分两层看：**工程手法（通用，可搬）** vs **MITM/系统级手法（扩展做不到，只能替代实现）**。

---

## 1. 分层架构

```
┌─────────────────────────────────────────────────────────────┐
│ Presentation  React 18 + TS + Tailwind + Zustand + Recharts  │  src/（前端）
│   App.tsx 壳（TitleBar+Sidebar+TopBar+Modal+Toaster）         │
│   store.ts 单一真相 ｜ pages/ 7 个页面 ｜ lib/tauri.ts 封装    │
└───────────────┬─────────────────────────────────────────────┘
                │ Tauri invoke / Tauri Event Bus
┌───────────────▼─────────────────────────────────────────────┐
│ Bridge (Rust / src-tauri)                                    │
│   commands/   env cert accounts checkin proxy switch          │
│               oauth profile api_server sms_code updater ...  │
│   ├─ fs_utils   原子 JSON 读写 + mask + 时间辅助              │
│   ├─ vault      Stronghold + OS Keychain 凭据加密（v2.9）     │
│   ├─ jwt        解析/续期/不校验签名                          │
│   ├─ python     spawn_script 注入 TRAEDATA_DIR                │
│   └─ api_server axum 内嵌 HTTP 服务 + 账号池 + SSE 转换       │
└───────┬───────────────────────────┬─────────────────────────┘
        │ 子进程                      │ 系统调用
┌───────▼────────────┐   ┌──────────▼─────────────────────────┐
│ Python Core        │   │ PowerShell 桥（仅 Win）              │
│ device_proxy.py    │   │ trae-switch-bridge.ps1              │
│  MITM 代理+JWT捕获 │   │ 登录态切换 + 6 层设备标识重置         │
│ auto_checkin.py    │   │                                      │
│  批量签到+冷却状态机│   │                                      │
└────────────────────┘   └──────────────────────────────────────┘
```

数据全部落本地 `%APPDATA%/TraeWorkAssistant/`（macOS 对应 `~/Library/Application Support/`），**零外部网络**。

---

## 2. 技术栈与选型理由

| 层 | 选型 | 为什么（作者原话） |
|---|---|---|
| 外壳 | **Tauri 2**（Rust） | 包体 8–15MB（远小于 Electron），可调用系统 API（注册表/证书/计划任务） |
| 前端 | React 18 + TS + Vite 5 + Tailwind 3 + Zustand 4 + Recharts 2 | Web 技术栈还原设计稿；状态用 Zustand；图表用 Recharts |
| 核心逻辑 | **Python 3.13**（`auto_checkin.py` / `device_proxy.py`） | 复用已验证的签到/代理逻辑，降低重写风险 |
| 登录态切换 | **PowerShell 5.1**（系统自带） | 复用已验证的备份/恢复/机器码重置逻辑 |
| API 网关 | **Rust axum**（内嵌，复用 Tauri tokio runtime） | OpenAI 兼容端点 + SSE 转换 + 账号池调度，无需独立进程 |
| HTTP 客户端 | **ureq**（同步）+ `spawn_blocking` | 双 Client：短请求 120s / 流式仅 ResponseHeaderTimeout |
| 凭据存储 | **Stronghold vault + OS DPAPI**（`vault.rs`） | jwt/refresh_token 加密落盘，主密码仅本机当前用户可解 |

**不采用**：Electron（太大）、WPF/WinUI（样式成本高）、PyQt（视觉不达要求）。
注意 `AGENT.md` 红线：**零新增依赖需评估**；**禁止引入 React Router / Redux / 额外 UI 库**；**禁止 `window.confirm()`**（WebView 不支持，用自定义 Modal）。

---

## 3. 核心子系统详解

### 3.1 本地 MITM 代理（`device_proxy.py`）—— 全项目最亮眼的能力

- **透明 HTTPS 解密**：监听 `127.0.0.1:8899`，对每个抵达的 `CONNECT` 域名（trae.cn / mchost.guru / bytedance / volcengine 等）签自发 CA 叶子证书并解密。
- **JWT 自动捕获写回**：嗅探 `authorization: Cloud-IDE-JWT` / `x-cloudide-token` / `x-icube-token`，从 payload 解 `data.id` 作账号主键，**不限 host**（鲁棒兼容未列出的子域）；按 user_id 匹配，带 **exp 防降级**（新 token 过期时间 ≤ 旧的就跳过覆盖）。
- **设备头改写**：仅对签到接口 `/checkin_credits/claim` 注入每账号独立的 `x-device-id` / `x-market-user-id` / `vscode-sessionid`，绕过「每设备每天」配额。
- **上游代理串联（v2.4.3）**：先读当前系统代理（用户的 VPN）作上游，非 Trae 域名的 CONNECT 走 `tunnel_raw` 透明转发；**退出原样还原**系统代理，避免「开代理后外网断网」。
- **WebSocket 帧解析**：双向隧道里解析 WS 帧（去掩码 / 续帧重组 / permessage-deflate 解压），抓最关键的 `create_agent_task` 流量。
- **降级兜底**：新版 Trae 鉴权请求不走 `--proxy-server`，于是加了 `--capture-local` 直接解密本地 Chromium Cookies/leveldb 提取 JWT（仅 Windows 有效）。
- **CA 生成双路径**：有 `cryptography` 库走内存签发；macOS 系统 python3 没有则退化到 `/usr/bin/openssl` CLI（兼容 LibreSSL，统一走 `-config` 扩展文件）。

### 3.2 多账号签到 + 错误冷却状态机（`auto_checkin.py`）

- 读 `checkin_accounts.json` → 逐账号 `status_check` + `signin` → 写 `checkin_summary.json` / `credits_history.json`。
- **6 类错误分类冷却**：`PlanLimit`(12h) / `SoftRate`(60s) / `SessionDead`(永久，需重登) / `NotFound`(60s) / `Server`(10min) / `Client`(10min)；连续 3 次 `Server/Client` 才冷却（容错瞬时抖动）。
- **关键修复**：脚本**强制 `NO_PROXY=*` 直连**，绝不能走本地 MITM 代理（否则代理进程一崩，端口变死端口，签到报 10061）。这与 Rust 侧 API 网关 `ureq` 不启用 proxy-from-env 是同一思路。
- **确定性设备派生**：`seeded_hex`（SHA-256 迭代）从 uid 派生 `device_id`，同一账号恒等；替代 `random.Random(seed)`（后者会产生全 '2' / 全 '5' 病态序列）。

### 3.3 账号池调度 + SSE 协议转换（API 网关，Rust `axum`）

- 内嵌 `axum` 服务（复用 Tauri tokio runtime），暴露 `POST /v1/chat/completions` + SSE 流式，兼容 OpenAI / Anthropic 协议。
- **`ApiPool` 调度状态机**（`pool.rs`）：内存索引 + 冷却/禁用；策略 `expire_first`（默认，过期优先→积分降序）/ `credit_first` / `random`；过滤零积分、已过期、冷却中、非选中分组；单请求最多换号 3 次（`MaxRotate`）；`note_error` 联动冷却。
- **协议转换**（`sse.rs`）：把 Trae SOLO 自定义 SSE 事件（`output/thought/token_usage/done/error`）逐 chunk 转成 OpenAI `chat.completion.chunk` / Anthropic `message_*`；**纯函数 + 中单元测试**，含错误下发的「是否已发数据」语义（决定能否重试）。
- **用量统计 + 多 Key 鉴权**：`api_usage.json` 按日聚合（90 天）；`api_keys.json` 多 Key 独立配额；未配启用 Key 时默认拒绝（防本机任意进程无鉴权消耗上游额度）。

### 3.4 多账号登录态隔离 / 快照（PowerShell 桥 + `profiles/`）

- `trae-switch-bridge.ps1` 非交互模式（`-Action Switch|Save|ResetDeviceIds` + `-Json` NDJSON 进度），精准备份 **9 类核心登录文件**（storage.json / state.vscdb / machineid / aha / Network 等），**非全量镜像**。
- **6 层设备标识重置**：machineid / storage.json 遥测 / aha.device / 注册表 MachineGuid / webview 追踪 / aha TinyStorage，防多账号关联。
- 切换流程：预检目标快照 → 关 Trae → 存当前到 last → 恢复目标 → 启 Trae。

### 3.5 凭据加密（`vault.rs`，v2.9.0）

- jwt / refresh_token 迁入 **Stronghold vault**（`conf/vault.stronghold`），主密码经 OS Keychain（Windows DPAPI）保护（`vault_key.bin`），JSON 落盘占位化。
- 迁移失败降级明文 + 下次启动重试；Python 签到走**临时解密文件**（`--accounts-file`，用后即删 + 启动清理崩溃残留）。

### 3.6 前端（React/Zustand）与 Tauri 命令/事件契约

- `store.ts` 单一真相，`init()` 在 `App.tsx` 启动一次；`lib/tauri.ts` 封装 `invoke` + 事件订阅。
- 契约要点：invoke **顶层参数名**跟随 Rust 函数签名（驼峰不替换）；**嵌套对象**字段保持 **snake_case**（serde 默认）。前端 `types.ts` 与 Rust DTO 完全一致。
- 长任务用 **Tauri Event Bus** 回传（如 `checkin-progress` 逐条 NDJSON），避免 invoke 阻塞。

---

## 4. 工程化亮点（可直接借鉴的「经验值」）

这些是**与具体业务无关的健壮性手法**，质量很高，值得在 OpenBiliClaw 里复刻：

1. **原子写：`tmp + os.replace`**（Rust `fs_utils` / Python `save_json`）。断电也不损坏 JSON。→ 扩展里写 `chrome.storage.local` 虽无 partial-write 问题，但任何本地文件/IndexedDB 批量更新都应学这套。
2. **`safe_lock()` 毒化恢复**：`lock().unwrap_or_else(|e| e.into_inner())`，Mutex 被 panic 毒化后仍能恢复数据继续跑，而不是崩。→ 多 worker 场景下通用。
3. **防代理回环**：上游 HTTP 客户端一律 `NO_PROXY=*`，否则会把自己当代理形成死循环（10s 超时→连续冷却）。→ 任何「本地服务 + 又改了系统代理」的组合都必须记这条。
4. **退出清理**：`Drop` trait 杀子进程 / 还原系统代理；应用退出事件里统一清理。→ 扩展若带 Native Messaging Host 必须同样处理。
5. **单实例 + 托盘**：`tauri-plugin-single-instance`，dev 模式故意不启用（避免与已装版互踢）。→ 桌面端专属。
6. **NDJSON 子进程流式通信**：Python 子进程逐行 JSON 输出，Rust 逐行 emit 事件，前端实时渲染进度。→ 若扩展未来有 native companion，这是标准范式。
7. **确定性身份派生**：`seeded_hex` 用 SHA-256 而非 `random.Random(seed)`，规避病态序列且跨语言一致（Rust/Python 同算法）。→ 多账号/指纹隔离场景必学。
8. **错误分类→冷却状态机 + 持久化**：把「可重试 / 需换号 / 需人工」三类失败显式建模，状态落盘重启保持。→ 任何批量异步任务（如批量抓取、批量渲染）都应抽这套。
9. **日志竞态修复**：`showDetail` 用 `useRef` 递增请求 ID，关闭弹窗后使进行中请求失效；代理日志先 `sendall(200)` 再 log（避免日志异常打死连接线程）。→ 细节但很关键。
10. **严格文档契约**：`AGENT.md` 把命令契约、红线、SOP、版本规则写成机器/人共读的手册；改契约必同步文档。→ 正是 OpenBiliClaw `AGENTS.md` 在做的事，可对照。

---

## 5. 与 OpenBiliClaw（知乎首页卡片化扩展）的对比与可迁移点

| 维度 | TraeWorkAssistant | OpenBiliClaw（你的项目） | 可迁移？ |
|---|---|---|---|
| 形态 | 桌面应用（Tauri） | 浏览器扩展（content script） | — |
| 能力边界 | 可装系统证书、起本地代理、调系统 API | 只能操作网页 DOM / 网络请求（webRequest / declarativeNetRequest / chrome.debugger） | — |
| 抓凭证方式 | **MITM 代理解密 HTTPS** | 扩展**不能 MITM**；只能读页面已渲染的 DOM、或拦截页面自身发出的请求（webRequest） | ❌ MITM 不可搬；替代：content script 读 DOM / `chrome.webRequest` 读请求头 |
| 多账号 | 多账号池 + 登录态快照 | 知乎卡片化是单用户渲染，暂无多账号诉求 | 部分（若以后做「多账号聚合看板」可借鉴池调度） |
| 批量任务 | 批量签到 + 冷却状态机 | 批量抓取/渲染知乎内容 | ✅ 借鉴「错误分类→冷却→重试」状态机 |
| 健壮性 | 原子写 / safe_lock / 防回环 | 扩展存储 + 并发渲染 | ✅ 直接借鉴 |
| 凭据 | Stronghold + OS Keychain | 扩展存 Cookie/JWT 用 `chrome.storage.session` + 系统钥匙串 | ✅ 思路一致（别明文落 `chrome.storage.local`） |
| 协议转换 | SSE→OpenAI | 若以后做「B 站/知乎 API 本地网关」 | ✅ 借鉴 `sse.rs` 纯函数转换 |
| IPC | Tauri invoke/Event | Native Messaging（若带 companion） | ✅ 借鉴 NDJSON 流 |

**结论**：架构骨架（桌面 vs 扩展）不同，**MITM 那一整套不能搬到扩展**；但
**状态机、原子写、确定性身份、NDJSON IPC、凭据加密思路、严格文档契约** 都是与业务无关的通用工程财富，可在 OpenBiliClaw 里直接采用。

---

## 6. 风险 / 合规（对你也有提醒价值）

- 该项目**逆向 Trae 私有接口**、自签 CA 装系统根信任、设备指纹伪造 —— 本身声明「可能违反 ToS，风险自担」。
- 真正可复用于你项目的启发：**多账号 / 抓凭证类功能永远伴随 ToS 与风控风险**，做之前先评估；OpenBiliClaw 的知乎卡片化是纯前端渲染（不改请求、不抓凭证），合规风险低，保持这个边界就好。
- 该仓库**仅 Windows 验证**（CA 安装、MachineGuid 重置）；macOS 版是后续移植，部分系统级能力在 macOS 上可能不完整。

---

## 7. 阅后结论

TraeWorkAssistant 是一个**工程完成度很高、文档极规范**的 Tauri 桌面应用范例，最值得学的是：
1. **把易变的核心逻辑放在 Python/PS 子进程**，UI 只调度展示（快速热更、降低重写风险）—— 对应你「扩展只做渲染、复杂逻辑下沉」的思路一致；
2. **错误分类→冷却状态机 + 持久化** 是批量异步任务的最佳实践模板；
3. **原子写 / 毒化恢复 / 防代理回环 / 退出清理** 是一组可直接抄的健壮性 primitives；
4. **`AGENT.md` 式契约文档** 与 OpenBiliClaw 的 `AGENTS.md` 是同一套工程纪律，可互相对照补全。

克隆已落在 `_ref_TraeWorkAssistant-mac/`，可随时深入阅读 `src-python/device_proxy.py`（代理）、`src-tauri/src/api_server/pool.rs` + `sse.rs`（网关）、`src-tauri/src/main.rs`（命令注册）这三个最值得精读的文件。
