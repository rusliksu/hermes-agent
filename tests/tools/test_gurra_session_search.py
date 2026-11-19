"""Typed recall must not return another peer's transcript or lose own hits to LIMIT."""
import json
from dataclasses import replace

import pytest

from agent.secret_scope import is_multiplex_active, set_multiplex_active
from gateway.access_registry import DeliveryTarget
from gateway.run import _profile_runtime_scope
from gateway.access_context import bind_resolved_access_context
from hermes_state import SessionDB
from tests.agent.test_gurra_memory_snapshot import context, homes
from tools.session_search_tool import session_search

_FIXTURES = (context, homes)  # imported for pytest fixture discovery, not called here


@pytest.fixture
def access():
    return replace(context(), capabilities=frozenset({"session_search"}))


def seed(db, sid, *, peer="1001", profile="alpha", source="telegram",
         kind="dm", user=None, thread=None, parent=None, content="recall OWN_MARKER"):
    db.create_session(sid, source=source, profile_name=profile, chat_id=peer,
                      chat_type=kind, user_id=peer if user is None else user,
                      thread_id=thread, parent_session_id=parent)
    db.set_session_title(sid, sid)
    db.append_message(sid, role="user", content=content)
    return db.get_messages(sid)[0]["id"]


@pytest.fixture(name="store")
def _store_fixture(homes):
    with SessionDB(homes["alpha"] / "state.db") as db:
        seed(db, "own")
        seed(db, "foreign", peer="2002", content="recall FOREIGN_MARKER")
        yield db


@pytest.mark.parametrize("shape", [{}, {"query": "recall"}, {"session_id": "own"},
                                  {"session_id": "own", "around_message_id": 1}])
def test_four_shapes_return_only_bound_peer(store, homes, access, shape):
    with _profile_runtime_scope(homes["alpha"]), bind_resolved_access_context(access):
        output = session_search(db=store, **shape)
    result = json.loads(output)
    assert result["success"] is True
    assert "OWN_MARKER" in output
    assert "FOREIGN_MARKER" not in output
    if "results" in result:
        assert [r["session_id"] for r in result["results"]] == ["own"]


@pytest.mark.parametrize("shape", [{"session_id": "foreign"},
                                  {"session_id": "foreign", "around_message_id": 2},
                                  {"query": "foreign"}])
def test_foreign_id_anchor_and_title_do_not_leak(store, homes, access, shape):
    with _profile_runtime_scope(homes["alpha"]), bind_resolved_access_context(access):
        output = session_search(db=store, **shape)
    assert "FOREIGN_MARKER" not in output
    result = json.loads(output)
    assert result.get("success") is False or result.get("results") == []


@pytest.mark.parametrize("query", [None, "recall"])
def test_scope_is_applied_before_candidate_and_fts_limits(store, homes, access, query):
    # More foreign rows than either native scan ceiling, all newer than the allowed hit.
    for n in range(330):
        seed(store, f"foreign-{n}", peer="2002", content="recall FOREIGN_MARKER")
    store._conn.execute("UPDATE sessions SET started_at=10, last_activity_at=10 WHERE id='own'")
    store._conn.execute("UPDATE messages SET timestamp=10 WHERE session_id='own'")
    store._conn.commit()
    with _profile_runtime_scope(homes["alpha"]), bind_resolved_access_context(access):
        result = json.loads(session_search(db=store, query=query or "", limit=1, sort="newest"))
    assert result["success"] is True
    assert [r["session_id"] for r in result["results"]] == ["own"]


@pytest.mark.parametrize("field,value", [("profile_name", "beta"), ("source", "cli"),
    ("chat_type", "group"), ("chat_id", "2002"), ("user_id", "2002"), ("thread_id", "9")])
def test_each_scope_dimension_is_enforced(store, homes, access, field, value):
    store._conn.execute(f"UPDATE sessions SET {field}=? WHERE id='own'", (value,))
    store._conn.commit()
    with _profile_runtime_scope(homes["alpha"]), bind_resolved_access_context(access):
        assert json.loads(session_search(db=store, session_id="own"))["success"] is False


