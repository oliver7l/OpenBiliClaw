#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
求职知识库统一检索命令 kb.py
用法：
  python3 kb.py 查 <关键词>         全文检索（02/03 + 腾讯文档资料/解码文本），输出命中文件+关键行
  python3 kb.py 岗位 <公司>         查看某公司备战包路径与要点
  python3 kb.py 数字 <关键词>       查真实数字（权威表，只出真实口径）
  python3 kb.py 速记 <公司>         一键生成该公司面试速记卡
  python3 kb.py 方向 <方向>         列出某方向全部方法论文档
  python3 kb.py 索引 [关键词]       全库文件索引查询（file_index.db，可加 --层 01/02/03）
  python3 kb.py 项目 [关键词]       列出全部项目（或按关键词过滤）
  python3 kb.py 全部                查看系统当前登记的全部岗位/项目/数字
  python3 kb.py 记录 <公司> <轮次> <被问要点>   追加一条面试日志
  python3 kb.py add                 新增专题笔记（自动 frontmatter + 索引重建，CLI 全程）
  python3 kb.py add --topic <专题> --title <标题>  --body "..."
  python3 kb.py flush [收件箱]      处理一次收件箱(文件名下划线前缀=专题名,自动归类)
  python3 kb.py watch [收件箱] [秒]  常驻监听收件箱，丢文件即自动归类+重建索引
  python3 kb.py 语义 <关键词> [--层 02|03] [--top N]   向量语义检索（Ollama bge-m3 本地）
