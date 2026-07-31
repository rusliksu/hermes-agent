"""Profile-bound MCP runtime pool isolation."""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from tools.mcp_profile_policy import typed_mcp_config_fingerprint


@pytest.fixture(autouse=True)
def _reset_mcp_profile_pool_state(monkeypatch, tmp_path):
    from agent.secret_scope import set_multiplex_active
    from gateway.session_context import reset_session_vars
    import model_tools
    from tools import mcp_tool
    from tools.registry import registry

    monkeypatch.setattr(
        "hermes_cli.profiles.get_profile_dir",
        lambda profile_id: tmp_path / "profiles" / str(profile_id),
    )
    set_multiplex_active(False)
    reset_session_vars()
    model_tools._tool_defs_cache.clear()
    for tool_name in list(registry.get_all_tool_names()):
        if tool_name.startswith("mcp__"):
            registry.deregister(tool_name)
    mcp_tool._servers.clear()
    mcp_tool._server_error_counts.clear()
    mcp_tool._server_breaker_opened_at.clear()
    mcp_tool._parallel_safe_servers.clear()
    mcp_tool._mcp_tool_server_names.clear()
    mcp_tool._legacy_pool.config_snapshots.clear()
    mcp_tool._legacy_pool.credential_ref_metadata.clear()
    mcp_tool._legacy_pool.allowed_tool_names.clear()
    mcp_tool._profile_pools.clear()
    yield
    set_multiplex_active(False)
    reset_session_vars()
    model_tools._tool_defs_cache.clear()
    for tool_name in list(registry.get_all_tool_names()):
        if tool_name.startswith("mcp__"):
            registry.deregister(tool_name)
    mcp_tool._servers.clear()
    mcp_tool._server_error_counts.clear()
    mcp_tool._server_breaker_opened_at.clear()
    mcp_tool._parallel_safe_servers.clear()
    mcp_tool._mcp_tool_server_names.clear()
    mcp_tool._legacy_pool.config_snapshots.clear()
    mcp_tool._legacy_pool.credential_ref_metadata.clear()
    mcp_tool._legacy_pool.allowed_tool_names.clear()
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
        _tools=[],
        _config={"tools": {"include": ["echo"], "resources": False, "prompts": False}},
        tool_timeout=120,
        _rpc_lock=asyncio.Lock(),
        _is_recycled_stdio=lambda: False,
    )


def _tool(name: str, description: str, properties: dict | None = None):
    return SimpleNamespace(
        name=name,
        description=description,
        inputSchema={
            "type": "object",
            "properties": properties or {},
        },
    )


def _run_coro_inline(coro_or_factory, timeout=30):
    coro = coro_or_factory() if callable(coro_or_factory) else coro_or_factory
    return asyncio.run(coro)


class _ListToolsSession:
    def __init__(self, tools, observed):
        self.tools = list(tools)
        self.observed = observed
        self.calls = 0

    async def list_tools(self, cursor=None):
        from gateway.session_context import get_resolved_access_context
        from tools import mcp_tool

        self.calls += 1
        context = get_resolved_access_context(None)
        self.observed.append(
            (
                getattr(context, "profile_id", None),
                mcp_tool._current_mcp_pool(create=False),
            )
        )
        return SimpleNamespace(tools=list(self.tools), nextCursor=None)


def _install_server(context, server):
    from gateway.session_context import bind_resolved_access_context
    from tools import mcp_tool

    with bind_resolved_access_context(context):
        pool = mcp_tool._current_mcp_pool()
        assert pool is not None
        server._pool = pool
        pool.config_snapshots["demo"] = typed_mcp_config_fingerprint(server._config)
        pool.allowed_tool_names["demo"] = frozenset({"echo"})
        pool.servers["demo"] = server
        return pool


def _register_demo_server(context, tools):
    from gateway.session_context import bind_resolved_access_context
    from tools import mcp_tool

    server = _server(f"from-{context.profile_id}")
    server._tools = list(tools)
    server._config = {
        "tools": {
            "include": [tool.name for tool in tools],
            "resources": False,
            "prompts": False,
        }
    }
    with bind_resolved_access_context(context):
        pool = _install_server(context, server)
        server._registered_tool_names = mcp_tool._register_server_tools(
            "demo",
            server,
            server._config,
        )
        return pool, server


