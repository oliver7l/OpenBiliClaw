# 阅读库（四套并存 → 统一到 articles）

> 边界最乱的一块：**四个都有人叫「阅读库」**，跨 5 个包、3 张表、2 个数据库。
> 本文记录 2026-09-15 的取证、拍板结论与迁移路径。

## 1. 现状（2026-09-15 实测）

| 名字 | 表 / 库 | 行数 | 前端入口 | 写入方 |
|---|---|---:|---|---|
| **「阅读库」** | `content.db.articles` | **90,564** | 桌面「阅读库」 | `scripts/content_library/*`（采集/回填/导入） |
| 「已读库」 | `openbiliclaw.db.read_archive` | 108 | 桌面「已读库」 | `scripts/content_library/import_readlib_to_db.py`；另有 `notes/service.py:125` 也扫同一目录写 `notes` |
| 「对话归档」 | `openbiliclaw.db.conversation_archive` | 140 | 两侧都有 | `scripts/content_library/sync_library_to_db.py` + `api/conversation_archive_routes.py` |
| 「笔记」 | `openbiliclaw.db.notes` | **2** | **两侧都没 UI** | `notes/` 包（2,110 行代码 + 1,217 行测试） |

为什么难：没有任何一处文档/代码写明「谁是真值源」，而 `notes.db` / `conversation_archive.db`
/ `reading*.db` 这些**记忆里存在的库其实都不存在**——数据全在 `openbiliclaw.db` + `content.db`，
边界因此更难辨认。

## 2. 拍板结论（2026-09-15，用户）

1. **`content.db.articles` 是唯一真值源**。`read_archive` + `notes` 的内容**迁入**它：
   - `read_archive` → `source_type = 'read-archive'`
   - `notes` → `source_type = 'note'`（`note_type` 追加进 `tags`，不丢信息）
2. **`conversation_archive` 保持独立**：它是「对话归档」，语义与文章不同，不参与合并。
3. **前端只留一个「阅读库」入口 +「已读」筛选**（筛选靠 `source_type`，不靠猜 `status`）。

## 3. 迁移工具

`scripts/content_library/migrate_readlib_to_articles.py`（**默认 dry-run**）

```bash
python scripts/content_library/migrate_readlib_to_articles.py            # 看 diff
python scripts/content_library/migrate_readlib_to_articles.py --json     # 机器可读
cp data/content.db data/backups/content.db.bak                           # 落库前自己备份
python scripts/content_library/migrate_readlib_to_articles.py --apply
```

| 开关 | 作用 |
|---|---|
| `--apply` | 真的写库（不加就是 dry-run） |
| `--kind read-archive\|notes\|all` | 只迁一侧（默认 all） |
| `--fill-blank` | URL 已存在的文章**只补空字段**，不覆盖非空 |
| `--mark-read` | 把已读库行写成 `status='finished'`（**默认不写**：迁移不替用户断言「已读」） |
| `--source-db` / `--target-db` | 换库路径（测试用） |

幂等键就是 `url`（两张源表与 `articles` 都是 `url UNIQUE`，列几乎逐列对应，不必发明新键）。

### 四条安全阀

1. **默认 dry-run**：不写库。
2. **绝不覆盖已有文章**：`url` 命中即跳过（那行可能已做过 AI 摘要 / 正文清洗，覆盖等于回退）；
   `--fill-blank` 也只补空。
3. **不伪造 URL**：`articles.url` 是 `NOT NULL UNIQUE`。`notes.source_url = "original"`
   这类**不是 URI** 的值 → 报 `unmigratable` 并跳过，而不是编一个 `notes://` 假链接
   （假链接会在前端渲染成死链，比不迁更难查）。
   反过来说，`local://readlib/<标题>` 这种旧管线造的占位符**照迁**——它们本来就是稳定 URI，
   且 `articles` 里已有 1,497 条非 http 的 url；只额外提示「点不开」。
4. **批内去重**：实测 `read_archive#167831` 与 `notes#1` 是**同一条**小红书笔记，
   必须只插一次，否则撞 `url UNIQUE`。

### dry-run 实测结果（2026-09-15，未落库）

```
counts: {'create': 66, 'skip_exists': 43, 'unmigratable': 1}   # 108 + 2 = 110 全覆盖
non_clickable_urls: 9        # 9 条 local://readlib/... 占位符
unmigratable: notes#2 「用户负向行为建模 — 面试专题」（source_url = 'original'）
```

即：**真正需要新增的只有 66 条**，42 条早已在 `articles` 里（同一 URL），1 条批内重复。

## 4. 已完成 / 未完成

| 步骤 | 状态 |
|---|---|
| 取证与拍板 | ✅ 2026-09-15 |
| 迁移脚本 + 19 例测试（含 6 组鉴别力校验） | ✅ 2026-09-15 |
| **落库（`--apply`）** | ⏸ **未执行**：需要你确认 diff 后手动跑（写的是 9 万行的 `content.db`） |
| 前端合并成单入口 +「已读」筛选 | ⏳ 未做（要动桌面端阅读库标签页） |
| `/api/read-archive/*` 与 `notes/*` 端点收口 | ⏳ 未做 |
| `notes` 包（2,110 行代码 / 2 行数据）标死或删除 | ⏳ 未做（先冻结写入，等前端切完再删） |
| 删 `read_archive` 表 | ⏳ 不动（冻结即可，删表不可逆） |

## 5. 边界提醒

- **`/api/reading/*` 分裂在两个文件**：`reading_routes.py`（suggestions / daily-brief /
  intent-search / stats / auto-tag / similar）+ `saved_sync_routes.py`（items / count /
  sources / items/{id}/tags / items/{id}/status / search）。收口时两边都要看。
- **「已读库」有两个导入器扫同一目录**（`scripts/content_library/import_readlib_to_db.py` →
  `read_archive`；`notes/service.py:125` → `notes`）——同一份文件被两套 schema 解析，长期必然
  分叉。归一后应只留一条。
- **稍后读/收藏**（`saved_memberships`）与本模块相邻但**不同**：那是「保存待看」，已在
  2026-09-15 切成 `saved_memberships` 正本，原生同步见 `docs/modules/saved_sync.md`。

## 6. 正文缺口与统一回补（refill）

`articles` 表长期有大量「有标题无正文」条目（SCHEMA：`content_text` 为空即「待补」）。补正文统一由
**`refill` 模块**承担（见 `docs/modules/refill.md` 与设计稿 `docs/refill-module-design.md`）：

- **独立子库 `refill.db`**：中央队列 `refill_queue`（`source_type/url/title/state/attempts/...`，
  `url` 唯一天然去重），高频回补状态写独立于 `content.db`，避免锁竞争。
- **去重锚点**：`content_text` 是唯一成功判据——任一通道抓成功写正文后，队项标 `done`、失配下次
  取值窗口；换通道/加配额可 `openbiliclaw refill reset --source` 重置 dropped。
- **通道**：`direct` / `search_click` / `ytdlp` / `getnote` / `bili_cli` / `zhihu_api`；调度由
  PM2 `openbiliclaw-refill`（`refill schedule`）按 `[refill].quota` 配额定补。
- **观测**：`openbiliclaw refill status --fresh` 一处看各平台队列口径 + articles 实时缺口。
