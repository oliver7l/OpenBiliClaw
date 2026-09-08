# GitHub 类似 Agent 项目调研：多智能体框架与知识库驱动架构

> 调研日期：2026-09-04
> 调研目标：为现有"求职备战 Agent"（候选人知识库→公司调研→简历匹配→题目预测→答案生成→备战包）寻找可借鉴的架构设计模式
> 运行环境约束：本地沙箱 + CLI 工具 + 本地文件知识库，优先关注可本地运行、轻量级、与文件系统/CLI 交互友好的框架

---

## 一、多智能体编排框架对比总表

| 维度 | LangGraph | CrewAI | AutoGen / AG2 | MetaGPT | Dify |
|---|---|---|---|---|---|
| **GitHub** | langchain-ai/langgraph | crewAIInc/crewAI | microsoft/autogen（维护模式）<br>ag2ai/ag2（社区活跃分支） | FoundationAgents/MetaGPT | langgenius/dify |
| **Star 数** | ~30K | ~50K | ~57K（原仓库）<br>~5K（AG2） | ~68K | ~100K+ |
| **最近更新** | 极活跃（v1.x 持续迭代） | 极活跃（v1.x） | 原仓库维护模式；AG2 活跃 | 活跃（重心转向 MGX 产品） | 极活跃 |
| **核心架构范式** | 图状态机（StateGraph） | 角色+任务+团队（Crew） | 对话式协作（GroupChat/Team） | SOP 驱动的模拟软件公司 | 可视化 DAG 工作流平台 |
| **状态管理** | 内置 Checkpointer，每步自动持久化，支持 thread_id 隔离、时间回溯、分支 | 任务间通过 context 传递，无内置检查点 | 对话历史即状态，支持 memory 模块 | 共享消息池（Message Pool）+ 环境上下文 | 工作流变量 + 节点间数据传递 |
| **工具调用** | ToolNode 标准化，支持并行调用、人工审批 | Agent 绑定 tools，支持动态分配 | Function calling 原生支持，MCP 集成 | Action 类封装工具调用 | 50+ 内置工具 + 自定义 HTTP/Code 节点 |
| **编排模式** | 节点+边+条件边，任意 DAG/循环 | Sequential / Hierarchical / Consensual | RoundRobin / Selector / Swarm / Nested | 固定 SOP 流水线（PRD→设计→编码→测试） | 拖拽式 DAG，支持 IF-ELSE、Loop、迭代 |
| **人机交互** | interrupt_before/after，动态 interrupt()，状态可编辑 | 有限，需自定义 | 支持人工介入对话 | 有限 | 人工审核节点 |
| **可恢复性** | 强：检查点+时间回溯+故障恢复 | 弱：无内置持久化 | 中：对话历史可持久化 | 弱 | 中：工作流执行记录 |
| **技术栈** | Python / JS-TS | Python | Python / .NET | Python | Python(FastAPI) + React + Docker |
| **部署形态** | 纯代码库，本地运行 | 纯代码库，本地运行 | 纯代码库，本地运行 | 纯代码库，本地运行 | 平台型，Docker 自托管 |
| **学习曲线** | 高（图抽象+状态管理概念多） | 低（角色/任务隐喻直观） | 中 | 中 | 低（可视化） |
| **本地友好度** | ★★★★★ | ★★★★★ | ★★★★★ | ★★★★☆ | ★★★☆☆（需 Docker 全套） |

---

## 二、知识库 / RAG 驱动 Agent 架构对比总表

| 维度 | LightRAG | LlamaIndex | Mem0 | GPT Researcher | agent-interview-hub |
|---|---|---|---|---|---|
| **GitHub** | HKUDS/LightRAG | run-llama/llama_index | mem0ai/mem0 | assafelovic/gpt-researcher | Zchary1106/agent-interview-hub |
| **Star 数** | ~38K | ~48K | ~45K | ~21K | 小型项目（百级） |
| **最近更新** | 极活跃 | 极活跃（v0.14+） | 活跃 | 活跃 | 活跃（2026-08 更新） |
| **核心定位** | 图增强 RAG 框架 | RAG-First 数据框架 | AI Agent 长期记忆层 | 自主研究 Agent | 面经采集+结构化知识库 |
| **知识组织方式** | 知识图谱（实体+关系）+ 向量索引双轨 | Document→Node→Index，支持向量/图谱/关键词多索引 | 结构化记忆条目（事实/偏好/经历）+ 图谱增强 | 搜索结果→摘要→聚合报告 | 公司目录 + 通用题库 + JSON 索引 |
| **检索策略** | 双层检索：Local（实体邻域）+ Global（社区摘要） | 路由检索：向量/关键词/图谱/混合，支持子问题分解 | 语义检索 + 图谱关联检索 | 多查询并行搜索 + 爬虫抓取 + 来源追踪 | 关键词匹配 + 目录结构导航 |
| **记忆机制** | 文档级增量更新，4 种存储（KV/Vector/Graph/DocStatus） | 无原生长期记忆，需外接 | 核心能力：提取→评估→更新两阶段流水线，跨会话持久化 | 无长期记忆，单次研究 | 面经候选 JSON 持久化，人工审核后回填 |
| **Agent 编排** | 无（纯 RAG 框架） | Workflow 事件驱动编排（@step）+ FunctionAgent/AgentWorkflow | 无（记忆中间件，可嵌入任意 Agent） | Planner→Execution(Crawler)→Publisher 三角色 | Interview Collector Agent（单 Agent 多源采集） |
| **工具/数据源** | 多种存储后端（Faiss/Chroma/Milvus/Neo4j/PG 等） | 300+ LlamaHub 连接器（PDF/API/SQL/Notion 等） | 多存储引擎（向量+图+KV 混合） | Tavily/Serper 搜索 + 网页爬虫 | 牛客/小红书/知乎/CSDN/GitHub 等公开来源 |
| **输出质量控制** | RAGAS 评估集成 + Langfuse 追踪 | 评估模块（Faithfulness/Relevancy） | 记忆准确性评估（LOCOMO 基准 +26%） | 来源追踪 + 引用标注 | 人工/Agent 审核候选后才入库 |
| **技术栈** | Python | Python / TS | Python | Python | Python + Markdown + GitHub Pages |
| **本地友好度** | ★★★★★（纯 Python，可选本地存储） | ★★★★★ | ★★★★☆（部分功能需云端） | ★★★★☆（需搜索 API） | ★★★★★（纯本地文件） |
| **与求职 Agent 契合度** | ★★★★☆（图谱适合公司/岗位关系建模） | ★★★★★（Workflow + RAG 全覆盖） | ★★★★☆（候选人画像长期记忆） | ★★★★★（公司调研环节直接复用） | ★★★★★（面经采集思路完全对齐） |

---

## 三、各项目详细分析

### 方向 A：多智能体编排框架

---

#### A1. LangGraph — 图状态机编排

**基本信息**
- GitHub：https://github.com/langchain-ai/langgraph
- Star：~30K（2026 年中）
- 最近更新：极活跃，v1.x 稳定版持续迭代
- 技术栈：Python / JavaScript / TypeScript，MIT 协议

**核心架构设计**

