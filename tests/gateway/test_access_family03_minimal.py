"""Minimal access_registry for family-03 must bind context and clear the image guard."""
from pathlib import Path

import pytest

from agent.secret_scope import set_multiplex_active
from gateway.access_context import get_resolved_access_context
from agent.access_paths import profile_read_block_error
from gateway.access_runtime import turn_access_scope
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter
from gateway.run import GatewayRunner


class Transport(BasePlatformAdapter):
    pass


Transport.__abstractmethods__ = frozenset()


def _minimal_registry():
    return {
        "roles": {
            "family": {
                "capabilities": ["memory_search", "public_web"],
                "active": True,
            },
        },
        "profiles": ["family-03"],
        "scope_capabilities": {
            "private": ["memory_search", "public_web"],
        },
        "backend_capabilities": ["memory_search", "public_web"],
        "principal_bindings": [
            {
                "principal_id": "dm-family-03",
                "role_id": "family",
                "profile_id": "family-03",
                "transport_identity": {
                    "platform": "telegram",
                    "account": "family-03-bot",
                    "peer_kind": "dm",
                    "user_id": "1001",
                    "chat_id": "1001",
                },
                "conversation_scope": "private",
                "delivery_target": {
                    "platform": "telegram",
                    "account": "family-03-bot",
                    "peer_kind": "dm",
                    "chat_id": "1001",
                },
                "active": True,
            },
        ],
        "shared_scope_bindings": [],
    }


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    profile = tmp_path / ".hermes" / "profiles" / "family-03"
    profile.mkdir(parents=True)
    (profile / "config.yaml").write_text("{}");

    config = GatewayConfig.from_dict({
        "gateway": {"multiplex_profiles": True},
        "access_registry": _minimal_registry(),
    })
    assert config.access_registry is not None

    runner = object.__new__(GatewayRunner)
    runner.config = config
    runner.access_registry = config.access_registry
    runner._primary_profile_name = "default"

    adapter = Transport(
        PlatformConfig(enabled=True, extra={"account": "family-03-bot"}),
        Platform.TELEGRAM,
    )
    adapter.gateway_runner = runner
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._profile_adapters = {"family-03": {}}

    set_multiplex_active(True)
    try:
        yield runner, adapter, tmp_path
    finally:
        set_multiplex_active(False)


def test_family03_registry_binds_context_and_image_guard_passes(rig):
    runner, adapter, tmp_path = rig
    source = adapter.build_source(chat_id="1001", user_id="1001")

    with turn_access_scope(source, runner=runner), runner._profile_scope_for_source(source):
        context = get_resolved_access_context()
        assert context is not None
        assert context.profile_id == "family-03"
        image = tmp_path / ".hermes" / "profiles" / "family-03" / "cache" / "images" / "fixture.jpg"
        assert profile_read_block_error(str(image)) is None
