"""Per-turn access context binding: registry contract types + ContextVars.

Types, codecs and scope helpers come from gateway.access_registry (the single
contract); this module only binds them per turn and resolves profile homes.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator, Optional

from gateway.access_registry import (
    DeliveryTarget,
    ResolvedAccessContext,
    deserialize_resolved_access_context,
    serialize_resolved_access_context,
    session_scope_from_resolved_access_context,
)

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
