"""Full-boundary regression for a registry-routed Telegram shared topic.

The test intentionally keeps the real ingress, session store/DB, profile
resolution, AIAgent construction, memory tool execution, and adapter delivery
path. Only provider response and Telegram send are external seams.
"""

import asyncio
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
)
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, SendResult
from gateway.session import SessionEntry
from gateway.session_identity import canonical_identity
from gateway.access_context import get_resolved_access_context
from hermes_constants import get_hermes_home
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
        thread_id=None,
    )
    delivery_target = DeliveryTarget(
        platform="telegram",
        account=ACCOUNT,
        peer_kind="group",
        chat_id=CHAT_ID,
        thread_id=None,
    )
    capabilities = frozenset({"room_memory", "public_web", "vision"})
    parent = SharedScopeBinding(
        principal_id="principal-room", role_id="shared_room", profile_id=PROFILE_ID,
        room_identity=room_identity, conversation_scope="room", delivery_target=delivery_target,
        participant_identities=(ParticipantIdentity("telegram", ACCOUNT, USER_ID),))
    exact = SharedScopeBinding(
        principal_id="principal-exact", role_id="shared_room", profile_id="exact-profile",
        room_identity=TransportIdentity("telegram", ACCOUNT, "group", USER_ID, CHAT_ID, "33"),
        conversation_scope="exact-room",
        delivery_target=DeliveryTarget("telegram", ACCOUNT, "group", CHAT_ID, "33"),
        participant_identities=(ParticipantIdentity("telegram", ACCOUNT, USER_ID),))
    return AccessRegistry(
        roles={"shared_room": RolePolicy("shared_room", capabilities)},
        profiles=frozenset({PROFILE_ID, "exact-profile"}),
        shared_scope_bindings=(parent, exact),
        scope_capabilities={"room": capabilities, "exact-room": capabilities},
        backend_capabilities=capabilities,
    )


def _provider_response(text: str | None, tools=None):
    message = SimpleNamespace(
        content=text,
        tool_calls=tools,
        reasoning=None,
        reasoning_content=None,
        reasoning_details=None,
    )
    choice = SimpleNamespace(message=message, finish_reason="tool_calls" if tools else "stop")
    return SimpleNamespace(choices=[choice], model="test-model", usage=None)


async def _wait_for_delivery(adapter: TelegramAdapter) -> None:
    tasks = tuple(adapter._session_tasks.values())
    assert tasks, "Telegram adapter did not create a processing task"
    await asyncio.wait_for(
        asyncio.gather(*tasks),
        timeout=15,
    )
    assert adapter.send.called, "Telegram delivery did not complete"


