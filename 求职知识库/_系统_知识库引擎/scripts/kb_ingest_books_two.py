#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb_ingest_books_two.py —— 把「磁盘上的实体书文件」入库到 书籍库.db（书籍专库）。

与 kb_split_books.py（从总库搬运已入库书籍）不同，本脚本处理的是
「总库里没有、只有本地文件」的新书：抽取正文 -> 落 doc/doc_content/doc_chunk
-> bge-m3 向量化 -> 重建 FTS5 全文索引，使书籍库自包含可检索。

特点：
  1) 实体文件先拷到 求职知识库/01_原始资料库/01_书籍/（归档，路径稳定），再入库
  2) PDF 用 pypdf 抽文字层；EPUB 用 zipfile+bs4 按 spine 顺序抽正文
  3) 分块复用 kb_migrate_p1.chunk_text（标题感知）
  4) 向量用 Ollama bge-m3；doc_vector 为 vec0，缺失才补（幂等）
  5) 书籍库 doc_fts 为外部内容表且无触发器，入库后整体 rebuild
  6) 入库前自动备份 书籍库.db 到 数据/_archive/

用法：
  python kb_ingest_books_two.py                 # 入库内置默认的两本书
  python kb_ingest_books_two.py /path/a.pdf /path/b.epub   # 指定文件
