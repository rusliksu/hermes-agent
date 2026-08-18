import asyncio
import sys
import threading
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from gateway.access_registry import DeliveryTarget, ResolvedAccessContext
from gateway.config import Platform
import gateway.run as gateway_run
from gateway.session import SessionSource


CONFIGURED_TOOLSETS = [
    "browser",
    "cronjob",
    "delegation",
    "file",
    "image_gen",
    "memory",
    "session_search",
    "terminal",
    "tts",
    "vision",
    "web",
    "wolfram",
    "custom_mcp_server",
    "discord_admin",
]


class _CapturingAgent:
    last_init = None

    def __init__(self, *args, **kwargs):
        type(self).last_init = dict(kwargs)
        self.model = kwargs.get("model")
        self.session_id = kwargs.get("session_id")
        self.tools = []

    def run_conversation(self, user_message: str, **kwargs):
        return {
            "final_response": "ok",
            "messages": [],
            "api_calls": 1,
        }


def _context(role_id: str, capabilities=()):
    return ResolvedAccessContext(
        principal_id="principal",
        role_id=role_id,
        profile_id="profile",
        conversation_scope="private",
        capabilities=frozenset(capabilities),
        delivery_target=DeliveryTarget(
            platform="telegram",
            account="bot-a",
            peer_kind="dm",
            chat_id="10001",
        ),
    )


def _shared_context(capabilities=()):
    return ResolvedAccessContext(
        principal_id="room",
        role_id="shared_room",
        profile_id="room-profile",
        conversation_scope="room",
        capabilities=frozenset(capabilities),
        delivery_target=DeliveryTarget(
            platform="telegram",
            account="bot-a",
            peer_kind="group",
            chat_id="-10001",
        ),
    )


def _source(context=None):
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-10001",
        chat_type="group",
        user_id="10001",
    )
    if context is not None:
        source.resolved_access_context = context
    return source


def _make_runner():
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.adapters = {}
    runner.session_store = None
    runner.config = None
    runner._voice_mode = {}
    runner._ephemeral_system_prompt = ""
    runner._prefill_messages = []
    runner._reasoning_config = None
    runner._show_reasoning = False
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._service_tier = None
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._background_tasks = set()
    runner._session_db = None
    runner._session_model_overrides = {}
    runner._session_reasoning_overrides = {}
    runner._pending_model_notes = {}
    runner._pending_approvals = {}
    runner._agent_cache = {}
    runner._agent_cache_lock = threading.Lock()
    runner._single_principal_policy = None
    runner._get_or_create_gateway_honcho = lambda session_key: (None, None)
    runner.hooks = MagicMock()
    runner.hooks.emit = AsyncMock()
    runner.hooks.loaded_hooks = []
    return runner


def test_legacy_none_context_returns_configured_toolsets_unchanged():
    assert gateway_run.GatewayRunner._toolsets_for_resolved_access_context(
        CONFIGURED_TOOLSETS,
        None,
    ) == CONFIGURED_TOOLSETS


def test_owner_context_returns_configured_toolsets_unchanged():
    assert gateway_run.GatewayRunner._toolsets_for_resolved_access_context(
        CONFIGURED_TOOLSETS,
        _context("owner", {"public_web"}),
    ) == CONFIGURED_TOOLSETS


def test_family_gets_full_configured_non_admin_surface():
    toolsets = gateway_run.GatewayRunner._toolsets_for_resolved_access_context(
        CONFIGURED_TOOLSETS,
        _context(
            "family",
            {"public_web"},
        ),
    )

    assert toolsets == sorted(
        name for name in CONFIGURED_TOOLSETS if name != "discord_admin"
    )


def test_admin_toolsets_are_excluded_for_non_owner_roles():
    assert gateway_run.GatewayRunner._toolsets_for_resolved_access_context(
        ["web", "discord_admin", "owner_admin"],
        _context("family", set()),
    ) == ["web"]


def test_family_capability_cannot_enable_disabled_platform_toolset():
    assert gateway_run.GatewayRunner._toolsets_for_resolved_access_context(
        ["web"],
        _context("family", {"public_web", "vision"}),
    ) == ["web"]


def test_family_documents_terminal_and_browser_capabilities_map_to_toolsets():
    assert gateway_run.GatewayRunner._toolsets_for_resolved_access_context(
        ["file", "terminal", "browser"],
        _context("family", {"documents", "docker_terminal", "isolated_browser"}),
    ) == ["browser", "file", "terminal"]


def test_shared_room_documents_exposes_file_tool_contract():
    toolsets, expected_tools = gateway_run.GatewayRunner._shared_tool_profile_for_source(
        _source(_shared_context({"documents"})),
        configured_toolsets=["file"],
    )

    assert toolsets == ["file"]
    assert expected_tools == frozenset(
        {"read_file", "write_file", "patch", "search_files", "deliver_artifact"}
    )


