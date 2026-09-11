# 迁移 049 目录内容到当前项目并清理原目录

## Context（背景）

用户在项目根 `002-探索项目/` 下有 `049-新疆旅行预算/` 和 `049-新疆之旅/` 两个目录，想确认其功能/数据是否已并入当前 OpenBiliClaw 的旅游系统，并打算迁移完整后删除原目录。用户特别强调：**注意区分哪些是真正的功能/数据，哪些只是当时的参考项目**——参考项目应放入 `references/` 目录，而不是塞进当前项目源码。

### 现状调查结论

1. **当前项目的旅游模块已存在**：
   - `src/openbiliclaw/travel/routes.py`：提供 `/api/travel/flights`、`/api/travel/doc`、`/api/travel/overview`
   - `src/openbiliclaw/web/js/views/travel.js`：旅游 tab 前端
   - `config.example.toml` 已有 `[travel]` 段模板
   - 但 **本机 `config.toml` 缺少 `[travel]` 段** → `data_path=""`，旅游 tab 读不到数据

2. **数据迁移已进行一大半**（`data/` 已在 `.gitignore`，是本地数据）：
   - `data/travel/新疆旅行预算.md`（17104 字节，与 049 源一致）
   - `data/travel/ctrip-ticket-crawler/our_routes_results.json`（68654 字节，与 049 源一致）
   - `data/travel/ctrip-ticket-crawler/` 含核心爬虫：`browser_automation/`、`pure_requests/`、`flights.db`、`README.md`、`pyproject.toml`、`routes.json`
   - `data/` 下还有新疆零星产物（`xinjiang_travel_plan.pdf` 等，与本任务无关，不动）

3. **049 里有两个是外部 clone 的 GitHub 参考项目**：
   - `049-新疆旅行预算/ctrip-ticket-crawler/`（origin `Yybrook/ctrip-ticket-crawler`，保留 `.git`、LICENSE(MIT)）
   - `049-新疆旅行预算/travel-price-advisor/`（origin `nzy-user/travel-price-advisor`，保留 `.git`）
   - 当前项目已有 `references/` 目录专门放这类 clone 项目（bili-video2book、wandao 等，均保留 `.git`）

4. **049-新疆之旅/** 是空目录。

## 迁移分类（最终方案）

| 049 内容 | 归类 | 去向 |
|---|---|---|
| `ctrip-ticket-crawler/` 完整目录（含 .git） | **参考项目**（外部 clone） | → `references/ctrip-ticket-crawler/` |
| `travel-price-advisor/` 完整目录（含 .git） | **参考项目**（外部 clone） | → `references/travel-price-advisor/` |
| `新疆旅行预算.md` | **用户数据** | 已在 `data/travel/`，保留 |
| `our_routes_results.json`、核心爬虫代码 | **功能数据** | 已在 `data/travel/ctrip-ticket-crawler/`，保留 |
| `049-新疆之旅/` | 空目录 | 删除 |

## 实施步骤

### 1. 参考项目移入 references/
- 将 `/Volumes/固态硬盘1T/002-探索项目/049-新疆旅行预算/ctrip-ticket-crawler/` **整体移动**到 `/.../040-OpenBiliClaw/references/ctrip-ticket-crawler/`（含 `.git`、LICENSE）
- 将 `/.../049-新疆旅行预算/travel-price-advisor/` **整体移动**到 `/.../040-OpenBiliClaw/references/travel-price-advisor/`（含 `.git`）
- 采用 `mv`（保留完整 git 历史与 LICENSE），而非复制

### 2. data/travel 数据核实
- 核验 `data/travel/新疆旅行预算.md` 与 `data/travel/ctrip-ticket-crawler/our_routes_results.json` 已在（已确认存在）即可，无需重复搬运

### 3. 接线配置 config.toml
- 在 `config.toml` 增加 `[travel]` 段，指向已迁入的数据目录：

```toml
[travel]
data_path = "data/travel"
budget_doc = "新疆旅行预算.md"
flights_json = "ctrip-ticket-crawler/our_routes_results.json"
```

- 这样 travel 路由从 `data/travel/` 解析预算文档与机票结果
- 同步确认 `config.example.toml` 的 `[travel]` 段注释说明数据目录已在项目内（可选微调）

### 4. 清理原 049 目录
- 迁移完成后删除：
  - `/Volumes/固态硬盘1T/002-探索项目/049-新疆旅行预算/`（已无剩余内容需要保留）
  - `/Volumes/固态硬盘1T/002-探索项目/049-新疆之旅/`（空目录）
- **仅在确认 data/travel 数据完好后删除**

### 5. 文档更新（遵循项目规则）
- `docs/modules/`：travel 相关（若有）补充"已实现功能"说明数据目录
- `docs/changelog.md`：当前版本块加一条迁移条目
- 若改动配置引用，同步 `docs/modules/config.md` 的 `[travel]` 段说明

## 验证
1. `python -c "import tomllib; c=tomllib.load(open('config.toml','rb')); print(c['travel'])"` 确认 `[travel]` 配置生效
2. 读取 `data/travel/新疆旅行预算.md`、`data/travel/ctrip-ticket-crawler/our_routes_results.json` 确认可读
3. 启动服务后请求 `/api/travel/doc`、`/api/travel/overview`、`/api/travel/flights` 返回数据而非 404
4. 前端旅游 tab 正常渲染预算与机票价格
5. 确认 049 两个目录已删除，`references/` 下两个项目 `.git` 完好

## 约束
- 不删除 `config.toml` 中其他配置
- 不修改 travel 路由/前端代码（只需接线数据）
- 删除前反复确认数据已就位