LangGraph 的核心抽象是 `StateGraph`——一个以共享状态为中心的有向图。每个节点（Node）是一个 Python 函数，接收当前状态、返回状态更新；边（Edge）定义节点间的流转，支持条件边（Conditional Edge）实现分支和循环。

```
用户输入 → [检索节点] → 条件判断 → [生成节点] → [工具节点] → 循环/结束
                ↑_________________________|
```

**状态管理与检查点（最核心亮点）**
- 每执行完一个节点，Checkpointer 自动将完整图状态序列化保存
- 支持多种后端：MemorySaver（内存）、SQLite、PostgreSQL、Redis、Couchbase 等
- 通过 `thread_id` 隔离不同会话/运行
- `get_state_history()` 可回溯任意历史检查点，支持"时间旅行"调试
- 支持从检查点创建分支（branch_from_checkpoint）
- 系统崩溃后可从最后检查点恢复，实现 Durable Execution

**人机交互（HITL）**
- `interrupt_before=["node_name"]`：在指定节点执行前暂停
- `interrupt_after=["node_name"]`：在指定节点完成后暂停
- `interrupt()`：在节点内部基于运行时状态动态暂停
- 暂停期间可查看和编辑状态，恢复后继续执行

**工具调用**
- `ToolNode` 标准化工具执行，支持并行工具调用
- 工具调用结果自动写回状态
- 支持人工审批工具调用（结合 interrupt）

**可借鉴的设计模式**
1. **状态图 + 检查点**：长流程可中断可恢复，面试备战跑完公司调研后中断，人工确认后再继续
2. **条件边分支**：根据简历匹配度决定走"深度备战"还是"基础备战"分支
3. **动态 interrupt**：在答案生成后自动暂停，等待人工审核质量
4. **时间回溯**：对不满意的题目预测结果，回溯到上一检查点重新生成

**对求职备战 Agent 的改造启示**
- **整体编排层**：用 StateGraph 替代当前线性脚本，6 个环节变为 6 个节点，节点间通过条件边连接
- **公司调研环节**：可设计为并行子图（多个公司同时调研），结果汇总后进入下一节点
- **状态持久化**：每个环节完成后自动保存检查点到本地 SQLite，断网/崩溃后可恢复
- **质量门控**：在"答案生成"节点后插入 interrupt，人工审核后再进入"备战包组装"
- **工具标准化**：将现有 xhs-cli / bili-cli / zhihu-cli 等封装为 ToolNode，统一调用接口

**局限性 / 引入成本**
- 学习曲线陡峭：StateGraph、Reducer、Checkpointer、Interrupt 等概念需要时间消化
- 与 LangChain 生态绑定较深（虽然可独立使用）
- 纯代码方式，无可视化编排界面
- 引入需要重构现有脚本结构，预计 3-5 天迁移成本

---

#### A2. CrewAI — 角色驱动的团队协作

**基本信息**
- GitHub：https://github.com/crewAIInc/crewAI
- Star：~50K（2026 年中，角色类框架中最高）
- 最近更新：极活跃，v1.x
- 技术栈：Python，MIT 协议

**核心架构设计**

CrewAI 用"公司团队"隐喻组织多 Agent 系统，核心三层抽象：

- **Agent（员工）**：定义 role（角色）、goal（目标）、backstory（背景）、tools（工具）
- **Task（任务）**：定义 description（描述）、expected_output（期望输出）、agent（执行者）、context（依赖的前置任务输出）、tools（任务级工具覆盖）
- **Crew（团队）**：组织 agents + tasks，定义 process（执行策略）

**编排模式（Process）**
1. **Sequential（顺序执行）**：任务按列表顺序依次执行，前一个任务的输出自动作为后一个的 context
2. **Hierarchical（层级执行）**：指定 manager_llm 或 manager_agent，由管理者动态分解任务、委派给合适的 Agent、验证结果后再推进
3. **Consensual（共识执行）**：多个 Agent 对结果投票达成共识（实验性）

**状态与上下文管理**
- 任务间通过 `context` 参数显式传递前置任务输出
- 支持 `output_file` 将任务结果保存为文件
- 无内置检查点/持久化机制（v1.x 部分改进）
- 支持 Memory（短期/长期/实体记忆），但不如 LangGraph 成熟

**工具调用**
- Agent 级绑定 tools，Task 级可覆盖
- 支持自定义工具（BaseTool 类）
- 支持 CrewAI 内置工具集（搜索、文件读写、代码执行等）
- 支持 MCP 工具集成

**可借鉴的设计模式**
1. **角色 + 任务委派**：将"公司调研员""简历匹配师""题目预测官""答案撰写人"定义为不同角色的 Agent，每个角色有专属 prompt 和工具
2. **Sequential 流水线**：天然适配求职备战的线性流程（调研→匹配→预测→生成→组装）
3. **Hierarchical 管理者模式**：设置一个"备战总监"Agent，负责任务分解和质量把控，适合复杂岗位的深度备战
4. **Task context 传递**：前一步输出自动作为后一步输入，减少手动状态管理
5. **output_file 持久化**：每个任务结果自动存为文件，天然适配本地文件知识库

**对求职备战 Agent 的改造启示**
- **角色化拆分**：现有单一大脚本拆分为 4-5 个角色 Agent，每个角色专注一个环节
- **任务定义标准化**：每个 Task 明确 expected_output（如"公司调研报告.md"），输出质量可验证
- **Hierarchical 模式用于复杂岗位**：对于百度程序化广告这类复杂岗位，设置 manager Agent 协调调研员+匹配师+预测官
- **工具按角色分配**：调研员绑定 xhs-cli/zhihu-cli，匹配师绑定文件读取工具，预测官绑定题库检索工具
- **output_file 直接写入知识库**：每个 Task 的 output_file 路径指向本地知识库对应目录

**局限性 / 引入成本**
- 无内置检查点：长流程中断后需从头开始（或自行实现文件级检查点）
- 状态管理能力弱于 LangGraph，复杂分支/循环处理不灵活
- Hierarchical 模式的 manager 决策质量依赖 LLM，可能不稳定
- 引入成本中等：1-2 天可搭建基础 Crew，但角色 prompt 调优需要时间

---

#### A3. AutoGen / AG2 — 对话式多 Agent 协作

**基本信息**
- GitHub（原）：https://github.com/microsoft/autogen （~57K Star，已进入维护模式）
- GitHub（社区分支）：https://github.com/ag2ai/ag2 （~5K Star，活跃）
- 后继：Microsoft Agent Framework（2026 年 4 月 GA）
- 技术栈：Python / .NET，Apache 2.0（AG2）

**核心架构设计**

AutoGen 将多 Agent 协作建模为**对话**。Agent 之间通过互发消息协作，核心抽象：

- **ConversableAgent**：可对话的 Agent 基类，支持发送/接收消息、注册回复函数
- **AssistantAgent**：LLM 驱动的助手，可调用工具
- **UserProxyAgent**：代表用户执行代码、调用工具、人工输入
- **GroupChat**：多 Agent 群聊，管理者决定下一个发言者
- **Team（AG2 新概念）**：替代 GroupChat，实现更灵活的协作模式

