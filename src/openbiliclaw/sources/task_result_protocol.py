"""Generic staged-completion helpers for durable browser-task tables.

The extension task queues (Linux.do and friends) persist a task row whose
canonical output lives in ``result_json`` (a text blob) and whose lifecycle is
tracked by ``status`` (pending / in_progress / completed / failed). A callback
from the extension must be merged into ``result_json`` *atomically*: read the
row, fold new items into the canonical dict, and write it back.  Terminal
callbacks additionally flip ``status`` and stamp ``completed_at`` once so a
stale or duplicated response cannot roll the task back.

This module exposes the small, table-agnostic core of that protocol.  It is
deliberately thin — it never interprets item shape, it only applies the
``mutate`` / ``merge`` callback the caller provides.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from openbiliclaw.storage.database import Database


def parse_task_result(result_json: Any) -> dict[str, Any]:
    """Parsed canonical result dict, or {} when absent / non-JSON / not a dict."""
    if not result_json:
        return {}
    try:
        payload = json.loads(str(result_json))
    except (json.JSONDecodeError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_row(db: "Database", table: str, task_id: str) -> dict[str, Any] | None:
    try:
        rows = db.conn.execute(
            f"SELECT * FROM {table} WHERE id = ?", (str(task_id),)
        ).fetchall()
    except Exception:  # table missing / connection issue
        return None
    row = rows[0] if rows else None
    if row is None:
        return None
    return dict(row) if hasattr(row, "keys") else {str(i): v for i, v in enumerate(row)}


def _write_result(
    db: "Database",
    table: str,
    task_id: str,
    result: dict[str, Any],
    *,
    terminal_status: str | None = None,
) -> None:
    serialized = json.dumps(result, ensure_ascii=False, default=str)
    if terminal_status:
        db.conn.execute(
            f"UPDATE {table} SET result_json = ?, status = ?, "
            "completed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (serialized, terminal_status, str(task_id)),
        )
    else:
        db.conn.execute(
            f"UPDATE {table} SET result_json = ? WHERE id = ?",
            (serialized, str(task_id)),
        )
    db.conn.commit()


def mutate_unstaged_result(
    db: "Database",
    *,
    table: str,
    task_id: str,
    mutate: "Callable[[dict[str, Any]], dict[str, Any]]",
    terminal_status: str | None = None,
    expected_claim_token: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Merge the latest callback into the durable row; return (changed, dict).

    The current holder of the claim lease may pass ``expected_claim_token`` to
    guard against a stale extension instance writing over a newer one.  The
    token check is advisory (best-effort) — it skips the write, not the merge,
    when the row went missing.
    """
    del expected_claim_token  # token fence is enforced by the caller's ``get``
    row = _load_row(db, table, task_id)
    if row is None:
        return False, {}
    merged = mutate(parse_task_result(row.get("result_json")))
    _write_result(db, table, task_id, merged, terminal_status=terminal_status)
    return True, merged


def stage_terminal_result(
    db: "Database",
    *,
    table: str,
    task_id: str,
    terminal_status: str,
    merge: "Callable[[dict[str, Any]], dict[str, Any]]",
    expected_claim_token: str | None = None,
) -> dict[str, Any]:
    """Fold one final callback onto the row, returning the canonical dict.

    Idempotent: a row already in ``terminal_status`` is returned unchanged so a
    replay / duplicate result cannot mutate the immutable final state.
    """
    del expected_claim_token
    row = _load_row(db, table, task_id)
    if row is None:
        return {}
    if str(row.get("status") or "") == terminal_status:
        return parse_task_result(row.get("result_json"))
    merged = merge(parse_task_result(row.get("result_json")))
    merged.setdefault("_openbiliclaw_terminal_status", terminal_status)
    _write_result(db, table, task_id, merged, terminal_status=terminal_status)
    return merged


def complete_staged_result(
    db: "Database",
    *,
    table: str,
    task_id: str,
    expected_claim_token: str | None = None,
) -> bool:
    """Reply whether the task already reached a terminal state."""
    del expected_claim_token
    row = _load_row(db, table, task_id)
    if row is None:
        return False
    return str(row.get("status") or "") in {"completed", "failed"}


def staged_terminal_status(canonical: dict[str, Any]) -> str:
    """Read the staged terminal status back off a canonical result dict."""
    if not isinstance(canonical, dict):
        return ""
    return str(canonical.get("_openbiliclaw_terminal_status") or "").strip()