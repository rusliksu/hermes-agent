from __future__ import annotations

import hashlib
import inspect
import json
import multiprocessing as mp
import os
import queue
import time
from pathlib import Path
from typing import Any

import pytest

import tools.send_message_tool as send_message_module
from gateway.access_registry import (
    AccessRegistry,
    DeliveryTarget,
    ParticipantIdentity,
    PrincipalBinding,
    ResolvedAccessContext,
    RolePolicy,
    SharedScopeBinding,
    TransportIdentity,
    deserialize_resolved_access_context,
)
from hermes_state import SessionDB
from tools.send_message_tool import (
    FAMILY_BROADCAST_TEXT,
    SEND_MESSAGE_SCHEMA,
    family_broadcast_operation,
)


@pytest.fixture(autouse=True)
def _isolate_broadcast_state_db(monkeypatch):
    """Point SessionDB at the per-test HERMES_HOME instead of the real state.db."""
    import hermes_state
    db_path = Path(os.environ["HERMES_HOME"]) / "state.db"
    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", db_path, raising=False)


_BROADCAST_SCOPE = "family_broadcast:v1"
_APPROVED_MESSAGE_DIGEST = (
    "f337ac57f33486543cc8bc9be40f4e76127bd52aca142948a20d34239b4ae5a7"
)
_ACCOUNT = "OFFLINE_ACCOUNT_SENTINEL"
_OWNER_ID = "OWNER_PRINCIPAL_RAW_SENTINEL"
_OWNER_PROFILE = "OWNER_PROFILE_RAW_SENTINEL"
_OWNER_CHAT = "OWNER_CHAT_RAW_SENTINEL"
_FAMILY_IDS = (
    "FAMILY_PRINCIPAL_ALPHA_RAW_SENTINEL",
    "FAMILY_PRINCIPAL_BETA_RAW_SENTINEL",
)
_FAMILY_PROFILES = (
    "FAMILY_PROFILE_ALPHA_RAW_SENTINEL",
    "FAMILY_PROFILE_BETA_RAW_SENTINEL",
)
_FAMILY_CHATS = (
    "FAMILY_CHAT_ALPHA_RAW_SENTINEL",
    "FAMILY_CHAT_BETA_RAW_SENTINEL",
)
_INACTIVE_ID = "INACTIVE_FAMILY_RAW_SENTINEL"
_SHARED_ID = "SHARED_ROOM_RAW_SENTINEL"
_PROVIDER_SECRET = "PROVIDER_RESULT_SECRET_SENTINEL"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _dm_binding(
    *,
    principal_id: str,
    role_id: str,
    profile_id: str,
    chat_id: str,
    scope: str,
    active: bool = True,
) -> PrincipalBinding:
    identity = TransportIdentity(
        platform="telegram",
        account=_ACCOUNT,
        peer_kind="dm",
        user_id=chat_id,
        chat_id=chat_id,
    )
    return PrincipalBinding(
        principal_id=principal_id,
        role_id=role_id,
        profile_id=profile_id,
        transport_identity=identity,
        conversation_scope=scope,
        delivery_target=DeliveryTarget(
            platform=identity.platform,
            account=identity.account,
            peer_kind=identity.peer_kind,
            chat_id=identity.chat_id,
            thread_id=identity.thread_id,
        ),
        active=active,
    )


