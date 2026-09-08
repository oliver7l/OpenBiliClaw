"""克隆系统业务逻辑层。

提供克隆站点的增删改查、导入已有站点、克隆新站点等高级功能。
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from .cloner import clone_website
from .models import (
    CloneSite,
    CloneSiteCreate,
    CloneSiteUpdate,
    CloneStats,
    CloneRequest,
    CloneStatus,
)
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

            # 尝试读取 SOURCE.txt 获取来源 URL
            source_url = ""
            source_file = item / "SOURCE.txt"
            if source_file.is_file():
                source_url = source_file.read_text(encoding="utf-8").strip()

            # 用更友好的方式推断名称
            name = _friendly_name(slug)

            site = self._store.create_site(CloneSiteCreate(
                name=name,
                slug=slug,
                source_url=source_url,
                local_path=slug,
                description="",
                category=_infer_category(slug),
                status=CloneStatus.CLONED,
                tags=[],
            ))
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

        site = self._store.create_site(CloneSiteCreate(
            name=request.name,
            slug=slug,
            source_url=request.url,
            local_path=slug,
            description="",
            category=request.category,
            status=CloneStatus.CLONING,
            tags=request.tags,
        ))

        try:
            result = clone_website(
                url=request.url,
                output_dir=site_dir,
                depth=request.depth,
            )
            if result["success"]:
                self._store.update_site(site.id, CloneSiteUpdate(
                    status=CloneStatus.CLONED,
                ))
                self._store.update_site_size(site.id, result["size_bytes"], result["file_count"])
            else:
                self._store.update_site(site.id, CloneSiteUpdate(
                    status=CloneStatus.FAILED,
                ))
        except Exception as exc:
            logger.exception("克隆站点失败: %s", request.url)
            self._store.update_site(site.id, CloneSiteUpdate(
                status=CloneStatus.FAILED,
            ))

        return self._store.get_site(site.id) or site


def _friendly_name(slug: str) -> str:
    """将 slug 转为可读的名称。"""
    # 处理常见的命名模式
    name = slug.replace("-", " ").replace("_", " ")
    # 首字母大写
    return " ".join(w.capitalize() for w in name.split())


def _infer_category(slug: str) -> str:
    """根据 slug 推断站点分类。"""
    slug_lower = slug.lower()
    if any(w in slug_lower for w in ("test", "quiz", "personality", "mbti", "disc", "心理")):
        return "tool"
    if any(w in slug_lower for w in ("game", "play", "card")):
        return "game"
    if any(w in slug_lower for w in ("art", "gallery", "design", "photo", "illust")):
        return "art"
    if any(w in slug_lower for w in ("travel", "map", "journey")):
        return "travel"
    return "website"