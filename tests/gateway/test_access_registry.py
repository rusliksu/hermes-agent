import hashlib
import json
from dataclasses import FrozenInstanceError, fields, replace

import pytest

from gateway.access_registry import (
    AccessDeniedError,
    AccessComparisonResult,
    AccessRegistry,
    DeliveryTarget,
    ParticipantIdentity,
    PrincipalBinding,
    RedactedAuditMetadata,
    RegistryValidationError,
    ResolvedAccessContext,
    RolePolicy,
    SharedScopeBinding,
    TransportIdentity,
    compare_legacy_access_resolution,
    deserialize_resolved_access_context,
    memory_scope_from_resolved_access_context,
    serialize_resolved_access_context,
    session_scope_from_resolved_access_context,
    shared_memory_namespace_for_access_context,
)


ACCOUNT = "synthetic-account"
OWNER_CAPS = frozenset({
    "attachments",
    "authenticated_browser",
    "cron",
    "documents",
    "host_shell",
    "image_generation",
    "memory_search",
    "owner_admin",
    "public_web",
    "self_reminder",
    "session_search",
    "vision",
    "voice_generation",
})
FAMILY_CAPS = frozenset({
    "attachments",
    "delegation",
    "documents",
    "docker_terminal",
    "image_generation",
    "isolated_browser",
    "memory_search",
    "public_web",
    "self_reminder",
    "session_search",
    "vision",
    "voice_generation",
    "wolfram",
})
ROOM_CAPS = frozenset({
    "attachments",
    "documents",
    "public_web",
    "room_memory",
    "room_session_search",
    "vision",
})
BACKEND_CAPS = (
    OWNER_CAPS
    | FAMILY_CAPS
    | ROOM_CAPS
    | frozenset({"backend_only", "scope_unknown", "wolfram"})
)


def _dm_identity(
    user_id: str,
    *,
    chat_id: str | None = None,
    account: str = ACCOUNT,
    thread_id: str | None = None,
):
    return TransportIdentity(
        platform="telegram",
        account=account,
        peer_kind="dm",
        user_id=user_id,
        chat_id=user_id if chat_id is None else chat_id,
        thread_id=thread_id,
    )


def _group_identity(chat_id: str, user_id: str, *, thread_id: str | None = None):
    return TransportIdentity(
        platform="telegram",
        account=ACCOUNT,
        peer_kind="group",
        user_id=user_id,
        chat_id=chat_id,
        thread_id=thread_id,
    )


def _target(identity: TransportIdentity) -> DeliveryTarget:
    return DeliveryTarget(
        platform=identity.platform,
        account=identity.account,
        peer_kind=identity.peer_kind,
        chat_id=identity.chat_id,
        thread_id=identity.thread_id,
    )


def _registry(
    *,
    principal_bindings: tuple[PrincipalBinding, ...] | None = None,
    shared_bindings: tuple[SharedScopeBinding, ...] | None = None,
    roles: dict[str, RolePolicy] | None = None,
    profiles: frozenset[str] | None = None,
    scope_capabilities: dict[str, frozenset[str]] | None = None,
    backend_capabilities: frozenset[str] | None = None,
) -> AccessRegistry:
    principal_bindings = principal_bindings if principal_bindings is not None else _principal_bindings()
    shared_bindings = shared_bindings if shared_bindings is not None else _shared_bindings()
    roles = roles if roles is not None else {
        "owner": RolePolicy("owner", OWNER_CAPS | {"unknown_owner_cap"}),
        "family": RolePolicy("family", FAMILY_CAPS),
        "shared_room": RolePolicy("shared_room", ROOM_CAPS | {"not_backend"}),
    }
    profiles = profiles if profiles is not None else frozenset(
        ["owner-profile"]
        + [f"family-profile-{index}" for index in range(9)]
        + [f"room-profile-{index}" for index in range(2)]
    )
    scope_capabilities = scope_capabilities if scope_capabilities is not None else {
        "private": OWNER_CAPS | FAMILY_CAPS | {"not_backend"},
        "room-0": ROOM_CAPS | {"not_backend"},
        "room-1": ROOM_CAPS,
    }
    return AccessRegistry(
        roles=roles,
        profiles=profiles,
        principal_bindings=principal_bindings,
        shared_scope_bindings=shared_bindings,
        scope_capabilities=scope_capabilities,
        backend_capabilities=backend_capabilities if backend_capabilities is not None else BACKEND_CAPS,
    )


def _principal_bindings() -> tuple[PrincipalBinding, ...]:
    bindings = [
        PrincipalBinding(
            principal_id="principal-owner",
            role_id="owner",
            profile_id="owner-profile",
            transport_identity=_dm_identity("opaque-owner"),
            conversation_scope="private",
            delivery_target=_target(_dm_identity("opaque-owner")),
        )
    ]
    for index in range(9):
        user_id = f"opaque-family-{index}"
        bindings.append(
            PrincipalBinding(
                principal_id=f"principal-family-{index}",
                role_id="family",
                profile_id=f"family-profile-{index}",
                transport_identity=_dm_identity(user_id),
                conversation_scope="private",
                delivery_target=_target(_dm_identity(user_id)),
            )
        )
    return tuple(bindings)


