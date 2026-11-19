"""Sessions categories must classify legacy cron IDs before SQL pagination/search."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cli.web_routers import sessions
from hermes_state import SessionDB


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "synthetic-state.db"
    db = SessionDB(path)
    for sid, source in (("human-cli", "cli"), ("human-telegram", "telegram"),
                        ("cron:telegram:legacy", "telegram"), ("job", "cron"),
                        ("worker", "delegate"), ("sidecar", "tool")):
        db.create_session(sid, source=source)
        db.append_message(sid, role="user", content="needle category fixture")
    db.close()
    monkeypatch.setattr(sessions, "_open_session_db_for_profile",
                        lambda profile, *, read_only: SessionDB(path, read_only=read_only))
    # 0.23 no longer auto-archives on the list endpoint; nothing to stub.
    monkeypatch.setattr(sessions, "_cron_default_profile", lambda: "default")
    app = FastAPI()
    for router in (sessions.list_router, sessions.search_router, sessions.manage_router):
        app.include_router(router)
    with TestClient(app) as api:
        yield api, path


@pytest.mark.parametrize("activity,expected,counts", [
    ("people", {"human-cli", "human-telegram"}, {"cli": 1, "telegram": 1}),
    ("automations", {"cron:telegram:legacy", "job", "worker", "sidecar"},
     {"telegram": 1, "cron": 1, "delegate": 1, "tool": 1}),
    ("all", {"human-cli", "human-telegram", "cron:telegram:legacy", "job", "worker", "sidecar"},
     {"cli": 1, "telegram": 2, "cron": 1, "delegate": 1, "tool": 1}),
])
def test_http_list_search_and_stats_share_activity_partition(client, activity, expected, counts):
    api, _path = client
    listing = api.get("/api/sessions", params={"activity": activity, "limit": 100}).json()
    assert {row["id"] for row in listing["sessions"]} == expected
    assert listing["total"] == len(expected)
    result = api.get("/api/sessions/search", params={"activity": activity, "q": "needle", "limit": 100}).json()
    assert {row["id"] for row in result["results"]} == expected
    stats = api.get("/api/sessions/stats", params={"activity": activity}).json()
    assert stats["total"] == stats["active_store"] == len(expected)
    assert stats["archived"] == 0
    assert stats["by_source"] == counts


def test_activity_is_applied_before_small_page_and_both_search_limits(client):
    api, path = client
    db = SessionDB(path)
    try:
        for i in range(60):
            sid = f"needle-job-{i:02}"
            db.create_session(sid, source="cron")
            db.append_message(sid, role="user", content="needle fixture")
        db.create_session("needle-person", source="telegram")
        db.append_message("needle-person", role="user", content="needle fixture")
        db._conn.execute("UPDATE sessions SET started_at=1,last_activity_at=1 WHERE id='needle-person'")
        db._conn.commit()
    finally:
        db.close()
    listing = api.get("/api/sessions", params={"activity": "people", "limit": 1, "order": "recent"}).json()
    assert len(listing["sessions"]) == 1
    assert listing["total"] == 3
    assert listing["sessions"][0]["id"] in {"human-cli", "human-telegram", "needle-person"}
    result = api.get("/api/sessions/search", params={"activity": "people", "q": "needle-person", "limit": 1}).json()
    assert [row["id"] for row in result["results"]] == ["needle-person"]
    db = SessionDB(path, read_only=True)
    try:
        assert [row["id"] for row in db.search_sessions_by_id("needle", activity="people", limit=1)] == ["needle-person"]
        assert {row["session_id"] for row in db.search_messages("needle", activity="people", limit=100)} == {"human-cli", "human-telegram", "needle-person"}
    finally:
        db.close()


def test_omitted_activity_preserves_all_and_invalid_activity_is_rejected(client):
    api, _path = client
    assert api.get("/api/sessions", params={"limit": 100}).json()["total"] == 6
    for endpoint in ("/api/sessions", "/api/sessions/search", "/api/sessions/stats"):
        assert api.get(endpoint, params={"activity": "unknown", "q": "needle"}).status_code == 400


@pytest.mark.parametrize("activity,source,active", [
    ("people", "telegram", 2), ("automations", "cron", 4), ("all", "telegram", 6),
])
def test_archived_stats_exclude_hidden_children_like_listing(client, activity, source, active):
    api, path = client
    db = SessionDB(path)
    try:
        db.create_session("archived-root", source=source)
        db.create_session("archived-child", source=source, parent_session_id="archived-root")
        db.set_session_archived("archived-root", True)
        db.set_session_archived("archived-child", True)
    finally:
        db.close()
    listing = api.get("/api/sessions", params={"activity": activity, "archived": "only"}).json()
    assert {row["id"] for row in listing["sessions"]} == {"archived-root"}
    assert listing["total"] == 1
    stats = api.get("/api/sessions/stats", params={"activity": activity}).json()
    assert stats["active_store"] == active
    assert stats["archived"] == listing["total"]
    assert stats["total"] == stats["active_store"] + stats["archived"]