def _mcp_defs(context, enabled_toolsets=None):
    from gateway.session_context import bind_resolved_access_context
    from model_tools import get_tool_definitions

    with bind_resolved_access_context(context):
        definitions = get_tool_definitions(
            enabled_toolsets=enabled_toolsets,
            quiet_mode=True,
            skip_tool_search_assembly=True,
        )
    return {
        d["function"]["name"]: d["function"]
        for d in definitions
        if d["function"]["name"].startswith("mcp__")
    }


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
        pool.config_snapshots["demo"] = typed_mcp_config_fingerprint(config)
        registered = await mcp_tool._discover_and_register_server("demo", config, pool)

    server = observed["server"]
    assert observed["connect"] == ("demo", config, pool)
    assert observed["register"] == ("demo", server, config, pool)
    assert server._pool is pool
    assert pool.servers["demo"] is server
    assert registered == ["mcp__demo__echo"]


@pytest.mark.asyncio
async def test_background_refresh_uses_captured_context_and_pool_despite_ambient_profile():
    from agent.secret_scope import set_multiplex_active
    from gateway.session_context import bind_resolved_access_context
    from tools import mcp_tool

    set_multiplex_active(True)
    ctx_a = _ctx("profile-a", "dm:a")
    ctx_b = _ctx("profile-b", "dm:b")
    observed = []

    with bind_resolved_access_context(ctx_a):
        pool_a = mcp_tool._current_mcp_pool()
        assert pool_a is not None
        server = mcp_tool.MCPServerTask("demo")
        server._pool = pool_a
        server._config = {
            "tools": {"include": ["fresh"], "resources": False, "prompts": False}
        }
        server.session = _ListToolsSession([_tool("fresh", "Fresh")], observed)
        server._capture_resolved_access_context()
        pool_a.servers["demo"] = server

    with bind_resolved_access_context(ctx_b):
        pool_b = mcp_tool._current_mcp_pool()
        assert pool_b is not None
        await server._refresh_tools_task()

    tool_name = mcp_tool.mcp_prefixed_tool_name("demo", "fresh")
    assert observed == [("profile-a", pool_a)]
    assert server._pool is pool_a
    assert server._registered_tool_names == [tool_name]
    assert tool_name in pool_a.tool_definitions
    assert pool_b.tool_definitions == {}


@pytest.mark.asyncio
async def test_background_refresh_restores_serialized_context_after_pool_reset():
    from agent.secret_scope import set_multiplex_active
    from gateway.access_registry import (
        deserialize_resolved_access_context,
        serialize_resolved_access_context,
    )
    from gateway.session_context import bind_resolved_access_context, reset_session_vars
    from tools import mcp_tool

    set_multiplex_active(True)
    ctx_a = _ctx("profile-a", "dm:a")
    ctx_b = _ctx("profile-b", "dm:b")
    observed = []

    with bind_resolved_access_context(ctx_a):
        pool_a = mcp_tool._current_mcp_pool()
        assert pool_a is not None
        server = mcp_tool.MCPServerTask("demo")
        server._pool = pool_a
        server._capture_resolved_access_context()
        captured = json.loads(json.dumps(server._captured_resolved_access_context))

    mcp_tool._profile_pools.clear()
    reset_session_vars()

    server._pool = None
    server._config = {
        "tools": {"include": ["fresh"], "resources": False, "prompts": False}
    }
    server.session = _ListToolsSession([_tool("fresh", "Fresh")], observed)
    server._captured_resolved_access_context = serialize_resolved_access_context(
        deserialize_resolved_access_context(captured)
    )

    with bind_resolved_access_context(ctx_b):
        await server._refresh_tools_task()

    expected_key = mcp_tool.MCPPoolKey(profile_id="profile-a", conversation_scope="dm:a")
    assert set(mcp_tool._profile_pools) == {expected_key}
    reopened_pool = mcp_tool._profile_pools[expected_key]
    assert observed == [("profile-a", reopened_pool)]
    assert server._pool is reopened_pool
    assert reopened_pool.servers["demo"] is server
    assert mcp_tool._legacy_pool.tool_definitions == {}
    assert all(key.profile_id != "profile-b" for key in mcp_tool._profile_pools)


@pytest.mark.asyncio
async def test_malformed_captured_context_fails_closed_before_server_task_spawn():
    from gateway.session_context import bind_resolved_access_context
    from tools import mcp_tool

    server = mcp_tool.MCPServerTask("demo")
    with bind_resolved_access_context({"profile_id": "profile-a"}):
        with pytest.raises(ValueError):
            await server.start({"command": "never-spawned"})

    assert server._task is None


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


