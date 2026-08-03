from __future__ import annotations

from gateway.access_registry import DeliveryTarget, ResolvedAccessContext
from gateway.session_context import bind_resolved_access_context


def _context() -> ResolvedAccessContext:
    return ResolvedAccessContext(
        principal_id="principal-a",
        role_id="family_standard",
        profile_id="profile-a",
        conversation_scope="dm:principal-a",
        capabilities=frozenset({"attachments"}),
        delivery_target=DeliveryTarget(
            platform="telegram",
            account="bot-a",
            peer_kind="dm",
            chat_id="10001",
        ),
    )


def _audio(tmp_path):
    profile_home = tmp_path / "profile-a"
    audio = profile_home / "media" / "voice.ogg"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"synthetic-audio")
    return profile_home, audio


def test_local_success_stops_explicit_chain(monkeypatch, tmp_path):
    from tools import transcription_tools as stt

    profile_home, audio = _audio(tmp_path)
    calls = []
    monkeypatch.setattr(
        stt,
        "_load_stt_config",
        lambda: {"fallbacks": ["local", "mistral"], "secret_references": {}},
    )
    monkeypatch.setattr(stt, "_stt_profile_home", lambda _context: profile_home)
    monkeypatch.setattr(
        stt,
        "_transcribe_local",
        lambda file_path, model: calls.append(("local", file_path, model))
        or {"success": True, "transcript": "локально"},
    )
    monkeypatch.setattr(
        stt,
        "_transcribe_mistral",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("cloud fallback must not run after local success")
        ),
    )

    with bind_resolved_access_context(_context()):
        result = stt._dispatch_to_stt_fallback_chain(str(audio))

    assert result == {
        "success": True,
        "transcript": "локально",
        "provider": "local",
    }
    assert [entry[0] for entry in calls] == ["local"]


def test_retryable_local_failure_uses_first_cloud_provider_with_profile_secret(
    monkeypatch, tmp_path,
):
    from tools import transcription_tools as stt

    profile_home, audio = _audio(tmp_path)
    calls = []
    monkeypatch.setattr(
        stt,
        "_load_stt_config",
        lambda: {
            "fallbacks": ["local", "mistral", "openai"],
            "secret_references": {"mistral": "profile://stt/mistral"},
        },
    )
    monkeypatch.setattr(stt, "_stt_profile_home", lambda _context: profile_home)
    monkeypatch.setattr(
        stt,
        "_resolve_profile_stt_secret",
        lambda _context, provider_id, reference: calls.append(
            ("secret", provider_id, reference)
        )
        or "opaque-profile-handle",
    )
    monkeypatch.setattr(
        stt,
        "_transcribe_local",
        lambda *args: calls.append(("local",))
        or {
            "success": False,
            "transcript": "",
            "error_type": "timeout",
        },
    )

    def fake_mistral(file_path, model, *, api_key=None):
        calls.append(("mistral", file_path, model, api_key))
        return {"success": True, "transcript": "облачно"}

    monkeypatch.setattr(stt, "_transcribe_mistral", fake_mistral)
    monkeypatch.setattr(
        stt,
        "_transcribe_openai",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("second cloud provider must not run")
        ),
    )

    with bind_resolved_access_context(_context()):
        result = stt._dispatch_to_stt_fallback_chain(str(audio))

    assert result["success"] is True
    assert result["transcript"] == "облачно"
    assert result["provider"] == "mistral"
    assert [entry[0] for entry in calls] == ["local", "secret", "mistral"]
    assert calls[-1][-1] == "opaque-profile-handle"


def test_cloud_provider_without_secret_reference_is_skipped(monkeypatch, tmp_path):
    from tools import transcription_tools as stt

    profile_home, audio = _audio(tmp_path)
    monkeypatch.setattr(
        stt,
        "_load_stt_config",
        lambda: {"fallbacks": ["local", "mistral"]},
    )
    monkeypatch.setattr(stt, "_stt_profile_home", lambda _context: profile_home)
    monkeypatch.setattr(
        stt,
        "_transcribe_local",
        lambda *args: {
            "success": False,
            "transcript": "",
            "error_type": "timeout",
        },
    )
    monkeypatch.setattr(
        stt,
        "_transcribe_mistral",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("provider without secret reference must be skipped")
        ),
    )

    with bind_resolved_access_context(_context()):
        result = stt._dispatch_to_stt_fallback_chain(str(audio))

    assert result["success"] is False
    assert result["error_type"] == "media_provider_unavailable"


def test_profile_secret_resolution_does_not_read_process_global_env(monkeypatch, tmp_path):
    from hermes_cli import config as hermes_config
    from tools import transcription_tools as stt

    profile_home, _audio_file = _audio(tmp_path)
    monkeypatch.setattr(stt, "_stt_profile_home", lambda _context: profile_home)
    monkeypatch.setattr(
        stt,
        "get_env_value",
        lambda _name: (_ for _ in ()).throw(
            AssertionError("profile resolver must not read the legacy env helper")
        ),
    )
    monkeypatch.setattr(
        hermes_config,
        "get_env_value_prefer_dotenv",
        lambda name: "profile-secret" if name == "MISTRAL_API_KEY" else None,
    )

    with bind_resolved_access_context(_context()):
        assert (
            stt._resolve_profile_stt_secret(
                _context(), "mistral", "profile://stt/mistral"
            )
            == "profile-secret"
        )


def test_input_outside_profile_is_rejected_before_provider(monkeypatch, tmp_path):
    from tools import transcription_tools as stt

    profile_home, _audio_file = _audio(tmp_path)
    outside = tmp_path / "owner" / "voice.ogg"
    outside.parent.mkdir()
    outside.write_bytes(b"not-for-profile")
    monkeypatch.setattr(stt, "_load_stt_config", lambda: {"fallbacks": ["local"]})
    monkeypatch.setattr(stt, "_stt_profile_home", lambda _context: profile_home)
    monkeypatch.setattr(
        stt,
        "_transcribe_local",
        lambda *args: (_ for _ in ()).throw(AssertionError("provider must not run")),
    )

    with bind_resolved_access_context(_context()):
        result = stt._dispatch_to_stt_fallback_chain(str(outside))

    assert result["success"] is False
    assert result["error_type"] == "provider_error"


def test_explicit_chain_without_context_fails_closed(monkeypatch, tmp_path):
    from tools import transcription_tools as stt

    _profile_home, audio = _audio(tmp_path)
    monkeypatch.setattr(stt, "_load_stt_config", lambda: {"fallbacks": ["local"]})
    result = stt._dispatch_to_stt_fallback_chain(str(audio))
    assert result["success"] is False
    assert result["error_type"] == "invalid_context"


def test_missing_fallbacks_preserves_legacy_local_dispatch(monkeypatch):
    from tools import transcription_tools as stt

    monkeypatch.setattr(stt, "_load_stt_config", lambda: {"provider": "local"})
    monkeypatch.setattr(stt, "_validate_audio_file", lambda _path: None)
    monkeypatch.setattr(stt, "_get_provider", lambda _config: "local")
    monkeypatch.setattr(
        stt,
        "_transcribe_local",
        lambda file_path, model: {
            "success": True,
            "transcript": "legacy-local",
            "provider": "local",
        },
    )

    assert stt.transcribe_audio("voice.ogg") == {
        "success": True,
        "transcript": "legacy-local",
        "provider": "local",
    }
