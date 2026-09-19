# 补抓系统改造开发计划 —— 借鉴 Cumora 三机制

> 创建：2026-09-18 ｜ 来源：研究 yetone/cumora（AI Agent 协作工具）后的落地转化
> 范围：06_正文补抓 模块及其挂载的 5 个自动化
> 状态：**待排期**（等 A-1 语义搜索上线后开工）

---

## 一、背景与动机

Cumora 用三个机制保证多 Agent 协作不撞车、不浪费、不干过期活：

| Cumora 机制 | 解决的问题 | 映射到我们的系统 |
|---|---|---|
| Atomic Claims（原子认领） | 两个 Agent 抢同一任务 | 5 个自动化同小时可能抓同一条文章，浪费配额/触发风控 |
| Small-Brain Triage（小模型分诊） | 垃圾请求不打扰大模型 | 37,182 条待补里混着大量死链/低价值条目，贵通道白烧 |
| Freshness Gate（新鲜度门控） | 基于过期上下文决策 | 自动化按"上次的计划"跑，上游状态变了也不自知 |

## 二、现状盘点（改造前基线）

**写同一批库的 5 个自动化**：

| 自动化 | ID | 频率 | 写入目标 |
|---|---|---|---|
| 得到大脑补抓 | 7e599a17 | 每小时 45 条 | openbiliclaw.db getnote_body_task + content.db articles |
| 统一正文补抓 | bfe61017 | 每日 13:30 limit100 | content.db articles |
| 小红书 token 桥接 | fcd3fd5a | 每小时 1 条 | content.db articles |
| xhs_refill 浏览器通道 | pm2: xhs-refill-hourly | 每小时 1 条 | content.db articles |
| 体检日报 | 88b126f4 | 每日 08:30 | 只读 + 报告 |

**当前互斥方式的漏洞**：
- `articles.body_fetch_attempts`（现分布：attempts=0 有 40,625 / 1 有 172 / 2 有 20 / 3 有 87）只是**事后计数**，不是事前锁
- 各脚本选任务的 SELECT 和写回 UPDATE 之间存在时间窗（浏览器通道一条要跑 30~60 秒），窗口内另一通道完全可能选中同一条 → 双倍消耗（得到大脑配额、xhs 浏览器动作）甚至风控风险
- SQLite 默认 journal 模式下并发写还会偶发 `database is locked`

## 三、改造一：原子认领（P0，收益最大）

### 3.1 Schema 变更（content.db）

```sql
-- 新增认领字段
ALTER TABLE articles ADD COLUMN claim_status TEXT DEFAULT 'pending';
  -- pending / claimed / done / dead / hold
ALTER TABLE articles ADD COLUMN claimed_by TEXT;      -- 通道标识: getnote/unified/xtoken/agentlimb
ALTER TABLE articles ADD COLUMN claimed_at TIMESTAMP; -- 租约起点
CREATE INDEX idx_articles_claim ON articles(claim_status, body_fetch_attempts);
```

### 3.2 认领 SQL（核心，SQLite 3.35+ 支持 RETURNING）

```sql
-- 每个通道取任务的原子操作（一条语句完成"选+锁"，无时间窗）
UPDATE articles
SET claim_status='claimed', claimed_by='agentlimb', claimed_at=datetime('now')
WHERE id = (
  SELECT id FROM articles
  WHERE claim_status='pending'
    AND body_fetch_attempts < 3
    AND COALESCE(title,'') != ''
  ORDER BY id LIMIT 1
)
RETURNING id, title, url;
```

**要点**：
- `UPDATE ... WHERE id=(SELECT ... LIMIT 1) RETURNING` 在 SQLite 里是原子的——两个进程同时执行，同一个 id 只会被 RETURNING 返回一次，另一个会自动取下一条
- 各通道加 `AND claimed_by` 过滤可选做"通道亲和"（如得到大脑只认领 `source_type` 适合导入得到大脑的）

### 3.3 租约释放（防"认领后进程崩了变死锁"）

```sql
-- 每个任务开始时先回收过期租约（租约时长按通道慢程度定：浏览器通道 10 分钟，CLI 通道 5 分钟）
UPDATE articles
SET claim_status='pending', claimed_by=NULL, claimed_at=NULL
WHERE claim_status='claimed'
  AND claimed_at < datetime('now', '-10 minutes');
```

成功 → `claim_status='done'`，body 写入；失败 → `body_fetch_attempts=attempts+1`，`claim_status='pending'`（回到队列）；三次失败 → `dead`。

### 3.4 脚本改造点

| 文件 | 改动 |
|---|---|
| `06_正文补抓/01_统一入口/backfill.py` | 选文章逻辑换成认领 SQL；加 `--channel` 参数标识身份 |
| `02_执行脚本/refill_library_bodies_v2.py` | 同上 |
| `02_执行脚本/backfill_xhs_tokens.py` | 同上 |
| `二创/xhs_refill/agentlimb_xhs_batch.py` | 同上（租约给 10 分钟） |
| `refill_via_getnote.py` | getnote 走自己的 task 表（已有 status 字段），只需把终态同步回 articles.claim_status |

### 3.5 验收标准

- [ ] 模拟并发：两个进程同时跑认领循环 100 次，`SELECT id, COUNT(*) FROM articles WHERE claim_status='claimed' GROUP BY id HAVING COUNT(*)>1` 恒为空
- [ ] 崩溃恢复：kill 一个正在跑的通道，10 分钟后租约自动回收，任务被其他通道接走
- [ ] 体检日报（88b126f4）新增"认领状态分布 + 过期租约数"监控项