def test_model_definitions_same_name_use_current_profile_schema_snapshot():
    from agent.secret_scope import set_multiplex_active

    set_multiplex_active(True)
    ctx_a = _ctx("profile-a", "dm:a")
    ctx_b = _ctx("profile-b", "dm:b")
    _register_demo_server(
        ctx_a,
        [_tool("echo", "Echo from A", {"a_only": {"type": "string"}})],
    )
    _register_demo_server(
        ctx_b,
        [_tool("echo", "Echo from B", {"b_only": {"type": "integer"}})],
    )

    defs_a = _mcp_defs(ctx_a, enabled_toolsets=["demo"])
    defs_b = _mcp_defs(ctx_b, enabled_toolsets=["demo"])

    assert defs_a["mcp__demo__echo"]["description"] == "Echo from A"
    assert set(defs_a["mcp__demo__echo"]["parameters"]["properties"]) == {"a_only"}
    assert defs_b["mcp__demo__echo"]["description"] == "Echo from B"
    assert set(defs_b["mcp__demo__echo"]["parameters"]["properties"]) == {"b_only"}


def test_model_definitions_do_not_leak_a_only_tool_to_profile_b():
    from agent.secret_scope import set_multiplex_active

    set_multiplex_active(True)
    ctx_a = _ctx("profile-a", "dm:a")
    ctx_b = _ctx("profile-b", "dm:b")
    _register_demo_server(ctx_a, [_tool("echo", "Shared"), _tool("a_only", "A only")])
    _register_demo_server(ctx_b, [_tool("echo", "Shared")])

    assert set(_mcp_defs(ctx_a, enabled_toolsets=["demo"])) == {
        "mcp__demo__a_only",
        "mcp__demo__echo",
    }
    assert set(_mcp_defs(ctx_b, enabled_toolsets=["demo"])) == {"mcp__demo__echo"}


def test_model_definitions_missing_multiplex_context_see_no_mcp_tools():
    from agent.secret_scope import set_multiplex_active
    from model_tools import get_tool_definitions

    set_multiplex_active(True)
    _register_demo_server(_ctx("profile-a", "dm:a"), [_tool("echo", "Echo")])

    definitions = get_tool_definitions(
        enabled_toolsets=["demo"],
        quiet_mode=True,
        skip_tool_search_assembly=True,
    )

    assert [
        d["function"]["name"]
        for d in definitions
        if d["function"]["name"].startswith("mcp__")
    ] == []


def test_deregistering_profile_a_snapshot_preserves_profile_b_definitions():
    from agent.secret_scope import set_multiplex_active
    from tools import mcp_tool

    set_multiplex_active(True)
    ctx_a = _ctx("profile-a", "dm:a")
    ctx_b = _ctx("profile-b", "dm:b")
    _pool_a, server_a = _register_demo_server(
        ctx_a,
        [_tool("echo", "Echo from A", {"a_only": {"type": "string"}})],
    )
    _register_demo_server(
        ctx_b,
        [_tool("echo", "Echo from B", {"b_only": {"type": "integer"}})],
    )

    mcp_tool.MCPServerTask._deregister_tools(server_a)

    assert _mcp_defs(ctx_a, enabled_toolsets=["demo"]) == {}
    defs_b = _mcp_defs(ctx_b, enabled_toolsets=["demo"])
    assert defs_b["mcp__demo__echo"]["description"] == "Echo from B"
    assert set(defs_b["mcp__demo__echo"]["parameters"]["properties"]) == {"b_only"}


def test_legacy_single_profile_mcp_definitions_still_use_registry():
    from tools import mcp_tool

    server = _server("legacy")
    server._tools = [_tool("echo", "Legacy echo")]
    mcp_tool._servers["demo"] = server
    server._registered_tool_names = mcp_tool._register_server_tools(
        "demo",
        server,
        {"tools": {"resources": False, "prompts": False}},
    )

    defs = _mcp_defs(_ctx("ignored", "ignored"), enabled_toolsets=["demo"])

    assert defs["mcp__demo__echo"]["description"] == "Legacy echo"


def _typed_raw_server(secret_key: str = "MCP_DEMO_TOKEN") -> dict:
    return {
        "command": "demo-mcp",
        "args": ["--mode", "safe"],
        "env": {"DEMO_TOKEN": "${credential:token}"},
        "headers": {"Authorization": "Bearer ${credential:token}"},
        "credential_refs": {"token": secret_key},
        "tools": {"include": ["echo"], "resources": False, "prompts": False},
    }


