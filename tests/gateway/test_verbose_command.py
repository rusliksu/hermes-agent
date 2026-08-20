"""Tests for gateway /verbose command (config-gated tool progress cycling)."""

from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

import gateway.run as gateway_run
from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource


def _make_event(text="/verbose", platform=Platform.TELEGRAM, user_id="12345", chat_id="67890"):
    """Build a MessageEvent for testing."""
    source = SessionSource(
        platform=platform,
        user_id=user_id,
        chat_id=chat_id,
        user_name="testuser",
    )
    return MessageEvent(text=text, source=source)


def _make_runner():
    """Create a bare GatewayRunner without calling __init__."""
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.adapters = {}
    runner._ephemeral_system_prompt = ""
    runner._prefill_messages = []
    runner._reasoning_config = None
    runner._show_reasoning = False
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._running_agents = {}
    runner.hooks = MagicMock()
    runner.hooks.emit = AsyncMock()
    runner.hooks.loaded_hooks = []
    runner._session_db = None
    runner._get_or_create_gateway_honcho = lambda session_key: (None, None)
    return runner


class TestVerboseCommand:
    """Tests for _handle_verbose_command in the gateway."""

    @pytest.mark.asyncio
    async def test_disabled_by_default(self, tmp_path, monkeypatch):
        """When tool_progress_command is false, /verbose returns an info message."""
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()
        config_path = hermes_home / "config.yaml"
        config_path.write_text("display:\n  tool_progress: all\n", encoding="utf-8")

        monkeypatch.setattr(gateway_run, "_hermes_home", hermes_home)

        runner = _make_runner()
        result = await runner._handle_verbose_command(_make_event())

        assert "not enabled" in result.lower()
        assert "tool_progress_command" in result

    @pytest.mark.asyncio
    async def test_next_cycles_mode(self, tmp_path, monkeypatch):
        """Explicit /verbose next cycles tool_progress mode per-platform."""
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()
        config_path = hermes_home / "config.yaml"
        config_path.write_text(
            "display:\n  tool_progress_command: true\n  tool_progress: all\n",
            encoding="utf-8",
        )

        monkeypatch.setattr(gateway_run, "_hermes_home", hermes_home)

        runner = _make_runner()
        result = await runner._handle_verbose_command(_make_event("/verbose next"))

        # all -> verbose
        assert "VERBOSE" in result
        assert "telegram" in result.lower()  # per-platform feedback

        # Verify config was saved to display.platforms.telegram
        saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert saved["display"]["platforms"]["telegram"]["tool_progress"] == "verbose"

    @pytest.mark.asyncio
    async def test_quoted_false_keeps_command_disabled(self, tmp_path, monkeypatch):
        """Quoted false must not enable the /verbose gateway command."""
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()
        config_path = hermes_home / "config.yaml"
        config_path.write_text(
            'display:\n  tool_progress_command: "false"\n  tool_progress: all\n',
            encoding="utf-8",
        )

        monkeypatch.setattr(gateway_run, "_hermes_home", hermes_home)

        runner = _make_runner()
        result = await runner._handle_verbose_command(_make_event())

        assert "not enabled" in result.lower()
        assert "tool_progress_command" in result

    @pytest.mark.asyncio
    async def test_cycles_through_all_modes(self, tmp_path, monkeypatch):
        """Calling /verbose repeatedly cycles through all tool-progress visibility modes."""
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()
        config_path = hermes_home / "config.yaml"
        config_path.write_text(
            "display:\n  tool_progress_command: true\n  tool_progress: 'off'\n",
            encoding="utf-8",
        )

        monkeypatch.setattr(gateway_run, "_hermes_home", hermes_home)
        runner = _make_runner()

        # off -> new -> all -> verbose -> log -> off
        expected = ["new", "all", "verbose", "log", "off"]
        for mode in expected:
            result = await runner._handle_verbose_command(_make_event("/verbose next"))
            saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            actual = saved["display"]["platforms"]["telegram"]["tool_progress"]
            assert actual == mode, \
                f"Expected {mode}, got {actual}"

    @pytest.mark.asyncio
    async def test_defaults_to_platform_default_when_no_tool_progress_set(self, tmp_path, monkeypatch):
        """When tool_progress is not in config, starts from platform default then cycles.

        Telegram's tier-1 preset overrides ``tool_progress`` to ``"off"`` so the
        platform stays final-answer-first by default on mobile inboxes.  The
        first ``/verbose`` invocation therefore cycles ``off → new``.
        """
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()
        config_path = hermes_home / "config.yaml"
        config_path.write_text(
            "display:\n  tool_progress_command: true\n",
            encoding="utf-8",
        )

        monkeypatch.setattr(gateway_run, "_hermes_home", hermes_home)

        runner = _make_runner()
        result = await runner._handle_verbose_command(_make_event("/verbose next"))

        # Telegram platform default is "off" → cycles to "new"
        assert "NEW" in result
        saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert saved["display"]["platforms"]["telegram"]["tool_progress"] == "new"

    @pytest.mark.asyncio
    async def test_per_platform_isolation(self, tmp_path, monkeypatch):
        """Cycling /verbose on Telegram doesn't change Slack's setting.

        Without a global tool_progress, each platform uses its built-in
        default — Telegram = 'off' (tier-1 inbox override), Slack = 'off'
        (quiet Slack default). Both cycle to 'new' on first /verbose.
        """
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()
        config_path = hermes_home / "config.yaml"
        # No global tool_progress → built-in platform defaults apply
        config_path.write_text(
            "display:\n  tool_progress_command: true\n",
            encoding="utf-8",
        )

        monkeypatch.setattr(gateway_run, "_hermes_home", hermes_home)
        runner = _make_runner()

        # Cycle on Telegram
        await runner._handle_verbose_command(
            _make_event("/verbose next", platform=Platform.TELEGRAM)
        )
        # Cycle on Slack
        await runner._handle_verbose_command(
            _make_event("/verbose next", platform=Platform.SLACK)
        )

        saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        platforms = saved["display"]["platforms"]
        # Telegram: off -> new (platform default = off, tier-1 inbox override)
        assert platforms["telegram"]["tool_progress"] == "new"
        # Slack: off -> new (first /verbose cycle from quiet default)
        assert platforms["slack"]["tool_progress"] == "new"

    @pytest.mark.asyncio
    async def test_multiplexed_topic_updates_serving_profile(self, tmp_path, monkeypatch):
        """Shared-topic /verbose persists where the following turn reads it."""
        hermes_home = tmp_path / "hermes"
        profile_home = tmp_path / "room-drafts"
        hermes_home.mkdir()
        profile_home.mkdir()
        control_path = hermes_home / "config.yaml"
        profile_path = profile_home / "config.yaml"
        control_path.write_text(
            "display:\n  tool_progress_command: true\n  tool_progress: all\n",
            encoding="utf-8",
        )
        profile_path.write_text(
            "display:\n  tool_progress: 'off'\n",
            encoding="utf-8",
        )

        monkeypatch.setattr(gateway_run, "_hermes_home", hermes_home)
        runner = _make_runner()
        runner.config = MagicMock(multiplex_profiles=True)
        runner._resolve_profile_home_for_source = MagicMock(
            return_value=profile_home
        )

        result = await runner._handle_verbose_command(_make_event("/verbose next"))

        assert "NEW" in result
        saved_profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        assert saved_profile["display"]["platforms"]["telegram"]["tool_progress"] == "new"
        saved_control = yaml.safe_load(control_path.read_text(encoding="utf-8"))
        assert "platforms" not in saved_control["display"]

    @pytest.mark.asyncio
    async def test_explicit_mode_applies_without_cycling(self, tmp_path, monkeypatch):
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()
        config_path = hermes_home / "config.yaml"
        config_path.write_text(
            "display:\n  tool_progress_command: true\n  tool_progress: 'off'\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(gateway_run, "_hermes_home", hermes_home)

        result = await _make_runner()._handle_verbose_command(
            _make_event("/verbose verbose")
        )

        assert "VERBOSE" in result
        saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert saved["display"]["platforms"]["telegram"]["tool_progress"] == "verbose"

    @pytest.mark.asyncio
    async def test_bare_command_opens_picker_without_mutating_config(
        self, tmp_path, monkeypatch
    ):
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()
        config_path = hermes_home / "config.yaml"
        config_path.write_text(
            "display:\n  tool_progress_command: true\n  tool_progress: all\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(gateway_run, "_hermes_home", hermes_home)
        runner = _make_runner()
        captured = {}

        async def _capture_picker(*args, **kwargs):
            captured.update(kwargs)
            return True

        runner._try_send_choice_picker = _capture_picker

        result = await runner._handle_verbose_command(_make_event())

        assert result is None
        assert [choice["value"] for choice in captured["choices"]] == [
            "off", "new", "all", "verbose", "log"
        ]
        assert next(
            choice for choice in captured["choices"] if choice["value"] == "all"
        )["is_current"] is True
        saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert "platforms" not in saved["display"]

        callback_result = await captured["on_choice_selected"]("67890", "verbose")
        assert "VERBOSE" in callback_result
        saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert saved["display"]["platforms"]["telegram"]["tool_progress"] == "verbose"

    @pytest.mark.asyncio
    async def test_invalid_mode_does_not_mutate_config(self, tmp_path, monkeypatch):
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()
        config_path = hermes_home / "config.yaml"
        original = "display:\n  tool_progress_command: true\n  tool_progress: all\n"
        config_path.write_text(original, encoding="utf-8")
        monkeypatch.setattr(gateway_run, "_hermes_home", hermes_home)

        result = await _make_runner()._handle_verbose_command(
            _make_event("/verbose maximum")
        )

        assert "off|new|all|verbose|log" in result.lower()
        assert config_path.read_text(encoding="utf-8") == original

    @pytest.mark.asyncio
    async def test_no_config_file_returns_disabled(self, tmp_path, monkeypatch):
        """When config.yaml doesn't exist, command reports disabled."""
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()
        # No config.yaml

        monkeypatch.setattr(gateway_run, "_hermes_home", hermes_home)

        runner = _make_runner()
        result = await runner._handle_verbose_command(_make_event())
        assert "not enabled" in result.lower()

    def test_verbose_is_in_gateway_known_commands(self):
        """The /verbose command is recognized by the gateway dispatch."""
        from hermes_cli.commands import GATEWAY_KNOWN_COMMANDS
        assert "verbose" in GATEWAY_KNOWN_COMMANDS
