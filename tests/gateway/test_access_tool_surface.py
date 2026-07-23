import asyncio
import importlib
import sys
import threading
import types
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.access_registry import (
    AccessDeniedError,
    canonical_access_context_fingerprint,
    DeliveryTarget,
    RedactedAuditMetadata,
    ResolvedAccessContext,
    shared_memory_namespace_for_access_context,
)
from gateway.config import Platform
import gateway.run as gateway_run
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource
from gateway.session_context import bind_resolved_access_context, reset_session_vars


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
]


class _CapturingAgent:
    last_init = None

    def __init__(self, *args, **kwargs):
        type(self).last_init = dict(kwargs)
        self.model = kwargs.get("model")
        self.session_id = kwargs.get("session_id")
        self.valid_tool_names = _tool_names_for_toolsets(kwargs.get("enabled_toolsets"))
        self.tools = []

    def run_conversation(self, user_message: str, **kwargs):
        return {
            "final_response": "ok",
            "messages": [],
            "api_calls": 1,
        }


def _tool_names_for_toolsets(toolsets):
    names = set()
    for toolset in toolsets or []:
        if toolset == "memory":
            names.add("memory")
        elif toolset == "web":
            names.update({"web_search", "web_extract"})
        elif toolset == "vision":
            names.add("vision_analyze")
    return names


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


def _shared_context(capabilities=(), *, thread_id=None):
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
            thread_id=thread_id,
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


def test_family_standard_uses_capability_and_config_intersection():
    toolsets = gateway_run.GatewayRunner._toolsets_for_resolved_access_context(
        CONFIGURED_TOOLSETS,
        _context(
            "family_standard",
            {
                "public_web",
                "vision",
                "image_generation",
                "voice_generation",
                "session_search",
                "self_reminder",
                "delegation",
                "terminal",
                "file",
                "browser",
                "arbitrary_mcp",
            },
        ),
    )

    assert toolsets == [
        "cronjob",
        "image_gen",
        "session_search",
        "tts",
        "vision",
        "web",
    ]
    assert not {
        "terminal",
        "file",
        "browser",
        "delegation",
        "wolfram",
        "custom_mcp_server",
        "kanban",
        "homeassistant",
    } & set(toolsets)


def test_family_standard_capability_cannot_enable_disabled_platform_toolset():
    assert gateway_run.GatewayRunner._toolsets_for_resolved_access_context(
        ["web"],
        _context("family_standard", {"public_web", "vision"}),
    ) == ["web"]


def test_family_memory_surface_requires_memory_search_capability_and_config():
    allowed = gateway_run.GatewayRunner._toolsets_for_resolved_access_context(
        CONFIGURED_TOOLSETS,
        _context("family_standard", {"memory_search"}),
    )
    missing_capability = gateway_run.GatewayRunner._toolsets_for_resolved_access_context(
        CONFIGURED_TOOLSETS,
        _context("family_standard", {"public_web"}),
    )
    missing_config = gateway_run.GatewayRunner._toolsets_for_resolved_access_context(
        ["web"],
        _context("family_standard", {"memory_search", "public_web"}),
    )

    assert allowed == ["memory"]
    assert missing_capability == ["web"]
    assert missing_config == ["web"]


def test_family_sandbox_delegation_only_with_capability_and_config():
    allowed = gateway_run.GatewayRunner._toolsets_for_resolved_access_context(
        CONFIGURED_TOOLSETS,
        _context("family_sandbox", {"public_web", "delegation", "terminal", "browser"}),
    )
    without_capability = gateway_run.GatewayRunner._toolsets_for_resolved_access_context(
        CONFIGURED_TOOLSETS,
        _context("family_sandbox", {"public_web"}),
    )
    without_config = gateway_run.GatewayRunner._toolsets_for_resolved_access_context(
        ["web"],
        _context("family_sandbox", {"public_web", "delegation"}),
    )

    assert allowed == ["delegation", "web"]
    assert without_capability == ["web"]
    assert without_config == ["web"]
    assert not {"terminal", "browser", "wolfram"} & set(allowed)


def test_shared_room_generic_surface_is_web_and_vision_only():
    assert gateway_run.GatewayRunner._toolsets_for_resolved_access_context(
        CONFIGURED_TOOLSETS,
        _shared_context({"room_memory", "public_web", "vision"}),
    ) == ["vision", "web"]


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

    assert toolsets == ["memory", "vision", "web"]
    assert expected_tools == frozenset(
        {"memory", "vision_analyze", "web_search", "web_extract"}
    )
    assert no_capability == ["vision", "web"]
    assert no_capability_tools == frozenset({"vision_analyze", "web_search", "web_extract"})
    assert no_config == ["vision", "web"]
    assert no_config_tools == frozenset({"vision_analyze", "web_search", "web_extract"})


def test_access_registry_shared_scope_is_opaque_without_legacy_policy():
    runner = _make_runner()
    context = _shared_context({"room_memory"}, thread_id="topic-7")
    source = _source(context)
    source.thread_id = "topic-7"

    scope = runner._shared_scope_for_source(source)

    assert scope.memory_namespace == shared_memory_namespace_for_access_context(context)
    assert scope.is_topic is True
    assert "room-profile" not in scope.memory_namespace
    assert "room" not in scope.memory_namespace
    assert "topic-7" not in scope.memory_namespace


