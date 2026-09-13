"""LLM provider 段的单一数据源与持久化回归（2026-09-13）。

``LLMConfig`` 声明了 10 个 provider 段，但「谁遍历 provider」这件事曾在多处
各写一遍，其中 3 处只列了 7 个，漏掉 ``zhipu`` / ``modelscope`` /
``siliconflow``：

- ``_apply_llm_update``（``PUT /api/config`` 的字段应用）→ 提交被静默忽略
- ``_render_config_toml``（写盘）→ **任何一次保存都会删掉手工配置的段**，
  因为那些段从未被渲染出来（数据丢失，非仅"不生效"）
- ``LLMConfigOut`` / ``_config_to_response``（``GET /api/config``）→ 读不回来
- ``_collect_config_issues``（校验）→ 把这三家判为「不支持的默认 provider」

修复方式是把 provider 集合收敛为 ``LLM_PROVIDER_NAMES``（从 ``LLMConfig``
自身派生），各环节改为引用它。这些测试锁定该收敛，并保证以后新增一个
provider 字段时，应用 / 写盘 / 回传 / 校验四处自动跟随、不会再漏。
"""

from __future__ import annotations

import re
from dataclasses import fields
from pathlib import Path

from openbiliclaw.api.models import LLMConfigOut, LLMProviderConfigOut
from openbiliclaw.config import (
    LLM_PROVIDER_NAMES,
    Config,
    LLMConfig,
    LLMProviderConfig,
    _collect_config_issues,
    _render_config_toml,
    load_config,
    save_config,
)

_DOMESTIC = ("zhipu", "modelscope", "siliconflow")
_SECTION_RE = re.compile(r"^\[llm\.([a-z_]+)\]$", re.M)


def _derived_provider_names() -> tuple[str, ...]:
    """独立地重新派生一次，作为对 ``LLM_PROVIDER_NAMES`` 的交叉校验。"""
    defaults = LLMConfig()
    return tuple(spec.name for spec in fields(defaults) if isinstance(getattr(defaults, spec.name), LLMProviderConfig))


def test_provider_names_have_a_single_source() -> None:
    """常量必须等于从数据类派生的结果，且与 llm/_compat 的映射一致。"""
    from openbiliclaw.llm import _compat

    assert _derived_provider_names() == LLM_PROVIDER_NAMES
    assert set(_compat._PROVIDER_NAMES) == set(LLM_PROVIDER_NAMES), (
        "llm/_compat._PROVIDER_NAMES 与 LLMConfig 的 provider 段漂移了；obc_llm 适配层会漏拷字段"
    )
    # 这三家曾经在 3 处被漏掉，钉住它们的存在
    assert set(_DOMESTIC) <= set(LLM_PROVIDER_NAMES)


def test_api_response_model_declares_every_provider() -> None:
    """``GET /api/config`` 的响应模型必须能表达每一个 provider 段。"""
    for name in LLM_PROVIDER_NAMES:
        assert name in LLMConfigOut.model_fields, f"LLMConfigOut 缺少 {name}"
        assert LLMConfigOut.model_fields[name].annotation is LLMProviderConfigOut


def test_render_writes_every_provider_section() -> None:
    """写盘必须覆盖全部 provider 段 —— 漏一段就等于删掉用户的手工配置。"""
    toml = _render_config_toml(Config(data_dir="/tmp/obc-test"))
    sections = set(_SECTION_RE.findall(toml))
    missing = sorted(set(LLM_PROVIDER_NAMES) - sections)
    assert not missing, f"_render_config_toml 未写出这些段: {missing}"


def test_resave_preserves_handwritten_provider_sections(tmp_path: Path) -> None:
    """回归：手工配置的 ``[llm.zhipu]`` 段不得被下一次保存静默删除。

    这是修复前真实会发生的数据丢失 —— 用户在 config.toml 里配好免费的
    智谱 GLM，然后在设置页改了个语言，整段连同 api_key 一起消失。
    """
    config_path = tmp_path / "config.toml"
    body = "\n".join(
        [
            "[general]",
            'language = "zh"',
            f'data_dir = "{tmp_path / "data"}"',
            "",
            "[llm]",
            'default_provider = "zhipu"',
            "",
        ]
        + [
            line
            for name in _DOMESTIC
            for line in (
                f"[llm.{name}]",
                f'api_key = "{name}-MANUAL-KEY"',
                'model = "some-model"',
                f'base_url = "https://{name}.example.com/v1"',
                "",
            )
        ]
    )
    config_path.write_text(body, encoding="utf-8")

    cfg = load_config(config_path)
    assert cfg.llm.zhipu.api_key == "zhipu-MANUAL-KEY", "加载路径应当读取该段"

    save_config(cfg, config_path)
    after = config_path.read_text(encoding="utf-8")
    for name in _DOMESTIC:
        assert f"[llm.{name}]" in after, f"保存后 [llm.{name}] 段被删除了"
        assert f"{name}-MANUAL-KEY" in after, f"保存后 {name} 的 api_key 丢失"
        # 这三家的 provider 工厂要求 base_url 非空，丢了就建不起来
        assert f"https://{name}.example.com/v1" in after, f"{name} 的 base_url 丢失"


def test_validator_accepts_every_declared_default_provider() -> None:
    """每个已声明的 provider 都应能作为 ``default_provider``，不被误判为无效。"""
    for name in LLM_PROVIDER_NAMES:
        cfg = Config(data_dir="/tmp/obc-test")
        cfg.llm.default_provider = name
        getattr(cfg.llm, name).api_key = "k"
        getattr(cfg.llm, name).base_url = "https://example.invalid/v1"
        messages = [issue.message for issue in _collect_config_issues(cfg)]
        assert not any("不支持的默认 provider" in msg for msg in messages), (
            f"{name} 被判为不支持的默认 provider: {messages}"
        )
