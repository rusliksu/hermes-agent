import json
from unittest.mock import MagicMock

import pytest

from gateway.access_registry import (
    DeliveryTarget,
    ResolvedAccessContext,
    deserialize_resolved_access_context,
    serialize_resolved_access_context,
)
from gateway.session_context import clear_session_vars, reset_session_vars, set_session_vars


def _ctx(
    principal_id: str,
    role_id: str = "family_standard",
    *,
    chat_id: str = "chat-1",
    thread_id: str | None = "thread-1",
    capabilities=frozenset({"self_reminder"}),
) -> ResolvedAccessContext:
    return ResolvedAccessContext(
        principal_id=principal_id,
        role_id=role_id,
        profile_id=f"profile-{principal_id}",
        conversation_scope="private" if role_id != "shared_room" else "room",
        capabilities=frozenset(capabilities),
        delivery_target=DeliveryTarget(
            platform="telegram",
            account="bot-main",
            peer_kind="dm" if role_id != "shared_room" else "group",
            chat_id=chat_id,
            thread_id=thread_id,
        ),
    )


@pytest.fixture(autouse=True)
def _cron_store(tmp_path, monkeypatch):
    cron_dir = tmp_path / "cron"
    monkeypatch.setattr("cron.jobs.CRON_DIR", cron_dir)
    monkeypatch.setattr("cron.jobs.JOBS_FILE", cron_dir / "jobs.json")
    monkeypatch.setattr("cron.jobs.OUTPUT_DIR", cron_dir / "output")
    reset_session_vars()
    yield
    reset_session_vars()


def _bind(context: ResolvedAccessContext):
    return set_session_vars(
        platform=context.delivery_target.platform,
        chat_id=context.delivery_target.chat_id,
        thread_id=context.delivery_target.thread_id or "",
        resolved_access_context=context,
    )


def test_family_create_persists_exact_six_field_context_and_list_filters():
    from cron.jobs import create_job, get_job, update_job
    from tools.cronjob_tools import cronjob

    family = _ctx("family-1")
    other = _ctx("family-2", chat_id="chat-2")
    legacy = create_job("legacy", "every 5m", name="legacy")
    foreign = create_job(
        "foreign",
        "every 5m",
        name="foreign",
        origin={"platform": "telegram", "chat_id": "chat-2", "thread_id": "thread-1"},
        resolved_access_context=serialize_resolved_access_context(other),
    )
    tokens = _bind(family)
    try:
        created = json.loads(cronjob(action="create", prompt="remind me", schedule="every 5m"))
        assert created["success"] is True
        created_id = created["job_id"]
        stored = get_job(created_id)
        encoded = serialize_resolved_access_context(family)
        assert stored["resolved_access_context"] == encoded
        with pytest.raises(ValueError):
            update_job(created_id, {"resolved_access_context": serialize_resolved_access_context(other)})
        assert get_job(created_id)["resolved_access_context"] == encoded
        assert set(stored["resolved_access_context"]) == {
            "principal_id",
            "role_id",
            "profile_id",
            "conversation_scope",
            "capabilities",
            "delivery_target",
        }
        assert deserialize_resolved_access_context(stored["resolved_access_context"]) == family
        assert "resolved_access_context" not in created["job"]

        listed = json.loads(cronjob(action="list", include_disabled=True))
        assert [job["job_id"] for job in listed["jobs"]] == [created["job_id"]]
        assert "resolved_access_context" not in listed["jobs"][0]
        assert legacy["id"] not in [job["job_id"] for job in listed["jobs"]]
        assert foreign["id"] not in [job["job_id"] for job in listed["jobs"]]
    finally:
        clear_session_vars(tokens)


def test_family_denies_explicit_target_before_create():
    from cron.jobs import list_jobs
    from tools.cronjob_tools import cronjob

    family = _ctx("family-1")
    tokens = _bind(family)
    try:
        before_count = len(list_jobs(include_disabled=True))
        denied = json.loads(
            cronjob(
                action="create",
                prompt="remind me",
                schedule="every 5m",
                deliver="telegram:chat-1:thread-1",
            )
        )
        assert denied["success"] is False
        assert "current conversation" in denied["error"]
        assert len(list_jobs(include_disabled=True)) == before_count
    finally:
        clear_session_vars(tokens)


