#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全库文件索引生成器 build_index.py
扫描 01原始资料库 / 02方向知识库 / 03岗位弹药库 全部文件，
生成:
  - 数据/06_全库文件索引.csv   (便于用表格打开查看)
  - 数据/knowledge.db          (SQLite 完整数据库,含 index 表)
用法:
  python3 scripts/build_index.py
"""
import csv, os, sqlite3, time

# 知识库根目录：由脚本位置推导（scripts/ → _系统_知识库引擎 → 根），便携可迁移
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE = os.path.join(ROOT, "_系统_知识库引擎")
DATA = os.path.join(ENGINE, "数据")
LAYERS = ["01_原始资料库", "02_方向知识库", "03_岗位弹药库"]

# 扩展名 -> 类型
EXT_TYPE = {
    ".md": "文档", ".markdown": "文档", ".txt": "文本", ".doc": "Word",
    ".docx": "Word", ".pdf": "PDF", ".xls": "表格", ".xlsx": "表格",
    ".csv": "表格", ".ppt": "PPT", ".pptx": "PPT", ".png": "图片",
    ".jpg": "图片", ".jpeg": "图片", ".gif": "图片", ".webp": "图片",
    ".zip": "压缩包", ".rar": "压缩包", ".7z": "压缩包", ".html": "网页",
    ".htm": "网页", ".json": "数据", ".py": "脚本", ".sh": "脚本",
    ".mp4": "视频", ".mov": "视频", ".mp3": "音频", ".wav": "音频",
}

def classify_ext(fn):
    ext = os.path.splitext(fn)[1].lower()
    return EXT_TYPE.get(ext, "其他")

def is_junk(fn):
    return fn.startswith("._") or fn == ".DS_Store" or fn.startswith("~$")

def main():
    rows = []
    for layer in LAYERS:
        base = os.path.join(ROOT, layer)
        if not os.path.isdir(base):
            continue
        for root, dirs, files in os.walk(base):
            # 跳过隐藏目录
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            rel_root = os.path.relpath(root, ROOT)
            # 子层 = 相对 root 的第一段(如 工作资料_腾讯 / 推荐系统 / 大宇无限-广告岗-面试准备)
            parts = rel_root.split(os.sep)
            sub = parts[1] if len(parts) > 1 else (parts[0] if len(parts) == 1 else "")
            for fn in sorted(files):
                if is_junk(fn):
                    continue
                fp = os.path.join(root, fn)
                rel = os.path.relpath(fp, ROOT)
                try:
                    size = os.path.getsize(fp)
                    mtime = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(fp)))
                except OSError:
                    size, mtime = 0, ""
                rows.append({
                    "路径": rel,
                    "层": layer,
                    "子层": sub,
                    "类型": classify_ext(fn),
                    "文件名": fn,
                    "扩展名": os.path.splitext(fn)[1].lower(),
                    "大小KB": round(size / 1024, 1),
                    "修改日期": mtime,
                })

    # 写 CSV
    csv_path = os.path.join(DATA, "06_全库文件索引.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["路径", "层", "子层", "类型", "文件名", "扩展名", "大小KB", "修改日期"])
        w.writeheader()
        w.writerows(rows)

    # 写 SQLite
    db_path = os.path.join(DATA, "knowledge.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE file_index(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        路径 TEXT, 层 TEXT, 子层 TEXT, 类型 TEXT,
        文件名 TEXT, 扩展名 TEXT, 大小KB REAL, 修改日期 TEXT)""")
    cur.execute("CREATE INDEX idx_layer ON file_index(层)")
    cur.execute("CREATE INDEX idx_sub ON file_index(子层)")
    cur.execute("CREATE INDEX idx_type ON file_index(类型)")
    cur.executemany("""INSERT INTO file_index(路径,层,子层,类型,文件名,扩展名,大小KB,修改日期)
        VALUES(:路径,:层,:子层,:类型,:文件名,:扩展名,:大小KB,:修改日期)""", rows)
    # 统计视图
    cur.execute("""CREATE VIEW layer_stats AS
        SELECT 层, COUNT(*) AS 文件数, SUM(CASE WHEN 类型='文档' OR 类型='文本' THEN 1 ELSE 0 END) AS 可检索文本
        FROM file_index GROUP BY 层""")
    conn.commit()
    conn.close()

    print(f"全库索引完成: 共 {len(rows)} 个文件")
    for layer in LAYERS:
        n = sum(1 for r in rows if r["层"] == layer)
        print(f"  {layer}: {n} 个")
    print(f"输出: {csv_path}")
    print(f"输出: {db_path}")

if __name__ == "__main__":
    main()
