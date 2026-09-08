#!/usr/bin/env python3
"""导入克隆知识库站点内容到阅读库。

用法:
  python -m openbiliclaw.scripts.import_knowledge_base --site learnbuffett
  python -m openbiliclaw.scripts.import_knowledge_base --site mungermodels
  python -m openbiliclaw.scripts.import_knowledge_base --site aichainmap
  python -m openbiliclaw.scripts.import_knowledge_base --all
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── 站点配置 ──────────────────────────────────────────────────────
SITE_CONFIG: dict[str, dict[str, Any]] = {
    "learnbuffett": {
        "dir": "learnbuffett-com/learnbuffett.com",
        "source_type": "learnbuffett",
        "source_name": "巴菲特知识库",
        "tags": ["投资", "巴菲特", "价值投资", "股东信"],
        "label": "巴菲特股东信知识库",
        "exclude_patterns": [
            r"index\.html$",
            r"book\.html$",
            r"about\.html$",
            r"changelog\.html$",
            r"graph\.html$",
            r"donate\.html$",
            r"coin-rain\.html$",
            r"talk\.html$",
            r"nav\.js$",
            r"search\.js$",
        ],
        # 要解析的页面目录
        "content_dirs": [
            "concepts",
            "companies",
            "people",
            "partnership",
            "berkshire",
            "special",
        ],
    },
    "mungermodels": {
        "dir": "mungermodels-com/mungermodels.com",
        "source_type": "mungermodels",
        "source_name": "芒格思维模型",
        "tags": ["思维模型", "芒格", "多元思维模型", "决策"],
        "label": "查理·芒格的思维模型",
        "exclude_patterns": [
            r"index\.html$",
            r"all\.html$",
            r"book\.html$",
            r"about\.html$",
            r"graph\.html$",
            r"canonical\.html$",
        ],
        "content_dirs": [
            "models",
            "disciplines",
            "scenarios",
        ],
    },
    "aichainmap": {
        "dir": "aichainmap-com/aichainmap.com",
        "source_type": "aichainmap",
        "source_name": "AI产业链地图",
        "tags": ["AI", "产业链", "芯片", "大模型", "投资"],
        "label": "AI 产业链地图",
        # 数据文件（SPA 应用，数据在 JS 中）
        "data_files": {
            "wiki-data": "data/wiki-data.js?v=3",
            "wiki-bodies": "data/wiki-bodies.js?v=1",
        },
    },
}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="导入克隆知识库到阅读库")
    p.add_argument("--site", choices=list(SITE_CONFIG) + ["all"], default="all")
    p.add_argument("--dry-run", action="store_true", help="仅扫描，不导入")
    p.add_argument("--limit", type=int, default=0, help="每个站点最多导入 N 篇")
    p.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    return p.parse_args()


def _find_html_files(site_dir: Path, config: dict) -> list[Path]:
    """找到所有需要解析的 HTML 文件。"""
    files: list[Path] = []
    for content_dir in config["content_dirs"]:
        target = site_dir.resolve() / content_dir
        if not target.is_dir():
            logger.warning("目录不存在: %s", target)
            continue
        for f in sorted(target.rglob("*.html")):
            rel = f.relative_to(site_dir.resolve())
            rel_str = str(rel)
            # 跳过排除模式
            if any(re.search(p, rel_str) for p in config["exclude_patterns"]):
                continue
            files.append(f)
    return files


def _extract_learnbuffett(soup: Any, path: Path) -> dict | None:
    """解析 learnbuffett.com 页面。"""
    title_el = soup.find("h1")
    if not title_el:
        return None
    title = title_el.get_text(strip=True)

    # 描述
    summary = ""
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        summary = meta_desc["content"]

    # 主内容
    main_el = soup.find("main", class_="main")
    article = main_el.find("article", class_="article") if main_el else None
    if not article:
        article = main_el

    # 提取正文（去掉 sidebar 等无关内容）
    content_parts: list[str] = []
    for el in article.find_all(["p", "h2", "h3", "h4", "blockquote", "ul", "ol", "div"]):
        # 跳过 backlinks 和 quotes-section
        if el.get("class") and any(
            c in ["backlinks-section", "quotes-section"] for c in el.get("class", [])
        ):
            continue
        text = el.get_text(strip=True)
        if not text or len(text) < 20:
            continue
        content_parts.append(text)

    # 提取标签
    tags = ["投资", "巴菲特", "价值投资"]

    # 从路径提取子分类
    rel = str(path.resolve())
    if "concepts" in rel:
        tags.append("投资概念")
    elif "companies" in rel:
        tags.append("公司")
    elif "people" in rel:
        tags.append("人物")
    elif "partnership" in rel:
        tags.append("合伙人信")
    elif "berkshire" in rel:
        tags.append("伯克希尔股东信")
    elif "special" in rel:
        tags.append("特别信件")

    # 提取英文名
    english_name = ""
    meta_el = soup.find("span", class_="english-name")
    if meta_el:
        english_name = meta_el.get_text(strip=True)

    return {
        "title": f"{title} {'(' + english_name + ')' if english_name else ''}",
        "summary": summary,
        "content": "\n\n".join(content_parts),
        "tags": tags,
    }


def _extract_mungermodels(soup: Any, path: Path) -> dict | None:
    """解析 mungermodels.com 页面。"""
    title_el = soup.find("h1", class_="detail__title")
    if not title_el:
        return None
    title = title_el.get_text(strip=True)

    # 英文名
    en_el = soup.find("div", class_="detail__en")
    english_name = en_el.get_text(strip=True) if en_el else ""

    # 描述
    meta_desc = soup.find("meta", attrs={"name": "description"})
    summary = meta_desc["content"] if meta_desc and meta_desc.get("content") else ""

    # 主内容
    article = soup.find("article", class_="prose")
    if not article:
        return None

    content_parts: list[str] = []
    for el in article.find_all(["p", "h2", "h3", "h4", "blockquote", "ul", "ol", "li"]):
        text = el.get_text(strip=True)
        if not text:
            continue
        # 跳过 checklist 和 faq
        parent = el.parent
        if parent and parent.get("class") and "checklist" in parent.get("class", []):
            text = "☐ " + text
        content_parts.append(text)

    # 提取标签
    tags = ["思维模型", "芒格", "多元思维模型"]

    # 从面包屑找学科
    breadcrumb = soup.find("div", class_="breadcrumb")
    if breadcrumb:
        for link in breadcrumb.find_all("a"):
            href = link.get("href", "")
            if "disciplines" in href:
                tags.append(link.get_text(strip=True))

    # 从路径提取分类
    rel = str(path)
    if "models" in rel:
        tags.append("模型")
    elif "disciplines" in rel:
        tags.append("学科")
    elif "scenarios" in rel:
        tags.append("应用场景")

    return {
        "title": f"{title} {'(' + english_name + ')' if english_name else ''}",
        "summary": summary,
        "content": "\n".join(content_parts),
        "tags": tags,
    }


def _extract_aichainmap(soup: Any, path: Path) -> dict | None:
    """解析 aichainmap.com 页面（SPA 应用，数据从 JS 加载）。"""
    return None  # 由 _import_aichainmap 直接处理数据文件


def _import_aichainmap(db: Any, site_path: Path, config: dict, args: argparse.Namespace) -> int:
    """从 aichainmap 的数据文件导入词条。"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.error("需要安装 BeautifulSoup: pip install beautifulsoup4")
        return 0

    # 读取数据文件
    data_dir = config["data_files"]
    wiki_data_file = site_path / data_dir["wiki-data"]
    wiki_bodies_file = site_path / data_dir["wiki-bodies"]

    if not wiki_data_file.exists() or not wiki_bodies_file.exists():
        logger.warning("数据文件不存在，跳过")
        return 0

    # 解析 JS 中的 JSON 数据
    def _parse_js_var(file_path: Path, var_name: str) -> dict:
        text = file_path.read_text("utf-8")
        m = re.search(rf"window\.{var_name}\s*=\s*(\{{.+?\}})\s*;?\s*$", text, re.DOTALL)
        if m:
            return json.loads(m.group(1))
        raise ValueError(f"无法解析 {var_name}")

    wiki_data = _parse_js_var(wiki_data_file, "WIKI_DATA")
    wiki_bodies = _parse_js_var(wiki_bodies_file, "WIKI_BODIES")

    meta = wiki_data.get("meta", {})
    logger.info(
        "AI产业链地图: %d 公司, %d 概念, %d 人物, %d 事件",
        meta.get("companies_count", 0),
        meta.get("concepts_count", 0),
        meta.get("people_count", 0),
        meta.get("events_count", 0),
    )

    LAYER_MAP = {
        "1": "第一层-能源与基础设施",
        "2": "第二层-计算芯片与硬件",
        "3": "第三层-平台与中间件",
        "4": "第四层-模型与能力",
        "5": "第五层-应用与产品",
    }

    imported = 0
    entity_types = [
        ("companies", "公司", "公司"),
        ("concepts", "概念", "概念"),
        ("people", "人物", "人物"),
        ("events", "事件", "事件"),
    ]

    for key, label, tag in entity_types:
        entities = wiki_data.get(key, {})
        bodies = wiki_bodies.get(key, {})
        logger.info("  导入 %s: %d 个", label, len(entities))

        for entity_name, entity_data in entities.items():
            try:
                title = entity_data.get("title", entity_name)
                entity_data.get("aliases", [])
                layer = entity_data.get("layer", "")
                subsector = entity_data.get("subsector", "")
                tags_raw = entity_data.get("tags", []) or []
                hq = entity_data.get("hq", "")
                ticker = entity_data.get("ticker", "")
                entity_data.get("status", "")

                summary_parts = []
                if layer:
                    layer_name = LAYER_MAP.get(layer, f"第{layer}层")
                    summary_parts.append(f"层级: {layer_name}")
                if subsector:
                    summary_parts.append(f"子行业: {subsector}")
                if hq:
                    summary_parts.append(f"总部: {hq}")
                if ticker:
                    summary_parts.append(f"代码: {ticker}")
                summary = " | ".join(summary_parts)[:500]

                all_tags = list(
                    set(config["tags"] + tags_raw + [label, f"层{layer}" if layer else ""])
                )
                all_tags = [t for t in all_tags if t]

                body_html = bodies.get(entity_name, "")
                content = ""
                if body_html:
                    soup = BeautifulSoup(body_html, "html.parser")
                    parts = []
                    for el in soup.find_all(["p", "h2", "h3", "h4", "li", "blockquote"]):
                        text = el.get_text(strip=True)
                        if text:
                            parts.append(text)
                    content = "\n\n".join(parts)
                else:
                    content = summary

                if not content:
                    continue

                slug = entity_name.lower().replace(" ", "-").replace("'", "").replace("&", "and")
                url = f"clone://aichainmap/{key}/{slug}"

                row_id = db.upsert_article(
                    source_type=config["source_type"],
                    source_name=config["source_name"],
                    title=title,
                    url=url,
                    summary=summary,
                    content_text=content[:100000],
                    tags=all_tags,
                )

                if row_id:
                    imported += 1
                    if args.verbose and imported % 100 == 0:
                        logger.info("  已导入 %d 篇...", imported)

            except Exception as e:
                if args.verbose:
                    import traceback

                    logger.error("  ✗ %s: %s\n%s", entity_name, e, traceback.format_exc())

    return imported