"""
import csv, os, re, sys, glob, sqlite3, subprocess, time

# 知识库根目录：由脚本位置推导（scripts/ → _系统_知识库引擎 → 根），便携可迁移
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE = os.path.join(ROOT, "_系统_知识库引擎")
DATA = os.path.join(ENGINE, "数据")

SEARCH_DIRS = [
    os.path.join(ROOT, "02_方向知识库"),
    os.path.join(ROOT, "03_岗位弹药库"),
    os.path.join(ROOT, "01_原始资料库/02_我的笔记/02_腾讯文档笔记"),
    os.path.join(ROOT, "01_原始资料库/08_解码文本"),
]
MAX_FILE = 2 * 1024 * 1024  # 只搜 2MB 以内文本
EXTS = (".md", ".txt")

def read_csv(name):
    p = os.path.join(DATA, name)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return list(csv.DictReader(f))

def find_jobs(keyword=None):
    rows = read_csv("01_岗位表.csv")
    if keyword:
        rows = [r for r in rows if keyword in r["公司"] or keyword in r["岗位"] or keyword in r["主打方向"]]
    return rows

def search_text(keyword):
    hits = []
    pat = re.compile(re.escape(keyword), re.IGNORECASE)
    for d in SEARCH_DIRS:
        if not os.path.isdir(d):
            continue
        for root, _, files in os.walk(d):
            for fn in files:
                if not fn.lower().endswith(EXTS):
                    continue
                fp = os.path.join(root, fn)
                try:
                    if os.path.getsize(fp) > MAX_FILE:
                        continue
                    with open(fp, encoding="utf-8", errors="replace") as f:
                        lines = f.readlines()
                except Exception:
                    continue
                for i, line in enumerate(lines):
                    if pat.search(line):
                        snippet = line.strip()
                        if len(snippet) > 90:
                            snippet = snippet[:90] + "…"
                        rel = fp.replace(ROOT + "/", "")
                        hits.append((rel, i + 1, snippet))
                        break  # 每文件只报第一个命中行
    return hits

def show_jobs(keyword=None):
    rows = find_jobs(keyword)
    if not rows:
        print("未找到匹配岗位")
        return
    print(f"\n{'公司':<12}{'岗位':<24}{'面试时间':<12}{'状态':<6} 主打方向")
    print("-" * 80)
    for r in rows:
        print(f"{r['公司']:<12}{r['岗位']:<24}{r['面试时间']:<12}{r['状态']:<6} {r['主打方向']}")
        print(f"  目录: {r['备战目录']}   简历: {r['简历版本']}")
        if r.get('备注'):
            print(f"  备注: {r['备注']}")

def show_numbers(keyword):
    rows = read_csv("03_真实数字表.csv")
    if keyword:
        rows = [r for r in rows if keyword in r["数字"] or keyword in r["口径"] or keyword in r["公司/项目"] or keyword in r["来源"]]
    if not rows:
        print(f"真实数字表中未找到: {keyword}")
        return
    print(f"\n『真实数字表』命中 {len(rows)} 条 (口径以表格为准,严禁编造):")
    for r in rows:
        print(f"  {r['数字']:<12} {r['口径']:<14} {r['公司/项目']:<30} 来源:{r['来源']}")

def show_projects(keyword=None):
    rows = read_csv("02_项目表.csv")
    if keyword:
        rows = [r for r in rows if keyword in r["项目名"] or keyword in r["公司"] or keyword in r["可讲要点"]]
    if not rows:
        print("未找到匹配项目")
        return
    print(f"\n项目库 ({len(rows)} 个):")
    for r in rows:
        print(f"  · {r['项目名']} [{r['公司']}] 核心数字: {r['核心数字']}")

def gen_card(company):
    jobs = find_jobs(company)
    if not jobs:
        print(f"岗位表中未找到: {company}")
        return
    j = jobs[0]
    print("=" * 70)
    print(f"速记卡 · {j['公司']} · {j['岗位']}  (面试 {j['面试时间']} · {j['状态']})")
    print("=" * 70)
    print(f"\n【一句话定位】主打方向: {j['主打方向']}")
    print(f"备战目录: {j['备战目录']}")
    if j.get('备注'):
        print(f"备注: {j['备注']}")
    print("\n【本项目相关核心数字】")
    # 速记卡应覆盖全部可讲弹药:优先按公司+主打方向匹配,未命中则展示全部
    nums = [r for r in read_csv("03_真实数字表.csv")
            if j['公司'] in r["公司/项目"] or (j['主打方向'] and j['主打方向'] in r["公司/项目"])]
    if not nums:
        nums = read_csv("03_真实数字表.csv")
    seen = set()
    shown = []
    for r in nums:
        k = (r["数字"], r["口径"], r["公司/项目"])
        if k not in seen:
            seen.add(k)
            shown.append(r)
    for r in shown[:15]:
        print(f"  {r['数字']} {r['口径']} ({r['公司/项目']})")
    print("\n【可讲项目】")
    projs = [r for r in read_csv("02_项目表.csv") if j['公司'] in r["公司"]]
    if not projs:
        projs = read_csv("02_项目表.csv")
    for r in projs:
        print(f"  · {r['项目名']} [{r['公司']}]: {r['核心数字']}")
    print("\n【题库入口】")
    qs = [r for r in read_csv("05_面试题索引.csv") if j['公司'] in r["公司"] or r["公司"] == "跨岗位"]
    for r in qs:
        print(f"  · {r['题目']} [{r['方向']}] -> {r['答案位置']}")

def _count_docs(folder):
    try:
        return sum(1 for f in os.listdir(folder) if f.endswith(".md"))
    except OSError:
        return 0


def _list_direction(folder, label):
    print(f"\n方向 [{label}] 的文档:")
    mds = sorted(glob.glob(os.path.join(folder, "*.md")))
    if not mds:
        print("  (该目录暂无 .md 文档)")
    for fp in mds:
        print(f"  · {os.path.basename(fp)}")


def show_direction(d):
    base = os.path.join(ROOT, "02_方向知识库")
    if not os.path.isdir(base):
        print("02_方向知识库不存在")
        return
    exact = os.path.join(base, d)
    if os.path.isdir(exact):
        _list_direction(exact, os.path.relpath(exact, ROOT))
        return
    # 递归搜嵌套层（如 简历技术专题/03_端云协同），目录名含关键字且有文档即可
    cands = []
    for root, dirs, _ in os.walk(base):
        for dd in dirs:
            p = os.path.join(root, dd)
            if d in dd and _count_docs(p):
                cands.append(p)
    cands.sort()
    if not cands:
        print(f"02_方向知识库下未找到方向: {d}")
        return
    print(f"『{d}』在 {len(cands)} 个目录命中:")
    for p in cands[:8]:
        _list_direction(p, os.path.relpath(p, ROOT))

def show_index(keyword=None, layer=None):
    db = os.path.join(DATA, "file_index.db")
    if not os.path.exists(db):
        print("file_index.db 不存在，请先运行: python3 scripts/build_index.py")
        return
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    sql = "SELECT 路径,层,子层,类型 FROM file_index WHERE 1=1"
    params = []
    if layer:
        sql += " AND 层=?"
        params.append(layer)
    if keyword:
        sql += " AND (文件名 LIKE ? OR 子层 LIKE ? OR 路径 LIKE ?)"
        kw = f"%{keyword}%"
        params += [kw, kw, kw]
    sql += " ORDER BY 层, 子层, 路径 LIMIT 50"
    rows = cur.execute(sql, params).fetchall()
    conn.close()
    if not rows:
        print(f"全库索引中未找到: {keyword or '(空)'}")
        return
    print(f"\n全库索引命中 {len(rows)} 个文件{('（关键词: ' + keyword) if keyword else ''}:")
    for rel, lay, sub, typ in rows:
        print(f"  [{lay[:2]}] {typ:<4} {sub:<22} {rel}")
    if len(rows) >= 50:
        print("  … 仅显示前50条，可用 --层 <01|02|03> 或更精确关键词缩小范围")

def add_log(company, rnd, points):
    p = os.path.join(DATA, "04_面试日志.csv")
    today = time.strftime("%Y-%m-%d")
    # 表头: 日期,公司,轮次,面试官角色,被问要点,复盘,复盘文档
    row = [today, company, rnd, "", points, "待复盘", ""]
    with open(p, "a", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow(row)
    print(f"已追加面试日志: {company} / {rnd} / {points}")

def _slug(s):
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "_", s.strip()).strip("_")
    return s or "未命名"

def add_note(argv):
    """kb.py add —— 自动生成带 frontmatter 的专题笔记 + 重建索引。"""
    topic = title = body = None
    input_file, ntype = None, "专题"
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--topic" and i + 1 < len(argv):
            topic, i = argv[i + 1], i + 2
        elif a == "--title" and i + 1 < len(argv):
            title, i = argv[i + 1], i + 2
        elif a == "--type" and i + 1 < len(argv):
            ntype, i = argv[i + 1], i + 2
        elif a == "--file" and i + 1 < len(argv):
            input_file, i = argv[i + 1], i + 2
        elif a == "--body" and i + 1 < len(argv):
            body, i = argv[i + 1], i + 2
        else:
            i += 1
    if not topic or not title:
        print("用法: kb.py add --topic <专题> --title <标题> [--body \"内容\" | --file path] [--type 专题|弹药|情报]")
        print("  — 自动放进对应专题目录、写入 frontmatter、重建全库索引，全程无需手动改文件")
        return
    body = body or ""
    if input_file:
        src = os.path.join(ROOT, input_file) if not os.path.isabs(input_file) else input_file
        if os.path.exists(src):
            with open(src, encoding="utf-8", errors="replace") as f:
                body += f.read()
    rel = _write_note(topic, title, body, ntype)
    print(f"已生成: {rel}")
    print("→ Obsidian Dataview 已自动收录（文件在专题目录即出现，无需登记）")
    try:
        subprocess.run([sys.executable, os.path.join(ENGINE, "scripts", "build_index.py")],
                       cwd=ROOT, check=True)
    except Exception as e:
        print(f"重建索引失败（可稍后手动运行 build_index.py）: {e}")
    print(f"可校验: python3 {os.path.join(ENGINE, 'scripts', 'kb.py')} 索引 {topic}")

# === 规则驱动打标 + 自动跨链（抄 ObsidianVaultManager 规则优先 + Kortex 自动链接思路） ===
DIR_RULES = [
    ("广告算法", ["RTB", "oCPX", "oCPC", "oCPM", "竞价", "CTR", "CVR", "归因", "DSP", "出价"]),
    ("推荐系统", ["召回", "排序", "粗排", "精排", "重排", "多目标", "多任务", "Embedding",
                  "DSSM", "MMoE", "ESMM", "DEN", "冷启动", "向量召回", "点击率", "多样性", "DPP", "特征"]),
    ("数据科学", ["AB实验", "指标", "漏斗", "留存", "画像", "显著性", "实验设计", "异常归因"]),
    ("机器学习与LLM", ["Transformer", "BERT", "大模型", "LLM", "RAG", "微调", "注意力", "损失", "正则"]),
    ("端云协同", ["端云", "模型压缩", "INT8", "量化", "联邦", "轻量化", "在线推理"]),
    ("对比学习", ["对比学习", "InfoNCE", "自监督", "SimCLR", "SimSiam", "负样本"]),
    ("多场景统一建模", ["多场景", "位置校准", "SAML", "BMM", "HC2", "场景迁移", "全场景统一样本"]),
    ("SQL与统计", ["窗口函数", "SQL", "join", "统计检验", "假设检验", "留存SQL"]),
]
TYPE_RULES = [
    ("面经", ["面经", "一面", "反问", "被问", "简历深挖"]),
    ("复盘", ["复盘", "已面", "面试记录"]),
    ("题库", ["题库", "八股", "高频", "押题", "预测题"]),
    ("弹药", ["STAR", "话术", "绩效", "提升", "涨幅"]),
]


# 标签注册表：别名把口语/英文缩写收敛到规范方向标签，避免碎片化（抄 obsidian-curator 别名解析）
TAG_ALIAS = {
    "推荐系统": ["推荐", "recsys", "推荐流"],
    "机器学习与LLM": ["机器学习", "深度学习", "ml", "ai", "大模型", "神经网络"],
    "数据科学": ["abtest", "ab实验", "实验", "指标体系"],
    "端云协同": ["端侧", "端上", "端到端"],
    "广告算法": ["广告", "广告投放", "广告召回"],
    "SQL与统计": ["sql", "sql优化"],
    "面经": ["面试", "面试记录", "被问"],
    "弹药": ["star法则", "star"],
}
# 通用词过滤（curator 第2步）：打标前从文本剔除不会命中任何规则关键词的泛化词，减少噪声
BLOCKED_GENERIC = ["笔记", "整理", "资料", "文档", "汇总", "总结", "备忘", "合集"]

OLLAMA = "http://127.0.0.1:11434"
EMB_MODEL = "bge-m3:latest"


def _ollama_embed(texts):
    """批量 embed（Ollama bge-m3）。Ollama 不可用/超时返回 None，供语义兜底静默降级。"""
    import json as _json
    import urllib.request
    if not texts:
        return []
    payload = {"model": EMB_MODEL, "input": texts}
    req = urllib.request.Request(OLLAMA + "/api/embed",
                                 data=_json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return _json.load(r)["embeddings"]
    except Exception:
        return None


def _semantic_direction_fallback(text, seen):
    """规则没命中任何方向时，用 bge-m3 向量把文本跟 8 个方向标签做余弦匹配，>0.6 补打。
    Ollama 不可用则返回空 —— 语义兜底绝不拖垮打标。"""
    import math
    # 每个方向用「标签 + 关键词」拼成 passage，embedding 才够语义（裸短词余弦普遍偏低）
    passages = [f"{name} {' '.join(kws)}" for name, kws in DIR_RULES]
    vecs = _ollama_embed(["Query: " + text + " 这段内容属于以下哪个方向？"] + passages)
    if not vecs or len(vecs) != 1 + len(DIR_RULES):
        return []

    def dot(a, b):
        return sum(x * y for x, y in zip(a, b))

    def nrm(a):
        return math.sqrt(dot(a, a)) or 1.0

    qv, qn = vecs[0], nrm(vecs[0])
    return [name for ((v), (name, _)) in zip(vecs[1:], DIR_RULES)
            if name not in seen and dot(qv, v) / (qn * nrm(v)) >= 0.60]


def _infer_tags(title, body=""):
    text = (title + "\n" + (body or ""))[:2000].lower()
    for w in BLOCKED_GENERIC:
        text = text.replace(w, " ")
    tags, seen = [], set()
    for tag, kws in DIR_RULES + TYPE_RULES:
        if tag in seen:
            continue
        aliases = TAG_ALIAS.get(tag, [])
        if any(k.lower() in text for k in kws) or any(a.lower() in text for a in aliases):
            tags.append(tag)
            seen.add(tag)
    # 语义兜底：只对「方向」补召回（题型靠关键词已够）
    dir_hit = {t for t in tags if t in {d for d, _ in DIR_RULES}}
    if not dir_hit:
        for d in _semantic_direction_fallback(text, seen):
            tags.append(d)
            seen.add(d)
    return tags


def _auto_link(inferred):
    """存在对应专题/方向 README 时，返回一个关联 wikilink 块（Obsidian 自动跨链）。
    专题文件夹带数字前缀（如 03_端云协同），故按「文件夹名包含标签」模糊匹配。"""
    links, seen = [], set()
    for tag in inferred:
        if tag in seen:
            continue
        for base in ("简历技术专题", ""):
            parent = os.path.join(ROOT, "02_方向知识库", base)
            if not os.path.isdir(parent):
                continue
            cands = [os.path.join(parent, d) for d in os.listdir(parent)
                     if os.path.isdir(os.path.join(parent, d)) and tag in d]
            c = next((os.path.join(f, "README.md") for f in cands
                      if os.path.exists(os.path.join(f, "README.md"))), None)
            if c:
                rel = os.path.relpath(c, ROOT).replace(os.sep, "/")[:-3]
                links.append((tag, rel))
                seen.add(tag)
                break
    if not links:
        return ""
    lines = "\n".join(f"- [[{rel}|{tag}]]" for tag, rel in links)
    return "\n\n## 关联\n\n" + lines + "\n"


def _topic_dir():
    d = os.path.join(ROOT, "02_方向知识库", "简历技术专题")
    os.makedirs(d, exist_ok=True)
    return d

def _write_note(topic, title, body, ntype="专题"):
    """按专题名解析/创建目录，写入带 frontmatter 的笔记，返回相对根路径。"""
    topic_dir = _topic_dir()
    exists = [d for d in sorted(os.listdir(topic_dir))
              if os.path.isdir(os.path.join(topic_dir, d))]
    folder = next((os.path.join(topic_dir, d) for d in exists
                   if topic in d or _slug(topic) in d), None)
    if not folder:
        nums = [int(re.match(r"^(\d+)", d).group(1)) for d in exists
                if re.match(r"^\d+", d)]
        folder = os.path.join(topic_dir, f"{max(nums) + 1 if nums else 1:02d}_{_slug(topic)}")
        os.makedirs(folder, exist_ok=True)
    today = time.strftime("%Y-%m-%d")
    path = os.path.join(folder, _slug(title) + ".md")
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{_slug(title)}_{n:02d}.md")
        n += 1
    # 自动打标（规则 + 语义兜底）与自动跨链
    inferred = _infer_tags(title, body)
    tags = ["简历技术专题", topic]
    tags += [t for t in inferred if t not in tags]
    fm = (f"---\ntags: [{', '.join(tags)}]\ntopic: {topic}\n"
          f"type: {ntype}\ncreated: {today}\nupdated: {today}\n---\n\n")
    content = "# " + title + "\n\n" + (body.strip() or "> 待补充")
    content += _auto_link(inferred)
    with open(path, "w", encoding="utf-8") as f:
        f.write(fm + content)
    return os.path.relpath(path, ROOT)

def _known_topics():
    """flush 白名单：前缀必须是已知方向 / 题型 / 专题目录（含数字前缀），否则一律归『未分类专题』。"""
    known = {name for name, _ in DIR_RULES} | {name for name, _ in TYPE_RULES}
    t = os.path.join(ROOT, "02_方向知识库", "简历技术专题")
    if os.path.isdir(t):
        known |= {re.sub(r"^\d+_", "", x) for x in os.listdir(t)
                  if os.path.isdir(os.path.join(t, x))}
    return known


def _flush_inbox(inbox=None):
    """把收件箱里的文件，按「文件名下划线前缀=专题名」自动归类为笔记。返回处理数。
    前缀必须命中白名单（已知方向/题型/专题），否则归入『未分类专题』，避免随此前缀建碎片目录。"""
    inbox = inbox or os.path.join(ROOT, "_收件箱")
    os.makedirs(inbox, exist_ok=True)
    done_dir = os.path.join(inbox, "_已处理")
    os.makedirs(done_dir, exist_ok=True)
    count = 0
    known = _known_topics()
    for fn in sorted(os.listdir(inbox)):
        if fn.startswith(".") or fn == "_已处理":
            continue
        src = os.path.join(inbox, fn)
        if not os.path.isfile(src):
            continue
        stem = os.path.splitext(fn)[0]
        topic = stem.split("_", 1)[0] if "_" in stem else None
        if not topic or topic not in known:
            topic = "未分类专题"
        try:
            with open(src, encoding="utf-8", errors="replace") as f:
                body = f.read()
        except OSError:
            continue
        rel = _write_note(topic, stem, body, ntype="专题")
        os.replace(src, os.path.join(done_dir, fn))
        count += 1
        print(f"· {fn} -> {rel}")
    if count:
        subprocess.run([sys.executable, os.path.join(ENGINE, "scripts", "build_index.py")],
                       cwd=ROOT)
    return count

def _watch_inbox(inbox=None, interval=5, once=False):
    inbox = inbox or os.path.join(ROOT, "_收件箱")
    os.makedirs(inbox, exist_ok=True)
    print(f"监听收件箱: {inbox}（每 {interval}s 扫一次）")
    print("把资料 .md/.txt 丢进去，自动归类成专题笔记并重建索引。Ctrl+C 退出。")
    while True:
        try:
            n = _flush_inbox(inbox)
            if n:
                print(f"本轮处理 {n} 个文件.")
        except Exception as e:
            print(f"出错: {e}")
        if once:
            return
        time.sleep(interval)

def _strip_fm(text):
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            return text[end + 3:]
    return text


def semantic_search(argv):
    """kb.py 语义 <关键词> —— 基于 Ollama bge-m3 的向量语义检索（抄 nobu666 hybrid 思路）。"""
    import math
    import urllib.request
    import json as _json
    q, layer, top = None, "02_方向知识库", 10
    for i, a in enumerate(argv):
        if a == "--层" and i + 1 < len(argv):
            layer = {"01": "01_原始资料库", "02": "02_方向知识库",
                     "03": "03_岗位弹药库"}.get(argv[i + 1], argv[i + 1])
        elif a == "--top" and i + 1 < len(argv):
            top = int(argv[i + 1])
        elif q is None:
            q = a
    if not q:
        print("用法: kb.py 语义 <关键词> [--层 02|03] [--top N]")
        print("  — 一次性 batch 嵌入该层全部 .md，按语义相关度排序（需本地 Ollama + bge-m3）")
        return
    if layer == "01_原始资料库":
        print("提示: 原始库体量大，语义搜索默认不扫它，请用 --层 02 或 --层 03。")
    docs = []
    base = os.path.join(ROOT, layer)
    for root, _, files in os.walk(base):
        for fn in files:
            if not fn.lower().endswith((".md", ".txt")) or fn == "README.md":
                continue
            fp = os.path.join(root, fn)
            try:
                t = open(fp, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            t = _strip_fm(t)[:1200].strip()
            if t:
                docs.append((fn, os.path.relpath(fp, ROOT), t))
    if not docs:
        print(f"{layer} 下无候选文档")
        return
    payload = {"model": EMB_MODEL,
               "input": ["Query: " + q] + ["Passage: " + t for _, _, t in docs]}
    req = urllib.request.Request(OLLAMA + "/api/embed",
                                 data=_json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        data = _json.load(r)
    embs = data["embeddings"]
    qv = embs[0]

    def dot(a, b):
        return sum(x * y for x, y in zip(a, b))

    def norm(a):
        return math.sqrt(dot(a, a)) or 1.0

    qn = norm(qv)
    scored = sorted(((dot(qv, v) / (qn * norm(v)), p, fn)
                     for v, (fn, p, _) in zip(embs[1:], docs)), reverse=True)
    print(f"语义检索『{q}』(层 {layer}) top{top}:")
    for s, p, fn in scored[:top]:
        print(f"  {s:.3f}  {p}")


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    cmd = args[0]
    if cmd == "查" and len(args) >= 2:
        hits = search_text(args[1])
        print(f"\n全文检索『{args[1]}』命中 {len(hits)} 个文件:")
        for rel, ln, snip in hits[:40]:
            print(f"  {rel}:{ln}  {snip}")
        if len(hits) > 40:
            print(f"  … 共 {len(hits)} 个")
    elif cmd == "岗位" and len(args) >= 2:
        show_jobs(args[1])
    elif cmd == "数字" and len(args) >= 2:
        show_numbers(args[1])
    elif cmd == "项目":
        show_projects(args[1] if len(args) >= 2 else None)
    elif cmd == "速记" and len(args) >= 2:
        gen_card(args[1])
    elif cmd == "方向" and len(args) >= 2:
        show_direction(args[1])
    elif cmd == "索引":
        kw = args[1] if len(args) >= 2 and not args[1].startswith("--") else None
        layer = None
        if "--层" in args:
            idx = args.index("--层")
            if idx + 1 < len(args):
                v = args[idx + 1]
                layer = {"01": "01_原始资料库", "02": "02_方向知识库", "03": "03_岗位弹药库"}.get(v, v)
        show_index(kw, layer)
    elif cmd == "全部":
        show_jobs()
        show_projects()
        print(f"\n真实数字表共 {len(read_csv('03_真实数字表.csv'))} 条; 面试日志 {len(read_csv('04_面试日志.csv'))} 条")
    elif cmd == "记录" and len(args) >= 3:
        add_log(args[1], args[2], " ".join(args[3:]))
    elif cmd == "add":
        add_note(args[1:])
    elif cmd == "flush":
        _flush_inbox(args[1] if len(args) >= 2 else None)
    elif cmd == "watch":
        iv = 5
        inbox = None
        if len(args) >= 2 and not args[1].isdigit():
            inbox = args[1]
        for a in args[1:]:
            if a.isdigit():
                iv = int(a)
        _watch_inbox(inbox, interval=iv)
    elif cmd == "语义":
        semantic_search(args[1:])
    else:
        print(__doc__)

if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass
        sys.exit(0)