def test_typed_same_server_tool_uses_profile_local_refs_only_env_header_and_hash_snapshots(monkeypatch):
    from agent.secret_scope import reset_secret_scope, set_multiplex_active, set_secret_scope
    from gateway.session_context import bind_resolved_access_context
    from tools import mcp_tool

    set_multiplex_active(True)
    ctx_a = _ctx("profile-a", "dm:a")
    ctx_b = _ctx("profile-b", "dm:b")
    captured: dict[str, dict] = {}

    async def fake_discover(name, cfg, pool=None):
        captured[pool.key.profile_id] = cfg
        return []

    monkeypatch.setattr(mcp_tool, "_MCP_AVAILABLE", True)
    monkeypatch.setattr(mcp_tool, "_run_on_mcp_loop", _run_coro_inline)
    monkeypatch.setattr(mcp_tool, "_discover_and_register_server", fake_discover)

    token_a = set_secret_scope({"MCP_DEMO_TOKEN": "profile-a-secret"})
    try:
        with bind_resolved_access_context(ctx_a):
            assert mcp_tool.register_mcp_servers({"demo": _typed_raw_server()}) == []
            pool_a = mcp_tool._current_mcp_pool(create=False)
    finally:
        reset_secret_scope(token_a)

    token_b = set_secret_scope({"MCP_DEMO_TOKEN": "profile-b-secret"})
    try:
        with bind_resolved_access_context(ctx_b):
            assert mcp_tool.register_mcp_servers({"demo": _typed_raw_server()}) == []
            pool_b = mcp_tool._current_mcp_pool(create=False)
    finally:
        reset_secret_scope(token_b)

    assert captured["profile-a"]["args"] == ["--mode", "safe"]
    assert captured["profile-b"]["args"] == ["--mode", "safe"]
    assert captured["profile-a"]["env"] == {"DEMO_TOKEN": "profile-a-secret"}
    assert captured["profile-b"]["env"] == {"DEMO_TOKEN": "profile-b-secret"}
    assert captured["profile-a"]["headers"] == {"Authorization": "Bearer profile-a-secret"}
    assert captured["profile-b"]["headers"] == {"Authorization": "Bearer profile-b-secret"}
    assert len(pool_a.config_snapshots["demo"]) == 64
    assert len(pool_b.config_snapshots["demo"]) == 64
    assert all(ch in "0123456789abcdef" for ch in pool_a.config_snapshots["demo"])
    assert all(ch in "0123456789abcdef" for ch in pool_b.config_snapshots["demo"])
    assert "profile-a-secret" not in pool_a.config_snapshots["demo"]
    assert "profile-b-secret" not in pool_b.config_snapshots["demo"]
    assert pool_a.config_snapshots["demo"] == typed_mcp_config_fingerprint(captured["profile-a"])
    assert pool_b.config_snapshots["demo"] == typed_mcp_config_fingerprint(captured["profile-b"])
    assert pool_a.credential_ref_metadata["demo"] == {"token": "MCP_DEMO_TOKEN"}
    assert pool_b.credential_ref_metadata["demo"] == {"token": "MCP_DEMO_TOKEN"}
    assert pool_a.allowed_tool_names["demo"] == frozenset({"echo"})
    assert pool_b.allowed_tool_names["demo"] == frozenset({"echo"})


@pytest.mark.parametrize(
    ("cfg", "scope", "reason"),
    [
        (_typed_raw_server(), {}, "profile-bound-mcp-credential-ref-missing"),
        (
            {**_typed_raw_server(), "args": ["${credential:token}"]},
            {"MCP_DEMO_TOKEN": "secret"},
            "profile-bound-mcp-credential-ref-location-denied",
        ),
        (
            {**_typed_raw_server(), "args": ["${env:MCP_DEMO_TOKEN}"]},
            {"MCP_DEMO_TOKEN": "secret"},
            "profile-bound-mcp-legacy-placeholder-denied",
        ),
        (
            {**_typed_raw_server(), "env": {"HOME": "${credential:token}"}},
            {"MCP_DEMO_TOKEN": "secret"},
            "profile-bound-mcp-env-key-denied",
        ),
        (
            {**_typed_raw_server(), "env": {"XDG_CONFIG_HOME": "${credential:token}"}},
            {"MCP_DEMO_TOKEN": "secret"},
            "profile-bound-mcp-env-key-denied",
        ),
        (
            {**_typed_raw_server(), "env": {"TMPDIR": "${credential:token}"}},
            {"MCP_DEMO_TOKEN": "secret"},
            "profile-bound-mcp-env-key-denied",
        ),
        (
            {**_typed_raw_server(), "tools": {"resources": False, "prompts": False}},
            {"MCP_DEMO_TOKEN": "secret"},
            "profile-bound-mcp-tools-include-missing",
        ),
        (
            {**_typed_raw_server(), "tools": {"include": ["*"], "resources": False, "prompts": False}},
            {"MCP_DEMO_TOKEN": "secret"},
            "profile-bound-mcp-tools-include-invalid",
        ),
        (
            {**_typed_raw_server(), "auth": "oauth", "oauth": {"client_secret": "literal"}},
            {"MCP_DEMO_TOKEN": "secret"},
            "profile-bound-oauth-client-secret-denied",
        ),
        (
            {**_typed_raw_server(), "auth": "oauth", "oauth": {"scope": "${credential:token}"}},
            {"MCP_DEMO_TOKEN": "secret"},
            "profile-bound-mcp-credential-ref-location-denied",
        ),
    ],
)
def test_typed_config_denies_before_spawn_or_interpolation(monkeypatch, cfg, scope, reason):
    from agent.secret_scope import reset_secret_scope, set_multiplex_active, set_secret_scope
    from gateway.session_context import bind_resolved_access_context
    from tools import mcp_tool

    set_multiplex_active(True)
    monkeypatch.setenv("MCP_DEMO_TOKEN", "ambient-must-not-satisfy-ref")
    monkeypatch.setattr(mcp_tool, "_MCP_AVAILABLE", True)
    monkeypatch.setattr(
        mcp_tool,
        "_discover_and_register_server",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("spawn/connect must not run")),
    )

    token = set_secret_scope(scope)
    try:
        with bind_resolved_access_context(_ctx("profile-a", "dm:a")):
            assert mcp_tool.register_mcp_servers({"demo": cfg}) == []
            pool = mcp_tool._current_mcp_pool(create=False)
    finally:
        reset_secret_scope(token)

    assert pool.connect_errors["demo"] == reason
    assert "demo" not in pool.config_snapshots


