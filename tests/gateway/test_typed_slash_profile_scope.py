"""Typed multiplex slash commands use only the resolved profile runtime."""

from types import SimpleNamespace

import pytest
import yaml

from gateway.access_registry import (
    AccessRegistry,
    DeliveryTarget,
    PrincipalBinding,
    RolePolicy,
    TransportIdentity,
)
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import SessionSource
from hermes_constants import get_hermes_home


ACCOUNT = "family-bot"
PROFILE = "family-alpha"


def _write_config(home, config):
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump(config),
        encoding="utf-8",
    )


def _read_config(home):
    return yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))


def _runner():
    identity = _identity()
    target = DeliveryTarget(
        platform="telegram",
        account=ACCOUNT,
        peer_kind="dm",
        chat_id="family-chat",
    )
    registry = AccessRegistry(
        roles={
            "family_standard": RolePolicy(
                "family_standard",
                frozenset({"chat"}),
            )
        },
        profiles=frozenset({PROFILE}),
        principal_bindings=(
            PrincipalBinding(
                principal_id="principal-family-alpha",
                role_id="family_standard",
                profile_id=PROFILE,
                transport_identity=identity,
                conversation_scope="private",
                delivery_target=target,
            ),
        ),
        scope_capabilities={"private": frozenset({"chat"})},
        backend_capabilities=frozenset({"chat"}),
    )
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(
                enabled=True,
                token="test-token",
            )
        },
        multiplex_profiles=True,
    )
    runner.access_registry = registry
    runner._single_principal_policy = runner.config.single_principal
    runner.session_store = None
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
    runner.hooks = SimpleNamespace(emit_collect=_empty_hook_results)
    return runner


def _identity():
    return TransportIdentity(
        platform="telegram",
        account=ACCOUNT,
        peer_kind="dm",
        user_id="family-chat",
        chat_id="family-chat",
    )


async def _empty_hook_results(*args, **kwargs):
    return []


def _event(text="/verbose"):
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="family-chat",
            chat_type="dm",
            user_id="family-chat",
            route_account=ACCOUNT,
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_text", "rewrite_to_slash"),
    [
        pytest.param("/verbose", False, id="direct"),
        pytest.param("make this verbose", True, id="pre_dispatch_rewrite"),
    ],
)
async def test_typed_slash_reads_and_writes_only_resolved_profile(
    tmp_path,
    monkeypatch,
    event_text,
    rewrite_to_slash,
):
    owner_home = tmp_path / "owner"
    profile_home = owner_home / "profiles" / PROFILE
    owner_config = {
        "display": {
            "tool_progress_command": False,
            "platforms": {"telegram": {"tool_progress": "off"}},
        }
    }
    profile_config = {
        "display": {
            "tool_progress_command": True,
            "platforms": {"telegram": {"tool_progress": "off"}},
        }
    }
    _write_config(owner_home, owner_config)
    _write_config(profile_home, profile_config)
    monkeypatch.setenv("HERMES_HOME", str(owner_home))
    monkeypatch.setattr("gateway.run._hermes_home", owner_home)

    def invoke_hook(hook_name, **kwargs):
        assert hook_name == "pre_gateway_dispatch"
        if rewrite_to_slash:
            assert kwargs["event"].text == event_text
            return [{"action": "rewrite", "text": "/verbose"}]
        return []

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", invoke_hook)
    monkeypatch.setattr("tools.slash_confirm.get_pending", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "tools.approval.has_blocking_approval",
        lambda *args, **kwargs: False,
    )

    result = await _runner()._handle_message(_event(event_text))

    assert "not enabled" not in result.lower()
    assert _read_config(owner_home) == owner_config
    assert (
        _read_config(profile_home)["display"]["platforms"]["telegram"]["tool_progress"]
        == "new"
    )


@pytest.mark.asyncio
async def test_typed_slash_confirm_callback_reenters_resolved_profile_scope(
    tmp_path,
    monkeypatch,
):
    from gateway.run import _profile_runtime_scope
    from tools import slash_confirm

    owner_home = tmp_path / "owner"
    profile_home = owner_home / "profiles" / PROFILE
    _write_config(owner_home, {})
    _write_config(profile_home, {})
    monkeypatch.setenv("HERMES_HOME", str(owner_home))
    monkeypatch.setattr("gateway.run._hermes_home", owner_home)

    runner = _runner()
    source = _event().source
    source.resolved_access_context = runner.access_registry.resolve(_identity())
    source.profile = PROFILE
    runner._session_key_for_source = lambda _source: "typed-session"
    runner._adapter_for_source = lambda _source: None
    runner._thread_metadata_for_source = lambda *_args: None
    runner._reply_anchor_for_event = lambda _event: None

    async def handler(choice):
        assert choice == "once"
        home = get_hermes_home()
        (home / "callback-write.txt").write_text("profile", encoding="utf-8")
        return home.name

    with _profile_runtime_scope(profile_home):
        await runner._request_slash_confirm(
            event=MessageEvent(text="/reload-mcp", source=source),
            command="reload-mcp",
            title="/reload-mcp",
            message="confirm",
            handler=handler,
        )

    result = await slash_confirm.resolve("typed-session", "1", "once")

    assert result == PROFILE
    assert (profile_home / "callback-write.txt").read_text(encoding="utf-8") == "profile"
    assert not (owner_home / "callback-write.txt").exists()
