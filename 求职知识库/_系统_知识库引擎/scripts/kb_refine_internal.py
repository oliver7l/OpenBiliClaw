# -*- coding: utf-8 -*-
"""
kb_refine_internal.py —— 内部资料全量提炼 pipeline

把 `01_原始资料库/工作资料_腾讯/2020年09月-内部资料/` 下所有原始资料，
用本地 Ollama (qwen2.5:7b) 提炼成结构化面试笔记，落到
`02_方向知识库/内部资料_提炼/<子目录>/<文件>.md`。

流程（每篇）：
  1. 取正文（doc.content，必要时 doc_content 最新版）
  2. 按 ~5000 字滑动窗口分块（overlap 500）
  3. 每块 → Ollama 抽要点（方法/模型/指标/数据/结论/坑）
  4. 汇总要点 → Ollama 合成结构化笔记（定位/摘要/方法/面试关联/术语/本人项目关联/价值）
  5. 写文件；已存在则跳过（断点续跑）

用法：
  python kb_refine_internal.py 预览          # 列出待提炼篇数
  python kb_refine_internal.py 执行 [--limit N] [--doc ID]   # 跑（可限制篇数/单篇）
  python kb_refine_internal.py 统计          # 已提炼 / 待提炼
"""
import os
import sys
import json
import time
import sqlite3
import argparse
import urllib.request

DB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "数据", "面试资料总库.db"))
SRC_PREFIX = "01_原始资料库/03_工作资料/工作资料_腾讯/2020年09月-内部资料/"
OUT_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "02_方向知识库", "内部资料_提炼_LLM"))
MODEL = os.environ.get("REFINE_MODEL", "qwen2.5:7b")
OLLAMA = "http://127.0.0.1:11434/api/chat"
CHUNK = 5000
OVERLAP = 500
CALL_TIMEOUT = 180  # 秒


def db():
    return sqlite3.connect(DB)


def get_content(c, doc_id):
    """优先 doc.content，否则 doc_content 最新版。"""
    row = c.execute("select content from doc where id=?", (doc_id,)).fetchone()
    if row and row[0] and len(row[0].strip()) > 50:
        return row[0]
    v = c.execute(
        "select content from doc_content where doc_id=? order by version desc limit 1",
        (doc_id,)).fetchone()
    return v[0] if v and v[0] else ""


MAX_CHARS = 250000  # 超长文档抽样上限（head + 中段 + 尾段）


def cap_content(text):
    """超长文档：head(40%) + 均匀中段(40%) + tail(20%) 采样到 ~MAX_CHARS，
    避免单篇上千次调用。短文档原样返回。"""
    if len(text) <= MAX_CHARS:
        return text, False
    head = int(MAX_CHARS * 0.4)
    tail = int(MAX_CHARS * 0.2)
    mid = MAX_CHARS - head - tail
    rest = text[head: len(text) - tail]
    step = max(1, len(rest) // mid)
    sampled = text[:head] + rest[::step][:mid] + text[len(text) - tail:]
    return sampled, True


def pending_docs(c):
    rows = c.execute(
        "select id, rel_path, char_count from doc "
        "where rel_path like ? and status='ok' and char_count>200 "
        "order by char_count desc", (SRC_PREFIX + "%",)).fetchall()
    return rows


def out_path(rel_path):
    sub = rel_path[len(SRC_PREFIX):]
    base, _ = os.path.splitext(sub)
    return os.path.join(OUT_ROOT, base + ".md")


def chunk_text(text, size=CHUNK, overlap=OVERLAP):
    text = text.strip()
    if len(text) <= size:
        return [text]
    chunks = []
    i = 0
    while i < len(text):
        chunks.append(text[i:i + size])
        i += size - overlap
    return chunks


def ollama_chat(system, user, temperature=0.3, num_ctx=8192):
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "options": {"temperature": temperature, "num_ctx": num_ctx},
        "stream": False,
    }
    data = json.dumps(payload).encode("utf-8")
    for attempt in range(3):
        try:
            req = urllib.request.Request(OLLAMA, data=data,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=CALL_TIMEOUT) as r:
                resp = json.loads(r.read().decode("utf-8"))
            return resp["message"]["content"].strip()
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))
    return ""


def extract_chunk(chunk):
    sys_p = ("你是求职面试知识库整理助手。从技术文档片段提取关键要点，"
             "输出简洁中文要点列表，每条≤40字。聚焦：方法/模型/算法/指标/"
             "公式/关键数据/核心结论/常见坑。只输出要点，不要解释、不要序号外的废话。")
    return ollama_chat(sys_p, chunk, temperature=0.2)


