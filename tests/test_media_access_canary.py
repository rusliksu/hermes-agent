"""Synthetic staging canary for access, media and dashboard boundaries."""

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from gateway.access_registry import (
    AccessDeniedError,
    AccessRegistry,
    DeliveryTarget,
    ParticipantIdentity,
    PrincipalBinding,
    RolePolicy,
    SharedScopeBinding,
    TransportIdentity,
)
from tools.media_provider_routing import dry_run_media_policy
from utils import fast_safe_load


ROOT = Path(__file__).resolve().parents[1]
BASE_COMMIT = "a4096896ed92d1edb3dd02e62876dc0fc1ce140a"
POLICY_FIXTURE = ROOT / "tests" / "fixtures" / "access_policy_matrix.json"
MEDIA_FIXTURE = ROOT / "tests" / "fixtures" / "media-provider-policy-canary.yaml"

PRIVATE_CAPS = frozenset({
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
OWNER_CAPS = PRIVATE_CAPS | frozenset({"cron", "host_shell", "owner_admin"})
SANDBOX_CAPS = PRIVATE_CAPS
ROOM_CAPS = frozenset({"attachments", "documents", "public_web", "room_memory", "room_session_search", "vision"})


def _fixture() -> dict:
    return json.loads(POLICY_FIXTURE.read_text(encoding="utf-8"))


def _transport_id(principal_id: str) -> str:
    return f"transport-{principal_id}"


def _identity(principal_id: str) -> TransportIdentity:
    value = _transport_id(principal_id)
    return TransportIdentity(
        platform="telegram",
        account="synthetic-bot",
        peer_kind="dm",
        user_id=value,
        chat_id=value,
    )


def _target(identity: TransportIdentity) -> DeliveryTarget:
    return DeliveryTarget(
        platform=identity.platform,
        account=identity.account,
        peer_kind=identity.peer_kind,
        chat_id=identity.chat_id,
        thread_id=identity.thread_id,
    )


def _registry() -> AccessRegistry:
    fixture = _fixture()
    roles = {
        "owner": RolePolicy("owner", OWNER_CAPS),
        "family_standard": RolePolicy("family_standard", PRIVATE_CAPS),
        "family_sandbox": RolePolicy("family_sandbox", SANDBOX_CAPS),
        "shared_room": RolePolicy("shared_room", ROOM_CAPS),
    }
    profiles = {row["profile_id"] for row in fixture["principals"]}
    profiles.update(row["profile_id"] for row in fixture["rooms"])
    principal_bindings = []
    scope_capabilities = {}
    role_caps = {
        "owner": OWNER_CAPS,
        "family_standard": PRIVATE_CAPS,
        "family_sandbox": SANDBOX_CAPS,
    }
    for row in fixture["principals"]:
        identity = _identity(row["principal_id"])
        scope = f"private:{row['principal_id']}"
        principal_bindings.append(
            PrincipalBinding(
                principal_id=row["principal_id"],
                role_id=row["role_id"],
                profile_id=row["profile_id"],
                transport_identity=identity,
                conversation_scope=scope,
                delivery_target=_target(identity),
            )
        )
        scope_capabilities[scope] = role_caps[row["role_id"]]

    shared_bindings = []
    for row in fixture["rooms"]:
        room_identity = TransportIdentity(
            platform="telegram",
            account="synthetic-bot",
            peer_kind="group",
            user_id=_transport_id(row["members"][0]),
            chat_id=f"chat-{row['scope_id']}",
        )
        scope = f"shared:{row['scope_id']}"
        shared_bindings.append(
            SharedScopeBinding(
                principal_id=f"binding-{row['scope_id']}",
                role_id="shared_room",
                profile_id=row["profile_id"],
                room_identity=room_identity,
                conversation_scope=scope,
                delivery_target=_target(room_identity),
                participant_identities=tuple(
                    ParticipantIdentity("telegram", "synthetic-bot", _transport_id(member))
                    for member in row["members"]
                ),
            )
        )
        scope_capabilities[scope] = ROOM_CAPS

    return AccessRegistry(
        roles=roles,
        profiles=frozenset(profiles),
        principal_bindings=tuple(principal_bindings),
        shared_scope_bindings=tuple(shared_bindings),
        scope_capabilities=scope_capabilities,
        backend_capabilities=OWNER_CAPS | SANDBOX_CAPS | ROOM_CAPS,
    )


def test_synthetic_access_matrix_is_fail_closed():
    registry = _registry()
    assert registry.require_valid_rollout_shape().valid

    for row in _fixture()["principals"]:
        context = registry.resolve(_identity(row["principal_id"]))
        assert context.profile_id == row["profile_id"]
        assert context.role_id == row["role_id"]
        expected_caps = (
            OWNER_CAPS
            if context.role_id == "owner"
            else PRIVATE_CAPS
        )
        assert context.capabilities == expected_caps

    room = next(row for row in _fixture()["rooms"] if row["scope_id"] == "room-drafts")
    room_context = registry.resolve(
        TransportIdentity(
            platform="telegram",
            account="synthetic-bot",
            peer_kind="group",
            user_id=_transport_id("principal-yulia"),
            chat_id="chat-room-drafts",
        )
    )
    assert room_context.profile_id == room["profile_id"]
    assert room_context.role_id == "shared_room"

    with pytest.raises(AccessDeniedError, match="missing_principal_binding"):
        registry.resolve(_identity("principal-unknown"))
    with pytest.raises(AccessDeniedError, match="dm_identity_mismatch"):
        registry.resolve(
            TransportIdentity(
                platform="telegram",
                account="synthetic-bot",
                peer_kind="dm",
                user_id="transport-principal-owner",
                chat_id="guessed-foreign-chat",
            )
        )
    with pytest.raises(AccessDeniedError):
        registry.resolve_exact_profile_context("profile-not-registered")


def test_media_canary_uses_configured_order_without_secret_values():
    config = fast_safe_load(MEDIA_FIXTURE.read_text(encoding="utf-8")) or {}
    report = dry_run_media_policy(config)
    assert report["valid"] is True
    assert report["operations"]["image_generation"]["provider_order"] == [
        "openai-codex",
        "fal",
        "openrouter",
    ]
    assert report["operations"]["stt"]["provider_order"] == [
        "local",
        "mistral",
        "openai",
        "elevenlabs",
    ]
    assert report["operations"]["tts"]["provider_order"] == [
        "edge",
        "openai",
        "elevenlabs",
    ]
    assert "profile://synthetic" not in json.dumps(report, sort_keys=True)


def test_dashboard_access_route_is_registered_and_redacted(monkeypatch, tmp_path):
    pytest.importorskip("starlette.testclient")
    from hermes_cli.web_server import _SESSION_HEADER_NAME, _SESSION_TOKEN, app
    from starlette.testclient import TestClient

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(
        "gateway.config.load_gateway_config",
        lambda: SimpleNamespace(access_registry=None),
    )
    client = TestClient(app)
    client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    response = client.get("/api/access/users")
    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is False
    encoded = json.dumps(payload)
    assert '"transport_identity":' not in encoded
    assert '"delivery_target":' not in encoded


def test_candidate_manifest_is_clean_and_sha256_addressable():
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout == ""
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    names = subprocess.run(
        ["git", "diff", "--name-only", f"{BASE_COMMIT}...{head}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert names
    forbidden = (".env", "credentials", "auth.json", ".pem", "private-key")
    assert not any(any(token in name.lower() for token in forbidden) for name in names)
    manifest = {
        "base": BASE_COMMIT,
        "head": head,
        "files": [
            {"path": name, "sha256": hashlib.sha256((ROOT / name).read_bytes()).hexdigest()}
            for name in names
        ],
    }
    assert all(len(item["sha256"]) == 64 for item in manifest["files"])
    assert hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
