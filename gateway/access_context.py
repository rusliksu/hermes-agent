"""Stage-1 access-context seam for the Gurra fork.

Only the slice the typed path guard needs: a frozen per-turn context naming the
owning profile and role, a ContextVar binding, and a profile-home resolver.
The full access registry, capabilities enforcement and routing identity land in
later stages (0001/0002) - keep this module free of gateway runtime imports so
the guard stays usable from tools and the agent loop.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

_ROLES = frozenset({"owner", "member"})


@dataclass(frozen=True)
class ResolvedAccessContext:
    """Who is acting: the owning profile and the role inside it."""

    profile_id: str
    role_id: str
    capabilities: frozenset[str] = frozenset()


_CURRENT: ContextVar[Optional[ResolvedAccessContext]] = ContextVar(
    "hermes_resolved_access_context", default=None
)


def get_resolved_access_context() -> Optional[ResolvedAccessContext]:
    return _CURRENT.get()


@contextmanager
def bind_resolved_access_context(context: Optional[ResolvedAccessContext]) -> Iterator[None]:
    token = _CURRENT.set(context)
    try:
        yield
    finally:
        _CURRENT.reset(token)


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
