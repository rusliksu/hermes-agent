"""Integrity tests for Telegram's nonce-bound interactive controls."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import PlatformConfig
from gateway.single_principal import SinglePrincipalPolicy
from plugins.platforms.telegram.adapter import TelegramAdapter


def _adapter(monkeypatch, *message_ids: int) -> TelegramAdapter:
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
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "*")
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="test-token"))
    adapter._bot = AsyncMock()
    ids = iter(message_ids or (41,))
    adapter._bot.send_message = AsyncMock(
        side_effect=lambda **_kwargs: SimpleNamespace(message_id=next(ids))
    )
    return adapter


def _query(
    *,
    message_id: int,
    chat_id: int = 100,
    thread_id: int | None = 7,
    user_id: int = 100,
    chat_type: str = "private",
    order: list[str] | None = None,
):
    message = SimpleNamespace(
        chat_id=chat_id,
        message_id=message_id,
        message_thread_id=thread_id,
        is_topic_message=thread_id is not None,
        chat=SimpleNamespace(
            id=chat_id,
            type=chat_type,
            is_forum=chat_type in {"group", "supergroup"} and thread_id is not None,
        ),
    )

    async def _answer(**_kwargs):
        if order is not None:
            order.append("ack")

    async def _edit_message_text(**_kwargs):
        if order is not None:
            order.append("edit")

    return SimpleNamespace(
        message=message,
        from_user=SimpleNamespace(id=user_id, first_name="Tester"),
        answer=AsyncMock(side_effect=_answer),
        edit_message_text=AsyncMock(side_effect=_edit_message_text),
    )


async def _send_choice(
    adapter: TelegramAdapter,
    callback,
    *,
    session_key: str,
    thread_id: int = 7,
    is_state_current=None,
    chat_id: str = "100",
    initiator_user_id: str = "100",
    allow_shared_lane_control: bool = False,
):
    return await adapter.send_choice_picker(
        chat_id=chat_id,
        title="Reasoning",
        choices=[{"value": "high", "label": "High"}],
        session_key=session_key,
        on_choice_selected=callback,
        metadata={"thread_id": str(thread_id)},
        initiator_user_id=initiator_user_id,
        is_state_current=is_state_current,
        allow_shared_lane_control=allow_shared_lane_control,
    )


def _attach_shared_policy(adapter: TelegramAdapter, chat_id: str = "-100"):
    policy = SinglePrincipalPolicy.from_dict(
        {
            "enabled": True,
            "telegram_owner_id": "9999",
            "telegram_shared_chat_ids": [chat_id],
        }
    )

    class _Runner:
        _single_principal_policy = policy

        def __init__(self):
            self.authorized = True
            self.auth_sources = []

        def _is_user_authorized(self, source):
            self.auth_sources.append(source)
            return self.authorized and bool(policy.authorize(source))

        async def handle(self, _event):
            return None

    runner = _Runner()
    adapter._message_handler = runner.handle
    return runner


@pytest.mark.asyncio
async def test_concurrent_choice_pickers_in_same_lane_do_not_overwrite(monkeypatch):
    adapter = _adapter(monkeypatch, 41, 42)
    first = AsyncMock(return_value="first")
    second = AsyncMock(return_value="second")

    assert (await _send_choice(adapter, first, session_key="session-a")).success
    assert (await _send_choice(adapter, second, session_key="session-b")).success

    by_message = {
        state["message_id"]: nonce
        for nonce, state in adapter._choice_picker_state.items()
    }
    assert len(by_message) == 2

    await adapter._handle_choice_picker_callback(
        _query(message_id=42), f"cp:{by_message['42']}:0", "100"
    )
    second.assert_awaited_once_with("100", "high")
    first.assert_not_awaited()
    assert by_message["41"] in adapter._choice_picker_state

    await adapter._handle_choice_picker_callback(
        _query(message_id=41), f"cp:{by_message['41']}:0", "100"
    )
    first.assert_awaited_once_with("100", "high")
    assert adapter._choice_picker_state == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query_kwargs",
    [
        {"user_id": 200},
        {"chat_id": 200},
        {"thread_id": 8},
        {"message_id": 99},
    ],
)
async def test_picker_rejects_wrong_owner_chat_thread_or_message(
    monkeypatch, query_kwargs
):
    adapter = _adapter(monkeypatch, 41)
    callback = AsyncMock(return_value="changed")
    await _send_choice(adapter, callback, session_key="session-a")
    nonce = next(iter(adapter._choice_picker_state))
    kwargs = {"message_id": 41, **query_kwargs}
    query = _query(**kwargs)

    await adapter._handle_choice_picker_callback(query, f"cp:{nonce}:0", "100")

    callback.assert_not_awaited()
    assert nonce in adapter._choice_picker_state
    query.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_expired_and_repeated_buttons_cannot_apply_selection(monkeypatch):
    adapter = _adapter(monkeypatch, 41, 42)
    old_callback = AsyncMock(return_value="old")
    new_callback = AsyncMock(return_value="new")
    await _send_choice(adapter, old_callback, session_key="old")
    old_nonce = next(iter(adapter._choice_picker_state))
    adapter._choice_picker_state[old_nonce]["expires_at"] = time.monotonic() - 1
    await _send_choice(adapter, new_callback, session_key="new")
    new_nonce = next(
        nonce for nonce in adapter._choice_picker_state if nonce != old_nonce
    )

    old_query = _query(message_id=41)
    await adapter._handle_choice_picker_callback(
        old_query, f"cp:{old_nonce}:0", "100"
    )
    old_callback.assert_not_awaited()
    assert new_nonce in adapter._choice_picker_state

    new_query = _query(message_id=42)
    await adapter._handle_choice_picker_callback(
        new_query, f"cp:{new_nonce}:0", "100"
    )
    await adapter._handle_choice_picker_callback(
        new_query, f"cp:{new_nonce}:0", "100"
    )
    new_callback.assert_awaited_once_with("100", "high")


@pytest.mark.asyncio
async def test_ack_precedes_current_validator_callback_and_edit(monkeypatch):
    adapter = _adapter(monkeypatch, 41)
    order: list[str] = []

    async def _is_current():
        order.append("validator")
        return True

    async def _select(_chat_id, _value):
        order.append("callback")
        return "changed"

    await _send_choice(
        adapter,
        _select,
        session_key="session-a",
        is_state_current=_is_current,
    )
    nonce = next(iter(adapter._choice_picker_state))

    await adapter._handle_choice_picker_callback(
        _query(message_id=41, order=order), f"cp:{nonce}:0", "100"
    )

    assert order == ["ack", "validator", "callback", "edit"]


@pytest.mark.asyncio
async def test_stale_session_is_rejected_after_ack_before_callback(monkeypatch):
    adapter = _adapter(monkeypatch, 41)
    order: list[str] = []
    callback = AsyncMock(return_value="changed")

    def _is_current():
        order.append("validator")
        return False

    await _send_choice(
        adapter,
        callback,
        session_key="old-session",
        is_state_current=_is_current,
    )
    nonce = next(iter(adapter._choice_picker_state))

    await adapter._handle_choice_picker_callback(
        _query(message_id=41, order=order), f"cp:{nonce}:0", "100"
    )

    assert order == ["ack", "validator", "edit"]
    callback.assert_not_awaited()
    assert nonce not in adapter._choice_picker_state


@pytest.mark.asyncio
async def test_shared_topic_lane_picker_reauthorizes_exact_initiator(monkeypatch):
    adapter = _adapter(monkeypatch, 41)
    runner = _attach_shared_policy(adapter)
    order: list[str] = []

    async def _select(_chat_id, _value):
        order.append("callback")
        return "changed"

    await _send_choice(
        adapter,
        _select,
        session_key="shared-topic",
        chat_id="-100",
        initiator_user_id="111",
        allow_shared_lane_control=True,
    )
    nonce = next(iter(adapter._choice_picker_state))

    await adapter._handle_choice_picker_callback(
        _query(
            message_id=41,
            chat_id=-100,
            user_id=111,
            chat_type="supergroup",
            order=order,
        ),
        f"cp:{nonce}:0",
        "-100",
    )

    assert order == ["ack", "callback", "edit"]
    assert len(runner.auth_sources) == 1
    assert runner.auth_sources[0].thread_id == "7"


@pytest.mark.asyncio
async def test_shared_topic_picker_rejects_other_user_without_consuming(monkeypatch):
    adapter = _adapter(monkeypatch, 41)
    _attach_shared_policy(adapter)
    callback = AsyncMock(return_value="changed")
    await _send_choice(
        adapter,
        callback,
        session_key="shared-topic",
        chat_id="-100",
        initiator_user_id="111",
        allow_shared_lane_control=True,
    )
    nonce = next(iter(adapter._choice_picker_state))

    await adapter._handle_choice_picker_callback(
        _query(
            message_id=41,
            chat_id=-100,
            user_id=222,
            chat_type="supergroup",
        ),
        f"cp:{nonce}:0",
        "-100",
    )

    callback.assert_not_awaited()
    assert nonce in adapter._choice_picker_state

    await adapter._handle_choice_picker_callback(
        _query(
            message_id=41,
            chat_id=-100,
            user_id=111,
            chat_type="supergroup",
        ),
        f"cp:{nonce}:0",
        "-100",
    )
    callback.assert_awaited_once_with("-100", "high")


@pytest.mark.asyncio
async def test_shared_topic_picker_fails_closed_after_access_revocation(monkeypatch):
    adapter = _adapter(monkeypatch, 41)
    runner = _attach_shared_policy(adapter)
    callback = AsyncMock(return_value="changed")
    await _send_choice(
        adapter,
        callback,
        session_key="shared-topic",
        chat_id="-100",
        initiator_user_id="111",
        allow_shared_lane_control=True,
    )
    nonce = next(iter(adapter._choice_picker_state))
    runner.authorized = False

    await adapter._handle_choice_picker_callback(
        _query(
            message_id=41,
            chat_id=-100,
            user_id=111,
            chat_type="supergroup",
        ),
        f"cp:{nonce}:0",
        "-100",
    )

    callback.assert_not_awaited()
    assert nonce in adapter._choice_picker_state


@pytest.mark.asyncio
async def test_shared_topic_global_or_unmarked_picker_remains_blocked(monkeypatch):
    adapter = _adapter(monkeypatch, 41)
    _attach_shared_policy(adapter)
    assert adapter._is_callback_user_authorized(
        "9999",
        chat_id="-100",
        chat_type="supergroup",
        thread_id="7",
        require_elevated=True,
        allow_shared_lane_control=True,
    ) is False
    callback = AsyncMock(return_value="changed")
    await _send_choice(
        adapter,
        callback,
        session_key="global-picker",
        chat_id="-100",
        initiator_user_id="111",
    )
    nonce = next(iter(adapter._choice_picker_state))

    await adapter._handle_choice_picker_callback(
        _query(
            message_id=41,
            chat_id=-100,
            user_id=111,
            chat_type="supergroup",
        ),
        f"cp:{nonce}:0",
        "-100",
    )

    callback.assert_not_awaited()
    assert nonce in adapter._choice_picker_state


@pytest.mark.asyncio
async def test_shared_topic_owner_can_stop_own_active_run(monkeypatch):
    adapter = _adapter(monkeypatch, 41)
    _attach_shared_policy(adapter)
    on_stop = AsyncMock(return_value="Stop requested")
    status_key = ("-100", "7", "shared-topic", "operation-1")
    await adapter._ensure_run_status_stop(
        status_key,
        message_id="41",
        chat_id="-100",
        thread_id="7",
        session_key="shared-topic",
        owner_user_id="111",
        operation_id="operation-1",
        on_stop=on_stop,
    )
    nonce = next(iter(adapter._run_status_state))

    await adapter._handle_run_status_callback(
        _query(
            message_id=41,
            chat_id=-100,
            user_id=111,
            chat_type="supergroup",
        ),
        f"rs:{nonce}:x",
    )

    on_stop.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_generated_callback_data_stays_within_telegram_limit(monkeypatch):
    adapter = _adapter(monkeypatch, 41, 42, 43)
    very_long = "provider-" + "x" * 300
    model_result = await adapter.send_model_picker(
        chat_id="100",
        providers=[
            {
                "slug": very_long,
                "name": "Provider " + "Y" * 300,
                "models": ["namespace/" + "z" * 300],
                "total_models": 1,
            }
        ],
        current_model="model",
        current_provider=very_long,
        session_key="model-session",
        on_model_selected=AsyncMock(),
        metadata={"thread_id": "7"},
        initiator_user_id="100",
    )
    assert model_result.success

    choice_result = await _send_choice(
        adapter, AsyncMock(), session_key="choice-session"
    )
    assert choice_result.success
    settings_result = await adapter.send_settings_picker(
        chat_id="100",
        title="Settings",
        actions=[{"value": "v" * 300, "label": "L" * 300}],
        session_key="settings-session",
        on_action_selected=AsyncMock(),
        metadata={"thread_id": "7"},
        initiator_user_id="100",
    )
    assert settings_result.success

    callback_data = []
    for call in adapter._bot.send_message.await_args_list:
        markup = call.kwargs["reply_markup"]
        callback_data.extend(
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
        )
    assert callback_data
    assert all(len(data.encode("utf-8")) <= 64 for data in callback_data)
