from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.access_registry import DeliveryTarget, ResolvedAccessContext
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource
from gateway.session_context import bind_resolved_access_context


def _context() -> ResolvedAccessContext:
    return ResolvedAccessContext(
        principal_id="principal-a",
        role_id="family_standard",
        profile_id="profile-a",
        conversation_scope="private",
        capabilities=frozenset({"public_web"}),
        delivery_target=DeliveryTarget(
            platform="telegram",
            account="bot-a",
            peer_kind="dm",
            chat_id="u1",
        ),
    )


def _source() -> SessionSource:
    source = SessionSource(
        platform=Platform.TELEGRAM,
        user_id="u1",
        chat_id="u1",
        chat_type="dm",
    )
    source.resolved_access_context = _context()
    source.profile = "profile-a"
    source.route_account = "bot-a"
    return source


def _runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="token")},
        multiplex_profiles=True,
    )
    runner.access_registry = SimpleNamespace(
        validate_resolved_context=lambda context: context
        if isinstance(context, ResolvedAccessContext)
        else (_ for _ in ()).throw(AssertionError("missing context"))
    )
    runner._session_key_for_source = lambda _source: "session-key-a"
    runner._reply_anchor_for_event = lambda _event: None
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._active_session_leases = {}
    runner._session_run_generation = {}
    runner._pending_messages = {}
    runner._queued_events = {}
    runner._busy_ack_ts = {}
    runner._busy_input_mode = "interrupt"
    runner._draining = False
    runner._external_drain_active = False
    runner._persist_active_agents = lambda: None
    runner.hooks = SimpleNamespace(emit_collect=AsyncMock(return_value=[]))
    runner._background_tasks = set()
    return runner


@pytest.mark.parametrize(
    "hostile_args",
    [
        {"profile_id": "profile-b"},
        {"role_id": "owner"},
        {"session_id": "foreign-session"},
        {"namespace": "foreign-namespace"},
        {"delivery_target": {"chat_id": "foreign-chat"}},
    ],
)
def test_typed_model_args_with_extra_authority_selector_deny_before_tool_dispatch(hostile_args):
    from model_tools import handle_function_call
    from tools.registry import registry

    calls = []
    tool_name = "authority_guard_dummy"
    registry.register(
        name=tool_name,
        toolset="test",
        schema={
            "name": tool_name,
            "description": "dummy",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
        },
        handler=lambda args, **_kw: calls.append(dict(args)) or '{"ok": true}',
    )
    try:
        with bind_resolved_access_context(_context()):
            result = handle_function_call(
                tool_name,
                {"query": "hello", **hostile_args},
            )
    finally:
        registry.deregister(tool_name)

    assert "authority selector denied" in result
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "/background --profile profile-b run this",
        "/background --role-id owner run this",
        "/background --session-id foreign-session run this",
        "/background --namespace foreign-namespace run this",
        "/background --delivery-target foreign-chat run this",
    ],
)
async def test_typed_command_args_with_authority_selector_deny_before_background_task(text):
    runner = _runner()
    event = MessageEvent(
        text=text,
        source=_source(),
    )
    event.internal = True
    runner._run_background_task = MagicMock(
        side_effect=AssertionError("background task must not start")
    )

    result = await runner._handle_message(event)

    assert "Authority selector denied" in result
    runner._run_background_task.assert_not_called()
    assert runner._background_tasks == set()


@pytest.mark.asyncio
@pytest.mark.parametrize("context", [None, {"profile_id": "profile-a"}])
async def test_typed_command_with_missing_or_malformed_context_denies_before_background_task(context):
    runner = _runner()
    source = _source()
    source.resolved_access_context = context
    event = MessageEvent(text="/background --profile profile-b run this", source=source)
    event.internal = True
    runner._run_background_task = MagicMock(
        side_effect=AssertionError("background task must not start")
    )

    result = await runner._handle_message(event)

    assert result is None
    runner._run_background_task.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tail",
    [
        "profile_id=profile-b",
        "role_id=owner",
        "session_id=foreign-session",
        "namespace=foreign-namespace",
        "delivery_target=foreign-chat",
    ],
)
async def test_telegram_slash_confirm_callback_payload_with_authority_selector_denies_without_resolve(tail):
    from plugins.platforms.telegram.adapter import TelegramAdapter
    from tools import slash_confirm as slash_confirm

    adapter = object.__new__(TelegramAdapter)
    adapter._slash_confirm_state = {"cid-a": "session-key-a"}
    adapter._is_callback_user_authorized = lambda *args, **kwargs: True
    adapter.format_message = lambda text: text
    adapter._link_preview_kwargs = lambda: {}

    resolved = []

    async def handler(choice):
        resolved.append(choice)
        return "done"

    slash_confirm.register("session-key-a", "cid-a", "new", handler)

    query = MagicMock()
    query.data = f"sc:once:cid-a:{tail}"
    query.message = MagicMock()
    query.message.chat_id = 1
    query.message.chat.type = "private"
    query.from_user = MagicMock(id="1", first_name="Tester")
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()

    await adapter._handle_callback_query(SimpleNamespace(callback_query=query), MagicMock())

    assert resolved == []
    assert slash_confirm.get_pending("session-key-a") is not None
    assert adapter._slash_confirm_state["cid-a"] == "session-key-a"
    assert "authority selector denied" in query.answer.await_args.kwargs["text"]
    query.edit_message_text.assert_not_called()
    slash_confirm.clear("session-key-a")
