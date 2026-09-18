# lezai.odn.cc HTTPS 证书：申请与续期

> 2026-09-17 建立。适用场景：乐仔相册的公网 HTTPS 通道（网页版 + 微信小程序真机）。

## ✅ 当前状态（2026-09-17 21:53 起）

已换成 **Let's Encrypt 正式证书**，自签占位证书退役：

| 项 | 值 |
| --- | --- |
| 签发者 | `Let's Encrypt` / 中间证书 `YR1` |
| 证书链 | `lezai.odn.cc` ← `YR1` ← `Root YR` ← `ISRG Root X1`（完整） |
| 有效期 | 2026-09-17 12:53:35 GMT → **2026-12-16 12:53:34 GMT**（90 天） |
| SAN | `DNS:lezai.odn.cc` |
| 密钥 | RSA 2048（不是 ECC，兼容性优先） |
| 端到端 | `curl` **不带 `-k`** 取登录页 → `code=200 ssl_verify=0` ✅ |

隧道只重启了 `frpc-lezai`（其余 54 个 pm2 进程 restart_time 零变化）。
`_acme-challenge.lezai` 这条 TXT 记录**已经可以删掉了**（不影响已签发的证书）。

> ⚠️ 到期前要重新走一遍流程（90 天，约 12 月中旬）。手动模式无法自动续期，见下文。

## 为什么需要真证书

自签证书在**浏览器里点「继续访问」能用**，但这几个场景会被硬拦：

- 微信小程序真机（`image` / `downloadFile` 校验证书链，开发者工具里的「不校验合法域名」只在模拟器内生效）
- 微信内置浏览器打开相册页面
- 系统层面的证书链校验（`curl` 默认校验）

所以**模拟器 + 局域网**用自签够用，**真机 / 公网分享**必须要受信任 CA 签发的证书。

## 架构前提（决定了申请方式）

| 事实 | 影响 |
| --- | --- |
| TLS 在**本地 frpc** 终止（`https2http` 插件） | 证书和私钥必须留在跑 frpc 的这台机器上，不是传到控制台 |
| PassNAT 控制台只填证书**绝对路径** | 换证书 = 换本地文件 + 重启 frpc |
| `odn.cc` 的 DNS 托管在**阿里云**（`vip1.alidns.com`），但控制权在 **PassNAT 控制台** | 拿不到 DNS API ⇒ 无法自动改 TXT ⇒ **只能用 DNS-01 手动模式** |
| Let's Encrypt API 本机**可直连**（`acme-v02.api.letsencrypt.org` 200） | 不需要代理，可直接签发 |

## 工具

`acme.sh` 装在 `~/.acme.sh`（v3.1.3）。
安装来源是 gitee 镜像（GitHub 本机不通）：

```bash
curl -sSL -o /tmp/acme.sh https://gitee.com/neilpang/acme.sh/raw/master/acme.sh
cd /tmp && sh ./acme.sh --install --force --nocron   # 必须在脚本所在目录执行
```

包装脚本：`~/.local/bin/obc-cert`（下面三条命令都是它）。

## 申请流程（首次 / 90 天后续期通用）

```bash
obc-cert request     # ① 生成 TXT 验证记录（打印出主机记录和记录值）
# ② 到 PassNAT 控制台添加 TXT：
#    隧道管理 → 域名管理 → odn.cc → 「验证DNS」
#    类型 TXT / 主机记录 _acme-challenge.lezai / 值 = 上一步打印的那串
obc-cert finish      # ③ 验证 + 签发 + 部署 + 重启 frpc + 自检
obc-cert status      # 随时查看当前证书是正式还是自签
```

`finish` 内部依次做了：`acme.sh --renew` → `--install-cert` 输出到
`~/.obc-certs/lezai.odn.cc/{fullchain.crt,privkey.key}` → `pm2 restart frpc-lezai` → 用 `openssl` +
`curl` 校验证书链和访问状态。

> **`--install-cert` 里带了 `--reloadcmd "pm2 restart frpc-lezai"`**，所以以后 acme.sh 自动续期时
> 会自己重启隧道。

## 关键技术点

