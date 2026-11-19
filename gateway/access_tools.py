"""Tool-surface policy for validated Gurra contexts; native availability stays separate."""
from typing import Any

from gateway.access_registry import ResolvedAccessContext


def user_toolset(name: Any) -> bool:
    name = str(name or "").strip().lower()
    return not name.endswith("_admin") and name not in {"global_cron", "host_shell", "owner_admin", "kanban"}


def toolsets_for_context(configured: list[str], context: Any) -> list[str]:
    if context is None:
        return list(configured)
    if not isinstance(context, ResolvedAccessContext):
        return []
    if context.role_id == "owner":
        return list(configured)
    if context.role_id not in {"family", "shared_room"}:
        return []
    return sorted(str(name) for name in configured if user_toolset(name))


def shared_tool_profile(source: Any, configured_toolsets: list[str] | None = None) -> tuple[list[str], frozenset[str]]:
    context = getattr(source, "resolved_access_context", None)
    if context is None:
        return sorted(str(name) for name in (configured_toolsets or ["memory"])), frozenset()
    if not isinstance(context, ResolvedAccessContext) or context.role_id != "shared_room":
        return [], frozenset()
    selected = toolsets_for_context(configured_toolsets or [], context)
    from toolsets import resolve_toolset
    expected = frozenset(tool for name in selected for tool in resolve_toolset(name))
    return selected, expected
