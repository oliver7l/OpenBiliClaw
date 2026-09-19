# 21_WorkBuddy多账号 — 切号无感（定时任务 + 消息通道全账号统一）

> 目标：不管切到哪个 WorkBuddy 账号，定时任务和微信/飞书等推送通道都表现一致。
> 原理：WorkBuddy 所有账号共用同一份本地数据，靠 `owner_user_id` 区分归属；
> 把「全账号并集」写进每个账号名下，切号即无感。

## 快速上手

```bash
# 日常操作（推荐）：双击一条龙
open 同步定时任务.command
#   流程 = status 展示 → plan 预览 → y 确认 → 自动退 WorkBuddy
#        → 任务同步 → 通道同步 → 重启 WorkBuddy

# 什么时候需要跑：在任一账号上新增/修改/删除了自动化，或新加/改绑了通道之后。
# 幂等：无变化时零操作，放心重跑。

# 命令行细粒度
python3 scripts/wb_sync_automations.py status|plan|apply [--uid <uuid>] [--exclude-paused]
python3 scripts/wb_sync_channels.py    status|plan|apply [--channel <名>] [--include-global]
```

## 架构知识（开发前必读）

### 1. 数据模型：哪些是全账号共用、哪些是 per-account

| 数据 | 位置 | 归属 |
| --- | --- | --- |
| 认证/登录态 | `~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info` | **切号=换这个文件**（uid/nickname 在 `.account` 里） |
| 自动化任务 | `~/.workbuddy/workbuddy.db` → `automations` 表，`owner_user_id` 列 | 共用库、按账号隔离 |
| 会话元数据 | 同库 `sessions` 表（`user_id` 列）+ 正文 `~/.workbuddy/projects/{ws}/{cid}.jsonl` + 云端映射 `edge-sync-mapping-v4.db` | 三件套，缺一不可 |
| 消息通道 | `~/.workbuddy/settings.json`：全局层 `claw.channels.*` + 账号层 `claw.users.<uid>.channels.*` | 账号层覆盖全局层 |
| ClawBot 轮询游标 | `~/.workbuddy/claw-state/weixin/<botId前缀>_im.bot.cursor.json` | 按 bot，与账号无关 |
| MCP 连接器信任态 | `~/.workbuddy/connectors/<uid>/`（mcp.json + connector-states*.json） | per-account，**states 加密绑定账号，不可直接复制** |
| 账号清单（含 uid） | `~/.wb-switch/accounts.json`，每项顶层 `uid` 字段 | workbuddy-switch 的数据 |

### 2. 调度规则

同一时刻只有**当前登录账号**的自动化会被执行、当前账号的通道会连接。
⇒ 只要每个账号名下都是同一份任务/通道配置，切号对外表现完全一致。

### 3. 通道两层模型（为什么飞书会"没连接"）

- 全局层通道（如 feishu/yuanbao 只在 `claw.channels` 有配置）疑似**只对
  `claw.legacyOwnerUid`（Aaron）生效**——其他账号账号层没配置就不连接。
- 修法：`wb_sync_channels.py apply --include-global` 把全局层有凭据的通道
  也写进每个账号层。当前 4 账号 × 5 通道（feishu/wechatmp/wecomaibot/
  weixinClawBot/yuanbao）全齐。
- 通道凭据（botToken/appSecret/botSecret）属于**外部应用的 bot**，与
  WorkBuddy 账号无关，可直接复用。多个 bot 绑同一个个人微信。

### 4. 任务同步规则（wb_sync_automations.py）

- 主任务集 = 全账号 `deleted_at IS NULL` 的并集，**按 name 去重**（最新 `updated_at` 优先）
- **过期的一次性提醒不传播**（`scheduled_at` 已过去的不进主集）
- 同步后账号层多出的任务（主集之外）会被删除；新增行 `owner_source='sync'` 可识别
- 动态列反射复制（`PRAGMA table_info` → 逐列搬运，仅跳过身份/运行态列），抗 schema 演进

