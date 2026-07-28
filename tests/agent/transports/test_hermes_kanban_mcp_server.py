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
READ_TOOL_NAMES = [
    "kanban_board_status",
    "kanban_list_tasks",
]
PREVIOUS_WRITE_TOOL_NAMES = [
    "kanban_enqueue",
    "kanban_claim_next",
    "kanban_heartbeat",
    "kanban_complete",
    "kanban_block",
    "kanban_add_dependency",
    "kanban_reclaim",
    "kanban_import_openspec_tasks",
]
WRITE_TOOL_NAMES = [
    *READ_TOOL_NAMES,
    *PREVIOUS_WRITE_TOOL_NAMES,
    "kanban_sync_external_task",
]


@pytest.fixture
def isolated_board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "hermes-home"
    db = tmp_path / "kanban.db"
    fake_home = tmp_path / "fake-home"
    home.mkdir()
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db))
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_BOARD", raising=False)

    from hermes_cli import kanban_db as kb

    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    assert db.resolve() != LIVE_KANBAN_DB.resolve()
    yield home, db, fake_home
    kb._INITIALIZED_PATHS.clear()


def _prepare_quiescent_wal(db: Path) -> dict[str, list[dict]]:
    conn = sqlite3.connect(str(db), isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert tuple(conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()) == (
            0,
            0,
            0,
        )
        state = _database_state(conn)
    finally:
        conn.close()
    Path(str(db) + ".init.lock").unlink(missing_ok=True)
    assert db.read_bytes()[18:20] == b"\x02\x02"
    for suffix in ("-wal", "-shm", ".init.lock"):
        assert not Path(str(db) + suffix).exists()
    return state


def _stdio_env(home: Path, db: Path, fake_home: Path) -> dict[str, str]:
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
async def _open_cli_kanban_session(
    home: Path,
    db: Path,
    fake_home: Path,
    *args: str,
):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "hermes_cli.main", "mcp", "serve-kanban", *args],
        cwd=PROJECT_ROOT,
        env=_stdio_env(home, db, fake_home),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=MCP_TIMEOUT)
            yield session


async def _read_jsonrpc_message(
    proc: asyncio.subprocess.Process,
    *,
    msg_id: int,
) -> dict:
    assert proc.stdout is not None
    while True:
        raw = await asyncio.wait_for(proc.stdout.readline(), timeout=2)
        assert raw, f"server exited before response id={msg_id}; rc={proc.returncode}"
        message = json.loads(raw)
        if message.get("id") == msg_id:
            return message


