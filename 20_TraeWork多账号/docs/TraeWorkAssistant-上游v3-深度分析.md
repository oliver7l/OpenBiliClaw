# smart-open/TraeWorkAssistant（AI Work 助手 v3.x）深度分析

> 研究日期：2026-09-15 ｜ 研究对象：`https://github.com/smart-open/TraeWorkAssistant`
> 关联条目：本地克隆 `references/TraeWorkAssistant-mac` 实为 **fork（ailogk/TraeWorkAssistant-mac，v2.9.4）**，
> 其既有报告见 `references/TraeWorkAssistant-架构分析.md`。本文聚焦**上游主仓 v3.5.x 的新形态**，
> 两份报告互补，不重复。

---

## 0. 一句话定位

**Windows 桌面端「AI 应用多账号一体化工作台」**——从单一的 Trae Work 签到工具（v2.x）
长成了覆盖 **Trae Work / Trae CN IDE / WorkBuddy / CodeBuddy / 豆包** 五应用的
**签到自动化 + 登录态切换 + 积分看板 + OpenAI/Anthropic/Codex 三协议兼容 API 网关**。
数据全本地、Tauri 2 + React 18 + Rust。

## 1. 仓库现状（2026-09-15 实测）

| 项 | 值 |
| --- | --- |
| 全称 / 默认分支 | `smart-open/TraeWorkAssistant` / `main`（v3.x 线） |
| 主语言 / License | Rust / **MIT**（版权 朱天伟，要求派生注明原库） |
| Star / Fork / Issues | **66** / **14** / 2 |
| 创建 / 最后 push | 2026-08-13 / **2026-09-15**（当天 8 连提交，处于高强度迭代） |
| 版本 | 3.5.2（09-15 升版，切换链路修复发版） |
| 旧产品线 | `trae_work_main` 分支维护 2.x，仅必要修复、不再新增功能 |
| 文档规模 | `product-design.md` 62KB / `tech-framework.md` 29KB / `user-manual.md` 30KB / `backlog.md` 59KB |

**一个月的爆发式增长**：8-13 建库 → 9-15 已到 3.5.2，中间完成了
「单应用 → 五应用」「Python/PS → 全量 Rust」「JSON → SQLite」「单协议 → 三协议三池」
四次形态级跃迁。这是个**以周为单位重构**的项目。

## 2. 架构：v2.x → v3.x 的四次跃迁

| 维度 | v2.9.4（本地 fork 停在这） | v3.5.x（上游现状） |
| --- | --- | --- |
| 支持应用 | Trae Work 单应用 | Trae Work / Trae CN / WorkBuddy / CodeBuddy / 豆包，**五应用** |
| 核心逻辑语言 | Python（`auto_checkin.py` + `device_proxy.py`）+ PowerShell 切换桥 | **全量 Rust**：`tasks/`（trae_checkin / wb_* / doubao_* / scheduler）+ `switcher/`，**零外部运行时** |
| 存储 | 十余个 JSON 文件散在 `data/` | **`data/aiwork.sqlite`**（WAL；kv 表 29 键 + 行文档实体表 12 + 列化流水表 8），首启迁移器把旧 JSON 导 `backup/` |
| API 网关 | OpenAI 单协议 + Trae 单池 | **OpenAI / Anthropic Messages / Codex Responses 三协议** + **Trae 池 / WorkBuddy 池 / 自定义池 三池调度** |
| 品牌与数据目录 | `TraeWorkAssistant` | 更名 `AIWorkAssistant`，安装/首启**自动复制迁移**（旧目录保留，新旧可并存） |

### 2.1 分层（v3.5.x）

