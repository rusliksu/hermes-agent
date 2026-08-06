"""Cron jobs must retain the six-field access context that created them."""

from unittest.mock import patch

import pytest

from cron.scheduler import _resolve_cron_access_context
from gateway.access_registry import (
    DeliveryTarget,
    ResolvedAccessContext,
    serialize_resolved_access_context,
)


def _context() -> ResolvedAccessContext:
    return ResolvedAccessContext(
        principal_id="principal-42",
        role_id="family_standard",
        profile_id="family-42",
        conversation_scope="private:principal-42",
        capabilities=frozenset({"memory_read", "public_web"}),
        delivery_target=DeliveryTarget(
            platform="telegram",
            account="main-bot",
            peer_kind="dm",
            chat_id="42",
        ),
    )


def test_resolver_round_trips_job_owned_context():
    payload = serialize_resolved_access_context(_context())

    with patch("agent.secret_scope.is_multiplex_active", return_value=True):
        resolved = _resolve_cron_access_context(
            {"id": "job-1", "resolved_access_context": payload}
        )

    assert resolved == _context()


def test_resolver_rejects_missing_context_in_multiplex():
    with patch("agent.secret_scope.is_multiplex_active", return_value=True):
        with pytest.raises(
            RuntimeError, match="missing_resolved_access_context"
        ):
            _resolve_cron_access_context({"id": "job-1"})


def test_resolver_rejects_malformed_context_in_multiplex():
    with patch("agent.secret_scope.is_multiplex_active", return_value=True):
        with pytest.raises(
            RuntimeError, match="malformed_resolved_access_context"
        ):
            _resolve_cron_access_context(
                {
                    "id": "job-1",
                    "resolved_access_context": {
                        "principal_id": "principal-42"
                    },
                }
            )


def test_resolver_fails_closed_when_mode_cannot_be_determined():
    with patch(
        "agent.secret_scope.is_multiplex_active",
        side_effect=RuntimeError("probe unavailable"),
    ):
        with pytest.raises(
            RuntimeError, match="multiplex_state_unavailable"
        ):
            _resolve_cron_access_context({"id": "job-1"})


@pytest.fixture
def isolated_cron_store(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    (hermes_home / "cron" / "output").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    import cron.jobs as jobs

    monkeypatch.setattr(jobs, "HERMES_DIR", hermes_home)
    monkeypatch.setattr(jobs, "CRON_DIR", hermes_home / "cron")
    monkeypatch.setattr(jobs, "JOBS_FILE", hermes_home / "cron" / "jobs.json")
    monkeypatch.setattr(jobs, "OUTPUT_DIR", hermes_home / "cron" / "output")
    return jobs


def test_create_job_persists_canonical_context(isolated_cron_store):
    jobs = isolated_cron_store
    payload = serialize_resolved_access_context(_context())

    created = jobs.create_job(
        prompt="remember this",
        schedule="every 1h",
        resolved_access_context=payload,
    )
    loaded = jobs.get_job(created["id"])

    assert loaded["resolved_access_context"] == payload


def test_create_job_rejects_malformed_context(isolated_cron_store):
    with pytest.raises(ValueError, match="malformed_resolved_access_context"):
        isolated_cron_store.create_job(
            prompt="bad",
            schedule="every 1h",
            resolved_access_context={"principal_id": "wrong"},
        )
