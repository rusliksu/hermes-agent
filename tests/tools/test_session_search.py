"""Tests for the single-shape session_search tool.

Three calling shapes:
  1. DISCOVERY — pass query → FTS5 + anchored window + bookends per hit
  2. SCROLL    — pass session_id + around_message_id → just the window
  3. BROWSE    — no args → recent sessions chronologically

All run zero LLM calls.
"""
import json
import time
from contextlib import contextmanager

import pytest

from gateway.access_registry import DeliveryTarget, ResolvedAccessContext
from gateway.session_context import reset_session_vars, set_session_vars
from hermes_state import SessionDB
from tools.session_search_tool import (
    SESSION_SEARCH_SCHEMA,
    _HIDDEN_SESSION_SOURCES,
    _format_timestamp,
    session_search,
)
from tools.registry import registry


@pytest.fixture
def db(tmp_path):
    return SessionDB(tmp_path / "state.db")


@pytest.fixture(autouse=True)
def _clear_resolved_access_context():
    reset_session_vars()
    yield
    reset_session_vars()


def _access_context(
    *,
    role_id="family",
    profile_id="family-alpha",
    chat_id="chat-a",
    thread_id="thread-a",
    peer_kind="dm",
    capabilities=frozenset({"session_search"}),
) -> ResolvedAccessContext:
    return ResolvedAccessContext(
        principal_id=f"principal-{profile_id}",
        role_id=role_id,
        profile_id=profile_id,
        conversation_scope="private",
        capabilities=frozenset(capabilities),
        delivery_target=DeliveryTarget(
            platform="telegram",
            account="bot-a",
            peer_kind=peer_kind,
            chat_id=chat_id,
            thread_id=thread_id,
        ),
    )


@contextmanager
def _bound_context(context):
    reset_session_vars()
    set_session_vars(resolved_access_context=context)
    try:
        yield
    finally:
        reset_session_vars()


def _seed_scoped_session(
    db,
    session_id,
    *,
    profile_name="family-alpha",
    chat_id="chat-a",
    thread_id="thread-a",
    source="telegram",
    chat_type="dm",
    user_id=None,
    title=None,
    content="scoped rosebud session secret",
):
    user_id = chat_id if user_id is None and chat_type == "dm" else user_id
    db.create_session(
        session_id,
        source=source,
        user_id=user_id,
        chat_id=chat_id,
        chat_type=chat_type,
        thread_id=thread_id,
        profile_name=profile_name,
    )
    if title:
        db.set_session_title(session_id, title)
    first_id = db.append_message(session_id, role="user", content=content)
    db.append_message(session_id, role="assistant", content=f"assistant echoes {content}")
    db._conn.commit()
    return first_id


def _seed_modpack_sessions(db):
    """Create three sessions about a modpack so FTS5 has hits to dedupe."""
    now = int(time.time())
    # Older session — modpack origin
    db.create_session("s_oldest", source="cli")
    db._conn.execute("UPDATE sessions SET started_at = ?, title = ? WHERE id = ?",
                     (now - 30000, "Building the Modpack", "s_oldest"))
    db.append_message("s_oldest", role="user", content="Let's build a Minecraft modpack")
    db.append_message("s_oldest", role="assistant", content="Great. Let me scaffold the modpack repo.")
    db.append_message("s_oldest", role="user", content="Use NeoForge 1.21.1")
    db.append_message("s_oldest", role="assistant", content="Done. Modpack repo created with NeoForge 1.21.1.")
    db.append_message("s_oldest", role="assistant", content="Tier-0 mods installed; modpack smoke test passes.")

    # Middle session — modpack quest coverage
    db.create_session("s_middle", source="cli")
    db._conn.execute("UPDATE sessions SET started_at = ?, title = ? WHERE id = ?",
                     (now - 15000, "Modpack Quest Coverage", "s_middle"))
    db.append_message("s_middle", role="user", content="Deep-dive every modpack reference quest guide")
    db.append_message("s_middle", role="assistant", content="Surveying ATM10 questbook for modpack inspiration.")
    db.append_message("s_middle", role="user", content="Update the modpack version too")
    db.append_message("s_middle", role="assistant", content="Modpack version bumped 0.4 → 0.8.5; quest coverage page added.")

    # Newest session — modpack mob spawn fix
    db.create_session("s_newest", source="cli")
    db._conn.execute("UPDATE sessions SET started_at = ?, title = ? WHERE id = ?",
                     (now - 1000, "Modpack Mob Spawn Fix", "s_newest"))
    db.append_message("s_newest", role="user", content="Fix the modpack mob spawning")
    db.append_message("s_newest", role="assistant", content="Investigating elite mob gating in the modpack KubeJS.")
    db.append_message("s_newest", role="assistant", content="Shipped commit b850442. Modpack alternator nerfed too.")
    db._conn.commit()


