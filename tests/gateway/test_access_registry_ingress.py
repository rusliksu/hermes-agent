import pytest

from gateway.access_registry import (
    AccessRegistry,
    DeliveryTarget,
    ParticipantIdentity,
    PrincipalBinding,
    RolePolicy,
    SharedScopeBinding,
    TransportIdentity,
)
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)
from gateway.profile_routing import ProfileRoute
from gateway.run import GatewayRunner


ACCOUNT = "bot-a"
CAPS = frozenset({"public_web"})
RAW_LOG_VALUES = (
    ACCOUNT,
    "wrong-account",
    "u1",
    "u2",
    "other-chat",
    "principal-family",
    "family-profile",
    "other-profile",
)


class _Adapter(BasePlatformAdapter):
    def __init__(self, *, account=ACCOUNT, runner=None):
        super().__init__(
            PlatformConfig(enabled=True, token="token", extra={"account": account}),
            Platform.TELEGRAM,
        )
        self.gateway_runner = runner

    async def connect(self):
        return True

    async def disconnect(self):
        pass

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        return SendResult(success=True, message_id="sent")

    async def get_chat_info(self, chat_id):
        return {"id": chat_id, "type": "private"}


def _target(identity):
    return DeliveryTarget(
        platform=identity.platform,
        account=identity.account,
        peer_kind=identity.peer_kind,
        chat_id=identity.chat_id,
        thread_id=identity.thread_id,
    )


def _identity(user_id="u1", *, account=ACCOUNT, chat_id=None, peer_kind="dm", thread_id=None):
    return TransportIdentity(
        platform="telegram",
        account=account,
        peer_kind=peer_kind,
        user_id=user_id,
        chat_id=user_id if chat_id is None else chat_id,
        thread_id=thread_id,
    )


def _registry(*, active=True, user_id="u1", profile_id="family-profile"):
    identity = _identity(user_id)
    return AccessRegistry(
        roles={"family": RolePolicy("family", CAPS)},
        profiles=frozenset({profile_id}),
        principal_bindings=(
            PrincipalBinding(
                principal_id="principal-family",
                role_id="family",
                profile_id=profile_id,
                transport_identity=identity,
                conversation_scope="private",
                delivery_target=_target(identity),
                active=active,
            ),
        ),
        scope_capabilities={"private": CAPS},
        backend_capabilities=CAPS,
    )


def _shared_room_registry(*, active=True):
    room_identity = _identity(
        "u1",
        chat_id="room-1",
        peer_kind="group",
    )
    return AccessRegistry(
        roles={"shared_room": RolePolicy("shared_room", CAPS)},
        profiles=frozenset({"family-profile"}),
        shared_scope_bindings=(
            SharedScopeBinding(
                principal_id="principal-family",
                role_id="shared_room",
                profile_id="family-profile",
                room_identity=room_identity,
                conversation_scope="room",
                delivery_target=_target(room_identity),
                participant_identities=(
                    ParticipantIdentity("telegram", ACCOUNT, "u1"),
                    ParticipantIdentity("telegram", ACCOUNT, "u2"),
                ),
                active=active,
            ),
        ),
        scope_capabilities={"room": CAPS},
        backend_capabilities=CAPS,
    )


def _runner(registry, *, profile_routes=None):
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="token")},
        multiplex_profiles=True,
        profile_routes=profile_routes or [],
    )
    runner.access_registry = registry
    runner._single_principal_policy = runner.config.single_principal
    runner._startup_restore_in_progress = False
    runner._scale_to_zero_note_real_inbound = lambda: None
    runner._is_user_authorized = lambda source: True
    runner._update_prompt_pending = {}
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._active_session_leases = {}
    runner._session_run_generation = {}
    runner._pending_messages = {}
    runner._queued_events = {}
    runner._busy_ack_ts = {}
    runner._busy_input_mode = "interrupt"
    runner._draining = False
    runner._external_drain_active = False
    runner._persist_active_agents = lambda: None
    return runner


class _ExplodingRegistry:
    def resolve(self, identity):
        raise AssertionError("multiplex-off ingress must not resolve identity")


def _event(
    adapter,
    *,
    text="hello",
    user_id="u1",
    chat_id=None,
    chat_type="dm",
    profile=None,
    account=ACCOUNT,
):
    if account != ACCOUNT:
        adapter.config.extra["account"] = account
    source = adapter.build_source(
        chat_id=user_id if chat_id is None else chat_id,
        chat_type=chat_type,
        user_id=user_id,
    )
    if profile is not None:
        source.profile = profile
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=source,
    )


