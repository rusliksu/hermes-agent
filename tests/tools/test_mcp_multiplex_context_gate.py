"""Regression coverage for multiplex MCP registration access gating."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_multiplex_context(monkeypatch, tmp_path):
    from agent.secret_scope import set_multiplex_active
    from gateway.session_context import reset_session_vars

    monkeypatch.setattr(
        "hermes_cli.profiles.get_profile_dir",
        lambda profile_id: tmp_path / "profiles" / str(profile_id),
    )
    set_multiplex_active(False)
    reset_session_vars()
    yield
    set_multiplex_active(False)
    reset_session_vars()


def _enable_multiplex():
    from agent.secret_scope import set_multiplex_active

    set_multiplex_active(True)


def _strict_access_context():
    from gateway.access_registry import DeliveryTarget, ResolvedAccessContext

    return ResolvedAccessContext(
        principal_id="principal-alpha",
        role_id="family",
        profile_id="profile-alpha",
        conversation_scope="dm:alpha",
        capabilities=frozenset({"mcp"}),
        delivery_target=DeliveryTarget(
            platform="telegram",
            account="bot-main",
            peer_kind="dm",
            chat_id="chat-alpha",
        ),
    )


def test_unscoped_discover_returns_empty_before_config_load(monkeypatch):
    from tools import mcp_tool

    _enable_multiplex()
    monkeypatch.setattr(mcp_tool, "_MCP_AVAILABLE", True)

    called = {"load": 0}

    def _load_config():
        called["load"] += 1
        raise AssertionError("config load must not run")

    monkeypatch.setattr(mcp_tool, "_load_mcp_config", _load_config)

    assert mcp_tool.discover_mcp_tools() == []
    assert called["load"] == 0


def test_unscoped_registration_returns_empty_before_filter_loop_or_spawn(monkeypatch):
    from tools import mcp_tool

    _enable_multiplex()
    monkeypatch.setattr(mcp_tool, "_MCP_AVAILABLE", True)

    side_effects: list[str] = []

    def _record(name):
        def _inner(*_args, **_kwargs):
            side_effects.append(name)
            raise AssertionError(f"{name} must not run")

        return _inner

    monkeypatch.setattr(mcp_tool, "_filter_suspicious_mcp_servers", _record("filter"))
    monkeypatch.setattr(mcp_tool, "_ensure_mcp_loop", _record("loop"))
    monkeypatch.setattr(mcp_tool, "_discover_and_register_server", _record("spawn"))

    assert mcp_tool.register_mcp_servers({"demo": {"command": "demo-mcp"}}) == []
    assert side_effects == []


def test_invalid_bound_context_denies_before_config_load(monkeypatch):
    from gateway.session_context import bind_resolved_access_context
    from tools import mcp_tool

    _enable_multiplex()
    monkeypatch.setattr(mcp_tool, "_MCP_AVAILABLE", True)

    called = {"load": 0}

    def _load_config():
        called["load"] += 1
        raise AssertionError("config load must not run")

    monkeypatch.setattr(mcp_tool, "_load_mcp_config", _load_config)

    with bind_resolved_access_context({"profile_id": "profile-alpha"}):
        assert mcp_tool.discover_mcp_tools() == []
    assert called["load"] == 0


def test_strict_bound_context_reaches_registration_filtering(monkeypatch):
    from gateway.session_context import bind_resolved_access_context
    from tools import mcp_tool

    _enable_multiplex()
    monkeypatch.setattr(mcp_tool, "_MCP_AVAILABLE", True)

    calls = {"filter": 0}

    def _filter(servers):
        calls["filter"] += 1
        return {}

    monkeypatch.setattr(mcp_tool, "_filter_suspicious_mcp_servers", _filter)

    with bind_resolved_access_context(_strict_access_context()):
        assert mcp_tool.register_mcp_servers({"demo": {"command": "demo-mcp"}}) == []
    assert calls["filter"] == 1


@pytest.mark.asyncio
async def test_http_oauth_multiplex_missing_captured_pool_denies_before_manager(monkeypatch):
    from tools import mcp_tool

    _enable_multiplex()
    monkeypatch.setattr(mcp_tool, "_MCP_HTTP_AVAILABLE", True)
    called = {"manager": 0}

    def _manager():
        called["manager"] += 1
        raise AssertionError("oauth manager must not be touched")

    monkeypatch.setattr("tools.mcp_oauth_manager.get_manager", _manager)
    server = mcp_tool.MCPServerTask("demo")
    server._auth_type = "oauth"

    with pytest.raises(ValueError, match="profile-bound-mcp-runtime-pool-missing"):
        await server._run_http({"url": "https://mcp.example/mcp", "auth": "oauth"})

    assert called["manager"] == 0


def test_auth_recovery_multiplex_missing_pool_denies_before_manager(monkeypatch):
    import json

    from tools import mcp_tool

    _enable_multiplex()
    called = {"manager": 0}

    def _manager():
        called["manager"] += 1
        raise AssertionError("oauth manager must not be touched")

    monkeypatch.setattr(mcp_tool, "_is_auth_error", lambda exc: True)
    monkeypatch.setattr("tools.mcp_oauth_manager.get_manager", _manager)

    result = json.loads(
        mcp_tool._handle_auth_error_and_retry(
            "demo",
            RuntimeError("401"),
            lambda: '{"result":"retry"}',
            "tools/call echo",
            pool=None,
        )
    )

    assert result == {"error": "profile-bound-mcp-runtime-pool-missing"}
    assert called["manager"] == 0


def test_legacy_non_multiplex_discover_still_reaches_config_load(monkeypatch):
    from tools import mcp_tool

    monkeypatch.setattr(mcp_tool, "_MCP_AVAILABLE", True)

    calls = {"load": 0}

    def _load_config():
        calls["load"] += 1
        return {}

    monkeypatch.setattr(mcp_tool, "_load_mcp_config", _load_config)

    assert mcp_tool.discover_mcp_tools() == []
    assert calls["load"] == 1
