# -*- coding: utf-8 -*-
"""
kb_refine_extractive.py —— 内部资料全量提炼（纯 Python 抽取式，无模型依赖）

把 `01_原始资料库/03_工作资料/工作资料_腾讯/2020年09月-内部资料/` 下所有原始资料，
抽取式提炼成结构化面试笔记，落到 `02_方向知识库/内部资料_提炼/<子目录>/<文件>.md`。

抽取内容：结构大纲 / 关键方法·模型·指标（带数字优先）/ 关键术语 /
与推荐·广告·数据科学面试的关联 / 与本人项目关联 / 价值评级。
不依赖任何外部模型，秒级跑完全部 256 篇；LLM 深度合成可后续叠加。

用法：
  python kb_refine_extractive.py 预览
  python kb_refine_extractive.py 执行 [--limit N] [--doc ID]
  python kb_refine_extractive.py 统计
"""
import os
import re
import sys
import time
import sqlite3
import argparse

DB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "数据", "面试资料总库.db"))
SRC_PREFIX = "01_原始资料库/03_工作资料/工作资料_腾讯/2020年09月-内部资料/"
OUT_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "02_方向知识库", "内部资料_提炼"))
MAX_CHARS = 250000

SIGNAL = ["模型", "算法", "方法", "策略", "指标", "准确率", "精确率", "召回率", "AUC", "KS",
          "Lift", "CTR", "CVR", "转化率", "曝光", "点击", "留存", "时长", "活跃", "GMV",
          "ROI", "ARPU", "出价", "校准", "冷启动", "负样本", "多目标", "召回", "排序",
          "推荐", "广告", "归因", "因果", "PSM", "DID", "特征", "embedding", "Embedding",
          "深度学习", "神经网络", "Transformer", "XGBoost", "LightGBM", "树模型", "向量",
          "用户", "画像", "实验", "AB", "结论", "提升", "增长", "下降", "优化", "评估",
          "框架", "系统", "架构", "链路", "漏斗", "分流", "预估", "匹配", "聚类", "分类",
          "回归", "序列", "图", "协同过滤", "Wide", "Deep", "DIN", "ESMM", "MMoE", "双塔"]
PROJECT_KW = {
    "微视": ["微视", "账号推荐", "好友推荐", "关注链路", "关注流"],
    "QQ看点/图集": ["QQ看点", "看点", "图集", "信息流"],
    "视频号": ["视频号"],
    "OPPO": ["OPPO", "应用商店", "海外"],
    "百度": ["百度", "应用商店"],
    "中信": ["中信", "信用卡", "分期", "客群", "征信"],
    "通用推荐广告": ["召回", "排序", "重排", "多目标", "冷启动", "出价", "CTR", "CVR", "归因", "因果"],
}
DOMAIN_TOPIC = {
    "召回": "召回策略（向量/图/协同过滤/多路）",
    "排序": "排序模型（Wide&Deep/DIN/ESMM/树模型）与重排",
    "多目标": "多目标排序（MMoE/ESMM/帕累托）",
    "冷启动": "冷启动（EE/元学习/内容特征）",
    "出价": "广告出价（oCPX/PID/pacing/校准）",
    "CTR": "CTR/CVR 预估与校准",
    "归因": "归因与因果推断（PSM/DID/AB）",
    "用户": "用户画像/分层/生命周期",
    "留存": "留存/时长/活跃 指标与因果",
    "特征": "特征工程/embedding",
    "实验": "AB 实验与评估框架",
    "漏斗": "转化漏斗与渠道效能",
}


def db():
    return sqlite3.connect(DB)


def get_content(c, doc_id):
    row = c.execute("select content from doc where id=?", (doc_id,)).fetchone()
    if row and row[0] and len(row[0].strip()) > 50:
        return row[0]
    v = c.execute(
        "select content from doc_content where doc_id=? order by version desc limit 1",
        (doc_id,)).fetchone()
    return v[0] if v and v[0] else ""


