"""Typed Gurra recall policy and the read-only view consumed by native search shapes."""
from pathlib import Path

from hermes_state_access import session_scope_sql


def resolved_session_scope(profile):
    """Authorize before opening any store; untyped non-multiplex CLI stays native."""
    from agent.access_paths import bound_profile_home
    from agent.secret_scope import is_multiplex_active
    from gateway.access_registry import (
        deserialize_resolved_access_context, serialize_resolved_access_context,
        session_scope_from_resolved_access_context,
    )
    from gateway.access_context import get_resolved_access_context

    context = get_resolved_access_context()
    if context is None:
        if is_multiplex_active():
            raise ValueError("session_search denied: resolved access context unavailable")
        return None, None
    context = deserialize_resolved_access_context(serialize_resolved_access_context(context))
    if context.role_id != "owner" and not (
        context.role_id == "family" and "session_search" in context.capabilities
    ):
        raise ValueError("session_search denied: role lacks permission")
    if context.delivery_target.peer_kind != "dm":
        raise ValueError("session_search denied: typed access requires a DM target")
    home = bound_profile_home()
    if profile is not None and str(profile).strip():
        from hermes_cli.profiles import normalize_profile_name
        if normalize_profile_name(profile) != context.profile_id:
            raise ValueError("session_search denied: foreign profile")
    return session_scope_from_resolved_access_context(context), home


class ScopedSessionReader:
    """Expose only scoped readers; lineage and hydration use the same SQL authority."""

    def __init__(self, db, scope, home):
        from hermes_state import SessionDB
        if not isinstance(db, SessionDB) or Path(db.db_path).resolve() != (home / "state.db").resolve():
            raise ValueError(
                f"session_search denied: bound profile store mismatch ({db.__class__.__name__}, "
                f"{Path(getattr(db, 'db_path', '')).resolve()!r} != {(home / 'state.db').resolve()!r})")
        self._db, self._scope = db, dict(scope)

    def get_session(self, sid):
        row = self._db.get_session(sid, session_scope=self._scope)
        if row and row.get("parent_session_id") and not self._db.get_session(
            row["parent_session_id"], session_scope=self._scope
        ):
            row["parent_session_id"] = None
        return row

    def resolve_session_by_title(self, title):
        return self._db.resolve_session_by_title(title, session_scope=self._scope)

    def search_messages(self, **kwargs):
        return self._db.search_messages(**kwargs, session_scope=self._scope)

    def list_recent_sessions_bounded(self, **kwargs):
        return self._db.list_recent_sessions_bounded(**kwargs, session_scope=self._scope)

    def get_messages(self, sid):
        return self._db.get_messages(sid) if self.get_session(sid) else []

    def get_messages_around(self, sid, anchor, **kwargs):
        if not self.get_session(sid):
            return {"window": [], "messages_before": 0, "messages_after": 0}
        return self._db.get_messages_around(sid, anchor, **kwargs)

    def get_anchored_view(self, sid, anchor, **kwargs):
        if not self.get_session(sid):
            return {"window": [], "messages_before": 0, "messages_after": 0,
                    "bookend_start": [], "bookend_end": []}
        return self._db.get_anchored_view(sid, anchor, **kwargs)

    def get_message_storage_state(self, message_id):
        clause, values = session_scope_sql(self._scope)
        row = self._db._read_one(
            "SELECT m.session_id, m.active, m.compacted FROM messages m "
            "JOIN sessions s ON s.id=m.session_id WHERE m.id=? AND " + clause,
            (message_id, *values),
        )
        return dict(row) if row else None

    def fts_rebuild_status(self):
        # Store-wide index counters include other peers; do not expose them as scoped recall metadata.
        return None
