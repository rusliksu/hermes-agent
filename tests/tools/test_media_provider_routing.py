"""Behavioral contracts for isolated media-provider fallback routing."""

from __future__ import annotations

from dataclasses import fields, replace

import pytest

from gateway.access_registry import (
    DeliveryTarget,
    ResolvedAccessContext,
    canonical_access_context_fingerprint,
)
from tools.media_provider_routing import (
    MediaProviderError,
    MediaProviderExecutor,
    MediaProviderPolicy,
    MediaProviderResolver,
    MediaResult,
)


def _context(
    *,
    role_id: str = "family",
    capabilities: frozenset[str] = frozenset({"voice_generation"}),
) -> ResolvedAccessContext:
    return ResolvedAccessContext(
        principal_id="synthetic-principal",
        role_id=role_id,
        profile_id="synthetic-profile",
        conversation_scope="synthetic-scope",
        capabilities=capabilities,
        delivery_target=DeliveryTarget(
            platform="telegram",
            account="synthetic-account",
            peer_kind="dm",
            chat_id="synthetic-chat",
        ),
    )


def _policy(
    providers: tuple[str, ...],
    *,
    secret_references: dict[str, str] | None = None,
    secret_required: frozenset[str] = frozenset(),
) -> MediaProviderPolicy:
    return MediaProviderPolicy(
        provider_order={"tts": providers},
        required_capabilities={"tts": "voice_generation"},
        secret_references=secret_references or {},
        secret_required=secret_required,
    )


def test_resolver_requires_the_six_field_validated_access_context():
    assert tuple(field.name for field in fields(ResolvedAccessContext)) == (
        "principal_id",
        "role_id",
        "profile_id",
        "conversation_scope",
        "capabilities",
        "delivery_target",
    )

    resolver = MediaProviderResolver()
    policy = _policy(("primary",))
    assert resolver.resolve(_context(), "tts", policy) == ("primary",)

    malformed = replace(_context(), principal_id="")
    with pytest.raises(MediaProviderError, match="invalid media access context") as exc_info:
        resolver.resolve(malformed, "tts", policy)
    assert exc_info.value.error_class == "invalid_context"


def test_policy_keeps_a_deterministic_immutable_order_and_legacy_primary():
    source = {"tts": ("second", "first", "second", "third")}
    policy = MediaProviderPolicy(
        provider_order=source,
        required_capabilities={"tts": "voice_generation"},
        secret_references={},
        secret_required=frozenset(),
    )
    source["tts"] = ("changed",)

    assert MediaProviderResolver().resolve(_context(), "tts", policy) == (
        "second",
        "first",
        "third",
    )
    with pytest.raises(TypeError):
        policy.provider_order["tts"] = ("changed",)  # type: ignore[index]

    legacy = MediaProviderPolicy.legacy({"tts": "only-provider"})
    assert legacy.provider_order["tts"] == ("only-provider",)


def test_resolver_filters_missing_capability_and_secret_reference():
    policy = _policy(
        ("needs-secret", "ready"),
        secret_required=frozenset({"needs-secret"}),
    )
    resolver = MediaProviderResolver()

    assert resolver.resolve(_context(capabilities=frozenset()), "tts", policy) == ()
    assert resolver.resolve(_context(), "tts", policy) == ("ready",)


def test_executor_never_uses_an_owner_provider_fallback():
    owner_calls: list[str] = []

    def owner_handler(_context, _input_handle, _secret_handle):
        owner_calls.append("owner")
        return MediaResult(text="owner result")

    executor = MediaProviderExecutor({"owner-provider": owner_handler})
    with pytest.raises(MediaProviderError) as exc_info:
        executor.execute("tts", _context(), "synthetic prompt", _policy(("family-provider",)))

    assert exc_info.value.error_class == "media_provider_unavailable"
    assert owner_calls == []


