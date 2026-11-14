"""Interrupted artifact uploads must be terminalized and re-armed on gateway load."""
import json
from datetime import datetime

from hermes_state import SessionDB


def test_load_marks_interrupted_upload_for_resume(tmp_path):
    from gateway.config import GatewayConfig
    from gateway.session import SessionEntry, SessionStore

    db = SessionDB(tmp_path / "state.db")
    try:
        db.create_session("root", source="telegram")
        token = db.begin_artifact_delivery("root")
        assert db.transition_artifact_delivery("root", token, "pending", "ready")

        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        store = SessionStore(sessions_dir, GatewayConfig())
        now = datetime.now()
        entry = SessionEntry(
            session_key="telegram:42", session_id="root", created_at=now, updated_at=now,
        )
        store._entries[entry.session_key] = entry
        store._db_for_key = lambda key: db

        store._recover_artifact_deliveries_locked()

        assert entry.resume_pending is True
        assert entry.resume_reason == "artifact_delivery_abandoned"
        assert entry.last_resume_marked_at is not None
        assert json.loads(db.get_session("root")["artifact_delivery_json"])["state"] == "abandoned"
    finally:
        db.close()