def _build_registry(
    *,
    family_variant: str = "",
    include_family: bool = True,
    include_shared: bool = True,
    include_inactive: bool = True,
    family_capabilities: frozenset[str] = frozenset({"receive"}),
) -> tuple[AccessRegistry, ResolvedAccessContext]:
    owner_scope = "owner-scope"
    owner = _dm_binding(
        principal_id=_OWNER_ID,
        role_id="owner",
        profile_id=_OWNER_PROFILE,
        chat_id=_OWNER_CHAT,
        scope=owner_scope,
    )
    suffix = f"-{family_variant}" if family_variant else ""
    principals: list[PrincipalBinding] = [owner]
    profiles = {_OWNER_PROFILE}
    scopes: dict[str, frozenset[str]] = {
        owner_scope: frozenset({"broadcast"}),
    }

    if include_family:
        for index in range(2):
            scope = f"family-scope-{index}{suffix}"
            principal = f"{_FAMILY_IDS[index]}{suffix}"
            profile = f"{_FAMILY_PROFILES[index]}{suffix}"
            chat = f"{_FAMILY_CHATS[index]}{suffix}"
            principals.append(
                _dm_binding(
                    principal_id=principal,
                    role_id="family",
                    profile_id=profile,
                    chat_id=chat,
                    scope=scope,
                )
            )
            profiles.add(profile)
            scopes[scope] = family_capabilities

    if include_inactive:
        inactive_scope = "inactive-family-scope"
        inactive_profile = "INACTIVE_FAMILY_PROFILE_RAW_SENTINEL"
        principals.append(
            _dm_binding(
                principal_id=_INACTIVE_ID,
                role_id="family",
                profile_id=inactive_profile,
                chat_id="INACTIVE_FAMILY_CHAT_RAW_SENTINEL",
                scope=inactive_scope,
                active=False,
            )
        )
        profiles.add(inactive_profile)
        scopes[inactive_scope] = frozenset({"receive"})

    shared_bindings: tuple[SharedScopeBinding, ...] = ()
    if include_shared:
        shared_scope = "shared-room-scope"
        shared_profile = "SHARED_ROOM_PROFILE_RAW_SENTINEL"
        room_identity = TransportIdentity(
            platform="telegram",
            account=_ACCOUNT,
            peer_kind="group",
            user_id="SHARED_MEMBER_RAW_SENTINEL",
            chat_id="SHARED_CHAT_RAW_SENTINEL",
        )
        shared_bindings = (
            SharedScopeBinding(
                principal_id=_SHARED_ID,
                role_id="shared_room",
                profile_id=shared_profile,
                room_identity=room_identity,
                conversation_scope=shared_scope,
                delivery_target=DeliveryTarget(
                    platform=room_identity.platform,
                    account=room_identity.account,
                    peer_kind=room_identity.peer_kind,
                    chat_id=room_identity.chat_id,
                    thread_id=room_identity.thread_id,
                ),
                participant_identities=(
                    ParticipantIdentity(
                        platform="telegram",
                        account=_ACCOUNT,
                        user_id="SHARED_MEMBER_RAW_SENTINEL",
                    ),
                ),
            ),
        )
        profiles.add(shared_profile)
        scopes[shared_scope] = frozenset({"room"})

    registry = AccessRegistry(
        roles={
            "owner": RolePolicy("owner", frozenset({"broadcast"})),
            "family": RolePolicy("family", family_capabilities),
            "shared_room": RolePolicy("shared_room", frozenset({"room"})),
        },
        profiles=frozenset(profiles),
        principal_bindings=tuple(principals),
        shared_scope_bindings=shared_bindings,
        scope_capabilities=scopes,
        backend_capabilities=frozenset(
            {"broadcast", "receive", "room"} | family_capabilities
        ),
    )
    registry.require_valid()
    return registry, registry.resolve(owner.transport_identity)


def _family_context(registry: AccessRegistry) -> ResolvedAccessContext:
    family = next(
        binding
        for binding in registry.principal_bindings
        if binding.active and binding.role_id == "family"
    )
    return registry.resolve(family.transport_identity)


def _group_owner_context(owner: ResolvedAccessContext) -> ResolvedAccessContext:
    return ResolvedAccessContext(
        principal_id=owner.principal_id,
        role_id=owner.role_id,
        profile_id=owner.profile_id,
        conversation_scope=owner.conversation_scope,
        capabilities=owner.capabilities,
        delivery_target=DeliveryTarget(
            platform="telegram",
            account=_ACCOUNT,
            peer_kind="group",
            chat_id="FORGED_GROUP_RAW_SENTINEL",
        ),
    )


def _forged_owner_context(owner: ResolvedAccessContext) -> ResolvedAccessContext:
    return ResolvedAccessContext(
        principal_id="FORGED_OWNER_RAW_SENTINEL",
        role_id="owner",
        profile_id=owner.profile_id,
        conversation_scope=owner.conversation_scope,
        capabilities=owner.capabilities,
        delivery_target=owner.delivery_target,
    )


