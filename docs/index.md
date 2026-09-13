# 📖 OpenBiliClaw 文档导航

> 本页面是项目文档的一站式入口。

## 项目概览

- [项目主页](index.html) — GitHub Pages 首页，桌面安装包 / 一句话安装、插件下载、GitHub 入口和产品卖点概览
- [主页 SEO 维护指南](seo.md) — Search Console / Bing 提交清单、sitemap / OG / JSON-LD 长期维护要点
- [项目规格说明书 (SPEC)](spec.md) — 完整的项目设计与规划
- [隐私权政策](privacy.md) — Chrome Web Store / 插件数据收集披露与本地优先数据流说明
- [Chrome Web Store 商店页文案](chrome-webstore-listing.md) — 可直接复制到商店后台的项目入口、安装使用说明和隐私引导
- [v0.1 开发任务清单](v0.1-todolist.md) — 当前版本的开发主线
- [技术债清单](technical-debt.md) — 已确认技术债、风险解析、建议治理方向和待确认 TODO 线索
- [重构规划（2026-09）](refactor-plan-2026-09.md) — 基于现状基线的分阶段治理：止血 → 一份事实 → 拆巨文件 → 修分层 → 抽取收口 → 技术债
- [架构设计](architecture.md) — 系统架构与模块关系
- [记忆系统设计](memory-design.md) — 多层网状记忆架构详解
- [变更日志](changelog.md) — 各里程碑交付记录
- [GitHub Releases](https://github.com/whiteguo233/OpenBiliClaw/releases/latest) — 普通用户看 Latest Release 的 `openbiliclaw-v*` 聚合页，同时下载浏览器插件 zip、实验性桌面安装包并查看后端源码 tag；维护者通道仍保留 `extension-v*` / `desktop-v*` / `backend-v*`
- [手动端到端联调](manual-e2e.md) — CLI、插件与 SQLite 的真实联调步骤
- [OpenClaw 接入最短指南](openclaw-quickstart.md) — Docker 优先、本地兜底的安装、初始化、skill 发现与 CLI bridge 自检
- [Agent 机器契约 (短)](agent-install.md) — 给 AI 智能体 WebFetch 的短契约,配合 README 的短粘贴语句
- [Agent 部署详细说明](agent-deployment.md) — 给人看的详细版本 + 所有 JSON 事件/错误码/排查表
- [Docker 部署指南](docker-deployment.md) — 手动 Docker / docker compose 部署步骤
- [后端自动更新 SPEC](specs/auto-update.md) — 后端源码自动应用、默认关闭的更新开关、git 安全边界与插件商店原生更新边界

## 可视化架构图

- [Soul 模块架构与流程图](diagrams/soul-architecture.html) — Soul 真实写回口、pipeline 输入边界、完整 rebuild 与局部写回路径
- [Soul 更新变化流程图](diagrams/soul-update-flow.html) — 事件来源矩阵、分层路由、典型场景和专属名词注释
- [Recommendation 模块架构与流程图](diagrams/recommendation-architecture.html) — 候选池 readiness、serve 热路径、PoolCurator、MMR 和反馈回流
- [Web HTML 模块架构与流程图](diagrams/web-architecture.html) — `/web` 桌面端、`/m` 移动端、REST hydration、runtime-stream 和用户动作边界
- [Discovery 模块架构图](diagrams/discovery-architecture.html) — 多源发现、刷新调度、评估优化和模块协议边界

## 模块文档

| 模块 | 文档 | 对应代码 | 状态 |
|------|------|----------|------|
| LLM 多模型支持 | [modules/llm.md](modules/llm.md) | `src/openbiliclaw/llm/` | ✅ v0.3.74 统一结构化 JSON 容错 + Ollama embedding 空凭据静默 |
| B 站接入层 | [modules/bilibili.md](modules/bilibili.md) | `src/openbiliclaw/bilibili/` | ✅ M3 完成 |
| 多源适配层 | [modules/discovery.md](modules/discovery.md#多源适配层) | `src/openbiliclaw/sources/` | ✅ v0.3.0 落地 B 站 / 小红书 / 通用 Web；已接入抖音 DOM-first search / hot / feed discovery 和 YouTube 初始化画像 |
| YouTube 接入 | [modules/youtube.md](modules/youtube.md) | `src/openbiliclaw/youtube/` + `src/openbiliclaw/sources/yt_tasks.py` | ✅ init / fetch smoke / Google Takeout 导入 |
| 记忆系统 | [modules/memory.md](modules/memory.md) | `src/openbiliclaw/memory/` | ✅ 完成 |
| 日记系统 | [modules/diary.md](modules/diary.md) | `src/openbiliclaw/diary/` | ✅ v0.3.173 完整日记记录 + AI 分析 + 多格式导入 + 桌面端页面 |
| 笔记系统 | [modules/notes.md](modules/notes.md) | `src/openbiliclaw/notes/` | ✅ v0.3.201 视频转笔记管线 + FTS 搜索 + 已读库导入 + CLI/API |
| 聊天记录分析系统 | [modules/chat_analysis.md](modules/chat_analysis.md) | `src/openbiliclaw/chat_analysis/` | ✅ 独立数据库，801 会话，360 万消息，832 分析片段，13 个 API 端点 |
| 灵魂引擎 | [modules/soul.md](modules/soul.md) | `src/openbiliclaw/soul/` | ✅ 完成 |
| 内容发现引擎 | [modules/discovery.md](modules/discovery.md) | `src/openbiliclaw/discovery/` | ✅ v0.3.x 多源 + 统一待评估池 + 跨源跨轮 topic 配额 |
| 推荐引擎 | [modules/recommendation.md](modules/recommendation.md) | `src/openbiliclaw/recommendation/` | ✅ v0.3.x 双轴 fatigue + per-group 候选窗口 + reshuffle 0.6s |
| 存储层 | [modules/storage.md](modules/storage.md) | `src/openbiliclaw/storage/` | ✅ SQLite schema + discovery_candidates 待评估池 + pool readiness 计数 |
| Knowledge Forge 知识锻造炉 | [modules/knowledge_forge.md](modules/knowledge_forge.md) | `src/openbiliclaw/knowledge_forge/` | ✅ v0.3.217 六管线 + 质量审计 + 知识图谱 + 自动补充 + 实体网络 774 / 共现 4,712 |
| 灵魂管线架构 | [modules/soul-pipeline-architecture.md](modules/soul-pipeline-architecture.md) | `src/openbiliclaw/soul/` | ✅ 完成 |
| 浏览器插件 | [modules/extension.md](modules/extension.md) | `extension/` | ✅ 支持 B 站 + 小红书 + 抖音 + YouTube / X 任务桥、跨平台行为采集、扩展驱动 E2E 捕捉自检、Cookie 同步、自启动开关和降级配置修复 |
| CLI 命令参考 | [modules/cli.md](modules/cli.md) | `src/openbiliclaw/cli.py` | ✅ 持续更新 (含 `autostart` / `setup-embedding` / `discover-douyin` / `fetch-youtube` / `import-youtube`) |
| 配置参考 | [modules/config.md](modules/config.md) | `config.example.toml` | ✅ 持续更新 (含 `[autostart]`、`/api/config` 回滚与 `reset_fields`) |
| 局域网密码门禁 | [modules/api-auth.md](modules/api-auth.md) | `src/openbiliclaw/auth_core.py` + `src/openbiliclaw/api/auth.py` | ✅ 可选 `[api.auth]` 密码门禁 + `/api/auth/*` + `set-password` |
| 集成适配层 | [modules/integrations.md](modules/integrations.md) | `src/openbiliclaw/integrations/` | ✅ OpenClaw adapter 已接入 |
| 运行时服务 | [modules/runtime.md](modules/runtime.md) | `src/openbiliclaw/runtime/` | ✅ refresh / candidate pipeline / presence gate / autostart / Ollama preflight / degraded boot / runtime-stream / 扩展 E2E 控制事件 / backend tag auto-update |
| 引导初始化 | [modules/init.md](modules/init.md) | `src/openbiliclaw/cli.py`（`run_guided_init`）+ `runtime/init_coordinator.py` + `runtime/init_prereqs.py` | ✅ v0.3.102 共享流水线 + `InitCoordinator` 状态机 + `/api/init*` + 写者门控 + 插件推荐 tab CTA |
| 求职面试备战 | [modules/interview.md](modules/interview.md) | `src/openbiliclaw/interview/` | ✅ v0.3.217 接入外部三层求职知识库：CLI `interview` 命令组 + `/api/interview/*` |
| 对话归档 | [modules/conversation_archive.md](modules/conversation_archive.md) | `src/openbiliclaw/conversation_archive/` + `api/conversation_archive_routes.py` + `scripts/sync_library_to_db.py` | ✅ v2（2026-09-13）：阅读收藏库 md 为**单一数据源**、本表为**派生镜像**；单表 + FTS5 trigram 全文检索；v2 增 `entry_num`/`group_name`/`dialog_excerpt`/`annotations`/`md_file` 列 + `GET /{id}/raw-md`（三件套闭环）；桌面 `/web/conversation-archive`（类型筛选 + 状态互通）与移动端 tab（v0.3.242 编号被阅读库补抓占用，changelog 记为 v0.3.246 补记） |
| 本地媒体浏览 | [modules/media.md](modules/media.md) | `src/openbiliclaw/media/` | ✅ v0.3.222 桌面 `/web/media` 页（视频 + 图片）+ `/api/media/*` |
| ed2k 下载管理 | [modules/ed2k.md](modules/ed2k.md) | `src/openbiliclaw/ed2k/` | ✅ v0.3.223 桌面「⬇ ed2k 下载」tab + `/api/ed2k/*` |
| 健康管理 | [modules/health.md](modules/health.md) | `src/openbiliclaw/health/` + `api/health_routes.py` | ✅ 桌面内嵌 `healthPage` 12 标签页；68 条 `/api/health/*` 路由（单一来源 health_routes.py）；子库 `data/health.db` |
| 周期记录 | [modules/cycle.md](modules/cycle.md) | `src/openbiliclaw/cycle/` | ✅ 独立小模块，`cycle_records` 表存 `data/cycle.db` |
| 豆瓣书影音 | [modules/douban.md](modules/douban.md) | `src/openbiliclaw/douban/` | ✅ v0.3.226 桌面「📚 豆瓣」tab + `/api/douban/*` + 独立库 `data/douban.db` + 可选内容源 |
| 周末怎么玩 | [modules/weekend.md](modules/weekend.md) | `src/openbiliclaw/weekend/` | ✅ v0.3.244 本地优先周末计划生成器：CLI `weekend` + `/api/weekend/*` + 周五主动推送 + `data/weekend.db` |
| 已读库 / 收藏同步 | — | `src/openbiliclaw/saved_sync/` | ✅ 原生保存路由 + 身份契约（`adapters/` 死代码已移除） |

## 开发指南

- [系统开发文档](development.md) — 当前状态手册（as-is baseline）：代码地图与模块状态、开发工作流、测试基线、已知问题登记 K1-K11
- [贡献指南](contributing.md) — 环境搭建、代码规范、文档更新要求
- [AGENTS.md](../AGENTS.md) — AI 代理开发规则（含文档更新强制要求）
