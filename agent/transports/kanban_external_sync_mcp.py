"""Shared MCP boundary for guarded Kanban external-task synchronization."""

from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

KANBAN_EXTERNAL_SYNC_TOOL = "kanban_sync_external_task"


def kanban_sync_external_task(
    *,
    external_key: str,
    source_path: str,
    title: str,
    assignee: str,
    desired_status: str,
    dry_run: bool,
    task_id: Optional[str] = None,
    expected_current_status: Optional[str] = None,
) -> str:
    """Synchronize one Kanban task by exact external identity."""
    if not dry_run and not str(expected_current_status or "").strip():
        return json.dumps(
            {
                "error": "expected_current_status is required when dry_run is false",
                "tool": KANBAN_EXTERNAL_SYNC_TOOL,
            }
        )

    from hermes_cli import kanban_db as kb

    try:
        with kb.connect_closing() as conn:
            result = kb.sync_external_task(
                conn,
                external_key=external_key,
                source_path=source_path,
                title=title,
                assignee=assignee,
                desired_status=desired_status,
                task_id=task_id,
                dry_run=dry_run,
                expected_current_status=expected_current_status,
            )
        return json.dumps(result.as_dict())
    except Exception as exc:
        logger.exception("tool %s raised", KANBAN_EXTERNAL_SYNC_TOOL)
        return json.dumps({"error": str(exc), "tool": KANBAN_EXTERNAL_SYNC_TOOL})
