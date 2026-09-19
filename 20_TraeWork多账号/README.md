# 20_TraeWork多账号 — TraeWork/TRAE 多开与账号轮转工具集

> 2026-09-19 归拢定稿。GUI 助手（`/Applications/TraeWorkAssistant.app`）与本工具集**共用同一套数据**
> （`~/Library/Application Support/cn.traework.assistant/`），可以混用。本 README 是机制研究报告 +
> 使用手册合一；逆向分析长文在 `docs/`，原始研究仓库位置见文末。

## 一、数据资产地图（谁存了什么，切换为什么只认档案）

```
┌─────────────────────────────────────────────────────────────────────┐
│ 客户端 TRAE SOLO CN（每个实例一份）                                   │
│   ~/Library/Application Support/TRAE SOLO CN*/User/globalStorage/   │
│   storage.json  ← 同一时刻只存【一个】账号的登录态（登新顶旧！）        │
│   核心：iCubeAuthInfo://icube.cloudide = 加密 blob（token+uid+...）  │
│        iCubeAuthInfo://icube-dc:<did>  = 设备 ECDSA 私钥（键名带DID）│
└──────────────┬──────────────────────────────────────────────────────┘
               │ capture（解密读出 uid，整文件拷贝）
               ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 档案仓 ~/Library/Application Support/cn.traework.assistant/          │
│   traework_profiles/<uid>/storage.json + meta.json   ← 切换的真值源  │
│   accounts.json   账号清单（token/设备绑定/额度快照，明文⚠️勿入库）    │
│   device.json     本机设备私钥（浏览器授权登录用）                     │
└──────────────┬──────────────────────────────────────────────────────┘
               │ switch（把目标档案的 storage.json 写回客户端 → 校验uid → 重启）
               ▼
        客户端以目标账号身份登录
```

**账号进入档案仓的两条路**：
1. **客户端登录 → 捕获**（capture / `twa_watch.py` 守护自动做）——原生理路径；
2. **合成**（`twa_synth.py synth`）——2026-09-19 突破：blob 加密完全可逆，
   用「现成档案当结构模板 + accounts.json 里的 token」直接造出可切换档案，
   不需要该账号在本机登录过（前提：token 未失效，合成前会验证）。

## 二、核心机制（逆向实证）

### 1. 登录态 blob 加解密（与 waxilo/TraeWorkAssistant 的 trae_auth.rs 一致）

```
密文 = base64( header[6B]="tc"+版本 + seed[32B随机] + AES-128-CBC(密文) )
密钥 = SHA512( SHA512(seed) || COMBO )   → key=[0:16], iv=[16:32]
明文 = SHA512摘要[64B] || JSON正文（PKCS7 去填充）
```
- `encrypt_blob` 为其严格逆：`_pkcs7(SHA512(pt)||pt)` + 新随机 seed，实测 round-trip 一致。
- **加密可逆 ⇒ 客户端登录态可以离线合成/修复**，这是整个 synth 方案的基石。

### 2. 服务端 API 鉴权（用错前缀 = code 1001）

```
Authorization: Cloud-IDE-JWT <token>     ← 不是 Bearer！
x-device-id: <数字设备ID>                 ← 取 iCubeAuthInfo://icube-dc:<did> 键名里的 did
User-Agent: TRAE SOLO CN/1.107.1
```

| 接口 | 用途 | 关键返回 |
|---|---|---|
| `POST /trae/api/v2/ug/checkin_credits/status` | 签到状态 | `enable/checked_in/credits`；9090=活动不可用 |
| `POST /trae/api/v2/ug/checkin_credits/claim` | 签到 | code=0 成功 |
| `POST /trae/api/v2/pay/ide_user_ent_usage` | 余额 | 余额 = `total_amount - consumed_amount` |
| `POST /icube/api/v1/user`（头 `x-icube-token`） | 客户端登录校验 | `loginAllowed:true` 即客户端认此 token |

### 3. 客户端多开（唯一正确姿势）

两份 `/Applications/TRAE SOLO CN*.app` 的 `package.json` `name` 相同 ⇒ 数据目录同名，
**直接双击副本会抢主客户端目录**。必须显式 `--user-data-dir`，且**直接跑 Electron 二进制时
第一个参数必须是 `Resources/app`，否则报 `bad option: --user-data-dir=...` 秒退**：

```bash
"/Applications/TRAE SOLO CN.app/Contents/MacOS/Electron" \
  "/Applications/TRAE SOLO CN.app/Contents/Resources/app" \
  "--user-data-dir=$HOME/Library/Application Support/TRAE SOLO CN 3" --no-sandbox &
```

`capture`/守护扫的是 `TRAE SOLO CN*` 全部目录，所以多开实例里登录的账号一样会进档案仓。