def _shared_bindings() -> tuple[SharedScopeBinding, ...]:
    return (
        SharedScopeBinding(
            principal_id="principal-room-0",
            role_id="shared_room",
            profile_id="room-profile-0",
            room_identity=_group_identity("opaque-room-0", "ignored-member"),
            conversation_scope="room-0",
            delivery_target=_target(_group_identity("opaque-room-0", "ignored-member")),
            participant_identities=(
                ParticipantIdentity("telegram", ACCOUNT, "opaque-owner"),
                ParticipantIdentity("telegram", ACCOUNT, "opaque-family-0"),
            ),
        ),
        SharedScopeBinding(
            principal_id="principal-room-1",
            role_id="shared_room",
            profile_id="room-profile-1",
            room_identity=_group_identity("opaque-room-1", "ignored-member"),
            conversation_scope="room-1",
            delivery_target=_target(_group_identity("opaque-room-1", "ignored-member")),
            participant_identities=(
                ParticipantIdentity("telegram", ACCOUNT, "opaque-family-1"),
            ),
        ),
    )


def _topic_binding(
    *,
    principal_id: str = "principal-room-topic",
    profile_id: str = "room-profile-1",
    conversation_scope: str = "room-1",
    active: bool = True,
) -> SharedScopeBinding:
    identity = _group_identity(
        "opaque-room-0",
        "ignored-member",
        thread_id="topic-7",
    )
    return SharedScopeBinding(
        principal_id=principal_id,
        role_id="shared_room",
        profile_id=profile_id,
        room_identity=identity,
        conversation_scope=conversation_scope,
        delivery_target=_target(identity),
        participant_identities=(
            ParticipantIdentity("telegram", ACCOUNT, "opaque-family-0"),
        ),
        active=active,
    )


def test_resolved_access_context_is_frozen_six_field_contract():
    assert [field.name for field in fields(ResolvedAccessContext)] == [
        "principal_id",
        "role_id",
        "profile_id",
        "conversation_scope",
        "capabilities",
        "delivery_target",
    ]
    context = _registry().resolve(_dm_identity("opaque-owner"))
    with pytest.raises(FrozenInstanceError):
        context.role_id = "other"
    assert not hasattr(context, "user_id")
    assert not hasattr(context, "chat_id")
    assert not hasattr(context, "account")


def test_exact_telegram_dm_success_and_capability_intersection():
    registry = _registry()
    context = registry.resolve(_dm_identity("opaque-family-0"))

    assert context == ResolvedAccessContext(
        principal_id="principal-family-0",
        role_id="family",
        profile_id="family-profile-0",
        conversation_scope="private",
        capabilities=FAMILY_CAPS,
        delivery_target=_target(_dm_identity("opaque-family-0")),
    )
    assert "not_backend" not in context.capabilities
    assert "backend_only" not in context.capabilities
    assert context.delivery_target.chat_id == "opaque-family-0"


def test_principal_dm_topic_uses_derived_delivery_and_preserves_session_scope():
    registry = _registry()
    identity = _dm_identity("opaque-family-0", thread_id="42")
    root_context = registry.resolve(_dm_identity("opaque-family-0"))

    context = registry.resolve(identity)

    assert context == replace(root_context, delivery_target=_target(identity))
    assert session_scope_from_resolved_access_context(context) == {
        "profile_name": "family-profile-0",
        "source": "telegram",
        "account": ACCOUNT,
        "chat_type": "dm",
        "chat_id": "opaque-family-0",
        "thread_id": "42",
        "user_id": "opaque-family-0",
        "is_dm": True,
    }
    assert registry.validate_resolved_context(context) == context


def test_principal_dm_topics_resolve_independently_with_literal_distinct_scopes():
    registry = _registry()
    identity_alpha = _dm_identity("opaque-family-0", thread_id="topic-alpha")
    identity_beta = _dm_identity("opaque-family-0", thread_id="topic-beta")

    context_alpha = registry.resolve(identity_alpha)
    context_beta = registry.resolve(identity_beta)
    scope_alpha = session_scope_from_resolved_access_context(context_alpha)
    scope_beta = session_scope_from_resolved_access_context(context_beta)

    assert (
        registry.validate_resolved_context_for_identity(context_alpha, identity_alpha)
        == context_alpha
    )
    assert (
        registry.validate_resolved_context_for_identity(context_beta, identity_beta)
        == context_beta
    )
    assert scope_alpha["thread_id"] == "topic-alpha"
    assert scope_beta["thread_id"] == "topic-beta"
    assert scope_alpha != scope_beta
    assert context_alpha.delivery_target.thread_id == "topic-alpha"
    assert context_beta.delivery_target.thread_id == "topic-beta"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda context: replace(context, principal_id="principal-family-1"),
        lambda context: replace(context, role_id="owner"),
        lambda context: replace(context, profile_id="family-profile-1"),
        lambda context: replace(context, conversation_scope="private-alt"),
        lambda context: replace(context, capabilities=OWNER_CAPS),
    ],
)
def test_validate_resolved_context_rejects_tampered_principal_dm_topic_context(mutate):
    registry = _registry()
    context = registry.resolve(_dm_identity("opaque-family-0", thread_id="42"))

    with pytest.raises(AccessDeniedError) as exc:
        registry.validate_resolved_context(mutate(context))

    assert exc.value.reason == "resolved_access_context_mismatch"


def test_legacy_family_role_aliases_normalize_to_canonical_family():
    assert RolePolicy("family_standard", FAMILY_CAPS).role_id == "family"
    assert replace(_principal_bindings()[1], role_id="family_sandbox").role_id == "family"
    context = ResolvedAccessContext(
        principal_id="principal-family-0",
        role_id="family_standard",
        profile_id="family-profile-0",
        conversation_scope="private",
        capabilities=FAMILY_CAPS,
        delivery_target=_target(_dm_identity("opaque-family-0")),
    )
    encoded = serialize_resolved_access_context(context)
    assert context.role_id == "family"
    assert encoded["role_id"] == "family"

    legacy_registry = _registry(
        roles={"family_standard": RolePolicy("family_standard", FAMILY_CAPS)},
        principal_bindings=(replace(_principal_bindings()[1], role_id="family_standard"),),
        shared_bindings=(),
        profiles=frozenset({"family-profile-0"}),
    )
    assert legacy_registry.resolve(_dm_identity("opaque-family-0")).role_id == "family"


