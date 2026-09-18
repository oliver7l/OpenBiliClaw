#!/usr/bin/env python3
"""从源库相册索引生成手机端相册页（``/album``）。

数据真值源是源库里的「乐仔的照片-相册.html」——它已经是 5206 张照片的
完整索引（缩略图名 / 拍摄时间 / 体积 / 原图相对路径，按月分组）。本脚本
**只读**源库，把这份索引重构为移动优先的页面与 PWA 资产，写进包内
``src/openbiliclaw/web/album/``，由 ``/album`` 静态挂载对外提供。

为什么要重新生成而不是直接挂源库那份 HTML：

- 源库那份是桌面版（hover 缩放、140px 网格、按文件名排序），手机上偏小；
- 原图是 ``.HEIC`` 直链，而安卓 Chrome 不认 HEIC——这里把 HEIC 条目改指
  源库侧已用 ``sips`` 预转好的 ``_heic_jpg/<大写名>.jpg``；
- 需要 PWA 三件套（manifest / apple-touch-icon / standalone）才能"添加到
  主屏幕"后当 App 用。

数据内嵌而非运行时走 API：页面自包含 → 无接口依赖、首屏直出；代价是
源库新增照片后必须重跑本脚本（与 ``openbiliclaw.lezai`` 的 sync 同款约定）。

用法::

    .venv/bin/python scripts/build_album_page.py
    .venv/bin/python scripts/build_album_page.py --source /path/to/源库
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from html import escape
from pathlib import Path
from urllib.parse import quote, unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openbiliclaw.album.paths import (  # noqa: E402
    heic_preview_dir,
    source_library_dir,
    thumbs_dir,
    web_assets_dir,
)

# 源库索引文件名（由源库侧生成，本脚本只读）
_INDEX_NAME = "乐仔的照片-相册.html"

# 一次扫描同时抓「月份分组标签」与「照片条目」，用 alternation 保持顺序
_ENTRY_RE = re.compile(
    r'<div class="mlabel">(?P<label>[^<]*)<small>(?P<meta>[^<]*)</small></div>'
    r'|<div class="cell" data-n="(?P<n>[^"]*)" data-d="(?P<d>[^"]*)"'
    r' data-s="(?P<s>\d+)">\s*<img[^>]*?src="(?P<thumb>[^"]*)"'
    r'[^>]*?data-full="(?P<full>[^"]*)"',
    re.S,
)

_ICON_SIZES = (180, 192, 512)
_ICON_FONT_CANDIDATES = (
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
)


# ── URL 构造 ────────────────────────────────────────────────────────────────


def _thumb_url(rel: str) -> str:
    """缩略图 → ``/album/thumbs/<名>``（源索引里的 src 是 ``_thumbs/...``）。"""
    name = unquote(rel.rsplit("/", 1)[-1])
    return "/album/thumbs/" + quote(name, safe="")


def _full_url(rel: str) -> str:
    """原图 → HEIC 走预转 JPEG，其余直取源库原图。

    HEIC 在安卓 WebView/Chrome 上无法解码（iOS Safari 才行），源库侧已把
    全部 HEIC 预转成 ``_heic_jpg/`` 下的 JPEG，命名规则是「stem **原样保留
    大小写**、只把扩展名换成 .jpg」（实测：iPhone 那批本就是大写
    ``07E42C6D_IMG_8726``，而 ``FullSizeRender (26)(1).heic`` 这类是小写，
    统一转大写会漏掉 132 张）。
    """
    rel = unquote(rel)
    month, _, name = rel.rpartition("/")
    stem, ext = os.path.splitext(name)
    if ext.lower() == ".heic":
        return "/album/heic/" + quote(stem + ".jpg", safe="")
    return "/album/full/" + quote(rel, safe="/")


def _parse_index(html: str) -> tuple[list[dict], list[dict]]:
    """把源索引解析成 ``(groups, photos)``；photos 按源顺序排列。"""
    groups: list[dict] = []
    photos: list[dict] = []
    current: dict | None = None
    for match in _ENTRY_RE.finditer(html):
        label = match.group("label")
        if label is not None:
            current = {
                "label": label.strip(),
                "meta": match.group("meta").strip(),
                "items": [],
            }
            groups.append(current)
            continue
        if current is None:  # 源文件结构异常：条目先于分组出现
            current = {"label": "未标注日期", "meta": "", "items": []}
            groups.append(current)
        raw_full = match.group("full")
        record = {
            "name": unquote(raw_full.rsplit("/", 1)[-1]),
            "date": match.group("d").strip(),
            "size": int(match.group("s")),
            "thumb": _thumb_url(match.group("thumb")),
            "full": _full_url(raw_full),
        }
        current["items"].append(record)
        photos.append(record)
    return groups, photos


# ── 页面模板 ────────────────────────────────────────────────────────────────

_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>乐仔的照片 · __TOTAL__ 张</title>
<meta name="theme-color" content="#ffb703">
<link rel="manifest" href="/album/manifest.json">
<link rel="apple-touch-icon" href="/album/assets/icon-180.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="乐仔相册">
<style>
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
:root{--bg:#0f1115;--fg:#e8eaed;--muted:#9aa0a6;--card:#1b1f27;--line:rgba(140,150,170,.18);
 --accent:#ffb703;--headH:104px}
@media (prefers-color-scheme:light){:root{--bg:#fff;--fg:#16181d;--muted:#6b7280;
 --card:#f1f2f4;--line:rgba(0,0,0,.09);--accent:#d97706}}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);overscroll-behavior-y:none;
 font:400 15px/1.45 -apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB",sans-serif}
header{position:sticky;top:0;z-index:20;background:var(--bg);border-bottom:1px solid var(--line);
 padding:calc(env(safe-area-inset-top) + 8px) 10px 0}
.row{display:flex;align-items:center;gap:8px;padding-bottom:8px}
h1{font-size:17px;margin:0;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
h1 em{font-style:normal;color:var(--muted);font-size:12px;font-weight:400;margin-left:6px}
.btn{background:var(--card);border:1px solid var(--line);color:var(--fg);border-radius:999px;
 width:34px;height:34px;flex:0 0 auto;font-size:15px;line-height:1;display:flex;align-items:center;
 justify-content:center;cursor:pointer;padding:0}
#searchWrap{display:none;gap:8px;padding-bottom:8px}
#searchWrap.on{display:flex}
#q{flex:1;min-width:0;background:var(--card);border:1px solid var(--line);color:var(--fg);
 border-radius:10px;padding:9px 12px;font-size:15px;appearance:none}
#sort{flex:0 0 auto;background:var(--card);color:var(--fg);border:1px solid var(--line);
 border-radius:10px;padding:9px 8px;font-size:14px}
nav{display:flex;gap:6px;overflow-x:auto;padding-bottom:10px;scrollbar-width:none;
 -webkit-overflow-scrolling:touch}
nav::-webkit-scrollbar{display:none}
.chip{flex:0 0 auto;font-size:13px;padding:5px 11px;border-radius:999px;background:var(--card);
 border:1px solid var(--line);color:var(--fg);cursor:pointer;white-space:nowrap;font:inherit;
 font-size:13px}
.chip:active{opacity:.55}
.mhead{position:sticky;top:var(--headH);z-index:10;background:var(--bg);padding:9px 12px 7px;
 font-size:14px;font-weight:600;color:var(--accent);display:flex;align-items:baseline;gap:8px}
.mhead small{color:var(--muted);font-weight:400;font-size:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(106px,1fr));gap:3px;padding:0 3px 12px}
.cell{position:relative;aspect-ratio:1;overflow:hidden;background:var(--card);border-radius:5px;
 cursor:zoom-in}
.cell img{width:100%;height:100%;object-fit:cover;display:block;background:var(--card)}
.empty{padding:40px 16px;text-align:center;color:var(--muted);font-size:14px;display:none}
#lb{position:fixed;inset:0;background:#000;z-index:100;display:none;flex-direction:column;
 touch-action:pan-y;-webkit-user-select:none;user-select:none}
#lb.on{display:flex}
#lbstage{flex:1;position:relative;display:flex;align-items:center;justify-content:center;
 overflow:hidden;min-height:0}
#lbi{max-width:100%;max-height:100%;object-fit:contain;display:block}
#lbstage .hint{position:absolute;color:#8b8b8b;font-size:13px}
#lbbar{display:flex;align-items:center;gap:12px;padding:10px 16px calc(env(safe-area-inset-bottom) + 12px);
 color:#eee;font-size:13px;background:#000}
#lbbar .sp{flex:1}
#lbbar a,#lbbar button{color:#fff;background:rgba(255,255,255,.16);border:0;border-radius:999px;
 padding:8px 15px;font-size:13px;text-decoration:none;cursor:pointer;font:inherit;font-size:13px}
#lbclose{position:absolute;top:calc(env(safe-area-inset-top) + 12px);right:12px;z-index:3;
 background:rgba(0,0,0,.5);color:#fff;border:0;width:36px;height:36px;border-radius:50%;
 font-size:16px;cursor:pointer}
#top{position:fixed;right:14px;bottom:calc(env(safe-area-inset-bottom) + 16px);z-index:30;
 background:var(--accent);color:#1a1a1a;border:0;border-radius:50%;width:42px;height:42px;
 font-size:17px;display:none;box-shadow:0 4px 14px rgba(0,0,0,.28);cursor:pointer}
#top.on{display:block}
</style>
</head>
<body>
<header>
  <div class="row">
    <h1>乐仔的照片<em>__TOTAL__ 张</em></h1>
    <button class="btn" id="sbtn" aria-label="搜索">&#128269;</button>
    <button class="btn" id="tbtn" aria-label="筛选排序">&#8645;</button>
  </div>
  <div id="searchWrap">
    <input type="search" id="q" placeholder="搜日期或文件名…" enterkeyhint="search">
    <select id="sort">
      <option value="date">拍摄时间</option>
      <option value="date-desc">时间倒序</option>
      <option value="name">文件名</option>
      <option value="size">体积</option>
    </select>
  </div>
  <nav id="chips">__CHIPS__</nav>
</header>
<main id="content">__BODY__</main>
<p class="empty" id="empty">没有匹配的照片</p>
<button id="top" aria-label="回到顶部">&#8593;</button>
<div id="lb">
  <button id="lbclose" aria-label="关闭">&#10005;</button>
  <div id="lbstage"><span class="hint" id="lbhint">载入中…</span><img id="lbi" alt=""></div>
  <div id="lbbar"><span id="lbc"></span><span class="sp"></span><a id="lbd" download>保存</a></div>
</div>
<script>__JS__</script>
</body>
</html>
"""