def _rendered_logs(caplog):
    return "\n".join(record.getMessage() for record in caplog.records)


def _assert_no_raw_access_values(rendered):
    for raw_value in RAW_LOG_VALUES:
        assert raw_value not in rendered


def _guard_denied_downstream(monkeypatch, runner):
    called = {
        "queued": False,
        "scaled": False,
        "authorized": False,
        "plugin": False,
    }

    def queue(event):
        called["queued"] = True

    def scale():
        called["scaled"] = True

    def auth(source):
        called["authorized"] = True
        return True

    async def downstream(*args, **kwargs):
        raise AssertionError("denied ingress must not reach the agent")

    def plugin_hook(*args, **kwargs):
        called["plugin"] = True
        return []

    runner._startup_restore_in_progress = True
    runner._queue_startup_restore_event = queue
    runner._scale_to_zero_note_real_inbound = scale
    runner._is_user_authorized = auth
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", plugin_hook)
    monkeypatch.setattr(GatewayRunner, "_handle_message_with_agent", downstream)
    return called


@pytest.fixture(autouse=True)
def _gateway_stubs(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *args, **kwargs: [])
    monkeypatch.setattr("tools.slash_confirm.get_pending", lambda *args, **kwargs: None)
    monkeypatch.setattr("tools.slash_confirm.clear_if_stale", lambda *args, **kwargs: None)
    monkeypatch.setattr("tools.approval.has_blocking_approval", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        "hermes_cli.active_sessions.try_acquire_active_session",
        lambda *args, **kwargs: (None, None),
    )
    monkeypatch.setattr(
        "hermes_cli.active_sessions.resolve_max_concurrent_sessions",
        lambda config: None,
    )


@pytest.mark.asyncio
async def test_registry_allow_binds_context_and_profile_before_agent():
    runner = _runner(_registry())
    adapter = _Adapter(runner=runner)

    async def status_handler(event):
        source = event.source
        assert source.profile == "family-profile"
        assert source.resolved_access_context.profile_id == "family-profile"
        assert source.resolved_access_context.principal_id == "principal-family"
        wire = source.to_dict()
        assert "resolved_access_context" not in wire
        assert "_trusted_transport_identity_fingerprint" not in wire
        assert type(source).from_dict(wire).resolved_access_context is None
        assert type(source).from_dict(wire)._trusted_transport_identity_fingerprint is None
        return "status ok"

    runner._handle_status_command = status_handler

    assert await runner._handle_message(_event(adapter, text="/status")) == "status ok"


@pytest.mark.asyncio
async def test_registry_internal_event_with_valid_context_binds_target_and_profile_before_agent():
    registry = _registry()
    runner = _runner(registry)
    adapter = _Adapter(runner=runner)
    event = _event(adapter, text="/status")
    event.internal = True
    event.source.route_account = None
    event.source.resolved_access_context = registry.resolve(_identity("u1"))

    async def status_handler(event):
        source = event.source
        assert source.profile == "family-profile"
        assert source.route_account == ACCOUNT
        assert source.resolved_access_context.profile_id == "family-profile"
        assert source.resolved_access_context.delivery_target.account == ACCOUNT
        return "status ok"

    runner._handle_status_command = status_handler

    assert await runner._handle_message(event) == "status ok"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_kwargs,registry",
    [
        ({"user_id": "unknown"}, _registry()),
        ({"account": ""}, _registry()),
        ({"user_id": "u1", "chat_id": "other"}, _registry()),
        ({}, _registry(active=False)),
    ],
)
async def test_registry_denials_stop_before_gateway_downstream(
    monkeypatch,
    caplog,
    event_kwargs,
    registry,
):
    runner = _runner(registry)
    adapter = _Adapter(runner=runner)
    called = _guard_denied_downstream(monkeypatch, runner)

    with caplog.at_level("WARNING"):
        assert await runner._handle_message(_event(adapter, **event_kwargs)) is None
    assert called == {
        "queued": False,
        "scaled": False,
        "authorized": False,
        "plugin": False,
    }
    _assert_no_raw_access_values(_rendered_logs(caplog))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case,reason",
    [
        ("missing_context", "missing_resolved_access_context"),
        ("stale_context", "resolved_access_context_mismatch"),
        ("chat_mismatch", "internal_delivery_target_mismatch"),
        ("route_account_mismatch", "internal_route_account_mismatch"),
        ("profile_mismatch", "profile_route_mismatch"),
        ("malformed_profile_route", "malformed_profile_route"),
    ],
)
async def test_registry_internal_denials_stop_before_gateway_downstream(
    monkeypatch,
    caplog,
    case,
    reason,
):
    registry = _registry()
    runner = _runner(registry)
    adapter = _Adapter(runner=runner)
    event = _event(adapter)
    event.internal = True
    if case == "missing_context":
        event.source.resolved_access_context = None
    elif case == "stale_context":
        stale_registry = _registry(user_id="u2")
        event.source.resolved_access_context = stale_registry.resolve(_identity("u2"))
    else:
        event.source.resolved_access_context = registry.resolve(_identity("u1"))
        if case == "chat_mismatch":
            event.source.chat_id = "other-chat"
        elif case == "route_account_mismatch":
            event.source.route_account = "wrong-account"
        elif case == "profile_mismatch":
            event.source.profile = "other-profile"
        elif case == "malformed_profile_route":
            event.source.profile = 123
    called = _guard_denied_downstream(monkeypatch, runner)

    with caplog.at_level("WARNING"):
        assert await runner._handle_message(event) is None

    assert called == {
        "queued": False,
        "scaled": False,
        "authorized": False,
        "plugin": False,
    }
    rendered = _rendered_logs(caplog)
    assert reason in rendered
    _assert_no_raw_access_values(rendered)


