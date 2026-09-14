# 框架地图（2026-09-15）

> 写给「半年后的自己」和任何第一次读这套代码的人。
> 目标：**读完这一页，能知道东西在哪、改动会波及什么**。
> 姊妹文档：`docs/module-review-2026-09-15.md`（六模块欠账清单）、`docs/module-cleanup-inventory-2026-09-14.md`（全局底账）。

---

## 1. 这个项目其实是「三条线」

看着乱，是因为它同时是三件事，且**三条线共享同一批数据库**：

| 线 | 干什么 | 怎么启动 | 出问题时的表现 |
|---|---|---|---|
| **采集线** | 18 个 producer 定时去各平台抓内容写库 | pm2 托管（`ecosystem.config.json`，21 个进程） | 推荐池空了 / 数据不更新 |
| **服务线** | 一个 FastAPI 服务（:8420）兜住所有业务 API + 两个 Web 前端 | pm2 `openbiliclaw-api`（内部是 `openbiliclaw serve-api`） | 页面转圈 / 接口 5xx |
| **工具线** | 手动跑的一次性脚本（导入、回填、迁移） | `scripts/` 下直接 `python xxx.py` | 跑完没效果 |

**判断一个问题属于哪条线**，先看它是「数据没进来」（采集线）、「接口不对」（服务线）还是「跑脚本没反应」（工具线）。三条线的排错手段完全不同。

---

## 2. 服务是怎么装起来的（**只有一条装配路径**）

```
pm2: openbiliclaw-api
      └─ cli/_build.py  _run_api_server()
            └─ api/app.py  create_app()        ← 唯一的装配函数
                  ├─ ① FastAPI + GZip/CORS 中间件
                  ├─ ② RuntimeContext             ← 所有单例的「登记处」
                  │     runtime_context.py: build_runtime_context()
                  │     构建失败 → build_degraded_runtime_context()（降级模式）
                  ├─ ③ AuthGate + register_auth_routes()   ← 密码门（局域网/远程才生效）
                  ├─ ④ register_all_routes(app, ctx, config, ...)
                  │     api/_route_registry.py            ← **业务路由的唯一入口**
                  │     └─ 30+ 个 register_xxx_routes / mount_xxx_router
                  ├─ ⑤ app.py 内联 15 个 @app.*（auth/health/notifications/cognition 等系统端点）
                  └─ ⑥ register_web_ui_routes(app, ctx)   ← 静态前端挂载
```

### 记住这一条就够
> **要加一个新 API，只改两处**：写 `api/xxx_routes.py`，然后在 `api/_route_registry.py` 里加一行。
> **不要在 `cli/`、不要在 `app.py` 里另开注册点。** 历史上正是因为多了一个注册点（`cli/_build.py` 里单独注册 `chat_analysis_routes`），导致「走 CLI 启动一切正常、裸 `create_app()` 少 15 条路径」这种极难察觉的缺陷。

### 静态前端挂在哪（两个 SPA + 两个工具页）

| URL | 源目录 | 说明 |
|---|---|---|
| `/web` | `web/desktop/` | **桌面端**（10 个子标签：备战 A / 研习 B / 复盘 C） |
| `/m` | `web/` | **移动端**（8 个子标签，**只有 A 域**，没有 B/C） |
| `/setup` | `web/setup/` | 安装向导 |
| `/shared` | `web/shared/` | 两侧共用资源 |

⚠️ 桌面和移动是**两套独立前端**，改了一个另一个不会跟着变。这是「我明明改了怎么没生效」的高频来源。

---

## 3. 一次请求走过的五道关

```
浏览器
  → uvicorn(:8420)
  → GZip 中间件
  → CORS 中间件
  → 鉴权中间件 AuthGate        本地请求直接放行；远程要密码
  → 【降级门】                  LLM 没配好 → 这里统一返回 503，**在路由之前**
  → 路由匹配 → handler
  → service → store → SQLite（主库或子库）
```

**⚠️ 降级门的副作用**（排错时必须知道）：
它会**在路由之前**统一返回 503，把 404 完全掩盖。所以：
- 「接口返回 503」≠ 接口不存在，要先看是降级门还是业务返回
- **写测试时禁止用「请求不是 404」来断言端点存在**——这个写法在端点缺失时也会通过（零鉴别力）。正确做法是断言 `(路径, 方法)` 出现在 `app.routes` 里。

---

## 4. 数据落在哪（最该记住的一张表）

主库 `data/openbiliclaw.db`（84 表）= 事件 / 画像 / 归档 / 配置态 / 历史遗留。
其余按**锁域隔离**拆成子库（避免长文本写入阻塞主业务）：