**协作模式**
1. **Two-agent chat**：两个 Agent 直接对话（如 Planner + Executor）
2. **Sequential chat**：多个 Agent 按顺序组成流水线
3. **GroupChat（RoundRobin）**：Agent 轮流发言，适合固定顺序
4. **GroupChat（Selector）**：LLM 选择下一个发言者，适合动态协作
5. **Swarm**：Agent 间通过 handoff 移交控制权
6. **Nested chat**：层级嵌套，外层 Agent 调用内层子对话

**状态管理**
- 对话历史即状态，每条消息携带上下文
- 支持 Memory 模块（短期/长期）
- AG2 支持状态持久化到数据库
- 无内置检查点/时间回溯

**工具调用**
- 原生 Function Calling 支持
- Code executor（沙箱执行 Python 代码）
- MCP 协议集成（AG2 原生支持）
- A2A 协议支持（Agent-to-Agent 互操作）

**可借鉴的设计模式**
1. **对话式协作**：题目预测官和答案撰写人可以通过对话反复打磨答案（预测官出题→撰写人答题→预测官追问→撰写人补充）
2. **UserProxyAgent**：代表用户执行 CLI 工具（xhs-cli 等），将工具输出返回给 LLM Agent
3. **Selector GroupChat**：动态选择下一步该哪个角色介入，适合面试备战中根据岗位复杂度动态调整流程
4. **Nested chat**：外层"备战总监"调用内层"公司调研子团队"（调研员+爬虫+审核员）完成子任务
5. **代码执行沙箱**：Agent 可直接写代码分析简历数据、生成统计图表

**对求职备战 Agent 的改造启示**
- **答案生成环节**：用"预测官↔撰写人"双 Agent 对话模式替代单次生成，通过多轮追问提升答案深度
- **工具执行层**：UserProxyAgent 统一封装所有 CLI 工具调用，LLM Agent 只负责决策，UserProxy 负责执行并返回结果
- **复杂岗位备战**：Nested chat 模式——外层总监 Agent 协调，内层为每个环节启动子对话团队
- **面试模拟**：GroupChat 模式——面试官 Agent + 候选人 Agent + 点评 Agent 三方对话，模拟真实面试

**局限性 / 引入成本**
- 原 AutoGen 已进入维护模式，新项目应考虑 AG2 或 Microsoft Agent Framework
- 对话式协作的 token 消耗较高（多轮对话上下文累积）
- 状态管理不如 LangGraph 精细，长流程可恢复性弱
- 对话方向不可控时容易陷入无效循环，需要设置 max_turns 或终止条件
- 引入成本中等：2-3 天搭建基础对话流

---

#### A4. MetaGPT — SOP 驱动的模拟软件公司

**基本信息**
- GitHub：https://github.com/FoundationAgents/MetaGPT（原 geekan/MetaGPT）
- Star：~68K
- 最近更新：活跃，但研发重心转向 MGX 商业化产品
- 技术栈：Python，MIT 协议

**核心架构设计**

MetaGPT 的核心理念是 **"Code = SOP(Team)"**——将标准操作流程（SOP）具体化，应用于由 LLM 组成的团队。它模拟一个完整的软件公司：

- **角色（Role）**：ProductManager（产品经理）、Architect（架构师）、ProjectManager（项目经理）、Engineer（工程师）、QAEngineer（测试工程师）
- **SOP 流水线**：用户需求 → PRD → 系统设计 → 任务分解 → 编码 → 测试
- **消息池（Message Pool / Environment）**：所有 Agent 共享的广播环境，Agent 订阅感兴趣的消息
- **角色注册表（Role Registry）**：管理角色的注册和发现

**关键机制**
- **标准化输出产物**：每个角色输出结构化文档（PRD 模板、设计文档模板、任务列表模板）
- **消息驱动**：角色通过发布/订阅消息协作，而非直接调用
- **CostManager**：预算控制和计费管理
- **Document**：统一的文档对象，支持序列化和版本管理

**可借鉴的设计模式**
1. **SOP 固化**：将求职备战的最佳实践固化为 SOP（公司调研 SOP→简历匹配 SOP→题目预测 SOP→答案生成 SOP），每个 SOP 有标准化输出模板
2. **标准化产物**：每个环节输出结构化文档（公司调研报告模板、匹配度评分表、题目预测清单、答案卡片模板），而非自由文本
3. **消息总线**：各环节通过发布/订阅消息解耦，调研员发布"公司调研完成"消息，匹配师订阅后开始工作
4. **角色技能矩阵**：每个角色有明确的 skills（调研员技能：多源搜索/信息去重/可信度评估；匹配师技能：JD 解析/技能对标/差距分析）

**对求职备战 Agent 的改造启示**
- **输出模板化**：为 6 个环节各设计一个标准化输出模板（参考 MetaGPT 的 PRD/设计文档模板），确保产物结构一致、可被下游消费
- **SOP 文档化**：将每个环节的操作步骤写成 SOP 文档，Agent 执行时严格遵循 SOP，减少随机性
- **消息解耦**：用文件系统模拟消息总线——每个环节完成后在指定目录写入"完成标记文件"，下游环节轮询检测后启动
- **角色 Profile**：为每个 Agent 定义结构化 Profile（name/goal/constraints/skills/tools），替代散乱的 prompt

**局限性 / 引入成本**
- 框架设计高度面向软件开发场景，直接用于求职备战需要大量定制
- 研发重心转向 MGX 商业产品，开源版更新可能放缓
- 消息池机制在本地单进程场景下价值有限
- 引入成本较高：需要抽象出求职领域的 SOP 和角色体系，预计 5-7 天

---

#### A5. Dify — 可视化工作流平台

**基本信息**
- GitHub：https://github.com/langgenius/dify
- Star：~100K+（2025 年 6 月突破 10 万）
- 最近更新：极活跃
- 技术栈：Python（FastAPI）+ React + Docker + PostgreSQL + Redis
- 部署：Docker Compose / Kubernetes 自托管

**核心架构设计**

Dify 是一个**平台型** LLM 应用开发平台，核心是可视化工作流引擎：

- **GraphEngine**：将用户定义的工作流解析为可执行 DAG
- **事件驱动调度**：GraphRunStartedEvent → NodeRunStartedEvent → NodeRunFinishedEvent，本地队列管理器分发节点执行
- **节点类型**：Start/End、LLM、IF-ELSE、Knowledge Retrieval（知识库检索）、Tool（工具）、HTTP Request、Loop（循环）、Variable Assigner（变量赋值）、Code（Python/JS 代码执行）、Agent（Agent 节点）
- **知识库**：内置 RAG 管道（文档上传→分块→向量化→检索），支持多种向量数据库

**可借鉴的设计模式**
1. **DAG 可视化编排**：工作流以 DAG 形式定义，节点间依赖清晰，支持并行分支
2. **Knowledge Retrieval 节点**：将知识库检索作为工作流中的一个标准节点，检索结果直接注入下游 LLM 节点的上下文
3. **Loop 节点**：支持迭代处理（如对多个公司循环执行调研）
4. **变量传递机制**：节点间通过工作流变量传递数据，每个节点有明确的输入/输出 schema
5. **Code 节点**：在工作流中嵌入 Python 代码，可直接调用本地 CLI 工具

