from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from agent.transports.hermes_tools_mcp_server import kanban_sync_external_task
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_diagnostics as kd
from hermes_cli.kanban import run_slash


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_CRASH_GRACE_SECONDS", "0")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    yield home
    kb._INITIALIZED_PATHS.clear()


def _diagnostic_kinds(conn: sqlite3.Connection, task_id: str, *, now: int) -> set[str]:
    task = kb.get_task(conn, task_id)
    return {
        diag.kind
        for diag in kd.compute_task_diagnostics(
            task,
            kb.list_events(conn, task_id),
            kb.list_runs(conn, task_id),
            now=now,
            config={"stranded_threshold_seconds": 60},
        )
    }


def test_external_sync_schema_has_canonical_columns_and_unique_index(kanban_home):
    with kb.connect_closing() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
        indexes = {
            row["name"]
            for row in conn.execute("PRAGMA index_list(tasks)")
            if "external" in row["name"]
        }

    assert {"external_key", "source_path"} <= columns
    assert "external_metadata" not in columns
    assert indexes == {"idx_tasks_external_key_unique"}


def test_sync_external_create_update_noop_and_exact_key(kanban_home):
    with kb.connect_closing() as conn:
        unrelated = kb.create_task(conn, title="same upstream title", assignee="alice")
        created = kb.sync_external_task(
            conn,
            external_key="linear/ISS-1",
            source_path="/queue/ISS-1",
            title="same upstream title",
            assignee="codex",
            desired_status="Ready",
        )

        assert created.action == "create"
        assert created.task_id and created.task_id != unrelated
        task = kb.get_task(conn, created.task_id)
        assert task.title == "same upstream title"
        assert task.assignee == "codex"
        assert task.status == "ready"
        assert task.external_key == "linear/ISS-1"
        assert task.source_path == "/queue/ISS-1"
        assert task.created_by == "external-sync"

        updated = kb.sync_external_task(
            conn,
            external_key="linear/ISS-1",
            source_path="/queue/ISS-1",
            title="renamed upstream title",
            assignee="codex",
            desired_status="Done",
        )
        assert updated.action == "update"
        assert updated.task_id == created.task_id
        assert {"title", "status", "completed_at"} <= set(updated.changed_fields)

        same = kb.sync_external_task(
            conn,
            external_key="linear/ISS-1",
            source_path="/queue/ISS-1",
            title="renamed upstream title",
            assignee="codex",
            desired_status="done",
        )
        assert same.action == "noop"
        assert kb.get_task(conn, unrelated).title == "same upstream title"


def test_sync_external_dry_run_does_not_write(kanban_home):
    with kb.connect_closing() as conn:
        planned = kb.sync_external_task(
            conn,
            external_key="jira/OPS-9",
            source_path="https://jira.example/OPS-9",
            title="ops task",
            assignee="codex",
            desired_status="Ready",
            dry_run=True,
        )
        assert planned.action == "create"
        assert planned.task_id is None
        assert conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE external_key = ?",
            ("jira/OPS-9",),
        ).fetchone()[0] == 0


def test_mcp_sync_external_uses_exact_key_not_title(kanban_home):
    with kb.connect_closing() as conn:
        unrelated = kb.create_task(conn, title="same upstream title", assignee="alice")

    result = json.loads(
        kanban_sync_external_task(
            external_key="linear/MCP-1",
            source_path="/queue/MCP-1",
            title="same upstream title",
            assignee="codex",
            desired_status="Ready",
            dry_run=True,
        )
    )

    assert result["action"] == "create"
    assert result["task_id"] is None
    with kb.connect_closing() as conn:
        assert kb.get_task(conn, unrelated).assignee == "alice"
        assert conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE external_key = ?",
            ("linear/MCP-1",),
        ).fetchone()[0] == 0


