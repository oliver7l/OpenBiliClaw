# 豆瓣观影/读书画像分析

## Context（背景）

豆瓣书影音模块已上线（`data/douban.db` 存 1410 条影视/书/音乐清单，桌面「📚 豆瓣」tab 展示）。用户希望进一步做**画像分析**来理解自己的观影/读书偏好与成长脉络。

用户已确认两个决策：
1. **分析深度**：统计画像 + LLM 深度画像报告**都做**
2. **展示位置**：在现有豆瓣 tab 内**新增一个「画像」子视图**

## 目标

1. **统计画像**：纯数据聚合，不调 LLM——按年份趋势、书影音类型偏好、来源分布、早年 vs 近年口味变化等，后端算好 JSON，前端以卡片/图表展示。
2. **LLM 深度画像报告**：让模型读用户全部书影音清单，生成"我的观影/读书心路"文字报告（可选、需用户点击生成、可缓存）。
3. **前端**：豆瓣 tab 内加「画像」子视图（与现有"书影音清单"主视图并列切换）。

## 已核实的项目模式（复用依据）

| 关注点 | 既有模式 | 文件 |
|---|---|---|
| LLM 高层调用 | `llm_service.complete_structured_task(system_instruction, user_input, ...)` 返回 `resp.content` | `src/openbiliclaw/diary/service.py` L243-252 |
| LLM 深度报告 prompt 构造 | 统计摘要 → 填充结构化 prompt → 要求模型生成报告 | `src/openbiliclaw/diary/insights.py` L547-597 |
| 统计聚合（内存分组） | `defaultdict` + `Counter` 按时段/类型分组，后端算好返回 | `src/openbiliclaw/diary/insights.py` L219-271 |
| 路由拿 LLM service | health 懒加载：`llm_service = getattr(ctx, "llm_service", None)` | `src/openbiliclaw/api/health_routes.py` L69-70 |
| 前端子视图切换 | travel tab：`data-travel-view` subtab 显隐 flights/overview/doc | `web/desktop/assets/js/app.js` L4281-4301 |
| 现有豆瓣子 tab | `douban-app.js` 里分类/状态筛选按钮 | `web/desktop/assets/js/douban-app.js` |

## 实施步骤

### 1. 统计画像后端（analytics）
- **新增** 在 `src/openbiliclaw/douban/` 下加 `analytics.py`：`DoubanAnalytics` 类，纯内存聚合 `douban_items` 清单：
  - `yearly_trend()`：按 `date` 的年份分组计数（影视看过/书读过/音乐听过分别），反映阅读/观影活跃度时间线
  - `type_distribution()`：按 `category` × `status` 分布
  - `yearly_earliest_latest()`：最早/最近年份（品味跨度）
  - `top_names()`：可提取部分高频词
  - 字段兜底：遇到空 `date`/缺 `pub`=空等不报错
- 聚合纯 Python（`defaultdict`+`Counter`），不调 LLM，快。复用 `douban/store.py` 的 `list_items(limit=10000)` 拿全量。

### 2. LLM 深度画像报告（insight）
- **新增** 在 `src/openbiliclaw/douban/` 下加 `insight.py`：
  - `build_profile_prompt(stats)`：仿 `diary/insights.py::build_yearly_report_prompt`，把统计摘要 + 近年代表清单填进 prompt，要求模型生成"我的观影/读书画像"报告（第一人称、说洞察不说教、分主题/风格/成长脉络、800字内）。
  - `generate_profile_profile(llm_service)`：异步调用 `complete_structured_task`，返回文本报告。
  - **缓存**：报告结果 + 生成时间存 `data/douban/profile_report.json`（`data/` 已 .gitignore）；非 `force` 时读缓存，避免每次重调 LLM。
- LLM 不可用（llm_service 为 None）时优雅返回 `{"ok": false, "reason": "LLM 未配置"}`，不报错。

### 3. 路由扩展
- **修改** `src/openbiliclaw/douban/routes.py`：
  - `build_douban_router(config, llm_service=None)` 增加可选参数。
  - 新增 `GET /api/douban/analytics` → 统计画像 JSON（纯数据，无需 LLM）。
  - 新增 `POST /api/douban/insight`（body `{force: bool=false}`）→ 生成/读缓存 LLM 深度报告；`GET /api/douban/insight` 读已有报告。
- **修改** `src/openbiliclaw/api/_route_registry.py` L300 区域：注册 douban 路由时从 ctx 传 `llm_service=getattr(ctx, "llm_service", None)`。

### 4. 前端（画像子视图）
- **修改** `src/openbiliclaw/web/desktop/index.html` 豆瓣 section：在工具栏加一个子视图切换按钮组「书影音 | 画像」。
- **修改** `src/openbiliclaw/web/desktop/assets/js/douban-app.js`：
  - 加两个子视图容器 `doubanListView`（现有清单）+ `doubanProfileView`（新增）。
  - 画像视图：顶部统计卡（总数、最早/最近年份、分类分布）+ 年度趋势（简表/条）+ 年代轨迹；下方「生成深度画像报告」按钮 → `POST /api/douban/insight` → 渲染报告文本；已有报告时直接显示。
  - 子 tab 切换仿 travel：`data-douban-view` 显隐。
- **修改** `src/openbiliclaw/web/desktop/assets/js/app.js`：无需改（douban-app.js 自包含；reload 逻辑已存在）。

### 5. 测试
- **新增** `tests/api/test_douban_analytics.py`（或并入 test_douban.py）：
  - 统计：yearly_trend/type_distribution 对样本数据聚合正确、空数据不报错。
  - 洞察：mock llm_service 时 `generate_profile_profile` 返回缓存/生成内容；llm 为 None 时返回 ok=false。
  - routes：`GET /api/douban/analytics` 200；`POST /api/douban/insight` mock 下 200。
  - 用 tmp db 隔离真实数据。

### 6. 文档
- 更新 `docs/modules/douban.md`：已实现功能加"统计分析画像 + LLM 深度报告"；API 表加 `/api/douban/analytics|insight`。
- 更新 `docs/changelog.md` 顶部加 v0.3.227 条目。

## 验证
1. `GET /api/douban/analytics` 返回真实统计（总量 1410、分类分布、年度趋势、最早/最近年份）。
2. `POST /api/douban/insight`（LLM 配置时）生成一份中文报告并在 tab 展示；再次调用命中缓存。
3. 前端豆瓣 tab 内「书影音 | 画像」子视图切换正常；画像视图出统计卡 + 报告。
4. LLM 未配置时洞察接口优雅返回 ok=false，统计照常可用。
5. `pytest tests/api/test_douban*` 通过；ruff + mypy 干净。

## 约束
- 统计画像不调 LLM（快、确定性）；深度报告才调 LLM（用户主动触发、可缓存）。
- 报告缓存 `data/douban/profile_report.json`，不提交 git（`data/` 已忽略）。
- 不新增 tab，只在豆瓣 tab 内加子视图；保持 douban-app.js 自包含，避免改 app.js 主体。
- 复用 diary/insights 的 LLM 报告 prompt 与 `complete_structured_task` 模式，不另造 LLM 封装。