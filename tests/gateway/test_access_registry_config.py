import pytest

from gateway.access_registry import (
    AccessRegistry,
    DeliveryTarget,
    PrincipalBinding,
    RolePolicy,
    SharedScopeBinding,
    TransportIdentity,
)
from gateway.config import AccessRegistryConfigError, GatewayConfig, Platform
from gateway.run import GatewayRunner
from gateway.session import SessionSource


SENTINEL_USER = "RAW_SENTINEL_USER_123"
SENTINEL_CHAT = "RAW_SENTINEL_CHAT_456"
SENTINEL_PROFILE = "RAW_SENTINEL_PROFILE_789"


def _identity_dict(*, user_id=SENTINEL_USER, chat_id=SENTINEL_USER, peer_kind="dm"):
    data = {
        "platform": "telegram",
        "account": "bot-a",
        "peer_kind": peer_kind,
        "chat_id": chat_id,
    }
    if user_id is not None:
        data["user_id"] = user_id
    return data


def _target_dict(*, chat_id=SENTINEL_USER, peer_kind="dm"):
    return {
        "platform": "telegram",
        "account": "bot-a",
        "peer_kind": peer_kind,
        "chat_id": chat_id,
    }


def _minimal_dm_registry():
    return {
        "roles": {
            "family": {
                "capabilities": ["memory_search", "public_web"],
                "active": True,
            },
        },
        "profiles": [SENTINEL_PROFILE],
        "scope_capabilities": {
            "private": ["memory_search", "public_web"],
        },
        "backend_capabilities": ["memory_search", "public_web"],
        "principal_bindings": [
            {
                "principal_id": "principal-family",
                "role_id": "family",
                "profile_id": SENTINEL_PROFILE,
                "transport_identity": _identity_dict(),
                "conversation_scope": "private",
                "delivery_target": _target_dict(),
                "active": True,
            },
        ],
        "shared_scope_bindings": [],
    }


def _shared_room_registry():
    return {
        "roles": {
            "shared_room": {
                "capabilities": ["room_memory", "public_web"],
            },
        },
        "profiles": ["room-profile"],
        "scope_capabilities": {
            "room": ["room_memory", "public_web"],
        },
        "backend_capabilities": ["room_memory", "public_web"],
        "principal_bindings": [],
        "shared_scope_bindings": [
            {
                "principal_id": "principal-room",
                "role_id": "shared_room",
                "profile_id": "room-profile",
                "room_identity": _identity_dict(
                    user_id=None,
                    chat_id=SENTINEL_CHAT,
                    peer_kind="group",
                ),
                "conversation_scope": "room",
                "delivery_target": _target_dict(
                    chat_id=SENTINEL_CHAT,
                    peer_kind="group",
                ),
                "participant_identities": [
                    {
                        "platform": "telegram",
                        "account": "bot-a",
                        "user_id": SENTINEL_USER,
                    },
                ],
            },
        ],
    }


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(
        "tools.tirith_security.ensure_installed",
        lambda *a, **k: None,
    )


def test_access_registry_absent_is_none_and_not_exported():
    config = GatewayConfig.from_dict({})

    assert config.access_registry is None
    assert "access_registry" not in config.to_dict()


def test_minimal_dm_access_registry_parses_to_immutable_types_and_runner_uses_config():
    config = GatewayConfig.from_dict({"access_registry": _minimal_dm_registry()})

    registry = config.access_registry
    assert isinstance(registry, AccessRegistry)
    assert isinstance(registry.roles["family"], RolePolicy)
    assert isinstance(registry.principal_bindings[0], PrincipalBinding)
    assert isinstance(
        registry.principal_bindings[0].transport_identity,
        TransportIdentity,
    )
    assert isinstance(registry.principal_bindings[0].delivery_target, DeliveryTarget)
    assert isinstance(registry.roles["family"].capabilities, frozenset)
    assert isinstance(registry.profiles, frozenset)
    assert isinstance(registry.principal_bindings, tuple)

    runner = GatewayRunner(config=config)
    assert runner.access_registry is registry

    explicit = GatewayConfig.from_dict(
        {"access_registry": _shared_room_registry()}
    ).access_registry
    explicit_runner = GatewayRunner(config=config, access_registry=explicit)
    assert explicit_runner.access_registry is explicit