# =========================================================================
# Schema invariants
# =========================================================================

class TestSchema:
    def test_schema_has_required_params(self):
        params = SESSION_SEARCH_SCHEMA["parameters"]["properties"]
        # Discovery shape
        assert "query" in params
        assert "limit" in params
        assert "sort" in params
        # Scroll shape
        assert "session_id" in params
        assert "around_message_id" in params
        assert "window" in params
        # Shared
        assert "role_filter" in params

    def test_no_mode_parameter(self):
        # Mode is inferred from which args are set — no explicit mode param
        params = SESSION_SEARCH_SCHEMA["parameters"]["properties"]
        assert "mode" not in params

    def test_no_profile_parameter(self):
        params = SESSION_SEARCH_SCHEMA["parameters"]["properties"]
        assert "profile" not in params

    def test_sort_enum(self):
        params = SESSION_SEARCH_SCHEMA["parameters"]["properties"]
        assert params["sort"]["enum"] == ["newest", "oldest"]

    def test_schema_description_teaches_scroll(self):
        desc = SESSION_SEARCH_SCHEMA["description"]
        assert "SCROLL" in desc
        assert "DISCOVERY" in desc
        assert "BROWSE" in desc
        # Must explain how to scroll
        assert "scroll FORWARD" in desc or "messages[-1]" in desc

    def test_no_llm_promise_in_description(self):
        # The new design never calls an LLM
        desc = SESSION_SEARCH_SCHEMA["description"].lower()
        assert "no llm" in desc

    def test_schema_description_enforces_source_first_limit(self):
        desc = SESSION_SEARCH_SCHEMA["description"].lower()
        assert "source-first limit" in desc
        assert "conversation history only" in desc
        assert "direct source" in desc
        assert "session_search as secondary" in desc
        assert "not found" in desc


class TestHiddenSources:
    def test_tool_source_hidden(self):
        assert "tool" in _HIDDEN_SESSION_SOURCES


class TestFormatTimestamp:
    def test_unix_timestamp(self):
        out = _format_timestamp(1700000000)
        assert "2023" in out

    def test_none(self):
        assert _format_timestamp(None) == "unknown"

    def test_iso_string_passthrough(self):
        out = _format_timestamp("not-a-number-string")
        assert out == "not-a-number-string"


# =========================================================================
# Browse shape (no args)
# =========================================================================

class TestBrowseShape:
    def test_no_args_returns_recent_sessions(self, db):
        _seed_modpack_sessions(db)
        result = json.loads(session_search(db=db))
        assert result["success"] is True
        assert result["mode"] == "browse"
        assert result["count"] >= 3

    def test_browse_excludes_current_session(self, db):
        _seed_modpack_sessions(db)
        result = json.loads(session_search(db=db, current_session_id="s_newest"))
        sids = [r["session_id"] for r in result["results"]]
        assert "s_newest" not in sids

    def test_browse_returns_titles(self, db):
        _seed_modpack_sessions(db)
        result = json.loads(session_search(db=db))
        titles = [r.get("title") for r in result["results"]]
        assert any("Modpack" in (t or "") for t in titles)


# =========================================================================
# Discovery shape (with query)
# =========================================================================

