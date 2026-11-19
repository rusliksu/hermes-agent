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
        now = datetime.now()
        entry = SessionEntry(
            session_key="telegram:42", session_id="root", created_at=now, updated_at=now,
        )
        (sessions_dir / "sessions.json").write_text(
            json.dumps({entry.session_key: entry.to_dict()}), encoding="utf-8",
        )

        store = SessionStore(sessions_dir, GatewayConfig())
        store._db_for_key = lambda key: db
        store._ensure_loaded_locked()

        loaded = store._entries[entry.session_key]
        assert loaded.resume_pending is True
        assert loaded.resume_reason == "artifact_delivery_abandoned"
        assert loaded.last_resume_marked_at is not None
        assert json.loads(db.get_session("root")["artifact_delivery_json"])["state"] == "abandoned"
    finally:
        db.close()