def _jsonrpc_line(message: dict) -> bytes:
    return (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")


def _sync_handler():
    from agent.transports import hermes_kanban_mcp_server as m

    return m._tool_handlers(allow_write=True)["kanban_sync_external_task"]


def _database_state(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    return {
        table: [
            dict(row)
            for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
        ]
        for table in ("tasks", "task_runs", "task_events")
    }


def _database_state_at_path(db: Path) -> dict[str, list[dict]]:
    uri = f"{db.resolve().as_uri()}?mode=ro"
    with contextlib.closing(
        sqlite3.connect(uri, uri=True, isolation_level=None)
    ) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return _database_state(conn)


async def _start_raw_server(
    home: Path,
    db: Path,
    fake_home: Path,
) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "hermes_cli.main",
        "mcp",
        "serve-kanban",
        cwd=PROJECT_ROOT,
        env=_stdio_env(home, db, fake_home),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def _initialize_raw_server(proc: asyncio.subprocess.Process) -> None:
    assert proc.stdin is not None
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
    assert "result" in await _read_jsonrpc_message(proc, msg_id=1)


async def _close_stdin_and_assert_clean_exit(
    proc: asyncio.subprocess.Process,
) -> None:
    assert proc.stdin is not None
    proc.stdin.close()
    with contextlib.suppress(BrokenPipeError, ConnectionResetError):
        await proc.stdin.wait_closed()
    try:
        returncode = await asyncio.wait_for(proc.wait(), timeout=2)
    except asyncio.TimeoutError:
        proc.terminate()
        await proc.wait()
        pytest.fail("stdio server required forced termination after EOF")
    assert returncode == 0


def test_exact_tool_lists_preserve_previous_ten_and_gate_sync_to_write():
    from agent.transports import hermes_kanban_mcp_server as m

    assert list(m._tool_names_for_mode()) == READ_TOOL_NAMES
    assert list(m._tool_names_for_mode(allow_write=True)) == WRITE_TOOL_NAMES
    assert len(m._tool_names_for_mode()) == 2
    assert len(m._tool_names_for_mode(allow_write=True)) == 11
    assert list(m.READ_TOOLS) == READ_TOOL_NAMES
    assert list(m.WRITE_TOOLS) == [
        *PREVIOUS_WRITE_TOOL_NAMES,
        "kanban_sync_external_task",
    ]
    assert set(READ_TOOL_NAMES + PREVIOUS_WRITE_TOOL_NAMES) <= set(WRITE_TOOL_NAMES)
    assert "kanban_sync_external_task" not in m._tool_handlers()


def test_sync_handler_reuses_existing_wrapper():
    from agent.transports import hermes_kanban_mcp_server as dedicated
    from agent.transports import kanban_external_sync_mcp as shared
    from agent.transports.hermes_tools_mcp_server import (
        KANBAN_EXTERNAL_SYNC_TOOL,
        kanban_sync_external_task,
    )

    assert KANBAN_EXTERNAL_SYNC_TOOL == "kanban_sync_external_task"
    assert KANBAN_EXTERNAL_SYNC_TOOL is shared.KANBAN_EXTERNAL_SYNC_TOOL
    assert kanban_sync_external_task is shared.kanban_sync_external_task
    assert dedicated._tool_handlers(True)[KANBAN_EXTERNAL_SYNC_TOOL] is (
        shared.kanban_sync_external_task
    )


def test_language_policy_and_status_labels_are_additive():
    from agent.transports import hermes_kanban_mcp_server as m

    assert "на русском языке" in m.SERVER_INSTRUCTIONS
    assert "Technical identifiers" in m.SERVER_INSTRUCTIONS
    assert "Формальная проверка Кириллицы" in m.SERVER_INSTRUCTIONS
    assert m._status_label("ready") == "Готово к работе"
    assert m._status_label("future-status") == "future-status"


def test_mcp_serve_kanban_cli_dispatch_passes_allow_write(monkeypatch):
    from agent.transports import hermes_kanban_mcp_server as server
    from hermes_cli.mcp_config import mcp_command
    from hermes_cli.subcommands.mcp import build_mcp_parser

    parser = argparse.ArgumentParser(prog="hermes")
    subparsers = parser.add_subparsers(dest="command")
    build_mcp_parser(subparsers, cmd_mcp=mcp_command)
    calls: list[list[str]] = []

    monkeypatch.setattr(
        server,
        "main",
        lambda argv: calls.append(list(argv)) or 0,
    )

    parser.parse_args(["mcp", "serve-kanban"]).func(
        parser.parse_args(["mcp", "serve-kanban"])
    )
    parser.parse_args(["mcp", "serve-kanban", "--allow-write"]).func(
        parser.parse_args(["mcp", "serve-kanban", "--allow-write"])
    )

    assert calls == [[], ["--allow-write"]]


def test_cli_stdio_read_only_quiescent_wal_only_creates_coordination_sidecars(
    isolated_board,
):
    home, db, fake_home = isolated_board
    from hermes_cli import kanban_db as kb

    with kb.connect_closing() as conn:
        kb.create_task(conn, title="alpha", assignee="alice")
    before_state = _prepare_quiescent_wal(db)
    before_db_files = {
        path for path in db.parent.iterdir() if path.name.startswith(db.name)
    }
    before_bytes = db.read_bytes()
    before_mtime = db.stat().st_mtime_ns

    async def run_smoke():
        async with _open_cli_kanban_session(home, db, fake_home) as session:
            tools = await asyncio.wait_for(session.list_tools(), timeout=MCP_TIMEOUT)
            assert [tool.name for tool in tools.tools] == READ_TOOL_NAMES
            status = await asyncio.wait_for(
                session.call_tool("kanban_board_status", {}),
                timeout=MCP_TIMEOUT,
            )
            listed = await asyncio.wait_for(
                session.call_tool("kanban_list_tasks", {}),
                timeout=MCP_TIMEOUT,
            )
        return json.loads(status.content[0].text), json.loads(listed.content[0].text)

    status, listed = asyncio.run(run_smoke())
    assert status["counts_by_status"]["ready"] == 1
    assert listed["tasks"][0]["status"] == "ready"
    assert listed["tasks"][0]["status_label"] == "Готово к работе"
    assert db.stat().st_mtime_ns == before_mtime
    assert db.read_bytes() == before_bytes
    assert _database_state_at_path(db) == before_state
    assert db.read_bytes()[18:20] == b"\x02\x02"
    wal = Path(str(db) + "-wal")
    shm = Path(str(db) + "-shm")
    created_db_files = {
        path for path in db.parent.iterdir() if path.name.startswith(db.name)
    } - before_db_files
    assert created_db_files <= {wal, shm}
    assert wal.exists()
    assert wal.stat().st_size == 0
    assert not Path(str(db) + ".init.lock").exists()


def test_cli_stdio_write_list_and_sync_schema_require_explicit_dry_run(
    isolated_board,
):
    home, db, fake_home = isolated_board

    async def run_smoke():
        async with _open_cli_kanban_session(
            home,
            db,
            fake_home,
            "--allow-write",
        ) as session:
            tools = await asyncio.wait_for(session.list_tools(), timeout=MCP_TIMEOUT)
            assert [tool.name for tool in tools.tools] == WRITE_TOOL_NAMES
            sync_tool = next(
                tool for tool in tools.tools if tool.name == "kanban_sync_external_task"
            )
            schema = sync_tool.inputSchema
            assert "dry_run" in schema["required"]
            assert schema["properties"]["dry_run"]["type"] == "boolean"

    asyncio.run(run_smoke())


def test_byte_line_framer_preserves_fragmented_frame():
    from agent.transports.hermes_kanban_mcp_stdio import ByteLineFramer

    framer = ByteLineFramer()
    chunks = (
        b'{"jsonrpc":',
        b'"2.0","id":2,',
        b'"method":"tools/list","params":{}}',
        b"\n",
    )

    assert framer.feed(chunks[0]) == []
    assert framer.feed(chunks[1]) == []
    assert framer.feed(chunks[2]) == []
    assert framer.feed(chunks[3]) == [b"".join(chunks[:-1])]
    assert framer.finish() is None


def test_byte_line_framer_emits_multiple_coalesced_frames():
    from agent.transports.hermes_kanban_mcp_stdio import ByteLineFramer

    framer = ByteLineFramer()
    assert framer.feed(b'{"id":1}\n{"id":2}\n') == [
        b'{"id":1}',
        b'{"id":2}',
    ]


def test_byte_line_framer_emits_residual_frame_once_at_eof():
    from agent.transports.hermes_kanban_mcp_stdio import ByteLineFramer

    framer = ByteLineFramer()
    assert framer.feed(b'{"id":3}') == []
    assert framer.finish() == b'{"id":3}'
    assert framer.finish() is None


def test_byte_line_framer_ignores_blank_frames_without_losing_neighbors():
    from agent.transports.hermes_kanban_mcp_stdio import ByteLineFramer

    framer = ByteLineFramer()
    assert framer.feed(b'\n{"id":1}\n\r\n  \n{"id":2}\n') == [
        b'{"id":1}',
        b'{"id":2}',
    ]
    assert framer.feed(b" \t") == []
    assert framer.finish() is None


def test_malformed_frame_is_validation_error_without_consuming_next_valid_frame():
    pytest.importorskip("mcp")
    from agent.transports.hermes_kanban_mcp_stdio import (
        ByteLineFramer,
        _parse_frame,
    )

    valid = _jsonrpc_line(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/list",
            "params": {},
        }
    )
    frames = ByteLineFramer().feed(b"{not-json}\n" + valid)
    parsed = [_parse_frame(frame) for frame in frames]

    assert len(parsed) == 2
    assert isinstance(parsed[0], Exception)
    assert not isinstance(parsed[1], Exception)
    assert json.loads(parsed[1].message.model_dump_json(by_alias=True))["id"] == 7


def test_cli_stdio_coalesced_initialized_and_tools_list_returns(isolated_board):
    pytest.importorskip("mcp")
    home, db, fake_home = isolated_board

    async def run_smoke():
        proc = await _start_raw_server(home, db, fake_home)
        assert proc.stdin is not None
        await _initialize_raw_server(proc)
        proc.stdin.write(
            _jsonrpc_line(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                }
            )
            + _jsonrpc_line(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                }
            )
        )
        await proc.stdin.drain()
        tools_list = await _read_jsonrpc_message(proc, msg_id=2)
        assert [
            tool["name"] for tool in tools_list["result"]["tools"]
        ] == READ_TOOL_NAMES
        await _close_stdin_and_assert_clean_exit(proc)

    asyncio.run(run_smoke())