def test_conflicting_legacy_family_aliases_fail_closed():
    registry = _registry(
        roles={
            "family": RolePolicy("family", FAMILY_CAPS),
            "family_sandbox": RolePolicy("family_sandbox", FAMILY_CAPS | {"extra_cap"}),
        },
        principal_bindings=(_principal_bindings()[1],),
        shared_bindings=(),
        profiles=frozenset({"family-profile-0"}),
    )

    conflicts = dict(registry.validate().conflicts)
    assert conflicts["conflicting_role_alias"] == 1
    with pytest.raises(AccessDeniedError) as exc:
        registry.resolve(_dm_identity("opaque-family-0"))
    assert exc.value.reason == "registry_validation_failed"


def test_all_family_bindings_get_common_family_tool_policy():
    registry = _registry()

    contexts = [
        registry.resolve(_dm_identity(f"opaque-family-{index}"))
        for index in range(1, 9)
    ]

    assert all(context.role_id == "family" for context in contexts)
    assert all(context.capabilities == FAMILY_CAPS for context in contexts)


@pytest.mark.parametrize(
    "role_id,role_caps",
    [
        ("family", FAMILY_CAPS),
    ],
)
@pytest.mark.parametrize("missing_from", ["role", "scope", "backend"])
def test_family_wolfram_requires_role_scope_and_backend(
    role_id,
    role_caps,
    missing_from,
):
    binding = replace(
        _principal_bindings()[1],
        role_id=role_id,
    )
    roles = {
        role_id: RolePolicy(
            role_id,
            role_caps - {"wolfram"} if missing_from == "role" else role_caps,
        )
    }
    scope_capabilities = {
        "private": role_caps - {"wolfram"} if missing_from == "scope" else role_caps
    }
    backend_capabilities = BACKEND_CAPS - {"wolfram"} if missing_from == "backend" else BACKEND_CAPS
    registry = _registry(
        principal_bindings=(binding,),
        roles=roles,
        scope_capabilities=scope_capabilities,
        backend_capabilities=backend_capabilities,
        profiles=frozenset({"family-profile-0"}),
        shared_bindings=(),
    )

    context = registry.resolve(_dm_identity("opaque-family-0"))

    assert context.role_id == role_id
    assert "public_web" in context.capabilities
    assert "wolfram" not in context.capabilities


def test_shared_room_does_not_inherit_family_wolfram_policy():
    registry = _registry(
        roles={
            "family": RolePolicy("family", FAMILY_CAPS),
            "shared_room": RolePolicy("shared_room", ROOM_CAPS),
        },
        scope_capabilities={
            "private": FAMILY_CAPS,
            "room-0": ROOM_CAPS | {"wolfram"},
        },
        shared_bindings=(_shared_bindings()[0],),
        principal_bindings=(),
        profiles=frozenset({"room-profile-0"}),
    )

    context = registry.resolve(_group_identity("opaque-room-0", "opaque-owner"))

    assert context.role_id == "shared_room"
    assert "public_web" in context.capabilities
    assert "wolfram" not in context.capabilities


def test_family_role_uses_common_wolfram_capabilities():
    binding = replace(
        _principal_bindings()[1],
        role_id="family",
    )
    registry = _registry(
        principal_bindings=(binding,),
        profiles=frozenset({"family-profile-0"}),
        shared_bindings=(),
    )

    context = registry.resolve(_dm_identity("opaque-family-0"))

    assert context.capabilities == FAMILY_CAPS


def test_validate_resolved_context_accepts_current_active_context():
    registry = _registry()
    context = registry.resolve(_dm_identity("opaque-family-0"))

    assert registry.validate_resolved_context(context) == context


def test_resolved_access_context_codec_roundtrips_exact_six_field_shape():
    context = _registry().resolve(_dm_identity("opaque-family-0"))
    encoded = serialize_resolved_access_context(context)

    assert list(encoded) == [
        "principal_id",
        "role_id",
        "profile_id",
        "conversation_scope",
        "capabilities",
        "delivery_target",
    ]
    assert list(encoded["delivery_target"]) == [
        "platform",
        "account",
        "peer_kind",
        "chat_id",
        "thread_id",
    ]
    assert encoded["capabilities"] == sorted(FAMILY_CAPS)
    assert deserialize_resolved_access_context(encoded) == context


def test_session_scope_helper_maps_dm_context_to_neutral_sessiondb_shape():
    context = _registry().resolve(_dm_identity("opaque-family-0"))

    assert session_scope_from_resolved_access_context(context) == {
        "profile_name": "family-profile-0",
        "source": "telegram",
        "account": ACCOUNT,
        "chat_type": "dm",
        "chat_id": "opaque-family-0",
        "thread_id": "",
        "user_id": "opaque-family-0",
        "is_dm": True,
    }


