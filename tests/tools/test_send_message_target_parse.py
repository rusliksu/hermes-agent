"""Parser-only and lightweight routing tests for send_message targets.

These stay separate from ``test_send_message_tool.py`` because that module
skips wholesale when optional Telegram dependencies are not installed.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from gateway.access_registry import DeliveryTarget, ResolvedAccessContext
from gateway.config import Platform
from gateway.session_context import bind_resolved_access_context
from tools.send_message_tool import _parse_target_ref, _send_to_platform, send_message_tool


def _run_async_immediately(coro):
    return asyncio.run(coro)


def _access_context(
    *,
    platform: str = "sendguard",
    chat_id: str = "12345",
    thread_id: str | None = None,
) -> ResolvedAccessContext:
    scope_thread = thread_id or "root"
    return ResolvedAccessContext(
        principal_id="principal-family",
        role_id="shared_room",
        profile_id="family-profile",
        conversation_scope=f"{platform}:shared:{chat_id}:{scope_thread}",
        capabilities=frozenset({"send_message"}),
        delivery_target=DeliveryTarget(
            platform=platform,
            account="bot-main",
            peer_kind="group",
            chat_id=chat_id,
            thread_id=thread_id,
        ),
    )


@pytest.fixture
def fake_sendguard(monkeypatch):
    from gateway.platform_registry import PlatformEntry, platform_registry

    sender = AsyncMock(return_value={"success": True, "message_id": "msg-1"})
    entry = PlatformEntry(
        name="sendguard",
        label="Send Guard",
        adapter_factory=lambda _cfg: None,
        check_fn=lambda: True,
        standalone_sender_fn=sender,
    )
    platform_registry.register(entry)
    monkeypatch.setattr("gateway.run._gateway_runner_ref", lambda: None)
    try:
        yield Platform("sendguard"), SimpleNamespace(enabled=True, token=None, extra={}), sender
    finally:
        platform_registry.unregister("sendguard")


def _sendguard_config(platform, pconfig):
    return SimpleNamespace(
        platforms={platform: pconfig},
        get_home_channel=lambda _platform: None,
    )


def test_photon_e164_target_is_explicit() -> None:
    chat_id, thread_id, is_explicit = _parse_target_ref("photon", "+15551234567")

    assert chat_id == "+15551234567"
    assert thread_id is None
    assert is_explicit is True


def test_e164_target_still_requires_phone_platform() -> None:
    assert _parse_target_ref("matrix", "+15551234567")[2] is False


def test_whatsapp_group_jid_target_is_explicit() -> None:
    chat_id, thread_id, is_explicit = _parse_target_ref(
        "whatsapp", "120363408391911677@g.us"
    )

    assert chat_id == "120363408391911677@g.us"
    assert thread_id is None
    assert is_explicit is True


def test_whatsapp_native_jids_are_explicit() -> None:
    assert _parse_target_ref("whatsapp", "19255551234@s.whatsapp.net")[2] is True
    assert _parse_target_ref("whatsapp", "149606612619433@lid")[2] is True
    assert _parse_target_ref("whatsapp", "status@broadcast")[2] is True
    assert _parse_target_ref("whatsapp", "120363000000000000@newsletter")[2] is True


def test_whatsapp_jid_suffix_only_matches_whatsapp() -> None:
    assert _parse_target_ref("telegram", "120363408391911677@g.us")[2] is False
    assert _parse_target_ref("signal", "149606612619433@lid")[2] is False


def test_whatsapp_friendly_name_still_uses_directory_resolution() -> None:
    assert _parse_target_ref("whatsapp", "general")[2] is False


def test_send_message_routes_whatsapp_group_jid_without_home_fallback() -> None:
    whatsapp_cfg = SimpleNamespace(enabled=True, token=None, extra={"api_url": "http://bridge"})
    config = SimpleNamespace(
        platforms={Platform.WHATSAPP: whatsapp_cfg},
        get_home_channel=lambda _platform: SimpleNamespace(chat_id="15551234567@s.whatsapp.net"),
    )

    with patch("gateway.config.load_gateway_config", return_value=config), \
         patch("tools.interrupt.is_interrupted", return_value=False), \
         patch("gateway.channel_directory.resolve_channel_name", side_effect=AssertionError("raw JID should not resolve via directory")), \
         patch("model_tools._run_async", side_effect=_run_async_immediately), \
         patch("tools.send_message_tool._send_to_platform", new=AsyncMock(return_value={"success": True})) as send_mock, \
         patch("gateway.mirror.mirror_to_session", return_value=True):
        result = json.loads(
            send_message_tool(
                {
                    "action": "send",
                    "target": "whatsapp:120363408391911677@g.us",
                    "message": "hello group",
                }
            )
        )

    assert result["success"] is True
    assert "note" not in result
    send_mock.assert_awaited_once_with(
        Platform.WHATSAPP,
        whatsapp_cfg,
        "120363408391911677@g.us",
        "hello group",
        thread_id=None,
        media_files=[],
        force_document=False,
    )


def test_typed_send_message_exact_delivery_target_is_allowed(fake_sendguard) -> None:
    platform, pconfig, sender = fake_sendguard
    config = _sendguard_config(platform, pconfig)

    with bind_resolved_access_context(_access_context()), \
         patch("gateway.config.load_gateway_config", return_value=config), \
         patch("tools.interrupt.is_interrupted", return_value=False), \
         patch("model_tools._run_async", side_effect=_run_async_immediately), \
         patch("gateway.mirror.mirror_to_session", return_value=True):
        result = json.loads(
            send_message_tool(
                {
                    "action": "send",
                    "target": "sendguard:12345",
                    "message": "exact send",
                }
            )
        )

    assert result == {"success": True, "message_id": "msg-1", "mirrored": True}
    sender.assert_awaited_once_with(
        pconfig,
        "12345",
        "exact send",
        thread_id=None,
        media_files=[],
        force_document=False,
    )


def test_typed_send_message_foreign_chat_denied_before_sender(fake_sendguard) -> None:
    platform, pconfig, sender = fake_sendguard
    config = _sendguard_config(platform, pconfig)

    with bind_resolved_access_context(_access_context()), \
         patch("gateway.config.load_gateway_config", return_value=config), \
         patch("tools.interrupt.is_interrupted", return_value=False), \
         patch("model_tools._run_async", side_effect=_run_async_immediately), \
         patch("gateway.mirror.mirror_to_session", return_value=True) as mirror:
        result = json.loads(
            send_message_tool(
                {
                    "action": "send",
                    "target": "sendguard:99999",
                    "message": "foreign send",
                }
            )
        )

    assert result == {
        "error": "send_message_access_denied",
        "reason": "delivery_target_mismatch",
    }
    sender.assert_not_awaited()
    mirror.assert_not_called()


@pytest.mark.parametrize(
    ("context", "platform_name", "chat_id", "thread_id", "reason"),
    [
        (_access_context(platform="telegram"), "sendguard", "12345", None, "delivery_target_mismatch"),
        (_access_context(thread_id="topic-7"), "sendguard", "12345", "topic-8", "delivery_target_mismatch"),
        ({"principal_id": "not-a-resolved-access-context"}, "sendguard", "12345", None, "malformed_resolved_access_context"),
    ],
)
def test_typed_send_boundary_denies_wrong_platform_thread_or_malformed_context(
    fake_sendguard,
    context,
    platform_name,
    chat_id,
    thread_id,
    reason,
) -> None:
    platform, pconfig, sender = fake_sendguard
    assert platform.value == platform_name

    with bind_resolved_access_context(context):
        result = asyncio.run(
            _send_to_platform(platform, pconfig, chat_id, "blocked", thread_id=thread_id)
        )

    assert result == {"error": "send_message_access_denied", "reason": reason}
    sender.assert_not_awaited()


def test_untyped_legacy_send_message_keeps_target_routing(fake_sendguard) -> None:
    platform, pconfig, sender = fake_sendguard
    config = _sendguard_config(platform, pconfig)

    with patch("gateway.config.load_gateway_config", return_value=config), \
         patch("tools.interrupt.is_interrupted", return_value=False), \
         patch("model_tools._run_async", side_effect=_run_async_immediately), \
         patch("gateway.mirror.mirror_to_session", return_value=False):
        result = json.loads(
            send_message_tool(
                {
                    "action": "send",
                    "target": "sendguard:99999",
                    "message": "legacy send",
                }
            )
        )

    assert result == {"success": True, "message_id": "msg-1"}
    sender.assert_awaited_once_with(
        pconfig,
        "99999",
        "legacy send",
        thread_id=None,
        media_files=[],
        force_document=False,
    )
