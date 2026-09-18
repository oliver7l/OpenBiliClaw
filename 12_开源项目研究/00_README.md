# 12_开源项目研究

> 2026-09-18 自建，把原本散落三处的开源研究（oss_research）资产收进项目根编号目录
> （接 `10_旅游/`、`11_qq相册导出-中三班-小孩儿分组/` 之后）。
> 对应桌面端「🔬 开源研究」tab（`/web/oss-research`）。

## 是什么

「把发给助手的 GitHub 链接 → 研究 → 结构化入库 → 前端 tab 展示」这个闭环的**数据 + 资产**落地区。
路由代码本身不在这里（必须留在可导入的 `src` 包内），见下表。

## 目录结构

| 目录 / 文件 | 内容 |
|---|---|
| `oss_research.db` | 唯一真值源，表 `oss_projects`，**95 条**记录（其中 27 条带 `report_path`） |
| `oss_research.db.bak_20260918` | 2026-09-18 迁移前的库备份（回滚点，`*.db.bak*` 已忽略） |
| `references/` | 第三方克隆 + 研究报告（**67 项 / 772M**）；研究报告 .md 由 API 只读 serve |
| `scripts/backfill.py` | 种子回填脚本，按 `(owner,name)` 幂等去重写入 `PROJECTS` 列表 |

相关代码（**不在本目录**）：

| 位置 | 职责 |
|---|---|
| `src/openbiliclaw/api/oss_research_routes.py` | 路由实现（`DEFAULT_DB_PATH` / 报告服务白名单） |
| `src/openbiliclaw/api/_route_registry.py` | 路由注册入口 |
| `src/openbiliclaw/web/desktop/assets/js/oss-research-app.js` | 桌面端页面 |
| `docs/modules/oss_research.md` | 模块文档（端点 / 字段 / 设计决策 / 迁移记录） |
| `docs/references-index.md` | `references/` 参考项目索引 |

## ⚠️ 三条铁律

1. **代码与本目录分离**：路由代码必须留在 `src/openbiliclaw/`（Python 包需可导入；`12_开源项目研究` 这种「数字+中文」目录名不能作包导入路径）。移动本目录内资产后，必须同步改 `oss_research_routes.py` 的 `DEFAULT_DB_PATH` 与报告服务白名单 `_ALLOWED_DIRS`。
2. **`report_path` 存「仓库相对路径」且必须带 `12_开源项目研究/` 前缀**（如 `12_开源项目研究/references/xxx.md`），前端据此拼站内链接。改库位置必须批量同步 DB 内 `report_path` 与 `caveats` 里的 `references/…` 引用，并逐条核验磁盘存在性。
3. **入库边界**：`references/`（非锚定忽略规则）与 `*.db`（含 `.bak`）**绝不入库**；`scripts/backfill.py` 是代码，**保持跟踪**——故本目录**不做整目录忽略**（与 07/08/09/11 的纯数据目录不同）。

## 变更前请读

- 模块文档：`docs/modules/oss_research.md`（含 2026-09-18「目录迁移」小节）
- 变更记录：`docs/changelog.md` 顶部「开源研究模块收编至 `12_开源项目研究/`」
- 版权：`references/` 下均为第三方项目，版权归原作者，许可证见各项目内 LICENSE；仅本地离线查阅，**不随仓库分发**。
