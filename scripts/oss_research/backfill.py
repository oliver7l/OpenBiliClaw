"""开源研究种子库回填脚本。

把已研究/已克隆的开源项目结构化结论写入 data/oss_research.db（前端「开源研究」tab
的数据源）。单一数据源：所有要入库的项目都登记在 PROJECTS 列表里，重复运行幂等
（按 (owner, name) 去重）。新项目研究完后在此追加条目再重跑即可。

内容分三批：
  1. 2026-09-14 当天深度研究的 6 个（TraeWorkAssistant/wikitok/lushu/brosis/exercise-helper/red）
  2. references/ 目录历史研究存量 13 个（索引 11 + 深度蓝图 2）
  3. references/ 目录仅克隆未精读 18 个（unstudied 占位，研究待补）

注意：caveats 是 TEXT 字段，只能传字符串（分号拼接），传 list 会绑定报错。

用法（在项目根目录执行）：
    .venv/bin/python3 scripts/oss_research/backfill.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# 让脚本能 import 到 src 下的包
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from openbiliclaw.api.oss_research_routes import DEFAULT_DB_PATH, insert_project  # noqa: E402

PROJECTS = [
    {
        "name": "TraeWorkAssistant-mac",
        "owner": "ailogk",
        "url": "https://github.com/ailogk/TraeWorkAssistant-mac",
        "one_liner": "Trae Work 多账号桌面管理工具（Tauri 2 移植版）",
        "purpose": "桌面端（macOS）多账号签到/管理工具，针对字节 AI IDE「Trae Work」。含本地 MITM 代理捕获 JWT、批量签到+积分看板、内嵌 OpenAI 兼容网关、账号池调度、设备标识重置。与官方无关联，作者声明仅供学习研究。",
        "tech_stack": ["Tauri 2", "React 18", "TypeScript", "Vite 5", "Tailwind 3", "Zustand", "Recharts", "Rust", "Python"],
        "structure_notes": "Rust 命令层（main.rs / api_server 含 axum+SSE 网关与账号池状态机）+ React 前端 + Python 代理/签到脚本 + 内嵌 API 网关；docs/ 设计文档极完整（需求/技术框架/API/开发计划/产品设计）。",
        "key_features": ["多账号 JWT 录入/分组/登录态切换", "本地 MITM 代理(127.0.0.1:8899)自动捕获 JWT", "一键批量签到+积分趋势看板", "内嵌 OpenAI 兼容 API 网关(账号池调度)", "6 层设备标识重置", "定时任务/暗色/全本地存储"],
        "relevance_summary": "形态不同（桌面应用非浏览器扩展），但工程手法可迁移：原子写、错误冷却状态机、代理回环防护、退出清理、确定性身份派生、NDJSON 流式 IPC。",
        "reusable_techniques": ["原子写 tmp+rename 防半截文件", "错误分类→冷却状态机+持久化", "NO_PROXY=* 防代理回环", "退出清理子进程/还原系统代理", "确定性身份派生 seeded_hex", "NDJSON 子进程流式 IPC", "严格契约文档(AGENT.md 式)"],
        "caveats": "桌面应用，不能解密 HTTPS；与知乎卡片化扩展架构不同，MITM 那套扩展做不了。",
        "report_path": "references/TraeWorkAssistant-架构分析.md",
        "tags": ["desktop", "tauri", "multi-account", "reference"],
    },
    {
        "name": "wikitok",
        "owner": "IsaacGemal",
        "url": "https://github.com/IsaacGemal/wikitok",
        "one_liner": "TikTok 式全屏竖向卡片流刷 Wikipedia（纯前端无后端）",
        "purpose": "React 18 + TS + Tailwind + Vite 的纯前端应用，浏览器直接 fetch Wikipedia API，整页就是竖向卡片流。正是要给知乎首页做的视觉形态。",
        "tech_stack": ["React 18", "TypeScript", "Tailwind", "Vite"],
        "key_features": ["整屏吸附滚动", "无限滚动(IntersectionObserver)", "双缓冲预加载", "图片淡入+骨架", "沉浸式卡片排版"],
        "relevance_summary": "直接有用——本身就是 TikTok 式全屏竖向卡片流，正是知乎首页卡片化要的视觉形态，且纯前端、feed 机制能搬进 content script。",
        "reusable_techniques": ["snap-y snap-mandatory 吸附滚动", "IntersectionObserver 哨兵预取(rootMargin 100px)", "双缓冲缩略图预加载(先 preload 再渲染)", "骨架+淡入+失败兜底", "100dvh 替代 100vh(移动端)", "scoped class 前缀隔离样式"],
        "caveats": "整页接管 DOM（扩展需注入 position:fixed 覆盖层）；调试用 console.log；点赞侧栏不适用于研究场景。",
        "report_path": "references/WikiTok-借鉴分析.md",
        "tags": ["card-feed", "frontend", "reference", "zhihu-extension"],
    },
    {
        "name": "lushu",
        "owner": "yangyang5214",
        "url": "https://github.com/yangyang5214/lushu",
        "one_liner": "Cloudflare Pages + D1 路书规划/分享应用（自带 Chrome 扩展）",
        "purpose": "基于 Cloudflare Pages + D1 的路书规划/分享应用：搜地点、驾车路线、多天行程整理成可分享路书。自带 Chrome 扩展，从小红书「问点点」抓攻略→DeepSeek 抽地点→回写路书。",
        "tech_stack": ["React", "Vite", "Cloudflare Pages", "D1", "Pages Functions", "Chrome Extension (MV3)", "DeepSeek API"],
        "key_features": ["Chrome 扩展(MV3)", "MAIN world 驱动小红书问点点", "LLM JSON 抽取地点", "客户端/边缘函数共享纯逻辑", "写回乐观并发"],
        "relevance_summary": "最有价值——自带 Chrome 扩展，与 OpenBiliClaw 扩展的 content/background/main 架构同构；LLM 抽取健壮性模板也可直接复用。",
        "reusable_techniques": ["content/background 分工协议(content 同源写回、background 跨站)", "MAIN world React 受控输入驱动(descriptor setter+_valueTracker)", "真点击派 Pointer/Mouse 事件而非 .click()", "流式输出稳定检测(连 4 次相同+长度阈值)", "健壮 LLM JSON 抽取(剥围栏→子串兜底→多形状)", "客户端/边缘共享纯逻辑(orderedIds 只是缓存)"],
        "caveats": "默认高德 POI/OSRM，国内需 WGS84↔GCJ-02 偏移；自托管路书需 Cloudflare 账号。",
        "report_path": "references/Lushu-借鉴分析.md",
        "tags": ["chrome-extension", "llm", "reference", "zhihu-extension", "xhs"],
    },
    {
        "name": "brosis",
        "owner": "AllenBall",
        "url": "https://github.com/AllenBall/brosis",
        "one_liner": "macOS 本地活动记录器（菜单栏 App，数据不离机、MCP 暴露给 AI）",
        "purpose": "macOS 原生菜单栏应用：经 Accessibility API + OCR 在本地加密记录用户活动（身处哪个 App/窗口/屏幕文字），由确定性规则生成时间线/日周台账/活动模式，经 MCP 只读暴露给 AI 助手。四大硬约束：默认不离机、存储前脱敏、存储上限、自包含。理解层零模型调用，向量搜索只是可选本地二级层。",
        "tech_stack": ["Swift", "SwiftPM", "SQLCipher", "SQLite FTS5", "sqlite-vec", "mlx-swift", "macOS AX API", "ScreenCaptureKit", "MCP", "Sparkle"],
        "structure_notes": "两个 SwiftPM 包：app（采集端 T4，AX 探针/适配器规则/局部 OCR/锁定状态机/MCP 集成动作层）+ core（单一存储服务，唯一持钥者，SQLCipher+FTS5+sqlite-vec+确定性规则层+本地 IPC+薄 MCP 服务）。brosis-mcp 经本地 socket 向持钥进程要数据，连库都不开。docs/ 设计计划与决策记录（D16/D22/D25 等）极完整。",
        "key_features": ["本地加密活动记录(SQLCipher)", "AX + OCR 双路采集", "确定性规则层(时间线/台账/活动模式,零模型)", "三通道混合检索(精确字段+FTS+向量,RRF k=60)", "MCP 只读服务(10 工具,grant 细粒度授权)", "入库前规则脱敏(gitleaks+Luhn+验证码)", "按 App 的适配器规则引擎", "本地 IPC 对端校验+限流", "配额上限(30MB/天,默认10GiB)"],
        "relevance_summary": "形态是桌面 App 与扩展不重叠，但「把本地数据变成 AI 可消费上下文」的四套机制正是 OpenBiliClaw 强化 agentic 能力最该抄的：MCP 出口、混合检索、确定性规则层、入库前脱敏——且 OpenBiliClaw 已有 Python API 与 sqlite-vec，迁移成本极低。",
        "reusable_techniques": ["MCP 只读服务+grant 细粒度授权暴露本地数据", "字段路由+FTS5+sqlite-vec+RRF(k=60) 混合检索", "确定性规则层替代 LLM 做理解/聚合", "入库前规则脱敏(gitleaks+Luhn+验证码,33 测试向量)", "严格时间口径(半开区间/桶对齐/区间并集/三类时间)", "规则纯数据+引擎执行+合成树单测(抽取层范式)", "原子写配置(tmp+replaceItemAt+时间戳备份)", "本地 IPC 对端校验(getpeereid+audit token)+限流", "硬性 fail-closed 约束(不离机/必脱敏/固定上限)"],
        "caveats": "macOS 原生 App，AX/OCR/SQLCipher/Metal 构建链平台绑定；Chrome 扩展无法做桌面采集。与知乎卡片化扩展的视觉形态无关。",
        "report_path": "references/Brosis-借鉴分析.md",
        "tags": ["macos", "desktop", "mcp", "local-first", "privacy", "reference", "sqlite-vec", "agentic"],
    },
    {
        "name": "exercise-helper",
        "owner": "touchren",
        "url": "https://github.com/touchren/exercise-helper",
        "one_liner": "「闻鼓而动」：久坐办公族晨练/晚练 PWA，纯 Vanilla JS 零构建零后端，预生成语音引导，闭眼跟练",
        "purpose": "把「坚持锻炼」的反人性设计（看屏幕对节奏、数次数、记顺序）全部卸给语音与状态机：隔天抗阻+每日拉伸自动排休息日，打开→按开始，18 分钟语音带完。数据全 localStorage，无账号无追踪。",
        "tech_stack": ["Vanilla HTML/CSS/JS", "PWA (Service Worker)", "Web Audio API", "localStorage", "预生成 TTS mp3 (Azure 晓晓)"],
        "structure_notes": "morning/ 与 evening/ 两套自包含子应用（各含 app/engine/audio/storage/settings/records/sw）+ 根级选择页 + 多个 SEO 静态页 + tts/ 预生成语音库 + llms.txt；零依赖零构建，AGPL-3.0。",
        "key_features": ["全程语音引导(动作名/次数/要点/节拍计数)", "隔天抗阻自动判定休息日", "训练状态机(事件表+预排程双轨定时)", "三通道语音降级(mp3→AudioContext→原生TTS→静默)", "SW 分层缓存(HTML/CSS/JS 网络优先)", "环境音程序合成+ducking", "微信 WebView 适配(viewport/DPR/缓存踩坑)"],
        "relevance_summary": "与 WikiTok 同属纯前端+本地存储路线，但多了语音引导、离线 SW、引导式会话状态机、微信 WebView 适配四块，对 OpenBiliClaw Web UI（同为本地 serve 的 SPA）与扩展均有直接可抄的健壮性原语。",
        "reusable_techniques": ["事件表+预排程双轨定时：tick 兜底推进，节奏敏感 token(倒计时/蜂鸣)独立 setTimeout 预排，恢复按已过秒数跳过——setInterval catch-up 压缩节奏问题的标准解 (engine.js:181-237)", "语音三通道降级+token 防串音：预生成mp3(exact→前缀兜底)→<audio>→AudioContext解码→原生TTS→静默推进；lastSpeechToken 单调递增，onEnd 只认当前 token；_estimateDurationMs 超时兜底绝不挂起 (audio.js:120-354)", "SW 分层缓存+注释带真机根因：HTML/CSS/JS 网络优先(微信 WebView 懒更新会锁死旧版)、sw.js 自身网络优先、静态缓存优先；版本号激活期清旧缓存 (sw.js:39-98)", "mergeSettings：结构以默认为准、数值以存储为准(新增设置自动有默认值)；读取一律 clone 不外泄内部引用；dayType 归一化兼容旧记录 (storage.js:37-57)", "环境音 Web Audio 程序合成(雨声/心跳/和弦)零文件体积，语音播报 duckDown 0.2x 播完 duckUp (audio.js:443-632)", "llms.txt——给 LLM 看的项目说明书，AGENTS.md 思路的 Web 标准版"],
        "caveats": "morning/evening 两目录同构代码各复制一份——是零构建部署形态的取舍，勿照搬到有模块系统的项目；68 条 TTS_MAP 硬编码靠 tts/gen-*.js 生成脚本保证与播报文本逐字一致，手写必漂移；微信小程序版不开源，PWA 版才是完整实现",
        "report_path": "references/ExerciseHelper-借鉴分析.md",
        "tags": ["pwa", "vanilla-js", "voice-guidance", "web-ui", "local-first", "reference"],
    },
    {
        "name": "red",
        "owner": "exoticknight",
        "url": "https://github.com/exoticknight/red",
        "one_liner": "RED：给 AI 辅助工程的项目知识建立状态机——Research(未决)/Evolve(推进中)/Document(已接受) 三态 + 授权检查点",
        "purpose": "解决 AI 协作四大痛点：记不住项目根基、混淆讨论与决定、难以检查 AI 据以行动的理解、对话无法收口。根因是给了 AI 内容却没给内容的「知识状态」。RED 把 先查一下/按方向试/这个定了 三种行动级别显式写进项目结构。",
        "tech_stack": ["Markdown + TOML front matter", "JSON Schema (config/artifact/output)", "Node CLI (733行, npm)", "Python CLI (810行, PyPI)", "Codex Skill", "GitHub Actions CI"],
        "structure_notes": "方法论长文(194行中文+英文+公众号版+SVG状态图) + spec/协议规范与三套 JSON Schema + cli/ 双实现共享 conformance fixtures + plugins/red Skill(路由化 references) + 自举(本仓库用 RED 维护, CI 跑 red check --json)。",
        "key_features": ["R/E/D 三态知识分类(状态≠可信度,两独立维度)", "R→E→D 双检查点需显式人类授权(宽泛早期请求不构成授权)", "D 不要求新目录=现有文档由 red.toml document.paths 声明", "文档与实现证据冲突→显式记入 Research 待裁决", "CLI 确定性操作(JSON输出+固定退出码), 只记录权威不创造权威", "双实现+共享 conformance cases.json(args+exitCode 序列)", "Skill 路由化按需加载(references 是查找目标非递归阅读清单)", "受管理指令区块(install 只管理自己的块保留其余)"],
        "relevance_summary": "与用户既有工作流同构：docs/plans/≈E、docs/modules+AGENTS.md≈D、借鉴分析≈R、MEMORY.md≈D、工作日志≈R(append-only)。项目已自发实践 RED,缺的只是 E 态显式承载与「冲突必须记录」纪律。与 brosis 互补：brosis 管 AI 看得到什么数据,RED 管 AI 如何理解数据的状态。",
        "reusable_techniques": ["知识状态显式化：把 R/E/D 三态词汇写进 AGENTS.md,让 Agent 自动分清待查/推进中/已定 (spec/protocol.md)", "检查点授权语义原句：得到可行方案≠获得实施授权；宽泛早期请求不构成后面的转换授权——正是用户「先方案确认再开发」的协议化 (protocol.md Transitions)", "冲突显式化：发现文档与实现不符必须记录(如 changelog/drift_reports)而非默默绕过——补 AGENTS.md 文档强制同步规则的最大漏洞", "Skill 路由化按需加载：主文件极短+分路由 references,操作需要时才读 (SKILL.md:20-34)——写任何 SKILL 的范本", "双实现一致性：cases.json 就是 args+exitCode 序列,Node/Python 跑同一组用例 (cli/conformance/)", "机器可读契约三件套：config/artifact/output 各配 JSON Schema+独立 exit-codes 文档 (spec/)", "TOML front matter Markdown 工件(+++ 分隔,id/state/status/title/created 必填)：人可读+机器可校验", "policy 开关(document_requires_approval 等)：协议留开关项目自选严格度 (red.toml:12-14)"],
        "caveats": "对单人+单 Agent 项目,完整 CLI+red.toml 偏重——取其神(三态区分+检查点纪律+冲突显式化)舍其形(双CLI/conformance/自举CI)；方法论长文若要挂载给 Agent 读,摘核心十几行即可,不必整篇；Research/Evolve 记录是否入 Git 由项目自定,协议不强制",
        "report_path": "references/RED-借鉴分析.md",
        "tags": ["methodology", "ai-collaboration", "docs-as-state", "cli", "reference", "workflow"],
    },
    # ── references/ 目录历史研究存量统一归档（2026-09-14）──────────────
    # 13 个已有正式研究（11 个见 references/参考项目索引_求职知识库优化.md，
    # 2 个见 references/两项目深度研究与Agent改造蓝图.md），18 个仅克隆未精读。
    {
        "name": "obsidian-curator",
        "owner": "onlelonely",
        "url": "https://github.com/onlelonely/obsidian-curator",
        "one_liner": "本地 Ollama 打 frontmatter 标签 + 语义聚类合并碎片标签",
        "purpose": "Obsidian 笔记自动打标：7 步管线——标准化→通用词过滤→别名→精确→模糊→语义→新标签。",
        "relevance_summary": "已借鉴：求职知识库 kb.py 的 TAG_ALIAS + BLOCKED_GENERIC + _semantic_direction_fallback（bge-m3 语义兜底）。",
        "caveats": "研究程度：已精读并落地。源码在 references/obsidian-curator。",
        "report_path": "references/参考项目索引_求职知识库优化.md",
        "tags": ["references-archive", "job-kg", "tagging", "obsidian", "llm", "reference"],
    },
    {
        "name": "obsidian-cli",
        "owner": "davidpp",
        "url": "https://github.com/davidpp/obsidian-cli",
        "one_liner": "Obsidian 命令行：增量/混合检索 + 带行定位的 PATCH 编辑 + frontmatter 组装",
        "purpose": "Obsidian vault 的 CLI 工具集，hybrid 检索与外科手术式笔记编辑。",
        "relevance_summary": "部分借鉴思路（hybrid 检索）；未整体引入。",
        "caveats": "研究程度：索引级（借鉴点+落地状态已登记），未单独写深度报告。源码在 references/obsidian-cli。",
        "report_path": "references/参考项目索引_求职知识库优化.md",
        "tags": ["references-archive", "job-kg", "obsidian", "cli", "reference"],
    },
    {
        "name": "obsidian-vault-mcp",
        "owner": "jpoindexter",
        "url": "https://github.com/jpoindexter/obsidian-vault-mcp",
        "one_liner": "把 Obsidian vault 暴露成 MCP：vault_find_related 本地 embedding 找相关页",
        "purpose": "MCP 服务暴露 vault，概念相关检索给 AI 用。",
        "relevance_summary": "未用；候选：把 vault_find_related 思路接到 MCP 只读服务方案（docs/plans/mcp-readonly-server.md）。",
        "caveats": "研究程度：索引级。源码在 references/obsidian-vault-mcp。",
        "report_path": "references/参考项目索引_求职知识库优化.md",
        "tags": ["references-archive", "job-kg", "mcp", "obsidian", "reference"],
    },
    {
        "name": "obsidian-semantic-mcp",
        "owner": "artificemachine",
        "url": "https://github.com/artificemachine/obsidian-semantic-mcp",
        "one_liner": "语义检索 MCP，SHA-256 哈希跳过未变更文件只 embed 新增（增量索引）",
        "purpose": "语义检索 MCP，增量索引降成本。",
        "relevance_summary": "思路已参考（增量索引）；未引入。",
        "caveats": "研究程度：索引级。源码在 references/obsidian-semantic-mcp。",
        "report_path": "references/参考项目索引_求职知识库优化.md",
        "tags": ["references-archive", "job-kg", "mcp", "embedding", "reference"],
    },
    {
        "name": "obsidian-auto-tagger",
        "owner": "undergroundpost",
        "url": "https://github.com/undergroundpost/obsidian-auto-tagger",
        "one_liner": "AI 打标时尊重已有标签词表，复用旧标签防近似重复",
        "purpose": "Obsidian 自动打标，防止标签碎片化。",
        "relevance_summary": "思路已被 kb.py 的 TAG_ALIAS 收敛方案替代。",
        "caveats": "研究程度：索引级。源码在 references/obsidian-auto-tagger。",
        "report_path": "references/参考项目索引_求职知识库优化.md",
        "tags": ["references-archive", "job-kg", "tagging", "obsidian", "reference"],
    },
    {
        "name": "obsidian-ai-tagger",
        "owner": "jaspermayone",
        "url": "https://github.com/jaspermayone/obsidian-ai-tagger",
        "one_liner": "Obsidian AI 打标插件（可自定义端点/本地模型，Apache-2.0）",
        "purpose": "打标插件，LLM 端点可插拔。",
        "relevance_summary": "未用。",
        "caveats": "研究程度：索引级。源码在 references/obsidian-ai-tagger。",
        "report_path": "references/参考项目索引_求职知识库优化.md",
        "tags": ["references-archive", "job-kg", "tagging", "obsidian", "reference"],
    },
    {
        "name": "wiki-rag",
        "owner": "Puppp-hh",
        "url": "https://github.com/Puppp-hh/wiki-rag",
        "one_liner": "把 md wiki 做成类似问 ChatGPT 的纯本地 RAG 问答",
        "purpose": "纯本地 markdown wiki RAG 问答。",
        "relevance_summary": "未用；候选：面试前对求职知识库「问一句即出答案」。",
        "caveats": "研究程度：索引级。源码在 references/wiki-rag。",
        "report_path": "references/参考项目索引_求职知识库优化.md",
        "tags": ["references-archive", "job-kg", "rag", "local-first", "reference"],
    },
    {
        "name": "markdown-rag-knowledgebase",
        "owner": "tayyub-ai",
        "url": "https://github.com/tayyub-ai/markdown-rag-knowledgebase",
        "one_liner": "markdown RAG 知识库问答",
        "purpose": "markdown RAG 知识库组装。",
        "relevance_summary": "未用。",
        "caveats": "研究程度：索引级。源码在 references/markdown-rag-knowledgebase。",
        "report_path": "references/参考项目索引_求职知识库优化.md",
        "tags": ["references-archive", "job-kg", "rag", "reference"],
    },
    {
        "name": "wiki-mcp",
        "owner": "patchmyday",
        "url": "https://github.com/patchmyday/wiki-mcp",
        "one_liner": "schema 强制的 wiki 搜索/写入 MCP，防 tag drift、孤儿笔记",
        "purpose": "用 schema 约束 MCP 写入：标签 schema、frontmatter 模板约束。",
        "relevance_summary": "契合打标收敛思路；未引入。MCP 只读服务方案可参考其 schema 约束设计。",
        "caveats": "研究程度：索引级。源码在 references/wiki-mcp。",
        "report_path": "references/参考项目索引_求职知识库优化.md",
        "tags": ["references-archive", "job-kg", "mcp", "schema", "reference"],
    },
    {
        "name": "interview-coach-skill",
        "owner": "wksudud",
        "url": "https://github.com/wksudud/interview-coach-skill",
        "one_liner": "AI 求职全流程 Skill：简历准备→岗位匹配→模拟面试→申请跟踪",
        "purpose": "求职全流程 Agent Skill，命令设计可参考。",
        "relevance_summary": "已参考命令设计思路。",
        "caveats": "研究程度：索引级。源码在 references/interview-coach-skill。",
        "report_path": "references/参考项目索引_求职知识库优化.md",
        "tags": ["references-archive", "job-kg", "skill", "interview", "reference"],
    },
    {
        "name": "khoj",
        "owner": "khoj-ai",
        "url": "https://github.com/khoj-ai/khoj",
        "one_liner": "完整自托管 AI 第二大脑（聚合文档 + 自然语言查询 + Web/CLI）",
        "purpose": "自托管第二大脑统一入口。",
        "relevance_summary": "未用；体量大（≈164MB）按需参考。",
        "caveats": "研究程度：索引级；体量大只做了概览。源码在 references/khoj。",
        "report_path": "references/参考项目索引_求职知识库优化.md",
        "tags": ["references-archive", "job-kg", "second-brain", "self-hosted", "reference"],
    },
    {
        "name": "my-interview",
        "owner": "H-Wren",
        "url": "https://github.com/H-Wren/my-interview",
        "one_liner": "面试备战 Agent：8-Phase 流水线（调研→匹配→出题→模拟→评估）+ P0-P4 出题法 + STAR 评估",
        "purpose": "把一个人/一份简历武装到能面任何公司的方法论骨架：Intake→Candidate KB→公司调研(5 维度+置信度)→Resume×JD Match→P0-P4 预测出题→Draft Answers→Mock→STAR 评估。单文件 Skill + 3 references。",
        "relevance_summary": "深度研究（307 行蓝图）。可直接映射求职知识库/面试 Agent 设计：出题方法论、Storybank 弹药库、去 AI 味 Greet 话术、真实材料原则（缺口诚实标注）。",
        "reusable_techniques": ["8-Phase 备战流水线", "P0-P4 五档出题优先级", "信息置信度标注 HIGH/MEDIUM/LOW/GAP", "Resume 是事实基准原则", "文件优先输出+版本管理(保留10版)", "公司风格适配 culture-tags", "去 AI 味话术 9 项检查清单"],
        "caveats": "研究程度：深度（蓝图含改造方案 §五）。源码在 references/my-interview。",
        "report_path": "references/两项目深度研究与Agent改造蓝图.md",
        "tags": ["references-archive", "job-kg", "interview", "agent", "skill", "reference"],
    },
    {
        "name": "agent-interview-hub",
        "owner": "Zchary1106",
        "url": "https://github.com/Zchary1106/agent-interview-hub",
        "one_liner": "面经知识库 + Interview Collector 采集 Agent（搜集→去重→评分→入库）",
        "purpose": "静态站仓库 + collect_interviews.py 采集流水线 + 跨平台 Agent 规范：Candidate 结构化 schema、自动打标签/评分/去重、公司三合一知识库组织。",
        "relevance_summary": "深度研究（307 行蓝图）。与 my-interview 互补：管「素材从哪来、怎么存」。采集流水线与去重评分可直接改造成面经采集器。",
        "reusable_techniques": ["Candidate 结构化 schema", "自动打标+评分+去重流水线", "公司三合一知识库组织", "collect_interviews.py 工程实现"],
        "caveats": "研究程度：深度（蓝图含改造方案 §五）。源码在 references/agent-interview-hub。",
        "report_path": "references/两项目深度研究与Agent改造蓝图.md",
        "tags": ["references-archive", "job-kg", "interview", "crawler", "agent", "reference"],
    },
    # ── 18 个仅克隆未精读（登记占位，研究待用户点名后补做）──────────────
    {
        "name": "agent-memory-architecture",
        "owner": "ThatsR4d",
        "url": "https://github.com/ThatsR4d/agent-memory-architecture",
        "one_liner": "LLM Agent 记忆架构：平面文件层级 + 混合检索 + 自动提取/整合",
        "caveats": "仅克隆未精读；README 显示为 Agent 记忆架构方案（与 LycheeMem/deep-memory/memos 同类可对照研究）。",
        "tags": ["references-archive", "unstudied", "agent-memory", "rag", "reference"],
    },
    {
        "name": "deep-memory",
        "owner": "kevintsai1202",
        "url": "https://github.com/kevintsai1202/deep-memory",
        "one_liner": "自进化知识积累与混合检索系统（Self-Evolving Knowledge Accumulation & Hybrid Retrieval）",
        "caveats": "仅克隆未精读；与 agent-memory-architecture/LycheeMem 同类，适合一起对比。",
        "tags": ["references-archive", "unstudied", "agent-memory", "rag", "reference"],
    },
    {
        "name": "LycheeMem",
        "owner": "LycheeMem",
        "url": "https://github.com/LycheeMem/LycheeMem",
        "one_liner": "LLM 记忆系统（litellm 格式 embedder：provider/model 配置）",
        "caveats": "仅克隆未精读；README 显示支持 openai 系 embedding 配置。",
        "tags": ["references-archive", "unstudied", "agent-memory", "embedding", "reference"],
    },
    {
        "name": "memos",
        "owner": "usememos",
        "url": "https://github.com/usememos/memos",
        "one_liner": "轻量自托管闪念笔记（Fast enough for every thought. Private enough for all of them.）",
        "caveats": "仅克隆未精读；知名开源项目（SQLite 单文件存储），与 OpenBiliClaw 内容库形态相近。",
        "tags": ["references-archive", "unstudied", "notes", "self-hosted", "reference"],
    },
    {
        "name": "anything-llm",
        "owner": "Mintplex-Labs",
        "url": "https://github.com/Mintplex-Labs/anything-llm",
        "one_liner": "与文档对话 + AI Agent 工作流的全栈 LLM 应用（多用户、可自托管）",
        "caveats": "仅克隆未精读；体量大，只做概览参考。",
        "tags": ["references-archive", "unstudied", "llm", "rag", "self-hosted", "reference"],
    },
    {
        "name": "reor",
        "owner": "reorproject",
        "url": "https://github.com/reorproject/reor",
        "one_liner": "本地 AI 笔记应用（自动关联 + 语义检索，Electron + 本地 LLM）",
        "caveats": "仅克隆未精读。",
        "tags": ["references-archive", "unstudied", "notes", "local-llm", "electron", "reference"],
    },
    {
        "name": "MyOwnDiaryRag",
        "owner": "hjyssg",
        "url": "https://github.com/hjyssg/MyOwnDiaryRag",
        "one_liner": "纯本地日记管理与检索系统：导入、全文搜索、AI 摘要、统计分析",
        "caveats": "仅克隆未精读；与 diary.db/nightDiary/sleep-memory 同类可对照。",
        "tags": ["references-archive", "unstudied", "diary", "rag", "local-first", "reference"],
    },
    {
        "name": "nightDiary",
        "owner": "yemmmmi",
        "url": "https://github.com/yemmmmi/nightDiary",
        "one_liner": "夜间日记应用",
        "caveats": "仅克隆未精读；README 无自述，需拆源码补全简介。",
        "tags": ["references-archive", "unstudied", "diary", "reference"],
    },
    {
        "name": "sleep-memory",
        "owner": "Angela-letter",
        "url": "https://github.com/Angela-letter/sleep-memory",
        "one_liner": "Sleep Memory（睡眠记忆相关项目）",
        "caveats": "仅克隆未精读；README 采用 One-liner 结构，内容待补。",
        "tags": ["references-archive", "unstudied", "diary", "reference"],
    },
    {
        "name": "bili-video2book",
        "owner": "LINJIANG12",
        "url": "https://github.com/LINJIANG12/bili-video2book",
        "one_liner": "一键把 B 站课程视频转成可精读的教材/学习笔记",
        "caveats": "仅克隆未精读；与 B 站接入链路（openbiliclaw.bilibili）相关。",
        "tags": ["references-archive", "unstudied", "bilibili", "notes", "llm", "reference"],
    },
    {
        "name": "ctrip-ticket-crawler",
        "owner": "Yybrook",
        "url": "https://github.com/Yybrook/ctrip-ticket-crawler",
        "one_liner": "携程机票列表页价格爬取实验：Chromium 真实会话 + 直读 batchSearch 响应",
        "caveats": "仅克隆未精读；browser_automation 方案（真实会话+读响应而非解析 DOM）对反爬抓取有参考价值。",
        "tags": ["references-archive", "unstudied", "crawler", "browser-automation", "travel", "reference"],
    },
    {
        "name": "travel-price-advisor",
        "owner": "nzy-user",
        "url": "https://github.com/nzy-user/travel-price-advisor",
        "one_liner": "行程比价助手：输入日期/出发地/目的地/时段，同时比价携程/飞猪/同程机票火车票",
        "caveats": "仅克隆未精读；与新疆旅行计划场景直接相关。",
        "tags": ["references-archive", "unstudied", "travel", "crawler", "reference"],
    },
    {
        "name": "wandao",
        "owner": "tllovesxs",
        "url": "https://github.com/tllovesxs/wandao",
        "one_liner": "万能导：多平台知识库 Markdown 导入导出（飞书/语雀/Notion/Obsidian/印象笔记等 15+ 平台）",
        "caveats": "仅克隆未精读；与 youdaonote-pull 同类，与阅读收藏库迁移场景相关。",
        "tags": ["references-archive", "unstudied", "markdown", "migration", "reference"],
    },
    {
        "name": "youdaonote-pull",
        "owner": "2dot4",
        "url": "https://github.com/2dot4/youdaonote-pull",
        "one_liner": "有道云笔记批量导出到本地 Markdown",
        "caveats": "仅克隆未精读。",
        "tags": ["references-archive", "unstudied", "markdown", "migration", "reference"],
    },
    {
        "name": "EHR-django",
        "owner": "MohsinRazaKhanSipra",
        "url": "https://github.com/MohsinRazaKhanSipra/EHR-django",
        "one_liner": "Django 电子病历（EHR）系统",
        "caveats": "仅克隆未精读；与 HealthCare-Management-System/MedSync-AI 同属医疗类，成组研究价值更高。",
        "tags": ["references-archive", "unstudied", "django", "healthcare", "reference"],
    },
    {
        "name": "HealthCare-Management-System",
        "owner": "MrAnayDongre",
        "url": "https://github.com/MrAnayDongre/HealthCare-Management-System",
        "one_liner": "Healthcare Information Management System (HIMS)，DBMS 课程项目",
        "caveats": "仅克隆未精读；医疗三件套之一。",
        "tags": ["references-archive", "unstudied", "dbms", "healthcare", "reference"],
    },
    {
        "name": "MedSync-AI",
        "owner": "tirth-patel06",
        "url": "https://github.com/tirth-patel06/MedSync-AI",
        "one_liner": "用药依从性助手：React 前端 + Express 后端",
        "caveats": "仅克隆未精读；医疗三件套之一。",
        "tags": ["references-archive", "unstudied", "react", "express", "healthcare", "reference"],
    },
    # ── 002-探索项目/ 各主题探索批次第三方仓库统一归档（2026-09-14 第二轮盘点）──
    # 45 个，tag=explore-archive + unstudied（研究待补），caveats 记录克隆位置。
    # 用户自有仓库（oliver7l/tanxue0118）与非项目目录（自研 App/静态站等）不在其列。
    # 小宇宙播客（000-小宇宙）
    {
        "name": "xiaoyuzhou_script_skill", "owner": "zdhgreat",
        "url": "https://github.com/zdhgreat/xiaoyuzhou_script_skill",
        "one_liner": "小宇宙播客 Claude Code Skill：官方 API 搜索/浏览/下载播客，支持批量爬取与音频转录",
        "caveats": "仅克隆未精读。克隆位置：002-探索项目/000-小宇宙/015-xiaoyuzhou_script_skill。",
        "tags": ["explore-archive", "unstudied", "xiaoyuzhou", "podcast", "crawler", "skill", "reference"],
    },
    {
        "name": "xiaoyuzhou-api", "owner": "ylw1997",
        "url": "https://github.com/ylw1997/xiaoyuzhou-api",
        "one_liner": "小宇宙 API 文档和测试仓库",
        "caveats": "仅克隆未精读。克隆位置：002-探索项目/000-小宇宙/016-xiaoyuzhou-api。",
        "tags": ["explore-archive", "unstudied", "xiaoyuzhou", "podcast", "api", "reference"],
    },
    {
        "name": "xiaoyuzhou-mcp", "owner": "r266-tech",
        "url": "https://github.com/r266-tech/xiaoyuzhou-mcp",
        "one_liner": "小宇宙 FM 只读 MCP server：列订阅/浏览单集/取官方转写 URL/搜索播客",
        "caveats": "仅克隆未精读。只读 MCP 形态与 OpenBiliClaw MCP 只读服务方案（docs/plans/mcp-readonly-server.md）同类。克隆位置：002-探索项目/000-小宇宙/017-xiaoyuzhou-mcp。",
        "tags": ["explore-archive", "unstudied", "xiaoyuzhou", "podcast", "mcp", "reference"],
    },
    # 微信读书（000-微信读书）
    {
        "name": "awesome-weread", "owner": "BENZEMA216",
        "url": "https://github.com/BENZEMA216/awesome-weread",
        "one_liner": "微信读书官方 Agent Skill 与社区生态项目精选清单（awesome list）",
        "caveats": "仅克隆未精读。克隆位置：002-探索项目/000-微信读书/002-awesome-weread。",
        "tags": ["explore-archive", "unstudied", "weread", "awesome", "reference"],
    },
    {
        "name": "carl-weread", "owner": "LearnPrompt",
        "url": "https://github.com/LearnPrompt/carl-weread",
        "one_liner": "把微信读书从「读了多少」改成「今天哪个问题可以被读懂一点」（AI 阅读辅助）",
        "caveats": "仅克隆未精读。克隆位置：002-探索项目/000-微信读书/004-carl-weread。",
        "tags": ["explore-archive", "unstudied", "weread", "ai-reading", "reference"],
    },
    {
        "name": "WeRead-Agent", "owner": "WenWen610",
        "url": "https://github.com/WenWen610/WeRead-Agent",
        "one_liner": "微信读书 AI 阅读助手：自然语言检索笔记、主题分析、归纳笔记、长期阅读记忆，接 Web/微信/QQ 多端",
        "caveats": "仅克隆未精读。克隆位置：002-探索项目/000-微信读书/009-WeRead-Agent。",
        "tags": ["explore-archive", "unstudied", "weread", "agent", "reference"],
    },
    {
        "name": "weread-skill-api", "owner": "lucis-yg",
        "url": "https://github.com/lucis-yg/weread-skill-api",
        "one_liner": "基于微信读书 weread-skills 的 API 网关服务（Node.js + Express）",
        "caveats": "仅克隆未精读。克隆位置：002-探索项目/000-微信读书/010-weread-skill-api。",
        "tags": ["explore-archive", "unstudied", "weread", "api", "reference"],
    },
    {
        "name": "weread-skill-desktop", "owner": "Duosl",
        "url": "https://github.com/Duosl/weread-skill-desktop",
        "one_liner": "安静的桌面工具：导出微信读书划线/笔记/阅读统计，数据可备份可迁移",
        "caveats": "仅克隆未精读。克隆位置：002-探索项目/000-微信读书/011-weread-skill-desktop。",
        "tags": ["explore-archive", "unstudied", "weread", "desktop", "export", "reference"],
    },
    # Telegram（016/035/036）
    {
        "name": "telegram-drive-bot", "owner": "Merack",
        "url": "https://github.com/Merack/telegram-drive-bot",
        "one_liner": "Telegram Drive Bot：把 Telegram 当网盘用的机器人（源码研究，含 darwin-arm64 release）",
        "caveats": "仅克隆未精读。克隆位置：002-探索项目/016-telegram-drive-bot/telegram-drive-bot-source。",
        "tags": ["explore-archive", "unstudied", "telegram", "bot", "reference"],
    },
    {
        "name": "tg-reader", "owner": "xin0907",
        "url": "https://github.com/xin0907/tg-reader",
        "one_liner": "TG Reader：Telegram 频道/群组阅读器",
        "caveats": "仅克隆未精读。克隆位置：002-探索项目/035-tg-reader/tg-reader。",
        "tags": ["explore-archive", "unstudied", "telegram", "reader", "reference"],
    },
    {
        "name": "tgtldr", "owner": "fr0der1c",
        "url": "https://github.com/fr0der1c/tgtldr",
        "one_liner": "TGTLDR：Telegram 内容摘要/速读工具（中英双语 README）",
        "caveats": "仅克隆未精读。克隆位置：002-探索项目/036-tgtldr/tgtldr。",
        "tags": ["explore-archive", "unstudied", "telegram", "summarizer", "reference"],
    },
    # 123 云盘（021）
    {
        "name": "123pan", "owner": "Qxyz17",
        "url": "https://github.com/Qxyz17/123pan",
        "one_liner": "123 云盘纯前端直接 API 访问工具：Web 界面浏览/播放文件，无需本地服务器",
        "caveats": "仅克隆未精读（两份副本 0001/0002，0002 标注 web 版成功）。克隆位置：002-探索项目/021-123云盘/。",
        "tags": ["explore-archive", "unstudied", "123pan", "netdisk", "reference"],
    },
    {
        "name": "123PanOpen", "owner": "xxxxxfpx",
        "url": "https://github.com/xxxxxfpx/123PanOpen",
        "one_liner": "x123pan：123 云盘官方 Open API 的社区 Python SDK（比官方更 Pythonic）",
        "caveats": "仅克隆未精读。克隆位置：002-探索项目/021-123云盘/0003-x123pan-python-sdk。",
        "tags": ["explore-archive", "unstudied", "123pan", "sdk", "python", "reference"],
    },
    {
        "name": "pan123-rs", "owner": "buladuo",
        "url": "https://github.com/buladuo/pan123-rs",
        "one_liner": "Rust 实现的 123 盘命令行工具：上传/下载/管理",
        "caveats": "仅克隆未精读。克隆位置：002-探索项目/021-123云盘/0004-pan123-rs。",
        "tags": ["explore-archive", "unstudied", "123pan", "rust", "cli", "reference"],
    },
    {
        "name": "123pan-cli", "owner": "123panNextGen",
        "url": "https://github.com/123panNextGen/123pan-cli",
        "one_liner": "Go 版 123 云盘管理工具：模拟安卓客户端协议绕过流量限制，CLI + TUI 双模式",
        "caveats": "仅克隆未精读。克隆位置：002-探索项目/021-123云盘/0005-123pan-cli。",
        "tags": ["explore-archive", "unstudied", "123pan", "go", "cli", "reference"],
    },
    {
        "name": "pan123next", "owner": "123panNextGen",
        "url": "https://github.com/123panNextGen/pan123next",
        "one_liner": "Dart/Flutter 第三方 123 云盘桌面客户端，Fluent UI，多平台（Win/macOS/Linux/Android）",
        "caveats": "仅克隆未精读。克隆位置：002-探索项目/021-123云盘/0006-pan123-next。",
        "tags": ["explore-archive", "unstudied", "123pan", "flutter", "desktop", "reference"],
    },
    {
        "name": "py-123pan-client", "owner": "sqkkyzx",
        "url": "https://github.com/sqkkyzx/py-123pan-client",
        "one_liner": "123 云盘开放平台非官方 Python SDK",
        "caveats": "仅克隆未精读。克隆位置：002-探索项目/021-123云盘/0007-py-123pan-client。",
        "tags": ["explore-archive", "unstudied", "123pan", "sdk", "python", "reference"],
    },
    # 115 网盘（029）
    {
        "name": "115-cloud", "owner": "lyang0932",
        "url": "https://github.com/lyang0932/115-cloud",
        "one_liner": "115 网盘开源 API 客户端",
        "caveats": "仅克隆未精读。克隆位置：002-探索项目/029-115项目/115-cloud。",
        "tags": ["explore-archive", "unstudied", "115", "netdisk", "reference"],
    },
    {
        "name": "115-media-hub", "owner": "zym20192019",
        "url": "https://github.com/zym20192019/115-media-hub",
        "one_liner": "FastAPI 媒体自动化面板：115/夸克/天翼/123/阿里多网盘转存 + .strm 生成 + TG 同步 + 影视订阅 + 刮削",
        "caveats": "仅克隆未精读；多网盘聚合+媒体库自动化，与 115/影视探索线直接相关。克隆位置：002-探索项目/029-115项目/115-media-hub。",
        "tags": ["explore-archive", "unstudied", "115", "netdisk", "media", "fastapi", "reference"],
    },
    {
        "name": "115-sdk-go", "owner": "xhofe",
        "url": "https://github.com/xhofe/115-sdk-go",
        "one_liner": "115 网盘 Go SDK（xhofe/alist 作者出品）",
        "caveats": "仅克隆未精读；无 README，需拆源码。克隆位置：002-探索项目/029-115项目/115-sdk-go。",
        "tags": ["explore-archive", "unstudied", "115", "sdk", "go", "reference"],
    },
    {
        "name": "115driver", "owner": "SheltonZhu",
        "url": "https://github.com/SheltonZhu/115driver",
        "one_liner": "115 网盘 Go 库 + CLI + MCP server 三合一，全功能驱动 115.com API",
        "caveats": "仅克隆未精读；MCP server 形态可对照 MCP 只读服务方案。克隆位置：002-探索项目/029-115项目/115driver。",
        "tags": ["explore-archive", "unstudied", "115", "go", "mcp", "reference"],
    },
    {
        "name": "115shell", "owner": "luoshuhui",
        "url": "https://github.com/luoshuhui/115shell",
        "one_liner": "115 网盘命令行交互工具，类 FTP 操作体验（上传/下载/目录管理）",
        "caveats": "仅克隆未精读。克隆位置：002-探索项目/029-115项目/115shell。",
        "tags": ["explore-archive", "unstudied", "115", "cli", "reference"],
    },
    {
        "name": "115wangpan", "owner": "shichao-an",
        "url": "https://github.com/shichao-an/115wangpan",
        "one_liner": "115 网盘相关工具",
        "caveats": "仅克隆未精读；README 信息少，需拆源码。克隆位置：002-探索项目/029-115项目/115wangpan。",
        "tags": ["explore-archive", "unstudied", "115", "reference"],
    },
    {
        "name": "p115client", "owner": "ChenyangGao",
        "url": "https://github.com/ChenyangGao/p115client",
        "one_liner": "115 网盘 Python 客户端（ChenyangGao 115 生态系列）",
        "caveats": "仅克隆未精读；无 README，需拆源码。克隆位置：002-探索项目/029-115项目/p115client。",
        "tags": ["explore-archive", "unstudied", "115", "python", "reference"],
    },
    {
        "name": "pansou", "owner": "fish2018",
        "url": "https://github.com/fish2018/pansou",
        "one_liner": "高性能网盘资源搜索 API：TG 搜索 + 自定义插件，并发搜索/智能排序/网盘分类",
        "caveats": "仅克隆未精读。克隆位置：002-探索项目/029-115项目/pansou。",
        "tags": ["explore-archive", "unstudied", "115", "search", "api", "reference"],
    },
    # 夸克网盘（030）
    {
        "name": "Ghosten-Player", "owner": "GhostenEditor",
        "url": "https://github.com/GhostenEditor/Ghosten-Player",
        "one_liner": "安卓影视播放器（夸克网盘播放方案核心，另有用户改版截图多张）",
        "caveats": "仅克隆未精读；目录里含用户自改版截图与 apk 研究记录。克隆位置：002-探索项目/030-夸克网盘/Ghosten-Player。",
        "tags": ["explore-archive", "unstudied", "quark", "android", "player", "reference"],
    },
    {
        "name": "kuake_cli", "owner": "zhangjingwei",
        "url": "https://github.com/zhangjingwei/kuake_cli",
        "one_liner": "夸克网盘命令行工具",
        "caveats": "仅克隆未精读。克隆位置：002-探索项目/030-夸克网盘/kuake_cli。",
        "tags": ["explore-archive", "unstudied", "quark", "cli", "reference"],
    },
    {
        "name": "python-quark", "owner": "ChenyangGao",
        "url": "https://github.com/ChenyangGao/python-quark",
        "one_liner": "夸克网盘 Python 客户端（ChenyangGao 网盘生态系列）",
        "caveats": "仅克隆未精读；无 README，需拆源码。克隆位置：002-探索项目/030-夸克网盘/python-quark。",
        "tags": ["explore-archive", "unstudied", "quark", "python", "reference"],
    },
    {
        "name": "quarkdrive-webdav", "owner": "chenqimiao",
        "url": "https://github.com/chenqimiao/quarkdrive-webdav",
        "one_liner": "夸克网盘 WebDAV 服务（挂载为本地盘）",
        "caveats": "仅克隆未精读；与用户 WebDAV 应用线（WebDAVViewer）直接相关。克隆位置：002-探索项目/030-夸克网盘/quarkdrive-webdav。",
        "tags": ["explore-archive", "unstudied", "quark", "webdav", "reference"],
    },
    # 视频/CMS/TVBox（007/039/044/045/046）
    {
        "name": "TV", "owner": "FongMi",
        "url": "https://github.com/FongMi/TV",
        "one_liner": "FongMi TV：基于 CatVod 的安卓影音应用，TV 大屏+手机双情境，外部配置扩展点播/直播/爬虫引擎/DLNA/遥控",
        "caveats": "仅克隆未精读；本目录为无 .git 源码包（minSdk 24, leanback/mobi 双 flavor，含 catvod/chaquo 模块）。克隆位置：002-探索项目/007-TVBOXTV。",
        "tags": ["explore-archive", "unstudied", "tvbox", "android", "video", "reference"],
    },
    {
        "name": "JlenVideo", "owner": "jinnian0703",
        "url": "https://github.com/jinnian0703/JlenVideo",
        "one_liner": "Kotlin + Jetpack Compose + Media3 的安卓视频客户端，浏览/搜索/播放苹果 CMS 站点影视",
        "caveats": "仅克隆未精读；046-JlenVideo-精简版 是基于它的用户精简改造研究副本。克隆位置：002-探索项目/044-JlenVideo。",
        "tags": ["explore-archive", "unstudied", "video-cms", "android", "kotlin", "reference"],
    },
    {
        "name": "Bismuth-Player", "owner": "Eq52",
        "url": "https://github.com/Eq52/Bismuth-Player",
        "one_liner": "Bismuth Player：「铋」一样精致的影视播放器壳",
        "caveats": "仅克隆未精读。克隆位置：002-探索项目/045-苹果CMS研究/Bismuth-Player。",
        "tags": ["explore-archive", "unstudied", "video-cms", "player", "reference"],
    },
    {
        "name": "TVAPP", "owner": "youhunwl",
        "url": "https://github.com/youhunwl/TVAPP",
        "one_liner": "TV 影视 App 相关资源仓库",
        "caveats": "仅克隆未精读；无 README，需拆源码。克隆位置：002-探索项目/045-苹果CMS研究/TVAPP。",
        "tags": ["explore-archive", "unstudied", "video-cms", "tvbox", "reference"],
    },
    {
        "name": "VideoX", "owner": "txwebroot",
        "url": "https://github.com/txwebroot/VideoX",
        "one_liner": "聚合视频源/电视直播/网盘媒体的独立应用：高性能播放引擎+内容同步+安全加固",
        "caveats": "仅克隆未精读。克隆位置：002-探索项目/045-苹果CMS研究/VideoX。",
        "tags": ["explore-archive", "unstudied", "video-cms", "aggregator", "reference"],
    },
    {
        "name": "apple-cms-collector", "owner": "guoymwork",
        "url": "https://github.com/guoymwork/apple-cms-collector",
        "one_liner": "GymTV 资源站采集器：苹果 CMS V10 采集接口自动发现与验证",
        "caveats": "仅克隆未精读。克隆位置：002-探索项目/045-苹果CMS研究/apple-cms-collector。",
        "tags": ["explore-archive", "unstudied", "video-cms", "crawler", "reference"],
    },
    {
        "name": "CatFun", "owner": "hjdhnx",
        "url": "https://github.com/hjdhnx/CatFun",
        "one_liner": "Flutter 多端影视应用（Android/Windows/macOS/iOS/Linux）",
        "caveats": "仅克隆未精读。克隆位置：002-探索项目/045-苹果CMS研究/catfun。",
        "tags": ["explore-archive", "unstudied", "video-cms", "flutter", "reference"],
    },
    {
        "name": "movie", "owner": "waifu-project",
        "url": "https://github.com/waifu-project/movie",
        "one_liner": "小猫影视（2.5.9 起转向闭源，此为最后开源版）",
        "caveats": "仅克隆未精读。克隆位置：002-探索项目/045-苹果CMS研究/catmovie。",
        "tags": ["explore-archive", "unstudied", "video-cms", "reference"],
    },
    {
        "name": "gao", "owner": "gaotianliuyun",
        "url": "https://github.com/gaotianliuyun/gao",
        "one_liner": "FongMi 影视/TVBox/猫影视配置文件合集（社区分享）",
        "caveats": "仅克隆未精读。克隆位置：002-探索项目/045-苹果CMS研究/gao。",
        "tags": ["explore-archive", "unstudied", "tvbox", "config", "reference"],
    },
    {
        "name": "hongguo-drama-downloader", "owner": "wangduoyu001",
        "url": "https://github.com/wangduoyu001/hongguo-drama-downloader",
        "one_liner": "红果短剧批量下载器（仅供学习/离线研究）",
        "caveats": "仅克隆未精读。克隆位置：002-探索项目/045-苹果CMS研究/hongguo-drama-downloader。",
        "tags": ["explore-archive", "unstudied", "video-cms", "downloader", "reference"],
    },
    {
        "name": "maccms-api", "owner": "osscv",
        "url": "https://github.com/osscv/maccms-api",
        "one_liner": "MACCMS VOD API 文档与实现：列表/搜索/筛选/详情",
        "caveats": "仅克隆未精读。克隆位置：002-探索项目/045-苹果CMS研究/maccms-api。",
        "tags": ["explore-archive", "unstudied", "video-cms", "api", "reference"],
    },
    {
        "name": "maccms10", "owner": "magicblack",
        "url": "https://github.com/magicblack/maccms10",
        "one_liner": "苹果 CMS V10：PHP+MySQL 快速建站系统（视频站事实标准）",
        "caveats": "仅克隆未精读；045 批次的核心研究对象。克隆位置：002-探索项目/045-苹果CMS研究/maccms10。",
        "tags": ["explore-archive", "unstudied", "video-cms", "php", "cms", "reference"],
    },
    {
        "name": "mfs-video-terminal", "owner": "magic-fss",
        "url": "https://github.com/magic-fss/mfs-video-terminal",
        "one_liner": "MFS 影视终端：命令行影视搜索播放（多 API 源聚合 + 浏览器/MPV/VLC 多后端 + TUI）",
        "caveats": "仅克隆未精读。克隆位置：002-探索项目/045-苹果CMS研究/mfs-video-terminal。",
        "tags": ["explore-archive", "unstudied", "video-cms", "cli", "tui", "reference"],
    },
    {
        "name": "tvbox", "owner": "qist",
        "url": "https://github.com/qist/tvbox",
        "one_liner": "OK 影视/TVBox/猫影视配置文件合集（社区分享）",
        "caveats": "仅克隆未精读。克隆位置：002-探索项目/045-苹果CMS研究/tvbox。",
        "tags": ["explore-archive", "unstudied", "tvbox", "config", "reference"],
    },
    {
        "name": "wanfeng-video", "owner": "fzw005421",
        "url": "https://github.com/fzw005421/wanfeng-video",
        "one_liner": "晚风影视：Windows 桌面端播放器对接苹果 CMS 资源站（m3u8/HLS，H.264+H.265）",
        "caveats": "仅克隆未精读。克隆位置：002-探索项目/045-苹果CMS研究/wanfeng-video。",
        "tags": ["explore-archive", "unstudied", "video-cms", "windows", "player", "reference"],
    },
    # 自媒体爬虫（二创/xhs_refill 内嵌克隆）
    {
        "name": "MediaCrawler", "owner": "NanmiCoder",
        "url": "https://github.com/NanmiCoder/MediaCrawler",
        "one_liner": "自媒体平台爬虫：小红书/抖音/快手/B站/微博/贴吧/知乎（签名逆向+Playwright）",
        "caveats": "仅克隆未精读；xhs_refill 目录还含用户自己的 agentlimb_* 小红书采集实验脚本（非第三方）。克隆位置：040-OpenBiliClaw/二创/xhs_refill/MediaCrawler。",
        "tags": ["explore-archive", "unstudied", "crawler", "xhs", "playwright", "reference"],
    },
    # 日记类研究批次（references/diary-projects，09-06 建；nightDiary 与 references/ 根目录重复不重复入库）
    {
        "name": "Night-Journal", "owner": "Haaaiawd",
        "url": "https://github.com/Haaaiawd/Night-Journal",
        "one_liner": "晚记 Night Journal：夜间日记/陪伴向记录应用",
        "caveats": "仅克隆未精读。克隆位置：references/diary-projects/Night-Journal（09-06 日记研究批次）。",
        "tags": ["explore-archive", "unstudied", "diary", "journal", "reference"],
    },
    {
        "name": "cube-diary", "owner": "HoPGoldy",
        "url": "https://github.com/HoPGoldy/cube-diary",
        "one_liner": "cube-diary：日记应用（HoPGoldy 出品）",
        "caveats": "仅克隆未精读。克隆位置：references/diary-projects/cube-diary。",
        "tags": ["explore-archive", "unstudied", "diary", "reference"],
    },
    {
        "name": "journiv-app", "owner": "journiv",
        "url": "https://github.com/journiv/journiv-app",
        "one_liner": "Journiv：日记/日志应用（journiv-app）",
        "caveats": "仅克隆未精读。克隆位置：references/diary-projects/journiv-app。",
        "tags": ["explore-archive", "unstudied", "diary", "journal", "reference"],
    },
    {
        "name": "memex", "owner": "memex-lab",
        "url": "https://github.com/memex-lab/memex",
        "one_liner": "Memex：记忆/知识管理应用（memex-lab）",
        "caveats": "仅克隆未精读；与 agent-memory 系同类可对照。克隆位置：references/diary-projects/memex。",
        "tags": ["explore-archive", "unstudied", "diary", "memory", "reference"],
    },
    {
        "name": "nightly-journal", "owner": "damofer",
        "url": "https://github.com/damofer/nightly-journal",
        "one_liner": "nightly-journal：夜间日记应用",
        "caveats": "仅克隆未精读。克隆位置：references/diary-projects/nightly-journal。",
        "tags": ["explore-archive", "unstudied", "diary", "reference"],
    },
    # 2026-09-14 深度研究批次（7）
    {
        "name": "apple-notes-cli", "owner": "ingjieye",
        "url": "https://github.com/ingjieye/apple-notes-cli",
        "one_liner": "macOS 终端直读 Apple Notes 本地 NoteStore.sqlite 的只读 CLI：mode=ro + gzip/protobuf 解码出 Markdown，不用 AppleScript 不用云 API",
        "purpose": "查-读-导三场景（search/recent/show/folders/export），零第三方运行时依赖（手写 protobuf varint 解码器），全文搜索 800 笔记 <1s，刻意不缓存永远反映实时库。",
        "tech_stack": ["python3.10+", "sqlite(mode=ro)", "gzip+protobuf(手写解码)", "argparse", "uv/pipx"],
        "key_features": ["全文搜索(支持regex/folder/since/json)", "增量导出(sha256签名manifest)", "纯函数解码层与IO层隔离", "grep约定退出码", "自带Claude Skill与CLAUDE.md导出"],
        "reusable_techniques": [
            "mode=ro vs immutable=1 陷阱：immutable 跳过 -wal 静默给陈旧快照；WAL 下 mode=ro 并发安全且永远最新（MCP 只读服务方案的活体印证）",
            "长操作前用 SQLite backup() API 拷稳定时间点副本",
            "增量导出 manifest：sha256签名(全文+附件指纹) + 四重跳过校验(签名/路径/文件存在/mtime±1s) + 孤儿清理只删带自家水印的文件",
            "safe_output_path 路径穿越防护（已抄进 oss-research 静态服务路由）",
            "回收站识别用 ZFOLDERTYPE==1 语言无关标志而非本地化字符串；嵌套子文件夹沿 parent 链全收",
            "排序元组 (match_count, iso_time) 一次搞定相关度优先+新近度破平局",
            "无 FTS 现实下：小数据量先量化再决定缓存与否（800条<1s 则不缓存）",
            "SKILL.md 范本：双语触发词/先查可用性/两步读/退出码约定/隐私节",
        ],
        "caveats": "直读他人 App 私有库是其场景所限（Apple 无公开 API），OpenBiliClaw 数据源都是自家库无需解码 blob；SetFile 改创建时间是外部依赖锦上添花；recent 的 Python 侧 break 代替 LIMIT 是被 folder 过滤逼的，自家查询不拼父链可直接 SQL LIMIT。报告已挪至 references/。",
        "report_path": "references/AppleNotesCLI-借鉴分析.md",
        "tags": ["macos", "cli", "sqlite", "readonly", "local-first", "mcp-plan-reference", "reference", "deep-research"],
    },
    # 微信读书研究批次（000-微信读书，09-14 迁入 references/；012 weread-skill-web 为用户自研不入库）
    {
        "name": "awesome-weread", "owner": "BENZEMA216",
        "url": "https://github.com/BENZEMA216/awesome-weread",
        "one_liner": "微信读书笔记导出/自动化工具合集（bot 自动更新 seen.json）",
        "caveats": "仅克隆未精读。克隆位置：references/awesome-weread。",
        "tags": ["explore-archive", "unstudied", "weread", "export", "reference"],
    },
    {
        "name": "carl-weread", "owner": "LearnPrompt",
        "url": "https://github.com/LearnPrompt/carl-weread",
        "one_liner": "carl-weread：微信读书 Skill + reading coach（v0.3），Python 包结构含 workflows/examples",
        "caveats": "仅克隆未精读。克隆位置：references/carl-weread。",
        "tags": ["explore-archive", "unstudied", "weread", "skill", "reference"],
    },
    {
        "name": "WeRead-Agent", "owner": "WenWen610",
        "url": "https://github.com/WenWen610/WeRead-Agent",
        "one_liner": "WeRead-Agent：微信读书对话 Agent（前后端+Docker+Prometheus/Grafana+evals）",
        "caveats": "仅克隆未精读。克隆位置：references/WeRead-Agent。",
        "tags": ["explore-archive", "unstudied", "weread", "agent", "reference"],
    },
    {
        "name": "weread-skill-api", "owner": "lucis-yg",
        "url": "https://github.com/lucis-yg/weread-skill-api",
        "one_liner": "微信读书 Skill 的 Node.js API 封装（Express server + src）",
        "caveats": "仅克隆未精读；用户自研 weread-skill-web（二创/）即基于此 API。克隆位置：references/weread-skill-api。",
        "tags": ["explore-archive", "unstudied", "weread", "nodejs", "api", "reference"],
    },
    {
        "name": "weread-skill-desktop", "owner": "Duosl",
        "url": "https://github.com/Duosl/weread-skill-desktop",
        "one_liner": "微信读书 Skill 桌面端（Tauri + Vite，含 landing/docs/ui-style-guide）",
        "caveats": "仅克隆未精读；迁移时已清 node_modules(154M)+src-tauri/target(2.8G) 构建缓存入废纸篓。克隆位置：references/weread-skill-desktop。",
        "tags": ["explore-archive", "unstudied", "weread", "tauri", "desktop", "reference"],
    },
    # 小宇宙研究批次（000-小宇宙，09-14 迁入 references/）
    {
        "name": "xiaoyuzhou_script_skill", "owner": "zdhgreat",
        "url": "https://github.com/zdhgreat/xiaoyuzhou_script_skill",
        "one_liner": "小宇宙播客脚本技能（SKILL.md + scripts，抓取/转写工作流）",
        "caveats": "仅克隆未精读。克隆位置：references/xiaoyuzhou_script_skill。",
        "tags": ["explore-archive", "unstudied", "xiaoyuzhou", "podcast", "skill", "reference"],
    },
    {
        "name": "xiaoyuzhou-api", "owner": "ylw1997",
        "url": "https://github.com/ylw1997/xiaoyuzhou-api",
        "one_liner": "小宇宙播客非官方 API 封装（Python，含 docs/tests）",
        "caveats": "仅克隆未精读。克隆位置：references/xiaoyuzhou-api。",
        "tags": ["explore-archive", "unstudied", "xiaoyuzhou", "podcast", "api", "reference"],
    },
    {
        "name": "xiaoyuzhou-mcp", "owner": "r266-tech",
        "url": "https://github.com/r266-tech/xiaoyuzhou-mcp",
        "one_liner": "小宇宙播客 MCP 服务（只读形态，与 mcp-readonly-server 方案同类）",
        "caveats": "仅克隆未精读；只读 MCP 形态与 OpenBiliClaw MCP 只读服务方案（docs/plans/mcp-readonly-server.md）同类。克隆位置：references/xiaoyuzhou-mcp。",
        "tags": ["explore-archive", "unstudied", "xiaoyuzhou", "podcast", "mcp", "reference"],
    },
]


def main() -> int:
    existing = set()
    try:
        import sqlite3
        conn = sqlite3.connect(str(DEFAULT_DB_PATH))
        conn.row_factory = sqlite3.Row
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS oss_projects ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, owner TEXT);"
        )
        for r in conn.execute("SELECT owner, name FROM oss_projects"):
            existing.add((r["owner"], r["name"]))
        conn.close()
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 读取已有数据失败（将全量插入）: {exc}")

    inserted = 0
    for p in PROJECTS:
        key = (p.get("owner", ""), p.get("name", ""))
        if key in existing:
            print(f"[skip] 已存在: {p['name']}")
            continue
        new_id = insert_project(DEFAULT_DB_PATH, p)
        inserted += 1
        print(f"[ok] 插入 #{new_id}: {p['name']}")

    print(f"\n完成：新增 {inserted} 条，已存在跳过 {len(PROJECTS) - inserted} 条。库={DEFAULT_DB_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