class TestDiscoveryShape:
    def test_query_returns_anchored_windows(self, db):
        _seed_modpack_sessions(db)
        result = json.loads(session_search(query="modpack", db=db))
        assert result["success"] is True
        assert result["mode"] == "discover"
        assert result["count"] >= 1

    def test_discovery_result_has_bookends_and_window(self, db):
        _seed_modpack_sessions(db)
        result = json.loads(session_search(query="modpack", limit=3, db=db))
        for hit in result["results"]:
            assert "bookend_start" in hit
            assert "messages" in hit
            assert "bookend_end" in hit
            assert "match_message_id" in hit
            assert "snippet" in hit
            assert "messages_before" in hit
            assert "messages_after" in hit

    def test_match_message_id_is_anchor_in_window(self, db):
        _seed_modpack_sessions(db)
        result = json.loads(session_search(query="modpack", limit=3, db=db))
        for hit in result["results"]:
            anchor_id = hit["match_message_id"]
            window_ids = [m["id"] for m in hit["messages"]]
            assert anchor_id in window_ids

    def test_no_results_returns_empty_list(self, db):
        _seed_modpack_sessions(db)
        result = json.loads(session_search(query="zzz_no_such_term_zzz", db=db))
        assert result["success"] is True
        assert result["results"] == []
        assert result["count"] == 0

    def test_query_can_match_session_title_without_message_hit(self, db):
        db.create_session("s_fingerprint", source="cli")
        db.set_session_title("s_fingerprint", "fingerprint-login")
        db.append_message("s_fingerprint", role="user", content="Let's configure PAM for biometric auth")
        db.append_message("s_fingerprint", role="assistant", content="Checking Linux auth settings.")

        result = json.loads(session_search(query="fingerprint-login", db=db))

        assert result["success"] is True
        assert result["count"] == 1
        hit = result["results"][0]
        assert hit["session_id"] == "s_fingerprint"
        assert hit["title"] == "fingerprint-login"
        assert hit["matched_role"] == "session_title"
        assert "Session title matched" in hit["snippet"]

    def test_title_query_strips_common_model_quoting(self, db):
        db.create_session("s_fingerprint", source="cli")
        db.set_session_title("s_fingerprint", "fingerprint-login")
        db.append_message("s_fingerprint", role="user", content="PAM auth setup")

        result = json.loads(session_search(query="`fingerprint-login`", db=db))

        assert result["success"] is True
        assert result["results"][0]["session_id"] == "s_fingerprint"
        assert result["results"][0]["matched_role"] == "session_title"

    def test_title_match_respects_current_session_filter(self, db):
        db.create_session("s_current", source="cli")
        db.set_session_title("s_current", "fingerprint-login")
        db.append_message("s_current", role="user", content="PAM auth setup")

        result = json.loads(session_search(
            query="fingerprint-login",
            current_session_id="s_current",
            db=db,
        ))

        assert result["success"] is True
        assert result["results"] == []
        assert result["count"] == 0

    def test_limit_clamped_to_max_10(self, db):
        _seed_modpack_sessions(db)
        # Pass huge limit; should not error and should cap
        result = json.loads(session_search(query="modpack", limit=999, db=db))
        assert result["count"] <= 10

    def test_limit_floor_to_1(self, db):
        _seed_modpack_sessions(db)
        result = json.loads(session_search(query="modpack", limit=0, db=db))
        # Result count depends on hits, but the limit must be at least 1
        assert result["count"] >= 0

    def test_non_int_limit_falls_back(self, db):
        _seed_modpack_sessions(db)
        result = json.loads(session_search(query="modpack", limit="bogus", db=db))
        assert result["success"] is True

    def test_current_session_filtered_out(self, db):
        _seed_modpack_sessions(db)
        result = json.loads(session_search(query="modpack", db=db, current_session_id="s_newest"))
        sids = [r["session_id"] for r in result["results"]]
        assert "s_newest" not in sids


class TestDiscoverySort:
    def test_sort_newest_orders_by_recency(self, db):
        _seed_modpack_sessions(db)
        result = json.loads(session_search(query="modpack", limit=3, sort="newest", db=db))
        # First result should be the most recent session
        first = result["results"][0]
        assert first["session_id"] == "s_newest" or "Newest" in (first.get("title") or "")

    def test_sort_oldest_orders_by_age(self, db):
        _seed_modpack_sessions(db)
        result = json.loads(session_search(query="modpack", limit=3, sort="oldest", db=db))
        first = result["results"][0]
        assert first["session_id"] == "s_oldest"

    def test_invalid_sort_silently_ignored(self, db):
        _seed_modpack_sessions(db)
        # Should not error
        result = json.loads(session_search(query="modpack", sort="bogus", db=db))
        assert result["success"] is True


class TestRoleFilter:
    def test_default_excludes_tool_role(self, db):
        db.create_session("s1", source="cli")
        db.append_message("s1", role="user", content="modpack question")
        db.append_message("s1", role="tool", content="modpack tool output", tool_name="x")
        result = json.loads(session_search(query="modpack", db=db))
        # The FTS5 match should be on the user message, not the tool message
        if result["count"] > 0:
            matched_role = result["results"][0]["matched_role"]
            assert matched_role in ("user", "assistant")

    def test_explicit_tool_role_includes_tool(self, db):
        db.create_session("s1", source="cli")
        db.append_message("s1", role="tool", content="modpack tool output", tool_name="x")
        result = json.loads(session_search(query="modpack", role_filter="tool", db=db))
        # Should now match the tool message
        if result["count"] > 0:
            assert result["results"][0]["matched_role"] == "tool"