**对求职备战 Agent 的改造启示**
- **DAG 思维**：即使不用 Dify 平台，也应将备战流程建模为 DAG——公司调研（并行）→ 汇总 → 简历匹配 → 题目预测 → 答案生成 → 备战包组装
- **知识库检索节点化**：将三层知识库（候选人/公司/题库）的检索封装为标准化"检索节点"，每个节点有明确的输入（查询）和输出（相关文档片段）
- **Loop 处理多公司**：对目标公司列表循环执行"调研→匹配→预测"，汇总后统一生成答案
- **变量 schema 化**：定义每个环节的输入/输出数据 schema（如 CompanyResearchResult、ResumeMatchScore、QuestionPrediction），用 Pydantic 模型约束

**局限性 / 引入成本**
- 平台型产品，需要 Docker + PostgreSQL + Redis 全套基础设施，与现有"本地脚本+文件"环境差异大
- 自定义 CLI 工具集成需要通过 HTTP 封装或 Code 节点，不够原生
- 知识库功能与现有本地文件知识库重叠，迁移成本高
- **不建议整体引入**，但 DAG 编排和节点化设计思路值得借鉴
- 引入成本：如整体部署需 1-2 天搭建环境，但与现有体系整合成本高

---

### 方向 B：知识库 / RAG 驱动的 Agent

---

#### B1. LightRAG — 图增强的双层检索 RAG

**基本信息**
- GitHub：https://github.com/HKUDS/LightRAG
- Star：~38K（EMNLP 2025 论文）
- 最近更新：极活跃
- 技术栈：Python，MIT 协议

**核心架构设计**

LightRAG 是香港大学提出的**图增强 RAG 框架**，核心创新是将知识图谱与向量检索结合，实现双层检索：

**索引构建三阶段**
1. **文档分块**：将文档切分为可管理的文本块
2. **实体/关系抽取**：用 LLM 从每个文本块中识别实体（人、组织、概念、日期等）和关系（边）
3. **知识图谱构建**：实体和关系组成知识图谱，同时为实体/关系/文本块生成向量嵌入

**四种存储**
- `KV_STORAGE`：LLM 响应缓存、文本块、文档信息（支持 JSON/文件）
- `VECTOR_STORAGE`：实体向量、关系向量、文本块向量（支持 Faiss/Chroma/Milvus/Qdrant/PG/Redis）
- `GRAPH_STORAGE`：实体关系图（支持 NetworkX/Neo4j/PG-AGE）
- `DOC_STATUS_STORAGE`：文档索引状态（支持增量更新）

**双层检索（核心亮点）**
- **Local 检索**：从查询中提取实体，在知识图谱中查找实体的邻域（直接关联的实体和关系），获取细粒度上下文
- **Global 检索**：基于图谱的社区检测，将相关实体聚类为社区，为每个社区生成摘要，查询时检索相关社区的摘要，获取全局视角
- Local + Global 结果合并后送入 LLM 生成答案

**可借鉴的设计模式**
1. **知识图谱建模**：将求职领域的实体（公司、岗位、技能、题目、候选人经历）和关系（公司-招聘岗位、岗位-要求技能、题目-考察技能、候选人-拥有技能）构建为知识图谱，支持关联推理
2. **双层检索**：回答"百度程序化广告岗会考什么"时，Local 检索"百度-程序化广告-技能要求"邻域，Global 检索"互联网广告岗位"社区摘要，两者结合给出全面预测
3. **增量更新**：新面经入库时只更新受影响的实体和关系，不需要重建整个索引
4. **多存储后端**：小规模用 JSON+NetworkX+Faiss 全本地运行，不需要外部数据库

**对求职备战 Agent 的改造启示**
- **公司-岗位-技能图谱**：从现有公司文档和面经中抽取"公司→岗位→核心技能→高频题目"的实体关系链，存入本地 NetworkX 图
- **题目预测增强**：预测题目时，先从图谱中检索目标岗位的"技能要求"实体，再沿"技能→题目"关系找到关联题目，比纯关键词检索更精准
- **候选人差距分析**：将候选人技能图谱与岗位技能图谱对比，直接找出缺失的技能节点和关联的薄弱题目
- **本地轻量部署**：用 JSON KV + Faiss 向量 + NetworkX 图的组合，完全本地运行，无需 Neo4j/Milvus 等重型组件

**局限性 / 引入成本**
- 实体/关系抽取依赖 LLM 调用，初始构建索引有一定 token 成本
- 知识图谱质量取决于抽取 prompt 的设计，需要调优
- 对纯文本知识库（如面经长文）的分块策略需要适配
- 引入成本：中等，2-3 天可搭建基础图谱索引，图谱质量调优需要持续迭代

---

#### B2. LlamaIndex — RAG-First 数据框架 + 事件驱动 Workflow

**基本信息**
- GitHub：https://github.com/run-llama/llama_index
- Star：~48K
- 最近更新：极活跃，v0.14+（Python），v0.12+（TS）
- 技术栈：Python / TypeScript，MIT 协议

**核心架构设计**

LlamaIndex 最初是 RAG 专用框架，2025 年后扩展为完整的 Agentic 应用框架，核心能力：

**五阶段 RAG 管道**
1. **Loading**：通过 300+ LlamaHub 连接器从 PDF/API/SQL/Notion/GitHub 等数据源加载文档
2. **Parsing/Chunking**：NodeParser 将文档切分为 Node，支持自定义分块策略
3. **Indexing**：构建 VectorStoreIndex（向量）、PropertyGraphIndex（知识图谱）、KeywordTableIndex（关键词）等多种索引
4. **Querying**：Router 查询引擎（自动选择最佳索引）、子问题分解、混合检索
5. **Evaluation**：Faithfulness（忠实度）、Relevancy（相关性）等评估指标

**事件驱动 Workflow（v0.11+ 推荐）**
- 替代旧的 DAG 编排，采用**事件驱动**模型
- 核心概念：`Event`（事件）、`@step`（步骤函数）、`Context`（上下文）
- 步骤通过 emit 事件触发后续步骤，运行时根据事件类型分发
- 支持条件分支、循环、并行、人工介入
- `FunctionAgent` 和 `AgentWorkflow`：将 Agent 作为 Workflow 中的一个步骤

**Agent 能力**
- `FunctionAgent`：工具调用 Agent，可作为 Workflow 步骤
- `AgentWorkflow`：多步骤 Agent 工作流
- ReAct / Function Calling 推理模式
- 支持 MCP 工具

**可借鉴的设计模式**
1. **事件驱动 Workflow**：备战流程用事件驱动而非硬编码顺序——"公司调研完成"事件触发"简历匹配"步骤，"匹配完成"事件触发"题目预测"步骤，天然支持并行和条件分支
2. **Router 检索**：三层知识库（候选人/公司/题库）各建索引，查询时 Router 自动判断该查哪个库或混合查询
3. **子问题分解**：预测"百度广告岗面试题"时，自动分解为"技术题""业务题""行为题"三个子问题，分别检索后合并
4. **PropertyGraphIndex**：内置知识图谱索引，可直接用于公司-岗位-技能关系建模
5. **评估嵌入**：在答案生成后自动运行 Faithfulness 评估，检测答案是否有事实性错误

