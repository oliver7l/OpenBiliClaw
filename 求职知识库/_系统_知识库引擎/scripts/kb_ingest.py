#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""求职知识库 · 全量资料入库框架（原始数据层）

三层数据框架的第 0 层：把求职知识库下所有「文档型」资料抽取为纯文本，
统一存入 SQLite + FTS5 全文索引，并登记处理台账 / 未处理清单。

用法：
    python3 kb_ingest.py 统计                      # 查看入库现状
    python3 kb_ingest.py 扫描                      # 只扫描登记，不提取
    python3 kb_ingest.py 运行 [--分类 简历] [--限制 50] [--强制]
    python3 kb_ingest.py 查 关键词 [--分类 X] [--限 20]
    python3 kb_ingest.py 未处理                    # 列出需要 OCR / 失败的文件

设计要点：
- 断点续跑：按 (path, mtime) 判断是否跳过，重复执行只处理新增/变更文件
- 分格式提取：md/txt/json/csv/html/xml 直读；pdf/pptx/docx/xlsx/ipynb 用库；doc 用 textutil
- 无文本层（扫描件/图片型幻灯片）标记为 need_ocr，进入未处理表，可走 OCR 通道补处理
- 排除代码二进制：class/jar/so/pyc/scala/java/py/js/css/图片/音视频等
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime

KB = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/求职知识库"
DB = os.path.join(KB, "_系统_知识库引擎/数据/面试资料总库.db")
TMP = os.path.join(KB, "_系统_知识库引擎/.tmp_ingest")

# ---------- 排除：代码与二进制 ----------
SKIP_EXT = {
    "class", "jar", "so", "dylib", "dll", "pyc", "pyo", "exe", "bin", "dat",
    "png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "svg", "tiff",
    "mp4", "mp3", "wav", "mov", "avi", "m4a", "flac",
    "zip", "gz", "tar", "bz2", "xz", "7z", "rar",
    "scala", "java", "cpp", "cc", "c", "h", "hpp", "go", "rb", "ts", "tsx",
    "jsx", "vue", "less", "scss", "sass", "map", "lock",
    "pkl", "pt", "pth", "h5", "onnx", "pb", "model", "npy", "npz",
    "parquet", "avro", "orc", "woff", "woff2", "ttf", "eot", "otf",
    "drawio", "vsdx", "xmind", "numbers", "pages", "key",
}
# 代码类文本（量大但与面试无关）
SKIP_TEXT_EXT = {"py", "js", "css", "xml", "properties", "yml", "yaml", "ini", "cfg", "conf", "sh", "bat", "sql"}

# ---------- 分类规则（按相对路径） ----------
CATEGORY_RULES = [
    ("简历", ["01_原始资料库/06_简历"]),
    ("岗位弹药", ["03_岗位弹药库"]),
    ("方向知识库", ["02_方向知识库"]),
    ("幻灯片笔记", ["解码文本", "程序化广告学习笔记"]),
    ("腾讯文档", ["01_原始资料库/02_我的笔记/02_腾讯文档笔记"]),
    ("公开情报", ["公开情报搜集", "Boss直聘岗位数据"]),
    ("工作资料", ["01_原始资料库/03_工作资料/工作资料_腾讯", "01_原始资料库/03_工作资料/工作资料_微视"]),
    ("书籍", ["01_原始资料库/01_书籍"]),
    ("早期材料", ["早期找工作材料"]),
    ("上传截图", ["用户上传截图"]),
    ("系统引擎", ["_系统_知识库引擎"]),
]


def classify(rel):
    for name, pats in CATEGORY_RULES:
        for p in pats:
            if p in rel:
                return name
    return "其他"


# ---------- 文本提取 ----------
def read_text(path):
    try:
        for enc in ("utf-8", "gbk", "gb18030", "latin-1"):
            try:
                with open(path, encoding=enc) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
    except Exception:
        pass
    return ""


def extract_plain(path):
    return read_text(path), "direct"


def extract_pdf(path):
    try:
        from pypdf import PdfReader
    except ImportError:
        return "", "no-pypdf"
    try:
        r = PdfReader(path)
        pages = []
        for p in r.pages:
            try:
                pages.append(p.extract_text() or "")
            except Exception:
                pages.append("")
        txt = "\n".join(pages)
        method = "pypdf"
        if len(txt.strip()) < 50 and len(r.pages) > 0:
            method = "need_ocr"   # 扫描件
        return txt, method
    except Exception as e:
        return "", "fail:%s" % type(e).__name__


