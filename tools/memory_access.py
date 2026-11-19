"""Typed curated-memory authorization, separate from native store mechanics."""
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional
from hermes_constants import get_hermes_home

if TYPE_CHECKING:
    from tools.memory_tool_store import MemoryStore

_TELEGRAM_ROOM_PEER_KINDS = frozenset({"group", "supergroup", "channel"})

def get_memory_dir():
    return get_hermes_home() / "memories"

class MemoryAccessDenied(RuntimeError):
    """Categorical memory authorization failure; message is safe for tools."""

    reason = "memory_access_denied"

    def __init__(self) -> None:
        super().__init__(self.reason)

def _strict_access_context(context: Any) -> Any:
    try:
        from gateway.access_registry import (
            ResolvedAccessContext,
            deserialize_resolved_access_context,
            serialize_resolved_access_context,
        )

        if not isinstance(context, ResolvedAccessContext):
            raise MemoryAccessDenied()
        return deserialize_resolved_access_context(
            serialize_resolved_access_context(context)
        )
    except MemoryAccessDenied:
        raise
    except Exception as exc:
        raise MemoryAccessDenied() from exc

def _strict_current_and_bound_context(store: "MemoryStore") -> tuple[Any, Any]:
    try:
        from gateway.access_context import get_resolved_access_context

        current = get_resolved_access_context(None)
    except Exception as exc:
        raise MemoryAccessDenied() from exc

    bound = getattr(store, "access_context", None)
    if current is None and bound is None:
        # No typed authority on either side is the legacy path (including multiplexing
        # without a configured access registry); access enforcement happens at ingress.
        if getattr(store, "require_access_context", False):
            raise MemoryAccessDenied()
        return None, None
    if current is None or bound is None:
        raise MemoryAccessDenied()

    current = _strict_access_context(current)
    bound = _strict_access_context(bound)
    if bound != current:
        raise MemoryAccessDenied()
    return current, bound

def _reject_symlink_components(profile_home: Path, expected_path: Path) -> None:
    try:
        relative = expected_path.relative_to(profile_home)
    except ValueError as exc:
        raise MemoryAccessDenied() from exc
    cursor = profile_home
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise MemoryAccessDenied()

def _require_exact_memory_path(memory_dir: Path, expected_path: Path) -> None:
    profile_home = get_hermes_home()
    if memory_dir != expected_path:
        raise MemoryAccessDenied()
    _reject_symlink_components(profile_home, expected_path)
    try:
        expected_path.resolve(strict=False).relative_to(profile_home.resolve(strict=False))
    except (OSError, ValueError) as exc:
        raise MemoryAccessDenied() from exc

def _typed_non_owner_context(store: "MemoryStore") -> bool:
    try:
        current, _bound = _strict_current_and_bound_context(store)
    except MemoryAccessDenied:
        return False
    return current is not None and current.role_id != "owner"

def _pending_scope_key_for_store(store: "MemoryStore") -> Optional[str]:
    current, _bound = _strict_current_and_bound_context(store)
    if current is None:
        return None
    return _current_pending_scope_key()

def _current_pending_scope_key() -> Optional[str]:
    """Server-derived pending authority; also verify the active profile home."""
    try:
        from gateway.access_context import get_resolved_access_context
        from gateway.access_registry import canonical_access_context_fingerprint
        from agent.access_paths import bound_profile_home
        current = get_resolved_access_context()
        if current is None:
            return None
        current = _strict_access_context(current)
        bound_profile_home()
        return canonical_access_context_fingerprint(current)
    except Exception as exc:
        raise MemoryAccessDenied() from exc

def _require_memory_access(store: "MemoryStore", target: Optional[str] = None) -> None:
    """Authorize a store against the current task-local access context.

    No task-local context and no bound store context is the legacy path and is
    intentionally allowed. Once either side is typed, both must exist, pass the
    strict six-field codec, and match exactly.
    """
    current, _bound = _strict_current_and_bound_context(store)
    if current is None:
        return

    delivery_target = getattr(current, "delivery_target", None)
    platform = getattr(delivery_target, "platform", None)
    peer_kind = getattr(delivery_target, "peer_kind", None)
    memory_dir = Path(getattr(store, "memory_dir", get_memory_dir()))
    profile_memories = get_hermes_home() / "memories"

    role_id = current.role_id
    capabilities = current.capabilities
    if role_id == "owner":
        if (
            platform != "telegram"
            or peer_kind != "dm"
        ):
            raise MemoryAccessDenied()
        _require_exact_memory_path(memory_dir, profile_memories)
        return

    if role_id == "family":
        if (
            platform != "telegram"
            or peer_kind != "dm"
            or "memory_search" not in capabilities
        ):
            raise MemoryAccessDenied()
        _require_exact_memory_path(memory_dir, profile_memories)
        return

    if role_id == "shared_room":
        try:
            from gateway.access_registry import shared_memory_namespace_for_access_context

            namespace = shared_memory_namespace_for_access_context(current)
        except ValueError as exc:
            raise MemoryAccessDenied() from exc
        expected_shared_dir = profile_memories / "shared" / namespace
        if (
            platform != "telegram"
            or peer_kind not in _TELEGRAM_ROOM_PEER_KINDS
            or "room_memory" not in capabilities
            or target == "user"
            or getattr(store, "allow_user_profile", True)
        ):
            raise MemoryAccessDenied()
        _require_exact_memory_path(memory_dir, expected_shared_dir)
        return

    raise MemoryAccessDenied()
