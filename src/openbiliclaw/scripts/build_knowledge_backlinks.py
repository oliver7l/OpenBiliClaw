#!/usr/bin/env python3
"""构建知识库概念反向索引。

从克隆站点的原始 HTML（或数据文件）中提取维基链接（wikilink），
构建 knowledge_concepts + knowledge_backlinks 反向索引表。

用法:
  python src/openbiliclaw/scripts/build_knowledge_backlinks.py --clear
  python src/openbiliclaw/scripts/build_knowledge_backlinks.py --source aichainmap
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None  # type: ignore


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="构建知识库概念反向索引")
    p.add_argument(
        "--source", default="all", choices=["all", "learnbuffett", "mungermodels", "aichainmap"]
    )
    p.add_argument("--clear", action="store_true", help="清空已有数据重新构建")
    p.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    return p.parse_args()


def _get_conn() -> sqlite3.Connection | None:
    """获取数据库连接。"""
    script_dir = Path(__file__).resolve().parent
    _paths = [
        script_dir.parent / "data" / "openbiliclaw.db",
        script_dir.parent / "data" / "cache" / "cache.db",
    ]
    try:
        from openbiliclaw.config import load_config

        cfg = load_config()
        if cfg.storage.db_path:
            _paths.insert(0, Path(cfg.storage.db_path))
    except Exception:
        pass
    db_path = next((p for p in _paths if p.exists()), None)
    if not db_path:
        logger.error("数据库不存在")
        return None
    conn = sqlite3.connect(str(db_path))
    logger.info("连接数据库: %s", db_path)
    return conn


def _clear_tables(conn: sqlite3.Connection) -> None:
    logger.info("清空已有概念数据...")
    conn.execute("DELETE FROM knowledge_concepts")
    conn.execute("DELETE FROM knowledge_backlinks")
    conn.commit()


# ── learnbuffett.com ──────────────────────────────────────────────


def _learnbuffett_articles(conn: sqlite3.Connection) -> dict[str, dict]:
    """获取 learnbuffett 文章 ID 映射。"""
    rows = conn.execute(
        "SELECT id, url, title FROM articles WHERE source_type = 'learnbuffett'"
    ).fetchall()
    mapping = {}
    for row in rows:
        article_id = row[0]
        url = row[1] or ""
        title = row[2] or ""
        # url 格式: clone://learnbuffett/concepts/内在价值.html
        # 提取路径: concepts/内在价值.html
        parts = url.split("/")
        if len(parts) >= 3:
            # parts = ["clone:", "", "learnbuffett", "concepts", "内在价值.html"]
            file_path = "/".join(parts[3:])
            mapping[file_path] = {"id": article_id, "title": title}
    return mapping


def _extract_learnbuffett_wikilinks(html: str) -> list[dict]:
    """从 learnbuffett HTML 中提取 wikilink。"""
    links = []
    if not BeautifulSoup:
        return links
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", class_="wikilink"):
        href = a.get("href", "")
        text = a.get_text(strip=True)
        if not href or not text or len(text) > 60:
            continue
        # 确定类型
        link_type = "concept"
        if "/companies/" in href or "公司" in href:
            link_type = "company"
        elif "/people/" in href or "人物" in href:
            link_type = "person"
        elif (
            "/letters/" in href or "股东信" in href or "partnership" in href or "berkshire" in href
        ):
            link_type = "letter"
        links.append({"text": text, "href": href, "type": link_type})
    return links


# ── mungermodels.com ──────────────────────────────────────────────


def _extract_mungermodels_wikilinks(html: str) -> list[dict]:
    """从 mungermodels HTML 中提取 wikilink。"""
    links = []
    if not BeautifulSoup:
        return links
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", class_="wl"):
        href = a.get("href", "")
        text = a.get_text(strip=True)
        if not href or not text or len(text) > 60:
            continue
        link_type = "concept"
        if "/disciplines/" in href:
            link_type = "discipline"
        elif "/scenarios/" in href:
            link_type = "scenario"
        elif "/models/" in href:
            link_type = "model"
        links.append({"text": text, "href": href, "type": link_type})
    return links


# ── aichainmap.com ────────────────────────────────────────────────


def _aichainmap_articles(conn: sqlite3.Connection) -> dict[str, dict]:
    """获取 aichainmap 文章 ID 映射。"""
    rows = conn.execute(
        "SELECT id, url, title FROM articles WHERE source_type = 'aichainmap'"
    ).fetchall()
    mapping = {}
    for row in rows:
        article_id = row[0]
        url = row[1] or ""
        title = row[2] or ""
        # url 格式: clone://aichainmap/companies/openai
        slug = url.split("/")[-1] if "/" in url else ""
        mapping[slug] = {"id": article_id, "title": title, "url": url}
        # 也存原名
        mapping[url] = {"id": article_id, "title": title, "url": url}
    return mapping


def _extract_aichainmap_wikilinks(html: str) -> list[dict]:
    """从 aichainmap HTML 正文中提取 wikilink。"""
    links = []
    if not BeautifulSoup:
        return links
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", class_=lambda c: c and "wikilink" in c):
        href = a.get("href", "")
        text = a.get_text(strip=True)
        # 从 class 中提取类型
        classes = a.get("class", []) or []
        link_type = "concept"
        for cls_val in classes:
            if "companies" in cls_val:
                link_type = "company"
                break
            if "concepts" in cls_val:
                link_type = "concept"
                break
            if "people" in cls_val:
                link_type = "person"
                break
            if "events" in cls_val:
                link_type = "event"
                break
            if "layers" in cls_val:
                link_type = "layer"
                break
        if not href or not text or len(text) > 60:
            continue
        links.append({"text": text, "href": href, "type": link_type})
    return links


# ── 主构建逻辑 ────────────────────────────────────────────────────


def _build_learnbuffett(conn: sqlite3.Connection, args: argparse.Namespace) -> tuple[int, int, int]:
    """从 learnbuffett 克隆文件提取链接。"""
    sites_dir = (
        Path(__file__).resolve().parent.parent
        / "web"
        / "clone"
        / "sites"
        / "learnbuffett-com"
        / "learnbuffett.com"
    )
    file_articles = _learnbuffett_articles(conn)

    total_concepts = 0
    total_backlinks = 0
    total_files = 0
    content_dirs = ["concepts", "companies", "people", "partnership", "berkshire", "special"]

    for content_dir in content_dirs:
        target = sites_dir / content_dir
        if not target.is_dir():
            continue
        for html_file in sorted(target.rglob("*.html")):
            rel = str(html_file.relative_to(sites_dir))
            source_info = file_articles.get(rel)
            if not source_info:
                source_info = file_articles.get(rel.replace(".html", ""))
                if not source_info:
                    continue
            source_id = source_info["id"]
            source_title = source_info["title"]
            source_url = f"clone://learnbuffett/{rel}"

            html = html_file.read_text("utf-8", errors="replace")
            wikilinks = _extract_learnbuffett_wikilinks(html)
            total_files += 1

            for link in wikilinks:
                concept = link["text"]
                link_type = link["type"]
                link_href = link["href"].lstrip("./")

                # 提取 context snippet
                context = ""

                conn.execute(
                    "INSERT INTO knowledge_concepts (concept, concept_type, source_site, source_article_id, source_article_url, context_snippet) VALUES (?, ?, ?, ?, ?, ?)",
                    (concept, link_type, "learnbuffett", source_id, source_url, context),
                )
                total_concepts += 1

                conn.execute(
                    "INSERT INTO knowledge_backlinks (source_article_id, source_title, source_url, source_site, target_concept, target_type, target_url) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        source_id,
                        source_title,
                        source_url,
                        "learnbuffett",
                        concept,
                        link_type,
                        link_href,
                    ),
                )
                total_backlinks += 1

            if args.verbose and total_files % 50 == 0:
                logger.info("  learnbuffett: %d 文件, %d 链接", total_files, total_backlinks)

    conn.commit()
    logger.info(
        "learnbuffett: %d 文件, %d 概念, %d 反向链接", total_files, total_concepts, total_backlinks
    )
    return total_files, total_concepts, total_backlinks


def _build_mungermodels(conn: sqlite3.Connection, args: argparse.Namespace) -> tuple[int, int, int]:
    """从 mungermodels 克隆文件提取链接。"""
    sites_dir = (
        Path(__file__).resolve().parent.parent
        / "web"
        / "clone"
        / "sites"
        / "mungermodels-com"
        / "mungermodels.com"
    )
    rows = conn.execute(
        "SELECT id, url, title FROM articles WHERE source_type = 'mungermodels'"
    ).fetchall()
    file_articles = {}
    for row in rows:
        article_id = row[0]
        url = row[1] or ""
        title = row[2] or ""
        # url 格式: clone://mungermodels/models/XXX.html
        parts = url.split("/")
        if len(parts) >= 3:
            file_path = "/".join(parts[3:])
            file_articles[file_path] = {"id": article_id, "title": title}

    total_concepts = 0
    total_backlinks = 0
    total_files = 0
    content_dirs = ["models", "disciplines", "scenarios"]

    for content_dir in content_dirs:
        target = sites_dir / content_dir
        if not target.is_dir():
            continue
        for html_file in sorted(target.rglob("*.html")):
            rel = str(html_file.relative_to(sites_dir))
            source_info = file_articles.get(rel)
            if not source_info:
                continue
            source_id = source_info["id"]
            source_title = source_info["title"]
            source_url = f"clone://mungermodels/{rel}"

            html = html_file.read_text("utf-8", errors="replace")
            wikilinks = _extract_mungermodels_wikilinks(html)
            total_files += 1

            for link in wikilinks:
                concept = link["text"]
                link_type = link["type"]
                link_href = link["href"].lstrip("./")

                conn.execute(
                    "INSERT INTO knowledge_concepts (concept, concept_type, source_site, source_article_id, source_article_url) VALUES (?, ?, ?, ?, ?)",
                    (concept, link_type, "mungermodels", source_id, source_url),
                )
                total_concepts += 1

                conn.execute(
                    "INSERT INTO knowledge_backlinks (source_article_id, source_title, source_url, source_site, target_concept, target_type, target_url) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        source_id,
                        source_title,
                        source_url,
                        "mungermodels",
                        concept,
                        link_type,
                        link_href,
                    ),
                )
                total_backlinks += 1

            if args.verbose and total_files % 50 == 0:
                logger.info("  mungermodels: %d 文件, %d 链接", total_files, total_backlinks)

    conn.commit()
    logger.info(
        "mungermodels: %d 文件, %d 概念, %d 反向链接", total_files, total_concepts, total_backlinks
    )
    return total_files, total_concepts, total_backlinks


def _build_aichainmap(conn: sqlite3.Connection, args: argparse.Namespace) -> tuple[int, int, int]:
    """从 aichainmap 数据文件提取链接。"""
    sites_dir = (
        Path(__file__).resolve().parent.parent
        / "web"
        / "clone"
        / "sites"
        / "aichainmap-com"
        / "aichainmap.com"
    )
    articles = _aichainmap_articles(conn)

    bodies_file = sites_dir / "data" / "wiki-bodies.js?v=1"
    if not bodies_file.exists():
        logger.warning("aichainmap 数据文件不存在: %s", bodies_file)
        return 0, 0, 0

    text = bodies_file.read_text("utf-8")
    m = re.search(r"window\.WIKI_BODIES\s*=\s*(\{.+?\})\s*;?\s*$", text, re.DOTALL)
    if not m:
        logger.error("无法解析 WIKI_BODIES 数据")
        return 0, 0, 0
    bodies = json.loads(m.group(1))

    # 构建 slug → 文章 ID 映射（更宽松的匹配）
    slug_map = {}
    for slug, info in articles.items():
        name = slug.replace("-", " ").lower()
        slug_map[slug] = info
        slug_map[name] = info

    total_concepts = 0
    total_backlinks = 0

    for entity_key, entity_bodies in bodies.items():
        for entity_name, body_html in entity_bodies.items():
            if not body_html:
                continue

            # 找对应文章
            slug = entity_name.lower().replace(" ", "-").replace("'", "").replace("&", "and")
            article_info = articles.get(slug) or articles.get(entity_name)
            if not article_info:
                # 尝试模糊匹配
                for s, info in slug_map.items():
                    if entity_name.lower() == s.lower() or slug == s.lower():
                        article_info = info
                        break
            if not article_info:
                continue

            source_id = article_info["id"]
            source_title = article_info["title"]
            source_url = article_info.get("url", f"clone://aichainmap/{entity_key}/{slug}")

            wikilinks = _extract_aichainmap_wikilinks(body_html)

            for link in wikilinks:
                concept = link["text"]
                link_type = link["type"]
                link_href = link["href"].lstrip("./")

                conn.execute(
                    "INSERT INTO knowledge_concepts (concept, concept_type, source_site, source_article_id, source_article_url) VALUES (?, ?, ?, ?, ?)",
                    (concept, link_type, "aichainmap", source_id, source_url),
                )
                total_concepts += 1

                conn.execute(
                    "INSERT INTO knowledge_backlinks (source_article_id, source_title, source_url, source_site, target_concept, target_type, target_url) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        source_id,
                        source_title,
                        source_url,
                        "aichainmap",
                        concept,
                        link_type,
                        link_href,
                    ),
                )
                total_backlinks += 1

            if args.verbose and total_backlinks % 1000 == 0:
                logger.info(
                    "  aichainmap: %d 实体, %d 链接", total_backlinks // 10, total_backlinks
                )

    conn.commit()
    logger.info(
        "aichainmap: %d 实体, %d 概念, %d 反向链接",
        total_backlinks // 10,
        total_concepts,
        total_backlinks,
    )
    return 0, total_concepts, total_backlinks


# ── 入口 ──────────────────────────────────────────────────────────


def main() -> None:
    args = _parse_args()
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(message)s")

    if not BeautifulSoup:
        logger.error("需要安装 BeautifulSoup: pip install beautifulsoup4")
        return

    conn = _get_conn()
    if not conn:
        return

    if args.clear:
        _clear_tables(conn)

    total_c = 0
    total_b = 0

    if args.source in ("all", "learnbuffett"):
        _, c, b = _build_learnbuffett(conn, args)
        total_c += c
        total_b += b

    if args.source in ("all", "mungermodels"):
        _, c, b = _build_mungermodels(conn, args)
        total_c += c
        total_b += b

    if args.source in ("all", "aichainmap"):
        _, c, b = _build_aichainmap(conn, args)
        total_c += c
        total_b += b

    logger.info("=" * 50)
    logger.info("总计: %d 概念, %d 反向链接", total_c, total_b)

    # TOP 概念
    stat = conn.execute(
        "SELECT target_concept, COUNT(*) as cnt FROM knowledge_backlinks "
        "GROUP BY target_concept ORDER BY cnt DESC LIMIT 20"
    ).fetchall()
    logger.info("TOP 20 概念:")
    for row in stat:
        logger.info("  %-30s %3d 次提及", row[0], row[1])

    conn.close()


if __name__ == "__main__":
    main()