def extract_pptx(path):
    try:
        from pptx import Presentation
    except ImportError:
        return "", "no-pptx"
    try:
        prs = Presentation(path)
        out = []
        for i, s in enumerate(prs.slides, 1):
            buf = []
            for sh in s.shapes:
                try:
                    if sh.has_text_frame:
                        t = "\n".join(p.text for p in sh.text_frame.paragraphs if p.text.strip())
                        if t.strip():
                            buf.append(t)
                    if getattr(sh, "has_table", False) and sh.has_table:
                        for row in sh.table.rows:
                            buf.append(" | ".join(c.text.strip() for c in row.cells))
                except Exception:
                    continue
            if buf:
                out.append("--- Slide %d ---\n%s" % (i, "\n".join(buf)))
        txt = "\n".join(out)
        method = "python-pptx"
        if len(txt.strip()) < 20:
            method = "need_ocr"   # 图片型幻灯片
        return txt, method
    except Exception as e:
        return "", "fail:%s" % type(e).__name__


def extract_docx(path):
    try:
        import docx
    except ImportError:
        return "", "no-docx"
    try:
        d = docx.Document(path)
        parts = [p.text for p in d.paragraphs if p.text.strip()]
        for t in d.tables:
            for row in t.rows:
                parts.append(" | ".join(c.text.strip() for c in row.cells))
        return "\n".join(parts), "python-docx"
    except Exception as e:
        return "", "fail:%s" % type(e).__name__


def extract_doc(path):
    """老式 .doc：交给 macOS textutil 转 txt"""
    try:
        r = subprocess.run(["textutil", "-convert", "txt", "-stdout", path],
                           capture_output=True, timeout=60)
        t = r.stdout.decode("utf-8", "ignore")
        if len(t.strip()) > 10:
            return t, "textutil"
        return "", "need_ocr"
    except Exception as e:
        return "", "fail:%s" % type(e).__name__


def extract_xlsx(path):
    try:
        import openpyxl
    except ImportError:
        return "", "no-openpyxl"
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        parts = []
        for ws in wb.worksheets:
            parts.append("### sheet: %s" % ws.title)
            for i, row in enumerate(ws.iter_rows(values_only=True), 1):
                vals = [str(v) for v in row if v not in (None, "")]
                if vals:
                    parts.append(" | ".join(vals))
                if i > 500:
                    parts.append("...(截断)")
                    break
        return "\n".join(parts), "openpyxl"
    except Exception as e:
        return "", "fail:%s" % type(e).__name__


def extract_html(path):
    raw = read_text(path)
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(raw, "html.parser")
        for t in soup(["script", "style"]):
            t.decompose()
        return soup.get_text("\n", strip=True), "bs4"
    except Exception:
        txt = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw, flags=re.S | re.I)
        txt = re.sub(r"<[^>]+>", " ", txt)
        return re.sub(r"\n{3,}", "\n\n", txt).strip(), "regex"


def extract_ipynb(path):
    raw = read_text(path)
    try:
        d = json.loads(raw)
        parts = []
        for c in d.get("cells", []):
            src = "".join(c.get("source", []))
            parts.append(src)
        return "\n".join(parts), "ipynb-json"
    except Exception:
        return raw, "raw"


EXTRACTORS = {
    "md": extract_plain, "txt": extract_plain, "csv": extract_plain,
    "json": extract_plain, "log": extract_plain,
    "pdf": extract_pdf,
    "pptx": extract_pptx, "ppt": extract_pptx,
    "docx": extract_docx, "doc": extract_doc,
    "xlsx": extract_xlsx, "xls": extract_xlsx,
    "html": extract_html, "htm": extract_html,
    "ipynb": extract_ipynb,
}


def sniff_ext(path, ext):
    """按文件魔数纠正真实格式（库里有 .pdf 实为 pptx/docx 的情况）"""
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError:
        return ext
    if head[:4] == b"PK\x03\x04":
        try:
            import zipfile
            names = zipfile.ZipFile(path).namelist()[:40]
            joined = " ".join(names)
            if "ppt/" in joined or "ppt/slides" in joined:
                return "pptx"
            if "word/" in joined:
                return "docx"
            if "xl/" in joined:
                return "xlsx"
        except Exception:
            return ext
        return ext
    if head[:5] == b"%PDF-":
        return "pdf"
    if head[:4] == b"\xd0\xcf\x11\xe0":   # OLE2：老 doc/xls/ppt
        return {"pdf": "doc", "docx": "doc"}.get(ext, ext)
    return ext