```
Presentation  React 18 + Tailwind（Dashboard/Accounts/Checkin/Credits/Logs/ApiService/Settings）
      │  Tauri invoke + Event Bus
State         Zustand（store.ts 单一真相）
      │
Bridge        Tauri Commands
 ├─ env / cert / proxy        环境检测、CA 证书、MITM 代理生命周期
 ├─ accounts / oauth / jwt    账号 CRUD、OAuth（含 WorkBuddy 扫码）、JWT 解析刷新
 ├─ checkin / misc / process  签到编排、schtasks、三级进程关闭
 ├─ switch / profile          登录态切换（target_app 参数化，5 应用 × 3 快照布局）
 ├─ doubao / trae_apps        豆包域 / 双应用账号自动发现
 ├─ vault                     Stronghold + DPAPI 凭据加密
 └─ api_server  axum 网关：Trae 池 + WB 池 + 三池调度（dispatch/unified_catalog/custom_models）
Rust Tasks    tasks/（含 scheduler 应用内调度器）+ device_proxy/（MITM，hyper+rustls 自建）
Switcher      独立域：profile 档案表 / locate 六级 exe 发现 / proc 三级关闭 / machine 6 层重置
Store         store/（SQLite：kv 键值文档表 / 行文档实体表 / 列化流水表）
```

### 2.2 三种登录态快照布局（这是 v3 最见功力的设计）

不同应用的数据分布完全不同，作者用**档案表驱动**统一了切换管线：

| 布局 | 适用 | 要备份的东西 |
| --- | --- | --- |
| **icube** | Trae 双应用 | 精准备份 9 类核心文件 |
| **chromium** | 豆包 | 白名单目录快照 + `snapshot_meta.json`（schemaVersion=1）+ 完整性三层校验 + `.bak` 单代回滚 + `ExpectedCurrentUid` 防误覆盖守卫 |
| **authfile** | WorkBuddy / CodeBuddy | L1 auth 文件 + L2 storage/user-* + L3（仅 CodeBuddy）vscdb 登录真源**含 -wal/-shm 边车** |

> 注意 L3 那条：`SQLite 登录真源必须连 WAL/SHM 边车一起备份`，否则恢复出的是过期状态——
> 这是多账号工具里极常见、且极难排查的坑，他们踩过并写进了档案表。

## 3. 三大核心能力

### 3.1 MITM 本地代理（`device_proxy/`，已 Rust 化）

- 127.0.0.1:8899，自签 CA + **动态叶子证书必须带 AKI**（OpenSSL 3.2+/Python 3.13 客户端强制）。
- 捕获 `trae.cn`/`trae.com.cn` 带 `Cloud-IDE-JWT` 的请求写回账号库（exp 防降级）；顺带抓豆包 Cookie（sessionid/sid_guard/ttwid）回写。
- **启动前先捕获用户已有系统代理（VPN）当上游**，停止时原样还原——这是"开代理就断外网"类工具的通病解法。
- 自身出站一律 `NO_PROXY=*` / `ProxyHandler({})` 绕系统代理，**防回环**。

### 3.2 签到与保活自动化

- 错误分类 → 冷却状态机（持久化，`SessionDead` 永久冷却需重登）：

| 错误 | 触发 | 冷却 |
| --- | --- | --- |
| `PlanLimit` | `code:1005` | 12 小时 |
| `SoftRate` | 429 | 60 秒 |
| `SessionDead` | 401 | 永久 |
| `NotFound` | 404 | 60 秒（**不累计**） |
| `Server`/`Client` | 5xx/其他 4xx | 累计达阈值 10 分钟 |

- 失败自动重试最多 2 轮（30s/90s）；`CheckinGuard`（tokio Mutex）应用级防重入，页面/托盘/静默三入口共用。
- **WorkBuddy 成长中心自动化**：travel / lottery / tasks / energy / streak 全实测；豆包/WorkBuddy 定时保活与续期。
- 调度双轨：Windows 计划任务 + **应用内调度器**（跨平台补充）。

### 3.3 API 网关（最有复用价值的部分）

| 端点 | 说明 |
| --- | --- |
| `/v1/chat/completions` | OpenAI 协议（流式 + 非流式） |
| `/v1/messages` | Anthropic Messages（message_start → content_block_* → message_delta → message_stop） |
| `/v1/responses` | Codex Responses 投影 |
| `/v1/images/generations` `/edits` | 生图双端点 |
| `/v1/models` | **统一模型目录**：Trae 官网同步 + WB 目录 + 自定义三源合并，canonical_id 归并 |
| `/v1/embeddings` | **明确 501**（不做假实现） |