def _write_offline_gateway_config(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        "platforms:\n"
        "  telegram:\n"
        "    enabled: true\n"
        "    token: offline-test-token\n",
        encoding="utf-8",
    )


@pytest.fixture
def profile_home() -> Path:
    home = Path(os.environ["HERMES_HOME"])
    _write_offline_gateway_config(home)
    return home


@pytest.fixture
def delivery_calls(monkeypatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def recording_send(
        platform,
        pconfig,
        chat_id,
        message,
        thread_id=None,
        media_files=None,
        force_document=False,
    ):
        calls.append(
            {
                "platform": platform.value,
                "configured": bool(pconfig and pconfig.enabled),
                "target_digest": _sha256(str(chat_id)),
                "thread_digest": _sha256(str(thread_id or "")),
                "message_digest": _sha256(message),
                "message_identity": id(message),
                "media_count": len(media_files or ()),
                "force_document": force_document,
            }
        )
        return {"success": True, "message_id": _PROVIDER_SECRET}

    monkeypatch.setattr(send_message_module, "_send_to_platform", recording_send)
    return calls


def _load_record(operation_key: str) -> tuple[str | None, dict[str, Any] | None]:
    operation_digest = _sha256(operation_key)
    db = SessionDB()
    try:
        raw = db.load_gateway_routing_entry(
            operation_digest,
            scope=_BROADCAST_SCOPE,
        )
    finally:
        db.close()
    if raw is None:
        return None, None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        parsed = None
    return raw, parsed if isinstance(parsed, dict) else None


def _save_raw_record(operation_key: str, raw: str) -> None:
    db = SessionDB()
    try:
        db.save_gateway_routing_entry(
            _sha256(operation_key),
            raw,
            scope=_BROADCAST_SCOPE,
        )
    finally:
        db.close()


def _broadcast_rows() -> dict[str, str]:
    db = SessionDB()
    try:
        return db.load_gateway_routing_entries(scope=_BROADCAST_SCOPE)
    finally:
        db.close()


def _public_text(result: dict[str, Any]) -> str:
    return json.dumps(result, sort_keys=True, ensure_ascii=False)


def _assert_publicly_redacted(result: dict[str, Any], captured_logs: str = "") -> None:
    visible = _public_text(result) + captured_logs
    for sentinel in (
        FAMILY_BROADCAST_TEXT,
        _OWNER_ID,
        _OWNER_PROFILE,
        _OWNER_CHAT,
        *_FAMILY_IDS,
        *_FAMILY_PROFILES,
        *_FAMILY_CHATS,
        _INACTIVE_ID,
        _SHARED_ID,
        _PROVIDER_SECRET,
    ):
        assert sentinel not in visible


def _broadcast_process_worker(
    home: str,
    rounds: int,
    barrier,
    result_queue,
    delivery_queue,
    worker_id: int,
) -> None:
    os.environ["HERMES_HOME"] = home
    registry, owner = _build_registry(include_shared=False, include_inactive=False)

    # Pre-open both production dependencies before the timed contention gate.
    prewarm = SessionDB()
    prewarm.close()
    from gateway.config import load_gateway_config

    load_gateway_config()
    active_round = {"value": -1}

    async def recording_send(
        platform,
        pconfig,
        chat_id,
        message,
        thread_id=None,
        media_files=None,
        force_document=False,
    ):
        delivery_queue.put(
            (
                active_round["value"],
                worker_id,
                _sha256(str(chat_id)),
                _sha256(message),
            )
        )
        return {"success": True, "message_id": _PROVIDER_SECRET}

    send_message_module._send_to_platform = recording_send

    try:
        for round_id in range(rounds):
            active_round["value"] = round_id
            barrier.wait(timeout=30)
            started = time.monotonic()
            result = family_broadcast_operation(
                {"operation_key": f"spawn-round-{round_id}"},
                caller_context=owner,
                access_registry=registry,
            )
            result_queue.put(
                (round_id, worker_id, result, time.monotonic() - started)
            )
            barrier.wait(timeout=30)
    except BaseException as exc:
        result_queue.put(("worker_error", worker_id, type(exc).__name__, -1.0))


def test_fixed_message_and_ordinary_send_surface_are_exact_and_separate():
    assert isinstance(FAMILY_BROADCAST_TEXT, str)
    assert _sha256(FAMILY_BROADCAST_TEXT) == _APPROVED_MESSAGE_DIGEST
    actions = SEND_MESSAGE_SCHEMA["parameters"]["properties"]["action"]["enum"]
    assert actions == ["send", "list", "react", "unreact"]

    ordinary = json.loads(
        send_message_module.send_message_tool({"action": "family_broadcast"})
    )
    assert "error" in ordinary
    assert "delivery_state" not in ordinary


def test_operation_signature_has_no_content_target_or_test_escape_hatch():
    parameters = inspect.signature(family_broadcast_operation).parameters
    assert tuple(parameters) == ("request", "caller_context", "access_registry")
    assert parameters["caller_context"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["access_registry"].kind is inspect.Parameter.KEYWORD_ONLY

    registry, owner = _build_registry()
    with pytest.raises(TypeError):
        family_broadcast_operation(
            {"operation_key": "signature-check"},
            caller_context=owner,
            access_registry=registry,
            message="CALLER_CONTENT_RAW_SENTINEL",
        )


def test_first_claim_persists_exact_family_dm_snapshot_and_redacts_public_result(
    profile_home,
    delivery_calls,
    caplog,
):
    registry, owner = _build_registry()
    operation_key = "OPERATION_KEY_RAW_SENTINEL"

    result = family_broadcast_operation(
        {"operation_key": operation_key},
        caller_context=owner,
        access_registry=registry,
    )

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
    assert len(delivery_calls) == 2
    assert {call["target_digest"] for call in delivery_calls} == {
        _sha256(chat) for chat in _FAMILY_CHATS
    }
    assert all(call["configured"] for call in delivery_calls)
    assert all(
        call["message_digest"] == _APPROVED_MESSAGE_DIGEST
        and call["message_identity"] == id(FAMILY_BROADCAST_TEXT)
        and call["media_count"] == 0
        and call["force_document"] is False
        for call in delivery_calls
    )

    raw, record = _load_record(operation_key)
    assert raw is not None and record is not None
    assert set(record) == {
        "schema_version",
        "operation_digest",
        "message_policy_version",
        "message_digest",
        "owner_fingerprint",
        "binding_snapshot",
        "delivery_state",
        "redacted_result",
        "created_at",
        "updated_at",
    }
    assert record["operation_digest"] == _sha256(operation_key)
    assert record["message_digest"] == _APPROVED_MESSAGE_DIGEST
    assert record["delivery_state"] == "completed"
    assert [entry["ordinal"] for entry in record["binding_snapshot"]] == [1, 2]
    assert [entry["binding_digest"] for entry in record["binding_snapshot"]] == sorted(
        entry["binding_digest"] for entry in record["binding_snapshot"]
    )
    contexts = [
        deserialize_resolved_access_context(entry["resolved_context"])
        for entry in record["binding_snapshot"]
    ]
    assert {context.principal_id for context in contexts} == set(_FAMILY_IDS)
    assert all(
        context.role_id == "family"
        and context.delivery_target.peer_kind == "dm"
        for context in contexts
    )
    assert operation_key not in raw
    assert FAMILY_BROADCAST_TEXT not in raw
    assert _OWNER_ID not in raw
    assert _OWNER_PROFILE not in raw
    assert _OWNER_CHAT not in raw
    assert _INACTIVE_ID not in raw
    assert _SHARED_ID not in raw
    assert _PROVIDER_SECRET not in raw
    _assert_publicly_redacted(result, caplog.text)
    assert operation_key not in _public_text(result) + caplog.text


def test_restart_replay_ignores_changed_empty_and_unavailable_registry(
    profile_home,
    delivery_calls,
):
    registry, owner = _build_registry()
    operation_key = "RESTART_OPERATION_KEY_RAW_SENTINEL"
    fresh = family_broadcast_operation(
        {"operation_key": operation_key},
        caller_context=owner,
        access_registry=registry,
    )
    assert fresh["delivery_state"] == "completed"
    assert len(delivery_calls) == 2
    delivery_calls.clear()

    empty_registry, _ = _build_registry(
        include_family=False,
        include_shared=False,
        include_inactive=False,
    )
    drifted_registry, _ = _build_registry(
        family_variant="drifted",
        include_shared=False,
        include_inactive=False,
    )
    expected = {**fresh, "replayed": True}
    replay_registries = (None, empty_registry, drifted_registry)
    for index in range(20):
        replay = family_broadcast_operation(
            {"operation_key": operation_key},
            caller_context=owner,
            access_registry=replay_registries[index % len(replay_registries)],
        )
        assert replay == expected

    assert delivery_calls == []
    raw, record = _load_record(operation_key)
    assert raw is not None and record is not None
    assert record["delivery_state"] == "completed"


@pytest.mark.parametrize(
    "extra",
    [
        {"message": "CALLER_CONTENT_RAW_SENTINEL"},
        {"text": "CALLER_CONTENT_RAW_SENTINEL"},
        {"content": "CALLER_CONTENT_RAW_SENTINEL"},
        {"body": "CALLER_CONTENT_RAW_SENTINEL"},
        {"template": "CALLER_CONTENT_RAW_SENTINEL"},
        {"media": "CALLER_MEDIA_RAW_SENTINEL"},
        {"file": "CALLER_FILE_RAW_SENTINEL"},
        {"target": "CALLER_TARGET_RAW_SENTINEL"},
        {"chat_id": "CALLER_TARGET_RAW_SENTINEL"},
        {"recipient": "CALLER_RECIPIENT_RAW_SENTINEL"},
        {"recipients": ["CALLER_RECIPIENT_RAW_SENTINEL"]},
        {"owner": "CALLER_AUTHORITY_RAW_SENTINEL"},
        {"role": "CALLER_AUTHORITY_RAW_SENTINEL"},
        {"profile": "CALLER_AUTHORITY_RAW_SENTINEL"},
        {"scope": "CALLER_AUTHORITY_RAW_SENTINEL"},
        {"capability": "CALLER_AUTHORITY_RAW_SENTINEL"},
        {"session": "CALLER_AUTHORITY_RAW_SENTINEL"},
        {
            "metadata": {
                "nested": {"delivery_target": "CALLER_TARGET_RAW_SENTINEL"}
            }
        },
    ],
)
def test_forbidden_request_fields_reject_before_state_or_delivery(
    profile_home,
    delivery_calls,
    caplog,
    extra,
):
    registry, owner = _build_registry()
    operation_key = "NEGATIVE_OPERATION_KEY_RAW_SENTINEL"
    result = family_broadcast_operation(
        {"operation_key": operation_key, **extra},
        caller_context=owner,
        access_registry=registry,
    )

    assert result == {"success": False, "error_code": "invalid_request"}
    assert delivery_calls == []
    assert _broadcast_rows() == {}
    assert operation_key not in _public_text(result) + caplog.text
    assert "RAW_SENTINEL" not in _public_text(result) + caplog.text


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"operation_key": None},
        {"operation_key": 7},
        {"operation_key": ""},
        {"operation_key": "   "},
        {"operation_key": "x" * 4097},
    ],
)
def test_malformed_operation_key_rejects_without_side_effects(
    profile_home,
    delivery_calls,
    payload,
):
    registry, owner = _build_registry()
    assert family_broadcast_operation(
        payload,
        caller_context=owner,
        access_registry=registry,
    ) == {"success": False, "error_code": "invalid_request"}
    assert delivery_calls == []
    assert _broadcast_rows() == {}


