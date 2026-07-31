from __future__ import annotations

import contextlib
import sys
from types import SimpleNamespace

from gateway.access_registry import (
    AccessRegistry,
    DeliveryTarget,
    PrincipalBinding,
    RolePolicy,
    TransportIdentity,
)
from gateway.config import GatewayConfig, HomeChannel, Platform, PlatformConfig
from hermes_cli.cli_commands_mixin import CLICommandsMixin


def _registry(*, chat_id: str = "u1") -> AccessRegistry:
    identity = TransportIdentity(
        platform="telegram",
        account="bot-a",
        peer_kind="dm",
        user_id=chat_id,
        chat_id=chat_id,
    )
    return AccessRegistry(
        roles={"family_standard": RolePolicy("family_standard", frozenset({"public_web"}))},
        profiles=frozenset({"family-alpha"}),
        principal_bindings=(
            PrincipalBinding(
                principal_id="principal-alpha",
                role_id="family_standard",
                profile_id="family-alpha",
                transport_identity=identity,
                conversation_scope="private",
                delivery_target=DeliveryTarget(
                    platform="telegram",
                    account="bot-a",
                    peer_kind="dm",
                    chat_id=chat_id,
                    thread_id=None,
                ),
            ),
        ),
        scope_capabilities={"private": frozenset({"public_web"})},
        backend_capabilities=frozenset({"public_web"}),
    )


def _config(*, registry: AccessRegistry | None, chat_id: str = "u1") -> GatewayConfig:
    return GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(
                enabled=True,
                token="***",
                extra={"account": "bot-a"},
                home_channel=HomeChannel(
                    platform=Platform.TELEGRAM,
                    chat_id=chat_id,
                    name="Telegram DM",
                ),
            )
        },
        multiplex_profiles=registry is not None,
        access_registry=registry,
    )


class _FakeDB:
    def __init__(self):
        self.requested = None

    def get_session(self, _session_id):
        return {"title": "CLI work"}

    def set_session_title(self, *_args):
        raise AssertionError("existing session should not need a stub")

    def request_handoff(self, session_id, platform, *, resolved_access_context=None):
        self.requested = (session_id, platform, resolved_access_context)
        return True

    def get_handoff_state(self, _session_id):
        return {"state": "failed", "platform": "telegram", "error": "stop polling"}


class _CLI(CLICommandsMixin):
    session_id = "cli-session"
    _agent_running = False

    def __init__(self, db):
        self._session_db = db


def test_cli_handoff_registry_denies_before_db_mutation(monkeypatch):
    db = _FakeDB()
    printed = []
    monkeypatch.setitem(sys.modules, "cli", SimpleNamespace(_cprint=printed.append))
    monkeypatch.setattr(
        "gateway.config.load_gateway_config",
        lambda: _config(registry=_registry(chat_id="other"), chat_id="u1"),
    )

    assert _CLI(db)._handle_handoff_command("/handoff telegram") is True

    assert db.requested is None
    assert any("Handoff denied:" in line for line in printed)


def test_cli_handoff_registry_passes_context_to_db(monkeypatch):
    db = _FakeDB()
    monkeypatch.setitem(sys.modules, "cli", SimpleNamespace(_cprint=lambda _line: None))
    monkeypatch.setattr(
        "gateway.config.load_gateway_config",
        lambda: _config(registry=_registry(), chat_id="u1"),
    )

    assert _CLI(db)._handle_handoff_command("/handoff telegram") is True

    assert db.requested[0:2] == ("cli-session", "telegram")
    assert db.requested[2].delivery_target.chat_id == "u1"
    assert db.requested[2].delivery_target.account == "bot-a"


def test_tui_handoff_registry_denies_before_db_mutation(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import tui_gateway.server as server

    class _DB(_FakeDB):
        def request_handoff(self, *args, **kwargs):
            raise AssertionError("request_handoff must not run")

    monkeypatch.setattr(
        "gateway.config.load_gateway_config",
        lambda: _config(registry=_registry(chat_id="other"), chat_id="u1"),
    )
    monkeypatch.setattr(server, "_ensure_session_db_row", lambda _session: None)
    monkeypatch.setattr(server, "_session_db", lambda _session: contextlib.nullcontext(_DB()))
    monkeypatch.setitem(
        server._sessions,
        "s1",
        {"session_key": "cli-session", "running": False},
    )
    try:
        response = server._methods["handoff.request"]("r1", {"session_id": "s1", "platform": "telegram"})
    finally:
        server._sessions.pop("s1", None)

    assert response["error"]["code"] == 4028
    assert response["error"]["message"].startswith("handoff denied:")


def test_tui_handoff_registry_passes_context_to_db(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import tui_gateway.server as server

    db = _FakeDB()
    monkeypatch.setattr(
        "gateway.config.load_gateway_config",
        lambda: _config(registry=_registry(), chat_id="u1"),
    )
    monkeypatch.setattr(server, "_ensure_session_db_row", lambda _session: None)
    monkeypatch.setattr(server, "_session_db", lambda _session: contextlib.nullcontext(db))
    monkeypatch.setitem(
        server._sessions,
        "s1",
        {"session_key": "cli-session", "running": False},
    )
    try:
        response = server._methods["handoff.request"]("r1", {"session_id": "s1", "platform": "telegram"})
    finally:
        server._sessions.pop("s1", None)

    assert "error" not in response
    assert db.requested[0:2] == ("cli-session", "telegram")
    assert db.requested[2].delivery_target.chat_id == "u1"