_JS = r"""
(function(){
  var head=document.querySelector('header'), root=document.documentElement;
  function syncHead(){root.style.setProperty('--headH', head.offsetHeight+'px')}
  syncHead(); addEventListener('resize', syncHead);

  // ── 搜索 / 排序面板 ───────────────────────────────────────────
  var sw=document.getElementById('searchWrap'), q=document.getElementById('q');
  document.getElementById('sbtn').onclick=function(){
    sw.classList.toggle('on'); syncHead(); if(sw.classList.contains('on')) q.focus();
  };
  document.getElementById('tbtn').onclick=function(){
    sw.classList.add('on'); document.getElementById('sort').focus(); syncHead();
  };

  // ── 月份快捷跳转 ──────────────────────────────────────────────
  var chips=document.getElementById('chips');
  chips.addEventListener('click',function(e){
    var b=e.target.closest('.chip'); if(!b) return;
    var sec=document.getElementById('g'+b.dataset.g); if(!sec) return;
    var y=sec.getBoundingClientRect().top+scrollY-head.offsetHeight-2;
    scrollTo({top:y,behavior:'smooth'});
  });

  // ── 筛选 / 排序 ───────────────────────────────────────────────
  var secs=[].slice.call(document.querySelectorAll('section.m'));
  var empty=document.getElementById('empty');
  function apply(){
    var s=q.value.toLowerCase().trim(), hit=0;
    secs.forEach(function(sec){
      var n=0;
      [].forEach.call(sec.querySelectorAll('.cell'),function(c){
        var ok=!s||c.dataset.n.toLowerCase().indexOf(s)>-1||(c.dataset.d||'').indexOf(s)>-1;
        c.style.display=ok?'':'none'; if(ok) n++;
      });
      sec.style.display=n?'':'none'; hit+=n;
    });
    empty.style.display=hit?'none':'block'; syncHead();
  }
  q.addEventListener('input',apply);
  document.getElementById('sort').addEventListener('change',function(e){
    var m=e.target.value;
    secs.forEach(function(sec){
      var g=sec.querySelector('.grid');
      var arr=[].slice.call(g.children);
      if(m==='date-desc') arr.reverse();
      else arr.sort(function(a,b){
        var ka,kb;
        if(m==='name'){ka=a.dataset.n;kb=b.dataset.n}
        else if(m==='size'){ka=+a.dataset.s;kb=+b.dataset.s}
        else{ka=a.dataset.d||'~';kb=b.dataset.d||'~'}
        return ka<kb?-1:(ka>kb?1:0);
      });
      arr.forEach(function(x){g.appendChild(x)});
    });
  });

  // ── 灯箱：查看原图 / 左右滑动 ─────────────────────────────────
  var lb=document.getElementById('lb'), lbi=document.getElementById('lbi'),
      lbc=document.getElementById('lbc'), lbd=document.getElementById('lbd'),
      hint=document.getElementById('lbhint');
  var view=[], cur=0;
  function visible(){return [].slice.call(document.querySelectorAll('.cell'))
    .filter(function(c){return c.style.display!=='none'});}
  function draw(i){
    if(!view.length) return;
    cur=(i+view.length)%view.length;
    var c=view[cur], url=c.dataset.f;
    hint.style.display=''; hint.textContent='载入中…';
    lbc.textContent=(cur+1)+' / '+view.length+(c.dataset.d?' · '+c.dataset.d:'');
    lbd.href=url; lbd.setAttribute('download',c.dataset.n);
    lbi.onload=function(){hint.style.display='none'};
    lbi.onerror=function(){hint.textContent='这张打不开（可能是 HEIC 原图）'; hint.style.display=''};
    lbi.src=url;
  }
  function open(c){view=visible(); var n=view.indexOf(c); draw(n<0?0:n); lb.classList.add('on');
    history.pushState({lb:1},'');}
  function close(){lb.classList.remove('on'); lbi.removeAttribute('src');}
  document.addEventListener('click',function(e){
    var c=e.target.closest('.cell'); if(c) open(c);
  });
  document.getElementById('lbclose').onclick=close;
  addEventListener('popstate',function(){if(lb.classList.contains('on')) close()});
  addEventListener('keydown',function(e){
    if(!lb.classList.contains('on')) return;
    if(e.key==='Escape') close();
    if(e.key==='ArrowLeft') draw(cur-1);
    if(e.key==='ArrowRight') draw(cur+1);
  });
  var tx=0, ty=0, moved=false;
  lb.addEventListener('touchstart',function(e){
    tx=e.touches[0].clientX; ty=e.touches[0].clientY; moved=false;
  },{passive:true});
  lb.addEventListener('touchmove',function(e){
    var dx=e.touches[0].clientX-tx, dy=e.touches[0].clientY-ty;
    if(Math.abs(dx)>12&&Math.abs(dx)>Math.abs(dy)) moved=true;
  },{passive:true});
  lb.addEventListener('touchend',function(e){
    if(!moved) return;
    var dx=e.changedTouches[0].clientX-tx;
    if(Math.abs(dx)>45) draw(cur+(dx<0?1:-1)); moved=false;
  },{passive:true});

  // ── 回到顶部 ─────────────────────────────────────────────────
  var top=document.getElementById('top');
  addEventListener('scroll',function(){top.classList.toggle('on',scrollY>700)},{passive:true});
  top.onclick=function(){scrollTo({top:0,behavior:'smooth'})};
})();
"""


