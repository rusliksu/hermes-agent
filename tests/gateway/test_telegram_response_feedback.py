"""Behavior tests for private, owner-bound Telegram response feedback."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.telegram.adapter import TelegramAdapter


_MISSING = object()


def _adapter(*, enabled=True, extra=None):
    config_extra = {}
    if enabled is not _MISSING:
        config_extra["feedback_buttons"] = enabled
    config_extra.update(extra or {})
    adapter = TelegramAdapter(PlatformConfig(
        enabled=True,
        token="fake-token",
        extra=config_extra,
    ))
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=42))
    bot.send_chat_action = AsyncMock()
    bot.edit_message_reply_markup = AsyncMock()
    adapter._bot = bot
    return adapter


def _query(*, user_id=123, chat_id=123, message_id=42, thread_id=None):
    query = MagicMock()
    query.from_user = SimpleNamespace(id=user_id, first_name="User")
    query.message = SimpleNamespace(
        chat_id=chat_id,
        message_id=message_id,
        message_thread_id=thread_id,
        chat=SimpleNamespace(id=chat_id, type="private"),
    )
    query.answer = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    return query


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, "true", "yes", "on", "1"])
async def test_final_dm_response_gets_four_feedback_choices(monkeypatch, enabled):
    import plugins.platforms.telegram.adapter as tg

    class _Button:
        def __init__(self, text, callback_data=None, **_kwargs):
            self.text = text
            self.callback_data = callback_data

    class _Markup:
        def __init__(self, rows):
            self.inline_keyboard = rows

    monkeypatch.setattr(tg, "InlineKeyboardButton", _Button)
    monkeypatch.setattr(tg, "InlineKeyboardMarkup", _Markup)
    adapter = _adapter(enabled=enabled)

    result = await adapter.send("123", "Короткий ответ", metadata={"notify": True})

    assert result.success is True
    adapter._bot.edit_message_reply_markup.assert_awaited_once()
    markup = adapter._bot.edit_message_reply_markup.await_args.kwargs["reply_markup"]
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert [button.text for button in buttons] == [
        "👍 Полезно", "👎 Не то", "🐢 Медленно", "🌐 Лишний интернет",
    ]
    assert len(adapter._feedback_state) == 1


@pytest.mark.asyncio
async def test_final_streaming_edit_gets_feedback_controls():
    adapter = _adapter()
    adapter._bot.edit_message_text = AsyncMock()

    result = await adapter.edit_message(
        "123",
        "42",
        "Финальный ответ",
        finalize=True,
        metadata={"notify": True},
    )

    assert result.success is True
    adapter._bot.edit_message_reply_markup.assert_awaited_once()
    assert len(adapter._feedback_state) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enabled", "chat_id", "metadata"),
    [
        (False, "123", {"notify": True}),
        (_MISSING, "123", {"notify": True}),
        (None, "123", {"notify": True}),
        ("false", "123", {"notify": True}),
        ("off", "123", {"notify": True}),
        ("unknown", "123", {"notify": True}),
        (True, "123", None),
        (True, "-100123", {"notify": True}),
        (True, "123", {"notify": True, "thread_id": "7"}),
    ],
)
async def test_feedback_is_opt_in_final_dm_only(enabled, chat_id, metadata):
    adapter = _adapter(enabled=enabled)

    result = await adapter.send(chat_id, "Ответ", metadata=metadata)

    assert result.success is True
    adapter._bot.edit_message_reply_markup.assert_not_awaited()
    assert adapter._feedback_state == {}
    assert getattr(adapter, "_feedback_sampling_state", {}) == {}


@pytest.mark.asyncio
async def test_sampled_feedback_appears_on_tenth_eligible_response():
    adapter = _adapter(enabled="sampled")
    adapter._bot.send_message.side_effect = [
        SimpleNamespace(message_id=index) for index in range(1, 11)
    ]

    for index in range(10):
        result = await adapter.send("123", "Ответ", metadata={"notify": True})
        assert result.success is True
        assert adapter._bot.edit_message_reply_markup.await_count == (index + 1) // 10

    adapter._bot.edit_message_reply_markup.assert_awaited_once()
    assert len(adapter._feedback_state) == 1


@pytest.mark.asyncio
async def test_sampled_feedback_respects_cooldown(monkeypatch):
    import plugins.platforms.telegram.adapter as tg

    now = [0.0]
    monkeypatch.setattr(tg, "time", SimpleNamespace(monotonic=lambda: now[0]))
    adapter = _adapter(
        enabled="sampled",
        extra={"feedback_sample_every": 2, "feedback_cooldown_seconds": 1800},
    )
    adapter._bot.send_message.side_effect = [
        SimpleNamespace(message_id=index) for index in range(1, 7)
    ]

    for _ in range(4):
        await adapter.send("123", "Ответ", metadata={"notify": True})
    assert adapter._bot.edit_message_reply_markup.await_count == 1

    now[0] = 1800.0
    for _ in range(2):
        await adapter.send("123", "Ответ", metadata={"notify": True})
    assert adapter._bot.edit_message_reply_markup.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cooldown", ["nan", 10**10000], ids=["nan", "huge-integer"],
)
async def test_invalid_sampling_config_uses_safe_defaults(cooldown):
    adapter = _adapter(
        enabled="sampled",
        extra={"feedback_sample_every": "bad", "feedback_cooldown_seconds": cooldown},
    )
    assert adapter._feedback_sample_every() == adapter._FEEDBACK_SAMPLE_EVERY_DEFAULT
    assert (
        adapter._feedback_cooldown_seconds()
        == adapter._FEEDBACK_COOLDOWN_SECONDS_DEFAULT
    )
    adapter._bot.send_message.side_effect = [
        SimpleNamespace(message_id=index) for index in range(1, 11)
    ]

    for _ in range(10):
        result = await adapter.send("123", "Ответ", metadata={"notify": True})
        assert result.success is True

    adapter._bot.edit_message_reply_markup.assert_awaited_once()


def test_sampled_feedback_state_is_bounded_and_deduplicates_messages():
    adapter = _adapter(
        enabled="sampled",
        extra={"feedback_sample_every": 1, "feedback_cooldown_seconds": 0},
    )

    assert adapter._sampled_response_feedback_due("1", "42") is True
    assert adapter._sampled_response_feedback_due("1", "42") is False
    for chat_id in range(2, adapter._CALLBACK_STATE_LIMIT + 2):
        adapter._sampled_response_feedback_due(str(chat_id), "42")

    assert len(adapter._feedback_sampling_state) == adapter._CALLBACK_STATE_LIMIT


@pytest.mark.asyncio
async def test_owner_feedback_is_recorded_once_without_identifiers(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "123")
    adapter = _adapter()
    await adapter.send("123", "Секретный текст", metadata={"notify": True})
    nonce = next(iter(adapter._feedback_state))
    query = _query()

    await adapter._handle_response_feedback_callback(query, f"fb:{nonce}:slow")
    await adapter._handle_response_feedback_callback(query, f"fb:{nonce}:slow")

    records = [
        json.loads(line)
        for line in (tmp_path / "logs" / "response-feedback.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(records) == 1
    assert set(records[0]) == {"timestamp", "platform", "feedback", "has_topic"}
    assert records[0]["platform"] == "telegram"
    assert records[0]["feedback"] == "slow"
    assert records[0]["has_topic"] is False
    query.edit_message_reply_markup.assert_awaited_once_with(reply_markup=None)


@pytest.mark.asyncio
async def test_foreign_user_cannot_submit_bound_feedback(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "123,124")
    adapter = _adapter()
    await adapter.send("123", "Ответ", metadata={"notify": True})
    nonce = next(iter(adapter._feedback_state))

    await adapter._handle_response_feedback_callback(
        _query(user_id=124), f"fb:{nonce}:good",
    )

    assert not (tmp_path / "logs" / "response-feedback.jsonl").exists()
    assert nonce in adapter._feedback_state


@pytest.mark.asyncio
async def test_feedback_markup_failure_does_not_fail_response():
    adapter = _adapter()
    adapter._bot.edit_message_reply_markup = AsyncMock(side_effect=RuntimeError("no markup"))

    result = await adapter.send("123", "Ответ", metadata={"notify": True})

    assert result.success is True
    assert adapter._feedback_state == {}