def test_family_without_self_reminder_denied_before_create_side_effect():
    from cron.jobs import list_jobs
    from tools.cronjob_tools import cronjob

    family = _ctx("family-1", capabilities=frozenset({"public_web"}))
    tokens = _bind(family)
    try:
        before_count = len(list_jobs(include_disabled=True))
        denied = json.loads(cronjob(action="create", prompt="x", schedule="every 5m"))
        assert denied["success"] is False
        assert "self_reminder" in denied["error"]
        assert len(list_jobs(include_disabled=True)) == before_count
    finally:
        clear_session_vars(tokens)


def test_family_denies_foreign_delivery_and_run_before_side_effects(monkeypatch):
    from cron.jobs import create_job, get_job, list_jobs
    from tools.cronjob_tools import cronjob

    family = _ctx("family-1")
    other = _ctx("family-2", chat_id="chat-2")
    owned = create_job(
        "owned",
        "every 5m",
        name="owned",
        origin={"platform": "telegram", "chat_id": "chat-1", "thread_id": "thread-1"},
        resolved_access_context=serialize_resolved_access_context(family),
    )
    foreign = create_job(
        "foreign",
        "every 5m",
        name="foreign",
        origin={"platform": "telegram", "chat_id": "chat-2", "thread_id": "thread-1"},
        resolved_access_context=serialize_resolved_access_context(other),
    )

    ran = []
    monkeypatch.setattr("tools.cronjob_tools._execute_job_now", lambda job: ran.append(job["id"]))
    tokens = _bind(family)
    try:
        before_count = len(list_jobs(include_disabled=True))
        denied_create = json.loads(
            cronjob(action="create", prompt="x", schedule="every 5m", deliver="local")
        )
        assert denied_create["success"] is False
        assert len(list_jobs(include_disabled=True)) == before_count

        for denied_deliver in ("all", "", []):
            denied_update = json.loads(
                cronjob(
                    action="update",
                    job_id=owned["id"],
                    deliver=denied_deliver,
                    name="mutated",
                )
            )
            assert denied_update["success"] is False
            stored_owned = get_job(owned["id"])
            assert stored_owned["deliver"] == "origin"
            assert stored_owned["name"] == "owned"

        denied_run = json.loads(cronjob(action="run", job_id=foreign["id"]))
        assert denied_run["success"] is False
        assert ran == []
    finally:
        clear_session_vars(tokens)


def test_shared_room_denied_and_owner_keeps_legacy_behavior_with_snapshot():
    from cron.jobs import get_job, list_jobs
    from tools.cronjob_tools import cronjob

    shared = _ctx("room-1", "shared_room", chat_id="room-1", capabilities=frozenset({"public_web"}))
    tokens = _bind(shared)
    try:
        denied = json.loads(cronjob(action="list"))
        assert denied["success"] is False
    finally:
        clear_session_vars(tokens)

    owner_without_cron = _ctx("owner", "owner", chat_id="owner-chat", capabilities=frozenset())
    tokens = _bind(owner_without_cron)
    try:
        before_count = len(list_jobs(include_disabled=True))
        denied = json.loads(
            cronjob(action="create", prompt="owner local", schedule="every 5m", deliver="local")
        )
        assert denied["success"] is False
        assert "cron capability" in denied["error"]
        assert len(list_jobs(include_disabled=True)) == before_count
    finally:
        clear_session_vars(tokens)

    owner = _ctx("owner", "owner", chat_id="owner-chat", capabilities=frozenset({"cron"}))
    tokens = _bind(owner)
    try:
        created = json.loads(
            cronjob(action="create", prompt="owner local", schedule="every 5m", deliver="local")
        )
        assert created["success"] is True
        stored = get_job(created["job_id"])
        assert stored["deliver"] == "local"
        assert stored["resolved_access_context"] == serialize_resolved_access_context(owner)
    finally:
        clear_session_vars(tokens)