@pytest.mark.parametrize("selector", [{"profile": "beta"}, {"session_id": "beta/foreign"}])
def test_foreign_profile_is_rejected_before_acquiring_or_opening_db(homes, access, selector, monkeypatch):
    import hermes_state_registry
    import tools.session_search_tool as search
    def forbidden(*args, **kwargs):
        pytest.fail("denied recall must not open a database")
    monkeypatch.setattr(hermes_state_registry, "acquire", forbidden)
    monkeypatch.setattr(search, "_resolve_profile_db", forbidden)
    with _profile_runtime_scope(homes["alpha"]), bind_resolved_access_context(access):
        assert json.loads(session_search(**selector))["success"] is False


def test_multiplex_missing_context_is_denied_before_acquiring_db(monkeypatch):
    import hermes_state_registry
    monkeypatch.setattr(hermes_state_registry, "acquire", lambda: pytest.fail("unbound DB acquire"))
    previous = is_multiplex_active()
    set_multiplex_active(True)
    try:
        with bind_resolved_access_context(None):
            assert json.loads(session_search())["success"] is False
    finally:
        set_multiplex_active(previous)


@pytest.mark.parametrize("invalid", ["capability", "role", "room", "home", "store"])
def test_invalid_authority_or_store_does_not_read_transcripts(store, homes, access, invalid):
    if invalid == "capability":
        access = replace(access, capabilities=frozenset())
    elif invalid == "role":
        access = replace(access, role_id="shared_room")
    elif invalid == "room":
        access = replace(access, delivery_target=DeliveryTarget("telegram", "fixture-bot", "group", "-1003", "7"))
    home = homes["beta"] if invalid == "home" else homes["alpha"]
    if invalid == "store":
        with SessionDB(homes["beta"] / "state.db") as other:
            seed(other, "other", profile="beta", content="FOREIGN_MARKER")
            with _profile_runtime_scope(home), bind_resolved_access_context(access):
                assert json.loads(session_search(db=other, session_id="other"))["success"] is False
    else:
        with _profile_runtime_scope(home), bind_resolved_access_context(access):
            assert json.loads(session_search(db=store, session_id="own"))["success"] is False


def test_foreign_compression_parent_and_child_cannot_leak(store, homes, access):
    store.end_session("foreign", "compression")
    seed(store, "own-child", parent="foreign")
    store.end_session("own", "compression")
    foreign_anchor = seed(store, "foreign-child", parent="own", peer="2002",
                          content="recall FOREIGN_CHILD_MARKER")
    with _profile_runtime_scope(homes["alpha"]), bind_resolved_access_context(access):
        browse = json.loads(session_search(db=store, limit=10))
        discover = session_search(db=store, query="recall", limit=10)
        scroll = json.loads(session_search(db=store, session_id="own", around_message_id=foreign_anchor))
    # Native browse hides standalone compression/delegation children, even in the old live code.
    assert {r["session_id"] for r in browse["results"]} == {"own"}
    assert "FOREIGN" not in discover
    assert '"parent_session_id": "foreign"' not in discover
    assert scroll["success"] is False


