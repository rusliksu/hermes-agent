"""Tests for TelegramAdapter.send_or_update_status (issue #30045).

The status-update path must:
  1. Send a fresh message on the first call for a topic/session/operation key.
  2. Edit that same message on subsequent calls with the same key.
  3. Fall back to sending fresh when the cached message edit fails.
  4. Keep distinct keys independent (no cross-talk).
"""

from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.base import SendResult


def _install_fake_telegram(monkeypatch):
    """Stub the python-telegram-bot package so TelegramAdapter can be imported."""
    fake_telegram = types.ModuleType("telegram")
    fake_telegram.Update = SimpleNamespace(ALL_TYPES=())
    fake_telegram.Bot = object
    fake_telegram.Message = object
    fake_telegram.InlineKeyboardButton = object
    fake_telegram.InlineKeyboardMarkup = object

    fake_error = types.ModuleType("telegram.error")
    fake_error.NetworkError = type("NetworkError", (Exception,), {})
    fake_error.BadRequest = type("BadRequest", (Exception,), {})
    fake_error.TimedOut = type("TimedOut", (Exception,), {})
    fake_telegram.error = fake_error

    fake_constants = types.ModuleType("telegram.constants")
    fake_constants.ParseMode = SimpleNamespace(MARKDOWN_V2="MarkdownV2")
    fake_constants.ChatType = SimpleNamespace(
        GROUP="group", SUPERGROUP="supergroup",
        CHANNEL="channel", PRIVATE="private",
    )
    fake_telegram.constants = fake_constants

    fake_ext = types.ModuleType("telegram.ext")
    fake_ext.Application = object
    fake_ext.CommandHandler = object
    fake_ext.CallbackQueryHandler = object
    fake_ext.MessageHandler = object
    fake_ext.ContextTypes = SimpleNamespace(DEFAULT_TYPE=object)
    fake_ext.filters = object

    fake_request = types.ModuleType("telegram.request")
    fake_request.HTTPXRequest = object

    monkeypatch.setitem(sys.modules, "telegram", fake_telegram)
    monkeypatch.setitem(sys.modules, "telegram.error", fake_error)
    monkeypatch.setitem(sys.modules, "telegram.constants", fake_constants)
    monkeypatch.setitem(sys.modules, "telegram.ext", fake_ext)
    monkeypatch.setitem(sys.modules, "telegram.request", fake_request)


@pytest.fixture
def adapter(monkeypatch):
    _install_fake_telegram(monkeypatch)
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
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "owner")

    a = tg.TelegramAdapter(PlatformConfig(enabled=True, token="fake-token"))
    a._bot = MagicMock()
    a._bot.edit_message_reply_markup = AsyncMock()
    # Patch send / edit_message so tests can drive them directly.
    a.send = AsyncMock()
    a.edit_message = AsyncMock()
    return a


@pytest.mark.asyncio
async def test_first_call_sends_and_caches_message_id(adapter):
    """First call for a (chat, key) pair must send and remember the id."""
    adapter.send.return_value = SendResult(success=True, message_id="100")

    result = await adapter.send_or_update_status("chat-1", "lifecycle", "starting")

    assert result.success is True
    assert result.message_id == "100"
    adapter.send.assert_awaited_once()
    adapter.edit_message.assert_not_awaited()
    assert adapter._status_message_ids[("chat-1", None, "", "lifecycle")] == "100"


@pytest.mark.asyncio
async def test_second_call_edits_in_place(adapter):
    """Same (chat, key) on the second call must edit, not send."""
    adapter.send.return_value = SendResult(success=True, message_id="100")
    adapter.edit_message.return_value = SendResult(success=True, message_id="100")

    await adapter.send_or_update_status("chat-1", "lifecycle", "step 1")
    await adapter.send_or_update_status("chat-1", "lifecycle", "step 2")

    adapter.send.assert_awaited_once()
    adapter.edit_message.assert_awaited_once()
    # Edit was directed at the cached message id.
    args, kwargs = adapter.edit_message.call_args
    assert args[0] == "chat-1"
    assert args[1] == "100"
    assert args[2] == "step 2"


@pytest.mark.asyncio
async def test_concurrent_first_updates_create_only_one_status_bubble(adapter):
    """Same operation is serialized so concurrent cache misses cannot double-send."""
    first_send_started = asyncio.Event()
    release_first_send = asyncio.Event()

    async def _send(*_args, **_kwargs):
        first_send_started.set()
        await release_first_send.wait()
        return SendResult(success=True, message_id="100")

    adapter.send.side_effect = _send
    adapter.edit_message.return_value = SendResult(success=True, message_id="100")
    first = asyncio.create_task(
        adapter.send_or_update_status(
            "123", "run", "first", session_key="session-a", operation_id="op-a"
        )
    )
    await first_send_started.wait()
    second = asyncio.create_task(
        adapter.send_or_update_status(
            "123", "run", "second", session_key="session-a", operation_id="op-a"
        )
    )
    release_first_send.set()
    await asyncio.gather(first, second)

    assert adapter.send.await_count == 1
    adapter.edit_message.assert_awaited_once()
    assert adapter._status_message_ids[("123", None, "session-a", "op-a")] == "100"