def test_mcp_sync_external_dry_run_writes_no_rows(kanban_home):
    with kb.connect_closing() as conn:
        before = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("tasks", "task_runs", "task_events")
        }

    result = json.loads(
        kanban_sync_external_task(
            external_key="jira/MCP-2",
            source_path="/queue/MCP-2",
            title="preview only",
            assignee="codex",
            desired_status="Done",
            dry_run=True,
        )
    )

    assert result["action"] == "create"
    assert result["dry_run"] is True
    with kb.connect_closing() as conn:
        after = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("tasks", "task_runs", "task_events")
        }
    assert after == before


def test_mcp_sync_external_rejects_stale_expected_status(kanban_home):
    with kb.connect_closing() as conn:
        created = kb.sync_external_task(
            conn,
            external_key="github/MCP-3",
            source_path="/queue/MCP-3",
            title="original title",
            assignee="codex",
            desired_status="Ready",
        )
        before = kb.get_task(conn, created.task_id)

    result = json.loads(
        kanban_sync_external_task(
            external_key="github/MCP-3",
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
        after = kb.get_task(conn, created.task_id)
    assert (
        after.status,
        after.title,
        after.assignee,
        after.external_key,
        after.source_path,
    ) == (
        before.status,
        before.title,
        before.assignee,
        before.external_key,
        before.source_path,
    )


def test_mcp_sync_external_requires_expected_status_for_apply(kanban_home):
    result = json.loads(
        kanban_sync_external_task(
            external_key="github/MCP-4",
            source_path="/queue/MCP-4",
            title="must stay absent",
            assignee="codex",
            desired_status="Ready",
            dry_run=False,
        )
    )

    assert result["error"] == (
        "expected_current_status is required when dry_run is false"
    )
    with kb.connect_closing() as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_sync_external_attach_existing_task_dry_run_and_apply(kanban_home):
    with kb.connect_closing() as conn:
        legacy = kb.create_task(conn, title="legacy title", assignee="old")
        planned = kb.sync_external_task(
            conn,
            external_key="legacy/1",
            source_path="/queue/legacy-1",
            title="external title",
            assignee="codex",
            desired_status="Ready",
            task_id=legacy,
            dry_run=True,
        )

        assert planned.action == "update"
        assert planned.task_id == legacy
        assert planned.after["id"] == legacy
        assert planned.after["external_key"] == "legacy/1"
        assert {"external_key", "title", "assignee", "source_path", "created_by"} <= set(
            planned.changed_fields
        )
        assert kb.get_task(conn, legacy).external_key is None

        applied = kb.sync_external_task(
            conn,
            external_key="legacy/1",
            source_path="/queue/legacy-1",
            title="external title",
            assignee="codex",
            desired_status="Ready",
            task_id=legacy,
        )
        task = kb.get_task(conn, legacy)

        assert applied.task_id == legacy
        assert task.external_key == "legacy/1"
        assert task.title == "external title"
        assert task.created_by == "external-sync"


def test_sync_external_unknown_task_id_writes_nothing(kanban_home):
    with kb.connect_closing() as conn:
        with pytest.raises(RuntimeError, match="does not exist"):
            kb.sync_external_task(
                conn,
                external_key="legacy/missing",
                source_path="/queue/missing",
                title="missing",
                assignee="codex",
                desired_status="Ready",
                task_id="t_missing",
            )
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_sync_external_task_id_occupied_by_different_key_writes_nothing(kanban_home):
    with kb.connect_closing() as conn:
        existing = kb.sync_external_task(
            conn,
            external_key="legacy/occupied",
            source_path="/queue/occupied",
            title="occupied",
            assignee="codex",
            desired_status="Ready",
        )
        with pytest.raises(RuntimeError, match="already attached"):
            kb.sync_external_task(
                conn,
                external_key="legacy/new",
                source_path="/queue/new",
                title="new",
                assignee="codex",
                desired_status="Ready",
                task_id=existing.task_id,
            )
        assert kb.get_task(conn, existing.task_id).external_key == "legacy/occupied"


def test_sync_external_existing_key_task_id_mismatch_writes_nothing(kanban_home):
    with kb.connect_closing() as conn:
        existing = kb.sync_external_task(
            conn,
            external_key="legacy/keyed",
            source_path="/queue/keyed",
            title="keyed",
            assignee="codex",
            desired_status="Ready",
        )
        other = kb.create_task(conn, title="other", assignee="codex")
        with pytest.raises(RuntimeError, match="not"):
            kb.sync_external_task(
                conn,
                external_key="legacy/keyed",
                source_path="/queue/keyed",
                title="should not land",
                assignee="codex",
                desired_status="Done",
                task_id=other,
            )
        assert kb.get_task(conn, existing.task_id).status == "ready"
        assert kb.get_task(conn, other).external_key is None


def test_sync_external_expected_status_guard_on_attach_writes_nothing(kanban_home):
    with kb.connect_closing() as conn:
        legacy = kb.create_task(conn, title="legacy", assignee="codex")
        with pytest.raises(RuntimeError, match="expected current status"):
            kb.sync_external_task(
                conn,
                external_key="legacy/status",
                source_path="/queue/status",
                title="should not land",
                assignee="codex",
                desired_status="Done",
                task_id=legacy,
                expected_current_status="Done",
            )
        task = kb.get_task(conn, legacy)
        assert task.external_key is None
        assert task.status == "ready"


def test_sync_external_expected_status_guard_mismatch_writes_nothing(kanban_home):
    with kb.connect_closing() as conn:
        created = kb.sync_external_task(
            conn,
            external_key="github/123",
            source_path="https://github.example/123",
            title="review issue",
            assignee="codex",
            desired_status="Ready",
        )
        with pytest.raises(RuntimeError, match="expected current status"):
            kb.sync_external_task(
                conn,
                external_key="github/123",
                source_path="https://github.example/123",
                title="should not land",
                assignee="codex",
                desired_status="Done",
                expected_current_status="Done",
            )
        task = kb.get_task(conn, created.task_id)
        assert task.title == "review issue"
        assert task.status == "ready"


def test_sync_external_ready_releases_stale_claim(kanban_home):
    with kb.connect_closing() as conn:
        created = kb.sync_external_task(
            conn,
            external_key="linear/READY",
            source_path="/queue/READY",
            title="claimed elsewhere",
            assignee="codex",
            desired_status="Ready",
        )
        claimed = kb.claim_task(conn, created.task_id, claimer="worker-1")
        assert claimed is not None

        result = kb.sync_external_task(
            conn,
            external_key="linear/READY",
            source_path="/queue/READY",
            title="claimed elsewhere",
            assignee="codex",
            desired_status="Ready",
        )
        assert result.action == "update"
        task = kb.get_task(conn, created.task_id)
        assert task.status == "ready"
        assert task.claim_lock is None
        assert task.claim_expires is None
        assert task.worker_pid is None
        assert task.current_run_id is None
        assert kb.latest_run(conn, created.task_id).outcome == "released"


def test_sync_external_done_writes_completion_run_and_events(kanban_home):
    with kb.connect_closing() as conn:
        created = kb.sync_external_task(
            conn,
            external_key="linear/DONE",
            source_path="/queue/DONE",
            title="done upstream",
            assignee="codex",
            desired_status="Done",
        )
        task = kb.get_task(conn, created.task_id)
        runs = kb.list_runs(conn, created.task_id)
        events = kb.list_events(conn, created.task_id)

    assert task.status == "done"
    assert len(runs) == 1
    assert runs[0].outcome == "completed"
    assert runs[0].summary == "External task synchronized as done"
    assert any(ev.kind == "completed" and ev.run_id == runs[0].id for ev in events)
    assert any(ev.kind == "external_synced" for ev in events)


def test_sync_external_batch_success_and_failure_rolls_back(kanban_home):
    with kb.connect_closing() as conn:
        specs = [
            kb.ExternalTaskSyncSpec(
                external_key=f"batch/done-{idx}",
                source_path=f"/queue/done-{idx}",
                title=f"done {idx}",
                assignee="codex",
                desired_status="Done",
            )
            for idx in range(2)
        ] + [
            kb.ExternalTaskSyncSpec(
                external_key=f"batch/ready-{idx}",
                source_path=f"/queue/ready-{idx}",
                title=f"ready {idx}",
                assignee="codex",
                desired_status="Ready",
            )
            for idx in range(3)
        ]

        results = kb.sync_external_tasks(conn, specs)
        assert len(results) == 5
        done_ids = [result.task_id for result in results[:2]]
        ready_ids = [result.task_id for result in results[2:]]
        for task_id in done_ids:
            task = kb.get_task(conn, task_id)
            assert task.status == "done"
            assert kb.list_runs(conn, task_id)[0].outcome == "completed"
            assert any(ev.kind == "completed" for ev in kb.list_events(conn, task_id))
        assert [kb.get_task(conn, task_id).status for task_id in ready_ids] == [
            "ready",
            "ready",
            "ready",
        ]

        failing = [
            kb.ExternalTaskSyncSpec(
                external_key=f"rollback/{idx}",
                source_path=f"/queue/rollback-{idx}",
                title=f"rollback {idx}",
                assignee="codex",
                desired_status="Ready",
            )
            for idx in range(3)
        ] + [
            kb.ExternalTaskSyncSpec(
                external_key="rollback/bad",
                source_path="/queue/bad",
                title="bad",
                assignee="codex",
                desired_status="Ready",
                task_id="t_missing",
            )
        ]
        with pytest.raises(RuntimeError, match="does not exist"):
            kb.sync_external_tasks(conn, failing)
        assert conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE external_key LIKE 'rollback/%'"
        ).fetchone()[0] == 0


def test_sync_external_batch_dry_run_writes_nothing(kanban_home):
    with kb.connect_closing() as conn:
        results = kb.sync_external_tasks(
            conn,
            [
                kb.ExternalTaskSyncSpec(
                    external_key="dry/batch",
                    source_path="/queue/dry",
                    title="dry",
                    assignee="codex",
                    desired_status="Ready",
                )
            ],
            dry_run=True,
        )
        assert results[0].dry_run is True
        assert conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE external_key = 'dry/batch'"
        ).fetchone()[0] == 0


