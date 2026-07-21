import pytest

from gateway.access_registry import (
    AccessRegistry,
    DeliveryTarget,
    PrincipalBinding,
    RolePolicy,
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


def _identity(user_id="u1", *, account=ACCOUNT, chat_id=None):
    return TransportIdentity(
        platform="telegram",
        account=account,
        peer_kind="dm",
        user_id=user_id,
        chat_id=user_id if chat_id is None else chat_id,
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


def _event(
    adapter,
    *,
    text="hello",
    user_id="u1",
    chat_id=None,
    profile=None,
    account=ACCOUNT,
):
    if account != ACCOUNT:
        adapter.config.extra["account"] = account
    source = adapter.build_source(
        chat_id=user_id if chat_id is None else chat_id,
        chat_type="dm",
        user_id=user_id,
    )
    if profile is not None:
        source.profile = profile
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=source,
    )


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
        assert type(source).from_dict(wire).resolved_access_context is None
        return "status ok"

    runner._handle_status_command = status_handler

    assert await runner._handle_message(_event(adapter, text="/status")) == "status ok"


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
    event_kwargs,
    registry,
):
    runner = _runner(registry)
    adapter = _Adapter(runner=runner)
    runner._startup_restore_in_progress = True
    queued = False
    scaled = False
    authorized = False

    def queue(event):
        nonlocal queued
        queued = True

    def scale():
        nonlocal scaled
        scaled = True

    def auth(source):
        nonlocal authorized
        authorized = True
        return True

    async def downstream(*args, **kwargs):
        raise AssertionError("denied ingress must not reach the agent")

    runner._queue_startup_restore_event = queue
    runner._scale_to_zero_note_real_inbound = scale
    runner._is_user_authorized = auth
    plugin_called = False

    def plugin_hook(*args, **kwargs):
        nonlocal plugin_called
        plugin_called = True
        return []

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", plugin_hook)
    monkeypatch.setattr(GatewayRunner, "_handle_message_with_agent", downstream)

    assert await runner._handle_message(_event(adapter, **event_kwargs)) is None
    assert queued is False
    assert scaled is False
    assert authorized is False
    assert plugin_called is False


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
