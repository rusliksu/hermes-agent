"""Egress handoff: one ready transaction becomes an upload claim or a terminal abandon."""
import contextlib
import json
from types import SimpleNamespace

import pytest

from gateway.access_context import DeliveryTarget, ResolvedAccessContext, bind_resolved_access_context
from gateway.artifact_delivery import abandon_confirmation, deliver_confirmed, prepare_confirmation
from gateway.platforms.base import SendResult
from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from hermes_state import SessionDB


class FakeAdapter:
    def __init__(self, receipt="42", success=True):
        self.receipt = receipt
        self.success = success
        self.sent = []

    def _final_delivery_adapter(self, source):
        return self

    def _media_delivery_scope(self, source):
        return contextlib.nullcontext()

    async def send_document(self, chat_id, file_path, metadata=None, _require_native=False):
        self.sent.append((chat_id, file_path))
        return SendResult(success=self.success, message_id=self.receipt if self.success else None)


def _runner(db):
    store = SimpleNamespace(_db_for_key=lambda key: db)
    return SimpleNamespace(
        session_store=store,
        _media_delivery_scope_for_source=lambda source: contextlib.nullcontext(),
    )


@pytest.fixture
def wired(tmp_path, monkeypatch):
    home = tmp_path / "state" / "profiles" / "alpha"
    home.mkdir(parents=True)
    artifact = home / "report.csv"
    artifact.write_text("fixture,value\n", encoding="utf-8")
    db = SessionDB(tmp_path / "state.db")
    db.create_session("fixture", source="telegram")
    context = ResolvedAccessContext(
        principal_id="person:1001",
        role_id="family",
        profile_id="alpha",
        conversation_scope="telegram:1001",
        capabilities=frozenset({"terminal", "documents"}),
        delivery_target=DeliveryTarget("telegram", "account", "dm", "1001"),
    )
    monkeypatch.setenv("TERMINAL_CWD", str(home))
    token = set_hermes_home_override(str(home))
    binding = bind_resolved_access_context(context)
    binding.__enter__()
    try:
        yield db, home, artifact
    finally:
        binding.__exit__(None, None, None)
        reset_hermes_home_override(token)
        db.close()


def _confirmation(db, artifact):
    token = db.begin_artifact_delivery("fixture")
    assert db.transition_artifact_delivery("fixture", token, "pending", "ready")
    path = str(artifact.resolve())
    return {
        "path": path,
        "media_tag": "MEDIA:" + path,
        "tool_call_id": "publish-1",
        "transaction_session_id": "fixture",
        "transaction_id": token,
    }


def _event():
    return SimpleNamespace(source=SimpleNamespace(chat_id="1001", platform=None))


@pytest.mark.asyncio
async def test_publish_confirms_then_claims_and_stores_the_receipt(wired):
    db, home, artifact = wired
    confirmation = _confirmation(db, artifact)
    event = _event()
    runner = _runner(db)
    assert await prepare_confirmation(runner, event, "telegram:1001", confirmation, publish=True) is True
    assert event.artifact_delivery_confirmation["path"] == confirmation["path"]
    adapter = FakeAdapter()
    result = await deliver_confirmed(adapter, event, "telegram:1001", {"caption": None})
    assert result.success is True and result.message_id == "42"
    assert adapter.sent and adapter.sent[0][1] == confirmation["path"]
    row = json.loads(db.get_session("fixture")["artifact_delivery_json"])
    assert row["state"] == "delivered" and row["receipt_id"] == "42"


@pytest.mark.asyncio
async def test_unpublished_confirmation_is_terminal(wired):
    db, home, artifact = wired
    confirmation = _confirmation(db, artifact)
    event = _event()
    assert await prepare_confirmation(_runner(db), event, "telegram:1001", confirmation, publish=False) is False
    assert json.loads(db.get_session("fixture")["artifact_delivery_json"])["state"] == "abandoned"


@pytest.mark.asyncio
async def test_missing_native_receipt_leaves_the_transaction_uncertain(wired):
    db, home, artifact = wired
    confirmation = _confirmation(db, artifact)
    event = _event()
    assert await prepare_confirmation(_runner(db), event, "telegram:1001", confirmation, publish=True) is True
    result = await deliver_confirmed(FakeAdapter(success=False), event, "telegram:1001", {})
    assert result.success is False
    assert json.loads(db.get_session("fixture")["artifact_delivery_json"])["state"] == "uncertain"


@pytest.mark.asyncio
async def test_abandon_confirmation_terminalizes_a_ready_transaction(wired):
    db, home, artifact = wired
    confirmation = _confirmation(db, artifact)
    event = _event()
    assert await prepare_confirmation(_runner(db), event, "telegram:1001", confirmation, publish=True) is True
    await abandon_confirmation(event)
    assert event.artifact_delivery_confirmation is None
    assert json.loads(db.get_session("fixture")["artifact_delivery_json"])["state"] == "abandoned"