"""
import os
import re
import sys
import time
import json
import shutil
import sqlite3
import datetime
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kb_migrate_p1 import chunk_text  # 标题感知分块
import sqlite_vec

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
KB = os.path.join(ROOT, "求职知识库")
BOOK_DIR = os.path.join(KB, "01_原始资料库/01_书籍")
DB = os.path.join(KB, "_系统_知识库引擎/数据/书籍库.db")
ARCHIVE = os.path.join(KB, "_系统_知识库引擎/数据/_archive")
OLLAMA = "http://localhost:11434/api/embed"
MODEL = "bge-m3"
BATCH = 16
MIN_CHARS = 60       # 碎块不向量化（与 kb_embed 一致）
MAX_CHARS = 1500     # 超长块截断送 embed

DEFAULT_BOOKS = [
    "/Users/imac/Downloads/大模型应用开发 动手做AI Agent (黄佳) (z-library.sk, 1lib.sk, z-lib.sk).pdf",
    "/Users/imac/Downloads/大江大河四部曲（读客文化出品。《欢乐颂》出品方正午阳光新剧《大江大河》原著小说，王凯、杨烁、董子健主演。豆瓣9.2高分。） (阿耐) (z-library.sk, 1lib.sk, z-lib.sk).epub",
]


# ---------------- 抽取 ----------------
def sanitize(t):
    if not t:
        return ""
    t = re.sub(r"[\ud800-\udfff]", "", t)
    t = t.replace("\x00", " ")
    t = "".join(ch for ch in t if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    return t


def extract_pdf(path):
    from pypdf import PdfReader
    try:
        r = PdfReader(path)
    except Exception as e:
        return "", "fail:%s" % type(e).__name__
    pages = [(p.extract_text() or "") for p in r.pages]
    txt = "\n".join(pages)
    txt = re.sub(r"\n{4,}", "\n\n\n", txt).strip()
    cjk = sum(1 for ch in txt if "\u4e00" <= ch <= "\u9fff")
    method = "pypdf(%d页)" % len(r.pages)
    if cjk < 200:
        method = "need_ocr"
    return sanitize(txt), method


def _epub_spine_order(z):
    """返回按阅读顺序排好的内容文件路径列表（失败回退全部 html）"""
    try:
        cont = z.read("META-INF/container.xml").decode("utf-8", "ignore")
        m = re.search(r'full-path="([^"]+)"', cont)
        opf_path = m.group(1) if m else None
        if not opf_path:
            raise ValueError("no rootfile")
        opf_dir = os.path.dirname(opf_path)
        opf = z.read(opf_path).decode("utf-8", "ignore")
        # manifest: id -> href
        man = {}
        for mid, href in re.findall(r'<item[^>]*\bid="([^"]+)"[^>]*\bhref="([^"]+)"', opf):
            man[mid] = href
        # spine: idref 顺序
        spine_ids = re.findall(r'<itemref[^>]*\bidref="([^"]+)"', opf)
        ordered = []
        for sid in spine_ids:
            href = man.get(sid)
            if not href:
                continue
            full = os.path.normpath(os.path.join(opf_dir, href)).replace("\\", "/")
            if full in z.namelist():
                ordered.append(full)
        if ordered:
            return ordered
    except Exception:
        pass
    # 回退：所有 xhtml/html 按文件名排序
    return sorted(n for n in z.namelist() if n.lower().endswith((".xhtml", ".html", ".htm")))


def extract_epub(path):
    import zipfile
    import bs4
    try:
        z = zipfile.ZipFile(path)
    except Exception as e:
        return "", "fail:%s" % type(e).__name__
    parts = []
    for name in _epub_spine_order(z):
        try:
            soup = bs4.BeautifulSoup(z.read(name), "lxml")
            for t in soup(["script", "style"]):
                t.decompose()
            parts.append(soup.get_text("\n", strip=True))
        except Exception:
            continue
    txt = "\n".join(p for p in parts if p).strip()
    txt = re.sub(r"\n{4,}", "\n\n\n", txt).strip()
    cjk = sum(1 for ch in txt if "\u4e00" <= ch <= "\u9fff")
    method = "epub(%d文件)" % len(parts)
    if cjk < 200:
        method = "epub-empty"
    return sanitize(txt), method


def extract(path, ext):
    if ext == "pdf":
        return extract_pdf(path)
    if ext == "epub":
        return extract_epub(path)
    return "", "unsupported"


def classify(rel):
    return "书籍" if "01_原始资料库/01_书籍" in rel else "其他"


def guess_title(rel):
    base = os.path.basename(rel)
    base = re.sub(r"\.(pdf|epub|mobi|azw3|txt|md)$", "", base, flags=re.I)
    return base


# ---------------- 向量 ----------------
def embed_batch(msgs):
    req = urllib.request.Request(
        OLLAMA, data=json.dumps({"model": MODEL, "input": msgs}).encode(),
        headers={"Content-Type": "application/json"})
    for attempt in range(3):
        try:
            return json.loads(urllib.request.urlopen(req, timeout=180).read())["embeddings"]
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 + attempt * 3)


def connect(db_path):
    c = sqlite3.connect(db_path, timeout=600)
    c.enable_load_extension(True)
    sqlite_vec.load(c)
    c.row_factory = sqlite3.Row
    return c


def backup():
    os.makedirs(ARCHIVE, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(ARCHIVE, "书籍库.db.bak-%s" % ts)
    shutil.copy2(DB, dst)
    return dst


def ingest_one(conn, src_path):
    """抽取 + 拷贝归档 + 写 doc/doc_content/doc_chunk/doc_vector，返回 doc_id 或 None"""
    if not os.path.exists(src_path):
        print("  ✗ 文件不存在: %s" % src_path)
        return None
    ext = os.path.splitext(src_path)[1].lstrip(".").lower()
    if ext not in ("pdf", "epub"):
        print("  ✗ 不支持的扩展名: %s" % ext)
        return None
    st = os.stat(src_path)
    # 1) 拷贝到书籍归档目录（幂等：同名同大小跳过）
    os.makedirs(BOOK_DIR, exist_ok=True)
    dest = os.path.join(BOOK_DIR, os.path.basename(src_path))
    if not (os.path.exists(dest) and os.path.getsize(dest) == st.st_size):
        shutil.copy2(src_path, dest)
    rel = os.path.relpath(dest, KB)
    abs_path = dest
    # 2) 抽取
    txt, method = extract(abs_path, ext)
    if not txt or len(txt) < 200:
        print("  ✗ 抽取失败/过短: %s (%s, %d字)" % (os.path.basename(src_path), method, len(txt or "")))
        return None
    # 3) 已存在则跳过（按 path 唯一）
    exist = conn.execute("SELECT id FROM doc WHERE path=?", (abs_path,)).fetchone()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    title = guess_title(rel)
    if exist:
        print("  ⊙ 已存在(doc#%d) 跳过: %s" % (exist["id"], title))
        return exist["id"]
    sha = __import__("hashlib").md5(txt.encode("utf-8", "ignore")).hexdigest()[:16]
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO doc(path,rel_path,category,ext,size,mtime,sha,title,content,
               char_count,extract_method,status,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (abs_path, rel, classify(rel), ext, st.st_size, st.st_mtime, sha,
         title, txt, len(txt), method, "ok", now))
    doc_id = cur.lastrowid
    # 4) doc_content version=0
    cur.execute(
        "INSERT INTO doc_content(doc_id,version,content,char_count,extract_method,created_at) "
        "VALUES(?,0,?,?,?,?)",
        (doc_id, txt, len(txt), method, now))
    # 5) 分块
    chunks = chunk_text(txt)
    n_chunks = 0
    for seq, (header, t) in enumerate(chunks):
        if not t:
            continue
        cur.execute(
            "INSERT INTO doc_chunk(doc_id,content_version,seq,header,text,char_count) "
            "VALUES(?,0,?,?,?,?)",
            (doc_id, seq, header, t, len(t)))
        n_chunks += 1
    # 6) 向量化（仅 >= MIN_CHARS 的块；vec0 按 chunk_id 主键，幂等 INSERT OR IGNORE）
    rows = conn.execute(
        "SELECT id, text FROM doc_chunk WHERE doc_id=? AND char_count>=?",
        (doc_id, MIN_CHARS)).fetchall()
    done = 0
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        vecs = embed_batch([(r["text"] or "")[:MAX_CHARS] for r in batch])
        for (cid, _), v in zip(batch, vecs):
            conn.execute("INSERT OR IGNORE INTO doc_vector(chunk_id, embedding) VALUES(?,?)",
                         (cid, sqlite_vec.serialize_float32(v)))
        done += len(batch)
        if done % 256 < BATCH:
            conn.commit()
            print("      向量 %d/%d 块" % (done, len(rows)), flush=True)
    conn.commit()
    print("  ✓ %s | doc#%d | %d字 / %d块 / %d向量 (%s)"
          % (title[:36], doc_id, len(txt), n_chunks, len(rows), method))
    return doc_id


def main():
    files = sys.argv[1:] or DEFAULT_BOOKS
    print("目标库: %s" % DB)
    bak = backup()
    print("已备份: %s (%.1f MB)" % (os.path.basename(bak), os.path.getsize(bak) / 1048576))
    conn = connect(DB)
    before = conn.execute("SELECT count(*) FROM doc").fetchone()[0]
    t0 = time.time()
    docs = []
    for f in files:
        print("\n▶ %s" % os.path.basename(f))
        did = ingest_one(conn, f)
        if did:
            docs.append(did)
    # 7) 重建 FTS（书籍库无触发器，必须整体 rebuild）
    conn.execute("INSERT INTO doc_fts(doc_fts) VALUES('rebuild')")
    conn.commit()
    after = conn.execute("SELECT count(*) FROM doc").fetchone()[0]
    print("\n=== 完成 ===")
    print("doc: %d -> %d (+%d)" % (before, after, after - before))
    for t in ("doc_chunk", "doc_content", "doc_vector"):
        print("  %-12s %d" % (t, conn.execute("SELECT count(*) FROM %s" % t).fetchone()[0]))
    # 校验
    try:
        conn.execute("PRAGMA integrity_check")
        print("integrity: OK")
    except Exception as e:
        print("integrity: %s" % e)
    print("耗时 %.1fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
