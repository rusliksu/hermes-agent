"""Gateway `/settings` hub contract."""

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import gateway.run as gateway_run
from gateway.config import GatewayConfig, Platform
from gateway.platforms.base import MessageEvent, SendResult
from gateway.session import SessionSource


class _SettingsAdapter:
    def __init__(self, success=True):
        self.success = success
        self.calls = []

    async def send_settings_picker(self, **kwargs):
        self.calls.append(kwargs)
        return SendResult(success=self.success, message_id="settings-1")


def _event(platform=Platform.TELEGRAM, thread_id="77", profile=None):
    return MessageEvent(
        text="/settings",
        message_id="message-1",
        source=SessionSource(
            platform=platform,
            chat_id="10001",
            chat_type="dm",
            thread_id=thread_id,
            user_id="10001",
            profile=profile,
        ),
    )


def _runner(adapter):
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.config = GatewayConfig()
    runner.session_store = None
    runner.adapters = {}
    runner._session_model_overrides = {}
    runner._session_reasoning_overrides = {}
    runner._session_service_tier_overrides = {}
    runner._normalize_source_for_session_key = lambda source: source
    runner._adapter_for_source = lambda _source: adapter
    runner._thread_metadata_for_source = (
        lambda source, anchor=None: {
            "thread_id": source.thread_id,
            "reply_to_message_id": anchor,
        }
    )
    runner._reply_anchor_for_event = lambda event: event.message_id
    runner._resolve_session_agent_runtime = lambda **_kwargs: (
        "gpt-5.6-luna",
        {"provider": "openai-codex"},
    )
    runner._resolve_session_reasoning_config = lambda **_kwargs: {
        "enabled": True,
        "effort": "high",
    }
    runner._resolve_session_service_tier = lambda **_kwargs: None
    runner._build_picker_session_validator = AsyncMock(
        return_value=AsyncMock(return_value=True)
    )
    return runner


@pytest.mark.asyncio
async def test_settings_card_shows_effective_combination_and_topic_actions(
    monkeypatch,
):
    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {})
    adapter = _SettingsAdapter()
    runner = _runner(adapter)
    runner._prewarm_topic_preferences_for_source = AsyncMock(return_value={})

    response = await runner._handle_settings_command(_event())

    assert response is None
    runner._prewarm_topic_preferences_for_source.assert_awaited_once()
    assert len(adapter.calls) == 1
    call = adapter.calls[0]
    assert "gpt-5.6-luna" in call["title"]
    assert "high" in call["title"]
    assert call["initiator_user_id"] == "10001"
    assert call["allow_shared_lane_control"] is True
    assert call["metadata"]["thread_id"] == "77"
    actions = {item["value"]: item for item in call["actions"]}
    assert actions["model:gpt-5.6-luna"]["is_current"] is True
    assert actions["reasoning:high"]["is_current"] is True
    assert "model:gpt-5.6-terra" in actions
    assert "model:gpt-5.6-sol" in actions
    assert "model:all" in actions
    assert "reasoning:xhigh" in actions
    assert "fast:fast" in actions
    assert actions["close"]["close"] is True


@pytest.mark.asyncio
async def test_settings_actions_delegate_to_typed_scope_handlers(monkeypatch):
    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {})
    adapter = _SettingsAdapter()
    runner = _runner(adapter)
    runner._handle_model_command = AsyncMock(return_value="model changed")
    runner._handle_reasoning_command = AsyncMock(return_value="reasoning changed")
    runner._handle_fast_command = AsyncMock(return_value="fast changed")

    await runner._handle_settings_command(_event())
    select = adapter.calls[0]["on_action_selected"]

    await select("10001", "model:gpt-5.6-sol")
    assert runner._handle_model_command.await_args.args[0].text == (
        "/model gpt-5.6-sol --provider openai-codex --topic"
    )
    await select("10001", "reasoning:xhigh")
    assert runner._handle_reasoning_command.await_args.args[0].text == (
        "/reasoning xhigh --topic"
    )
    await select("10001", "fast:fast")
    assert runner._handle_fast_command.await_args.args[0].text == (
        "/fast fast --session"
    )
    await select("10001", "model:all")
    assert runner._handle_model_command.await_args.args[0].text == "/model --topic"


@pytest.mark.asyncio
async def test_settings_actions_restore_multiplex_profile_runtime_scope(monkeypatch):
    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {})
    adapter = _SettingsAdapter()
    runner = _runner(adapter)
    runner.config.multiplex_profiles = True
    profile_home = Path("/profiles/family")
    runner._resolve_profile_home_for_source = lambda source: (
        profile_home if source.profile == "family" else None
    )

    active_scopes = []
    entered_scopes = []

    @contextmanager
    def _runtime_scope(home):
        entered_scopes.append(home)
        active_scopes.append(home)
        try:
            yield
        finally:
            active_scopes.pop()

    monkeypatch.setattr(gateway_run, "_profile_runtime_scope", _runtime_scope)

    handled = []

    def _handler(name):
        async def _handle(event):
            handled.append((name, tuple(active_scopes), event.source.profile))
            return f"{name} changed"

        return _handle

    runner._handle_model_command = _handler("model")
    runner._handle_reasoning_command = _handler("reasoning")
    runner._handle_fast_command = _handler("fast")

    await runner._handle_settings_command(_event(profile="family"))
    select = adapter.calls[0]["on_action_selected"]

    await select("10001", "model:gpt-5.6-sol")
    await select("10001", "reasoning:xhigh")
    await select("10001", "fast:fast")

    assert entered_scopes == [profile_home, profile_home, profile_home]
    assert handled == [
        ("model", (profile_home,), "family"),
        ("reasoning", (profile_home,), "family"),
        ("fast", (profile_home,), "family"),
    ]
    assert active_scopes == []


@pytest.mark.asyncio
async def test_settings_falls_back_to_text_off_telegram(monkeypatch):
    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {})
    runner = _runner(adapter=None)

    response = await runner._handle_settings_command(
        _event(platform=Platform.DISCORD, thread_id=None)
    )

    assert isinstance(response, str)
    assert "gpt-5.6-luna" in response
    assert "high" in response
    assert "normal" in response.lower()


@pytest.mark.asyncio
async def test_settings_picker_failure_returns_text_fallback(monkeypatch):
    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {})
    adapter = _SettingsAdapter(success=False)
    runner = _runner(adapter)

    response = await runner._handle_settings_command(_event())

    assert isinstance(response, str)
    assert "gpt-5.6-luna" in response


@pytest.mark.asyncio
async def test_settings_binding_failure_falls_back_without_sending(monkeypatch):
    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {})
    adapter = _SettingsAdapter()
    runner = _runner(adapter)
    runner._build_picker_session_validator = AsyncMock(return_value=None)

    response = await runner._handle_settings_command(_event())

    assert isinstance(response, str)
    assert adapter.calls == []
