"""回填已分析的开源项目到 data/oss_research.db。

把已经研究过的 3 个仓库（TraeWorkAssistant-mac / wikitok / lushu）的结构化结论
写入开源研究库，使前端 tab 立即可用。重复运行幂等：按 (owner, name) 去重。

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
        "report_path": "TraeWorkAssistant-架构分析.md",
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
        "report_path": "WikiTok-借鉴分析.md",
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
        "report_path": "Lushu-借鉴分析.md",
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
        "report_path": "Brosis-借鉴分析.md",
        "tags": ["macos", "desktop", "mcp", "local-first", "privacy", "reference", "sqlite-vec", "agentic"],
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