def test_session_scope_helper_maps_room_context_without_user_authority():
    context = _registry().resolve(_group_identity("opaque-room-0", "opaque-family-0"))

    assert session_scope_from_resolved_access_context(context) == {
        "profile_name": "room-profile-0",
        "source": "telegram",
        "account": ACCOUNT,
        "chat_type": "group",
        "chat_id": "opaque-room-0",
        "thread_id": "",
        "user_id": "",
        "is_dm": False,
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda raw: raw.pop("principal_id"),
        lambda raw: raw.__setitem__("version", 1),
        lambda raw: raw.__setitem__("profile_id", ""),
        lambda raw: raw.__setitem__("role_id", 7),
        lambda raw: raw.__setitem__("capabilities", ("public_web",)),
        lambda raw: raw.__setitem__("capabilities", ["public_web", "public_web"]),
        lambda raw: raw.__setitem__("capabilities", [""]),
        lambda raw: raw.__setitem__("capabilities", ["public_web", 7]),
        lambda raw: raw["delivery_target"].pop("account"),
        lambda raw: raw["delivery_target"].__setitem__("extra", "x"),
        lambda raw: raw["delivery_target"].__setitem__("thread_id", ""),
        lambda raw: raw["delivery_target"].__setitem__("thread_id", 7),
    ],
)
def test_resolved_access_context_codec_rejects_malformed_without_raw_values(mutate):
    raw_secret = "raw-secret-profile"
    raw = serialize_resolved_access_context(_registry().resolve(_dm_identity("opaque-family-0")))
    raw["profile_id"] = raw_secret
    mutate(raw)

    with pytest.raises(ValueError) as exc:
        deserialize_resolved_access_context(raw)

    assert str(exc.value) in {
        "malformed_resolved_access_context",
        "malformed_delivery_target",
        "malformed_capabilities",
        "duplicate_capabilities",
        "invalid_thread_id",
    }
    assert raw_secret not in str(exc.value)
    assert "opaque-family-0" not in str(exc.value)
    assert ACCOUNT not in str(exc.value)


def test_session_scope_helper_rejects_malformed_context():
    with pytest.raises(ValueError, match="malformed_resolved_access_context"):
        session_scope_from_resolved_access_context({"profile_id": "family-profile-0"})


def test_memory_scope_helper_keeps_private_profiles_distinct_and_opaque():
    context_a = _registry().resolve(_dm_identity("opaque-family-0"))
    context_b = _registry().resolve(_dm_identity("opaque-family-1"))

    scope_a = memory_scope_from_resolved_access_context(context_a)
    scope_b = memory_scope_from_resolved_access_context(context_b)

    assert scope_a.startswith("memory/personal/")
    assert scope_b.startswith("memory/personal/")
    assert scope_a != scope_b
    rendered = scope_a + scope_b
    for raw in (
        "family-profile-0",
        "family-profile-1",
        "opaque-family-0",
        "opaque-family-1",
        ACCOUNT,
    ):
        assert raw not in rendered


def test_memory_scope_helper_uses_profile_and_conversation_for_personal_scope():
    context = _registry().resolve(_dm_identity("opaque-family-0"))
    same_scope = replace(
        context,
        delivery_target=_target(_dm_identity("opaque-family-0", chat_id="other-chat")),
    )
    other_conversation = replace(context, conversation_scope="private-alt")

    assert memory_scope_from_resolved_access_context(context) == (
        memory_scope_from_resolved_access_context(same_scope)
    )
    assert memory_scope_from_resolved_access_context(context) != (
        memory_scope_from_resolved_access_context(other_conversation)
    )


def test_memory_scope_helper_keeps_shared_root_and_topics_distinct_and_opaque():
    root = _registry().resolve(_group_identity("opaque-room-0", "opaque-family-0"))
    topic = replace(
        root,
        delivery_target=DeliveryTarget(
            platform=root.delivery_target.platform,
            account=root.delivery_target.account,
            peer_kind=root.delivery_target.peer_kind,
            chat_id=root.delivery_target.chat_id,
            thread_id="topic-7",
        ),
    )

    root_scope = memory_scope_from_resolved_access_context(root)
    topic_scope = memory_scope_from_resolved_access_context(topic)

    assert root_scope.startswith("memory/shared/access/")
    assert topic_scope.startswith("memory/shared/access/")
    assert root_scope != topic_scope
    rendered = root_scope + topic_scope
    for raw in (
        "room-profile-0",
        "room-profile-1",
        "room-0",
        "room-1",
        "opaque-room-0",
        "topic-7",
        ACCOUNT,
    ):
        assert raw not in rendered


@pytest.mark.parametrize("context", [None, {"profile_id": "family-profile-0"}])
def test_memory_scope_helper_rejects_missing_or_malformed_context(context):
    with pytest.raises(ValueError):
        memory_scope_from_resolved_access_context(context)


@pytest.mark.parametrize(
    "context,reason",
    [
        (None, "missing_resolved_access_context"),
        ({"profile_id": "family-profile-0"}, "malformed_resolved_access_context"),
    ],
)
def test_validate_resolved_context_rejects_missing_or_wrong_type(context, reason):
    with pytest.raises(AccessDeniedError) as exc:
        _registry().validate_resolved_context(context)

    assert exc.value.reason == reason
    rendered = json.dumps(exc.value.audit.as_dict())
    assert "family-profile-0" not in rendered


def test_validate_resolved_context_rejects_stale_or_forged_context_without_ids():
    context = _registry().resolve(_dm_identity("opaque-family-0"))
    forged = replace(context, profile_id="family-profile-1")

    with pytest.raises(AccessDeniedError) as exc:
        _registry().validate_resolved_context(forged)

    assert exc.value.reason == "resolved_access_context_mismatch"
    rendered = json.dumps(exc.value.audit.as_dict()) + str(exc.value)
    assert "principal-family-0" not in rendered
    assert "family-profile-0" not in rendered
    assert "family-profile-1" not in rendered
    assert "opaque-family-0" not in rendered
    assert ACCOUNT not in rendered


