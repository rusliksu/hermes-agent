from __future__ import annotations

from pathlib import Path

from gateway.access_registry import DeliveryTarget, ResolvedAccessContext
from gateway.session_context import bind_resolved_access_context


def _context() -> ResolvedAccessContext:
    return ResolvedAccessContext(
        principal_id="principal-a",
        role_id="family_standard",
        profile_id="profile-a",
        conversation_scope="dm:principal-a",
        capabilities=frozenset({"voice_generation"}),
        delivery_target=DeliveryTarget(
            platform="telegram",
            account="bot-a",
            peer_kind="dm",
            chat_id="10001",
        ),
    )


def _output(tmp_path):
    profile_home = tmp_path / "profile-a"
    output = profile_home / "cache" / "audio" / "reply.mp3"
    return profile_home, output


def test_edge_success_stops_explicit_chain(monkeypatch, tmp_path):
    from tools import tts_tool

    profile_home, output = _output(tmp_path)
    calls = []
    monkeypatch.setattr(tts_tool, "_tts_profile_home", lambda _context: profile_home)
    monkeypatch.setattr(
        tts_tool,
        "_load_tts_config",
        lambda: {"fallbacks": ["edge", "openai"]},
    )
    monkeypatch.setattr(
        tts_tool,
        "_run_edge_tts_sync",
        lambda text, path, config: calls.append(("edge", text, path))
        or (Path(path).write_bytes(b"edge-audio") and path),
    )
    monkeypatch.setattr(
        tts_tool,
        "_generate_openai_tts",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("fallback must not run after Edge success")
        ),
    )
    monkeypatch.setattr(
        tts_tool,
        "_validate_tts_artifact",
        lambda _context, path: Path(path),
    )

    with bind_resolved_access_context(_context()):
        result = tts_tool._dispatch_to_tts_fallback_chain(
            "hello", str(output), configured=(("edge", "openai"), {}),
        )

    assert result == {
        "success": True,
        "file_path": str(output.resolve()),
        "provider": "edge",
    }
    assert [entry[0] for entry in calls] == ["edge"]


def test_edge_timeout_falls_back_to_openai_with_profile_secret(monkeypatch, tmp_path):
    from tools import tts_tool

    profile_home, output = _output(tmp_path)
    calls = []
    monkeypatch.setattr(tts_tool, "_tts_profile_home", lambda _context: profile_home)
    monkeypatch.setattr(tts_tool, "_load_tts_config", lambda: {"openai": {}})
    monkeypatch.setattr(
        tts_tool,
        "_resolve_profile_tts_secret",
        lambda _context, provider_id, reference: calls.append(
            ("secret", provider_id, reference)
        )
        or "opaque-profile-handle",
    )
    monkeypatch.setattr(
        tts_tool,
        "_run_edge_tts_sync",
        lambda *args: calls.append(("edge",))
        or (_ for _ in ()).throw(TimeoutError("edge timed out")),
    )

    def fake_openai(text, path, config, *, api_key=None, base_url=None, **kwargs):
        calls.append(("openai", text, path, api_key, base_url))
        Path(path).write_bytes(b"openai-audio")
        return path

    monkeypatch.setattr(tts_tool, "_generate_openai_tts", fake_openai)
    monkeypatch.setattr(
        tts_tool,
        "_validate_tts_artifact",
        lambda _context, path: Path(path),
    )

    with bind_resolved_access_context(_context()):
        result = tts_tool._dispatch_to_tts_fallback_chain(
            "hello",
            str(output),
            configured=(
                ("edge", "openai"),
                {"openai": "profile://tts/openai"},
            ),
        )

    assert result["success"] is True
    assert result["provider"] == "openai"
    assert [entry[0] for entry in calls] == ["edge", "secret", "openai"]
    assert calls[-1][3] == "opaque-profile-handle"


def test_missing_secret_reference_skips_cloud_provider(monkeypatch, tmp_path):
    from tools import tts_tool

    profile_home, output = _output(tmp_path)
    monkeypatch.setattr(tts_tool, "_tts_profile_home", lambda _context: profile_home)
    monkeypatch.setattr(tts_tool, "_load_tts_config", lambda: {})
    monkeypatch.setattr(
        tts_tool,
        "_run_edge_tts_sync",
        lambda *args: (_ for _ in ()).throw(TimeoutError("edge timed out")),
    )
    monkeypatch.setattr(
        tts_tool,
        "_generate_openai_tts",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("provider without secret reference must be skipped")
        ),
    )

    with bind_resolved_access_context(_context()):
        result = tts_tool._dispatch_to_tts_fallback_chain(
            "hello",
            str(output),
            configured=(("edge", "openai"), {}),
        )

    assert result["success"] is False
    assert result["error_type"] == "media_provider_unavailable"