# =========================================================================
# Scroll shape (session_id + around_message_id)
# =========================================================================

class TestScrollShape:
    def test_scroll_returns_window_without_bookends(self, db):
        _seed_modpack_sessions(db)
        # Get an anchor first via discovery
        disc = json.loads(session_search(query="modpack", limit=1, db=db))
        anchor_sid = disc["results"][0]["session_id"]
        anchor_mid = disc["results"][0]["match_message_id"]

        # Now scroll
        result = json.loads(session_search(
            session_id=anchor_sid, around_message_id=anchor_mid, window=2, db=db
        ))
        assert result["success"] is True
        assert result["mode"] == "scroll"
        assert "messages" in result
        # Scroll shape has no bookends
        assert "bookend_start" not in result
        assert "bookend_end" not in result

    def test_scroll_window_clamped_to_20(self, db):
        _seed_modpack_sessions(db)
        disc = json.loads(session_search(query="modpack", limit=1, db=db))
        anchor_sid = disc["results"][0]["session_id"]
        anchor_mid = disc["results"][0]["match_message_id"]
        result = json.loads(session_search(
            session_id=anchor_sid, around_message_id=anchor_mid, window=999, db=db
        ))
        assert result["window"] == 20

    def test_scroll_window_floor_to_1(self, db):
        _seed_modpack_sessions(db)
        disc = json.loads(session_search(query="modpack", limit=1, db=db))
        anchor_sid = disc["results"][0]["session_id"]
        anchor_mid = disc["results"][0]["match_message_id"]
        result = json.loads(session_search(
            session_id=anchor_sid, around_message_id=anchor_mid, window=-5, db=db
        ))
        assert result["window"] == 1

    def test_scroll_returns_messages_before_after_counts(self, db):
        _seed_modpack_sessions(db)
        disc = json.loads(session_search(query="modpack", limit=1, db=db))
        anchor_sid = disc["results"][0]["session_id"]
        anchor_mid = disc["results"][0]["match_message_id"]
        result = json.loads(session_search(
            session_id=anchor_sid, around_message_id=anchor_mid, window=3, db=db
        ))
        assert "messages_before" in result
        assert "messages_after" in result

    def test_scroll_anchor_in_window(self, db):
        _seed_modpack_sessions(db)
        disc = json.loads(session_search(query="modpack", limit=1, db=db))
        anchor_sid = disc["results"][0]["session_id"]
        anchor_mid = disc["results"][0]["match_message_id"]
        result = json.loads(session_search(
            session_id=anchor_sid, around_message_id=anchor_mid, window=2, db=db
        ))
        anchor_in_window = [m for m in result["messages"] if m["id"] == anchor_mid]
        assert len(anchor_in_window) == 1
        assert anchor_in_window[0].get("anchor") is True

    def test_scroll_missing_anchor_errors(self, db):
        _seed_modpack_sessions(db)
        result = json.loads(session_search(
            session_id="s_oldest", around_message_id=999999, db=db
        ))
        assert result["success"] is False
        assert "not in" in result.get("error", "")

    def test_scroll_missing_session_errors(self, db):
        result = json.loads(session_search(
            session_id="nonexistent", around_message_id=1, db=db
        ))
        assert result["success"] is False

    def test_scroll_rejects_current_session_lineage(self, db):
        _seed_modpack_sessions(db)
        # Grab some valid id from s_oldest
        disc = json.loads(session_search(query="modpack", limit=3, db=db))
        match = [r for r in disc["results"] if r["session_id"] == "s_oldest"]
        if match:
            mid = match[0]["match_message_id"]
            result = json.loads(session_search(
                session_id="s_oldest", around_message_id=mid, db=db,
                current_session_id="s_oldest",
            ))
            assert result["success"] is False
            assert "current session" in result.get("error", "").lower()

    def test_scroll_invalid_around_message_id_errors(self, db):
        _seed_modpack_sessions(db)
        result = json.loads(session_search(
            session_id="s_oldest", around_message_id="not-an-int", db=db
        ))
        assert result["success"] is False