**对求职备战 Agent 的改造启示**
- **Workflow 编排层**：用 LlamaIndex Workflow 替代现有线性脚本，每个环节是一个 `@step`，通过事件传递数据
- **三层知识库索引化**：候选人知识库→VectorStoreIndex（个人经历语义检索）；公司知识库→PropertyGraphIndex（公司-岗位-技能图谱）；题库→KeywordTableIndex + VectorStoreIndex（题目关键词+语义混合检索）
- **Router 统一查询入口**：Agent 需要知识时调用 Router，自动选择最合适的知识库和检索策略
- **答案质量评估**：生成答案后自动调用 FaithfulnessEvaluator，检查答案是否与检索到的面经/题库一致，不一致则触发重新生成
- **LlamaHub 连接器**：利用 GitHub/Notion/PDF 连接器直接加载现有本地文件知识库

**局限性 / 引入成本**
- 框架概念较多（Index/Retriever/QueryEngine/Workflow/Agent），学习曲线中高
- 事件驱动 Workflow 是较新的功能，文档和最佳实践仍在演进
- 与现有 CLI 工具的集成需要自定义 Tool 封装
- 引入成本：中等偏高，3-5 天完成基础迁移，RAG 管道调优需要持续迭代

---

#### B3. Mem0 — AI Agent 长期记忆层

**基本信息**
- GitHub：https://github.com/mem0ai/mem0
- Star：~45K
- 最近更新：活跃
- 技术栈：Python，MIT 协议（开源版）
- 论文：arXiv 2504.19413

**核心架构设计**

Mem0 是一个**记忆中间件**，为无状态的 LLM 提供跨会话的持久记忆。它不是完整的 Agent 框架，而是可以嵌入任意 Agent 系统的记忆层。

**两阶段记忆流水线（核心机制）**
1. **Extraction（提取）**：从用户-Assistant 对话对中，用 LLM 提取值得记忆的信息（事实、偏好、经历、目标等），生成结构化记忆条目
2. **Update（更新）**：将新记忆与已有记忆比对，判断是新增、更新还是删除（如用户改变了偏好），避免记忆冲突和冗余

**Mem0^g（图谱增强记忆）**
- 将记忆存储为有向标签图，实体为节点、关系为边
- 支持更深层的关联推理（如"用户在百度工作"→"百度是互联网公司"→"用户有互联网行业经验"）

**多存储引擎架构**
- 向量存储：语义检索记忆
- 图存储：实体关系推理
- KV 存储：快速键值查找
- 支持多种后端：Qdrant/Chroma/Milvus/PG/Neo4j/Redis 等

**核心 API**
- `memory.add(messages, user_id)`：从对话中添加记忆
- `memory.search(query, user_id)`：语义检索相关记忆
- `memory.get_all(user_id)`：获取所有记忆
- `memory.update(memory_id, data)`：更新特定记忆
- `memory.delete(memory_id)`：删除记忆

**性能数据**
- LOCOMO 基准上比 OpenAI Memory 准确率 +26%
- 响应延迟比全上下文方式降低 91%
- Token 使用量降低 90%+

**可借鉴的设计模式**
1. **候选人画像记忆**：将候选人的技能、经历、偏好、面试表现等持久化为结构化记忆，跨多次备战会话复用，不需要每次重新读取简历
2. **提取-更新两阶段**：每次面试模拟后，自动从对话中提取"候选人薄弱点""进步领域""需要强化的题目类型"，更新候选人画像
3. **记忆版本管理**：记录候选人技能的变化轨迹（如"3 月时 SQL 薄弱，8 月已掌握窗口函数"），用于追踪备战进度
4. **记忆检索注入**：每次生成答案或题目时，先检索候选人记忆，将"候选人擅长/薄弱领域"注入 prompt，实现个性化

**对求职备战 Agent 的改造启示**
- **候选人画像层**：在现有三层知识库之外，增加第四层"候选人动态画像"，用 Mem0 风格的结构化记忆存储候选人的技能评估、面试历史、薄弱点追踪
- **面试后自动更新**：每次模拟面试或真实面试后，运行记忆提取 pipeline，从面试记录中提取"被问到的题目""答得好的点""答不上来的点""面试官反馈"，更新候选人画像
- **个性化题目预测**：预测题目时，先检索候选人记忆中的"薄弱技能"和"历史被问题目"，加权提升相关题目的预测概率
- **进度追踪**：利用记忆的时间维度，生成"备战进度报告"——哪些技能从薄弱变为掌握，哪些题目类型仍需强化
- **本地轻量实现**：不需要完整 Mem0，可用 JSON 文件 + 简单的 LLM 提取 prompt 实现核心的"提取-更新"机制

**局限性 / 引入成本**
- 完整 Mem0 需要向量数据库，开源版部分高级功能（如图谱记忆）可能需要付费版
- 记忆提取质量依赖 LLM，可能产生错误或冗余记忆，需要人工审核机制
- 对求职场景的适配需要自定义提取 prompt（提取"技能/薄弱点/面试表现"而非通用"偏好"）
- 引入成本：低-中，1-2 天可实现简化版记忆层，完整 Mem0 集成需 2-3 天

---

#### B4. GPT Researcher — 自主研究 Agent

**基本信息**
- GitHub：https://github.com/assafelovic/gpt-researcher
- Star：~21K
- 最近更新：活跃
- 技术栈：Python，MIT 协议

**核心架构设计**

GPT Researcher 是一个**自主深度研究 Agent**，从单 Agent 演化为多 Agent 系统，核心架构：

**三角色多 Agent 流水线**
1. **Planner（规划者）**：根据研究主题生成一组研究问题，这些问题 collectively 构成对主题的客观全面覆盖
2. **Execution Agents（执行者，可并行）**：每个研究问题触发一个 Crawler Agent，负责：
   - 搜索相关网页（Tavily/Serper 等搜索 API）
   - 爬取网页内容
   - 对每个资源生成摘要并追踪来源
3. **Publisher（发布者）**：过滤和聚合所有执行者的摘要，生成结构化的综合研究报告（支持 Markdown/PDF/DOCX）

**关键机制**
- **研究问题分解**：Planner 生成的问题覆盖主题的不同维度（是什么/为什么/怎么做/案例/趋势）
- **并行爬虫**：多个 Crawler Agent 并行运行，大幅缩短研究时间
- **来源追踪**：每个摘要都标注来源 URL，最终报告带引用
- **深度研究模式**：支持迭代式深挖，对初步发现的关键点进行二次研究

**可借鉴的设计模式**
1. **Planner-Execution-Publisher 三角色**：公司调研环节直接复用——Planner 生成"需要调研的维度"（业务/技术/团队/面试/薪资），Execution 并行抓取各维度信息，Publisher 汇总为公司调研报告
2. **研究问题预分解**：不直接让 Agent 自由搜索，而是先由 Planner 生成结构化的调研问题清单，确保覆盖全面
3. **并行执行**：多个调研维度（牛客面经/小红书/知乎/官网/招聘 JD）并行抓取，结果汇总
4. **来源追踪**：每条调研信息标注来源 URL 和平台，最终报告带引用，便于核实
5. **迭代深挖**：初步调研后，对发现的关键信息（如某轮面试的具体题目）进行二次深挖