def test_typed_oauth_without_secret_uses_exact_include_and_hash_snapshot(monkeypatch):
    from agent.secret_scope import reset_secret_scope, set_multiplex_active, set_secret_scope
    from gateway.session_context import bind_resolved_access_context
    from tools import mcp_tool

    set_multiplex_active(True)
    captured = {}

    async def fake_discover(name, cfg, pool=None):
        captured["cfg"] = cfg
        captured["pool"] = pool
        return []

    monkeypatch.setattr(mcp_tool, "_MCP_AVAILABLE", True)
    monkeypatch.setattr(mcp_tool, "_run_on_mcp_loop", _run_coro_inline)
    monkeypatch.setattr(mcp_tool, "_discover_and_register_server", fake_discover)

    token = set_secret_scope({})
    try:
        with bind_resolved_access_context(_ctx("profile-a", "dm:a")):
            assert mcp_tool.register_mcp_servers({
                "demo": {
                    "url": "https://mcp.example/mcp",
                    "auth": "oauth",
                    "oauth": {"scope": "read"},
                    "tools": {"include": ["echo"], "resources": False, "prompts": False},
                }
            }) == []
            pool = mcp_tool._current_mcp_pool(create=False)
    finally:
        reset_secret_scope(token)

    assert captured["pool"] is pool
    assert captured["cfg"]["auth"] == "oauth"
    assert captured["cfg"]["oauth"] == {"scope": "read"}
    assert pool.allowed_tool_names["demo"] == frozenset({"echo"})
    assert pool.config_snapshots["demo"] == typed_mcp_config_fingerprint(captured["cfg"])


@pytest.mark.asyncio
async def test_http_oauth_provider_uses_captured_pool_home_despite_ambient_context(monkeypatch, tmp_path):
    from agent.secret_scope import set_multiplex_active
    from gateway.session_context import bind_resolved_access_context
    from tools import mcp_tool

    set_multiplex_active(True)
    ctx_a = _ctx("profile-a", "dm:a")
    ctx_b = _ctx("profile-b", "dm:b")
    with bind_resolved_access_context(ctx_a):
        pool_a = mcp_tool._current_mcp_pool()
    with bind_resolved_access_context(ctx_b):
        pool_b = mcp_tool._current_mcp_pool()
    assert pool_a.profile_home != pool_b.profile_home

    observed = {}

    class StopAfterProvider(RuntimeError):
        pass

    class FakeManager:
        def get_or_build_provider(self, server_name, server_url, oauth_config, **kwargs):
            observed.update(
                server_name=server_name,
                server_url=server_url,
                oauth_config=oauth_config,
                kwargs=kwargs,
            )
            raise StopAfterProvider()

    server = mcp_tool.MCPServerTask("demo")
    server._pool = pool_a
    server._auth_type = "oauth"

    monkeypatch.setattr(mcp_tool, "_MCP_HTTP_AVAILABLE", True)
    monkeypatch.setattr(mcp_tool, "_validate_remote_mcp_url", lambda *_a, **_k: None)
    monkeypatch.setattr("tools.mcp_oauth_manager.get_manager", lambda: FakeManager())

    with bind_resolved_access_context(ctx_b), pytest.raises(StopAfterProvider):
        await server._run_http({"url": "https://mcp.example/mcp", "auth": "oauth"})

    assert observed["kwargs"] == {
        "hermes_home": pool_a.profile_home,
        "pool_key": pool_a.key,
    }


