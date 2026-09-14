# PM2 部署拓扑（原生进程）

> 最后核对：2026-09-14　｜　权威来源：`ecosystem.config.json` + `scripts/devops/check_pm2_ecosystem.py`

本机通过 pm2 托管 **21 个进程**（19 个 Python producer + 2 个 shell 包装脚本）。
`ecosystem.config.json` 必须与 `pm2 jlist` 的实际拓扑一致——这是换机 / pm2 重装后
能恢复全部采集能力的前提。

## 1. 进程清单（21 个）

### 常驻服务（2）

| 进程 | 入口 | 端口 / 说明 |
|---|---|---|
| `openbiliclaw-api` | `start-api.sh` | `:8420` 主 API（全部 HTTP 端点） |
| `pool-feed-api` | `start-pool-feed.sh` | `:8421` 独立推荐流服务，只读 `pool.db`；与主 API 进程隔离，主进程的采集/LLM/冷算不影响推荐流秒开 |

### 信息流采集（11）

| 进程 | 入口 | 参数 |
|---|---|---|
| `openbiliclaw-bili-feed` | `runtime/bilibili_producer.py` | — |
| `openbiliclaw-douyin-feed` | `runtime/douyin_producer.py` | `--interval 6` |
| `openbiliclaw-hupu-feed` | `runtime/hupu_feed_producer.py` | — |
| `openbiliclaw-hupu-bxj` | `runtime/hupu_feed_producer.py` | `--bxj --interval 12` |
| `openbiliclaw-toutiao-feed` | `runtime/toutiao_feed_producer.py` | — |
| `openbiliclaw-v2ex-feed` | `runtime/v2ex_producer.py` | `--mode api` |
| `openbiliclaw-v2ex-rss` | `runtime/v2ex_producer.py` | `--mode rss` |
| `openbiliclaw-xhs-feed` | `runtime/xhs_producer.py` | — |
| `openbiliclaw-zhihu-feed` | `runtime/zhihu_producer.py` | — |
| `openbiliclaw-youtube-feed` | `runtime/youtube_producer.py` | — |
| `openbiliclaw-xiaoyuzhou-feed` | `runtime/xiaoyuzhou_feed_producer.py` | — |

### 收藏 / 点赞同步（7）

| 进程 | 入口 | 参数 |
|---|---|---|
| `openbiliclaw-bili-favorites` | `runtime/bilibili_favorites_producer.py` | `--loop --all` |
| `openbiliclaw-xhs-favorites` | `runtime/xhs_favorites_producer.py` | `--loop` |
| `openbiliclaw-zhihu-favorites` | `runtime/zhihu_favorites_producer.py` | `--loop` |
| `openbiliclaw-xiaoyuzhou-favorites` | `runtime/xiaoyuzhou_favorites_producer.py` | `--loop --all` |
| `openbiliclaw-x-favorites` | `runtime/x_favorites_producer.py` | `--loop --all` |
| `openbiliclaw-douyin-likes` | `runtime/douyin_producer.py` | `--likes --interval 24` |
| `openbiliclaw-douyin-favorites` | `runtime/douyin_producer.py` | `--favorites --interval 24` |

### 汇聚（1）

| 进程 | 入口 | 参数 |
|---|---|---|
| `openbiliclaw-inbox-merger` | `runtime/inbox_merger.py` | `--interval 5` |

## 2. 两套启动写法（重要）

**Python producer**（19 个）——可执行文件是 venv 的 python，**脚本路径放在 `args[0]`**：

```json
{
  "name": "openbiliclaw-zhihu-feed",
  "cwd": "<项目根>",
  "script": "<项目根>/.venv/bin/python3",
  "args": ["src/openbiliclaw/runtime/zhihu_producer.py"],
  "interpreter": "none",
  "exec_mode": "fork",
  "instances": 1,
  "autorestart": true,
  "watch": false,
  "env": { "PYTHONHOME": "", "PYTHONPATH": "" }
}
```

**Shell 包装脚本**（2 个）——入口即 `script`，`interpreter` 为 `bash`，不带 `args`。