@pytest.mark.parametrize("route,query,role", [
    ("fts", "recall", "user"), ("fts", "needle absent", "user"),
    ("fts", "召回", "user"), ("fts", "你", "user"),
    ("stale", "recall", "user"), ("fts", "recall", "tool"),
    ("gap", "recall", "user"),
])
def test_native_search_fallbacks_keep_sql_scope(store, homes, access, route, query, role):
    # Real SQLite indexes/canonical rows, including OR relaxation, substring/LIKE and rebuild gaps.
    for sid, marker in [("own", "OWN_MARKER"), ("foreign", "FOREIGN_MARKER")]:
        store.append_message(sid, role=role, content=f"recall needle 召回你好 {marker}")
    if route == "stale":
        from hermes_state_common import FTS_STALE_KEY
        store.set_meta(FTS_STALE_KEY, "1")
    elif route == "gap":
        with store._lock:
            store._conn.execute("DELETE FROM messages_fts")
            store._conn.execute("DELETE FROM messages_fts_trigram")
            store._conn.commit()
        store.set_meta("fts_rebuild_progress", "0")
        store.set_meta("fts_rebuild_high_water", str(store.get_messages("foreign")[-1]["id"]))
    with _profile_runtime_scope(homes["alpha"]), bind_resolved_access_context(access):
        output = session_search(db=store, query=query, role_filter=role, limit=1, sort="newest")
    result = json.loads(output)
    assert result["success"] is True
    assert [r["session_id"] for r in result["results"]] == ["own"]
    assert "OWN_MARKER" in output and "FOREIGN_MARKER" not in output


def test_same_profile_link_and_owner_policy_are_preserved(store, homes, access):
    owner = replace(access, role_id="owner", capabilities=frozenset())
    with _profile_runtime_scope(homes["alpha"]), bind_resolved_access_context(owner):
        own = json.loads(session_search(db=store, session_id="alpha/own"))
        foreign = json.loads(session_search(db=store, session_id="foreign"))
    assert own["success"] is True
    assert own["messages"][0]["content"] == "recall OWN_MARKER"
    assert foreign["success"] is False


def test_numbered_foreign_title_cannot_shadow_own_title(store, homes, access):
    store.set_session_title("own", "Topic")
    store.set_session_title("foreign", "Topic #2")
    with _profile_runtime_scope(homes["alpha"]), bind_resolved_access_context(access):
        output = session_search(db=store, query="Topic")
    result = json.loads(output)
    assert [r["session_id"] for r in result["results"]] == ["own"]
    assert result["results"][0]["title"] == "Topic"
    assert "FOREIGN_MARKER" not in output


def test_gateway_origin_producer_and_default_db_acquire_preserve_scope(homes, access, monkeypatch):
    import hermes_state
    from gateway.config import GatewayConfig
    from gateway.session import Platform, SessionSource, SessionStore
    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", hermes_state._IMPORT_DEFAULT_DB_PATH)
    with _profile_runtime_scope(homes["alpha"]):
        gateway = SessionStore(homes["alpha"] / "sessions", GatewayConfig(multiplex_profiles=True))
        try:
            for peer, marker in [("1001", "OWN_MARKER"), ("2002", "FOREIGN_MARKER")]:
                authority = replace(access, delivery_target=DeliveryTarget("telegram", "fixture-bot", "dm", peer))
                source = SessionSource(Platform.TELEGRAM, peer, user_id=peer, profile="alpha",
                                       route_account="fixture-bot", resolved_access_context=authority)
                with bind_resolved_access_context(authority):
                    entry = gateway.get_or_create_session(source)
                    gateway.append_to_transcript(entry.session_id, {"role": "user", "content": "recall " + marker})
            with bind_resolved_access_context(access):
                output = session_search(query="recall")  # native registry acquires the actual profile store
            result = json.loads(output)
            assert result["success"] is True
            assert len(result["results"]) == 1
            assert "OWN_MARKER" in output and "FOREIGN_MARKER" not in output
        finally:
            gateway.close_all_db_handles()


def test_untyped_legacy_cross_profile_recall_is_preserved(homes):
    with SessionDB(homes["beta"] / "state.db") as other:
        seed(other, "legacy", profile="beta", content="LEGACY_MARKER")
    previous = is_multiplex_active()
    set_multiplex_active(False)
    try:
        with _profile_runtime_scope(homes["alpha"]), bind_resolved_access_context(None):
            result = json.loads(session_search(session_id="beta/legacy"))
            assert result["success"] is True
            assert result["messages"][0]["content"] == "LEGACY_MARKER"
    finally:
        set_multiplex_active(previous)