| 库 | 大小 | 装什么 |
|---|---|---|
| `embedding_cache.db` | 1.2 GB | 向量缓存（丢了会全量重算，不会丢数据） |
| `chat_analysis.db` | 924 MB | 聊天记录分析 |
| `content.db` | 724 MB | 阅读库正文（`articles` 9 万条） |
| `pool.db` / `events.db` / `discovery.db` / `article_rag.db` | 108–194 MB | 候选池 / 行为事件 / 发现引擎 / 文章 RAG |
| `interview.db`(31 表) / `diary.db`(31 表) | 25–29 MB | 面试（含复盘）/ 日记 |
| `knowledge.db`(18 表) | 16 MB | 知识图谱 |
| `resume.db` | 0.5 MB | **岗位与投递状态的真值** |
| `health.db`(16 表) / `weekend.db` / `travel.db` / `douban.db` / `oss_research.db` / `interview_questions.db` | < 1 MB | 各自业务 |

**三个已知陷阱**：
1. **主库里还留着 15 张全 0 行的 `health_*` 空壳表**，真实数据在 `health.db`。误用主库连接写 = 双写。
2. 子库与主库**可能有同名表**（health_*、interview_*），看代码时先确认连的是哪个连接。
3. `data/` 整个被 gitignore ⇒ **数据库不在版本控制里**，`git status` 看不见它们的变化。

---

## 5. 六个业务子系统各在哪（对应你要梳理的那些模块）

| 模块 | 代码 | 数据库 | 前端页面 | 备注 |
|---|---|---|---|---|
| 日记 | `diary/`（13.8K 行） | `diary.db`（31 表） | 桌面 + 移动 | 有 `sources/` 导入管线（苹果备忘录/有道/WPS） |
| 面试 | `interview/{job,study,review}/`（5K 行） | `interview.db` + `interview_questions.db` + `resume.db` + `knowledge.db` | 桌面（A/B/C）+ 移动（仅 A） | **三域三库，唯一跨 4 个库的模块** |
| 健康 | `health/`（4K 行） | `health.db`（16 表） | 桌面 `health-app.js` | 表前缀 `health_`；`store.py` 单文件 89KB |
| 旅游 | `travel/`（465 行） | `travel.db`（8 表）+ `data/travel/` 下的 md | 桌面 `travel.js` | md 与 db **不同步** |
| 周末活动 | `weekend/`（1.1K 行） | `weekend.db`（3 表） | 桌面 | 运行时还会**跨库读** `diary.db` / `douban.db` |
| 阅读库 | `reading/` + `saved_sync/` + `notes/` + `conversation_archive/`（3.7K 行） | `content.db` + 主库 3 张表 | 桌面为主 | **一名字三套数据**，见术语表 |

采集线（`sources/` 11K 行 + `runtime/` 20K 行）不在上表——它没有自己的前端，只负责往库里灌数据。

---

## 6. 术语表：七个「容易搞混」的名字

| 你听到的名字 | 到底指什么 | 别和什么混 |
|---|---|---|
| **「阅读库」** | ① `content.db.articles`（9 万条，桌面「阅读库」标签页）② 主库 `read_archive`（108 条，"已读库"）③ 主库 `conversation_archive`（137 条，对话归档） | 三个都有人叫「阅读库」。**默认指 ①**（唯一有量的那个） |
| **`/api/health`** | ① 精确路径 `/api/health` = **系统探针**（服务活着吗） ② 前缀 `/api/health/*` = **医疗档案**（35 条） | 同一个词两个含义，且探针在 `app.py` 里、档案在 `health/` 包里 |
| **「health」** | ① `health/` 包 = 医疗档案 ② `scripts/health/91160_check_slots.py` = 挂号号源监控 | **两者零代码关系**，只是名字都叫 health |
| **「事件」** | ① `events.db`（独立库，行为事件主存） ② 主库里的 `events` 表（历史遗留） | 名字相同，先看代码连的是哪个库 |
| **「记忆」** | ① `memory/` 包 = 事件持久化 ② `diary/memory_system.py` + `advanced_memory.py` = 日记的六层记忆 ③ `obc_soul` 里的 core memory = 画像 | 三套「记忆」互不相干 |
| **「面试」** | A=岗位备战（`interview/job/`） B=题目研习（`study/`） C=复盘（`review/`） | 说到「面试接口」必须问是 A/B/C 哪个域 |
| **「配置」** | `config.toml`（本地，被 gitignore）+ `config.example.toml`（模板）+ `config_backups/`（历史备份） | 改配置先备份到 `config_backups/`，别留下 `config.toml.bak` 在仓库根 |

---

## 7. 改代码前必须知道的 6 条硬契约

1. **路由**：新模块只在 `api/_route_registry.py` 注册；`*_routes.py` 里的 Pydantic 模型必须**运行时导入**（放进 `TYPE_CHECKING` 会让 body 静默降级成 query 参数，且 `/openapi.json` 恒 500）。
2. **删端点**：删任何「看起来重复」的端点前，必须证明它的副本**真的已注册**——`_route_registry.py` 是显式白名单，**不会自动发现**新文件。
3. **路径**：项目根只走 `config._project_root()`（或 `interview/_paths.PROJECT_ROOT`）。**禁止** `Path(__file__).parents[N]`、**禁止** `Path("data/xxx.db")` 这种 CWD 相对写法——它们是当前六模块最普遍的隐患。
4. **测试断言**：禁写「请求非 404」来证明端点存在（降级门会掩盖）。断言路由表或 OpenAPI。
5. **跑测试**：服务在线时**不要**跑大批量测试（每个 `create_app()` 都在等 SQLite 写锁，全量 `tests/api` 20 分钟跑不完，还会把线上 `/api/health` 拖到超时）。只跑目标文件。
6. **改完要不要重启**：改 Python → `pm2 restart openbiliclaw-api`；改前端 JS/HTML/CSS → **不用**重启（静态文件按请求读）。`pm2` 进程严禁手工起/杀。

