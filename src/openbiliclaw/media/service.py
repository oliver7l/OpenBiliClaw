"""媒体浏览核心逻辑。

非递归扫描配置根目录，安全解析文件路径（防目录穿越），按媒体类型
/关键词/分页返回条目。纯 Python + stdlib，便于单元测试。

设计决策：
- 顶层 ``os.scandir`` 非递归扫描：媒体根目录可能极大（GB 级视频），
  递归遍历会阻塞首屏；子目录由用户逐层进入。
- 文件访问必须落回已配置根目录内，杜绝任意文件读取。
"""

from __future__ import annotations

import os
from pathlib import Path

VIDEO_EXTS: frozenset[str] = frozenset(
    {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".flv", ".wmv", ".ts"}
)
IMAGE_EXTS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".heic", ".avif"}
)

class MediaNotFoundError(ValueError):
    """请求的根目录或文件不存在 / 超出配置范围。"""


class MediaService:
    """面向一组配置根目录的媒体浏览服务。"""

    def __init__(self, roots: list[str]) -> None:
        # 规范化根目录（绝对路径 + 去尾斜杠），并保持去重后的顺序
        self._roots: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            path = Path(root)
            if not path.is_absolute():
                path = (Path.cwd() / path).resolve()
            else:
                path = path.resolve()
            key = os.path.normcase(str(path))
            if key not in seen:
                seen.add(key)
                self._roots.append(path)

    @property
    def roots(self) -> list[str]:
        return [str(path) for path in self._roots]

    def roots_meta(self) -> list[dict[str, object]]:
        """每个配置根目录的元信息（存在性 + 顶层媒体数量）。"""
        meta: list[dict[str, object]] = []
        for path in self._roots:
            video_count = 0
            image_count = 0
            if path.is_dir():
                try:
                    with os.scandir(path) as entries:
                        for entry in entries:
                            if entry.is_file(follow_symlinks=True):
                                ext = Path(entry.name).suffix.lower()
                                if ext in VIDEO_EXTS:
                                    video_count += 1
                                elif ext in IMAGE_EXTS:
                                    image_count += 1
                except OSError:
                    pass
            meta.append(
                {
                    "path": str(path),
                    "name": path.name or str(path),
                    "exists": path.exists(),
                    "is_dir": path.is_dir(),
                    "video_count": video_count,
                    "image_count": image_count,
                }
            )
        return meta

    def resolve_root(self, root: str) -> Path:
        """解析并校验根目录，返回规范化后的 Path。"""
        wanted = Path(root)
        wanted = wanted.resolve() if not wanted.is_absolute() else wanted.resolve()
        wanted_key = os.path.normcase(str(wanted))
        for path in self._roots:
            if os.path.normcase(str(path)) == wanted_key:
                return path
        raise MediaNotFoundError(f"root 不在已配置目录内: {root}")

    def _resolve_subdir(self, root: Path, sub: str) -> tuple[Path, str]:
        """把 sub 子路径解析到 root 下的绝对目录路径（含安全校验）。"""
        sub = (sub or "").strip().strip("/\\")
        if not sub:
            return root, ""
        resolved = (root / sub).resolve()
        if not self._is_within(resolved, root):
            raise MediaNotFoundError("子目录超出根目录范围")
        if not resolved.is_dir():
            raise MediaNotFoundError(f"子目录不存在: {sub}")
        return resolved, os.path.normpath(sub)

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        common = os.path.commonpath(
            [os.path.normcase(str(root)), os.path.normcase(str(path))]
        )
        return common == os.path.normcase(str(root))

    def list_items(
        self,
        root: str,
        *,
        kind: str = "all",
        q: str = "",
        sub: str = "",
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, object]:
        """分页列出 root（或 root/sub）下的媒体条目。

        kind ∈ {"all", "video", "image"}；q 为文件名大小写不敏感包含匹配。
        返回 ``{"items": [...], "has_more": bool, "offset": int}``。
        """
        root_path = self.resolve_root(root)
        dir_path, rel_prefix = self._resolve_subdir(root_path, sub)
        query = (q or "").strip().lower()
        items: list[dict[str, object]] = []

        try:
            entries = list(os.scandir(dir_path))
        except OSError as exc:
            raise MediaNotFoundError(f"无法读取目录 {dir_path}: {exc}") from exc

        for entry in entries:
            name = entry.name
            rel = os.path.normpath(os.path.join(rel_prefix, name)) if rel_prefix else name
            if entry.is_dir(follow_symlinks=True):
                # 目录只在 all / dir 分类下列出；切到 video/image 时不混入目录
                if kind in ("all", "dir") and (not query or query in name.lower()):
                    items.append(
                        {
                            "name": name,
                            "is_dir": True,
                            "kind": "dir",
                            "size": 0,
                            "mtime": 0,
                            "rel": rel,
                        }
                    )
                continue
            if not entry.is_file(follow_symlinks=True):
                continue
            ext = Path(name).suffix.lower()
            is_video = ext in VIDEO_EXTS
            is_image = ext in IMAGE_EXTS
            if kind == "video" and not is_video:
                continue
            if kind == "image" and not is_image:
                continue
            if kind == "all" and not is_video and not is_image:
                continue
            if query and query not in name.lower():
                continue
            stat = entry.stat(follow_symlinks=True)
            items.append(
                {
                    "name": name,
                    "is_dir": False,
                    "kind": "video" if is_video else "image",
                    "size": int(stat.st_size),
                    "mtime": int(stat.st_mtime),
                    "rel": rel,
                }
            )

        # 子目录在前，文件按名称忽略大小写排序
        items.sort(
            key=lambda item: (
                not bool(item["is_dir"]),
                str(item["name"]).lower(),
            )
        )
        has_more = offset + limit < len(items)
        page = items[offset : offset + limit]
        return {"items": page, "has_more": has_more, "offset": offset}

    def resolve_file(self, root: str, rel: str) -> Path:
        """安全解析 root 下的文件路径，返回存在的文件 Path。

        路径经过 ``resolve()`` 与根目录共同前缀校验，杜绝 ``../`` 穿越。
        """
        if not rel:
            raise MediaNotFoundError("缺少文件路径")
        root_path = self.resolve_root(root)
        rel = rel.strip().strip("/\\")
        resolved = (root_path / rel).resolve()
        if self._is_within(resolved, root_path) and str(resolved) != str(root_path):
            if resolved.is_file():
                return resolved
        raise MediaNotFoundError(f"文件不存在或超出范围: {rel}")