## 四、改造二：小模型分诊（P1，省配额）

### 4.1 设计：规则先行，小模型兜底

**第一层：零成本规则分诊**（能挡掉大头，不花一分钱）：

```sql
-- 直接判死类（进 claim_status='dead'，不进任何通道）：
-- ① 标题是转载占位/已删除样式（"该内容已被发布者删除"等模板标题）
-- ② 无标题无摘要且 URL 为短链跳转（小红书短链无 xsec_token 大概率打不开）
-- ③ 发布时间 > 180 天 且 同标题在库中已有 done 的另一条（重复源保留一条）
```

**第二层：小模型打分**（只处理规则剩下的灰色地带）：

- 输入：title + summary + source_type + url 特征
- 输出：`worth_try`（值得补）/ `likely_dead`（疑似死链）/ `low_value`（低价值：如纯图片贴、投票贴，补了也没正文）
- 模型选型（按成本排序，都用 WorkBuddy 云端 LLM 免密钥通道）：
  1. 本地 Ollama 小模型（qwen2.5:3b）——零成本，先试这个
  2. 云端轻量模型（如 doubao-lite / glm-4-flash 级）——1k 条成本约几毛钱

### 4.2 执行方案

- 新脚本 `06_正文补抓/02_执行脚本/triage_pending.py`：`--limit 200` 每次跑 200 条，产出写回 `claim_status`（`dead` / `hold`）+ 新字段 `triage_reason`
- 先跑 **500 条标注试点**：分诊结果 vs 实际补抓结果对比，统计"分诊判死但实际能补到"的误杀率，**误杀率 >2% 则收紧规则重调**
- 试点通过后才全量：37,182 条预估 60~70% 被分流，实际进入贵通道的缩到 1.2 万条以内

### 4.3 验收标准

- [ ] 试点 500 条报告（误杀率、分流率）出档
- [ ] 通道消费的队列里 dead/hold 占比 < 1%

## 五、改造三：新鲜度门控（P2，防旧计划覆盖新决策）

### 5.1 设计

新建 `06_正文补抓/03_文档/task_freshness.json`（或表），每条自动化登记**上游签名**：

| 自动化 | 上游是什么 | 签名内容 |
|---|---|---|
| 得到大脑补抓 | 配额策略（1000/天）+ backfill.py 版本 | 策略参数 hash + 脚本 mtime |
| 统一补抓 | 同上 | 同上 |
| xhs 桥接 | 频率纪律（1条/时）+ cookie 有效性 | 频率参数 + cookie 最近验证时间 |
| CLIP 嵌入 | 剩余量 | embeddings count（超过目标即自动停止轮次） |
| xhs_refill | 判死逻辑版本 | 脚本 hash |

**执行逻辑**：每个自动化脚本开头先读自己的登记签名，与当前实际签名比对——
- 一致 → 正常跑
- 不一致（如 backfill.py 被改过、判死逻辑升级了、cookie 换了）→ **HOLD 本轮 + 微信通知你**："上游变了，旧计划暂停，请确认后再放行"
- 确认放行 = 更新登记签名（可以做成 backfill.py 的 `confirm` 子命令）

### 5.2 防的具体事故（真实场景）

- 之前出现过：脚本指向废弃表跑了一周才发现（如果当时有 mtime/目标表签名检查，第一次运行就会 HOLD）
- 自动化静默消失后重建，旧的调度参数残留（如频率从 600 改 200 后旧配置没死干净）
- 用户口头改了策略（"降到 200/小时"）但 automation 里的 prompt 没同步——签名检查会暴露这种"说了没改"

### 5.3 验收标准

- [ ] 改任一脚本 mtime 后下一轮自动 HOLD 并通知
- [ ] `backfill.py confirm` 放行后恢复正常

## 六、实施顺序与工作量

| 阶段 | 内容 | 工作量 | 依赖 |
|---|---|---|---|
| S1 | 原子认领（schema+认领 SQL+租约+5 脚本接入） | 半天 | 无，可立即做 |
| S2 | 规则分诊层 + 500 条试点报告 | 半天 | S1（分诊直接写 claim_status） |
| S3 | 小模型分诊兜底接入 + 全量跑批 | 半天 | S2 试点通过 |
| S4 | 新鲜度门控 + confirm 子命令 | 半天 | 无硬依赖，随时可插 |
| S5 | 体检日报扩展监控（认领分布/过期租约/分流漏斗） | 1 小时 | S1 |

**建议排期**：等 CLIP 嵌入今晚跑完、A-1 语义搜索上线之后启动（S1 会动 5 个在跑自动化的脚本，错峰更稳）。总工作量约 2 天。

## 七、风险与回滚

- **最大风险**：认领改造覆盖所有通道时，某个脚本漏改仍走旧 SELECT——对策：S1 上线后**保留旧逻辑为 fallback 分支**（认领 SQL 失败时退回旧路径并告警），跑一周无事故后删
- Schema 变更是**纯增量**（加列加索引），不动现有数据，回滚 = 改回脚本即可
- 分诊误杀风险靠 500 条试点 + 2% 误杀率闸门兜底；`dead` 状态可一键批量复活（`UPDATE articles SET claim_status='pending', triage_reason=NULL WHERE claim_status='dead'`）

## 附：参考资料

- yetone/cumora（MIT）：freshness-gate / atomic-claims / small-brain-triage 三个 design doc
- 本库现状：content.db articles 表（attempts 分布见 §二）、openbiliclaw.db getnote_body_task
