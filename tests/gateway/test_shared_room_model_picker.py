"""Regression coverage for registry-owned shared-room /model delivery."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.i18n import t
from gateway.access_registry import (
    AccessRegistry,
    DeliveryTarget,
    ParticipantIdentity,
    ResolvedAccessContext,
    RolePolicy,
    SharedScopeBinding,
    TransportIdentity,
)
from gateway.config import Platform
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import SessionSource


ACCOUNT = "shared-telegram-account"
CHAT = "shared-room"
TOPIC = "room-topic"
MEMBER = "room-member"
ROOM_PROFILE = "room-profile"


async def _inline_to_thread(func, /, *args, **kwargs):
    return func(*args, **kwargs)


@pytest.fixture(autouse=True)
def _inline_model_command_offloads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep this authorization regression independent of executor teardown."""
    monkeypatch.setattr("gateway.slash_commands.asyncio.to_thread", _inline_to_thread)


class _PickerAdapter:
    """Picker-capable default Telegram adapter with a server-owned account."""

    def __init__(self, account: str = ACCOUNT):
        self._account = account
        self.calls: list[dict] = []

    def _route_account(self) -> str:
        return self._account

    async def send_model_picker(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(success=True)


class _SessionStore:
    def __init__(self, session_id: str = "session-one"):
        self.session_id = session_id

    def peek_session_id(self, _session_key: str) -> str:
        return self.session_id

    def _cached_topic_preferences(self, _source: SessionSource) -> tuple[bool, dict]:
        return True, {}

    def get_topic_preferences(self, _source: SessionSource) -> dict:
        return {}


def _registry() -> AccessRegistry:
    capabilities = frozenset({"documents"})
    room_identity = TransportIdentity(
        platform="telegram",
        account=ACCOUNT,
        peer_kind="group",
        user_id=MEMBER,
        chat_id=CHAT,
        thread_id=TOPIC,
    )
    target = DeliveryTarget(
        platform="telegram",
        account=ACCOUNT,
        peer_kind="group",
        chat_id=CHAT,
        thread_id=TOPIC,
    )
    return AccessRegistry(
        roles={"shared_room": RolePolicy("shared_room", capabilities)},
        profiles=frozenset({ROOM_PROFILE}),
        shared_scope_bindings=(
            SharedScopeBinding(
                principal_id="shared-room-principal",
                role_id="shared_room",
                profile_id=ROOM_PROFILE,
                room_identity=room_identity,
                conversation_scope="shared-room-scope",
                delivery_target=target,
                participant_identities=(
                    ParticipantIdentity("telegram", ACCOUNT, MEMBER),
                ),
            ),
        ),
        scope_capabilities={"shared-room-scope": capabilities},
        backend_capabilities=capabilities,
    )


def _runner(
    adapter: object,
    *,
    access_registry: AccessRegistry | None = None,
    profile_adapters: dict[str, dict] | None = None,
    multiplex: bool = True,
) -> GatewayRunner:
    runner = object.__new__(GatewayRunner)
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._profile_adapters = profile_adapters or {}
    runner.access_registry = access_registry
    runner.config = SimpleNamespace(multiplex_profiles=multiplex)
    runner._resolve_profile_home_for_source = lambda _source: Path("/tmp/test-room-profile")
    runner._voice_mode = {}
    runner._session_model_overrides = {}
    runner._running_agents = {}
    runner.session_store = _SessionStore()
    return runner


def _shared_source(*, profile: str | None = None) -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id=CHAT,
        chat_type="group",
        user_id=MEMBER,
        thread_id=TOPIC,
        profile=profile,
        route_account=ACCOUNT,
    )


def _event(command: str, source: SessionSource) -> MessageEvent:
    return MessageEvent(
        text=command,
        message_type=MessageType.TEXT,
        source=source,
    )


def _bind_trusted_shared_context(runner: GatewayRunner, source: SessionSource) -> None:
    context = runner._resolve_access_context_for_source(source)
    source.resolved_access_context = context
    source.profile = context.profile_id


def _stub_provider_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "gateway.run._load_gateway_config",
        lambda: {"model": {"default": "current-model", "provider": "openrouter"}},
    )
    monkeypatch.setattr(
        "hermes_cli.model_switch.list_picker_providers",
        lambda **_kwargs: [
            {
                "slug": "openrouter",
                "name": "OpenRouter",
                "models": ["picker-model"],
                "total_models": 1,
                "is_current": True,
            }
        ],
    )
    monkeypatch.setattr(
        "hermes_cli.model_switch.list_authenticated_providers",
        lambda **_kwargs: [
            {
                "slug": "openrouter",
                "name": "OpenRouter",
                "models": ["fallback-model"],
                "total_models": 1,
                "is_current": True,
            }
        ],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["/model", "/model@pickerbot"])