def test_executor_retries_safe_errors_then_returns_fallback_result():
    calls: list[str] = []
    audit = []

    def unavailable(_context, _input_handle, _secret_handle):
        calls.append("primary")
        raise MediaProviderError("timeout", "ignored", "media provider timed out")

    def fallback(_context, _input_handle, _secret_handle):
        calls.append("fallback")
        return MediaResult(text="done")

    result = MediaProviderExecutor(
        {"primary": unavailable, "fallback": fallback},
        audit_sink=audit.append,
    ).execute("tts", _context(), "synthetic prompt", _policy(("primary", "fallback")))

    assert result.text == "done"
    assert calls == ["primary", "fallback"]
    assert [(event.provider_id, event.error_class, event.success) for event in audit] == [
        ("primary", "timeout", False),
        ("fallback", None, True),
    ]
    assert MediaProviderError("timeout").retryable is True
    assert MediaProviderError("media_provider_unavailable").retryable is False


def test_executor_stops_on_terminal_error_without_trying_fallback():
    calls: list[str] = []

    def terminal(_context, _input_handle, _secret_handle):
        calls.append("primary")
        raise MediaProviderError("provider_error", "wrong-provider")

    def fallback(_context, _input_handle, _secret_handle):
        calls.append("fallback")
        return MediaResult(text="should not run")

    executor = MediaProviderExecutor({"primary": terminal, "fallback": fallback})
    with pytest.raises(MediaProviderError) as exc_info:
        executor.execute("tts", _context(), "synthetic prompt", _policy(("primary", "fallback")))

    assert exc_info.value.error_class == "provider_error"
    assert exc_info.value.provider == "primary"
    assert calls == ["primary"]


def test_executor_attempts_each_provider_at_most_once():
    calls: list[str] = []

    def primary(_context, _input_handle, _secret_handle):
        calls.append("primary")
        raise MediaProviderError("rate_limited", "primary")

    def fallback(_context, _input_handle, _secret_handle):
        calls.append("fallback")
        return MediaResult(text="done")

    result = MediaProviderExecutor({"primary": primary, "fallback": fallback}).execute(
        "tts",
        _context(),
        "synthetic prompt",
        _policy(("primary", "primary", "fallback")),
    )

    assert result.text == "done"
    assert calls == ["primary", "fallback"]


def test_audit_events_exclude_secret_prompt_and_audio_payloads():
    secret_reference = "opaque://secret-reference-not-for-audit"
    prompt = "prompt-not-for-audit"
    audio_bytes = b"raw-audio-bytes-not-for-audit"
    audit = []
    seen_secret_handles = []

    def resolve_secret(_context, _provider_id, reference):
        assert reference == secret_reference
        return "opaque-secret-handle"

    def handler(_context, input_handle, secret_handle):
        assert input_handle == {"prompt": prompt, "audio": audio_bytes}
        seen_secret_handles.append(secret_handle)
        return MediaResult(text="done", metadata={"received": True})

    context = _context()
    result = MediaProviderExecutor(
        {"private-provider": handler},
        secret_resolver=resolve_secret,
        audit_sink=audit.append,
    ).execute(
        "tts",
        context,
        {"prompt": prompt, "audio": audio_bytes},
        _policy(
            ("private-provider",),
            secret_references={"private-provider": secret_reference},
            secret_required=frozenset({"private-provider"}),
        ),
    )

    assert result.text == "done"
    assert seen_secret_handles == ["opaque-secret-handle"]
    assert len(audit) == 1
    assert audit[0].profile_ref == canonical_access_context_fingerprint(context)[:12]
    audit_bytes = repr(audit).encode()
    assert secret_reference.encode() not in audit_bytes
    assert prompt.encode() not in audit_bytes
    assert audio_bytes not in audit_bytes
    assert context.principal_id.encode() not in audit_bytes
    assert context.profile_id.encode() not in audit_bytes


def test_missing_secret_skips_provider_without_calling_resolver_or_handler():
    resolver_calls: list[str] = []
    handler_calls: list[str] = []

    def resolve_secret(_context, provider_id, _reference):
        resolver_calls.append(provider_id)
        return "opaque-secret-handle"

    def handler(_context, _input_handle, _secret_handle):
        handler_calls.append("handler")
        return MediaResult(text="should not run")

    executor = MediaProviderExecutor(
        {"needs-secret": handler},
        secret_resolver=resolve_secret,
    )
    with pytest.raises(MediaProviderError) as exc_info:
        executor.execute(
            "tts",
            _context(),
            "synthetic prompt",
            _policy(("needs-secret",), secret_required=frozenset({"needs-secret"})),
        )

    assert exc_info.value.error_class == "media_provider_unavailable"
    assert resolver_calls == []
    assert handler_calls == []