def test_malformed_or_non_owner_context_rejects_before_persistence(
    profile_home,
    delivery_calls,
):
    registry, owner = _build_registry()
    contexts = (
        None,
        object(),
        _family_context(registry),
        _group_owner_context(owner),
        ResolvedAccessContext(
            principal_id="",
            role_id="owner",
            profile_id=owner.profile_id,
            conversation_scope=owner.conversation_scope,
            capabilities=owner.capabilities,
            delivery_target=owner.delivery_target,
        ),
    )

    for index, context in enumerate(contexts):
        result = family_broadcast_operation(
            {"operation_key": f"bad-context-{index}"},
            caller_context=context,
            access_registry=registry,
        )
        assert result == {"success": False, "error_code": "access_denied"}

    assert delivery_calls == []
    assert _broadcast_rows() == {}


def test_invalid_registry_classes_and_ambiguous_real_registry_fail_closed(
    profile_home,
    delivery_calls,
):
    registry, owner = _build_registry()
    owner_binding = registry.principal_bindings[0]
    family_binding = next(
        binding for binding in registry.principal_bindings if binding.role_id == "family"
    )
    ambiguous = AccessRegistry(
        roles=registry.roles,
        profiles=registry.profiles,
        principal_bindings=(owner_binding, family_binding, family_binding),
        shared_scope_bindings=(),
        scope_capabilities=registry.scope_capabilities,
        backend_capabilities=registry.backend_capabilities,
    )

    for index, invalid_registry in enumerate((None, object(), ambiguous)):
        result = family_broadcast_operation(
            {"operation_key": f"bad-registry-{index}"},
            caller_context=owner,
            access_registry=invalid_registry,
        )
        assert result == {
            "success": False,
            "error_code": "registry_unavailable",
        }

    assert delivery_calls == []
    assert _broadcast_rows() == {}