@pytest.mark.asyncio
async def test_registry_prebound_external_context_is_idempotent_and_does_not_resolve_twice():
    registry = _registry()

    class CountingRegistry:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.resolve_calls = 0

        def resolve(self, identity):
            self.resolve_calls += 1
            return self.wrapped.resolve(identity)

        def validate_resolved_context(self, context):
            return self.wrapped.validate_resolved_context(context)

    counting_registry = CountingRegistry(registry)
    runner = _runner(counting_registry)
    adapter = _Adapter(runner=runner)
    event = _event(adapter)

    assert runner._allow_access_registry_ingress(event)
    assert event.source.resolved_access_context is not None
    assert event.source.profile == "family-profile"
    assert event.source._trusted_transport_identity_fingerprint

    assert runner._allow_access_registry_ingress(event)
    assert counting_registry.resolve_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    ["account", "chat", "user", "thread", "platform"],
)
async def test_registry_prebound_context_mismatch_denies_external_before_downstream(
    monkeypatch,
    caplog,
    field,
):
    registry = _registry()
    runner = _runner(registry)
    adapter = _Adapter(runner=runner)
    event = _event(adapter)
    assert runner._allow_access_registry_ingress(event)
    if field == "account":
        event.source.route_account = "wrong-account"
    elif field == "chat":
        event.source.chat_id = "other-chat"
    elif field == "user":
        event.source.user_id = "u2"
    elif field == "thread":
        event.source.thread_id = "thread-1"
    elif field == "platform":
        event.source.platform = Platform.DISCORD
    called = _guard_denied_downstream(monkeypatch, runner)

    with caplog.at_level("WARNING"):
        assert await runner._handle_message(event) is None

    assert called == {
        "queued": False,
        "scaled": False,
        "authorized": False,
        "plugin": False,
    }
    rendered = _rendered_logs(caplog)
    assert "trusted_transport_identity_mismatch" in rendered
    _assert_no_raw_access_values(rendered)


@pytest.mark.asyncio
async def test_registry_shared_room_initial_resolve_stamps_marker_and_second_call_is_idempotent():
    registry = _shared_room_registry()

    class CountingRegistry:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.resolve_calls = 0

        def resolve(self, identity):
            self.resolve_calls += 1
            return self.wrapped.resolve(identity)

        def validate_resolved_context(self, context):
            return self.wrapped.validate_resolved_context(context)

    counting_registry = CountingRegistry(registry)
    runner = _runner(counting_registry)
    adapter = _Adapter(runner=runner)
    event = _event(adapter, user_id="u1", chat_id="room-1", chat_type="group")

    assert runner._allow_access_registry_ingress(event)
    assert event.source.resolved_access_context.role_id == "shared_room"
    assert event.source._trusted_transport_identity_fingerprint

    assert runner._allow_access_registry_ingress(event)
    assert counting_registry.resolve_calls == 1


