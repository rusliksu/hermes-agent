"""Profile-bound MCP runtime pool isolation."""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _reset_mcp_profile_pool_state():
    from agent.secret_scope import set_multiplex_active
    from gateway.session_context import reset_session_vars
    from tools import mcp_tool

    set_multiplex_active(False)
    reset_session_vars()
    mcp_tool._servers.clear()
    mcp_tool._server_error_counts.clear()
    mcp_tool._server_breaker_opened_at.clear()
    mcp_tool._parallel_safe_servers.clear()
    mcp_tool._mcp_tool_server_names.clear()
    mcp_tool._profile_pools.clear()
    yield
    set_multiplex_active(False)
    reset_session_vars()
    mcp_tool._servers.clear()
    mcp_tool._server_error_counts.clear()
    mcp_tool._server_breaker_opened_at.clear()
    mcp_tool._parallel_safe_servers.clear()
    mcp_tool._mcp_tool_server_names.clear()
    mcp_tool._profile_pools.clear()


def _ctx(profile_id: str, scope: str):
    from gateway.access_registry import DeliveryTarget, ResolvedAccessContext

    return ResolvedAccessContext(
        principal_id=f"principal-{profile_id}",
        role_id="family_standard",
        profile_id=profile_id,
        conversation_scope=scope,
        capabilities=frozenset({"mcp"}),
        delivery_target=DeliveryTarget(
            platform="telegram",
            account="bot-main",
            peer_kind="dm",
            chat_id=f"chat-{profile_id}",
        ),
    )


class _FakeSession:
    def __init__(self, label: str):
        self.label = label
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name, arguments=None):
        self.calls.append((name, dict(arguments or {})))
        return SimpleNamespace(
            content=[SimpleNamespace(text=self.label)],
            isError=False,
            structuredContent=None,
        )


def _server(label: str):
    session = _FakeSession(label)
    return SimpleNamespace(
        name="demo",
        session=session,
        _rpc_lock=asyncio.Lock(),
        _is_recycled_stdio=lambda: False,
    )


def _run_coro_inline(coro_or_factory, timeout=30):
    coro = coro_or_factory() if callable(coro_or_factory) else coro_or_factory
    return asyncio.run(coro)


def _install_server(context, server):
    from gateway.session_context import bind_resolved_access_context
    from tools import mcp_tool

    with bind_resolved_access_context(context):
        pool = mcp_tool._current_mcp_pool()
        assert pool is not None
        server._pool = pool
        pool.servers["demo"] = server
        return pool


def test_same_server_name_dispatches_to_current_profile_pool(monkeypatch):
    from agent.secret_scope import set_multiplex_active
    from gateway.session_context import bind_resolved_access_context
    from tools import mcp_tool

    set_multiplex_active(True)
    ctx_a = _ctx("profile-a", "dm:a")
    ctx_b = _ctx("profile-b", "dm:b")
    server_a = _server("from-a")
    server_b = _server("from-b")
    _install_server(ctx_a, server_a)
    _install_server(ctx_b, server_b)

    monkeypatch.setattr(mcp_tool, "_run_on_mcp_loop", _run_coro_inline)
    handler = mcp_tool._make_tool_handler("demo", "echo", 120)

    with bind_resolved_access_context(ctx_a):
        assert json.loads(handler({"x": "a"})) == {"result": "from-a"}
    with bind_resolved_access_context(ctx_b):
        assert json.loads(handler({"x": "b"})) == {"result": "from-b"}

    assert server_a.session.calls == [("echo", {"x": "a"})]
    assert server_b.session.calls == [("echo", {"x": "b"})]


def test_missing_multiplex_context_cannot_see_legacy_or_profile_pools(monkeypatch):
    from agent.secret_scope import set_multiplex_active
    from tools import mcp_tool

    set_multiplex_active(True)
    legacy = _server("legacy")
    profile = _server("profile")
    mcp_tool._servers["demo"] = legacy
    _install_server(_ctx("profile-a", "dm:a"), profile)

    monkeypatch.setattr(mcp_tool, "_run_on_mcp_loop", _run_coro_inline)
    handler = mcp_tool._make_tool_handler("demo", "echo", 120)
    result = json.loads(handler({}))

    assert "not connected" in result["error"]
    assert mcp_tool._server_pool(legacy) is None
    assert legacy.session.calls == []
    assert profile.session.calls == []


@pytest.mark.asyncio
async def test_bound_multiplex_discovery_passes_and_stores_exact_pool(monkeypatch):
    from agent.secret_scope import set_multiplex_active
    from gateway.session_context import bind_resolved_access_context
    from tools import mcp_tool

    set_multiplex_active(True)
    context = _ctx("profile-a", "dm:a")
    config = {"connect_timeout": 1}
    observed = {}

    async def fake_connect_server(name, server_config, pool):
        observed["connect"] = (name, server_config, pool)
        server = SimpleNamespace(
            name=name,
            _pool=pool,
            _tools=[],
            _registered_tool_names=[],
            tool_timeout=120,
        )
        observed["server"] = server
        return server

    def fake_register_server_tools(name, server, server_config):
        observed["register"] = (name, server, server_config, server._pool)
        assert server._pool is observed["connect"][2]
        return ["mcp__demo__echo"]

    monkeypatch.setattr(mcp_tool, "_connect_server", fake_connect_server)
    monkeypatch.setattr(mcp_tool, "_register_server_tools", fake_register_server_tools)

    with bind_resolved_access_context(context):
        pool = mcp_tool._current_mcp_pool()
        assert pool is not None
        registered = await mcp_tool._discover_and_register_server("demo", config, pool)

    server = observed["server"]
    assert observed["connect"] == ("demo", config, pool)
    assert observed["register"] == ("demo", server, config, pool)
    assert server._pool is pool
    assert pool.servers["demo"] is server
    assert registered == ["mcp__demo__echo"]


def test_breaker_and_parallel_safe_state_are_profile_pool_local(monkeypatch):
    from agent.secret_scope import set_multiplex_active
    from gateway.session_context import bind_resolved_access_context
    from tools import mcp_tool

    set_multiplex_active(True)
    ctx_a = _ctx("profile-a", "dm:a")
    ctx_b = _ctx("profile-b", "dm:b")
    server_a = _server("from-a")
    server_b = _server("from-b")
    pool_a = _install_server(ctx_a, server_a)
    pool_b = _install_server(ctx_b, server_b)
    pool_a.error_counts["demo"] = mcp_tool._CIRCUIT_BREAKER_THRESHOLD
    pool_a.breaker_opened_at["demo"] = time.monotonic()
    pool_a.parallel_safe_servers.add("demo")
    pool_b.parallel_safe_servers.discard("demo")
    mcp_tool._mcp_tool_server_names["mcp__demo__echo"] = "demo"

    monkeypatch.setattr(mcp_tool, "_run_on_mcp_loop", _run_coro_inline)
    handler = mcp_tool._make_tool_handler("demo", "echo", 120)

    with bind_resolved_access_context(ctx_a):
        assert "unreachable" in json.loads(handler({}))["error"]
        assert mcp_tool.is_mcp_tool_parallel_safe("mcp__demo__echo") is True
    with bind_resolved_access_context(ctx_b):
        assert json.loads(handler({})) == {"result": "from-b"}
        assert mcp_tool.is_mcp_tool_parallel_safe("mcp__demo__echo") is False

    assert server_a.session.calls == []
    assert server_b.session.calls == [("echo", {})]