async def test_registry_owned_shared_room_uses_default_telegram_picker(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    """A trusted room uses its registry-owned primary transport, not a fallback."""
    adapter = _PickerAdapter()
    runner = _runner(
        adapter,
        access_registry=_registry(),
        profile_adapters={ROOM_PROFILE: {}},
    )
    _stub_provider_lists(monkeypatch)
    source = _shared_source()
    _bind_trusted_shared_context(runner, source)
    event = _event(command, source)

    result = await runner._handle_model_command(event)

    assert event.get_command() == "model"
    assert result is None
    assert len(adapter.calls) == 1
    kwargs = adapter.calls[0]
    assert kwargs["initiator_user_id"] == source.user_id
    assert kwargs["metadata"]["thread_id"] == source.thread_id
    assert kwargs["allow_shared_lane_control"] is True
    validator = kwargs["is_state_current"]
    assert await validator() is True
    runner.session_store.session_id = "session-two"
    assert await validator() is False


@pytest.mark.asyncio
async def test_owner_dm_still_uses_its_primary_picker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _PickerAdapter()
    runner = _runner(adapter, multiplex=False)
    _stub_provider_lists(monkeypatch)
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="owner-dm",
        chat_type="dm",
        user_id="owner",
    )

    assert await runner._handle_model_command(_event("/model", source)) is None
    assert len(adapter.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    ["ordinary-secondary", "malformed-context", "unknown-context"],
)
async def test_control_delivery_never_uses_default_adapter_without_trusted_registry_room(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """Secondary authz stays fail-closed when control delivery is not proven."""
    adapter = _PickerAdapter()
    registry = _registry() if case != "ordinary-secondary" else None
    profile = "secondary-profile" if registry is None else ROOM_PROFILE
    runner = _runner(
        adapter,
        access_registry=registry,
        profile_adapters={profile: {}},
        multiplex=False,
    )
    _stub_provider_lists(monkeypatch)
    source = _shared_source(profile=profile)
    if case == "malformed-context":
        source.resolved_access_context = object()
    elif case == "unknown-context":
        source.resolved_access_context = ResolvedAccessContext(
            principal_id="untrusted-principal",
            role_id="shared_room",
            profile_id=ROOM_PROFILE,
            conversation_scope="unknown-scope",
            capabilities=frozenset({"documents"}),
            delivery_target=DeliveryTarget(
                platform="telegram",
                account=ACCOUNT,
                peer_kind="group",
                chat_id=CHAT,
                thread_id=TOPIC,
            ),
        )

    result = await runner._handle_model_command(_event("/model", source))

    assert runner._authorization_adapter(Platform.TELEGRAM, profile) is None
    assert isinstance(result, str)
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_text_fallback_caps_every_provider_and_reports_the_remainder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unavailable picker output never serializes an unbounded provider catalog."""
    runner = _runner(object(), multiplex=False)
    custom_models = [f"custom-model-{index}" for index in range(8)]
    built_in_models = [f"built-in-model-{index}" for index in range(8)]
    monkeypatch.setattr(
        "gateway.run._load_gateway_config",
        lambda: {"model": {"default": "current-model", "provider": "openrouter"}},
    )
    monkeypatch.setattr(
        "hermes_cli.model_switch.list_authenticated_providers",
        lambda **_kwargs: [
            {
                "slug": "custom:catalog",
                "name": "Configured catalog",
                "models": custom_models,
                "total_models": len(custom_models),
                "is_current": True,
                "is_user_defined": True,
            },
            {
                "slug": "openrouter",
                "name": "Built-in catalog",
                "models": built_in_models,
                "total_models": len(built_in_models),
                "is_current": False,
            },
        ],
    )
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="fallback-dm",
        chat_type="dm",
        user_id="owner",
    )

    result = await runner._handle_model_command(_event("/model", source))

    assert result is not None
    for catalog in (custom_models, built_in_models):
        for model in catalog[:5]:
            assert model in result
        for model in catalog[5:]:
            assert model not in result
    assert result.count(t("gateway.model.more_models_suffix", count=3)) == 2