---

## 8. 复盘：之前重构确实出过 4 次错，而且是**同一个根因**

| # | 谁引入 | 症状 | 多久才发现 | 根因 |
|---|---|---|---|---|
| 1 | `260683c8` 拆 `app.py` → 11 个模块 | `PATCH /api/articles/{id}` body 被降级成 query（恒 422）＋ `/openapi.json` 恒 500 | 6 天（09-08→09-14） | 拆分时把 Pydantic 模型挪进了 `TYPE_CHECKING`，没人跑「能否生成 OpenAPI」 |
| 2 | 同上 | OpenAPI `operationId` 重复（两条 graph 端点撞名） | 被 #1 掩盖，修完 #1 才暴露 | 同名函数 + 路径只差 `/` 与 `-`，规范化后撞名 |
| 3 | `d5f02792` K5「删 95 个重复函数」 | `POST /api/delight/sent` 直接消失（推送冷却永久不刷新） | **5 天**（09-09→09-14），且**无任何报错** | 「我看别处有副本 → 这份是重复的」——但那个副本所在的模块**从未接线**。调用方又是 fire-and-forget + `except: pass` |
| 4 | 更早的拆分遗留 | 裸 `create_app()` 少 15 条 `/api/chat-analysis/*` | 直到 09-15 盘点才被发现 | 注册入口分叉（CLI 独占注册） |

**共同根因一句话**：
> **变更靠「静态读代码 + 假设」，而不是「运行时断言」。**
> 拆分/清理是安全的动作，危险的是**没有在改完后问一句「现在真的还能跑吗？」**。

补充：这几次「搞错」的**都不是业务逻辑写错**，而是**装配与契约**出错——所以它们不报错、不崩、测试全绿，只是「少了几条端点」「参数换了个位置」。这也是为什么你会觉得「框架和逻辑不清晰」：**真正复杂的是装配关系，而不是业务逻辑**。

---

## 9. 现在的防线（已落地）与还缺的

**已落地**（改坏会立刻红灯）：

| 防线 | 位置 | 守住什么 |
|---|---|---|
| F4 组 | `tests/api/test_api_route_regressions.py` | 能否生成 OpenAPI + body 不被降级成 query |
| F5 组 | 同上 | operationId 全局唯一 |
| F6 组 | 同上 | delight 四条端点存在 + 客户端引用的 path 真实存在 |
| F7 组 | 同上 | 每个 `api/*_routes.py` 必须贡献端点（孤儿模块闸） |
| F8 组 | 同上 | 同一 `(path, method)` 不得重复注册 |
| 部署一致性 | `scripts/devops/check_pm2_ecosystem.py` | 声明 21 个进程 ⟺ 实跑 21 个 |

**已经补上的第三个**（2026-09-15）：

| 防线 | 位置 | 守住什么 |
|---|---|---|
| 路径闸（棘轮） | `tests/test_architecture_contracts.py` | 禁止**新增** `Path(__file__).parents[N]` 与 `Path("data/...")`；基线内 34 处放行、只许减少。含 3 组自检（能命中／不误报／基线不指向不存在的文件） |

**还缺的两个**（建议补）：

1. **子库闸**：断言「`health_*` 只出现在 `health.db`、`interview_*` 只出现在 `interview.db`」→ 防止主库空壳表被误写。
2. **说明闸**：任何 `docs/modules/<m>.md` 里写到的脚本/表/端点，必须在测试里存在性校验一次（本次梳理发现 `health.md` 教的 `scripts/import_health_data.py` 根本不存在、`diary.md` 教的 `--debug-dump` 从 CLI 够不到）。→ 让文档不再骗人。

### 路径闸怎么用（很轻）

```bash
.venv/bin/python -m pytest tests/test_architecture_contracts.py -q
```

- **新增**违规 → 红灯，报「新文件 / 既有文件新增」两种情形
- **修好**一处 → 从基线里删掉那一行（基线只应该变短）
- 刻意不做「一次性全改」：34 处分布在 8 个包，逐个改风险高，棘轮允许分批推进

---

## 10. 一句话总结怎么用这份地图

- 想知道**某个功能在哪**：查 §5 表 → 进对应包 → 看 `_route_registry.py` 里它的注册行。
- 想知道**数据在哪**：查 §4 表 → 记住「子库隔离、主库有历史遗留空壳表」。
- 想改东西**怕搞坏**：先看 §7 六条硬契约，改完跑 §9 的回归测试。
- 觉得**名字混乱**：查 §6 术语表。
