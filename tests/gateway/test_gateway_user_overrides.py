"""Tests for config-backed gateway per-user overrides."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import gateway.run as gateway_run
from gateway.config import Platform
from gateway.session import SessionSource
from gateway.user_overrides import parse_gateway_user_override


def _source(*, user_id="111", chat_id="111", chat_type="dm"):
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id=chat_id,
        chat_type=chat_type,
        user_id=user_id,
    )


def _config(override, *, user_key="111"):
    return {
        "model": {"default": "global-model", "provider": "global-provider"},
        "gateway": {
            "user_overrides": {
                "telegram": {
                    user_key: override,
                }
            }
        },
    }


def _runner():
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.config = None
    runner.session_store = None
    runner._session_model_overrides = {}
    runner._session_reasoning_overrides = {}
    return runner


def test_quoted_yaml_user_id_matches_dm():
    override = parse_gateway_user_override(
        _config({"scope": "dm", "model": "gpt-5.4"}, user_key="111"),
        _source(user_id=111),
    )

    assert override is not None
    assert override.user_id == "111"
    assert override.model == "gpt-5.4"


def test_numeric_yaml_user_id_matches_dm():
    override = parse_gateway_user_override(
        _config({"scope": "dm", "reasoning_effort": "high"}, user_key=111),
        _source(user_id="111"),
    )

    assert override is not None
    assert override.reasoning_config == {"enabled": True, "effort": "high"}


def test_same_user_in_group_stays_on_global_runtime(monkeypatch):
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs",
        lambda: {"provider": "global-provider", "api_key": "global-key"},
    )

    runner = _runner()
    model, runtime = runner._resolve_session_agent_runtime(
        source=_source(user_id="111", chat_id="-100", chat_type="group"),
        user_config=_config({"scope": "dm", "model": "dm-model"}),
    )

    assert model == "global-model"
    assert runtime["provider"] == "global-provider"


def test_matched_dm_model_override_resolves_runtime(monkeypatch):
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs",
        lambda: {
            "provider": "global-provider",
            "api_key": "global-key",
            "base_url": "https://global.example/v1",
            "api_mode": "chat_completions",
        },
    )

    def fake_switch_model(**kwargs):
        assert kwargs["raw_input"] == "gpt-5.4"
        assert kwargs["explicit_provider"] == "openai-codex"
        return SimpleNamespace(
            success=True,
            new_model="gpt-5.4",
            target_provider="openai-codex",
            api_key="codex-key",
            base_url="https://chatgpt.com/backend-api/codex",
            api_mode="codex_responses",
            provider_label="OpenAI Codex",
        )

    import hermes_cli.model_switch as model_switch

    monkeypatch.setattr(model_switch, "switch_model", fake_switch_model)

    runner = _runner()
    model, runtime = runner._resolve_session_agent_runtime(
        source=_source(user_id="111"),
        user_config=_config(
            {
                "scope": "dm",
                "model": "gpt-5.4",
                "provider": "openai-codex",
            }
        ),
    )

    assert model == "gpt-5.4"
    assert runtime["provider"] == "openai-codex"
    assert runtime["api_key"] == "codex-key"
    assert runtime["api_mode"] == "codex_responses"


def test_session_model_override_wins_over_dm_default(monkeypatch):
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs",
        lambda: {"provider": "global-provider", "api_key": "global-key"},
    )

    runner = _runner()
    source = _source(user_id="111")
    session_key = runner._session_key_for_source(source)
    runner._session_model_overrides[session_key] = {
        "model": "session-model",
        "provider": "session-provider",
        "api_key": "session-key",
        "api_mode": "chat_completions",
    }

    model, runtime = runner._resolve_session_agent_runtime(
        source=source,
        session_key=session_key,
        user_config=_config({"scope": "dm", "model": "dm-model"}),
    )

    assert model == "session-model"
    assert runtime["provider"] == "session-provider"
    assert runtime["api_key"] == "session-key"


def test_reasoning_override_uses_dm_default_without_model(monkeypatch):
    monkeypatch.setattr(
        gateway_run.GatewayRunner,
        "_load_reasoning_config",
        MagicMock(return_value={"enabled": True, "effort": "medium"}),
    )

    runner = _runner()
    result = runner._resolve_session_reasoning_config(
        source=_source(user_id="111"),
        user_config=_config({"scope": "dm", "reasoning_effort": "low"}),
    )

    assert result == {"enabled": True, "effort": "low"}
