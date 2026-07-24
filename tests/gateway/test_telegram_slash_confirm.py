"""Regression guard: send_slash_confirm must use format_message + MARKDOWN_V2."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

_repo = str(Path(__file__).resolve().parents[2])
if _repo not in sys.path:
    sys.path.insert(0, _repo)


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

from plugins.platforms.telegram.adapter import TelegramAdapter
from gateway.config import PlatformConfig
from gateway.single_principal import SinglePrincipalPolicy


OWNER = "10001"
FAMILY = "30003"


def _make_adapter():
    config = PlatformConfig(enabled=True, token="test-token", extra={})
    adapter = TelegramAdapter(config)
    adapter._bot = AsyncMock()
    adapter._app = MagicMock()
    return adapter


class _SinglePrincipalRunner:
    def __init__(self):
        self._single_principal_policy = SinglePrincipalPolicy.from_dict(
            {
                "enabled": True,
                "telegram_owner_id": OWNER,
                "telegram_allowed_user_ids": [FAMILY],
            }
        )

    async def _handle_message(self, event):
        return None

    def _is_user_authorized(self, source):
        return bool(self._single_principal_policy.authorize(source))

    def _is_elevated_user_authorized(self, source):
        return bool(self._single_principal_policy.authorize_elevated(source))


class TestSendSlashConfirm:

    @pytest.mark.asyncio
    async def test_uses_markdown_v2_and_escapes_special_chars(self):
        """send_slash_confirm must pass preview through format_message and use
        MARKDOWN_V2 — so commands with underscores, dots, or brackets don't
        raise BadRequest: Can't parse entities."""
        adapter = _make_adapter()
        sent = {}

        async def mock_send(**kwargs):
            sent.update(kwargs)
            return SimpleNamespace(message_id=7)

        adapter._bot.send_message = AsyncMock(side_effect=mock_send)

        result = await adapter.send_slash_confirm(
            chat_id="100",
            title="Confirm",
            message="/run script_name.sh --flag=value [option]",
            session_key="sk",
            confirm_id="cid1",
        )

        assert result.success is True
        assert "MARKDOWN_V2" in repr(sent["parse_mode"])
        # Underscores and dots must be escaped by format_message
        assert "script\\_name" in sent["text"]
        assert "\\." in sent["text"]

    @pytest.mark.asyncio
    async def test_stores_slash_confirm_state(self):
        adapter = _make_adapter()
        adapter._bot.send_message = AsyncMock(
            return_value=SimpleNamespace(message_id=8)
        )

        await adapter.send_slash_confirm(
            chat_id="100",
            title="Confirm",
            message="reload-mcp",
            session_key="my-session",
            confirm_id="cid2",
        )

        assert adapter._slash_confirm_state["cid2"] == "my-session"

    @pytest.mark.asyncio
    async def test_not_connected_returns_failure(self):
        adapter = _make_adapter()
        adapter._bot = None

        result = await adapter.send_slash_confirm(
            chat_id="100",
            title="Confirm",
            message="reload-mcp",
            session_key="sk",
            confirm_id="cid3",
        )

        assert result.success is False

    @pytest.mark.asyncio
    async def test_family_slash_confirm_callback_is_owner_only(self):
        from tools import slash_confirm as sc

        adapter = _make_adapter()
        adapter._message_handler = _SinglePrincipalRunner()._handle_message
        session_key = "agent:main:telegram:dm:30003"
        confirm_id = "cid-family"
        adapter._slash_confirm_state[confirm_id] = session_key
        choices = []

        async def handler(choice):
            choices.append(choice)
            return None

        sc.register(session_key, confirm_id, "new", handler)

        query = AsyncMock()
        query.data = f"sc:once:{confirm_id}"
        query.message = MagicMock()
        query.message.chat_id = int(FAMILY)
        query.message.chat.type = "private"
        query.from_user = MagicMock()
        query.from_user.id = f"0{FAMILY}"
        query.from_user.first_name = "Family"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        update = MagicMock()
        update.callback_query = query

        await adapter._handle_callback_query(update, MagicMock())

        assert choices == []
        assert sc.get_pending(session_key) is not None
        assert adapter._slash_confirm_state[confirm_id] == session_key
        assert "not authorized" in query.answer.call_args[1]["text"].lower()
        query.edit_message_text.assert_not_called()
        sc.clear(session_key)

    @pytest.mark.asyncio
    async def test_owner_slash_confirm_callback_still_resolves(self):
        from tools import slash_confirm as sc

        adapter = _make_adapter()
        adapter._message_handler = _SinglePrincipalRunner()._handle_message
        session_key = "agent:main:telegram:dm:10001"
        confirm_id = "cid-owner"
        adapter._slash_confirm_state[confirm_id] = session_key
        choices = []

        async def handler(choice):
            choices.append(choice)
            return None

        sc.register(session_key, confirm_id, "new", handler)

        query = AsyncMock()
        query.data = f"sc:once:{confirm_id}"
        query.message = MagicMock()
        query.message.chat_id = int(OWNER)
        query.message.chat.type = "private"
        query.from_user = MagicMock()
        query.from_user.id = f"0{OWNER}"
        query.from_user.first_name = "Owner"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        update = MagicMock()
        update.callback_query = query

        await adapter._handle_callback_query(update, MagicMock())

        assert choices == ["once"]
        assert sc.get_pending(session_key) is None
        assert confirm_id not in adapter._slash_confirm_state
        query.edit_message_text.assert_called_once()