def _get_db(script_dir: Path) -> Any:
    """获取数据库连接。"""
    from openbiliclaw.storage.database import Database

    _db_paths = [
        script_dir.parent / "data" / "openbiliclaw.db",
        script_dir.parent / "data" / "cache" / "cache.db",
    ]
    try:
        from openbiliclaw.config import load_config

        cfg = load_config()
        if cfg.storage.db_path:
            _db_paths.insert(0, Path(cfg.storage.db_path))
    except Exception:
        pass
    db_path = next((p for p in _db_paths if p.exists()), None)
    if not db_path:
        logger.warning("数据库不存在，跳过导入")
        return None
    db = Database(db_path=str(db_path))
    db.initialize()
    return db


def main() -> None:
    args = _parse_args()
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(message)s")

    # 找到克隆站点目录
    script_dir = Path(__file__).resolve().parent  # scripts/
    sites_dir = script_dir.parent / "web" / "clone" / "sites"

    # 确定要导入的站点
    sites = list(SITE_CONFIG) if args.site == "all" else [args.site]

    for site_name in sites:
        config = SITE_CONFIG[site_name]
        site_path = (sites_dir / config["dir"]).resolve()
        if not site_path.is_dir():
            logger.warning("跳过 %s: 目录不存在 %s", site_name, site_path)
            continue

        # 特殊处理 aichainmap（数据文件方式）
        if site_name == "aichainmap":
            # 连接数据库
            db = _get_db(script_dir)
            if not db:
                continue
            imported = _import_aichainmap(db, site_path, config, args)
            logger.info("%s: 导入完成，共 %d 篇", config["label"], imported)
            continue

        files = _find_html_files(site_path, config)
        if not files:
            logger.warning("%s: 没有找到可导入的页面", site_name)
            continue

        logger.info("%s: 找到 %d 个页面", config["label"], len(files))

        if args.dry_run:
            for f in files[:10]:
                rel = f.relative_to(site_path.resolve())
                logger.info("  [DRY] %s", rel)
            if len(files) > 10:
                logger.info("  ... 还有 %d 个文件", len(files) - 10)
            continue

        # 导入
        extractors = {
            "learnbuffett": _extract_learnbuffett,
            "mungermodels": _extract_mungermodels,
            "aichainmap": _extract_aichainmap,
        }
        extractor = extractors.get(site_name)
        if not extractor:
            logger.error("没有提取器: %s", site_name)
            continue

        # 用 BeautifulSoup 解析
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("需要安装 BeautifulSoup: pip install beautifulsoup4")
            sys.exit(1)

        # 连接数据库
        db = _get_db(script_dir)
        if not db:
            continue

        imported = 0
        for f in files:
            try:
                html = f.read_text("utf-8")
                soup = BeautifulSoup(html, "html.parser")
                result = extractor(soup, f)
                if not result:
                    continue

                rel = f.relative_to(site_path.resolve())
                url = f"clone://{site_name}/{rel}"

                all_tags = list(set(config["tags"] + result["tags"]))

                row_id = db.upsert_article(
                    source_type=config["source_type"],
                    source_name=config["source_name"],
                    title=result["title"],
                    url=url,
                    summary=result["summary"][:500],
                    content_text=result["content"],
                    tags=all_tags,
                )

                if row_id:
                    imported += 1
                    if args.verbose:
                        logger.info("  ✓ %s → id=%s", rel, row_id)
                else:
                    logger.warning("  ✗ %s 导入失败", rel)

            except Exception as e:
                import traceback

                logger.error("  ✗ %s: %s\n%s", f.name, e, traceback.format_exc())

            if args.limit and imported >= args.limit:
                break

        logger.info("%s: 导入完成，共 %d 篇", config["label"], imported)


if __name__ == "__main__":
    main()
