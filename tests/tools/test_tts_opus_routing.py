import json
import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from gateway.session_context import _UNSET, _VAR_MAP
from tools import tts_tool


_PATH_MARKER = "/tmp/hermes-tts-ffmpeg-path-marker"
_CONTROLLED_SECRET_ENV = {
    "PATH": _PATH_MARKER,
    "OPENAI_API_KEY": "provider-secret",
    "FIRECRAWL_API_KEY": "tool-secret",
    "TELEGRAM_BOT_TOKEN": "gateway-secret",
    "GATEWAY_RELAY_SECRET": "relay-secret",
    "AUXILIARY_TTS_API_KEY": "dynamic-secret",
}
_SECRET_ENV_NAMES = tuple(k for k in _CONTROLLED_SECRET_ENV if k != "PATH")


def _reset_session_context() -> None:
    for var in _VAR_MAP.values():
        var.set(_UNSET)


@pytest.fixture(autouse=True)
def _clean_session_platform(monkeypatch):
    _reset_session_context()
    monkeypatch.delenv("HERMES_SESSION_PLATFORM", raising=False)
    yield
    _reset_session_context()


async def _write_edge_output(_text: str, output_path: str, _tts_config: dict) -> str:
    Path(output_path).write_bytes(b"mp3")
    return output_path


def test_ffmpeg_conversion_gets_sanitized_explicit_env(tmp_path, monkeypatch):
    captured = {}
    mp3 = tmp_path / "speech.mp3"
    mp3.write_bytes(b"mp3")

    def fake_run(cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        Path(cmd[-2]).write_bytes(b"ogg")
        return Mock(returncode=0, stderr=b"")

    monkeypatch.setattr(tts_tool, "_has_ffmpeg", lambda: True)
    monkeypatch.setattr(tts_tool.subprocess, "run", fake_run)

    with patch.dict(os.environ, _CONTROLLED_SECRET_ENV, clear=True):
        result = tts_tool._convert_to_opus(str(mp3))

    assert result == str(tmp_path / "speech.ogg")
    env = captured["env"]
    assert env is not None
    for name in _SECRET_ENV_NAMES:
        assert name not in env
    assert _PATH_MARKER in env.get("PATH", "")


def test_edge_cli_preserves_native_mp3(tmp_path, monkeypatch):
    out = tmp_path / "speech.mp3"
    convert = Mock()

    monkeypatch.setattr(tts_tool, "_load_tts_config", lambda: {"provider": "edge"})
    monkeypatch.setattr(tts_tool, "_import_edge_tts", lambda: object())
    monkeypatch.setattr(tts_tool, "_generate_edge_tts", _write_edge_output)
    monkeypatch.setattr(tts_tool, "_convert_to_opus", convert)

    result = json.loads(tts_tool.text_to_speech_tool("hello", output_path=str(out)))

    assert result["success"] is True
    assert result["file_path"] == str(out)
    assert result["voice_compatible"] is False
    assert result["media_tag"] == f"MEDIA:{out}"
    convert.assert_not_called()


def test_edge_telegram_converts_to_opus_voice(tmp_path, monkeypatch):
    out = tmp_path / "speech.mp3"
    opus = tmp_path / "speech.ogg"

    def fake_convert(path: str) -> str:
        assert path == str(out)
        opus.write_bytes(b"ogg")
        return str(opus)

    convert = Mock(side_effect=fake_convert)

    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setattr(tts_tool, "_load_tts_config", lambda: {"provider": "edge"})
    monkeypatch.setattr(tts_tool, "_import_edge_tts", lambda: object())
    monkeypatch.setattr(tts_tool, "_generate_edge_tts", _write_edge_output)
    monkeypatch.setattr(tts_tool, "_convert_to_opus", convert)

    result = json.loads(tts_tool.text_to_speech_tool("hello", output_path=str(out)))

    assert result["success"] is True
    assert result["file_path"] == str(opus)
    assert result["voice_compatible"] is True
    assert result["media_tag"] == f"[[audio_as_voice]]\nMEDIA:{opus}"
    convert.assert_called_once_with(str(out))
