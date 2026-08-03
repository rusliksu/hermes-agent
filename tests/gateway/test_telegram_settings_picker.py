"""Tests for the refreshable Telegram /settings hub card."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.i18n import reset_language_cache
from gateway.config import PlatformConfig
from plugins.platforms.telegram.adapter import TelegramAdapter


@pytest.fixture(autouse=True)
def _reset_i18n_language_cache():
    reset_language_cache()
    yield
    reset_language_cache()


def _adapter(monkeypatch) -> TelegramAdapter:
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
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "100")
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="test-token"))
    adapter._bot = AsyncMock()
    adapter._bot.send_message = AsyncMock(
        return_value=SimpleNamespace(message_id=51)
    )
    return adapter


def _query(*, thread_id=7, user_id=100):
    message = SimpleNamespace(
        chat_id=100,
        message_id=51,
        message_thread_id=thread_id,
        is_topic_message=True,
        chat=SimpleNamespace(id=100, type="private", is_forum=False),
    )
    return SimpleNamespace(
        message=message,
        from_user=SimpleNamespace(id=user_id, first_name="Owner"),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


@pytest.mark.asyncio
async def test_settings_action_refreshes_same_card_and_preserves_other_controls(
    monkeypatch,
):
    adapter = _adapter(monkeypatch)
    callback = AsyncMock(
        return_value={
            "title": "Model: Luna; reasoning: high",
            "actions": [
                {"value": "model", "label": "Model: Luna", "is_current": True},
                {"value": "reasoning", "label": "Reasoning: high", "is_current": True},
            ],
        }
    )

    result = await adapter.send_settings_picker(
        chat_id="100",
        title="Settings",
        actions=[
            {"value": "model", "label": "Model: Sol"},
            {"value": "reasoning", "label": "Reasoning: low"},
        ],
        session_key="telegram:100:7",
        on_action_selected=callback,
        metadata={"thread_id": "7"},
        initiator_user_id="100",
    )

    assert result.success
    nonce, state = next(iter(adapter._settings_picker_state.items()))
    sent_buttons = _buttons(adapter._bot.send_message.call_args.kwargs["reply_markup"])
    assert [button.text for button in sent_buttons][-1] == "✕ Close"

    query = _query()
    await adapter._handle_settings_picker_callback(query, f"st:{nonce}:0")

    query.answer.assert_awaited_once_with()
    callback.assert_awaited_once_with("100", "model")
    assert nonce not in adapter._settings_picker_state
    refreshed_nonce, refreshed_state = next(
        iter(adapter._settings_picker_state.items())
    )
    assert refreshed_nonce != nonce
    assert refreshed_state is state
    assert state["busy"] is False
    assert [action["value"] for action in state["actions"]] == [
        "model",
        "reasoning",
        "close",
    ]
    refreshed = _buttons(query.edit_message_text.call_args.kwargs["reply_markup"])
    assert refreshed[0].text == "✓ Model: Luna"
    assert refreshed[1].text == "✓ Reasoning: high"
    assert all(
        button.callback_data.startswith(f"st:{refreshed_nonce}:")
        for button in refreshed
    )

    replay = _query()
    await adapter._handle_settings_picker_callback(replay, f"st:{nonce}:0")
    callback.assert_awaited_once_with("100", "model")
    replay.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_settings_close_consumes_only_its_card(monkeypatch):
    adapter = _adapter(monkeypatch)
    callback = AsyncMock(return_value="unused")
    await adapter.send_settings_picker(
        chat_id="100",
        title="Settings",
        actions=[{"value": "model", "label": "Model"}],
        session_key="telegram:100:7",
        on_action_selected=callback,
        metadata={"thread_id": "7"},
        initiator_user_id="100",
    )
    nonce, state = next(iter(adapter._settings_picker_state.items()))
    close_index = next(
        index for index, action in enumerate(state["actions"]) if action.get("close")
    )

    query = _query()
    await adapter._handle_settings_picker_callback(
        query, f"st:{nonce}:{close_index}"
    )

    query.answer.assert_awaited_once_with()
    callback.assert_not_awaited()
    assert nonce not in adapter._settings_picker_state
    assert query.edit_message_text.call_args.kwargs["reply_markup"] is None
    assert query.edit_message_text.call_args.kwargs["text"] == "Close"


@pytest.mark.asyncio
async def test_settings_wrong_topic_is_rejected_without_action(monkeypatch):
    adapter = _adapter(monkeypatch)
    callback = AsyncMock(return_value="changed")
    await adapter.send_settings_picker(
        chat_id="100",
        title="Settings",
        actions=[{"value": "model", "label": "Model"}],
        session_key="telegram:100:7",
        on_action_selected=callback,
        metadata={"thread_id": "7"},
        initiator_user_id="100",
    )
    nonce = next(iter(adapter._settings_picker_state))

    await adapter._handle_settings_picker_callback(
        _query(thread_id=8), f"st:{nonce}:0"
    )

    callback.assert_not_awaited()
    assert nonce in adapter._settings_picker_state


@pytest.mark.asyncio
async def test_settings_picker_localizes_russian_chrome_and_callback_states(
    monkeypatch,
):
    monkeypatch.setenv("HERMES_LANGUAGE", "ru")
    reset_language_cache()
    adapter = _adapter(monkeypatch)
    callback = AsyncMock(side_effect=RuntimeError("boom"))
    await adapter.send_settings_picker(
        chat_id="100",
        title="",
        actions=[{"value": "model", "label": "Модель"}],
        session_key="telegram:100:7",
        on_action_selected=callback,
        metadata={"thread_id": "7"},
        initiator_user_id="100",
    )
    nonce, state = next(iter(adapter._settings_picker_state.items()))
    sent_buttons = _buttons(adapter._bot.send_message.call_args.kwargs["reply_markup"])
    assert sent_buttons[-1].text == "✕ Закрыть"

    malformed = _query()
    await adapter._handle_settings_picker_callback(malformed, "st:broken")
    malformed.answer.assert_awaited_once_with(
        text="Настройки устарели — снова вызовите /settings."
    )

    invalid = _query()
    await adapter._handle_settings_picker_callback(invalid, f"st:{nonce}:99")
    assert "Настройки устарели" in invalid.edit_message_text.call_args.kwargs["text"]

    failed = _query()
    await adapter._handle_settings_picker_callback(failed, f"st:{nonce}:0")
    assert "Не удалось изменить настройку" in failed.edit_message_text.call_args.kwargs[
        "text"
    ]
    assert "boom" in failed.edit_message_text.call_args.kwargs["text"]

    close_index = next(
        index for index, action in enumerate(state["actions"]) if action.get("close")
    )
    closed = _query()
    await adapter._handle_settings_picker_callback(
        closed, f"st:{nonce}:{close_index}"
    )
    assert closed.edit_message_text.call_args.kwargs["text"] == "Закрыть"


@pytest.mark.asyncio
async def test_settings_picker_localizes_russian_generic_title(monkeypatch):
    monkeypatch.setenv("HERMES_LANGUAGE", "ru")
    reset_language_cache()
    adapter = _adapter(monkeypatch)
    await adapter.send_settings_picker(
        chat_id="100",
        title="",
        actions=[{"value": "model", "label": "Модель"}],
        session_key="telegram:100:7",
        on_action_selected=AsyncMock(return_value={}),
        metadata={"thread_id": "7"},
        initiator_user_id="100",
    )
    nonce = next(iter(adapter._settings_picker_state))

    query = _query()
    await adapter._handle_settings_picker_callback(query, f"st:{nonce}:0")

    assert "Настройки устарели" in query.edit_message_text.call_args.kwargs["text"]
