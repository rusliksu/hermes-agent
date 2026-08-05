"""Durable async delegation keeps its originating access context."""

import json
import queue
import time

from tools import async_delegation as ad
from tools.process_registry import process_registry


def _payload():
    return {
        "principal_id": "principal-42",
        "role_id": "family_sandbox",
        "profile_id": "family-42",
        "conversation_scope": "private:principal-42",
        "capabilities": ["memory_read", "delegation"],
        "delivery_target": {
            "platform": "telegram",
            "account": "main-bot",
            "peer_kind": "dm",
            "chat_id": "42",
        },
    }


def _wait_for(delegation_id, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_registry.completion_queue.empty():
            event = process_registry.completion_queue.get_nowait()
            if event.get("delegation_id") == delegation_id:
                return event
        time.sleep(0.01)
    return None


def test_async_completion_and_durable_restore_carry_context(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    payload = _payload()
    result = ad.dispatch_async_delegation(
        goal="scoped task",
        context=None,
        toolsets=None,
        role="leaf",
        model="m",
        session_key="owner-session",
        runner=lambda: {"status": "completed", "summary": "done"},
        resolved_access_context=payload,
    )

    event = _wait_for(result["delegation_id"])
    assert event is not None
    assert event["resolved_access_context"] == payload

    restored = queue.Queue()
    assert ad.restore_undelivered_completions(restored) == 1
    restored_event = restored.get_nowait()
    assert restored_event["resolved_access_context"] == payload

    with ad._DB_LOCK, ad._connect() as conn:
        row = conn.execute(
            "SELECT task_json, event_json FROM async_delegations WHERE delegation_id=?",
            (result["delegation_id"],),
        ).fetchone()
    assert json.loads(row[0])["resolved_access_context"] == payload
    assert json.loads(row[1])["resolved_access_context"] == payload