def test_validate_resolved_context_rejects_invalid_registry_without_ids():
    binding = PrincipalBinding(
        principal_id="principal-family-0",
        role_id="missing-role",
        profile_id="family-profile-0",
        transport_identity=_dm_identity("opaque-family-0"),
        conversation_scope="private",
        delivery_target=_target(_dm_identity("opaque-family-0")),
    )
    registry = _registry(principal_bindings=(binding,))
    context = ResolvedAccessContext(
        principal_id="principal-family-0",
        role_id="missing-role",
        profile_id="family-profile-0",
        conversation_scope="private",
        capabilities=frozenset(),
        delivery_target=_target(_dm_identity("opaque-family-0")),
    )

    with pytest.raises(AccessDeniedError) as exc:
        registry.validate_resolved_context(context)

    assert exc.value.reason == "registry_validation_failed"
    rendered = json.dumps(exc.value.audit.as_dict())
    assert "principal-family-0" not in rendered
    assert "family-profile-0" not in rendered
    assert "opaque-family-0" not in rendered


def test_validate_resolved_context_rejects_ambiguous_context_without_ids():
    context = _registry().resolve(_dm_identity("opaque-family-0"))
    bindings = _principal_bindings()
    registry = _registry(principal_bindings=bindings + (bindings[1],))

    with pytest.raises(AccessDeniedError) as exc:
        registry.validate_resolved_context(context)

    assert exc.value.reason == "ambiguous_resolved_access_context"
    rendered = json.dumps(exc.value.audit.as_dict()) + str(exc.value)
    assert "principal-family-0" not in rendered
    assert "family-profile-0" not in rendered
    assert "opaque-family-0" not in rendered


@pytest.mark.parametrize(
    "identity,reason",
    [
        (_dm_identity("opaque-family-0", chat_id="other-chat"), "dm_identity_mismatch"),
        (_dm_identity("unknown-user"), "missing_principal_binding"),
        (
            TransportIdentity(
                platform="telegram",
                account=ACCOUNT,
                peer_kind="dm",
                user_id=123,
                chat_id="123",
            ),
            "malformed_identity",
        ),
        (
            TransportIdentity(
                platform="discord",
                account=ACCOUNT,
                peer_kind="dm",
                user_id="opaque-family-0",
                chat_id="opaque-family-0",
            ),
            "unknown_platform",
        ),
    ],
)
def test_dm_mismatch_unknown_and_malformed_denied_without_fallback(identity, reason):
    with pytest.raises(AccessDeniedError) as exc:
        _registry().resolve(identity)

    assert exc.value.reason == reason
    rendered = json.dumps(exc.value.audit.as_dict())
    assert "unknown-user" not in rendered
    assert "opaque-family-0" not in rendered
    assert "other-chat" not in rendered


def test_validation_rejects_duplicate_unknown_role_and_unknown_profile_without_ids():
    bindings = list(_principal_bindings())
    bindings.append(bindings[1])
    bindings[2] = PrincipalBinding(
        principal_id="principal-family-1",
        role_id="missing-role",
        profile_id="missing-profile",
        transport_identity=_dm_identity("opaque-family-1"),
        conversation_scope="private",
        delivery_target=_target(_dm_identity("opaque-family-1")),
    )
    registry = _registry(principal_bindings=tuple(bindings))

    with pytest.raises(RegistryValidationError) as exc:
        registry.require_valid()

    conflicts = dict(exc.value.report.conflicts)
    assert conflicts["duplicate_principal_binding"] == 1
    assert conflicts["unknown_role"] == 1
    assert conflicts["unknown_profile"] == 1
    rendered = json.dumps(exc.value.report.as_dict()) + str(exc.value)
    assert "opaque-family-0" not in rendered
    assert "opaque-family-1" not in rendered
    assert "missing-profile" not in rendered


def test_resolve_denies_when_active_registry_validation_fails():
    bindings = list(_principal_bindings())
    bindings.append(bindings[1])
    registry = _registry(principal_bindings=tuple(bindings))

    with pytest.raises(AccessDeniedError) as exc:
        registry.resolve(_dm_identity("opaque-owner"))

    assert exc.value.reason == "registry_validation_failed"
    rendered = json.dumps(exc.value.audit.as_dict())
    assert "opaque-owner" not in rendered


def test_validation_rejects_role_key_mismatch_malformed_role_and_binding_without_crashing():
    roles = {
        "owner": object(),
        "family": RolePolicy("not-family", FAMILY_CAPS),
        "shared_room": RolePolicy("shared_room", ROOM_CAPS),
    }
    registry = _registry(
        roles=roles,
        principal_bindings=(object(),) + _principal_bindings(),
    )

    report = registry.validate()

    conflicts = dict(report.conflicts)
    assert conflicts["malformed_role"] == 1
    assert conflicts["mismatched_role_key"] == 1
    assert conflicts["malformed_principal_binding"] == 1


def test_validation_rejects_malformed_authority_and_dm_delivery_mismatch():
    binding = PrincipalBinding(
        principal_id="",
        role_id="owner",
        profile_id="owner-profile",
        transport_identity=_dm_identity("opaque-owner"),
        conversation_scope="private",
        delivery_target=_target(_dm_identity("different-owner")),
    )
    registry = _registry(principal_bindings=(binding,))

    conflicts = dict(registry.validate().conflicts)

    assert conflicts["malformed_binding_authority"] == 1
    assert conflicts["delivery_target_mismatch"] == 1


def test_validation_rejects_malformed_nested_identity_without_crashing():
    binding = PrincipalBinding(
        principal_id="principal-nested",
        role_id="owner",
        profile_id="owner-profile",
        transport_identity=object(),
        conversation_scope="private",
        delivery_target=_target(_dm_identity("opaque-owner")),
    )
    registry = _registry(principal_bindings=(binding,))

    conflicts = dict(registry.validate().conflicts)

    assert conflicts["malformed_principal_binding"] == 1
    assert conflicts["delivery_target_mismatch"] == 1


