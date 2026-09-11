# 备份清理清单（2026-09-11）

> ✅ **已执行完成**。
> 操作：清理 `data/` 下冗余历史备份。用户决策：**保留最新 1 份完整快照，其余删除**。
> 全部目标位于 `data/`（已整体 gitignore），**零代码引用**（仅 4 个已跑完的一次性迁移脚本注释提及）。
> 执行方式：同盘暂存 → 验证 → 删除（两阶段，可中断）。

## 执行结果

| 指标 | 清理前 | 清理后 | 变化 |
|------|--------|--------|------|
| 项目总大小 | 52G | **29G** | **−23G** |
| `data/` | 34G | **9.9G** | **−24G** |
| 卷可用空间 | 421GiB | **440GiB** | **+19GiB**（同盘另有其它项目波动） |

**执行步骤**（2026-09-11 10:05–10:10）
1. 核实：逐项确认冗余（迁移脚本已跑完 / 表集合被当前库覆盖 / 零引用）。
2. 回滚点：`cp` 保留单体快照到 `data/backups/rollback-20260909/`，`PRAGMA integrity_check` = ok。
3. 阶段一：同盘 `mv` 到 `data/_cleanup_staging_20260911/`（26G，瞬间完成）。
4. 阶段二：验证 —— 活库完好、`create_app()` 正常、`/api` 390 条、`/api/health/stats` 200（17 患者）、`/api/notes` 200、全量 import **417/0 FAIL**、`tests/cycle` 10 passed。
5. 阶段三：`rm -rf` 暂存区 + 清理 2 个 0 字节垃圾库。

**顺带清理**：`data/database.db`（0B）、`data/hiser.db`（0B）、`data/openbiliclaw.db?mode=ro`（0B，文件名误含 query string）。

**备注**：删除后 `df` 曾短暂未反映（APFS 异步回收），约 1 分钟后确认空间已释放。

---

## 保留（回滚点）

| 文件 | 大小 | 说明 |
|------|------|------|
| `data/backups/rollback-20260909/openbiliclaw-20260909-082527.db` (+`-wal`) | 1.6G + 139M | **唯一完整的单体主库快照**（168 表 / events 229761 / recommendations 136017，`PRAGMA integrity_check` = ok）。这是唯一能单独还原拆分前全貌的工件 |

> 说明：按名称时间戳最新的是 `openbiliclaw_pre_p9_cleanup_20260909-220342.db`（58M），但它是 **db sharding 之后的小主库**（78 表，diary_entries 等已迁出），单独无法还原全貌，故不作为回滚点。

## 删除

### A. `data/_archive/`（整目录，20G）

| 项 | 大小 |
|----|------|
| `backups_20260910/` | 17G |
| `_backup_p8_20260910/` | 601M |
| `面试资料总库_移除书籍前_20260910_091332.db` | 459M |
| `面试资料总库_移除方向知识库前_20260910_102517.db` | 289M |
| `面试资料总库_移除方向知识库前_20260910_102405.db` | 289M |
| `面试资料总库_清理其他前_20260910_100840.db` | 289M |
| `面试资料总库_移除幻灯片笔记前_20260910_103911.db` | 247M |
| `面试处理库_清理孤儿前_20260910_000802.db` | 174M |
| `面试处理库_归一化前_20260910_000155.db` | 173M |
| `面试处理库_归一化前_20260909_235952.db` | 173M |
| `interview_合并前_20260910_001949.db` | 20M |
| `面试弹药库_归档_20260910.db` | 9.4M |
| `幻灯片笔记_归档_20260910.db` | 1.7M |
| `_dead_shells_20260909/` | 0B |

### B. `data/` 顶层孤儿备份（3.5G）

| 项 | 大小 |
|----|------|
| `openbiliclaw.db.bak-pre-interview` (+`-wal` 139M / `-shm`) | 1.6G |
| `openbiliclaw.db.bak-pre-events` (+`-wal` 139M / `-shm`) | 1.6G |
| `openbiliclaw.db.bak-pre-pool` (+`-wal` 44M / `-shm`) | 1.1G |

### C. 其他

| 项 | 大小 | 说明 |
|----|------|------|
| `openbiliclaw.db.backup-20260907-083148` | 1.0G | 09-07 冷备 |
| `data/openbiliclaw.db?mode=ro` | 0B | **垃圾文件**（文件名误含 `?mode=ro`，0 字节） |

## 删除前核实结论

1. **A 类全部为已完成的迁移/拆分前快照**（09-02 ~ 09-10）。db sharding P2–P9 与求职知识库各阶段整合均已完成并验证，当前 18 个 KB 库全部健康（书籍库 89 / 看点库 138 / 面试资料总库 299 / 算法面试库 355 …）。
2. **`面试弹药库_归档` / `幻灯片笔记_归档` 的表集合被当前库完全覆盖**（逐表比对通过）。
3. **B 类**为 P2/P3–P6/P7 拆库前的主库快照，对应迁移脚本（`migrate_events_db.py` / `migrate_pool_db.py` / `finalize_db_sharding.py` / `migrate_interview_db.py`）均已跑完。
4. **零代码/CI/测试引用**（全仓 grep 仅命中上述 4 个一次性脚本的注释）。

## 未触碰

- 所有**活库**：`data/openbiliclaw.db`、`pool.db`、`health.db`、`events.db`、`content.db`、`knowledge.db`、`discovery.db`、`llm.db` 等及其 `-wal`/`-shm`
- `data/clone-sites/`（3.0G）、`data/embedding_cache.db`（1.2G）、`data/douyin_profile/`（995M）、`data/image-cache/`（311M）—— 本次不动
- `求职知识库/`、`notes/`、`references/`、`images/`、`docs/`、`src/`
