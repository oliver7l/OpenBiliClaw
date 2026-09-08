# 两参考项目深度研究 × 求职备战 Agent 改造蓝图

> 生成日期：2026-09-04
> 研究对象：`H-Wren/my-interview`（面试备战流水线 Agent） + `Zchary1106/agent-interview-hub`（面经知识库 + Interview Collector 采集 Agent）
> 目的：作为把已配好的 CLI 工具矩阵升级为"自主智能求职备战 Agent"的施工图。源码已 clone 至本目录（`my-interview/`、`agent-interview-hub/`），可随时对照。

---

## 一、项目速览

| 维度 | H-Wren/my-interview | Zchary1106/agent-interview-hub |
|---|---|---|
| 定位 | **面试备战 Agent**（全流程：调研→匹配→出题→模拟→评估） | **面经知识库 + 面经采集 Agent**（搜集→去重→评分→入库） |
| 形态 | 单个 Codex/Claude Skill（SKILL.md 单文件 + 3 个 references + agents 配置） | 静态站仓库 + `collect_interviews.py` 采集流水线 + `interview-collector` 跨平台 Agent 规范 |
| 许可证 | MIT | MIT |
| 核心资产 | 8-Phase 备战流水线 + P0-P4 出题法 + STAR 评估 + Storybank + 去AI味话术 | Candidate 结构化 schema + 自动打标签/评分/去重 + 公司三合一知识库组织 |
| 语言 | 中英双语 | 中文为主 |

**一句话结论**：`my-interview` 提供"**把一个人/一份简历武装到能面任何公司**"的方法论骨架；`agent-interview-hub` 提供"**用 CLI 从全网持续沉淀面经素材**"的工程化实现。两者天然互补：一个管"怎么准备"，一个管"素材从哪来、怎么存"。改造后的 Agent 应是二者的合体 + 用户已有的 CLI 工具矩阵 + 用户自己的三层知识库。

---

## 二、H-Wren/my-interview 深度拆解（"怎么准备"）

### 2.1 命令体系

| 命令 | 作用 |
|---|---|
| `/interview` 或自然语言 | 启动完整备战流水线（一次跑完 8 Phase） |
| `/mock {slug}` | 基于已有 prep 做模拟面试 |
| `/storybank` (add/list/gaps) | STAR 故事库管理：添加、按能力标签列出、查缺口 |
| `/hype {slug}` | 面试前信心简报（一周前/前一天/当天早上三档） |
| `/debrief {slug}` | 面试后复盘：真实题目回填 `[REAL]` 标记 |
| `/update-prep {slug}` | 新情报增量合并（带版本管理） |
| `/greet` | HR 打招呼话术生成（冷投/内推/Gap回应/感谢信） |
| `/list-preps` | 列出所有已生成 prep |

### 2.2 8-Phase 流水线（本 Agent 的骨架）

```
Step 0  Intake        → 目标公司/职位/JD(5种方式)/面试轮次/简历
Phase 0 Candidate KB  → 三段式：基本画像 / 核心竞争力 / 可复用案例库(STAR)
Phase 1 Company Research → 5 维度搜索 + 加权评分 + 交叉验证置信度
Phase 1.5 Product Deep-Dive → 条件触发（JD/公司重心在具体产品时）
Phase 2 Resume×JD Match → 逐项匹配✅❌⚠️ + 不清晰点标记 + 改写建议(before/after)
Phase 3 Predicted Questions ⭐ → P0-P4 五档优先级 + 三类必出题
Phase 4 Draft Answers → 仅用真实事实 + 诚实标注缺口
Phase 5 Mock Interview → 6-8题/30-45min + 公司风格适配
Phase 6 STAR Evaluation → (S+T+A×2+R×2)/6 + 技术题4维度
```

### 2.3 关键设计原则（必须继承）

