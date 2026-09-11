"""克隆系统业务逻辑层。

提供克隆站点的增删改查、导入已有站点、克隆新站点等高级功能。
"""

from __future__ import annotations

import logging
import re
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from .cloner import clone_website
from .models import (
    CloneRequest,
    CloneSite,
    CloneSiteCreate,
    CloneSiteUpdate,
    CloneStats,
    CloneStatus,
)

if TYPE_CHECKING:
    from .store import CloneStore

logger = logging.getLogger(__name__)

_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*[a-z0-9]$")


def _slugify(name: str) -> str:
    """将名称转为 URL 友好的 slug。"""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9\u4e00-\u9fff_-]", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug


class CloneService:
    """克隆系统服务。"""

    def __init__(self, store: CloneStore, sites_dir: str | Path | None = None) -> None:
        self._store = store
        self._sites_dir = Path(sites_dir) if sites_dir else None

    @property
    def store(self) -> CloneStore:
        return self._store

    # ── 站点管理 ────────────────────────────────────────────────

    def create_site(self, data: CloneSiteCreate) -> CloneSite:
        """创建新站点记录。"""
        return self._store.create_site(data)

    def get_site(self, site_id: int) -> CloneSite | None:
        """获取站点详情。"""
        return self._store.get_site(site_id)

    def get_site_by_slug(self, slug: str) -> CloneSite | None:
        """根据 slug 获取站点。"""
        return self._store.get_site_by_slug(slug)

    def list_sites(
        self,
        limit: int = 50,
        offset: int = 0,
        category: str | None = None,
        status: str | None = None,
        tag: str | None = None,
        search: str | None = None,
    ) -> tuple[list[CloneSite], int]:
        """列出站点。"""
        return self._store.list_sites(
            limit=limit,
            offset=offset,
            category=category,
            status=status,
            tag=tag,
            search=search,
        )

    def update_site(self, site_id: int, data: CloneSiteUpdate) -> CloneSite | None:
        """更新站点。"""
        return self._store.update_site(site_id, data)

    def delete_site(self, site_id: int) -> bool:
        """删除站点。"""
        return self._store.delete_site(site_id)

    def get_stats(self) -> CloneStats:
        """获取统计信息。"""
        return self._store.get_stats()

    def list_tags(self) -> list[dict]:
        """列出所有标签。"""
        return self._store.list_tags()

    # ── 导入已有站点 ────────────────────────────────────────────

    def import_existing_sites(self, sites_dir: str | Path) -> list[CloneSite]:
        """扫描目录下的已有站点文件夹，批量导入到数据库。"""
        sites_path = Path(sites_dir)
        if not sites_path.is_dir():
            logger.warning("sites_dir 不存在: %s", sites_dir)
            return []

        imported = []
        for item in sorted(sites_path.iterdir()):
            if not item.is_dir():
                continue
            slug = item.name
            # 如果是隐藏目录则跳过
            if slug.startswith("."):
                continue

            # 检查是否已导入
            existing = self._store.get_site_by_slug(slug)
            if existing is not None:
                continue

            # 统计文件大小和数量
            size_bytes = 0
            file_count = 0
            for f in item.rglob("*"):
                if f.is_file():
                    size_bytes += f.stat().st_size
                    file_count += 1

            # 尝试读取 SOURCE.txt 获取来源 URL（提取首个干净的 http(s) URL，
            # 而不是把整个文件内容塞进 source_url 字段）
            source_url = ""
            source_text = ""
            source_file = item / "SOURCE.txt"
            if source_file.is_file():
                source_text = source_file.read_text(encoding="utf-8", errors="ignore").strip()
                source_url = _extract_source_url(source_text)

            # 用更友好的方式推断名称
            name = _friendly_name(slug)

            # 从 index.html 提取 <title> 作为描述来源
            html_title = _extract_html_title(item)

            site = self._store.create_site(
                CloneSiteCreate(
                    name=name,
                    slug=slug,
                    source_url=source_url,
                    local_path=slug,
                    description=html_title,
                    category=_infer_category(slug, source_url, html_title),
                    status=CloneStatus.CLONED,
                    tags=[],
                )
            )
            self._store.update_site_size(site.id, size_bytes, file_count)
            site.size_bytes = size_bytes
            site.file_count = file_count
            imported.append(site)

        logger.info("批量导入 %d 个克隆站点", len(imported))
        return imported

    # ── 克隆新站点 ──────────────────────────────────────────────

    def clone_new_site(self, request: CloneRequest) -> CloneSite:
        """克隆一个新网站。

        1. 创建站点记录
        2. 调用克隆引擎
        3. 更新站点状态和大小信息
        """
        if self._sites_dir is None:
            raise RuntimeError("sites_dir 未设置，无法克隆")

        slug = _slugify(request.name)
        site_dir = self._sites_dir / slug

        site = self._store.create_site(
            CloneSiteCreate(
                name=request.name,
                slug=slug,
                source_url=request.url,
                local_path=slug,
                description="",
                category=request.category,
                status=CloneStatus.CLONING,
                tags=request.tags,
            )
        )

        try:
            result = clone_website(
                url=request.url,
                output_dir=site_dir,
                depth=request.depth,
            )
            if result["success"]:
                self._store.update_site(
                    site.id,
                    CloneSiteUpdate(
                        status=CloneStatus.CLONED,
                    ),
                )
                self._store.update_site_size(site.id, result["size_bytes"], result["file_count"])
            else:
                self._store.update_site(
                    site.id,
                    CloneSiteUpdate(
                        status=CloneStatus.FAILED,
                    ),
                )
        except Exception:
            logger.exception("克隆站点失败: %s", request.url)
            self._store.update_site(
                site.id,
                CloneSiteUpdate(
                    status=CloneStatus.FAILED,
                ),
            )

        return self._store.get_site(site.id) or site


