# scripts/traework — TraeWork/TRAE 多账号切换工具

`TraeWorkAssistant.app`（GUI）与本目录脚本**共用同一套数据**，都在
`~/Library/Application Support/cn.traework.assistant/`，可以混用：

| 路径 | 作用 |
|---|---|
| `accounts.json` | 账号清单（token / 设备绑定 / 额度快照）——助手界面读它 |
| `device.json` | 本机设备私钥与 device_id / machine_id（浏览器授权登录用） |
| `traework_profiles/<uid>/` | 每个账号的客户端登录态快照（`storage.json`）——**切换只认这个** |

## 三个入口

- `切换Trae账号.command` —— 双击开菜单：列档案、数字键切换、`r` 重捕获、`a` 新增账号收尾
- `捕获新账号.command` —— 新账号「登录后收尾」一键跑：捕获 → 入库 → 校验
- `自动捕获守护.command` —— 开/关后台守护：谁登录了就自动进档案仓（可选，省掉手动收尾）

## 新加账号为什么切不到 / 签不到

**切换 = 把 `traework_profiles/<uid>/storage.json` 写回主客户端。** 账号若只是通过
**浏览器授权**加进 `accounts.json`（`device.json` 那条路），本机就没有客户端登录态，
档案是空的 ⇒ 菜单里看不到、切不过去，助手侧额度也只能是 `null`。

所以新账号必须**在本机某个 TRAE SOLO CN 里真正登录过一次**（手机号+验证码），
让 `.../TRAE SOLO CN*/User/globalStorage/storage.json` 落下这个账号，才能被捕获。

### 收尾三步

```bash
cd scripts/traework
python twa_switch_account.py capture                 # ① 各副本登录态 → 档案
python twa_scan_accounts.py --inject --update        # ② 新账号入库 + 刷新已有账号 token
python twa_switch_account.py status                  # ③ 校验：档案数 == 账号数
```

或直接双击 `捕获新账号.command`（等价，且会先提醒退出 TraeWorkAssistant 以免它回写覆盖）。

### 已经登录过、但当时没捕获 → 两条路

`storage.json` 同一时刻**只保存一个账号**，登下一个就把上一个顶掉。账号还在 `accounts.json`
里（助手能列出名字）但档案仓没有它时，二选一：

1. **合成档案（不需要登录）**：`twa_synth.py synth` —— blob 加密是完全可逆的（实测
   round-trip 一致），可以用「现成档案当结构模板 + accounts.json 里的 token」直接合成
   可切换档案；合成前已验证该 token 过 `/icube/api/v1/user` 的 `loginAllowed=true`。
   双击 `一键自动收尾.command` = synth → verify → checkin 一条龙。
2. **重新登录一次**：在任一客户端副本里登录该账号 → `捕获新账号.command` 收尾
   （或开 `自动捕获守护.command` 以后自动入库）。
   浏览器授权留下的 token 若已在服务端失效，就只能走这条。

**想以后不再丢**：双击 `自动捕获守护.command` 开一个后台守护，每 15s 扫一遍所有
`TRAE SOLO CN*` 副本，谁登录了就自动进档案仓（日志 `.../cn.traework.assistant/twa_watch.log`）。
手动管理：`twa_watch.py --once|--status|--stop`。

## 不想动主客户端？开第二个实例登录

两份 `/Applications/TRAE SOLO CN*.app` 的 `package.json` 里 `name` 都是
`TRAE SOLO CN`，所以**数据目录同名**，直接双击副本会抢同一个目录。要多开必须显式指定：

```bash
# 直接跑 Electron 二进制必须把 Resources/app 作为第一个参数，否则报
# "bad option: --user-data-dir=..." 秒退（2026-09-19 实测踩坑）
"/Applications/TRAE SOLO CN.app/Contents/MacOS/Electron" \
  "/Applications/TRAE SOLO CN.app/Contents/Resources/app" \
  "--user-data-dir=$HOME/Library/Application Support/TRAE SOLO CN 3" --no-sandbox &
```

或直接双击 `开空白客户端.command`（已内置正确写法）。

`capture` 扫的是 `TRAE SOLO CN*` 全部目录，所以新实例里登的账号一样会被收进档案。
（`TRAE SOLO CN 3` / `4` 目前只剩 Crashpad 空壳，是历史的，可复用其名。）

## 脚本清单

| 文件 | 行数 | 作用 |
|---|---|---|
| `twa_switch_account.py` | ~243 | `capture` / `status` / `switch <手机号>`，档案式登录态交换 |
| `twa_scan_accounts.py` | ~250 | 解密 storage.json 发现账号；`--inject` 新增、`--update` 刷新 token（`--dry-run` 可试跑） |
| `twa_switch_menu.py` | ~100 | 交互菜单 |
| `twa_watch.py` | ~130 | 登录态自动捕获守护（`--once` / `--status` / `--stop`） |
| `login_switch.rs` / `waxilo-login-switch.patch` | — | Rust 侧参考实现与补丁 |

解密算法（与 `waxilo/TraeWorkAssistant` 的 `trae_auth.rs` 一致）：`storage.json` 的
`iCubeAuthInfo://icube.cloudide` blob → base64 → seed `[6:38]` → SHA512 派生密钥 →
AES-128-CBC 解密 → 前 64 字节摘要 + JSON 正文。

⚠️ `accounts.json` / `storage.json` 里是**明文 token**，不要入库、不要外传。