def test_cli_stdio_eof_exits_zero_without_forced_shutdown(isolated_board):
    pytest.importorskip("mcp")
    home, db, fake_home = isolated_board

    async def run_smoke():
        proc = await _start_raw_server(home, db, fake_home)
        await _initialize_raw_server(proc)
        await _close_stdin_and_assert_clean_exit(proc)

    asyncio.run(run_smoke())


def test_cli_stdio_residual_request_at_eof_flushes_response_and_exits_zero(
    isolated_board,
):
    pytest.importorskip("mcp")
    home, db, fake_home = isolated_board

    async def run_smoke():
        proc = await _start_raw_server(home, db, fake_home)
        assert proc.stdin is not None
        proc.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "pytest", "version": "0"},
                    },
                },
                separators=(",", ":"),
            ).encode("utf-8")
        )
        await proc.stdin.drain()
        proc.stdin.close()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            await proc.stdin.wait_closed()

        response = await _read_jsonrpc_message(proc, msg_id=1)
        assert response["result"]["serverInfo"]["name"] == "hermes-kanban"
        assert await asyncio.wait_for(proc.wait(), timeout=2) == 0

    asyncio.run(run_smoke())


def test_dedicated_adapter_stays_below_containment_limit():
    adapter = PROJECT_ROOT / "agent" / "transports" / "hermes_kanban_mcp_server.py"

    assert len(adapter.read_text(encoding="utf-8").splitlines()) < 1000


