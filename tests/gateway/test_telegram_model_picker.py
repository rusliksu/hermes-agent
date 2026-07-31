"""Tests for Telegram model picker thread fallback."""

import sys
from pathlib import Path
from types import MethodType, SimpleNamespace
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

from gateway.access_registry import (
    AccessRegistry,
    DeliveryTarget,
    ParticipantIdentity,
    PrincipalBinding,
    ResolvedAccessContext,
    RolePolicy,
    SharedScopeBinding,
    TransportIdentity,
)
from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import SessionSource
from gateway.session_context import get_resolved_access_context, reset_session_vars, set_session_vars
from plugins.platforms.telegram.adapter import TelegramAdapter


ACCOUNT = "bot-a"
CAPS = frozenset({"model_switch"})
PICKER_CONTEXT_METADATA_KEY = "_resolved_access_context"
UNAVAILABLE_PICKER_TEXT = "Picker expired or unauthorized — run the command again."


class _LegacyRunner:
    def _handle_message(self, _event):
        return None

    def _is_user_authorized(self, _source):
        return True


class _DenyLegacyRunner:
    def _handle_message(self, _event):
        return None

    def _is_user_authorized(self, _source):
        return False


def _make_adapter():
    adapter = TelegramAdapter(
        PlatformConfig(enabled=True, token="test-token", extra={"account": ACCOUNT})
    )
    adapter._bot = AsyncMock()
    adapter._app = MagicMock()
    adapter._message_handler = _LegacyRunner()._handle_message
    return adapter


class _Runner:
    def __init__(self, registry: AccessRegistry | None):
        self.access_registry = registry
        self.config = SimpleNamespace(multiplex_profiles=True)
        self._allow_access_registry_ingress = MethodType(
            GatewayRunner._allow_access_registry_ingress,
            self,
        )

    def _profile_name_for_source(self, _source):
        return None


def _adapter_with_registry(registry: AccessRegistry) -> TelegramAdapter:
    adapter = _make_adapter()
    adapter.gateway_runner = _Runner(registry)
    return adapter


def _dm_context(user_id: str, profile_id: str = "profile-a") -> ResolvedAccessContext:
    return ResolvedAccessContext(
        principal_id=f"principal-{profile_id}",
        role_id="family_standard",
        profile_id=profile_id,
        conversation_scope="private",
        capabilities=CAPS,
        delivery_target=DeliveryTarget(
            platform="telegram",
            account=ACCOUNT,
            peer_kind="dm",
            chat_id=user_id,
            thread_id=None,
        ),
    )


def _dm_binding(user_id: str, profile_id: str = "profile-a") -> PrincipalBinding:
    return PrincipalBinding(
        principal_id=f"principal-{profile_id}",
        role_id="family_standard",
        profile_id=profile_id,
        transport_identity=TransportIdentity(
            platform="telegram",
            account=ACCOUNT,
            peer_kind="dm",
            user_id=user_id,
            chat_id=user_id,
            thread_id=None,
        ),
        conversation_scope="private",
        delivery_target=DeliveryTarget(
            platform="telegram",
            account=ACCOUNT,
            peer_kind="dm",
            chat_id=user_id,
            thread_id=None,
        ),
    )


def _dm_registry(*bindings: PrincipalBinding) -> AccessRegistry:
    profiles = frozenset(binding.profile_id for binding in bindings)
    return AccessRegistry(
        roles={"family_standard": RolePolicy("family_standard", CAPS)},
        profiles=profiles,
        principal_bindings=bindings,
        scope_capabilities={"private": CAPS},
        backend_capabilities=CAPS,
    )


def _group_topic_context() -> ResolvedAccessContext:
    return ResolvedAccessContext(
        principal_id="principal-room",
        role_id="shared_room",
        profile_id="room-profile",
        conversation_scope="shared_room",
        capabilities=CAPS,
        delivery_target=DeliveryTarget(
            platform="telegram",
            account=ACCOUNT,
            peer_kind="group",
            chat_id="-100123",
            thread_id="77",
        ),
    )


