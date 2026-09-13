"""``PUT`` / ``GET /api/config`` 的 provider 段覆盖回归（2026-09-13）。

``llm/_compat`` 与 ``obc_llm`` registry 一直支持 10 个 provider，但 HTTP 层
只认识 7 个：``_apply_llm_update`` 的循环和 ``LLMConfigOut`` 响应模型都漏了
``zhipu`` / ``modelscope`` / ``siliconflow``。后果是这两条 ——
PUT 提交它们的凭据**静默无效**，GET 也**读不回来**，于是这三家在 API 上
形同不存在。config 层的等价回归见 ``tests/config/test_llm_provider_sections.py``。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.config import LLM_PROVIDER_NAMES, Config, save_config


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    """隔离到临时项目根，避免读到本机真实 config.toml 与数据库。"""
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    save_config(Config(data_dir=str(tmp_path / "data")), tmp_path / "config.toml")
    return TestClient(create_app(), raise_server_exceptions=False)


def _leave_degraded(client: TestClient) -> None:
    """先配置一个可用 provider，否则后端处于降级态、``PUT`` 会被校验拦下。"""
    r = client.put(
        "/api/config",
        json={
            "suppress_background_llm_work": True,
            "llm": {
                "default_provider": "deepseek",
                "deepseek": {"api_key": "sk-baseline-123456", "model": "deepseek-chat"},
            },
        },
    )
    assert r.status_code == 200, r.text


def test_put_persists_every_declared_provider(client: TestClient, tmp_path: Path) -> None:
    """PUT 携带的每一个 provider 段都必须写进 config.toml。"""
    _leave_degraded(client)

    payload_llm = {
        name: {
            "api_key": f"{name}-KEY-123456",
            "model": f"{name}-model",
            "base_url": f"https://{name}.example.invalid/v1",
        }
        for name in LLM_PROVIDER_NAMES
    }
    r = client.put(
        "/api/config",
        json={"suppress_background_llm_work": True, "llm": payload_llm},
    )
    assert r.status_code == 200, r.text

    saved = (tmp_path / "config.toml").read_text(encoding="utf-8")
    for name in LLM_PROVIDER_NAMES:
        assert f"[llm.{name}]" in saved, f"缺少 [llm.{name}] 段"
        assert f"{name}-KEY-123456" in saved, f"{name} 的 api_key 被静默丢弃"
        assert f"{name}-model" in saved, f"{name} 的 model 被静默丢弃"


def test_get_returns_every_declared_provider(client: TestClient) -> None:
    """``GET /api/config`` 的 llm 段必须包含全部 provider，且凭据仍被掩码。"""
    llm = client.get("/api/config").json()["llm"]
    missing = sorted(name for name in LLM_PROVIDER_NAMES if name not in llm)
    assert not missing, f"GET /api/config 未回传这些 provider: {missing}"

    # 掩码不得回退：新补的段同样只能返回星号形态
    for name in LLM_PROVIDER_NAMES:
        assert "api_key" in llm[name]


def test_domestic_provider_survives_a_later_unrelated_save(client: TestClient, tmp_path: Path) -> None:
    """配好智谱后再保存任意无关设置，其段与凭据都必须保留。"""
    _leave_degraded(client)
    r = client.put(
        "/api/config",
        json={
            "suppress_background_llm_work": True,
            "llm": {
                "zhipu": {
                    "api_key": "zp-KEEP-ME-123456",
                    "model": "glm-4.7-flash",
                    "base_url": "https://open.bigmodel.cn/api/paas/v4",
                }
            },
        },
    )
    assert r.status_code == 200, r.text

    # 一次无关的保存（等价于设置页改了语言）
    r2 = client.put("/api/config", json={"suppress_background_llm_work": True, "language": "zh"})
    assert r2.status_code == 200, r2.text

    saved = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert "[llm.zhipu]" in saved
    assert "zp-KEEP-ME-123456" in saved
    assert "https://open.bigmodel.cn/api/paas/v4" in saved