**三池调度（`dispatch.rs`）**：

- 资源池：`trae`（SOLO `llm_utils_chat`，积分 208）/ `buddy`（copilot.tencent.com 或 www.workbuddy.ai `/v2/chat/completions`）/ `custom`（自建 OpenAI 兼容上游，命中即直达）。
- 池间策略：`smart`（默认：**池内最早积分到期优先 → 模型倍率小者优先 → 健康账号积分总和多优先**）/ `priority`（严格按序取首个可用池）；`per_model` 模型级覆盖优先；优先级缺失回退 `["buddy","trae"]`。
- 会话粘性 + `ck_` 子 Key（`allowed_accounts` 上游白名单 + `schedule_mode`）+ 四段模型路由 + 审核指纹清洗 + web_search 工具代执行。

## 4. WorkBuddy 协议要点（对我们直接相关）

| 用途 | 端点 | 要点 |
| --- | --- | --- |
| token 续期 | `POST www.codebuddy.cn/v2/plugin/auth/token/refresh` | `X-Refresh-Token` 头、空体 `{}`；**该头只允许出现在此端点** |
| 签到 | `POST /v2/billing/meter/daily-checkin` | `code:10001`=已签容错 |
| 积分三件套 | `<domain>/billing/meter/get-user-resource-{summary,paid-packages,free-packages}` | 需 `X-Client-Platform: web` |
| 对话上游 | `copilot.tencent.com/v2/chat/completions`（CN）/ `www.workbuddy.ai`（Global） | **只回 SSE**，非流式本地聚合 |
| 本地 quota 兜底 | `GET 127.0.0.1:<port>/api/v1/quota` | 扫 `~/.workbuddy/*.port` + 端口段探测 |

**区域路由铁律**：CN（domain 不含 `.workbuddy.ai`）→ chat 走 `copilot.tencent.com`、billing 走 `www.codebuddy.cn`；
Global → 两者都走 `www.workbuddy.ai`。**令牌域与请求域不一致会被网关拒绝**。
三铁律：① Origin/Referer 必带；② 缺省字段显式 `X-No-User-Id / X-No-Enterprise-Id / X-No-Department-Info: 1` 占位；③ **chat 请求绝不携带 `X-Refresh-Token`**。

**B.3 联调避坑清单（十条，条条是实测）**——挑四条最贵的：

1. **上游拒绝非流式**（code 11101）→ 强制 `stream:true`，非流式本地聚合（tool_calls delta 按 index 合并）。
2. **Claude Code 指纹触发审核** → 指纹清洗 + **两句固定 system 模板逐字入黑名单**（`You are Claude Code…` / `Main branch…`），映射表外置 `wb_template_map.json` 热更新。
3. **prompt cache 对代理流量恒不命中**（按冷启动全价计费）→ 成本模型必须按无缓存估算。
4. **凭证双源冲突**（auth 文件被客户端启动重写）→ 桌面文件只读 + 工具侧 token store 副本「谁新用谁」（`expiresAtMs` 晚者胜出）+ 原子写/文件锁。

**会话数据三件套**：`projects/{ws}/{cid}.jsonl` 正文 + `workbuddy.db` sessions 表 + `edge-sync-mapping-v2.db` 云端映射（`convmsg:{uid}` 决定云端归属）——**备份缺一不可**。

## 5. 工程化可迁移手法

| 手法 | 做法 | 迁到哪里 |
| --- | --- | --- |
| 原子写 | `write_json` tmp + rename | 任何落盘状态 |
| 信封抗变 | `dig()` 沿 data/result/resp/response/info 递归下钻（限深 8） | 对接不稳定第三方 API |
| 锁毒化自愈 | `safe_lock()` 替 `lock().unwrap()`，中毒恢复内部数据继续跑 | 长驻服务 |
| 进程三级关闭 | 优雅 WM_CLOSE(3s) → 树杀(2s) → 返回 Err 交人工 | 外部进程管理 |
| 确定性身份派生 | `rand_digits(n, seed=user_id)`，同账号恒同标识 | 虚拟设备/伪匿名 ID |
| 迁移幂等闸门 | SQLite `user_version` 控首启迁移，旧数据导 `backup/` | 数据格式升级 |
| 不做假实现 | `/v1/embeddings` 明确 501 | API 设计诚实性 |
| 失败原因透传 | 子进程 stderr 截取后 toast 透出，而非"静默只写 console" | 所有异常处理 |
| 文档单一真相 | 完整命令契约只在 `AGENT.md` §5，其他文档只留概览避免双维护漂移 | 文档治理 |