def _group_topic_registry() -> AccessRegistry:
    return AccessRegistry(
        roles={"shared_room": RolePolicy("shared_room", CAPS)},
        profiles=frozenset({"room-profile"}),
        shared_scope_bindings=(
            SharedScopeBinding(
                principal_id="principal-room",
                role_id="shared_room",
                profile_id="room-profile",
                room_identity=TransportIdentity(
                    platform="telegram",
                    account=ACCOUNT,
                    peer_kind="group",
                    user_id="room",
                    chat_id="-100123",
                    thread_id="77",
                ),
                conversation_scope="shared_room",
                delivery_target=DeliveryTarget(
                    platform="telegram",
                    account=ACCOUNT,
                    peer_kind="group",
                    chat_id="-100123",
                    thread_id="77",
                ),
                participant_identities=(
                    ParticipantIdentity("telegram", ACCOUNT, "111"),
                ),
            ),
        ),
        scope_capabilities={"shared_room": CAPS},
        backend_capabilities=CAPS,
    )


def _query(
    *,
    user_id: str | None = "111",
    chat_id: str = "111",
    chat_type: str = "private",
    thread_id: str | None = None,
    message_id: int | None = 42,
):
    query = AsyncMock()
    query.from_user = SimpleNamespace(id=user_id, first_name="Tester")
    query.message = MagicMock()
    query.message.chat_id = chat_id
    query.message.message_id = message_id
    query.message.message_thread_id = thread_id
    query.message.is_topic_message = thread_id is not None
    query.message.chat = SimpleNamespace(
        id=chat_id,
        type=chat_type,
        title="Room",
        full_name="Tester",
        is_forum=chat_type in {"group", "supergroup"} and thread_id is not None,
    )
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    return query


async def _sent_message(**_kwargs):
    return SimpleNamespace(message_id=42)