def test_inactive_owner_policy_cannot_authorize_a_new_claim(
    profile_home,
    delivery_calls,
):
    registry, owner = _build_registry()
    inactive_owner_registry = AccessRegistry(
        roles={
            **registry.roles,
            "owner": RolePolicy(
                "owner",
                frozenset({"broadcast"}),
                active=False,
            ),
        },
        profiles=registry.profiles,
        principal_bindings=registry.principal_bindings,
        shared_scope_bindings=registry.shared_scope_bindings,
        scope_capabilities=registry.scope_capabilities,
        backend_capabilities=registry.backend_capabilities,
    )
    inactive_owner = ResolvedAccessContext(
        principal_id=owner.principal_id,
        role_id=owner.role_id,
        profile_id=owner.profile_id,
        conversation_scope=owner.conversation_scope,
        capabilities=frozenset(),
        delivery_target=owner.delivery_target,
    )

    result = family_broadcast_operation(
        {"operation_key": "inactive-owner-policy"},
        caller_context=inactive_owner,
        access_registry=inactive_owner_registry,
    )

    assert result == {"success": False, "error_code": "access_denied"}
    assert delivery_calls == []
    assert _broadcast_rows() == {}


@pytest.mark.parametrize(
    "raw_record",
    [
        "not-json",
        "NaN",
        "{}",
        '{"schema_version":2}',
        '["unexpected-record"]',
    ],
)
def test_corrupt_or_unknown_existing_record_is_not_overwritten_or_delivered(
    profile_home,
    delivery_calls,
    raw_record,
):
    registry, owner = _build_registry()
    operation_key = "CORRUPT_RECORD_OPERATION_RAW_SENTINEL"
    _save_raw_record(operation_key, raw_record)

    result = family_broadcast_operation(
        {"operation_key": operation_key},
        caller_context=owner,
        access_registry=None,
    )

    assert result == {"success": False, "error_code": "record_invalid"}
    assert delivery_calls == []
    assert _load_record(operation_key)[0] == raw_record


