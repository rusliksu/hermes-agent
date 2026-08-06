"""Import the minimal Spec Kitty ``tasks.md`` checklist into Hermes Kanban.

Supported source format is intentionally narrow:

    - [ ] 1.1 Task title
    - [x] 1.2 Completed-in-plan title

The task id is the first non-space token after the checkbox and remains a
string. Remaining text is the title. Normal Markdown indentation is accepted;
blank and non-task lines are ignored. The importer never writes back to
Spec Kitty and never deletes Kanban rows that disappear from a later import.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from hermes_cli import kanban_db as kb


_TASK_LINE_RE = re.compile(r"^\s*[-*+]\s+\[([ xX])\]\s+(\S+)(?:\s+(.*?))?\s*$")


@dataclass(frozen=True)
class SpecKittyTask:
    task_id: str
    title: str
    checked: bool


def parse_spec_kitty_tasks_md(text: str) -> list[SpecKittyTask]:
    """Parse the minimum supported Spec Kitty checkbox task format."""
    tasks: list[SpecKittyTask] = []
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
            raise ValueError(f"Spec Kitty task {task_id!r} on line {line_number} has no title")
        if task_id in seen:
            raise ValueError(f"duplicate Spec Kitty task id {task_id!r} on line {line_number}")
        if "::" in task_id:
            raise ValueError(f"Spec Kitty task id {task_id!r} must not contain '::'")
        seen.add(task_id)
        tasks.append(
            SpecKittyTask(
                task_id=task_id,
                title=title,
                checked=match.group(1).lower() == "x",
            )
        )
    return tasks


def import_spec_kitty_tasks_md(
    conn: sqlite3.Connection,
    source_path: str | Path,
    *,
    repo: Optional[str] = None,
) -> dict[str, Any]:
    """Atomically upsert Spec Kitty tasks into Kanban by external_key.

    Stable identity is ``<repo>::<change-slug>::<task-id>``. If ``repo`` is
    omitted, it defaults to the directory name that owns ``kitty-specs/``.
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
    parsed = parse_spec_kitty_tasks_md(path.read_text(encoding="utf-8"))
    prefix = f"{repo_name}::{change_slug}::"
    definitions = [
        kb.SpecKittyTaskDefinition(
            external_key=f"{prefix}{item.task_id}",
            source_path=source_path_text,
            title=item.title,
            body=_task_body(item),
        )
        for item in parsed
    ]
    batch = kb.upsert_spec_kitty_task_definitions(
        conn,
        definitions,
        external_key_prefix=prefix,
    )
    counts = {"created": 0, "updated": 0, "unchanged": 0}
    task_results: list[dict[str, Any]] = []
    for item, upserted in zip(parsed, batch.items):
        counts[upserted.action] += 1
        task_results.append(
            {
                "id": upserted.task_id,
                "external_key": upserted.external_key,
                "source_task_id": item.task_id,
                "title": item.title,
                "checked": item.checked,
                "action": upserted.action,
            }
        )

    return {
        "source_path": source_path_text,
        "repo": repo_name,
        "change_slug": change_slug,
        **counts,
        "missing": [item.as_dict() for item in batch.missing],
        "tasks": task_results,
    }


def _source_identity_parts(path: Path) -> tuple[str, str]:
    parts = path.parts
    for idx, part in enumerate(parts[:-2]):
        if part == "kitty-specs" and parts[idx + 2] == "tasks.md":
            change_slug = parts[idx + 1]
            repo_root = Path(*parts[:idx]) if idx else Path(".")
            return change_slug, repo_root.name
    raise ValueError("source_path must match kitty-specs/<change-slug>/tasks.md")


def _task_body(task: SpecKittyTask) -> str:
    return f"Spec Kitty задача {task.task_id}\n\n{task.title}"
