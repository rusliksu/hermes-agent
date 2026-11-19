"""Late memory callbacks must retain their original authority and storage home."""
from pathlib import Path

import pytest

from agent.memory_manager import MemoryManager
from agent.memory_provider import MemoryProvider
from gateway.access_registry import DeliveryTarget, ResolvedAccessContext
from gateway.run import _profile_runtime_scope
from gateway.access_context import bind_resolved_access_context, get_resolved_access_context
from hermes_constants import get_hermes_home
from tools.memory_tool_store import MemoryStore


def context(profile="alpha", *, thread=None):
    room = thread is not None
    return ResolvedAccessContext(principal_id="member-" + profile,
        role_id="shared_room" if room else "family", profile_id=profile,
        conversation_scope="registered-room" if room else "dm-" + profile,
        capabilities=frozenset({"room_memory" if room else "memory_search"}),
        delivery_target=DeliveryTarget("telegram", "fixture-bot", "group" if room else "dm",
                                       "-1003" if room else "1001", thread))


@pytest.fixture
def homes(tmp_path, monkeypatch):
    root = tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(root))
    for name in ("alpha", "beta"):
        home = root / "profiles" / name
        home.mkdir(parents=True)
        (home / "config.yaml").write_text("{}\n")
    return {name: root / "profiles" / name for name in ("alpha", "beta")}


class ProbeProvider(MemoryProvider):
    name = "builtin"

    def __init__(self):
        self.records = []
        self.kwargs = {}

    def record(self, event):
        ctx = get_resolved_access_context()
        self.records.append((event, ctx.profile_id if ctx else "unset", get_hermes_home().name))

    def is_available(self):
        return True

    def initialize(self, session_id, **kwargs):
        self.kwargs = kwargs
        self.record("initialize")

    def get_tool_schemas(self):
        self.record("schemas")
        return []

    def system_prompt_block(self):
        self.record("prompt")
        return "fixture"

    def prefetch(self, query, **kwargs):
        self.record("prefetch")
        return "fixture"

    def sync_turn(self, user_content, assistant_content, **kwargs):
        self.record("sync")

    def on_session_end(self, messages):
        self.record("end")

    def shutdown(self):
        self.record("shutdown")


def test_callbacks_and_real_background_worker_keep_initial_snapshot(homes):
    provider = ProbeProvider()
    with _profile_runtime_scope(homes["alpha"]), bind_resolved_access_context(context()):
        manager = MemoryManager()
        manager.add_provider(provider)
        manager.initialize_all("fixture-session")
    with _profile_runtime_scope(homes["beta"]), bind_resolved_access_context(context("beta")):
        try:
            manager.build_system_prompt()
            manager.prefetch_all("Recall the fixture project", session_id="fixture-session")
            manager.sync_all("fixture user", "fixture reply", session_id="fixture-session")
            assert manager.flush_pending(timeout=2)
            manager.on_session_end([])
        finally:
            manager.shutdown_all()
    assert {event for event, _, _ in provider.records} >= {"schemas", "initialize", "prompt", "prefetch", "sync", "end", "shutdown"}
    assert all((profile, home) == ("alpha", "alpha") for _, profile, home in provider.records)


def test_provider_receives_derived_scope_instead_of_caller_selector(homes):
    with _profile_runtime_scope(homes["alpha"]), bind_resolved_access_context(context()):
        manager = MemoryManager()
        provider = ProbeProvider()
        manager.add_provider(provider)
        manager.initialize_all("fixture-session", memory_scope="forged-owner-namespace")
        manager.shutdown_all()
    assert provider.kwargs["memory_scope"] == "memory/personal/36e63a633e271388fac5e7f0d0dbf843de0f2e5e308da2ae3aa95102d2f0e327"
    assert "resolved_access_context" not in provider.kwargs


def test_builtin_store_separates_two_topics_and_reloads_original(homes):
    with _profile_runtime_scope(homes["alpha"]), bind_resolved_access_context(context(thread="11")):
        first = MemoryStore()
        first.load_from_disk()
        assert first.add("memory", "fixture topic eleven")["success"]
    with _profile_runtime_scope(homes["alpha"]), bind_resolved_access_context(context(thread="22")):
        second = MemoryStore()
        second.load_from_disk()
        assert second.memory_entries == []
        assert second.add("memory", "fixture topic twenty-two")["success"]
    with _profile_runtime_scope(homes["alpha"]), bind_resolved_access_context(context(thread="11")):
        reloaded = MemoryStore()
        reloaded.load_from_disk()
        assert reloaded.memory_entries == ["fixture topic eleven"]
    parent = homes["alpha"] / "memories" / "shared" / "access"
    assert (parent / "93193a1d9a681083caab19e497e97f91b744e4aac75e05786c976cf54544b48b" / "MEMORY.md").read_text().strip() == "fixture topic eleven"
    assert (parent / "c96074a5071080ebba7225aa58607cafeb1fd6d741d04c1a1860a629c5f0842f" / "MEMORY.md").read_text().strip() == "fixture topic twenty-two"


def test_store_from_another_topic_cannot_leak_snapshot_or_accept_tool_write(homes):
    import json
    from tools.memory_access import MemoryAccessDenied
    from tools.memory_tool import memory_tool
    with _profile_runtime_scope(homes["alpha"]), bind_resolved_access_context(context(thread="11")):
        first = MemoryStore()
        first.load_from_disk()
        assert first.add("memory", "fixture private topic eleven")["success"]
        first.load_from_disk()
    with _profile_runtime_scope(homes["alpha"]), bind_resolved_access_context(context(thread="22")):
        with pytest.raises(MemoryAccessDenied):
            first.format_for_system_prompt("memory")
        refused = json.loads(memory_tool(action="add", content="foreign fixture", store=first))
        assert refused["success"] is False
        assert "fixture private topic eleven" not in json.dumps(refused)
