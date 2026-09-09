#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb_ocr.py —— 图片型文档 OCR 入库（macOS Vision 原生 OCR，中英混排效果最好）

背景：知识库里有两类文档用常规提取器拿不到正文
  1) PPTX「图片容器」：slide 里只有标题，正文是整页截图 / 图片
  2) 扫描版 PDF：整页是图像，无文字层

策略：
  PPTX  → 解压 ppt/media/* 直接 OCR 内嵌图（比转 PDF 再渲染快，且不丢内容）
  PDF   → PyMuPDF 渲染每页为 200dpi PNG → OCR

用法：
  python kb_ocr.py 测试 <文件路径>          # 单文件试跑，打印前 500 字
  python kb_ocr.py 预览                     # 列出待 OCR 清单（按体积）
  python kb_ocr.py 执行 [N] [--最大页数 M]   # 批量执行并写回数据库

依赖：pymupdf, pyobjc-framework-Vision, pyobjc-framework-Quartz
"""
import os
import re
import sys
import time
import zipfile
import sqlite3
import datetime
import subprocess
import tempfile

KB = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/求职知识库"
DB = os.path.join(KB, "_系统_知识库引擎/数据/面试资料总库.db")
TMP = "/tmp/kb_ocr_tmp"
os.makedirs(TMP, exist_ok=True)

# ────────────────────────── Vision OCR ──────────────────────────
_VISION = None


def _init_vision():
    global _VISION
    if _VISION is not None:
        return _VISION
    import Vision
    from Foundation import NSURL
    from Quartz import CIImage

    def ocr(img_path):
        url = NSURL.fileURLWithPath_(img_path)
        ci = CIImage.imageWithContentsOfURL_(url)
        if ci is None:
            return ""
        handler = Vision.VNImageRequestHandler.alloc().initWithCIImage_options_(ci, None)
        out = []

        def done(request, error):
            obs = request.results()
            if not obs:
                return
            for o in obs:
                try:
                    out.append(o.topCandidates_(1)[0].string())
                except Exception:
                    pass

        req = Vision.VNRecognizeTextRequest.alloc().initWithCompletionHandler_(done)
        req.setRecognitionLanguages_(["zh-Hans", "en-US"])
        req.setUsesLanguageCorrection_(True)
        req.setRecognitionLevel_(0)  # 0=accurate
        try:
            handler.performRequests_error_([req], None)
        except Exception:
            return ""
        return "\n".join(out)

    _VISION = ocr
    return ocr


def ocr_image(path):
    """OCR 单张图片，返回识别文本"""
    try:
        return _init_vision()(path) or ""
    except Exception as e:
        return ""


# ────────────────────────── 抽取器 ──────────────────────────
MEDIA_EXT = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".emf", ".wmf")


def _natkey(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def pptx_xml(path):
    """直接从 slide/notes XML 抽全部 <a:t>（含 group 嵌套、备注）
    —— 比 python-pptx 更稳，且能拿到备注页里的大段内容"""
    z = zipfile.ZipFile(path)
    out = []

    def texts(x):
        return [t.strip() for t in re.findall(r"<a:t>(.*?)</a:t>", x, re.S) if t.strip()]

    sl = sorted([n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)],
                key=lambda x: int(re.findall(r"\d+", x)[0]))
    for i, sn in enumerate(sl):
        ts = texts(z.read(sn).decode("utf-8", "ignore"))
        if ts:
            out.append("--- P%d ---\n%s" % (i + 1, " ".join(ts)))
    nt = sorted([n for n in z.namelist() if re.match(r"ppt/notesSlides/notesSlide\d+\.xml$", n)],
                key=lambda x: int(re.findall(r"\d+", x)[0]))
    for n in nt:
        ts = texts(z.read(n).decode("utf-8", "ignore"))
        if ts:
            out.append("--- 备注 ---\n%s" % " ".join(ts))
    z.close()
    return "\n".join(out)


def ocr_pptx(path, max_img=400, min_side=200, with_ocr=True, media_min_mb=0.3):
    """PPTX 提取 = XML 文字层 + （媒体体积大时）OCR 内嵌图补充

    实测：多数「空壳」PPT 其实有文字层，只是老提取器没拿到；
    只有 media 很大的（内容整页是截图）才真正需要 OCR。
    """
    txt_xml = pptx_xml(path)
    if not with_ocr:
        return txt_xml, 0
    z = zipfile.ZipFile(path)
    med = [i for i in z.infolist() if i.filename.startswith("ppt/media/")]
    if sum(i.file_size for i in med) / 1048576 < media_min_mb:
        z.close()
        return txt_xml, 0
    media = [i.filename for i in med if i.filename.lower().endswith(MEDIA_EXT)]
    media.sort(key=lambda n: -z.getinfo(n).file_size)
    media = media[:max_img]
    media.sort(key=_natkey)

    outs, n_img = [], 0
    for name in media:
        info = z.getinfo(name)
        if info.file_size < 3000:
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext in (".emf", ".wmf"):
            continue
        tmp = os.path.join(TMP, "m_%d%s" % (n_img, ext if ext != ".gif" else ".png"))
        try:
            with open(tmp, "wb") as f:
                f.write(z.read(name))
            # 超大图先缩，避免 Vision 卡死
            try:
                subprocess.run(["sips", "-Z", "2400", tmp, "--out", tmp],
                               capture_output=True, timeout=30)
            except Exception:
                pass
            try:
                out = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", tmp],
                                     capture_output=True, text=True, timeout=10).stdout
                mm = re.findall(r"pixel(?:Width|Height): (\d+)", out)
                if len(mm) == 2 and (int(mm[0]) < min_side or int(mm[1]) < min_side):
                    continue
            except Exception:
                pass
            t = ocr_image(tmp)
            if t.strip():
                outs.append("--- 图 %s ---\n%s" % (os.path.basename(name), t.strip()))
                n_img += 1
        except Exception:
            pass
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    z.close()
    all_txt = txt_xml
    if outs:
        all_txt += "\n\n=== 图片 OCR ===\n" + "\n\n".join(outs)
    return all_txt, n_img


def ocr_pdf(path, max_page=2000, dpi=200, do_ocr=True):
    """渲染 PDF 每页 → OCR。若页面已有文字层则直接取文字（更快更准）"""
    import pymupdf
    d = pymupdf.open(path)
    outs = []
    n_img = 0
    for i, page in enumerate(d):
        if i >= max_page:
            break
        txt = page.get_text().strip()
        if len(txt) > 120:  # 有文字层
            outs.append("--- P%d ---\n%s" % (i + 1, txt))
            continue
        if not do_ocr:      # 快提模式：只取文字层，不渲染 OCR
            continue
        try:
            pix = page.get_pixmap(dpi=dpi)
            if pix.width < 100:
                continue
            tmp = os.path.join(TMP, "p_%d.png" % i)
            pix.save(tmp)
            t = ocr_image(tmp)
            os.remove(tmp)
            if t.strip():
                outs.append("--- P%d(OCR) ---\n%s" % (i + 1, t.strip()))
                n_img += 1
        except Exception:
            pass
    d.close()
    return "\n\n".join(outs), n_img


def docx_xml(path):
    """docx 文字层：word/document.xml 的 <w:t>，保留段落结构"""
    z = zipfile.ZipFile(path)
    out = []
    for n in ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml"):
        if n not in z.namelist():
            continue
        x = z.read(n).decode("utf-8", "ignore")
        # 按段落切分
        for pm in re.finditer(r"<w:p[ >].*?</w:p>", x, re.S):
            ts = [t for t in re.findall(r"<w:t[^>]*>(.*?)</w:t>", pm.group(0), re.S) if t.strip()]
            if ts:
                out.append("".join(ts))
    z.close()
    return "\n".join(out)


def clean(t):
    t = re.sub(r"[ \t\xa0]+", " ", t)
    t = re.sub(r"\n\s*\n(\s*\n)+", "\n\n", t)
    return t.strip()


# ────────────────────────── 命令 ──────────────────────────
def cmd_测试(path):
    ext = os.path.splitext(path)[1].lower()
    t0 = time.time()
    if ext == ".pptx":
        txt, n = ocr_pptx(path)
    elif ext == ".pdf":
        txt, n = ocr_pdf(path, max_page=6)
    else:
        print("不支持:", ext)
        return
    txt = clean(txt)
    print("图片数/页数: %d | 耗时 %.1fs | 文本 %d 字" % (n, time.time() - t0, len(txt)))
    print("─" * 66)
    print(txt[:500])


def cmd_预览():
    conn = sqlite3.connect(DB, timeout=180)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT path,rel_path,char_count,size,ext FROM doc "
        "WHERE (status='empty' OR char_count<200) AND ext IN ('pptx','pdf','docx','ppt','pptx') "
        "ORDER BY size DESC").fetchall()
    print("待 OCR %d 篇\n" % len(rows))
    for r in rows:
        if not os.path.exists(r["path"]):
            continue
        print("  %7.1fMB  %-5s %5dB  %s" % (
            r["size"] / 1048576, r["ext"], r["char_count"], os.path.basename(r["rel_path"])[:56]))


def cmd_执行(limit=None, max_page=2000, skip_books=True, with_ocr=True):
    conn = sqlite3.connect(DB, timeout=300)
    conn.row_factory = sqlite3.Row
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    q = ("SELECT id,path,rel_path,char_count,size,ext,category FROM doc "
         "WHERE (status='empty' OR char_count<200) AND ext IN ('pptx','pdf','docx','ppt') "
         "ORDER BY size DESC")
    rows = [r for r in conn.execute(q).fetchall() if os.path.exists(r["path"])]
    if skip_books:
        rows = [r for r in rows if r["category"] != "技术书籍"]
    if limit:
        rows = rows[:limit]
    print("本批 %d 篇（图 OCR %s）\n" % (len(rows), "开" if with_ocr else "关"))
    ok = fail = 0
    t0 = time.time()
    for r in rows:
        name = os.path.basename(r["rel_path"])[:44]
        try:
            n = 0
            if r["ext"] == "pptx":
                txt, n = ocr_pptx(r["path"], with_ocr=with_ocr)
                method = "PPTX XML+备注" + ("+图OCR" if n else "")
            elif r["ext"] == "pdf":
                import pymupdf
                _d = pymupdf.open(r["path"])
                _n = _d.page_count
                _d.close()
                if _n > 30 and with_ocr:
                    # 大部头扫描书：单篇几百页，OCR 成本过高，留到专项任务
                    conn.execute(
                        "UPDATE doc SET status='need_ocr', extract_method='扫描版(%d页)待专项OCR', updated_at=? WHERE id=?" % _n,
                        (now, r["id"]))
                    conn.commit()
                    print("  ⏸ %-44s %d页扫描版，留专项" % (name, _n))
                    continue
                txt, n = ocr_pdf(r["path"], max_page=max_page, do_ocr=with_ocr)
                method = "PDF 文字层" + ("/OCR" if with_ocr else "")
            elif r["ext"] == "docx":
                txt = docx_xml(r["path"])
                method = "DOCX XML直提"
            else:
                continue
            txt = clean(txt)
            cjk = sum(1 for ch in txt if "\u4e00" <= ch <= "\u9fff")
            if cjk < 30:
                print("  ✗ %-44s 中文%d（内容不足）" % (name, cjk))
                fail += 1
                continue
            conn.execute(
                "UPDATE doc SET content=?, char_count=?, status='ok', "
                "extract_method=?, updated_at=? WHERE id=?",
                (txt, len(txt), method, now, r["id"]))
            conn.commit()
            ok += 1
            print("  ✓ %-44s 图/页%3d → %6d字(中文%5d) 累计%.0fs" % (
                name, n, len(txt), cjk, time.time() - t0))
        except Exception as e:
            print("  ! %-44s 异常 %s" % (name, str(e)[:44]))
            fail += 1
    print("\n完成：成功 %d / 失败 %d / 耗时 %.0fs" % (ok, fail, time.time() - t0))
    tot = conn.execute("SELECT COUNT(*) FROM doc WHERE status='ok'").fetchone()[0]
    w = conn.execute("SELECT SUM(char_count) FROM doc WHERE status='ok'").fetchone()[0] or 0
    print("库内：%d 篇 / %.0f 万字" % (tot, w / 10000))


if __name__ == "__main__":
    a = sys.argv[1] if len(sys.argv) > 1 else "预览"
    if a == "测试":
        cmd_测试(sys.argv[2])
    elif a == "预览":
        cmd_预览()
    elif a == "执行":
        n = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else None
        mp = 2000
        for i, s in enumerate(sys.argv):
            if s == "--最大页数" and i + 1 < len(sys.argv):
                mp = int(sys.argv[i + 1])
        cmd_执行(n, max_page=mp)
    elif a == "快提":
        # 只做 XML/文字层直提，不做图片 OCR（秒级完成，覆盖大多数空壳）
        cmd_执行(with_ocr=False)
    else:
        print(__doc__)