def test_sync_external_cli_json(kanban_home):
    raw = run_slash(
        "sync-external --external-key cli/1 --source-path /tmp/cli-1 "
        "--title 'cli task' --assignee codex --status Ready --json"
    )
    payload = json.loads(raw)
    assert payload["action"] == "create"
    assert payload["external_key"] == "cli/1"
    assert payload["after"]["source_path"] == "/tmp/cli-1"


def test_sync_external_cli_task_id_and_batch_json_path(kanban_home, tmp_path):
    with kb.connect_closing() as conn:
        legacy = kb.create_task(conn, title="legacy", assignee="old")
    raw = run_slash(
        "sync-external --external-key cli/legacy --source-path /tmp/legacy "
        f"--title 'legacy updated' --assignee codex --status Ready --task-id {legacy} --json"
    )
    payload = json.loads(raw)
    assert payload["task_id"] == legacy
    assert payload["after"]["external_key"] == "cli/legacy"

    batch_path = tmp_path / "batch.json"
    batch_path.write_text(
        json.dumps(
            [
                {
                    "external_key": "cli/batch-1",
                    "source_path": "/tmp/batch-1",
                    "title": "batch 1",
                    "assignee": "codex",
                    "status": "Ready",
                }
            ]
        ),
        encoding="utf-8",
    )
    batch_raw = run_slash(f"sync-external-batch {batch_path} --json")
    batch_payload = json.loads(batch_raw)
    assert batch_payload[0]["external_key"] == "cli/batch-1"