1. **所有输出基于真实材料** — 禁止编造经历/指标；缺口诚实标注「此处需你补充」
2. **信息标注置信度** — HIGH（多源一致）/ MEDIUM（有限佐证）/ LOW（单源猜测）/ GAP（无信息）
3. **Resume 是事实基准** — 简历/知识库/你说过的话是唯一可用的个人经历来源
4. **反馈闭环** — 模拟面试与真实面试结果都回填改进后续预测
5. **文件优先输出** — 全部产出写 `preps/{slug}/full-prep.md`，对话框只显示进度摘要；`>>` 追加写入
6. **自动执行不中途提问** — 信息不足时诚实标注「此处信息不足，请补充」，运行完再汇报
7. **版本管理** — `preps/{slug}/versions/v{时间戳}/`，保留最近 10 版，支持回滚

### 2.4 Phase 1 公司调研：5 维度 + 评分公式（重要）

**5 个搜索维度**：
1. 业务基本面（商业模式/财报/客户/市场/竞对）
2. 近期动态（过去 6-12 个月：裁员招聘/新产品/战略/高管）
3. 职场口碑（小红书/知乎/脉脉 site: 搜索 / Glassdoor / Blind）
4. **面经深度挖掘 ⭐**（精确到岗位：`[公司] [完整岗位名] 面经`；多平台交叉）
5. LeetCode/技术面（技术岗）

**结果评分**（每条 1-5 分三项加权）：
```
综合分 = 新近度×0.3 + 来源可信度×0.4 + 相关性×0.3
新近度：6个月内=5, 1年内=4, 2年内=3, 更老=1
可信度：一手经验=5, 多源交叉=4, 单篇分享=3, 匿名=2
相关性：精确匹配岗位=5, 同公司不同岗=3, 泛行业=1
低于 2.5 丢弃；交叉验证 → HIGH(≥50%独立源一致)/MEDIUM(25-49%)/LOW(<25%)/GAP(无)
```

### 2.5 Phase 3 出题方法论 ⭐（本 Agent 最核心价值）

**题目来源与优先级**：
```
P0 🔴 面经中出现的真实题目（标注来源平台+时间）
P1 🟠 JD 核心技术/经验要求推导
P2 🟡 简历薄弱点追问（来自 Phase 2 Gap 分析）
P3 🟢 公司业务/产品相关（来自 Phase 1/1.5）
P4 🔵 通用 Behavioral
```

**数量控制**：电面/HR面 5-7 题（P0≥1, P2≥1）；技术/产品面 8-10 题（P1≥3, P3≥2）；Behavioral/Onsite 12-15 题；终面 6-8 题（P3/P4 为主）

**三类必出题**：
1. **简历矛盾题**（P2 为主）— 扫描 title 与实际职责不符/时间线异常/前后描述不一致/行业黑话/「我们」而非「我」/缺转型动机
2. **转型/缺口题**（P1/P2）— 转型动机、结果数据缺失、技能熟练度模糊、离职原因；诚实标注哪些是框架、哪些需用户填
3. **经历深挖题**（P1）— 从简历最核心成就深挖操作细节，防「只用了工具就说深度集成」

**每道题的输出格式**（可背诵、可追踪）：
```
[P0-P4] · [类型] · [难度 ⭐-⭐⭐⭐]
问题：完整问题文本
▸ 面试官想了解什么：一句话点明
▸ 为什么这题会出现：标注信息来源（简历原文/推断/无来源/需补充）
▸ 回答框架：基于简历事实的逻辑链（标注☑️基于简历/⚠️推断/❌需自己填）
▸ Follow-up 预判：若A则追问…/若B则追问…
▸ ⚠️ 你需要注意：哪些部分简历不支持、缺什么数据、需自己确认什么
▸ 建议用时 + 来源 Phase
```

**透明度规则**：每条预测题必须标注来源与可信度，绝不替用户编经历。

### 2.6 STAR 评估与 Storybank（弹药库）

**STAR 评分** `(S + T + A×2 + R×2) / 6`，维度细则见 `references/star-framework.md`：
- S 情境（背景清晰）/ T 任务（个人角色目标明确）/ A 行动（具体步骤+个人贡献+决策）/ R 结果（量化+反思）
- 高分模板（领导力题/失败题）+ 常见错误清单（S/T混说、全程「我们」、无数、超3分钟、无反思、编造）

