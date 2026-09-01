"""Probe sensenova json_mode behavior. Does NOT print api keys."""
import asyncio
import json

from openbiliclaw.config import load_config
from openbiliclaw.llm.registry import build_llm_registry


def _mask(s: str) -> str:
    return (s[:6] + "…" + s[-4:]) if s and len(s) > 12 else (s or "")


async def main() -> None:
    cfg = load_config()
    registry = build_llm_registry(cfg)
    provider = registry.get("openai")
    print(f"provider_type={type(provider).__name__}")
    print(f"model={provider._model} base_url={_mask(provider.base_url)}")

    messages = [
        {
            "role": "system",
            "content": "只输出 JSON，不要多余文字，格式：{\"one_liner\":\"一句话\",\"points\":[\"要点1\",\"要点2\",\"要点3\"]}",
        },
        {
            "role": "user",
            "content": "标题：Sensenova JSON 模式探测\n正文：" + ("这是用于探测 JSON 模式的测试正文内容。" * 30),
        },
    ]

    # 1) json_mode=True (mirror the summarize endpoint)
    try:
        r = await provider.complete(messages, temperature=0.3, max_tokens=700, json_mode=True)
        print("\n[1] json_mode=True max_tokens=700 -> OK")
        print("    content:", repr((r.content or "")[:120]))
    except Exception as e:  # noqa: BLE001
        print(f"\n[1] json_mode=True max_tokens=700 -> FAIL {type(e).__name__}: {e}")

    # 2) json_mode=False
    try:
        r = await provider.complete(messages, temperature=0.3, max_tokens=700, json_mode=False)
        print("\n[2] json_mode=False -> OK")
        print("    content:", repr((r.content or "")[:120]))
    except Exception as e:  # noqa: BLE001
        print(f"\n[2] json_mode=False -> FAIL {type(e).__name__}: {e}")

    # 3) raw SDK call to inspect full message fields (reasoning_content etc.)
    try:
        client = provider._client  # noqa: SLF001  -- reuse the configured AsyncOpenAI client
        raw = await client.chat.completions.create(
            model=provider._model,
            messages=messages,
            temperature=0.3,
            max_tokens=700,
            response_format={"type": "json_object"},
        )
        msg = raw.choices[0].message
        print("\n[3] raw SDK json_object -> HTTP OK")
        print("    message fields:", [k for k in vars(msg) if not k.startswith("_")])
        d = msg.model_dump(exclude_none=True)
        for k, v in d.items():
            s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
            print(f"    {k}: {repr(s[:300])}")
        print("    usage:", raw.usage)
        print("    finish_reason:", raw.choices[0].finish_reason)
    except Exception as e:  # noqa: BLE001
        print(f"\n[3] raw SDK json_object -> FAIL {type(e).__name__}: {e}")


    # 4) big max_tokens, json_mode=True (hypothesis: reasoning eats the budget)
    try:
        r = await provider.complete(messages, temperature=0.3, max_tokens=3000, json_mode=True)
        print("\n[4] json_mode=True max_tokens=3000 -> OK")
        print("    content:", repr((r.content or "")[:200]))
    except Exception as e:  # noqa: BLE001
        print(f"\n[4] json_mode=True max_tokens=3000 -> FAIL {type(e).__name__}: {e}")

    # 5) raw SDK, max_tokens=3000, no response_format
    try:
        client = provider._client  # noqa: SLF001
        raw = await client.chat.completions.create(
            model=provider._model,
            messages=messages,
            temperature=0.3,
            max_tokens=3000,
        )
        msg = raw.choices[0].message
        print("\n[5] raw SDK max_tokens=3000 (no response_format) -> HTTP OK")
        d = msg.model_dump(exclude_none=True)
        for k, v in d.items():
            s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
            print(f"    {k}: {repr(s[:300])}")
        print("    finish_reason:", raw.choices[0].finish_reason, "| usage:", raw.usage)
    except Exception as e:  # noqa: BLE001
        print(f"\n[5] raw SDK max_tokens=3000 -> FAIL {type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