def test_owner_configured_context_list_shows_legacy_and_own_snapshot_hides_foreign(monkeypatch):
    from cron.jobs import create_job
    from tools.cronjob_tools import cronjob

    owner = _ctx("owner", "owner", chat_id="owner-chat", capabilities=frozenset({"cron"}))
    family = _ctx("family-1")
    room = _ctx("room-1", "shared_room", chat_id="room-1", capabilities=frozenset({"public_web"}))
    legacy = create_job("legacy", "every 5m", name="legacy")
    own = create_job(
        "own",
        "every 5m",
        name="own",
        resolved_access_context=serialize_resolved_access_context(owner),
    )
    foreign_family = create_job(
        "foreign family",
        "every 5m",
        name="foreign-family",
        origin={"platform": "telegram", "chat_id": "chat-1", "thread_id": "thread-1"},
        resolved_access_context=serialize_resolved_access_context(family),
    )
    foreign_room = create_job(
        "foreign room",
        "every 5m",
        name="foreign-room",
        origin={"platform": "telegram", "chat_id": "room-1", "thread_id": "thread-1"},
        resolved_access_context=serialize_resolved_access_context(room),
    )
    ran = []
    monkeypatch.setattr(
        "tools.cronjob_tools._execute_job_now",
        lambda job: ran.append(job["id"]) or {"claimed": True, "success": True, "error": None},
    )

    tokens = _bind(owner)
    try:
        listed = json.loads(cronjob(action="list", include_disabled=True))
        assert [job["job_id"] for job in listed["jobs"]] == [legacy["id"], own["id"]]

        denied = json.loads(cronjob(action="run", job_id=foreign_family["id"]))
        assert denied["success"] is False
        denied = json.loads(cronjob(action="run", job_id=foreign_room["id"]))
        assert denied["success"] is False
        assert ran == []

        allowed = json.loads(cronjob(action="run", job_id=legacy["id"]))
        assert allowed["success"] is True
        assert allowed["job"]["execution_success"] is True
        assert ran == [legacy["id"]]
    finally:
        clear_session_vars(tokens)


def test_context_from_foreign_job_denied_before_create_side_effect():
    from cron.jobs import create_job, list_jobs
    from tools.cronjob_tools import cronjob

    family = _ctx("family-1")
    other = _ctx("family-2", chat_id="chat-2")
    foreign = create_job(
        "foreign",
        "every 5m",
        name="foreign",
        origin={"platform": "telegram", "chat_id": "chat-2", "thread_id": "thread-1"},
        resolved_access_context=serialize_resolved_access_context(other),
    )
    tokens = _bind(family)
    try:
        before_count = len(list_jobs(include_disabled=True))
        denied = json.loads(
            cronjob(action="create", prompt="x", schedule="every 5m", context_from=[foreign["id"]])
        )
        assert denied["success"] is False
        assert len(list_jobs(include_disabled=True)) == before_count
    finally:
        clear_session_vars(tokens)


def test_update_context_from_foreign_job_denied_before_side_effect():
    from cron.jobs import create_job, get_job
    from tools.cronjob_tools import cronjob

    family = _ctx("family-1")
    other = _ctx("family-2", chat_id="chat-2")
    owned = create_job(
        "owned",
        "every 5m",
        name="owned",
        origin={"platform": "telegram", "chat_id": "chat-1", "thread_id": "thread-1"},
        resolved_access_context=serialize_resolved_access_context(family),
    )
    foreign = create_job(
        "foreign",
        "every 5m",
        name="foreign",
        origin={"platform": "telegram", "chat_id": "chat-2", "thread_id": "thread-1"},
        resolved_access_context=serialize_resolved_access_context(other),
    )
    tokens = _bind(family)
    try:
        denied = json.loads(
            cronjob(action="update", job_id=owned["id"], context_from=[foreign["id"]])
        )
        assert denied["success"] is False
        assert get_job(owned["id"]).get("context_from") is None
    finally:
        clear_session_vars(tokens)


