"""Regression test for /reload-mcp refreshing cached agent tool lists.

Before this fix, the gateway's _execute_mcp_reload reconnected MCP servers
and updated the global _servers registry, but cached AIAgent instances kept
their original tools list. Users had to run /new (discarding conversation
history) for the agent to pick up the new tools.

This test exercises _execute_mcp_reload directly with mocked MCP discovery
and asserts that every cached agent's `tools` and `valid_tool_names`
attributes are overwritten with the freshly-discovered tool set.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.session import SessionEntry, SessionSource, build_session_key


def _make_source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        user_id="u1",
        chat_id="c1",
        user_name="tester",
        chat_type="dm",
    )


def _make_event() -> MessageEvent:
    return MessageEvent(text="/reload-mcp", source=_make_source(), message_id="m1")


def _resolved_context(profile_id: str = "profile-a"):
    from gateway.access_registry import DeliveryTarget, ResolvedAccessContext

    return ResolvedAccessContext(
        principal_id=f"principal-{profile_id}",
        role_id="family_standard",
        profile_id=profile_id,
        conversation_scope=f"dm:{profile_id}",
        capabilities=frozenset({"mcp"}),
        delivery_target=DeliveryTarget(
            platform="telegram",
            account="bot-main",
            peer_kind="dm",
            chat_id=f"chat-{profile_id}",
        ),
    )


def _make_runner_with_cached_agents(num_agents: int = 2):
    """Build a bare GatewayRunner with `num_agents` fake cached agents."""
    import threading

    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")}
    )

    # Session store stub — _execute_mcp_reload writes a transcript message
    # at the end; tests don't care about that side effect.
    session_entry = SessionEntry(
        session_key=build_session_key(_make_source()),
        session_id="sess-1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = session_entry
    runner.session_store.append_to_transcript = MagicMock()

    # Build N fake cached agents with stale `tools` + `valid_tool_names`.
    runner._agent_cache = OrderedDict()
    runner._agent_cache_lock = threading.Lock()
    for i in range(num_agents):
        stale_tool = {
            "type": "function",
            "function": {"name": f"stale_tool_{i}", "description": "old"},
        }
        agent = SimpleNamespace(
            tools=[stale_tool],
            valid_tool_names={f"stale_tool_{i}"},
            enabled_toolsets=None,
            disabled_toolsets=None,
        )
        runner._agent_cache[f"session-{i}"] = (agent, f"sig-{i}")

    return runner


async def _execute_reload_and_close_executor(runner):
    try:
        return await runner._execute_mcp_reload(_make_event())
    finally:
        runner._shutdown_executor()


@pytest.mark.asyncio
async def test_reload_mcp_refreshes_cached_agent_tools():
    """After /reload-mcp succeeds, every cached agent gets its tool list
    replaced with the freshly-discovered set."""
    runner = _make_runner_with_cached_agents(num_agents=3)

    # Snapshot the stale state so we can assert it changed.
    pre_reload_tools = {
        key: list(entry[0].tools) for key, entry in runner._agent_cache.items()
    }

    # Fresh tools that get_tool_definitions() will return after the reload.
    fresh_tool_defs = [
        {
            "type": "function",
            "function": {"name": "HassTurnOn", "description": "Turns on a device"},
        },
        {
            "type": "function",
            "function": {"name": "HassTurnOff", "description": "Turns off a device"},
        },
    ]

    with (
        patch("tools.mcp_tool.shutdown_current_mcp_servers"),
        patch("tools.mcp_tool.discover_mcp_tools", return_value=["HassTurnOn", "HassTurnOff"]),
        patch(
            "tools.mcp_tool.snapshot_current_mcp_server_names",
            side_effect=[{"homeassistant"}, {"homeassistant"}],
        ),
        patch("model_tools.get_tool_definitions", return_value=fresh_tool_defs),
    ):
        result = await _execute_reload_and_close_executor(runner)

    # The reload itself returned a status string (not an exception).
    assert isinstance(result, str)

    # Every cached agent has fresh tools and the matching valid_tool_names.
    expected_names = {"HassTurnOn", "HassTurnOff"}
    for key, (agent, _sig) in runner._agent_cache.items():
        assert agent.tools == fresh_tool_defs, (
            f"Agent {key} kept stale tools: {agent.tools} != {fresh_tool_defs}"
        )
        assert agent.valid_tool_names == expected_names, (
            f"Agent {key} kept stale valid_tool_names: {agent.valid_tool_names}"
        )
        # Sanity check that the swap actually changed something.
        assert agent.tools != pre_reload_tools[key]


@pytest.mark.asyncio
async def test_reload_mcp_handles_empty_agent_cache():
    """Reload with no cached agents (e.g. fresh gateway) must not raise."""
    runner = _make_runner_with_cached_agents(num_agents=0)
    assert len(runner._agent_cache) == 0

    with (
        patch("tools.mcp_tool.shutdown_current_mcp_servers"),
        patch("tools.mcp_tool.discover_mcp_tools", return_value=[]),
        patch("tools.mcp_tool.snapshot_current_mcp_server_names", return_value=set()),
        patch("model_tools.get_tool_definitions", return_value=[]),
    ):
        result = await _execute_reload_and_close_executor(runner)

    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_reload_mcp_preserves_per_agent_toolset_overrides():
    """If a cached agent was built with enabled_toolsets=["safe"], the
    refresh must pass that same list to get_tool_definitions so the agent
    doesn't silently gain disabled tools after a reload."""
    runner = _make_runner_with_cached_agents(num_agents=1)
    # Override the toolsets on the cached agent.
    agent, _sig = runner._agent_cache["session-0"]
    agent.enabled_toolsets = ["safe"]
    agent.disabled_toolsets = ["terminal"]

    captured_calls = []

    def _capture_get_tool_definitions(**kwargs):
        captured_calls.append(kwargs)
        return [{"type": "function", "function": {"name": "refreshed"}}]

    with (
        patch("tools.mcp_tool.shutdown_current_mcp_servers"),
        patch("tools.mcp_tool.discover_mcp_tools", return_value=["refreshed"]),
        patch(
            "tools.mcp_tool.snapshot_current_mcp_server_names",
            side_effect=[{"homeassistant"}, {"homeassistant"}],
        ),
        patch("model_tools.get_tool_definitions", side_effect=_capture_get_tool_definitions),
    ):
        await _execute_reload_and_close_executor(runner)

    assert captured_calls, "get_tool_definitions was never called to refresh the cache"
    assert captured_calls[0]["enabled_toolsets"] == ["safe"]
    assert captured_calls[0]["disabled_toolsets"] == ["terminal"]


