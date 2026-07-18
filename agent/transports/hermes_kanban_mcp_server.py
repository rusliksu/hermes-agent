"""Dedicated Hermes Kanban MCP server.

This server intentionally exposes only a narrow Kanban task API.  The default
startup mode is read-only: it opens SQLite via ``mode=ro`` + ``query_only`` and
does not route reads through ``kanban_db.connect()``, which would initialize the
database and create lock/WAL sidecar files.

Run with:

    python -m agent.transports.hermes_kanban_mcp_server
    python -m agent.transports.hermes_kanban_mcp_server --allow-write
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)

READ_TOOLS: tuple[str, ...] = (
    "kanban_board_status",
    "kanban_list_tasks",
)

WRITE_TOOLS: tuple[str, ...] = (
    "kanban_enqueue",
    "kanban_claim_next",
    "kanban_heartbeat",
    "kanban_complete",
    "kanban_block",
    "kanban_add_dependency",
    "kanban_reclaim",
    "kanban_import_openspec_tasks",
)

MAX_TITLE_CHARS = 300
MAX_BODY_CHARS = 8 * 1024
MAX_FIELD_CHARS = 4 * 1024
MAX_METADATA_BYTES = 4 * 1024
DEFAULT_LIST_LIMIT = 100
MAX_LIST_LIMIT = 200
MAX_LEASE_SECONDS = 24 * 60 * 60
CLAIM_CANDIDATE_LIMIT = 20


def _ok(**fields: Any) -> dict[str, Any]:
    return {"ok": True, **fields}


def _err(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


def _bounded_text(
    value: Any,
    *,
    name: str,
    max_chars: int,
    required: bool = False,
) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    if value is None:
        if required:
            return None, _err("invalid_argument", f"{name} is required")
        return None, None
    text = str(value)
    if required and not text.strip():
        return None, _err("invalid_argument", f"{name} is required")
    if len(text) > max_chars:
        return None, _err(
            "invalid_argument",
            f"{name} exceeds {max_chars} characters",
        )
    return text, None


def _bounded_int(
    value: Any,
    *,
    name: str,
    default: Optional[int] = None,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> tuple[Optional[int], Optional[dict[str, Any]]]:
    if value is None:
        return default, None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None, _err("invalid_argument", f"{name} must be an integer")
    if minimum is not None and parsed < minimum:
        return None, _err("invalid_argument", f"{name} must be >= {minimum}")
    if maximum is not None and parsed > maximum:
        return None, _err("invalid_argument", f"{name} must be <= {maximum}")
    return parsed, None


def _bounded_metadata(
    value: Any,
) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    if value is None:
        return None, None
    if not isinstance(value, dict):
        return None, _err("invalid_argument", "metadata must be an object")
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        return None, _err(
            "invalid_argument",
            f"metadata must be JSON-serializable: {exc}",
        )
    if len(encoded.encode("utf-8")) > MAX_METADATA_BYTES:
        return None, _err(
            "invalid_argument",
            f"metadata exceeds {MAX_METADATA_BYTES} bytes as JSON",
        )
    return value, None


def _safe_board_metadata(board: Optional[str]) -> dict[str, Any]:
    from hermes_cli import kanban_db as kb

    meta = dict(kb.read_board_metadata(board))
    meta.pop("db_path", None)
    meta.pop("default_workdir", None)
    return meta


def _resolve_board_and_path(board: Optional[str]) -> tuple[str, Path]:
    from hermes_cli import kanban_db as kb

    # kanban_db_path validates explicit board slugs through the canonical
    # normalizer without creating the DB or board directories.
    path = kb.kanban_db_path(board=board)
    slug = (
        board.strip().lower()
        if isinstance(board, str) and board.strip()
        else kb.get_current_board()
    )
    return slug, path


@contextlib.contextmanager
def _readonly_connection(board: Optional[str]):
    slug, path = _resolve_board_and_path(board)
    if not path.exists():
        yield slug, path, None
        return

    # immutable=1 prevents SQLite from creating WAL/SHM sidecars while still
    # using URI mode=ro. These read tools are short-lived point-in-time probes.
    uri = f"file:{quote(str(path.resolve()), safe='/')}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        yield slug, path, conn
    finally:
        conn.close()


def _task_payload(task: Any, *, include_body: bool) -> dict[str, Any]:
    data = {
        "id": task.id,
        "title": task.title,
        "assignee": task.assignee,
        "status": task.status,
        "priority": task.priority,
        "created_by": task.created_by,
        "created_at": task.created_at,
        "started_at": task.started_at,
        "completed_at": task.completed_at,
        "tenant": task.tenant,
        "workspace_kind": task.workspace_kind,
        "branch_name": task.branch_name,
        "project_id": task.project_id,
        "claim_expires": task.claim_expires,
        "current_run_id": task.current_run_id,
    }
    if include_body:
        data["body"] = task.body
    return data


def _claim_token_matches(
    conn: sqlite3.Connection,
    task_id: str,
    claim_token: str,
) -> tuple[Optional[int], Optional[dict[str, Any]]]:
    row = conn.execute(
        "SELECT status, claim_lock, current_run_id FROM tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    if row is None:
        return None, _err("not_found", f"task {task_id} was not found")
    if row["status"] != "running" or not row["claim_lock"]:
        return None, _err(
            "claim_required",
            f"task {task_id} is not running under a claim",
        )
    if row["claim_lock"] != claim_token:
        return None, _err("claim_mismatch", "claim_token does not own this task")
    run_id = row["current_run_id"]
    if run_id is None:
        return None, _err("claim_required", f"task {task_id} has no active run")
    return int(run_id), None


def kanban_board_status(board: Optional[str] = None) -> dict[str, Any]:
    """Return safe board metadata and aggregate task counts by status."""
    try:
        from hermes_cli import kanban_db as kb

        with _readonly_connection(board) as (slug, _path, conn):
            metadata = _safe_board_metadata(slug)
            counts = {status: 0 for status in sorted(kb.VALID_STATUSES)}
            db: dict[str, Any] = {"exists": conn is not None}
            if conn is not None:
                rows = conn.execute(
                    "SELECT status, COUNT(*) AS count FROM tasks GROUP BY status"
                ).fetchall()
                for row in rows:
                    counts[str(row["status"])] = int(row["count"])
                page_count = conn.execute("PRAGMA page_count").fetchone()[0]
                page_size = conn.execute("PRAGMA page_size").fetchone()[0]
                db.update(
                    {
                        "page_count": int(page_count),
                        "page_size": int(page_size),
                        "schema_version": int(
                            conn.execute("PRAGMA schema_version").fetchone()[0]
                        ),
                        "user_version": int(
                            conn.execute("PRAGMA user_version").fetchone()[0]
                        ),
                    }
                )
            return _ok(board=slug, metadata=metadata, db=db, counts_by_status=counts)
    except ValueError as exc:
        return _err("invalid_argument", str(exc))
    except sqlite3.Error as exc:
        return _err("sqlite_error", str(exc))


def kanban_list_tasks(
    board: Optional[str] = None,
    status: Optional[str] = None,
    assignee: Optional[str] = None,
    limit: int = DEFAULT_LIST_LIMIT,
) -> dict[str, Any]:
    """List task metadata only: no bodies, comments, results, paths, or runs."""
    parsed_limit, error = _bounded_int(
        limit,
        name="limit",
        default=DEFAULT_LIST_LIMIT,
        minimum=1,
        maximum=MAX_LIST_LIMIT,
    )
    if error:
        return error
    try:
        from hermes_cli import kanban_db as kb

        if status is not None and status not in kb.VALID_STATUSES:
            return _err("invalid_argument", f"status must be one of {sorted(kb.VALID_STATUSES)}")
        assignee_text, error = _bounded_text(assignee, name="assignee", max_chars=128)
        if error:
            return error
        if assignee_text is not None:
            assignee_text = assignee_text.strip() or None

        with _readonly_connection(board) as (slug, _path, conn):
            if conn is None:
                return _ok(
                    board=slug,
                    tasks=[],
                    count=0,
                    limit=parsed_limit,
                    truncated=False,
                )
            query = """
                SELECT
                    t.id, t.title, t.assignee, t.status, t.priority,
                    t.created_by, t.created_at, t.started_at, t.completed_at,
                    t.tenant, t.workspace_kind, t.branch_name, t.project_id,
                    (SELECT COUNT(*) FROM task_links l WHERE l.child_id = t.id) AS parent_count,
                    (SELECT COUNT(*) FROM task_links l WHERE l.parent_id = t.id) AS child_count
                  FROM tasks t
                 WHERE 1=1
            """
            params: list[Any] = []
            if status is not None:
                query += " AND t.status = ?"
                params.append(status)
            else:
                query += " AND t.status != 'archived'"
            if assignee_text is not None:
                assignee_text = kb._canonical_assignee(assignee_text)
                query += " AND t.assignee = ?"
                params.append(assignee_text)
            query += " ORDER BY t.priority DESC, t.created_at ASC, t.id ASC LIMIT ?"
            params.append(int(parsed_limit) + 1)
            rows = conn.execute(query, params).fetchall()
            visible = rows[: int(parsed_limit)]
            return _ok(
                board=slug,
                tasks=[dict(row) for row in visible],
                count=len(visible),
                limit=int(parsed_limit),
                truncated=len(rows) > int(parsed_limit),
            )
    except ValueError as exc:
        return _err("invalid_argument", str(exc))
    except sqlite3.Error as exc:
        return _err("sqlite_error", str(exc))


def _write_conn(board: Optional[str]):
    from hermes_cli import kanban_db as kb

    return kb, kb.connect(board=board)


def kanban_enqueue(
    title: str,
    body: Optional[str] = None,
    assignee: Optional[str] = None,
    priority: Optional[int] = None,
    board: Optional[str] = None,
) -> dict[str, Any]:
    title_text, error = _bounded_text(
        title,
        name="title",
        max_chars=MAX_TITLE_CHARS,
        required=True,
    )
    if error:
        return error
    body_text, error = _bounded_text(body, name="body", max_chars=MAX_BODY_CHARS)
    if error:
        return error
    assignee_text, error = _bounded_text(
        assignee,
        name="assignee",
        max_chars=128,
    )
    if error:
        return error
    parsed_priority, error = _bounded_int(
        priority,
        name="priority",
        default=0,
        minimum=-1000,
        maximum=1000,
    )
    if error:
        return error
    try:
        kb, conn = _write_conn(board)
        try:
            task_id = kb.create_task(
                conn,
                title=title_text or "",
                body=body_text,
                assignee=(assignee_text.strip() if assignee_text else None),
                priority=int(parsed_priority or 0),
                initial_status="running",
                board=board,
            )
            task = kb.get_task(conn, task_id)
            return _ok(
                board=(board or kb.get_current_board()),
                task=(
                    _task_payload(task, include_body=True)
                    if task
                    else {"id": task_id}
                ),
            )
        finally:
            conn.close()
    except ValueError as exc:
        return _err("invalid_argument", str(exc))
    except Exception as exc:
        logger.exception("kanban_enqueue failed")
        return _err("kanban_error", str(exc))


def kanban_claim_next(
    assignee: str,
    board: Optional[str] = None,
    lease_seconds: Optional[int] = None,
) -> dict[str, Any]:
    assignee_text, error = _bounded_text(
        assignee,
        name="assignee",
        max_chars=128,
        required=True,
    )
    if error:
        return error
    lease, error = _bounded_int(
        lease_seconds,
        name="lease_seconds",
        default=None,
        minimum=1,
        maximum=MAX_LEASE_SECONDS,
    )
    if error:
        return error
    assignee_raw = (assignee_text or "").strip()
    try:
        kb, conn = _write_conn(board)
        try:
            assignee_text = kb._canonical_assignee(assignee_raw) or ""
            claim_token = f"mcp:{assignee_text}:{secrets.token_urlsafe(24)}"
            rows = conn.execute(
                """
                SELECT id
                  FROM tasks
                 WHERE status = 'ready'
                   AND claim_lock IS NULL
                   AND (assignee IS NULL OR assignee = ?)
                 ORDER BY priority DESC, created_at ASC, id ASC
                 LIMIT ?
                """,
                (assignee_text, CLAIM_CANDIDATE_LIMIT),
            ).fetchall()
            for row in rows:
                claimed = kb.claim_task(
                    conn,
                    row["id"],
                    ttl_seconds=lease,
                    claimer=claim_token,
                )
                if claimed is not None:
                    return _ok(
                        board=(board or kb.get_current_board()),
                        claimed=True,
                        claim_token=claim_token,
                        task=_task_payload(claimed, include_body=True),
                    )
            return _ok(
                board=(board or kb.get_current_board()),
                claimed=False,
                task=None,
            )
        finally:
            conn.close()
    except ValueError as exc:
        return _err("invalid_argument", str(exc))
    except Exception as exc:
        logger.exception("kanban_claim_next failed")
        return _err("kanban_error", str(exc))


def kanban_heartbeat(
    task_id: str,
    claim_token: str,
    board: Optional[str] = None,
) -> dict[str, Any]:
    task_id_text, error = _bounded_text(
        task_id,
        name="task_id",
        max_chars=128,
        required=True,
    )
    if error:
        return error
    token_text, error = _bounded_text(
        claim_token,
        name="claim_token",
        max_chars=256,
        required=True,
    )
    if error:
        return error
    try:
        kb, conn = _write_conn(board)
        try:
            run_id, error = _claim_token_matches(
                conn,
                task_id_text or "",
                token_text or "",
            )
            if error:
                return error
            claim_ok = kb.heartbeat_claim(conn, task_id_text or "", claimer=token_text)
            worker_ok = kb.heartbeat_worker(
                conn,
                task_id_text or "",
                expected_run_id=run_id,
            )
            if not (claim_ok and worker_ok):
                return _err("claim_required", f"could not heartbeat {task_id_text}")
            return _ok(board=(board or kb.get_current_board()), task_id=task_id_text)
        finally:
            conn.close()
    except ValueError as exc:
        return _err("invalid_argument", str(exc))
    except Exception as exc:
        logger.exception("kanban_heartbeat failed")
        return _err("kanban_error", str(exc))


def kanban_complete(
    task_id: str,
    claim_token: str,
    summary: str,
    result: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    board: Optional[str] = None,
) -> dict[str, Any]:
    task_id_text, error = _bounded_text(
        task_id,
        name="task_id",
        max_chars=128,
        required=True,
    )
    if error:
        return error
    token_text, error = _bounded_text(
        claim_token,
        name="claim_token",
        max_chars=256,
        required=True,
    )
    if error:
        return error
    summary_text, error = _bounded_text(
        summary,
        name="summary",
        max_chars=MAX_FIELD_CHARS,
        required=True,
    )
    if error:
        return error
    result_text, error = _bounded_text(result, name="result", max_chars=MAX_BODY_CHARS)
    if error:
        return error
    metadata_obj, error = _bounded_metadata(metadata)
    if error:
        return error
    try:
        from agent.redact import redact_sensitive_text

        summary_text = redact_sensitive_text(summary_text or "", force=True)
        if result_text:
            result_text = redact_sensitive_text(result_text, force=True)
        if metadata_obj is not None:
            redacted = redact_sensitive_text(
                json.dumps(metadata_obj, ensure_ascii=False),
                force=True,
            )
            metadata_obj = json.loads(redacted)
    except Exception:
        pass
    try:
        kb, conn = _write_conn(board)
        try:
            run_id, error = _claim_token_matches(
                conn,
                task_id_text or "",
                token_text or "",
            )
            if error:
                return error
            ok = kb.complete_task(
                conn,
                task_id_text or "",
                summary=summary_text,
                result=result_text,
                metadata=metadata_obj,
                expected_run_id=run_id,
            )
            if not ok:
                return _err("kanban_error", f"could not complete {task_id_text}")
            task = kb.get_task(conn, task_id_text or "")
            return _ok(
                board=(board or kb.get_current_board()),
                task=_task_payload(task, include_body=False) if task else None,
            )
        finally:
            conn.close()
    except ValueError as exc:
        return _err("invalid_argument", str(exc))
    except Exception as exc:
        logger.exception("kanban_complete failed")
        return _err("kanban_error", str(exc))


def kanban_block(
    task_id: str,
    claim_token: str,
    reason: str,
    kind: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    board: Optional[str] = None,
) -> dict[str, Any]:
    task_id_text, error = _bounded_text(
        task_id,
        name="task_id",
        max_chars=128,
        required=True,
    )
    if error:
        return error
    token_text, error = _bounded_text(
        claim_token,
        name="claim_token",
        max_chars=256,
        required=True,
    )
    if error:
        return error
    reason_text, error = _bounded_text(
        reason,
        name="reason",
        max_chars=MAX_FIELD_CHARS,
        required=True,
    )
    if error:
        return error
    metadata_obj, error = _bounded_metadata(metadata)
    if error:
        return error
    try:
        from agent.redact import redact_sensitive_text

        reason_text = redact_sensitive_text(reason_text or "", force=True)
        if metadata_obj is not None:
            redacted = redact_sensitive_text(
                json.dumps(metadata_obj, ensure_ascii=False),
                force=True,
            )
            metadata_obj = json.loads(redacted)
    except Exception:
        pass
    try:
        kb, conn = _write_conn(board)
        try:
            if kind is not None and kind not in kb.VALID_BLOCK_KINDS:
                return _err(
                    "invalid_argument",
                    f"kind must be one of {sorted(kb.VALID_BLOCK_KINDS)}",
                )
            run_id, error = _claim_token_matches(
                conn,
                task_id_text or "",
                token_text or "",
            )
            if error:
                return error
            ok = kb.block_task(
                conn,
                task_id_text or "",
                reason=reason_text,
                kind=kind,
                expected_run_id=run_id,
            )
            if not ok:
                return _err("kanban_error", f"could not block {task_id_text}")
            if metadata_obj is not None:
                # block_task has no metadata argument; attach bounded metadata to
                # the same closing run rather than inventing a parallel state path.
                conn.execute(
                    "UPDATE task_runs SET metadata = ? WHERE id = ?",
                    (json.dumps(metadata_obj, ensure_ascii=False), run_id),
                )
            task = kb.get_task(conn, task_id_text or "")
            return _ok(
                board=(board or kb.get_current_board()),
                task=_task_payload(task, include_body=False) if task else None,
            )
        finally:
            conn.close()
    except ValueError as exc:
        return _err("invalid_argument", str(exc))
    except Exception as exc:
        logger.exception("kanban_block failed")
        return _err("kanban_error", str(exc))


def kanban_add_dependency(
    parent_id: str,
    child_id: str,
    board: Optional[str] = None,
) -> dict[str, Any]:
    parent_text, error = _bounded_text(
        parent_id,
        name="parent_id",
        max_chars=128,
        required=True,
    )
    if error:
        return error
    child_text, error = _bounded_text(
        child_id,
        name="child_id",
        max_chars=128,
        required=True,
    )
    if error:
        return error
    try:
        kb, conn = _write_conn(board)
        try:
            kb.link_tasks(conn, parent_text or "", child_text or "")
            return _ok(
                board=(board or kb.get_current_board()),
                parent_id=parent_text,
                child_id=child_text,
            )
        finally:
            conn.close()
    except ValueError as exc:
        return _err("invalid_argument", str(exc))
    except Exception as exc:
        logger.exception("kanban_add_dependency failed")
        return _err("kanban_error", str(exc))


def kanban_reclaim(
    task_id: str,
    reason: str,
    board: Optional[str] = None,
) -> dict[str, Any]:
    task_id_text, error = _bounded_text(
        task_id,
        name="task_id",
        max_chars=128,
        required=True,
    )
    if error:
        return error
    reason_text, error = _bounded_text(
        reason,
        name="reason",
        max_chars=MAX_FIELD_CHARS,
        required=True,
    )
    if error:
        return error
    try:
        kb, conn = _write_conn(board)
        try:
            ok = kb.reclaim_task(conn, task_id_text or "", reason=reason_text)
            if not ok:
                return _err("kanban_error", f"could not reclaim {task_id_text}")
            task = kb.get_task(conn, task_id_text or "")
            return _ok(
                board=(board or kb.get_current_board()),
                task=_task_payload(task, include_body=False) if task else None,
            )
        finally:
            conn.close()
    except ValueError as exc:
        return _err("invalid_argument", str(exc))
    except Exception as exc:
        logger.exception("kanban_reclaim failed")
        return _err("kanban_error", str(exc))


def kanban_import_openspec_tasks(
    source_path: str,
    repo: Optional[str] = None,
    board: Optional[str] = None,
) -> dict[str, Any]:
    """Import minimal OpenSpec tasks.md checkboxes into Kanban.

    Supports only ``- [ ] 1.1 Title`` and ``- [x] 1.2 Title`` task lines
    under ``openspec/changes/<change-slug>/tasks.md``. The source file is read
    but never modified; existing Kanban tasks update only source-owned fields.
    """
    source_text, error = _bounded_text(
        source_path,
        name="source_path",
        max_chars=4096,
        required=True,
    )
    if error:
        return error
    repo_text, error = _bounded_text(repo, name="repo", max_chars=256)
    if error:
        return error
    try:
        kb, conn = _write_conn(board)
        try:
            from hermes_cli.kanban_openspec import import_openspec_tasks_md

            result = import_openspec_tasks_md(
                conn,
                source_text or "",
                repo=(repo_text.strip() if repo_text else None),
            )
            return _ok(board=(board or kb.get_current_board()), **result)
        finally:
            conn.close()
    except ValueError as exc:
        return _err("invalid_argument", str(exc))
    except sqlite3.IntegrityError as exc:
        return _err("sqlite_error", str(exc))
    except Exception as exc:
        logger.exception("kanban_import_openspec_tasks failed")
        return _err("kanban_error", str(exc))


def _tool_handlers(allow_write: bool = False) -> dict[str, Callable[..., dict[str, Any]]]:
    handlers: dict[str, Callable[..., dict[str, Any]]] = {
        "kanban_board_status": kanban_board_status,
        "kanban_list_tasks": kanban_list_tasks,
    }
    if allow_write:
        handlers.update(
            {
                "kanban_enqueue": kanban_enqueue,
                "kanban_claim_next": kanban_claim_next,
                "kanban_heartbeat": kanban_heartbeat,
                "kanban_complete": kanban_complete,
                "kanban_block": kanban_block,
                "kanban_add_dependency": kanban_add_dependency,
                "kanban_reclaim": kanban_reclaim,
                "kanban_import_openspec_tasks": kanban_import_openspec_tasks,
            }
        )
    return handlers


def _tool_names_for_mode(allow_write: bool = False) -> tuple[str, ...]:
    return tuple(_tool_handlers(allow_write).keys())


def _build_server(*, allow_write: bool = False) -> Any:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - install hint
        raise ImportError(
            f"hermes-kanban MCP server requires the 'mcp' package: {exc}"
        ) from exc

    mcp = FastMCP(
        "hermes-kanban",
        instructions=(
            "Narrow Hermes Kanban task adapter. Default mode exposes only "
            "read-only board status and task metadata listing. Write tools "
            "are registered only when the server starts with --allow-write."
        ),
    )
    for name, handler in _tool_handlers(allow_write).items():
        mcp.add_tool(handler, name=name, description=(handler.__doc__ or name).strip())
    logger.info(
        "hermes-kanban MCP server registered %d tools (allow_write=%s)",
        len(_tool_handlers(allow_write)),
        allow_write,
    )
    return mcp


@contextlib.asynccontextmanager
async def _stdio_transport():
    """Compatibility stdio transport for the installed MCP SDK.

    The SDK version used by Hermes exposes ``FastMCP.run_stdio_async()``, but
    its stdin wrapper can stall on subprocess pipes in this environment.  Keep
    FastMCP's server/session handling and replace only the line-oriented stdio
    read/write loops.
    """
    try:
        import anyio
        from mcp import types
        from mcp.shared.message import SessionMessage
    except ImportError as exc:  # pragma: no cover - checked before run
        raise ImportError(
            f"hermes-kanban MCP server requires the 'mcp' package: {exc}"
        ) from exc

    read_writer, read_stream = anyio.create_memory_object_stream(0)
    write_stream, write_reader = anyio.create_memory_object_stream(0)

    async def stdin_reader() -> None:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes | Exception | None] = asyncio.Queue()
        stdin_fd = sys.stdin.fileno()
        was_blocking = os.get_blocking(stdin_fd)
        os.set_blocking(stdin_fd, False)
        reader_removed = False

        def remove_stdin_reader() -> None:
            nonlocal reader_removed
            if not reader_removed:
                reader_removed = True
                with contextlib.suppress(Exception):
                    loop.remove_reader(stdin_fd)

        def on_stdin_ready() -> None:
            try:
                chunk = os.read(stdin_fd, 65536)
            except BlockingIOError:
                return
            except OSError as exc:
                remove_stdin_reader()
                queue.put_nowait(exc)
                return
            if not chunk:
                remove_stdin_reader()
                queue.put_nowait(None)
                return
            queue.put_nowait(chunk)

        try:
            loop.add_reader(stdin_fd, on_stdin_ready)
            async with read_writer:
                buffer = bytearray()
                while True:
                    chunk = await queue.get()
                    if chunk is None:
                        if buffer:
                            try:
                                message = types.JSONRPCMessage.model_validate_json(
                                    bytes(buffer)
                                )
                            except Exception as exc:  # pragma: no cover - malformed client input
                                await read_writer.send(exc)
                            else:
                                await read_writer.send(SessionMessage(message))
                        break
                    if isinstance(chunk, Exception):
                        await read_writer.send(chunk)
                        break
                    buffer.extend(chunk)
                    while True:
                        try:
                            newline_index = buffer.index(b"\n")
                        except ValueError:
                            break
                        line = bytes(buffer[:newline_index])
                        del buffer[: newline_index + 1]
                        try:
                            message = types.JSONRPCMessage.model_validate_json(line)
                        except Exception as exc:  # pragma: no cover - malformed client input
                            await read_writer.send(exc)
                            continue
                        await read_writer.send(SessionMessage(message))
        except anyio.ClosedResourceError:  # pragma: no cover
            await anyio.lowlevel.checkpoint()
        finally:
            remove_stdin_reader()
            with contextlib.suppress(OSError):
                os.set_blocking(stdin_fd, was_blocking)

    async def stdout_writer() -> None:
        try:
            async with write_reader:
                async for session_message in write_reader:
                    data = session_message.message.model_dump_json(
                        by_alias=True,
                        exclude_none=True,
                    )
                    sys.stdout.write(data + "\n")
                    sys.stdout.flush()
        except anyio.ClosedResourceError:  # pragma: no cover
            await anyio.lowlevel.checkpoint()

    async with anyio.create_task_group() as tg:
        tg.start_soon(stdin_reader)
        tg.start_soon(stdout_writer)
        yield read_stream, write_stream


async def _run_stdio(server: Any) -> None:
    async with _stdio_transport() as (read_stream, write_stream):
        await server._mcp_server.run(
            read_stream,
            write_stream,
            server._mcp_server.create_initialization_options(),
        )


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    allow_write = "--allow-write" in argv
    verbose = "--verbose" in argv or "-v" in argv

    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        stream=sys.stderr,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    os.environ.setdefault("HERMES_QUIET", "1")
    os.environ.setdefault("HERMES_REDACT_SECRETS", "true")

    try:
        server = _build_server(allow_write=allow_write)
    except ImportError as exc:
        sys.stderr.write(f"hermes-kanban MCP server cannot start: {exc}\n")
        return 2

    try:
        import anyio

        anyio.run(_run_stdio, server)
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        logger.exception("hermes-kanban MCP server crashed")
        sys.stderr.write(f"hermes-kanban MCP server error: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
