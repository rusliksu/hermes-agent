"""Regression tests for #30479 — session-scoped /model and /reasoning overrides
silently lost on Telegram forum/DM topics and after compression session splits.

Root cause: ``_handle_message_with_agent`` rewrites ``source.thread_id`` via
``_recover_telegram_topic_thread_id`` (lobby/stripped reply -> the user's
last-active bound topic) *before* deriving the session key for a message turn.
The ``/model`` and ``/reasoning`` command handlers derived their override key
from the raw inbound ``event.source``, skipping that recovery — so the override
was stored under one key and the next message turn read a different key, and the
override was dropped.

Fix: both command handlers normalize the source via
``_normalize_source_for_session_key`` before deriving the override key, so
storage and read keys are identical.
"""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import gateway.run as gateway_run
from gateway.access_registry import (
    AccessRegistry,
    DeliveryTarget,
    PrincipalBinding,
    RolePolicy,
    TransportIdentity,
    session_scope_from_resolved_access_context,
)
from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource, build_session_key
from gateway.session_context import get_resolved_access_context


def _make_runner(recovered_thread_id=None):
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.config = None
    runner.session_store = None
    runner._session_db = None
    runner._session_model_overrides = {}
    runner._session_reasoning_overrides = {}
    runner._agent_cache = {}
    runner._agent_cache_lock = threading.Lock()
    # Stub topic recovery: returns the bound topic id for a lobby message,
    # None otherwise (the real method's contract).
    runner._recover_telegram_topic_thread_id = MagicMock(return_value=recovered_thread_id)
    return runner


def _topic_dm_source(thread_id):
    """A Telegram DM in topic mode. thread_id="" / "1" == General/lobby."""
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="555",
        chat_name="Forum DM",
        chat_type="dm",
        user_id="user-1",
        thread_id=thread_id,
    )


def _topic_access_registry():
    capabilities = frozenset({"memory_read"})
    return AccessRegistry(
        roles={"family": RolePolicy("family", capabilities)},
        profiles=frozenset({"family-profile"}),
        principal_bindings=(
            PrincipalBinding(
                principal_id="principal-family",
                role_id="family",
                profile_id="family-profile",
                transport_identity=TransportIdentity(
                    platform="telegram",
                    account="bot-a",
                    peer_kind="dm",
                    user_id="555",
                    chat_id="555",
                ),
                conversation_scope="private",
                delivery_target=DeliveryTarget(
                    platform="telegram",
                    account="bot-a",
                    peer_kind="dm",
                    chat_id="555",
                ),
            ),
        ),
        scope_capabilities={"private": capabilities},
        backend_capabilities=capabilities,
    )


def _registry_topic_dm_source(thread_id=None):
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="555",
        chat_name="Forum DM",
        chat_type="dm",
        user_id="555",
        thread_id=thread_id,
        route_account="bot-a",
    )


def test_normalize_rewrites_lobby_thread_to_bound_topic():
    """A lobby (stripped) reply gets pinned to the user's bound topic id."""
    runner = _make_runner(recovered_thread_id="42")
    src = _topic_dm_source(thread_id="")  # lobby/General — no message_thread_id

    normalized = runner._normalize_source_for_session_key(src)

    assert normalized.thread_id == "42"
    # Original source is left untouched (we return a copy).
    assert src.thread_id == ""


def test_normalize_passthrough_when_no_recovery():
    """No recovery -> source returned unchanged (identity)."""
    runner = _make_runner(recovered_thread_id=None)
    src = _topic_dm_source(thread_id="42")

    normalized = runner._normalize_source_for_session_key(src)

    assert normalized is src


def test_normalize_swallows_recovery_exceptions():
    """Recovery raising must not break the command — return the raw source."""
    runner = _make_runner()
    runner._recover_telegram_topic_thread_id = MagicMock(side_effect=RuntimeError("boom"))
    src = _topic_dm_source(thread_id="")

    normalized = runner._normalize_source_for_session_key(src)

    assert normalized is src


def test_override_key_matches_message_turn_key_after_recovery():
    """The bug, end to end at the key level.

    /model arrives as a lobby reply (thread_id="").  The next message turn
    runs recovery and lands on the bound topic ("42").  After the fix, the
    key the command stores under must equal the key the message turn reads.
    """
    runner = _make_runner(recovered_thread_id="42")

    # --- /model command path (raw inbound is a lobby reply) ---
    command_source = _topic_dm_source(thread_id="")
    normalized_command_source = runner._normalize_source_for_session_key(command_source)
    # _session_key_for_source falls back to build_session_key when there is no
    # session_store; emulate that resolution here directly.
    command_key = build_session_key(normalized_command_source)

    # --- next message turn path (recovery already applied to source) ---
    message_turn_source = _topic_dm_source(thread_id="42")
    message_turn_key = build_session_key(message_turn_source)

    assert command_key == message_turn_key

    # And the orphaning the bug caused: storing under the RAW (pre-recovery)
    # key would NOT be found by the message turn.
    raw_key = build_session_key(command_source)
    assert raw_key != message_turn_key


@pytest.mark.asyncio
async def test_access_context_is_rebound_after_topic_recovery_before_session_append(tmp_path):
    """A recovered topic must use a context scope bound to the recovered key."""
    from hermes_state import SessionDB

    runner = _make_runner(recovered_thread_id="42")
    runner.access_registry = _topic_access_registry()
    runner.config = SimpleNamespace(multiplex_profiles=True)
    db = SessionDB(db_path=tmp_path / "state.db")
    observed = {}

    async def inner(event):
        bound_context = get_resolved_access_context()
        observed["bound_context"] = bound_context

        normalized = runner._normalize_source_for_session_key(event.source)
        event.source = normalized
        observed["normalized_thread_id"] = normalized.thread_id
        observed["session_key"] = build_session_key(
            normalized,
            profile="family-profile",
        )

        session_id = "recovered-topic-session"
        db.create_session(
            session_id=session_id,
            source="telegram",
            session_key=observed["session_key"],
            user_id="555",
            chat_id="555",
            chat_type="dm",
            thread_id="42",
            profile_name="family-profile",
        )
        observed["session_row_thread_id"] = db.get_session(session_id)["thread_id"]

        scope = session_scope_from_resolved_access_context(bound_context)
        observed["bound_scope_thread_id"] = scope["thread_id"]
        observed["append_id"] = db.append_message(
            session_id=session_id,
            role="user",
            content="owner topic turn",
            session_scope=scope,
        )
        observed["message_count"] = db.get_session(session_id)["message_count"]
        return observed

    runner._handle_message_inner = inner
    event = MessageEvent(
        text="owner topic turn",
        source=_registry_topic_dm_source(),
    )

    try:
        result = await runner._handle_message(event)
    finally:
        db.close()

    assert result["bound_context"] is not None
    assert result["normalized_thread_id"] == "42"
    assert result["session_row_thread_id"] == "42"
    assert result["session_key"] == "agent:family-profile:telegram:dm:555:42"
    assert result["append_id"] == 1, (
        "SessionDB append denied by stale access scope: "
        f"bound_scope_thread_id={result['bound_scope_thread_id']!r}, "
        f"normalized_thread_id={result['normalized_thread_id']!r}, "
        f"session_row_thread_id={result['session_row_thread_id']!r}, "
        f"session_key={result['session_key']!r}"
    )
    assert result["message_count"] == 1
