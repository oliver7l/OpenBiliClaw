# 旅游模块（travel）

> **改动前先读 §真值源**。这个模块最贵的教训不是代码，是「md 和 db 谁说了算」
> 从来没定清楚过，导致同一份行程在两个地方各自漂移。

---

## 1. 模块职责

围绕一次家庭旅行（当前实例：**国庆北疆金秋 8 日游**，2026-10-02 ~ 10-09，11 人）
提供「查得到」的能力：行程、航班、住宿、费用、成员、行李清单，以及机票比价。

**三个数据来源，各自不同用途：**

| 来源 | 位置 | 充当什么角色 |
|---|---|---|
| `10_旅游/travel.db` | SQLite（8 张表） | **派生视图 + 独有可变状态**（清单勾选、身份证号） |
| `10_旅游/*.md` | Markdown（10 篇） | **内容真值源**（「最终定稿」那一篇定全局） |
| `10_旅游/ctrip-ticket-crawler/*.json` | 爬虫产物 | 机票比价（实时低价 vs 心理价位） |

> 2026-09-18 起数据目录从 `data/travel/` 迁至项目根 `10_旅游/`（编号工作区，
> 与 `01_`~`09_` 口径一致，个人旅行资料不入 git）。`TravelConfig` 默认值与
> `scripts/travel/build_travel_db.py` 默认常量均已同步。

---

## 2. 端点（全部 `GET`，前缀 `/api/travel`）

| 端点 | 数据源 | 返回 |
|---|---|---|
| `/flights` | 爬虫 JSON | 每条航线最低价 + `vs_baseline`（降幅告警）+ Top3 |
| `/documents` | `data_path` 目录 | md 文档列表（文件名 / 大小 / 时间） |
| `/doc` | 单个 md 文件 | Markdown 全文（默认 `新疆旅行预算.md`） |
| `/overview` | `budget_doc` | 预算明细（7 项）+ 三方案对比（5 项） |
| `/itinerary` | **travel.db** | 8 天行程 + 11 成员 + 22 条清单 |
| `/expenses` | **travel.db** | `trip_expenses` 逐笔费用 |
| `/flights-detail` | **travel.db** | `trip_flights` 按去程/返程分组 |
| `/hotels` | **travel.db** | `trip_hotels` 逐晚住宿 |

⚠️ **路径解析**（2026-09-15 修）：`TravelConfig` 的 4 个配置项全部由
`build_travel_router()` 里的 `_under_root()` 统一解析 ——
**相对路径按项目根、绝对路径原样、`~` 展开**。此前 DB 端点硬编码
`_project_root()/"data"/"travel.db"`，文档端点在 `data_path == ""` 时直接 404。

配置默认值（`src/openbiliclaw/config.py::TravelConfig`）：

```toml
[travel]
data_path    = "10_旅游"                                       # md 目录，禁止留空
db_path      = "10_旅游/travel.db"
budget_doc   = "新疆旅行预算.md"
flights_json = "ctrip-ticket-crawler/our_routes_results.json"
```

`data_path` **必须给非空默认值**：`config.toml` 本身在 `.gitignore` 内（换机即丢），
留空会让桌面端「✈️ 旅行」三个子标签直接 404。`_route_registry.py` 里显式留空时
回退到 `TravelConfig` **类默认值**（不是回退到 `""`，这点曾经写错）。

---

## 3. 真值源（务必按这个口径改）

> **md 是「内容」的真值源，db 是「派生视图 + 独有可变状态」。**

| 内容 | 谁是爹 | 说明 |
|---|---|---|
| 每日天数 / 日期 / 标题 / 住宿 / 餐饮 / 车程 | `新疆8天精确时间游玩计划-最终定稿.md` | 结构性字段，**始终跟随 md** |
| `trip_days.description` | **db 优先** | 人工精炼文案，生成器**只在原值为空时**补 md 摘要 |
| 航班 / 住宿 / 成员的内容列 | md | 单向 md → db |
| `trip_checklist.owner` / `.done` | **只有 db** | md 里根本没有这两列，生成器绝不写 |
| `trip_members.id_card` | **只有 db** | 同上 |
| `trips.destination` / `.status` / `.notes` | **只有 db** | 生成器不回写 trips，只用它定位行程 |
| `trip_expenses` | ⚠️ 见下 | md 与 db 的键粒度不一致，默认不同步 |

**同步范围（默认）**：`days` / `flights` / `hotels` / `members`。
**需显式 `--include`**：`expenses` / `checklist` —— 理由见 §4.3。

---

## 4. 生成器 `scripts/travel/build_travel_db.py`

### 4.1 用法