def sanitize(t):
    """去掉代理字符/控制字符，避免写库时 UnicodeEncodeError"""
    if not t:
        return ""
    t = re.sub(r"[\ud800-\udfff]", "", t)              # 代理对残留
    t = t.replace("\x00", " ")
    t = "".join(ch for ch in t if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    return t


def extract(path, ext):
    real = sniff_ext(path, ext)
    fn = EXTRACTORS.get(real)
    if not fn:
        return "", "unsupported"
    try:
        txt, method = fn(path)
    except Exception as e:
        return "", "fail:%s" % type(e).__name__
    txt = re.sub(r"\n{4,}", "\n\n\n", txt or "").strip()
    txt = sanitize(txt)
    if real != ext:
        method = "%s(as %s)" % (method, ext)
    return txt, method


# ---------- 数据库 ----------
def init_db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS doc(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        path TEXT UNIQUE, rel_path TEXT, category TEXT, ext TEXT,
        size INTEGER, mtime REAL, sha TEXT,
        title TEXT, content TEXT, char_count INTEGER,
        extract_method TEXT, status TEXT, updated_at TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_doc_cat ON doc(category)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_doc_status ON doc(status)")
    # trigram 对中文子串检索友好；不支持时回退 unicode61
    # external content：FTS 不存全文影子表（省一半体积），doc 表变更靠触发器增量同步
    fts_ok = False
    for tok in ("trigram", "unicode61"):
        try:
            c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS doc_fts USING fts5("
                      "rel_path, title, content, "
                      "content='doc', content_rowid='id', tokenize='%s')" % tok)
            fts_ok = tok
            break
        except Exception:
            continue
    if not fts_ok:
        c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS doc_fts USING fts5("
                  "rel_path, title, content, content='doc', content_rowid='id')")
    # 增量同步触发器（幂等：先 DROP 再 CREATE）
    c.executescript("""
DROP TRIGGER IF EXISTS doc_fts_ai;
CREATE TRIGGER doc_fts_ai AFTER INSERT ON doc BEGIN
  INSERT INTO doc_fts(rowid, rel_path, title, content) VALUES (NEW.id, NEW.rel_path, NEW.title, NEW.content);
END;
DROP TRIGGER IF EXISTS doc_fts_ad;
CREATE TRIGGER doc_fts_ad AFTER DELETE ON doc BEGIN
  INSERT INTO doc_fts(doc_fts, rowid, rel_path, title, content) VALUES ('delete', OLD.id, OLD.rel_path, OLD.title, OLD.content);
END;
DROP TRIGGER IF EXISTS doc_fts_au;
CREATE TRIGGER doc_fts_au AFTER UPDATE OF rel_path, title, content ON doc BEGIN
  INSERT INTO doc_fts(doc_fts, rowid, rel_path, title, content) VALUES ('delete', OLD.id, OLD.rel_path, OLD.title, OLD.content);
  INSERT INTO doc_fts(rowid, rel_path, title, content) VALUES (NEW.id, NEW.rel_path, NEW.title, NEW.content);
END;
""")
    c.execute("""CREATE TABLE IF NOT EXISTS unprocessed(
        id INTEGER PRIMARY KEY AUTOINCREMENT, rel_path TEXT, kind TEXT,
        reason TEXT, status TEXT, updated_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS ingest_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT, run_at TEXT, scanned INTEGER,
        inserted INTEGER, updated INTEGER, skipped INTEGER, empty INTEGER,
        need_ocr INTEGER, failed INTEGER, elapsed_s REAL, note TEXT)""")
    conn.commit()
    return conn


def scan_files(root=KB):
    """返回 [(abs_path, rel_path, ext, size, mtime)]"""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {".git", "node_modules", "__pycache__", ".tmp_ingest"}]
        for fn in filenames:
            if fn.startswith(".") or fn.startswith("~$"):   # 隐藏文件 / Office 锁定临时文件
                continue
            ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
            if ext in SKIP_EXT:
                continue
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, root)
            if ext in SKIP_TEXT_EXT:
                continue
            if ext not in EXTRACTORS:
                continue
            try:
                st = os.stat(p)
            except OSError:
                continue
            out.append((p, rel, ext, st.st_size, st.st_mtime))
    return sorted(out, key=lambda x: x[1])


