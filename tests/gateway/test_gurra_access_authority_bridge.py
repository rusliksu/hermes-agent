"""Resolved access context must satisfy the 0.23 session actor capability contract."""
from types import SimpleNamespace

import pytest

from gateway.access_registry import DeliveryTarget, ResolvedAccessContext
from gateway.access_runtime import session_actor_for_context
from gateway.session_authority import SessionAuthority
from gateway.session_contract import SessionRef
from hermes_state import SessionDB
from hermes_state_runtime import RuntimeStoreError


def _context(role="member"):
    return ResolvedAccessContext(
        principal_id="person:1001",
        role_id=role,
        profile_id="alpha",
        conversation_scope="telegram:1001",
        capabilities=frozenset({"terminal", "documents"}),
        delivery_target=DeliveryTarget("telegram", "account", "dm", "1001"),
    )


@pytest.fixture
def authority(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.create_session("fixture", source="telegram")
    auth = SessionAuthority(SimpleNamespace(), profile_id="alpha", instance_id="i", db=db, epoch=1)
    auth.sessions["fixture"] = SimpleNamespace(source=None, route="telegram:alpha:fixture")
    try:
        yield auth
    finally:
        db.close()


def test_member_actor_passes_submit_and_carries_tool_capabilities():
    actor = session_actor_for_context(_context(), transport_id="telegram:alpha")
    assert actor.subject == "person:1001"
    assert actor.profile_id == "alpha"
    assert "session:submit" in actor.capabilities
    assert "tool:documents" in actor.capabilities


def test_member_actor_is_accepted_by_the_authority(authority):
    actor = session_actor_for_context(_context(), transport_id="telegram:alpha")
    authority.authorize(actor, SessionRef("alpha", "fixture"), "session:submit")


def test_role_without_verbs_is_denied(authority):
    actor = session_actor_for_context(_context("observer"), transport_id="telegram:alpha")
    assert actor.capabilities == frozenset({"tool:documents", "tool:terminal"})
    with pytest.raises(RuntimeStoreError) as caught:
        authority.authorize(actor, SessionRef("alpha", "fixture"), "session:submit")
    assert caught.value.reason == "permission_denied"


def test_foreign_profile_actor_is_rejected(authority):
    actor = session_actor_for_context(_context(), transport_id="telegram:beta")
    foreign = actor.__class__(subject=actor.subject, profile_id="beta",
                              capabilities=actor.capabilities, transport_id=actor.transport_id)
    with pytest.raises(RuntimeStoreError) as caught:
        authority.authorize(foreign, SessionRef("beta", "fixture"), "session:submit")
    assert caught.value.reason == "profile_mismatch"
