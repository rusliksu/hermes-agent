"""Boundary: bound document ingress -> registry tool -> store -> native upload receipt."""
import contextlib
import json
from types import SimpleNamespace

import pytest

from agent.artifact_turn import ARTIFACT_FAILURE, artifact_turn_scope
from gateway.access_context import DeliveryTarget, ResolvedAccessContext, bind_resolved_access_context
from gateway.artifact_delivery import abandon_confirmation, deliver_confirmed, prepare_confirmation
from gateway.platforms.base import SendResult
from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from hermes_state import SessionDB
from model_tools import handle_function_call


class FakeTransport:
    """The transport seam: a native upload that answers with a receipt."""

    def __init__(self, receipt="tg-42", success=True):
        self.receipt = receipt
        self.success = success
        self.uploads = []

    def _final_delivery_adapter(self, source):
        return self

    def _media_delivery_scope(self, source):
        return contextlib.nullcontext()

    async def send_document(self, chat_id, file_path, metadata=None, _require_native=False):
        self.uploads.append((chat_id, file_path))
        return SendResult(success=self.success, message_id=self.receipt if self.success else None)


def _runner(db):
    return SimpleNamespace(
        session_store=SimpleNamespace(_db_for_key=lambda key: db),
        _media_delivery_scope_for_source=lambda source: contextlib.nullcontext(),
    )


def _event():
    return SimpleNamespace(
        source=SimpleNamespace(chat_id="1001", platform=None),
        artifact_delivery_confirmation=None,
    )


def _publish_call(path):
    return SimpleNamespace(
        function=SimpleNamespace(name="deliver_artifact", arguments=json.dumps({"path": str(path)})),
    )


@pytest.fixture
def wired(tmp_path, monkeypatch):
    root = tmp_path / "state"
    homes = {}
    for name in ("alpha", "beta"):
        home = root / "profiles" / name
        home.mkdir(parents=True)
        homes[name] = home
    artifact = homes["alpha"] / "report.csv"
    artifact.write_text("fixture,value\n", encoding="utf-8")
    db = SessionDB(tmp_path / "state.db")
    db.create_session("fixture", source="telegram")
    context = ResolvedAccessContext(
        profile_id="alpha",
        role_id="family",
        capabilities=frozenset({"terminal", "documents"}),
        delivery_target=DeliveryTarget("telegram", "1001"),
    )
    monkeypatch.setenv("TERMINAL_CWD", str(homes["alpha"]))
    token = set_hermes_home_override(str(homes["alpha"]))
    binding = bind_resolved_access_context(context)
    binding.__enter__()
    try:
        yield homes, db, artifact
    finally:
        binding.__exit__(None, None, None)
        reset_hermes_home_override(token)
        db.close()


def _tool_message(result_payload, tool_call_id="publish-1"):
    return {
        "role": "tool",
        "tool_name": "deliver_artifact",
        "tool_call_id": tool_call_id,
        "content": json.dumps(result_payload),
    }


def _write_message(path, tool_call_id="write-1"):
    payload = {
        "success": True,
        "bytes_written": 14,
        "files_modified": [str(path)],
        "resolved_path": str(path),
    }
    return {"role": "tool", "tool_name": "write_file", "tool_call_id": tool_call_id, "content": json.dumps(payload)}


@pytest.mark.asyncio
async def test_bound_document_reaches_a_native_upload_receipt(wired):
    homes, db, artifact = wired
    agent = SimpleNamespace(
        _session_db=db,
        session_id="fixture",
        stream_delta_callback=lambda *a, **k: None,
        interim_assistant_callback=lambda *a, **k: None,
        _stream_callback=lambda *a, **k: None,
        _turn_failed_file_mutations={},
    )
    path = artifact.resolve()
    with artifact_turn_scope(agent) as turn:
        assert turn is not None
        turn.begin_batch([_publish_call(path)])
        assert turn.transaction_id

        dispatched = json.loads(handle_function_call(
            "deliver_artifact", {"path": str(path)}, task_id="fixture",
        ))
        assert dispatched["success"] is True
        assert dispatched["media_tag"] == "MEDIA:" + str(path)

        turn.observe(_write_message(path))
        turn.observe(_tool_message(dispatched))
        assert turn.finish() == ("confirmed", None)
        result = turn.export({"completed": True, "final_response": "Готово"})
    confirmation = result["artifact_delivery_confirmation"]
    assert confirmation["path"] == str(path)

    event = _event()
    assert await prepare_confirmation(_runner(db), event, "telegram:1001", confirmation, publish=True) is True
    transport = FakeTransport()
    uploaded = await deliver_confirmed(transport, event, "telegram:1001", {"caption": None})
    assert uploaded.success is True and uploaded.message_id == "tg-42"
    assert transport.uploads == [("1001", str(path))]
    row = json.loads(db.get_session("fixture")["artifact_delivery_json"])
    assert row["state"] == "delivered" and row["receipt_id"] == "tg-42"


@pytest.mark.asyncio
async def test_foreign_document_never_reaches_the_upload(wired):
    homes, db, artifact = wired
    foreign = homes["beta"] / "foreign.csv"
    foreign.write_text("foreign,value\n", encoding="utf-8")
    refused = json.loads(handle_function_call(
        "deliver_artifact", {"path": str(foreign)}, task_id="fixture",
    ))
    assert refused["success"] is False
    assert "media_tag" not in refused
    event = _event()
    transport = FakeTransport()
    assert await prepare_confirmation(_runner(db), event, "telegram:1001", None, publish=True) is False
    assert transport.uploads == []
    assert event.artifact_delivery_confirmation is None


@pytest.mark.asyncio
async def test_unpublished_turn_abandons_before_any_upload(wired):
    homes, db, artifact = wired
    path = artifact.resolve()
    db_token = db.begin_artifact_delivery("fixture")
    assert db.transition_artifact_delivery("fixture", db_token, "pending", "ready")
    confirmation = {
        "path": str(path),
        "media_tag": "MEDIA:" + str(path),
        "tool_call_id": "publish-1",
        "transaction_session_id": "fixture",
        "transaction_id": db_token,
    }
    event = _event()
    assert await prepare_confirmation(_runner(db), event, "telegram:1001", confirmation, publish=False) is False
    assert json.loads(db.get_session("fixture")["artifact_delivery_json"])["state"] == "abandoned"
    transport = FakeTransport()
    assert transport.uploads == []
    await abandon_confirmation(event)


def test_deliver_artifact_is_registered_in_the_file_toolset():
    from toolsets import TOOLSETS

    assert "deliver_artifact" in TOOLSETS["file"]["tools"]
