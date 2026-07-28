from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.kanban_openspec import (
    import_openspec_tasks_md,
    parse_openspec_tasks_md,
)

LIVE_KANBAN_DB = Path("/home/openclaw/.hermes/kanban.db")


@pytest.fixture
def temp_board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".hermes"
    db = tmp_path / "kanban.db"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db))
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_BOARD", raising=False)
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    assert db.resolve() != LIVE_KANBAN_DB.resolve()
    yield home
    kb._INITIALIZED_PATHS.clear()


def _source(tmp_path: Path, text: str, change: str = "add-widget") -> Path:
    path = tmp_path / "repo" / "openspec" / "changes" / change / "tasks.md"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    return path


def _task_by_key(conn: sqlite3.Connection, external_key: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM tasks WHERE external_key = ?", (external_key,)
    ).fetchone()
    assert row is not None
    return row


def test_parser_accepts_minimum_fixture_and_preserves_string_ids():
    parsed = parse_openspec_tasks_md(
        """
          - [ ] 1.1 Task title
        - [x] 1.2 Completed-in-plan title
        not a task
        """
    )

    assert [task.task_id for task in parsed] == ["1.1", "1.2"]
    assert all(isinstance(task.task_id, str) for task in parsed)
    assert [task.title for task in parsed] == [
        "Task title",
        "Completed-in-plan title",
    ]
    assert [task.checked for task in parsed] == [False, True]


def test_parser_rejects_duplicate_task_ids():
    with pytest.raises(ValueError, match="duplicate OpenSpec task id '1.1'"):
        parse_openspec_tasks_md("- [ ] 1.1 A\n- [x] 1.1 B\n")


def test_first_import_round_trips_russian_text_with_technical_terms(
    temp_board, tmp_path
):
    source_text = (
        "- [ ] 1.1 Обновить MCP/API раздел в README\n"
        "- [x] 1.2 Проверить UTF-8 импорт для README\n"
    )
    source = _source(tmp_path, source_text)

    with kb.connect_closing() as conn:
        result = import_openspec_tasks_md(conn, source, repo="repo")
        rows = conn.execute("SELECT * FROM tasks ORDER BY external_key").fetchall()
        listed = sorted(kb.list_tasks(conn), key=lambda task: task.external_key or "")
        read_back = kb.get_task(conn, listed[0].id)

    assert result["created"] == 2
    assert result["updated"] == 0
    assert result["unchanged"] == 0
    assert result["source_path"] == str(source)
    assert [row["external_key"] for row in rows] == [
        "repo::add-widget::1.1",
        "repo::add-widget::1.2",
    ]
    assert [row["source_path"] for row in rows] == [str(source), str(source)]
    assert [row["status"] for row in rows] == ["todo", "todo"]
    assert [row["created_by"] for row in rows] == ["openspec", "openspec"]
    assert [row["workspace_kind"] for row in rows] == ["scratch", "scratch"]
    assert [task.title for task in listed] == [
        "Обновить MCP/API раздел в README",
        "Проверить UTF-8 импорт для README",
    ]
    assert read_back is not None
    assert read_back.body == (
        "OpenSpec задача 1.1\n\nОбновить MCP/API раздел в README"
    )


def test_identical_second_import_creates_no_duplicates(temp_board, tmp_path):
    source = _source(tmp_path, "- [ ] 1.1 First task\n")

    with kb.connect_closing() as conn:
        first = import_openspec_tasks_md(conn, source, repo="repo")
        second = import_openspec_tasks_md(conn, source, repo="repo")
        count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        event_count = conn.execute("SELECT COUNT(*) FROM task_events").fetchone()[0]

    assert first["created"] == 1
    assert second["created"] == 0
    assert second["updated"] == 0
    assert second["unchanged"] == 1
    assert count == 1
    assert event_count == 1


def test_create_update_unchanged_write_canonical_events_only_when_changed(
    temp_board, tmp_path
):
    source = _source(tmp_path, "- [ ] 1.1 Исходная задача\n")

    with kb.connect_closing() as conn:
        created = import_openspec_tasks_md(conn, source, repo="repo")
        task_id = created["tasks"][0]["id"]
        assert [
            row["kind"]
            for row in conn.execute(
                "SELECT kind FROM task_events WHERE task_id = ? ORDER BY id",
                (task_id,),
            )
        ] == ["created"]

        unchanged = import_openspec_tasks_md(conn, source, repo="repo")
        assert unchanged["tasks"][0]["action"] == "unchanged"
        assert conn.execute(
            "SELECT COUNT(*) FROM task_events WHERE task_id = ?", (task_id,)
        ).fetchone()[0] == 1

        source.write_text("- [ ] 1.1 Обновлённая задача\n", encoding="utf-8")
        updated = import_openspec_tasks_md(conn, source, repo="repo")
        events = conn.execute(
            "SELECT kind, payload FROM task_events WHERE task_id = ? ORDER BY id",
            (task_id,),
        ).fetchall()

    assert updated["tasks"][0]["action"] == "updated"
    assert [event["kind"] for event in events] == ["created", "external_synced"]
    payload = json.loads(events[-1]["payload"])
    assert payload == {
        "external_key": "repo::add-widget::1.1",
        "changed_fields": ["body", "title"],
    }


