from __future__ import annotations

import pytest

from gateway.access_registry import (
    DeliveryTarget,
    ResolvedAccessContext,
    shared_memory_namespace_for_access_context,
)
from gateway.config import Platform
import gateway.run as gateway_run
from gateway.session import SessionSource
from toolsets import resolve_toolset


USER_TOOLSETS = [
    "browser",
    "code_execution",
    "cronjob",
    "delegation",
    "file",
    "image_gen",
    "memory",
    "session_search",
    "terminal",
    "tts",
    "versioned_custom_mcp",
    "vision",
    "web",
]
ADMIN_TOOLSETS = ["discord_admin", "global_cron", "host_shell"]


def _context(role_id: str, *, profile: str, scope: str, peer_kind: str) -> ResolvedAccessContext:
    return ResolvedAccessContext(
        principal_id=f"synthetic-{role_id}",
        role_id=role_id,
        profile_id=profile,
        conversation_scope=scope,
        capabilities=frozenset(),
        delivery_target=DeliveryTarget(
            platform="telegram",
            account="synthetic-account",
            peer_kind=peer_kind,
            chat_id=f"synthetic-{scope}",
        ),
    )


def _source(context: ResolvedAccessContext) -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id=context.delivery_target.chat_id,
        chat_type=context.delivery_target.peer_kind,
        user_id="synthetic-user",
        resolved_access_context=context,
    )


def _schemas(toolsets: list[str]) -> set[str]:
    return {
        tool
        for toolset in toolsets
        for tool in resolve_toolset(toolset)
    }


@pytest.mark.parametrize(
    ("role_id", "peer_kind"),
    [("family", "dm"), ("shared_room", "group")],
)
def test_authenticated_roles_share_one_non_admin_baseline(role_id, peer_kind):
    configured = USER_TOOLSETS + ADMIN_TOOLSETS + ["kanban"]
    effective = gateway_run.GatewayRunner._toolsets_for_resolved_access_context(
        configured,
        _context(role_id, profile="synthetic-profile", scope="synthetic-scope", peer_kind=peer_kind),
    )
    assert effective == sorted(USER_TOOLSETS)
    assert _schemas(effective) == _schemas(USER_TOOLSETS)


def test_owner_keeps_baseline_and_admin_overlay_while_unknown_fails_closed():
    configured = USER_TOOLSETS + ADMIN_TOOLSETS + ["kanban"]
    owner = gateway_run.GatewayRunner._toolsets_for_resolved_access_context(
        configured,
        _context("owner", profile="owner-profile", scope="private", peer_kind="dm"),
    )
    unknown = gateway_run.GatewayRunner._toolsets_for_resolved_access_context(
        configured,
        _context("unknown", profile="unknown-profile", scope="unknown", peer_kind="group"),
    )
    assert owner == configured
    assert unknown == []
    assert set(ADMIN_TOOLSETS).issubset(owner)


def test_shared_profile_expected_tools_cover_representative_user_operations():
    context = _context(
        "shared_room",
        profile="room-profile",
        scope="room-root",
        peer_kind="group",
    )
    toolsets, expected_tools = gateway_run.GatewayRunner._shared_tool_profile_for_source(
        _source(context),
        configured_toolsets=USER_TOOLSETS + ADMIN_TOOLSETS + ["kanban"],
    )
    assert toolsets == sorted(USER_TOOLSETS)
    assert expected_tools == frozenset(_schemas(USER_TOOLSETS))
    assert {
        "browser_navigate",
        "browser_snapshot",
        "terminal",
        "process",
        "read_file",
        "write_file",
        "execute_code",
        "delegate_task",
        "memory",
        "session_search",
        "deliver_artifact",
    } <= expected_tools


def test_shared_memory_namespace_stays_bound_to_exact_scope():
    root = _context(
        "shared_room",
        profile="room-profile",
        scope="room-root",
        peer_kind="group",
    )
    topic = _context(
        "shared_room",
        profile="room-profile",
        scope="room-topic",
        peer_kind="group",
    )
    assert shared_memory_namespace_for_access_context(root) != shared_memory_namespace_for_access_context(topic)


@pytest.mark.parametrize("malformed", [None, object()])
def test_malformed_context_fails_closed_without_reopening_tools(malformed):
    assert gateway_run.GatewayRunner._toolsets_for_resolved_access_context(
        USER_TOOLSETS,
        malformed,
    ) == ([] if malformed is not None else USER_TOOLSETS)
