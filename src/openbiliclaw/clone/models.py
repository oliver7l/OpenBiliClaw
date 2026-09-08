"""克隆系统数据模型。

定义克隆站点、标签、统计等核心数据结构。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class CloneStatus(StrEnum):
    """克隆站点状态。"""

    CLONED = "cloned"  # 已克隆完成
    CLONING = "cloning"  # 正在克隆中
    FAILED = "failed"  # 克隆失败
    MOVED = "moved"  # 源站已迁移
    ARCHIVED = "archived"  # 已归档


class CloneSite(BaseModel):
    """克隆站点记录。"""

    id: int = Field(description="站点唯一 ID")
    name: str = Field(description="站点名称")
    slug: str = Field(description="URL 标识，用于访问")
    source_url: str = Field(default="", description="原始来源 URL")
    local_path: str = Field(description="站点在本地的相对路径（相对于 clone/sites/）")
    description: str = Field(default="", description="站点描述")
    category: str = Field(
        default="other", description="分类：website/single-page/tool/game/art/other"
    )
    status: CloneStatus = Field(default=CloneStatus.CLONED, description="克隆状态")
    size_bytes: int = Field(default=0, description="站点文件总大小")
    file_count: int = Field(default=0, description="站点文件数")
    tags: list[str] = Field(default_factory=list, description="标签列表")
    created_at: datetime = Field(description="克隆时间")
    updated_at: datetime = Field(description="更新时间")


class CloneSiteCreate(BaseModel):
    """创建克隆站点请求。"""

    name: str = Field(min_length=1, max_length=200, description="站点名称")
    slug: str = Field(min_length=1, max_length=200, description="URL 标识")
    source_url: str = Field(default="", description="原始来源 URL")
    local_path: str = Field(default="", description="站点本地路径")
    description: str = Field(default="", description="站点描述")
    category: str = Field(default="other", description="分类")
    status: CloneStatus = Field(default=CloneStatus.CLONED, description="状态")
    tags: list[str] = Field(default_factory=list, description="标签列表")


class CloneSiteUpdate(BaseModel):
    """更新克隆站点请求。"""

    name: str | None = Field(default=None, description="站点名称")
    description: str | None = Field(default=None, description="站点描述")
    category: str | None = Field(default=None, description="分类")
    status: CloneStatus | None = Field(default=None, description="状态")
    tags: list[str] | None = Field(default=None, description="标签列表")


class CloneStats(BaseModel):
    """克隆系统统计信息。"""

    total_sites: int = Field(description="站点总数")
    total_categories: int = Field(description="分类数")
    total_tags: int = Field(description="标签数")
    total_size_bytes: int = Field(description="总大小")
    total_files: int = Field(description="总文件数")
    category_distribution: dict[str, int] = Field(default_factory=dict, description="分类分布")
    status_distribution: dict[str, int] = Field(default_factory=dict, description="状态分布")


class CloneRequest(BaseModel):
    """克隆新站点请求。"""

    url: str = Field(min_length=1, description="要克隆的网站 URL")
    name: str = Field(min_length=1, max_length=200, description="站点名称")
    category: str = Field(default="website", description="分类")
    tags: list[str] = Field(default_factory=list, description="标签")
    depth: int = Field(default=1, ge=1, le=3, description="克隆深度")