## 命令速查

| 场景 | 命令 |
| --- | --- |
| 看当前账号 + 各账号任务数 | `wb_sync_automations.py status` |
| 看通道矩阵（谁有哪些通道） | `wb_sync_channels.py status` |
| 预览将发生的变更 | `... plan`（不写任何东西） |
| 执行同步 | `... apply`（自动备份；应用运行中会拒绝，`--force` 越过） |
| 只同步部分账号 | `apply --uid <owner_user_id>`（可多次） |
| 只同步某个通道 | `apply --channel weixinClawBot` |
| 一条龙（退应用→同步→重启） | 双击 `同步定时任务.command` |

## 安全机制

- 每次 apply 前自动备份到 `~/.workbuddy/automation-backups/`
  （`workbuddy.db.pre-sync-*` / `settings.json.pre-wechat-*` / `pre-channels-*`）
- 检测到 WorkBuddy 运行中会拒绝写（内存态可能覆写），`.command` 会先优雅退出再执行
- `automations_backup/`（全量导出的任务 JSON，含个人日程）已 gitignore，绝不入库

## 踩坑清单（血泪教训）

1. **sqlite 只读查询随时可做；写库必须 WorkBuddy 退出后**（或 --force 后尽快重启）。
2. accounts.json 里部分账号 uid 在顶层 `a['uid']`，`auth_raw.account.uid` 是 None——**两处都要取**。
3. 零任务账号不在 automations 表里，plan 的账号集合必须 **union switch_accounts 的 uid**，且 `by_owner.get(uid, [])` 防 KeyError。
4. ClawBot cursor 文件名是 botId 的 **@ 前缀**（`a05ebd7f574b_im.bot.cursor.json`），别拿整串 channelId 拼路径。
5. plan 的"各账号一致"判定必须**对照全部账号数**——只有 1 个账号有配置 ≠ 一致。
6. 全局层通道只对 legacyOwnerUid 生效 ⇒ **必须铺进账号层**才对其他账号生效。
7. 中文路径 grep 用 ripgrep/Grep 工具；写库类操作执行后**必须二次复检**（曾遇 Edit 报成功但未落盘）。
8. 自动化主集里同名任务取最新版本即可放心覆盖；过期一次性任务记得剔除，否则会永久传播死任务。

## 后续开发方向（按优先级）

1. **切号后自动同步**：目前是快照式手动同步。可在 workbuddy-switch 切换流程
   （关应用→写认证→重启）之间插入 `wb_sync_automations.py apply`，实现真·自动。
   观察点：switch 是 Rust 二进制，可用 LaunchAgent 监听认证文件 mtime 变化触发。
2. **MCP 连接器层同步**：`connectors/<uid>/mcp.json` 是明文 server 定义（并集可同步），
   但 `connector-states*.json` 被 aes-256-gcm + accountIdentityKey 加密绑定账号，
   不能复制。若要统一只能引导各账号重新 Trust/授权。
3. **会话跨账号**：三件套复制可行但 ×N 翻倍，按需用 workbuddy-switch 的
   切换时勾选复制（其 copy_session_to_user 会注册 edge-sync 映射）。
4. **通道守护日志**：飞书/微信的连接日志不在 main/daemon/renderer.log 里，
   连接层排障需先找到 IM 守护进程的日志位置（ioa-im 相关）。
5. **多实例互斥**：统一 bot 后多实例同跑会争抢轮询游标；可加单实例锁文件。

## 相关资料

- workbuddy-switch 上游深度分析：`12_开源项目研究/references/workbuddy-switch-深度分析.md`
  （会话路径 B 复制、切换流水线、动态列反射等范式都在里面）
- TraeWork 多账号（另一个产品，勿混淆）：`20_TraeWork多账号/`
- 账号 uid 对照：`~/.wb-switch/accounts.json`（运行时读取，脚本不硬编码）
