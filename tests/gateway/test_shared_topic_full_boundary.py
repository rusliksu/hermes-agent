"""Full-boundary regression for a registry-routed Telegram shared topic.

The test intentionally keeps the real ingress, session store/DB, profile
resolution, AIAgent construction, shared-memory binder, and adapter delivery
path. Only provider response and Telegram send are external seams.
"""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import gateway.run as gateway_run
import hermes_state
import run_agent
from gateway.access_registry import (
    AccessRegistry,
    DeliveryTarget,
    ParticipantIdentity,
    RolePolicy,
    SharedScopeBinding,
    TransportIdentity,
    shared_memory_namespace_for_access_context,
)
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, SendResult
from gateway.session import SessionSource
from gateway.single_principal import SinglePrincipalPolicy
from plugins.platforms.telegram.adapter import TelegramAdapter


ACCOUNT = "boundary-bot"
CHAT_ID = "-10001"
THREAD_ID = "31"
USER_ID = "member-1"
PROFILE_ID = "room-profile"


def _registry() -> AccessRegistry:
    room_identity = TransportIdentity(
        platform="telegram",
        account=ACCOUNT,
        peer_kind="group",
        user_id="ignored-member",
        chat_id=CHAT_ID,
        thread_id=THREAD_ID,
    )
    delivery_target = DeliveryTarget(
        platform="telegram",
        account=ACCOUNT,
        peer_kind="group",
        chat_id=CHAT_ID,
        thread_id=THREAD_ID,
    )
    capabilities = frozenset({"room_memory", "public_web", "vision"})
    return AccessRegistry(
        roles={"shared_room": RolePolicy("shared_room", capabilities)},
        profiles=frozenset({PROFILE_ID}),
        shared_scope_bindings=(
            SharedScopeBinding(
                principal_id="principal-room",
                role_id="shared_room",
                profile_id=PROFILE_ID,
                room_identity=room_identity,
                conversation_scope="room",
                delivery_target=delivery_target,
                participant_identities=(
                    ParticipantIdentity("telegram", ACCOUNT, USER_ID),
                ),
            ),
        ),
        scope_capabilities={"room": capabilities},
        backend_capabilities=capabilities,
    )


def _provider_response(text: str):
    message = SimpleNamespace(
        content=text,
        tool_calls=None,
        reasoning=None,
        reasoning_content=None,
        reasoning_details=None,
    )
    choice = SimpleNamespace(message=message, finish_reason="stop")
    return SimpleNamespace(choices=[choice], model="test-model", usage=None)


async def _wait_for_delivery(adapter: TelegramAdapter) -> None:
    tasks = tuple(adapter._session_tasks.values())
    assert tasks, "Telegram adapter did not create a processing task"
    await asyncio.wait_for(
        asyncio.gather(*tasks),
        timeout=15,
    )
    assert adapter.send.called, "Telegram delivery did not complete"


