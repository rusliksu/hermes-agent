"""Process events must carry original authority and never fall back to foreground routing."""
import time
from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

import tools.process_registry as processes
from gateway.access_runtime import turn_access_scope
from gateway.config import Platform
from gateway.session import SessionStore
from gateway.access_context import get_resolved_access_context
from gateway.session_context import clear_session_vars, set_session_vars
from hermes_constants import get_hermes_home
from tests.gateway.test_access_native_identity import received, rig

_FIXTURES = (rig, received)  # imported for pytest fixture discovery, not called here
from tools.terminal_tool_background import _stamp_gateway_routing, _register_completion_watcher


@pytest.fixture
def seeded(rig, monkeypatch):
    store = SessionStore(sessions_dir=get_hermes_home() / "sessions", config=rig.config)
    rig.session_store = store
    rig.adapters[Platform.TELEGRAM]._mark_connected()
    source = received(rig)
    entry = store.get_or_create_session(source)
    registry = processes.ProcessRegistry()
    monkeypatch.setattr(processes, "process_registry", registry)
    tokens = set_session_vars(platform="telegram", chat_id="1001", chat_type="dm", user_id="1001",
                             session_id=entry.session_id, session_key=entry.session_key)
    with turn_access_scope(source, runner=rig), rig._profile_scope_for_source(source):
        session = registry.spawn_local("printf PRIVATE_PROCESS_FIXTURE; exec sleep 20", cwd=str(get_hermes_home()),
                                      task_id="fixture-task", session_key=entry.session_key)
        from gateway.session_context import get_session_env
        _stamp_gateway_routing(session, get_session_env)
        session.notify_on_complete = True
        session.watch_patterns = ["WATCH_EVENT_FIXTURE"]
    yield rig, registry, source, session, entry
    registry.kill_all()
    store.close_all_db_handles()
    clear_session_vars(tokens)


def synthetic_event(session, entry):
    return {"type": "completion", "session_id": session.id, "session_key": entry.session_key,
            "parent_session_id": entry.session_id, "command": "synthetic", "output": "PRIVATE_PROCESS_FIXTURE",
            "exit_code": 0, "resolved_access_context": deepcopy(session.resolved_access_context)}


@pytest.mark.parametrize("kind", ["completion", "watch_match", "heartbeat", "live_watcher", "recovered_watcher"])
def test_native_producer_carries_independent_context_snapshot(seeded, kind):
    rig, registry, source, session, entry = seeded
    with turn_access_scope(source, runner=rig), rig._profile_scope_for_source(source):
        if kind == "completion":  # health: allow HX011 -- one producer per event kind, distinct registry APIs
            registry.kill_process(session.id)
            event = registry.completion_queue.get(timeout=5)
        elif kind == "watch_match":
            registry._check_watch_patterns(session, "WATCH_EVENT_FIXTURE\n")
            event = registry.completion_queue.get(timeout=5)
        elif kind == "heartbeat":
            from tests.waiting import wait_until

            # 0.23 skips a heartbeat tick with no new output: wait for the child's printf.
            wait_until(lambda: session.total_output_chars > 0, timeout=10)
            registry._emit_heartbeat(session, time.time())
            event = registry.completion_queue.get(timeout=5)
        elif kind == "live_watcher":
            _register_completion_watcher(registry, session, entry.session_key)
            event = registry.pending_watchers[-1]
        else:
            session.watcher_interval = 5
            registry._write_checkpoint()
            fresh = processes.ProcessRegistry()
            assert fresh.recover_from_checkpoint() == 1
            event = fresh.pending_watchers[-1]
    assert event.get("resolved_access_context", {}).get("profile_id") == "alpha"
    assert event["parent_session_id"] == entry.session_id
    if kind in {"completion", "watch_match", "heartbeat"}:
        assert event["type"] == kind
    try:
        session.resolved_access_context["delivery_target"]["chat_id"] = "tampered"
        assert event["resolved_access_context"]["delivery_target"]["chat_id"] == "1001"
    finally:
        session.resolved_access_context["delivery_target"]["chat_id"] = "1001"


@pytest.mark.parametrize("tamper", ["missing", "profile", "session_key", "chat", "grants", "account"])
def test_source_rejects_missing_or_conflicting_process_authority(seeded, tamper):
    rig, _registry, _source, session, entry = seeded
    event = synthetic_event(session, entry)
    if tamper == "missing":  # health: allow HX011 -- one tampering axis per row, distinct event fields
        event.pop("resolved_access_context")
    elif tamper == "profile":
        event["resolved_access_context"]["profile_id"] = "beta"
    elif tamper == "session_key":
        other = rig.session_store.get_or_create_session(received(rig, "1002"))
        event["session_key"] = other.session_key
    elif tamper == "chat":
        event["chat_id"] = "1002"
    elif tamper == "grants":
        old = rig.access_registry
        rig.access_registry = replace(old, roles={name: replace(role, capabilities=role.capabilities - {"terminal"}) for name, role in old.roles.items()})
    else:
        rig.adapters[Platform.TELEGRAM].config.extra["account"] = "different-bot"
    assert rig._build_process_event_source(event) is None


@pytest.mark.asyncio
async def test_completion_scope_binds_authority_and_restores_caller(seeded):
    rig, _registry, _source, session, entry = seeded
    event = synthetic_event(session, entry)
    async with rig._completion_event_scope(event):
        assert get_resolved_access_context().profile_id == "alpha"
        assert get_hermes_home().name == "alpha"
    assert get_resolved_access_context() is None


@pytest.mark.asyncio
async def test_invalid_process_event_is_dropped_before_model_injection(seeded):
    rig, _registry, _source, session, entry = seeded
    event = synthetic_event(session, entry)
    event.pop("resolved_access_context")
    adapter = rig.adapters[Platform.TELEGRAM]
    adapter.handle_message = AsyncMock()
    result = await rig._inject_watch_notification("PRIVATE_PROCESS_FIXTURE", event)
    assert result is None
    adapter.handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_receipt_uses_validated_target_not_caller_arguments(seeded):
    rig, _registry, _source, session, entry = seeded
    event = synthetic_event(session, entry)
    adapter = rig.adapters[Platform.TELEGRAM]
    adapter.send = AsyncMock()
    await rig._send_watcher_message("telegram", "1002", "foreign-thread", "PRIVATE_PROCESS_FIXTURE", event)
    assert adapter.send.await_count == 1
    assert adapter.send.await_args.args[0] == "1001"
    assert not (adapter.send.await_args.kwargs.get("metadata") or {}).get("thread_id")
    event.pop("resolved_access_context")
    adapter.send.reset_mock()
    await rig._send_watcher_message("telegram", "1001", None, "PRIVATE_PROCESS_FIXTURE", event)
    adapter.send.assert_not_awaited()