def test_validation_rejects_shared_room_delivery_mismatch_and_duplicate_member():
    binding = SharedScopeBinding(
        principal_id="principal-room-0",
        role_id="shared_room",
        profile_id="room-profile-0",
        room_identity=_group_identity("opaque-room-0", "ignored-member"),
        conversation_scope="room-0",
        delivery_target=_target(_group_identity("other-room", "ignored-member")),
        participant_identities=(
            ParticipantIdentity("telegram", ACCOUNT, "opaque-family-0"),
            ParticipantIdentity("telegram", ACCOUNT, "opaque-family-0"),
            object(),
        ),
    )
    registry = _registry(shared_bindings=(binding,))

    conflicts = dict(registry.validate().conflicts)

    assert conflicts["delivery_target_mismatch"] == 1
    assert conflicts["duplicate_shared_room_member"] == 1
    assert conflicts["malformed_shared_room_membership"] == 1


@pytest.mark.parametrize(
    "principal_id",
    ["principal-owner", "principal-family-0", "principal-room-1"],
)
def test_validation_rejects_room_principal_id_reuse_across_private_and_rooms(principal_id):
    bad_room = replace(_shared_bindings()[0], principal_id=principal_id)
    registry = _registry(shared_bindings=(bad_room,) + _shared_bindings()[1:])

    conflicts = dict(registry.validate().conflicts)

    assert conflicts["duplicate_principal_id"] == 1
    with pytest.raises(AccessDeniedError) as exc:
        registry.resolve(_group_identity("opaque-room-0", "opaque-owner"))
    assert exc.value.reason == "registry_validation_failed"


@pytest.mark.parametrize("profile_id", ["owner-profile", "family-profile-0"])
def test_validation_rejects_room_profile_reuse_across_private_and_rooms(profile_id):
    bad_room = replace(_shared_bindings()[0], profile_id=profile_id)
    registry = _registry(shared_bindings=(bad_room,) + _shared_bindings()[1:])

    conflicts = dict(registry.validate().conflicts)

    assert conflicts["duplicate_principal_profile"] == 1
    with pytest.raises(AccessDeniedError) as exc:
        registry.resolve(_group_identity("opaque-room-0", "opaque-owner"))
    assert exc.value.reason == "registry_validation_failed"


def test_rollout_shape_accepts_one_owner_nine_family_and_two_rooms():
    report = _registry().require_valid_rollout_shape()
    assert report.valid

    bindings = _principal_bindings()
    roles = [binding.role_id for binding in bindings]
    assert roles.count("owner") == 1
    assert roles.count("family") == 9


def test_rollout_shape_rejects_extra_active_private_principal_binding():
    extra = PrincipalBinding(
        principal_id="principal-extra",
        role_id="family",
        profile_id="family-profile-extra",
        transport_identity=_dm_identity("opaque-extra"),
        conversation_scope="private",
        delivery_target=_target(_dm_identity("opaque-extra")),
    )
    registry = _registry(
        principal_bindings=_principal_bindings() + (extra,),
        profiles=frozenset(
            ["owner-profile", "family-profile-extra"]
            + [f"family-profile-{index}" for index in range(9)]
            + [f"room-profile-{index}" for index in range(2)]
        ),
    )

    with pytest.raises(RegistryValidationError) as exc:
        registry.require_valid_rollout_shape()

    conflicts = dict(exc.value.report.conflicts)
    assert conflicts["active_principal_binding_count"] == 1
    assert conflicts["family_count"] == 1


def test_rollout_shape_rejects_extra_active_private_role():
    extra = PrincipalBinding(
        principal_id="principal-extra",
        role_id="shared_room",
        profile_id="family-profile-extra",
        transport_identity=_dm_identity("opaque-extra"),
        conversation_scope="private",
        delivery_target=_target(_dm_identity("opaque-extra")),
    )
    registry = _registry(
        principal_bindings=_principal_bindings() + (extra,),
        profiles=frozenset(
            ["owner-profile", "family-profile-extra"]
            + [f"family-profile-{index}" for index in range(9)]
            + [f"room-profile-{index}" for index in range(2)]
        ),
    )

    with pytest.raises(RegistryValidationError) as exc:
        registry.require_valid_rollout_shape()

    conflicts = dict(exc.value.report.conflicts)
    assert conflicts["active_principal_binding_count"] == 1
    assert conflicts["invalid_private_role_count"] == 1


def test_shared_room_requires_exact_binding_and_membership_without_role_elevation():
    registry = _registry()

    context = registry.resolve(_group_identity("opaque-room-0", "opaque-family-0"))
    assert context.principal_id == "principal-room-0"
    assert context.role_id == "shared_room"
    assert context.profile_id == "room-profile-0"
    assert context.conversation_scope == "room-0"
    assert context.capabilities == ROOM_CAPS
    assert "docker_terminal" not in context.capabilities
    assert "delegation" not in context.capabilities

    with pytest.raises(AccessDeniedError) as exc:
        registry.resolve(_group_identity("opaque-room-0", "opaque-family-1"))
    assert exc.value.reason == "participant_not_member"

    with pytest.raises(AccessDeniedError) as exc:
        registry.resolve(_group_identity("unknown-room", "opaque-family-0"))
    assert exc.value.reason == "missing_shared_scope_binding"


def test_root_shared_room_binding_resolves_member_topics_with_derived_delivery():
    registry = _registry()

    context = registry.resolve(
        _group_identity("opaque-room-0", "opaque-family-0", thread_id="topic-7")
    )

    assert context.principal_id == "principal-room-0"
    assert context.role_id == "shared_room"
    assert context.profile_id == "room-profile-0"
    assert context.conversation_scope == "room-0"
    assert context.capabilities == ROOM_CAPS
    assert context.delivery_target == DeliveryTarget(
        platform="telegram",
        account=ACCOUNT,
        peer_kind="group",
        chat_id="opaque-room-0",
        thread_id="topic-7",
    )
    assert registry.validate_resolved_context(context) == context


