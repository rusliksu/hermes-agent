"""Gateway status bridge forwards topic/run ownership to Telegram."""

import pytest

from gateway.platforms.base import SendResult
from gateway.run import _send_or_update_status_coro


class _StatusAdapter:
    def __init__(self):
        self.call = None

    async def send_or_update_status(self, *args, **kwargs):
        self.call = (args, kwargs)
        return SendResult(success=True, message_id="status-1")


@pytest.mark.asyncio
async def test_status_bridge_forwards_session_operation_and_stop_callback():
    adapter = _StatusAdapter()

    async def on_stop():
        return "stopped"

    def is_current():
        return True

    result = await _send_or_update_status_coro(
        adapter,
        "10001",
        "long_running",
        "working",
        {"thread_id": "77"},
        session_key="agent:main:telegram:dm:10001:77",
        owner_user_id="10001",
        operation_id="run:3",
        on_stop=on_stop,
        is_state_current=is_current,
    )

    assert result.success is True
    _args, kwargs = adapter.call
    assert kwargs["metadata"] == {"thread_id": "77"}
    assert kwargs["session_key"].endswith(":77")
    assert kwargs["owner_user_id"] == "10001"
    assert kwargs["operation_id"] == "run:3"
    assert kwargs["on_stop"] is on_stop
    assert kwargs["is_state_current"] is is_current