@pytest.mark.skip(reason="restored-lane delivery needs the 0005 restart-authority port")
@pytest.mark.asyncio
async def test_parent_topics_exact_override_memory_restore_and_delivery_at_full_boundary(
    monkeypatch,
    tmp_path,
    request,
):
    from agent.secret_scope import is_multiplex_active, set_multiplex_active

    previous_multiplex_state = is_multiplex_active()
    request.addfinalizer(
        lambda: set_multiplex_active(previous_multiplex_state)
    )

    home = tmp_path / "hermes-home"
    profile_home = home / "profiles" / PROFILE_ID
    for profile in (PROFILE_ID, "exact-profile"):
        for relative in ("sessions", "memories", "logs", "workspace", "home"):
            (home / "profiles" / profile / relative).mkdir(parents=True, exist_ok=True)
        (home / "profiles" / profile / "config.yaml").write_text("memory:\n  user_profile_enabled: false\nmodel:\n  context_length: 100000\nauxiliary:\n  title_generation:\n    enabled: false\n")

    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(gateway_run, "_hermes_home", home)
    monkeypatch.setattr(gateway_run, "_env_path", home / ".env")
    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", home / "state.db")
    monkeypatch.setattr(
        gateway_run,
        "_load_gateway_config",
        lambda: {
            "platform_toolsets": {"telegram": ["memory"]},
            "display": {"tool_progress": "off", "thinking_progress": False},
            "memory": {"user_profile_enabled": False},
            "agent": {"max_iterations": 3},
        },
    )
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

    captured = []
    provider_client = MagicMock()
    def client_factory(agent, *args, **kwargs):
        if agent not in captured:
            captured.append(agent)
        return provider_client
    monkeypatch.setattr(run_agent.AIAgent, "_create_openai_client", client_factory)
    monkeypatch.setattr("agent.process_bootstrap.OpenAI", lambda *a, **k: provider_client)


    config = GatewayConfig(
        sessions_dir=home / "sessions",
        multiplex_profiles=True,
        access_registry=_registry(),
        write_sessions_json=False,
        platforms={
            Platform.TELEGRAM: PlatformConfig(enabled=True, token="test-token", extra={"account": ACCOUNT})
        },
    )
    runner = gateway_run.GatewayRunner(config)

    adapter = TelegramAdapter(config.platforms[Platform.TELEGRAM])
    adapter.send = AsyncMock(
        return_value=SendResult(success=True, message_id="sent-1")
    )
    adapter.send_typing = AsyncMock()
    adapter.stop_typing = AsyncMock()
    adapter.gateway_runner = runner
    adapter.set_message_handler(runner._handle_message)
    runner.adapters[Platform.TELEGRAM] = adapter

    from openai.types.chat import ChatCompletionMessageToolCall
    import json
    from hashlib import sha256

    # Independent fixture oracle: literal six-field inputs, not production namespace helpers.
    expectations = (("31", "room-profile", "room", "principal-room"),
                    ("32", "room-profile", "room", "principal-room"),
                    ("33", "exact-profile", "exact-room", "principal-exact"),
                    ("31", "room-profile", "room", "principal-room"))
    try:
        for thread, profile, scope, principal in expectations:
            calls = []
            tool = ChatCompletionMessageToolCall(id="memory-write-" + thread, type="function",
                function={"name": "memory", "arguments": json.dumps({"action": "add", "target": "memory", "content": "fixture topic " + thread})})
            def response(**kwargs):
                context = get_resolved_access_context()
                calls.append((context, get_hermes_home(), kwargs))
                if len(calls) == 1:
                    return _provider_response(None, [tool])
                return _provider_response("GROUP_SMOKE_OK_" + thread)
            provider_client.chat.completions.create.side_effect = response
            adapter.send.reset_mock()
            event = MessageEvent(text="Save fixture topic and reply", message_id="inbound-" + thread,
                source=adapter.build_source(chat_id=CHAT_ID, chat_type="group", user_id=USER_ID, thread_id=thread))
            await adapter.handle_message(event)
            await _wait_for_delivery(adapter)
            assert len(calls) == 2, "real memory tool must complete before final model reply"
            assert all(c.profile_id == profile and c.conversation_scope == scope and
                       c.principal_id == principal and c.delivery_target.thread_id == thread
                       for c, _, _ in calls)
            assert all(h == home / "profiles" / profile for _, h, _ in calls)
            tool_results = [m for m in calls[-1][2]["messages"] if m.get("role") == "tool"]
            assert tool_results and json.loads(tool_results[-1]["content"])["success"] is True
            digest = sha256("\0".join((profile, scope, "telegram", ACCOUNT, "group", CHAT_ID, thread)).encode()).hexdigest()
            memory_file = home / "profiles" / profile / "memories" / "shared" / "access" / digest / "MEMORY.md"
            assert memory_file.read_text().strip() == "fixture topic " + thread
            delivered = adapter.send.await_args_list
            assert any((c.kwargs.get("content") or (c.args[1] if len(c.args) > 1 else "")) == "GROUP_SMOKE_OK_" + thread for c in delivered)
            assert all((c.kwargs.get("metadata") or {}).get("thread_id") == thread for c in delivered)
            owner = next(a for a in captured if a._memory_store.access_context.delivery_target.thread_id == thread)
            assert owner._memory_store.allow_user_profile is False
            entry = runner.session_store.get_or_create_session(event.source)
            durable = SessionEntry.from_dict(json.loads(json.dumps(entry.to_dict())))
            restored = runner._restored_source(durable)
            identity = canonical_identity(restored, runner=runner)
            assert identity.runtime_profile == profile
            assert identity.transport is None
            assert runner._delivery_adapter_for(restored) is adapter
            durable.origin.thread_id = "34"
            assert runner._restored_source(durable) is None
            restored.user_id = "unregistered"
            sends_before = adapter.send.call_count
            result, _ = await adapter.send_final_ledgered(
                MessageEvent(text="", source=restored), entry.session_key,
                "PRIVATE_FIXTURE_MUST_NOT_BE_DELIVERED", {"thread_id": thread}, reply_to=None)
            assert result.success is False
            assert adapter.send.call_count == sends_before

        # Real background-process producers and gateway consumers, under a foreign caller topic.
        import time
        import tools.process_registry as processes
        from gateway.access_runtime import turn_access_scope
        from gateway.access_context import set_session_vars, clear_session_vars, get_session_env
        from tools.terminal_tool_background import _stamp_gateway_routing
        from tools.process_registry import format_process_notification

        adapter._mark_connected()
        registry = processes.ProcessRegistry()
        monkeypatch.setattr(processes, "process_registry", registry)
        producer = adapter.build_source(chat_id=CHAT_ID, chat_type="group", user_id=USER_ID, thread_id="31")
        foreground = adapter.build_source(chat_id=CHAT_ID, chat_type="group", user_id=USER_ID, thread_id="32")
        producer_entry = runner.session_store.get_or_create_session(producer)
        tokens = set_session_vars(platform="telegram", chat_id=CHAT_ID, chat_type="group", user_id=USER_ID,
                                  thread_id="31", session_key=producer_entry.session_key,
                                  session_id=producer_entry.session_id)
        try:
            with turn_access_scope(producer, runner=runner), runner._profile_scope_for_source(producer):
                process = registry.spawn_local("printf PROCESS_FIXTURE_31; exec sleep 20", cwd=str(get_hermes_home()),
                                               task_id="boundary-process", session_key=producer_entry.session_key)
                _stamp_gateway_routing(process, get_session_env)
                process.notify_on_complete = True
                process.watch_patterns = ["WATCH_EVENT_31"]
            for kind, marker in (("watch_match", "WATCH_EVENT_31"), ("heartbeat", "HEART_EVENT_31"),
                                 ("completion", "PROCESS_FIXTURE_31")):
                calls = []
                def process_response(**kwargs):
                    calls.append((get_resolved_access_context(), get_hermes_home(), kwargs))
                    return _provider_response("PROCESS_EVENT_OK_31")
                provider_client.chat.completions.create.side_effect = process_response
                adapter.send.reset_mock()
                with turn_access_scope(foreground, runner=runner), runner._profile_scope_for_source(foreground):
                    if kind == "watch_match":
                        registry._check_watch_patterns(process, marker + "\n")
                        await runner._drain_watch_notifications(registry.completion_queue)
                    elif kind == "heartbeat":
                        process.append_output(marker + "\n")
                        registry._emit_heartbeat(process, time.time())
                        await runner._drain_watch_notifications(registry.completion_queue)
                    else:
                        registry.kill_process(process.id, consume_output=False)
                        event = registry.completion_queue.get(timeout=5)
                        assert event["type"] == "completion"
                        await runner._enqueue_process_completion_notification(format_process_notification(event), event)
                    await _wait_for_delivery(adapter)
                    assert get_resolved_access_context().delivery_target.thread_id == "32"
                assert len(calls) == 1
                context, scoped_home, provider_request = calls[0]
                assert context.profile_id == "room-profile"
                assert context.delivery_target.thread_id == "31"
                assert scoped_home == home / "profiles" / "room-profile"
                assert marker in json.dumps(provider_request["messages"])
                delivered = adapter.send.await_args_list
                assert any((c.kwargs.get("content") or (c.args[1] if len(c.args) > 1 else "")) == "PROCESS_EVENT_OK_31" for c in delivered)
                assert all((c.kwargs.get("chat_id") or c.args[0]) == CHAT_ID for c in delivered)
                assert all((c.kwargs.get("metadata") or {}).get("thread_id") == "31" for c in delivered)
            # Fresh registry adopts the real PID; its restored watcher runs the native completion body.
            with turn_access_scope(producer, runner=runner), runner._profile_scope_for_source(producer):
                restored_process = registry.spawn_local("printf RECOVER_NOTIFY_31; exec sleep 20",
                    cwd=str(get_hermes_home()), task_id="recovered-boundary", session_key=producer_entry.session_key)
                _stamp_gateway_routing(restored_process, get_session_env)
                restored_process.notify_on_complete = True
                restored_process.watcher_interval = 0.01
                registry._write_checkpoint()
                recovered = processes.ProcessRegistry()
                assert recovered.recover_from_checkpoint() == 1
                watcher = recovered.pending_watchers[-1]
            monkeypatch.setattr(processes, "process_registry", recovered)
            calls = []
            adapter.send.reset_mock()
            try:
                with turn_access_scope(foreground, runner=runner), runner._profile_scope_for_source(foreground):
                    recovered.kill_process(restored_process.id, consume_output=False)
                    assert runner._build_process_event_source(watcher) is not None
                    finished = recovered.get(restored_process.id)
                    assert finished.exited
                    completion = runner._build_process_completion_event(watcher, finished, restored_process.id)
                    assert runner._build_process_event_source(completion) is not None
                    assert not recovered.is_completion_consumed(restored_process.id)
                    await asyncio.wait_for(runner._run_process_watcher(watcher), timeout=15)
                    await _wait_for_delivery(adapter)
                    assert get_resolved_access_context().delivery_target.thread_id == "32"
                assert len(calls) == 1
                assert calls[0][0].delivery_target.thread_id == "31"
                assert calls[0][0].profile_id == "room-profile"
                assert calls[0][1] == home / "profiles" / "room-profile"
                assert "RECOVER_NOTIFY_31" in json.dumps(calls[0][2]["messages"])
                assert all((c.kwargs.get("metadata") or {}).get("thread_id") == "31" for c in adapter.send.await_args_list)
            finally:
                recovered.kill_all()
        finally:
            registry.kill_all()
            clear_session_vars(tokens)

        before = provider_client.chat.completions.create.call_count
        adapter.send.reset_mock()
        denied = MessageEvent(text="unregistered fixture", source=adapter.build_source(
            chat_id=CHAT_ID, chat_type="group", user_id="unregistered", thread_id="31"))
        await adapter.handle_message(denied)
        tasks = tuple(adapter._session_tasks.values())
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=15)
        assert provider_client.chat.completions.create.call_count == before
        assert not adapter.send.called
        assert not (profile_home / "memories" / "MEMORY.md").exists()
        assert not (profile_home / "memories" / "USER.md").exists()
    finally:
        runner.close_all_session_db_handles()
        runner.session_store.close_all_db_handles()