def test_exact_topic_shared_binding_overrides_root_binding():
    root = _shared_bindings()[0]
    exact = _topic_binding()
    registry = _registry(shared_bindings=(root, exact))

    context = registry.resolve(
        _group_identity("opaque-room-0", "opaque-family-0", thread_id="topic-7")
    )

    assert context.principal_id == "principal-room-topic"
    assert context.profile_id == "room-profile-1"
    assert context.conversation_scope == "room-1"
    assert context.delivery_target.thread_id == "topic-7"
    assert registry.validate_resolved_context(context) == context


def test_active_exact_topic_binding_wins_over_stale_disabled_exact_binding():
    root = _shared_bindings()[0]
    exact = _topic_binding()
    stale_disabled_exact = _topic_binding(
        principal_id="principal-stale-disabled-topic",
        active=False,
    )
    registry = _registry(shared_bindings=(root, stale_disabled_exact, exact))

    context = registry.resolve(
        _group_identity("opaque-room-0", "opaque-family-0", thread_id="topic-7")
    )

    assert context.principal_id == "principal-room-topic"
    assert context.profile_id == "room-profile-1"
    assert context.conversation_scope == "room-1"
    assert registry.validate_resolved_context(context) == context


def test_disabled_exact_topic_binding_denies_without_parent_fallback():
    root = _shared_bindings()[0]
    disabled_exact = _topic_binding(
        principal_id="principal-disabled-topic",
        active=False,
    )
    registry = _registry(shared_bindings=(root, disabled_exact))

    with pytest.raises(AccessDeniedError) as exc:
        registry.resolve(
            _group_identity("opaque-room-0", "opaque-family-0", thread_id="topic-7")
        )

    assert exc.value.reason == "disabled_shared_scope_binding"


def test_disabled_root_shared_binding_denies_topic_without_missing_fallback():
    disabled_root = replace(_shared_bindings()[0], active=False)
    registry = _registry(shared_bindings=(disabled_root,))

    with pytest.raises(AccessDeniedError) as exc:
        registry.resolve(
            _group_identity("opaque-room-0", "opaque-family-0", thread_id="topic-7")
        )

    assert exc.value.reason == "disabled_shared_scope_binding"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda context: replace(
            context,
            delivery_target=replace(context.delivery_target, thread_id="topic-8"),
        ),
        lambda context: replace(context, profile_id="room-profile-0"),
        lambda context: replace(context, conversation_scope="room-0"),
        lambda context: replace(context, capabilities=frozenset({"public_web"})),
    ],
)
def test_validate_resolved_context_rejects_tampered_topic_context(mutate):
    root = _shared_bindings()[0]
    exact = _topic_binding()
    registry = _registry(shared_bindings=(root, exact))
    context = registry.resolve(
        _group_identity("opaque-room-0", "opaque-family-0", thread_id="topic-7")
    )

    with pytest.raises(AccessDeniedError) as exc:
        registry.validate_resolved_context(mutate(context))

    assert exc.value.reason == "resolved_access_context_mismatch"


def test_topic_shared_namespaces_include_delivery_thread_dimension_without_raw_ids():
    registry = _registry()
    root = registry.resolve(_group_identity("opaque-room-0", "opaque-family-0"))
    topic_7 = registry.resolve(
        _group_identity("opaque-room-0", "opaque-family-0", thread_id="topic-7")
    )
    topic_8 = registry.resolve(
        _group_identity("opaque-room-0", "opaque-family-0", thread_id="topic-8")
    )

    namespaces = {
        shared_memory_namespace_for_access_context(root),
        shared_memory_namespace_for_access_context(topic_7),
        shared_memory_namespace_for_access_context(topic_8),
    }

    assert len(namespaces) == 3
    for namespace in namespaces:
        assert namespace.startswith("access/")
        assert "opaque-room-0" not in namespace
        assert "topic-" not in namespace
        assert "room-profile" not in namespace
        assert "room-0" not in namespace


def test_topic_shared_room_non_member_and_unknown_room_stay_denied():
    registry = _registry()

    with pytest.raises(AccessDeniedError) as exc:
        registry.resolve(
            _group_identity("opaque-room-0", "opaque-family-1", thread_id="topic-7")
        )
    assert exc.value.reason == "participant_not_member"

    with pytest.raises(AccessDeniedError) as exc:
        registry.resolve(
            _group_identity("unknown-room", "opaque-family-0", thread_id="topic-7")
        )
    assert exc.value.reason == "missing_shared_scope_binding"


def test_room_membership_does_not_create_private_role():
    registry = _registry()
    with pytest.raises(AccessDeniedError) as exc:
        registry.resolve(_dm_identity("room-only-participant"))
    assert exc.value.reason == "missing_principal_binding"


def test_redacted_audit_metadata_never_exposes_raw_transport_ids():
    identity = TransportIdentity(
        platform="telegram",
        account="raw-account-secret",
        peer_kind="dm",
        user_id="raw-user-secret",
        chat_id="raw-chat-secret",
        thread_id="raw-thread-secret",
    )
    metadata = RedactedAuditMetadata.from_transport("deny", identity)
    rendered = json.dumps(metadata.as_dict())

    assert metadata.platform == "telegram"
    assert metadata.peer_kind == "dm"
    assert "raw-account-secret" not in rendered
    assert "raw-user-secret" not in rendered
    assert "raw-chat-secret" not in rendered
    assert "raw-thread-secret" not in rendered
    assert metadata.account_ref
    assert metadata.user_ref
    assert metadata.chat_ref
    for raw_value in (
        "raw-account-secret",
        "raw-user-secret",
        "raw-chat-secret",
        "raw-thread-secret",
    ):
        sha_prefix = hashlib.sha256(raw_value.encode("utf-8")).hexdigest()[:12]
        assert sha_prefix not in rendered