# ── 资产生成 ────────────────────────────────────────────────────────────────


def _fmt_size(n: int) -> str:
    if n >= 1 << 30:
        return f"{n / (1 << 30):.1f}GB"
    if n >= 1 << 20:
        return f"{n / (1 << 20):.0f}MB"
    return f"{n / 1024:.0f}KB"


def _render_body(groups: list[dict]) -> tuple[str, str]:
    chips: list[str] = []
    blocks: list[str] = []
    for idx, group in enumerate(groups):
        chips.append(
            f'<button class="chip" data-g="{idx}">{escape(group["label"])}</button>'
        )
        cells = []
        for item in group["items"]:
            cells.append(
                '<div class="cell" data-n="{name}" data-d="{date}" data-s="{size}"'
                ' data-f="{full}"><img loading="lazy" decoding="async" src="{thumb}"'
                ' alt="{name}"></div>'.format(
                    name=escape(item["name"], quote=True),
                    date=escape(item["date"], quote=True),
                    size=item["size"],
                    full=escape(item["full"], quote=True),
                    thumb=escape(item["thumb"], quote=True),
                )
            )
        blocks.append(
            '<section class="m" id="g{g}"><div class="mhead">{label}<small>{meta}</small>'
            '</div><div class="grid">{cells}</div></section>'.format(
                g=idx,
                label=escape(group["label"]),
                meta=escape(group["meta"]),
                cells="".join(cells),
            )
        )
    return "".join(chips), "".join(blocks)


