# 15_健康模块 · 资料归拢总索引

- **建立**：2026-09-18（健康子系统资料单独归拢；**代码仍在主仓原地运行，本目录不是代码迁出**）
- **定位**：个人与家庭医疗档案管理子系统（`src/openbiliclaw/health/`），覆盖患者/就诊/健康问题/用药/化验/检查/医生/文档/预约/依从性十大实体，2026-09-16 已按「只留有数据的」原则瘦身（删 4 个零行实体）

## 一、权威位置地图（代码不动，以这里为准）

| 资产 | 权威路径 | 说明 |
|---|---|---|
| 核心代码 | `src/openbiliclaw/health/` | models 816 行 / store 1,861 行 / service 438 行 |
| 周期记录子模块 | `src/openbiliclaw/cycle/` | 独立小模块，当前**无数据**（cycle.db 不存在） |
| API 路由 | `src/openbiliclaw/api/health_routes.py` | 25 路径/54 操作，单一来源，经 `_route_registry` 收口 create_app |
| 前端页面 | `src/openbiliclaw/web/desktop/assets/js/health-app.js` | 桌面 healthPage，11 标签页，`const API='/api/health'` |
| 数据库 | `data/health.db` | 15 张 `health_*` 表，独立子库（P7 拆分，与主库锁域隔离） |
| 迁移工具 | `15_健康模块/03_脚本/migrate_health_db.py` | 子库迁移/主库空壳表清理（2026-09-15 drop 已执行过）；旧路径 `scripts/migrate_health_db.py` 已删除（不留软链） |
| 挂号监控 | `15_健康模块/03_脚本/91160_check_slots.py` | ⚠️ 与健康档案代码**无关系**、仅业务同名（91160 挂号号源监控）；旧路径 `scripts/health/` 已删除。**实测已停摆**：pm2/launchd/WorkBuddy 自动化均无此任务，凭证目录 09-14 后无运行痕迹，docs「每 2h 已上线」为过期说法 |
| 测试 | `tests/health/`、`tests/cycle/` | 特征化测试+时间线分页 |
| 模块文档 | `docs/modules/health.md` | 唯一真值；本目录 01_文档 存快照 |

## 二、本目录内容

- `01_文档/health模块文档-快照20260918.md`：模块文档快照（含 API 全表、数据模型、设计决策）
- `02_数据备份/health-backup-20260918.db`：**在线一致性备份**（sqlite3 .backup 生成，216K）
- `03_脚本/`：`migrate_health_db.py`（P7 迁移工具，根锚点已改为向上找 pyproject+src 标记）＋ `91160_check_slots.py`（挂号监控，读 `~/.workbuddy/91160-monitor/user_key.txt` 凭证，输出末行 JSON 供自动化判断；**当前无任何调度器在跑**，如需恢复挂号监控须新建自动化并把命令指向本路径）

## 三、当前数据现状（2026-09-18 备份口径）

- health_patients **17** / health_encounters **2** / health_lab_results **2**（其余实体更少）
- 主库中已无 `health_*` 残留表（P7 迁出+空壳 drop 均完成，备份在 `data/backups/`）

## 四、易混澄清（踩过的坑）

1. `GET /api/health`（系统探针）≠ `/api/health/*`（医疗档案 API）——只是共用前缀；服务探活另有约定（云端应用用 `/healthz`，本服务用根探针）。
2. `03_脚本/91160_check_slots.py` 是**挂号号源监控**，与本模块仅同名、无代码关系（原在 `scripts/health/`，2026-09-18 迁入本目录，旧路径已删除、不留软链）。
3. 文档曾提过的 `scripts/import_health_data.py` **从未入库**，勿按旧说明操作；批量导入直接调 `HealthService.create_*`。
4. cycle（周期记录）是独立包独立库，健康文档把它列为关联组件，但**不是 health 包的一部分**。

## 五、后续若要真拆独立子项目

需动：路由注册（`_route_registry`/create_app 收口）、包 import、前端 tab 注册（`_desktop_index_response` tuple 登记）、分层棘轮 AST 测试、pm2 重启验证——工作量中等，做之前先立 `docs/plans/`。