```bash
# 预演（默认）——只打 diff，一个字节都不写
.venv/bin/python scripts/travel/build_travel_db.py

# 落库
.venv/bin/python scripts/travel/build_travel_db.py --apply

# 换源 / 换库 / 换行程
.venv/bin/python scripts/travel/build_travel_db.py --md 10_旅游/xxx.md --db /tmp/t.db --trip-id 1

# 范围控制
.venv/bin/python scripts/travel/build_travel_db.py --only hotels
.venv/bin/python scripts/travel/build_travel_db.py --include checklist --dry-run

# 机器可读
.venv/bin/python scripts/travel/build_travel_db.py --json
```

### 4.2 参数

| 参数 | 默认 | 作用 |
|---|---|---|
| `--md` | `10_旅游` 下首个名字含「定稿」的 md | 源 Markdown |
| `--db` | `10_旅游/travel.db` | 目标库 |
| `--trip-id` | `trips` 里 id 最小的 | 写到哪个行程（**不自动新建**） |
| `--mode` | `dry-run` | `apply` 才真写 |
| `--only` | — | 只同步这几张表（可重复） |
| `--include` | — | 额外同步 `expenses` / `checklist` |
| `--prune-stale` | 关 | 删除 db 里有、md 里没有的行 |
| `--force-description` | 关 | 连人工 `description` 也用 md 摘要覆盖 |

### 4.3 幂等键与安全阀

| 表 | 幂等键 | 备注 |
|---|---|---|
| `trip_days` | `(trip_id, day_number)` | `description` 仅补空 |
| `trip_flights` | `(trip_id, flight_type, flight_no, passengers)` | 同一航班不同乘机人分组必须能共存 |
| `trip_hotels` | `(trip_id, day_number)` | **刻意不带 `hotel_name`**，见下 |
| `trip_members` | `(trip_id, name)` | `id_card` 不写 |
| `trip_expenses` | `(trip_id, category, item)` | ⚠️ 现有键本身不唯一，慎用 |
| `trip_checklist` | `(trip_id, category, item)` | **只插不改** |

三条设计权衡（都有回归测试锁着）：

1. **住宿键不带酒店名**：db 现有行被人工加了后缀（亚朵S→**亚朵S酒店**、维也纳→**维也纳酒店**），
   带名字做键会把 7 晚全判成新行 —— apply 一遍等于原地复制一份。代价是同一晚不能分两间房，
   所以加了 `day_number` 重复键守卫（撞上会报错而不是静默覆盖）。
2. **`--prune-stale` 默认关**：删数据不可逆，先把「陈旧 N 条」报出来让人决定。
3. **疑似改名单独报**：`↔ 疑似改名 ('去程','CZ5196','刘霞,姐夫,田佳禾,岳母') →
   ('去程','CZ5196','刘霞霞,田海军,田嘉和,师保花')`。这种 `md 全称 vs db 简称`
   的分歧**不自动合并**（也可能真是两笔），只喊人来看。

### 4.4 当前真实 diff（2026-09-15 dry-run 实测）

```
[trip_days]     更新 8              —— 标题/住宿/餐饮/车程跟随 md
[trip_flights]  新增 3 / 陈旧 4 / 疑似改名 4
[trip_hotels]   更新 7              —— 含 V1 遗留的布尔津/奎屯改成 V3 的白哈巴/克拉玛依
[trip_members]  更新 8 / 未变 3     —— 备注、关系、年龄
```

其中 `trip_flights` 的新增/陈旧来自**真实分歧**：md 把 10-07 的 CZ2312 三个人写成一行、
票价 `-`；db 是「爸爸 ¥3,647」+「三嬢三姑爷 ¥1,830」两笔分开买的（db 更细）。
这类取舍不替用户决定，等他拍板。

---

## 5. 解析器 `src/openbiliclaw/travel/md_parser.py`

纯函数、无 IO、无 sqlite —— pytest 可以直接喂样本字符串。

- **结构匹配**：章节按「层级 + 标题正则」定位（`住宿安排`、`每日(精确)?行程`…），
  表格按**表头列名**取值。不按中文标题全等匹配 —— 标题加个 `⚠️` 就静默空解析，
  是最难查的一类失败。
- **缺章节必报错**：找不到章节抛 `TravelMarkdownError`，绝不返回空列表 ——
  「标题改了 → 静默同步 0 行 → 页面继续显示旧数据，且没人知道」是最坏的失败模式。
- 导出：`parse_outline` / `parse_meta` / `parse_days` / `parse_flights` /
  `parse_hotels` / `parse_members` / `parse_expenses` / `parse_checklist` / `parse_document`。

容易踩的反例（都有测试）：