def test_read_only_handlers_do_not_call_write_init_helpers(isolated_board, monkeypatch):
    _home, db, _fake_home = isolated_board
    from agent.transports import hermes_kanban_mcp_server as m
    from hermes_cli import kanban_db as kb

    with kb.connect_closing() as conn:
        kb.create_task(conn, title="alpha", assignee="alice")
    _prepare_quiescent_wal(db)
    before_mtime = db.stat().st_mtime_ns
    real_sqlite_connect = sqlite3.connect
    sqlite_connect_calls = []

    def forbidden(*_args, **_kwargs):
        raise AssertionError("read-only MCP tool called a write/init DB helper")

    def recording_sqlite_connect(database, *args, **kwargs):
        sqlite_connect_calls.append((database, kwargs.copy()))
        return real_sqlite_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", recording_sqlite_connect)
    monkeypatch.setattr(kb, "connect", forbidden)
    monkeypatch.setattr(kb, "init_db", forbidden)
    monkeypatch.setattr(kb, "recompute_ready", forbidden)

    assert m.kanban_board_status()["ok"] is True
    listed = m.kanban_list_tasks()
    assert listed["ok"] is True
    assert listed["tasks"][0]["title"] == "alpha"
    assert "body" not in listed["tasks"][0]
    assert "workspace_path" not in listed["tasks"][0]
    assert db.stat().st_mtime_ns == before_mtime
    assert sqlite_connect_calls
    assert all(
        str(database).startswith("file:")
        and str(database).endswith("?mode=ro")
        and "immutable" not in str(database)
        and kwargs.get("uri") is True
        for database, kwargs in sqlite_connect_calls
    )


def test_read_only_handler_sees_committed_active_wal_without_domain_writes(
    isolated_board,
):
    _home, db, _fake_home = isolated_board
    from agent.transports import hermes_kanban_mcp_server as m

    assert db.resolve() != LIVE_KANBAN_DB.resolve()
    wal = Path(str(db) + "-wal")
    shm = Path(str(db) + "-shm")
    init_lock = Path(str(db) + ".init.lock")
    init_lock.unlink(missing_ok=True)
    assert not init_lock.exists()
    writer = sqlite3.connect(str(db), isolation_level=None)
    writer.row_factory = sqlite3.Row
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        main_before_commit = db.read_bytes()
        writer.execute(
            """
            INSERT INTO tasks (
                id, title, status, priority, created_at, workspace_kind
            ) VALUES ('t_active_wal', 'active WAL row', 'ready', 0, 1, 'scratch')
            """
        )
        assert wal.exists() and wal.stat().st_size > 32
        assert shm.exists()
        assert db.read_bytes() == main_before_commit
        before_state = _database_state(writer)
        main_before_read = db.read_bytes()
        main_mtime_before_read = db.stat().st_mtime_ns
        wal_before_read = wal.read_bytes()

        with m._readonly_connection(None) as (_slug, reader_path, reader):
            assert reader_path.resolve() == db.resolve()
            assert reader.execute("PRAGMA query_only").fetchone()[0] == 1
            assert reader.execute(
                "SELECT title FROM tasks WHERE id = 't_active_wal'"
            ).fetchone()[0] == "active WAL row"
        listed = m.kanban_list_tasks()

        assert listed["ok"] is True
        assert any(task["id"] == "t_active_wal" for task in listed["tasks"])
        assert _database_state(writer) == before_state
        assert db.read_bytes() == main_before_read
        assert db.stat().st_mtime_ns == main_mtime_before_read
        assert wal.read_bytes() == wal_before_read
        assert not init_lock.exists()
    finally:
        writer.close()


