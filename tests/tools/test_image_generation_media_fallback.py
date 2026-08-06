from __future__ import annotations

import json
from pathlib import Path

from gateway.access_registry import DeliveryTarget, ResolvedAccessContext
from gateway.session_context import bind_resolved_access_context


def _context() -> ResolvedAccessContext:
    return ResolvedAccessContext(
        principal_id="principal-a",
        role_id="family",
        profile_id="profile-a",
        conversation_scope="dm:principal-a",
        capabilities=frozenset({"image_generation"}),
        delivery_target=DeliveryTarget(
            platform="telegram",
            account="bot-a",
            peer_kind="dm",
            chat_id="10001",
        ),
    )


class _FakeProvider:
    def __init__(self, name, result_factory, calls):
        self.name = name
        self._result_factory = result_factory
        self.calls = calls

    def generate(self, **kwargs):
        self.calls.append((self.name, kwargs))
        return self._result_factory(kwargs)

    def is_available(self):
        return True


def test_codex_is_first_and_falls_back_on_capability(monkeypatch, tmp_path):
    from tools import image_generation_tool as image_tool

    profile_home = tmp_path / "profile-a"
    output = profile_home / "cache" / "images" / "fal.png"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"png")
    calls = []

    providers = {
        "openai-codex": _FakeProvider(
            "openai-codex",
            lambda _kwargs: {
                "success": False,
                "error_type": "capability_unsupported",
            },
            calls,
        ),
        "fal": _FakeProvider(
            "fal",
            lambda _kwargs: {"success": True, "image": str(output)},
            calls,
        ),
    }
    monkeypatch.setattr(
        image_tool, "_read_configured_image_fallbacks",
        lambda: ("openai-codex", "fal"),
    )
    monkeypatch.setattr(
        image_tool, "_registered_image_provider",
        lambda provider_id: providers.get(provider_id),
    )
    monkeypatch.setattr(image_tool, "_image_profile_home", lambda _context: profile_home)

    with bind_resolved_access_context(_context()):
        payload = json.loads(image_tool._dispatch_to_image_fallback_chain("draw cat", "square"))

    assert payload["success"] is True
    assert payload["provider"] == "fal"
    assert payload["image"] == str(output.resolve())
    assert [name for name, _kwargs in calls] == ["openai-codex", "fal"]


def test_successful_codex_stops_chain_and_keeps_profile_path(monkeypatch, tmp_path):
    from tools import image_generation_tool as image_tool

    profile_home = tmp_path / "profile-a"
    output = profile_home / "cache" / "images" / "codex.png"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"png")
    calls = []
    providers = {
        "openai-codex": _FakeProvider(
            "openai-codex",
            lambda _kwargs: {"success": True, "image": str(output)},
            calls,
        ),
        "fal": _FakeProvider(
            "fal",
            lambda _kwargs: (_ for _ in ()).throw(
                AssertionError("fallback must not run")
            ),
            calls,
        ),
    }
    monkeypatch.setattr(
        image_tool, "_read_configured_image_fallbacks",
        lambda: ("openai-codex", "fal"),
    )
    monkeypatch.setattr(
        image_tool, "_registered_image_provider",
        lambda provider_id: providers.get(provider_id),
    )
    monkeypatch.setattr(image_tool, "_image_profile_home", lambda _context: profile_home)

    with bind_resolved_access_context(_context()):
        payload = json.loads(image_tool._dispatch_to_image_fallback_chain("draw cat", "square"))

    assert payload["success"] is True
    assert payload["provider"] == "openai-codex"
    assert Path(payload["image"]).resolve() == output.resolve()
    assert [name for name, _kwargs in calls] == ["openai-codex"]


def test_artifact_outside_profile_is_terminal(monkeypatch, tmp_path):
    from tools import image_generation_tool as image_tool

    profile_home = tmp_path / "profile-a"
    outside = tmp_path / "owner" / "cache" / "images" / "wrong.png"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"png")
    calls = []
    providers = {
        "openai-codex": _FakeProvider(
            "openai-codex",
            lambda _kwargs: {"success": True, "image": str(outside)},
            calls,
        ),
        "fal": _FakeProvider(
            "fal",
            lambda _kwargs: (_ for _ in ()).throw(
                AssertionError("terminal path error must not fall back")
            ),
            calls,
        ),
    }
    monkeypatch.setattr(
        image_tool, "_read_configured_image_fallbacks",
        lambda: ("openai-codex", "fal"),
    )
    monkeypatch.setattr(
        image_tool, "_registered_image_provider",
        lambda provider_id: providers.get(provider_id),
    )
    monkeypatch.setattr(image_tool, "_image_profile_home", lambda _context: profile_home)

    with bind_resolved_access_context(_context()):
        payload = json.loads(image_tool._dispatch_to_image_fallback_chain("draw cat", "square"))

    assert payload["success"] is False
    assert payload["error_type"] == "provider_error"
    assert [name for name, _kwargs in calls] == ["openai-codex"]


def test_fallback_policy_without_context_fails_closed(monkeypatch):
    from tools import image_generation_tool as image_tool

    monkeypatch.setattr(
        image_tool, "_read_configured_image_fallbacks",
        lambda: ("openai-codex", "fal"),
    )
    payload = json.loads(image_tool._dispatch_to_image_fallback_chain("draw cat", "square"))
    assert payload == {
        "success": False,
        "image": None,
        "error": "invalid media access context",
        "error_type": "invalid_context",
    }


def test_image_cache_uses_typed_profile_home(monkeypatch, tmp_path):
    from agent import image_gen_provider

    profile_home = tmp_path / "profile-a"
    monkeypatch.setattr(
        "agent.runtime_cwd.bound_profile_home",
        lambda: profile_home,
    )
    monkeypatch.setattr(
        "hermes_constants.get_hermes_home",
        lambda: (_ for _ in ()).throw(AssertionError("global home used")),
    )

    assert image_gen_provider._images_cache_dir() == (
        profile_home / "cache" / "images"
    )