def test_existing_record_is_bound_to_original_typed_owner(
    profile_home,
    delivery_calls,
):
    registry, owner = _build_registry()
    operation_key = "OWNER_BOUND_OPERATION_RAW_SENTINEL"
    first = family_broadcast_operation(
        {"operation_key": operation_key},
        caller_context=owner,
        access_registry=registry,
    )
    assert first["success"] is True
    original_raw = _load_record(operation_key)[0]
    delivery_calls.clear()

    result = family_broadcast_operation(
        {"operation_key": operation_key},
        caller_context=_forged_owner_context(owner),
        access_registry=None,
    )

    assert result == {"success": False, "error_code": "record_invalid"}
    assert delivery_calls == []
    assert _load_record(operation_key)[0] == original_raw


@pytest.mark.parametrize(
    "mutation",
    (
        "unknown_schema",
        "message_mismatch",
        "extra_field",
        "broken_ordinal",
        "duplicate_binding",
        "legacy_role_alias",
        "noncanonical_capability_order",
        "result_extra_field",
    ),
)
def test_mutated_snapshot_or_integrity_record_fails_closed(
    profile_home,
    delivery_calls,
    mutation,
):
    family_capabilities = (
        frozenset({"observe", "receive"})
        if mutation == "noncanonical_capability_order"
        else frozenset({"receive"})
    )
    registry, owner = _build_registry(family_capabilities=family_capabilities)
    operation_key = f"MUTATED_RECORD_{mutation}_RAW_SENTINEL"
    first = family_broadcast_operation(
        {"operation_key": operation_key},
        caller_context=owner,
        access_registry=registry,
    )
    assert first["success"] is True
    _, record = _load_record(operation_key)
    assert record is not None
    delivery_calls.clear()

    if mutation == "unknown_schema":
        record["schema_version"] = 2
    elif mutation == "message_mismatch":
        record["message_digest"] = "0" * 64
    elif mutation == "extra_field":
        record["unexpected"] = True
    elif mutation == "broken_ordinal":
        record["binding_snapshot"][0]["ordinal"] = 2
    elif mutation == "duplicate_binding":
        duplicate = dict(record["binding_snapshot"][0])
        duplicate["ordinal"] = 3
        record["binding_snapshot"].append(duplicate)
    elif mutation == "legacy_role_alias":
        record["binding_snapshot"][0]["resolved_context"]["role_id"] = (
            "family_standard"
        )
    elif mutation == "noncanonical_capability_order":
        capabilities = record["binding_snapshot"][0]["resolved_context"][
            "capabilities"
        ]
        assert len(capabilities) == 2
        record["binding_snapshot"][0]["resolved_context"]["capabilities"] = list(
            reversed(capabilities)
        )
    else:
        record["redacted_result"]["provider_detail"] = _PROVIDER_SECRET

    mutated_raw = json.dumps(
        record,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    _save_raw_record(operation_key, mutated_raw)

    result = family_broadcast_operation(
        {"operation_key": operation_key},
        caller_context=owner,
        access_registry=None,
    )
    assert result == {"success": False, "error_code": "record_invalid"}
    assert delivery_calls == []
    assert _load_record(operation_key)[0] == mutated_raw


def test_failed_delivery_is_durable_redacted_and_never_retried(
    profile_home,
    monkeypatch,
    caplog,
):
    registry, owner = _build_registry()
    operation_key = "FAILED_DELIVERY_OPERATION_RAW_SENTINEL"
    calls: list[str] = []

    async def failing_send(
        platform,
        pconfig,
        chat_id,
        message,
        thread_id=None,
        media_files=None,
        force_document=False,
    ):
        calls.append(_sha256(str(chat_id)))
        return {"error": _PROVIDER_SECRET}

    monkeypatch.setattr(send_message_module, "_send_to_platform", failing_send)
    failed = family_broadcast_operation(
        {"operation_key": operation_key},
        caller_context=owner,
        access_registry=registry,
    )

    assert failed == {
        "success": False,
        "replayed": False,
        "delivery_state": "failed",
        "recipient_count": 2,
        "receipts": [
            {"ordinal": 1, "status": "failed"},
            {"ordinal": 2, "status": "failed"},
        ],
        "error_code": "delivery_failed",
    }
    assert len(calls) == 2
    raw, record = _load_record(operation_key)
    assert raw is not None and record is not None
    assert record["delivery_state"] == "failed"
    assert _PROVIDER_SECRET not in raw
    calls.clear()

    replay = family_broadcast_operation(
        {"operation_key": operation_key},
        caller_context=owner,
        access_registry=None,
    )
    assert replay == {**failed, "replayed": True}
    assert calls == []
    _assert_publicly_redacted(failed, caplog.text)
    _assert_publicly_redacted(replay, caplog.text)


def test_post_claim_crash_stays_claimed_and_replay_never_redelivers(
    profile_home,
    monkeypatch,
):
    registry, owner = _build_registry()
    operation_key = "CRASHED_DELIVERY_OPERATION_RAW_SENTINEL"
    calls: list[str] = []

    async def crashing_send(
        platform,
        pconfig,
        chat_id,
        message,
        thread_id=None,
        media_files=None,
        force_document=False,
    ):
        calls.append(_sha256(str(chat_id)))
        raise KeyboardInterrupt

    monkeypatch.setattr(send_message_module, "_send_to_platform", crashing_send)
    with pytest.raises(KeyboardInterrupt):
        family_broadcast_operation(
            {"operation_key": operation_key},
            caller_context=owner,
            access_registry=registry,
        )
    assert len(calls) == 1
    raw, record = _load_record(operation_key)
    assert raw is not None and record is not None
    assert record["delivery_state"] == "claimed"
    calls.clear()

    replay = family_broadcast_operation(
        {"operation_key": operation_key},
        caller_context=owner,
        access_registry=None,
    )
    assert replay == {
        "success": True,
        "replayed": True,
        "delivery_state": "claimed",
        "recipient_count": 2,
        "receipts": [
            {"ordinal": 1, "status": "incomplete"},
            {"ordinal": 2, "status": "incomplete"},
        ],
    }
    assert calls == []


def test_spawn_contention_has_one_winner_and_one_delivery_sequence_per_round(
    profile_home,
):
    initialized = SessionDB()
    initialized.close()

    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(9)
    result_queue = ctx.Queue()
    delivery_queue = ctx.Queue()
    rounds = 20
    processes = [
        ctx.Process(
            target=_broadcast_process_worker,
            args=(
                str(profile_home),
                rounds,
                barrier,
                result_queue,
                delivery_queue,
                worker_id,
            ),
        )
        for worker_id in range(8)
    ]
    all_delivery_events: list[tuple[int, int, str, str]] = []

    try:
        for process in processes:
            process.start()

        for round_id in range(rounds):
            barrier.wait(timeout=30)
            deadline = time.monotonic() + 2.0
            outcomes = []
            for _ in processes:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(
                        f"broadcast contenders timed out in round {round_id}"
                    )
                try:
                    outcomes.append(result_queue.get(timeout=remaining))
                except queue.Empty as exc:
                    raise AssertionError(
                        f"broadcast contenders timed out in round {round_id}"
                    ) from exc

            assert all(outcome[0] == round_id for outcome in outcomes), outcomes
            assert all(outcome[3] < 2.0 for outcome in outcomes)
            results = [outcome[2] for outcome in outcomes]
            assert sum(result.get("replayed") is False for result in results) == 1
            assert all(result["recipient_count"] == 2 for result in results)
            assert all(
                [receipt["ordinal"] for receipt in result["receipts"]] == [1, 2]
                for result in results
            )
            assert all(
                f"spawn-round-{round_id}" not in _public_text(result)
                for result in results
            )

            for _ in range(2):
                all_delivery_events.append(delivery_queue.get(timeout=2))
            barrier.wait(timeout=30)
    finally:
        for process in processes:
            process.join(timeout=10)
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)

    while True:
        try:
            all_delivery_events.append(delivery_queue.get_nowait())
        except queue.Empty:
            break

    assert all(process.exitcode == 0 for process in processes)
    assert len(all_delivery_events) == rounds * 2
    for round_id in range(rounds):
        events = [event for event in all_delivery_events if event[0] == round_id]
        assert len(events) == 2
        assert {event[2] for event in events} == {
            _sha256(chat) for chat in _FAMILY_CHATS
        }

    for q in (result_queue, delivery_queue):
        q.close()
        q.join_thread()
        assert {event[3] for event in events} == {_APPROVED_MESSAGE_DIGEST}

    rows = _broadcast_rows()
    assert len(rows) == rounds
    for raw in rows.values():
        record = json.loads(raw)
        assert record["delivery_state"] == "completed"
        assert len(record["binding_snapshot"]) == 2
