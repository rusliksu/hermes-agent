"""Tests for Telegram model picker thread fallback."""

import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _ensure_telegram_mock():
    if "telegram" in sys.modules and hasattr(sys.modules["telegram"], "__file__"):
        return

    mod = MagicMock()
    mod.ext.ContextTypes.DEFAULT_TYPE = type(None)
    mod.constants.ParseMode.MARKDOWN = "Markdown"
    mod.constants.ParseMode.MARKDOWN_V2 = "MarkdownV2"
    mod.constants.ParseMode.HTML = "HTML"
    mod.constants.ChatType.PRIVATE = "private"
    mod.constants.ChatType.GROUP = "group"
    mod.constants.ChatType.SUPERGROUP = "supergroup"
    mod.constants.ChatType.CHANNEL = "channel"
    mod.error.NetworkError = type("NetworkError", (OSError,), {})
    mod.error.TimedOut = type("TimedOut", (OSError,), {})
    mod.error.BadRequest = type("BadRequest", (Exception,), {})

    for name in ("telegram", "telegram.ext", "telegram.constants", "telegram.request"):
        sys.modules.setdefault(name, mod)
    sys.modules.setdefault("telegram.error", mod.error)


_ensure_telegram_mock()

from gateway.config import PlatformConfig
from plugins.platforms.telegram.adapter import TelegramAdapter


def _make_adapter():
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="test-token"))
    adapter._bot = AsyncMock()
    adapter._app = MagicMock()
    return adapter


@pytest.fixture(autouse=True)
def _allow_test_owner(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "12345")


def _make_query(*, message_id=42, thread_id=None, user_id="12345"):
    message = SimpleNamespace(
        chat_id=12345,
        message_id=message_id,
        message_thread_id=thread_id,
        is_topic_message=thread_id is not None,
        chat=SimpleNamespace(id=12345, type="private", is_forum=False),
    )
    return SimpleNamespace(
        message=message,
        from_user=SimpleNamespace(id=user_id, first_name="Owner"),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )


def _put_model_state(adapter, nonce="nonceNonce12", **overrides):
    state = {
        "kind": "model",
        "message_id": "42",
        "chat_id": "12345",
        "thread_id": None,
        "user_id": "12345",
        "providers": [],
        "provider_actions": [],
        "current_model": "model_1",
        "current_provider": "openai",
        "session_key": "s",
        "on_model_selected": AsyncMock(),
        "expires_at": time.monotonic() + 60,
        "busy": False,
    }
    state.update(overrides)
    adapter._model_picker_state[nonce] = state
    return nonce, state


