"""Pairwise privacy checks for profile-scoped media fallback routing."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

import pytest

from gateway.access_registry import (
    DeliveryTarget,
    ResolvedAccessContext,
    canonical_access_context_fingerprint,
)
from gateway.session_context import bind_resolved_access_context
from tools.media_provider_routing import (
    MediaProviderExecutor,
    MediaProviderPolicy,
    MediaResult,
)


def _context(profile_id: str, chat_id: str, *, capability: str) -> ResolvedAccessContext:
    return ResolvedAccessContext(
        principal_id=f"principal-{profile_id}",
        role_id="family",
        profile_id=profile_id,
        conversation_scope=f"dm:{chat_id}",
        capabilities=frozenset({capability}),
        delivery_target=DeliveryTarget(
            platform="telegram",
            account="synthetic-account",
            peer_kind="dm",
            chat_id=chat_id,
        ),
    )


@pytest.mark.parametrize(
    ("request_profile", "foreign_profile"),
    [("profile-a", "profile-b"), ("profile-b", "profile-a")],
)
def test_stt_rejects_foreign_profile_input_before_provider(
    monkeypatch, tmp_path, request_profile, foreign_profile,
):
    from tools import transcription_tools as stt

    roots = {
        profile: tmp_path / profile
        for profile in (request_profile, foreign_profile)
    }
    foreign_audio = roots[foreign_profile] / "media" / "voice.ogg"
    foreign_audio.parent.mkdir(parents=True)
    foreign_audio.write_bytes(b"foreign-audio")
    context = _context(request_profile, request_profile, capability="attachments")
    provider_calls: list[str] = []

    monkeypatch.setattr(stt, "_load_stt_config", lambda: {"fallbacks": ["local"]})
    monkeypatch.setattr(stt, "_stt_profile_home", lambda bound: roots[bound.profile_id])
    monkeypatch.setattr(
        stt,
        "_transcribe_local",
        lambda *args: provider_calls.append("local")
        or {"success": True, "transcript": "must-not-run"},
    )

    with bind_resolved_access_context(context):
        result = stt._dispatch_to_stt_fallback_chain(str(foreign_audio))

    assert result["success"] is False
    assert result["error_type"] == "provider_error"
    assert provider_calls == []


@pytest.mark.parametrize(
    ("request_profile", "foreign_profile"),
    [("profile-a", "profile-b"), ("profile-b", "profile-a")],
)
def test_tts_rejects_foreign_profile_output_before_provider(
    monkeypatch, tmp_path, request_profile, foreign_profile,
):
    from tools import tts_tool

    roots = {
        profile: tmp_path / profile
        for profile in (request_profile, foreign_profile)
    }
    foreign_output = roots[foreign_profile] / "cache" / "audio" / "reply.mp3"
    context = _context(request_profile, request_profile, capability="voice_generation")
    provider_calls: list[str] = []

    monkeypatch.setattr(tts_tool, "_tts_profile_home", lambda bound: roots[bound.profile_id])
    monkeypatch.setattr(tts_tool, "_load_tts_config", lambda: {})
    monkeypatch.setattr(
        tts_tool,
        "_run_edge_tts_sync",
        lambda *args: provider_calls.append("edge") or str(foreign_output),
    )

    with bind_resolved_access_context(context):
        result = tts_tool._dispatch_to_tts_fallback_chain(
            "private turn",
            str(foreign_output),
            configured=(("edge",), {}),
        )

    assert result["success"] is False
    assert result["error_type"] == "provider_error"
    assert provider_calls == []


@pytest.mark.parametrize(
    ("request_profile", "foreign_profile"),
    [("profile-a", "profile-b"), ("profile-b", "profile-a")],
)
def test_image_rejects_foreign_profile_artifact_without_fallback(
    monkeypatch, tmp_path, request_profile, foreign_profile,
):
    from tools import image_generation_tool as image_tool

    roots = {
        profile: tmp_path / profile
        for profile in (request_profile, foreign_profile)
    }
    foreign_image = roots[foreign_profile] / "cache" / "images" / "image.png"
    foreign_image.parent.mkdir(parents=True)
    foreign_image.write_bytes(b"foreign-image")
    context = _context(request_profile, request_profile, capability="image_generation")
    calls: list[str] = []

    class _Provider:
        def __init__(self, provider_id: str):
            self.provider_id = provider_id

        def is_available(self):
            return True

        def generate(self, **_kwargs):
            calls.append(self.provider_id)
            if self.provider_id == "openai-codex":
                return {"success": True, "image": str(foreign_image)}
            raise AssertionError("terminal path error must not use fallback")

    providers = {name: _Provider(name) for name in ("openai-codex", "fal")}
    monkeypatch.setattr(
        image_tool,
        "_read_configured_image_fallbacks",
        lambda: ("openai-codex", "fal"),
    )
    monkeypatch.setattr(
        image_tool,
        "_registered_image_provider",
        lambda provider_id: providers[provider_id],
    )
    monkeypatch.setattr(
        image_tool,
        "_image_profile_home",
        lambda bound: roots[bound.profile_id],
    )

    with bind_resolved_access_context(context):
        result = image_tool._dispatch_to_image_fallback_chain("private prompt", "square")

    assert result is not None
    assert '"success": false' in result
    assert '"error_type": "provider_error"' in result
    assert calls == ["openai-codex"]


def test_executor_pairwise_turns_ignore_guessed_profile_and_session_ids(tmp_path):
    roots = {
        profile: tmp_path / profile
        for profile in ("profile-a", "profile-b")
    }
    contexts = {
        profile: _context(profile, f"chat-{profile}", capability="voice_generation")
        for profile in roots
    }
    audit = []
    handler_calls = []
    lock = Lock()

    def handler(context, _input_handle, _secret_handle):
        output = roots[context.profile_id] / "result.txt"
        with lock:
            handler_calls.append((context.profile_id, context.delivery_target.chat_id))
        return MediaResult(
            text=f"result-{context.profile_id}",
            metadata={
                "delivery_target": context.delivery_target.chat_id,
                "output_path": str(output),
            },
        )

    policy = MediaProviderPolicy(
        provider_order={"tts": ("scoped",)},
        required_capabilities={"tts": "voice_generation"},
        secret_references={},
        secret_required=frozenset(),
    )
    executor = MediaProviderExecutor({"scoped": handler}, audit_sink=audit.append)

    def run_turn(profile_id: str):
        return executor.execute(
            "tts",
            contexts[profile_id],
            {
                "profile_id": "owner",
                "session_id": f"owner-session-{profile_id}",
                "prompt": f"private-prompt-{profile_id}",
            },
            policy,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = dict(zip(roots, pool.map(run_turn, roots)))

    assert set(results) == {"profile-a", "profile-b"}
    for profile_id, result in results.items():
        assert result.text == f"result-{profile_id}"
        assert result.metadata["delivery_target"] == f"chat-{profile_id}"
        assert Path(result.metadata["output_path"]).is_relative_to(roots[profile_id])
    assert sorted(handler_calls) == [
        ("profile-a", "chat-profile-a"),
        ("profile-b", "chat-profile-b"),
    ]
    assert {
        event.profile_ref
        for event in audit
    } == {
        canonical_access_context_fingerprint(context)[:12]
        for context in contexts.values()
    }
    audit_text = repr(audit)
    assert "owner-session-" not in audit_text
    assert "private-prompt-" not in audit_text
    assert "owner" not in audit_text


def test_tts_contextvars_keep_concurrent_delivery_and_audit_pairwise(
    monkeypatch, tmp_path,
):
    from tools import tts_tool

    roots = {
        profile: tmp_path / profile
        for profile in ("profile-a", "profile-b")
    }
    contexts = {
        profile: _context(profile, f"chat-{profile}", capability="voice_generation")
        for profile in roots
    }
    calls = []
    audit = []
    lock = Lock()

    monkeypatch.setattr(tts_tool, "_load_tts_config", lambda: {"fallbacks": ["edge"]})
    monkeypatch.setattr(tts_tool, "_tts_profile_home", lambda bound: roots[bound.profile_id])

    def fake_edge(text, output_path, _config):
        with lock:
            calls.append((text, output_path))
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(text.encode())
        return output_path

    monkeypatch.setattr(tts_tool, "_run_edge_tts_sync", fake_edge)
    monkeypatch.setattr(tts_tool, "_validate_tts_artifact", lambda _context, path: Path(path))
    monkeypatch.setattr(tts_tool, "_tts_provider_audit", audit.append)

    async def run_turn(profile_id: str):
        context = contexts[profile_id]
        output = roots[profile_id] / "cache" / "audio" / "reply.mp3"
        with bind_resolved_access_context(context):
            return await asyncio.to_thread(
                tts_tool._dispatch_to_tts_fallback_chain,
                f"turn-{profile_id}",
                str(output),
                configured=(("edge",), {}),
            )

    async def run_all():
        return await asyncio.gather(run_turn("profile-a"), run_turn("profile-b"))

    results = asyncio.run(run_all())
    assert [result["success"] for result in results] == [True, True]
    assert {
        Path(result["file_path"]).resolve()
        for result in results
    } == {
        (roots[profile] / "cache" / "audio" / "reply.mp3").resolve()
        for profile in roots
    }
    assert {
        event.profile_ref
        for event in audit
    } == {
        canonical_access_context_fingerprint(context)[:12]
        for context in contexts.values()
    }
    assert sorted(path for _text, path in calls) == sorted(
        str((roots[profile] / "cache" / "audio" / "reply.mp3").resolve())
        for profile in roots
    )
