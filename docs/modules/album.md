# 乐仔时间线相册模块（album）

> **真值源铁律**：照片数据（原图、缩略图、HEIC 预览、归档目录）的真值源在
> 源库（项目内 `08_乐仔相册/乐仔的照片`，2026-09-18 自 030-夸克网盘迁入），本模块只是**发布视图**。不要在本项目里
> 增删照片或改月份目录——要改就改源库，再重跑页面构建脚本。

---

## 1. 模块职责

把「乐仔的照片」（5206 张 / 16.5GB，按拍摄年月归档的原始时间线）以移动
优先的相册页对外提供：

- `GET /album/` —— 手机端相册页（3 列缩略图墙 + 年月快捷跳转 + 全屏查看）
- `GET /album/thumbs/<名>.jpg` —— 缩略图（5206 张 / 219MB，源库 `_thumbs/`）
- `GET /album/heic/<名>.jpg` —— HEIC 预转 JPEG（750 张，源库 `_heic_jpg/`）
- `GET /album/full/<月>/<名>` —— 原图（源库根，按月目录）
- `GET /album/login/` —— 公开登录页（未登录导航的落点；**注意末尾斜杠**，见 §7）
- `GET /album/manifest.json`、`/album/assets/icon-*.png` —— PWA 资产

**与 `lezai` 的分工（两个不同源库，别混）**：

