from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[3]
LIVE_KANBAN_DB = Path("/home/openclaw/.hermes/kanban.db")
MCP_TIMEOUT = 10


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


def _tool_result_payload(result) -> dict:
    assert result.content, "MCP tool returned no content"
    return json.loads(result.content[0].text)


def _init_temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    home = tmp_path / "hermes-home"
    db = tmp_path / "kanban.db"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db))
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_BOARD", raising=False)

    from hermes_cli import kanban_db as kb

    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    assert db.resolve() != LIVE_KANBAN_DB.resolve()
    return home, db


def _stdio_env(tmp_path: Path, home: Path, db: Path) -> dict[str, str]:
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir(exist_ok=True)
    return {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(PROJECT_ROOT),
        "HERMES_HOME": str(home),
        "HERMES_KANBAN_DB": str(db),
        "HOME": str(fake_home),
        "TZ": "UTC",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


@contextlib.asynccontextmanager
async def _open_cli_kanban_session(tmp_path: Path, home: Path, db: Path, *args: str):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "hermes_cli.main", "mcp", "serve-kanban", *args],
        cwd=PROJECT_ROOT,
        env=_stdio_env(tmp_path, home, db),
    )
    errlog_path = tmp_path / "mcp-stderr.log"
    with errlog_path.open("w", encoding="utf-8") as errlog:
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=MCP_TIMEOUT)
                yield session


async def _read_jsonrpc_message(proc: asyncio.subprocess.Process, *, msg_id: int) -> dict:
    assert proc.stdout is not None
    while True:
        raw = await asyncio.wait_for(proc.stdout.readline(), timeout=2)
        assert raw, f"server exited before response id={msg_id}; rc={proc.returncode}"
        message = json.loads(raw)
        if message.get("id") == msg_id:
            return message


def _jsonrpc_line(message: dict) -> bytes:
    return (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")


def test_default_tool_exposure_is_read_only():
    from agent.transports import hermes_kanban_mcp_server as m

    names = set(m._tool_names_for_mode())
    assert names == {"kanban_board_status", "kanban_list_tasks"}
    assert not (names & set(m.WRITE_TOOLS))
    assert "kanban_import_openspec_tasks" not in names


def test_allow_write_exposes_only_dedicated_kanban_tools():
    from agent.transports import hermes_kanban_mcp_server as m

    names = set(m._tool_names_for_mode(allow_write=True))
    assert names == set(m.READ_TOOLS) | set(m.WRITE_TOOLS)
    assert "kanban_comment" not in names
    assert "web_search" not in names
    assert "terminal" not in names
    assert "read_file" not in names
    assert "hermes_tools" not in names
    assert "kanban_import_openspec_tasks" in names


def test_mcp_serve_kanban_cli_dispatch_passes_allow_write(monkeypatch):
    from agent.transports import hermes_kanban_mcp_server as server
    from hermes_cli.mcp_config import mcp_command
    from hermes_cli.subcommands.mcp import build_mcp_parser

    parser = argparse.ArgumentParser(prog="hermes")
    subparsers = parser.add_subparsers(dest="command")
    build_mcp_parser(subparsers, cmd_mcp=mcp_command)

    calls: list[list[str]] = []

    def fake_main(argv):
        calls.append(list(argv))
        return 0

    monkeypatch.setattr(server, "main", fake_main)

    args = parser.parse_args(["mcp", "serve-kanban"])
    args.func(args)
    args = parser.parse_args(["mcp", "serve-kanban", "--allow-write"])
    args.func(args)

    assert calls == [[], ["--allow-write"]]


def test_cli_stdio_read_only_smoke_no_db_mutation(tmp_path, monkeypatch):
    home, db = _init_temp_db(tmp_path, monkeypatch)
    from hermes_cli import kanban_db as kb

    with kb.connect() as conn:
        kb.create_task(conn, title="alpha", assignee="alice")

    _quiet_sidecars(db)
    before_mtime = db.stat().st_mtime_ns

    async def run_smoke():
        async with _open_cli_kanban_session(tmp_path, home, db) as session:
            tools = await asyncio.wait_for(session.list_tools(), timeout=MCP_TIMEOUT)
            names = [tool.name for tool in tools.tools]
            assert names == ["kanban_board_status", "kanban_list_tasks"]

            status_result = await asyncio.wait_for(
                session.call_tool("kanban_board_status", {}),
                timeout=MCP_TIMEOUT,
            )
            listed_result = await asyncio.wait_for(
                session.call_tool("kanban_list_tasks", {}),
                timeout=MCP_TIMEOUT,
            )

        status = _tool_result_payload(status_result)
        listed = _tool_result_payload(listed_result)
        assert status["ok"] is True
        assert status["counts_by_status"]["ready"] == 1
        assert listed["ok"] is True
        assert listed["count"] == 1
        assert listed["tasks"][0]["title"] == "alpha"

    asyncio.run(run_smoke())

    assert db.resolve() != LIVE_KANBAN_DB.resolve()
    assert db.stat().st_mtime_ns == before_mtime
    for suffix in ("-wal", "-shm", ".init.lock"):
        assert not Path(str(db) + suffix).exists()


def test_cli_stdio_coalesced_initialized_and_tools_list_returns(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    home, db = _init_temp_db(tmp_path, monkeypatch)

    async def run_smoke():
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "hermes_cli.main",
            "mcp",
            "serve-kanban",
            cwd=PROJECT_ROOT,
            env=_stdio_env(tmp_path, home, db),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert proc.stdin is not None
        try:
            proc.stdin.write(
                _jsonrpc_line(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "clientInfo": {"name": "pytest", "version": "0"},
                        },
                    }
                )
            )
            await proc.stdin.drain()
            initialized = await _read_jsonrpc_message(proc, msg_id=1)
            assert "result" in initialized

            coalesced = b"".join(
                [
                    _jsonrpc_line(
                        {
                            "jsonrpc": "2.0",
                            "method": "notifications/initialized",
                            "params": {},
                        }
                    ),
                    _jsonrpc_line(
                        {
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "tools/list",
                            "params": {},
                        }
                    ),
                ]
            )
            proc.stdin.write(coalesced)
            await proc.stdin.drain()

            tools_list = await asyncio.wait_for(
                _read_jsonrpc_message(proc, msg_id=2),
                timeout=2,
            )
            assert "result" in tools_list
            assert [tool["name"] for tool in tools_list["result"]["tools"]] == [
                "kanban_board_status",
                "kanban_list_tasks",
            ]
        finally:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()

    asyncio.run(run_smoke())