**对求职备战 Agent 的改造启示**
- **公司调研环节重构**：完全采用 GPT Researcher 的三角色架构
  - Planner：输入公司名+岗位，生成调研问题清单（"该岗位的核心技术栈是什么？""最近 3 个月有哪些面经？""团队规模和汇报关系？""薪资范围？"）
  - Execution：每个问题分配一个 Crawler Agent，调用对应的 CLI 工具（xhs-cli/zhihu-cli/nowcoder 搜索）并行抓取
  - Publisher：汇总所有结果，去重、按维度分类、标注来源，生成"公司调研报告.md"
- **调研问题模板化**：为不同类型公司（大厂/初创/外企）预设调研问题模板，Planner 在模板基础上动态调整
- **并行 CLI 调用**：现有多个 CLI 情报工具可以并行调用，不需要串行等待
- **来源可信度标注**：Publisher 汇总时对来源打可信度标签（官方招聘页>牛客面经>小红书讨论>匿名爆料）

**局限性 / 引入成本**
- 依赖搜索 API（Tavily/Serper），需要 API key 和费用
- 网页爬取可能遇到反爬限制
- 研究报告质量取决于搜索结果的覆盖度，小众公司可能信息不足
- 与现有 CLI 工具集成需要将 CLI 封装为 Crawler Agent 的工具
- 引入成本：低-中，1-2 天可将公司调研环节改造为三角色模式

---

#### B5. agent-interview-hub — 面经采集与结构化知识库

**基本信息**
- GitHub：https://github.com/Zchary1106/agent-interview-hub
- Star：小型项目（百级），但与求职备战场景高度对齐
- 最近更新：活跃（2026-08-29 更新）
- 技术栈：Python + Markdown + GitHub Pages

**核心架构设计**

这是一个**国内 AI Agent 工程师面试知识库**项目，核心是"多来源面经采集→结构化→回填公司文档"的闭环：

**知识体系结构**
```
AI Agent 工程师知识体系
├── 基础层（Transformer/LLM/Prompt）
├── 核心层（Agent 设计模式/RAG/Function Calling/MCP/框架）
├── 进阶层（Agentic RAG/微调/安全评估）
└── 实战层（系统设计/岗位要求/面经）
```

**Interview Collector Agent（核心机制）**
- 从牛客、小红书、知乎、CSDN、博客园、掘金、GitHub 等公开来源搜索面经
- 整理成**结构化候选**（JSON 格式）
- 人工或 Agent 审核候选后，同步到索引和公司文档
- 支持安装为 GitHub Copilot CLI / Claude Code / Cursor 的指令或 Skill

**采集流程**
```
1. doctor：检查本机搜索/读取工具可用性
2. search：按平台搜索，生成候选 JSON + Markdown 报告
3. 审核：人工或 Agent 审核候选质量
4. 入库：整理进 data/interviews.json
5. 构建：build_site.py 生成 GitHub Pages 静态站
```

**公司文档组织**
- 按公司分目录（字节跳动/百度/腾讯/阿里/小红书等）
- 每个公司目录包含：岗位要求、面试题、真实面经
- 通用知识单独组织（八股文题库/核心概念/高频拷打题）

**可借鉴的设计模式**
1. **多来源采集→结构化候选→审核→回填**：面经采集的标准 pipeline，先收集原始信息→结构化为统一格式→质量审核→写入正式知识库
2. **候选 JSON 中间格式**：采集结果先存为结构化 JSON（包含公司/岗位/题目/答案/来源/可信度/时间），审核后再转为 Markdown 文档
3. **按公司分目录的知识库组织**：公司级文档包含岗位要求+面试题+真实面经三合一，与现有公司知识库结构一致
4. **Collector Agent 可安装为 CLI Skill**：将采集逻辑封装为可安装的 Agent 指令，支持在 Copilot/Claude/Cursor 等环境中调用
5. **通用题库与公司面经分离**：通用八股文/核心概念单独组织，公司特定面经按公司组织，两者通过标签关联

**对求职备战 Agent 的改造启示**
- **面经采集 pipeline 标准化**：现有公司调研中的面经采集，改造为"多源搜索→候选 JSON→审核→回填公司文档"的标准流程
  - 候选 JSON schema：`{company, position, source_platform, source_url, questions[], answers[], date, confidence, tags[]}`
  - 审核环节：用 LLM 自动判断候选质量（是否重复、是否可信、是否完整），低质量的标记待人工审核
- **公司文档三合一**：每个公司的知识库文档统一包含"岗位要求 + 面试题库 + 真实面经"三个 section，与 agent-interview-hub 的结构对齐
- **Collector 封装为 CLI 工具**：将面经采集逻辑封装为独立的 CLI 命令（如 `interview-collect --company 百度 --position 程序化广告`），可被主 Agent 作为工具调用
- **通用题库层**：在现有三层知识库中，将"通用面试题库"（八股文/算法/系统设计）独立为一层，与公司特定面经分离，题目预测时先查通用题库再查公司面经
- **增量采集**：定期运行 Collector 增量采集新面经，只添加新候选，不重复已有内容

**局限性 / 引入成本**
- 项目本身是知识库而非 Agent 框架，需要提取其设计模式而非直接使用代码
- 面经采集依赖各平台的搜索能力，部分平台可能有反爬
- 候选质量审核需要 LLM 调用或人工投入
- 引入成本：低，1 天可将采集 pipeline 思路应用到现有流程

---

## 四、综合结论：最值得借鉴的 3 个架构设计模式

### 模式一：状态图 + 检查点的可恢复工作流编排（源自 LangGraph）

**为什么最值得借鉴**

求职备战是一个**长流程、多步骤、可中断**的任务。现有线性脚本的最大痛点是：任何一步失败或需要人工介入时，整个流程需要从头开始。LangGraph 的"状态图 + 检查点"模式完美解决这个问题。

**核心设计要素**
1. **共享状态对象**：定义一个 `InterviewPrepState`（Pydantic 模型），包含候选人信息、公司列表、调研结果、匹配分数、预测题目、生成答案、备战包路径等所有环节的中间产物
2. **节点化步骤**：6 个环节各为一个节点函数，输入是当前状态，输出是状态的增量更新
3. **条件边分支**：
   - 公司调研后：根据信息充分度决定"继续"还是"补充调研"
   - 简历匹配后：根据匹配度决定"深度备战"还是"基础备战"
   - 答案生成后：根据质量评分决定"通过"还是"重写"
4. **检查点持久化**：每完成一个节点，自动将状态保存到本地 SQLite/JSON 文件，通过 `run_id` 标识
5. **中断与恢复**：
   - 在"答案生成"后设置 `interrupt_after`，暂停等待人工审核
   - 恢复时从最后检查点加载状态，继续执行后续节点
   - 支持"时间回溯"：对不满意的结果，回溯到上一检查点重新执行

**如何应用到求职备战 Agent**