def test_family_job_with_own_snapshot_and_local_deliver_cannot_update_or_run(monkeypatch):
    from cron.jobs import create_job, get_job
    from tools.cronjob_tools import cronjob

    family = _ctx("family-1")
    tampered = create_job(
        "tampered",
        "every 5m",
        name="tampered",
        deliver="local",
        origin={"platform": "telegram", "chat_id": "chat-1", "thread_id": "thread-1"},
        resolved_access_context=serialize_resolved_access_context(family),
    )
    ran = []
    monkeypatch.setattr(
        "tools.cronjob_tools._execute_job_now",
        lambda job: ran.append(job["id"]) or {"claimed": True, "success": True, "error": None},
    )

    tokens = _bind(family)
    try:
        denied_update = json.loads(
            cronjob(action="update", job_id=tampered["id"], name="still-tampered")
        )
        assert denied_update["success"] is False
        assert get_job(tampered["id"])["name"] == "tampered"

        denied_run = json.loads(cronjob(action="run", job_id=tampered["id"]))
        assert denied_run["success"] is False
        assert ran == []
    finally:
        clear_session_vars(tokens)


def test_scheduler_denies_malformed_and_tampered_context_before_script_or_delivery(monkeypatch):
    from cron.scheduler import _deliver_result, run_job, run_one_job

    script_calls = []
    monkeypatch.setattr(
        "cron.scheduler._run_job_script_with_claim_heartbeat",
        lambda job, script: script_calls.append(job["id"]) or (True, "ran"),
    )
    malformed_job = {
        "id": "bad",
        "name": "bad",
        "no_agent": True,
        "script": "ok.py",
        "deliver": "origin",
        "origin": {"platform": "telegram", "chat_id": "chat-1", "thread_id": "thread-1"},
        "resolved_access_context": {"profile_id": "broken"},
    }
    monkeypatch.setattr("cron.scheduler.create_execution", lambda *a, **kw: pytest.fail("called"))
    assert run_one_job(malformed_job) is False
    with pytest.raises(RuntimeError, match="malformed persisted resolved_access_context"):
        run_job(malformed_job)
    with pytest.raises(RuntimeError, match="malformed persisted resolved_access_context"):
        _deliver_result(malformed_job, "content")
    assert script_calls == []

    family = _ctx("family-1")
    tampered_job = {
        **malformed_job,
        "id": "tampered",
        "deliver": "local",
        "resolved_access_context": serialize_resolved_access_context(family),
    }
    with pytest.raises(RuntimeError, match="requires deliver=origin"):
        run_job(tampered_job)
    assert script_calls == []


@pytest.mark.parametrize("deliver", ["local", "all", "telegram:chat-1:thread-1"])
def test_scheduler_denies_family_persisted_non_origin_delivery_before_script(monkeypatch, deliver):
    from cron.scheduler import run_job

    family = _ctx("family-1")
    script_calls = []
    monkeypatch.setattr(
        "cron.scheduler._run_job_script_with_claim_heartbeat",
        lambda job, script: script_calls.append(job["id"]) or (True, "ran"),
    )
    job = {
        "id": f"family-{deliver}",
        "name": "family",
        "no_agent": True,
        "script": "ok.py",
        "deliver": deliver,
        "origin": {"platform": "telegram", "chat_id": "chat-1", "thread_id": "thread-1"},
        "resolved_access_context": serialize_resolved_access_context(family),
    }
    with pytest.raises(RuntimeError, match="requires deliver=origin"):
        run_job(job)
    assert script_calls == []


def test_scheduler_uses_owner_persisted_delivery_target_before_home_fallback(monkeypatch):
    from cron.scheduler import _resolve_delivery_targets

    owner = _ctx(
        "owner",
        "owner",
        chat_id="owner-chat",
        capabilities=frozenset({"cron"}),
    )
    home_lookups = []

    def get_home_target_chat_id(platform_name):
        home_lookups.append(platform_name)
        return "owner-default"

    monkeypatch.setattr(
        "cron.scheduler._get_home_target_chat_id",
        get_home_target_chat_id,
    )
    job = {
        "id": "owner-origin",
        "name": "owner-origin",
        "deliver": "origin",
        "resolved_access_context": serialize_resolved_access_context(owner),
    }

    assert _resolve_delivery_targets(job) == [
        {
            "platform": "telegram",
            "chat_id": "owner-chat",
            "thread_id": "thread-1",
        }
    ]
    assert home_lookups == []


