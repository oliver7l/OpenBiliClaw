#!/usr/bin/env python3
"""统一相册库概览统计（输出到 stdout，可重定向成 STATS.md）。

  python stats.py > STATS.md
"""
import sqlite3

LIB = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/19_统一相册库"
DB_PATH = f"{LIB}/library.db"


def main():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    total, giga = cur.execute(
        "SELECT COUNT(*), SUM(size)/1e9 FROM files").fetchone()
    print("# 统一相册库概览\n")
    print(f"- 媒体文件总数: **{total}**（{giga:.1f} GB）")
    print(f"- 去重后唯一内容: {cur.execute('SELECT COUNT(DISTINCT content_key) FROM files').fetchone()[0]}")
    print(f"- 重复副本: {cur.execute('SELECT COUNT(*) FROM files WHERE is_primary=0').fetchone()[0]}"
          f"（已集中到 originals/_重复/）")
    print(f"- 有拍摄日期: {cur.execute('SELECT COUNT(*) FROM files WHERE taken IS NOT NULL').fetchone()[0]}")
    print(f"- 有 EXIF: {cur.execute('SELECT COUNT(*) FROM exif').fetchone()[0]}")
    print(f"- 人脸: {cur.execute('SELECT COUNT(*) FROM faces').fetchone()[0]}"
          f"（已扫描照片 {cur.execute('SELECT COUNT(DISTINCT content_key) FROM faces').fetchone()[0]} 张"
          f" + 已确认无人脸 {cur.execute('SELECT COUNT(*) FROM faces_scanned').fetchone()[0]} 张）")
    print(f"- CLIP 语义向量: {cur.execute('SELECT COUNT(*) FROM clips').fetchone()[0]}"
          f"（覆盖 {cur.execute('SELECT COUNT(DISTINCT content_key) FROM clips').fetchone()[0]} 个唯一内容）")
    print(f"- 派生版本关联(缩放/压缩副本): {cur.execute('SELECT COUNT(*) FROM derived').fetchone()[0]} 对"
          f"（跨库 {cur.execute('''SELECT COUNT(*) FROM derived d
              JOIN files a ON a.content_key=d.ck_a AND a.is_primary=1
              JOIN files b ON b.content_key=d.ck_b AND b.is_primary=1
              WHERE a.lib<>b.lib''').fetchone()[0]} 对）")
    print(f"- 人物标签: {cur.execute('SELECT COUNT(*) FROM photo_person_tags').fetchone()[0]}"
          f"（覆盖 {cur.execute('SELECT COUNT(DISTINCT content_key) FROM photo_person_tags').fetchone()[0]} 张照片）\n")

    print("## 按来源\n")
    print("| 来源 | 类型 | 数量 | 体积 GB |")
    print("|---|---|---:|---:|")
    for r in cur.execute("""SELECT source, kind, COUNT(*), SUM(size)/1e9
                            FROM files GROUP BY source, kind ORDER BY 4 DESC"""):
        print(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]:.1f} |")

    print("\n## 按拍摄年月（照片，前 20）\n")
    print("| 年月 | 数量 |")
    print("|---|---:|")
    for r in cur.execute("""SELECT ym, COUNT(*) FROM files
                            WHERE kind='photo' AND ym IS NOT NULL
                            GROUP BY ym ORDER BY ym DESC LIMIT 20"""):
        print(f"| {r[0]} | {r[1]} |")

    print("\n## 人物标签分布\n")
    print("| 人物 | 来源 | 照片数 |")
    print("|---|---|---:|")
    for r in cur.execute("""SELECT person, source, COUNT(DISTINCT content_key)
                            FROM photo_person_tags GROUP BY person, source
                            ORDER BY 3 DESC"""):
        print(f"| {r[0]} | {r[1]} | {r[2]} |")

    n_multi = cur.execute("""SELECT COUNT(*) FROM (
        SELECT content_key FROM photo_person_tags GROUP BY content_key
        HAVING COUNT(DISTINCT person) >= 2)""").fetchone()[0]
    print(f"\n多人物同框照片: **{n_multi}** 张")
    for r in cur.execute("""SELECT COUNT(*) FROM photo_person_tags t1
        JOIN photo_person_tags t2 ON t1.content_key=t2.content_key
        WHERE t1.person='乐仔' AND t2.person='七月'"""):
        print(f"- 乐仔+七月同框: {r[0]} 张")


if __name__ == "__main__":
    main()