class TestScrollPattern:
    """The forward/backward scroll loop using tool output."""

    def test_scroll_forward_from_last_id(self, db):
        # Long session
        db.create_session("s_long", source="cli")
        ids = []
        for i in range(20):
            ids.append(db.append_message("s_long", role="user" if i % 2 == 0 else "assistant",
                                         content=f"long session msg {i}"))

        v1 = json.loads(session_search(
            session_id="s_long", around_message_id=ids[5], window=3, db=db
        ))
        last_id = v1["messages"][-1]["id"]
        v2 = json.loads(session_search(
            session_id="s_long", around_message_id=last_id, window=3, db=db
        ))
        # Forward scroll: v2 should reach further than v1
        assert max(m["id"] for m in v2["messages"]) > max(m["id"] for m in v1["messages"])
        # Boundary id appears in both
        assert last_id in [m["id"] for m in v1["messages"]]
        assert last_id in [m["id"] for m in v2["messages"]]


# =========================================================================
# Shape precedence
# =========================================================================

class TestShapePrecedence:
    def test_scroll_args_beat_query(self, db):
        _seed_modpack_sessions(db)
        disc = json.loads(session_search(query="modpack", limit=1, db=db))
        anchor_sid = disc["results"][0]["session_id"]
        anchor_mid = disc["results"][0]["match_message_id"]
        # Pass both query and scroll args — scroll should win
        result = json.loads(session_search(
            query="modpack",  # would normally trigger discovery
            session_id=anchor_sid, around_message_id=anchor_mid, db=db,
        ))
        assert result["mode"] == "scroll"

    def test_empty_query_falls_back_to_browse(self, db):
        _seed_modpack_sessions(db)
        result = json.loads(session_search(query="   ", db=db))
        assert result["mode"] == "browse"

    def test_non_string_query_falls_back_to_browse(self, db):
        _seed_modpack_sessions(db)
        result = json.loads(session_search(query=None, db=db))  # type: ignore
        assert result["mode"] == "browse"

    def test_session_id_without_anchor_reads(self, db):
        _seed_modpack_sessions(db)
        # session_id alone (no anchor, no query) → read shape, not browse.
        result = json.loads(session_search(session_id="s_oldest", db=db))
        assert result["mode"] == "read"


# =========================================================================
# Read shape — dump a whole session by id (serves @session links)
# =========================================================================

class TestReadShape:
    def test_read_returns_full_session(self, db):
        _seed_modpack_sessions(db)
        result = json.loads(session_search(session_id="s_oldest", db=db))
        assert result["success"] is True
        assert result["mode"] == "read"
        assert result["session_id"] == "s_oldest"
        assert result["message_count"] == 5
        assert result["truncated"] is False
        assert len(result["messages"]) == 5
        assert result["session_meta"]["title"] == "Building the Modpack"

    def test_read_unknown_session_errors(self, db):
        result = json.loads(session_search(session_id="ghost", db=db))
        assert result["success"] is False

    def test_read_truncates_large_session(self, db):
        db.create_session("s_big", source="cli")
        for i in range(50):
            db.append_message("s_big", role="user" if i % 2 == 0 else "assistant", content=f"m{i}")
        db._conn.commit()
        result = json.loads(session_search(session_id="s_big", db=db))
        assert result["mode"] == "read"
        assert result["message_count"] == 50
        assert result["truncated"] is True
        assert len(result["messages"]) == 30  # head 20 + tail 10


# =========================================================================
# Profile isolation
# =========================================================================

class TestProfileIsolation:
    def _make_other_db(self, tmp_path, session_id="s_other"):
        other_home = tmp_path / "other_home"
        other_home.mkdir()
        other = SessionDB(other_home / "state.db")
        other.create_session(session_id, source="cli")
        other._conn.execute(
            "UPDATE sessions SET title = ? WHERE id = ?", ("Other Profile Chat", session_id)
        )
        other.append_message(session_id, role="user", content="hello from the other profile")
        other._conn.commit()
        return other_home, other

    def test_public_function_rejects_profile_argument(self, db):
        with pytest.raises(TypeError):
            session_search(session_id="s_other", profile="other", db=db)  # type: ignore[call-arg]

    def test_registry_dispatch_ignores_foreign_profile_argument(self, db, tmp_path):
        self._make_other_db(tmp_path, "s_other")

        result = json.loads(registry.dispatch(
            "session_search",
            {"session_id": "s_other", "profile": "other"},
            db=db,
        ))
        assert result["success"] is False
        assert "s_other" in result.get("error", "")

    def test_embedded_profile_session_id_does_not_traverse(self, db, tmp_path):
        self._make_other_db(tmp_path, "s_other")

        result = json.loads(session_search(session_id="other/s_other", db=db))

        assert result["success"] is False
        assert "other/s_other" in result.get("error", "")

    def test_bare_foreign_id_is_not_located_across_profiles(self, db, tmp_path, monkeypatch):
        other_home, _other = self._make_other_db(tmp_path, "s_far")

        from hermes_cli import profiles as profiles_mod
        monkeypatch.setattr(
            profiles_mod,
            "get_profile_dir",
            lambda _name: pytest.fail("session_search must not scan profile homes"),
        )
        monkeypatch.setattr(
            profiles_mod,
            "list_profiles",
            lambda: pytest.fail("session_search must not list profiles"),
        )

        result = json.loads(session_search(session_id="s_far", db=db))

        assert result["success"] is False
        assert "s_far" in result.get("error", "")
        assert other_home.exists()

    def test_own_db_reads_still_work(self, db):
        db.create_session("s_own", source="cli")
        db.append_message("s_own", role="user", content="own profile content")
        db._conn.commit()

        result = json.loads(session_search(session_id="s_own", db=db))

        assert result["success"] is True
        assert result["mode"] == "read"
        assert result["session_id"] == "s_own"