def _write_icons(assets_dir: Path) -> list[str]:
    """生成 PWA 图标（暖色渐变 + 「乐」字），返回写出的文件名。"""
    from PIL import Image, ImageDraw, ImageFont

    font_path = next((p for p in _ICON_FONT_CANDIDATES if Path(p).is_file()), None)
    written: list[str] = []
    for size in _ICON_SIZES:
        bg = Image.new("RGB", (size, size), "#ffb703")
        px = bg.load()
        for y in range(size):  # 竖直渐变：#ffd166 → #f08c00
            t = y / max(size - 1, 1)
            r = int(0xFF + (0xF0 - 0xFF) * t)
            g = int(0xD1 + (0x8C - 0xD1) * t)
            b = int(0x66 + (0x00 - 0x66) * t)
            for x in range(size):
                px[x, y] = (r, g, b)
        if font_path:
            font = ImageFont.truetype(font_path, int(size * 0.58))
            draw = ImageDraw.Draw(bg)
            box = draw.textbbox((0, 0), "乐", font=font)
            draw.text(
                ((size - (box[2] - box[0])) / 2 - box[0],
                 (size - (box[3] - box[1])) / 2 - box[1]),
                "乐",
                font=font,
                fill="#ffffff",
            )
        name = f"icon-{size}.png"
        bg.save(assets_dir / name, "PNG", optimize=True)
        written.append(name)
    return written


