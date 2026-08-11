"""Explicit, fail-closed publication of a bound outbound artifact."""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any

from agent.runtime_cwd import bound_profile_home, resolve_bound_profile_cwd
from gateway.access_registry import (
    DeliveryTarget,
    ResolvedAccessContext,
    deserialize_resolved_access_context,
    serialize_resolved_access_context,
)
from gateway.session_context import (
    get_current_delivery_target,
    get_resolved_access_context,
)
from tools.registry import registry


_TOOL_NAME = "deliver_artifact"
_REQUIRED_ARGUMENTS = frozenset({"path"})
_DOCUMENT_ROLES = frozenset({"family", "shared_room"})
_ALLOWED_ROLES = _DOCUMENT_ROLES | {"owner"}
_MEDIA_MIME_PREFIXES = ("image/", "audio/", "video/")


def is_outbound_document_path(raw_path: str) -> bool:
    """Return whether ``raw_path`` names a gateway-deliverable non-media file."""
    if not isinstance(raw_path, str) or not raw_path.strip() or "\x00" in raw_path:
        return False
    try:
        from gateway.platforms.base import MEDIA_DELIVERY_EXTS

        suffix = Path(raw_path.strip()).suffix.lower()
        if suffix not in MEDIA_DELIVERY_EXTS:
            return False
    except Exception:
        return False
    mime_type, _ = mimetypes.guess_type(raw_path)
    return not (mime_type and mime_type.startswith(_MEDIA_MIME_PREFIXES))


def _result(
    *,
    success: bool,
    error: str | None = None,
    artifact: Path | None = None,
) -> str:
    payload: dict[str, Any] = {
        "success": success,
        "status": "ready_for_delivery" if success else "failed",
    }
    if error is not None:
        payload["error"] = error
    if artifact is not None:
        payload["file_name"] = artifact.name
        payload["media_tag"] = f"MEDIA:{artifact}"
    return json.dumps(payload, ensure_ascii=False)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _strict_context() -> tuple[ResolvedAccessContext | None, str | None]:
    context = get_resolved_access_context(None)
    if context is None:
        return None, "missing_context"
    if not isinstance(context, ResolvedAccessContext):
        return None, "malformed_context"
    try:
        canonical = deserialize_resolved_access_context(
            serialize_resolved_access_context(context)
        )
    except (TypeError, ValueError):
        return None, "malformed_context"
    if canonical.role_id not in _ALLOWED_ROLES:
        return None, "malformed_context"

    current_target = get_current_delivery_target(None)
    if not isinstance(current_target, DeliveryTarget) or current_target != canonical.delivery_target:
        return None, "context_target_mismatch"
    return canonical, None


def _bound_roots(task_id: str) -> tuple[Path, ...]:
    profile_home = bound_profile_home()
    configured_workspace = resolve_bound_profile_cwd()
    if profile_home is None or configured_workspace is None:
        raise ValueError("bound roots unavailable")

    roots = [profile_home.resolve(strict=True), configured_workspace.resolve(strict=True)]
    runtime_candidates: list[Any] = []
    try:
        from tools.terminal_tool import get_session_cwd, resolve_task_overrides

        runtime_candidates.append(get_session_cwd(task_id))
        runtime_candidates.append(resolve_task_overrides(task_id).get("cwd"))
    except Exception:
        pass
    for candidate in runtime_candidates:
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        # Revalidate every runtime candidate against the typed profile config;
        # never use process cwd, HOME, or TERMINAL_CWD as a fallback.
        resolved = resolve_bound_profile_cwd(candidate.strip())
        if resolved is not None:
            roots.append(resolved.resolve(strict=True))
    return tuple(dict.fromkeys(roots))


def _resolve_artifact(raw_path: str, task_id: str) -> tuple[Path | None, str | None]:
    try:
        roots = _bound_roots(task_id)
    except (OSError, RuntimeError, ValueError):
        return None, "bound_roots_unavailable"

    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = roots[1] / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        return None, "file_not_found"
    except (OSError, RuntimeError, ValueError):
        return None, "invalid_path"

    if not resolved.is_file():
        return None, "not_regular_file"
    if not any(_is_within(resolved, root) for root in roots):
        return None, "path_outside_bound_roots"
    mime_type, _ = mimetypes.guess_type(resolved.name)
    if mime_type and mime_type.startswith(_MEDIA_MIME_PREFIXES):
        return None, "media_type_not_supported"
    return resolved, None


def validate_bound_artifact_output(
    raw_path: str,
    task_id: str,
) -> str | None:
    """Validate an intended generated document path for a bound shared context.

    Existing owner and non-document writes are deliberately outside this guard.
    For family/shared turns, an output path may not escape the same trusted roots
    later enforced by :func:`_resolve_artifact`, including through a symlinked
    parent. The candidate need not exist yet.
    """
    raw_context = get_resolved_access_context(None)
    if (
        not isinstance(raw_context, ResolvedAccessContext)
        or raw_context.role_id not in _DOCUMENT_ROLES
        or not is_outbound_document_path(raw_path)
    ):
        return None
    context, context_error = _strict_context()
    if context_error is not None or context is None:
        return context_error or "malformed_context"
    if "documents" not in context.capabilities:
        return "missing_documents_capability"
    try:
        roots = _bound_roots(task_id)
    except (OSError, RuntimeError, ValueError):
        return "bound_roots_unavailable"

    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = roots[1] / candidate
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return "invalid_path"
    if not any(_is_within(resolved, root) for root in roots):
        return "path_outside_bound_roots"
    return None


def bound_document_context_active() -> bool:
    """Return whether the current trusted turn is family/shared document-bound."""
    context, context_error = _strict_context()
    return bool(
        context_error is None
        and context is not None
        and context.role_id in _DOCUMENT_ROLES
        and "documents" in context.capabilities
    )


def _deliver_artifact(args: dict[str, Any], **kwargs: Any) -> str:
    if not isinstance(args, dict) or frozenset(args) != _REQUIRED_ARGUMENTS:
        return _result(success=False, error="invalid_arguments")
    raw_path = args.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip() or "\x00" in raw_path:
        return _result(success=False, error="invalid_arguments")

    context, context_error = _strict_context()
    if context_error is not None:
        return _result(success=False, error=context_error)
    assert context is not None
    if context.role_id in _DOCUMENT_ROLES and "documents" not in context.capabilities:
        return _result(success=False, error="missing_documents_capability")

    artifact, path_error = _resolve_artifact(
        raw_path.strip(),
        str(kwargs.get("task_id") or "default"),
    )
    if path_error is not None:
        return _result(success=False, error=path_error)
    assert artifact is not None
    return _result(success=True, artifact=artifact)


DELIVER_ARTIFACT_SCHEMA = {
    "name": _TOOL_NAME,
    "description": (
        "Mark one existing non-image document or archive from the current bound "
        "profile/workspace for delivery to this conversation. Pass only the path. "
        "The server-bound destination cannot be selected by the model."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path, or bound-workspace-relative path, to the artifact.",
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    },
}


registry.register(
    name=_TOOL_NAME,
    toolset="file",
    schema=DELIVER_ARTIFACT_SCHEMA,
    handler=_deliver_artifact,
    emoji="📎",
)