## 6. 风险与合规（必须知道）

- **明确违反服务条款**：项目自己 6 条免责声明写得很清楚——"可能违反 Trae Work 服务条款，
  后果（封号/积分清零/功能限制）自负"，并声明"请仅管理本人合法持有的账号"。
- **生态位敏感**：多账号签到 + 积分聚合 + 对外转售式网关，本质是**薅厂商免费额度**，
  与厂商商业利益直接冲突。Work 积分那条路他们自己也标注了"上线前需评估 ToS 风险"。
- **技术性脆弱**：整条链路建立在逆向私有协议之上（`llm_utils_chat`、`@aha-kit` 加密体、
  豆包 `a_bogus` 签名）。上游一改全线失效，作者自己把「TW 升级导致接口变化」列为**高风险**。
- **Work 积分已证伪**：外部无法复刻 `create_agent_task`（真实身份复刻仍 4001），
  唯一路径是多活会话编排，作者评估后 **W-01 已排除**。

## 7. 第四个同名仓库：`waxilo/TraeWorkAssistant`（唯一原生 macOS 版）

> 2026-09-15 晚补充。至此「TraeWorkAssistant」这个名字下已有四个来源，
> **彼此无 fork 关系**，实现路线差异极大。

### 7.0 谱系澄清（重要——四个同名仓库别混）

| 仓库 | 关系 | 版本 | 技术路线 | macOS |
| --- | --- | --- | --- | --- |
| `smart-open/TraeWorkAssistant` | 上游主仓 | 3.5.2 | MITM 证书 | ✗ 仅 Windows |
| 同主仓 `trae_work_main` 分支 | 旧产品线 | 2.x | MITM 证书 | ✗ |
| `ailogk/TraeWorkAssistant-mac` | 独立仓库（非 fork） | 2.9.4 | MITM 证书 | ⚠️ 源码级、无 Release |
| **`waxilo/TraeWorkAssistant`** | **独立仓库（非 fork）** | **0.1.3** | **反代 + 端点改写（免证书）** | **✓ aarch64 dmg** |

作者 `sloan`（GitHub `waxilo`），2026-09-14 02:53 建库，2 天 8 个提交推到 v0.1.3。
单 crate 扁平结构（22 个 `.rs` 全平铺在 `src/`），**纯 Rust，无 Python / PowerShell 依赖**
——这正是它能干净支持 macOS 的根本原因。

### 7.1 技术路线：为什么它能「免证书」

前三个版本都走 MITM（本地 HTTPS 代理 + 把自签 CA 装进系统信任库）。
waxilo 版**一个证书都不装**：

1. 改 `product.json` 的 `bootConfig.{remote,agent,ckg,cue,hub}.trae.normal` → `http://127.0.0.1:<port>`
2. 反代只做两件事：把 `Authorization` 换成池内账号的 `Cloud-IDE-JWT`；路径/查询/body/响应（含 SSE）原样透传
3. 代价：给 `out/main.js` 打补丁松开 scheme 闸门（3 处）

**补丁为什么必须存在**（实测）：TraeWork 拼 Electron URL pattern 时是
`c.startsWith("https://") ? c : "https://"+c`，于是 `http://127.0.0.1:8788` 会变成
`https://http://127.0.0.1:8788/*` → `Invalid url pattern` → **TraeWork 启动即崩**。

三处补丁**缺一不可**，其中第 3 处最容易被当成可选项：
那张 pattern 数组是 `solo-lite-websocket-headers` 规则的来源，也就是**唯一**给智能体请求注入
`Authorization` + `X-User-Region` 的地方。只打前两处 → 端点降 http 后规则不再命中 →
不带身份头 → 白名单外的透传路径直接 401 → 得到「看着能跑、实际全废」的**假成功**。

