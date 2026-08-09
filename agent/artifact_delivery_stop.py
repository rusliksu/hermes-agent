"""Bounded turn-end guard for generated family/shared documents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from agent.tool_dispatch_helpers import _extract_file_mutation_targets
from tools.artifact_delivery_tool import (
    bound_document_context_active,
    is_outbound_document_path,
)


def _call_name(call: dict[str, Any]) -> str:
    function = call.get("function")
    if isinstance(function, dict):
        return str(function.get("name") or "")
    return str(call.get("name") or "")


def _call_args(call: dict[str, Any]) -> dict[str, Any]:
    function = call.get("function")
    raw = function.get("arguments") if isinstance(function, dict) else call.get("arguments")
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def bound_artifact_stop_action(
    messages: Iterable[dict[str, Any]],
    *,
    attempts: int,
) -> tuple[str, str | None, dict[str, str] | None]:
    """Return the stop action, optional nudge, and exact delivery provenance."""
    if not bound_document_context_active():
        return "none", None, None

    relevant_write = False
    deliver_call_ids: set[str] = set()
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            name = _call_name(call)
            if name in {"write_file", "patch"}:
                relevant_write = relevant_write or any(
                    is_outbound_document_path(path)
                    for path in _extract_file_mutation_targets(
                        name, _call_args(call)
                    )
                )
            elif name == "deliver_artifact":
                call_id = call.get("id") or call.get("call_id")
                if call_id:
                    deliver_call_ids.add(str(call_id))

    if not relevant_write:
        return "none", None, None

    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"tool", "function"}:
            continue
        call_id = str(message.get("tool_call_id") or message.get("call_id") or "")
        if call_id not in deliver_call_ids:
            continue
        try:
            payload = json.loads(str(message.get("content") or ""))
        except (TypeError, ValueError):
            continue
        if (
            isinstance(payload, dict)
            and payload.get("success") is True
            and payload.get("status") == "ready_for_delivery"
            and isinstance(payload.get("media_tag"), str)
        ):
            media_tag = payload["media_tag"]
            path = media_tag.removeprefix("MEDIA:")
            if (
                media_tag == f"MEDIA:{path}"
                and Path(path).is_absolute()
                and is_outbound_document_path(path)
            ):
                return "confirmed", None, {
                    "tool_call_id": call_id,
                    "path": path,
                    "media_tag": media_tag,
                }

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