class TestTelegramModelPicker:
    @pytest.mark.asyncio
    async def test_send_model_picker_escapes_dynamic_provider_label(self):
        adapter = _make_adapter()
        sent = {}

        async def mock_send_message(**kwargs):
            sent.update(kwargs)
            return SimpleNamespace(message_id=101)

        adapter._bot.send_message = AsyncMock(side_effect=mock_send_message)

        result = await adapter.send_model_picker(
            chat_id="12345",
            providers=[
                {"slug": "provider_one", "name": "Provider One", "total_models": 1, "is_current": True}
            ],
            current_model="model_1",
            current_provider="provider_one",
            session_key="s",
            on_model_selected=AsyncMock(),
            metadata={"thread_id": "99999"},
            initiator_user_id="12345",
        )

        assert result.success is True
        assert "MARKDOWN_V2" in repr(sent["parse_mode"])
        assert "provider\\_one" in sent["text"]
        assert "`model_1`" in sent["text"]

    @pytest.mark.asyncio
    async def test_back_button_escapes_dynamic_provider_label(self):
        adapter = _make_adapter()
        nonce, _ = _put_model_state(
            adapter,
            providers=[
                {
                    "slug": "provider_one",
                    "name": "Provider One",
                    "total_models": 1,
                    "is_current": True,
                }
            ],
            current_model="model_1",
            current_provider="provider_one",
        )

        query = _make_query()
        await adapter._handle_model_picker_callback(
            query, f"mp:{nonce}:b", "12345"
        )

        edit_kwargs = query.edit_message_text.call_args[1]
        assert "MARKDOWN_V2" in repr(edit_kwargs["parse_mode"])
        assert "provider\\_one" in edit_kwargs["text"]
        assert "`model_1`" in edit_kwargs["text"]

    @pytest.mark.asyncio
    async def test_model_selected_edits_message_on_success(self):
        """Regression: the mm: (model selected → switch) success path must
        edit the picker message to show the confirmation and remove the
        buttons.  An earlier revision of this PR over-indented the
        edit_message_text block so it lived inside the except branch and
        only fired when the callback raised."""
        adapter = _make_adapter()
        callback = AsyncMock(return_value="Switched to `gpt-5`")
        nonce, _ = _put_model_state(
            adapter,
            providers=[
                {"slug": "openai", "name": "OpenAI", "total_models": 1, "is_current": True}
            ],
            current_model="model_1",
            current_provider="openai",
            on_model_selected=callback,
            selected_provider="openai",
            model_list=["gpt-5"],
        )

        query = _make_query()
        await adapter._handle_model_picker_callback(
            query, f"mp:{nonce}:m:0", "12345"
        )

        callback.assert_awaited_once()
        query.edit_message_text.assert_awaited()
        edit_kwargs = query.edit_message_text.call_args[1]
        assert "MARKDOWN_V2" in repr(edit_kwargs["parse_mode"])
        assert "`gpt-5`" in edit_kwargs["text"]
        assert nonce not in adapter._model_picker_state

    @pytest.mark.asyncio
    async def test_provider_group_folds_and_drills_down(self, monkeypatch):
        """A provider family (e.g. MiniMax) collapses to one mpg: button at
        the top level; tapping it expands to its authenticated members as
        mp: buttons. A group reduced to a single authenticated member shows
        no submenu (direct mp: button).

        Inspects callback_data by recording every InlineKeyboardButton built,
        which is robust to whether `telegram` is the real SDK or the module
        mock (the SDK markup objects don't expose a plain iterable under the
        mock)."""
        import plugins.platforms.telegram.adapter as tg

        built: list = []

        class _RecordingButton:
            def __init__(self, text, callback_data=None, **kw):
                self.text = text
                self.callback_data = callback_data
                built.append(callback_data)

        class _RecordingMarkup:
            def __init__(self, rows):
                self.inline_keyboard = rows

        monkeypatch.setattr(tg, "InlineKeyboardButton", _RecordingButton)
        monkeypatch.setattr(tg, "InlineKeyboardMarkup", _RecordingMarkup)

        adapter = _make_adapter()

        async def mock_send_message(**kwargs):
            return SimpleNamespace(message_id=101)

        adapter._bot.send_message = AsyncMock(side_effect=mock_send_message)

        providers = [
            {"slug": "minimax", "name": "MiniMax", "total_models": 2},
            {"slug": "minimax-cn", "name": "MiniMax (China)", "total_models": 3},
            {"slug": "xai", "name": "xAI", "total_models": 1},
        ]

        await adapter.send_model_picker(
            chat_id="12345",
            providers=providers,
            current_model="m",
            current_provider="minimax",
            session_key="s",
            on_model_selected=AsyncMock(),
            metadata=None,
            initiator_user_id="12345",
        )

        nonce = next(iter(adapter._model_picker_state))
        group_callback = next(value for value in built if value.startswith(f"mp:{nonce}:g:"))
        assert any(value.startswith(f"mp:{nonce}:p:") for value in built)
        assert all(len(value.encode("utf-8")) <= 64 for value in built if value)

        built.clear()
        query = _make_query(message_id=101)

        await adapter._handle_model_picker_callback(query, group_callback, "12345")

        member_callbacks = [value for value in built if value.startswith(f"mp:{nonce}:p:")]
        assert len(member_callbacks) == 2
        assert f"mp:{nonce}:b" in built

    @pytest.mark.asyncio
    async def test_provider_picker_paginates_past_first_ten(self, monkeypatch):
        import plugins.platforms.telegram.adapter as tg

        class _RecordingButton:
            def __init__(self, text, callback_data=None, **kw):
                self.text = text
                self.callback_data = callback_data

        class _RecordingMarkup:
            def __init__(self, rows):
                self.inline_keyboard = rows

        monkeypatch.setattr(tg, "InlineKeyboardButton", _RecordingButton)
        monkeypatch.setattr(tg, "InlineKeyboardMarkup", _RecordingMarkup)

        adapter = _make_adapter()
        sent = {}

        async def mock_send_message(**kwargs):
            sent.update(kwargs)
            return SimpleNamespace(message_id=101)

        adapter._bot.send_message = AsyncMock(side_effect=mock_send_message)

        providers = [
            {"slug": f"provider-{i}", "name": f"Provider {i}", "total_models": 1}
            for i in range(10)
        ]
        providers.append({
            "slug": "zai",
            "name": "Z.AI / GLM",
            "models": ["glm-5.2"],
            "total_models": 1,
        })

        await adapter.send_model_picker(
            chat_id="12345",
            providers=providers,
            current_model="model_1",
            current_provider="provider-0",
            session_key="s",
            on_model_selected=AsyncMock(),
            metadata=None,
            initiator_user_id="12345",
        )

        def _buttons(markup):
            return [
                button
                for row in markup.inline_keyboard
                for button in row
            ]

        nonce = next(iter(adapter._model_picker_state))
        first_page = _buttons(sent["reply_markup"])
        assert all(button.text != "Z.AI / GLM (1)" for button in first_page)
        next_callback = next(
            button.callback_data
            for button in first_page
            if button.callback_data == f"mp:{nonce}:v:1"
        )

        query = _make_query(message_id=101)

        await adapter._handle_model_picker_callback(query, next_callback, "12345")

        second_page = _buttons(query.edit_message_text.call_args[1]["reply_markup"])
        zai_callback = next(
            button.callback_data
            for button in second_page
            if button.text == "Z.AI / GLM (1)"
        )
        assert any(button.callback_data == f"mp:{nonce}:v:0" for button in second_page)

        await adapter._handle_model_picker_callback(query, zai_callback, "12345")
        assert adapter._model_picker_state[nonce]["selected_provider"] == "zai"

        await adapter._handle_model_picker_callback(query, f"mp:{nonce}:b", "12345")
        back_page = _buttons(query.edit_message_text.call_args[1]["reply_markup"])
        assert any(button.text == "Z.AI / GLM (1)" for button in back_page)

    @pytest.mark.asyncio
    async def test_expensive_model_requires_confirmation(self, monkeypatch):
        adapter = _make_adapter()
        callback = AsyncMock(return_value="Switched to `openai/gpt-5.5-pro`")
        nonce, _ = _put_model_state(
            adapter,
            providers=[
                {"slug": "openrouter", "name": "OpenRouter", "total_models": 1, "is_current": True}
            ],
            current_model="model_1",
            current_provider="openrouter",
            on_model_selected=callback,
            selected_provider="openrouter",
            model_list=["openai/gpt-5.5-pro"],
        )
        monkeypatch.setattr(
            "hermes_cli.model_cost_guard.expensive_model_warning",
            lambda *_args, **_kwargs: SimpleNamespace(
                message="!!! EXPENSIVE MODEL WARNING !!!\ndid you mean to select openai/gpt-5.5?"
            ),
        )

        query = _make_query()

        await adapter._handle_model_picker_callback(query, f"mp:{nonce}:m:0", "12345")

        callback.assert_not_awaited()
        assert nonce in adapter._model_picker_state
        first_edit = query.edit_message_text.call_args[1]
        assert "EXPENSIVE MODEL WARNING" in first_edit["text"]
        assert first_edit["reply_markup"] is not None

        await adapter._handle_model_picker_callback(query, f"mp:{nonce}:c:0", "12345")

        callback.assert_awaited_once_with("12345", "openai/gpt-5.5-pro", "openrouter")
        assert nonce not in adapter._model_picker_state

    @pytest.mark.asyncio
    async def test_retries_without_thread_when_thread_not_found(self):
        adapter = _make_adapter()
        providers = [{"slug": "openai", "name": "OpenAI", "total_models": 2, "is_current": True}]
        call_log = []

        class FakeBadRequest(Exception):
            pass

        async def mock_send_message(**kwargs):
            call_log.append(dict(kwargs))
            if kwargs.get("message_thread_id") is not None:
                raise FakeBadRequest("Message thread not found")
            return SimpleNamespace(message_id=99)

        adapter._bot.send_message = AsyncMock(side_effect=mock_send_message)

        result = await adapter.send_model_picker(
            chat_id="12345",
            providers=providers,
            current_model="gpt-5",
            current_provider="openai",
            session_key="s",
            on_model_selected=AsyncMock(),
            metadata={"thread_id": "99999"},
            initiator_user_id="12345",
        )

        assert result.success is True
        assert len(call_log) == 2
        assert call_log[0]["message_thread_id"] == 99999
        assert "message_thread_id" not in call_log[1] or call_log[1]["message_thread_id"] is None
