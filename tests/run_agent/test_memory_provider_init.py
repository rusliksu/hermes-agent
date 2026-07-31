"""Regression tests for memory provider selection during AIAgent init."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gateway.access_registry import DeliveryTarget, ResolvedAccessContext
from gateway.session_context import (
    bind_resolved_access_context,
    reset_session_vars,
    set_session_vars,
)


class RecordingMemoryProvider:
    name = "recording"

    def __init__(self):
        self.init_kwargs = None
        self.init_session_id = None

    def is_available(self):
        return True

    def initialize(self, session_id, **kwargs):
        self.init_session_id = session_id
        self.init_kwargs = dict(kwargs)

    def get_tool_schemas(self):
        return []

    def shutdown(self):
        pass


def _access_context(role_id="family_standard") -> ResolvedAccessContext:
    return ResolvedAccessContext(
        principal_id="principal",
        role_id=role_id,
        profile_id="profile",
        conversation_scope="private",
        capabilities=frozenset({"memory_search"}),
        delivery_target=DeliveryTarget(
            platform="telegram",
            account="bot-a",
            peer_kind="dm",
            chat_id="10001",
        ),
    )


@contextmanager
def _patched_agent_init(cfg):
    with (
        patch("hermes_cli.config.load_config", return_value=cfg),
        patch("agent.secret_scope.is_multiplex_active", return_value=True),
        patch("plugins.memory.load_memory_provider"),
        patch("agent.model_metadata.get_model_context_length", return_value=204_800),
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        yield


def test_blank_memory_provider_does_not_auto_enable_honcho():
    """Blank memory.provider should remain opt-out even if Honcho fallback looks configured."""
    cfg = {"memory": {"provider": ""}, "agent": {}}
    honcho_cfg = SimpleNamespace(enabled=True, api_key="stale-key", base_url=None)

    with (
        patch("hermes_cli.config.load_config", return_value=cfg),
        patch("hermes_cli.config.save_config") as save_config,
        patch(
            "plugins.memory.honcho.client.HonchoClientConfig.from_global_config",
            return_value=honcho_cfg,
        ) as from_global_config,
        patch("plugins.memory.load_memory_provider") as load_memory_provider,
        patch("agent.model_metadata.get_model_context_length", return_value=204_800),
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        from run_agent import AIAgent

        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=False,
        )

    assert agent._memory_manager is None
    from_global_config.assert_not_called()
    load_memory_provider.assert_not_called()
    save_config.assert_not_called()


def test_aiagent_forwards_user_id_alt_to_memory_provider():
    provider = RecordingMemoryProvider()
    cfg = {"memory": {"provider": "recording"}, "agent": {}}

    with (
        patch("hermes_cli.config.load_config", return_value=cfg),
        patch("plugins.memory.load_memory_provider", return_value=provider),
        patch("agent.model_metadata.get_model_context_length", return_value=204_800),
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        from run_agent import AIAgent

        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=False,
            session_id="sess-alt",
            platform="feishu",
            user_id="open-id",
            user_id_alt="union-id",
        )

    assert agent._memory_manager is not None
    assert provider.init_session_id == "sess-alt"
    assert provider.init_kwargs["user_id"] == "open-id"
    assert provider.init_kwargs["user_id_alt"] == "union-id"
    assert provider.init_kwargs["platform"] == "feishu"
    assert "warning_callback" not in provider.init_kwargs
    assert "status_callback" not in provider.init_kwargs


def test_typed_non_owner_context_does_not_initialize_external_memory_provider():
    provider = RecordingMemoryProvider()
    cfg = {"memory": {"provider": "recording"}, "agent": {}}
    reset_session_vars()

    with (
        bind_resolved_access_context(_access_context("family_standard")),
        patch("hermes_cli.config.load_config", return_value=cfg),
        patch("plugins.memory.load_memory_provider", return_value=provider) as load_memory_provider,
        patch("agent.model_metadata.get_model_context_length", return_value=204_800),
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        from run_agent import AIAgent

        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=False,
            session_id="sess-family",
            platform="telegram",
        )

    reset_session_vars()
    assert agent._memory_manager is None
    assert provider.init_kwargs is None
    load_memory_provider.assert_not_called()


def test_builtin_memory_hydration_failure_clears_store_and_flags():
    cfg = {
        "memory": {
            "memory_enabled": True,
            "user_profile_enabled": True,
            "provider": "",
        },
        "agent": {},
    }

    class FailingMemoryStore:
        def __init__(self, **_kwargs):
            pass

        def load_from_disk(self):
            raise RuntimeError("memory_access_denied")

    with (
        patch("hermes_cli.config.load_config", return_value=cfg),
        patch("tools.memory_tool.MemoryStore", FailingMemoryStore),
        patch("plugins.memory.load_memory_provider") as load_memory_provider,
        patch("agent.model_metadata.get_model_context_length", return_value=204_800),
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        from run_agent import AIAgent

        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=False,
        )

    assert agent._memory_store is None
    assert agent._memory_enabled is False
    assert agent._user_profile_enabled is False
    load_memory_provider.assert_not_called()


class CoreShadowProvider:
    """Provider that tries to register tools shadowing built-in core tools."""

    name = "core-shadow"

    def get_tool_schemas(self):
        return [
            {"name": "clarify", "description": "shadows built-in clarify"},
            {"name": "delegate_task", "description": "shadows built-in delegate"},
            {"name": "honcho_search", "description": "legit memory tool"},
        ]


def test_aiagent_requires_memory_context_only_when_multiplex_active():
    cfg = {
        "memory": {
            "memory_enabled": True,
            "user_profile_enabled": True,
            "provider": "",
        },
        "agent": {},
    }
    captured = []

    class RecordingMemoryStore:
        def __init__(self, **kwargs):
            captured.append(dict(kwargs))

        def load_from_disk(self):
            pass

    with (
        patch("hermes_cli.config.load_config", return_value=cfg),
        patch("tools.memory_tool.MemoryStore", RecordingMemoryStore),
        patch("agent.secret_scope.is_multiplex_active", return_value=True),
        patch("plugins.memory.load_memory_provider") as load_memory_provider,
        patch("agent.model_metadata.get_model_context_length", return_value=204_800),
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        from run_agent import AIAgent

        AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=False,
        )

    assert captured[0]["require_access_context"] is True
    load_memory_provider.assert_not_called()


def test_aiagent_stores_session_scope_from_resolved_access_context():
    cfg = {"memory": {"provider": ""}, "agent": {}}
    context = _access_context()
    set_session_vars(resolved_access_context=context)
    try:
        with _patched_agent_init(cfg):
            from run_agent import AIAgent

            agent = AIAgent(
                api_key="test-key-1234567890",
                base_url="https://openrouter.ai/api/v1",
                quiet_mode=True,
                skip_context_files=True,
                skip_memory=True,
            )
    finally:
        reset_session_vars()

    assert agent._session_scope_required is True
    assert agent._session_scope == {
        "profile_name": "profile",
        "source": "telegram",
        "chat_type": "dm",
        "chat_id": "10001",
        "thread_id": "",
        "user_id": "10001",
        "is_dm": True,
    }


def test_aiagent_multiplex_missing_context_stores_invalid_scope():
    cfg = {"memory": {"provider": ""}, "agent": {}}
    reset_session_vars()
    with _patched_agent_init(cfg):
        from run_agent import AIAgent

        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )

    assert agent._session_scope_required is True
    assert agent._session_scope == {}


def test_compression_passes_agent_session_scope_to_archive_and_compact(tmp_path):
    from hermes_state import SessionDB
    from run_agent import AIAgent

    cfg = {"memory": {"provider": ""}, "agent": {}}
    context = _access_context()
    expected_scope = {
        "profile_name": "profile",
        "source": "telegram",
        "chat_type": "dm",
        "chat_id": "10001",
        "thread_id": "",
        "user_id": "10001",
        "is_dm": True,
    }
    db = SessionDB(tmp_path / "state.db")
    db.create_session(
        "session-a",
        source="telegram",
        profile_name="profile",
        chat_type="dm",
        chat_id="10001",
        thread_id="",
        user_id="10001",
    )
    db.append_message("session-a", role="user", content="before")
    db._conn.commit()
    original_archive = db.archive_and_compact
    captured = {}

    def archive_spy(*args, **kwargs):
        captured["session_scope"] = kwargs.get("session_scope")
        return original_archive(*args, **kwargs)

    db.archive_and_compact = archive_spy
    set_session_vars(resolved_access_context=context)
    try:
        with _patched_agent_init(cfg):
            agent = AIAgent(
                api_key="test-key-1234567890",
                base_url="https://openrouter.ai/api/v1",
                quiet_mode=True,
                skip_context_files=True,
                skip_memory=True,
                session_db=db,
                session_id="session-a",
            )
    finally:
        reset_session_vars()

    compressor = MagicMock()
    compressor.compress.return_value = [{"role": "user", "content": "summary"}]
    compressor.compression_count = 1
    compressor._last_compression_made_progress = True
    compressor._last_summary_fallback_used = False
    compressor._last_summary_error = None
    compressor._last_compress_aborted = False
    compressor._last_aux_model_failure_model = None
    compressor._last_aux_model_failure_error = None
    agent.context_compressor = compressor
    agent.compression_in_place = True
    agent._compression_feasibility_checked = True
    agent.commit_memory_session = MagicMock()

    compressed, _prompt = agent._compress_context(
        [{"role": "user", "content": "before"}],
        "system",
        approx_tokens=100,
        force=True,
    )

    assert compressed == [{"role": "user", "content": "summary"}]
    assert captured["session_scope"] == expected_scope
    assert agent._last_compaction_in_place is True


def test_multiplex_missing_context_cannot_compact_in_place(tmp_path):
    from hermes_state import SessionDB
    from run_agent import AIAgent

    cfg = {"memory": {"provider": ""}, "agent": {}}
    db = SessionDB(tmp_path / "state.db")
    db.create_session("session-a", source="telegram")
    db.append_message("session-a", role="user", content="before")
    db._conn.commit()
    reset_session_vars()
    with _patched_agent_init(cfg):
        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            session_db=db,
            session_id="session-a",
        )

    compressor = MagicMock()
    compressor.compress.return_value = [{"role": "user", "content": "summary"}]
    compressor.compression_count = 1
    compressor._last_compression_made_progress = True
    compressor._last_summary_fallback_used = False
    compressor._last_summary_error = None
    compressor._last_compress_aborted = False
    compressor._last_aux_model_failure_model = None
    compressor._last_aux_model_failure_error = None
    agent.context_compressor = compressor
    agent.compression_in_place = True
    agent._compression_feasibility_checked = True
    agent.commit_memory_session = MagicMock()

    with pytest.raises(RuntimeError, match="session compaction denied by session scope"):
        agent._compress_context(
            [{"role": "user", "content": "before"}],
            "system",
            approx_tokens=100,
            force=True,
        )

    assert agent._session_scope == {}
    assert getattr(agent, "_last_compaction_in_place", False) is False
    assert [m["content"] for m in db.get_messages("session-a")] == ["before"]
    assert db.get_session("session-a")["message_count"] == 1


def test_core_tool_names_rejected_from_memory_routing_table():
    """Memory tools shadowing core tool names are rejected at registration (#40466).

    Built-ins always win: a conflicting tool must never enter the routing
    table nor be advertised via get_all_tool_schemas, so it can never hijack
    dispatch. The non-conflicting tool is preserved.
    """
    from agent.memory_manager import MemoryManager

    mm = MemoryManager()
    mm.add_provider(CoreShadowProvider())

    # Reserved names never enter the routing table
    assert not mm.has_tool("clarify")
    assert not mm.has_tool("delegate_task")
    assert "clarify" not in mm._tool_to_provider
    assert "delegate_task" not in mm._tool_to_provider

    # Non-conflicting tool survives
    assert mm.has_tool("honcho_search")
    assert "honcho_search" in mm._tool_to_provider

    # Manager never advertises a schema it would refuse to route
    schema_names = {s.get("name") for s in mm.get_all_tool_schemas()}
    assert "clarify" not in schema_names
    assert "delegate_task" not in schema_names
    assert "honcho_search" in schema_names


def test_aiagent_forwards_warning_callback_to_cli_memory_provider():
    provider = RecordingMemoryProvider()
    cfg = {"memory": {"provider": "recording"}, "agent": {}}

    with (
        patch("hermes_cli.config.load_config", return_value=cfg),
        patch("plugins.memory.load_memory_provider", return_value=provider),
        patch("agent.model_metadata.get_model_context_length", return_value=204_800),
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        from run_agent import AIAgent

        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=False,
            session_id="sess-cli",
            platform="cli",
        )

    assert agent._memory_manager is not None
    assert provider.init_session_id == "sess-cli"
    assert provider.init_kwargs["platform"] == "cli"
    assert provider.init_kwargs["warning_callback"] == agent._emit_warning
    assert provider.init_kwargs["status_callback"] == agent._emit_status
