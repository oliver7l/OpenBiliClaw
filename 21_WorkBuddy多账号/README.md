# 21_WorkBuddy多账号 — 全账号共用一份定时任务

## 原理（为什么可行）

- WorkBuddy 所有账号共用同一个本地库 `~/.workbuddy/workbuddy.db`，
  `automations` 表靠 `owner_user_id` 列区分归属；切号只是换
  `~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info`
  认证文件 + 重启应用，**本地库不动**。
- 同一时刻只有当前登录账号的自动化会被调度执行。
- 因此：把「全账号任务并集」写进每个账号名下，切哪个账号看到的都是同一份任务，调度结果不变。

## 主任务集规则

1. 取所有账号 `deleted_at IS NULL` 的任务，按 **name 去重**（最新 `updated_at` 优先）；
2. **已过期的一次性提醒不传播**（如过期面试提醒）；
3. 暂停任务默认随主集同步（保持暂停状态），`--exclude-paused` 可排除；
4. 同步后某账号名下多出的任务（主集之外）会被**删除**——所以执行前必有备份。

## 用法

```bash
# 1) 双击（推荐）：预览 → 确认 → 自动退 WorkBuddy → 写库 → 重启
open 同步定时任务.command

# 2) 命令行
python3 scripts/wb_sync_automations.py status   # 当前账号 + 各账号任务数
python3 scripts/wb_sync_automations.py plan     # dry-run 预览变更
python3 scripts/wb_sync_automations.py apply    # 备份后执行
python3 scripts/wb_sync_automations.py plan --uid <uuid>   # 只看/只同步某账号
```

## 安全机制

- apply 前自动备份整个 db → `~/.workbuddy/automation-backups/workbuddy.db.pre-sync-<时间戳>`
- 检测到 WorkBuddy 运行中会拒绝写库（内存缓存可能覆盖写入结果）；
  `.command` 会先优雅退出再执行，完成自动重启。`--force` 可强行写入（不推荐）。
- 新增行 `owner_source='sync'`，可一眼识别同步来的任务。

## 什么时候需要重新跑

各账号任务集是**快照式同步**：之后在任何账号上新增/修改/删除了任务，
再跑一次 `同步定时任务.command` 即可把变更传播到全部账号（幂等，无变化时零操作）。

## 通道全景：哪些共用、哪些要统一

`settings.json` 两层通道配置：全局层 `claw.channels.*`（所有账号共用）+
账号层 `claw.users.<uid>.channels.*`（per-user 覆盖，不一致的通道切号后表现不同）。

| 通道 | 配置层 | 结论 |
| --- | --- | --- |
| 微信服务号 wechatmp | 各账号一致（webhook，无凭据） | 天然无感，不用动 |
| 飞书 feishu | 原仅全局层 → 已铺进账号层 | 切号后也能连 |
| 元宝 yuanbao | 原仅全局层 → 已铺进账号层 | 同上 |
| 微信 ClawBot weixinClawBot | 各账号独立 bot | 已统一（cursor 最新的 bot） |
| 企微 bot wecomaibot | 仅个别账号有 | 已统一到全部账号 |

**重要发现**：全局层通道（feishu/yuanbao）疑似只对 `legacyOwnerUid`（Aaron）生效，
其他账号没有账号层配置就不会连接——这就是"切号后飞书没连接"的根因，
与周英当初微信失效同模式。`wb_sync_channels.py apply --include-global`
会把全局层有凭据的通道写进每个账号层，保证任何账号登录都显式启用。

`scripts/wb_sync_channels.py`（plan/apply，通用版，已并入 `.command`）：
对每个 per-user 通道，取「enabled 优先、凭据最全」的一份写入全部账号；
已一致的自动跳过。凭据（botToken/appSecret/botSecret）都属于外部应用的 bot、
与 WorkBuddy 账号无关，复用安全。per-user 的会话路由（conversationBindings）不动。

另一层：**MCP 连接器**（飞书 CLI、腾讯文档等，`~/.workbuddy/connectors/<uid>/`）
也是 per-account 的信任/授权状态（各账号 167~213 个 server 不等），如发现切号后
某个连接器不可用，可再做 connectors 目录同步（涉及 keys，需单独评估）。

注意：多个 WorkBuddy 实例同时在线会争抢同一 bot 的消息轮询，建议同时只跑一个实例。

## 会话（聊天记录）为什么不同步

会话是「三件套」结构：正文 jsonl + sessions 表行 + edge-sync 云端归属映射，
复制到别的账号 = 每份 ×N 且每次切换都翻倍，得不偿失。
workbuddy-switch 自带「切换时勾选会话复制到目标账号」，按需复制个别重要会话即可。

## 账号 uid 对照（2026-09-19，来自 ~/.wb-switch/accounts.json）

| 昵称 | uid |
| --- | --- |
| Aaron | 019a3fec-c43c-404e-81b8-aa7e3ae82102 |
| 15367791243 | 1ebb7676-82b6-4bdd-8f44-df93c7155852 |
| 古灵阁 | b24541f1-fd0b-45a4-b8e7-7b51643b6544 |
| 小土豆 | b4297bbb-40f7-4bd1-82ee-b41b5a9b0010 |
| 周英（当前） | 1b7c3a9b-721a-40af-99e7-7f2c53800605 |
| 19942328729 | 591815b5-73b4-4d9f-8beb-9497797cdb8e |
