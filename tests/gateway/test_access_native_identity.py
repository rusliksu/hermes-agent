"""One native routing identity must carry validated registry authority."""
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from gateway.access_registry import (AccessRegistry, DeliveryTarget, PrincipalBinding,
                                    RolePolicy, TransportIdentity)
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter
from gateway.platforms.event import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import SessionSource
from gateway.session_identity import canonical_identity


class Transport(BasePlatformAdapter):
    pass


Transport.__abstractmethods__ = frozenset()


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    caps = frozenset({"private_memory", "public_web", "terminal"})
    bindings = []
    for uid, profile in (("1001", "alpha"), ("1002", "beta")):
        (tmp_path / ".hermes" / "profiles" / profile).mkdir(parents=True)
        (tmp_path / ".hermes" / "profiles" / profile / "config.yaml").write_text("{}\n")
        bindings.append(PrincipalBinding(
            principal_id="member-" + profile, role_id="family", profile_id=profile,
            transport_identity=TransportIdentity("telegram", "fixture-bot", "dm", uid, uid),
            conversation_scope="private-" + profile,
            delivery_target=DeliveryTarget("telegram", "fixture-bot", "dm", uid),
        ))
    registry = AccessRegistry(roles={"family": RolePolicy("family", caps)},
        profiles=frozenset({"alpha", "beta"}), principal_bindings=tuple(bindings),
        scope_capabilities={"private-alpha": caps, "private-beta": caps}, backend_capabilities=caps)
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(multiplex_profiles=True, access_registry=registry)
    runner.access_registry = registry
    runner._primary_profile_name = "default"
    adapter = Transport(PlatformConfig(enabled=True, extra={"account": "fixture-bot"}), Platform.TELEGRAM)
    adapter.gateway_runner = runner
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._profile_adapters = {"alpha": {}, "beta": {}}
    return runner


def source(uid="1001"):
    s = SessionSource(platform=Platform.TELEGRAM, chat_id=uid, user_id=uid)
    s.route_account = "fixture-bot"
    return s


def received(rig, uid="1001"):
    return rig.adapters[Platform.TELEGRAM].build_source(chat_id=uid, user_id=uid)


def test_unknown_registry_sender_has_no_native_identity(rig):
    s = received(rig, "1009")
    assert canonical_identity(s, runner=rig) is None
    assert s.profile_route_rejected is True


def test_native_identity_binds_registered_profiles_without_owner_fallback(rig):
    identities = [canonical_identity(received(rig, uid), runner=rig) for uid in ("1001", "1002", "1001")]
    assert [i.runtime_profile for i in identities] == ["alpha", "beta", "alpha"]
    assert [getattr(getattr(i, "access_context", None), "profile_id", None) for i in identities] == ["alpha", "beta", "alpha"]
    assert identities[0].runtime_home == identities[2].runtime_home
    assert identities[0].runtime_home != identities[1].runtime_home


def test_pinned_identity_rejects_changed_sender(rig):
    s = received(rig)
    assert canonical_identity(s, runner=rig) is not None
    s.user_id = "1009"
    assert canonical_identity(s, runner=rig) is None
    assert s.profile_route_rejected is True


def test_real_adapter_stamps_account_and_registry_profile_before_keys(rig):
    from gateway.session_identity import identity_of
    adapter = rig.adapters[Platform.TELEGRAM]
    s = adapter.build_source(chat_id="1002", user_id="1002")
    assert s.profile == "beta"
    assert s.route_account == "fixture-bot"
    assert identity_of(s).access_context.delivery_target.chat_id == "1002"
    assert adapter._source_session_key(s) == "agent:beta:telegram:dm:1002"


def test_source_account_cannot_override_physical_transport(rig):
    s = received(rig)
    s.route_account = "forged-bot"
    identity = canonical_identity(s, runner=rig)
    assert identity.access_context.delivery_target.account == "fixture-bot"


def test_missing_server_owned_account_fails_closed(rig):
    rig.adapters[Platform.TELEGRAM].config.extra.clear()
    s = received(rig)
    assert canonical_identity(s, runner=rig) is None
    assert rig._is_user_authorized_for_source(s) is False


def test_authorization_uses_registry_and_revalidates_pinned_sender(rig):
    s = received(rig)
    assert rig._is_user_authorized_for_source(s) is True
    assert rig.adapters[Platform.TELEGRAM]._canonicalize(s) is not None
    s.user_id = "1009"
    assert rig._is_user_authorized_for_source(s) is False
    assert rig.adapters[Platform.TELEGRAM]._canonicalize(s) is None


