"""The bound-document turn scope: hold text, mint one transaction, export one confirmation."""
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from agent.artifact_turn import ARTIFACT_FAILURE, artifact_turn_scope
from gateway.access_context import DeliveryTarget, ResolvedAccessContext, bind_resolved_access_context
from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from hermes_state import SessionDB


@pytest.fixture
def workspace(tmp_path):
    home = tmp_path / "state" / "profiles" / "alpha"
    home.mkdir(parents=True)
    (home / "report.csv").write_text("fixture,value\n", encoding="utf-8")
    return home


@contextmanager
def bound(home, monkeypatch):
    monkeypatch.setenv("TERMINAL_CWD", str(home))
    context = ResolvedAccessContext(
        principal_id="person:1001",
        role_id="family",
        profile_id="alpha",
        conversation_scope="telegram:1001",
        capabilities=frozenset({"terminal", "documents"}),
        delivery_target=DeliveryTarget("telegram", "account", "dm", "1001"),
    )
    home_token = set_hermes_home_override(str(home))
    try:
        with bind_resolved_access_context(context):
            yield
    finally:
        reset_hermes_home_override(home_token)


def _agent(tmp_path, workspace):
    db = SessionDB(tmp_path / "state.db")
    db.create_session("fixture", source="telegram")
    agent = SimpleNamespace(
        _session_db=db,
        session_id="fixture",
        stream_delta_callback=lambda *a, **k: None,
        interim_assistant_callback=lambda *a, **k: None,
        _stream_callback=lambda *a, **k: None,
        _turn_failed_file_mutations={},
    )
    return agent, db


def _publish_call(path):
    return SimpleNamespace(function=SimpleNamespace(name="deliver_artifact", arguments=json.dumps({"path": str(path)})))


def _write_event(path):
    payload = {
        "success": True,
        "bytes_written": 14,
        "files_modified": [str(path)],
        "resolved_path": str(path),
    }
    return {"role": "tool", "tool_name": "write_file", "tool_call_id": "write-1", "content": json.dumps(payload)}


def _delivery_event(path):
    payload = {"success": True, "status": "ready_for_delivery", "file_name": "report.csv", "media_tag": "MEDIA:" + str(path)}
    return {
        "role": "tool",
        "name": "deliver_artifact",
        "tool_call_id": "publish-1",
        "content": json.dumps(payload),
    }


def test_scope_holds_text_and_exports_one_confirmation(tmp_path, workspace, monkeypatch):
    agent, db = _agent(tmp_path, workspace)
    try:
        with bound(workspace, monkeypatch):
            with artifact_turn_scope(agent) as turn:
                assert turn is not None
                assert agent.stream_delta_callback is None
                assert agent._stream_callback is None
                turn.begin_batch([_publish_call(workspace / "report.csv")])
                assert turn.transaction_id
                row = json.loads(db.get_session("fixture")["artifact_delivery_json"])
                assert row["state"] == "pending"
                artifact = (workspace / "report.csv").resolve()
                turn.observe(_write_event(artifact))
                turn.observe(_delivery_event(artifact))
                action, nudge = turn.finish()
                assert (action, nudge) == ("confirmed", None)
                result = turn.export({"completed": True, "final_response": "Готово"})
                assert result["completed"] is True
                assert result["artifact_delivery_confirmation"]["transaction_session_id"] == "fixture"
                assert result["artifact_delivery_confirmation"]["path"].endswith("report.csv")
                assert json.loads(db.get_session("fixture")["artifact_delivery_json"])["state"] == "ready"
            assert callable(agent.stream_delta_callback)
    finally:
        db.close()


def test_scope_blocks_a_failed_turn_and_abandons_the_transaction(tmp_path, workspace, monkeypatch):
    agent, db = _agent(tmp_path, workspace)
    try:
        with bound(workspace, monkeypatch):
            with artifact_turn_scope(agent) as turn:
                turn.begin_batch([_publish_call(workspace / "report.csv")])
                turn.observe(_delivery_event((workspace / "report.csv").resolve()))
                result = turn.export({"completed": True, "final_response": "Готово"})
                assert result["final_response"] == ARTIFACT_FAILURE
                assert result["failed"] is True
            state = json.loads(db.get_session("fixture")["artifact_delivery_json"])
            assert state["state"] in {"abandoned", "ready"}
    finally:
        db.close()


def test_scope_is_inert_without_a_bound_document_context(workspace, monkeypatch):
    agent = SimpleNamespace(_session_db=None, session_id="fixture")
    with artifact_turn_scope(agent) as turn:
        assert turn is None