def test_block_handler_rolls_back_all_tables_when_metadata_persistence_fails(
    isolated_board,
):
    from agent.transports import hermes_kanban_mcp_server as m
    from hermes_cli import kanban_db as kb

    claim_token = "mcp:test:block-failure"
    with kb.connect_closing() as conn:
        task_id = kb.create_task(conn, title="atomic block", assignee="alice")
        assert kb.claim_task(conn, task_id, claimer=claim_token) is not None
        conn.execute(
            """
            CREATE TRIGGER reject_block_metadata
            BEFORE UPDATE OF metadata ON task_runs
            WHEN NEW.metadata IS NOT NULL
            BEGIN
                SELECT RAISE(ABORT, 'reject block metadata');
            END
            """
        )
        before = _database_state(conn)

    result = m.kanban_block(
        task_id,
        claim_token,
        "нужны данные",
        metadata={"source": "handler-test"},
    )

    assert result == {
        "ok": False,
        "error": {"code": "kanban_error", "message": "reject block metadata"},
    }
    with kb.connect_closing() as conn:
        after = _database_state(conn)
    assert after == before


def test_block_handler_persists_bounded_metadata_in_closed_run(isolated_board):
    from agent.transports import hermes_kanban_mcp_server as m
    from hermes_cli import kanban_db as kb

    claim_token = "mcp:test:block-success"
    metadata = {"source": "handler-test", "attempt": 2}
    with kb.connect_closing() as conn:
        task_id = kb.create_task(conn, title="metadata block", assignee="alice")
        assert kb.claim_task(conn, task_id, claimer=claim_token) is not None

    result = m.kanban_block(
        task_id,
        claim_token,
        "нужны данные",
        metadata=metadata,
    )

    assert result["ok"] is True
    with kb.connect_closing() as conn:
        assert kb.get_task(conn, task_id).status == "blocked"
        assert kb.latest_run(conn, task_id).metadata == metadata


def test_sync_apply_without_expected_status_stops_before_db_connect(
    isolated_board,
    monkeypatch,
):
    from hermes_cli import kanban_db as kb

    with kb.connect_closing() as conn:
        before = _database_state(conn)
    real_connect_closing = kb.connect_closing

    def forbidden(*_args, **_kwargs):
        raise AssertionError("guard failure reached DB connect")

    monkeypatch.setattr(kb, "connect_closing", forbidden)
    result = json.loads(
        _sync_handler()(
            external_key="github/MCP-guard",
            source_path="/queue/MCP-guard",
            title="must stay absent",
            assignee="codex",
            desired_status="Ready",
            dry_run=False,
        )
    )
    assert result["error"] == (
        "expected_current_status is required when dry_run is false"
    )
    monkeypatch.setattr(kb, "connect_closing", real_connect_closing)
    with kb.connect_closing() as conn:
        after = _database_state(conn)
    assert after == before


def test_sync_exact_key_not_title_and_dry_run_writes_no_rows(isolated_board):
    from hermes_cli import kanban_db as kb

    with kb.connect_closing() as conn:
        unrelated = kb.create_task(conn, title="same upstream title", assignee="alice")
        before = _database_state(conn)

    result = json.loads(
        _sync_handler()(
            external_key="linear/MCP-exact",
            source_path="/queue/MCP-exact",
            title="same upstream title",
            assignee="codex",
            desired_status="Ready",
            dry_run=True,
        )
    )
    assert result["action"] == "create"
    assert result["task_id"] is None
    assert result["dry_run"] is True

    with kb.connect_closing() as conn:
        assert kb.get_task(conn, unrelated).assignee == "alice"
        assert kb.get_task(conn, unrelated).external_key is None
        after = _database_state(conn)
    assert after == before


def test_sync_stale_status_rejection_writes_nothing(isolated_board):
    from hermes_cli import kanban_db as kb

    with kb.connect_closing() as conn:
        created = kb.sync_external_task(
            conn,
            external_key="github/MCP-stale",
            source_path="/queue/MCP-stale",
            title="original title",
            assignee="codex",
            desired_status="Ready",
        )
        before = _database_state(conn)

    result = json.loads(
        _sync_handler()(
            external_key="github/MCP-stale",
            source_path="/queue/changed",
            title="should not land",
            assignee="alice",
            desired_status="Done",
            dry_run=False,
            expected_current_status="Done",
        )
    )
    assert "expected current status" in result["error"]

    with kb.connect_closing() as conn:
        after = _database_state(conn)
    assert after == before