def test_auth_recovery_uses_captured_pool_despite_ambient_context(monkeypatch):
    from agent.secret_scope import set_multiplex_active
    from gateway.session_context import bind_resolved_access_context, reset_session_vars
    from tools import mcp_tool

    set_multiplex_active(True)
    ctx_a = _ctx("profile-a", "dm:a")
    ctx_b = _ctx("profile-b", "dm:b")
    with bind_resolved_access_context(ctx_a):
        pool_a = mcp_tool._current_mcp_pool()
    with bind_resolved_access_context(ctx_b):
        pool_b = mcp_tool._current_mcp_pool()
    assert pool_a.profile_home != pool_b.profile_home

    observed = []

    class FakeManager:
        async def handle_401(self, server_name, failed_access_token=None, **kwargs):
            observed.append((server_name, failed_access_token, kwargs))
            return False

    monkeypatch.setattr(mcp_tool, "_is_auth_error", lambda exc: True)
    monkeypatch.setattr(mcp_tool, "_run_on_mcp_loop", _run_coro_inline)
    monkeypatch.setattr("tools.mcp_oauth_manager.get_manager", lambda: FakeManager())

    with bind_resolved_access_context(ctx_b):
        result_b = json.loads(
            mcp_tool._handle_auth_error_and_retry(
                "demo",
                RuntimeError("401"),
                lambda: '{"result":"retry"}',
                "tools/call echo",
                pool=pool_a,
            )
        )
    reset_session_vars()
    result_missing = json.loads(
        mcp_tool._handle_auth_error_and_retry(
            "demo",
            RuntimeError("401"),
            lambda: '{"result":"retry"}',
            "tools/call echo",
            pool=pool_a,
        )
    )

    assert result_b["needs_reauth"] is True
    assert result_missing["needs_reauth"] is True
    assert observed == [
        (
            "demo",
            None,
            {"hermes_home": pool_a.profile_home, "pool_key": pool_a.key},
        ),
        (
            "demo",
            None,
            {"hermes_home": pool_a.profile_home, "pool_key": pool_a.key},
        ),
    ]
    assert pool_a.error_counts["demo"] == 2
    assert "demo" not in pool_b.error_counts


def test_typed_process_env_provider_key_cannot_satisfy_missing_ref(monkeypatch):
    from agent.secret_scope import reset_secret_scope, set_multiplex_active, set_secret_scope
    from gateway.session_context import bind_resolved_access_context
    from tools import mcp_tool

    set_multiplex_active(True)
    monkeypatch.setenv("MCP_OPENAI_API_KEY", "ambient-provider-secret")
    monkeypatch.setattr(mcp_tool, "_MCP_AVAILABLE", True)
    token = set_secret_scope({})
    try:
        with bind_resolved_access_context(_ctx("profile-a", "dm:a")):
            assert mcp_tool.register_mcp_servers({
                "demo": _typed_raw_server("MCP_OPENAI_API_KEY")
            }) == []
            pool = mcp_tool._current_mcp_pool(create=False)
    finally:
        reset_secret_scope(token)

    assert pool.connect_errors["demo"] == "profile-bound-mcp-credential-ref-missing"
    assert pool.config_snapshots == {}


def test_typed_child_env_empty_ambient_env_does_not_fallback_to_process_env(monkeypatch, tmp_path):
    from tools.mcp_profile_policy import build_typed_child_env

    profile_home = tmp_path / "profiles" / "profile-a"
    monkeypatch.setenv("PATH", "/ambient/bin")
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-provider-secret")

    env = build_typed_child_env({}, profile_home=profile_home, ambient_env={})

    assert "PATH" not in env
    assert "OPENAI_API_KEY" not in env
    assert env["HOME"] == str(profile_home.resolve())
    assert env["HERMES_HOME"] == str(profile_home.resolve())
    assert env["TMPDIR"] == str(profile_home.resolve() / "tmp")
    assert env["TEMP"] == str(profile_home.resolve() / "tmp")
    assert env["TMP"] == str(profile_home.resolve() / "tmp")