@pytest.mark.asyncio
async def test_allowed_shared_topic_survives_optional_tool_reduction_at_full_boundary(
    monkeypatch,
    tmp_path,
    caplog,
    request,
):
    from agent.secret_scope import is_multiplex_active, set_multiplex_active

    previous_multiplex_state = is_multiplex_active()
    request.addfinalizer(
        lambda: set_multiplex_active(previous_multiplex_state)
    )

    home = tmp_path / "hermes-home"
    profile_home = home / "profiles" / PROFILE_ID
    for relative in ("sessions", "memories", "logs", "workspace", "home"):
        (profile_home / relative).mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(gateway_run, "_hermes_home", home)
    monkeypatch.setattr(gateway_run, "_env_path", home / ".env")
    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", home / "state.db")
    monkeypatch.setattr(gateway_run, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        gateway_run,
        "_load_gateway_config",
        lambda: {
            "platform_toolsets": {"telegram": ["memory", "web", "vision"]},
            "display": {"tool_progress": "off", "thinking_progress": False},
            "memory": {},
            "agent": {"max_iterations": 1},
        },
    )
    monkeypatch.setattr(gateway_run, "_load_gateway_runtime_config", lambda: {})
    monkeypatch.setattr(
        gateway_run,
        "_resolve_gateway_model",
        lambda config=None: "test-model",
    )
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs",
        lambda: {
            "provider": "openrouter",
            "api_mode": "chat_completions",
            "base_url": "https://example.invalid/v1",
            "api_key": "test-key-1234567890",
        },
    )
    monkeypatch.setattr(
        "agent.model_metadata.get_model_context_length",
        lambda *_args, **_kwargs: 100_000,
    )

    provider_client = MagicMock()
    provider_client.chat.completions.create.return_value = _provider_response(
        "GROUP_SMOKE_OK"
    )
    monkeypatch.setattr(run_agent, "OpenAI", lambda *args, **kwargs: provider_client)
    monkeypatch.setattr(
        run_agent.AIAgent,
        "_create_request_openai_client",
        lambda self, **kwargs: provider_client,
    )
    monkeypatch.setattr(
        run_agent.AIAgent,
        "_close_request_openai_client",
        lambda self, client, **kwargs: None,
    )

    policy = SinglePrincipalPolicy.from_dict(
        {
            "enabled": True,
            "telegram_owner_id": "10001",
            "telegram_shared_chat_ids": [CHAT_ID],
            "allow_owner_bound_relay": False,
        }
    )
    config = GatewayConfig(
        sessions_dir=home / "sessions",
        multiplex_profiles=True,
        single_principal=policy,
        access_registry=_registry(),
        write_sessions_json=False,
        platforms={
            Platform.TELEGRAM: PlatformConfig(enabled=True, token="test-token")
        },
    )
    runner = gateway_run.GatewayRunner(config)

    captured = {}
    real_bind = gateway_run.GatewayRunner._bind_shared_memory

    def observe_shared_bind(agent, scope, memory_config, **kwargs):
        real_bind(agent, scope, memory_config, **kwargs)
        captured["agent"] = agent
        captured["scope"] = scope

    monkeypatch.setattr(
        gateway_run.GatewayRunner,
        "_bind_shared_memory",
        staticmethod(observe_shared_bind),
    )

    adapter = TelegramAdapter(config.platforms[Platform.TELEGRAM])
    adapter.send = AsyncMock(
        return_value=SendResult(success=True, message_id="sent-1")
    )
    adapter.send_typing = AsyncMock()
    adapter.stop_typing = AsyncMock()
    adapter.set_message_handler(runner._handle_message)
    runner.adapters[Platform.TELEGRAM] = adapter

    event = MessageEvent(
        text="Reply exactly GROUP_SMOKE_OK",
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id=CHAT_ID,
            chat_type="group",
            user_id=USER_ID,
            thread_id=THREAD_ID,
            route_account=ACCOUNT,
        ),
        message_id="inbound-1",
    )

    caplog.set_level(logging.ERROR, logger="gateway.run")
    await adapter.handle_message(event)
    await _wait_for_delivery(adapter)

    delivered = [
        call.kwargs.get("content")
        if "content" in call.kwargs
        else call.args[1]
        for call in adapter.send.await_args_list
    ]
    assert "GROUP_SMOKE_OK" in delivered
    assert not any(
        "Sorry, I encountered an unexpected error" in text for text in delivered
    )
    assert not any(
        record.getMessage().startswith("Agent error") for record in caplog.records
    )

    agent = captured["agent"]
    assert captured["scope"] is not None
    memory_namespace = shared_memory_namespace_for_access_context(
        event.source.resolved_access_context
    )
    expected_memory_dir = (
        profile_home / "memories" / "shared" / memory_namespace
    )
    assert agent.valid_tool_names == {"memory"}
    assert agent._memory_enabled is True
    assert agent._user_profile_enabled is False
    assert agent._memory_store.allow_user_profile is False
    assert agent._memory_store.access_context is event.source.resolved_access_context
    assert agent._memory_store.memory_dir == expected_memory_dir
    assert not (profile_home / "memories" / "MEMORY.md").exists()
    assert not (profile_home / "memories" / "USER.md").exists()

    assert runner._session_db is not None
    assert runner.session_store._db is not None
    runner._session_db._db.close()
    runner.session_store._db.close()
