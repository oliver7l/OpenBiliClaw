import sqlite3
c=sqlite3.connect("data/openbiliclaw.db")
TOT=c.execute("SELECT count(*) FROM articles").fetchone()[0]
print("全库文章总数:", TOT)

empty=c.execute("SELECT count(*) FROM articles WHERE TRIM(IFNULL(content_text,''))='' OR length(content_text)<10").fetchone()[0]
print("[维度1] 无正文(空或<10字):", empty, f"({empty*100/TOT:.1f}%)")

mb_empty=c.execute("SELECT count(*) FROM articles WHERE tags LIKE '%mindback%' AND (TRIM(IFNULL(content_text,''))='' OR length(content_text)<10)").fetchone()[0]
mb=c.execute("SELECT count(*) FROM articles WHERE tags LIKE '%mindback%'").fetchone()[0]
print(f"[维度2] mindback批次 空壳: {mb_empty}/{mb} ({mb_empty*100/mb:.1f}%)")

dup_url=c.execute("SELECT count(*) FROM (SELECT url,count(*) n FROM articles GROUP BY url HAVING n>1)").fetchone()[0]
print("[维度3] 同 url 重复组数:", dup_url)

print("\n[维度4] 各源 空壳数 (无正文)")
print(f"{'source_type':<18}{'空壳':>8}{'总量':>8}{'空壳率':>8}")
for st,tot in c.execute("SELECT source_type,count(*) FROM articles GROUP BY source_type ORDER BY 2 DESC"):
    e=c.execute("SELECT count(*) FROM articles WHERE source_type=? AND (TRIM(IFNULL(content_text,''))='' OR length(content_text)<10)",(st,)).fetchone()[0]
    if e>0:
        print(f"{st:<18}{e:>8}{tot:>8}{e*100/tot:>7.1f}%")

# 维度5: 重复 note_id / bvid (同内容多行) 在 xhs/bilibili 里
print("\n[维度5] 小红书 同 note_id 多行检查 (用 url 提取 id)")
for st,pat in [("xiaohongshu","explore/%"),("bilibili","video/%"),("youtube","watch%")]:
    dup=c.execute(f"""SELECT count(*) FROM (
        SELECT substr(url, instr(url,'{pat.replace('%','')}')+length('{pat.replace('%','')}')) as id, count(*) n
        FROM articles WHERE source_type='{st}' GROUP BY id HAVING n>1)""").fetchone()[0]
    print(f"  {st} 重复组: {dup}")
PY