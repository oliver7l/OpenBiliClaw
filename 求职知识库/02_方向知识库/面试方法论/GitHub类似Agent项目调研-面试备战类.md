# GitHub 类似 Agent 项目调研 — 面试备战类

> 调研日期：2026-09-04
> 调研目标：为现有"求职备战 Agent"改造提供参考，重点关注简历-JD匹配算法、面试题预测分级、STAR故事库管理、模拟面试交互、多源面经结构化采集等环节的特色设计。
> 基线项目：H-Wren/my-interview（现有 agent 工作流骨架来源）

---

## 一、项目对比总表

| # | 项目名 | GitHub URL | Star | 最近更新 | 核心环节覆盖 | 技术栈 | 最大可借鉴点 |
|---|--------|-----------|------|---------|-------------|--------|-------------|
| 0 | **H-Wren/my-interview**（基线） | [link](https://github.com/H-Wren/my-interview) | 小 | 活跃 v2.2.0 | 全流程：KB→调研→匹配→P0-P4题→答案→Mock→STAR评估→复盘→HR话术 | Codex Skill (纯Markdown) + Claude Code + WebSearch | P0-P4五级题目优先级体系；置信度标注；文件优先输出；去AI味HR话术 |
| 1 | **wanyichen06/LLMInternSkill** | [link](https://github.com/wanyichen06/LLMInternSkill) | 301 | 活跃 | 简历润色→JD匹配→证据审计→面试拷打→补证据计划→Project Scout→LaTeX导出 | Codex Skill + Markdown + LaTeX模板 | **Evidence Guard 真实性边界**；Project Scout 补证据闭环；逐行简历诊断 |
| 2 | **he-yufeng/FindJobs-Agent** | [link](https://github.com/he-yufeng/FindJobs-Agent) | 252 | 活跃 | 岗位爬虫→LLM岗位分析→简历解析匹配→AI模拟面试 | Python(Flask) + React + SQLite + OpenAI + Selenium | **多公司岗位爬虫+标准化管道**；技能标签评分体系；岗位分类法 |
| 3 | **1624899/ai_interview（面面）** | [link](https://github.com/1624899/ai_interview) | 64 | 活跃 | 简历多专家诊断→JD定向优化→模拟面试(文本+语音)→多轮面试→能力雷达 | Python(FastAPI+LangGraph) + Next.js 15 + PostgreSQL 16 | **LangGraph状态机编排**；简历圆桌会议三专家+Reflector；实时语音面试(Qwen3-Omni) |
| 4 | **younnieCutler/japan-career-agent** | [link](https://github.com/younnieCutler/japan-career-agent) | ~100 | 活跃 | 经历棚卸→证据审批→JD匹配→職務経歴書→模拟面试→转职策略→谈判 | Python(PyPI) + Claude Code/Codex插件 + 本地GUI + Career Vault | **证据门控(Evidence-gated)架构**；Confirmed/Unknown/Contradictory状态机；硬冲突不被平均抵消 |
| 5 | **jennifer88huang/interview-skills** | [link](https://github.com/jennifer88huang/interview-skills) | ~200 | 活跃 | JD+简历匹配→公司风格分析→专属10问→互动追问→好/差答案对比→HR面+谈薪 | OpenClaw/Codex Skill + GitHub Pages UI + 浏览器端 | **大厂风格差异化适配**；好答案vs差答案对比模板；多轮连贯模拟(一面→二面→三面→HR) |
| 6 | **XUZIAa/Multi-Agent-Mock-Interview** | [link](https://github.com/XUZIAa/Multi-Agent-Mock-Interview) | 120 | 活跃 | 简历上传→差距诊断→实时语音模拟面试→多维复盘→错题本→成长轨迹 | Tauri 2(Rust) + React 19 + Python FastAPI sidecar + SQLite + WebSocket | **双环架构(Fast/Slow Loop)防人格漂移**；硬时间闸门；代码沙盒可运行；韵律分析 |
| 7 | **Chozzc/Lujie-Careerkit** | [link](https://github.com/Chozzc/Lujie-Careerkit) | ~150 | 活跃 v0.3.0 | 简历编辑→JD匹配→面试备战指南→Mock面试→AI复盘→投递追踪→素材库 | Next.js 16 + React 19 + Prisma + SQLite + Docker + 4个Agent Skill | **全链路职业工作台**；AI修改逐段审核接受/拒绝；投递管道追踪；4个独立Agent Skill封装 |

---

## 二、每个项目详细分析

### 0. H-Wren/my-interview（基线项目 — 现有 agent 骨架来源）

**基本信息**
- GitHub：https://github.com/H-Wren/my-interview
- Star：较少（个人 Skill 项目）
- 最近更新：v2.2.0，持续活跃
- License：MIT

**核心功能与工作流**

完整的 8 阶段流水线，全部自动执行、不中途提问：

```
Step 0 Intake（公司+职位+JD+简历）
  → Phase 0 Candidate KB（三段式：基本画像/核心竞争力/可复用案例库）
  → Phase 1 Company Research（5维度搜索+加权评分+置信度交叉验证）
  → Phase 1.5 Product Deep-Dive（条件触发）
  → Phase 2 Resume×JD Match（逐项匹配表+不清晰点标记+改写建议）
  → Phase 3 Predicted Questions P0-P4（核心！）
  → Phase 4 Draft Answers（仅用真实事实）
  → Phase 5 Mock Interview（公司风格适配）
  → Phase 6 STAR Evaluation（S/T/A×2/R×2 加权评分）
  → Prep Output（合并为 full-prep.md）
```

附加模块：Storybank 管理（add/list/gaps）、Hype 信心简报、Debrief 复盘、/update-prep 增量更新+版本管理、/greet HR话术（去AI味检查清单）。

**技术栈**
- 纯 Codex Skill（Markdown 定义工作流），无独立代码
- 运行于 Claude Code / Claude Desktop / Claude Web
- 工具：Read / Write / Edit / Bash / WebSearch / WebFetch
- 参考文件：star-framework.md、competency-taxonomy.md（15类能力）、company-culture-tags.md
- 输出：preps/{slug}/ 目录，默认合并为 single full-prep.md

**Prompt 设计亮点**
1. **P0-P4 五级题目优先级**：P0=面经真题、P1=JD核心要求推导、P2=简历薄弱点追问、P3=公司业务/产品、P4=通用题库。每道题标注来源和可信度。
2. **三类必出题方法论**：简历矛盾题、转型/缺口题、经历深挖题，每类有完整出题模板和示例。
3. **面试官追问风格适配表**：按公司类型（国企/外企/创业/国内大厂）定义 Follow-up 特点。
4. **透明度规则**：每道题标注「来自简历原文」「基于推断」「无信息来源」「需要你补充数据」。
5. **去AI味检查清单**：9项逐条检查（无开场套话/无空总结/无黑话/无翻译腔等）。

**输出格式**
- slug 规则：`{公司英文简称}-{岗位简称}-{日期}`
- 默认单文件 `preps/{slug}/full-prep.md`，对话只显示进度摘要
- 每道题固定格式：问题→面试官想了解什么→为什么出现→回答框架(含来源标注)→Follow-up预判→注意事项→建议用时→来源
- 独立 SKILL.md 副本支持 `/mock {slug}` 直接调用

**与现有 agent 骨架的对应关系**
- 完全对应：Candidate KB → Company Research → Resume×JD Match → P0-P4 Questions → Draft Answers+STAR → Prep Package
- 现有 agent 在此基础上扩展了：三层知识库（原始资料/方向知识/岗位弹药）、CLI 情报工具矩阵（小红书/B站/知乎/微博/BOSS）、kb.py 检索引擎

**局限性**
- 纯 Skill 形态，无独立后端/数据库，数据持久化依赖文件系统
- 公司调研依赖 WebSearch，无结构化爬虫
- 模拟面试为文本对话，无语音/实时交互
- 无投递追踪/岗位管理功能

---

### 1. wanyichen06/LLMInternSkill — 证据边界驱动的简历+面试工具箱

**基本信息**
- GitHub：https://github.com/wanyichen06/LLMInternSkill
- Star：301
- 最近更新：持续活跃（4 commits，有完整 examples/evals/docs）
- License：MIT
- 定位：大模型实习简历与求职工具箱，evidence-bound resume toolkit

**核心功能与工作流**

```
raw resume + materials/ + target_jd.txt
  → 简历润色（Before/After + 技术表达增强）
  → JD 匹配（match table + 岗位关键词重排）
  → 真实性边界（Truth Boundary：可以写/谨慎写/补证据后写/不能写/无法判断）
  → Evidence Contract（证据契约）
  → 定制简历
  → 面试拷打（逐行拷打 + 追问链 + 危险/及格/强回答三档）
  → 回答卡（Answer Cards）
  → 补证据计划（1天/3天/1周）
  → Project Scout（开源项目推荐 + 最小运行路径 + 改造点 + 可写证据）
  → LaTeX 简历草稿（Bill Ryan 中文模板）
```

**技术栈**
- Codex Skill（SKILL.md + skill-references/ + templates/）
- 目录结构：agents/、docs/、evals/、examples/、references/、skill-references/、templates/
- LaTeX 模板：Bill Ryan elegant resume 中文版（XeLaTeX 编译）
- 有 evals/ 目录（评估集）和 examples/（完整旗舰示例：豆包 Seed 搜索排序）

**亮点/可借鉴点**

1. **Evidence Guard（证据守卫）— 最核心差异化**
   - 五级判断：可以写 / 谨慎写 / 补证据后写 / 不能写 / 无法判断
   - 不把"做过 RAG demo"包装成"主导企业级智能知识库系统上线"
   - Before/After 示例明确展示"降级危险表达"的逻辑

2. **Project Scout（补证据闭环）**
   - 当材料太弱时，不硬写，而是推荐开源项目去补
   - 每个推荐包含：why this fits / why not risk / minimum run path / what to modify / what evidence to collect / resume-safe claim after completion / interview grilling questions
   - 明确边界：学习开源项目不能直接包装成工作经历，只有真实复现+改造+记录证据后才能写

3. **面试拷打三档回答**
   - 危险回答 / 及格回答 / 强回答，逐行简历生成追问链
   - 比 my-interview 的"回答框架"更具体，直接给出三档对比

4. **岗位方向专项检查清单**
   - 10+ 个 LLM 细分方向（RAG/Agent/Agentic RL/Post-training/Pretraining/搜索排序/AIGC/多模态等），每个方向有专门的"会重点检查什么"

5. **完整输出包编号**
   - 01_jd_analysis → 11_final_pack，11个文件有序产出

**与现有 agent 骨架的对应关系**
- Candidate KB → 对应 materials/ 文件夹审计 + Truth Boundary
- Resume×JD Match → 对应 JD Tailoring + match table
- Predicted Questions → 对应 Interview Grilling（逐行拷打）
- Draft Answers → 对应 Answer Cards（危险/及格/强三档）
- **独有**：Evidence Guard、Project Scout、LaTeX 导出
- **未覆盖**：公司调研（无 WebSearch）、模拟面试交互、STAR 评估、HR话术

**局限性**
- 聚焦 LLM 实习/AI 求职方向，通用性不如 my-interview
- 无公司调研模块，不做 WebSearch
- 无交互式模拟面试，只有"面试拷打"题目生成
- 无故事库管理（Storybank）

---

### 2. he-yufeng/FindJobs-Agent — 岗位爬虫驱动的全栈求职助手

**基本信息**
- GitHub：https://github.com/he-yufeng/FindJobs-Agent
- Star：252
- 最近更新：活跃（有 CI、Roadmap）
- License：MIT
- 定位：从岗位爬取到模拟面试的全栈求职助手

**核心功能与工作流**

```
Job Crawler（腾讯/网易/字节/Amazon等，API+Selenium）
  → LLM Job Analysis（提取学历/专业要求、技能标签1-5评分、岗位分类）
  → Resume Parsing & Matching（PDF/Word解析、技能评分、case-insensitive匹配百分比）
  → AI Mock Interview（从JD生成题目、多轮对话、实时反馈）
  → SQLite 持久化（jobs.db，CSV自动迁移）
```

**技术栈**
- 后端：Python 3.9+、Flask（api_server.py）、OpenAI API
- 前端：React（JobsPage / ResumePage / InterviewPage）
- 爬虫：job_crawler_v2.py（API）+ job_crawler_selenium.py（Selenium）+ freehire_source.py（聚合源）
- 存储：SQLite（jobs.db），CSV/JSON 兜底
- 数据：tech_taxonomy.json（岗位分类法）、all_labels.csv（技能标签库）
- 模块：job_agent.py（LLM分析）、interview_agent.py（面试）、resume_parser.py、tag_rate.py（技能评分）、pipeline.py（数据管道）

**亮点/可借鉴点**

1. **多公司岗位爬虫 + 标准化管道**
   - 直接从公司官网 career 页面爬取（腾讯/网易/字节/Amazon），不是从招聘平台
   - pipeline.py 支持 crawl→analyze→score→serve 全链路或单阶段运行
   - freehire.me 聚合源作为可选补充（公开 API 无需 key）

2. **技能标签评分体系（tag_rate.py）**
   - 每个岗位的技能要求打 1-5 分
   - 简历技能也评分，然后计算 case-insensitive 匹配百分比
   - 有 all_labels.csv 技能标签库和 tech_taxonomy.json 岗位分类法

3. **LLM 岗位分析结构化**
   - 提取教育/专业要求、技能标签评分、岗位分类
   - 分析结果存入 SQLite，支持筛选（facets API 运行时读取词汇表）

4. **从 JD 直接驱动模拟面试**
   - 任何岗位的 JD 都可以直接启动 AI 模拟面试
   - 面试题目从该 JD 生成，多轮对话带实时反馈

**与现有 agent 骨架的对应关系**
- Company Research → 对应岗位爬虫（但只爬岗位，不做公司口碑/面经调研）
- Resume×JD Match → 对应 resume_parser + tag_rate 技能评分匹配
- Predicted Questions → 对应 interview_agent 从 JD 生成题目
- Mock Interview → 对应 AI mock interview（多轮+实时反馈）
- **独有**：岗位爬虫、技能标签量化评分、SQLite岗位库
- **未覆盖**：Candidate KB、STAR故事库、题目分级(P0-P4)、公司调研、HR话术、复盘

**局限性**
- 公司调研维度弱：只爬岗位 JD，不做口碑/面经/业务调研
- 面试题无优先级分级，无简历薄弱点针对性出题
- 无 STAR 评估框架
- 无候选人知识库复用机制
- 爬虫覆盖公司有限（Roadmap 中计划扩展）
- 前端较重，部署需要 Python+Node+Chrome

---

### 3. 1624899/ai_interview（面面）— LangGraph 多智能体 + 实时语音

**基本信息**
- GitHub：https://github.com/1624899/ai_interview
- Star：64
- 最近更新：非常活跃（实时语音面试为 New! 功能）
- License：非商业使用许可证（Non-Commercial Use License）
- 定位：基于 LangGraph 的全能求职助手，全真模拟面试 + 简历深度优化

**核心功能与工作流**

两大核心工作流：

**A. 简历优化流（多专家圆桌会议）**
```
简历上传 → 简历初步分析(Analyzer)
  → 圆桌会议：匹配分析师 + 内容优化师 + HR审核官 三专家会诊
  → Quality Assurance (Reflector) 节点二次审核反思
  → JD定向优化（关键词提取 + STAR法则应用）
  → 简历生成（整合面试对话亮点）
```

**B. 面试模拟流（LangGraph 状态机）**
```
简历+JD → 智能规划生成个性化题目清单
  → 全真模拟（面试中不即时反馈）
  → 智能提示（卡壳时获取提示）
  → 多轮面试系统（一面基础→二面深度→三面综合，自动去重）
  → 结束报告（评分+优缺点+录用建议）
  → 能力评估（5维雷达图：技术/沟通/问题解决/学习/团队协作）
```

新增：实时语音面试（Qwen3-Omni + VAD + 浏览器STT + SSE双流）

**技术栈**
- 后端：Python 3.11+、FastAPI、LangGraph（状态机编排）、LangChain、PostgreSQL 16
- 前端：Next.js 15（App Router）、TypeScript、Tailwind CSS、shadcn/ui、Zustand
- 语音：Qwen3-Omni 实时交互、SSE 音频+文本双流、VAD、浏览器端 STT、IndexedDB 缓存
- 部署：Docker / docker-compose
- 核心文件：graph.py（面试状态机）、voice_interview.py（语音引擎）、resume_optimizer_graph.py（多专家）、resume_generation_graph.py、resume_analyzer_graph.py

**亮点/可借鉴点**

1. **LangGraph 状态机编排面试流程**
   - 面试流程用 StateGraph 建模，支持复杂状态流转
   - 多轮面试自动继承简历/JD，自动区分轮次策略，问题去重
   - 这是比纯 Prompt 驱动更工程化的做法

2. **简历优化圆桌会议（三专家 + Reflector）**
   - 匹配分析师（JD匹配度）、内容优化师（修改建议）、HR审核官（筛选视角）
   - Reflector 节点对专家建议二次审核反思，确保输出质量
   - 多 Agent 协作而非单 Agent 输出

3. **实时语音面试**
   - Qwen3-Omni 全双工语音，支持打断面试官
   - 语音会话可从文本模式一键克隆，保留上下文
   - VAD + 浏览器 STT，IndexedDB 本地缓存录音

4. **能力画像雷达图**
   - 5维度评分（技术/沟通/问题解决/学习/团队协作）
   - 多次面试后的综合能力趋势（累计档案）
   - 自动识别展现的技术技能标签

**与现有 agent 骨架的对应关系**
- Resume×JD Match → 对应匹配分析师 + JD定向优化
- Predicted Questions → 对应智能规划生成题目（但无 P0-P4 分级）
- Mock Interview → 对应全真模拟（文本+语音，多轮）
- Draft Answers+STAR → 对应结束报告 + 能力评估（但无 STAR 结构化评分）
- **独有**：LangGraph状态机、多专家圆桌、实时语音、能力雷达
- **未覆盖**：Candidate KB（无复用知识库）、公司调研、STAR故事库、HR话术、题目分级

**局限性**
- 非商业许可证，不能用于商业用途
- 无公司调研模块（不做 WebSearch/口碑/面经采集）
- 面试题无 P0-P4 优先级分级体系
- 无候选人知识库跨岗位复用
- 无 STAR 故事库管理
- 部署较重（需要 PostgreSQL + Docker）
- Star 数较低（64），但功能完成度高

---

### 4. younnieCutler/japan-career-agent — 证据门控的职业决策系统

**基本信息**
- GitHub：https://github.com/younnieCutler/japan-career-agent
- Star：约 100（PyPI + npm 双发布）
- 最近更新：非常活跃（有 CI test、release、CHANGELOG）
- License：MIT
- 定位：Evidence-based career decision support，本地优先，不做 SaaS

**核心功能与工作流**

核心三步循环：
```
1. Record what happened（棚卸し/tanaoroshi）
   → 将过去工作转化为 contexts / experiences / checkable evidence
   → 无法验证的保持 Unknown
2. Approve it（审批）
   → 任何事实进入 canonical career record 前必须用户确认
   → 没有来源的数字被拒绝
3. Use it（使用）
   → JD匹配 / 職務経歴書 / 面试练习 / 下一步行动
   → 所有输出只引用已确认的证据
```

12 个 Skill 模块：
- career-tanaoroshi（经历棚卸）
- career-document / humanize-japanese-career（文档生成）
- career-maintenance（职业记录维护）
- jiko-bunseki（自我分析/方向探索）
- job-seeker-agent（求职文档准备）
- hiring-manager-agent / kigyou-bunseki（JD和雇主分析）
- matching-simulator / company-battlecard（机会对比）
- mock-interviewer（模拟面试）
- tenshoku-strategy（转职策略）
- career-agent（核心运行时）
- verify / intent / factcheck / challenge / trim（验证与挑战）

**技术栈**
- Python 3.11-3.13（canonical runtime），发布到 PyPI
- npm 包作为入口（npx japan-career-agent），内部定位 uv/pipx 安装 Python
- Claude Code / Codex 插件（plugin marketplace）
- 本地 GUI（可选）
- Career Vault（本地职业记录库）+ Evidence Ledger（证据账本）
- 确定性文档门控（deterministic document gate）+ HTML 渲染

**亮点/可借鉴点**

1. **证据门控架构（Evidence-gated）— 最值得学习的设计哲学**
   - 每个请求遵循：请求 → Career Agent → Evidence+State → 需要确认？→ 用户审批 → Canonical State → 分析/准备
   - 确认步骤不可跳过
   - 词汇表严格定义：Confirmed / Unknown / Contradictory / Stale / Low Confidence
   - JD匹配状态：Matched / Missing / Unknown（不是简单的分数）
   - 决策状态：Proceed / Review / Conflict

2. **硬冲突不被平均抵消**
   - "A confirmed hard, legal, must-have, or dealbreaker conflict is not averaged away by another strength"
   - 一个硬性不匹配不会因为其他优势被抹平
   - 这比简单的"匹配度百分比"更诚实、更有用

3. **不预测录用结果**
   - "The system does not predict whether you will be hired"
   - 只提供证据和分析，最终决策由用户做
   - 不自动投递、不自动发消息

4. **证据与偏好分离**
   - interest_level 记录用户偏好，但不改变客观证据、决策状态或排序
   - 简历/JD/网页/YAML 都是 career data，不是 instruction

5. **多形态交付**
   - CLI 命令、Claude Code/Codex 插件、本地 GUI，共用同一个 Python runtime 和 Career Vault
   - 插件不持有自己的 career facts 副本

**与现有 agent 骨架的对应关系**
- Candidate KB → 对应 Career Vault + tanaoroshi（更严格的证据审批）
- Company Research → 对应 kigyou-bunseki + hiring-manager-agent
- Resume×JD Match → 对应 matching-simulator（Matched/Missing/Unknown 三态）
- Predicted Questions → 对应 mock-interviewer（但无 P0-P4 分级）
- **独有**：证据门控、硬冲突处理、机会对比(battlecard)、转职策略、谈判
- **未覆盖**：STAR故事库、题目分级、HR话术、答案草稿

**局限性**
- 面向日本求职市场（職務経歴書、日本語），部分设计需本地化
- 无 P0-P4 题目优先级体系
- 无 STAR 结构化评分
- 安装链路较复杂（npx→uv→Python）
- 无中文面经采集能力

---

### 5. jennifer88huang/interview-skills — 大厂风格差异化 + 好/差答案对比

**基本信息**
- GitHub：https://github.com/jennifer88huang/interview-skills
- Star：约 200
- 最近更新：活跃（2026-07 新增案例，2026-06 新增网页 UI）
- License：未明确
- 定位：大厂 AI 模拟面试官，基于 JD+简历生成专属问题

**核心功能与工作流**

```
目标公司+岗位 → 粘贴JD → 上传简历
  → JD vs 简历匹配度分析（强匹配/需补强/简历弱点）
  → 公司风格分析（字节追算法/阿里问价值观/腾讯挖底层/Google重代码质量）
  → 专属面试10问（题目类型/难度星级/参考答案提示/追问方向）
  → 备战建议（紧急/参考分级）
  → 互动追问模式（说"帮我追问"继续深挖）
  → 好答案vs差答案对比
  → 多场景：技术面/HR面/薪资谈判/多轮完整面试
```

**技术栈**
- OpenClaw / Codex Skill（SKILL.md 驱动）
- GitHub Pages 网页 UI（在线模拟面试，支持 JD/简历上传）
- 支持模型供应商配置（OpenAI 兼容 API）
- 浏览器端文件解析（PDF/Word/图片/TXT/Markdown）
- 安装：`/skill install git:jennifer88huang/interview-skills@main` 或 install-skill.sh

**亮点/可借鉴点**

1. **大厂风格差异化适配**
   - 内置公司风格画像：字节（节奏快+算法必考+项目深挖+impact）、阿里（客户第一+协同+数据结果）、腾讯（底层原理）、Google/Meta/Amazon/Microsoft（代码质量+系统设计+领导力原则）
   - 面试题风格根据公司自动调整
   - 这比 my-interview 的 company-culture-tags.md 更具体到出题风格

2. **好答案 vs 差答案对比模板**
   - 每道题可查看高分示范答法、低分踩坑示例与点评
   - 技术题版和行为题版各有完整模板
   - 差答案分析：为什么差（模糊动作/无排查顺序/无具体案例）
   - 好答案分析：为什么好（框架完整+覆盖全面+真实项目收尾）
   - 这是 my-interview 没有的——my-interview 只给回答框架，不给好坏对比

3. **多轮完整面试连贯模拟**
   - 一面（基础）→ 二面（深度）→ 三面（综合）→ HR面 连贯题组
   - 每轮有承接逻辑（上一轮答不好，下一轮怎么被放大追问）
   - 每轮通过关键 + 淘汰风险
   - 全流程备战建议

4. **HR 面专项 + 薪资谈判**
   - HR 面专项题组：离职原因、薪资预期、职业规划
   - 每题隐藏考察点 + 回答原则 + 踩坑提醒 + 追问方向
   - 薪资谈判话术：策略 + 分场景话术 + 不该说的话 + 收口建议

5. **匹配度三级分类**
   - ✅ 强匹配 / ⚠️ 需补强 / ⚠️ 简历弱点
   - 比简单的匹配百分比更有行动指导意义

**与现有 agent 骨架的对应关系**
- Company Research → 对应公司风格分析（但只做风格，不做业务/口碑调研）
- Resume×JD Match → 对应匹配度三级分析
- Predicted Questions → 对应专属10问（无 P0-P4 分级，但有难度星级）
- Draft Answers → 对应好/差答案对比
- Mock Interview → 对应互动追问模式
- **独有**：好/差答案对比、多轮连贯模拟、HR面+谈薪专项、大厂风格出题
- **未覆盖**：Candidate KB、STAR故事库、公司深度调研、答案草稿文件化、复盘

**局限性**
- 无候选人知识库复用（每次重新输入简历）
- 无公司深度调研（不做 WebSearch，只靠内置风格画像）
- 题目无 P0-P4 优先级分级（只有难度星级）
- 无 STAR 结构化评分
- 无故事库管理
- 输出在对话中，无文件优先的 prep package 概念

---

### 6. XUZIAa/Multi-Agent-Mock-Interview — 双环架构防漂移的实时语音面试

**基本信息**
- GitHub：https://github.com/XUZIAa/Multi-Agent-Mock-Interview
- Star：120
- 最近更新：活跃（首个公开发布版）
- License：GPL-3.0
- 定位：本地运行的 AI 模拟面试系统，全双工语音对话

**核心功能与工作流**

```
准备面试（四步向导）：
  简历上传(PDF/DOCX/TXT) → 目标岗位 → 差距诊断(可跳过) → 面试设置
  → 出题（约1分钟）
  → 实时语音面试（可打断/被打断、求助提词、代码沙盒、摄像头）
  → 自动复盘（满5分钟）：多维雷达图 + 逐字稿高亮批注 + 满分答案重构 + 韵律分析 + 专项提升方案
  → 错题本 + 成长轨迹
```

**技术栈**
- 桌面外壳：Tauri 2（Rust）
- 前端：React 19 + Vite 7 + TypeScript + Tailwind 4 + shadcn/ui
- 图表：Recharts；代码编辑器：CodeMirror 6
- 后端：Python 3.12 + FastAPI（sidecar 子进程）
- 数据：SQLAlchemy 2.0 async + aiosqlite（WAL 模式）
- 实时语音：WebSocket + OpenAI Realtime 风格协议
- 音频：sounddevice + numpy（16kHz采集/24kHz播放）
- 文档解析：pdfplumber + python-docx
- 打包：PyInstaller + Tauri NSIS（下载即用安装包）

**亮点/可借鉴点**

1. **双环架构（Fast Loop / Slow Loop）— 解决 AI 面试官人格漂移**
   - Fast Loop（口）：实时语音模型，只负责听懂+用人设语气说出意图，有界、短、不依赖长期记忆
   - Slow Loop（脑）：后端权威状态机 InterviewState，追踪当前考察维度/已问题目/JD技能覆盖度/STAR完整度/追问深度/打断额度/技能深度阶梯/漂移分数
   - 人格不是"被记住"的，而是每轮重新注入的
   - 这是对"AI面试官聊几轮就跑偏"问题的系统性解决方案

2. **防漂移三道防线**
   - 人设契约：结构化定义编译成运行时指令
   - 输出守卫：正则前置+小模型复核，拦截"作为AI助手"等越界
   - 心跳重锚：每若干轮或漂移抬头时，强制重灌人设与状态摘要

3. **硬时间闸门**
   - 10/20/30/45分钟四档预设，到点强制进入收尾
   - 不给模型自由裁量时间的机会
   - 不足5分钟不生成完整复盘

4. **真题补充机制**
   - 题库在模型出题之外，追加高频真题
   - 频次来自多篇面经交叉统计
   - 目前只对 Java 后端与 Agent 方向生效

5. **代码沙盒**
   - 面试中可以出代码题，写完直接运行和跑用例
   - 支持 Python（自带运行时）和 JavaScript（需 Node）

6. **韵律分析**
   - 语速、停顿、填充词（"嗯""然后"），规则计算而非模型猜测
   - 逐字稿高亮批注

**与现有 agent 骨架的对应关系**
- Resume×JD Match → 对应差距诊断（较简单）
- Predicted Questions → 对应出题（模型出题+真题补充，但无分级）
- Mock Interview → 对应实时语音模拟（最强项）
- Draft Answers+STAR → 对应满分答案重构 + 多维评分
- **独有**：双环架构、防漂移、实时语音、代码沙盒、韵律分析、错题本
- **未覆盖**：Candidate KB、公司调研、STAR故事库、HR话术、题目分级(P0-P4)

**局限性**
- GPL-3.0 许可证（传染性，商业使用需注意）
- 无公司调研模块
- 无候选人知识库复用
- 无 P0-P4 题目分级
- 无 STAR 故事库管理
- 真题补充只覆盖 Java 后端和 Agent 方向
- 首个公开发布版，可能有 bug

---

### 7. Chozzc/Lujie-Careerkit — 全链路职业工作台 + 4 个 Agent Skill

**基本信息**
- GitHub：https://github.com/Chozzc/Lujie-Careerkit
- Star：约 150
- 最近更新：活跃（v0.3.0，实验性 in-app agent）
- License：Apache-2.0
- 定位：从简历编辑到 offer 接受的全链路职业 Agent 工作台

**核心功能与工作流**

```
简历库（多版本管理） → AI简历分析（action-result结构/证据/清晰度诊断）
  → JD匹配（粘贴JD → AI诊断证据 → 重排重点 → 生成岗位定制版）
  → 面试备战指南（概览+能力画像+证据缺口+核心知识+经历深挖+针对性题目+准备计划）
  → Mock面试（从简历+JD生成题目 → 保存答案草稿 → AI复盘报告）
  → 投递追踪（公司/岗位/来源/阶段/截止日/跟进日期/笔记/JD文本/关联简历版本）
  → 职业素材库（按角色/类型组织所有产出物）
  → 实验性 in-app Agent（自然语言搜索投递/读取隐私过滤后的简历概览/创建更新投递卡片）
```

4 个独立 Agent Skill（可在 Codex/Claude Code 中直接调用）：
- `resume-improvement`：简历诊断+改进，可选 JD 定向
- `prepare-job-interview`：公司+岗位调研 → 结构化面试备战指南
- `mock-interview-coach`：交互式模拟面试 + 自适应追问 + 证据驱动复盘
- `job-application-writer`：求职信/HR打招呼/邮件/内推/跟进消息

**技术栈**
- 前端/全栈：Next.js 16 + React 19 + TypeScript
- 数据库：Prisma ORM + SQLite（本地文件 prisma/dev.db）
- 部署：Docker（GHCR 镜像）+ docker-compose
- AI：OpenAI 兼容 API（可配置 Base URL + 模型 + Key）
- API Key 加密存储：LUJIE_SETTINGS_SECRET 加密后存 SQLite
- Agent Skills：`.agents/skills/` 目录下 4 个 SKILL.md
- 导出：PDF / PNG / 可编辑 DOCX / Word

**亮点/可借鉴点**

1. **AI 修改逐段审核机制**
   - AI 简历优化后，打开分步审核界面
   - 变更按简历 section 分组，原文与 AI 建议并排显示
   - 每条变更可独立接受/拒绝/编辑
   - 只保存接受的内容到新版本，原简历不变
   - GPA/日期/缺失事实等 AI 不应推断的信息，引导回编辑器手动确认

2. **4 个独立 Agent Skill 封装**
   - 不是一个大而全的 Skill，而是拆成 4 个职责清晰的 Skill
   - 每个 Skill 包含完整工作流、研究要求、事实边界、质量检查
   - 可在 Codex 中自动选择或显式调用（`$resume-improvement`）
   - 这种拆分方式比 my-interview 的单一大 Skill 更模块化

3. **投递管道追踪**
   - 看板式管理：bookmarked → applied → replied → interview → offer → rejected
   - 每个投递关联简历版本、JD 文本、跟进日期
   - Dashboard 指标：投递数/进行中/待跟进/offer 数
   - 这是面试备战下游的完整闭环

4. **职业素材库**
   - 按角色/类型组织：原始JD、定制简历、面试备战指南、Mock答案、AI复盘
   - 每个角色有独立 archive 页面：结构化目录+角色概览+准备路径+产出物列表
   - 支持编辑/删除/查看

5. **隐私过滤**
   - in-app Agent 读取简历概览时，自动过滤邮箱/电话等联系方式
   - API Key 加密存储

**与现有 agent 骨架的对应关系**
- Candidate KB → 对应简历库（多版本，但非结构化知识库）
- Company Research → 对应 prepare-job-interview skill 中的公司调研
- Resume×JD Match → 对应 JD 匹配模块
- Predicted Questions → 对应面试备战指南中的针对性题目
- Draft Answers+STAR → 对应 Mock 答案草稿 + AI 复盘
- Prep Package → 对应面试备战指南（可导出 Word/PDF）
- **独有**：投递追踪、素材库、AI修改逐段审核、4 Skill 模块化、全链路工作台
- **未覆盖**：P0-P4 题目分级、STAR故事库管理、HR话术去AI味、面经采集

**局限性**
- 面试题无 P0-P4 优先级分级
- 无 STAR 故事库（Storybank）管理
- 无面经结构化采集（不做小红书/知乎/牛客爬虫）
- 无 CLI 情报工具矩阵
- in-app Agent 仍为实验性，不能直接编辑简历或跑完整工作流
- 较重的全栈应用，不如纯 Skill 轻量

---

## 三、综合结论

### 最值得参考的 3 个项目及理由

#### 🥇 第一名：younnieCutler/japan-career-agent — 证据门控架构

**推荐理由：**
这是所有调研项目中设计哲学最成熟的一个。它的"证据门控"思想直接解决了现有 agent 最大的风险点——AI 编造经历。

具体可借鉴：
1. **Confirmed / Unknown / Contradictory 三态证据模型**：替代简单的"匹配度百分比"，让候选人清楚知道哪些是确认的事实、哪些是缺口、哪些有矛盾
2. **硬冲突不被平均抵消**：一个硬性不匹配（如"必须5年经验但只有3年"）不会因为其他优势被抹平，这比加权打分更诚实
3. **审批步骤不可跳过**：任何事实进入 canonical record 前必须用户确认，从机制上防止幻觉
4. **证据与偏好分离**：interest_level 不改变客观证据状态

**对现有 agent 的改造建议：** 在 Candidate KB 层引入证据状态标签，在 Resume×JD Match 中用 Matched/Missing/Unknown 替代简单的 ✅/❌，在 Predicted Questions 中对每个题目的事实基础标注证据状态。

#### 🥈 第二名：wanyichen06/LLMInternSkill — 证据守卫 + 补证据闭环

**推荐理由：**
它的 Evidence Guard 和 Project Scout 形成了"发现缺口→补证据→再写入"的完整闭环，这是现有 agent 缺少的关键环节。现有 agent 标注了缺口但没有告诉用户怎么补。

具体可借鉴：
1. **五级真实性判断**（可以写/谨慎写/补证据后写/不能写/无法判断）：比 my-interview 的"标注缺口"更精细
2. **Project Scout 补证据计划**：当简历某方向太弱时，推荐具体开源项目去复现，给出最小运行路径、改造点、完成后可写的简历 claim、以及面试会追问什么
3. **面试拷打三档回答**（危险/及格/强）：比 my-interview 的"回答框架"更有教学价值
4. **岗位方向专项检查清单**：10+ 个 LLM 细分方向各有重点检查项

**对现有 agent 的改造建议：** 在 Phase 2（Resume×JD Match）后增加"补证据计划"环节，对每个 GAP 给出具体的补强路径；在 Phase 4（Draft Answers）中增加危险/及格/强三档对比。

#### 🥉 第三名：XUZIAa/Multi-Agent-Mock-Interview — 双环架构解决模拟面试漂移

**推荐理由：**
如果现有 agent 要强化模拟面试环节，这个项目的双环架构是最值得参考的工程方案。它系统性地解决了"AI面试官聊几轮就忘了自己是谁"的核心痛点。

具体可借鉴：
1. **Fast Loop / Slow Loop 双环分离**：实时语音只负责"口"，后端状态机负责"脑"，人格每轮重新注入而非依赖记忆
2. **InterviewState 权威状态**：追踪考察维度/已问题目/JD覆盖度/STAR完整度/追问深度/漂移分数，每轮落盘可恢复
3. **防漂移三道防线**：人设契约 + 输出守卫 + 心跳重锚
4. **硬时间闸门**：到点强制收尾，不给模型自由裁量

**对现有 agent 的改造建议：** 如果要将模拟面试从纯文本升级为交互式（甚至语音），引入 Slow Loop 状态机来追踪面试进度，而非完全依赖 LLM 的上下文记忆。

### 各环节最佳实践汇总

| 环节 | 最佳参考项目 | 可借鉴的具体设计 |
|------|-------------|----------------|
| Candidate KB | japan-career-agent | 证据状态标签（Confirmed/Unknown/Contradictory）+ 用户审批门 |
| Company Research | my-interview（基线） | 5维度搜索 + 加权评分 + HIGH/MEDIUM/LOW/GAP 置信度 |
| 面经采集 | FindJobs-Agent | 结构化爬虫 + 标准化管道 + SQLite 持久化 |
| Resume×JD Match | LLMInternSkill + japan-career-agent | 五级真实性判断 + Matched/Missing/Unknown 三态 + 硬冲突不平均 |
| 题目预测分级 | my-interview（基线） | P0-P4 五级优先级 + 三类必出题方法论 + 透明度标注 |
| 答案草稿 | LLMInternSkill + interview-skills | 危险/及格/强三档对比 + 好答案vs差答案模板 |
| STAR故事库 | my-interview（基线） | 15类能力标签 + gaps 分析 + 自动匹配算法 |
| 模拟面试交互 | Multi-Agent-Mock-Interview + 面面 | 双环架构防漂移 + LangGraph状态机 + 实时语音 |
| 面试评估 | 面面 + Multi-Agent | 5维能力雷达 + 韵律分析 + 逐字稿批注 + 满分答案重构 |
| 补证据闭环 | LLMInternSkill | Project Scout + 1天/3天/1周补证据计划 |
| HR话术 | my-interview（基线） | 去AI味9项检查清单 + 5场景话术 |
| 投递追踪 | Lujie-Careerkit | 看板式管道 + 简历版本关联 + 跟进提醒 |
| 模块化封装 | Lujie-Careerkit | 4个独立Agent Skill拆分，而非单一大Skill |

### 对现有 agent 改造的优先级建议

1. **高优先级（核心差异化）**：引入 japan-career-agent 的证据状态模型，在 Candidate KB 和 Resume×JD Match 中落地
2. **高优先级（补齐闭环）**：引入 LLMInternSkill 的 Project Scout 补证据计划，在 Match 阶段后增加"缺口怎么补"
3. **中优先级（提升教学价值）**：在 Draft Answers 中增加危险/及格/强三档对比，参考 interview-skills 的好/差答案模板
4. **中优先级（模拟面试升级）**：如要做交互式模拟面试，参考 Multi-Agent-Mock-Interview 的 Slow Loop 状态机
5. **低优先级（下游扩展）**：参考 Lujie-Careerkit 增加投递追踪和素材库管理
6. **持续保持**：my-interview 的 P0-P4 题目分级、置信度标注、去AI味话术是现有骨架的强项，应继续强化

---

> 报告生成时间：2026-09-04
> 数据来源：GitHub API + 各项目 README/SKILL.md 原文
> 调研项目数：基线 1 个 + 对比项目 7 个