def _manifest(icon_names: list[str]) -> str:
    icons = []
    for name in icon_names:
        if name.endswith("180.png"):
            continue  # 180px 是 apple-touch-icon 专用，不进 manifest
        size = name.removeprefix("icon-").removesuffix(".png")
        icons.append(
            {
                "src": f"/album/assets/{name}",
                "sizes": f"{size}x{size}",
                "type": "image/png",
                "purpose": "any",
            }
        )
    return json.dumps(
        {
            "name": "乐仔的照片",
            "short_name": "乐仔相册",
            "start_url": "/album/",
            "scope": "/album/",
            "display": "standalone",
            "orientation": "portrait",
            "background_color": "#0f1115",
            "theme_color": "#ffb703",
            "icons": icons,
        },
        ensure_ascii=False,
        indent=2,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="生成手机端相册页（/album）")
    parser.add_argument("--source", help="源库目录（默认取 album.paths 的锚点）")
    args = parser.parse_args()

    source = Path(args.source).expanduser() if args.source else source_library_dir()
    index_path = source / _INDEX_NAME
    if not index_path.is_file():
        print(f"[fail] 源库索引不存在：{index_path}", file=sys.stderr)
        return 1

    groups, photos = _parse_index(index_path.read_text(encoding="utf-8"))
    total = len(photos)
    if not total:
        print("[fail] 索引解析出 0 张照片，源文件结构可能变了", file=sys.stderr)
        return 1

    # HEIC 预览齐不齐——缺了页面上就是一个个打不开的黑格，必须显式报出来
    heic_total = sum(1 for p in photos if p["full"].startswith("/album/heic/"))
    have = {p.name for p in heic_preview_dir().glob("*")} if heic_preview_dir().is_dir() else set()
    heic_missing = [
        p for p in photos
        if p["full"].startswith("/album/heic/") and unquote(p["full"].rsplit("/", 1)[-1]) not in have
    ]
    thumb_missing = [p for p in photos if not (thumbs_dir() / unquote(p["thumb"].rsplit("/", 1)[-1])).is_file()]

    chips, body = _render_body(groups)
    html = (
        _PAGE.replace("__CHIPS__", chips)
        .replace("__BODY__", body)
        .replace("__JS__", _JS)
        .replace("__TOTAL__", f"{total}")
    )

    assets = web_assets_dir()
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "index.html").write_text(html, encoding="utf-8")
    (assets / "assets").mkdir(exist_ok=True)
    icons = _write_icons(assets / "assets")
    (assets / "manifest.json").write_text(_manifest(icons), encoding="utf-8")

    total_bytes = sum(p["size"] for p in photos)
    print(f"[ok] 照片 {total} 张 / {_fmt_size(total_bytes)}；分组 {len(groups)} 个")
    print(f"[ok] HEIC {heic_total} 张（预览缺失 {len(heic_missing)}）")
    if heic_missing:
        for p in heic_missing[:5]:
            print(f"     [miss] {p['name']}", file=sys.stderr)
    print(f"[ok] 缩略图缺失 {len(thumb_missing)}")
    if thumb_missing:
        for p in thumb_missing[:5]:
            print(f"     [miss] {p['name']}", file=sys.stderr)
    print(f"[ok] 页面 → {assets / 'index.html'}（{_fmt_size((assets / 'index.html').stat().st_size)}）")
    print(f"[ok] 图标 → {', '.join(icons)} + manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