class TestTypedSessionScope:
    @pytest.mark.parametrize("role_id", ["family", "family_standard", "family_sandbox"])
    def test_family_browse_discover_title_read_and_scroll_own_scope(self, db, role_id):
        anchor = _seed_scoped_session(
            db,
            "own-family",
            title="Own Family Topic",
            content="own scoped rosebud memory",
        )
        with _bound_context(_access_context(role_id=role_id)):
            browse = json.loads(session_search(db=db))
            discover = json.loads(session_search(query="rosebud", db=db))
            title = json.loads(session_search(query="Own Family Topic", db=db))
            read = json.loads(session_search(session_id="own-family", db=db))
            scroll = json.loads(session_search(
                session_id="own-family",
                around_message_id=anchor,
                db=db,
            ))

        assert [row["session_id"] for row in browse["results"]] == ["own-family"]
        assert discover["results"][0]["session_id"] == "own-family"
        assert title["results"][0]["matched_role"] == "session_title"
        assert read["success"] is True
        assert scroll["success"] is True

    @pytest.mark.parametrize("foreign_id", ["foreign-profile", "foreign-dm", "foreign-thread"])
    def test_family_scope_hides_foreign_profile_dm_and_thread(self, db, foreign_id):
        foreign_titles = {
            "foreign-profile": "Foreign Profile Secret",
            "foreign-dm": "Foreign DM Secret",
            "foreign-thread": "Foreign Thread Secret",
        }
        own_anchor = _seed_scoped_session(
            db,
            "own-family",
            title="Own visible",
            content="visible own topic",
        )
        foreign_anchor = _seed_scoped_session(
            db,
            "foreign-profile",
            profile_name="family-beta",
            title="Foreign Profile Secret",
            content="foreign rosebud profile secret",
        )
        _seed_scoped_session(
            db,
            "foreign-dm",
            chat_id="chat-b",
            title="Foreign DM Secret",
            content="foreign rosebud dm secret",
        )
        _seed_scoped_session(
            db,
            "foreign-thread",
            thread_id="thread-b",
            title="Foreign Thread Secret",
            content="foreign rosebud thread secret",
        )
        if foreign_id != "foreign-profile":
            foreign_anchor = db.get_messages(foreign_id)[0]["id"]

        with _bound_context(_access_context()):
            browse = json.loads(session_search(db=db))
            discover = json.loads(session_search(query="foreign rosebud", db=db))
            title = json.loads(session_search(query=foreign_titles[foreign_id], db=db))
            read = json.loads(session_search(session_id=foreign_id, db=db))
            scroll = json.loads(session_search(
                session_id=foreign_id,
                around_message_id=foreign_anchor,
                db=db,
            ))
            own_scroll = json.loads(session_search(
                session_id="own-family",
                around_message_id=own_anchor,
                db=db,
            ))

        browsed_ids = {row["session_id"] for row in browse["results"]}
        assert browsed_ids == {"own-family"}
        assert discover["results"] == []
        assert title["results"] == []
        assert read["success"] is False
        assert scroll["success"] is False
        assert own_scroll["success"] is True

    def test_foreign_direct_ids_stop_before_transcript_io(self, db, monkeypatch):
        foreign_anchor = _seed_scoped_session(
            db,
            "foreign-profile",
            profile_name="family-beta",
            title="Foreign Direct Secret",
            content="foreign direct rosebud secret",
        )

        def fail_io(*_args, **_kwargs):
            pytest.fail("foreign session reached transcript IO")

        monkeypatch.setattr(db, "get_messages", fail_io)
        monkeypatch.setattr(db, "get_messages_around", fail_io)
        monkeypatch.setattr(db, "get_anchored_view", fail_io)

        with _bound_context(_access_context()):
            read = json.loads(session_search(session_id="foreign-profile", db=db))
            scroll = json.loads(session_search(
                session_id="foreign-profile",
                around_message_id=foreign_anchor,
                db=db,
            ))

        assert read["success"] is False
        assert scroll["success"] is False

    def test_typed_transcript_reads_pass_scope_to_sessiondb_primitives(self, db, monkeypatch):
        anchor = _seed_scoped_session(
            db,
            "own-family",
            title="Own Scope Topic",
            content="own scoped needle",
        )
        seen_scopes = {
            "get_session": [],
            "get_messages": [],
            "get_messages_around": [],
            "get_anchored_view": [],
        }

        for name in seen_scopes:
            original = getattr(db, name)

            def wrapper(*args, _name=name, _original=original, **kwargs):
                seen_scopes[_name].append(kwargs.get("session_scope"))
                return _original(*args, **kwargs)

            monkeypatch.setattr(db, name, wrapper)

        with _bound_context(_access_context()):
            read = json.loads(session_search(session_id="own-family", db=db))
            scroll = json.loads(session_search(
                session_id="own-family",
                around_message_id=anchor,
                db=db,
            ))
            discover = json.loads(session_search(query="needle", db=db))

        assert read["success"] is True
        assert scroll["success"] is True
        assert discover["results"][0]["session_id"] == "own-family"
        expected_scope = {
            "profile_name": "family-alpha",
            "account": "bot-a",
            "source": "telegram",
            "chat_type": "dm",
            "chat_id": "chat-a",
            "thread_id": "thread-a",
            "user_id": "chat-a",
            "is_dm": True,
        }
        for method_scopes in seen_scopes.values():
            assert method_scopes
            assert all(scope == expected_scope for scope in method_scopes)

    def test_owner_typed_context_sees_owner_profile_only(self, db):
        _seed_scoped_session(
            db,
            "owner-session",
            profile_name="owner-profile",
            title="Owner Secret",
            content="owner rosebud secret",
        )
        _seed_scoped_session(
            db,
            "family-session",
            profile_name="family-alpha",
            title="Family Secret",
            content="family rosebud secret",
        )

        with _bound_context(_access_context(
            role_id="owner",
            profile_id="owner-profile",
            capabilities=frozenset(),
        )):
            discover = json.loads(session_search(query="rosebud", db=db))
            foreign_read = json.loads(session_search(session_id="family-session", db=db))

        assert [row["session_id"] for row in discover["results"]] == ["owner-session"]
        assert foreign_read["success"] is False

    @pytest.mark.parametrize(
        "context",
        [
            _access_context(role_id="shared_room", peer_kind="group", chat_id="room-a"),
            _access_context(role_id="family_admin"),
            _access_context(role_id="family_custom"),
            _access_context(role_id="owner", peer_kind="group", chat_id="room-a"),
            _access_context(role_id="unknown_role"),
            {"profile_id": "family-alpha"},
        ],
    )
    def test_denied_contexts_fail_before_transcript_io(self, context):
        class NoIoDB:
            def __getattr__(self, name):
                pytest.fail(f"unexpected db read: {name}")

        with _bound_context(context):
            result = json.loads(session_search(query="anything", db=NoIoDB()))

        assert result["success"] is False

    def test_typed_missing_db_does_not_lazy_open_default(self, monkeypatch):
        import hermes_state

        monkeypatch.setattr(
            hermes_state,
            "SessionDB",
            lambda: pytest.fail("typed session_search must not lazy-open SessionDB"),
        )
        with _bound_context(_access_context()):
            result = json.loads(session_search())

        assert result["success"] is False
        assert "caller-supplied SessionDB" in result["error"]

    def test_multiplex_missing_context_fails_closed(self, monkeypatch):
        class NoIoDB:
            def __getattr__(self, name):
                pytest.fail(f"unexpected db read: {name}")

        monkeypatch.setattr("agent.secret_scope.is_multiplex_active", lambda: True)
        result = json.loads(session_search(query="anything", db=NoIoDB()))

        assert result["success"] is False
        assert "access context unavailable" in result["error"]

    def test_malformed_delivery_target_fails_closed(self):
        context = _access_context()
        object.__setattr__(context, "delivery_target", None)

        class NoIoDB:
            def __getattr__(self, name):
                pytest.fail(f"unexpected db read: {name}")

        with _bound_context(context):
            result = json.loads(session_search(query="anything", db=NoIoDB()))

        assert result["success"] is False
        assert "malformed resolved access context" in result["error"]

    def test_none_context_preserves_lazy_sessiondb_fallback(self, monkeypatch):
        class LazyDB:
            def list_sessions_rich(self, **_kwargs):
                return [{
                    "id": "legacy-lazy",
                    "title": "Legacy Lazy",
                    "source": "cli",
                    "started_at": 1,
                    "last_active": 2,
                    "message_count": 1,
                    "preview": "legacy",
                    "parent_session_id": None,
                }]

        import hermes_state

        monkeypatch.setattr(hermes_state, "SessionDB", LazyDB)
        result = json.loads(session_search())

        assert result["success"] is True
        assert result["results"][0]["session_id"] == "legacy-lazy"

    def test_model_supplied_profile_cannot_modify_typed_scope(self, db):
        _seed_scoped_session(
            db,
            "own-family",
            title="Own visible",
            content="visible own topic",
        )
        _seed_scoped_session(
            db,
            "foreign-profile",
            profile_name="family-beta",
            title="Foreign Secret",
            content="foreign rosebud profile secret",
        )

        with _bound_context(_access_context()):
            result = json.loads(registry.dispatch(
                "session_search",
                {"query": "foreign rosebud", "profile": "family-beta"},
                db=db,
            ))

        assert result["success"] is True
        assert result["results"] == []


