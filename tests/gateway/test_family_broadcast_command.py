"""Production-path tests for the gateway-owned family broadcast command."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import tools.send_message_tool as send_message_module
from gateway.access_registry import (
    AccessRegistry,
    DeliveryTarget,
    PrincipalBinding,
    RolePolicy,
    TransportIdentity,
)
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionEntry, SessionSource, build_session_key
from hermes_state import SessionDB


@pytest.fixture(autouse=True)
def _isolate_broadcast_state_db(monkeypatch):
    import os
    from pathlib import Path as _Path
    import hermes_state
    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", _Path(os.environ["HERMES_HOME"]) / "state.db", raising=False)


_ACCOUNT = "TEST_ONLY_BROADCAST_ACCOUNT"
_OWNER_PEER = "TEST_ONLY_OWNER_PEER"
_PRIVATE_SENTINEL = "TEST_ONLY_PRIVATE_INPUT"
_BROADCAST_SCOPE = "family_broadcast:v1"


def _binding(label: str, role: str) -> PrincipalBinding:
    peer = _OWNER_PEER if role == "owner" else f"TEST_ONLY_{label}_PEER"
    identity = TransportIdentity(
        platform="telegram",
        account=_ACCOUNT,
        peer_kind="dm",
        user_id=peer,
        chat_id=peer,
    )
    return PrincipalBinding(
        principal_id=f"TEST_ONLY_{label}_PRINCIPAL",
        role_id=role,
        profile_id=f"TEST_ONLY_{label}_PROFILE",
        transport_identity=identity,
        conversation_scope=f"test-only-{label.lower()}-scope",
        delivery_target=DeliveryTarget(
            platform=identity.platform,
            account=identity.account,
            peer_kind=identity.peer_kind,
            chat_id=identity.chat_id,
        ),
    )


def _registry() -> AccessRegistry:
    bindings = (
        _binding("OWNER", "owner"),
        _binding("FAMILY_ALPHA", "family"),
        _binding("FAMILY_BETA", "family"),
    )
    registry = AccessRegistry(
        roles={
            "owner": RolePolicy("owner", frozenset({"broadcast"})),
            "family": RolePolicy("family", frozenset({"receive"})),
        },
        profiles=frozenset(binding.profile_id for binding in bindings),
        principal_bindings=bindings,
        scope_capabilities={
            bindings[0].conversation_scope: frozenset({"broadcast"}),
            bindings[1].conversation_scope: frozenset({"receive"}),
            bindings[2].conversation_scope: frozenset({"receive"}),
        },
        backend_capabilities=frozenset({"broadcast", "receive"}),
    )
    registry.require_valid()
    return registry


def _source_for(binding: PrincipalBinding) -> SessionSource:
    source = SessionSource(
        platform=Platform.TELEGRAM,
        user_id=str(binding.transport_identity.user_id),
        chat_id=str(binding.transport_identity.chat_id),
        user_name="test-only-caller",
        chat_type="dm",
    )
    source.route_account = _ACCOUNT
    return source


def _event(
    text: str,
    binding: PrincipalBinding,
    *,
    media: bool = False,
) -> MessageEvent:
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=_source_for(binding),
        message_id="TEST_ONLY_MESSAGE_EVENT",
        media_urls=["TEST_ONLY_MEDIA_PATH"] if media else [],
        media_types=["application/octet-stream"] if media else [],
    )


def _runner(registry: AccessRegistry | None):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        multiplex_profiles=True,
        platforms={
            Platform.TELEGRAM: PlatformConfig(enabled=True),
        },
    )
    runner.access_registry = registry
    runner.adapters = {}
    runner._profile_adapters = {}
    runner._voice_mode = {}
    runner._sessions = {}
    runner._session_stall_notified = {}
    runner._startup_restore_in_progress = False
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._draining = False
    runner.hooks = SimpleNamespace(
        emit=AsyncMock(),
        emit_collect=AsyncMock(return_value=[]),
        loaded_hooks=False,
    )

    owner_source = (
        _source_for(registry.principal_bindings[0])
        if registry is not None
        else SessionSource(
            platform=Platform.TELEGRAM,
            user_id=_OWNER_PEER,
            chat_id=_OWNER_PEER,
            chat_type="dm",
        )
    )
    session_entry = SessionEntry(
        session_key=build_session_key(owner_source),
        session_id="TEST_ONLY_SESSION",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = session_entry
    runner.session_store.load_transcript.return_value = []
    runner.session_store.has_any_sessions.return_value = True
    runner._session_db = None
    runner._reasoning_config = None
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._show_reasoning = False
    runner._is_user_authorized = lambda _source: True
    runner._set_session_env = lambda _context: None
    runner._should_send_voice_reply = lambda *_args, **_kwargs: False
    runner._send_voice_reply = AsyncMock()
    runner._capture_gateway_honcho_if_configured = lambda *args, **kwargs: None
    runner._emit_gateway_run_progress = AsyncMock()
    runner._run_agent = AsyncMock(
        side_effect=AssertionError("broadcast command reached the model loop")
    )
    return runner


def _decode_result(value: str) -> dict:
    result = json.loads(value)
    assert isinstance(result, dict)
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("command_name", ["family-broadcast", "family_broadcast"])
async def test_real_gateway_dispatch_uses_trusted_context_and_fixed_candidate(
    monkeypatch,
    command_name,
):
    registry = _registry()
    owner_binding = registry.principal_bindings[0]
    event = _event(f"/{command_name} test-operation-key", owner_binding)
    assert not hasattr(event.source, "resolved_access_context")

    delivery_config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(enabled=True),
        }
    )
    monkeypatch.setattr(
        "gateway.config.load_gateway_config",
        lambda: delivery_config,
    )

    operation_calls = []
    real_operation = send_message_module.family_broadcast_operation

    def recording_operation(request, **trusted):
        operation_calls.append((request, trusted))
        return real_operation(request, **trusted)

    monkeypatch.setattr(
        send_message_module,
        "family_broadcast_operation",
        recording_operation,
    )

    deliveries = []

    async def recording_send(
        platform,
        pconfig,
        chat_id,
        message,
        thread_id=None,
        media_files=None,
        force_document=False,
    ):
        deliveries.append(
            {
                "platform": platform,
                "configured": pconfig.enabled,
                "target_digest": hashlib.sha256(str(chat_id).encode()).hexdigest(),
                "message_identity": id(message),
                "message_digest": hashlib.sha256(message.encode()).hexdigest(),
                "thread_id": thread_id,
                "media_files": media_files,
                "force_document": force_document,
            }
        )
        return {"success": True, "message_id": "TEST_ONLY_PROVIDER_RECEIPT"}

    monkeypatch.setattr(send_message_module, "_send_to_platform", recording_send)

    encoded = await _runner(registry)._handle_message(event)
    result = _decode_result(encoded)

    assert result == {
        "success": True,
        "replayed": False,
        "delivery_state": "completed",
        "recipient_count": 2,
        "receipts": [
            {"ordinal": 1, "status": "delivered"},
            {"ordinal": 2, "status": "delivered"},
        ],
    }
    assert len(operation_calls) == 1
    request, trusted = operation_calls[0]
    assert request == {
        "operation_key": "test-operation-key",
    }
    assert trusted == {
        "caller_context": registry.resolve(owner_binding.transport_identity),
        "access_registry": registry,
    }
    assert event.source.resolved_access_context == trusted["caller_context"]

    fixed_identity = id(send_message_module.FAMILY_BROADCAST_TEXT)
    fixed_digest = hashlib.sha256(
        send_message_module.FAMILY_BROADCAST_TEXT.encode()
    ).hexdigest()
    assert len(deliveries) == 2
    assert all(
        delivery["platform"] is Platform.TELEGRAM
        and delivery["configured"] is True
        and delivery["message_identity"] == fixed_identity
        and delivery["message_digest"] == fixed_digest
        and delivery["thread_id"] is None
        and delivery["media_files"] is None
        and delivery["force_document"] is False
        for delivery in deliveries
    )
    visible = encoded + json.dumps(result, sort_keys=True)
    assert send_message_module.FAMILY_BROADCAST_TEXT not in visible
    assert _PRIVATE_SENTINEL not in visible

    db = SessionDB()
    try:
        rows = db.load_gateway_routing_entries(scope=_BROADCAST_SCOPE)
    finally:
        db.close()
    assert len(rows) == 1
    stored = next(iter(rows.values()))
    assert send_message_module.FAMILY_BROADCAST_TEXT not in stored
    assert "test-operation-key" not in stored


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "with_media"),
    [
        ("/family-broadcast", False),
        ("/family-broadcast key extra", False),
        (f"/family-broadcast key message={_PRIVATE_SENTINEL}", False),
        (f"/family-broadcast key target={_PRIVATE_SENTINEL}", False),
        (f"/family-broadcast key profile={_PRIVATE_SENTINEL}", False),
        ("/family-broadcast key", True),
        pytest.param(
            "/family_broadcast",
            False,
            id="telegram-spelling-missing-key",
        ),
        pytest.param(
            f"/family_broadcast key role={_PRIVATE_SENTINEL}",
            False,
            id="telegram-spelling-authority-selector",
        ),
    ],
)
async def test_invalid_command_input_stops_before_operation(
    monkeypatch,
    text,
    with_media,
):
    registry = _registry()
    calls = []

    def forbidden_operation(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("invalid command input reached the operation")

    monkeypatch.setattr(
        send_message_module,
        "family_broadcast_operation",
        forbidden_operation,
    )
    encoded = await _runner(registry)._handle_message(
        _event(text, registry.principal_bindings[0], media=with_media)
    )

    assert _decode_result(encoded) == {
        "success": False,
        "error_code": "invalid_request",
    }
    assert calls == []
    assert _PRIVATE_SENTINEL not in encoded
    db = SessionDB()
    try:
        assert db.load_gateway_routing_entries(scope=_BROADCAST_SCOPE) == {}
    finally:
        db.close()


@pytest.mark.asyncio
async def test_lower_operation_exception_is_generic_and_privacy_safe(
    monkeypatch,
    caplog,
):
    registry = _registry()
    operation_calls = []
    delivery_calls = []

    def raising_operation(*args, **kwargs):
        operation_calls.append((args, kwargs))
        raise RuntimeError(_PRIVATE_SENTINEL)

    def forbidden_delivery(*args, **kwargs):
        delivery_calls.append((args, kwargs))
        raise AssertionError("failed lower operation reached delivery")

    monkeypatch.setattr(
        send_message_module,
        "family_broadcast_operation",
        raising_operation,
    )
    monkeypatch.setattr(
        send_message_module,
        "_family_broadcast_deliver",
        forbidden_delivery,
    )

    with caplog.at_level("WARNING", logger="gateway.run"):
        encoded = await _runner(registry)._handle_message(
            _event(
                "/family-broadcast test-operation-key",
                registry.principal_bindings[0],
            )
        )

    assert _decode_result(encoded) == {
        "success": False,
        "error_code": "operation_failed",
    }
    assert len(operation_calls) == 1
    assert delivery_calls == []
    db = SessionDB()
    try:
        assert db.load_gateway_routing_entries(scope=_BROADCAST_SCOPE) == {}
    finally:
        db.close()
    assert _PRIVATE_SENTINEL not in encoded + caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lower_result",
    [
        pytest.param(
            {"success": _PRIVATE_SENTINEL},
            id="malformed-success",
        ),
        pytest.param(
            {
                "success": False,
                "error_code": "invalid_request",
                "private_detail": _PRIVATE_SENTINEL,
            },
            id="extra-field",
        ),
        pytest.param(
            {"success": False, "error_code": [_PRIVATE_SENTINEL]},
            id="list-error-code",
        ),
        pytest.param(
            {
                "success": True,
                "replayed": False,
                "delivery_state": [_PRIVATE_SENTINEL],
                "recipient_count": 1,
                "receipts": [{"ordinal": 1, "status": "delivered"}],
            },
            id="list-delivery-state",
        ),
        pytest.param(
            {
                "success": True,
                "replayed": False,
                "delivery_state": "completed",
                "recipient_count": 1,
                "receipts": [{"ordinal": 1, "status": [_PRIVATE_SENTINEL]}],
            },
            id="list-receipt-status",
        ),
        pytest.param(
            {
                "success": False,
                "error_code": {"private": _PRIVATE_SENTINEL},
            },
            id="mapping-error-code",
        ),
        pytest.param(
            {
                "success": True,
                "replayed": False,
                "delivery_state": {"private": _PRIVATE_SENTINEL},
                "recipient_count": 1,
                "receipts": [{"ordinal": 1, "status": "delivered"}],
            },
            id="mapping-delivery-state",
        ),
        pytest.param(
            {
                "success": True,
                "replayed": False,
                "delivery_state": "completed",
                "recipient_count": 1,
                "receipts": [
                    {"ordinal": 1, "status": {"private": _PRIVATE_SENTINEL}}
                ],
            },
            id="mapping-receipt-status",
        ),
    ],
)
async def test_malformed_lower_result_is_generic_and_privacy_safe(
    monkeypatch,
    caplog,
    lower_result,
):
    registry = _registry()
    operation_calls = []
    delivery_calls = []

    def malformed_operation(*args, **kwargs):
        operation_calls.append((args, kwargs))
        return lower_result

    def forbidden_delivery(*args, **kwargs):
        delivery_calls.append((args, kwargs))
        raise AssertionError("malformed lower result reached delivery")

    monkeypatch.setattr(
        send_message_module,
        "family_broadcast_operation",
        malformed_operation,
    )
    monkeypatch.setattr(
        send_message_module,
        "_family_broadcast_deliver",
        forbidden_delivery,
    )

    with caplog.at_level("WARNING", logger="gateway.run"):
        encoded = await _runner(registry)._handle_message(
            _event(
                "/family-broadcast test-operation-key",
                registry.principal_bindings[0],
            )
        )

    assert _decode_result(encoded) == {
        "success": False,
        "error_code": "operation_failed",
    }
    assert len(operation_calls) == 1
    assert delivery_calls == []
    db = SessionDB()
    try:
        assert db.load_gateway_routing_entries(scope=_BROADCAST_SCOPE) == {}
    finally:
        db.close()
    assert _PRIVATE_SENTINEL not in encoded + caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["family", "missing_registry"])
async def test_untrusted_runtime_state_stops_before_operation(monkeypatch, case):
    registry = _registry()
    calls = []

    def forbidden_operation(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("untrusted runtime state reached the operation")

    monkeypatch.setattr(
        send_message_module,
        "family_broadcast_operation",
        forbidden_operation,
    )
    if case == "family":
        runner = _runner(registry)
        binding = registry.principal_bindings[1]
        expected = {"success": False, "error_code": "access_denied"}
    else:
        runner = _runner(None)
        binding = registry.principal_bindings[0]
        expected = {"success": False, "error_code": "registry_unavailable"}

    encoded = await runner._handle_message(
        _event("/family_broadcast test-operation-key", binding)
    )

    assert _decode_result(encoded) == expected
    assert calls == []
    db = SessionDB()
    try:
        assert db.load_gateway_routing_entries(scope=_BROADCAST_SCOPE) == {}
    finally:
        db.close()
