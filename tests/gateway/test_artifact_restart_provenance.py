"""Restart-safe provenance for bound document delivery."""

from __future__ import annotations

import json
import sqlite3
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import hermes_state
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, SendResult
from gateway.run import GatewayRunner, build_resume_recovery_note
from gateway.session import SessionSource, SessionStore, build_session_key
from plugins.platforms.telegram.adapter import TelegramAdapter

_NONCE = "A" * 43


def _source(*, profile: str | None = None) -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="restart-provenance-chat",
        chat_type="dm",
        user_id="restart-provenance-user",
        profile=profile,
    )


def _store(monkeypatch: pytest.MonkeyPatch, root: Path) -> SessionStore:
    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", root / "state.db")
    return SessionStore(
        sessions_dir=root / "sessions",
        config=GatewayConfig(write_sessions_json=False),
    )


def _close(store: SessionStore) -> None:
    if store._db is not None:
        store._db.close()


def _artifact_delivery(db: hermes_state.SessionDB, session_id: str):
    row = db.get_session(session_id)
    raw = row["artifact_delivery_json"] if row else None
    return json.loads(raw) if raw is not None else None


@pytest.mark.parametrize(
    "raw",
    [
        "{malformed",
        "null",
        "[]",
        '{"state":"pending"}',
        '{"state":"pending","turn_id":1}',
        '{"state":"pending","turn_id":"turn-a","receipt_id":"receipt-a"}',
        '{"state":"delivered","turn_id":"turn-a"}',
        '{"state":"delivered","turn_id":"turn-a","receipt_id":"receipt-a","extra":true}',
        '{"turn_id":"turn-a","state":"pending"}',
        '{ "state":"pending","turn_id":"turn-a"}',
        '{"state":"pending","transaction_id":"too-short"}',
        '{"state":"uncertain"}',
    ],
)
def test_decoder_marks_every_invalid_or_noncanonical_payload_malformed(raw):
    value, is_valid = hermes_state.SessionDB._decode_artifact_delivery(raw)

    assert value == {"state": "uncertain", "malformed": True}
    assert is_valid is False


@pytest.mark.parametrize(
    "value",
    [
        {"state": "pending", "transaction_id": _NONCE},
        {"state": "ready", "transaction_id": _NONCE},
        {"state": "delivery_started", "transaction_id": _NONCE},
        {"state": "delivered", "transaction_id": _NONCE, "receipt_id": "receipt-a"},
        {"state": "uncertain", "transaction_id": _NONCE},
        {"state": "uncertain", "malformed": True},
        {"state": "abandoned", "transaction_id": _NONCE},
    ],
)
def test_decoder_accepts_only_exact_canonical_state_shapes(value):
    raw = hermes_state.SessionDB._encode_artifact_delivery(value)

    assert hermes_state.SessionDB._decode_artifact_delivery(raw) == (value, True)