**安全边界设计（值得学）**：

- 补丁后闸门变 `includes("://")`，`https://` 与 `http://` 都能过 → **打过补丁的应用向下兼容**
- 逐字节可还原（反向替换 + 文件尾标记 `//[twa-gate v1]` + sha256 自证）
- **fail-closed**：闸门匹配数 ≠ 2 或找不到 pattern 数组 → 拒绝打补丁，绝不半打
- 只动 `out/main.js`，**绝不动 `product.json.checksums` 里受校验的 14 个文件**

### 7.2 macOS 是一等公民（不是事后移植）

26 处 `cfg(target_os)`，macOS 8 处 / Windows 8 处 —— **双平台对等开发**：

| 能力 | macOS 实现 |
| --- | --- |
| TraeWork 探测 | `/Applications/{app}.app/Contents/Resources/app` + `~/Library/Application Support/{TRAE SOLO CN,TRAE,Trae TRAE,TRAE CN}/User/globalStorage` |
| 端口占用诊断 | `lsof -nP +c 0 -iTCP:<port> -sTCP:LISTEN -Fcn` |
| 打开浏览器 | `open`（Win: rundll32 / Linux: xdg-open） |
| 开机自启 | `MacosLauncher::LaunchAgent` |
| Dock 点击 | `RunEvent::Reopen` → 唤起主窗口 |

两处关键设计让它**天然避开**了 ailogk 移植版在 Mac 上的三个硬伤：

1. **凭据不依赖 DPAPI**——自己的 `device.json` 存 EC P-256 密钥对 + 设备号。
   而且这不是偷懒，是**必需的**：`ExchangeToken` 签发的 token 绑定签发时的设备身份，
   续签要求 `DeviceInfo.DeviceID` 一致 **且** `DeviceProof.Signature` 用**签发时那对私钥**签名，
   否则 `20403 Token device not match`。作者甚至实测出「拿官方客户端自己落盘的私钥
   去续签它自己的 token 也是 20403」。早期版本每次启动现生成密钥、用完即弃
   → 签发的 token **永远无法续签**，这是 v0.1.x 修掉的核心 bug。
2. **定时签到不依赖系统计划任务**——进程内 `scheduler` 线程（HH:MM 每日触发），
   与进程同生命周期。代价是必须保持应用运行，收益是跨平台无差别。
3. **设备标识不碰注册表**——自维护 device_id / machine_id，Mac 上同样可换。

顺带解决两个 Mac 上的真实痛点：
- **TraeWork 升级会整份替换 `product.json`** → 端点改写丢失 → 每 5 分钟自愈重写
- **启动清扫**：端点改写的唯一保留条件是「接管开着 **且** 反代确实在监听」，其余一律恢复官方直连
  （「端点指向一个没人接的端口，是整应用不可用」）；并按**回环指纹**清理老版本残留的代理设置，
  绝不碰用户自己配的非回环代理

### 7.3 池化调度

- **会话粘滞**：`/chat_sessions/:id/*` 固定复用同一账号（会话属于账号，换号丢上下文）
- **额度到期优先轮换**：到期最早 → 无到期数据靠后 → 积分多者优先；依据 `ide_user_ent_usage`
  的 `expire_time`（秒级），只统计「还有余量」的包，10 分钟 TTL 快照落盘
- **限流无感切换**：429 → 10 分钟冷却 + 解绑会话 + 换号重发（上限 2 次）
- **只给扣费路径换凭据**：仅 `/api/remote/v1/{chat_sessions,models,file_converts}` 换 token，
  其余（技能市场 / git / 设置）沿用 TraeWork 自身凭据——「换了对用户意味着看到别人账号的数据，
  而扣费一分钱也省不下」
- 签到限流实测：`claim` 返回 **9074「当前参与用户太多，请稍后再试」**（状态查询一直正常、
  领取可连续十几分钟 9074）→ 3 轮补签（首轮 + 2 轮，间隔 600s）

### 7.4 发现的问题（若为本人项目可直接修）