**Storybank 15 类能力标签**（`references/competency-taxonomy.md`）：
`领导力/冲突/失败/团队协作/客户至上/创新/沟通/技术决策/mentorship/模糊地带/Ownership/影响力/优先级/适应力/道德困境`
- 每个故事打标签 + strength 评分（≥4 优先推荐）
- Behavioral 问题自动匹配：问题关键词→标签→推荐强匹配高分故事；无强匹配则退而标注「建议补充」

### 2.7 公司风格适配（`references/company-culture-tags.md`）

| 公司类型 | 面试风格 | 追问特点 |
|---|---|---|
| FAANG/外企 | 结构化有评估表 | 追问 Action 细节「能具体说说什么吗」 |
| Startup | 随意但深入 | 追问决策过程「当时为什么选这个方案」 |
| 国内大厂 | 直接高效 | 追问量化「数据多少」 |
| 银行/金融 | 正式层级分明 | 追问方法论「你的分析框架是什么」 |
| 咨询 | 案例驱动 | 直接给案例题 |

### 2.8 Greet：去 AI 味话术（9 项检查清单 + 说人话规则表）

5 场景：冷投附言/HR 初筛开场/内推介绍/Gap 回应/感谢信。
检查清单：无开场套话/无空总结/无二元对比/无商业黑话/无过度接住/有具体匹配点/无翻译腔/无 AI 收尾/信息保真。
说人话速查：`「很高兴有机会向您推荐自己」→「你好，我申请了XX岗位，简单说下匹配点：」` 等 7 条反模式映射。

---

## 三、agent-interview-hub 深度拆解（"素材从哪来、怎么存"）

### 3.1 知识库组织（公司三合一，可直接照搬结构）

每家公司一个目录，3 份文档：
- **`岗位要求.md`** — 招聘方向/核心要求（学历/技术栈/工程能力）/薪资/参考链接
- **`面试题与面经.md`** — 每问带「💡 思考逻辑」+ 深度参考答案（公司业务导向，如百度题都挂搜索场景）
- **`真实面经-网络实录.md`** — 一手面经原文

另有 `通用知识/`（八股文/核心概念/RAG/Agent/MCP/系统设计/学习路线图）独立组织，与公司面经分离。

### 3.2 Interview Collector Agent（`agents/interview-collector/AGENT.md`）

**行为边界**（必须继承）：
- 只采公开来源；**不绕过登录墙/付费墙/反爬**
- **不向用户要 cookie/凭证**；登录态平台（小红书等）由用户浏览器登录 + 本地工具读取
- 产出的是"候选"（candidate），由人/Agent 审核后才入库——**不盲目改写知识库**

**8 步采集工作流**：
```
1. 构建定向查询（公司×主题）→ 2. 多源搜索 → 3. 只读公开页/搜索结果
→ 4. 提取结构化字段 → 5. 去重 → 6. 评分 → 7. 输出 JSON 候选 + MD 摘要 → 8. 验证一致性
```

**Candidate 输出 schema**（改造成数据契约）：
```json
{
  "id": "stable-kebab-id",
  "platform": "牛客",
  "title": "面经标题",
  "company": "字节跳动",
  "role": "AI Agent开发",
  "published_at": "2026-05-20",
  "source_url": "https://...",
  "source_note_id": null,
  "source_lookup": null,
  "score": 5,
  "topics": ["Agent", "RAG", "MCP"],
  "summary": "一到两句摘要，不能大段复制原文。"
}
```

**小红书特例**：不用不稳定 `search_result/<id>` 直链 → `source_url: null` + `source_note_id` + `source_lookup: "小红书站内搜索原标题：…"`

### 3.3 collect_interviews.py 工程实现（可直接借鉴/改造）

**自动打标签**：
- 公司关键词 21 个，简写归一化：`字节→字节跳动`、`阿里→阿里巴巴`、`DeepMind→Google`
- 主题关键词 13 类：Agent/RAG/MCP/Function Calling/Multi-Agent/LangGraph/Memory/Rerank/BM25/LoRA-SFT/Evaluation/Inference/Safety
- `detect_company(text)` + `detect_topics(text)` 从「标题+摘要+查询词」自动打标