def guess_title(rel, content):
    base = os.path.basename(rel)
    base = re.sub(r"\.(md|txt|pdf|pptx?|docx?|xlsx?|html?|json|ipynb)$", "", base, flags=re.I)
    base = re.sub(r"^书籍__|^\d+[_-]", "", base)
    for line in (content or "").splitlines()[:15]:
        line = line.strip().lstrip("#").strip()
        if line and len(line) <= 60:
            return "%s · %s" % (base[:40], line)
    return base


def run(args):
    conn = init_db()
    files = scan_files()
    if args.分类:
        files = [f for f in files if classify(f[1]) == args.分类]
    if args.限制:
        files = files[: args.限制]
    print("待处理 %d 个文件" % len(files))

    stat = dict(scanned=0, inserted=0, updated=0, skipped=0, empty=0, need_ocr=0, failed=0)
    t0 = time.time()
    cur = conn.cursor()
    for i, (p, rel, ext, size, mtime) in enumerate(files, 1):
        stat["scanned"] += 1
        exist = cur.execute("SELECT mtime, status FROM doc WHERE path=?", (p,)).fetchone()
        # duplicate 视为已收敛（与保留行同内容），不再重复提取，防复活
        if exist and not args.强制 and abs(exist["mtime"] - mtime) < 1 and exist["status"] in ("ok", "duplicate"):
            stat["skipped"] += 1
            continue
        try:
            txt, method = extract(p, ext)
        except Exception as e:
            txt, method = "", "fail:%s" % type(e).__name__
        n = len(txt)
        if method.startswith("fail") or method.startswith("no-") or method == "unsupported":
            status = "failed"
            stat["failed"] += 1
        elif method == "need_ocr":
            status = "need_ocr"
            stat["need_ocr"] += 1
        elif n < 30:
            status = "empty"
            stat["empty"] += 1
        else:
            status = "ok"
        title = guess_title(rel, txt)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sha = hashlib.md5(txt.encode("utf-8", "ignore")).hexdigest()[:16] if txt else ""
        cur.execute("""INSERT INTO doc(path,rel_path,category,ext,size,mtime,sha,title,content,
                       char_count,extract_method,status,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(path) DO UPDATE SET
                       rel_path=excluded.rel_path, category=excluded.category, ext=excluded.ext,
                       size=excluded.size, mtime=excluded.mtime, title=excluded.title,
                       content=excluded.content, char_count=excluded.char_count,
                       extract_method=excluded.extract_method,
                       status=CASE WHEN doc.status='duplicate' AND doc.sha=excluded.sha
                                   THEN 'duplicate' ELSE excluded.status END,
                       updated_at=excluded.updated_at""",
                    (p, rel, classify(rel), ext, size, mtime,
                     sha, title, txt, n,
                     method, status, now))
        if exist:
            stat["updated"] += 1
        else:
            stat["inserted"] += 1
        if i % 100 == 0:
            conn.commit()
            print("  ... %d/%d" % (i, len(files)), flush=True)
    conn.commit()

    # 未处理表
    cur.execute("DELETE FROM unprocessed")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for r in cur.execute("SELECT rel_path, ext, status, extract_method FROM doc WHERE status!='ok'"):
        reason = {"need_ocr": "无文本层（扫描件/图片型），需 OCR",
                  "empty": "抽取结果为空",
                  "failed": "解析失败: %s" % r["extract_method"]}.get(r["status"], r["status"])
        cur.execute("INSERT INTO unprocessed(rel_path,kind,reason,status,updated_at) VALUES(?,?,?,?,?)",
                    (r["rel_path"], r["ext"], reason, r["status"], now))
    conn.commit()
    elapsed = round(time.time() - t0, 1)
    cur.execute("""INSERT INTO ingest_log(run_at,scanned,inserted,updated,skipped,empty,need_ocr,failed,elapsed_s,note)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), stat["scanned"], stat["inserted"],
                 stat["updated"], stat["skipped"], stat["empty"], stat["need_ocr"],
                 stat["failed"], elapsed, args.分类 or "全库"))
    conn.commit()
    # 索引由 doc 表触发器增量同步，无需全量 rebuild（仅结构迁移/修复时手动调 kb_ingest.py 索引）
    print("完成：新增 %d / 更新 %d / 跳过 %d / 空 %d / 需OCR %d / 失败 %d，用时 %ss"
          % (stat["inserted"], stat["updated"], stat["skipped"], stat["empty"],
             stat["need_ocr"], stat["failed"], elapsed))


def rebuild_fts(conn):
    """外部内容表重建：FTS 不存正文，重建=清索引+重插 ok 行（触发器保后续增量）"""
    c = conn.cursor()
    try:
        c.execute("INSERT INTO doc_fts(doc_fts) VALUES('delete-all')")
        rows = c.execute("SELECT id, rel_path, title, content FROM doc "
                         "WHERE status='ok' AND content!=''").fetchall()
        c.executemany("INSERT INTO doc_fts(rowid,rel_path,title,content) VALUES(?,?,?,?)", rows)
        conn.commit()
        print("全文索引重建：%d 篇（增量触发器已接管后续同步）" % len(rows))
    except Exception as e:
        print("FTS 重建失败：", e)


def search(kw, category=None, limit=20):
    conn = init_db()
    c = conn.cursor()
    sql_cat = " AND d.category=?" if category else ""
    params = [kw]
    if category:
        params.append(category)
    rows = []
    try:
        if len(kw) >= 3:
            rows = c.execute(
                "SELECT d.rel_path, d.title, d.category, snippet(doc_fts,2,'[',']','…',14) s "
                "FROM doc_fts JOIN doc d ON d.id = doc_fts.rowid "
                "WHERE doc_fts MATCH ? AND d.status='ok'%s LIMIT ?" % sql_cat,
                params + [limit]).fetchall()
    except Exception:
        rows = []
    if not rows:
        rows = c.execute(
            "SELECT rel_path, title, category, substr(content,1,160) s FROM doc "
            "WHERE content LIKE ?%s LIMIT ?" % sql_cat,
            ["%" + kw + "%"] + ([category] if category else []) + [limit]).fetchall()
    for r in rows:
        print("[%s] %s\n    %s\n    %s\n" % (r[2], r[0], r[1], r[3].replace("\n", " ")[:220]))
    print("命中 %d 条" % len(rows))


def stats():
    conn = init_db()
    c = conn.cursor()
    print("=== 入库总览 ===")
    for r in c.execute("SELECT status, COUNT(*), SUM(char_count) FROM doc GROUP BY status"):
        print("  %-10s %6d 篇  %10s 字" % (r[0], r[1], format(r[2] or 0, ",")))
    print("=== 按分类 ===")
    for r in c.execute("""SELECT category, COUNT(*), SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END),
                          SUM(char_count) FROM doc GROUP BY category ORDER BY 4 DESC"""):
        print("  %-8s 总%5d  成功%5d  %10s 字" % (r[0], r[1], r[2], format(r[3] or 0, ",")))
    print("=== 未处理 TOP ===")
    for r in c.execute("SELECT kind, COUNT(*) FROM unprocessed GROUP BY kind ORDER BY 2 DESC LIMIT 10"):
        print("  %-8s %d" % (r[0], r[1]))


def list_unprocessed():
    conn = init_db()
    for r in conn.execute("SELECT rel_path, reason, status FROM unprocessed ORDER BY status, rel_path LIMIT 100"):
        print("[%s] %s\n    %s" % (r[2], r[0], r[1]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["运行", "扫描", "查", "统计", "未处理", "索引"])
    ap.add_argument("kw", nargs="?")
    ap.add_argument("--分类")
    ap.add_argument("--限制", type=int)
    ap.add_argument("--限", type=int, default=20)
    ap.add_argument("--强制", action="store_true")
    a = ap.parse_args()
    if a.cmd == "运行":
        run(a)
    elif a.cmd == "扫描":
        fs = scan_files()
        from collections import Counter
        cc = Counter(classify(f[1]) for f in fs)
        print("可处理文件 %d 个" % len(fs))
        for k, v in cc.most_common():
            print("  %-8s %d" % (k, v))
    elif a.cmd == "查":
        search(a.kw, a.分类, a.限)
    elif a.cmd == "统计":
        stats()
    elif a.cmd == "未处理":
        list_unprocessed()
    elif a.cmd == "索引":
        rebuild_fts(init_db())


if __name__ == "__main__":
    main()