```
                    ┌─────────────┐
                    │  候选人输入  │
                    └──────┬──────┘
                           ▼
              ┌────────────────────────┐
              │  Node1: 公司调研(并行) │ ◄── 检查点1
              └───────────┬────────────┘
                          ▼
              ┌────────────────────────┐
              │  条件: 信息充分?       │
              │  Yes → 继续  No → 补充 │
              └───────────┬────────────┘
                          ▼
              ┌────────────────────────┐
              │  Node2: 简历匹配       │ ◄── 检查点2
              └───────────┬────────────┘
                          ▼
              ┌────────────────────────┐
              │  条件: 匹配度?         │
              │  ≥80% → 深度  <80% → 基础│
              └───────────┬────────────┘
                          ▼
              ┌────────────────────────┐
              │  Node3: 题目预测       │ ◄── 检查点3
              └───────────┬────────────┘
                          ▼
              ┌────────────────────────┐
              │  Node4: 答案生成       │ ◄── 检查点4
              └───────────┬────────────┘
                          ▼
              ┌────────────────────────┐
              │  ⏸ interrupt_after    │
              │  人工审核答案质量       │
              └───────────┬────────────┘
                          ▼
              ┌────────────────────────┐
              │  Node5: 备战包组装     │ ◄── 检查点5
              └───────────┬────────────┘
                          ▼
                    ┌─────────┐
                    │  完成   │
                    └─────────┘
```

**实现路径**
- 短期（1-2 天）：不引入 LangGraph，用 Python 实现简化版——定义 State 字典 + 每个步骤函数 + JSON 文件检查点 + 简单的条件判断
- 中期（3-5 天）：引入 LangGraph，用 StateGraph + MemorySaver/SqliteSaver + interrupt 实现完整功能
- 关键：先定义好 `InterviewPrepState` 的数据结构，这是整个编排的基础

---

### 模式二：Planner-Execution-Publisher 三角色并行研究架构（源自 GPT Researcher + agent-interview-hub）

**为什么最值得借鉴**

公司调研是求职备战中**信息来源最多、最适合并行化**的环节。现有串行调用多个 CLI 工具的方式效率低、覆盖不全。GPT Researcher 的三角色架构 + agent-interview-hub 的结构化候选 pipeline 是公司调研环节的最佳实践。

**核心设计要素**
1. **Planner（规划者）**：
   - 输入：公司名 + 岗位名 + 候选人背景
   - 输出：结构化的调研问题清单（覆盖业务/技术/团队/面试/薪资/文化 6 个维度）
   - 每个问题明确指定：调研维度、推荐数据源、优先级
2. **Execution（执行者，并行）**：
   - 每个调研问题分配一个 Worker Agent
   - Worker 调用对应的 CLI 工具（xhs-cli 搜小红书、zhihu-cli 搜知乎、nowcoder 搜牛客、web search 搜官网/招聘页）
   - 每个 Worker 输出：结构化信息片段 + 来源 URL + 可信度评分
   - 多个 Worker 并行执行，通过 asyncio 或多线程管理
3. **Publisher（汇总者）**：
   - 收集所有 Worker 的输出
   - 去重（相同信息来自多个来源时合并，保留所有来源引用）
   - 按维度分类整理
   - 可信度加权（官方 > 认证用户 > 匿名）
   - 输出：标准化的"公司调研报告.md"，按维度分 section，每条信息带来源引用
4. **结构化候选中间格式**（源自 agent-interview-hub）：
   - Worker 输出先存为 JSON 候选：`{dimension, content, source_platform, source_url, date, confidence, tags}`
   - Publisher 审核候选质量后再转为正式文档

**如何应用到求职备战 Agent**

```
公司调研请求
    │
    ▼
┌──────────┐
│ Planner  │ 生成调研问题清单：
│          │ 1. [业务] 百度程序化广告平台的核心产品是什么？ → 官网/36kr
│          │ 2. [技术] 该岗位要求的技术栈？ → 招聘JD/LinkedIn
│          │ 3. [面试] 最近3个月有哪些面经？ → 牛客/小红书/知乎
│          │ 4. [团队] 团队规模和汇报关系？ → 脉脉/知乎
│          │ 5. [薪资] 薪资范围和职级？ → 脉脉/offershow
│          │ 6. [文化] 团队氛围和加班情况？ → 小红书/脉脉
└────┬─────┘
     │
     ├──────────┬──────────┬──────────┬──────────┬──────────┐
     ▼          ▼          ▼          ▼          ▼          ▼
 ┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐
 │Worker1│ │Worker2│ │Worker3│ │Worker4│ │Worker5│ │Worker6│
 │业务调研│ │技术调研│ │面试调研│ │团队调研│ │薪资调研│ │文化调研│
 │web    │ │JD API │ │多CLI  │ │脉脉   │ │offer  │ │小红书 │
 │search │ │       │ │并行   │ │       │ │show   │ │       │
 └───┬───┘ └───┬───┘ └───┬───┘ └───┬───┘ └───┬───┘ └───┬───┘
     │          │          │          │          │          │
     └──────────┴──────────┴──────────┴──────────┴──────────┘
                              │
                              ▼
                      ┌──────────────┐
                      │  Publisher   │ 去重→分类→可信度加权→来源引用
                      │              │ 输出：公司调研报告.md
                      └──────────────┘
```

**实现路径**
- 短期（1 天）：将现有公司调研脚本拆分为"问题生成→并行搜索→汇总"三个函数，用 asyncio.gather 并行调用 CLI 工具
- 中期（2-3 天）：引入结构化候选 JSON 中间格式，增加去重和可信度评分逻辑
- 关键：Planner 的调研问题模板是核心资产，需要为不同类型公司（大厂/初创/外企）和岗位（技术/产品/运营）预设模板

---

### 模式三：三层知识库 + 图谱增强检索 + 候选人动态画像（源自 LightRAG + LlamaIndex + Mem0）

**为什么最值得借鉴**

现有求职备战 Agent 有三层知识库（候选人/公司/题库），但检索方式可能是简单的文件读取或关键词匹配。业界成熟的做法是**多种索引混合 + 知识图谱关联 + 动态画像个性化**，三者结合可以大幅提升题目预测和答案生成的精准度。

**核心设计要素**

**1. 三层知识库的索引化（源自 LlamaIndex）**

| 知识库 | 索引类型 | 检索方式 | 适用场景 |
|---|---|---|---|
| 候选人知识库 | VectorStoreIndex（语义） | 语义检索个人经历、项目细节 | 答案生成时检索"我做过什么类似项目" |
| 公司知识库 | PropertyGraphIndex（图谱）+ VectorStoreIndex | 图谱关联检索（公司→岗位→技能→题目）+ 语义检索 | 题目预测时检索"该岗位常考什么" |
| 题库 | KeywordTableIndex + VectorStoreIndex | 关键词匹配（精确题目）+ 语义检索（相似题目） | 题目预测和答案参考 |

**2. 知识图谱建模（源自 LightRAG）**

构建求职领域知识图谱，核心实体和关系：

