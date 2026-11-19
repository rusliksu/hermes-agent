"""Real SQLite artifact claims, receipts and compression-lineage recovery (0.23 candidate)."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from hermes_state import SessionDB


def state(db, sid="fixture"):
    return json.loads(db.get_session(sid)["artifact_delivery_json"])


def test_claim_is_exact_once_and_receipt_is_required(tmp_path):
    with SessionDB(tmp_path / "state.db") as db:
        db.create_session("fixture", source="cli")
        token = db.begin_artifact_delivery("fixture")
        assert db.begin_artifact_delivery("fixture") == token
        assert state(db)["state"] == "pending"
        assert db.transition_artifact_delivery("fixture", token, "pending", "ready")
        assert db.transition_artifact_delivery("fixture", token, "ready", "delivery_started")
        assert not db.transition_artifact_delivery("fixture", token, "ready", "delivery_started")
        assert not db.transition_artifact_delivery("fixture", token, "delivery_started", "delivered")
        assert db.begin_artifact_delivery("fixture") is None
        assert db.transition_artifact_delivery(
            "fixture", token, "delivery_started", "delivered", receipt_id="fixture-receipt",
        )
        assert state(db) == {
            "state": "delivered", "transaction_id": token, "receipt_id": "fixture-receipt",
        }


@pytest.mark.parametrize(
    "before,after",
    [("pending", "abandoned"), ("ready", "abandoned"), ("delivery_started", "uncertain")],
)
def test_restart_terminalizes_without_replay_across_compression(tmp_path, before, after):
    path = tmp_path / "state.db"
    with SessionDB(path) as db:
        db.create_session("fixture", source="telegram")
        token = db.begin_artifact_delivery("fixture")
        if before != "pending":
            assert db.transition_artifact_delivery("fixture", token, "pending", "ready")
        if before == "delivery_started":
            assert db.transition_artifact_delivery("fixture", token, "ready", "delivery_started")
        db.end_session("fixture", "compression")
        db.create_session("child", source="telegram", parent_session_id="fixture")
    with SessionDB(path) as restored:
        recovered = restored.recover_artifact_delivery("child")
        assert recovered["previous_state"] == before
        assert recovered["state"] == after
        assert state(restored)["state"] == after
        assert not restored.transition_artifact_delivery(
            "fixture", token, "ready", "delivery_started",
        )
        assert restored.recover_artifact_delivery("child") is None


def test_transaction_survives_two_compressions_and_sets_on_the_root(tmp_path):
    path = tmp_path / "state.db"
    with SessionDB(path) as db:
        db.create_session("root", source="telegram")
        token = db.begin_artifact_delivery("root")
        assert db.transition_artifact_delivery("root", token, "pending", "ready")
        db.end_session("root", "compression")
        db.create_session("mid", source="telegram", parent_session_id="root")
        db.end_session("mid", "compression")
        db.create_session("tip", source="telegram", parent_session_id="mid")
    with SessionDB(path) as restored:
        recovered = restored.recover_artifact_delivery("tip")
        assert recovered["session_id"] == "root"
        assert recovered["previous_state"] == "ready"
        assert recovered["state"] == "abandoned"
        assert state(restored, "root")["state"] == "abandoned"
        assert restored.get_session("tip")["artifact_delivery_json"] is None
        assert restored.recover_artifact_delivery("tip") is None


def test_malformed_transaction_is_quarantined(tmp_path):
    with SessionDB(tmp_path / "state.db") as db:
        db.create_session("fixture", source="cli")
        db.begin_artifact_delivery("fixture")
        db._conn.execute(
            "UPDATE sessions SET artifact_delivery_json=? WHERE id='fixture'",
            ('{"state":"ready","transaction_id":"invalid","extra":1}',),
        )
        db._conn.commit()
        assert db.begin_artifact_delivery("fixture") is None
        assert state(db) == {"state": "uncertain", "malformed": True}


def test_independent_handles_cannot_replace_or_double_claim(tmp_path):
    path = tmp_path / "state.db"
    with SessionDB(path) as first, SessionDB(path) as second:
        first.create_session("fixture", source="cli")
        with ThreadPoolExecutor(max_workers=2) as pool:
            tokens = list(pool.map(
                lambda db: db.begin_artifact_delivery("fixture"), [first, second],
            ))
            assert tokens[0] == tokens[1] and tokens[0]
            assert first.transition_artifact_delivery("fixture", tokens[0], "pending", "ready")
            claims = list(pool.map(
                lambda db: db.transition_artifact_delivery(
                    "fixture", tokens[0], "ready", "delivery_started",
                ),
                [first, second],
            ))
            assert sum(claims) == 1


def test_native_read_only_store_without_extension_remains_readable(tmp_path):
    path = tmp_path / "state.db"
    with SessionDB(path) as db:
        db.create_session("fixture", source="cli")
        db.append_message("fixture", "user", "native fixture")
    with SessionDB(path, read_only=True) as reader:
        assert reader.get_session("fixture")["source"] == "cli"
        assert reader.get_messages("fixture")[0]["content"] == "native fixture"
