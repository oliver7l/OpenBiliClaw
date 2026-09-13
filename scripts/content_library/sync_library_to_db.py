#!/usr/bin/env python3
"""内容库 v2 同步：阅读收藏库 md（单一数据源）→ conversation_archive（前端派生镜像）。

用法：python3 scripts/sync_library_to_db.py [--dry-run]
- 解析 notes/阅读收藏库/阅读收藏库.md 索引 + 各条目 md
- 按 md 源文件名（md_file）幂等匹配既有行，其余新增
- FTS 由表触发器自动维护；幂等可重跑
"""
import os
import re
import sqlite3
import sys
from pathlib import Path

BASE = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/notes/阅读收藏库"
IDX = os.path.join(BASE, "阅读收藏库.md")
DB = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/data/openbiliclaw.db"

TYPE2KIND = {"链接原文": "link_article", "摘要": "summary", "对话解读": "dialogue"}

# 与 .workbuddy/skills/reading-plan-board/scripts/build_plan_data.py 的 category() 保持一致
_ALG = ['推荐系统','生成式推荐','搜广推','广告','CTR','召回','排序','时长预估','协同过滤','精排','eCPM','归因','竞价','多任务','损失','MiniOneRec','OpenOneRec','TGR','GR4AD','TAGR','SID','画像','推荐算法','大模型','Agent','RAG','意图识别','embedding','向量','DSP','出价','负向行为','FFT','ChatGPT','WikiSkill','负反馈','系统设计','Densing']
_CAREER = ['管理','沟通','表达','实习','入职','下属','团队','领导','AI Native','职业化','幼化','外刊']
_FIN = ['投资','宏观','量化','货币','芒格','巴菲特','美联储','金融','HRT','HFT','价格','PPI','CPI','周期']
_LIFE = ['心理','成长','自我','意识','全麻','影','剧','文学','治愈','台词','生活','租房','旅行','搏击','视频转文字','效率','工具','MarkTimes','BarNook','VibeCoding','别墅','深圳','聊天话术','亲密']


def category(tags, title):
    t = tags + " " + title
    if any(k in t for k in ['面试','面经','求职','秋招','简历','反问','onepage','HR','裁员','真题']):
        return '求职 · 面试'
    if any(k in t for k in _ALG):
        return '算法 · 推荐广告'
    if any(k in t for k in _CAREER):
        return '职场 · 成长'
    if any(k in t for k in _FIN):
        return '投资 · 宏观'
    if any(k in t for k in _LIFE):
        return '生活 · 兴趣'
    return '其他'


def ensure_columns(conn):
    """幂等补列（v2 内容库新增字段）。"""
    existing = {r[1] for r in conn.execute("PRAGMA table_info(conversation_archive)")}
    for col, ddl in (
        ("entry_num", "INTEGER DEFAULT 0"),
        ("group_name", "TEXT DEFAULT ''"),
        ("dialog_excerpt", "TEXT DEFAULT ''"),
        ("annotations", "TEXT DEFAULT ''"),
        ("md_file", "TEXT DEFAULT ''"),
    ):
        if col not in existing:
            conn.execute(f"ALTER TABLE conversation_archive ADD COLUMN {col} {ddl}")


def parse_index():
    entries = []
    for line in Path(IDX).read_text(encoding="utf-8").splitlines():
        if not re.match(r"^\| \d+ \|", line):
            continue
        parts = [p.strip() for p in line.strip().strip("|").split("|")]
        if len(parts) < 8:
            continue
        entries.append(dict(num=int(parts[0]), date=parts[1], typ=parts[2],
                            title=" | ".join(parts[3:-4]),
                            src=parts[-4], author=parts[-3], tags=parts[-2], summary=parts[-1]))
    return entries


def find_file(n):
    pats = [rf"^{n}-", rf"^{n:02d}-"]
    for f in sorted(os.listdir(BASE)):
        if f.endswith(".md") and any(re.match(p, f) for p in pats):
            return os.path.join(BASE, f)
    return None