def cap_content(text):
    if len(text) <= MAX_CHARS:
        return text, False
    head = int(MAX_CHARS * 0.4)
    tail = int(MAX_CHARS * 0.2)
    mid = MAX_CHARS - head - tail
    rest = text[head: len(text) - tail]
    step = max(1, len(rest) // mid)
    return text[:head] + rest[::step][:mid] + text[len(text) - tail:], True


def split_sentences(text):
    parts = re.split(r"[。！？\n;；]+", text)
    return [p.strip() for p in parts if len(p.strip()) >= 6]


def detect_headings(text):
    heads = []
    for line in text.splitlines():
        s = line.strip()
        if not s or len(s) > 40:
            continue
        if re.match(r"^#{1,4}\s", s):
            heads.append(s.lstrip("# ").strip())
        elif re.match(r"^[一二三四五六七八九十]+[、.．]", s):
            heads.append(s)
        elif re.match(r"^\d+[.、．]", s):
            heads.append(s)
        elif re.match(r"^[\u4e00-\u9fa5A-Za-z]{2,20}[:：]", s):
            heads.append(s.split("[:：]")[0])
    # 去重保序
    seen, out = set(), []
    for h in heads:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out[:40]


def extract_keysentences(text):
    nums = re.compile(r"(\d+(\.\d+)?\s?(%|％|万|亿|倍|k|w|W)|提升\s*\d+|增长\s*\d+|下降\s*\d+|\+\d+)")
    scored = []
    seen = set()
    for s in split_sentences(text):
        hit = sum(1 for kw in SIGNAL if kw.lower() in s.lower())
        if hit == 0:
            continue
        key = s[:30]
        if key in seen:
            continue
        seen.add(key)
        has_num = 1 if nums.search(s) else 0
        scored.append((hit * 2 + has_num, len(s), s))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [s for _, _, s in scored[:30]]


def extract_terms(text):
    terms = set()
    for m in re.findall(r"\b[A-Z]{2,}[A-Za-z]*\b", text):
        if m not in ("AI", "UI", "ID", "API", "SDK", "APP", "OK", "CV", "NLP", "DB"):
            terms.add(m)
    for kw in ["Wide&Deep", "DeepFM", "DIN", "DIEN", "ESMM", "MMoE", "PLE", "PSM",
               "DID", "RNN", "LSTM", "GRU", "CNN", "Transformer", "BERT", "XGBoost",
               "LightGBM", "Word2Vec", "GNN", "GCN", "GraphSAGE", "oCPX", "eCPM",
               "CTR", "CVR", "AUC", "KS", "Lift", "UV", "PV", "ARPU", "ROI",
               "Embedding", "Embedding"]:
        if kw.lower() in text.lower():
            terms.add(kw)
    return sorted(terms)[:30]


def project_relevance(text):
    tl = text.lower()
    hits = []
    for proj, kws in PROJECT_KW.items():
        if any(k.lower() in tl for k in kws):
            hits.append(proj)
    return hits


def topic_relevance(text):
    tl = text.lower()
    return [v for k, v in DOMAIN_TOPIC.items() if k.lower() in tl]


def build_note(rel_path, char_count, text, sampled):
    stem = os.path.splitext(rel_path.split("/")[-1])[0]
    headings = detect_headings(text)
    keys = extract_keysentences(text)
    terms = extract_terms(text)
    proj = project_relevance(text)
    topics = topic_relevance(text)

    # 一句话定位
    domain = "技术" if topics else "业务/项目"
    if proj:
        loc = "、".join(proj[:3])
        oneliner = f"关于{loc}相关的{domain}资料（《{stem}》），含 {len(keys)} 条关键要点。"
    else:
        oneliner = f"内部资料《{stem}》，属{domain}文档，提取 {len(keys)} 条关键要点供复习索引。"

    # 核心摘要：取前 2 条关键句（或首段）
    if keys:
        summary = "；".join(keys[:2])
    else:
        first = [s for s in split_sentences(text) if len(s) > 20][:1]
        summary = first[0][:200] if first else stem
    summary = summary[:400]

    value = "中"
    reason = "含部分方法/指标要点，可按需查阅"
    if proj and (len(keys) >= 8 or terms):
        value, reason = "高", f"与本人项目（{('、'.join(proj[:3]))}）直接关联，且含方法/指标/术语"
    elif len(keys) < 3:
        value, reason = "低", "关键要点少，可能为数据/会议记录类，参考性有限"

    L = []
    L.append(f"# {stem} · 提炼笔记")
    L.append("")
    meta = f"> 来源：`{rel_path}` ｜ {char_count}字"
    if sampled:
        meta += f" ｜ ⚠️超长抽样(全文约{char_count//10000}万→抽取{len(text)//10000}万)"
    L.append(meta)
    L.append("")
    L.append("## 一句话定位")
    L.append(oneliner)
    L.append("")
    L.append("## 核心内容摘要")
    L.append(summary)
    L.append("")
    if headings:
        L.append("## 结构大纲")
        for h in headings[:25]:
            L.append(f"- {h}")
        L.append("")
    L.append("## 关键方法 · 模型 · 指标")
    if keys:
        for k in keys:
            L.append(f"- {k}")
    else:
        L.append("- （未提取到强信号句子）")
    L.append("")
    L.append("## 与推荐/广告/数据科学面试的关联")
    if topics:
        for t in topics:
            L.append(f"- {t}")
    else:
        L.append("- 无明显领域关键词，可能为通用业务/数据记录")
    L.append("")
    if terms:
        L.append("## 关键术语")
        L.append("、".join(terms))
        L.append("")
    L.append("## 与本人项目的关联")
    if proj:
        L.append("、".join(proj) + "（文中出现相关关键词）")
    else:
        L.append("无明显关联")
    L.append("")
    L.append("## 价值评估")
    L.append(f"**{value}** —— {reason}")
    L.append("")
    return "\n".join(L)


def out_path(rel_path):
    sub = rel_path[len(SRC_PREFIX):]
    base, _ = os.path.splitext(sub)
    return os.path.join(OUT_ROOT, base + ".md")


def pending_docs(c):
    return c.execute(
        "select id, rel_path, char_count from doc "
        "where rel_path like ? and status='ok' and char_count>200 "
        "order by char_count desc", (SRC_PREFIX + "%",)).fetchall()


def refine_one(c, doc_id, rel_path, char_count):
    op = out_path(rel_path)
    if os.path.exists(op):
        return "skip"
    text = get_content(c, doc_id)
    if not text or len(text.strip()) < 100:
        return "empty"
    text, sampled = cap_content(text)
    note = build_note(rel_path, char_count, text, sampled)
    os.makedirs(os.path.dirname(op), exist_ok=True)
    with open(op, "w", encoding="utf-8") as f:
        f.write(note + "\n")
    return "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["预览", "执行", "统计"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--doc", type=int, default=0)
    args = ap.parse_args()
    c = db()
    if args.cmd == "预览":
        rows = pending_docs(c)
        print(f"待提炼：{len(rows)} 篇，共 {sum(r[2] or 0 for r in rows)/10000:.0f} 万字")
        for i, (d, p, w) in enumerate(rows[:12]):
            print(f"  [{d}] {w or 0}字  {p[len(SRC_PREFIX):]}")
        if len(rows) > 12:
            print(f"  ... 其余 {len(rows)-12} 篇")
        return
    if args.cmd == "统计":
        rows = pending_docs(c)
        done = sum(1 for _, p, _ in rows if os.path.exists(out_path(p)))
        print(f"总计 {len(rows)} 篇 ｜ 已提炼 {done} ｜ 待提炼 {len(rows)-done}")
        return
    if args.doc:
        rows = [c.execute("select id,rel_path,char_count from doc where id=?",
                          (args.doc,)).fetchone()]
    else:
        rows = pending_docs(c)
    if args.limit:
        rows = rows[:args.limit]
    t0 = time.time()
    ok = skip = fail = 0
    for d, p, w in rows:
        try:
            r = refine_one(c, d, p, w)
        except Exception as e:
            r = f"err:{e}"
        if r == "ok":
            ok += 1
        elif r == "skip":
            skip += 1
        else:
            fail += 1
            print(f"  ✗ [{d}] {r}  {p[len(SRC_PREFIX):]}")
        if (ok + fail) % 40 == 0:
            print(f"  …已处理 {ok+fail} 篇（ok={ok} skip={skip} fail={fail}）")
    print(f"\n完成：ok={ok} skip={skip} fail={fail} ｜ 耗时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