- **9 个死依赖**：v0.1.3「免证书」重构后，`rustls` / `rustls-pemfile` / `rcgen` /
  `webpki-roots` / `time` / `aes` / `cbc` / `cipher` / `hmac` 在 `src-tauri/src/` 中
  **均 0 处引用**；且 `Cargo.toml` 仍留「见 `src/tls.rs` 顶部说明」的注释，
  而 **`tls.rs` 已被删除**——注释指向不存在的文件。清掉后可少整棵 rcgen/rustls 依赖树。
- **无 LICENSE / 无 README**：CI 注释里写「如需 Intel/通用包，见 README『跨平台说明』」，
  但根目录没有 README → 该指引悬空；无 LICENSE 也意味着默认「保留所有权利」，他人不能合法复用。

### 7.5 风险

- **0 star / 建库 2 天 / v0.1.x / 无 README 无 LICENSE**——成熟度与可审计性远低于上游（66 star）
- **需修改 TraeWork 安装目录内的 `out/main.js`**：虽然只动不受 `product.json.checksums`
  保护的文件且可逐字节还原，但那是 TraeWork 自己的校验，**与 macOS 的 app 代码签名是两套体系**。
  首次使用建议先用非主力账号验证 TraeWork 仍能正常启动。
- **全部对话流量经本机反代**（只换鉴权、正文透传）——信任边界取决于作者
- 与同类项目一样：多账号共享额度，可能违反 ToS

## 8. 平台可用性与 macOS 替代选型（2026-09-15 补问）

### 8.1 多账号切换：能，而且是它的核心能力

`switcher/` 模块用**档案表驱动三种快照布局**，覆盖 5 应用：

| 布局 | 应用 | 备份内容 |
| --- | --- | --- |
| icube | Trae / Trae Work | 精准备份 9 类核心文件 |
| chromium | 豆包 | 白名单目录快照 + `snapshot_meta.json` + 完整性三层校验 + `.bak` 单代回滚 + `ExpectedCurrentUid` 防误覆盖守卫 |
| authfile | **WorkBuddy / CodeBuddy** | L1 auth 文件 + L2 storage/user-* + L3 vscdb 登录真源**含 -wal/-shm 边车** |

切换动作组：`Switch / SaveCurrentLogin / ResetMachineId / ResetDeviceIds / BackupCurrent / RestoreOnly / KeepAlive`。
配套 6 层设备标识重置 + 进程三级关闭 + 全局互斥拒绝并发切换。
**代价**：切换靠整体回滚 `state.vscdb`，项目列表会退到快照时点（§8.1 的 P1 方案尚未落地）。

### 8.2 上游主仓仅 Windows，且无 macOS 计划

README 与 `tech-framework.md` 均写明「**仅 Windows 10/11**」。硬依赖全是不可移植的：
`WebView2 Runtime` / `VS Build Tools (C++)` / `schtasks`（计划任务）/ `DPAPI`（凭据加密）/
注册表 `MachineGuid` / `certutil`（根证书）/ `UAC` 提权；打包目标只有 `msi` + `nsis`。

### 8.3 社区 macOS 移植 `ailogk/TraeWorkAssistant-mac`（能力折半）

> 注意：它**在 GitHub 上不是 fork**（`fork=false`，无 parent），是代码复制后自建的独立仓库。
> 1 star / 0 fork / **无任何 Release** / 最后 push 2026-09-12（上游仍在狂飙）。

**已适配的部分**（做得很认真）：

| 项 | macOS 方案 |
| --- | --- |
| 切换桥 | `trae-switch-bridge.ps1` → **`trae-switch-bridge.py`**（纯标准库，同 NDJSON 协议） |
| 根证书 | `certutil` → **`security add-trusted-cert`**（login.keychain-db） |
| 孤儿进程清理 | `Win32_Process` → **`pgrep -f` + `kill -9`** |
| Python 运行时 | CLT 预检 `mac_clt_installed()`——**不能用 spawn python3 探测**（未装 CLT 时 `/usr/bin/python3` 是触发安装弹窗的 shim，会挂起表现为「点了没反应」） |
| 证书生成 | 移除 `cryptography` 依赖，缺库时退化 **`openssl` CLI** |
| 打包 | `app` + `dmg`，icon `.icns` |

