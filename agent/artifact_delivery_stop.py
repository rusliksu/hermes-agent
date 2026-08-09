"""Bounded turn-end guard for generated family/shared documents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from agent.tool_result_classification import file_mutation_result_landed
from tools import artifact_delivery_tool
from tools.artifact_delivery_tool import (
    bound_document_context_active,
    is_outbound_document_path,
)


_MUTATION_TOOLS = frozenset({"write_file", "patch"})


def _tool_result_name(message: dict[str, Any]) -> str | None:
    names = {
        value
        for value in (message.get("tool_name"), message.get("name"))
        if isinstance(value, str) and value
    }
    return next(iter(names)) if len(names) == 1 else None


def _tool_result_payload(message: dict[str, Any]) -> dict[str, Any] | None:
    raw = message.get("content")
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _validated_document_path(raw_path: Any) -> str | None:
    if not isinstance(raw_path, str) or not Path(raw_path).is_absolute():
        return None
    if not is_outbound_document_path(raw_path):
        return None
    if artifact_delivery_tool.validate_bound_artifact_output(raw_path, "default"):
        return None
    return raw_path


def _successful_mutation_paths(
    tool_name: str,
    payload: dict[str, Any] | None,
    raw_result: Any,
) -> tuple[str, ...] | None:
    if payload is None or not file_mutation_result_landed(tool_name, raw_result):
        return None

    raw_paths = payload.get("files_modified")
    if not isinstance(raw_paths, list) or not raw_paths:
        return None
    if any(
        not isinstance(path, str) or not path or not Path(path).is_absolute()
        for path in raw_paths
    ):
        return None
    if len(set(raw_paths)) != len(raw_paths):
        return None

    resolved_path = payload.get("resolved_path")
    if resolved_path is not None and (
        not isinstance(resolved_path, str)
        or len(raw_paths) != 1
        or raw_paths[0] != resolved_path
    ):
        return None

    document_paths: list[str] = []
    for path in raw_paths:
        if not is_outbound_document_path(path):
            continue
        validated = _validated_document_path(path)
        if validated is None:
            return None
        document_paths.append(validated)
    return tuple(document_paths)


def _failed_document_mutation(
    payload: dict[str, Any] | None,
) -> bool | None:
    if payload is None:
        return None
    error = payload.get("error")
    if isinstance(error, str) and error.startswith("bound_artifact_output_rejected:"):
        return True
    resolved_path = payload.get("resolved_path")
    if not isinstance(resolved_path, str) or not Path(resolved_path).is_absolute():
        return None
    if not is_outbound_document_path(resolved_path):
        return False
    return True


def _delivery_confirmation(
    message: dict[str, Any],
    payload: dict[str, Any] | None,
) -> dict[str, str] | None:
    call_id = message.get("tool_call_id") or message.get("call_id")
    if not isinstance(call_id, str) or not call_id or payload is None:
        return None
    if payload.get("success") is not True or payload.get("status") != "ready_for_delivery":
        return None
    media_tag = payload.get("media_tag")
    if not isinstance(media_tag, str) or not media_tag.startswith("MEDIA:"):
        return None
    path = _validated_document_path(media_tag.removeprefix("MEDIA:"))
    if path is None or media_tag != f"MEDIA:{path}":
        return None
    return {
        "tool_call_id": call_id,
        "path": path,
        "media_tag": media_tag,
    }


def bound_artifact_stop_action(
    messages: Iterable[dict[str, Any]],
    *,
    attempts: int,
) -> tuple[str, str | None, dict[str, str] | None]:
    """Return the stop action, optional nudge, and exact delivery provenance."""
    if not bound_document_context_active():
        return "none", None, None

    latest_mutation: dict[str, int] = {}
    deliveries: list[tuple[int, dict[str, str]]] = []
    relevant_activity = False
    for sequence, message in enumerate(messages):
        if not isinstance(message, dict) or message.get("role") not in {"tool", "function"}:
            continue
        tool_name = _tool_result_name(message)
        payload = _tool_result_payload(message)
        if tool_name in _MUTATION_TOOLS:
            paths = _successful_mutation_paths(
                tool_name,
                payload,
                message.get("content"),
            )
            if paths is None:
                failed_document = _failed_document_mutation(payload)
                # Missing/malformed results and pathless failures are
                # intentionally ambiguous and therefore fail closed.
                relevant_activity = relevant_activity or failed_document is not False
            else:
                for path in paths:
                    relevant_activity = True
                    latest_mutation[path] = sequence
        elif tool_name == "deliver_artifact":
            relevant_activity = True
            confirmation = _delivery_confirmation(message, payload)
            if confirmation is not None:
                deliveries.append((sequence, confirmation))

    qualifying = [
        confirmation
        for sequence, confirmation in deliveries
        if latest_mutation.get(confirmation["path"], sequence) < sequence
    ]
    if len(qualifying) == 1:
        return "confirmed", None, qualifying[0]

    if not relevant_activity:
        return "none", None, None

    if attempts < 1:
        return "continue", (
            "[System: The generated outbound document was not safely prepared for "
            "this bound conversation. Make exactly one corrective attempt now: "
            "create a new document inside the current trusted profile/workspace "
            "root, then call deliver_artifact for that new path. Do not copy or "
            "reuse an outside file, do not emit a legacy MEDIA path, and do not "
            "claim success before deliver_artifact succeeds.]"
        ), None
    return "failed", None, None


__all__ = ["bound_artifact_stop_action"]