- `含在去程¥6600内` → **`None`** 而不是 6600：否则 HU7446 这笔会被计入机票总额。
- `田嘉和` 是一个人的名字，按「和」切词会切成 `田嘉`。
- `21:00-01:00+1` / 跨零点 → 到达日期必须 +1 天，否则落地时间比起飞还早。
- 费用小计行（`**合计：¥6,506**`）必须跳过，否则重复计数。

---

## 6. 前端契约

两个入口，**字段名必须完全一致**：

| 入口 | 文件 |
|---|---|
| 桌面端 | `web/desktop/assets/js/app.js` + `travel-view.js`（VM） |
| 移动端 | `web/js/views/travel.js` |

真实字段名（2026-09-15 前桌面端全读错，恒 `undefined`）：

| 桌面端旧写法 ❌ | 后端真实字段 ✅ |
|---|---|
| `a.price` / `a.drop` / `a.flight` | `vs_baseline.diff` / `vs_baseline.pct` / `lowest_flight` |
| `r.departure_time` | `lowest_departure`（完整时间戳，需 `clockOf()` 压成 `HH:MM`） |
| `r.child_price` | `child_fare` |

VM 约定沿用项目范式（`assets/js/travel-view.js`，UMD）：浏览器挂 `window.TravelView`，
node 可直接 `require`，测试用 `node -e` 真跑断言，**不用 grep 源码**。

⚠️ 新增 JS 必须登记到 `api/_web_ui_routes.py` 里 `_desktop_index_response()`
的那个 **`for script in (...)` tuple**（静态资源 `?v=` 版本号）；它是函数内编译期
常量，**改完要重启 API**。`_desktop_page_names` 是另一个清单（页面白名单），加错不报错也不生效。

---

## 7. Schema（`10_旅游/travel.db`）

```
trips         id, title, destination, start_date, end_date, people_count, status, budget, notes
trip_days     id, trip_id, day_number, date, title, description, transport, accommodation, meals, highlights, notes
trip_flights  id, trip_id, flight_type, flight_no, airline, departure_city, arrival_city,
              departure_time, arrival_time, departure_airport, arrival_airport, aircraft,
              duration, passengers, passenger_count, price, order_no, notes
trip_hotels   id, trip_id, day_number, date, city, hotel_name, star_rating, address,
              check_in, check_out, room_type, room_count, price, included_in_tour, guest_count, breakfast, notes
trip_expenses id, trip_id, category, item, detail, amount, currency, created_at
trip_members  id, trip_id, name, relation, age, id_card, notes
trip_checklist id, trip_id, category, item, owner, done, notes
```

---

## 8. 测试

| 文件 | 覆盖 |
|---|---|
| `tests/travel/test_md_parser.py` | 21 例：各表解析 + 6 组反例（缺章节/改名/坏时刻/金额/人名） |
| `tests/travel/test_build_travel_db.py` | 11 例：dry-run 不写库、幂等、状态列不被冲、幂等键、错误路径、`--prune-stale` |
| `tests/api/test_api_travel.py` | 16 例：路由表存在性、路径解析、默认值、注入临时库读真数据 |
| `tests/desktop/test_travel_view_model.py` | 12 例：前端字段名由 node 真跑断言 |

每条断言都做过**鉴别力校验**（把实现改错 → 必须变红），已验证的突变：
去掉 checklist `insert_only`、住宿键加回 `hotel_name`、dry-run 也提交、
航班键去掉 `passengers`、金额改贪心匹配。

---

## 9. 未完成 / 待拍板

- [ ] `trip_expenses` 的 `(category, item)` 键去重 + 是否纳入默认同步范围
- [ ] `trip_flights` 的 CZ2312 分歧：跟着 md 合成一行，还是保留 db 的两笔分账
- [ ] 桌面端旅行页能否编辑（现在是纯只读）
- [ ] 移动端 `views/travel.js` 与桌面端 `travel-view.js` 尚未共用同一份 VM

---

## 10. 变更记录

- **2026-09-15** Step 1：路径改配置驱动（`TravelConfig.db_path` + `data_path` 非空默认 +
  删 4 处硬编码）；修前端字段漂移并抽出 `travel-view.js`；登记版本号清单；端点 8/8 回到 200。
- **2026-09-15** Step 2：新增纯函数解析器 `travel/md_parser.py` 与生成器
  `scripts/travel/build_travel_db.py`（默认 dry-run、幂等键 upsert、状态列永不覆盖、
  `--prune-stale` / `--include` / 疑似改名告警）。**未对真实库执行 apply**，等拍板。
- **2026-09-15** Step 3：本文档 + 32 例测试（全部做过鉴别力校验）。

方案全文见 `docs/plans/旅游模块梳理与整合方案.md`。