**未适配的三处硬伤**：

1. **凭据 vault 直接不可用**——`vault.rs` 非 Windows 分支硬返回 `Err("vault 仅支持 Windows")`，
   `jwt`/`refresh_token` 只能走「写失败降级明文」路径。
2. **定时签到不可用**——`schtasks` 在 macOS 无替代实现（无 launchd/cron 分支）。
3. **6 层设备重置只能做 5 层**——注册表 `MachineGuid` 在 macOS 不存在。

且它停在 **v2.9.4 → 只支持 Trae Work 单应用，不支持 WorkBuddy / 豆包**。
**无 Release = 没有现成 dmg，必须自己配 Rust + Node 工具链编译。**

### 8.4 macOS 上能用哪个：横向对比

| 项 | smart-open 上游 | ailogk mac 移植 | **waxilo** | **workbuddy-switch** |
| --- | --- | --- | --- | --- |
| Star | 66 | 1 | 0 | **363** |
| macOS 支持 | ✗ 仅 Windows | ⚠️ 源码级，无 Release | ✓ **aarch64 dmg** | ✓ **aarch64 + x86_64 dmg** |
| 目标应用 | Trae Work / Trae CN / WorkBuddy / CodeBuddy / 豆包 | 仅 Trae Work | 仅 TraeWork | **WorkBuddy / CodeBuddy** |
| CodeBuddy CLI / CN IDE | 部分 | ✗ | ✗ | ✓ |
| 最后更新 | 09-15 | 09-12 | 09-15 | 09-15 |
| 安装方式 | MSI / NSIS | 自行编译 | Release dmg | Release + **`npm i -g` webui** |

**结论**：做 TraeWork 多账号 → waxilo 版（Mac 可用，但极新）；
做 WorkBuddy 多账号 → workbuddy-switch（成熟度最高）。

`changexbc/workbuddy-switch` 功能：OAuth 扫码登录 / 本机导入 / 手动 token；
**一键切换**（备份认证文件 → 关闭 WorkBuddy → 写入 → 重启）；
**会话复制**（jsonl 正文 + `workbuddy.db` 索引 + edge-sync 注册，云端归属目标账号——
正好补上了 smart-open 版 §8.1 判定「会话真迁移 ❌」而放弃的能力）；
自动签到（30 分钟补签）；**Token 保活**（惰性刷新 + 每日保活）；积分到期查询（7 天内高亮 + 到期优先排序）；
**自动轮换**（后台把 CLI 后续启动账号设为积分最紧迫者）；积分/Token 统计。

macOS 两个已知前置：首次启动需 Control+点击「打开」或 `xattr -rd com.apple.quarantine`；
切换账号需要授权「App 管理 / 完全磁盘访问」。

> ⚠️ 以上几个项目同属灰色地带（多账号共享额度），使用前请自行判断服务条款风险。

## 9. 对我们的价值判断

**不建议照搬其业务形态**（合规风险高，且与 OpenBiliClaw 的定位无关）。

**但有三类东西值得抄**：

1. **多池调度 + 三协议网关的抽象**：`smart` 策略（最早到期优先 → 倍率小优先 → 健康额度多优先）、
   五态冷却机、会话粘性、模型目录四层兜底——这套**「多上游供应商统一抽象 + 健康检查 + 智能选路」**
   是通用架构，OpenBiliClaw 的 LLM provider 层（`config.LLM_PROVIDER_NAMES`）完全可以吸收：
   目前我们是「切 provider」，缺的正是**同 provider 下多凭据的池化与冷却调度**。
2. **信封抗变 + 明确 501 + 失败原因透传**这类工程纪律。
3. **SQLite 迁移的 `user_version` 幂等闸门**——我们项目正有「旅游 travel.db 正本」等迁移待做，可直接复用。

**顺带提醒**：我们项目里 `~/.workbuddy/` 下就有真实的 WorkBuddy 凭据与端口文件，
上述 B.1/B.3 的协议事实说明——**本地 127.0.0.1 的 quota 端点是可被扫描探测的**。
这属于本机可信边界内的正常能力，无需处理，但值得知道。
