# content_library —— 内容库统一脚本目录

本目录是**内容/阅读相关脚本的唯一入口**。所有与「阅读收藏库」「articles 抓取池」「已读库」相关的
运维脚本都在这里，不要散落到 `scripts/` 根目录。

## 数据流（三件套）

```
用户转发链接 / 粘贴摘要
        │
        ▼
notes/阅读收藏库/NN-平台-标题.md          ← 单一数据源（五段：元数据头/我的解读/对话摘录/原文/批注）
notes/阅读收藏库/阅读收藏库.md            ← 索引（8 列，含「类型」列）
        │
        ├─→ sync_library_to_db.py ──▶ data/openbiliclaw.db · conversation_archive   ← 派生镜像
        │                                        │
        │                                        ▼
        │                             /web 前端「对话归档」页（+ /m 移动端 tab）
        │                             支持全文搜索、原始 md 回看、三态状态
        │
        └─→ （看板脚本在 skill 内）
            .workbuddy/skills/reading-plan-board/scripts/build_plan_{data,html}.py
                                        │
                                        ▼
                            notes/阅读收藏库/阅读计划.html   ← 消化进度看板（6 批次 × 三态）
```

**要点**：`笔记 md` 是唯一数据源，`DB` 与 `前端页`、`看板` 都是派生视图。改内容只改 md，然后跑同步。

## 脚本清单

### 收藏库 v2（md → DB）
| 脚本 | 用途 | 触发方式 |
|------|------|----------|
| `sync_library_to_db.py` | 收藏库 md → `conversation_archive`，幂等（匹配键 = `md_file`） | 每次归档后手动；`--dry-run` 预览 |

### articles 抓取池（`data/content.db`）
| 脚本 | 用途 | 触发方式 |
|------|------|----------|
| `collect_topic_to_library.py` | 按关键词采集 B站/知乎内容入库 | 手动 |
| `collect_v2ex_archive.py` | V2EX 热榜归档（cxyfreedom/v2ex-hot-hub） | **pm2**：`openbiliclaw-v2ex-archive` |
| `import_md_to_library.py` | 本地已抓 md 批量导入（免联网重抓） | 手动 |
| `import_xhs_xlsx.py` | 「社媒助手」导出的小红书 xlsx 导入 | 手动 |
| `refill_library_bodies_v2.py` | 空正文统一补抓（zhihu/xhs/yt/bili…），用法 `[limit] [--source=源] [--dry-run]` | 手动 |
| `sync_content_cache_to_library.py` | 推荐池 `content_cache` → `articles` 增量同步 | **自动化任务**（每日 0 点） |
| `zhihu_api_body.py` | 知乎正文抓取 helper（被 refill 调用，非独立 CLI） | 内部调用 |

### 已读库（`notes/已读库` → `read_archive`）
| 脚本 | 用途 | 触发方式 |
|------|------|----------|
| `archive_zhihu_readlib.py` | 知乎回答/专栏归档为已读库四件套 | 手动 |
| `import_readlib_to_db.py` | 已读库文件存档 → `read_archive` 表 | 手动；`tests/test_import_readlib_feed.py` 覆盖 |

### legacy/（历史一次性脚本，仅存档）
`import_conversation_archive.py`（首批 13 条搬迁，已完成）、`import_xhs37_mini_onerec.py`（提炼面试题）。

## ⚠️ 维护约定

1. **路径定位一律用 `__file__`**：项目根 = `Path(__file__).resolve().parents[2]`（本目录深一层；
   `legacy/` 深两层用 `parents[3]`）。**禁止硬编码绝对路径**。
2. **移动本目录下脚本时必须同步修改**：
   - `ecosystem.config.json`（pm2 任务）
   - 自动化任务「内容库同步进阅读库（增量）」的命令路径（**平台侧配置，须在自动化界面改**）
   - 同目录外仍在 `scripts/` 根的内容链路脚本（`refill_article_bodies.py`、`collect_mindback_caches.py` 等）
   - `tests/test_import_readlib_feed.py` 的 `sys.path`
   - `docs/modules/conversation_archive.md` 等文档
   > 历史教训：脚本曾被搬入本目录但未改脚本内路径表达式，导致 `BASE` 指向 `scripts/` 而非项目根，
   > 脚本「静默失效」且触发了潜伏的缩进 bug。
3. 归档操作：新归档条目 → 跑 `sync_library_to_db.py` → 刷新看板（skill 脚本）。
4. `notes/` 整体在 `.gitignore` 中（个人内容），归档产物无需额外忽略规则。
5. **CLI 一律用 `argparse`**：支持 `--help`，未知参数报错退出。禁止手写 `for arg in sys.argv`
   —— 那条路上拼错的参数会被静默忽略、直接开跑（历史事故：给 `refill_library_bodies_v2.py`
   传 `--help` 被当成 limit 默认值，直接开始抓取）。涉及抓取/写库的脚本还要提供 `--dry-run`。

## 常见命令

```bash
# 收藏库 md → DB（幂等）
.venv/bin/python scripts/content_library/sync_library_to_db.py

# 预览将产生的变更
.venv/bin/python scripts/content_library/sync_library_to_db.py --dry-run

# 补抓空正文：先看队列，再实跑
.venv/bin/python scripts/content_library/refill_library_bodies_v2.py 100 --dry-run
.venv/bin/python scripts/content_library/refill_library_bodies_v2.py 100

# 刷新阅读计划看板
PY=.venv/bin/python; SK=.workbuddy/skills/reading-plan-board/scripts
$PY "$SK/build_plan_data.py" && $PY "$SK/build_plan_html.py"
```
