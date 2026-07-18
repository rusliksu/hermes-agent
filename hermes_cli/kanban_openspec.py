"""Import the minimal OpenSpec ``tasks.md`` checklist into Hermes Kanban.

Supported source format is intentionally narrow:

    - [ ] 1.1 Task title
    - [x] 1.2 Completed-in-plan title

The task id is the first non-space token after the checkbox and remains a
string. Remaining text is the title. Normal Markdown indentation is accepted;
blank and non-task lines are ignored. The importer never writes back to
OpenSpec and never deletes Kanban rows that disappear from a later import.
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from hermes_cli import kanban_db as kb


_TASK_LINE_RE = re.compile(r"^\s*[-*+]\s+\[([ xX])\]\s+(\S+)(?:\s+(.*?))?\s*$")


@dataclass(frozen=True)
class OpenSpecTask:
    task_id: str
    title: str
    checked: bool


def parse_openspec_tasks_md(text: str) -> list[OpenSpecTask]:
    """Parse the minimum supported OpenSpec checkbox task format."""
    tasks: list[OpenSpecTask] = []
    seen: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        match = _TASK_LINE_RE.match(line)
        if match is None:
            continue
        task_id = match.group(2)
        title = (match.group(3) or "").strip()
        if not title:
            raise ValueError(f"OpenSpec task {task_id!r} on line {line_number} has no title")
        if task_id in seen:
            raise ValueError(f"duplicate OpenSpec task id {task_id!r} on line {line_number}")
        if "::" in task_id:
            raise ValueError(f"OpenSpec task id {task_id!r} must not contain '::'")
        seen.add(task_id)
        tasks.append(
            OpenSpecTask(
                task_id=task_id,
                title=title,
                checked=match.group(1).lower() == "x",
            )
        )
    return tasks


def import_openspec_tasks_md(
    conn: sqlite3.Connection,
    source_path: str | Path,
    *,
    repo: Optional[str] = None,
) -> dict[str, Any]:
    """Atomically upsert OpenSpec tasks into Kanban by external_key.

    Stable identity is ``<repo>::<change-slug>::<task-id>``. If ``repo`` is
    omitted, it defaults to the directory name that owns ``openspec/``.
    Existing rows update only ``external_key``, ``source_path``, ``title``,
    and ``body``. New imported rows start in fixed status ``todo``.
    """
    path = Path(source_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.is_file():
        raise ValueError(f"source_path does not exist or is not a file: {path}")
    change_slug, default_repo = _source_identity_parts(path)
    repo_name = (repo if repo is not None else default_repo).strip()
    if not repo_name:
        raise ValueError("repo is required")
    if "::" in repo_name or "::" in change_slug:
        raise ValueError("repo and change slug must not contain '::'")

    source_path_text = str(path)
    parsed = parse_openspec_tasks_md(path.read_text(encoding="utf-8"))
    created = 0
    updated = 0
    unchanged = 0
    task_results: list[dict[str, Any]] = []
    imported_keys: set[str] = set()
    prefix = f"{repo_name}::{change_slug}::"
    now = int(time.time())

    with kb.write_txn(conn):
        for item in parsed:
            external_key = f"{prefix}{item.task_id}"
            imported_keys.add(external_key)
            body = _task_body(item)
            row = conn.execute(
                """
                SELECT id, title, body, source_path
                  FROM tasks
                 WHERE external_key = ?
                """,
                (external_key,),
            ).fetchone()
            if row is None:
                task_id = _insert_imported_task(
                    conn,
                    external_key=external_key,
                    source_path=source_path_text,
                    title=item.title,
                    body=body,
                    created_at=now,
                )
                created += 1
                action = "created"
            else:
                task_id = str(row["id"])
                if (
                    row["title"] != item.title
                    or row["body"] != body
                    or row["source_path"] != source_path_text
                ):
                    conn.execute(
                        """
                        UPDATE tasks
                           SET external_key = ?,
                               source_path = ?,
                               title = ?,
                               body = ?
                         WHERE id = ?
                        """,
                        (external_key, source_path_text, item.title, body, task_id),
                    )
                    updated += 1
                    action = "updated"
                else:
                    unchanged += 1
                    action = "unchanged"
            task_results.append(
                {
                    "id": task_id,
                    "external_key": external_key,
                    "source_task_id": item.task_id,
                    "title": item.title,
                    "checked": item.checked,
                    "action": action,
                }
            )

        rows = conn.execute(
            """
            SELECT id, external_key, title, status
              FROM tasks
             WHERE substr(external_key, 1, ?) = ?
             ORDER BY external_key ASC
            """,
            (len(prefix), prefix),
        ).fetchall()
        missing = [
            dict(row)
            for row in rows
            if row["external_key"] not in imported_keys
        ]

    return {
        "source_path": source_path_text,
        "repo": repo_name,
        "change_slug": change_slug,
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "missing": missing,
        "tasks": task_results,
    }


def _source_identity_parts(path: Path) -> tuple[str, str]:
    parts = path.parts
    for idx, part in enumerate(parts[:-3]):
        if part == "openspec" and parts[idx + 1] == "changes" and parts[idx + 3] == "tasks.md":
            change_slug = parts[idx + 2]
            repo_root = Path(*parts[:idx]) if idx else Path(".")
            return change_slug, repo_root.name
    raise ValueError("source_path must match openspec/changes/<change-slug>/tasks.md")


def _task_body(task: OpenSpecTask) -> str:
    return f"OpenSpec задача {task.task_id}\n\n{task.title}"


def _insert_imported_task(
    conn: sqlite3.Connection,
    *,
    external_key: str,
    source_path: str,
    title: str,
    body: str,
    created_at: int,
) -> str:
    for attempt in range(2):
        task_id = kb._new_task_id()
        try:
            conn.execute(
                """
                INSERT INTO tasks (
                    id, title, body, status, priority, created_by, created_at,
                    workspace_kind, external_key, source_path
                ) VALUES (?, ?, ?, 'todo', 0, 'openspec', ?, 'scratch', ?, ?)
                """,
                (task_id, title, body, created_at, external_key, source_path),
            )
            return task_id
        except sqlite3.IntegrityError:
            if attempt == 1:
                raise
    raise RuntimeError("unreachable")
