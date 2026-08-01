"""Resolution and scope contracts for topic settings."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest

import gateway.run as gateway_run
from gateway.config import ChannelOverride, GatewayConfig, Platform, PlatformConfig
from gateway.session import SessionSource


def _source():
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="10001",
        chat_type="dm",
        thread_id="17585",
        user_id="10001",
    )


def _runner(topic_preferences=None):
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.config = None
    runner._session_model_overrides = {}
    runner._session_reasoning_overrides = {}
    runner._session_service_tier_overrides = {}
    runner._last_resolved_model = {}
    runner.session_store = SimpleNamespace(
        get_topic_preferences=lambda _source: dict(topic_preferences or {}),
        get_model_override=lambda _key: None,
    )
    return runner


def test_scope_parser_defaults_and_rejects_conflicts():
    parse = gateway_run.GatewayRunner._parse_gateway_scope_args

    assert parse("high", default_scope="topic") == ("high", "topic", None)
    assert parse("high --session", default_scope="topic") == (
        "high", "session", None
    )
    _, scope, error = parse(
        "high --session --global", default_scope="topic"
    )
    assert scope is None
    assert error and "only one" in error
    _, scope, error = parse(
        "--topic", default_scope="session", allowed_scopes=("session", "global")
    )
    assert scope is None
    assert error and "not supported" in error


def test_topic_model_preference_beats_live_legacy_and_channel_override(monkeypatch):
    source = _source()
    topic = {
        "model_override": {
            "model": "gpt-5.6-luna",
            "provider": "openai-codex",
        }
    }
    runner = _runner(topic)
    runner.config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(
                enabled=True,
                channel_overrides={
                    "10001": ChannelOverride(
                        model="channel-model", provider="channel-provider"
                    )
                },
            )
        }
    )
    session_key = runner._session_key_for_source(source)
    runner._session_model_overrides[session_key] = {
        "model": "legacy-model",
        "provider": "legacy-provider",
        "api_key": "legacy-key",
    }

    monkeypatch.setattr(gateway_run, "_resolve_gateway_model", lambda _cfg=None: "global")
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs_for_provider",
        lambda provider: {
            "provider": provider,
            "api_key": "fresh-topic-key",
            "api_mode": "codex_responses",
            "base_url": "https://example.invalid",
        },
    )

    model, runtime = runner._resolve_session_agent_runtime(
        source=source, session_key=session_key, user_config={}
    )

    assert model == "gpt-5.6-luna"
    assert runtime["provider"] == "openai-codex"
    assert runtime["api_key"] == "fresh-topic-key"


def test_topic_provider_override_fails_closed_without_credentials(monkeypatch):
    source = _source()
    runner = _runner(
        {
            "model_override": {
                "model": "gpt-5.6-luna",
                "provider": "openai-codex",
            }
        }
    )
    monkeypatch.setattr(gateway_run, "_resolve_gateway_model", lambda _cfg=None: "global")
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs_for_provider",
        MagicMock(side_effect=RuntimeError("credentials unavailable")),
    )
    global_runtime = MagicMock(
        return_value={
            "provider": "anthropic",
            "api_key": "wrong-provider-key",
            "base_url": "https://api.anthropic.com",
            "api_mode": "anthropic_messages",
            "credential_pool": {"anthropic": ["account"]},
        }
    )
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", global_runtime)

    with pytest.raises(
        RuntimeError, match="model override provider 'openai-codex'"
    ):
        runner._resolve_session_agent_runtime(source=source, user_config={})

    global_runtime.assert_not_called()


def test_topic_model_preference_is_an_intentional_switch():
    source = _source()
    runner = _runner(
        {
            "model_override": {
                "model": "gpt-5.6-luna",
                "provider": "openai-codex",
            }
        }
    )

    assert runner._is_intentional_model_switch(
        runner._session_key_for_source(source),
        "gpt-5.6-luna",
        source=source,
    ) is True


def test_topic_reasoning_preference_beats_legacy_and_global():
    source = _source()
    runner = _runner({"reasoning_effort": "high"})
    session_key = runner._session_key_for_source(source)
    runner._session_reasoning_overrides[session_key] = {
        "enabled": True,
        "effort": "low",
    }
    runner._load_reasoning_config = MagicMock(
        return_value={"enabled": True, "effort": "medium"}
    )

    assert runner._resolve_session_reasoning_config(
        source=source, session_key=session_key, model="gpt-5.6-luna"
    ) == {"enabled": True, "effort": "high"}
    runner._load_reasoning_config.assert_not_called()


@pytest.mark.asyncio
async def test_model_scope_applier_keeps_topic_separate_from_legacy():
    source = _source()
    runner = _runner()
    runner._async_session_store = SimpleNamespace(
        _store=runner.session_store,
        update_topic_preferences=AsyncMock(),
        set_model_override=AsyncMock(),
    )
    key = runner._session_key_for_source(source)
    legacy = {"model": "legacy"}
    runner._session_model_overrides[key] = legacy
    topic = {"model": "gpt-5.6-luna", "provider": "openai-codex"}

    await runner._apply_model_scope(
        source=source, session_key=key, override=topic, scope="topic"
    )

    runner._async_session_store.update_topic_preferences.assert_awaited_once_with(
        source, model_override=topic
    )
    assert runner._session_model_overrides[key] is legacy


@pytest.mark.asyncio
async def test_model_scope_session_failure_keeps_live_override_unchanged():
    source = _source()
    runner = _runner()
    runner._async_session_store = SimpleNamespace(
        _store=runner.session_store,
        set_model_override=AsyncMock(side_effect=OSError("sessions store full")),
    )
    key = runner._session_key_for_source(source)
    old_override = {"model": "old-model", "provider": "openai-codex"}
    runner._session_model_overrides[key] = dict(old_override)

    with pytest.raises(OSError, match="sessions store full"):
        await runner._apply_model_scope(
            source=source,
            session_key=key,
            override={"model": "new-model", "provider": "openai-codex"},
            scope="session",
        )

    assert runner._session_model_overrides[key] == old_override


@pytest.mark.asyncio
async def test_model_scope_global_clear_failure_does_not_write_config():
    source = _source()
    runner = _runner()
    old_override = {"model": "old-model", "provider": "openai-codex"}
    runner._async_session_store = SimpleNamespace(
        _store=runner.session_store,
        get_model_override=AsyncMock(return_value=old_override),
        set_model_override=AsyncMock(side_effect=OSError("sessions store full")),
    )
    key = runner._session_key_for_source(source)
    runner._session_model_overrides[key] = dict(old_override)
    persist_global = MagicMock()

    with pytest.raises(OSError, match="sessions store full"):
        await runner._apply_model_scope(
            source=source,
            session_key=key,
            override={"model": "new-model", "provider": "openai-codex"},
            scope="global",
            persist_global=persist_global,
        )

    persist_global.assert_not_called()
    assert runner._session_model_overrides[key] == old_override


@pytest.mark.asyncio
async def test_model_scope_global_config_failure_restores_durable_override():
    source = _source()
    runner = _runner()
    old_override = {"model": "old-model", "provider": "openai-codex"}
    set_override = AsyncMock()
    runner._async_session_store = SimpleNamespace(
        _store=runner.session_store,
        get_model_override=AsyncMock(return_value=old_override),
        set_model_override=set_override,
    )
    key = runner._session_key_for_source(source)
    runner._session_model_overrides[key] = dict(old_override)

    def _fail_config_write():
        raise OSError("config disk full")

    with pytest.raises(OSError, match="config disk full"):
        await runner._apply_model_scope(
            source=source,
            session_key=key,
            override={"model": "new-model", "provider": "openai-codex"},
            scope="global",
            persist_global=_fail_config_write,
        )

    assert set_override.await_args_list == [call(key, None), call(key, old_override)]
    assert runner._session_model_overrides[key] == old_override


@pytest.mark.asyncio
async def test_global_model_scope_commits_are_serialized():
    source = _source()
    runner = _runner()
    first_clear_started = asyncio.Event()
    release_first_clear = asyncio.Event()
    get_calls = 0
    clear_calls = 0

    async def _get_override(_key):
        nonlocal get_calls
        get_calls += 1
        return {"model": "old-model", "provider": "openai-codex"}

    async def _set_override(_key, value):
        nonlocal clear_calls
        if value is None:
            clear_calls += 1
            if clear_calls == 1:
                first_clear_started.set()
                await release_first_clear.wait()

    runner._async_session_store = SimpleNamespace(
        _store=runner.session_store,
        get_model_override=_get_override,
        set_model_override=_set_override,
    )
    key = runner._session_key_for_source(source)
    first = asyncio.create_task(
        runner._apply_model_scope(
            source=source,
            session_key=key,
            override={"model": "first"},
            scope="global",
            persist_global=MagicMock(),
        )
    )
    await first_clear_started.wait()
    second = asyncio.create_task(
        runner._apply_model_scope(
            source=source,
            session_key=key,
            override={"model": "second"},
            scope="global",
            persist_global=MagicMock(),
        )
    )
    await asyncio.sleep(0)

    assert get_calls == 1
    release_first_clear.set()
    await asyncio.gather(first, second)


def test_fast_override_is_presence_sensitive_and_falls_back_to_global():
    source = _source()
    runner = _runner()
    key = runner._session_key_for_source(source)
    runner._load_service_tier = MagicMock(return_value="priority")

    runner._set_session_service_tier_override(key, None)
    assert runner._resolve_session_service_tier(
        source=source, session_key=key
    ) is None
    runner._load_service_tier.assert_not_called()

    runner._set_session_service_tier_override(key, None, clear=True)
    assert runner._resolve_session_service_tier(
        source=source, session_key=key
    ) == "priority"
