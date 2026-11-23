"""Native image serialization on the TUI/Desktop prompt path (remote attachments).

Regression: _route_turn_images referenced _active_image_routing_identity and
sys without binding either, so every image turn fell back to a text path note
(NameError swallowed by the routing decision) instead of an image_url part.
"""

from unittest.mock import patch

from agent.image_routing import build_native_content_parts
from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from tui_gateway.prompt_turn import _route_turn_images


class _Agent:
    provider = "mock"
    model = "mock-model"


def test_native_mode_serializes_image_bytes(tmp_path):
    image = tmp_path / "photo.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

    with patch("hermes_cli.config.load_config", return_value={"agent": {"image_input_mode": "native"}}):
        parts = _route_turn_images(_Agent(), "what is this", [str(image)])

    assert isinstance(parts, list)
    assert [part["type"] for part in parts] == ["text", "image_url"]
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "what is this" in parts[0]["text"]


def test_text_mode_keeps_path_reference(tmp_path):
    image = tmp_path / "photo.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

    with patch("hermes_cli.config.load_config", return_value={"agent": {"image_input_mode": "text"}}):
        out = _route_turn_images(_Agent(), "what is this", [str(image)])

    assert isinstance(out, str)
    assert str(image) in out


def test_session_upload_is_not_blocked_as_another_profile(tmp_path):
    home = tmp_path / "profile"
    image = home / "images" / "upload.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

    token = set_hermes_home_override(str(home))
    try:
        with patch("agent.file_safety.raise_if_read_blocked", side_effect=ValueError("blocked")):
            parts, skipped = build_native_content_parts("what is this", [str(image)])
    finally:
        reset_hermes_home_override(token)

    assert skipped == []
    assert [part["type"] for part in parts] == ["text", "image_url"]


def test_paths_outside_the_session_upload_dir_stay_guarded(tmp_path):
    image = tmp_path / "outside.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

    with patch("agent.file_safety.raise_if_read_blocked", side_effect=ValueError("blocked")):
        _, skipped = build_native_content_parts("what is this", [str(image)])

    assert skipped == [str(image)]