def test_legacy_sessions_schema_adds_nullable_artifact_column_without_data_loss(
    tmp_path,
):
    db_path = tmp_path / "legacy-state.db"
    legacy_schema = hermes_state.SCHEMA_SQL.replace(
        "    artifact_delivery_json TEXT,\n", ""
    )
    assert legacy_schema != hermes_state.SCHEMA_SQL

    conn = sqlite3.connect(db_path)
    conn.executescript(legacy_schema)
    legacy_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(sessions)")
    }
    assert "artifact_delivery_json" not in legacy_columns
    conn.execute(
        "INSERT INTO schema_version (version) VALUES (?)",
        (hermes_state.SCHEMA_VERSION,),
    )
    conn.execute(
        """INSERT INTO sessions (
               id, source, display_name, started_at, ended_at, end_reason,
               message_count, input_tokens, output_tokens
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("legacy-session", "telegram", "Legacy state", 10.0, 20.0, "agent_close", 1, 7, 11),
    )
    conn.execute(
        """INSERT INTO messages (session_id, role, content, timestamp)
           VALUES (?, ?, ?, ?)""",
        ("legacy-session", "assistant", "preserved transcript", 15.0),
    )
    conn.commit()
    conn.close()

    db = hermes_state.SessionDB(db_path)
    column_rows = list(db._conn.execute("PRAGMA table_info(sessions)"))
    columns = {row[1]: row for row in column_rows}
    session = db.get_session("legacy-session")
    messages = db.get_messages("legacy-session")

    assert sum(row[1] == "artifact_delivery_json" for row in column_rows) == 1
    assert columns["artifact_delivery_json"][3] == 0
    assert columns["artifact_delivery_json"][4] is None
    assert session["artifact_delivery_json"] is None
    assert session["display_name"] == "Legacy state"
    assert session["end_reason"] == "agent_close"
    assert session["message_count"] == 1
    assert session["input_tokens"] == 7
    assert session["output_tokens"] == 11
    assert [(message["role"], message["content"]) for message in messages] == [
        ("assistant", "preserved transcript")
    ]
    db.close()


@pytest.mark.parametrize("persist_tool_result", [False, True])
def test_pending_reopen_abandons_without_trusting_committed_tool_rows(
    monkeypatch,
    tmp_path,
    persist_tool_result,
):
    store = _store(monkeypatch, tmp_path)
    entry = store.get_or_create_session(_source())
    transaction_id = store._db.begin_artifact_delivery(entry.session_id)
    assert transaction_id
    if persist_tool_result:
        store._db.append_message(
            entry.session_id,
            "tool",
            json.dumps(
                {
                    "success": True,
                    "status": "ready_for_delivery",
                    "media_tag": "MEDIA:/trusted/report.xlsx",
                }
            ),
            tool_name="deliver_artifact",
            tool_call_id="imported-looking-result",
        )
    _close(store)

    reopened = _store(monkeypatch, tmp_path)
    [recovered_entry] = reopened.list_sessions()
    transaction = _artifact_delivery(reopened._db, entry.session_id)

    assert transaction == {
        "state": "abandoned",
        "transaction_id": transaction_id,
    }
    assert recovered_entry.resume_pending is True
    assert recovered_entry.resume_reason == "artifact_delivery_abandoned"
    note = build_resume_recovery_note(recovered_entry.resume_reason, "")
    assert "explicitly retry" in note.lower()
    assert "do not re-execute" in note.lower()
    _close(reopened)


def test_uncertain_recovery_note_is_channel_neutral_and_has_no_durable_receipt():
    note = build_resume_recovery_note("artifact_delivery_uncertain", "")

    assert "telegram" not in note.lower()
    assert "no durable receipt" in note.lower()


def test_only_matching_transaction_can_make_pending_ready(tmp_path):
    db = hermes_state.SessionDB(tmp_path / "state.db")
    db.create_session("session-a", "telegram")
    transaction_id = db.begin_artifact_delivery("session-a")
    assert transaction_id

    assert not db.transition_artifact_delivery(
        "session-a", "B" * 43, "pending", "ready"
    )
    assert not db.transition_artifact_delivery(
        "missing-session", transaction_id, "pending", "ready"
    )
    assert _artifact_delivery(db, "session-a") == {
        "state": "pending",
        "transaction_id": transaction_id,
    }
    assert db.transition_artifact_delivery(
        "session-a", transaction_id, "pending", "ready"
    )
    db.close()


def test_nonterminal_is_reused_but_terminal_mints_a_new_transaction(tmp_path):
    db = hermes_state.SessionDB(tmp_path / "state.db")
    db.create_session("session-a", "telegram")
    transaction_id = db.begin_artifact_delivery("session-a")
    assert transaction_id
    assert db.begin_artifact_delivery("session-a") == transaction_id
    assert db.transition_artifact_delivery(
        "session-a", transaction_id, "pending", "abandoned"
    )
    next_transaction_id = db.begin_artifact_delivery("session-a")
    assert next_transaction_id
    assert next_transaction_id != transaction_id
    db.close()


def test_compression_tip_recovers_exact_pending_parent(tmp_path):
    db = hermes_state.SessionDB(tmp_path / "state.db")
    db.create_session("parent", "telegram")
    transaction_id = db.begin_artifact_delivery("parent")
    assert transaction_id
    db.end_session("parent", "compression")
    db.create_session("child", "telegram", parent_session_id="parent")

    assert db.recover_artifact_delivery("child") == {
        "previous_state": "pending",
        "state": "abandoned",
        "session_id": "parent",
        "transaction_id": transaction_id,
    }
    assert _artifact_delivery(db, "child") is None
    assert _artifact_delivery(db, "parent")["state"] == "abandoned"
    db.close()


def test_compression_tip_skips_terminal_child_and_recovers_live_parent(tmp_path):
    db = hermes_state.SessionDB(tmp_path / "state.db")
    db.create_session("parent", "telegram")
    parent_transaction_id = db.begin_artifact_delivery("parent")
    assert parent_transaction_id
    db.end_session("parent", "compression")
    db.create_session("child", "telegram", parent_session_id="parent")
    child_transaction_id = db.begin_artifact_delivery("child")
    assert child_transaction_id
    assert db.transition_artifact_delivery(
        "child", child_transaction_id, "pending", "abandoned"
    )

    assert db.recover_artifact_delivery("child") == {
        "previous_state": "pending",
        "state": "abandoned",
        "session_id": "parent",
        "transaction_id": parent_transaction_id,
    }
    assert _artifact_delivery(db, "child") == {
        "state": "abandoned",
        "transaction_id": child_transaction_id,
    }
    assert _artifact_delivery(db, "parent") == {
        "state": "abandoned",
        "transaction_id": parent_transaction_id,
    }
    db.close()


@pytest.mark.parametrize(
    ("operation", "initial_state", "expected_state"),
    [
        ("reset", "pending", "abandoned"),
        ("switch", "ready", "abandoned"),
        ("reset", "delivery_started", "uncertain"),
        ("switch", "delivery_started", "uncertain"),
    ],
)
def test_reset_and_switch_terminalize_without_inheritance(
    monkeypatch,
    tmp_path,
    operation,
    initial_state,
    expected_state,
):
    store = _store(monkeypatch, tmp_path)
    entry = store.get_or_create_session(_source())
    old_session_id = entry.session_id
    transaction_id = store._db.begin_artifact_delivery(old_session_id)
    assert transaction_id
    if initial_state in {"ready", "delivery_started"}:
        assert store._db.transition_artifact_delivery(
            old_session_id, transaction_id, "pending", "ready"
        )
    if initial_state == "delivery_started":
        assert store._db.transition_artifact_delivery(
            old_session_id, transaction_id, "ready", "delivery_started"
        )

    if operation == "reset":
        new_entry = store.reset_session(entry.session_key)
    else:
        target = "switch-target"
        store._db.create_session(target, "telegram")
        new_entry = store.switch_session(entry.session_key, target)

    assert new_entry is not None
    assert _artifact_delivery(store._db, old_session_id)["state"] == expected_state
    assert _artifact_delivery(store._db, new_entry.session_id) is None
    _close(store)


@pytest.mark.parametrize("operation", ["reset", "switch"])
@pytest.mark.parametrize("terminalization_fails", [False, True])
def test_reset_and_switch_publish_only_after_successful_terminalization(
    monkeypatch,
    tmp_path,
    operation,
    terminalization_fails,
):
    store = _store(monkeypatch, tmp_path)
    old_entry = store.get_or_create_session(_source())
    old_session_id = old_entry.session_id
    transaction_id = store._db.begin_artifact_delivery(old_session_id)
    assert transaction_id
    if operation == "switch":
        store._db.create_session("switch-target", "telegram")

    observed_routes = []

    def terminalize(session_id):
        observed_routes.append(store._entries[old_entry.session_key].session_id)
        assert session_id == old_session_id
        if terminalization_fails:
            raise RuntimeError("synthetic terminalization failure")
        return {
            "previous_state": "pending",
            "state": "abandoned",
            "session_id": old_session_id,
            "transaction_id": transaction_id,
        }

    monkeypatch.setattr(store._db, "recover_artifact_delivery", terminalize)

    if operation == "reset":
        result = store.reset_session(old_entry.session_key)
    else:
        result = store.switch_session(old_entry.session_key, "switch-target")

    assert observed_routes == [old_session_id]
    if terminalization_fails:
        assert result is None
        assert store._entries[old_entry.session_key] is old_entry
        assert store._entries[old_entry.session_key].session_id == old_session_id
    else:
        assert result is not None
        assert store._entries[old_entry.session_key].session_id == result.session_id
        assert result.session_id != old_session_id
    _close(store)


def test_malformed_json_and_profile_session_transaction_mismatches_fail_closed(tmp_path):
    profile_a = hermes_state.SessionDB(tmp_path / "profile-a" / "state.db")
    profile_b = hermes_state.SessionDB(tmp_path / "profile-b" / "state.db")
    for db in (profile_a, profile_b):
        db.create_session("same-id", "telegram")
    transaction_id = profile_a.begin_artifact_delivery("same-id")
    assert transaction_id
    assert _artifact_delivery(profile_b, "same-id") is None
    assert not profile_a.transition_artifact_delivery(
        "same-id", "B" * 43, "pending", "ready"
    )
    profile_a._execute_write(
        lambda conn: conn.execute(
            "UPDATE sessions SET artifact_delivery_json = ? WHERE id = ?",
            ("{malformed", "same-id"),
        )
    )

    recovered = profile_a.recover_artifact_delivery("same-id")
    assert recovered["state"] == "uncertain"
    assert _artifact_delivery(profile_a, "same-id") == {
        "state": "uncertain",
        "malformed": True,
    }
    profile_a.close()
    profile_b.close()


def test_concurrent_ready_cas_has_one_winner(tmp_path):
    path = tmp_path / "state.db"
    first = hermes_state.SessionDB(path)
    second = hermes_state.SessionDB(path)
    first.create_session("session-a", "telegram")
    transaction_id = first.begin_artifact_delivery("session-a")
    assert transaction_id

    def claim(db):
        return db.transition_artifact_delivery(
            "session-a", transaction_id, "pending", "ready"
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, (first, second)))

    assert sorted(results) == [False, True]
    first.close()
    second.close()


def _telegram_adapter() -> TelegramAdapter:
    return TelegramAdapter(PlatformConfig(enabled=True, token="synthetic-token"))


def _gateway_runner(transition: AsyncMock) -> GatewayRunner:
    runner = GatewayRunner.__new__(GatewayRunner)
    runner._session_db = SimpleNamespace(transition_artifact_delivery=transition)
    return runner


def _confirmation(path: Path) -> dict[str, str]:
    return {
        "transaction_session_id": "session-a",
        "transaction_id": _NONCE,
        "path": str(path),
        "media_tag": f"MEDIA:{path}",
    }


@pytest.mark.asyncio
async def test_stale_generation_abandons_ready_confirmation_before_return(
    tmp_path,
):
    artifact = tmp_path / "report.xlsx"
    transition = AsyncMock(return_value=True)
    runner = _gateway_runner(transition)
    event = MessageEvent(text="create report", source=_source())

    publishable = await runner._prepare_artifact_delivery_confirmation(
        event,
        _confirmation(artifact),
        publish=False,
    )

    assert publishable is False
    transition.assert_awaited_once_with(
        "session-a", _NONCE, "ready", "abandoned"
    )
    assert event.artifact_delivery_confirmation is None
    assert not hasattr(event, "_artifact_delivery_transition")


@pytest.mark.asyncio
async def test_stop_during_final_completion_abandons_suppressed_ready_confirmation(
    tmp_path,
):
    artifact = tmp_path / "report.xlsx"
    artifact.write_bytes(b"synthetic workbook")
    adapter = _telegram_adapter()
    adapter.config.typing_indicator = False
    adapter.send = AsyncMock(return_value=SendResult(success=True, message_id="text"))
    adapter.send_document = AsyncMock(
        return_value=SendResult(success=True, message_id="document")
    )
    event = MessageEvent(text="create report", source=_source())
    session_key = build_session_key(event.source)
    transition = AsyncMock(return_value=True)
    calls = 0

    async def handler(current_event):
        nonlocal calls
        calls += 1
        if calls == 1:
            current_event.artifact_delivery_confirmation = _confirmation(artifact)
            current_event._artifact_delivery_transition = transition
            adapter._active_sessions[session_key].set()
            adapter._pending_messages[session_key] = MessageEvent(
                text="/stop", source=_source()
            )
            return f"DOCUMENT_SENT_OK\nMEDIA:{artifact}"
        return None

    adapter.set_message_handler(handler)
    adapter._active_sessions[session_key] = asyncio.Event()

    await adapter._process_message_background(event, session_key)
    followup = adapter._session_tasks.get(session_key)
    if followup is not None and followup is not asyncio.current_task():
        await followup

    transition.assert_awaited_once_with("ready", "abandoned")
    adapter.send_document.assert_not_awaited()
    adapter.send.assert_not_awaited()
    assert event.artifact_delivery_confirmation is None
    assert not hasattr(event, "_artifact_delivery_transition")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed_confirmation",
    [
        {"path": "/tmp/leak.xlsx", "media_tag": "MEDIA:/tmp/leak.xlsx"},
        "not-structured-provenance",
        {},
    ],
)
async def test_malformed_confirmation_quarantines_text_and_media(
    tmp_path,
    malformed_confirmation,
):
    transition = AsyncMock(return_value=True)
    runner = _gateway_runner(transition)
    event = MessageEvent(text="create report", source=_source())

    publishable = await runner._prepare_artifact_delivery_confirmation(
        event,
        malformed_confirmation,
        publish=True,
    )

    assert publishable is False
    transition.assert_not_awaited()
    assert event.artifact_delivery_confirmation is None
    assert not hasattr(event, "_artifact_delivery_transition")


@pytest.mark.asyncio
async def test_invalid_path_with_usable_transaction_ids_is_abandoned(
    tmp_path,
):
    malformed = _confirmation(tmp_path / "report.xlsx")
    malformed["media_tag"] = "MEDIA:/different/report.xlsx"
    transition = AsyncMock(return_value=True)
    runner = _gateway_runner(transition)
    event = MessageEvent(text="create report", source=_source())

    publishable = await runner._prepare_artifact_delivery_confirmation(
        event,
        malformed,
        publish=True,
    )

    assert publishable is False
    transition.assert_awaited_once_with(
        "session-a", _NONCE, "ready", "abandoned"
    )
    assert event.artifact_delivery_confirmation is None


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt_write_crashes", [False, True])
async def test_native_send_claims_started_then_requires_durable_receipt(
    tmp_path,
    receipt_write_crashes,
):
    db_path = tmp_path / "state.db"
    db = hermes_state.SessionDB(db_path)
    db.create_session("session-a", "telegram")
    transaction_id = db.begin_artifact_delivery("session-a")
    assert transaction_id
    assert db.transition_artifact_delivery(
        "session-a", transaction_id, "pending", "ready"
    )
    artifact = tmp_path / "report.xlsx"
    artifact.write_bytes(b"synthetic workbook")
    adapter = _telegram_adapter()

    async def native_send(**_kwargs):
        assert _artifact_delivery(db, "session-a")["state"] == "delivery_started"
        return SimpleNamespace(message_id=808)

    adapter._bot = SimpleNamespace(send_document=AsyncMock(side_effect=native_send))
    event = MessageEvent(text="", source=_source())

    async def transition(expected, new, receipt_id=None):
        if receipt_write_crashes and new == "delivered":
            raise RuntimeError("synthetic receipt write crash")
        return db.transition_artifact_delivery(
            "session-a",
            transaction_id,
            expected,
            new,
            receipt_id=receipt_id,
        )

    event._artifact_delivery_transition = transition
    result = await adapter._deliver_confirmed_artifact(
        event,
        str(artifact),
        metadata={"thread_id": "808"},
    )

    adapter._bot.send_document.assert_awaited_once()
    if receipt_write_crashes:
        assert result.success is False
        assert _artifact_delivery(db, "session-a")["state"] == "uncertain"
        db.close()
    else:
        assert result.success is True
        assert _artifact_delivery(db, "session-a") == {
            "state": "delivered",
            "transaction_id": transaction_id,
            "receipt_id": "808",
        }
        db.close()


class _ExplodingReceipt:
    @property
    def message_id(self):
        raise RuntimeError("synthetic post-claim receipt crash")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("send_outcome", "receipt_outcome"),
    [
        pytest.param(RuntimeError("native adapter crash"), True, id="adapter-exception"),
        pytest.param(SendResult(success=False, error="native rejected"), True, id="failed-result"),
        pytest.param(SendResult(success=True), True, id="missing-receipt"),
        pytest.param(SendResult(success=True, message_id=""), True, id="empty-receipt"),
        pytest.param(SendResult(success=True, message_id="808"), False, id="receipt-cas-failure"),
        pytest.param(SendResult(success=True, message_id="808"), RuntimeError("receipt write crash"), id="receipt-exception"),
        pytest.param(_ExplodingReceipt(), True, id="post-claim-exception"),
    ],
)
async def test_every_post_claim_failure_best_effort_marks_uncertain(
    tmp_path,
    send_outcome,
    receipt_outcome,
):
    artifact = tmp_path / "report.xlsx"
    artifact.write_bytes(b"synthetic workbook")
    adapter = _telegram_adapter()
    if isinstance(send_outcome, BaseException):
        adapter.send_document = AsyncMock(side_effect=send_outcome)
    else:
        adapter.send_document = AsyncMock(return_value=send_outcome)
    event = MessageEvent(text="", source=_source())
    transitions = []

    async def transition(expected, new, receipt_id=None):
        transitions.append((expected, new, receipt_id))
        if new == "delivered":
            if isinstance(receipt_outcome, BaseException):
                raise receipt_outcome
            return receipt_outcome
        return True

    event._artifact_delivery_transition = transition
    result = await adapter._deliver_confirmed_artifact(
        event, str(artifact), metadata=None
    )

    assert result.success is False
    assert transitions[0] == ("ready", "delivery_started", None)
    assert transitions[-1] == ("delivery_started", "uncertain", None)
    assert transitions.count(("delivery_started", "uncertain", None)) == 1


@pytest.mark.asyncio
async def test_failed_confirmed_delivery_sends_no_success_text_or_warning_fallback(
    tmp_path,
):
    artifact = tmp_path / "report.xlsx"
    artifact.write_bytes(b"synthetic workbook")
    adapter = _telegram_adapter()
    adapter.send_document = AsyncMock(
        return_value=SendResult(success=False, error="native rejected")
    )
    adapter.send = AsyncMock(return_value=SendResult(success=True, message_id="text"))

    async def handler(_event):
        return f"DOCUMENT_SENT_OK\nMEDIA:{artifact}"

    async def hold_typing(*_args, **_kwargs):
        await asyncio.Event().wait()

    transitions = []

    async def transition(expected, new, receipt_id=None):
        transitions.append((expected, new, receipt_id))
        return True

    adapter.set_message_handler(handler)
    adapter._keep_typing = hold_typing
    event = MessageEvent(text="", source=_source())
    event.artifact_delivery_confirmation = {
        "path": str(artifact),
        "media_tag": f"MEDIA:{artifact}",
    }
    event._artifact_delivery_transition = transition

    await adapter._process_message_background(event, build_session_key(event.source))

    adapter.send_document.assert_awaited_once()
    adapter.send.assert_not_awaited()
    assert transitions[-1] == ("delivery_started", "uncertain", None)


@pytest.mark.asyncio
async def test_native_requirement_blocks_warning_fallback_but_unrelated_call_is_unchanged(
    tmp_path,
):
    artifact = tmp_path / "report.xlsx"
    artifact.write_bytes(b"synthetic workbook")
    adapter = _telegram_adapter()
    adapter._bot = SimpleNamespace(
        send_document=AsyncMock(side_effect=RuntimeError("synthetic Telegram failure"))
    )
    adapter.send = AsyncMock(return_value=SendResult(success=True, message_id="warning"))
    event = MessageEvent(text="", source=_source())

    async def transition(_expected, _new, receipt_id=None):
        return True

    event._artifact_delivery_transition = transition
    confirmed = await adapter._deliver_confirmed_artifact(
        event, str(artifact), metadata=None
    )
    assert confirmed.success is False
    adapter.send.assert_not_awaited()

    ordinary = await adapter.send_document("restart-provenance-chat", str(artifact))
    assert ordinary.success is True
    adapter.send.assert_awaited_once()


def test_only_bound_document_batches_enter_transaction(monkeypatch):
    from agent import artifact_delivery_stop

    monkeypatch.setattr(
        artifact_delivery_stop, "bound_document_context_active", lambda: True
    )

    def call(name, arguments):
        return SimpleNamespace(
            function=SimpleNamespace(name=name, arguments=json.dumps(arguments))
        )

    assert artifact_delivery_stop.bound_artifact_tool_batch_relevant(
        [call("write_file", {"path": "report.xlsx"})]
    )
    assert artifact_delivery_stop.bound_artifact_tool_batch_relevant(
        [call("deliver_artifact", {"path": "/trusted/report.xlsx"})]
    )
    for name, args in (
        ("write_file", {"path": "helper.py"}),
        ("send_document", {"path": "/unrelated/report.xlsx"}),
        ("read_file", {"path": "report.xlsx"}),
        ("image_generate", {}),
        ("text_to_speech", {}),
        ("vision_analyze", {}),
    ):
        assert not artifact_delivery_stop.bound_artifact_tool_batch_relevant(
            [call(name, args)]
        )
