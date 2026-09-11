"""ed2k / Kad 下载管理服务。

经本机安装的 ``mule`` CLI 调用 MLDonkey（Colima + Docker 容器），封装
搜索、下载、进度、取消、commit 等操作。每个命令返回 ``(ok, text)``，
text 是 ``mule --json`` 的原始输出或错误消息。
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

# MLDonkey 容器名（与 mule-cli 约定一致）
MLDONKEY_CONTAINER = "mule-mldonkey"

# 默认落地目录的相对后缀（容器 incoming/files 映射到宿主目录）
INCOMING_FILES_SUFFIX = "/incoming/files/"


class MuleService:
    """薄封装 mule CLI 的操作集合。"""

    def __init__(self, mule_path: str = "", download_dir: str = "") -> None:
        self._mule_path = mule_path.strip()
        self._download_dir = download_dir.strip()

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _resolve_mule(self) -> str:
        """解析 mule 可执行文件路径。优先配置项，其次 PATH。"""
        if self._mule_path:
            return self._mule_path
        found = shutil.which("mule")
        if found:
            return found
        raise FileNotFoundError(
            "mule CLI 未在 PATH 中找到（参考：安装 mule-cli 并 symlink 到 ~/bin/mule）"
        )

    def run(self, args: list[str], timeout: int = 60) -> tuple[bool, str]:
        """执行 mule 命令，返回 (ok, text)。"""
        try:
            exe = self._resolve_mule()
            p = subprocess.run(
                [exe, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            out = (p.stdout or "") + (p.stderr or "")
            return p.returncode == 0, out.strip()
        except subprocess.TimeoutExpired:
            return False, "操作超时，MLDonkey 未在时限内响应，请稍后重试"
        except FileNotFoundError:
            return False, "mule CLI 未找到，请确认已安装并在 PATH 中"

    @staticmethod
    def _as_json(text: str) -> dict[str, Any]:
        """尽力把输出解析为 dict；失败时保留 raw。"""
        try:
            return json.loads(text) if text else {}
        except json.JSONDecodeError:
            return {"raw": text, "error": "mule 返回了无法解析的输出"}

    # ------------------------------------------------------------------
    # 命令操作
    # ------------------------------------------------------------------
    def ensure_engine(self) -> dict[str, Any]:
        """启动 Colima + 容器并等待核心就绪，返回 net 快照。"""
        script = (
            "colima status >/dev/null 2>&1 || colima start; "
            "mule daemon up >/dev/null 2>&1; "
            "mule net --json"
        )
        try:
            p = subprocess.run(
                ["bash", "-lc", script],
                capture_output=True,
                text=True,
                timeout=180,
            )
            out = (p.stdout or "") + (p.stderr or "")
            return self._as_json(out.strip() or '{"error": "engine not ready"}')
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return {"error": "无法启动 MLDonkey 引擎（colima/docker/mule 未就绪）"}

    def net(self) -> dict[str, Any]:
        ok, text = self.run(["net", "--json"])
        return self._as_json(text)

    def search(self, query: str, wait: int = 35, limit: int = 25) -> dict[str, Any]:
        ok, text = self.run(
            ["search", query, "--wait", str(wait), "--limit", str(limit), "--json"],
            timeout=wait + 60,
        )
        data = self._as_json(text)
        if not ok:
            data.setdefault("error", "搜索失败")
        return data

    def download(self, ids: list[int]) -> dict[str, Any]:
        ok, text = self.run(["download", *[str(i) for i in ids], "--json"])
        return self._as_json(text)

    def download_link(self, link: str) -> dict[str, Any]:
        link = (link or "").strip()
        if not link:
            return {"ok": False, "error": "链接为空"}
        ok, text = self.run(["console", "dllink " + link], timeout=60)
        return {"ok": ok, "output": text}

    def downloads(self) -> dict[str, Any]:
        ok, text = self.run(["downloads", "--json"])
        return self._as_json(text)

    def cancel(self, ids: list[int]) -> dict[str, Any]:
        ok, text = self.run(["cancel", *[str(i) for i in ids]])
        return {"ok": ok, "output": text}

    def commit(self) -> dict[str, Any]:
        ok, text = self.run(["commit"])
        return {"ok": ok, "output": text}

    def path(self) -> str:
        """返回完成文件落地目录（宿主路径）。"""
        if self._download_dir:
            return self._download_dir
        try:
            # 精确取 destination 指向 incoming/files 的挂载源（容器可有多个挂载）
            p = subprocess.run(
                [
                    "docker",
                    "inspect",
                    MLDONKEY_CONTAINER,
                    "--format",
                    # shellcheck disable=SC2016
                    '{{range $i, $m := .Mounts}}'
                    '{{if eq $m.Destination "/var/lib/mldonkey/incoming/files"}}{{println $m.Source}}{{end}}'
                    '{{end}}',
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            src = (p.stdout or "").strip()
            if src:
                return src + "/"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return "（无法解析容器挂载，本机可能未运行 MLDonkey）"
