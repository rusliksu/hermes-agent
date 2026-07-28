"""Tests for kanban lifecycle plugin hooks.

Verifies that claim/complete/block transitions fire the
kanban_task_claimed / kanban_task_completed / kanban_task_blocked plugin
hooks AFTER the board DB change is committed, with the documented kwargs,
and that a misbehaving hook callback never breaks the transition.
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.plugins import VALID_HOOKS, get_plugin_manager


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


@pytest.fixture
def captured_hooks(monkeypatch):
    """Register capturing callbacks for the three kanban lifecycle hooks.

    Patches the plugin manager's _hooks dict directly (the same registry
    invoke_hook reads) and restores it afterward.
    """
    mgr = get_plugin_manager()
    events: list[tuple[str, dict]] = []
    saved = {k: list(v) for k, v in mgr._hooks.items()}
    for hook in ("kanban_task_claimed", "kanban_task_completed", "kanban_task_blocked"):
        mgr._hooks.setdefault(hook, []).append(
            lambda _h=hook, **kw: events.append((_h, kw))
        )
    try:
        yield events
    finally:
        mgr._hooks = saved


def test_hooks_are_registered_as_valid():
    """The three lifecycle hook names are part of VALID_HOOKS."""
    assert "kanban_task_claimed" in VALID_HOOKS
    assert "kanban_task_completed" in VALID_HOOKS
    assert "kanban_task_blocked" in VALID_HOOKS


def test_claim_fires_hook(kanban_home, captured_hooks):
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="t", assignee="worker")
        claimed = kb.claim_task(conn, tid)
        assert claimed is not None
    finally:
        conn.close()
    fired = [e for e in captured_hooks if e[0] == "kanban_task_claimed"]
    assert len(fired) == 1
    kw = fired[0][1]
    assert kw["task_id"] == tid
    assert kw["assignee"] == "worker"
    assert "profile_name" in kw
    assert kw["run_id"] is not None


def test_complete_fires_hook_with_summary(kanban_home, captured_hooks):
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="t", assignee="worker")
        kb.claim_task(conn, tid)
        assert kb.complete_task(conn, tid, summary="all done")
    finally:
        conn.close()
    fired = [e for e in captured_hooks if e[0] == "kanban_task_completed"]
    assert len(fired) == 1
    kw = fired[0][1]
    assert kw["task_id"] == tid
    assert kw["summary"] == "all done"
    assert kw["assignee"] == "worker"


def test_block_fires_hook_with_reason(kanban_home, captured_hooks):
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="t", assignee="worker")
        kb.claim_task(conn, tid)
        assert kb.block_task(conn, tid, reason="needs human")
    finally:
        conn.close()
    fired = [e for e in captured_hooks if e[0] == "kanban_task_blocked"]
    assert len(fired) == 1
    kw = fired[0][1]
    assert kw["task_id"] == tid
    assert kw["reason"] == "needs human"


def test_no_hook_on_failed_transition(kanban_home, captured_hooks):
    """complete_task on an unclaimed/nonexistent task fires no hook."""
    conn = kb.connect()
    try:
        # Completing a task that doesn't exist returns False without firing.
        assert kb.complete_task(conn, "t_doesnotexist", summary="x") is False
    finally:
        conn.close()
    assert [e for e in captured_hooks if e[0] == "kanban_task_completed"] == []


def test_misbehaving_hook_does_not_break_transition(kanban_home, monkeypatch):
    """A hook callback that raises must not break the board transition."""
    mgr = get_plugin_manager()
    saved = {k: list(v) for k, v in mgr._hooks.items()}

    def _boom(**kw):
        raise RuntimeError("plugin exploded")

    mgr._hooks.setdefault("kanban_task_completed", []).append(_boom)
    try:
        conn = kb.connect()
        try:
            tid = kb.create_task(conn, title="t", assignee="worker")
            kb.claim_task(conn, tid)
            # Despite the raising hook, completion succeeds and persists.
            assert kb.complete_task(conn, tid, summary="ok") is True
            assert kb.get_task(conn, tid).status == "done"
        finally:
            conn.close()
    finally:
        mgr._hooks = saved


def _database_state(conn):
    return {
        table: [
            dict(row)
            for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")
        ]
        for table in ("tasks", "task_runs", "task_events")
    }


def _prepare_block_route(conn, route):
    task_id = kb.create_task(conn, title=f"{route} route", assignee="worker")
    assert kb.claim_task(conn, task_id, claimer="worker") is not None
    if route == "triage":
        with kb.write_txn(conn):
            conn.execute(
                """
                UPDATE tasks
                   SET block_kind = 'needs_input', block_recurrences = ?
                 WHERE id = ?
                """,
                (kb.BLOCK_RECURRENCE_LIMIT - 1, task_id),
            )
    return task_id, ("dependency" if route == "dependency" else "needs_input")


@pytest.mark.parametrize(
    ("route", "expected_status", "expected_event"),
    [
        ("dependency", "todo", "dependency_wait"),
        ("blocked", "blocked", "blocked"),
        ("triage", "triage", "block_loop_detected"),
    ],
)
def test_block_hook_observes_each_route_after_commit(
    kanban_home,
    monkeypatch,
    route,
    expected_status,
    expected_event,
):
    db = kb.kanban_db_path()
    observations = []
    with kb.connect_closing() as conn:
        task_id, kind = _prepare_block_route(conn, route)

        def observe(event, observed_task_id, **_fields):
            uri = f"{db.resolve().as_uri()}?mode=ro"
            with contextlib.closing(
                sqlite3.connect(uri, uri=True, isolation_level=None)
            ) as reader:
                reader.row_factory = sqlite3.Row
                reader.execute("PRAGMA query_only=ON")
                task = reader.execute(
                    "SELECT status FROM tasks WHERE id = ?",
                    (observed_task_id,),
                ).fetchone()
                events = reader.execute(
                    "SELECT kind FROM task_events WHERE task_id = ? ORDER BY id",
                    (observed_task_id,),
                ).fetchall()
            observations.append(
                (event, task["status"], [row["kind"] for row in events])
            )

        monkeypatch.setattr(kb, "_fire_kanban_lifecycle_hook", observe)
        assert kb.block_task(conn, task_id, reason="wait", kind=kind)

    assert observations == [
        ("kanban_task_blocked", expected_status, [
            "created",
            "claimed",
            expected_event,
        ])
    ]


@pytest.mark.parametrize("route", ["dependency", "blocked", "triage"])
def test_block_commit_failure_rolls_back_each_route_without_hook(
    kanban_home,
    monkeypatch,
    route,
):
    hook_calls = []
    appended_events = []
    with kb.connect_closing() as conn:
        task_id, kind = _prepare_block_route(conn, route)
        before = _database_state(conn)
        real_boundary = kb._execute_boundary_with_retry
        real_append_event = kb._append_event

        def fail_commit(boundary_conn, sql):
            if sql == "COMMIT":
                raise sqlite3.OperationalError("injected COMMIT failure")
            return real_boundary(boundary_conn, sql)

        def record_event(*args, **kwargs):
            appended_events.append(args[2])
            return real_append_event(*args, **kwargs)

        monkeypatch.setattr(kb, "_execute_boundary_with_retry", fail_commit)
        monkeypatch.setattr(kb, "_append_event", record_event)
        monkeypatch.setattr(
            kb,
            "_fire_kanban_lifecycle_hook",
            lambda *args, **kwargs: hook_calls.append((args, kwargs)),
        )

        with pytest.raises(sqlite3.OperationalError, match="injected COMMIT failure"):
            kb.block_task(conn, task_id, reason="wait", kind=kind)
        after = _database_state(conn)

    assert appended_events
    assert after == before
    assert hook_calls == []