@pytest.mark.asyncio
async def test_cleanup_tombstone_blocks_late_fire_and_forget_status(adapter):
    """A status future starting after run cleanup must not recreate a bubble."""
    assert not await adapter.clear_run_status("123", "session-a", "op-a")

    result = await adapter.send_or_update_status(
        "123", "run", "late", session_key="session-a", operation_id="op-a"
    )

    assert result.success
    adapter.send.assert_not_awaited()
    adapter.edit_message.assert_not_awaited()
    assert adapter._status_message_ids == {}


@pytest.mark.asyncio
async def test_edit_failure_falls_back_to_fresh_send(adapter):
    """When edit_message fails the cache is cleared and a new send happens."""
    adapter.send.side_effect = [
        SendResult(success=True, message_id="100"),
        SendResult(success=True, message_id="200"),
    ]
    adapter.edit_message.return_value = SendResult(
        success=False, error="Bad Request: message to edit not found",
    )

    await adapter.send_or_update_status("chat-1", "lifecycle", "step 1")
    result = await adapter.send_or_update_status("chat-1", "lifecycle", "step 2")

    assert result.success is True
    assert result.message_id == "200"
    assert adapter.send.await_count == 2
    assert adapter.edit_message.await_count == 1
    # Cache now points at the fresh message id.
    assert adapter._status_message_ids[("chat-1", None, "", "lifecycle")] == "200"


@pytest.mark.asyncio
async def test_distinct_status_keys_do_not_collide(adapter):
    """A different status_key gets its own message; the original isn't touched."""
    adapter.send.side_effect = [
        SendResult(success=True, message_id="100"),
        SendResult(success=True, message_id="200"),
    ]

    await adapter.send_or_update_status("chat-1", "lifecycle", "ctx pressure")
    await adapter.send_or_update_status("chat-1", "model-switch", "switched to opus")

    assert adapter.send.await_count == 2
    adapter.edit_message.assert_not_awaited()
    assert adapter._status_message_ids[("chat-1", None, "", "lifecycle")] == "100"
    assert adapter._status_message_ids[("chat-1", None, "", "model-switch")] == "200"


@pytest.mark.asyncio
async def test_distinct_chat_ids_do_not_collide(adapter):
    """Same status_key in different chats must not edit each other's messages."""
    adapter.send.side_effect = [
        SendResult(success=True, message_id="100"),
        SendResult(success=True, message_id="200"),
    ]

    await adapter.send_or_update_status("chat-1", "lifecycle", "first")
    await adapter.send_or_update_status("chat-2", "lifecycle", "second")

    assert adapter.send.await_count == 2
    adapter.edit_message.assert_not_awaited()
    assert adapter._status_message_ids[("chat-1", None, "", "lifecycle")] == "100"
    assert adapter._status_message_ids[("chat-2", None, "", "lifecycle")] == "200"


@pytest.mark.asyncio
async def test_topic_session_and_operation_status_keys_do_not_collide(adapter):
    adapter.send.side_effect = [
        SendResult(success=True, message_id="100"),
        SendResult(success=True, message_id="200"),
        SendResult(success=True, message_id="300"),
    ]

    await adapter.send_or_update_status(
        "123", "run", "topic 7", metadata={"thread_id": "7"},
        session_key="session-a", operation_id="op-a",
    )
    await adapter.send_or_update_status(
        "123", "run", "topic 8", metadata={"thread_id": "8"},
        session_key="session-a", operation_id="op-a",
    )
    await adapter.send_or_update_status(
        "123", "run", "new generation", metadata={"thread_id": "7"},
        session_key="session-a", operation_id="op-b",
    )

    assert adapter.send.await_count == 3
    adapter.edit_message.assert_not_awaited()
    assert set(adapter._status_message_ids) == {
        ("123", "7", "session-a", "op-a"),
        ("123", "8", "session-a", "op-a"),
        ("123", "7", "session-a", "op-b"),
    }