def test_family_keeps_configured_surface_without_capability_flags():
    allowed = gateway_run.GatewayRunner._toolsets_for_resolved_access_context(
        CONFIGURED_TOOLSETS,
        _context(
            "family",
            {"public_web", "delegation", "wolfram", "terminal", "browser"},
        ),
    )
    without_capability = gateway_run.GatewayRunner._toolsets_for_resolved_access_context(
        CONFIGURED_TOOLSETS,
        _context("family", {"public_web"}),
    )
    without_config = gateway_run.GatewayRunner._toolsets_for_resolved_access_context(
        ["web"],
        _context("family", {"public_web", "delegation"}),
    )

    assert allowed == sorted(
        name for name in CONFIGURED_TOOLSETS if name != "discord_admin"
    )
    assert without_capability == sorted(
        name for name in CONFIGURED_TOOLSETS if name != "discord_admin"
    )
    assert without_config == ["web"]
    assert {"terminal", "browser", "delegation"} <= set(allowed)


def test_shared_room_gets_full_configured_non_admin_surface():
    assert gateway_run.GatewayRunner._toolsets_for_resolved_access_context(
        CONFIGURED_TOOLSETS,
        _shared_context({"room_memory", "public_web", "vision"}),
    ) == sorted(name for name in CONFIGURED_TOOLSETS if name != "discord_admin")


def test_shared_profile_memory_requires_room_memory_capability_and_config():
    source = _source(_shared_context({"room_memory", "public_web", "vision"}))

    toolsets, expected_tools = gateway_run.GatewayRunner._shared_tool_profile_for_source(
        source,
        configured_toolsets=["memory", "web", "vision", "terminal"],
    )
    no_capability, no_capability_tools = (
        gateway_run.GatewayRunner._shared_tool_profile_for_source(
            _source(_shared_context({"public_web", "vision"})),
            configured_toolsets=["memory", "web", "vision"],
        )
    )
    no_config, no_config_tools = gateway_run.GatewayRunner._shared_tool_profile_for_source(
        source,
        configured_toolsets=["web", "vision"],
    )

    from toolsets import resolve_toolset

    expected = frozenset(
        tool
        for toolset in ["memory", "web", "vision", "terminal"]
        for tool in resolve_toolset(toolset)
    )
    assert toolsets == ["memory", "terminal", "vision", "web"]
    assert expected_tools == expected
    assert no_capability == ["memory", "vision", "web"]
    assert no_capability_tools == frozenset(
        tool
        for toolset in ["memory", "web", "vision"]
        for tool in resolve_toolset(toolset)
    )
    assert no_config == ["vision", "web"]
    assert no_config_tools == frozenset(
        tool
        for toolset in ["web", "vision"]
        for tool in resolve_toolset(toolset)
    )


def test_malformed_and_unknown_roles_fail_closed_to_empty_toolsets():
    assert gateway_run.GatewayRunner._toolsets_for_resolved_access_context(
        CONFIGURED_TOOLSETS,
        object(),
    ) == []
    assert gateway_run.GatewayRunner._toolsets_for_resolved_access_context(
        CONFIGURED_TOOLSETS,
        _context("unknown_role", {"public_web"}),
    ) == []
    assert gateway_run.GatewayRunner._shared_tool_profile_for_source(
        _source(_context("unknown_role", {"room_memory"})),
        configured_toolsets=["memory"],
    ) == ([], frozenset())


def test_configured_toolset_is_not_removed_for_family_without_capability_flag():
    assert gateway_run.GatewayRunner._toolsets_for_resolved_access_context(
        ["terminal"],
        _context("family", {"public_web"}),
    ) == ["terminal"]


def test_run_agent_passes_filtered_toolsets_and_shared_override_does_not_reopen(
    monkeypatch,
):
    monkeypatch.setattr(
        gateway_run,
        "_load_gateway_config",
        lambda: {
            "platform_toolsets": {
                "telegram": ["memory", "web", "terminal", "browser", "delegation"],
            },
        },
    )
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", lambda *a, **k: {})
    monkeypatch.setattr(gateway_run, "_resolve_gateway_model", lambda *a, **k: "model")

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = _CapturingAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    runner = _make_runner()
    runner._single_principal_policy = SimpleNamespace(
        shared_scope=lambda source: SimpleNamespace(memory_namespace="room")
    )
    bind_shared_memory = MagicMock()
    monkeypatch.setattr(runner, "_bind_shared_memory", bind_shared_memory)

    _CapturingAgent.last_init = None
    result = asyncio.run(
        runner._run_agent(
            message="ping",
            context_prompt="",
            history=[],
            source=_source(_shared_context({"public_web"})),
            session_id="session-1",
            session_key="agent:main:telegram:group:-10001",
        )
    )

    assert result["final_response"] == "ok"
    assert _CapturingAgent.last_init["enabled_toolsets"] == [
        "browser",
        "delegation",
        "memory",
        "terminal",
        "web",
    ]
    bind_shared_memory.assert_called_once()
    assert "browser_snapshot" in bind_shared_memory.call_args.kwargs["expected_tool_names"]
    assert "web_search" in bind_shared_memory.call_args.kwargs["expected_tool_names"]
