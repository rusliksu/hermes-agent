"""Stage-1 access-context seam for the Gurra fork.

Covers the slice the typed path guard and the bound-document pipeline need: a
frozen per-turn context (profile, role, capabilities, delivery target), a
ContextVar binding with the target, and profile-home helpers. The full access
registry, routing identity and capability enforcement land in a later stage -
keep this module free of gateway runtime imports.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

_ROLES = frozenset({"owner", "member", "family", "shared_room"})


@dataclass(frozen=True)
class DeliveryTarget:
    """Server-bound destination; the model can never choose it."""

    platform: str
    chat_id: str
    thread_id: Optional[str] = None


@dataclass(frozen=True)
class ResolvedAccessContext:
    """Who is acting: the owning profile, the role inside it, and the bound target."""

    profile_id: str
    role_id: str
    capabilities: frozenset[str] = frozenset()
    delivery_target: Optional[DeliveryTarget] = None


_CURRENT: ContextVar[Optional[ResolvedAccessContext]] = ContextVar(
    "hermes_resolved_access_context", default=None
)
_TARGET: ContextVar[Optional[DeliveryTarget]] = ContextVar(
    "hermes_current_delivery_target", default=None
)


def get_resolved_access_context(default=None):
    value = _CURRENT.get()
    return default if value is None else value


def get_current_delivery_target(default=None):
    value = _TARGET.get()
    return default if value is None else value


@contextmanager
def bind_resolved_access_context(context, target=None) -> Iterator[None]:
    context_token = _CURRENT.set(context)
    explicit = target if target is not None else getattr(context, "delivery_target", None)
    target_token = _TARGET.set(explicit)
    try:
        yield
    finally:
        _TARGET.reset(target_token)
        _CURRENT.reset(context_token)


def _serialize_target(target: Optional[DeliveryTarget]) -> Optional[dict[str, object]]:
    if target is None:
        return None
    if not isinstance(target, DeliveryTarget):
        raise ValueError("delivery target must be a DeliveryTarget")
    if not isinstance(target.platform, str) or not target.platform:
        raise ValueError("delivery target has no platform")
    if not isinstance(target.chat_id, str) or not target.chat_id:
        raise ValueError("delivery target has no chat id")
    if target.thread_id is not None and not isinstance(target.thread_id, str):
        raise ValueError("delivery target thread id must be a string")
    return {"platform": target.platform, "chat_id": target.chat_id, "thread_id": target.thread_id}


def serialize_resolved_access_context(context: ResolvedAccessContext) -> dict[str, object]:
    """Validate the shape every consumer relies on; raise on a malformed context."""
    if not isinstance(context, ResolvedAccessContext):
        raise ValueError("resolved access context must be a ResolvedAccessContext")
    if not isinstance(context.profile_id, str) or not context.profile_id:
        raise ValueError("resolved access context has no profile")
    if context.role_id not in _ROLES:
        raise ValueError("resolved access context has an unknown role")
    return {
        "profile_id": context.profile_id,
        "role_id": context.role_id,
        "capabilities": sorted(str(name) for name in context.capabilities),
        "delivery_target": _serialize_target(context.delivery_target),
    }


def deserialize_resolved_access_context(data) -> ResolvedAccessContext:
    if not isinstance(data, dict):
        raise ValueError("resolved access context must be a mapping")
    if set(data) != {"profile_id", "role_id", "capabilities", "delivery_target"}:
        raise ValueError("resolved access context has an unexpected shape")
    raw_target = data["delivery_target"]
    target = None
    if raw_target is not None:
        if not isinstance(raw_target, dict) or set(raw_target) != {"platform", "chat_id", "thread_id"}:
            raise ValueError("delivery target has an unexpected shape")
        target = DeliveryTarget(
            platform=str(raw_target["platform"]),
            chat_id=str(raw_target["chat_id"]),
            thread_id=raw_target["thread_id"],
        )
    context = ResolvedAccessContext(
        profile_id=str(data["profile_id"]),
        role_id=str(data["role_id"]),
        capabilities=frozenset(str(name) for name in data["capabilities"]),
        delivery_target=target,
    )
    if _serialize_target(context.delivery_target) != _serialize_target(target):
        raise ValueError("delivery target did not round-trip")
    return context


def session_scope_from_resolved_access_context(context: ResolvedAccessContext) -> dict[str, object]:
    """Neutral store scope derived from the context (profile + bound target)."""
    serialize_resolved_access_context(context)
    return {
        "profile_id": context.profile_id,
        "role_id": context.role_id,
        "delivery_target": _serialize_target(context.delivery_target),
    }


def home_root() -> Path:
    """Root that owns the profiles directory, independent of the profile currently bound."""
    from hermes_constants import get_hermes_home

    active = Path(get_hermes_home()).resolve()
    if active.parent.name == "profiles":
        return active.parent.parent
    return active


def profile_home(context: ResolvedAccessContext) -> Path:
    """Home of the context profile, resolved without reading the bound scope."""
    root = home_root()
    if context.profile_id == "default":
        return root
    return root / "profiles" / context.profile_id