| | `lezai` | `album`（本模块） |
|---|---|---|
| 源库 | 08_乐仔相册/**乐仔相片库** | 08_乐仔相册/**乐仔的照片** |
| 规模 | 2576 张（人脸识别后分类） | 5206 张（未识别原始时间线） |
| 内容 | 成长分析平台页 + 同伴网络 | 纯浏览（缩略图墙 + 原图） |
| 挂载 | `/lezai/` | `/album/` |
| 门禁 | ⚠️ 公开（历史遗留） | ✅ 走密码门禁 |

## 2. 目录与路径锚点

| 内容 | 位置 | 说明 |
|---|---|---|
| 模块代码 | `src/openbiliclaw/album/` | `paths.py` 锚点 + `__init__.py` 导出 |
| 页面资产 | `src/openbiliclaw/web/album/` | index.html（1.4MB，数据内嵌）+ manifest + 图标，**随包入 git** |
| 登录页 | `src/openbiliclaw/web/album/login/index.html` | 唯一公开的页面 |
| 页面构建脚本 | `scripts/build_album_page.py` | 读源库索引 → 生成页面，**只读源库** |
| 外部源库 | `/Volumes/固态硬盘1T/002-探索项目/030-夸克网盘/乐仔的照片/` | 真值源；可用 `OBC_ALBUM_SOURCE_DIR` 覆盖 |
| 源库索引 | 源库内 `乐仔的照片-相册.html` | 构建脚本的输入（5206 条：缩略图/时间/体积/原图路径） |

## 3. 更新流程（源库新增照片后）

```bash
cd <项目根> && .venv/bin/python scripts/build_album_page.py
# 换盘/换机：--source /path/to/乐仔的照片
pm2 restart 84        # 静态挂载无版本号机制，必须重启才对外生效
```

脚本会打印自检结果，**这三个数必须为 0 才发**：HEIC 预览缺失、缩略图缺失。
（HEIC 命名规则坑：`_heic_jpg/` 是「stem 原样保留大小写 + .jpg」，不是转大写——
`FullSizeRender (26)(1).heic` 这类混合大小写文件名会因为转大写而漏配 132 张。）

## 4. 路由挂载与门禁（⚠️ 两个铁律）

```python
app.mount("/album/thumbs", ...)   # 必须注册在 /album 之前
app.mount("/album/heic",   ...)
app.mount("/album/full",   ...)
app.mount("/album", StaticFiles(..., html=True))
```

1. **前缀吞并**：`/album` 会吞掉 `/album/thumbs` 等子前缀，子挂载必须在前面
   （同 `/lezai/thumbs` 先例）。
2. **门禁**：`api/auth.py` 的 `_PROTECTED_STATIC_PREFIXES` 登记了 `/album`。
   背景——`_is_public()` 原本对「非 `/api` 开头」一律放行，而后端经 frpc 直通
   公网（`infra/frpc/frpc-passnat4.toml` 把 8420 整个端口映射出去），裸挂等于把
   5206 张家庭照片发布到互联网。`_PROTECTED_STATIC_PUBLIC` 里的
   `/album/login`、`/album/assets`、`/album/manifest.json` 保持公开。
3. 未登录的 **HTML 导航**（Accept 含 text/html）→ 302 跳 `/album/login?next=…`；
   图片等子资源仍回 401，不连环跳转。登录成功后 session cookie 生效，同源
   图片请求自动携带，因此**不需要**给图片 URL 签名。

## 5. PWA（「像小程序」的部分）

`manifest.json` + `apple-touch-icon` + `apple-mobile-web-app-capable` 三件套齐全，
手机上「添加到主屏幕」后以 standalone 全屏运行，外观与小程序一致。图标由构建
脚本用 Pillow 现场画（暖色渐变 + 「乐」字，字体取 STHeiti）。

**手机入口卡**由 `scripts/build_album_entry_card.py` 生成，输出
`docs/album-手机入口.png`：左边局域网 `http://<本机IP>:8420/album/`（推荐，无证书
警告、微信内置浏览器也能直接开），右边公网 `https://lezai.odn.cc/album/`。
⚠️ 局域网那枚二维码把**本机 IP 写死**，换网 / DHCP 变址后要重跑脚本（IP 自动
探测，也可 `--lan-ip` 手动指定）。二维码按**整数模块尺寸**绘制，改版后务必用
`cv2.QRCodeDetector` 实测解码——肉眼看「像二维码」不算验证。

## 6. 已知边界 / 待续

- **原图体积**：单张 2–5MB，手机上首屏靠缩略图（219MB 总量）扛，点开看原图
  才有大图流量；若嫌慢可再加一档「展示尺寸」（长边 1600 的 JPEG 缓存）。
- **未纳入门禁的邻居**：`/lezai` 仍是公开的（2576 张照片），若要一并收紧，
  把 `/lezai` 加进 `_PROTECTED_STATIC_PREFIXES` 并在 `_PROTECTED_STATIC_PUBLIC`
  放行它的静态依赖即可——但会影响桌面端「🍼 乐仔」tab 的内嵌 iframe，需一起测。
- **无服务端检索**：页面数据内嵌，搜索/排序都在前端做（5206 条 DOM 操作实测可接受）。

## 7. 公网 HTTPS 通道（PassNAT 隧道 + 反代契约）⚠️

手机走 `https://lezai.odn.cc/album/`（小程序/外网访问都需要 HTTPS + 备案域名，
自建后端出不了这个门槛）。链路：

```
浏览器/小程序 ──https──> frps(114.66.28.185) ──按 SNI 原样转发 TLS──>
  frpc-lezai(本机, pm2 id=119) ──https2http 插件在此解密──> 127.0.0.1:8420
```

- 隧道配置：`/Users/imac/Downloads/1131-mindback2/infra/frpc/frpc-lezai-https.toml`
- 进程：`pm2 restart frpc-lezai`（**不要**动 id=84，那是后端本体）
- 证书目录：`/Users/imac/.obc-certs/lezai.odn.cc/`

### 7.1 四条契约（每一条都是踩出来的）

1. **TLS 在 frpc 终止**，frps 只读 ClientHello 的 SNI 做路由、不解密。所以配置里
   必须有 `[proxies.plugin] type="https2http"` + `crtPath`/`keyPath` 两个**绝对路径**，
   证书只留在本机（这也是 PassNAT 文档要求的形态）。缺了插件的表现是**握手阶段**
   就被拒：`SSL alert 112 (unrecognized_name)`，连证书都看不到。
2. **绝对不要写 `hostHeaderRewrite`**。它会把 Host 改写成 `127.0.0.1`，而 Starlette
   的「目录补斜杠」等重定向是用**请求 Host** 拼绝对 URL 的，结果浏览器被送去
   `https://127.0.0.1/album/login/` —— 登录页在手机上彻底打不开。保留原始 Host。
3. **必须声明外部协议**：`requestHeaders.set.X-Forwarded-Proto = "https"`，
   同时在 `config.toml` 把 **`trusted_proxies = ["127.0.0.1"]`**（否则
   `auth_core.effective_scheme_host` 不采信 X-Forwarded-*）。不配的表现是：
   应用以为自己在 `http://127.0.0.1`，与浏览器 `Origin: https://lezai.odn.cc`
   不同源 → **登录 403 `origin_forbidden`**，且 session cookie 不带 `Secure`。
   ⚠️ 安全前提：**`trust_loopback` 必须保持 `false`**，否则公网流量经 frp 回环
   再伪造 `X-Forwarded-For: 127.0.0.1` 就能冒充本机绕过密码。
4. **子域名一旦定下就别改**：证书是按 hostname 签发的，改名要重建隧道 + 重签证书。
   另注意这条隧道把**整个 8420**（不只 `/album`）挂到了该域名下，含公开的 `/lezai`。

### 7.2 证书怎么来

PassNAT **不代发证书**，流程：面板「证书 → 申请证书 → 手动申请」，域名填
`lezai.odn.cc` → 得到 TXT 校验值 → 提工单请客服添加 `_acme-challenge.lezai` →
回面板点「确认」→ 下载证书包 → 跑

```bash
~/.obc-certs/install-cert.sh ~/Downloads/<证书包>.zip   # 归一化 + 配对自检
pm2 restart frpc-lezai
```

`install-cert.sh` 会把证书与私钥落成固定名 `fullchain.crt` / `privkey.key`
（Apache 格式 = 站点证书 + 中间证书拼一条链），并把这两个绝对路径回填到控制台。
续期同样要重走 TXT（只支持 TXT 验证，无法自动化）。

> 域名 `odn.cc` 是 PassNAT 自家的：whois 阿里云注册、NS HiChina，
> 备案为 **浙ICP备2022019220号-4（浙江原光云计算有限公司）**，微信的备案校验能过。
> 但它是**借来的**域名，解析与证书都由服务商掌控，自用可以、别当长期资产。

### 7.3 上线自检（四条，缺一不可）

```bash
B=https://lezai.odn.cc
curl -sk -o /dev/null -w "%{http_code}\n" $B/api/health                       # 200 通道通
curl -sk -o /dev/null -w "%{http_code}\n" $B/album/thumbs/<任一>.jpg           # 401 门禁在
curl -sk -H "Accept: text/html" $B/album/ -o /dev/null -w "%{http_code}\n"     # 302 → /album/login/
curl -sk -X POST -H "Origin: $B" -H 'Content-Type: application/json' \
     -d '{"password":"…"}' $B/api/auth/login -o /dev/null -w "%{http_code}\n"   # 200 带回 Origin 能登
```

最后一条最容易漏：**不带 `Origin` 头测会假绿**（curl 默认不发 Origin，走
`req_origin is None` 的宽松分支），真浏览器一定发。

## 8. 微信小程序通道（无 Cookie 客户端的媒体签名）

小程序工程在仓库根 `miniprogram-album/`，与网页版**共用同一份真值源**（源库的
`乐仔的照片-相册.html`），但取图方式不同：

| | 网页版（PWA） | 微信小程序 |
|---|---|---|
| 图片请求 | 浏览器自动带 session cookie | `<image src>` **既不带 cookie、也带不了自定义 header** |
| 鉴权 | cookie 门禁 | URL 查询参数 `?k=<媒体签名>` |
| 数据来源 | 页面内嵌（构建时注入） | `data/photos.js` 内嵌（构建时注入） |

### 8.1 媒体签名怎么工作

`auth_core.album_media_token(session_secret)` 用 HMAC-SHA256 派生一个 32 字符
签名（`hmac(secret, "obc-album-media-v1")[:32]`），门禁里
`api/auth.py::_album_media_ok` 做常数时间比对后放行。

- **无状态**：服务端不存任何东西，重启后同 secret 推出同值；
- **可整体作废**：改 `config.toml` 的 `api.auth.session_secret` + 重启，所有已
  分发的图片链接立刻失效（这是唯一的撤销手段，故 secret 不能外泄）；
- **权限边界（关键）**：只对 `_ALBUM_MEDIA_PATHS`（`/album/thumbs|heic|full/`）
  生效。签名打不开相册页面（仍 302 跳登录），换不到 `/api` 会话，更碰不到
  `/api/auth/admin`。签名会随 URL 传播（页面、分享、日志），所以越权面必须卡死。

### 8.2 重新生成（源库新增照片后）

```bash
.venv/bin/python scripts/build_album_miniprogram.py     # 产出 data/photos.js + utils/config.js
```

生成的这两个文件含**媒体签名与个人照片路径**，已在 `.gitignore` 里排除。
脚本会跳过源库里混进来的非照片条目（`00-` 前缀，如整理时生成的总览拼图）。

### 8.3 小程序端性能约定（别踩）

5205 张图**不能**一次性渲染（小程序节点数扛不住，`setData` 单次上限 1MB）：

- 首页按**月份切换**（默认最新月），月内**分页 60 张**，且只用**路径增量**
  `setData`（`list[12] = {...}`），不重传已渲染的几千条；
- 查看页**单图**渲染 + 手势/点击切换 + 预取相邻两张，**不用 swiper**
  （swiper 会把该月全部 item 建出来）。

### 8.4 本地跑起来

1. 装微信开发者工具（稳定版，macOS ARM64 的 `.pkg`）；
2. 导入 `miniprogram-album/` 目录，AppID 选「测试号」或填自己的（工程内默认
   `touristappid` 游客模式，**真机预览需要真实 AppID**）；
3. 工具里保持「不校验合法域名/TLS 证书」勾选（`project.config.json` 已写
   `urlCheck: false`）——当前公网证书是自签占位，校验开着会加载失败；
4. 真机预览：手机开「调试」模式同样跳过域名校验。**正式版**才需要真证书 +
   备案域名（`request`/`downloadFile` 两个白名单都要填）。