class TestTelegramModelPicker:
    @pytest.mark.asyncio
    async def test_model_command_slash_path_saves_source_context_without_contextvar(
        self, tmp_path, monkeypatch
    ):
        import gateway.run as gateway_run
        import gateway.slash_commands as slash_commands

        adapter = _make_adapter()
        adapter._send_message_with_thread_fallback = AsyncMock(side_effect=_sent_message)
        context = _dm_context("111", "profile-a")
        runner = object.__new__(GatewayRunner)
        runner.adapters = {Platform.TELEGRAM: adapter}
        runner.config = SimpleNamespace(multiplex_profiles=True, quick_commands={})
        runner._session_model_overrides = {}
        runner._session_key_for_source = lambda _source: "s"
        runner._normalize_source_for_session_key = lambda source: source
        runner._adapter_for_source = lambda _source: adapter
        runner._thread_metadata_for_source = lambda _source, _anchor=None: {}
        runner._reply_anchor_for_event = lambda _event: None
        runner._resolve_profile_home_for_source = lambda _source: Path(tmp_path)

        monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
        monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {})
        async def _inline_to_thread(func, /, *args, **kwargs):
            return func(*args, **kwargs)

        monkeypatch.setattr(slash_commands.asyncio, "to_thread", _inline_to_thread)
        monkeypatch.setattr(
            "hermes_cli.model_switch.list_picker_providers",
            lambda **_kwargs: [
                {"slug": "openai", "name": "OpenAI", "models": ["gpt-5"], "total_models": 1}
            ],
        )

        set_session_vars(
            session_key="foreign",
            platform="telegram",
            chat_id="foreign",
            resolved_access_context=_dm_context("999", "profile-z"),
        )
        reset_session_vars()
        source = SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="111",
            chat_type="dm",
            user_id="111",
            route_account=ACCOUNT,
        )
        source.resolved_access_context = context
        event = MessageEvent(text="/model", message_type=MessageType.TEXT, source=source)

        assert await runner._handle_model_command(event) is None

        assert get_resolved_access_context(None) is None
        assert adapter._model_picker_state["111"]["resolved_access_context"] is context

    @pytest.mark.asyncio
    async def test_model_picker_registry_unknown_callback_denied_before_state_oracle(self):
        adapter = _adapter_with_registry(_dm_registry(_dm_binding("111")))
        query = _query(user_id="999", chat_id="999")

        await adapter._handle_model_picker_callback(query, "mb", "999")

        query.answer.assert_awaited_once()
        assert "not authorized" in query.answer.call_args.kwargs["text"]
        query.edit_message_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_model_picker_registry_context_mismatch_denied_before_mutation(self):
        adapter = _adapter_with_registry(
            _dm_registry(_dm_binding("111", "profile-a"), _dm_binding("222", "profile-b"))
        )
        callback = AsyncMock(return_value="switched")
        adapter._model_picker_state["111"] = {
            "providers": [{"slug": "openai", "name": "OpenAI", "models": ["gpt-5"]}],
            "current_model": "old",
            "current_provider": "openai",
            "session_key": "s",
            "on_model_selected": callback,
            "msg_id": 42,
            "resolved_access_context": _dm_context("111", "profile-a"),
        }
        query = _query(user_id="222", chat_id="222")

        await adapter._handle_model_picker_callback(query, "mp:openai", "111")

        query.answer.assert_awaited_once()
        assert query.answer.call_args.kwargs["text"] == UNAVAILABLE_PICKER_TEXT
        query.edit_message_text.assert_not_awaited()
        callback.assert_not_awaited()
        assert "selected_provider" not in adapter._model_picker_state["111"]

    @pytest.mark.asyncio
    async def test_model_picker_registry_missing_state_matches_context_mismatch_response(self):
        adapter = _adapter_with_registry(_dm_registry(_dm_binding("111", "profile-a")))
        query = _query(user_id="111", chat_id="111")

        await adapter._handle_model_picker_callback(query, "mp:openai", "111")

        query.answer.assert_awaited_once()
        assert query.answer.call_args.kwargs["text"] == UNAVAILABLE_PICKER_TEXT
        query.edit_message_text.assert_not_awaited()
        assert adapter._model_picker_state == {}

    @pytest.mark.asyncio
    async def test_model_picker_registry_exact_context_allows_group_topic_callback(self):
        context = _group_topic_context()
        adapter = _adapter_with_registry(_group_topic_registry())
        adapter._bot.send_message = AsyncMock(side_effect=_sent_message)
        callback = AsyncMock()

        result = await adapter.send_model_picker(
            chat_id="-100123",
            providers=[{"slug": "openai", "name": "OpenAI", "models": ["gpt-5"]}],
            current_model="old",
            current_provider="openai",
            session_key="s",
            on_model_selected=callback,
            metadata={"thread_id": "77", PICKER_CONTEXT_METADATA_KEY: context},
        )

        assert result.success is True
        assert adapter._model_picker_state["-100123"]["resolved_access_context"] == context

        query = _query(
            user_id="111",
            chat_id="-100123",
            chat_type="supergroup",
            thread_id="77",
        )
        await adapter._handle_model_picker_callback(query, "mp:openai", "-100123")

        assert adapter._model_picker_state["-100123"]["selected_provider"] == "openai"
        query.edit_message_text.assert_awaited()
        query.answer.assert_awaited()

    @pytest.mark.asyncio
    async def test_choice_picker_registry_malformed_callback_denied_before_state_oracle(self):
        adapter = _adapter_with_registry(_dm_registry(_dm_binding("111")))
        query = _query(user_id=None, chat_id="111")

        await adapter._handle_choice_picker_callback(query, "cp:0", "111")

        query.answer.assert_awaited_once()
        assert "not authorized" in query.answer.call_args.kwargs["text"]
        query.edit_message_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_choice_picker_registry_context_mismatch_denied_before_callback_and_pop(self):
        adapter = _adapter_with_registry(
            _dm_registry(_dm_binding("111", "profile-a"), _dm_binding("222", "profile-b"))
        )
        callback = AsyncMock(return_value="choice changed")
        adapter._choice_picker_state["111"] = {
            "msg_id": 42,
            "choices": [{"value": "fast", "label": "Fast"}],
            "session_key": "s",
            "on_choice_selected": callback,
            "resolved_access_context": _dm_context("111", "profile-a"),
        }
        query = _query(user_id="222", chat_id="222")

        await adapter._handle_choice_picker_callback(query, "cp:0", "111")

        query.answer.assert_awaited_once()
        assert query.answer.call_args.kwargs["text"] == UNAVAILABLE_PICKER_TEXT
        query.edit_message_text.assert_not_awaited()
        callback.assert_not_awaited()
        assert "111" in adapter._choice_picker_state

    @pytest.mark.asyncio
    async def test_choice_picker_registry_missing_state_matches_context_mismatch_response(self):
        adapter = _adapter_with_registry(_dm_registry(_dm_binding("111", "profile-a")))
        query = _query(user_id="111", chat_id="111")

        await adapter._handle_choice_picker_callback(query, "cp:0", "111")

        query.answer.assert_awaited_once()
        assert query.answer.call_args.kwargs["text"] == UNAVAILABLE_PICKER_TEXT
        query.edit_message_text.assert_not_awaited()
        assert adapter._choice_picker_state == {}

    @pytest.mark.asyncio
    async def test_choice_picker_registry_exact_context_allows_callback(self):
        context = _dm_context("111", "profile-a")
        adapter = _adapter_with_registry(_dm_registry(_dm_binding("111", "profile-a")))
        adapter._bot.send_message = AsyncMock(side_effect=_sent_message)
        callback = AsyncMock(return_value="choice changed")

        result = await adapter.send_choice_picker(
            chat_id="111",
            title="Pick",
            choices=[{"value": "fast", "label": "Fast"}],
            session_key="s",
            on_choice_selected=callback,
            metadata={PICKER_CONTEXT_METADATA_KEY: context},
        )

        assert result.success is True
        assert adapter._choice_picker_state["111"]["resolved_access_context"] == context

        query = _query(user_id="111", chat_id="111")
        await adapter._handle_choice_picker_callback(query, "cp:0", "111")

        callback.assert_awaited_once_with("111", "fast")
        query.edit_message_text.assert_awaited()
        assert "111" not in adapter._choice_picker_state

    @pytest.mark.asyncio
    async def test_model_picker_legacy_unauthorized_denied_before_state_oracle(self):
        adapter = _make_adapter()
        adapter._message_handler = _DenyLegacyRunner()._handle_message
        query = _query(user_id="999", chat_id="999")

        await adapter._handle_model_picker_callback(query, "mb", "999")

        query.answer.assert_awaited_once()
        assert "not authorized" in query.answer.call_args.kwargs["text"]
        query.edit_message_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_choice_picker_legacy_unauthorized_denied_before_state_oracle(self):
        adapter = _make_adapter()
        adapter._message_handler = _DenyLegacyRunner()._handle_message
        query = _query(user_id="999", chat_id="999")

        await adapter._handle_choice_picker_callback(query, "cp:0", "999")

        query.answer.assert_awaited_once()
        assert "not authorized" in query.answer.call_args.kwargs["text"]
        query.edit_message_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_choice_picker_legacy_authorized_callback_still_applies(self):
        adapter = _make_adapter()
        callback = AsyncMock(return_value="choice changed")
        adapter._choice_picker_state["111"] = {
            "msg_id": 42,
            "choices": [{"value": "fast", "label": "Fast"}],
            "session_key": "s",
            "on_choice_selected": callback,
        }
        query = _query(user_id="111", chat_id="111")

        await adapter._handle_choice_picker_callback(query, "cp:0", "111")

        callback.assert_awaited_once_with("111", "fast")
        query.edit_message_text.assert_awaited()
        assert "111" not in adapter._choice_picker_state

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message_id", [None, 43])
    async def test_model_picker_rejects_missing_or_mismatched_message_id(self, message_id):
        adapter = _make_adapter()
        callback = AsyncMock()
        adapter._model_picker_state["111"] = {
            "providers": [{"slug": "openai", "name": "OpenAI", "models": ["gpt-5"]}],
            "current_model": "old",
            "current_provider": "openai",
            "session_key": "s",
            "on_model_selected": callback,
            "msg_id": 42,
        }
        query = _query(user_id="111", chat_id="111", message_id=message_id)

        await adapter._handle_model_picker_callback(query, "mp:openai", "111")

        query.answer.assert_awaited_once()
        assert query.answer.call_args.kwargs["text"] == UNAVAILABLE_PICKER_TEXT
        query.edit_message_text.assert_not_awaited()
        callback.assert_not_awaited()
        assert "selected_provider" not in adapter._model_picker_state["111"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message_id", [None, 43])
    async def test_choice_picker_rejects_missing_or_mismatched_message_id(self, message_id):
        adapter = _make_adapter()
        callback = AsyncMock(return_value="choice changed")
        adapter._choice_picker_state["111"] = {
            "msg_id": 42,
            "choices": [{"value": "fast", "label": "Fast"}],
            "session_key": "s",
            "on_choice_selected": callback,
        }
        query = _query(user_id="111", chat_id="111", message_id=message_id)

        await adapter._handle_choice_picker_callback(query, "cp:0", "111")

        query.answer.assert_awaited_once()
        assert query.answer.call_args.kwargs["text"] == UNAVAILABLE_PICKER_TEXT
        query.edit_message_text.assert_not_awaited()
        callback.assert_not_awaited()
        assert "111" in adapter._choice_picker_state

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
        )

        assert result.success is True
        assert "MARKDOWN_V2" in repr(sent["parse_mode"])
        assert "provider\\_one" in sent["text"]
        assert "`model_1`" in sent["text"]

    @pytest.mark.asyncio
    async def test_back_button_escapes_dynamic_provider_label(self):
        adapter = _make_adapter()
        adapter._model_picker_state["12345"] = {
            "providers": [{"slug": "provider_one", "name": "Provider One", "total_models": 1, "is_current": True}],
            "current_model": "model_1",
            "current_provider": "provider_one",
            "session_key": "s",
            "on_model_selected": AsyncMock(),
            "msg_id": 42,
        }

        query = AsyncMock()
        query.data = "mb"
        query.message = MagicMock()
        query.message.chat_id = 12345
        query.message.message_id = 42
        query.from_user = MagicMock()
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        await adapter._handle_model_picker_callback(query, "mb", "12345")

        edit_kwargs = query.edit_message_text.call_args[1]
        assert "MARKDOWN_V2" in repr(edit_kwargs["parse_mode"])
        assert "provider\\_one" in edit_kwargs["text"]
        assert "`model_1`" in edit_kwargs["text"]

    @pytest.mark.asyncio
    async def test_model_selected_edits_message_on_success(self, monkeypatch):
        """Regression: the mm: (model selected → switch) success path must
        edit the picker message to show the confirmation and remove the
        buttons.  An earlier revision of this PR over-indented the
        edit_message_text block so it lived inside the except branch and
        only fired when the callback raised."""
        adapter = _make_adapter()
        monkeypatch.setattr(
            "hermes_cli.model_cost_guard.expensive_model_warning",
            lambda *_args, **_kwargs: None,
        )
        callback = AsyncMock(return_value="Switched to `gpt-5`")
        adapter._model_picker_state["12345"] = {
            "providers": [
                {"slug": "openai", "name": "OpenAI", "total_models": 1, "is_current": True}
            ],
            "current_model": "model_1",
            "current_provider": "openai",
            "session_key": "s",
            "on_model_selected": callback,
            "selected_provider": "openai",
            "model_list": ["gpt-5"],
            "msg_id": 42,
        }

        query = AsyncMock()
        query.data = "mm:0"
        query.message = MagicMock()
        query.message.chat_id = 12345
        query.message.message_id = 42
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        await adapter._handle_model_picker_callback(query, "mm:0", "12345")

        callback.assert_awaited_once()
        query.edit_message_text.assert_awaited()
        edit_kwargs = query.edit_message_text.call_args[1]
        assert "MARKDOWN_V2" in repr(edit_kwargs["parse_mode"])
        assert "`gpt-5`" in edit_kwargs["text"]
        assert "12345" not in adapter._model_picker_state

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
        )

        assert "mpg:minimax" in built
        assert "mp:xai" in built
        assert "mp:minimax" not in built
        assert "mp:minimax-cn" not in built

        built.clear()
        query = AsyncMock()
        query.message = MagicMock()
        query.message.chat_id = 12345
        query.message.message_id = 101
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        await adapter._handle_model_picker_callback(query, "mpg:minimax", "12345")

        assert "mp:minimax" in built
        assert "mp:minimax-cn" in built
        assert "mb" in built

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
        )

        def _callbacks(markup):
            return [
                button.callback_data
                for row in markup.inline_keyboard
                for button in row
            ]

        first_page = _callbacks(sent["reply_markup"])
        assert "mp:zai" not in first_page
        assert "mpv:1" in first_page

        query = AsyncMock()
        query.message = MagicMock()
        query.message.chat_id = 12345
        query.message.message_id = 101
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        await adapter._handle_model_picker_callback(query, "mpv:1", "12345")

        second_page = _callbacks(query.edit_message_text.call_args[1]["reply_markup"])
        assert "mp:zai" in second_page
        assert "mpv:0" in second_page

        await adapter._handle_model_picker_callback(query, "mp:zai", "12345")
        assert adapter._model_picker_state["12345"]["selected_provider"] == "zai"

        await adapter._handle_model_picker_callback(query, "mb", "12345")
        back_page = _callbacks(query.edit_message_text.call_args[1]["reply_markup"])
        assert "mp:zai" in back_page

    @pytest.mark.asyncio
    async def test_expensive_model_requires_confirmation(self, monkeypatch):
        import plugins.platforms.telegram.adapter as tg

        adapter = _make_adapter()
        callback = AsyncMock(return_value="Switched to `openai/gpt-5.5-pro`")
        adapter._model_picker_state["12345"] = {
            "providers": [
                {"slug": "openrouter", "name": "OpenRouter", "total_models": 1, "is_current": True}
            ],
            "current_model": "model_1",
            "current_provider": "openrouter",
            "session_key": "s",
            "on_model_selected": callback,
            "selected_provider": "openrouter",
            "model_list": ["openai/gpt-5.5-pro"],
            "msg_id": 42,
        }
        monkeypatch.setattr(
            "hermes_cli.model_cost_guard.expensive_model_warning",
            lambda *_args, **_kwargs: SimpleNamespace(
                message="!!! EXPENSIVE MODEL WARNING !!!\ndid you mean to select openai/gpt-5.5?"
            ),
        )
        async def _inline_to_thread(func, /, *args, **kwargs):
            return func(*args, **kwargs)

        monkeypatch.setattr(tg.asyncio, "to_thread", _inline_to_thread)

        query = AsyncMock()
        query.message = MagicMock()
        query.message.chat_id = 12345
        query.message.message_id = 42
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        await adapter._handle_model_picker_callback(query, "mm:0", "12345")

        callback.assert_not_awaited()
        assert "12345" in adapter._model_picker_state
        first_edit = query.edit_message_text.call_args[1]
        assert "EXPENSIVE MODEL WARNING" in first_edit["text"]
        assert first_edit["reply_markup"] is not None

        await adapter._handle_model_picker_callback(query, "mc:0", "12345")

        callback.assert_awaited_once_with("12345", "openai/gpt-5.5-pro", "openrouter")
        assert "12345" not in adapter._model_picker_state

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
        )

        assert result.success is True
        assert len(call_log) == 2
        assert call_log[0]["message_thread_id"] == 99999
        assert "message_thread_id" not in call_log[1] or call_log[1]["message_thread_id"] is None
