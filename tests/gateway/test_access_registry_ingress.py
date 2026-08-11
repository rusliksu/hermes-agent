"""Focused ingress tests for the staged access-registry boundary."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from gateway.access_registry import (
    AccessDeniedError,
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
        roles={"family": RolePolicy("family", frozenset({"memory_read"}))},
        profiles=frozenset({"family-42"}),
        principal_bindings=(
            PrincipalBinding(
                principal_id="principal-42",
                role_id="family",
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


def _multi_profile_registry() -> AccessRegistry:
    bindings = []
    scope_capabilities = {}
    for user_id in ("42", "43"):
        principal_id = f"principal-{user_id}"
        profile_id = f"family-{user_id}"
        scope = f"private:{principal_id}"
        bindings.append(
            PrincipalBinding(
                principal_id=principal_id,
                role_id="family",
                profile_id=profile_id,
                transport_identity=TransportIdentity(
                    platform="telegram",
                    account="main-bot",
                    peer_kind="dm",
                    user_id=user_id,
                    chat_id=user_id,
                ),
                conversation_scope=scope,
                delivery_target=DeliveryTarget(
                    platform="telegram",
                    account="main-bot",
                    peer_kind="dm",
                    chat_id=user_id,
                ),
            )
        )
        scope_capabilities[scope] = frozenset({"memory_read"})
    return AccessRegistry(
        roles={"family": RolePolicy("family", frozenset({"memory_read"}))},
        profiles=frozenset({"family-42", "family-43"}),
        principal_bindings=tuple(bindings),
        scope_capabilities=scope_capabilities,
        backend_capabilities=frozenset({"memory_read"}),
    )


def _dm_event(user_id: str) -> MessageEvent:
    return MessageEvent(
        text=f"hello-{user_id}",
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id=user_id,
            user_id=user_id,
            chat_type="dm",
            route_account="main-bot",
        ),
    )


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
    assert seen[0].role_id == "family"
    assert event.source.profile == "family-42"
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


@pytest.mark.asyncio
async def test_simultaneous_profile_turns_keep_access_contexts_pairwise_isolated():
    runner = _runner()
    runner.access_registry = _multi_profile_registry()
    ready = asyncio.Event()
    calls = 0
    seen = {}

    async def inner(event):
        nonlocal calls
        context = get_resolved_access_context()
        assert context is not None
        seen[context.principal_id] = (context.profile_id, event.source.chat_id)
        calls += 1
        if calls == 2:
            ready.set()
        await ready.wait()
        # Both tasks remain suspended at the same time; resuming one must not
        # observe the sibling's profile or delivery target.
        current = get_resolved_access_context()
        assert current is context
        return context.profile_id

    runner._handle_message_inner = inner
    results = await asyncio.gather(
        runner._handle_message(_dm_event("42")),
        runner._handle_message(_dm_event("43")),
    )

    assert results == ["family-42", "family-43"]
    assert seen == {
        "principal-42": ("family-42", "42"),
        "principal-43": ("family-43", "43"),
    }
    assert get_resolved_access_context() is None


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


def test_ingress_rejects_post_resolution_dm_topic_tamper_against_original_source():
    registry = _registry()
    identity_alpha = TransportIdentity(
        platform="telegram",
        account="main-bot",
        peer_kind="dm",
        user_id="42",
        chat_id="42",
        thread_id="topic-alpha",
    )
    resolved_alpha = registry.resolve(identity_alpha)
    tampered = replace(
        resolved_alpha,
        delivery_target=replace(
            resolved_alpha.delivery_target,
            thread_id="topic-beta",
        ),
    )
    runner = _runner()
    runner.access_registry = SimpleNamespace(
        resolve=lambda identity: tampered,
        validate_resolved_context=registry.validate_resolved_context,
        validate_resolved_context_for_identity=registry.validate_resolved_context_for_identity,
    )
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="42",
        user_id="42",
        chat_type="dm",
        thread_id="topic-alpha",
        route_account="main-bot",
    )

    with pytest.raises(AccessDeniedError) as exc:
        runner._resolve_access_context_for_source(source)

    assert exc.value.reason == "resolved_access_context_source_mismatch"


def test_session_search_cannot_select_foreign_profile(monkeypatch):
    from inspect import signature
    from tools import session_search_tool

    assert "profile" not in signature(session_search_tool.session_search).parameters


def test_session_search_does_not_scan_other_profiles_on_scoped_miss():
    from tools import session_search_tool

    # The old cross-profile locator is intentionally gone. A caller-supplied
    # SessionDB is required for typed access, so there is no fallback scan.
    assert not hasattr(session_search_tool, "_locate_session_db")


def test_session_key_uses_resolved_context_profile_without_legacy_source_route(tmp_path):
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore

    runner = _runner()
    config = GatewayConfig.from_dict({"multiplex_profiles": True})
    store = SessionStore(tmp_path, config)
    context = runner.access_registry.resolve(
        TransportIdentity(
            platform="telegram",
            account="main-bot",
            peer_kind="dm",
            user_id="42",
            chat_id="42",
        )
    )
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="42",
        chat_type="dm",
        user_id="42",
        resolved_access_context=context,
    )

    assert store._generate_session_key(source).startswith("agent:family-42:")
