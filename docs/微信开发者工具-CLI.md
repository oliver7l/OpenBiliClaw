# 微信开发者工具 CLI 使用说明

> 2026-09-17 建立。用于「乐仔相册」微信小程序的本地开发与预览。

## 一、安装位置（免 sudo）

微信开发者工具安装在**用户目录**而非 `/Applications`，因为安装包 payload 路径是
`./Applications/wechatwebdevtools.app`，可以直接装到当前用户家目录，不需要管理员密码：

```bash
mkdir -p ~/Applications
installer -pkg ~/Downloads/wechat_devtools_2.02.2608070_darwin_arm64.pkg \
          -target CurrentUserHomeDirectory
```

- 安装后路径：`~/Applications/wechatwebdevtools.app`
- 安装包由腾讯 Developer ID 签名 + Apple 公证，可信
- pkg 内约 5091 个 `._` 开头文件是 macOS 元数据，安装时自动过滤，属正常现象

## 二、⚠️ 最大坑：Electron 变量污染导致 IDE 打不开窗口

WorkBuddy 沙箱会向子进程注入两个变量：

| 变量 | 值 | 后果 |
| --- | --- | --- |
| `ELECTRON_RUN_AS_NODE` | `1` | Electron 二进制**退化成纯 Node 进程**，GUI 永不出现，且静默退出无报错 |
| `NODE_OPTIONS` | `--require=.../node-language-shim.cjs` | 强制主进程加载沙箱 shim，启动异常 |

因为 `open` 会把调用者的环境变量传给新启动的 app，所以在沙箱 shell 里跑
`open ~/Applications/wechatwebdevtools.app` 会「返回成功但进程为零」。

**解法**：启动前 `unset ELECTRON_RUN_AS_NODE NODE_OPTIONS NODE_EXTRA_CA_CERTS NODE_REPL_EXTERNAL_MODULE`，
或用下述包装器。

## 三、包装器（已就绪）

| 命令 | 用途 |
| --- | --- |
| `wxide` | 启动 IDE 图形界面 |
| `wxide <项目路径>` | 启动 IDE 并打开指定小程序项目 |
| `wxdev <子命令>` | 调用开发者工具 CLI |

两个脚本位于 `~/.local/bin/`，内部已处理变量清理。

## 四、首次使用需手动完成两件事

CLI 依赖 IDE 的 HTTP 服务端口，该端口**默认关闭**，且登录态必须扫码获得：

1. 打开 IDE → 右上角「设置」→「安全设置」→ 打开「服务端口」
2. 扫码登录微信（登录态会保存，之后无需重复）

完成后 `wxdev` 才能正常调用 IDE。

## 五、常用命令

```bash
# 打开项目（IDE 需已启动且服务端口已开）
wxdev open --project "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/17_云端应用/miniprogram-album"

# 生成预览二维码（手机扫码，体验版）
wxdev preview --project <路径> --qr-output /tmp/preview.png

# 上传代码（需真实 AppID，非 touristappid）
wxdev upload --project <路径> -v 1.0.0 -d "初版"

# 开启自动化调试（配合 miniprogram-automator 做端到端测试）
wxdev auto --project <路径> --auto-port 9420

# 退出 / 重新登录
wxdev quit
wxdev login
```

其他可用子命令：`islogin`、`close`、`build-npm`、`auto-preview`、`auto-replay`、
`cloud`、`reset-fileutils`、`cache`、`build-ipa`、`build-apk`。

## 六、当前工程状态

- 工程目录：`17_云端应用/miniprogram-album/`
- `project.config.json` 中 `appid` 为 `touristappid`（游客模式）
  - 游客模式：可在模拟器看界面，**不能** `preview` / `upload`
  - 真机预览需换成真实 AppID
- `urlCheck: false`：调试时跳过域名校验
  - 正式版需在 mp 后台配置 request / downloadFile 合法域名（`lezai.odn.cc`）

## 七、常见问题

**IDE 进程在跑但看不到窗口？**
检查启动环境是否有 `ELECTRON_RUN_AS_NODE=1`，用 `wxide` 启动。

**CLI 报 `IDE service port disabled`？**
IDE 里手动打开服务端口（见第四节）；或关掉 IDE 后用
`wxdev open --project <路径> --port 3799` 让它重新拉起。

**日志中大量 `SSL handshake failed / net_error -100`？**
IDE 联网握手失败的噪音日志，不影响本地模拟器与编译。

日志位置：`/tmp/wxdevtools.log`