```
实体类型：Company（公司）、Position（岗位）、Skill（技能）、Question（题目）、
         Candidate（候选人）、Experience（经历）、Concept（概念）

关系类型：
  Company -[RECRUITS]-> Position      百度 -[招聘]-> 程序化广告分析师
  Position -[REQUIRES]-> Skill         程序化广告 -[要求]-> SQL
  Position -[EXAMS]-> Question          程序化广告 -[常考]-> 竞价机制原理
  Question -[TESTS]-> Skill             竞价机制题 -[考察]-> 广告投放知识
  Candidate -[HAS]-> Skill              我 -[拥有]-> Python
  Candidate -[LACKS]-> Skill            我 -[缺乏]-> 竞价算法
  Candidate -[HAS_EXPERIENCE]-> Experience  我 -[有经历]-> 银行数据分析
  Skill -[PREREQUISITE]-> Skill         SQL -[前置]-> 数据库基础
```

**检索时的图谱增强**：
- 预测"百度程序化广告岗题目"：从"百度"节点→"程序化广告"岗位→"REQUIRES"技能→"EXAMS"关联题目→多跳检索
- 差距分析：候选人技能节点 vs 岗位要求技能节点，直接找出 LACKS 关系的技能
- 题目推荐：从候选人 LACKS 技能→TESTS 关系→推荐优先练习的题目

**3. 候选人动态画像（源自 Mem0）**

在三层知识库之外，增加第四层——候选人动态画像，用结构化记忆追踪候选人的变化：

```
候选人画像记忆条目：
{
  "skill_evaluation": {
    "SQL": {"level": "intermediate", "last_assessed": "2026-08-15", "weak_points": ["窗口函数", "性能优化"]},
    "Python": {"level": "advanced", "last_assessed": "2026-08-01"},
    "广告投放": {"level": "beginner", "last_assessed": "2026-09-01", "weak_points": ["竞价机制", "RTB流程"]}
  },
  "interview_history": [
    {"date": "2026-08-20", "company": "腾讯", "position": "数据分析", "questions_asked": [...], "performance": "good", "feedback": "SQL题答得好，系统设计需加强"}
  ],
  "progress_tracking": {
    "weak_skills_improved": ["SQL窗口函数（从不会到掌握）"],
    "remaining_gaps": ["竞价算法", "DSP平台架构"],
    "practice_questions_completed": 47
  }
}
```

**记忆更新机制**：
- 每次模拟面试/真实面试后，自动从面试记录中提取"被问题目""表现""薄弱点"
- 更新候选人画像中的 skill_evaluation 和 interview_history
- 题目预测时，将候选人薄弱技能和历史被问题目加权，提升相关题目的预测概率

**如何应用到求职备战 Agent**

```
┌─────────────────────────────────────────────────────────────┐
│                    统一检索入口 (Router)                       │
└────────┬────────────────┬────────────────┬───────────────────┘
         │                │                │
         ▼                ▼                ▼
  ┌────────────┐  ┌────────────┐  ┌────────────┐
  │ 候选人画像  │  │ 公司知识库  │  │ 题库        │
  │ (Mem0风格) │  │ (图谱+向量) │  │ (关键词+向量)│
  │            │  │            │  │            │
  │ ·技能评估  │  │ ·公司-岗位 │  │ ·通用八股  │
  │ ·面试历史  │  │ ·岗位-技能 │  │ ·算法题    │
  │ ·薄弱点    │  │ ·技能-题目 │  │ ·系统设计  │
  │ ·进度追踪  │  │ ·面经原文  │  │ ·行为面试  │
  └────────────┘  └────────────┘  └────────────┘
         │                │                │
         └────────────────┼────────────────┘
                          ▼
              ┌─────────────────────┐
              │  混合检索结果        │
              │  ·候选人相关经历     │
              │  ·公司岗位技能要求   │
              │  ·关联高频题目       │
              │  ·候选人薄弱点加权   │
              └──────────┬──────────┘
                         ▼
              ┌─────────────────────┐
              │  注入 Prompt        │
              │  题目预测 / 答案生成 │
              └─────────────────────┘
```

**实现路径**
- 短期（1-2 天）：
  - 用 Faiss（本地向量库）为三层知识库各建一个向量索引
  - 实现简单的 Router：根据查询类型选择检索哪个库
  - 候选人画像用 JSON 文件维护，每次面试后手动/半自动更新
- 中期（3-5 天）：
  - 引入 NetworkX 构建公司-岗位-技能-题目知识图谱
  - 实现图谱多跳检索（岗位→技能→题目）
  - 实现候选人画像的自动提取更新 pipeline（面试记录→LLM 提取→更新 JSON）
- 长期：引入 LightRAG 或 LlamaIndex 的 PropertyGraphIndex，替代自建图谱
- 关键：先从公司知识库的图谱建模开始，这是对题目预测提升最大的部分

---

## 五、改造路线图建议

### 第一阶段（1-2 周）：轻量改造，快速见效
1. **公司调研环节**：采用 Planner-Execution-Publisher 三角色模式，并行调用 CLI 工具，输出带来源引用的调研报告
2. **面经采集 pipeline**：引入结构化候选 JSON + 审核机制，标准化面经入库流程
3. **检查点机制**：用 JSON 文件实现简单的步骤级检查点，支持中断恢复

### 第二阶段（2-4 周）：架构升级
1. **编排层**：引入 LangGraph（或自建简化版状态图），将 6 环节改造为节点化工作流，支持条件分支和人工审核中断
2. **知识库索引化**：用 Faiss 为三层知识库建向量索引，实现 Router 统一检索
3. **公司知识图谱**：用 NetworkX 构建公司-岗位-技能-题目图谱，增强题目预测

### 第三阶段（1-2 月）：深度优化
1. **候选人动态画像**：实现 Mem0 风格的记忆提取-更新 pipeline，自动追踪候选人技能变化和面试历史
2. **答案质量控制**：在工作流中嵌入验证/反思节点（Faithfulness 评估 + 答案自检）
3. **图谱增强检索**：引入 LightRAG 或 LlamaIndex PropertyGraphIndex，替代自建图谱
4. **面试模拟**：用 AutoGen 风格的多 Agent 对话实现面试官-候选人-点评三方模拟

---

## 六、参考链接

### 多智能体框架
- LangGraph：https://github.com/langchain-ai/langgraph
- CrewAI：https://github.com/crewAIInc/crewAI
- AutoGen（原）：https://github.com/microsoft/autogen
- AG2（社区分支）：https://github.com/ag2ai/ag2
- MetaGPT：https://github.com/FoundationAgents/MetaGPT
- Dify：https://github.com/langgenius/dify

### 知识库 / RAG Agent
- LightRAG：https://github.com/HKUDS/LightRAG
- LlamaIndex：https://github.com/run-llama/llama_index
- Mem0：https://github.com/mem0ai/mem0
- GPT Researcher：https://github.com/assafelovic/gpt-researcher
- agent-interview-hub：https://github.com/Zchary1106/agent-interview-hub

### 对比分析参考
- LangGraph vs CrewAI vs AutoGen 2026 对比：https://bigaiagent.tech/langgraph-vs-crewai-vs-autogen-2026-2/
- 多 Agent 框架开发者实践实证研究：https://arxiv.org/pdf/2512.01939
- Mem0 论文：https://arxiv.org/pdf/2504.19413
- LightRAG 官网：https://lightrag.github.io/

---

*报告生成时间：2026-09-04 | 数据来源：GitHub、各项目官方文档、技术博客、arXiv 论文*
