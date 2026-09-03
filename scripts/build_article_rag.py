#!/usr/bin/env python3
"""Build the article RAG index from ``articles.content_text``.

Chunks every article that carries real body text, embeds each chunk with the
local Ollama ``bge-m3`` model, and stores the vectors in a *separate* SQLite
database (``data/article_rag.db``). The chat retriever
(``src/openbiliclaw/rag/retriever.py``) reads that DB at request time.

Design notes
------------
* Resumable: already-indexed ``article_id``s are skipped, so re-running picks
  up only new/edited articles. Run it in the background; it won't redo work.
* Local embedding only (no cloud quota / cost). ``bge-m3`` on CPU is ~3s/call,
  so we fan out with a small thread pool (default 3) and retry on transient
  connection errors with backoff.
* Vector storage is JSON text in the chunks table — simple and compatible with
  the retriever's pure-Python cosine scan.

Usage
-----
    .venv/bin/python scripts/build_article_rag.py            # full build
    .venv/bin/python scripts/build_article_rag.py --limit 50 # dry cap
    .venv/bin/python scripts/build_article_rag.py --concurrency 4
"""

from __future__ import annotations

import argparse
import array
import json
import math
import os
import re
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAIN_DB = PROJECT_ROOT / "data" / "openbiliclaw.db"
RAG_DB = PROJECT_ROOT / "data" / "article_rag.db"
CONFIG_PATH = PROJECT_ROOT / "config.toml"

CHUNK_CHARS = 600
MIN_CHUNK_CHARS = 30
EMBED_TIMEOUT = 30.0

_DEFAULT_EMBED = {
    "provider": "ollama",
    "model": "bge-m3",
    "base_url": "http://localhost:11434/v1",
    "api_key": "",
    "output_dimensionality": 1024,
}

_SENT_SPLIT = re.compile(r"[^。！？!?；;\n]*[。！？!?；;\n]?")


def _load_embed_config() -> dict:
    cfg = dict(_DEFAULT_EMBED)
    if tomllib is None or not CONFIG_PATH.exists():
        return cfg
    try:
        with open(CONFIG_PATH, "rb") as fh:
            data = tomllib.load(fh)
        emb = (data.get("llm") or {}).get("embedding") or {}
        for k in ("provider", "model", "base_url", "api_key", "output_dimensionality"):
            if emb.get(k) not in (None, ""):
                cfg[k] = emb[k]
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] read config.toml failed: {exc}", file=sys.stderr)
    return cfg


