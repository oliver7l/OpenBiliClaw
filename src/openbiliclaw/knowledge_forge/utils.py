"""Knowledge Forge 工具函数。

重点说明 —— simhash 的中文处理：
    文档 3.5.4 的参考实现用 ``re.findall(r'[\\w\\u4e00-\\u9fff]+', text)`` 分词，
    但中文没有空格，这个正则会把整句（甚至整段）匹配成**一个 token**，
    导致 simhash 退化为「整串 hash」：任意一字不同 => 汉明距离巨大 => 相似度失效。
    这里改用**中文 2-gram + 英文单词**混合切分，才是中文近重检测的正确做法。
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import re
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

# 英文/数字 token
_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")
# 连续中文串
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
# HTML 标签
_TAG_RE = re.compile(r"<[^>]+>")
# HTML 实体
_ENTITY_RE = re.compile(r"&(?:[a-zA-Z][a-zA-Z0-9]{1,31}|#\d{1,7}|#[xX][0-9a-fA-F]{1,6});")
# 句子分隔
_SENT_SPLIT_RE = re.compile(r"[。！？!?；;\n]+")


def strip_html_tags(text: str, *, remove_links: bool = False) -> str:
    """移除 HTML 标签。remove_links=True 时连 <a> 的文本一起移除。"""
    if not text:
        return ""
    if remove_links:
        text = re.sub(r"<a\b[^>]*>.*?</a>", "", text, flags=re.S | re.I)
    # 先移除整块非正文标签及其内容
    for tag in ("script", "style", "nav", "footer", "header", "aside", "form", "noscript", "svg"):
        text = re.sub(rf"<{tag}\b[^>]*>.*?</{tag}>", "", text, flags=re.S | re.I)
    return _TAG_RE.sub("", text)


def decode_html_entities(text: str) -> str:
    """解码 HTML 实体（&amp; &#39; 等）。"""
    if not text or "&" not in text:
        return text
    return html.unescape(text)


def compress_whitespace(text: str, *, remove_empty_lines: bool = True) -> str:
    """压缩多余空白，统一换行符。"""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    if remove_empty_lines:
        text = "\n".join(line.strip() for line in text.split("\n") if line.strip())
    return text.strip()


def tokenize(text: str, *, ngram: int = 2) -> list[str]:
    """中英混合分词：中文按 n-gram 切，英文/数字按词切。"""
    if not text:
        return []
    tokens: list[str] = []
    lowered = text.lower()
    for seg in _CJK_RE.findall(lowered):
        if len(seg) <= ngram:
            tokens.append(seg)
        else:
            tokens.extend(seg[i : i + ngram] for i in range(len(seg) - ngram + 1))
    tokens.extend(_TOKEN_RE.findall(lowered))
    return tokens


def simhash(text: str, hash_bits: int = 64) -> int:
    """计算中文友好的 simhash（2-gram 分词 + 按位加权）。"""
    tokens = tokenize(text)
    if not tokens:
        return 0
    v = [0] * hash_bits
    for tok in tokens:
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
        for i in range(hash_bits):
            v[i] += 1 if h & (1 << i) else -1
    result = 0
    for i in range(hash_bits):
        if v[i] > 0:
            result |= 1 << i
    return result


def hamming_distance(h1: int, h2: int) -> int:
    """两个 simhash 的汉明距离。"""
    return bin(h1 ^ h2).count("1")


def simhash_similarity(h1: int, h2: int, hash_bits: int = 64) -> float:
    """Simhash 相似度 0-1。"""
    if not h1 and not h2:
        return 1.0
    return 1.0 - hamming_distance(h1, h2) / hash_bits


def content_hash(text: str) -> str:
    """正文 SHA256（前 32 位），用于精确去重。"""
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:32]


def count_effective_sentences(text: str, min_len: int = 8) -> int:
    """统计有效句子数（长度 >= min_len 且非纯符号）。"""
    if not text:
        return 0
    n = 0
    for seg in _SENT_SPLIT_RE.split(text):
        s = seg.strip()
        if len(s) >= min_len and re.search(r"[\u4e00-\u9fffA-Za-z0-9]", s):
            n += 1
    return n if n else (1 if len(text.strip()) >= min_len else 0)


def has_encoding_error(text: str) -> bool:
    """是否含乱码替换字符。"""
    return "\ufffd" in text if text else False


def truncate_lines(text: str, keep_until: int) -> str:
    """保留前 keep_until 行。"""
    lines = text.split("\n")
    return "\n".join(lines[:keep_until])


def iter_batches(items: Iterable[Any], size: int) -> Iterable[list[Any]]:
    """批量切分。"""
    batch: list[Any] = []
    for it in items:
        batch.append(it)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