def test_typed_handler_denies_missing_snapshot_before_server_call(monkeypatch):
    from agent.secret_scope import set_multiplex_active
    from gateway.session_context import bind_resolved_access_context
    from tools import mcp_tool

    set_multiplex_active(True)
    context = _ctx("profile-a", "dm:a")
    server = _server("from-a")
    with bind_resolved_access_context(context):
        pool = mcp_tool._current_mcp_pool()
        server._pool = pool
        pool.servers["demo"] = server
        handler = mcp_tool._make_tool_handler("demo", "echo", 120)
        assert json.loads(handler({})) == {"error": "profile-bound-mcp-snapshot-missing"}

    assert server.session.calls == []


def test_typed_handler_denies_tool_missing_from_exact_raw_allowlist(monkeypatch):
    from agent.secret_scope import set_multiplex_active
    from gateway.session_context import bind_resolved_access_context
    from tools import mcp_tool

    set_multiplex_active(True)
    context = _ctx("profile-a", "dm:a")
    server = _server("from-a")
    server._config = {"tools": {"include": ["other"], "resources": False, "prompts": False}}
    with bind_resolved_access_context(context):
        pool = _install_server(context, server)
        pool.config_snapshots["demo"] = typed_mcp_config_fingerprint(server._config)
        pool.allowed_tool_names["demo"] = frozenset({"other"})
        handler = mcp_tool._make_tool_handler("demo", "echo", 120)
        assert json.loads(handler({})) == {"error": "profile-bound-mcp-snapshot-missing"}

    assert server.session.calls == []


def test_typed_disabled_config_clears_stale_snapshot_and_denies_handler(monkeypatch):
    from agent.secret_scope import reset_secret_scope, set_multiplex_active, set_secret_scope
    from gateway.session_context import bind_resolved_access_context
    from tools import mcp_tool

    set_multiplex_active(True)
    context = _ctx("profile-a", "dm:a")
    server = _server("from-a")
    token = set_secret_scope({"MCP_DEMO_TOKEN": "secret"})
    try:
        with bind_resolved_access_context(context):
            pool = _install_server(context, server)
            pool.credential_ref_metadata["demo"] = {"token": "MCP_DEMO_TOKEN"}
            assert "demo" in pool.config_snapshots

            mcp_tool._prepare_typed_mcp_servers_for_pool(
                {"demo": {**_typed_raw_server(), "enabled": False}},
                pool,
            )
            handler = mcp_tool._make_tool_handler("demo", "echo", 120)
            assert json.loads(handler({})) == {"error": "profile-bound-mcp-snapshot-missing"}
    finally:
        reset_secret_scope(token)

    assert server.session.calls == []


def test_typed_build_safe_env_uses_profile_home_xdg_without_ambient_provider_env(monkeypatch, tmp_path):
    from tools import mcp_tool

    profile_home = tmp_path / "profiles" / "profile-a"
    monkeypatch.setenv("HOME", "/owner/home")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/owner/.config")
    monkeypatch.setenv("TMPDIR", "/owner/tmp")
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient-anthropic")
    monkeypatch.setattr(
        "hermes_cli.profiles.get_profile_dir",
        lambda profile_id: profile_home,
    )

    pool = mcp_tool.MCPServerPool(
        key=mcp_tool.MCPPoolKey(profile_id="profile-a", conversation_scope="dm:a"),
        profile_home=str(profile_home.resolve(strict=False)),
    )
    env = mcp_tool._build_safe_env({"DEMO_TOKEN": "resolved"}, pool=pool)

    assert env["HOME"] == str(profile_home.resolve())
    assert env["HERMES_HOME"] == str(profile_home.resolve())
    assert env["TMPDIR"] == str(profile_home.resolve() / "tmp")
    assert env["TEMP"] == str(profile_home.resolve() / "tmp")
    assert env["TMP"] == str(profile_home.resolve() / "tmp")
    assert env["XDG_CONFIG_HOME"].startswith(str(profile_home.resolve()))
    assert env["XDG_CACHE_HOME"].startswith(str(profile_home.resolve()))
    assert env["XDG_DATA_HOME"].startswith(str(profile_home.resolve()))
    assert env["XDG_STATE_HOME"].startswith(str(profile_home.resolve()))
    assert env["DEMO_TOKEN"] == "resolved"
    assert "OPENAI_API_KEY" not in env
    assert "ANTHROPIC_API_KEY" not in env