def test_changed_source_updates_only_source_owned_fields(temp_board, tmp_path):
    source = _source(tmp_path, "- [ ] 1.1 Old title\n")

    with kb.connect_closing() as conn:
        import_openspec_tasks_md(conn, source, repo="repo")
        row = _task_by_key(conn, "repo::add-widget::1.1")
        task_id = row["id"]
        conn.execute(
            "UPDATE tasks SET status = 'running', assignee = 'alice', priority = 50, "
            "claim_lock = 'lock', claim_expires = 1234, consecutive_failures = 7 "
            "WHERE id = ?",
            (task_id,),
        )
        source.write_text("- [ ] 1.1 New title\n", encoding="utf-8")
        result = import_openspec_tasks_md(conn, source, repo="repo")
        changed = _task_by_key(conn, "repo::add-widget::1.1")

    assert result["updated"] == 1
    assert changed["title"] == "New title"
    assert changed["body"] == "OpenSpec задача 1.1\n\nNew title"
    assert changed["status"] == "running"
    assert changed["assignee"] == "alice"
    assert changed["priority"] == 50
    assert changed["claim_lock"] == "lock"
    assert changed["claim_expires"] == 1234
    assert changed["consecutive_failures"] == 7


@pytest.mark.parametrize("status", ["running", "done"])
def test_reimport_preserves_operational_claim_and_run_state(
    temp_board, tmp_path, status
):
    source = _source(tmp_path, "- [ ] 1.1 Claimed task\n")

    with kb.connect_closing() as conn:
        import_openspec_tasks_md(conn, source, repo="repo")
        row = _task_by_key(conn, "repo::add-widget::1.1")
        task_id = row["id"]
        run_id = conn.execute(
            """
            INSERT INTO task_runs (
                task_id, profile, status, claim_lock, claim_expires,
                worker_pid, last_heartbeat_at, started_at
            ) VALUES (?, 'alice', 'running', 'lock', 9999, 4242, 8888, 7777)
            """,
            (task_id,),
        ).lastrowid
        conn.execute(
            """
            UPDATE tasks
               SET status = ?,
                   assignee = 'alice',
                   claim_lock = 'lock',
                   claim_expires = 9999,
                   worker_pid = 4242,
                   last_heartbeat_at = 8888,
                   current_run_id = ?,
                   last_failure_error = 'keep',
                   result = 'keep result',
                   workflow_template_id = 'workflow',
                   current_step_key = 'step'
             WHERE id = ?
            """,
            (status, run_id, task_id),
        )
        before_task = dict(_task_by_key(conn, "repo::add-widget::1.1"))
        before_run = dict(
            conn.execute("SELECT * FROM task_runs WHERE id = ?", (run_id,)).fetchone()
        )
        source.write_text("- [ ] 1.1 Claimed task renamed\n", encoding="utf-8")
        import_openspec_tasks_md(conn, source, repo="repo")
        after_task = _task_by_key(conn, "repo::add-widget::1.1")
        after_run = dict(
            conn.execute("SELECT * FROM task_runs WHERE id = ?", (run_id,)).fetchone()
        )

    assert dict(after_task) == {
        **before_task,
        "title": "Claimed task renamed",
        "body": "OpenSpec задача 1.1\n\nClaimed task renamed",
    }
    assert after_run == before_run


def test_import_batch_rolls_back_tasks_and_events_on_failure(temp_board, tmp_path):
    source = _source(
        tmp_path,
        "- [ ] 1.1 Первая задача\n- [ ] 1.2 Вторая задача\n",
    )

    with kb.connect_closing() as conn:
        conn.execute(
            """
            CREATE TRIGGER reject_second_openspec_task
            BEFORE INSERT ON tasks
            WHEN NEW.external_key = 'repo::add-widget::1.2'
            BEGIN
                SELECT RAISE(ABORT, 'reject second definition');
            END
            """
        )
        before = {
            table: [dict(row) for row in conn.execute(
                f"SELECT * FROM {table} ORDER BY rowid"
            )]
            for table in ("tasks", "task_runs", "task_events")
        }
        with pytest.raises(sqlite3.IntegrityError, match="reject second definition"):
            import_openspec_tasks_md(conn, source, repo="repo")
        after = {
            table: [dict(row) for row in conn.execute(
                f"SELECT * FROM {table} ORDER BY rowid"
            )]
            for table in ("tasks", "task_runs", "task_events")
        }

    assert after == before


def test_removed_source_line_does_not_mutate_existing_task(temp_board, tmp_path):
    source = _source(tmp_path, "- [ ] 1.1 Keep\n- [ ] 1.2 Missing later\n")

    with kb.connect_closing() as conn:
        import_openspec_tasks_md(conn, source, repo="repo")
        missing_before = dict(_task_by_key(conn, "repo::add-widget::1.2"))
        source.write_text("- [ ] 1.1 Keep\n", encoding="utf-8")
        result = import_openspec_tasks_md(conn, source, repo="repo")
        missing_after = dict(_task_by_key(conn, "repo::add-widget::1.2"))

    assert result["created"] == 0
    assert result["missing"][0]["external_key"] == "repo::add-widget::1.2"
    assert missing_after == missing_before


def test_checked_checkbox_does_not_force_existing_status_change(
    temp_board, tmp_path
):
    source = _source(tmp_path, "- [ ] 1.1 Keep status\n")

    with kb.connect_closing() as conn:
        import_openspec_tasks_md(conn, source, repo="repo")
        row = _task_by_key(conn, "repo::add-widget::1.1")
        conn.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (row["id"],))
        source.write_text("- [x] 1.1 Keep status\n", encoding="utf-8")
        result = import_openspec_tasks_md(conn, source, repo="repo")
        after = _task_by_key(conn, "repo::add-widget::1.1")

    assert result["unchanged"] == 1
    assert after["status"] == "done"