def test_access_registry_shared_namespaces_split_root_and_topics():
    root = _shared_context({"room_memory"})
    topic_7 = _shared_context({"room_memory"}, thread_id="topic-7")
    topic_8 = _shared_context({"room_memory"}, thread_id="topic-8")

    namespaces = {
        shared_memory_namespace_for_access_context(root),
        shared_memory_namespace_for_access_context(topic_7),
        shared_memory_namespace_for_access_context(topic_8),
    }

    assert len(namespaces) == 3
    for namespace in namespaces:
        assert namespace.startswith("access/")
        assert "room-profile" not in namespace
        assert "room" not in namespace
        assert "topic-" not in namespace


def test_access_context_fingerprint_is_opaque_and_six_field_stable():
    context = _shared_context({"room_memory", "public_web"})
    fingerprint = canonical_access_context_fingerprint(context)

    assert len(fingerprint) == 64
    assert fingerprint == canonical_access_context_fingerprint(context)
    assert "room-profile" not in fingerprint
    assert "room" not in fingerprint
    assert "principal" not in fingerprint


def test_shared_namespace_helper_rejects_wrong_or_malformed_role():
    with pytest.raises(ValueError):
        shared_memory_namespace_for_access_context(_context("family_standard"))
    with pytest.raises(ValueError):
        shared_memory_namespace_for_access_context(SimpleNamespace(role_id="shared_room"))


def test_shared_scope_ignores_non_typed_role_claim_without_legacy_policy():
    runner = _make_runner()
    source = _source()
    source.resolved_access_context = SimpleNamespace(
        principal_id="room",
        role_id="shared_room",
        profile_id="room-profile",
        conversation_scope="room",
        capabilities=frozenset({"room_memory"}),
        delivery_target=_shared_context().delivery_target,
    )

    assert runner._shared_scope_for_source(source) is None


