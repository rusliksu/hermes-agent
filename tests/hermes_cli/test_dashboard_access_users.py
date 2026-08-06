"""Redacted dashboard view tests for the configured access registry."""

import json
from types import SimpleNamespace

import pytest

from gateway.access_registry import (
    AccessRegistry,
    DeliveryTarget,
    PrincipalBinding,
    RolePolicy,
    TransportIdentity,
)


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
        roles={
            "family": RolePolicy(
                "family",
                frozenset({"memory_read", "public_web"}),
            )
        },
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
        scope_capabilities={
            "private:principal-42": frozenset({"memory_read", "public_web"})
        },
        backend_capabilities=frozenset({"memory_read", "public_web"}),
    )


def _client():
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")
    from hermes_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

    client = TestClient(app)
    client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return client


def _keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def test_access_users_returns_redacted_role_profile_health(
    _isolate_hermes_home, monkeypatch
):
    import hermes_cli.profiles as profiles_mod

    monkeypatch.setattr(
        "gateway.config.load_gateway_config",
        lambda: SimpleNamespace(access_registry=_registry()),
    )
    monkeypatch.setattr(
        profiles_mod,
        "list_profiles",
        lambda: [SimpleNamespace(name="family-42", gateway_running=True)],
    )

    response = _client().get("/api/access/users")

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["validation"]["verdict"] == "pass"
    assert body["rooms"] == []
    assert len(body["users"]) == 1
    user = body["users"][0]
    assert user["principal_id"] == "principal-42"
    assert user["role_id"] == "family"
    assert user["profile_id"] == "family-42"
    assert user["effective_capabilities"] == ["memory_read", "public_web"]
    assert user["profile_health"] == {
        "registered": True,
        "directory_present": True,
        "gateway_running": True,
    }
    assert user["isolation"]["status"] == "healthy"
    assert user["isolation"]["context_contract"] == "six_fields"
    assert user["isolation"]["transport_ids_redacted"] is True

    keys = set(_keys(body))
    assert "transport_identity" not in keys
    assert "delivery_target" not in keys
    assert "chat_id" not in keys
    assert "user_id" not in keys
    assert "filesystem_path" not in keys
    assert "chat-42" not in json.dumps(body)
    assert "main-bot" not in json.dumps(body)


def test_access_users_is_disabled_without_registry(_isolate_hermes_home, monkeypatch):
    monkeypatch.setattr(
        "gateway.config.load_gateway_config",
        lambda: SimpleNamespace(access_registry=None),
    )

    response = _client().get("/api/access/users")

    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert response.json()["users"] == []
    assert response.json()["rooms"] == []