def test_invalid_artifact_is_terminal_and_does_not_fallback(monkeypatch, tmp_path):
    from tools import tts_tool
    from tools.media_provider_routing import MediaProviderError

    profile_home, output = _output(tmp_path)
    calls = []
    monkeypatch.setattr(tts_tool, "_tts_profile_home", lambda _context: profile_home)
    monkeypatch.setattr(tts_tool, "_load_tts_config", lambda: {})
    monkeypatch.setattr(
        tts_tool,
        "_run_edge_tts_sync",
        lambda text, path, config: calls.append("edge")
        or (Path(path).write_bytes(b"not-audio") and path),
    )
    monkeypatch.setattr(
        tts_tool,
        "_validate_tts_artifact",
        lambda *args: (_ for _ in ()).throw(MediaProviderError("provider_error")),
    )
    monkeypatch.setattr(
        tts_tool,
        "_generate_openai_tts",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("invalid artifact must not trigger fallback")
        ),
    )

    with bind_resolved_access_context(_context()):
        result = tts_tool._dispatch_to_tts_fallback_chain(
            "hello", str(output), configured=(("edge", "openai"), {}),
        )

    assert result["success"] is False
    assert result["error_type"] == "provider_error"
    assert calls == ["edge"]


def test_provider_returned_path_outside_profile_is_terminal(monkeypatch, tmp_path):
    from tools import tts_tool

    profile_home, output = _output(tmp_path)
    outside = tmp_path / "owner" / "audio.mp3"
    outside.parent.mkdir()
    outside.write_bytes(b"outside-profile")
    monkeypatch.setattr(tts_tool, "_tts_profile_home", lambda _context: profile_home)
    monkeypatch.setattr(tts_tool, "_load_tts_config", lambda: {})
    monkeypatch.setattr(
        tts_tool,
        "_run_edge_tts_sync",
        lambda text, path, config: outside.as_posix(),
    )
    monkeypatch.setattr(
        tts_tool,
        "_generate_openai_tts",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("outside artifact must not trigger fallback")
        ),
    )

    with bind_resolved_access_context(_context()):
        result = tts_tool._dispatch_to_tts_fallback_chain(
            "hello", str(output), configured=(("edge", "openai"), {}),
        )

    assert result["success"] is False
    assert result["error_type"] == "provider_error"


def test_explicit_chain_without_context_fails_closed(monkeypatch, tmp_path):
    from tools import tts_tool

    _profile_home, output = _output(tmp_path)
    result = tts_tool._dispatch_to_tts_fallback_chain(
        "hello", str(output), configured=(("edge",), {}),
    )
    assert result["success"] is False
    assert result["error_type"] == "invalid_context"


def test_missing_fallbacks_preserves_legacy_dispatch(monkeypatch):
    from tools import tts_tool

    monkeypatch.setattr(tts_tool, "_load_tts_config", lambda: {})
    assert tts_tool._dispatch_to_tts_fallback_chain("hello", "reply.mp3") is None


def test_public_tool_routes_explicit_chain_before_legacy_provider(monkeypatch, tmp_path):
    import json

    from tools import tts_tool

    profile_home, output = _output(tmp_path)
    monkeypatch.setattr(
        tts_tool,
        "_load_tts_config",
        lambda: {"fallbacks": ["edge"]},
    )
    monkeypatch.setattr(tts_tool, "_tts_profile_home", lambda _context: profile_home)
    monkeypatch.setattr(
        tts_tool,
        "_run_edge_tts_sync",
        lambda text, path, config: Path(path).write_bytes(b"edge-audio") and path,
    )
    monkeypatch.setattr(
        tts_tool,
        "_validate_tts_artifact",
        lambda _context, path: Path(path),
    )

    with bind_resolved_access_context(_context()):
        result = json.loads(tts_tool.text_to_speech_tool("hello", str(output)))

    assert result["success"] is True
    assert result["provider"] == "edge"
    assert Path(result["file_path"]).resolve() == output.resolve()


def test_public_tool_default_output_is_profile_owned(monkeypatch, tmp_path):
    import json

    from tools import tts_tool

    profile_home, _output_path = _output(tmp_path)
    profile_audio_dir = profile_home / "cache" / "audio"
    monkeypatch.setattr(tts_tool, "_load_tts_config", lambda: {"fallbacks": ["edge"]})
    monkeypatch.setattr(tts_tool, "_tts_profile_home", lambda _context: profile_home)
    monkeypatch.setattr(
        tts_tool,
        "_get_default_output_dir",
        lambda: str(profile_audio_dir),
    )
    monkeypatch.setattr(
        tts_tool,
        "_run_edge_tts_sync",
        lambda text, path, config: Path(path).write_bytes(b"edge-audio") and path,
    )
    monkeypatch.setattr(
        tts_tool,
        "_validate_tts_artifact",
        lambda _context, path: Path(path),
    )

    with bind_resolved_access_context(_context()):
        result = json.loads(tts_tool.text_to_speech_tool("hello"))

    assert result["success"] is True
    assert Path(result["file_path"]).resolve().is_relative_to(profile_home.resolve())
