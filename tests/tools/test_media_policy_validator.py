"""Dry-run validation for profile-scoped media provider policy."""

import pytest

from gateway.access_registry import DeliveryTarget, ResolvedAccessContext
from tools.media_provider_routing import (
    dry_run_media_policy,
    parse_media_provider_policy,
    validate_media_policy_config,
)


_ALL_MEDIA_CAPABILITIES = frozenset(
    {"image_generation", "attachments", "voice_generation"}
)


def _context(
    *,
    role_id: str = "family_standard",
    principal_id: str = "principal-family",
    capabilities: frozenset[str] = _ALL_MEDIA_CAPABILITIES,
    peer_kind: str = "dm",
) -> ResolvedAccessContext:
    return ResolvedAccessContext(
        principal_id=principal_id,
        role_id=role_id,
        profile_id=f"profile-{principal_id}",
        conversation_scope="shared:synthetic" if peer_kind != "dm" else "private",
        capabilities=capabilities,
        delivery_target=DeliveryTarget(
            platform="telegram",
            account="synthetic-account",
            peer_kind=peer_kind,
            chat_id="synthetic-chat",
            thread_id="topic-1" if peer_kind != "dm" else None,
        ),
    )


def _configured_policy() -> dict:
    return {
        "image_gen": {
            "fallbacks": {"image_generation": ["openai-codex", "fal"]},
        },
        "stt": {
            "fallbacks": ["local", "mistral"],
            "secret_references": {"mistral": "profile://stt/mistral"},
        },
        "tts": {
            "fallbacks": {
                "tts": ["edge", "openai"],
                "secret_references": {"openai": "profile://tts/openai"},
            },
        },
    }


def test_dry_run_returns_provider_order_and_redacts_secret_references():
    report = dry_run_media_policy(
        _configured_policy(),
        context=_context(),
        known_principal_ids={"principal-family"},
    )

    assert report["schema"] == "media-policy-dry-run/v1"
    assert report["valid"] is True
    assert report["mode"] == "fallback"
    assert report["context"]["status"] == "valid"
    assert report["operations"]["image_generation"]["provider_order"] == [
        "openai-codex",
        "fal",
    ]
    assert report["operations"]["stt"]["secret_reference_status"] == {
        "mistral": "configured"
    }
    assert report["operations"]["tts"]["providers"] == [
        {"provider_id": "edge", "status": "ready"},
        {"provider_id": "openai", "status": "configured"},
    ]
    assert "profile://stt/mistral" not in repr(report)
    assert "profile://tts/openai" not in repr(report)


def test_missing_fallbacks_preserve_legacy_provider_choices():
    raw = {"stt": {"provider": "local"}, "tts": {"provider": "edge"}}
    result = validate_media_policy_config(raw)

    assert result.valid is True
    assert result.policy is not None
    assert result.policy.provider_order["stt"] == ("local",)
    assert result.policy.provider_order["tts"] == ("edge",)
    assert result.report["mode"] == "legacy"
    assert result.report["operations"]["stt"]["mode"] == "legacy"
    assert result.report["operations"]["tts"]["provider_order"] == ["edge"]


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ({"image_gen": {"fallbacks": ["unknown-image"]}}, "unknown_provider"),
        ({"stt": {"fallbacks": {"stt": "local"}}}, "invalid_provider_order"),
        (
            {
                "tts": {
                    "fallbacks": ["openai"],
                    "secret_references": {"openai": ""},
                }
            },
            "invalid_secret_reference",
        ),
    ],
)
def test_invalid_policy_is_reported_without_constructing_a_policy(raw, code):
    result = validate_media_policy_config(raw)

    assert result.valid is False
    assert result.policy is None
    assert any(item["code"] == code for item in result.report["diagnostics"])
    with pytest.raises(ValueError):
        parse_media_provider_policy(raw)


@pytest.mark.parametrize(
    ("role_id", "peer_kind"),
    [
        ("owner", "dm"),
        ("family_standard", "dm"),
        ("family_sandbox", "dm"),
        ("shared_room", "group"),
    ],
)
def test_known_principal_matrix_is_accepted(role_id, peer_kind):
    context = _context(
        role_id=role_id,
        principal_id=f"principal-{role_id}",
        peer_kind=peer_kind,
    )
    report = dry_run_media_policy(
        _configured_policy(),
        context=context,
        known_principal_ids={f"principal-{role_id}"},
    )

    assert report["valid"] is True
    assert report["context"]["status"] == "valid"
    assert report["operations"]["stt"]["capability_status"] == "available"


def test_unknown_identity_fails_closed_without_owner_fallback():
    report = dry_run_media_policy(
        _configured_policy(),
        context=_context(principal_id="principal-unknown"),
        known_principal_ids={"principal-family"},
    )

    assert report["valid"] is False
    assert report["context"]["status"] == "invalid"
    assert any(item["code"] == "unknown_identity" for item in report["diagnostics"])
    assert all(
        provider["provider_id"] != "owner"
        for details in report["operations"].values()
        for provider in details["providers"]
    )


def test_capability_status_is_redacted_and_missing_capability_does_not_rewrite_policy():
    report = dry_run_media_policy(
        _configured_policy(),
        context=_context(capabilities=frozenset({"attachments"})),
        known_principal_ids={"principal-family"},
    )

    assert report["valid"] is True
    assert report["operations"]["stt"]["capability_status"] == "available"
    assert report["operations"]["image_generation"]["capability_status"] == "unavailable"
    assert report["operations"]["tts"]["capability_status"] == "unavailable"
    assert report["operations"]["tts"]["providers"][1]["status"] == (
        "capability_unavailable"
    )
