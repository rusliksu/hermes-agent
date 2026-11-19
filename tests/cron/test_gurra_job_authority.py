"""Cron must persist its own authority and reject unscoped execution before scripts."""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from agent.secret_scope import is_multiplex_active, set_multiplex_active
from cron.jobs import create_job, get_job, list_jobs, update_job, use_cron_store
from cron.scheduler import run_job
from gateway.access_registry import serialize_resolved_access_context
from gateway.run import _profile_runtime_scope
from gateway.access_context import bind_resolved_access_context, get_resolved_access_context
from tests.agent.test_gurra_memory_snapshot import context, homes

_FIXTURES = (context, homes)  # imported for pytest fixture discovery, not called here
from tools.cronjob_tools import cronjob


def test_real_cron_tool_persists_an_independent_immutable_snapshot(homes):
    caller_profile = "alpha"
    with _profile_runtime_scope(homes[caller_profile]), bind_resolved_access_context(context(caller_profile)), use_cron_store(homes[caller_profile]):
        response = json.loads(cronjob(action="create", prompt="synthetic fixture", schedule="every 1h", paused=True))
        assert response["success"] is True, response
        stored = get_job(response["job_id"])
        assert stored["resolved_access_context"]["profile_id"] == "alpha"
        assert stored["resolved_access_context"]["delivery_target"]["chat_id"] == "1001"
        replacement = serialize_resolved_access_context(context("beta"))
        with pytest.raises(ValueError, match="cannot be updated"):
            update_job(response["job_id"], {"resolved_access_context": replacement})
        assert get_job(response["job_id"])["resolved_access_context"] == stored["resolved_access_context"]


def test_missing_cron_tool_authority_does_not_create_a_job(homes):
    previous = is_multiplex_active()
    set_multiplex_active(True)
    try:
        with _profile_runtime_scope(homes["alpha"]), bind_resolved_access_context(None), use_cron_store(homes["alpha"]):
            response = json.loads(cronjob(action="create", prompt="fixture", schedule="every 1h", paused=True))
            assert response["success"] is False
            assert list_jobs(include_disabled=True) == []
    finally:
        set_multiplex_active(previous)


def test_invalid_snapshot_is_rejected_before_native_job_storage(homes):
    payload = serialize_resolved_access_context(context())
    payload["extra"] = "untrusted"
    with _profile_runtime_scope(homes["alpha"]), use_cron_store(homes["alpha"]):
        with pytest.raises(ValueError, match="malformed_resolved_access_context"):
            create_job(prompt="fixture", schedule="every 1h", paused=True, resolved_access_context=payload)
        assert list_jobs(include_disabled=True) == []


@pytest.mark.parametrize("damage", ["missing", "malformed", "home"])
def test_cron_rejects_bad_authority_before_real_script_execution(homes, damage):
    script = homes["beta"] / "scripts" / "fixture.py" if damage == "home" else homes["alpha"] / "scripts" / "fixture.py"
    script.parent.mkdir()
    marker = script.with_suffix(".ran")
    script.write_text("from pathlib import Path\nPath(__file__).with_suffix('.ran').write_text('executed')\nprint('fixture')\n")
    job = {"id": "fixture", "name": "fixture", "no_agent": True, "script": str(script), "deliver": "local"}
    if damage != "missing":
        job["resolved_access_context"] = serialize_resolved_access_context(context())
    if damage == "malformed":
        job["resolved_access_context"]["extra"] = "untrusted"
    previous = is_multiplex_active()
    set_multiplex_active(True)
    try:
        with _profile_runtime_scope(homes["beta"] if damage == "home" else homes["alpha"]), bind_resolved_access_context(None):
            blocked = False
            try:
                run_job(job)
            except RuntimeError as error:
                assert "Cron job blocked" in str(error)
                blocked = True
        assert not marker.exists(), "The rejected job executed its script"
        assert blocked
    finally:
        set_multiplex_active(previous)


def test_scoped_script_job_runs_and_restores_the_callers_context(homes):
    script = homes["alpha"] / "scripts" / "fixture.py"
    script.parent.mkdir()
    script.write_text("from pathlib import Path\nPath(__file__).with_suffix('.ran').write_text('executed')\nprint('fixture')\n")
    job = {"id": "fixture", "name": "fixture", "no_agent": True, "script": str(script), "deliver": "local",
           "resolved_access_context": serialize_resolved_access_context(replace(context(), role_id="owner"))}
    other = replace(context("beta"), role_id="owner")
    with _profile_runtime_scope(homes["alpha"]), bind_resolved_access_context(other):
        success, _, response, error = run_job(job)
        assert success is True and error is None, (response, error)
        assert response.strip() == "fixture"
        assert Path(script.with_suffix(".ran")).read_text() == "executed"
        assert get_resolved_access_context() == other


@pytest.mark.parametrize("foreign", [False, True])
def test_native_cron_file_send_uses_job_scope_and_rejects_foreign_files(homes, monkeypatch, foreign):
    import asyncio
    import threading
    from unittest.mock import AsyncMock
    from cron.scheduler_delivery import _deliver_result
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.platforms.base import SendResult
    from hermes_constants import get_hermes_home
    from tests.gateway.test_access_native_identity import Transport

    paths = {}
    for name, home in homes.items():
        path = home / "cache" / "documents" / "report.csv"
        path.parent.mkdir(parents=True)
        path.write_text("fixture,value\n")
        paths[name] = path
    config = GatewayConfig(platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, extra={"account": "fixture-bot"})})
    monkeypatch.setattr("gateway.config.load_gateway_config", lambda: config)
    monkeypatch.setattr("cron.scheduler.load_config", lambda: {"cron": {"wrap_response": False}})
    async def no_fallback(*args, **kwargs):
        pytest.fail("native test must not use a standalone network sender")
    monkeypatch.setattr("tools.send_message_tool._send_to_platform", no_fallback)
    adapter = Transport(config.platforms[Platform.TELEGRAM], Platform.TELEGRAM)
    observed = []
    async def send(chat_id, text, **kwargs):
        assert chat_id == "1001"
        assert get_resolved_access_context().profile_id == "alpha"
        assert get_hermes_home() == homes["alpha"]
        return SendResult(success=True, message_id="text-fixture")
    async def document(chat_id, file_path, **kwargs):
        assert chat_id == "1001"
        assert get_resolved_access_context().profile_id == "alpha"
        assert Path(file_path) == paths["alpha"]
        observed.append(file_path)
        return SendResult(success=True, message_id="document-fixture")
    adapter.send = AsyncMock(side_effect=send)
    adapter.send_document = AsyncMock(side_effect=document)
    job = {"id": "fixture", "name": "fixture", "deliver": "origin",
           "origin": {"platform": "telegram", "chat_id": "1001", "chat_type": "dm", "user_id": "1001"},
           "resolved_access_context": serialize_resolved_access_context(replace(context(), role_id="owner"))}
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        with _profile_runtime_scope(homes["alpha"]), bind_resolved_access_context(context("beta")):
            error = _deliver_result(job, "Fixture\nMEDIA:" + str(paths["beta" if foreign else "alpha"]),
                                    adapters={Platform.TELEGRAM: adapter}, loop=loop)
            assert get_resolved_access_context().profile_id == "beta"
        if foreign:
            assert error is not None
            assert observed == []
        else:
            assert error is None, error
            assert observed == [str(paths["alpha"])]
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        assert not thread.is_alive()
        loop.close()
