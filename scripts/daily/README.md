# scripts/daily —— 每日推送脚本

给「定时把项目状态推到聊天」用的一组只读脚本。全部走 `OBC_API_BASE`（默认 `http://127.0.0.1:8420`）
或只读 sqlite 连接，不做任何写入（唯一例外是把当日快照写到 `data/daily_snapshots/`）。

| 脚本 | 用途 | 输出 JSON 末行 | 建议时间 |
|---|---|---|---|
| `job_brief.py` | 求职简报：逾期/今日待办 + 未来 N 天面试 | `overdue/due_today/upcoming_interviews` | 08:00 |
| `diary_morning.py` | 日记晨间简报：昨日口述 + 今日提醒 + 历史上的今天 + 待收口 | `fragments_yesterday/open_loops_total` | 07:30 |
| `reading_daily.py` | 阅读库日报：昨天读完 + 画像更新 + 明天推荐 | `finished_today/tomorrow` | 08:30 |
| `pipeline_health.py` | 采集线体检：源状态 + 积压 + 子系统上次运行 + pm2 + 库体积 | `ok/issues` | 09:00 |
| `application_digest.py` | 投递进展日报：与昨日快照 diff | `has_changes/added/changed` | 21:00 |

## 用法

```bash
cd /Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw
.venv/bin/python scripts/daily/job_brief.py --days 3
.venv/bin/python scripts/daily/diary_morning.py --loops 3
.venv/bin/python scripts/daily/reading_daily.py --top 5
.venv/bin/python scripts/daily/pipeline_health.py [--quiet]
.venv/bin/python scripts/daily/application_digest.py [--dry-run]
```

## 快照与对比

`data/daily_snapshots/` 下两类文件，按日期命名，只增不减（可手动清理老文件）：

- `applications_YYYY-MM-DD.json` —— 投递记录全量快照，**日报靠它算变化**
- `pipeline_health_YYYY-MM-DD.json` —— 积压 / pm2 重启数 / 库体积快照，用来算「比昨天多了多少」

首次运行时没有历史快照，只会输出当前盘面（不算变化），第二天起才有效。

## 阈值（想调就改文件顶部常量）

- `pipeline_health.py`：`STALE_LIMITS`（各子系统允许的最大沉默小时数）、`DISK_GROWTH_WARN_MB`、`DISK_TOTAL_WARN_GB`
- 积压告警：得到相关 > 500 条，或单个源队列单日新增 > 300 条
