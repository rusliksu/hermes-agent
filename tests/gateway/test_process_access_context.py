"""Privacy regressions for durable background-process access context."""

from types import SimpleNamespace
import json

import pytest

from gateway.access_registry import (
    AccessRegistry,
    DeliveryTarget,
    PrincipalBinding,
    RolePolicy,
    TransportIdentity,
    serialize_resolved_access_context,
)
from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.run import GatewayRunner
from gateway.session import SessionSource
from gateway.session_context import get_resolved_access_context
from tools.process_registry import ProcessRegistry, ProcessSession


@pytest.fixture
def isolated_registry(tmp_path, monkeypatch):
    import tools.process_registry as process_registry_module

    monkeypatch.setattr(process_registry_module, "CHECKPOINT_PATH", tmp_path / "processes.json")
    return ProcessRegistry()


def _access_registry() -> AccessRegistry:
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
        principal_bindings=(PrincipalBinding(
            principal_id="principal-42",
            role_id="family_standard",
            profile_id="family-42",
            transport_identity=identity,
            conversation_scope="private:principal-42",
            delivery_target=target,
        ),),
        scope_capabilities={"private:principal-42": frozenset({"memory_read"})},
        backend_capabilities=frozenset({"memory_read"}),
    )


def test_completion_event_carries_only_canonical_access_payload(isolated_registry):
    payload = {
        "principal_id": "principal-42",
        "role_id": "family_standard",
        "profile_id": "family-42",
        "conversation_scope": "private:principal-42",
        "capabilities": ["memory_read"],
        "delivery_target": {
            "platform": "telegram",
            "account": "main-bot",
            "peer_kind": "dm",
            "chat_id": "42",
        },
    }
    session = ProcessSession(
        id="proc_context",
        command="echo done",
        notify_on_complete=True,
        resolved_access_context=payload,
        exited=True,
        exit_code=0,
        output_buffer="done\n",
    )
    isolated_registry._running[session.id] = session

    isolated_registry._move_to_finished(session)

    event = isolated_registry.completion_queue.get_nowait()
    assert event["resolved_access_context"] == payload
    assert set(event["resolved_access_context"]) == {
        "principal_id",
        "role_id",
        "profile_id",
        "conversation_scope",
        "capabilities",
        "delivery_target",
    }


def test_checkpoint_preserves_access_payload_for_recovery(isolated_registry):
    payload = {
        "principal_id": "principal-42",
        "role_id": "family_standard",
        "profile_id": "family-42",
        "conversation_scope": "private:principal-42",
        "capabilities": ["memory_read"],
        "delivery_target": {
            "platform": "telegram",
            "account": "main-bot",
            "peer_kind": "dm",
            "chat_id": "42",
        },
    }
    session = ProcessSession(
        id="proc_checkpoint",
        command="sleep 10",
        resolved_access_context=payload,
    )
    isolated_registry._running[session.id] = session
    isolated_registry._write_checkpoint()

    import tools.process_registry as process_registry_module

    data = json.loads(process_registry_module.CHECKPOINT_PATH.read_text())
    assert data[0]["resolved_access_context"] == payload


def test_synthetic_event_requires_context_when_registry_is_active():
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.access_registry = _access_registry()
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="42",
        user_id="42",
        chat_type="dm",
        route_account="main-bot",
    )
    runner.session_store = SimpleNamespace(
        _ensure_loaded=lambda: None,
        _entries={"agent:main:telegram:dm:42": SimpleNamespace(origin=source)},
    )
    runner._session_source_cache = {}

    assert runner._build_process_event_source({
        "session_key": "agent:main:telegram:dm:42",
        "session_id": "proc_missing_context",
    }) is None


def test_synthetic_event_binds_and_validates_persisted_context():
    registry = _access_registry()
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.access_registry = registry
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="42",
        user_id="42",
        chat_type="dm",
        route_account="main-bot",
    )
    runner.session_store = SimpleNamespace(
        _ensure_loaded=lambda: None,
        _entries={"agent:main:telegram:dm:42": SimpleNamespace(origin=source)},
    )
    runner._session_source_cache = {}
    context = registry.resolve(TransportIdentity(
        platform="telegram",
        account="main-bot",
        peer_kind="dm",
        user_id="42",
        chat_id="42",
    ))

    restored = runner._build_process_event_source({
        "session_key": "agent:main:telegram:dm:42",
        "session_id": "proc_context",
        "resolved_access_context": serialize_resolved_access_context(context),
    })

    assert restored is not None
    assert restored.resolved_access_context == context


@pytest.mark.asyncio
async def test_internal_typed_event_rebinds_context_before_inner_handler():
    registry = _access_registry()
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.access_registry = registry
    runner.config = SimpleNamespace(multiplex_profiles=True)
    seen = []

    async def inner(_event):
        seen.append(get_resolved_access_context())
        return "ok"

    runner._handle_message_inner = inner
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="42",
        user_id="42",
        chat_type="dm",
        route_account="main-bot",
    )
    source.resolved_access_context = registry.resolve(TransportIdentity(
        platform="telegram",
        account="main-bot",
        peer_kind="dm",
        user_id="42",
        chat_id="42",
    ))

    assert await runner._handle_message(MessageEvent(
        text="completion",
        source=source,
        internal=True,
    )) == "ok"
    assert seen == [source.resolved_access_context]
