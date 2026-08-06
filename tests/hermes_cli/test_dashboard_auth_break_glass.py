"""Break-glass lease privacy and lifecycle contracts."""

import datetime as dt
import json

import pytest

from hermes_cli.dashboard_auth.audit import (
    BREAK_GLASS_MAX_AGE,
    BreakGlassError,
    assert_break_glass_read,
    create_break_glass_lease,
    revoke_break_glass_lease,
)


@pytest.fixture
def profile_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def _now() -> dt.datetime:
    return dt.datetime(2026, 8, 5, 12, 0, tzinfo=dt.timezone.utc)


def test_break_glass_requires_reason_and_reconfirmation(profile_home):
    with pytest.raises(ValueError, match="reason"):
        create_break_glass_lease(
            "",
            read_only_scope="profile-a/session-1",
            reconfirmed=True,
            now=_now(),
        )
    with pytest.raises(BreakGlassError, match="reconfirmation"):
        create_break_glass_lease(
            "incident review",
            read_only_scope="profile-a/session-1",
            reconfirmed=False,
            now=_now(),
        )


def test_break_glass_is_fifteen_minutes_read_only_and_metadata_only(profile_home):
    reason = "inspect private message body: do not copy"
    lease = create_break_glass_lease(
        reason,
        read_only_scope="profile-a/session-1",
        reconfirmed=True,
        now=_now(),
    )

    assert lease.expires_at - lease.issued_at == BREAK_GLASS_MAX_AGE
    assert lease.is_active(_now())
    assert_break_glass_read(lease, action="history_read", now=_now())

    for action in (
        "bulk_search",
        "export",
        "model_delivery",
        "telegram_delivery",
        "tool_execution",
        "memory_mutation",
    ):
        with pytest.raises(BreakGlassError):
            assert_break_glass_read(lease, action=action, now=_now())
    with pytest.raises(BreakGlassError):
        assert_break_glass_read(
            lease,
            action="export: private message body",
            now=_now(),
        )

    with pytest.raises(BreakGlassError, match="expired"):
        assert_break_glass_read(lease, now=lease.expires_at)

    raw = (profile_home / "logs" / "dashboard-auth.log").read_text()
    assert reason not in raw
    assert "private message body" not in raw
    events = [json.loads(line) for line in raw.splitlines()]
    assert any(event["event"] == "break_glass_created" for event in events)
    assert any(event["event"] == "break_glass_expired" for event in events)
    assert all("scope_hash" in event for event in events)


def test_break_glass_manual_revoke_is_immediate_and_audited(profile_home):
    lease = create_break_glass_lease(
        "support investigation",
        read_only_scope="profile-a/session-1",
        reconfirmed=True,
        now=_now(),
    )
    revoked = revoke_break_glass_lease(lease, now=_now())

    assert revoked.revoked is True
    with pytest.raises(BreakGlassError, match="revoked"):
        assert_break_glass_read(revoked, now=_now())
    assert revoke_break_glass_lease(revoked, now=_now()) is revoked

    events = [
        json.loads(line)
        for line in (profile_home / "logs" / "dashboard-auth.log").read_text().splitlines()
    ]
    assert any(event["event"] == "break_glass_revoked" for event in events)
    assert all("support investigation" not in json.dumps(event) for event in events)
