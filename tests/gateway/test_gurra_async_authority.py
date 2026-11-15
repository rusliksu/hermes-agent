"""Detached completions must retain the dispatch authority through SQLite and profile rebind."""
from copy import deepcopy
import queue
import time

import pytest

from gateway.access_runtime import turn_access_scope
from gateway.config import Platform
from gateway.session import SessionStore
from gateway.access_context import get_resolved_access_context
from gateway.session_context import clear_session_vars, set_session_vars
from hermes_constants import get_hermes_home
from tests.gateway.test_access_native_identity import received, rig

_FIXTURES = (rig, received)  # imported for pytest fixture discovery, not called here
from tools import async_delegation as ad
import tools.process_registry as processes


@pytest.fixture
def origin(rig, monkeypatch):
    ad._reset_for_tests()
    store = SessionStore(get_hermes_home() / "sessions", rig.config)
    rig.session_store = store
    source = received(rig)
    entry = store.get_or_create_session(source)
    registry = processes.ProcessRegistry()
    monkeypatch.setattr(processes, "process_registry", registry)
    tokens = set_session_vars(platform="telegram", chat_id="1001", chat_type="dm", user_id="1001",
                              session_id=entry.session_id, session_key=entry.session_key)
    try:
        yield rig, source, entry, registry.completion_queue
    finally:
        deadline = time.monotonic() + 5
        while ad.active_count() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ad.active_count() == 0
        ad._reset_for_tests()
        clear_session_vars(tokens)
        store.close_all_db_handles()


@pytest.mark.asyncio
@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("orphan", [False, True])
async def test_native_detached_completion_retains_authority_after_durable_restore(origin, batch, orphan):
    runner, source, entry, completions = origin
    arguments = dict(context=None, toolsets=None, role="default", model=None,
                     session_key=entry.session_key, parent_session_id=entry.session_id,
                     runner=lambda: {"summary": "ASYNC_FIXTURE", "results": []})
    with turn_access_scope(source, runner=runner), runner._profile_scope_for_source(source):
        handle = (ad.dispatch_async_delegation_batch(goals=["fixture"], **arguments) if batch
                  else ad.dispatch_async_delegation(goal="fixture", **arguments))
        assert handle["status"] == "dispatched"
        event = completions.get(timeout=5)
        assert event["resolved_access_context"]["profile_id"] == "alpha"
        assert event["resolved_access_context"]["delivery_target"]["chat_id"] == "1001"
        assert event["parent_session_id"] == entry.session_id
        if orphan:
            # Reuse the real dispatch row as a crash fixture: only liveness/outcome changes.
            # Its task_json must reconstruct authority independently of completion event_json.
            with ad._transaction() as conn:
                conn.execute("""UPDATE async_delegations SET state='running', event_json=NULL,
                    result_json=NULL, owner_pid=2147483647, owner_started_at=NULL
                    WHERE delegation_id=?""", (handle["delegation_id"],))
        restored_queue = queue.Queue()
        assert ad.restore_undelivered_completions(restored_queue) == 1
        restored = restored_queue.get_nowait()
        assert restored["resolved_access_context"] == event["resolved_access_context"]
        assert restored["status"] == ("unknown" if orphan else "completed")
    other = received(runner, "1002")
    with turn_access_scope(other, runner=runner), runner._profile_scope_for_source(other):
        async with runner._completion_event_scope(restored):
            assert get_resolved_access_context().profile_id == "alpha"
            assert get_hermes_home().name == "alpha"
        assert get_resolved_access_context().profile_id == "beta"
        assert get_hermes_home().name == "beta"


@pytest.mark.parametrize("damage", ["missing", "malformed", "destination", "capability"])
def test_detached_completion_cannot_use_native_routing_fallback(origin, damage):
    runner, source, entry, _ = origin
    from gateway.access_registry import serialize_resolved_access_context
    event = {"type": "async_delegation", "session_key": entry.session_key,
             "parent_session_id": entry.session_id,
             "resolved_access_context": deepcopy(serialize_resolved_access_context(source.resolved_access_context))}
    if damage == "missing":
        del event["resolved_access_context"]
    elif damage == "malformed":
        event["resolved_access_context"]["extra"] = "untrusted"
    elif damage == "destination":
        event["resolved_access_context"]["delivery_target"]["chat_id"] = "1002"
    else:
        event["resolved_access_context"]["capabilities"] = ["private_memory", "public_web", "terminal", "documents"]
    assert runner._build_process_event_source(event) is None