def test_cli_stdio_write_mode_happy_path(tmp_path, monkeypatch):
    home, db = _init_temp_db(tmp_path, monkeypatch)
    from agent.transports import hermes_kanban_mcp_server as m
    from hermes_cli import kanban_db as kb

    async def run_smoke():
        async with _open_cli_kanban_session(
            tmp_path,
            home,
            db,
            "--allow-write",
        ) as session:
            tools = await asyncio.wait_for(session.list_tools(), timeout=MCP_TIMEOUT)
            assert {tool.name for tool in tools.tools} == set(m.READ_TOOLS) | set(
                m.WRITE_TOOLS
            )

            enqueued = _tool_result_payload(
                await asyncio.wait_for(
                    session.call_tool(
                        "kanban_enqueue",
                        {
                            "title": "Do the work",
                            "body": "Detailed task body",
                            "assignee": "alice",
                            "priority": 7,
                        },
                    ),
                    timeout=MCP_TIMEOUT,
                )
            )
            task_id = enqueued["task"]["id"]

            claimed = _tool_result_payload(
                await asyncio.wait_for(
                    session.call_tool(
                        "kanban_claim_next",
                        {"assignee": "alice", "lease_seconds": 60},
                    ),
                    timeout=MCP_TIMEOUT,
                )
            )
            assert claimed["claimed"] is True
            assert claimed["task"]["id"] == task_id
            token = claimed["claim_token"]

            heartbeat = _tool_result_payload(
                await asyncio.wait_for(
                    session.call_tool(
                        "kanban_heartbeat",
                        {"task_id": task_id, "claim_token": token},
                    ),
                    timeout=MCP_TIMEOUT,
                )
            )
            assert heartbeat["ok"] is True

            completed = _tool_result_payload(
                await asyncio.wait_for(
                    session.call_tool(
                        "kanban_complete",
                        {
                            "task_id": task_id,
                            "claim_token": token,
                            "summary": "Implemented and tested",
                            "result": "done",
                            "metadata": {"tests": ["mcp-stdio"]},
                        },
                    ),
                    timeout=MCP_TIMEOUT,
                )
            )
            assert completed["ok"] is True
            return task_id

    task_id = asyncio.run(run_smoke())

    assert db.resolve() != LIVE_KANBAN_DB.resolve()
    with kb.connect() as conn:
        task = kb.get_task(conn, task_id)
        assert task.status == "done"
        run = kb.latest_run(conn, task_id)
        assert run.summary == "Implemented and tested"
        assert run.metadata == {"tests": ["mcp-stdio"]}


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