def test_redacted_audit_metadata_sanitizes_malformed_platform_and_peer_kind():
    identity = TransportIdentity(
        platform="telegram:raw-platform-secret",
        account="raw-account-secret",
        peer_kind="group:raw-peer-secret",
        user_id="raw-user-secret",
        chat_id="raw-chat-secret",
    )
    metadata = RedactedAuditMetadata.from_transport("deny", identity)
    rendered = json.dumps(metadata.as_dict())

    assert metadata.platform == "unknown"
    assert metadata.peer_kind == "unknown"
    assert "raw-platform-secret" not in rendered
    assert "raw-peer-secret" not in rendered


def test_compare_mode_matching_allow_and_profile():
    result = compare_legacy_access_resolution(
        legacy_allowed=True,
        legacy_profile_id="family-profile-0",
        registry=_registry(),
        identity=_dm_identity("opaque-family-0"),
    )

    assert result.legacy_outcome == "allow"
    assert result.resolved_outcome == "allow"
    assert result.outcome_agrees is True
    assert result.profile_agrees is True
    assert result.legacy_profile == "explicit"
    assert result.comparison_reason == "profiles_match"


def test_compare_mode_reports_wrong_profile_without_raw_profile_ids():
    result = compare_legacy_access_resolution(
        legacy_allowed=True,
        legacy_profile_id="family-profile-1",
        registry=_registry(),
        identity=_dm_identity("opaque-family-0"),
    )

    assert result.outcome_agrees is True
    assert result.profile_agrees is False
    assert result.comparison_reason == "profile_mismatch"
    rendered = json.dumps(result.as_dict())
    assert "family-profile-0" not in rendered
    assert "family-profile-1" not in rendered


@pytest.mark.parametrize("legacy_profile_id", [None, "", "default"])
def test_compare_mode_legacy_allow_with_implicit_profile_is_mismatch(legacy_profile_id):
    result = compare_legacy_access_resolution(
        legacy_allowed=True,
        legacy_profile_id=legacy_profile_id,
        registry=_registry(),
        identity=_dm_identity("opaque-owner"),
    )

    assert result.legacy_outcome == "allow"
    assert result.resolved_outcome == "allow"
    assert result.outcome_agrees is True
    assert result.profile_agrees is False
    assert result.legacy_profile == "implicit_fallback"
    assert result.comparison_reason == "legacy_implicit_profile"


def test_compare_mode_legacy_allow_new_deny():
    result = compare_legacy_access_resolution(
        legacy_allowed=True,
        legacy_profile_id="owner-profile",
        registry=_registry(),
        identity=_dm_identity("unknown-principal"),
    )

    assert result.legacy_outcome == "allow"
    assert result.resolved_outcome == "deny"
    assert result.outcome_agrees is False
    assert result.profile_agrees is None
    assert result.resolved_reason == "missing_principal_binding"
    assert result.comparison_reason == "outcome_mismatch"


def test_compare_mode_both_deny():
    result = compare_legacy_access_resolution(
        legacy_allowed=False,
        legacy_profile_id=None,
        registry=_registry(),
        identity=_dm_identity("unknown-principal"),
    )

    assert result.legacy_outcome == "deny"
    assert result.resolved_outcome == "deny"
    assert result.outcome_agrees is True
    assert result.profile_agrees is None
    assert result.legacy_profile == "not_applicable"
    assert result.resolved_reason == "missing_principal_binding"
    assert result.comparison_reason == "outcomes_match"


def test_compare_mode_invalid_registry_is_expected_new_denial():
    bindings = _principal_bindings()
    registry = _registry(principal_bindings=bindings + (bindings[0],))

    result = compare_legacy_access_resolution(
        legacy_allowed=True,
        legacy_profile_id="owner-profile",
        registry=registry,
        identity=_dm_identity("opaque-owner"),
    )

    assert result.legacy_outcome == "allow"
    assert result.resolved_outcome == "deny"
    assert result.outcome_agrees is False
    assert result.resolved_reason == "registry_validation_failed"
    assert result.comparison_reason == "outcome_mismatch"


def test_compare_mode_result_is_frozen():
    result = compare_legacy_access_resolution(
        legacy_allowed=False,
        legacy_profile_id=None,
        registry=_registry(),
        identity=_dm_identity("unknown-principal"),
    )

    assert isinstance(result, AccessComparisonResult)
    with pytest.raises(FrozenInstanceError):
        result.resolved_reason = "changed"


def test_compare_mode_as_dict_is_redacted_and_categorical():
    identity = TransportIdentity(
        platform="telegram",
        account="raw-account-secret",
        peer_kind="dm",
        user_id="raw-user-secret",
        chat_id="raw-user-secret",
    )
    result = compare_legacy_access_resolution(
        legacy_allowed=True,
        legacy_profile_id="raw-legacy-profile-secret",
        registry=_registry(),
        identity=identity,
    )

    rendered = json.dumps(result.as_dict(), sort_keys=True)
    assert result.audit.event == "compare"
    for raw_value in (
        "raw-account-secret",
        "raw-user-secret",
        "raw-legacy-profile-secret",
        "owner-profile",
    ):
        assert raw_value not in rendered
        sha_prefix = hashlib.sha256(raw_value.encode("utf-8")).hexdigest()[:12]
        assert sha_prefix not in rendered
    assert result.as_dict()["audit"]["account_ref"] == "present"
