"""3.3.3 观点矛盾检测器（Contradiction Detector）。

对共享主题标签的文章对，用 LLM 判断观点是否实质矛盾，写入
``article_relations``（relation_type=contradiction）。

流程（设计文档 3.3.3）：
    1. 查找同主题候选：共享 ≥ min_shared_tags 个核心标签的文章对
    2. LLM 检测：输入 文章A摘要 + 文章B摘要 + 主题，输出 是否矛盾/置信度/矛盾点
    3. confidence > 阈值 → 写入 article_relations（relation_type=contradiction）
    4. 生成矛盾报告

实现说明：
    - 增量：已存在 contradiction 关系的配对自动跳过
    - 摘要优先用 summary_compact，回退 ai_summary / 正文截断，控制 token 成本
    - 并发受限（max_concurrent），LLM 失败/不可用时整对跳过并记录
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from .config import ContradictionConfig, KnowledgeForgeConfig, load_kf_config
from .models import now_cn
from .prompts import CONTRADICTION_PROMPT
from .utils import get_llm_client

logger = logging.getLogger(__name__)


def _default_db_path() -> Path:
    try:
        from openbiliclaw.config import load_config

        cfg = load_config()
        p = getattr(cfg, "storage", None)
        if p is not None and getattr(p, "db_path", None):
            return Path(str(p.db_path))
    except Exception:  # noqa: BLE001
        pass
    return Path("data/openbiliclaw.db")


def _parse_tags(raw: str | None) -> list[str]:
    """解析 tags 字段（JSON 数组字符串或逗号分隔）。"""
    s = str(raw or "").strip()
    if not s:
        return []
    try:
        tags = json.loads(s)
        if isinstance(tags, list):
            return [str(t).strip() for t in tags if str(t).strip()]
    except Exception:  # noqa: BLE001
        pass
    return [t.strip() for t in s.split(",") if t.strip()]


class ContradictionDetector:
    """观点矛盾检测器。"""

    def __init__(
        self,
        *,
        config: KnowledgeForgeConfig | None = None,
        db_path: str | Path | None = None,
    ) -> None:
        self.config = config or load_kf_config()
        self.cfg: ContradictionConfig = self.config.contradiction
        self.db_path = Path(db_path) if db_path else _default_db_path()
        self.llm = get_llm_client()

    # ------------------------------------------------------------------ 主入口
    async def detect(
        self,
        *,
        limit: int = 0,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """检测观点矛盾（增量）。返回统计。"""
        stats: dict[str, Any] = {
            "pairs_scanned": 0,
            "contradictions": 0,
            "checked": 0,
            "skipped_existing": 0,
            "llm_errors": 0,
            "errors": [],
        }
        pairs = self._fetch_candidate_pairs(limit=limit)
        if not pairs:
            return stats
        stats["pairs_scanned"] = len(pairs)

        sem = asyncio.Semaphore(self.cfg.max_concurrent)

        async def _judge(pair: dict[str, Any]) -> dict[str, Any] | None:
            async with sem:
                return await self._judge_pair(pair, dry_run=dry_run)

        results = await asyncio.gather(*(_judge(p) for p in pairs), return_exceptions=True)
        for pair, res in zip(pairs, results, strict=False):
            if isinstance(res, BaseException):
                stats["llm_errors"] += 1
                stats["errors"].append({"pair": (pair["a_id"], pair["b_id"]), "error": str(res)})
                continue
            if res is None:
                continue
            stats["checked"] += 1
            if bool(res.get("contradiction")):
                stats["contradictions"] += 1
        return stats

    # ------------------------------------------------------------------ 候选
    def _fetch_candidate_pairs(self, *, limit: int = 0) -> list[dict[str, Any]]:
        """查找共享 ≥ min_shared_tags 个标签、且未检测过矛盾的文章对。

        做法：按 tag 分组（tags JSON），tag 出现次数 ≤ 20 的组内两两配对，
        避免热门标签产生 O(n²) 爆炸。已存在 contradiction 关系的配对跳过。
        """
        conn = self._connect()
        try:
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT id, tags, title, summary_compact, ai_summary,"
                    " content_cleaned, content_text FROM articles"
                    " WHERE tags IS NOT NULL AND tags <> ''"
                ).fetchall()
            ]
        finally:
            conn.close()

        # tag → 文章 id 列表（限制组大小控制组合数）
        tag_groups: dict[str, list[int]] = {}
        row_by_id: dict[int, dict[str, Any]] = {}
        for r in rows:
            row_by_id[int(r["id"])] = r
            for t in _parse_tags(r.get("tags")):
                if len(t) > 30:
                    continue
                tag_groups.setdefault(t, []).append(int(r["id"]))

        # 已有 contradiction 配对
        existing = self._existing_contradiction_pairs()
        candidates: dict[tuple[int, int], tuple[list[str], int, int]] = {}
        for tag, ids in tag_groups.items():
            if len(ids) < 2 or len(ids) > 20:
                continue
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    a, b = ids[i], ids[j]
                    key = (min(a, b), max(a, b))
                    if key in existing or key in candidates:
                        continue
                    candidates[key] = (candidates.get(key, ([], a, b))[0] + [tag], a, b)

        out: list[dict[str, Any]] = []
        for (a, b), (shared, _, _) in candidates.items():
            if len(shared) < self.cfg.min_shared_tags:
                continue
            ra, rb = row_by_id.get(a), row_by_id.get(b)
            if ra is None or rb is None:
                continue
            out.append(
                {
                    "a_id": a,
                    "b_id": b,
                    "shared_tags": shared,
                    "title_a": str(ra.get("title") or ""),
                    "title_b": str(rb.get("title") or ""),
                    "summary_a": self._pick_summary(ra),
                    "summary_b": self._pick_summary(rb),
                }
            )
        if limit and limit > 0:
            out = out[:limit]
        return out

    def _existing_contradiction_pairs(self) -> set[tuple[int, int]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT article_id_a, article_id_b FROM article_relations"
                " WHERE relation_type='contradiction'"
            ).fetchall()
            return {
                (
                    min(int(r["article_id_a"]), int(r["article_id_b"])),
                    max(int(r["article_id_a"]), int(r["article_id_b"])),
                )
                for r in rows
            }
        finally:
            conn.close()

    def _pick_summary(self, row: dict[str, Any]) -> str:
        """摘要选择：compact > ai_summary > 正文截断。"""
        for key in ("summary_compact", "ai_summary"):
            v = str(row.get(key) or "").strip()
            if v:
                return v[: self.cfg.summary_chars]
        body = str(row.get("content_cleaned") or row.get("content_text") or "").strip()
        return body[: self.cfg.summary_chars]

    # ------------------------------------------------------------------ LLM 判定
    async def _judge_pair(self, pair: dict[str, Any], *, dry_run: bool) -> dict[str, Any] | None:
        if not pair["summary_a"] or not pair["summary_b"]:
            return None
        topic = "、".join(pair["shared_tags"][:3])
        try:
            resp = await self.llm.complete(
                self.cfg.llm,
                system_instruction="你是观点矛盾检测器。只输出 JSON，不输出其他内容。",
                user_input=CONTRADICTION_PROMPT.format(
                    topic=topic,
                    summary_a=pair["summary_a"],
                    summary_b=pair["summary_b"],
                ),
                max_tokens=200,
                temperature=0.1,
                caller="knowledge_forge.contradiction",
                json_mode=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("contradiction LLM failed for %s/%s: %s", pair["a_id"], pair["b_id"], exc)
            return None

        parsed = self._parse_judgement((resp.content or "").strip())
        if parsed is None:
            return None
        a, b = pair["a_id"], pair["b_id"]
        # dry_run：不落库，仅返回结果
        if not dry_run:
            self._write_relation(
                a,
                b,
                parsed["contradiction"],
                parsed["confidence"],
                parsed["description"],
            )
        return {
            "contradiction": parsed["contradiction"],
            "confidence": parsed["confidence"],
            "description": parsed["description"],
            "pair": (a, b),
            "topic": topic,
        }

    def _parse_judgement(self, raw: str) -> dict[str, Any] | None:
        """解析 LLM 输出 JSON。容忍包在 ```json ... ``` 里。"""
        if not raw:
            return None
        text = raw.strip()
        if text.startswith("```"):
            # 去掉围栏
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            data = json.loads(text)
        except Exception:  # noqa: BLE001
            # 尝试提取第一个 { ... }
            try:
                start = text.index("{")
                end = text.rindex("}")
                data = json.loads(text[start : end + 1])
            except Exception:  # noqa: BLE001
                logger.debug("contradiction parse failed: %s", raw[:200])
                return None
        if not isinstance(data, dict):
            return None
        contradiction = bool(data.get("contradiction"))
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        description = str(data.get("description") or "")
        # 置信度低于阈值视为不矛盾（不写库）
        return {
            "contradiction": contradiction and confidence >= self.cfg.confidence_threshold,
            "confidence": confidence,
            "description": description,
        }

    # ------------------------------------------------------------------ 存储
    def _write_relation(
        self, a: int, b: int, contradiction: bool, confidence: float, description: str
    ) -> None:
        """写 contradiction 关系。不矛盾的高置信度结论也记录（供审计），
        但仅 contradiction=True 写入 relation_type=contradiction。"""
        rtype = "contradiction" if contradiction else "not_contradiction"
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO article_relations
                   (article_id_a, article_id_b, relation_type, confidence,
                    description, created_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(article_id_a, article_id_b, relation_type)
                   DO UPDATE SET confidence=excluded.confidence,
                       description=excluded.description""",
                (a, b, rtype, round(confidence, 3), description[:500], now_cn()),
            )
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def generate_report(self, limit: int = 50) -> str:
        """生成矛盾报告（Markdown）。"""
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT r.article_id_a, a.title AS title_a, r.article_id_b,
                          b.title AS title_b, r.confidence, r.description, r.created_at
                   FROM article_relations r
                   LEFT JOIN articles a ON a.id = r.article_id_a
                   LEFT JOIN articles b ON b.id = r.article_id_b
                   WHERE r.relation_type='contradiction'
                   ORDER BY r.confidence DESC, r.created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        finally:
            conn.close()
        if not rows:
            return "# ⚔️ 观点矛盾报告\n\n暂无已标记的矛盾观点。"
        lines = ["# ⚔️ 观点矛盾报告", "", f"生成时间：{now_cn()}", ""]
        for r in rows:
            lines.append(
                f"- **文章{int(r['article_id_a'])}**《{r['title_a']}》 vs "
                f"**文章{int(r['article_id_b'])}**《{r['title_b']}》"
                f"（置信度 {float(r['confidence']):.0%}）"
            )
            if r["description"]:
                lines.append(f"  - 矛盾点：{r['description'][:200]}")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 同步便捷入口
# --------------------------------------------------------------------------- #
def run_contradiction_detection(
    *,
    limit: int = 0,
    dry_run: bool = False,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """同步执行观点矛盾检测（内部跑事件循环）。"""

    async def _run() -> dict[str, Any]:
        return await ContradictionDetector(db_path=db_path).detect(limit=limit, dry_run=dry_run)

    return asyncio.run(_run())