def split_sections(text):
    """按标准五段标题切分；容错老格式（无标题则整体当原文）。"""
    heads = ["我的解读", "对话摘录", "原文", "批注"]
    pos = []
    for h in heads:
        m = re.search(rf"^## {h}\s*$", text, re.M)
        if m:
            pos.append((m.start(), h, m.end()))
    pos.sort()
    secs, body = {}, text
    if pos:
        secs["preamble"] = text[: pos[0][0]].strip()
        for i, (_s, h, e) in enumerate(pos):
            end = pos[i + 1][0] if i + 1 < len(pos) else len(text)
            secs[h] = text[e:end].strip().strip("-").strip()
        body = secs
    else:
        body = {"原文": text.strip()}
    return body, secs if pos else None


def parse_meta(text):
    meta_lines = re.findall(r"^> (.*)$", text[:2500], re.M)
    joined = "\n".join(meta_lines)
    url = re.search(r"https?://[^\s）)\|\"']+", joined)
    pub = re.search(r"(?:发布时间|发布)[:：]\s*(\d{4}-\d{2}-\d{2})", joined)
    votes = re.search(r"赞(?:同)?\s*(\d+)", joined)
    comments = re.search(r"评(?:论)?\s*(\d+)", joined)
    uq = re.search(r"\*\*你的提问\*\*[：:]\s*(.+)", joined)
    return dict(
        url=url.group(0).rstrip(".") if url else "",
        pub=pub.group(1) if pub else "",
        votes=int(votes.group(1)) if votes else 0,
        comments=int(comments.group(1)) if comments else 0,
        user_question=uq.group(1).strip() if uq else "（直接转发链接）",
    )


def main(dry=False):
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    ensure_columns(conn)
    next_seq = conn.execute("SELECT COALESCE(MAX(seq),0)+1 FROM conversation_archive").fetchone()[0]
    stats = dict(updated=0, inserted=0, skipped=0)

    for e in parse_index():
        fp = find_file(e["num"])
        if not fp:
            stats["skipped"] += 1
            continue
        text = Path(fp).read_text(encoding="utf-8")
        meta = parse_meta(text)
        body, _ = split_sections(text)
        analysis = body.get("我的解读", "")
        original = body.get("原文", "")
        dialog = body.get("对话摘录", "")
        uq = meta["user_question"]
        if "你的提问" in dialog and uq == "（直接转发链接）":
            m = re.search(r"\*\*你的提问\*\*[：:]\s*(.+)", dialog)
            if m:
                uq = m.group(1).strip()

        kind = TYPE2KIND.get(e["typ"], "link_article")
        md_file = os.path.basename(fp)
        # 幂等匹配：一律按 md 源文件名（md_file）。
        # 不做 seq 回退——历史行 seq 与收藏库编号不一致，且正文可能偶然出现
        # 「对话归档 seq N」字样，按 seq 匹配会误覆盖到无关行。
        row = conn.execute("SELECT id FROM conversation_archive WHERE md_file=?", (md_file,)).fetchone()

        vals = dict(
            kind=kind, user_question=uq, question_title=e["title"],
            source_url=meta["url"], source_type=e["src"], author=e["author"],
            headline="", voteup_count=meta["votes"], comment_count=meta["comments"],
            published_at=meta["pub"], tags=e["tags"],
            extracted_original_md=original, my_analysis_md=analysis,
            entry_num=e["num"], group_name=category(e["tags"], e["title"]),
            dialog_excerpt=dialog, annotations=body.get("批注", ""),
            md_file=os.path.basename(fp),
        )
        if dry:
            print(f"[dry] {e['num']} {e['typ']} -> {'update' if row else 'insert'} {md_file}")
            if not row:
                next_seq += 1
            continue

        if row:
            sets = ", ".join(f"{k}=?" for k in vals)
            conn.execute(f"UPDATE conversation_archive SET {sets} WHERE id=?", (*vals.values(), row["id"]))
            stats["updated"] += 1
        else:
            cols = ", ".join(vals)
            ph = ", ".join("?" for _ in vals)
            conn.execute(
                f"INSERT INTO conversation_archive (seq, {cols}, updated_at) VALUES (?, {ph}, datetime('now','localtime'))",
                (next_seq, *vals.values()),
            )
            next_seq += 1
            stats["inserted"] += 1

    if not dry:
        conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM conversation_archive").fetchone()[0]
    by_kind = dict(conn.execute("SELECT kind, COUNT(*) FROM conversation_archive GROUP BY kind").fetchall())
    print(f"完成: {stats} | 总行数={total} | by_kind={by_kind}")


if __name__ == "__main__":
    main(dry="--dry-run" in sys.argv)
