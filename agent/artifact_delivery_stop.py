"""Bounded turn-end guard for generated family/shared documents."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from agent.tool_result_classification import file_mutation_result_landed
from tools import artifact_delivery_tool
from tools.artifact_delivery_tool import (
    bound_document_context_active,
    is_outbound_document_path,
)


_MUTATION_TOOLS = frozenset({"write_file", "patch"})
_TERMINAL_DOCUMENT_SUFFIX = re.compile(
    r"\.(?:xlsx?|csv|docx?|pdf|od[st]|pptx?|zip)\b",
    re.IGNORECASE,
)
_TERMINAL_DOCUMENT_PATH = re.compile(
    r"(?P<path>(?:/|[A-Za-z]:[\\/])[^\s\"'`<>]+"
    r"\.(?:xlsx?|csv|docx?|pdf|od[st]|pptx?|zip))",
    re.IGNORECASE,
)


def _terminal_document_paths(
    message: dict[str, Any],
    payload: dict[str, Any] | None,
) -> tuple[str, ...]:
    """Extract successful absolute document paths printed by ``terminal``."""
    if payload is None or payload.get("exit_code") != 0 or payload.get("error"):
        return ()
    candidates = [payload.get("output")]
    raw_content = message.get("content")
    if isinstance(raw_content, str):
        candidates.append(raw_content)
    paths: list[str] = []
    for value in candidates:
        if not isinstance(value, str):
            continue
        for match in _TERMINAL_DOCUMENT_PATH.finditer(value):
            path = match.group("path").rstrip(".,;:)]}")
            if path not in paths:
                paths.append(path)
    return tuple(paths)


def _terminal_document_activity(
    message: dict[str, Any],
    payload: dict[str, Any] | None,
) -> bool:
    """Return whether a terminal result shows a generated document path.

    Terminal is intentionally not treated as a generic file mutation: most
    terminal calls in a shared chat are unrelated reads or code tasks. A
    document suffix in the command output is the narrow signal emitted by
    common generators (for example ``Saved: ...xlsx``), and is enough to
    route the turn through the existing one-shot publication correction.
    """
    candidates: list[Any] = []
    if payload is not None:
        candidates.extend(payload.get(key) for key in ("output", "error"))
    raw_content = message.get("content")
    if isinstance(raw_content, str):
        candidates.append(raw_content)
    return any(
        isinstance(value, str) and _TERMINAL_DOCUMENT_SUFFIX.search(value)
        for value in candidates
    )


def bound_artifact_tool_batch_relevant(tool_calls: Iterable[Any]) -> bool:
    """Return whether a bound tool batch can create or publish a document."""
    if not bound_document_context_active():
        return False
    from agent.tool_dispatch_helpers import _extract_file_mutation_targets

    for tool_call in tool_calls:
        function = getattr(tool_call, "function", None)
        tool_name = getattr(function, "name", None)
        if tool_name == "deliver_artifact":
            return True
        if tool_name not in _MUTATION_TOOLS:
            continue
        try:
            arguments = json.loads(getattr(function, "arguments", "") or "{}")
        except (TypeError, ValueError):
            return True
        if not isinstance(arguments, dict):
            return True
        targets = _extract_file_mutation_targets(tool_name, arguments)
        if not targets:
            return True
        if any(is_outbound_document_path(path) for path in targets):
            return True
    return False


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
    successful_terminal_sequences: list[int] = []
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
        elif tool_name == "terminal":
            relevant_activity = relevant_activity or _terminal_document_activity(message, payload)
            if (
                payload is not None
                and payload.get("exit_code") == 0
                and not payload.get("error")
            ):
                successful_terminal_sequences.append(sequence)
            for raw_path in _terminal_document_paths(message, payload):
                validated = _validated_document_path(raw_path)
                if validated is not None:
                    relevant_activity = True
                    latest_mutation[validated] = sequence

    qualifying = [
        confirmation
        for sequence, confirmation in deliveries
        if latest_mutation.get(confirmation["path"], sequence) < sequence
        or (
            attempts >= 1
            and any(terminal_sequence < sequence for terminal_sequence in successful_terminal_sequences)
        )
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


__all__ = ["bound_artifact_stop_action", "bound_artifact_tool_batch_relevant"]
