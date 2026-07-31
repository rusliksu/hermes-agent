"""Lifecycle-scoped gateway delivery regressions for terminal completions.

The gateway contract here is deliberately narrower than exactly-once: one live
GatewayRunner suppresses concurrent/replayed copies after successful adapter
injection, failed injection remains retryable, and durable async-delegation
state (when available) is acknowledged through its authoritative SQLite API.
"""

import asyncio
import json
import queue
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import Platform
from gateway.access_registry import (
    AccessRegistry,
    DeliveryTarget,
    PrincipalBinding,
    ResolvedAccessContext,
    RolePolicy,
    TransportIdentity,
    serialize_resolved_access_context,
)
from gateway.run import GatewayRunner
from gateway.session import SessionSource
from tools.process_registry import ProcessRegistry, ProcessSession
from tools.async_delegation import ASYNC_DELEGATION_ACCESS_CONTEXT_KEY


@pytest.fixture(autouse=True)
def isolated_registry(tmp_path, monkeypatch):
    """Any current/future durable compatibility path must stay in tmp state."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import tools.process_registry as pr_module

    monkeypatch.setattr(pr_module, "CHECKPOINT_PATH", tmp_path / "processes.json")
    registry = pr_module.ProcessRegistry()
    monkeypatch.setattr(pr_module, "process_registry", registry)
    return registry


def _runner(adapter, *, origins=None, access_registry=None):
    runner = object.__new__(GatewayRunner)
    runner._running = True
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._profile_adapters = {}
    if access_registry is not None:
        runner._profile_adapters["family-profile"] = {Platform.TELEGRAM: adapter}
    runner.session_store = SimpleNamespace(
        _ensure_loaded=lambda: None,
        _entries=origins or {},
    )
    runner.config = SimpleNamespace(multiplex_profiles=bool(access_registry))
    runner.access_registry = access_registry
    runner._session_source_cache = {}
    runner._completion_delivery_lock = __import__("threading").Lock()
    runner._completion_deliveries_inflight = set()
    runner._completion_deliveries_delivered = OrderedDict()
    runner._completion_delivery_retention = 2048
    return runner


def _access_context(*, chat_id="12345", profile_id="family-profile"):
    return ResolvedAccessContext(
        principal_id="principal-family",
        role_id="family_sandbox",
        profile_id=profile_id,
        conversation_scope="private",
        capabilities=frozenset({"delegation", "public_web"}),
        delivery_target=DeliveryTarget(
            platform="telegram",
            account="synthetic-account",
            peer_kind="dm",
            chat_id=chat_id,
            thread_id=None,
        ),
    )


def _access_registry(context=None):
    context = context or _access_context()
    return AccessRegistry(
        roles={
            context.role_id: RolePolicy(
                context.role_id,
                context.capabilities,
            )
        },
        profiles=frozenset({context.profile_id}),
        principal_bindings=(
            PrincipalBinding(
                principal_id=context.principal_id,
                role_id=context.role_id,
                profile_id=context.profile_id,
                transport_identity=TransportIdentity(
                    platform="telegram",
                    account=context.delivery_target.account,
                    peer_kind="dm",
                    user_id=context.delivery_target.chat_id,
                    chat_id=context.delivery_target.chat_id,
                ),
                conversation_scope=context.conversation_scope,
                delivery_target=context.delivery_target,
            ),
        ),
        shared_scope_bindings=(),
        scope_capabilities={context.conversation_scope: context.capabilities},
        backend_capabilities=context.capabilities,
    )


def _origin_for(context):
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id=context.delivery_target.chat_id,
        chat_type=context.delivery_target.peer_kind,
        user_id=context.delivery_target.chat_id,
    )


def _async_event(delegation_id="deleg_duplicate"):
    return {
        "type": "async_delegation",
        "delegation_id": delegation_id,
        "session_key": "agent:main:telegram:dm:12345:678",
        "goal": "Investigate flaky test",
        "status": "completed",
        "summary": "Found it",
        "api_calls": 1,
        "duration_seconds": 12.0,
        "dispatched_at": 1000.0,
        "completed_at": 1012.0,
        # PR #62479 stamps these on gateway-owned events. They must not
        # change the producer identity used for queue replay.
        "origin_profile": "default",
        "origin_hermes_home": "/tmp/hermes-default",
    }


def _completion_event(*, started_at, session_id="proc_reused"):
    return {
        "type": "completion",
        "session_id": session_id,
        "session_key": "agent:main:telegram:dm:123",
        "platform": "telegram",
        "chat_type": "dm",
        "chat_id": "123",
        "started_at": started_at,
        "command": "echo done",
        "exit_code": 0,
        "completion_reason": "exited",
        "output": "done\n",
    }


def _terminal_event(evt_type="completion", *, context=None):
    context = context or _access_context()
    evt = _completion_event(started_at=1234.5, session_id="proc_terminal_context")
    evt["type"] = evt_type
    evt["session_key"] = "agent:main:telegram:dm:12345"
    evt["chat_id"] = context.delivery_target.chat_id
    evt["user_id"] = context.delivery_target.chat_id
    if evt_type == "watch_match":
        evt.update({"pattern": "READY", "output": "READY\n"})
    elif evt_type == "watch_disabled":
        evt.update({"message": "watch disabled"})
    evt[ASYNC_DELEGATION_ACCESS_CONTEXT_KEY] = serialize_resolved_access_context(context)
    return evt


def _stop_after_sleeps(monkeypatch, runner, count):
    sleep_calls = 0

    async def _bounded_sleep(_delay):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= count:
            runner._running = False

    monkeypatch.setattr(asyncio, "sleep", _bounded_sleep)


def test_duplicate_async_queue_replay_injects_once(monkeypatch, isolated_registry):
    """Byte-identical queue replays produce one turn in one gateway lifecycle."""
    isolated = queue.Queue()
    monkeypatch.setattr(isolated_registry, "completion_queue", isolated)
    isolated.put(dict(_async_event()))
    isolated.put(dict(_async_event()))

    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter)
    _stop_after_sleeps(monkeypatch, runner, count=2)

    asyncio.run(runner._async_delegation_watcher(interval=0))

    adapter.handle_message.assert_awaited_once()


def test_unroutable_async_event_is_not_requeued_forever(
    monkeypatch, isolated_registry,
):
    isolated = queue.Queue()
    monkeypatch.setattr(isolated_registry, "completion_queue", isolated)
    event = _async_event("deleg_desktop_or_cli")
    event["session_key"] = "20260711_unparseable_ui_session"
    isolated.put(event)

    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter)
    _stop_after_sleeps(monkeypatch, runner, count=2)

    asyncio.run(runner._async_delegation_watcher(interval=0))

    adapter.handle_message.assert_not_awaited()
    assert isolated.empty()


def test_concurrent_claims_share_the_same_narrow_delivery_seam():
    """Concurrent consumers in one runner cannot both enter the adapter."""
    entered = asyncio.Event()
    release = asyncio.Event()

    async def _blocked_injection(_event):
        entered.set()
        await release.wait()

    adapter = SimpleNamespace(handle_message=AsyncMock(side_effect=_blocked_injection))
    runner = _runner(adapter)
    event = _async_event()
    text = "completion"

    async def _exercise():
        first = asyncio.create_task(runner._deliver_completion_notification(text, dict(event)))
        await entered.wait()
        second = asyncio.create_task(runner._deliver_completion_notification(text, dict(event)))
        await asyncio.sleep(0)
        release.set()
        return await asyncio.gather(first, second)

    assert sorted(asyncio.run(_exercise()), key=str) == [None, True]
    adapter.handle_message.assert_awaited_once()


def test_failed_async_injection_is_retried_and_only_success_is_acked(
    monkeypatch, isolated_registry,
):
    isolated = queue.Queue()
    monkeypatch.setattr(isolated_registry, "completion_queue", isolated)
    isolated.put(_async_event())

    adapter = SimpleNamespace(
        handle_message=AsyncMock(side_effect=[RuntimeError("temporary"), None])
    )
    runner = _runner(adapter)
    _stop_after_sleeps(monkeypatch, runner, count=3)

    from tools import async_delegation

    acknowledgements = []
    monkeypatch.setattr(
        async_delegation,
        "complete_completion_delivery",
        lambda delegation_id, _claim_id: acknowledgements.append(delegation_id) or True,
        raising=False,
    )

    asyncio.run(runner._async_delegation_watcher(interval=0))

    assert adapter.handle_message.await_count == 2
    assert acknowledgements == ["deleg_duplicate"]


def test_distinct_process_incarnations_are_not_deduplicated():
    """Producer spawn time distinguishes a reused process session ID."""
    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter)

    async def _exercise():
        first = await runner._deliver_completion_notification(
            "first", _completion_event(started_at=10.0)
        )
        second = await runner._deliver_completion_notification(
            "second", _completion_event(started_at=20.0)
        )
        return first, second

    assert asyncio.run(_exercise()) == (True, True)

    assert adapter.handle_message.await_count == 2


def test_delivered_identity_retention_is_bounded():
    """Lifecycle dedupe cannot grow without bound in a long-running gateway."""
    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter)
    runner._completion_delivery_retention = 2
    runner._completion_deliveries_delivered = OrderedDict()

    async def _exercise():
        for index in range(3):
            await runner._deliver_completion_notification(
                f"completion {index}",
                _async_event(f"deleg_retention_{index}"),
            )

    asyncio.run(_exercise())

    assert len(runner._completion_deliveries_delivered) == 2
    assert ("async_delegation", "deleg_retention_0", "") not in (
        runner._completion_deliveries_delivered
    )
    assert ("async_delegation", "deleg_retention_2", "") in (
        runner._completion_deliveries_delivered
    )


def test_delivery_state_is_isolated_per_gateway_profile_lifecycle():
    """A process-local claim in one profile never suppresses another runner."""
    default_adapter = SimpleNamespace(handle_message=AsyncMock())
    profile_adapter = SimpleNamespace(handle_message=AsyncMock())
    default_runner = _runner(default_adapter)
    profile_runner = _runner(profile_adapter)
    event = _async_event("deleg_same_producer_id")

    async def _exercise():
        first = await default_runner._deliver_completion_notification(
            "default", dict(event),
        )
        second = await profile_runner._deliver_completion_notification(
            "profile", dict(event),
        )
        return first, second

    assert asyncio.run(_exercise()) == (True, True)
    default_adapter.handle_message.assert_awaited_once()
    profile_adapter.handle_message.assert_awaited_once()


def test_async_completion_uses_canonical_origin_routing(monkeypatch, isolated_registry):
    isolated = queue.Queue()
    monkeypatch.setattr(isolated_registry, "completion_queue", isolated)
    event = _async_event("deleg_routing")
    isolated.put(event)

    canonical = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="canonical-chat",
        chat_type="group",
        thread_id="canonical-topic",
    )
    entry = SimpleNamespace(origin=canonical)
    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter, origins={event["session_key"]: entry})
    _stop_after_sleeps(monkeypatch, runner, count=2)

    asyncio.run(runner._async_delegation_watcher(interval=0))

    delivered = adapter.handle_message.await_args.args[0]
    assert delivered.source == canonical


def test_registry_async_completion_restores_validated_context_before_adapter():
    context = _access_context()
    event = _async_event("deleg_context_restore")
    event[ASYNC_DELEGATION_ACCESS_CONTEXT_KEY] = serialize_resolved_access_context(context)

    origin = _origin_for(context)
    prior_context = _access_context(chat_id="prior-chat", profile_id="prior-profile")
    origin.resolved_access_context = prior_context
    entry = SimpleNamespace(origin=origin)
    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(
        adapter,
        origins={event["session_key"]: entry},
        access_registry=_access_registry(context),
    )

    assert asyncio.run(runner._inject_watch_notification("completion", event)) is True

    delivered = adapter.handle_message.await_args.args[0]
    assert delivered.source is not origin
    assert delivered.source.resolved_access_context == context
    assert delivered.source.profile == context.profile_id
    assert delivered.source.route_account == context.delivery_target.account
    assert origin.resolved_access_context is prior_context
    assert origin.profile is None
    assert origin.route_account is None


@pytest.mark.parametrize("evt_type", ["completion", "watch_match", "watch_disabled"])
def test_registry_terminal_notification_restores_validated_context_before_adapter(evt_type):
    context = _access_context()
    event = _terminal_event(evt_type, context=context)

    origin = _origin_for(context)
    prior_context = _access_context(chat_id="prior-chat", profile_id="prior-profile")
    origin.resolved_access_context = prior_context
    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(
        adapter,
        origins={event["session_key"]: SimpleNamespace(origin=origin)},
        access_registry=_access_registry(context),
    )

    assert asyncio.run(runner._inject_watch_notification("terminal event", event)) is True

    delivered = adapter.handle_message.await_args.args[0]
    assert delivered.source is not origin
    assert delivered.source.resolved_access_context == context
    assert delivered.source.profile == context.profile_id
    assert delivered.source.route_account == context.delivery_target.account
    assert origin.resolved_access_context is prior_context
    assert origin.profile is None
    assert origin.route_account is None


@pytest.mark.parametrize(
    "payload, reason",
    [
        (None, "missing_resolved_access_context"),
        ({"principal_id": "raw-malformed-marker"}, "malformed_resolved_access_context"),
        (
            {
                **serialize_resolved_access_context(_access_context()),
                "profile_id": "foreign-profile",
            },
            "resolved_access_context_mismatch",
        ),
    ],
)
def test_registry_async_completion_drops_bad_context_before_routing(
    payload,
    reason,
    caplog,
):
    event = _async_event("deleg_bad_context")
    if payload is not None:
        event[ASYNC_DELEGATION_ACCESS_CONTEXT_KEY] = payload

    session_store = MagicMock()
    session_store._entries = {}
    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter, access_registry=_access_registry(_access_context()))
    runner.session_store = session_store

    assert asyncio.run(runner._inject_watch_notification("completion", event)) is None

    adapter.handle_message.assert_not_awaited()
    session_store._ensure_loaded.assert_not_called()
    assert reason in caplog.text
    assert "raw-malformed-marker" not in caplog.text
    assert "foreign-profile" not in caplog.text


@pytest.mark.parametrize(
    "payload, reason",
    [
        (None, "missing_resolved_access_context"),
        ({"principal_id": "raw-malformed-marker"}, "malformed_resolved_access_context"),
        (
            {
                **serialize_resolved_access_context(_access_context()),
                "profile_id": "foreign-profile",
            },
            "resolved_access_context_mismatch",
        ),
    ],
)
def test_registry_terminal_notification_drops_bad_context_before_routing(
    payload,
    reason,
    caplog,
):
    event = _terminal_event("completion")
    if payload is None:
        event.pop(ASYNC_DELEGATION_ACCESS_CONTEXT_KEY, None)
    else:
        event[ASYNC_DELEGATION_ACCESS_CONTEXT_KEY] = payload

    session_store = MagicMock()
    session_store._entries = {}
    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter, access_registry=_access_registry(_access_context()))
    runner.session_store = session_store

    assert asyncio.run(runner._inject_watch_notification("terminal event", event)) is None

    adapter.handle_message.assert_not_awaited()
    session_store._ensure_loaded.assert_not_called()
    assert reason in caplog.text
    assert "raw-malformed-marker" not in caplog.text
    assert "foreign-profile" not in caplog.text


@pytest.mark.parametrize(
    "origin, reason, raw_marker",
    [
        (
            SessionSource(
                platform=Platform.TELEGRAM,
                chat_id="raw-foreign-chat-marker",
                chat_type="dm",
                user_id="raw-foreign-chat-marker",
            ),
            "internal_delivery_target_mismatch",
            "raw-foreign-chat-marker",
        ),
        (
            SessionSource(
                platform=Platform.TELEGRAM,
                chat_id="12345",
                chat_type="dm",
                user_id="12345",
                profile="raw-foreign-profile-marker",
            ),
            "profile_route_mismatch",
            "raw-foreign-profile-marker",
        ),
        (
            SessionSource(
                platform=Platform.TELEGRAM,
                chat_id="12345",
                chat_type="dm",
                user_id="12345",
                route_account="raw-foreign-account-marker",
            ),
            "internal_route_account_mismatch",
            "raw-foreign-account-marker",
        ),
    ],
)
def test_registry_async_completion_drops_source_mismatch_without_adapter(
    origin,
    reason,
    raw_marker,
    caplog,
):
    context = _access_context(chat_id="12345")
    prior_context = _access_context(chat_id="prior-chat", profile_id="prior-profile")
    prior_profile = origin.profile
    prior_route_account = origin.route_account
    origin.resolved_access_context = prior_context
    event = _async_event("deleg_source_mismatch")
    event[ASYNC_DELEGATION_ACCESS_CONTEXT_KEY] = serialize_resolved_access_context(context)

    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(
        adapter,
        origins={event["session_key"]: SimpleNamespace(origin=origin)},
        access_registry=_access_registry(context),
    )
    runner._adapter_for_source = MagicMock()

    assert asyncio.run(runner._inject_watch_notification("completion", event)) is None

    runner._adapter_for_source.assert_not_called()
    adapter.handle_message.assert_not_awaited()
    assert origin.resolved_access_context is prior_context
    assert origin.profile == prior_profile
    assert origin.route_account == prior_route_account
    assert reason in caplog.text
    assert raw_marker not in caplog.text


def test_registry_terminal_notification_drops_source_mismatch_without_adapter(caplog):
    context = _access_context(chat_id="12345")
    event = _terminal_event("completion", context=context)
    origin = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="raw-foreign-chat-marker",
        chat_type="dm",
        user_id="raw-foreign-chat-marker",
    )
    prior_context = _access_context(chat_id="prior-chat", profile_id="prior-profile")
    origin.resolved_access_context = prior_context

    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(
        adapter,
        access_registry=_access_registry(context),
    )
    runner._session_sources = OrderedDict([(event["session_key"], origin)])
    runner._adapter_for_source = MagicMock()

    assert asyncio.run(runner._inject_watch_notification("terminal event", event)) is None

    runner._adapter_for_source.assert_not_called()
    adapter.handle_message.assert_not_awaited()
    assert origin.resolved_access_context is prior_context
    assert origin.profile is None
    assert origin.route_account is None
    assert "internal_delivery_target_mismatch" in caplog.text
    assert "raw-foreign-chat-marker" not in caplog.text


def test_async_completion_without_registry_legacy_missing_context_still_delivers():
    event = _async_event("deleg_legacy_no_registry")
    adapter = SimpleNamespace(handle_message=AsyncMock())
    origin = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="12345",
        chat_type="dm",
        user_id="12345",
    )
    runner = _runner(adapter, origins={event["session_key"]: SimpleNamespace(origin=origin)})

    assert asyncio.run(runner._inject_watch_notification("completion", event)) is True

    adapter.handle_message.assert_awaited_once()
    assert adapter.handle_message.await_args.args[0].source is origin


def test_terminal_notification_without_registry_legacy_missing_context_still_delivers():
    event = _terminal_event("completion")
    event.pop(ASYNC_DELEGATION_ACCESS_CONTEXT_KEY, None)
    adapter = SimpleNamespace(handle_message=AsyncMock())
    origin = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="12345",
        chat_type="dm",
        user_id="12345",
    )
    runner = _runner(adapter, origins={event["session_key"]: SimpleNamespace(origin=origin)})

    assert asyncio.run(runner._inject_watch_notification("terminal event", event)) is True

    adapter.handle_message.assert_awaited_once()
    assert adapter.handle_message.await_args.args[0].source is origin


def test_explicit_kill_returns_output_before_consuming_notification(monkeypatch):
    import tools.process_registry as pr_module

    registry = ProcessRegistry()
    session = ProcessSession(
        id="proc_kill_consumed",
        command="sleep 999",
        task_id="task",
        started_at=1.0,
        output_buffer="important terminal output\n",
        notify_on_complete=True,
    )
    session.process = MagicMock()
    session.process.pid = 4242
    registry._running[session.id] = session
    monkeypatch.setattr(registry, "_terminate_host_pid", lambda *_a, **_kw: None)
    monkeypatch.setattr(registry, "_write_checkpoint", lambda: None)
    monkeypatch.setattr(pr_module, "process_registry", registry)

    result = registry.kill_process(session.id)
    assert result["status"] == "killed"
    assert result["output"] == "important terminal output\n"
    assert registry.is_completion_consumed(session.id)

    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter)

    async def _instant_sleep(*_a, **_kw):
        pass

    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
    asyncio.run(runner._run_process_watcher({
        "session_id": session.id,
        "check_interval": 0,
        "session_key": "agent:main:telegram:dm:123",
        "platform": "telegram",
        "chat_type": "dm",
        "chat_id": "123",
        "notify_on_complete": True,
    }))

    adapter.handle_message.assert_not_awaited()


def test_process_tool_redacts_explicit_kill_output(monkeypatch):
    from tools import process_registry as pr_module

    registry = ProcessRegistry()
    session = ProcessSession(
        id="proc_kill_redacted",
        command="printenv",
        task_id="task",
        started_at=1.0,
        output_buffer="PRIVATE_TOKEN=opaque-value\n",
        exited=True,
        exit_code=0,
    )
    registry._finished[session.id] = session
    monkeypatch.setattr(pr_module, "process_registry", registry)

    def _redact(result):
        assert result["output"] == "PRIVATE_TOKEN=opaque-value\n"
        result["output"] = "PRIVATE_TOKEN=<redacted>\n"
        return result

    monkeypatch.setattr(pr_module, "_redact_process_result", _redact)

    result = json.loads(pr_module._handle_process({
        "action": "kill",
        "session_id": session.id,
    }))
    assert result["output"] == "PRIVATE_TOKEN=<redacted>\n"


def test_kill_of_already_exited_process_returns_output_before_consuming():
    registry = ProcessRegistry()
    session = ProcessSession(
        id="proc_already_exited",
        command="echo complete",
        task_id="task",
        started_at=1.0,
        output_buffer="complete\n",
        exited=True,
        exit_code=0,
    )
    registry._finished[session.id] = session

    result = registry.kill_process(session.id)

    assert result["status"] == "already_exited"
    assert result["output"] == "complete\n"
    assert registry.is_completion_consumed(session.id)


def test_read_log_only_consumes_when_terminal_output_page_is_observed():
    registry = ProcessRegistry()
    session = ProcessSession(
        id="proc_paged_log",
        command="printf lines",
        task_id="task",
        started_at=1.0,
        output_buffer="first\nsecond\nfinal\n",
        exited=True,
        exit_code=0,
    )
    registry._finished[session.id] = session

    middle_page = registry.read_log(session.id, offset=1, limit=1)
    assert middle_page["output"] == "second"
    assert not registry.is_completion_consumed(session.id)

    final_page = registry.read_log(session.id, offset=2, limit=1)
    assert final_page["output"] == "final"
    assert registry.is_completion_consumed(session.id)


def test_bulk_kill_does_not_consume_discarded_completion_output(monkeypatch):
    registry = ProcessRegistry()
    session = ProcessSession(
        id="proc_bulk_kill",
        command="sleep 999",
        task_id="task",
        started_at=1.0,
        output_buffer="output bulk cleanup does not return\n",
        notify_on_complete=True,
    )
    session.process = MagicMock()
    session.process.pid = 4243
    registry._running[session.id] = session
    monkeypatch.setattr(registry, "_terminate_host_pid", lambda *_a, **_kw: None)
    monkeypatch.setattr(registry, "_write_checkpoint", lambda: None)

    assert registry.kill_all() == 1
    assert not registry.is_completion_consumed(session.id)
    queued = registry.completion_queue.get_nowait()
    assert queued["session_id"] == session.id
    assert queued["started_at"] == session.started_at
    assert queued["output"] == "output bulk cleanup does not return\n"


def test_unobserved_normal_completion_still_notifies(monkeypatch):
    import tools.process_registry as pr_module

    class _Registry:
        def get(self, _session_id):
            return SimpleNamespace(
                output_buffer="done\n",
                exited=True,
                exit_code=0,
                command="echo done",
                started_at=1234.5,
            )

        def is_completion_consumed(self, _session_id):
            return False

    monkeypatch.setattr(pr_module, "process_registry", _Registry())
    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter)

    async def _instant_sleep(*_a, **_kw):
        pass

    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
    asyncio.run(runner._run_process_watcher({
        "session_id": "proc_unobserved",
        "check_interval": 0,
        "session_key": "agent:main:telegram:dm:123",
        "platform": "telegram",
        "chat_type": "dm",
        "chat_id": "123",
        "notify_on_complete": True,
    }))

    adapter.handle_message.assert_awaited_once()


def test_autonomous_completion_redacts_real_command_and_output_secrets(monkeypatch):
    import agent.redact as redact_module
    import tools.process_registry as pr_module

    secret = "abc123randomopaquetokenvalue999"
    registry = ProcessRegistry()
    session = ProcessSession(
        id="proc_autonomous_redaction",
        command=f"printenv MY_SERVICE_TOKEN={secret}",
        task_id="task",
        started_at=1234.5,
        output_buffer=f"MY_SERVICE_TOKEN={secret}\nHOME=/home/user\n",
        exited=True,
        exit_code=0,
        notify_on_complete=True,
    )
    registry._finished[session.id] = session
    monkeypatch.setattr(pr_module, "process_registry", registry)
    monkeypatch.setattr(redact_module, "_REDACT_ENABLED", True)

    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter)

    async def _instant_sleep(*_a, **_kw):
        pass

    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
    asyncio.run(runner._run_process_watcher({
        "session_id": session.id,
        "check_interval": 0,
        "session_key": "agent:main:telegram:dm:123",
        "platform": "telegram",
        "chat_type": "dm",
        "chat_id": "123",
        "notify_on_complete": True,
    }))

    delivered = adapter.handle_message.await_args.args[0]
    assert secret not in delivered.text
    assert "HOME=/home/user" in delivered.text
