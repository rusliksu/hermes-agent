"""Durable authority snapshot carried by process watcher/completion events."""
from __future__ import annotations


def process_event_metadata(session=None) -> dict:
    """The bound access context at producer time, serialized for the consumer to revalidate."""
    from gateway.access_registry import serialize_resolved_access_context

    import copy

    context = getattr(session, "resolved_access_context", None) if session is not None else None
    if context is None:
        from gateway.access_context import get_resolved_access_context

        context = get_resolved_access_context()
    if context is None:
        return {}
    payload = copy.deepcopy(context) if isinstance(context, dict) else serialize_resolved_access_context(context)
    return {
        "resolved_access_context": payload,
        "parent_session_id": getattr(session, "parent_session_id", "") or "",
    }