@pytest.mark.asyncio
async def test_turn_boundary_binds_profile_and_context_then_restores(rig):
    from hermes_constants import get_hermes_home
    from gateway.access_context import get_resolved_access_context, get_current_delivery_target
    seen = []

    async def model_boundary(message, context_prompt, history, s, session_id, **kwargs):
        ctx = get_resolved_access_context()
        seen.append((ctx.profile_id, get_hermes_home().name, get_current_delivery_target().chat_id))
        return {"final_response": "fixture"}

    rig._run_agent_inner = model_boundary
    for uid in ("1001", "1002", "1001"):
        await rig._run_agent("fixture", "", [], received(rig, uid), "fixture-session")
        assert get_resolved_access_context() is None
        assert get_current_delivery_target() is None
    assert seen == [("alpha", "alpha", "1001"), ("beta", "beta", "1002"), ("alpha", "alpha", "1001")]


@pytest.mark.asyncio
async def test_unknown_sender_never_starts_turn(rig):
    from gateway.session_identity import IdentityUnresolved
    rig._run_agent_inner = AsyncMock()
    with pytest.raises(IdentityUnresolved):
        await rig._run_agent("fixture", "", [], received(rig, "1009"), "fixture-session")
    rig._run_agent_inner.assert_not_awaited()


@pytest.mark.asyncio
async def test_ingress_drops_unknown_before_hooks_or_turn(rig):
    rig._hm_pre_gateway_dispatch_hook = AsyncMock()
    event = MessageEvent(text="fixture", message_type=MessageType.TEXT, source=received(rig, "1009"))
    assert await rig._hm_admit_event(event) is None
    rig._hm_pre_gateway_dispatch_hook.assert_not_awaited()


def test_unattached_source_cannot_claim_a_registered_transport(rig):
    assert canonical_identity(source(), runner=rig) is None


def test_registry_with_no_bindings_cannot_fall_back_to_transport_allowlist(rig):
    old = rig.access_registry
    rig.access_registry = AccessRegistry(roles=old.roles, profiles=old.profiles, principal_bindings=(),
                                        scope_capabilities=old.scope_capabilities,
                                        backend_capabilities=old.backend_capabilities)
    assert rig._is_user_authorized_for_source(received(rig)) is False


def test_deleted_profile_invalidates_a_pinned_identity(rig):
    s = received(rig)
    identity = canonical_identity(s, runner=rig)
    (identity.runtime_home / "config.yaml").unlink()
    assert canonical_identity(s, runner=rig) is None


def test_access_authority_is_not_serialized_in_source_wire_dict(rig):
    wire = received(rig).to_dict()
    assert "resolved_access_context" not in wire
    assert "route_account" not in wire


def test_reused_source_after_clear_is_resolved_for_new_sender(rig):
    from gateway.session_identity import clear_identity
    s = received(rig)
    clear_identity(s)
    s.user_id = s.chat_id = "1002"
    assert canonical_identity(s, runner=rig).runtime_profile == "beta"


def test_pinned_source_cannot_move_to_another_receiving_adapter(rig):
    s = received(rig)
    other = Transport(PlatformConfig(enabled=True, extra={"account": "another-bot"}), Platform.TELEGRAM)
    other.gateway_runner = rig
    assert other._canonicalize(s) is None
    assert s.profile_route_rejected is True


@pytest.mark.asyncio
async def test_overlapping_turns_keep_their_own_access_and_profile(rig):
    import asyncio
    from hermes_constants import get_hermes_home
    from gateway.access_context import get_resolved_access_context
    both_running = asyncio.Event()
    started = []

    async def model_boundary(message, context_prompt, history, s, session_id, **kwargs):
        before = (get_resolved_access_context().profile_id, get_hermes_home().name)
        started.append(before)
        if len(started) == 2:
            both_running.set()
        await asyncio.wait_for(both_running.wait(), timeout=2)
        assert (get_resolved_access_context().profile_id, get_hermes_home().name) == before
        return {"final_response": "fixture"}

    rig._run_agent_inner = model_boundary
    await asyncio.gather(*(rig._run_agent("fixture", "", [], received(rig, uid), "fixture-" + uid)
                           for uid in ("1001", "1002")))
    assert sorted(started) == [("alpha", "alpha"), ("beta", "beta")]
    assert get_resolved_access_context() is None