## 三、工具清单与使用决策树

```
要做什么？                          用什么（scripts/ 下，均可双击）
─────────────────────────────────────────────────────────────
日常切账号                          切换Trae账号.command（菜单数字键，r=重捕获）
新账号只有浏览器 token、切不了        一键自动收尾.command（synth→verify→checkin 一条龙）
新账号走客户端登录（验证码）          开空白客户端.command 登录 → 捕获新账号.command
以后登录即自动入库                   自动捕获守护.command（15s 扫描，双击由你的终端常驻）
批量签到 + 刷余额快照                twa_checkin.py all（--dry-run 试跑；exit 1=有账号失败）
批量多开/停实例管理                  traework-multi-open.sh（交互菜单，来自 1172 项目）
```

| 脚本 | 行数 | 职责 |
|---|---|---|
| `twa_switch_account.py` | ~250 | `capture`/`status`/`switch <手机号>`，档案式登录态交换（切换核心） |
| `twa_scan_accounts.py` | ~260 | 解密所有副本 storage.json 发现账号；`--inject` 新增 / `--update` 刷 token（写前备份+自检） |
| `twa_switch_menu.py` | ~110 | 交互菜单（列档案/切换/r 重捕获/a 收尾） |
| `twa_synth.py` | ~275 | 合成/校验/回滚档案（`list`/`synth`/`verify`/`rollback --uid`） |
| `twa_checkin.py` | ~170 | 签到 + 余额快照刷新（`checkin`/`credits`/`all`） |
| `twa_watch.py` | ~150 | 登录态自动捕获守护（copy2 保 mtime ⇒ 天然幂等） |
| `login_switch.rs` / `waxilo-login-switch.patch` | — | Rust 侧参考实现与补丁 |
| `traework-multi-open.sh` | ~180 | 多开实例管理（启停/状态，`--user-data-dir` 隔离） |

## 四、实测踩坑记录（别再踩一遍）

1. **Electron 直接启动秒退**：少传 `Resources/app` 参数 ⇒ `bad option: --user-data-dir=...`。
   曾导致 `开空白客户端.command` 一直没真正开过第二实例（输出重定向到 /dev/null 看不到报错）。
2. **鉴权前缀**：`Bearer` 恒 1001；必须 `Cloud-IDE-JWT`。裸调加签接口任何 header 组合都过不了。
3. **storage.json 单账号**：登新顶旧，不当时捕获即永久丢（合成可救回 token 未失效的）。
4. **同名 app 抢目录**：双击 `TRAE SOLO CN 2.app` 不是多开。
5. **常驻进程必须由用户终端起**：AI 沙箱里 nohup/launchctl 起的守护活不过单次工具调用。
6. **改 accounts.json 前必备份 + 结构自检**（应用端遇到坏结构会静默清空账号，血泪教训）。
7. **签到 enable=false**：服务端活动未对该账号开放（通常是没在客户端登录激活过），切过去
   登一次再签。token 本身可能是好的——别把「签不了」一律归因为「token 坏」。

## 五、现状（2026-09-19）与未决问题

- **6 账号 / 6 档案全部就位**：4 份捕获 + 2 份合成（`136****59`、`199****29`）。
- **✅ 合成方案已实证**：第二实例（CN3）加载合成档案后，客户端主动重写了 storage.json
  （9.2K→14.7K）且解密身份仍是 `136****59` —— 客户端接受了合成登录态；随后 capture 已把
  该账号升级为"真捕获"档案。`199****29` 仍为合成态，切一次即可同样升级。
- 两个新账号签到返回 9090（活动未激活），在客户端登录一次后应可签（`136****59` 现在已
  在第二实例里处于登录态，可再跑 `twa_checkin.py all` 验证）。
- 助手 GUI（v0.1.3，Tauri/Rust）**源码工程缺失**，要改 GUI 得先找回/重建源码。

## 六、相关资料与原始位置

- 逆向长文：`docs/`（架构分析 / 六仓库对比 / 上游 v3 深度分析，复制自 12_开源项目研究）。
- 研究仓库原件（未搬动）：
  `12_开源项目研究/references/{TraeWorkAssistant-mac, Trae-Work-CN-Account-Manager, trae-credential-reverse-engineering, traehop}`
  —— 其中 `trae-credential-reverse-engineering/docs/FINDINGS.md` 与
  `src/phase6-decryption/traeClient.ts` 是本工具集逆向结论的原始出处。
- 多开脚本原件：`1172-github项目/020-multi-open-tools/apps/`（doubao/wechat 兄弟脚本同款套路）。
- GUI 助手数据：`~/Library/Application Support/cn.traework.assistant/`（含 accounts.json ⚠️明文 token）。