def _friendly_name(slug: str) -> str:
    """将 slug 转为可读的名称。"""
    # 处理常见的命名模式
    name = slug.replace("-", " ").replace("_", " ")
    # 首字母大写
    return " ".join(w.capitalize() for w in name.split())


def _infer_category(slug: str, source_url: str = "", html_title: str = "") -> str:
    """根据 slug、来源 URL 和页面标题推断站点分类。

    三级信号：来源 URL 域名/路径 > 页面标题 > slug 关键词。
    比只看 slug 猜测可靠得多（旧逻辑会把 aichainmap 猜成 travel）。
    """
    # 组合所有可用文本，按可靠性排序
    signals = [
        source_url.lower(),
        html_title.lower(),
        slug.lower(),
    ]
    for text in signals:
        if any(w in text for w in ("test", "quiz", "personality", "mbti", "disc", "心理", "测评")):
            return "tool"
        if any(w in text for w in ("game", "游戏")):
            return "game"
        if any(w in text for w in ("art", "gallery", "design", "photo", "illust", "插画", "画廊")):
            return "art"
        if any(w in text for w in ("travel", "journey", "旅行", "旅游")):
            return "travel"
    # "map" 只对 URL/标题信号生效，不再匹配 slug（aichainmap 之类的误伤源）
    if any(w in source_url.lower() + html_title.lower() for w in ("map", "地图")):
        return "travel"
    return "website"


def _extract_source_url(text: str) -> str:
    """从 SOURCE.txt 文本中提取首个干净的 http(s) URL。"""
    match = re.search(r"https?://[^\s\"'<>\)\]]+", text)
    if not match:
        return ""
    url = match.group(0).rstrip(".,;，。")
    return url


def _extract_html_title(site_dir: Path) -> str:
    """从站点 index.html 提取 <title>，作为 description。"""
    for name in ("index.html", "index.htm"):
        html_file = site_dir / name
        if html_file.is_file():
            with suppress(OSError, UnicodeDecodeError):
                head = html_file.read_text(encoding="utf-8", errors="ignore")[:20000]
                match = re.search(r"<title[^>]*>(.*?)</title>", head, re.IGNORECASE | re.DOTALL)
                if match:
                    title = re.sub(r"\s+", " ", match.group(1)).strip()
                    return title[:200]
    return ""
