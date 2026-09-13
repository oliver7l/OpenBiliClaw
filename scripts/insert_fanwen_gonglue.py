#!/usr/bin/env python3
"""入库：面试反问环节完整攻略（问题+信号解读）→ interview_scripts id=116
来源：小红书 @littlegirlsbluehair「搜推实习onepage（八）：反问是一门艺术」
撤销：DELETE FROM interview_scripts WHERE id=116;
"""
import sqlite3

CONTENT = """## 反问环节完整攻略：反问其实还是在展现自我

所有反问都应利己，围绕三个目标：①探出面试结果 ②探出组内氛围和坑 ③展现职场特质（好学/努力/好用）。一次最多问 3 个。

## 一、探面试结果

**Q1「从今天的交流来看，您觉得我在哪些方面还需要加强？无论结果如何都希望珍惜这次交流机会，得到您的建议」**
- ✅ 认真给具体建议、甚至聊到"进来以后" → 基本稳了，聊得越久概率越大
- ✅ 稳中之稳：反过来问你之前的发展经历（转码原因等唠嗑）
- ⚠️ 只说缺点（项目/模型/策略/业务要加强）且没有"校招生可以培养"之类补话 → 大概率过不了

**Q2「这轮之后大概还有几轮？后续流程和时间节奏是怎样的？」**
- 愿意详细讲流程 → 过了；含糊让你等 HR 通知 → 大概率没过

## 二、探组内氛围与坑

**Q1「校招生/新人进来后期望是什么？多久能独立负责一个模块？」**
- 看_ld_是当社招用（干不出活打低绩效）还是真培养
- 侧问「组里作息节奏大概是怎样的」判断强度
- 面试时就让你不舒服的面试官，进组后大概率更甚，慎去

**Q2「组里最近半年到一年的规划？」**
- 判断业务前景：要去增量的组，不是"觉得好"或"什么火去什么"
- 组间差距比任何差距都重要；外面听说某组好，可能只是大组里一个小组好

**Q3「组内有没有比较成熟的文档体系？」**
- 没有文档积累的组不要去：没人带、问多了 mentor 烦，最后还背"能力不足"标签

## 三、展现职场特质

**Q「除了业务迭代，组里在前沿方向（如 scaling up、agent for rec）上有探索空间吗？」**
- 简历偏传统推荐的人必问：引导面试官追问你的前沿知识储备，提升评级
- 兼做避雷：连 scaling up 都没做/不了解的组，前沿性存疑

## 拼多多二面使用建议

组合三问：①哪些需要加强（探结果）②新人期望与节奏（探坑）③前沿方向（展特质）。第③问可自然衔接生成式推荐准备内容（OneRec 线上收益 +0.54%/1.24%、MiniOneRec/OpenOneRec、推荐 Scaling Law），面试官追问时把准备好的内容打出去。"""

con = sqlite3.connect("data/interview.db")
cur = con.cursor()
cur.execute("SELECT id FROM interview_scripts WHERE title LIKE '%反问环节完整攻略%'")
if cur.fetchone():
    print("已存在，跳过")
else:
    cur.execute(
        """INSERT INTO interview_scripts
        (type, company, position, title, content, key_points, priority, tags, used_count, note, source, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'), datetime('now', 'localtime'))""",
        (
            "反问话术", "通用", "算法岗",
            "反问环节完整攻略：问题+面试官反应信号解读（小红书onepage八）",
            CONTENT,
            "探结果|探坑|展特质;最多3问;Q1需要加强=读空气核心题;前沿问题可衔接生成式推荐弹药",
            "高",
            '["反问","面试技巧","信号解读","搜推","校招","拼多多二面"]',
            0,
            "与 id3-25 单问题清单互补：本条是组合攻略+反应解读",
            "kb_documents#xhs_6aa28ca8",
        ),
    )
    print("inserted id =", cur.lastrowid)
con.commit()
con.close()