@pytest.mark.asyncio
async def test_reload_mcp_uses_context_preserving_current_pool_helpers():
    from gateway.session_context import bind_resolved_access_context, get_resolved_access_context

    runner = _make_runner_with_cached_agents(num_agents=0)
    context = _resolved_context("profile-a")
    seen: list[tuple[str, str]] = []

    def _profile_id():
        return get_resolved_access_context().profile_id

    def _snapshot():
        seen.append(("snapshot", _profile_id()))
        return {"current-profile-server"}

    def _shutdown():
        seen.append(("shutdown", _profile_id()))

    def _discover():
        seen.append(("discover", _profile_id()))
        return ["mcp__current_profile_server__echo"]

    with (
        bind_resolved_access_context(context),
        patch("tools.mcp_tool.shutdown_current_mcp_servers", side_effect=_shutdown),
        patch("tools.mcp_tool.discover_mcp_tools", side_effect=_discover),
        patch("tools.mcp_tool.snapshot_current_mcp_server_names", side_effect=_snapshot),
        patch.dict("tools.mcp_tool._servers", {"foreign-server": object()}, clear=True),
        patch("model_tools.get_tool_definitions", return_value=[]),
    ):
        result = await _execute_reload_and_close_executor(runner)

    assert seen == [
        ("snapshot", "profile-a"),
        ("shutdown", "profile-a"),
        ("discover", "profile-a"),
        ("snapshot", "profile-a"),
    ]
    assert "current-profile-server" in result
    assert "foreign-server" not in result