**评分逻辑**（基础 3 分，累加，封顶 5）：
```
+1 命中公司名  +1 发布在 2025-2026  +1 命中 ≥3 个主题
+1 平台为牛客/小红书  +1 GitHub 且标题含面试/interview/Agent/RAG/LLM
```

**去重逻辑**（按 key 取 score 高者）：
```
key = source_url | xhs:note_id | title:platform:normalize_title(标题)
```

**双输出**：
- `interview_candidates.json`（原始候选，供审核）
- `interview_candidates.md`（按平台分组的可读报告：分数/公司/标题/来源/摘要/标签）

**命令**：`doctor`（工具自检）/ `search --platform nowcoder,zhihu,blogs,github,xiaohongshu --query … --append`（追加去重）/ `rss --feed …` / `render`

**质量校验**（`validate_data.py`，可作交付前的数据门禁）：
- 必须字段 id/platform/title/score；id 唯一；source_url 唯一且非空时不重复
- score 必须 1-5 整数；topics 必须是 list；**禁止存小红书不稳定直链**

---

## 四、两项目对比与可借鉴点汇总

| 能力 | my-interview | agent-interview-hub | 改造后的 Agent 取法 |
|---|---|---|---|
| 简历→画像/案例库 | ✅ Phase 0 三段式 | ❌ | 取 my-interview Phase 0 |
| 公司调研 | ✅ 5 维度+加权评分 | 采集辅助 | 取 my-interview 方法 + 用采集脚本补素材 |
| 面经采集 | 仅搜索 | ✅ 完整 pipeline | 取 agent-interview-hub（适配用户 CLI） |
| 出题预测 | ✅ P0-P4 + 三类必出题 | 题库参考 | 取 my-interview 全量 |
| 答案起草 | ✅ 仅真实事实 | ❌ | 取 my-interview |
| 模拟面试 | ✅ 风格适配 | ❌ | 取 my-interview |
| STAR 评估 | ✅ 加权公式 | ❌ | 取 my-interview |
| 故事库 | ✅ 15 类标签 | ❌ | 取 my-interview |
| 话术/去AI味 | ✅ Greet | ❌ | 取 my-interview |
| 面经结构化 | ❌ | ✅ Candidate schema | 取 agent-interview-hub |
| 数据校验 | ❌ | ✅ validate_data.py | 取 agent-interview-hub |
| 版本/回滚 | ✅ | ❌ | 取 my-interview |

**结论**：主流程 100% 取 my-interview 骨架；面经素材采集/结构化/校验 100% 取 agent-interview-hub；两者通过"用户已有的 CLI 工具矩阵 + 三层知识库 + 简历"缝合。

---

## 五、改造蓝图（求职备战 Agent 设计）

### 5.1 目标形态

一个**可自主、智能运行的求职备战 Agent**，具备四种能力并闭环：
1. **情报采集**（agent-interview-hub 模式）：用已配好的 CLI（zhihu-toolkit/xhs-cli/bili-cli/weibo-cli/zget/OpenCLI）低频串行采面经→结构化候选→评分去重→审核入库
2. **备战流水线**（my-interview 模式）：Intake→画像→公司调研→JD匹配→P0-P4 出题→答案→模拟→STAR 评估
3. **弹药库**（Storybank）：从用户简历/知识库/历史资料提炼可跨岗位复用的 STAR 故事，15 类能力标签
4. **知识补充**（CLI 知识获取）：按岗位方向用 CLI 采技术文章→考点卡→入 02_方向知识库

### 5.2 与用户现有资产映射

| 改造后组件 | 来源 | 用户现状 |
|---|---|---|
| 候选人知识库 | my-interview Phase 0 | 简历 `01_原始资料库/简历/童力-大于无限-广告算法工程师_v2.md`（已有多版本）→ 提炼 STAR 弹药库 |
| 公司调研 | my-interview Phase 1 | 已有 5 家目标公司（大宇/比亚迪/乐趣/万声/GoodLuck），情报已落 `03_岗位弹药库/` |
| 面经采集 | agent-interview-hub + 用户 CLI | zhihu-toolkit（读正文）/xhs-cli/bili-cli/weibo-cli/zget/OpenCLI 全可用 |
| 题库/知识 | agent-interview-hub 通用知识 | `02_方向知识库/`（广告算法/推荐/数据科学）已沉淀 |
| 输出目录 | my-interview preps/{slug} | `03_岗位弹药库/<公司>-面试准备/` |

