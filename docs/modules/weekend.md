# 周末怎么玩模块（weekend）

> 把日记情绪、豆瓣想看/想读、灵魂画像、本地活动种子聚合成本地优先的「周末怎么玩」计划：
> 自动给出 3 个带「为什么适合你」理由的方案 + 直接能执行的卡片，支持确认、跳过与打卡复盘。

## 概述

`weekend/` 包实现纯离线的周末活动规划器。它以「你」为唯一输入，从多处已有数据推断这周末该出门还是宅家、去哪、看什么，产出一份可读、可执行、事后可复盘的周末计划。

| 输入 | 来源 | 用途 |
|------|------|------|
| 日记情绪 | `data/diary.db`（`diary_entries.mood_score`，已回填） | 近 14 天情绪基线 → 决定 出门/宅家 文案与精力 |
| 豆瓣想看/想读 | `data/douban.db`（`douban_items` 且 `status='wish'`） | 宅家方案的「弹药库」（书 / 影） |
| 灵魂画像周末模式 | `obc_soul` `ContextMode.weekend_patterns`（可选，当前多为空） | 补充偏好描述 |
| 本地活动种子 | `data/weekend.db`（`weekend_spots`） | 出门方案候选（深圳本地活动） |

| 组件 | 职责 | 核心文件 |
|------|------|----------|
| 数据模型 | `WeekendSpot` / `PlanOption` / `WeekendPlan` / `CheckIn` / `PlanMode` / `PlanStatus` | `models.py` |
| 存储层 | SQLite 表管理、CRUD、检索、打卡 | `store.py`（`WeekendStore`） |
| 业务层 | 多源聚合、模式判定、方案生成、周五定时触发 | `engine.py`（`WeekendEngine`） |
| CLI | `openbiliclaw weekend` 命令组 | `cli.py` |
| API | `/api/weekend/*` REST 接口 | `routes.py` |
| 配置 | `[weekend]` 段 + `WeekendConfig` | `config.py` |

设计原则：**默认纯规则、可离线、可测试**，不依赖任何外部 LLM/网络；`use_llm=true` 时仅把规则生成的方案交给模型润色文案，失败自动回退规则结果（failsafe）。活动数据通过手动 `seed` 导入或联网 provider（默认关闭）进入本地库，坚持本地优先。

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| 计划生成（auto） | ✅ | `generate(mode='auto')` 依据情绪基线决定混合方案，产出 3 个带「为什么适合你」理由的方案 |
| 出门方案 | ✅ | 从 `weekend_spots` 按「优先区域 + 免费 + 带娃/独处」评分分组（亲子 / 独处 / 文化），生成可执行卡片 |
| 宅家方案 | ✅ | 读豆瓣 `wish` 列表，生成「读本书 / 看部片」两类方案，附具体书名/片名 |
| 显式模式 | ✅ | `mode=outdoor` / `indoor` 可强制只出出门 / 宅家方案 |
| 情绪驱动文案 | ✅ | 近 14 天情绪高/低调整方案语气与精力标注（low/medium/high） |
| 活动种子库 | ✅ | `WeekendSpot` 结构化入库（区域/时间/交通/是否免费/适合人群/有效期），默认排除过期 |
| 种子导入 CLI | ✅ | `openbiliclaw weekend seed` 把 `data/weekend_seed_activities.json` 导入 `weekend.db`（UPSERT，可清空重导） |
| 历史计划 | ✅ | 列表 / 按周六日期查询 / 是否已存在本周计划 |
| 确认 / 跳过 | ✅ | `decide_plan(week, status)` 把计划置为 `confirmed` / `skipped` |
| 事后打卡复盘 | ✅ | `CheckIn`（方案序号 + 1~5 评分 + 备注），可多次打卡 |
| 周五主动推送 | ✅ | 运行时循环 `_loop_weekend_plan` 每 600s 轮询，仅在周五 18:00–23:00 且本周未生成时触发，发布 `weekend.plan` 事件 |
| LLM 润色（可选） | ✅（默认关） | `use_llm=true` 时润色 title/why/actions，异常回退规则结果 |
| 单元测试 | ✅ | 10 个用例覆盖模型、存储、生成、周五门控、`saturday_of_week` 边界 |

## 模块结构

```
src/openbiliclaw/weekend/
├── __init__.py     # 导出公开 API：PlanMode/PlanStatus/WeekendSpot/WeekendPlan/PlanOption/CheckIn/WeekendStore/WeekendEngine/DEFAULT_DB_PATH/build_weekend_router
├── models.py       # 数据模型（StrEnum + dataclass）
├── store.py        # WeekendStore：weekend_spots / weekend_plans / weekend_checkins 三表
├── engine.py       # WeekendEngine：多源聚合 + 方案生成 + 周五定时触发
├── routes.py       # build_weekend_router → /api/weekend/*
└── cli.py          # weekend 命令组（register(app) 注册到主 CLI）
```

挂载点：
- CLI：`src/openbiliclaw/cli/__init__.py` 中 `try/except` 调用 `openbiliclaw.weekend.cli.register(app)`。
- 路由：`src/openbiliclaw/api/_route_registry.py` 中 `try/except` 调用 `build_weekend_router(...)` 并 `include_router`。
- 配置：`src/openbiliclaw/config.py` 的 `WeekendConfig` 聚合进主 `Config.weekend`。
- 运行时：`_refresh_loop_supervision_mixin.py` 的 `run_forever()` 注册 `_loop_weekend_plan`（600s）。

## 公开 API

### CLI

