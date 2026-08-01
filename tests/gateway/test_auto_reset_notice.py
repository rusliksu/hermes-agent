"""User-facing reset wording must preserve session continuity."""

from gateway.run import _build_auto_reset_notice


def test_auto_reset_notice_says_previous_session_is_saved_and_resumable():
    notice = _build_auto_reset_notice("daily schedule at 4:00")

    assert "Active context cleared" in notice
    assert "previous session was saved" in notice
    assert "/resume" in notice
    assert "Conversation history cleared" not in notice