def test_sync_external_batch_cli_stdin_and_validation(kanban_home, monkeypatch):
    import io

    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(
            json.dumps(
                [
                    {
                        "external_key": "cli/stdin",
                        "source_path": "/tmp/stdin",
                        "title": "stdin",
                        "assignee": "codex",
                        "status": "Ready",
                    }
                ]
            )
        ),
    )
    payload = json.loads(run_slash("sync-external-batch - --dry-run --json"))
    assert payload[0]["dry_run"] is True
    with kb.connect_closing() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE external_key = 'cli/stdin'"
        ).fetchone()[0] == 0

    bad = kb.ExternalTaskSyncSpec.from_mapping
    with pytest.raises(ValueError, match="unknown field"):
        bad({"external_key": "x", "source_path": "x", "title": "x", "assignee": "x", "status": "Ready", "extra": 1})


def test_sync_external_exact_key_serializes_racing_writers(kanban_home):
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def worker(title: str) -> None:
        try:
            with kb.connect_closing() as conn:
                barrier.wait(timeout=5)
                kb.sync_external_task(
                    conn,
                    external_key="race/1",
                    source_path="/queue/race-1",
                    title=title,
                    assignee="codex",
                    desired_status="Ready",
                )
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=("race a",)),
        threading.Thread(target=worker, args=("race b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    with kb.connect_closing() as conn:
        rows = conn.execute(
            "SELECT id FROM tasks WHERE external_key = ?",
            ("race/1",),
        ).fetchall()
        assert len(rows) == 1


def test_registered_external_ready_is_not_stranded(kanban_home):
    with kb.connect_closing() as conn:
        result = kb.sync_external_task(
            conn,
            external_key="linear/EXT-7",
            source_path="/queue/EXT-7",
            title="external waits elsewhere",
            assignee="codex",
            desired_status="Ready",
        )
        old = int(time.time()) - 3600
        with kb.write_txn(conn):
            conn.execute("UPDATE task_events SET created_at = ? WHERE task_id = ?", (old, result.task_id))
            conn.execute("UPDATE tasks SET created_at = ? WHERE id = ?", (old, result.task_id))

        assert "stranded_in_ready" not in _diagnostic_kinds(conn, result.task_id, now=old + 3600)


def test_unregistered_external_shape_still_stranded(kanban_home):
    with kb.connect_closing() as conn:
        tid = kb.create_task(conn, title="broken lane", assignee="codex")
        old = int(time.time()) - 3600
        with kb.write_txn(conn):
            conn.execute(
                "UPDATE tasks SET external_key = 'broken/1', source_path = '/queue/broken', "
                "created_at = ? WHERE id = ?",
                (old, tid),
            )
            conn.execute("UPDATE task_events SET created_at = ? WHERE task_id = ?", (old, tid))

        assert "stranded_in_ready" in _diagnostic_kinds(conn, tid, now=old + 3600)


def test_dispatcher_skips_registered_external_lane_even_if_profile_exists(monkeypatch, kanban_home):
    from hermes_cli import profiles

    monkeypatch.setattr(profiles, "profile_exists", lambda name: name == "codex")
    spawned = []

    def spawn_fn(task, workspace):
        spawned.append(task.id)
        return 1234

    with kb.connect_closing() as conn:
        result = kb.sync_external_task(
            conn,
            external_key="linear/EXT-8",
            source_path="/queue/EXT-8",
            title="external terminal lane",
            assignee="codex",
            desired_status="Ready",
        )
        dispatch = kb.dispatch_once(conn, spawn_fn=spawn_fn)
        assert result.task_id in dispatch.skipped_nonspawnable
        assert spawned == []
        assert kb.get_task(conn, result.task_id).status == "ready"
        assert kb.has_spawnable_ready(conn) is False