```bash
openbiliclaw weekend generate [-m auto|outdoor|indoor] [-w YYYY-MM-DD]   # 生成本周末计划
openbiliclaw weekend plans [-n 20]                                       # 列出历史计划
openbiliclaw weekend spots [-d 宝安] [-f 带娃]                           # 列出本地活动（默认排除过期）
openbiliclaw weekend decide -w YYYY-MM-DD [-s confirmed|skipped]         # 确认/跳过某周计划
openbiliclaw weekend checkin -p <plan_id> -o <序号> -r <1~5> [-t 备注]   # 打卡复盘
openbiliclaw weekend seed [-p data/weekend_seed_activities.json] [--clear]  # 导入活动种子
```

### Python 引擎

```python
from openbiliclaw.weekend import WeekendStore, WeekendEngine, PlanMode
from openbiliclaw.weekend.models import WeekendSpot

store = WeekendStore()                       # 默认 data/weekend.db
engine = WeekendEngine(store)

engine.recent_mood(days=14)                  # 情绪基线 dict（avg / count / tone）
engine.douban_wish(limit_per=6)              # {"books": [...], "movies": [...]}
engine.soul_weekend_patterns()              # 灵魂画像周末模式描述（可为 None）

plan = engine.generate(mode="auto")          # WeekendPlan（默认本周六，3 个方案）
plan.options[0].why                          # 「为什么适合你」

engine.saturday_of_week(date(2026, 9, 9))    # "2026-09-12"（周三→本周六）
engine.is_friday_push_window()               # 周五 18–23 点内为 True

store.import_spots([WeekendSpot(...)])       # UPSERT 活动种子
store.list_spots(district="南山")            # 默认排除 valid_until < 今天
store.save_plan(plan)
store.decide_plan("2026-09-12", "confirmed")
store.add_checkin(CheckIn(plan_id=plan.id, option_index=0, rating=5))
```

### HTTP API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/weekend/status` | 模块状态（spots 数、最近计划、配置可用模式） |
| POST | `/api/weekend/generate` | 生成计划 `{mode?, week_of?}` |
| GET | `/api/weekend/plans?limit=` | 历史计划列表 |
| GET | `/api/weekend/plans/{week_of}` | 某周末计划（无则 404） |
| POST | `/api/weekend/plans/{week_of}/decide` | 确认/跳过 `{status: confirmed\|skipped, option_index?}`；status 非法 400 |
| POST | `/api/weekend/checkin` | 打卡 `{plan_id, option_index, rating(1~5), note?}` |
| GET | `/api/weekend/spots?district=&suitable_for=&limit=` | 活动种子列表（默认排除过期） |

事件（运行时发布到 `event_hub`）：`{"type": "weekend.plan", "week_of", "mode", "option_count", "source": "friday_push"}`，仅在周五窗口内、且本周尚未生成计划时推送。

## 配置项

`config.toml` 的 `[weekend]` 段（对应 `WeekendConfig`）：

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `enabled` | bool | `true` | 是否启用模块 |
| `db_path` | string | `data/weekend.db` | 本地 SQLite 路径 |
| `auto_friday_push` | bool | `true` | 运行时是否在周五窗口自动生成并推送 |
| `friday_push_hour` | int | `20` | 周五推送窗口起始小时（窗口 = `[hour, hour+3)`，默认 20:00–23:00） |
| `use_llm` | bool | `false` | 是否用 LLM 润色方案文案（失败回退规则） |
| `seed_path` | string | `data/weekend_seed_activities.json` | `seed` 命令默认导入的种子 JSON |
| `online_providers` | list[str] | `[]` | 联网抓取 provider 名称（默认空 = 仅本地种子，保持本地优先） |

```toml
[weekend]
enabled = true
db_path = "data/weekend.db"
auto_friday_push = true
friday_push_hour = 20
use_llm = false
seed_path = "data/weekend_seed_activities.json"
online_providers = []
```

## 设计决策

1. **本地优先、联网可选**：活动素材默认来自手动 `seed` 的本地 JSON，联网 provider 默认关闭且需显式声明；即使打开，也以「手动投喂优先、联网补充」为原则，不静默抓取。契合项目整体本地优先立场。
2. **纯规则可离线可测**：核心生成逻辑不依赖任何模型或网络，全部基于已有本地数据（日记/豆瓣/画像/种子）做确定性聚合，因此能在 `tmp_path` 上完整单测。LLM 仅作为可选的文案润色层，且必须 failsafe 回退。
3. **auto 永远给「混合」方案**：情绪基线只调整文案语气与精力标注（high/low），不改变方向；`mode='auto'` 固定产出「出门 + 宅家×2」的混合 3 方案，保证无论天气/心情都有可选项，符合 MVP「3 个带理由的方案 + 可执行卡片」目标。
4. **评分驱动出门候选**：出门活动按「优先区域（宝安/南山/福田）+2、带娃/家庭 +2、免费 +1、独处/休息 +1」打分并分组（亲子 / 独处 / 文化），每组生成一个带「为什么适合你」理由与交通/时间卡片的方案。
5. **以周六为周键**：`saturday_of_week()` 以周一为一周起点，周一~周六映射所在周周六，周日回退到刚过去的周六，作为计划的 `week_of` 主键，保证一周只生成一份计划、可幂等覆盖。
6. **周五推送是「守门员」而非生成器**：运行时每 600s 轮询，仅当处于周五 18:00–23:00 且本周 `weekend_plans` 尚无记录时才生成并发布 `weekend.plan` 事件；其余时间静默，避免重复打扰。手动 `generate` 不受窗口限制。
7. **与现有挂载链一致**：沿用 `interview` 模块的「CLI `register` + 路由注册表 + `Config` 数据类 + 运行时 loop」四件套，全部用 `try/except` 包裹，模块缺失/异常不影响主程序启动。