# --------------------------------------------------------------------------- #
# KF LLM 客户端：文档 7.1/7.2 的「主 provider + fallback + 熔断」降级策略
# --------------------------------------------------------------------------- #


def _default_registry() -> Any:
    """惰性构建全局 LLM registry（只构建一次）。

    注意：build_llm_registry 期望 LLMConfig（config.llm），
    传整个 Config 会因缺少 ``openai`` 等属性而失败（obc_llm.registry）。
    """
    from openbiliclaw.config import load_config
    from openbiliclaw.llm._compat_registry import build_llm_registry

    cfg = load_config()
    llm_cfg = cfg.llm if hasattr(cfg, "llm") else cfg
    return build_llm_registry(llm_cfg)


class KFLlmClient:
    """Knowledge Forge 专属 LLM 客户端。

    每个模块可配置主 provider + fallback provider（文档 7.1）。
    降级策略（文档 7.2）：
        - 主 provider 调用失败（超时/限流/错误）→ 自动切 fallback
        - 每次降级记录日志
        - 主 provider 连续失败 ``max_failures`` 次 → 熔断 ``cooldown_seconds``，
          期间直接用 fallback；熔断结束后自动探测恢复
        - fallback 也失败 → 抛 ``RuntimeError`` 由调用方标记任务失败

    用法：:

        client = KFLlmClient()
        resp = await client.complete(
            spec, system_instruction="...", user_input="...", json_mode=True,
        )
    """

    def __init__(
        self,
        *,
        registry: Any | None = None,
        max_failures: int = 5,
        cooldown_seconds: int = 300,
    ) -> None:
        self._registry = registry or _default_registry()
        self._max_failures = max_failures
        self._cooldown_seconds = cooldown_seconds
        # provider -> 连续失败次数 / 熔断截止时间戳
        self._consecutive_failures: dict[str, int] = {}
        self._cooldown_until: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def complete(
        self,
        spec: Any,
        *,
        system_instruction: str,
        user_input: str,
        json_mode: bool = False,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        caller: str = "knowledge_forge",
    ) -> Any:
        """按 spec 的主/fallback provider 执行一次调用，返回 LLMResponse。"""
        messages = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_input},
        ]
        provider = getattr(spec, "provider", "") or ""
        fallback = getattr(spec, "fallback", "") or ""
        last_error: Exception | None = None

        for attempt in (provider, fallback):
            if not attempt:
                continue
            if await self._blocked(attempt):
                logger.info("KF LLM provider %s 处于熔断冷却期，跳过", attempt)
                continue
            try:
                resp = await self._registry.complete_provider(
                    attempt,
                    messages,
                    json_mode=json_mode,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    model=getattr(spec, "model", "") or None,
                )
                if not resp or not (resp.content or "").strip():
                    raise RuntimeError(f"provider {attempt} 返回空内容")
                await self._on_success(attempt)
                return resp
            except Exception as exc:  # noqa: BLE001 — 任何失败都走降级
                last_error = exc
                await self._on_failure(attempt)
                logger.warning(
                    "KF LLM provider %s 调用失败（caller=%s）：%s；%s",
                    attempt,
                    caller,
                    exc,
                    "尝试 fallback" if fallback and attempt != fallback else "无可用降级",
                )
        raise RuntimeError(
            f"KF LLM 全部 provider 失败（primary={provider}, fallback={fallback}）：{last_error}"
        ) from last_error

    async def _blocked(self, provider: str) -> bool:
        async with self._lock:
            until = self._cooldown_until.get(provider, 0.0)
            return time.time() < until

    async def _on_success(self, provider: str) -> None:
        async with self._lock:
            self._consecutive_failures.pop(provider, None)
            self._cooldown_until.pop(provider, None)

    async def _on_failure(self, provider: str) -> None:
        async with self._lock:
            n = self._consecutive_failures.get(provider, 0) + 1
            self._consecutive_failures[provider] = n
            if n >= self._max_failures:
                self._cooldown_until[provider] = time.time() + self._cooldown_seconds
                self._consecutive_failures[provider] = 0
                logger.warning(
                    "KF LLM provider %s 连续失败 %d 次，熔断 %.0f 秒",
                    provider,
                    self._max_failures,
                    self._cooldown_seconds,
                )


_llm_client: KFLlmClient | None = None


def get_llm_client(**kwargs: Any) -> KFLlmClient:
    """进程级单例 LLM 客户端（批量任务复用同一 registry 与熔断状态）。"""
    global _llm_client
    if _llm_client is None:
        _llm_client = KFLlmClient(**kwargs)
    return _llm_client
