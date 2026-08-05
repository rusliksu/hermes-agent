"""Focused ingress tests for the staged access-registry boundary."""

from types import SimpleNamespace

import pytest

from gateway.access_registry import (
    AccessRegistry,
    DeliveryTarget,
    PrincipalBinding,
    RolePolicy,
    TransportIdentity,
)
from gateway.platforms.base import MessageEvent
from gateway.run import GatewayRunner
from gateway.session import Platform, SessionSource
from gateway.session_context import bind_resolved_access_context, get_resolved_access_context


def _registry() -> AccessRegistry:
    identity = TransportIdentity(
        platform="telegram",
        account="main-bot",
        peer_kind="dm",
        user_id="42",
        chat_id="42",
    )
    target = DeliveryTarget(
        platform="telegram",
        account="main-bot",
        peer_kind="dm",
        chat_id="42",
    )
    return AccessRegistry(
        roles={"family_standard": RolePolicy("family_standard", frozenset({"memory_read"}))},
        profiles=frozenset({"family-42"}),
        principal_bindings=(
            PrincipalBinding(
                principal_id="principal-42",
                role_id="family_standard",
                profile_id="family-42",
                transport_identity=identity,
                conversation_scope="private:principal-42",
                delivery_target=target,
            ),
        ),
        scope_capabilities={"private:principal-42": frozenset({"memory_read"})},
        backend_capabilities=frozenset({"memory_read"}),
    )


def _runner() -> GatewayRunner:
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.access_registry = _registry()
    runner.config = SimpleNamespace(multiplex_profiles=True)
    return runner


@pytest.mark.asyncio
async def test_registry_wrapper_binds_exact_context_and_restores_it():
    runner = _runner()
    seen = []

    async def inner(event):
        seen.append(get_resolved_access_context())
        return "ok"

    runner._handle_message_inner = inner
    event = MessageEvent(
        text="hello",
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="42",
            user_id="42",
            chat_type="dm",
            route_account="main-bot",
        ),
    )

    assert await runner._handle_message(event) == "ok"
    assert seen[0].principal_id == "principal-42"
    assert seen[0].role_id == "family_standard"
    assert get_resolved_access_context() is None


@pytest.mark.asyncio
async def test_unknown_or_mismatched_identity_is_rejected_before_inner():
    runner = _runner()
    called = False

    async def inner(event):
        nonlocal called
        called = True
        return "must-not-run"

    runner._handle_message_inner = inner
    unknown = MessageEvent(
        text="unknown",
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="99",
            user_id="99",
            chat_type="dm",
            route_account="main-bot",
        ),
    )
    mismatched = MessageEvent(
        text="spoof",
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="42",
            user_id="99",
            chat_type="dm",
            route_account="main-bot",
        ),
    )

    assert await runner._handle_message(unknown) is None
    assert await runner._handle_message(mismatched) is None
    assert called is False


def test_route_account_round_trips_as_server_owned_source_field():
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="42",
        user_id="42",
        chat_type="dm",
        route_account="main-bot",
    )
    restored = SessionSource.from_dict(source.to_dict())
    assert restored.route_account == "main-bot"


def test_session_search_cannot_select_foreign_profile(monkeypatch):
    from tools import session_search_tool

    with bind_resolved_access_context(SimpleNamespace(profile_id="family-42")):
        result = session_search_tool.session_search(
            profile="owner",
            db=object(),
        )
    assert '"success": false' in result
    assert "foreign profile scope" in result


def test_session_search_does_not_scan_other_profiles_on_scoped_miss(monkeypatch):
    from tools import session_search_tool

    class MissingDB:
        def get_session(self, _session_id):
            return None

    def forbidden_scan(_session_id):
        raise AssertionError("cross-profile scan must not run")

    monkeypatch.setattr(session_search_tool, "_locate_session_db", forbidden_scan)
    with bind_resolved_access_context(SimpleNamespace(profile_id="family-42")):
        result = session_search_tool.session_search(
            session_id="missing-session",
            db=MissingDB(),
        )
    assert '"success": false' in result
