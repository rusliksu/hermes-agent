from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def isolated_board(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_BOARD", raising=False)
    from hermes_cli import kanban_db as kb

    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    yield home
    kb._INITIALIZED_PATHS.clear()


def _db_path():
    from hermes_cli import kanban_db as kb

    return kb.kanban_db_path()


def _quiet_sidecars(db: Path) -> None:
    conn = sqlite3.connect(str(db), isolation_level=None)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    for suffix in ("-wal", "-shm", ".init.lock"):
        sidecar = Path(str(db) + suffix)
        if sidecar.exists():
            sidecar.unlink()


def test_default_tool_exposure_is_read_only():
    from agent.transports import hermes_kanban_mcp_server as m

    names = set(m._tool_names_for_mode())
    assert names == {"kanban_board_status", "kanban_list_tasks"}
    assert not (names & set(m.WRITE_TOOLS))


def test_allow_write_exposes_only_dedicated_kanban_tools():
    from agent.transports import hermes_kanban_mcp_server as m

    names = set(m._tool_names_for_mode(allow_write=True))
    assert names == set(m.READ_TOOLS) | set(m.WRITE_TOOLS)
    assert "kanban_comment" not in names
    assert "web_search" not in names
    assert "terminal" not in names
    assert "read_file" not in names
    assert "hermes_tools" not in names


def test_read_only_status_and_list_do_not_init_or_create_sidecars(isolated_board, monkeypatch):
    from hermes_cli import kanban_db as kb
    from agent.transports import hermes_kanban_mcp_server as m

    with kb.connect() as conn:
        kb.create_task(conn, title="alpha", assignee="alice")

    db = _db_path()
    _quiet_sidecars(db)
    before_mtime = db.stat().st_mtime_ns

    def forbidden(*_args, **_kwargs):
        raise AssertionError("read-only MCP tool called a write/init DB helper")

    monkeypatch.setattr(kb, "connect", forbidden)
    monkeypatch.setattr(kb, "init_db", forbidden)
    monkeypatch.setattr(kb, "recompute_ready", forbidden)

    status = m.kanban_board_status()
    listed = m.kanban_list_tasks()

    assert status["ok"] is True
    assert status["counts_by_status"]["ready"] == 1
    assert listed["ok"] is True
    assert listed["count"] == 1
    assert listed["tasks"][0]["title"] == "alpha"
    assert "body" not in listed["tasks"][0]
    assert "result" not in listed["tasks"][0]
    assert "workspace_path" not in listed["tasks"][0]
    assert "claim_lock" not in listed["tasks"][0]
    assert db.stat().st_mtime_ns == before_mtime
    for suffix in ("-wal", "-shm", ".init.lock"):
        assert not Path(str(db) + suffix).exists()


def test_enqueue_claim_heartbeat_complete_happy_path(isolated_board):
    from agent.transports import hermes_kanban_mcp_server as m
    from hermes_cli import kanban_db as kb

    enqueued = m.kanban_enqueue(
        title="Do the work",
        body="Detailed task body",
        assignee="alice",
        priority=7,
    )
    assert enqueued["ok"] is True
    task_id = enqueued["task"]["id"]

    claimed = m.kanban_claim_next("alice", lease_seconds=60)
    assert claimed["ok"] is True
    assert claimed["claimed"] is True
    assert claimed["task"]["id"] == task_id
    assert claimed["task"]["body"] == "Detailed task body"
    token = claimed["claim_token"]

    heartbeat = m.kanban_heartbeat(task_id, token)
    assert heartbeat["ok"] is True

    completed = m.kanban_complete(
        task_id,
        token,
        summary="Implemented and tested",
        result="done",
        metadata={"tests": ["unit"]},
    )
    assert completed["ok"] is True
    assert completed["task"]["status"] == "done"
    assert "body" not in completed["task"]

    with kb.connect() as conn:
        task = kb.get_task(conn, task_id)
        assert task.status == "done"
        run = kb.latest_run(conn, task_id)
        assert run.summary == "Implemented and tested"
        assert run.metadata == {"tests": ["unit"]}


def test_two_claim_attempts_cannot_both_own_one_task(isolated_board):
    from agent.transports import hermes_kanban_mcp_server as m

    enqueued = m.kanban_enqueue(title="single owner", assignee="alice")
    task_id = enqueued["task"]["id"]

    first = m.kanban_claim_next("alice")
    second = m.kanban_claim_next("alice")

    assert first["ok"] is True
    assert first["claimed"] is True
    assert first["task"]["id"] == task_id
    assert second["ok"] is True
    assert second["claimed"] is False
    assert second["task"] is None


def test_claim_next_does_not_claim_foreign_assignee(isolated_board):
    from agent.transports import hermes_kanban_mcp_server as m

    m.kanban_enqueue(title="bob only", assignee="bob")
    claimed = m.kanban_claim_next("alice")
    assert claimed["ok"] is True
    assert claimed["claimed"] is False


def test_wrong_claim_token_cannot_complete_or_block(isolated_board):
    from agent.transports import hermes_kanban_mcp_server as m
    from hermes_cli import kanban_db as kb

    task_id = m.kanban_enqueue(title="token gated", assignee="alice")["task"]["id"]
    claimed = m.kanban_claim_next("alice")
    token = claimed["claim_token"]

    bad_complete = m.kanban_complete(task_id, "wrong-token", summary="done")
    bad_block = m.kanban_block(task_id, "wrong-token", reason="blocked")

    assert bad_complete["ok"] is False
    assert bad_complete["error"]["code"] == "claim_mismatch"
    assert bad_block["ok"] is False
    assert bad_block["error"]["code"] == "claim_mismatch"
    with kb.connect() as conn:
        assert kb.get_task(conn, task_id).status == "running"

    good = m.kanban_complete(task_id, token, summary="done")
    assert good["ok"] is True


def test_add_dependency_and_reclaim_use_canonical_db_state(isolated_board):
    from agent.transports import hermes_kanban_mcp_server as m
    from hermes_cli import kanban_db as kb

    parent = m.kanban_enqueue(title="parent", assignee="alice")["task"]["id"]
    child = m.kanban_enqueue(title="child", assignee="alice")["task"]["id"]
    linked = m.kanban_add_dependency(parent, child)
    assert linked["ok"] is True
    with kb.connect() as conn:
        assert child in kb.child_ids(conn, parent)
        assert kb.get_task(conn, child).status == "todo"

    claimed = m.kanban_claim_next("alice")
    reclaimed = m.kanban_reclaim(claimed["task"]["id"], reason="admin retry")
    assert reclaimed["ok"] is True
    assert reclaimed["task"]["status"] == "ready"