@pytest.mark.asyncio
async def test_registry_shared_room_changed_participant_denies_external_before_downstream(
    monkeypatch,
    caplog,
):
    runner = _runner(_shared_room_registry())
    adapter = _Adapter(runner=runner)
    event = _event(adapter, user_id="u1", chat_id="room-1", chat_type="group")
    assert runner._allow_access_registry_ingress(event)
    event.source.user_id = "u2"
    called = _guard_denied_downstream(monkeypatch, runner)

    with caplog.at_level("WARNING"):
        assert await runner._handle_message(event) is None

    assert called == {
        "queued": False,
        "scaled": False,
        "authorized": False,
        "plugin": False,
    }
    rendered = _rendered_logs(caplog)
    assert "trusted_transport_identity_mismatch" in rendered
    _assert_no_raw_access_values(rendered)


@pytest.mark.asyncio
async def test_registry_shared_room_copied_context_without_marker_denies_external_ingress(
    monkeypatch,
    caplog,
):
    registry = _shared_room_registry()
    runner = _runner(registry)
    adapter = _Adapter(runner=runner)
    event = _event(adapter, user_id="u1", chat_id="room-1", chat_type="group")
    event.source.resolved_access_context = registry.resolve(
        _identity("u1", chat_id="room-1", peer_kind="group")
    )
    event.source.profile = "family-profile"
    called = _guard_denied_downstream(monkeypatch, runner)

    with caplog.at_level("WARNING"):
        assert await runner._handle_message(event) is None

    assert called == {
        "queued": False,
        "scaled": False,
        "authorized": False,
        "plugin": False,
    }
    rendered = _rendered_logs(caplog)
    assert "missing_trusted_transport_identity" in rendered
    _assert_no_raw_access_values(rendered)


@pytest.mark.asyncio
async def test_registry_with_multiplex_off_denies_before_resolution_and_downstream(
    monkeypatch,
    caplog,
):
    runner = _runner(_ExplodingRegistry())
    runner.config.multiplex_profiles = False
    adapter = _Adapter(runner=runner)
    event = _event(adapter)
    scaled = False
    authorized = False

    def scale():
        nonlocal scaled
        scaled = True

    def auth(source):
        nonlocal authorized
        authorized = True
        return True

    async def downstream(*args, **kwargs):
        raise AssertionError("denied ingress must not reach the agent")

    runner._scale_to_zero_note_real_inbound = scale
    runner._is_user_authorized = auth
    plugin_called = False

    def plugin_hook(*args, **kwargs):
        nonlocal plugin_called
        plugin_called = True
        return []

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", plugin_hook)
    monkeypatch.setattr(GatewayRunner, "_handle_message_with_agent", downstream)

    with caplog.at_level("WARNING"):
        assert await runner._handle_message(event) is None

    assert getattr(event.source, "resolved_access_context", None) is None
    assert event.source.profile in (None, "")
    assert scaled is False
    assert authorized is False
    assert plugin_called is False
    rendered_logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "access_registry_requires_multiplex" in rendered_logs
    _assert_no_raw_access_values(rendered_logs)


@pytest.mark.asyncio
async def test_registry_internal_event_no_longer_bypasses_configured_registry_when_multiplex_off():
    runner = _runner(_ExplodingRegistry())
    runner.config.multiplex_profiles = False
    adapter = _Adapter(runner=runner)
    event = _event(adapter)
    event.internal = True

    assert runner._allow_access_registry_ingress(event) is False
    assert getattr(event.source, "resolved_access_context", None) is None


@pytest.mark.asyncio
async def test_registry_none_internal_legacy_behavior_remains_allowed():
    runner = _runner(None)
    runner.config.multiplex_profiles = False
    adapter = _Adapter(runner=runner)
    event = _event(adapter)
    event.internal = True

    assert runner._allow_access_registry_ingress(event) is True
    assert getattr(event.source, "resolved_access_context", None) is None


@pytest.mark.asyncio
async def test_registry_denies_profile_route_mismatch_before_downstream(monkeypatch):
    runner = _runner(
        _registry(),
        profile_routes=[
            ProfileRoute(
                name="family-route",
                platform="telegram",
                profile="other-profile",
                account=ACCOUNT,
                peer_kind="dm",
                user_id="u1",
            )
        ],
    )
    adapter = _Adapter(runner=runner)
    runner._scale_to_zero_note_real_inbound = lambda: pytest.fail("scale gate ran")
    runner._is_user_authorized = lambda source: pytest.fail("auth gate ran")
    monkeypatch.setattr(
        "hermes_cli.plugins.invoke_hook",
        lambda *args, **kwargs: pytest.fail("plugin hook ran"),
    )
    monkeypatch.setattr(
        GatewayRunner,
        "_handle_message_with_agent",
        lambda *args, **kwargs: pytest.fail("agent downstream ran"),
    )

    assert await runner._handle_message(_event(adapter)) is None