def test_scheduler_denies_owner_explicit_foreign_delivery_target():
    from cron.scheduler import _resolve_delivery_targets

    owner = _ctx(
        "owner",
        "owner",
        chat_id="owner-chat",
        capabilities=frozenset({"cron"}),
    )
    job = {
        "id": "owner-foreign",
        "name": "owner-foreign",
        "deliver": "telegram:foreign-chat",
        "resolved_access_context": serialize_resolved_access_context(owner),
    }

    with pytest.raises(RuntimeError, match="resolved access context"):
        _resolve_delivery_targets(job)


@pytest.mark.parametrize(
    ("context", "message"),
    [
        (_ctx("owner", "owner", chat_id="owner-chat", capabilities=frozenset()), "cron capability"),
        (_ctx("guest", "guest", chat_id="guest-chat"), "role 'guest'"),
    ],
)
def test_scheduler_denies_unknown_role_and_owner_without_cron_before_script(
    monkeypatch,
    context,
    message,
):
    from cron.scheduler import run_job

    script_calls = []
    monkeypatch.setattr(
        "cron.scheduler._run_job_script_with_claim_heartbeat",
        lambda job, script: script_calls.append(job["id"]) or (True, "ran"),
    )
    job = {
        "id": "denied-role",
        "name": "denied-role",
        "no_agent": True,
        "script": "ok.py",
        "deliver": "origin",
        "origin": {
            "platform": context.delivery_target.platform,
            "chat_id": context.delivery_target.chat_id,
            "thread_id": context.delivery_target.thread_id,
        },
        "resolved_access_context": serialize_resolved_access_context(context),
    }
    with pytest.raises(RuntimeError, match=message):
        run_job(job)
    assert script_calls == []


def test_scheduler_restores_resolved_context_inside_agent_run(monkeypatch, tmp_path):
    from cron.scheduler import run_job

    family = _ctx("family-1")
    seen = {}
    fake_db = MagicMock()
    monkeypatch.setenv("HERMES_MODEL", "test-model")

    class FakeAgent:
        def __init__(self, *args, **kwargs):
            pass

        def run_conversation(self, *args, **kwargs):
            from gateway.session_context import get_resolved_access_context, get_session_env

            seen["context"] = get_resolved_access_context()
            seen["delivery"] = {
                "platform": get_session_env("HERMES_CRON_AUTO_DELIVER_PLATFORM"),
                "chat_id": get_session_env("HERMES_CRON_AUTO_DELIVER_CHAT_ID"),
                "thread_id": get_session_env("HERMES_CRON_AUTO_DELIVER_THREAD_ID"),
            }
            return {"final_response": "ok", "completed": True}

        def close(self):
            pass

    job = {
        "id": "family-run",
        "name": "family-run",
        "prompt": "say ok",
        "deliver": "origin",
        "origin": {"platform": "telegram", "chat_id": "chat-1", "thread_id": "thread-1"},
        "resolved_access_context": serialize_resolved_access_context(family),
    }
    with (
        monkeypatch.context() as m,
    ):
        m.setattr("cron.scheduler._hermes_home", tmp_path)
        m.setattr("hermes_state.SessionDB", lambda: fake_db)
        m.setattr(
            "hermes_cli.runtime_provider.resolve_runtime_provider",
            lambda **kwargs: {
                "api_key": "***",
                "base_url": "https://example.invalid/v1",
                "provider": "openrouter",
                "api_mode": "chat_completions",
            },
        )
        m.setattr("run_agent.AIAgent", FakeAgent)
        success, _output, final_response, error = run_job(job)

    assert success is True
    assert final_response == "ok"
    assert error is None
    assert seen["context"] == family
    assert seen["delivery"] == {
        "platform": "telegram",
        "chat_id": "chat-1",
        "thread_id": "thread-1",
    }