def _chunk_text(text: str) -> list[str]:
    """Split text into ~CHUNK_CHARS chunks with 1-sentence overlap.

    Handles both CJK and Latin text by splitting on sentence punctuation.
    """
    text = re.sub(r"\r\n|\r", "\n", text or "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []
    sentences = [s.strip() for s in _SENT_SPLIT.findall(text) if s and s.strip()]
    if not sentences:
        return [text] if len(text) >= MIN_CHUNK_CHARS else []

    chunks: list[str] = []
    current: list[str] = []
    cur_len = 0
    for sent in sentences:
        if current and cur_len + len(sent) > CHUNK_CHARS:
            chunk = "".join(current).strip()
            if len(chunk) >= MIN_CHUNK_CHARS:
                chunks.append(chunk)
            # overlap: carry the last sentence into the next chunk
            current = [current[-1]] if current else []
            cur_len = len(current[0]) if current else 0
        current.append(sent)
        cur_len += len(sent)
    if current:
        chunk = "".join(current).strip()
        if len(chunk) >= MIN_CHUNK_CHARS:
            chunks.append(chunk)
    return chunks


# --------------------------------------------------------------------------- #
# Embedding client (thread-safe, shared across worker threads)
# --------------------------------------------------------------------------- #
class EmbedClient:
    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg
        self._base = str(cfg.get("base_url") or "").rstrip("/")
        self._api_key = str(cfg.get("api_key") or "")
        self._model = cfg.get("model")
        self._local = threading.local()  # one httpx.Client per worker thread

    def _client(self) -> httpx.Client:
        cl = getattr(self._local, "client", None)
        if cl is None:
            cl = httpx.Client(timeout=EMBED_TIMEOUT)
            self._local.client = cl
        return cl

    def embed(self, text: str, max_retries: int = 4) -> list[float] | None:
        if not self._base:
            return None
        url = f"{self._base}/embeddings"
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        body = {"model": self._model, "input": text}
        delay = 2.0
        for attempt in range(max_retries):
            try:
                resp = self._client().post(url, json=body, headers=headers)
                resp.raise_for_status()
                return [float(x) for x in resp.json()["data"][0]["embedding"]]
            except Exception as exc:  # noqa: BLE001
                err = str(exc)
                if "429" in err or "Captcha" in err or "captcha" in err:
                    # Should never happen for a local model, but be safe.
                    print(f"[embed] rate-limited, aborting run: {err}", file=sys.stderr)
                    raise
                if attempt < max_retries - 1:
                    time.sleep(delay)
                    delay = min(delay * 2, 30.0)
                else:
                    print(f"[embed] failed after {max_retries} tries: {err}", file=sys.stderr)
        return None


# --------------------------------------------------------------------------- #
# RAG DB helpers
# --------------------------------------------------------------------------- #
# Content sources indexed into the same RAG index. ``source_table`` is stored
# on each chunk because ``articles.id`` and ``read_archive.id`` are separate
# ID spaces and would otherwise collide.
SOURCE_TABLES = (
    ("articles", "阅读库"),
    ("read_archive", "已读库"),
)


def _init_rag_db() -> sqlite3.Connection:
    RAG_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(RAG_DB), timeout=15.0)
    # MEMORY journal: no -wal/-shm/-journal temp files. Avoids the sandbox
    # file-write interception that blocks creating journal files for a new DB,
    # while still keeping per-transaction atomicity (committed rows are
    # durable in the main db file). WAL would be marginally better for
    # concurrent read-during-write, but the build commits per article so the
    # brief read locks are negligible.
    conn.execute("PRAGMA journal_mode=MEMORY")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_table TEXT NOT NULL DEFAULT 'articles',
            article_id INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            title TEXT DEFAULT '',
            url TEXT DEFAULT '',
            source_name TEXT DEFAULT '',
            author TEXT DEFAULT '',
            vector TEXT NOT NULL
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_article ON chunks(article_id)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
    )
    # Migration: older indexes were built before read_archive support. This
    # must run BEFORE the index below, which references the new column.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(chunks)").fetchall()}
    if "source_table" not in cols:
        conn.execute(
            "ALTER TABLE chunks ADD COLUMN source_table TEXT NOT NULL DEFAULT 'articles'"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_table, article_id)"
    )
    conn.commit()
    return conn


def _indexed_ids(conn: sqlite3.Connection, source_table: str) -> set[int]:
    try:
        rows = conn.execute(
            "SELECT DISTINCT article_id FROM chunks WHERE source_table=?",
            (source_table,),
        ).fetchall()
        return {int(r[0]) for r in rows}
    except sqlite3.OperationalError:
        return set()