def test_family_standard_wolfram_binding_capability_parses_and_intersects_scope_backend():
    raw = _minimal_dm_registry()
    raw["roles"] = {
        "family_standard": {
            "capabilities": ["public_web"],
        },
    }
    raw["principal_bindings"][0]["role_id"] = "family_standard"
    raw["scope_capabilities"]["private"] = [
        "public_web",
        "wolfram",
        "not_backend",
    ]
    raw["backend_capabilities"] = ["public_web", "wolfram", "backend_only"]
    raw["principal_bindings"][0]["capabilities"] = [
        "wolfram",
        "not_backend",
        "scope_unknown",
    ]

    registry = GatewayConfig.from_dict({"access_registry": raw}).access_registry
    context = registry.resolve(
        TransportIdentity(
            platform="telegram",
            account="bot-a",
            peer_kind="dm",
            user_id=SENTINEL_USER,
            chat_id=SENTINEL_USER,
        )
    )

    assert registry.principal_bindings[0].capabilities == frozenset(
        {"wolfram", "not_backend", "scope_unknown"}
    )
    assert context.capabilities == frozenset({"public_web", "wolfram"})


def test_shared_room_public_web_registry_resolves_to_configured_telegram_profile():
    registry = GatewayConfig.from_dict(
        {"access_registry": _shared_room_registry()}
    ).access_registry
    context = registry.resolve(
        TransportIdentity(
            platform="telegram",
            account="bot-a",
            peer_kind="group",
            user_id=SENTINEL_USER,
            chat_id=SENTINEL_CHAT,
        )
    )
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id=SENTINEL_CHAT,
        chat_type="group",
        user_id=SENTINEL_USER,
    )
    source.resolved_access_context = context

    configured_toolsets = ["memory", "web", "vision", "terminal", "delegation"]
    toolsets, expected_tools = GatewayRunner._shared_tool_profile_for_source(
        source,
        configured_toolsets=configured_toolsets,
    )
    from model_tools import get_tool_definitions

    runtime_tool_names = frozenset(
        definition["function"]["name"]
        for definition in get_tool_definitions(
            enabled_toolsets=toolsets,
            quiet_mode=True,
        )
    )

    assert isinstance(registry.shared_scope_bindings[0], SharedScopeBinding)
    assert toolsets == sorted(configured_toolsets)
    assert expected_tools == runtime_tool_names
    assert "memory" in expected_tools


@pytest.mark.parametrize(
    "mutate,exc_type",
    [
        (lambda raw: raw.update({"roles": []}), AccessRegistryConfigError),
        (
            lambda raw: raw["principal_bindings"][0]["transport_identity"].pop(
                "user_id"
            ),
            AccessRegistryConfigError,
        ),
        (
            lambda raw: raw["principal_bindings"][0].update({"active": ["true"]}),
            AccessRegistryConfigError,
        ),
        (
            lambda raw: raw["principal_bindings"][0].update(
                {"capabilities": "wolfram"}
            ),
            AccessRegistryConfigError,
        ),
        (
            lambda raw: raw["principal_bindings"][0].update(
                {"role_id": "missing-role"}
            ),
            AccessRegistryConfigError,
        ),
        (
            lambda raw: raw["principal_bindings"].append(
                dict(raw["principal_bindings"][0])
            ),
            AccessRegistryConfigError,
        ),
        (
            lambda raw: raw["principal_bindings"][0].update(
                {"RAW_SENTINEL_UNKNOWN_KEY": True}
            ),
            AccessRegistryConfigError,
        ),
    ],
)
def test_malformed_access_registry_errors_are_sanitized(mutate, exc_type):
    raw = _minimal_dm_registry()
    mutate(raw)

    with pytest.raises(exc_type) as caught:
        GatewayConfig.from_dict({"access_registry": raw})

    message = str(caught.value)
    assert "access_registry" in message
    assert SENTINEL_USER not in message
    assert SENTINEL_CHAT not in message
    assert SENTINEL_PROFILE not in message
    assert "RAW_SENTINEL_UNKNOWN_KEY" not in message
    assert "missing-role" not in message