def test_shared_memory_binder_uses_current_task_local_access_context(tmp_path, monkeypatch):
    memory_tool_mod = importlib.import_module("tools.memory_tool")

    context = _shared_context({"room_memory"})
    monkeypatch.setattr(gateway_run, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(memory_tool_mod, "get_hermes_home", lambda: tmp_path)
    agent = SimpleNamespace(valid_tool_names={"memory"})
    reset_session_vars()

    try:
        with bind_resolved_access_context(context):
            gateway_run.GatewayRunner._bind_shared_memory(
                agent,
                SimpleNamespace(
                    memory_namespace=shared_memory_namespace_for_access_context(context)
                ),
                {},
            )
    finally:
        reset_session_vars()

    assert agent._memory_store.access_context == context
    assert agent._memory_store.memory_dir == (
        tmp_path
        / "memories"
        / "shared"
        / shared_memory_namespace_for_access_context(context)
    )
    assert agent._memory_store.allow_user_profile is False
    assert agent._memory_manager is None


def test_memory_slash_scopes_shared_room_store_to_profile_and_context(
    tmp_path, monkeypatch
):
    from gateway.session_context import get_resolved_access_context
    from hermes_constants import (
        get_hermes_home,
        reset_hermes_home_override,
        set_hermes_home_override,
    )
    import hermes_cli.write_approval_commands as write_commands

    memory_tool_mod = importlib.import_module("tools.memory_tool")

    context = _shared_context({"room_memory"})
    profile_home = tmp_path / "room-profile"
    profile_home.mkdir()
    source = _source(context)
    event = MessageEvent(
        text="/memory pending --namespace guessed",
        source=source,
    )
    runner = _make_runner()
    runner.access_registry = MagicMock()
    runner.access_registry.validate_resolved_context.return_value = context
    runner._resolve_profile_home_for_source = MagicMock(return_value=profile_home)
    runner._session_key_for_source = MagicMock(return_value="session-key")
    runner._evict_cached_agent = MagicMock()

    @contextmanager
    def fake_profile_scope(home):
        token = set_hermes_home_override(home)
        try:
            yield
        finally:
            reset_hermes_home_override(token)

    captured = {}

    def fake_load_on_disk_store(**kwargs):
        captured["store_kwargs"] = kwargs
        captured["home"] = get_hermes_home()
        captured["context"] = get_resolved_access_context()
        return object()

    def fake_pending(
        subsystem,
        args,
        memory_store=None,
        set_mode_fn=None,
        pending_scope_key=None,
    ):
        captured["args"] = args
        captured["memory_store"] = memory_store
        captured["pending_scope_key"] = pending_scope_key
        return "ok"

    monkeypatch.setattr(gateway_run, "_profile_runtime_scope", fake_profile_scope)
    monkeypatch.setattr(memory_tool_mod, "load_on_disk_store", fake_load_on_disk_store)
    monkeypatch.setattr(write_commands, "handle_pending_subcommand", fake_pending)

    assert asyncio.run(runner._handle_memory_command(event)) == "ok"

    shared_scope = runner._shared_scope_for_source(source)
    assert shared_scope is not None
    namespace = shared_scope.memory_namespace
    assert captured["home"] == profile_home
    assert captured["context"] == context
    assert captured["store_kwargs"] == {
        "memory_dir": profile_home / "memories" / "shared" / namespace,
        "allow_user_profile": False,
        "access_context": context,
    }
    assert captured["args"] == ["pending", "--namespace", "guessed"]
    assert captured["pending_scope_key"] == canonical_access_context_fingerprint(context)
    assert "guessed" not in str(captured["store_kwargs"]["memory_dir"])
    assert "room-profile" not in namespace
    assert "room" not in namespace


def test_memory_slash_configured_registry_denies_malformed_context_before_io(
    monkeypatch,
):
    memory_tool_mod = importlib.import_module("tools.memory_tool")

    source = _source()
    event = MessageEvent(text="/memory pending", source=source)
    runner = _make_runner()
    runner.access_registry = MagicMock()
    runner.access_registry.validate_resolved_context.side_effect = AccessDeniedError(
        "malformed_resolved_access_context",
        RedactedAuditMetadata("test"),
    )
    runner._resolve_profile_home_for_source = MagicMock(
        side_effect=AssertionError("profile home should not resolve")
    )
    load_store = MagicMock(side_effect=AssertionError("memory store should not load"))
    monkeypatch.setattr(memory_tool_mod, "load_on_disk_store", load_store)

    assert asyncio.run(runner._handle_memory_command(event)) == "Memory access denied."
    runner._resolve_profile_home_for_source.assert_not_called()
    load_store.assert_not_called()


@pytest.mark.parametrize(
    "context",
    [
        _shared_context({"public_web"}),
        _context("unknown_role", {"room_memory", "memory_search"}),
    ],
)
def test_memory_slash_denies_role_without_memory_capability_before_profile_or_store(
    monkeypatch, context
):
    memory_tool_mod = importlib.import_module("tools.memory_tool")

    source = _source(context)
    event = MessageEvent(text="/memory pending", source=source)
    runner = _make_runner()
    runner.access_registry = MagicMock()
    runner.access_registry.validate_resolved_context.return_value = context
    runner._resolve_profile_home_for_source = MagicMock(
        side_effect=AssertionError("profile home should not resolve")
    )
    runner._shared_scope_for_source = MagicMock(
        side_effect=AssertionError("shared scope should not resolve")
    )
    load_store = MagicMock(side_effect=AssertionError("memory store should not load"))
    monkeypatch.setattr(memory_tool_mod, "load_on_disk_store", load_store)

    assert asyncio.run(runner._handle_memory_command(event)) == "Memory access denied."
    runner._resolve_profile_home_for_source.assert_not_called()
    runner._shared_scope_for_source.assert_not_called()
    load_store.assert_not_called()


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


def test_empty_toolset_result_stays_empty_list_not_default_all():
    assert gateway_run.GatewayRunner._toolsets_for_resolved_access_context(
        ["terminal"],
        _context("family_standard", {"public_web"}),
    ) == []


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
    assert _CapturingAgent.last_init["enabled_toolsets"] == ["web"]
    assert _CapturingAgent.last_init["skip_context_files"] is True
    assert _CapturingAgent.last_init["skip_memory"] is True
    assert _CapturingAgent.last_init["prefill_messages"] is None
    assert _CapturingAgent.last_init["user_id"] is None
    assert _CapturingAgent.last_init["user_id_alt"] is None
    assert _CapturingAgent.last_init["user_name"] is None
    bind_shared_memory.assert_not_called()


def test_run_agent_access_registry_shared_room_binds_only_room_memory_namespace(
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
    captured = {}

    def bind_shared_memory(agent, scope, memory_config, *, expected_tool_names):
        captured["scope"] = scope
        captured["expected_tool_names"] = expected_tool_names

    monkeypatch.setattr(runner, "_bind_shared_memory", bind_shared_memory)

    context = _shared_context({"room_memory", "public_web"})
    source = _source(context)
    expected_namespace = runner._shared_scope_for_source(source).memory_namespace

    _CapturingAgent.last_init = None
    result = asyncio.run(
        runner._run_agent(
            message="ping",
            context_prompt="",
            history=[],
            source=source,
            session_id="session-1",
            session_key="agent:main:telegram:group:-10001",
        )
    )

    assert result["final_response"] == "ok"
    assert _CapturingAgent.last_init["enabled_toolsets"] == ["memory", "web"]
    assert _CapturingAgent.last_init["skip_context_files"] is True
    assert _CapturingAgent.last_init["skip_memory"] is True
    assert _CapturingAgent.last_init["prefill_messages"] is None
    assert _CapturingAgent.last_init["user_id"] is None
    assert captured["scope"].memory_namespace == expected_namespace
    assert captured["expected_tool_names"] == frozenset(
        {"memory", "web_search", "web_extract"}
    )
    assert "room-profile" not in expected_namespace
    assert "room" not in expected_namespace
