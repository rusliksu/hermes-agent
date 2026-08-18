from __future__ import annotations

from gateway.access_registry import DeliveryTarget, ResolvedAccessContext
import gateway.run as gateway_run
from toolsets import resolve_toolset


def _context(role_id: str) -> ResolvedAccessContext:
    return ResolvedAccessContext(
        principal_id="synthetic-principal",
        role_id=role_id,
        profile_id="synthetic-profile",
        conversation_scope="synthetic-scope",
        capabilities=frozenset(),
        delivery_target=DeliveryTarget(
            platform="telegram",
            account="synthetic-account",
            peer_kind="group" if role_id == "shared_room" else "dm",
            chat_id="synthetic-chat",
        ),
    )


def _schemas(toolsets: list[str]) -> set[str]:
    return {
        tool
        for toolset in toolsets
        for tool in resolve_toolset(toolset)
    }


def test_readiness_surface_has_browser_and_execution_baseline():
    configured = [
        "browser",
        "code_execution",
        "delegation",
        "file",
        "memory",
        "session_search",
        "terminal",
        "web",
    ]
    effective = gateway_run.GatewayRunner._toolsets_for_resolved_access_context(
        configured,
        _context("shared_room"),
    )
    assert effective == configured
    assert {
        "browser_navigate",
        "browser_snapshot",
        "web_search",
        "terminal",
        "process",
        "read_file",
        "execute_code",
        "delegate_task",
        "memory",
        "session_search",
    } <= _schemas(effective)


def test_readiness_keeps_admin_delta_owner_only():
    configured = ["browser", "discord_admin", "file", "global_cron", "memory"]
    owner = gateway_run.GatewayRunner._toolsets_for_resolved_access_context(
        configured,
        _context("owner"),
    )
    family = gateway_run.GatewayRunner._toolsets_for_resolved_access_context(
        configured,
        _context("family"),
    )
    assert owner == configured
    assert family == ["browser", "file", "memory"]
    assert set(family).isdisjoint({"discord_admin", "global_cron"})


def test_readiness_does_not_count_runtime_only_kanban_as_user_surface():
    configured = ["browser", "kanban", "memory"]
    shared = gateway_run.GatewayRunner._toolsets_for_resolved_access_context(
        configured,
        _context("shared_room"),
    )
    assert shared == ["browser", "memory"]