⚠️ 不要把 Python producer 写成 `script = xxx_producer.py` + `interpreter = .venv/bin/python3`。
那种写法会让 pm2 用 venv 的 python 去“解释”一个 Python 可执行文件，全部起不来。

⚠️ `env.PYTHONHOME/PYTHONPATH` 置空是**刻意**的：pm2 daemon 常从被污染的 shell 继承
宿主（WorkBuddy / 豆包浏览器 / TRAE）注入的 Python 路径。不清空会把无关的
`python-packages` 目录塞进 `sys.path`。

## 3. 恢复流程

```bash
cd <项目根>

# 方式一（推荐，最省事）：用 pm2 自己的快照
pm2 resurrect

# 方式二：从版本控制里的声明重建
pm2 start ecosystem.config.json

# 两者做完都应固化快照
pm2 save
```

- `pm2 resurrect` 读 `~/.pm2/dump.pm2`，是**上次 `pm2 save` 时的拓扑**。
- `pm2 start ecosystem.config.json` 读版本控制里的声明，**换机 / 新环境用这个**。
- 两者不一致时，以 `ecosystem.config.json` 为准并 `pm2 save` 覆盖快照。
- ⚠️ `pm2 start ecosystem.config.json` 会**重启所有同名进程**（等于应用新配置），
  请勿在采集敏感时段执行。

## 4. 一致性检查

```bash
# 声明 vs 实际运行（在部署机上跑）
.venv/bin/python scripts/devops/check_pm2_ecosystem.py

# 仅检查声明自身是否自洽（无需 pm2，可用于 CI）
.venv/bin/python scripts/devops/check_pm2_ecosystem.py --offline
```

退出码 `0` 表示一致；`1` 会打印逐项差异（缺声明 / 多声明 / script / args / interpreter / cwd 不符）。
**改动 `ecosystem.config.json` 或增删进程后，请跑一次这个脚本。**

它**不**接入 `scripts/ci-check.sh`：CI 环境没有 pm2，也没有这套部署拓扑，
把部署期检查塞进提交期护栏只会制造噪音。

## 5. 待决事项（不算已完成）

1. **`collect_v2ex_archive.py` 的部署状态与文档不符。**
   `scripts/content_library/README.md` 与本脚本 docstring 都写明它由 pm2 常驻
   （进程名 `openbiliclaw-v2ex-archive`，`--loop --interval 24`），且自称是
   “推荐池 V2EX 断流的真正补法”（走 GitHub 镜像 `cxyfreedom/v2ex-hot-hub`，
   免 token、带全文、带历史）。但实测：**该进程从未以该名运行过**
   （`~/.pm2/logs/` 无对应日志、`dump.pm2` 无、当前 pm2 无）；`data/v2ex_hot_hub/`
   的 `.git` 更新于 09-13 21:07，来自**手动执行**。
   → 要么补上部署，要么修掉 README 的误导描述。**当前决定：先不动**，
   `ecosystem.config.json` 只描述实际在跑的 21 个。

2. **V2EX 两个模式并存**：`--mode api` 间歇 403（`www.v2ex.com/api/topics/*.json`
   被风控，09-12 曾整轮 `feed skipped: no_data`），`--mode rss`（RSSHub）是稳定主力，
   每 6 小时约 50 条。是否让 api 模式退场由用户定。

3. **pm2 日志无轮转，体积失控**（2026-09-14 实测）：
   `frpc-zero-out.log` 1.9G、`zhihu-api-error.log` 1.0G、`frpc-passnat2/1` 831M/781M、
   `openbiliclaw-v2ex-rss-error.log` 224M（其中内容其实是 INFO 级正常日志）。
   建议安装 `pm2-logrotate` 并为 stderr 泛滥的进程调低日志级别。

## 相关

- 模块级整理底账：`docs/module-cleanup-inventory-2026-09-14.md`（§4 批次 ①）
- 一致性检查：`scripts/devops/check_pm2_ecosystem.py`
- Docker 部署（另一路径）：`docs/docker-deployment.md`