def test_typed_register_rechecks_snapshot_after_preparation_before_discovery(monkeypatch):
    from agent.secret_scope import reset_secret_scope, set_multiplex_active, set_secret_scope
    from gateway.session_context import bind_resolved_access_context
    from tools import mcp_tool

    set_multiplex_active(True)
    monkeypatch.setattr(mcp_tool, "_MCP_AVAILABLE", True)

    async def forbidden_connect_server(*_args, **_kwargs):
        raise AssertionError("_connect_server must not run when typed snapshot changed")

    def race_then_run(coro_or_factory, timeout=120):
        pool = mcp_tool._current_mcp_pool(create=False)
        assert pool is not None
        pool.config_snapshots["demo"] = "f" * 64
        return _run_coro_inline(coro_or_factory, timeout=timeout)

    monkeypatch.setattr(mcp_tool, "_connect_server", forbidden_connect_server)
    monkeypatch.setattr(mcp_tool, "_run_on_mcp_loop", race_then_run)

    token = set_secret_scope({"MCP_DEMO_TOKEN": "secret"})
    try:
        with bind_resolved_access_context(_ctx("profile-a", "dm:a")):
            assert mcp_tool.register_mcp_servers({"demo": _typed_raw_server()}) == []
            pool = mcp_tool._current_mcp_pool(create=False)
    finally:
        reset_secret_scope(token)

    assert pool.connect_errors["demo"] == "profile-bound-mcp-snapshot-missing"
    assert "demo" not in pool.servers


def test_typed_utility_operations_allowed_only_when_explicit_flags_true(monkeypatch):
    from agent.secret_scope import reset_secret_scope, set_multiplex_active, set_secret_scope
    from gateway.session_context import bind_resolved_access_context
    from tools import mcp_tool

    set_multiplex_active(True)
    context = _ctx("profile-a", "dm:a")
    token = set_secret_scope({"MCP_DEMO_TOKEN": "secret"})
    try:
        with bind_resolved_access_context(context):
            pool = mcp_tool._current_mcp_pool()
            assert pool is not None
            prepared = mcp_tool._prepare_typed_mcp_servers_for_pool(
                {
                    "demo": {
                        **_typed_raw_server(),
                        "tools": {"include": ["echo"], "resources": True, "prompts": False},
                    }
                },
                pool,
            )
            server = _server("from-a")
            server._config = prepared["demo"]
            server._pool = pool
            pool.servers["demo"] = server

            assert mcp_tool._typed_mcp_call_allowed(pool, "demo", "echo") is True
            assert mcp_tool._typed_mcp_call_allowed(pool, "demo", "list_resources") is True
            assert mcp_tool._typed_mcp_call_allowed(pool, "demo", "read_resource") is True
            assert mcp_tool._typed_mcp_call_allowed(pool, "demo", "list_prompts") is False
            assert mcp_tool._typed_mcp_call_allowed(pool, "demo", "get_prompt") is False
    finally:
        reset_secret_scope(token)


def test_typed_guessed_disabled_utility_handler_denied(monkeypatch):
    from agent.secret_scope import set_multiplex_active
    from gateway.session_context import bind_resolved_access_context
    from tools import mcp_tool

    set_multiplex_active(True)
    context = _ctx("profile-a", "dm:a")
    server = _server("from-a")
    with bind_resolved_access_context(context):
        pool = _install_server(context, server)
        pool.allowed_tool_names["demo"] = frozenset({"echo"})
        handler = mcp_tool._make_list_resources_handler("demo", 120)
        assert json.loads(handler({})) == {"error": "profile-bound-mcp-snapshot-missing"}

    assert server.session.calls == []


def test_typed_shutdown_without_server_clears_prepared_state_and_bumps_generation():
    from agent.secret_scope import reset_secret_scope, set_multiplex_active, set_secret_scope
    from gateway.session_context import bind_resolved_access_context
    from tools import mcp_tool

    set_multiplex_active(True)
    token = set_secret_scope({"MCP_DEMO_TOKEN": "secret"})
    try:
        with bind_resolved_access_context(_ctx("profile-a", "dm:a")):
            pool = mcp_tool._current_mcp_pool()
            assert pool is not None
            mcp_tool._prepare_typed_mcp_servers_for_pool({"demo": _typed_raw_server()}, pool)
            pool.connect_errors["demo"] = "profile-bound-mcp-connect-failed"
            before_generation = pool.generation

            assert pool.servers == {}
            assert pool.config_snapshots
            assert pool.credential_ref_metadata == {"demo": {"token": "MCP_DEMO_TOKEN"}}
            assert pool.allowed_tool_names == {"demo": frozenset({"echo"})}
            assert pool.connect_errors == {"demo": "profile-bound-mcp-connect-failed"}

            mcp_tool.shutdown_current_mcp_servers()
    finally:
        reset_secret_scope(token)

    assert pool.config_snapshots == {}
    assert pool.credential_ref_metadata == {}
    assert pool.allowed_tool_names == {}
    assert pool.connect_errors == {}
    assert pool.connecting == set()
    assert pool.generation == before_generation + 1
