"""RSSHub 本地自部署服务管理。

RSSHub（https://github.com/DIYgod/RSSHub）为很多中文站（含豆瓣）生成聚合 RSS。
官方公共实例已限制访问，故在本地用 Docker 跑一个 RSSHub 容器，项目启动时可自动
拉起（仿 Ollama preflight）。

- 检测 ``127.0.0.1:1200`` 存活则跳过。
- 未存活则 ``docker run`` 拉起（``--restart unless-stopped``）。
- 若本机有 Clash/代理（默认探测 127.0.0.1:7890）可达，注入 ``HTTP(S)_PROXY``，
  让 RSSHub 拉取受限/墙外内容（用户有 7890 代理）。
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

from openbiliclaw.docker_runtime import can_connect, resolve_optional_proxy_env

logger = logging.getLogger(__name__)

CONTAINER_NAME = "openbiliclaw-rsshub"
IMAGE = "diygod/rsshub"
PORT = 1200
# 用户 Clash 代理默认端口；可被 resolve_optional_proxy_env 探测。
PROXY_HOST = "host.docker.internal"
PROXY_PORT = 7890


def rsshub_is_up(host: str = "127.0.0.1", port: int = PORT) -> bool:
    """检测本地 RSSHub 是否已监听。"""
    return can_connect(host, port, timeout=1.0)


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _inject_proxy_env() -> dict[str, str]:
    """探测 host.docker.internal:7890 代理；可达则返回注入容器代理 env。"""
    return resolve_optional_proxy_env(
        {},
        can_connect=can_connect,
        proxy_host=PROXY_HOST,
        proxy_port=PROXY_PORT,
        timeout=1.0,
    )


def _start_container(port: int = PORT, image: str = IMAGE) -> bool:
    """docker run RSSHub 容器（不存在才建）。启动失败不抛异常。"""
    try:
        subprocess.run(
            ["docker", "inspect", CONTAINER_NAME],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:  # noqa: BLE001
        pass

    # 容器存在于 docker ps -a 则直接 start；否则 run
    try:
        ps = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"name={CONTAINER_NAME}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        exists = (ps.stdout or "").strip()
        if exists:
            subprocess.run(["docker", "start", CONTAINER_NAME], capture_output=True, text=True, timeout=30)
        else:
            proxy_env = _inject_proxy_env()
            cmd = [
                "docker",
                "run",
                "-d",
                "--name",
                CONTAINER_NAME,
                "--restart",
                "unless-stopped",
                "-p",
                f"127.0.0.1:{port}:1200",
            ]
            for key, val in proxy_env.items():
                cmd += ["-e", f"{key}={val}"]
            cmd.append(image)
            subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except Exception:  # noqa: BLE001
        logger.exception("RSSHub 容器拉起失败")
        return False
    return True


def ensure_rsshub(
    *,
    wait_seconds: int = 30,
    can_connect_fn: Callable[[str, int, float], bool] = can_connect,
    logger_fn: Callable[[str], None] | None = None,
) -> bool:
    """确保本地 RSSHub 可用：存活则跳过；否则 docker run 拉起并等待就绪。

    返回是否最终可用（端口可连）。
    """
    log = logger_fn or logger.info

    if rsshub_is_up():
        return True
    if not _docker_available():
        log("RSSHub 未运行且本机无 docker，请手动部署 RSSHub 或安装 Docker")
        return False

    _start_container(port=PORT)
    for _ in range(wait_seconds):
        if can_connect_fn("127.0.0.1", PORT, 1.0):
            return True
        time.sleep(1)
    return rsshub_is_up()


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    ok = ensure_rsshub()
    print("RSSHub ready" if ok else "RSSHub not ready")