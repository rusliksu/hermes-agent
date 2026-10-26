"""Focused tests for access-registry ingress on the current gateway."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from gateway.access_registry import (
    AccessDeniedError,
    AccessRegistry,
    DeliveryTarget,
    PrincipalBinding,
    RedactedAuditMetadata,
    RolePolicy,
    TransportIdentity,
)
from gateway.config import GatewayConfig, Platform
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource


_ACCOUNT = "bot-ingress-test"


def _binding(role: str = "owner") -> PrincipalBinding:
    identity = TransportIdentity(
        platform="telegram",
        account=_ACCOUNT,
        peer_kind="dm",
        user_id=f"{role}-user",
        chat_id=f"{role}-user",
    )
    return PrincipalBinding(
        principal_id=f"{role}-principal",
        role_id=role,
        profile_id=f"{role}-profile",
        transport_identity=identity,
        conversation_scope=f"{role}-scope",
        delivery_target=DeliveryTarget(
            platform=identity.platform,
            account=identity.account,
            peer_kind=identity.peer_kind,
            chat_id=identity.chat_id,
        ),
    )


def _registry() -> AccessRegistry:
    owner = _binding("owner")
    registry = AccessRegistry(
        roles={
            "owner": RolePolicy("owner", frozenset({"broadcast"})),
        },
        profiles=frozenset({owner.profile_id}),
        principal_bindings=(owner,),
        shared_scope_bindings=frozenset(),
        scope_capabilities={owner.conversation_scope: frozenset({"broadcast"})},
        backend_capabilities=frozenset({"broadcast"}),
    )
    registry.require_valid()
    return registry


def _source(**overrides) -> SessionSource:
    values = dict(
        platform=Platform.TELEGRAM,
        user_id="owner-user",
        chat_id="owner-user",
        chat_type="dm",
    )
    values.update(overrides)
    source = SessionSource(**values)
    source.route_account = _ACCOUNT
    return source


def _runner(registry):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(multiplex_profiles=True)
    runner.access_registry = registry
    return runner


def test_transport_identity_uses_session_route_account():
    from gateway.run import GatewayRunner

    identity = GatewayRunner._transport_identity_for_source(_source())

    assert identity == _binding().transport_identity


def test_resolve_access_context_returns_exact_owner_context():
    owner = _binding()
    runner = _runner(_registry())

    context = runner._resolve_access_context_for_source(_source())

    assert context.principal_id == owner.principal_id
    assert context.role_id == "owner"
    assert context.profile_id == owner.profile_id
    assert context.delivery_target.chat_id == owner.delivery_target.chat_id


def test_stamped_profile_mismatch_fails_closed():
    runner = _runner(_registry())

    with pytest.raises(AccessDeniedError):
        runner._resolve_access_context_for_source(_source(profile="other-profile"))


@pytest.mark.asyncio
async def test_denied_ingress_stops_before_session_store():
    from gateway.run import GatewayRunner

    class DenyingRegistry:
        def resolve(self, _identity):
            raise AccessDeniedError(
                "missing_principal_binding",
                RedactedAuditMetadata(event="resolve"),
            )

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(multiplex_profiles=True)
    runner.access_registry = DenyingRegistry()
    runner.session_store = SimpleNamespace(
        get_or_create_session=lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("session store must not run before trusted access")
        )
    )

    event = MessageEvent(text="hello", source=_source())
    assert await runner._handle_message(event) is None