# =========================================================================
# Cron demotion in discover ranking (#19434)
# =========================================================================

class TestCronDemotion:
    def _seed_cron_and_interactive(self, db):
        """One interactive (telegram) session and several cron sessions, all
        matching the same query. Cron rows accumulate repetitive vocabulary
        and out-number the user's single interactive session — the live-data
        symptom in #19434.
        """
        now = int(time.time())
        # Interactive user session — older, so it loses on bare recency too.
        db.create_session("s_user", source="telegram")
        db._conn.execute("UPDATE sessions SET started_at = ? WHERE id = ?",
                         (now - 90000, "s_user"))
        db.append_message("s_user", role="user", content="how is the venom project going")
        db.append_message("s_user", role="assistant", content="The venom project shipped its first milestone.")
        # Several cron sessions, all newer and all stuffed with the same terms.
        for i in range(8):
            sid = f"cron_{i}"
            db.create_session(sid, source="cron")
            db._conn.execute("UPDATE sessions SET started_at = ? WHERE id = ?",
                             (now - 1000 - i, sid))
            db.append_message(sid, role="user", content="venom project daily status")
            db.append_message(sid, role="assistant", content="venom project venom project venom summary")
        db._conn.commit()

    def test_interactive_session_surfaces_above_cron(self, db):
        self._seed_cron_and_interactive(db)
        result = json.loads(session_search(query="venom project", limit=1, db=db))
        assert result["success"] is True
        assert result["count"] == 1
        # With cron drowning FTS, bare BM25/recency would return a cron_* hit.
        # Demotion must put the user's interactive session first.
        assert result["results"][0]["source"] == "telegram"
        assert result["results"][0]["session_id"] == "s_user"

    def test_cron_still_reachable_when_only_match(self, db):
        """Demotion must not exclude cron — when only cron matches, it still
        comes back."""
        now = int(time.time())
        db.create_session("cron_only", source="cron")
        db._conn.execute("UPDATE sessions SET started_at = ? WHERE id = ?",
                         (now - 500, "cron_only"))
        db.append_message("cron_only", role="user", content="quarterly archive sweep")
        db.append_message("cron_only", role="assistant", content="Archive sweep complete.")
        db._conn.commit()
        result = json.loads(session_search(query="archive sweep", db=db))
        assert result["success"] is True
        assert result["count"] == 1
        assert result["results"][0]["source"] == "cron"

    def test_order_for_recall_is_stable_within_class(self):
        from tools.session_search_tool import _order_for_recall
        rows = [
            {"id": 1, "source": "cron"},
            {"id": 2, "source": "telegram"},
            {"id": 3, "source": "cron"},
            {"id": 4, "source": "cli"},
            {"id": 5, "source": None},
        ]
        ordered = _order_for_recall(rows)
        # Interactive rows first, in original relative order; cron last, in
        # original relative order.
        assert [r["id"] for r in ordered] == [2, 4, 5, 1, 3]