def _store_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Build article RAG index")
    ap.add_argument("--limit", type=int, default=0, help="Max articles to process (0 = all)")
    ap.add_argument("--concurrency", type=int, default=3, help="Embed worker threads")
    ap.add_argument("--min-len", type=int, default=50, help="Min content_text length")
    ap.add_argument(
        "--source",
        default="",
        choices=["", "articles", "read_archive"],
        help="Restrict to one source table (default: all sources)",
    )
    ap.add_argument("--dry-run", action="store_true", help="List candidates only")
    args = ap.parse_args()

    if not MAIN_DB.exists():
        print(f"main db not found: {MAIN_DB}", file=sys.stderr)
        return 1
    cfg = _load_embed_config()
    dim = int(cfg.get("output_dimensionality") or 1024)
    print(f"[config] embedding {cfg.get('model')} @ {cfg.get('base_url')} dim={dim}")

    src = sqlite3.connect(str(MAIN_DB), timeout=15.0)
    src.row_factory = sqlite3.Row
    rag = _init_rag_db()
    client = EmbedClient(cfg)

    limit = args.limit or 0
    processed = 0
    chunks_written = 0
    failed = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as ex:
        for source_table, label in SOURCE_TABLES:
            if args.source and source_table != args.source:
                continue
            # ``id`` spaces are per-table, so each source is tracked separately.
            rows = src.execute(
                "SELECT id, title, url, author, source_name, content_text "
                f"FROM {source_table} WHERE content_text IS NOT NULL "
                f"AND length(content_text) >= {args.min_len} ORDER BY id"
            ).fetchall()
            done_ids = _indexed_ids(rag, source_table)
            candidates = [r for r in rows if int(r["id"]) not in done_ids]
            print(f"[scan] {label}({source_table}): {len(rows)} with content; "
                  f"{len(done_ids)} already indexed; {len(candidates)} to process")

            if args.dry_run:
                for r in candidates[:5]:
                    print(f"  would index {source_table}#{r['id']}: {r['title'][:40]}")
                continue

            # Process item-by-item to keep DB writes ordered & resumable.
            for r in candidates:
                if limit and processed >= limit:
                    break
                article_id = int(r["id"])
                chunks = _chunk_text(r["content_text"])
                if not chunks:
                    continue
                # Embed chunks concurrently for this item.
                futures = {ex.submit(client.embed, ch): i for i, ch in enumerate(chunks)}
                vecs: list[list[float] | None] = [None] * len(chunks)
                item_ok = True
                for fut in as_completed(futures):
                    idx = futures[fut]
                    try:
                        vecs[idx] = fut.result()
                    except Exception as exc:  # noqa: BLE001
                        print(
                            f"[embed] {source_table}#{article_id} chunk {idx} aborted: {exc}",
                            file=sys.stderr,
                        )
                        item_ok = False
                        break
                if not item_ok:
                    failed += 1
                    continue
                # Write chunks (delete stale first so re-indexing is idempotent).
                try:
                    rag.execute(
                        "DELETE FROM chunks WHERE source_table=? AND article_id=?",
                        (source_table, article_id),
                    )
                    for i, (ch, vec) in enumerate(zip(chunks, vecs)):
                        if not vec or len(vec) != dim:
                            failed += 1
                            continue
                        rag.execute(
                            "INSERT INTO chunks(source_table, article_id, chunk_index, text, "
                            "title, url, source_name, author, vector) "
                            "VALUES(?,?,?,?,?,?,?,?,?)",
                            (
                                source_table,
                                article_id,
                                i,
                                ch,
                                r["title"] or "",
                                r["url"] or "",
                                r["source_name"] or "",
                                r["author"] or "",
                                json.dumps(vec),
                            ),
                        )
                        chunks_written += 1
                    rag.commit()
                except sqlite3.OperationalError as exc:
                    print(
                        f"[db] {source_table}#{article_id} write failed: {exc}",
                        file=sys.stderr,
                    )
                    rag.rollback()
                    failed += 1
                    continue
                processed += 1
                if processed % 25 == 0:
                    rate = processed / max(0.01, time.time() - t0)
                    print(f"[progress] {processed} items, {chunks_written} chunks, "
                          f"{rate:.2f}/s")

            if limit and processed >= limit:
                break

    elapsed = time.time() - t0
    total_chunks = rag.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    _store_meta(rag, "built_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
    _store_meta(rag, "article_count", str(processed))
    _store_meta(rag, "chunk_count", str(total_chunks))
    rag.commit()
    rag.close()
    src.close()
    print(f"[done] indexed {processed} articles ({chunks_written} chunks), "
          f"{failed} failed. total chunks in db: {total_chunks}. "
          f"elapsed {elapsed/60:.1f}min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
