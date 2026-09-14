"""Tests for discovery cover-image preparation."""

from __future__ import annotations

import base64
from io import BytesIO
from types import SimpleNamespace

import pytest
from obc_discovery.multimodal import prepare_cover_image_input
from PIL import Image


@pytest.mark.asyncio
async def test_prepare_cover_image_input_resizes_and_encodes_jpeg(monkeypatch) -> None:
    source = BytesIO()
    Image.new("RGB", (320, 160), color=(255, 0, 0)).save(source, format="PNG")

    async def fake_get_or_fetch_cover_bytes(url: str) -> tuple[bytes, str]:
        assert url == "https://i.ytimg.com/vi/demo/hqdefault.jpg"
        return source.getvalue(), "image/png"

    # 抽取后 get_or_fetch_cover_bytes 从模块级函数变为 _image_cache 的方法；
    # 注入 fake cache 到 obc_discovery.multimodal._image_cache。
    fake_cache = SimpleNamespace(get_or_fetch_cover_bytes=fake_get_or_fetch_cover_bytes)
    monkeypatch.setattr("obc_discovery.multimodal._image_cache", fake_cache)

    prepared = await prepare_cover_image_input(
        content_id="yt-demo",
        cover_url="https://i.ytimg.com/vi/demo/hqdefault.jpg",
        max_px=64,
        quality=60,
        timeout_seconds=1,
    )

    assert prepared is not None
    assert prepared.content_id == "yt-demo"
    assert prepared.mime_type == "image/jpeg"
    assert prepared.data_url.startswith("data:image/jpeg;base64,")

    encoded = prepared.data_url.split(",", 1)[1]
    image = Image.open(BytesIO(base64.b64decode(encoded)))
    assert max(image.size) <= 64
