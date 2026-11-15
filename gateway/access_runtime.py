"""Registry authority inside the native gateway routing identity."""
from typing import Any
from contextlib import contextmanager, nullcontext

from gateway.access_registry import (AccessDeniedError, RedactedAuditMetadata,
                                    TransportIdentity, serialize_resolved_access_context)


def has_access_registry(runner: Any) -> bool:
    """A configured registry owns every user ingress, including revoked bindings."""
    return getattr(runner, "access_registry", None) is not None


def deny(reason: str):
    raise AccessDeniedError(reason, RedactedAuditMetadata(event="native_identity"))


def resolve_context(source: Any, *, runner: Any, adapter: Any = None, pinned: Any = None):
    registry = getattr(runner, "access_registry", None)
    if not has_access_registry(runner):
        return None
    if adapter is None:
        deny("missing_transport_identity")
    extra = getattr(getattr(adapter, "config", None), "extra", {}) or {}
    account = extra.get("account")
    if not isinstance(account, str) or not account.strip():
        deny("missing_transport_account")
    account = account.strip()
    identity = TransportIdentity.from_session_source(source, account=account)
    context = registry.resolve(identity)
    context = registry.validate_resolved_context_for_identity(context, identity)
    for previous in (pinned, getattr(source, "resolved_access_context", None)):
        if previous is not None and serialize_resolved_access_context(previous) != serialize_resolved_access_context(context):
            deny("resolved_access_context_mismatch")
    profile = (getattr(source, "profile", None) or "").strip()
    if profile and profile != context.profile_id:
        deny("profile_route_mismatch")
    source.route_account = account
    return context


def profile_home(context: Any):
    from hermes_cli.profiles import get_profile_dir, profile_exists
    if not profile_exists(context.profile_id):
        deny("missing_profile")
    return get_profile_dir(context.profile_id)


@contextmanager
def turn_access_scope(source: Any, *, runner: Any):
    from gateway.session_identity import canonical_identity, IdentityUnresolved
    from gateway.access_context import bind_resolved_access_context
    registered = has_access_registry(runner)
    identity = canonical_identity(source, runner=runner) if registered else None
    if registered and (identity is None or identity.access_context is None):
        raise IdentityUnresolved("registry_access_denied")
    context = identity.access_context if identity is not None else None
    with bind_resolved_access_context(context):
        yield context


_SESSION_VERBS_BY_ROLE = {
    "owner": frozenset({"session:submit", "session:interrupt", "session:cancel",
                        "session:mutate", "session:respond", "session:operator"}),
    "member": frozenset({"session:submit", "session:interrupt", "session:cancel", "session:respond"}),
    "family": frozenset({"session:submit", "session:interrupt", "session:cancel", "session:respond"}),
    "shared_room": frozenset({"session:submit", "session:respond"}),
}


def session_actor_for_context(context: Any, *, transport_id: str):
    """Bridge the resolved access context onto the 0.23 session actor contract.

    The authority only checks ``actor.subject/profile_id/capabilities``; tool-level
    capabilities ride the actor as ``tool:<name>`` so they never grant a session verb.
    """
    from gateway.session_contract import SessionActor

    serialize_resolved_access_context(context)
    verbs = _SESSION_VERBS_BY_ROLE.get(context.role_id, frozenset())
    tool_caps = frozenset(f"tool:{name}" for name in context.capabilities)
    return SessionActor(
        subject=context.principal_id,
        profile_id=context.profile_id,
        capabilities=frozenset(set(verbs) | tool_caps),
        transport_id=transport_id,
    )


def media_delivery_scope_for_source(runner: Any, source: Any):
    """Context + profile scope for a turn's media paths after the turn scope was reset (#109024).

    Delivery runs on the adapter side, where the turn's contextvars are gone: the source's own
    resolved context is rebound so path validation sees the same authority as the turn did.
    """
    from gateway.access_context import bind_resolved_access_context
    from gateway.run import _profile_runtime_scope

    context = getattr(source, "resolved_access_context", None)
    if context is not None:
        return _bind_context_and_scope(bind_resolved_access_context, context, _profile_runtime_scope(profile_home(context), {}))
    if not getattr(getattr(runner, "config", None), "multiplex_profiles", False):
        return nullcontext()
    return _profile_runtime_scope(runner._resolve_profile_home_for_source(source), {})


@contextmanager
def _bind_context_and_scope(bind, context, scope):
    with bind(context), scope:
        yield