**1. TXT 值在同一次申请中固定**
LE 对同一账号 + 同一域名的 pending authorization 会复用 challenge token。
实测连续两次 `--issue --dns` 拿到的 TXT 值完全相同 ⇒ **用户只需加一次记录**，
`--renew` 也复用同一值。

**2. 为什么用 RSA 2048 而不是默认的 ECC**
acme.sh 默认 `ec-256`。虽然现代设备都支持 ECDSA，但较老的 Android 微信内置浏览器
（X5 内核）对 ECDSA 证书链有历史兼容问题。这个证书要服务于家人手机，选兼容性最好的
RSA 2048（`--keylength 2048`）。

**3. 证书文件名不能改**
`frpc-lezai-https.toml` 里写死了：

```toml
[proxies.plugin]
crtPath = "/Users/imac/.obc-certs/lezai.odn.cc/fullchain.crt"
keyPath = "/Users/imac/.obc-certs/lezai.odn.cc/privkey.key"
```

`--install-cert` 的输出路径与之保持一致，所以**换证书不需要改 frpc 配置**，只要重启。

**4. 手动模式不能自动续期**
DNS-01 需要人工加 TXT，所以 acme.sh 无法配置 cron 自动续（安装时已用 `--nocron` 跳过）。
**有效期 90 天，到期前重跑一次 `request` → 加 TXT → `finish` 即可**，
到期提醒会发到 `obc-album@lezai.odn.cc`（该邮箱并不真实存在，别指望收到）。

## 排查

```bash
# TXT 是否已生效（阿里云 DNS）
dig +short TXT _acme-challenge.lezai.odn.cc @223.5.5.5

# 证书是谁签的
openssl x509 -in ~/.obc-certs/lezai.odn.cc/fullchain.crt -noout -issuer -enddate

# 隧道配置里的证书路径
grep -E "crtPath|keyPath" /Users/imac/Downloads/1131-mindback2/infra/frpc/frpc-lezai-https.toml

# 端到端校验（0 = 证书链可信）
curl -sS -o /dev/null -w "%{ssl_verify_result}\n" https://lezai.odn.cc/
```

**TXT 加了但验证失败**：DNS 传播需要时间，等 1-2 分钟重跑 `finish`；
确认主机记录填的是 `_acme-challenge.lezai`（PassNAT 若要求完整域名则是
`_acme-challenge.lezai.odn.cc`），值不要带引号。

**换完证书浏览器仍报错**：重启 frpc 前它是旧证书，`pm2 restart frpc-lezai` 后
浏览器需强制刷新（TLS 会话缓存）。

**`finish` 报「签发未成功」但证书其实是好的**：acme.sh 在「证书还没到续期时间」时
**不会输出 `Cert success`，而是打印 `Skipping. Next renewal time is: ...`**。
早先 `finish` 只认 `Cert success`，于是把这种**幂等重跑**误判成失败并 `exit 1`，
**导致部署/重启那两步被跳过**（证书签下来了但隧道还在用旧证书，症状是"签好了却仍报证书错"）。
现已修：识别到 acme.sh 跳过续期且本地已有 LE 正式证书时，打印现有有效期后继续部署。
⇒ 判断签发是否真成功的**权威依据是证书文件本身**，不是命令输出：

```bash
openssl x509 -in ~/.acme.sh/lezai.odn.cc/fullchain.cer -noout -issuer -enddate
```

**`finish` 全程会重启 frpc 两次**（`--install-cert` 的 `reloadcmd` 一次 + 脚本第 3 步一次），
是有意的安全网：确保无论 `--install-cert` 走没走成，隧道都必然加载新证书。隧道中断约 1 秒。

**`--qr-output` 之类要落盘的 CLI 子命令在沙箱内必失败**（与本证书流程无关，但同一台机器上
用微信开发者工具 CLI 时会撞到）：报「二维码输出路径无效或不存在」，换任何路径都一样，
必须脱离沙箱执行。

## 相关文档

- 反代四条契约（hostHeaderRewrite / X-Forwarded-Proto / trusted_proxies 等）：`docs/modules/album.md §7`
- 隧道配置与启动器：`.local/bin/frpc-odn.sh`、`infra/frpc/frpc-lezai-https.toml`
