# 架构重构计划（渐进式、低风险）

> 目标：应对系统持续变大（app.py 15K行、database.py 8.6K行、cli.py 8.5K行），渐进式重构。
> 原则：**小步快跑、可回滚、不改变行为、每步验证**。

## 核心原则

1. **小步快跑**：每次只改一个小模块，不搞大爆炸式重构
2. **可回滚**：每一步都能快速回退（Git 提交粒度细）
3. **不改变行为**：重构只改变代码结构，不改变功能和接口
4. **测试保障**：重构前运行测试，重构后立即运行测试验证
5. **先易后难**：从最安全、收益最大的部分开始
6. **文档同步**：每一步都更新对应文档

## 当前架构状态

### 已有模块化基础（降低重构风险）

- **RuntimeContext**（`api/runtime_context.py`）：所有路由通过它访问运行时组件，本身就是依赖注入容器
- **路由注册函数模式**：`saved_sync_routes.py` 的 `register_saved_sync_routes(app, ctx)`、`auth.py` 的 `register_auth_routes`
- **API 工具函数**（`api/utils.py`，已提取）：纯函数集中管理
- **缓存层**（`storage/cache.py`）：统一两级缓存

### 待拆分的大文件

| 文件 | 行数 | 拆分目标 |
|------|------|---------|
| `api/app.py` | 15.4K | 按领域拆到 `api/routes/`，app.py 只留入口 |
| `storage/database.py` | 8.6K | 按领域拆到 `storage/repositories/` |
| `cli.py` | 8.5K | 按命令组拆到 `cli/commands/` |
| `runtime/refresh.py` | 3.1K | 拆分调度器和各平台 producer |
| `recommendation/engine.py` | 3.0K | 拆分排序、表达生成、疲劳控制 |
| `discovery/engine.py` | 2.8K | 拆分发现策略、评估、候选管理 |
| `web/desktop/assets/js/app.js` | 9.5K | 按页面拆到 `js/pages/` + `js/components/` |

## 路线图

### 阶段0：准备工作 ✅ 已完成

- [x] 创建重构分支 `refactor/architecture`
- [x] 运行完整测试套件建立基线（3425 passed）
- [x] 记录已知环境问题（hupu/toutiao CLI 缺失、文件描述符限制）

### 阶段1：纯新增模块 / 提取纯函数（风险极低）

**策略**：只提取**纯函数**（无副作用、无闭包依赖），提取后在原文件保留导入，调用方式不变。

- [x] `api/utils.py`：提取4个纯函数（normalize_source_platform 等），测试验证通过
- [ ] 继续提取 `_is_rfc1918_ipv4`、`_usable_lan_candidate` 等网络工具函数
- [ ] 提取 `_config_backup_path`、`_snapshot_config_file` 等配置工具函数
- [ ] 提取 `_select_init_platforms`、`_event_row_id` 等纯函数

**验证方式**：每个函数提取后运行相关测试 + 完整测试套件。

### 阶段2：路由拆分（风险中）

**策略**：复制 `register_saved_sync_routes(app, ctx)` 模式，按领域拆分。

```
api/routes/
├── __init__.py
├── diary.py          # 日记路由
├── pool.py           # 内容池路由
├── recommendation.py # 推荐路由
├── discovery.py      # 发现路由
├── soul.py           # 画像路由
├── health.py         # 健康路由
└── meta.py           # 元信息/状态路由
```

**注意**：路由函数依赖 `create_app()` 闭包变量，拆分时需改为通过参数传入 `ctx: RuntimeContext`。

### 阶段3：存储层拆分（风险中）

**策略**：按领域拆分 database.py。

```
storage/repositories/
├── base.py          # 仓储基类
├── diary.py         # 日记 CRUD
├── content.py       # 内容池 CRUD
├── event.py         # 事件 CRUD
├── recommendation.py # 推荐 CRUD
└── discovery.py     # 发现 CRUD
```

### 阶段4：CLI 拆分（风险中）

**策略**：按命令组拆分 cli.py。

### 阶段5：前端拆分（风险中）

**策略**：按页面拆分 app.js（复制日记模块按需加载模式）。

### 阶段6：内部实现重构（风险高，谨慎）

**策略**：只改内部实现，外部接口和行为完全不变。每个模块重构前确保有完整测试覆盖。

## 测试基线

| 指标 | 值 | 说明 |
|------|-----|------|
| 测试用例 | 3473 个 | 收集总数 |
| 通过 | 3425 个 | 基线 |
| 失败 | 13 个 | 已知环境问题（hupu/toutiao CLI 缺失） |
| 跳过 | 16 个 | 已知跳过 |
| 错误 | 19 个 | 已知环境问题 |
| 文件描述符限制 | ≥16384 | macOS 默认 256 会导致大量假失败 |

## 每次重构必须执行的检查

### 重构前
- [ ] 运行测试，确认基线通过
- [ ] 记录性能基线
- [ ] 明确重构范围（只改什么，不改什么）

### 重构后
- [ ] 运行完整测试套件（用 `ulimit -n 16384` 避免文件描述符假失败）
- [ ] 确认结果与基线一致（不允许新增失败）
- [ ] 手动验证关键功能
- [ ] 更新相关文档
- [ ] 提交（`refactor: ...` 前缀）

## 已知环境问题（非重构引入）

1. **hupu / toutiao CLI 不存在**：`tests/` 中依赖外部 CLI 的测试失败（`/Users/imac/.local/bin/hupu` 不存在）
2. **文件描述符限制**：macOS 默认 256，测试套件需要 ≥16384 才能完整运行
3. **外部 API 403**：某些测试依赖外部服务（如 auto-update），网络受限时失败
