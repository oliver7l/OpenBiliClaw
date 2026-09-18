# scripts/daily —— 每日推送脚本

给「定时把项目状态推到聊天」用的一组脚本。全部走 `OBC_API_BASE`（默认 `http://127.0.0.1:8420`）
或 sqlite 连接；除注明外均为只读，唯一写入是快照/草稿落到 `data/daily_snapshots/`、
刷题进度落到 `interview_questions.db.iq_daily`、跟进待办落到 `interview.db.todo`。

## 面试模块套件（2026-09-16 上线）

| 脚本 | 用途 | 写入 | 接入点 |
|---|---|---|---|
| `merge_question_pools.py` | 把 interview.db 的 199 条岗位预测题并入统一刷题队列（iq_questions/iq_queue），幂等 | 两张 iq 表 | 手动/结构变更后跑一次 |
| `quiz_daily.py` | 从统一队列选今天该刷的题（按激活计划 daily_target），含昨日欠账提醒 | iq_daily 当日行（已选 id 存 notes） | 早报第 3 段 |
| `review_digest.py` | 近 2 天未复盘面试 + 近 3 天口述里的面试碎片 → 复盘草稿骨架 | `data/daily_snapshots/review_drafts/review_<date>_<公司>.md` | 早报末段（自动化据此补全四节） |
| `interview_ammo.py` | 明天有面试 → 生成弹药卡（面试信息 + 该公司预测题 + 速记卡路径） | 无 | 独立自动化 20:00，有面试才推送 |
| `application_digest.py`（增强） | 投递状态变化 → 自动建跟进待办（标题带「（自动）」，2 天后到期，同名 pending 去重） | interview.db.todo | 21:00 投递日报 |

已知坑：SQLite 裸 `WHERE company`（无操作符）对纯文本列恒为假，必须写 `WHERE company != ''`。

## 早报五段（morning_digest.py）

| 脚本 | 用途 | 输出 JSON 末行 |
|---|---|---|
| `morning_digest.py` | **合并早报**：依次跑下面五个并拼成一条（日记 + 求职 + 刷题 + 阅读 + 复盘） | `sections/failed` |
| `diary_morning.py` | 日记晨间简报：昨日口述 + 今日提醒 + 历史上的今天 + 待收口 | `fragments_yesterday/open_loops_total` |
| `job_brief.py` | 求职简报：逾期/今日待办 + 未来 N 天面试 | `overdue/due_today/upcoming_interviews` |
| `quiz_daily.py` | 今日刷题 5 题 + 昨日欠账 | `today_ids/count/backlog` |
| `reading_daily.py` | 阅读库日报：昨天读完 + 画像更新 + 明天推荐 | `finished_today/tomorrow` |
| `review_digest.py` | 复盘材料包（当天无素材时输出为空，早报自动跳过该段） | `has_material/items` |

## 用法

```bash
cd /Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw
.venv/bin/python scripts/daily/morning_digest.py            # 合并早报全文
.venv/bin/python scripts/daily/quiz_daily.py --count 3      # 临时改刷题数
.venv/bin/python scripts/daily/interview_ammo.py [--days 2] [--include-today]
.venv/bin/python scripts/daily/application_digest.py [--dry-run] [--no-todo]
.venv/bin/python scripts/daily/merge_question_pools.py      # 预测题增量导入
```

## 快照与对比

`data/daily_snapshots/` 下按日期命名，只增不减（可手动清理老文件）：

- `applications_YYYY-MM-DD.json` —— 投递全量快照，**日报靠它算变化**
- `pipeline_health_YYYY-MM-DD.json` —— 积压 / pm2 重启数 / 库体积快照
- `review_drafts/review_<date>_<公司>.md` —— 复盘草稿，早报自动化补全后应不再含「待补全」

## 阈值（想调就改文件顶部常量）

- `pipeline_health.py`：`STALE_LIMITS`（各子系统允许的最大沉默小时数）、`DISK_GROWTH_WARN_MB`、`DISK_TOTAL_WARN_GB`
- 积压告警：得到相关 > 500 条，或单个源队列单日新增 > 300 条
- 刷题目标：`iq_plans.daily_target`（当前「秋招面试冲刺」= 5 题/天）
