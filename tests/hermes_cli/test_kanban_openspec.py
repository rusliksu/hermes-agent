from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.kanban_openspec import (
    import_openspec_tasks_md,
    parse_openspec_tasks_md,
)


@pytest.fixture
def temp_board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb._INITIALIZED_PATHS.clear()
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


def test_legacy_db_migration_adds_external_columns_and_partial_unique_index(tmp_path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            body TEXT,
            assignee TEXT,
            status TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 0,
            created_by TEXT,
            created_at INTEGER NOT NULL,
            started_at INTEGER,
            completed_at INTEGER,
            workspace_kind TEXT NOT NULL DEFAULT 'scratch',
            workspace_path TEXT,
            claim_lock TEXT,
            claim_expires INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE task_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            payload TEXT,
            created_at INTEGER NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()

    with kb.connect(db_path) as migrated:
        columns = {row["name"] for row in migrated.execute("PRAGMA table_info(tasks)")}
        indexes = {
            row["name"]: dict(row)
            for row in migrated.execute("PRAGMA index_list(tasks)")
        }
        index_sql = migrated.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
            ("idx_tasks_external_key_unique",),
        ).fetchone()["sql"]

    assert "external_key" in columns
    assert "source_path" in columns
    assert indexes["idx_tasks_external_key_unique"]["unique"] == 1
    assert "WHERE external_key IS NOT NULL" in index_sql


def test_external_key_unique_rejects_duplicates_but_allows_null(temp_board):
    with kb.connect() as conn:
        conn.execute(
            "INSERT INTO tasks (id, title, status, created_at, workspace_kind, external_key) "
            "VALUES ('a', 'A', 'todo', 1, 'scratch', NULL)"
        )
        conn.execute(
            "INSERT INTO tasks (id, title, status, created_at, workspace_kind, external_key) "
            "VALUES ('b', 'B', 'todo', 1, 'scratch', NULL)"
        )
        conn.execute(
            "INSERT INTO tasks (id, title, status, created_at, workspace_kind, external_key) "
            "VALUES ('c', 'C', 'todo', 1, 'scratch', 'repo::change::1.1')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO tasks (id, title, status, created_at, workspace_kind, external_key) "
                "VALUES ('d', 'D', 'todo', 1, 'scratch', 'repo::change::1.1')"
            )


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


def test_first_import_round_trips_russian_text_with_technical_terms(temp_board, tmp_path):
    source_text = (
        "- [ ] 1.1 Обновить MCP/API раздел в README\n"
        "- [x] 1.2 Проверить UTF-8 импорт для README\n"
    )
    source = _source(tmp_path, source_text)

    with kb.connect() as conn:
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
    assert [task.title for task in listed] == [
        "Обновить MCP/API раздел в README",
        "Проверить UTF-8 импорт для README",
    ]
    assert read_back is not None
    assert read_back.title == "Обновить MCP/API раздел в README"
    assert read_back.body == (
        "OpenSpec задача 1.1\n\nОбновить MCP/API раздел в README"
    )
    assert read_back.external_key == "repo::add-widget::1.1"
    assert read_back.source_path == str(source)


def test_identical_second_import_creates_no_duplicates(temp_board, tmp_path):
    source = _source(tmp_path, "- [ ] 1.1 First task\n")

    with kb.connect() as conn:
        first = import_openspec_tasks_md(conn, source, repo="repo")
        second = import_openspec_tasks_md(conn, source, repo="repo")
        count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]

    assert first["created"] == 1
    assert second["created"] == 0
    assert second["updated"] == 0
    assert second["unchanged"] == 1
    assert count == 1


def test_changed_source_title_and_body_update_only_source_owned_fields(temp_board, tmp_path):
    source = _source(tmp_path, "- [ ] 1.1 Old title\n")

    with kb.connect() as conn:
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


def test_reimport_preserves_operational_claim_and_run_state(temp_board, tmp_path):
    source = _source(tmp_path, "- [ ] 1.1 Claimed task\n")

    with kb.connect() as conn:
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
               SET status = 'running',
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
            (run_id, task_id),
        )
        before_run = dict(
            conn.execute("SELECT * FROM task_runs WHERE id = ?", (run_id,)).fetchone()
        )
        source.write_text("- [ ] 1.1 Claimed task renamed\n", encoding="utf-8")
        import_openspec_tasks_md(conn, source, repo="repo")
        after_task = _task_by_key(conn, "repo::add-widget::1.1")
        after_run = dict(
            conn.execute("SELECT * FROM task_runs WHERE id = ?", (run_id,)).fetchone()
        )

    assert after_task["title"] == "Claimed task renamed"
    assert after_task["status"] == "running"
    assert after_task["assignee"] == "alice"
    assert after_task["claim_lock"] == "lock"
    assert after_task["claim_expires"] == 9999
    assert after_task["worker_pid"] == 4242
    assert after_task["last_heartbeat_at"] == 8888
    assert after_task["current_run_id"] == run_id
    assert after_task["last_failure_error"] == "keep"
    assert after_task["result"] == "keep result"
    assert after_task["workflow_template_id"] == "workflow"
    assert after_task["current_step_key"] == "step"
    assert after_run == before_run


def test_removed_source_line_does_not_mutate_existing_task(temp_board, tmp_path):
    source = _source(tmp_path, "- [ ] 1.1 Keep\n- [ ] 1.2 Missing later\n")

    with kb.connect() as conn:
        import_openspec_tasks_md(conn, source, repo="repo")
        missing_before = dict(_task_by_key(conn, "repo::add-widget::1.2"))
        source.write_text("- [ ] 1.1 Keep\n", encoding="utf-8")
        result = import_openspec_tasks_md(conn, source, repo="repo")
        missing_after = dict(_task_by_key(conn, "repo::add-widget::1.2"))

    assert result["created"] == 0
    assert result["missing"][0]["external_key"] == "repo::add-widget::1.2"
    assert missing_after == missing_before


def test_checked_checkbox_does_not_force_existing_status_change(temp_board, tmp_path):
    source = _source(tmp_path, "- [ ] 1.1 Keep status\n")

    with kb.connect() as conn:
        import_openspec_tasks_md(conn, source, repo="repo")
        row = _task_by_key(conn, "repo::add-widget::1.1")
        conn.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (row["id"],))
        source.write_text("- [x] 1.1 Keep status\n", encoding="utf-8")
        result = import_openspec_tasks_md(conn, source, repo="repo")
        after = _task_by_key(conn, "repo::add-widget::1.1")

    assert result["unchanged"] == 1
    assert after["status"] == "done"
