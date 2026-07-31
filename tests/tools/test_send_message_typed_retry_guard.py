"""Typed Telegram delivery must not fall back from topic to root chat."""

import asyncio
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.access_registry import DeliveryTarget, ResolvedAccessContext
from gateway.session_context import bind_resolved_access_context
from tools.send_message_tool import _send_telegram


@pytest.fixture
def telegram_bot_factory(monkeypatch):
    parse_mode = SimpleNamespace(MARKDOWN_V2="MarkdownV2", HTML="HTML")
    constants_mod = SimpleNamespace(ParseMode=parse_mode)

    class BotFactory:
        bots = []

        def __new__(cls, *_args, **_kwargs):
            return cls.bots.pop(0)

    telegram_mod = types.ModuleType("telegram")
    telegram_mod.Bot = BotFactory
    telegram_mod.MessageEntity = lambda **kw: SimpleNamespace(**kw)
    telegram_mod.constants = constants_mod
    monkeypatch.setitem(sys.modules, "telegram", telegram_mod)
    monkeypatch.setitem(sys.modules, "telegram.constants", constants_mod)
    monkeypatch.setattr("gateway.platforms.base.resolve_proxy_url", lambda *_a, **_kw: None)
    return BotFactory


def _typed_topic_context():
    return ResolvedAccessContext(
        principal_id="principal-room",
        role_id="shared_room",
        profile_id="room-profile",
        conversation_scope="telegram:room:-1001234567890:17585",
        capabilities=frozenset({"send_message"}),
        delivery_target=DeliveryTarget(
            platform="telegram",
            account="bot-main",
            peer_kind="group",
            chat_id="-1001234567890",
            thread_id="17585",
        ),
    )


def test_typed_topic_thread_not_found_denies_before_root_text_retry(telegram_bot_factory):
    bot = SimpleNamespace()
    bot.send_message = AsyncMock(side_effect=[
        Exception("Bad Request: message thread not found"),
        SimpleNamespace(message_id=22),
    ])
    telegram_bot_factory.bots.append(bot)

    with bind_resolved_access_context(_typed_topic_context()):
        result = asyncio.run(
            _send_telegram("tok", "-1001234567890", "hello", thread_id="17585")
        )

    assert result == {
        "error": "send_message_access_denied",
        "reason": "delivery_target_mismatch",
    }
    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.kwargs["message_thread_id"] == 17585


def test_typed_topic_thread_not_found_denies_before_root_media_retry(
    telegram_bot_factory, tmp_path
):
    media_path = tmp_path / "doc.txt"
    media_path.write_text("payload")
    bot = SimpleNamespace()
    bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=1))
    bot.send_document = AsyncMock(side_effect=[
        Exception("Bad Request: message thread not found"),
        SimpleNamespace(message_id=33),
    ])
    telegram_bot_factory.bots.append(bot)

    with bind_resolved_access_context(_typed_topic_context()):
        result = asyncio.run(
            _send_telegram(
                "tok",
                "-1001234567890",
                "",
                media_files=[(str(media_path), False)],
                thread_id="17585",
            )
        )

    assert result == {
        "error": "send_message_access_denied",
        "reason": "delivery_target_mismatch",
    }
    bot.send_document.assert_awaited_once()
    assert bot.send_document.await_args.kwargs["message_thread_id"] == 17585


def test_legacy_thread_not_found_still_retries_without_thread_id(telegram_bot_factory):
    bot = SimpleNamespace()
    bot.send_message = AsyncMock(side_effect=[
        Exception("Bad Request: message thread not found"),
        SimpleNamespace(message_id=44),
    ])
    telegram_bot_factory.bots.append(bot)

    result = asyncio.run(
        _send_telegram("tok", "-1001234567890", "hello", thread_id="17585")
    )

    assert result["success"] is True
    assert bot.send_message.await_count == 2
    assert bot.send_message.await_args_list[0].kwargs["message_thread_id"] == 17585
    assert "message_thread_id" not in bot.send_message.await_args_list[1].kwargs