### 5.3 流水线设计（合并版）

```
【采集层】CLI 工具矩阵 → Candidate schema（自动打公司/主题标签+评分+去重）
              ↓ 审核
        面经库 interviews.json + 公司三合一文档
              ↓
【备战层】Intake → Candidate KB(画像+STAR案例库)
              ↓
        Company Research(5维度+置信度) → [Product Deep-Dive]
              ↓
        Resume×JD Match(逐项+不清晰点+改写建议)
              ↓
        Predicted Questions(P0-P4 + 三类必出 + 来源标注)
              ↓
        Draft Answers(仅真实事实+缺口标注) → 分面试官类型分组
              ↓
        Mock Interview(风格适配) → STAR 评估((S+T+A×2+R×2)/6)
              ↓
        Prep 输出 preps/{slug}/full-prep.md（版本管理，保留10版）
              ↓
【闭环】Hype(面试前信心简报) → Debrief(复盘回填[REAL]) → update-prep(增量合并)
【话术】Greet(HR 打招呼 5 场景 + 去AI味检查)
```

### 5.4 落地产物清单

| 文件 | 内容 | 依据 |
|---|---|---|
| `SKILL.md` 升级 | 双流水线（采集+备战）+ 命令体系 + 防风控铁律 | my-interview 命令表 + agent-interview-hub 工作流 + 用户既有 SKILL.md |
| `references/evidence-model.md` | 置信度 HIGH/MEDIUM/LOW/GAP + 5 维度评分公式 + 每题透明度规则 | my-interview Phase 1/3 |
| `references/interview-pipeline.md` | 8-Phase 详细执行规范 + P0-P4 + 三类必出题 + 每题输出模板 | my-interview 全量 |
| `references/star-framework.md` | STAR 评分细则 + 15 类能力标签 + 高分模板 + 常见错误 | my-interview references |
| `references/company-style.md` | 6 类公司面试风格 + 追问特点 + 模拟开场 | my-interview company-culture-tags |
| `references/candidate-schema.md` | 面经 Candidate schema + 评分/去重/小红书特例 + 公司三合一文档模板 | agent-interview-hub AGENT.md + collect_interviews.py |
| `scripts/check_tools.sh` | CLI 工具环境自检 | 用户工具矩阵（zhihu/xhs/bili/weibo/zget/opencli） |
| `scripts/interview_collect.py` | 面经采集：CLI 调用→Candidate→评分去重→JSON+MD 双输出 | agent-interview-hub collect_interviews.py 改造适配用户 CLI |

### 5.5 与用户既有 job-intel-agent Skill 的关系

- 现有 `job-intel-agent`（.user_skills）保留为"采集 Skill 层"，本轮按上述蓝图升级其 SKILL.md + 新增 references + 补齐 scripts（当前 `scripts/check_tools.sh` 被引用但缺失）。
- 独立 Agent（OrganizerAgent o_0001EnrvXgK）后台运行时，以升级后的 Skill 为操作手册，自主完成"采集→备战→弹药库→知识"闭环。
- 最终对用户呈现为一个可随时召唤的"求职备战智能体"：说"准备 X 公司 X 岗面试"即自动跑完整流水线；说"再采一波 X 面经"即触发采集层。

---

## 六、待办与风险

- 本蓝图基于两个仓库 **README + SKILL.md + 3 个 references + AGENT.md + collect_interviews.py + validate_data.py + interviews.json 样例** 的完整研读，已全部 clone 到本目录可对照。
- `agent-interview-hub/scripts/build_site.py`（62KB 静态站构建）与本 Agent 目标无直接关系，不迁移；`通用知识/` 题库内容后续按需引用。
- 用户尚未提供的平台凭证（X/Reddit/V2EX）不影响主流程（优先用已打通 6 大平台）。
- 采集合规红线不变：只读、低频串行、间隔 ≥6-10 秒、单轮 6-10 次封顶、遇 403 不绕过换通道。