def synthesize(bullets, meta):
    sys_p = ("你是求职面试知识库整理助手。根据多篇要点整合一篇结构化面试提炼笔记（Markdown）。"
             "候选人是10年算法工程师，做过腾讯微视/QQ看点图集/视频号/OPPO(国内+海外)/"
             "百度/中信信用卡的推荐与广告项目。笔记要帮他快速回忆这篇资料、并判断对面试有无用。")
    user = f"""资料信息：{meta}
以下是从该资料分块提取的要点（可能重复/杂乱）：

{chr(10).join('- ' + b for b in bullets)}

请输出如下结构的 Markdown 笔记（不要加额外前言）：

# <资料标题（从要点推断，简洁）>
> 来源：{meta}

## 一句话定位
（≤40字，这篇资料讲什么）

## 核心内容摘要
（3-5句，讲清主干）

## 关键方法 · 模型 · 指标
（要点列表，抓方法/模型/算法/指标/数据，去重）

## 与推荐/广告/数据科学面试的关联
（这篇资料能支撑哪些面试话题？列出可迁移点）

## 关键术语
（列出文中重要术语/缩写，便于检索）

## 与本人项目的关联
（若与微视/图集/视频号/OPPO/百度/中信项目相关，点出关联；无关写"无明显关联"）

## 价值评估
（高 / 中 / 低 —— 对面试准备的价值，并一句话理由）
"""
    return ollama_chat(sys_p, user, temperature=0.3, num_ctx=16384)


def synthesize_direct(full_text, meta):
    """短文档（≤14k字）一步合成：直接读全文产出结构化笔记，省一次抽取调用。"""
    sys_p = ("你是求职面试知识库整理助手。根据下面这篇技术资料，直接输出一篇结构化面试提炼笔记（Markdown）。"
             "候选人是10年算法工程师，做过腾讯微视/QQ看点图集/视频号/OPPO(国内+海外)/"
             "百度/中信信用卡的推荐与广告项目。笔记要帮他快速回忆这篇资料、并判断对面试有无用。")
    user = f"""资料信息：{meta}

资料全文如下：
{full_text}

请输出如下结构的 Markdown 笔记（不要加额外前言）：

# <资料标题（简洁）>
> 来源：{meta}

## 一句话定位
（≤40字）

## 核心内容摘要
（3-5句，讲清主干）

## 关键方法 · 模型 · 指标
（要点列表，抓方法/模型/算法/指标/数据，去重）

## 与推荐/广告/数据科学面试的关联
（这篇资料能支撑哪些面试话题？列出可迁移点）

## 关键术语
（列出文中重要术语/缩写，便于检索）

## 与本人项目的关联
（若与微视/图集/视频号/OPPO/百度/中信项目相关，点出关联；无关写"无明显关联"）

## 价值评估
（高 / 中 / 低 —— 对面试准备的价值，并一句话理由）
"""
    return ollama_chat(sys_p, user, temperature=0.3, num_ctx=16384)


# 长文档切块尺寸（加大以减半调用次数）
LONG_CHUNK = 12000
LONG_OVERLAP = 1200


def refine_one(c, doc_id, rel_path, char_count):
    op = out_path(rel_path)
    if os.path.exists(op):
        return "skip"
    text = get_content(c, doc_id)
    if not text or len(text.strip()) < 100:
        return "empty"
    meta = f"{rel_path} ｜ {char_count}字"
    text, sampled = cap_content(text)
    if sampled:
        meta += " ｜ ⚠️超长抽样(全文%.0f万→抽取%.0f万)" % (
            (char_count or 0) / 10000, len(text) / 10000)
    # 短文档（≤14k字）：一步合成，1 次调用
    if len(text) <= 14000:
        note = synthesize_direct(text, meta)
        if not note:
            return "fail"
    else:
        # 长文档：滑动窗口抽取要点 → 合成
        chunks = chunk_text(text, LONG_CHUNK, LONG_OVERLAP)
        bullets = []
        for ch in chunks:
            b = extract_chunk(ch)
            if b:
                bullets.extend([ln.strip(" -·\t") for ln in b.splitlines() if ln.strip()])
            time.sleep(0.2)
        if not bullets:
            return "nobullets"
        note = synthesize(bullets, meta)
        if not note:
            return "fail"
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

    # 执行
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
        if (ok + fail) % 5 == 0:
            print(f"  …已处理 {ok+fail} 篇（ok={ok} skip={skip} fail={fail}）")
    print(f"\n完成：ok={ok} skip={skip} fail={fail} ｜ 耗时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