def _stop_query(*, message_id=100, thread_id=7, user_id="owner"):
    message = SimpleNamespace(
        chat_id=123,
        message_id=message_id,
        message_thread_id=thread_id,
        is_topic_message=True,
        chat=SimpleNamespace(id=123, type="private", is_forum=False),
    )
    return SimpleNamespace(
        message=message,
        from_user=SimpleNamespace(id=user_id, first_name="Owner"),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_owner_bound_stop_acks_then_cancels_once(adapter):
    order = []
    adapter.send.return_value = SendResult(success=True, message_id="100")

    async def _stop():
        order.append("stop")
        return "Stopped"

    await adapter.send_or_update_status(
        "123", "run", "Working", metadata={"thread_id": "7"},
        session_key="session-a", owner_user_id="owner", operation_id="op-a",
        on_stop=_stop,
    )
    markup = adapter._bot.edit_message_reply_markup.call_args.kwargs["reply_markup"]
    callback_data = markup.inline_keyboard[0][0].callback_data
    assert len(callback_data.encode("utf-8")) <= 64

    query = _stop_query()

    async def _answer(**_kwargs):
        order.append("ack")

    query.answer.side_effect = _answer
    await adapter._handle_run_status_callback(query, callback_data)
    await adapter._handle_run_status_callback(query, callback_data)

    assert order == ["ack", "stop", "ack"]
    assert adapter._run_status_state == {}
    assert adapter._run_status_nonce_by_key == {}
    assert adapter._status_message_ids == {}


@pytest.mark.asyncio
async def test_stop_serializes_with_status_refresh_without_orphaning_nonce(adapter):
    adapter.send.return_value = SendResult(success=True, message_id="100")
    adapter.edit_message.return_value = SendResult(success=True, message_id="100")
    stop = AsyncMock(return_value="Stopped")
    validation_started = asyncio.Event()
    release_validation = asyncio.Event()

    async def _is_current():
        validation_started.set()
        await release_validation.wait()
        return True

    await adapter.send_or_update_status(
        "123", "run", "Working", metadata={"thread_id": "7"},
        session_key="session-a", owner_user_id="owner", operation_id="op-a",
        on_stop=stop, is_state_current=_is_current,
    )
    markup = adapter._bot.edit_message_reply_markup.call_args.kwargs["reply_markup"]
    callback_data = markup.inline_keyboard[0][0].callback_data
    query = _stop_query()
    stop_task = asyncio.create_task(
        adapter._handle_run_status_callback(query, callback_data)
    )
    await validation_started.wait()
    refresh_task = asyncio.create_task(
        adapter.send_or_update_status(
            "123", "run", "Still working", metadata={"thread_id": "7"},
            session_key="session-a", owner_user_id="owner", operation_id="op-a",
            on_stop=stop, is_state_current=_is_current,
        )
    )
    await asyncio.sleep(0.01)
    release_validation.set()
    await asyncio.gather(stop_task, refresh_task)

    stop.assert_awaited_once_with()
    assert adapter._run_status_state == {}
    assert adapter._run_status_nonce_by_key == {}
    assert adapter._status_message_ids == {}


@pytest.mark.asyncio
async def test_stop_rejects_foreign_user_and_stale_generation(adapter):
    adapter.send.return_value = SendResult(success=True, message_id="100")
    stop = AsyncMock(return_value="Stopped")
    current = AsyncMock(return_value=False)
    await adapter.send_or_update_status(
        "123", "run", "Working", metadata={"thread_id": "7"},
        session_key="session-a", owner_user_id="owner", operation_id="op-a",
        on_stop=stop, is_state_current=current,
    )
    markup = adapter._bot.edit_message_reply_markup.call_args.kwargs["reply_markup"]
    callback_data = markup.inline_keyboard[0][0].callback_data
    nonce = callback_data.split(":")[1]

    foreign_query = _stop_query(user_id="foreign")
    await adapter._handle_run_status_callback(foreign_query, callback_data)
    stop.assert_not_awaited()
    current.assert_not_awaited()
    assert nonce in adapter._run_status_state

    owner_query = _stop_query()
    order = []

    async def _answer(**_kwargs):
        order.append("ack")

    async def _current():
        order.append("validator")
        return False

    owner_query.answer.side_effect = _answer
    adapter._run_status_state[nonce]["is_state_current"] = _current
    await adapter._handle_run_status_callback(owner_query, callback_data)

    assert order == ["ack", "validator"]
    stop.assert_not_awaited()
    assert nonce not in adapter._run_status_state


@pytest.mark.asyncio
async def test_clear_run_status_expires_stop_button(adapter):
    adapter.send.return_value = SendResult(success=True, message_id="100")
    stop = AsyncMock(return_value="Stopped")
    await adapter.send_or_update_status(
        "123", "run", "Working", metadata={"thread_id": "7"},
        session_key="session-a", owner_user_id="owner", operation_id="op-a",
        on_stop=stop,
    )
    markup = adapter._bot.edit_message_reply_markup.call_args.kwargs["reply_markup"]
    callback_data = markup.inline_keyboard[0][0].callback_data

    assert await adapter.clear_run_status(
        "123", "session-a", "op-a", metadata={"thread_id": "7"}
    )
    await adapter._handle_run_status_callback(_stop_query(), callback_data)

    stop.assert_not_awaited()
    assert adapter._run_status_state == {}
    assert adapter._status_message_ids == {}
