"""Tests for #46866: plain-text approval responses must resolve a blocking
dangerous-command approval instead of being steered/queued.

When the agent is blocked inside tools/approval.py waiting for a dangerous
command to be approved, a messaging user who replies "yes" / "approve" /
"deny" (without the leading slash) must have that response routed to the
approval handler.  Previously the bare-word reply fell through to the
steer/queue/interrupt logic in _handle_active_session_busy_message — the
approval never resolved, timed out, and auto-denied.

Slash forms (/approve, /deny) already bypass at the base-adapter guard;
this covers the bare-word forms Signal/SMS users naturally type.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionSource
from gateway.single_principal import SinglePrincipalPolicy


OWNER = "10001"
FAMILY = "30003"


def _make_source(user_id: str = "u1") -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        user_id=user_id,
        chat_id=user_id,
        user_name="tester",
        chat_type="dm",
    )


def _make_event(text: str, user_id: str = "u1") -> MessageEvent:
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=_make_source(user_id),
        message_id="m1",
    )


def _clear_approval_state():
    from tools import approval as mod
    mod._gateway_queues.clear()
    mod._gateway_notify_cbs.clear()
    mod._session_approved.clear()
    mod._permanent_approved.clear()
    mod._pending.clear()


def _make_runner(single_principal_policy=None):
    """Minimal GatewayRunner that exercises the real busy-session handler."""
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")},
        single_principal=single_principal_policy,
    )
    adapter = MagicMock()
    adapter.send = AsyncMock()
    adapter.authorization_is_upstream = False
    adapter._send_with_retry = AsyncMock(
        return_value=SimpleNamespace(success=True, message_id="reply1")
    )
    # _unwrap_ephemeral is a real base-adapter method; emulate its contract.
    adapter._unwrap_ephemeral = lambda r: (r, 0) if isinstance(r, str) else (None, 0)
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._busy_ack_ts = {}
    runner._draining = False
    runner.session_store = None
    if single_principal_policy is None:
        runner._is_user_authorized = lambda _source: True
    else:
        runner._single_principal_policy = single_principal_policy
    # _handle_active_session_busy_message uses these only on the
    # non-approval fall-through path; harmless to stub.
    runner._busy_input_mode = "interrupt"
    runner._busy_text_mode = "interrupt"
    return runner, adapter


def _register_blocking_approval(runner, source=None):
    """Register a real blocking approval entry for the runner's session."""
    from tools.approval import _ApprovalEntry, _gateway_queues
    source = source or _make_source()
    session_key = runner._session_key_for_source(source)
    entry = _ApprovalEntry({"command": "rm -rf /tmp/test"})
    _gateway_queues.setdefault(session_key, []).append(entry)
    return session_key, entry


def _single_principal_policy():
    return SinglePrincipalPolicy.from_dict(
        {
            "enabled": True,
            "telegram_owner_id": OWNER,
            "telegram_allowed_user_ids": [FAMILY],
        }
    )


@pytest.mark.parametrize("reply", ["yes", "approve", "ok", "y", "confirm"])
def test_plaintext_yes_resolves_approval(reply):
    _clear_approval_state()
    runner, adapter = _make_runner()
    session_key, entry = _register_blocking_approval(runner)

    handled = asyncio.run(
        runner._handle_active_session_busy_message(_make_event(reply), session_key)
    )

    assert handled is True
    assert entry.event.is_set()
    assert entry.result == "once"
    # The user gets a confirmation reply, not silence.
    adapter._send_with_retry.assert_awaited()
    _clear_approval_state()


@pytest.mark.parametrize("reply", ["no", "deny", "reject", "n", "cancel"])
def test_plaintext_no_denies_approval(reply):
    _clear_approval_state()
    runner, adapter = _make_runner()
    session_key, entry = _register_blocking_approval(runner)

    handled = asyncio.run(
        runner._handle_active_session_busy_message(_make_event(reply), session_key)
    )

    assert handled is True
    assert entry.event.is_set()
    assert entry.result == "deny"
    adapter._send_with_retry.assert_awaited()
    _clear_approval_state()


def test_plaintext_always_maps_to_permanent_choice():
    _clear_approval_state()
    runner, adapter = _make_runner()
    session_key, entry = _register_blocking_approval(runner)

    handled = asyncio.run(
        runner._handle_active_session_busy_message(_make_event("always"), session_key)
    )

    assert handled is True
    assert entry.result == "always"
    _clear_approval_state()


def test_plaintext_session_maps_to_session_choice():
    _clear_approval_state()
    runner, adapter = _make_runner()
    session_key, entry = _register_blocking_approval(runner)

    handled = asyncio.run(
        runner._handle_active_session_busy_message(_make_event("session"), session_key)
    )

    assert handled is True
    assert entry.result == "session"
    _clear_approval_state()


@pytest.mark.parametrize("reply", ["yes", "always"])
def test_family_plaintext_approval_words_are_owner_only(reply):
    _clear_approval_state()
    runner, adapter = _make_runner(_single_principal_policy())
    session_key, entry = _register_blocking_approval(runner, _make_source(FAMILY))

    handled = asyncio.run(
        runner._handle_active_session_busy_message(
            _make_event(reply, FAMILY),
            session_key,
        )
    )

    assert handled is True
    assert not entry.event.is_set()
    assert entry.result is None
    adapter._send_with_retry.assert_awaited()
    _clear_approval_state()


@pytest.mark.parametrize(
    ("reply", "expected"),
    [("yes", "once"), ("always", "always")],
)
def test_owner_plaintext_approval_words_still_resolve(reply, expected):
    _clear_approval_state()
    runner, _adapter = _make_runner(_single_principal_policy())
    session_key, entry = _register_blocking_approval(runner, _make_source(OWNER))

    handled = asyncio.run(
        runner._handle_active_session_busy_message(
            _make_event(reply, OWNER),
            session_key,
        )
    )

    assert handled is True
    assert entry.event.is_set()
    assert entry.result == expected
    _clear_approval_state()


def test_no_pending_approval_does_not_consume_conversational_yes():
    """A bare 'yes' with NO blocking approval must NOT be treated as an
    approval — it falls through to normal busy handling (design intent:
    'yes' in conversation must not execute a dangerous command)."""
    _clear_approval_state()
    runner, adapter = _make_runner()
    source = _make_source()
    session_key = runner._session_key_for_source(source)
    # No approval registered.

    handled = asyncio.run(
        runner._handle_active_session_busy_message(_make_event("yes"), session_key)
    )

    # No approval existed, so nothing was resolved — the "yes" is treated
    # as ordinary text, not as a dangerous-command approval (design intent).
    # (It still flows through normal busy handling, which may send a busy
    # ack; the contract here is only that no approval was consumed.)
    from tools.approval import _gateway_queues
    assert session_key not in _gateway_queues
    _clear_approval_state()


def test_unrelated_text_with_pending_approval_falls_through():
    """Text that is neither approve nor deny vocab must NOT resolve the
    approval — it falls through to normal busy handling."""
    _clear_approval_state()
    runner, adapter = _make_runner()
    session_key, entry = _register_blocking_approval(runner)

    handled = asyncio.run(
        runner._handle_active_session_busy_message(
            _make_event("what files are here?"), session_key
        )
    )

    # Approval still pending — not resolved by unrelated text.
    assert not entry.event.is_set()
    _clear_approval_state()
