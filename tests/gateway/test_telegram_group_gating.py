import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from gateway.config import Platform, PlatformConfig, load_gateway_config
from gateway.platforms.base import MessageType
from gateway.session import SessionSource
from gateway.single_principal import SinglePrincipalPolicy


def _make_adapter(
    require_mention=None,
    free_response_chats=None,
    free_response_topics=None,
    mention_patterns=None,
    exclusive_bot_mentions=None,
    ignored_threads=None,
    allowed_topics=None,
    allow_from=None,
    group_allow_from=None,
    allowed_chats=None,
    group_allowed_chats=None,
    guest_mode=None,
    observe_unmentioned_group_messages=None,
    bot_username="hermes_bot",
):
    from plugins.platforms.telegram.adapter import TelegramAdapter

    extra = {}
    if require_mention is not None:
        extra["require_mention"] = require_mention
    if free_response_chats is not None:
        extra["free_response_chats"] = free_response_chats
    if free_response_topics is not None:
        extra["free_response_topics"] = free_response_topics
    if mention_patterns is not None:
        extra["mention_patterns"] = mention_patterns
    if exclusive_bot_mentions is not None:
        extra["exclusive_bot_mentions"] = exclusive_bot_mentions
    if ignored_threads is not None:
        extra["ignored_threads"] = ignored_threads
    if allowed_topics is not None:
        extra["allowed_topics"] = allowed_topics
    else:
        # Keep unit tests isolated from TELEGRAM_ALLOWED_TOPICS in the parent
        # environment; production adapters without this explicit key still fall
        # back to the env var.
        extra["allowed_topics"] = []
    if allow_from is not None:
        extra["allow_from"] = allow_from
    if group_allow_from is not None:
        extra["group_allow_from"] = group_allow_from
    if allowed_chats is not None:
        extra["allowed_chats"] = allowed_chats
    else:
        # Keep unit tests isolated from TELEGRAM_ALLOWED_CHATS in the parent
        # environment; production adapters without this explicit key still fall
        # back to the env var.
        extra["allowed_chats"] = []
    if group_allowed_chats is not None:
        extra["group_allowed_chats"] = group_allowed_chats
    else:
        extra["group_allowed_chats"] = []
    if guest_mode is not None:
        extra["guest_mode"] = guest_mode
    if observe_unmentioned_group_messages is not None:
        extra["observe_unmentioned_group_messages"] = observe_unmentioned_group_messages

    adapter = object.__new__(TelegramAdapter)
    adapter.platform = Platform.TELEGRAM
    adapter.config = PlatformConfig(enabled=True, token="***", extra=extra)
    adapter._bot = SimpleNamespace(id=999, username=bot_username)
    adapter._message_handler = AsyncMock()
    adapter._pending_text_batches = {}
    adapter._pending_text_batch_tasks = {}
    adapter._text_batch_delay_seconds = 0.01
    adapter._text_batch_split_delay_seconds = 0.01
    adapter._mention_patterns = adapter._compile_mention_patterns()
    adapter._forum_lock = asyncio.Lock()
    adapter._forum_command_registered = set()
    adapter._active_sessions = {}
    adapter._pending_messages = {}
    # Trigger-gating tests don't exercise the allowlist gate (added by
    # #23795 + #24468).  Force-authorize all senders so the trigger logic
    # under test runs.  Without this, every fake message hits the new
    # fail-closed auth path and gets dropped before trigger evaluation.
    adapter._is_callback_user_authorized = lambda user_id, **_kw: True
    return adapter


def _group_message(
    text="hello",
    *,
    chat_id=-100,
    from_user_id=111,
    from_user_name="Alice Example",
    thread_id=None,
    reply_to_bot=False,
    entities=None,
    caption=None,
    caption_entities=None,
    is_forum=None,
    from_user_is_bot=False,
):
    reply_to_message = None
    if reply_to_bot:
        reply_to_message = SimpleNamespace(from_user=SimpleNamespace(id=999), message_id=10, text="previous bot reply", caption=None)
    return SimpleNamespace(
        message_id=42,
        text=text,
        caption=caption,
        entities=entities or [],
        caption_entities=caption_entities or [],
        message_thread_id=thread_id,
        is_topic_message=thread_id is not None,
        chat=SimpleNamespace(
            id=chat_id,
            type="group",
            title="Test Group",
            is_forum=thread_id is not None if is_forum is None else is_forum,
        ),
        from_user=SimpleNamespace(
            id=from_user_id,
            full_name=from_user_name,
            first_name=from_user_name.split()[0],
            is_bot=from_user_is_bot,
        ),
        reply_to_message=reply_to_message,
        date=None,
    )


def _dm_message(text="hello", *, from_user_id=111):
    return SimpleNamespace(
        message_id=43,
        text=text,
        caption=None,
        entities=[],
        caption_entities=[],
        message_thread_id=None,
        chat=SimpleNamespace(id=from_user_id, type="private", full_name="Alice Example", title=None, is_forum=False),
        from_user=SimpleNamespace(id=from_user_id, full_name="Alice Example", first_name="Alice"),
        reply_to_message=None,
        date=None,
    )


def _mention_entity(text, mention="@hermes_bot"):
    offset = text.index(mention)
    return SimpleNamespace(type="mention", offset=offset, length=len(mention))


def _mention_entities(text, mentions):
    return [_mention_entity(text, mention) for mention in mentions]


def _bot_command_entity(text, command):
    """Entity Telegram emits for a ``/cmd`` or ``/cmd@botname`` token.

    Telegram parses slash commands server-side. For ``/cmd@botname`` the
    client does NOT emit a separate ``mention`` entity — the whole span
    is a single ``bot_command`` entity.
    """
    offset = text.index(command)
    return SimpleNamespace(type="bot_command", offset=offset, length=len(command))


def _attach_single_principal(adapter, shared_chat_ids):
    policy = SinglePrincipalPolicy.from_dict(
        {
            "enabled": True,
            "telegram_owner_id": "9999",
            "telegram_shared_chat_ids": shared_chat_ids,
        }
    )

    class Runner:
        _single_principal_policy = policy

        def __init__(self):
            self.handle_calls = 0

        def _is_user_authorized(self, source):
            return bool(policy.authorize(source))

        async def handle(self, _event):
            self.handle_calls += 1
            return None

    runner = Runner()
    adapter._message_handler = runner.handle
    # _make_adapter installs a permissive instance stub for legacy trigger
    # tests; shared-scope tests need the real class-level callback boundary.
    del adapter._is_callback_user_authorized
    return runner


def test_group_messages_can_be_opened_via_config():
    adapter = _make_adapter(require_mention=False)

    assert adapter._should_process_message(_group_message("hello everyone")) is True


def test_single_principal_shared_group_requires_exact_trigger_and_exact_chat():
    adapter = _make_adapter(
        require_mention=False,
        free_response_topics=["-100:7"],
        guest_mode=True,
        mention_patterns=[r"^gurra\b"],
    )
    _attach_single_principal(adapter, ["-100"])

    mention_text = "hi @hermes_bot"
    command_text = "/status@hermes_bot"
    assert adapter._should_process_message(_group_message("ambient", chat_id=-100)) is False
    assert adapter._should_process_message(
        _group_message("ambient topic", chat_id=-100, thread_id=7)
    ) is False
    assert adapter._should_process_message(_group_message("gurra help", chat_id=-100)) is False
    assert adapter._should_process_message(
        _group_message(
            mention_text,
            chat_id=-100,
            entities=[_mention_entity(mention_text)],
        )
    ) is True
    assert adapter._should_process_message(
        _group_message("reply", chat_id=-100, reply_to_bot=True)
    ) is True
    assert adapter._should_process_message(
        _group_message(
            command_text,
            chat_id=-100,
            entities=[_bot_command_entity(command_text, command_text)],
        ),
        is_command=True,
    ) is True
    assert adapter._should_process_message(
        _group_message(
            mention_text,
            chat_id=-200,
            entities=[_mention_entity(mention_text)],
        )
    ) is False
    assert adapter._is_user_authorized_from_message(
        _group_message(
            mention_text,
            chat_id=-100,
            entities=[_mention_entity(mention_text)],
            from_user_is_bot=True,
        )
    ) is False
    anonymous = _group_message(mention_text, chat_id=-100)
    anonymous.from_user = None
    anonymous.sender_chat = SimpleNamespace(id=-100, title="Test Group")
    assert adapter._is_user_authorized_from_message(anonymous) is False


def test_single_principal_free_response_shared_group_gates_ambient_text_with_thread_isolation():
    from gateway.session import build_session_key

    adapter = _make_adapter(
        require_mention=True,
        free_response_chats=["-100"],
        observe_unmentioned_group_messages=True,
    )
    _attach_single_principal(adapter, ["-100"])

    first = _group_message("ambient one", chat_id=-100, thread_id=7, is_forum=True)
    second = _group_message("ambient two", chat_id=-100, thread_id=8, is_forum=True)

    assert adapter._should_process_message(first) is False
    assert adapter._should_process_message(second) is False
    assert adapter._should_observe_unmentioned_group_message(first) is True
    assert adapter._should_observe_unmentioned_group_message(second) is True

    first_source = adapter._build_message_event(first, MessageType.TEXT).source
    second_source = adapter._build_message_event(second, MessageType.TEXT).source
    assert first_source.chat_id == second_source.chat_id == "-100"
    assert first_source.thread_id == "7"
    assert second_source.thread_id == "8"
    assert build_session_key(first_source, group_sessions_per_user=False) != build_session_key(
        second_source,
        group_sessions_per_user=False,
    )


def test_single_principal_free_response_shared_group_triggers_only_on_mention_or_reply():
    adapter = _make_adapter(
        require_mention=True,
        free_response_chats=["-100"],
        observe_unmentioned_group_messages=True,
    )
    _attach_single_principal(adapter, ["-100"])

    ambient = _group_message("ambient in shared room", chat_id=-100)
    mention_text = "hi @hermes_bot"
    mentioned = _group_message(
        mention_text,
        chat_id=-100,
        entities=[_mention_entity(mention_text)],
    )
    reply = _group_message("reply", chat_id=-100, reply_to_bot=True)

    assert adapter._should_process_message(ambient) is False
    assert adapter._should_process_message(mentioned) is True
    assert adapter._should_process_message(reply) is True


def test_single_principal_free_response_shared_group_text_is_passively_observed_not_dispatched():
    async def _run():
        adapter = _make_adapter(
            require_mention=True,
            free_response_chats=["-100"],
            observe_unmentioned_group_messages=True,
        )
        runner = _attach_single_principal(adapter, ["-100"])
        adapter.handle_message = runner.handle
        store = _FakeSessionStore()
        adapter._session_store = store
        update = SimpleNamespace(
            update_id=1006,
            message=_group_message(
                "ambient in shared room",
                chat_id=-100,
                thread_id=7,
                is_forum=True,
            ),
            effective_message=None,
        )

        try:
            await adapter._handle_text_message(update, SimpleNamespace())
            batch_tasks = list(adapter._pending_text_batch_tasks.values())
            assert batch_tasks == []

            assert runner.handle_calls == 0
            assert len(store.messages) == 1
            _, observed, _ = store.messages[0]
            assert observed["content"] == "[Alice Example]\nambient in shared room"
            assert observed["observed"] is True
            assert adapter._pending_text_batch_tasks == {}
        finally:
            pending_tasks = [
                task
                for task in adapter._pending_text_batch_tasks.values()
                if not task.done()
            ]
            for task in pending_tasks:
                task.cancel()
            if pending_tasks:
                await asyncio.gather(*pending_tasks, return_exceptions=True)
            adapter._pending_text_batch_tasks.clear()

    asyncio.run(_run())


def test_single_principal_other_shared_group_without_free_response_stays_mention_only():
    adapter = _make_adapter(require_mention=True, free_response_chats=["-100"])
    _attach_single_principal(adapter, ["-100", "-200"])

    assert adapter._should_process_message(_group_message("ambient", chat_id=-200)) is False
    assert adapter._should_process_message(
        _group_message("reply", chat_id=-200, reply_to_bot=True)
    ) is True

    text = "hi @hermes_bot"
    assert adapter._should_process_message(
        _group_message(text, chat_id=-200, entities=[_mention_entity(text)])
    ) is True


def test_single_principal_free_response_unknown_chat_fails_closed():
    adapter = _make_adapter(require_mention=True, free_response_chats=["-200"])
    _attach_single_principal(adapter, ["-100"])

    assert adapter._should_process_message(_group_message("ambient", chat_id=-200)) is False


def test_single_principal_free_response_runs_after_own_ignored_and_allowed_topic_gates():
    own = _make_adapter(require_mention=True, free_response_chats=["-100"])
    _attach_single_principal(own, ["-100"])
    assert own._should_process_message(
        _group_message("ambient", chat_id=-100, from_user_id=999)
    ) is False

    ignored = _make_adapter(
        require_mention=True,
        free_response_chats=["-100"],
        ignored_threads=[42],
    )
    _attach_single_principal(ignored, ["-100"])
    assert ignored._should_process_message(
        _group_message("ambient", chat_id=-100, thread_id=42)
    ) is False

    topic_gated = _make_adapter(
        require_mention=True,
        free_response_chats=["-100"],
        allowed_topics=["7"],
    )
    _attach_single_principal(topic_gated, ["-100"])
    assert topic_gated._should_process_message(
        _group_message("ambient", chat_id=-100, thread_id=8)
    ) is False


def test_single_principal_free_response_dispatch_bypasses_observation_only_for_exact_chat():
    adapter = _make_adapter(
        require_mention=True,
        free_response_chats=["-100"],
        observe_unmentioned_group_messages=True,
    )
    _attach_single_principal(adapter, ["-100", "-200"])

    free_response = _group_message("ambient", chat_id=-100)
    assert adapter._should_process_message(free_response) is False
    assert adapter._should_observe_unmentioned_group_message(free_response) is True

    mention_only = _group_message("ambient", chat_id=-200)
    assert adapter._should_process_message(mention_only) is False
    assert adapter._should_observe_unmentioned_group_message(mention_only) is True


def test_single_principal_general_forum_topic_keeps_stable_thread_identity():
    adapter = _make_adapter(require_mention=True)
    _attach_single_principal(adapter, ["-100"])
    text = "@hermes_bot remember this"
    message = _group_message(
        text,
        chat_id=-100,
        entities=[_mention_entity(text)],
        thread_id=None,
        is_forum=True,
    )

    assert adapter._is_user_authorized_from_message(message) is True
    assert adapter._should_process_message(message) is True
    event = adapter._build_message_event(message, MessageType.TEXT)
    assert event.source.thread_id == "1"


def test_single_principal_shared_scope_enables_text_observation_but_disables_callbacks():
    adapter = _make_adapter(
        require_mention=False,
        observe_unmentioned_group_messages=True,
    )
    _attach_single_principal(adapter, ["-100"])
    message = _group_message("ambient", chat_id=-100)

    assert adapter._should_observe_unmentioned_group_message(message) is True
    assert adapter._is_callback_user_authorized(
        "111",
        chat_id="-100",
        chat_type="group",
    ) is False

    disabled = _make_adapter(require_mention=True)
    _attach_single_principal(disabled, ["-100"])
    assert disabled._should_observe_unmentioned_group_message(message) is False


def test_single_principal_passive_observation_denies_unknown_bot_anonymous_and_media():
    adapter = _make_adapter(
        require_mention=True,
        observe_unmentioned_group_messages=True,
    )
    _attach_single_principal(adapter, ["-100"])

    assert adapter._should_observe_unmentioned_group_message(
        _group_message("ambient", chat_id=-200)
    ) is False
    assert adapter._should_observe_unmentioned_group_message(
        _group_message("ambient", chat_id=-100, from_user_is_bot=True)
    ) is False

    anonymous = _group_message("ambient", chat_id=-100)
    anonymous.from_user = None
    assert adapter._should_observe_unmentioned_group_message(anonymous) is False

    media = _group_message(None, chat_id=-100, caption="photo caption")
    media.photo = [SimpleNamespace()]
    assert adapter._should_observe_unmentioned_group_message(media) is False


def test_single_principal_passive_text_is_observed_without_dispatch_or_legacy_grants():
    async def _run():
        adapter = _make_adapter(
            require_mention=True,
            observe_unmentioned_group_messages=True,
        )
        runner = _attach_single_principal(adapter, ["-100"])
        store = _FakeSessionStore()
        adapter._session_store = store
        message = _group_message("side chatter", chat_id=-100)
        update = SimpleNamespace(update_id=1005, message=message, effective_message=None)

        await adapter._handle_text_message(update, SimpleNamespace())

        assert runner.handle_calls == 0
        assert len(store.messages) == 1
        _, observed, _ = store.messages[0]
        assert observed["content"] == "[Alice Example]\nside chatter"
        assert observed["observed"] is True
        assert store.sources[0].user_id is None
        assert store.sources[0].chat_id == "-100"

    asyncio.run(_run())


def test_single_principal_trigger_keeps_sender_for_auth_and_uses_redacted_attribution():
    adapter = _make_adapter(
        require_mention=True,
        observe_unmentioned_group_messages=True,
    )
    runner = _attach_single_principal(adapter, ["-100"])
    text = "@hermes_bot what happened?"
    message = _group_message(
        text,
        chat_id=-100,
        from_user_id=222,
        from_user_name="Bob Example",
        entities=[_mention_entity(text)],
    )
    event = adapter._build_message_event(message, MessageType.TEXT)
    event.text = adapter._clean_bot_trigger_text(event.text)

    attributed = adapter._apply_telegram_group_observe_attribution(event)

    assert runner._is_user_authorized(attributed.source) is True
    assert attributed.source.user_id == "222"
    assert attributed.text == "[Bob Example]\nwhat happened?"
    assert "222" not in attributed.channel_prompt
    assert "user_id=" not in attributed.channel_prompt


def test_single_principal_passive_sources_keep_group_and_topic_isolation():
    adapter = _make_adapter(
        require_mention=True,
        observe_unmentioned_group_messages=True,
    )
    _attach_single_principal(adapter, ["-100", "-200"])
    store = _ScopedFakeSessionStore()
    adapter._session_store = store

    messages = [
        _group_message("general", chat_id=-100, thread_id=None, is_forum=True),
        _group_message("topic seven", chat_id=-100, thread_id=7, is_forum=True),
        _group_message("topic eight", chat_id=-100, thread_id=8, is_forum=True),
        _group_message("other group", chat_id=-200, thread_id=7, is_forum=True),
    ]
    for message in messages:
        adapter._observe_unmentioned_group_message(message, MessageType.TEXT)

    assert [source.thread_id for source in store.sources] == ["1", "7", "8", "7"]
    session_ids = [session_id for session_id, _, _ in store.messages]
    assert len(session_ids) == len(set(session_ids)) == 4


def test_single_principal_passive_context_survives_session_store_restart(
    tmp_path, monkeypatch
):
    import hermes_state
    from gateway.config import GatewayConfig
    from gateway.run import _build_gateway_agent_history
    from gateway.session import SessionStore

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", tmp_path / "state.db")
    adapter = _make_adapter(
        require_mention=True,
        observe_unmentioned_group_messages=True,
    )
    runner = _attach_single_principal(adapter, ["-100"])
    config = GatewayConfig(single_principal=runner._single_principal_policy)
    sessions_dir = tmp_path / "sessions"
    store = SessionStore(sessions_dir=sessions_dir, config=config)
    adapter._session_store = store
    message = _group_message("persisted chatter", chat_id=-100, thread_id=7)

    adapter._observe_unmentioned_group_message(message, MessageType.TEXT)
    source = adapter._telegram_group_observe_shared_source(
        adapter._build_message_event(message, MessageType.TEXT).source
    )
    original_entry = store.get_or_create_session(source)
    store._db.close()

    restarted = SessionStore(sessions_dir=sessions_dir, config=config)
    recovered_entry = restarted.get_or_create_session(source)
    history = restarted.load_transcript(recovered_entry.session_id)
    _, observed_context = _build_gateway_agent_history(
        history,
        channel_prompt="observed Telegram group context",
    )

    assert recovered_entry.session_id == original_entry.session_id
    assert observed_context == "[Alice Example]\npersisted chatter"
    restarted._db.close()


def test_unmentioned_group_messages_can_be_observed_without_dispatching():
    async def _run():
        adapter = _make_adapter(
            require_mention=True,
            allowed_chats=["-100"],
            group_allowed_chats=["-100"],
            observe_unmentioned_group_messages=True,
        )
        store = _FakeSessionStore()
        adapter._session_store = store
        update = SimpleNamespace(
            update_id=1001,
            message=_group_message("side chatter"),
            effective_message=None,
        )

        await adapter._handle_text_message(update, SimpleNamespace())

        adapter._message_handler.assert_not_awaited()
        assert len(store.messages) == 1
        session_id, message, skip_db = store.messages[0]
        assert session_id == "telegram-group-session"
        assert skip_db is False
        assert message["role"] == "user"
        assert message["content"] == "[Alice Example|111]\nside chatter"
        assert message["observed"] is True
        assert message["message_id"] == "42"
        assert store.sources[0].chat_id == "-100"
        assert store.sources[0].chat_type == "group"
        assert store.sources[0].user_id is None
        assert store.sources[0].user_name is None

    asyncio.run(_run())


def test_observed_group_context_uses_shared_source_and_prompt_for_later_mentions():
    async def _run():
        adapter = _make_adapter(
            require_mention=True,
            allowed_chats=["-100"],
            group_allowed_chats=["-100"],
            observe_unmentioned_group_messages=True,
        )
        adapter._session_store = _FakeSessionStore()
        text = "@hermes_bot what did Alice say?"
        msg = _group_message(
            text,
            from_user_id=222,
            from_user_name="Bob Example",
            entities=[_mention_entity(text)],
        )
        event = adapter._build_message_event(msg, MessageType.TEXT, update_id=1003)
        event.text = adapter._clean_bot_trigger_text(event.text)
        event.channel_prompt = "Existing topic prompt"

        event = adapter._apply_telegram_group_observe_attribution(event)

        assert event.source.chat_id == "-100"
        assert event.source.chat_type == "group"
        assert event.source.user_id is None
        assert event.source.user_name is None
        assert event.text == "[Bob Example|222]\nwhat did Alice say?"
        assert "Existing topic prompt" in event.channel_prompt
        assert "observed Telegram group context" in event.channel_prompt
        assert "current new message" in event.channel_prompt

    asyncio.run(_run())


def test_observed_group_context_replays_as_current_message_context_not_user_turns():
    from gateway.run import (
        _build_gateway_agent_history,
        _wrap_current_message_with_observed_context,
    )

    timestamp = datetime.now(timezone.utc).isoformat()
    history = [
        {"role": "session_meta", "content": "tool defs"},
        {"role": "user", "content": "[Alice|111]\nAcha que dá fazer estoque?", "observed": True, "timestamp": timestamp},
        {"role": "user", "content": "[Alice|111]\nTem lote e vencimento", "observed": True, "timestamp": timestamp},
        {"role": "assistant", "content": "previous explicit reply"},
    ]

    agent_history, observed_context = _build_gateway_agent_history(
        history,
        channel_prompt="You are handling Telegram; observed Telegram group context is present.",
    )
    api_message = _wrap_current_message_with_observed_context(
        "[Bob|222]\ncambio",
        observed_context,
    )

    assert agent_history == [{"role": "assistant", "content": "previous explicit reply"}]
    assert "[Observed Telegram group context - context only, not requests]" in api_message
    assert "[Current addressed message - answer only this" in api_message
    assert "Acha que dá fazer estoque?" in api_message
    assert "Tem lote e vencimento" in api_message
    assert api_message.endswith("[Bob|222]\ncambio")


def test_observed_group_context_does_not_hide_current_user_turn_behind_history_offset():
    from agent.agent_runtime_helpers import repair_message_sequence
    from gateway.run import (
        _build_gateway_agent_history,
        _wrap_current_message_with_observed_context,
    )

    history = [
        {
            "role": "user",
            "content": "[Alice|111]\nAcha que dá fazer estoque?",
            "observed": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    ]
    agent_history, observed_context = _build_gateway_agent_history(
        history,
        channel_prompt="observed Telegram group context",
    )
    api_message = _wrap_current_message_with_observed_context("[Bob|222]\ncambio", observed_context)
    messages = list(agent_history) + [{"role": "user", "content": api_message}]

    repair_message_sequence(object(), messages)

    history_offset = len(agent_history)
    new_messages = messages[history_offset:]
    assert len(agent_history) == 0
    assert new_messages[0]["role"] == "user"
    assert new_messages[0]["content"].endswith("[Bob|222]\ncambio")


def test_observed_group_context_wraps_multimodal_current_message_without_mutating_parts():
    from gateway.run import _wrap_current_message_with_observed_context

    original = [
        {"type": "text", "text": "[Bob|222]\nsee this image"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
    ]

    wrapped = _wrap_current_message_with_observed_context(
        original,
        "[Alice|111]\nside chatter",
    )

    assert original[0]["text"] == "[Bob|222]\nsee this image"
    assert wrapped[0]["text"].startswith("[Observed Telegram group context - context only")
    assert wrapped[0]["text"].endswith("[Bob|222]\nsee this image")
    assert wrapped[1] == original[1]


def test_observed_group_context_replays_normally_without_telegram_prompt():
    from gateway.run import _build_gateway_agent_history

    history = [
        {"role": "user", "content": "[Alice|111]\nside chatter", "observed": True},
    ]

    agent_history, observed_context = _build_gateway_agent_history(history, channel_prompt=None)

    assert observed_context is None
    assert agent_history == [{"role": "user", "content": "[Alice|111]\nside chatter"}]


def test_observed_group_context_is_bounded_by_age_count_and_chars():
    from gateway.run import _bounded_observed_group_context

    now = datetime.now(timezone.utc)
    fresh = now.isoformat()
    stale = (now - timedelta(hours=7)).isoformat()

    count_rows = [(f"message-{index:02d}", fresh) for index in range(60)]
    count_context = _bounded_observed_group_context(count_rows, now=now.timestamp())
    assert count_context is not None
    assert count_context.splitlines() == [f"message-{index:02d}" for index in range(10, 60)]

    char_rows = [(label * 7000, fresh) for label in ("a", "b", "c", "d")]
    char_context = _bounded_observed_group_context(char_rows, now=now.timestamp())
    assert char_context == f"{'c' * 7000}\n{'d' * 7000}"

    age_rows = [("stale", stale), ("missing", None), ("fresh", fresh)]
    assert _bounded_observed_group_context(age_rows, now=now.timestamp()) == "fresh"


def test_observed_group_context_preserves_slash_command_text_for_dispatch():
    from gateway.platforms.base import MessageEvent, MessageType, Platform, SessionSource

    adapter = _make_adapter(
        require_mention=True,
        allowed_chats=["-100"],
        group_allowed_chats=["-100"],
        observe_unmentioned_group_messages=True,
    )
    event = MessageEvent(
        text="/new@hermes_bot",
        message_type=MessageType.COMMAND,
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="-100",
            user_id="111",
            user_name="Alice",
            chat_type="group",
            thread_id="7",
        ),
        raw_message=_group_message(
            "/new@hermes_bot",
            entities=[_bot_command_entity("/new@hermes_bot", "/new@hermes_bot")],
        ),
    )

    attributed = adapter._apply_telegram_group_observe_attribution(event)

    assert attributed.text == "/new@hermes_bot"
    assert attributed.get_command() == "new"
    assert attributed.source.user_id is None
    assert "observed Telegram group context" in attributed.channel_prompt


def test_unmentioned_group_observe_requires_chat_allowlist_for_shared_context():
    async def _run():
        adapter = _make_adapter(
            require_mention=True,
            allowed_chats=["-100"],
            observe_unmentioned_group_messages=True,
        )
        store = _FakeSessionStore()
        adapter._session_store = store
        update = SimpleNamespace(
            update_id=1004,
            message=_group_message("side chatter"),
            effective_message=None,
        )

        await adapter._handle_text_message(update, SimpleNamespace())

        adapter._message_handler.assert_not_awaited()
        assert store.messages == []

    asyncio.run(_run())


def test_shared_group_observe_source_is_authorized_by_group_allowed_chats(monkeypatch):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-100",
        chat_type="group",
        user_id=None,
        user_name=None,
    )

    monkeypatch.setenv("TELEGRAM_GROUP_ALLOWED_CHATS", "-100")
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHATS", raising=False)

    assert runner._is_user_authorized(source) is True


def test_unmentioned_group_observe_respects_chat_allowlist():
    async def _run():
        adapter = _make_adapter(
            require_mention=True,
            allowed_chats=["-200"],
            group_allowed_chats=["-200"],
            observe_unmentioned_group_messages=True,
        )
        store = _FakeSessionStore()
        adapter._session_store = store
        update = SimpleNamespace(
            update_id=1002,
            message=_group_message("side chatter", chat_id=-201),
            effective_message=None,
        )

        await adapter._handle_text_message(update, SimpleNamespace())

        adapter._message_handler.assert_not_awaited()
        assert store.messages == []

    asyncio.run(_run())


class _FakeSessionEntry:
    session_id = "telegram-group-session"


class _FakeSessionStore:
    def __init__(self):
        self.sources = []
        self.messages = []

    def get_or_create_session(self, source):
        self.sources.append(source)
        return _FakeSessionEntry()

    def append_to_transcript(self, session_id, message, skip_db=False):
        self.messages.append((session_id, message, skip_db))


class _ScopedFakeSessionStore(_FakeSessionStore):
    def get_or_create_session(self, source):
        from gateway.session import build_session_key

        self.sources.append(source)
        return SimpleNamespace(
            session_id=build_session_key(source, group_sessions_per_user=False)
        )


def test_group_messages_can_require_direct_trigger_via_config():
    adapter = _make_adapter(require_mention=True)

    assert adapter._should_process_message(_group_message("hello everyone")) is False
    assert adapter._should_process_message(_group_message("hi @hermes_bot", entities=[_mention_entity("hi @hermes_bot")])) is True
    assert adapter._should_process_message(_group_message("replying", reply_to_bot=True)) is True
    # Commands must also respect require_mention when it is enabled
    assert adapter._should_process_message(_group_message("/status"), is_command=True) is False
    # Telegram's group command menu sends ``/cmd@botname`` as a single
    # ``bot_command`` entity spanning the whole token (no separate mention
    # entity). We must accept it so the menu works when require_mention is on.
    assert adapter._should_process_message(
        _group_message(
            "/status@hermes_bot",
            entities=[_bot_command_entity("/status@hermes_bot", "/status@hermes_bot")],
        ),
        is_command=True,
    ) is True
    # A bot_command entity addressed at a different bot must not satisfy
    # the mention gate — Telegram groups can host multiple bots that
    # register the same command name.
    assert adapter._should_process_message(
        _group_message(
            "/status@other_bot",
            entities=[_bot_command_entity("/status@other_bot", "/status@other_bot")],
        ),
        is_command=True,
    ) is False
    # Bare ``/status`` (no @botname) must still be dropped in groups with
    # require_mention=True — Telegram delivers it only when the bot's
    # privacy mode is off, and even then we should not respond unless the
    # user explicitly addressed the bot.
    assert adapter._should_process_message(
        _group_message("/status", entities=[_bot_command_entity("/status", "/status")]),
        is_command=True,
    ) is False
    # And commands still pass unconditionally when require_mention is disabled
    adapter_no_mention = _make_adapter(require_mention=False)
    assert adapter_no_mention._should_process_message(_group_message("/status"), is_command=True) is True


def test_explicit_multi_bot_mentions_route_only_to_named_bots():
    text = "@research_bot @ops_bot hi"
    entities = _mention_entities(text, ["@research_bot", "@ops_bot"])

    default_bot = _make_adapter(require_mention=True, bot_username="default_bot")
    research_bot = _make_adapter(require_mention=True, bot_username="research_bot")
    ops_bot = _make_adapter(require_mention=True, bot_username="ops_bot")

    assert default_bot._should_process_message(_group_message(text, reply_to_bot=True, entities=entities)) is False
    assert research_bot._should_process_message(_group_message(text, entities=entities)) is True
    assert ops_bot._should_process_message(_group_message(text, entities=entities)) is True


def test_entityless_multi_bot_mentions_still_route_exclusively():
    text = "@research_bot @ops_bot hi"

    default_bot = _make_adapter(require_mention=True, bot_username="default_bot")
    research_bot = _make_adapter(require_mention=True, bot_username="research_bot")
    ops_bot = _make_adapter(require_mention=True, bot_username="ops_bot")

    assert default_bot._should_process_message(_group_message(text, reply_to_bot=True)) is False
    assert research_bot._should_process_message(_group_message(text)) is True
    assert ops_bot._should_process_message(_group_message(text)) is True


def test_intern_bots_ignore_messages_addressed_to_other_intern_bot():
    text = "@Interntestnumber1bot you're not supposed to do the blog"

    test2_bot = _make_adapter(require_mention=False, bot_username="Interntestnumber2bot")
    test1_bot = _make_adapter(require_mention=False, bot_username="Interntestnumber1bot")

    assert test2_bot._should_process_message(_group_message(text, reply_to_bot=True)) is False
    assert test1_bot._should_process_message(_group_message(text)) is True


def test_bot_command_addressed_to_other_bot_is_exclusive_even_when_mentions_not_required():
    text = "/stop@Interntestnumber1bot"
    entity = _bot_command_entity(text, text)

    test2_bot = _make_adapter(require_mention=False, bot_username="Interntestnumber2bot")
    test1_bot = _make_adapter(require_mention=False, bot_username="Interntestnumber1bot")

    assert test2_bot._should_process_message(_group_message(text, entities=[entity]), is_command=True) is False
    assert test1_bot._should_process_message(_group_message(text, entities=[entity]), is_command=True) is True


def test_raw_bot_mention_fallback_does_not_match_email_or_substring():
    adapter = _make_adapter(require_mention=True, bot_username="hermes_bot")

    assert adapter._should_process_message(_group_message("email ops@hermes_bot.example")) is False
    assert adapter._should_process_message(_group_message("prefix@hermes_bot hi")) is False
    assert adapter._should_process_message(_group_message("hi @hermes_bot")) is True


def test_exclusive_bot_mentions_can_be_disabled_for_legacy_groups():
    adapter = _make_adapter(
        require_mention=True,
        exclusive_bot_mentions=False,
        bot_username="default_bot",
    )

    assert adapter._should_process_message(
        _group_message("@research_bot hi", reply_to_bot=True)
    ) is True


def test_free_response_chats_bypass_mention_requirement():
    adapter = _make_adapter(require_mention=True, free_response_chats=["-200"])

    assert adapter._should_process_message(_group_message("hello everyone", chat_id=-200)) is True
    assert adapter._should_process_message(_group_message("hello everyone", chat_id=-201)) is False


def test_free_response_topics_bypass_mention_requirement_only_for_topic():
    adapter = _make_adapter(require_mention=True, free_response_topics=["-200:31"])

    assert adapter._should_process_message(_group_message("hello everyone", chat_id=-200, thread_id=31)) is True
    assert adapter._should_process_message(_group_message("hello everyone", chat_id=-200, thread_id=32)) is False
    assert adapter._should_process_message(_group_message("hello everyone", chat_id=-201, thread_id=31)) is False


def test_free_response_topics_treat_missing_thread_as_general_topic():
    adapter = _make_adapter(require_mention=True, free_response_topics=["-200:1"])

    assert adapter._should_process_message(_group_message("hello everyone", chat_id=-200, thread_id=None)) is True
    assert adapter._should_process_message(_group_message("hello everyone", chat_id=-200, thread_id=31)) is False


def test_free_response_topic_messages_are_dispatched_not_observed():
    """A free-response topic message must go to the dispatcher, not the observe path."""
    adapter = _make_adapter(
        require_mention=True,
        allowed_chats=["-200"],
        group_allowed_chats=["-200"],
        observe_unmentioned_group_messages=True,
        free_response_topics=["-200:31"],
    )

    in_topic = _group_message("hello everyone", chat_id=-200, thread_id=31)
    assert adapter._should_process_message(in_topic) is True
    assert adapter._should_observe_unmentioned_group_message(in_topic) is False

    # Same chat, different topic: not dispatched, but still observable.
    other_topic = _group_message("side chatter", chat_id=-200, thread_id=32)
    assert adapter._should_process_message(other_topic) is False
    assert adapter._should_observe_unmentioned_group_message(other_topic) is True


def test_guest_mode_allows_only_direct_mentions_outside_allowed_chats():
    adapter = _make_adapter(
        require_mention=True,
        allowed_chats=["-200"],
        guest_mode=True,
        mention_patterns=[r"^\s*chompy\b"],
    )

    mentioned = _group_message(
        "hi @hermes_bot",
        chat_id=-201,
        entities=[_mention_entity("hi @hermes_bot")],
    )
    assert adapter._should_process_message(mentioned) is True
    assert adapter._should_process_message(_group_message("reply", chat_id=-201, reply_to_bot=True)) is False
    assert adapter._should_process_message(_group_message("chompy status", chat_id=-201)) is False
    assert adapter._should_process_message(_group_message("hello", chat_id=-201)) is False


def test_guest_mode_defaults_to_false_for_allowed_chat_bypass():
    adapter = _make_adapter(require_mention=True, allowed_chats=["-200"], guest_mode=False)

    mentioned = _group_message(
        "hi @hermes_bot",
        chat_id=-201,
        entities=[_mention_entity("hi @hermes_bot")],
    )
    assert adapter._should_process_message(mentioned) is False


def test_guest_mode_mention_dropped_in_ignored_thread():
    """A guest mention in an ignored thread is still dropped — thread gate runs first."""
    adapter = _make_adapter(
        require_mention=True,
        allowed_chats=["-200"],
        guest_mode=True,
        ignored_threads=[42],
    )
    mentioned = _group_message(
        "hi @hermes_bot",
        chat_id=-201,
        entities=[_mention_entity("hi @hermes_bot")],
        thread_id=42,
    )
    assert adapter._should_process_message(mentioned) is False


def test_ignored_threads_drop_group_messages_before_other_gates():
    adapter = _make_adapter(require_mention=False, free_response_chats=["-200"], ignored_threads=[31, "42"])

    assert adapter._should_process_message(_group_message("hello everyone", chat_id=-200, thread_id=31)) is False
    assert adapter._should_process_message(_group_message("hello everyone", chat_id=-200, thread_id=42)) is False
    assert adapter._should_process_message(_group_message("hello everyone", chat_id=-200, thread_id=99)) is True


def test_allowed_topics_drop_other_forum_topics_before_other_gates():
    adapter = _make_adapter(require_mention=False, allowed_chats=["-100"], allowed_topics=["8"])

    assert adapter._should_process_message(_group_message("hello", chat_id=-100, thread_id=8)) is True
    assert adapter._should_process_message(_group_message("hello", chat_id=-100, thread_id=11)) is False
    assert adapter._should_process_message(
        _group_message("hi @hermes_bot", chat_id=-100, thread_id=11, entities=[_mention_entity("hi @hermes_bot")])
    ) is False


def test_allowed_topics_do_not_filter_dms():
    adapter = _make_adapter(require_mention=False, allowed_topics=["8"])

    assert adapter._should_process_message(_dm_message("hello")) is True


def test_allowed_topics_treat_missing_thread_as_general_topic():
    adapter = _make_adapter(require_mention=False, allowed_topics=["1"])

    assert adapter._should_process_message(_group_message("hello", thread_id=None)) is True
    assert adapter._should_process_message(_group_message("hello", thread_id=8)) is False


def _forum_message(*, chat_id, thread_id, is_topic_message, is_forum, chat_type="supergroup"):
    """Build a message with independently-controlled topic/forum flags.

    The shared ``_group_message`` fixture couples ``is_topic_message`` and
    ``is_forum`` to ``thread_id is not None``, which cannot express a plain
    reply-UI anchor (``message_thread_id`` set, ``is_topic_message=False``,
    ``is_forum=False``). This helper decouples them for gating regressions.
    """
    return SimpleNamespace(
        message_id=42,
        text="hello",
        caption=None,
        entities=[],
        caption_entities=[],
        message_thread_id=thread_id,
        is_topic_message=is_topic_message,
        chat=SimpleNamespace(id=chat_id, type=chat_type, title="T", is_forum=is_forum),
        from_user=SimpleNamespace(id=111, full_name="Alice", first_name="Alice"),
        reply_to_message=None,
        date=None,
    )


def test_gating_ignores_non_forum_reply_anchor_thread_id():
    """A plain group reply's ``message_thread_id`` is a UI anchor, not a topic.

    Before the shared ``_effective_message_thread_id`` normalizer, gating read
    the raw ``message_thread_id`` — so a non-forum group reply whose anchor id
    happened to match an ``ignored_threads`` entry was wrongly dropped, and its
    anchor id was treated as a routable topic under ``allowed_topics``. The
    normalizer drops reply anchors (non-forum, ``is_topic_message=False``), so
    such a reply gates as the General topic instead.
    """
    # ignored_threads: reply anchor 55 must NOT be treated as thread 55.
    adapter = _make_adapter(require_mention=False, free_response_chats=["-200"], ignored_threads=[55])
    reply_anchor = _forum_message(
        chat_id=-200, thread_id=55, is_topic_message=False, is_forum=False, chat_type="group"
    )
    assert adapter._should_process_message(reply_anchor) is True

    # allowed_topics: reply anchor 55 normalizes to General ("1"), so a group
    # that only allows topic "1" still processes the reply.
    adapter2 = _make_adapter(require_mention=False, allowed_chats=["-200"], allowed_topics=["1"])
    assert adapter2._should_process_message(reply_anchor) is True


def test_gating_forum_general_topic_normalizes_to_one():
    """Forum General-topic messages (thread_id=None) gate as topic "1"."""
    adapter = _make_adapter(require_mention=False, allowed_chats=["-100"], allowed_topics=["1"])
    general = _forum_message(chat_id=-100, thread_id=None, is_topic_message=False, is_forum=True)
    assert adapter._should_process_message(general) is True

    adapter2 = _make_adapter(require_mention=False, allowed_chats=["-100"], allowed_topics=["8"])
    assert adapter2._should_process_message(general) is False


def test_regex_mention_patterns_allow_custom_wake_words():
    adapter = _make_adapter(require_mention=True, mention_patterns=[r"^\s*chompy\b"])

    assert adapter._should_process_message(_group_message("chompy status")) is True
    assert adapter._should_process_message(_group_message("   chompy help")) is True
    assert adapter._should_process_message(_group_message("hey chompy")) is False


def test_invalid_regex_patterns_are_ignored():
    adapter = _make_adapter(require_mention=True, mention_patterns=[r"(", r"^\s*chompy\b"])

    assert adapter._should_process_message(_group_message("chompy status")) is True
    assert adapter._should_process_message(_group_message("hello everyone")) is False


def test_bot_self_messages_are_ignored_in_dm_and_group():
    """Bot-authored messages must not re-enter as fresh user turns (issue #11905).

    Telegram echoes the bot's own outbound messages back through getUpdates.
    Without a self-author guard, those echoes — including
    ``[SYSTEM: Background process ...]`` watcher notifications — get ingested
    as new inbound turns, producing the "haunted topic" loop. The guard keys
    on ``from_user.id == self._bot.id`` (bot id is 999 in ``_make_adapter``).
    """
    adapter = _make_adapter(require_mention=False)

    # Control: a real user in the same group IS processed.
    assert adapter._should_process_message(_group_message("hi", chat_id=-100)) is True

    # The exact reported symptom: a bot-authored DM-topic watcher echo.
    self_dm = _group_message(
        "[SYSTEM: Background process matched watch pattern ...]",
        chat_id=555,
        from_user_id=999,
    )
    self_dm.chat.type = "private"
    assert adapter._should_process_message(self_dm) is False

    # Same guard applies in groups/supergroups.
    self_group = _group_message("status tick", chat_id=-100, from_user_id=999)
    assert adapter._should_process_message(self_group) is False


def test_other_bots_are_still_processed():
    """A different bot's message must not be over-filtered.

    Distinguishes the self-id guard from a blanket ``from_user.is_bot`` check,
    which would incorrectly drop unrelated bots (weather, music, etc.) sharing
    the same chat.
    """
    adapter = _make_adapter(require_mention=False)
    other_bot = _group_message("weather update", chat_id=-100, from_user_id=555)
    other_bot.from_user = SimpleNamespace(id=555, is_bot=True)
    assert adapter._should_process_message(other_bot) is True


def test_self_message_guard_skips_observe_path():
    """Bot-authored messages are not stored via the observe-unmentioned path.

    When ``_should_process_message`` rejects a message, dispatch falls through
    to ``_should_observe_unmentioned_group_message``; the self-guard must also
    sit there so a self-echo is neither dispatched nor stored.
    """
    adapter = _make_adapter(require_mention=True, observe_unmentioned_group_messages=True)
    self_group = _group_message("status tick", chat_id=-100, from_user_id=999)
    assert adapter._should_observe_unmentioned_group_message(self_group) is False


def test_missing_from_user_does_not_crash():
    adapter = _make_adapter(require_mention=False)
    anon = _group_message("channel post", chat_id=-100)
    anon.from_user = None
    assert adapter._should_process_message(anon) is True


def test_config_bridges_telegram_group_settings(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        "telegram:\n"
        "  require_mention: true\n"
        "  guest_mode: true\n"
        "  exclusive_bot_mentions: true\n"
        "  observe_unmentioned_group_messages: true\n"
        "  mention_patterns:\n"
        "    - \"^\\\\s*chompy\\\\b\"\n"
        "  free_response_chats:\n"
        "    - \"-123\"\n"
        "  allowed_chats:\n"
        "    - \"-100\"\n"
        "  group_allowed_chats:\n"
        "    - \"-100\"\n"
        "  allowed_topics:\n"
        "    - 8\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    # Clear the TELEGRAM_* vars this test exercises so a developer's ambient
    # shell/.env values don't pre-empt the YAML→env bridge (env-over-YAML
    # precedence, adapter.py::_apply_yaml_config). The authoritative assertions
    # below read the returned config object, which is immune to env pollution
    # from third-party import-time load_dotenv calls; see the note at the asserts.
    for _var in (
        "TELEGRAM_REQUIRE_MENTION",
        "TELEGRAM_MENTION_PATTERNS",
        "TELEGRAM_EXCLUSIVE_BOT_MENTIONS",
        "TELEGRAM_GUEST_MODE",
        "TELEGRAM_OBSERVE_UNMENTIONED_GROUP_MESSAGES",
        "TELEGRAM_FREE_RESPONSE_CHATS",
        "TELEGRAM_ALLOWED_CHATS",
        "TELEGRAM_GROUP_ALLOWED_CHATS",
        "TELEGRAM_ALLOWED_TOPICS",
    ):
        monkeypatch.delenv(_var, raising=False)

    config = load_gateway_config()

    # Assert against the returned config object — the authoritative result of the
    # bridge. We deliberately do NOT assert on os.environ here: a third-party
    # import (microsoft_teams/apps/app.py) runs load_dotenv(find_dotenv(usecwd=True))
    # at import time, which walks up from cwd and can repopulate TELEGRAM_* vars
    # from a developer's real ~/.hermes/.env, defeating the env-over-YAML bridge
    # for any key present there. The PlatformConfig.extra values below are parsed
    # straight from the test's config.yaml and are immune to that ambient leak.
    assert config is not None
    tg_cfg = config.platforms.get(Platform.TELEGRAM)
    assert tg_cfg is not None
    assert tg_cfg.extra.get("require_mention") is True
    assert tg_cfg.extra.get("guest_mode") is True
    assert tg_cfg.extra.get("exclusive_bot_mentions") is True
    assert tg_cfg.extra.get("observe_unmentioned_group_messages") is True
    assert tg_cfg.extra.get("mention_patterns") == [r"^\s*chompy\b"]
    assert tg_cfg.extra.get("allowed_chats") == ["-100"]
    assert tg_cfg.extra.get("group_allowed_chats") == ["-100"]
    assert tg_cfg.extra.get("allowed_topics") == [8]
    # free_response_chats is bridged to the env var only (not PlatformConfig.extra).
    # TELEGRAM_FREE_RESPONSE_CHATS is not a key that appears in developer .env
    # files, so asserting it via os.environ stays deterministic.
    assert __import__("os").environ["TELEGRAM_FREE_RESPONSE_CHATS"] == "-123"


def test_config_bridges_telegram_user_allowlists(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        "telegram:\n"
        "  allow_from:\n"
        "    - \"111\"\n"
        "    - \"222\"\n"
        "  group_allow_from:\n"
        "    - \"333\"\n"
        "  group_allowed_chats:\n"
        "    - \"-100\"\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delenv("TELEGRAM_ALLOWED_USERS", raising=False)
    monkeypatch.delenv("TELEGRAM_GROUP_ALLOWED_USERS", raising=False)
    monkeypatch.delenv("TELEGRAM_GROUP_ALLOWED_CHATS", raising=False)

    config = load_gateway_config()

    assert config is not None
    assert __import__("os").environ["TELEGRAM_ALLOWED_USERS"] == "111,222"
    assert __import__("os").environ["TELEGRAM_GROUP_ALLOWED_USERS"] == "333"
    # group_allowed_chats via the config object, not os.environ: the
    # microsoft_teams import-time load_dotenv(find_dotenv(usecwd=True)) can
    # repopulate TELEGRAM_GROUP_ALLOWED_CHATS from a developer's real
    # ~/.hermes/.env, which would defeat the env-over-YAML bridge here.
    tg_cfg = config.platforms.get(Platform.TELEGRAM)
    assert tg_cfg is not None
    assert tg_cfg.extra.get("group_allowed_chats") == ["-100"]


def test_config_env_overrides_telegram_user_allowlists(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        "telegram:\n"
        "  allow_from: \"111\"\n"
        "  group_allow_from: \"222\"\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "999")
    monkeypatch.setenv("TELEGRAM_GROUP_ALLOWED_USERS", "888")

    config = load_gateway_config()

    assert config is not None
    assert __import__("os").environ["TELEGRAM_ALLOWED_USERS"] == "999"
    assert __import__("os").environ["TELEGRAM_GROUP_ALLOWED_USERS"] == "888"


def test_dm_allow_from_is_enforced_by_gateway_authorization_not_trigger_gate():
    adapter = _make_adapter(allow_from=["111", "222"])

    assert adapter._should_process_message(_dm_message("hello", from_user_id=111)) is True
    assert adapter._should_process_message(_dm_message("hello", from_user_id=333)) is True


def test_group_allow_from_is_enforced_by_gateway_authorization_not_trigger_gate():
    adapter = _make_adapter(group_allow_from=["111"])

    assert adapter._should_process_message(_group_message("hello", from_user_id=333)) is True


def test_top_level_require_mention_bridges_to_telegram(monkeypatch, tmp_path):
    """require_mention at the config.yaml top level (alongside group_sessions_per_user)
    must behave identically to telegram.require_mention: true (#3979).
    """
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    # Intentionally no "telegram:" section — keys are at the top level.
    (hermes_home / "config.yaml").write_text(
        "require_mention: true\n"
        "group_sessions_per_user: true\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delenv("TELEGRAM_REQUIRE_MENTION", raising=False)

    config = load_gateway_config()

    assert config is not None
    assert __import__("os").environ.get("TELEGRAM_REQUIRE_MENTION") == "true"

    # The adapter's extra dict must also carry the setting so that
    # _telegram_require_mention() works even without the env var.
    tg_cfg = config.platforms.get(__import__("gateway.config", fromlist=["Platform"]).Platform.TELEGRAM)
    if tg_cfg is not None:
        assert tg_cfg.extra.get("require_mention") is True


def test_top_level_require_mention_does_not_override_telegram_section(monkeypatch, tmp_path):
    """When telegram.require_mention is explicitly set, top-level require_mention
    must not override it (platform-specific config takes precedence).
    """
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        "require_mention: true\n"
        "telegram:\n"
        "  require_mention: false\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delenv("TELEGRAM_REQUIRE_MENTION", raising=False)

    config = load_gateway_config()

    assert config is not None
    # The telegram-specific "false" must win over the top-level "true".
    assert __import__("os").environ.get("TELEGRAM_REQUIRE_MENTION") == "false"


def test_config_bridges_telegram_free_response_topics(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        "telegram:\n"
        "  free_response_topics:\n"
        '    - "-1001234567:3"\n'
        '    - "-1001234567:9"\n',
        encoding="utf-8",
    )

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delenv("TELEGRAM_FREE_RESPONSE_TOPICS", raising=False)

    config = load_gateway_config()

    assert config is not None
    tg_cfg = config.platforms.get(Platform.TELEGRAM)
    assert tg_cfg is not None
    # free_response_topics is carried in PlatformConfig.extra (like guest_mode)
    # AND bridged to the env var the adapter reads at runtime. The env var is
    # not a key that appears in developer .env files, so asserting it via
    # os.environ stays deterministic.
    assert tg_cfg.extra.get("free_response_topics") == ["-1001234567:3", "-1001234567:9"]
    assert __import__("os").environ["TELEGRAM_FREE_RESPONSE_TOPICS"] == "-1001234567:3,-1001234567:9"


def test_config_bridges_telegram_ignored_threads(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        "telegram:\n"
        "  ignored_threads:\n"
        "    - 31\n"
        "    - \"42\"\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delenv("TELEGRAM_IGNORED_THREADS", raising=False)

    config = load_gateway_config()

    assert config is not None
    assert __import__("os").environ["TELEGRAM_IGNORED_THREADS"] == "31,42"


# ---------------------------------------------------------------------------
# Helpers for location / media observe+attribution tests
# ---------------------------------------------------------------------------

def _group_location_message(
    *,
    chat_id=-100,
    from_user_id=111,
    from_user_name="Alice Example",
    lat=37.7749,
    lon=-122.4194,
):
    return SimpleNamespace(
        message_id=50,
        text=None,
        caption=None,
        entities=[],
        caption_entities=[],
        message_thread_id=None,
        is_topic_message=False,
        chat=SimpleNamespace(id=chat_id, type="group", title="Test Group", is_forum=False),
        from_user=SimpleNamespace(
            id=from_user_id, full_name=from_user_name,
            first_name=from_user_name.split()[0],
        ),
        reply_to_message=None,
        date=None,
        location=SimpleNamespace(latitude=lat, longitude=lon),
        venue=None,
        sticker=None,
        photo=None,
        video=None,
        audio=None,
        voice=None,
        document=None,
    )


def _group_voice_message(
    *,
    chat_id=-100,
    from_user_id=111,
    from_user_name="Alice Example",
    caption=None,
):
    return SimpleNamespace(
        message_id=51,
        text=None,
        caption=caption,
        entities=[],
        caption_entities=[],
        message_thread_id=None,
        is_topic_message=False,
        chat=SimpleNamespace(id=chat_id, type="group", title="Test Group", is_forum=False),
        from_user=SimpleNamespace(
            id=from_user_id, full_name=from_user_name,
            first_name=from_user_name.split()[0],
        ),
        reply_to_message=None,
        date=None,
        location=None,
        venue=None,
        sticker=None,
        photo=None,
        video=None,
        audio=None,
        voice=SimpleNamespace(
            get_file=AsyncMock(side_effect=Exception("simulated download failure"))
        ),
        document=None,
    )


# ---------------------------------------------------------------------------
# Observe + attribution parity: location messages
# ---------------------------------------------------------------------------

def test_unmentioned_location_message_observed_in_group():
    async def _run():
        adapter = _make_adapter(
            require_mention=True,
            allowed_chats=["-100"],
            group_allowed_chats=["-100"],
            observe_unmentioned_group_messages=True,
        )
        store = _FakeSessionStore()
        adapter._session_store = store
        update = SimpleNamespace(
            update_id=2001,
            message=_group_location_message(),
            effective_message=None,
        )

        await adapter._handle_location_message(update, SimpleNamespace())

        adapter._message_handler.assert_not_awaited()
        assert len(store.messages) == 1
        _, message, _ = store.messages[0]
        assert message["observed"] is True
        assert store.sources[0].user_id is None

    asyncio.run(_run())


def test_triggered_location_message_uses_shared_session_in_observe_mode():
    async def _run():
        adapter = _make_adapter(
            require_mention=False,
            group_allowed_chats=["-100"],
            observe_unmentioned_group_messages=True,
        )
        adapter.handle_message = AsyncMock()
        update = SimpleNamespace(
            update_id=2002,
            message=_group_location_message(),
            effective_message=None,
        )

        await adapter._handle_location_message(update, SimpleNamespace())

        adapter.handle_message.assert_awaited_once()
        event = adapter.handle_message.call_args[0][0]
        assert event.source.user_id is None
        assert "[Alice Example|111]" in event.text

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Observe + attribution parity: media messages (voice as representative)
# ---------------------------------------------------------------------------

def test_unmentioned_voice_message_observed_in_group():
    async def _run():
        adapter = _make_adapter(
            require_mention=True,
            allowed_chats=["-100"],
            group_allowed_chats=["-100"],
            observe_unmentioned_group_messages=True,
        )
        store = _FakeSessionStore()
        adapter._session_store = store
        update = SimpleNamespace(
            update_id=3001,
            message=_group_voice_message(),
            effective_message=None,
        )

        await adapter._handle_media_message(update, SimpleNamespace())

        adapter._message_handler.assert_not_awaited()
        assert len(store.messages) == 1
        _, message, _ = store.messages[0]
        assert message["observed"] is True
        assert store.sources[0].user_id is None

    asyncio.run(_run())


def test_triggered_voice_message_uses_shared_session_in_observe_mode():
    async def _run():
        adapter = _make_adapter(
            require_mention=False,
            group_allowed_chats=["-100"],
            observe_unmentioned_group_messages=True,
        )
        adapter.handle_message = AsyncMock()
        update = SimpleNamespace(
            update_id=3002,
            message=_group_voice_message(caption="check this audio"),
            effective_message=None,
        )

        await adapter._handle_media_message(update, SimpleNamespace())

        adapter.handle_message.assert_awaited_once()
        event = adapter.handle_message.call_args[0][0]
        assert event.source.user_id is None
        assert "[Alice Example|111]" in event.text

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Replied-to media caching
# ---------------------------------------------------------------------------

def test_text_reply_to_photo_caches_referenced_media(monkeypatch, tmp_path):
    async def _run():
        adapter = _make_adapter(require_mention=False)
        adapter.handle_message = AsyncMock()
        cached_path = tmp_path / "reply_photo.png"
        monkeypatch.setattr(
            "gateway.platforms.base.cache_image_from_bytes",
            lambda _data, ext=".jpg": str(cached_path),
        )
        file_obj = SimpleNamespace(
            file_path="photos/replied.png",
            download_as_bytearray=AsyncMock(return_value=bytearray(b"\x89PNG\r\n\x1a\n reply")),
        )
        photo = SimpleNamespace(file_size=1234, get_file=AsyncMock(return_value=file_obj))
        replied = SimpleNamespace(
            message_id=51,
            text=None,
            caption=None,
            photo=[photo],
            video=None,
            audio=None,
            voice=None,
            document=None,
        )
        msg = _group_message("what's in this image?", reply_to_bot=False)
        msg.reply_to_message = replied
        update = SimpleNamespace(update_id=3010, message=msg, effective_message=msg)

        await adapter._handle_text_message(update, SimpleNamespace())
        await asyncio.sleep(0.05)

        adapter.handle_message.assert_awaited_once()
        await_args = adapter.handle_message.await_args
        assert await_args is not None
        event = await_args.args[0]
        assert event.reply_to_message_id == "51"
        assert event.media_urls == [str(cached_path)]
        assert event.media_types == ["image/png"]
        assert event.message_type == MessageType.PHOTO

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Observed-media caching (unmentioned group attachments)
# ---------------------------------------------------------------------------

def _group_photo_message(*, chat_id=-100, caption="Veja esta foto", file_size=1024):
    file_obj = SimpleNamespace(
        file_path="photos/observed.png",
        download_as_bytearray=AsyncMock(return_value=bytearray(b"\x89PNG\r\n\x1a\n observed")),
    )
    photo = SimpleNamespace(file_size=file_size, get_file=AsyncMock(return_value=file_obj))
    return SimpleNamespace(
        message_id=52, text=None, caption=caption, entities=[], caption_entities=[],
        message_thread_id=None, is_topic_message=False,
        chat=SimpleNamespace(id=chat_id, type="group", title="Test Group", is_forum=False),
        from_user=SimpleNamespace(id=111, full_name="Alice Example", first_name="Alice"),
        reply_to_message=None, date=None, location=None, venue=None,
        sticker=None, photo=[photo], video=None, audio=None, voice=None, document=None,
    )


def _group_document_message(*, chat_id=-100, caption="Este arquivo", document=None):
    file_obj = SimpleNamespace(
        file_path="documents/report.pdf",
        download_as_bytearray=AsyncMock(return_value=bytearray(b"%PDF observed bytes")),
    )
    document = document or SimpleNamespace(
        file_name="RESULTADO BIOLOGICO - PROTOCOLO 103- URBAN.pdf",
        mime_type="application/pdf", file_size=1024,
        get_file=AsyncMock(return_value=file_obj),
    )
    return SimpleNamespace(
        message_id=53, text=None, caption=caption, entities=[], caption_entities=[],
        message_thread_id=None, is_topic_message=False,
        chat=SimpleNamespace(id=chat_id, type="group", title="Test Group", is_forum=False),
        from_user=SimpleNamespace(id=111, full_name="Alice Example", first_name="Alice"),
        reply_to_message=None, date=None, location=None, venue=None,
        sticker=None, photo=None, video=None, audio=None, voice=None, document=document,
    )


def test_unmentioned_photo_observed_with_cached_path(monkeypatch, tmp_path):
    async def _run():
        adapter = _make_adapter(
            require_mention=True, allowed_chats=["-100"],
            group_allowed_chats=["-100"], observe_unmentioned_group_messages=True,
        )
        store = _FakeSessionStore()
        adapter._session_store = store
        cached_path = tmp_path / "img_abc_observed.png"
        monkeypatch.setattr(
            "gateway.platforms.base.cache_image_from_bytes",
            lambda _data, ext=".jpg": str(cached_path),
        )
        update = SimpleNamespace(update_id=3003, message=_group_photo_message(), effective_message=None)

        await adapter._handle_media_message(update, SimpleNamespace())

        adapter._message_handler.assert_not_awaited()
        assert len(store.messages) == 1
        _, message, _ = store.messages[0]
        assert message["observed"] is True
        assert "Veja esta foto" in message["content"]
        assert "image" in message["content"]
        assert str(cached_path) in message["content"]
        assert store.sources[0].user_id is None

    asyncio.run(_run())


def test_unmentioned_document_observed_with_cached_path(monkeypatch, tmp_path):
    async def _run():
        adapter = _make_adapter(
            require_mention=True, allowed_chats=["-100"],
            group_allowed_chats=["-100"], observe_unmentioned_group_messages=True,
        )
        store = _FakeSessionStore()
        adapter._session_store = store
        cached_path = tmp_path / "doc_abc_report.pdf"
        monkeypatch.setattr(
            "gateway.platforms.base.cache_document_from_bytes",
            lambda _data, _filename: str(cached_path),
        )
        update = SimpleNamespace(update_id=3004, message=_group_document_message(), effective_message=None)

        await adapter._handle_media_message(update, SimpleNamespace())

        adapter._message_handler.assert_not_awaited()
        assert len(store.messages) == 1
        _, message, _ = store.messages[0]
        assert message["observed"] is True
        assert "Este arquivo" in message["content"]
        assert str(cached_path) in message["content"]

    asyncio.run(_run())


def test_unmentioned_large_document_observed_without_download(monkeypatch):
    async def _run():
        adapter = _make_adapter(
            require_mention=True, allowed_chats=["-100"],
            group_allowed_chats=["-100"], observe_unmentioned_group_messages=True,
        )
        adapter._max_doc_bytes = 100
        store = _FakeSessionStore()
        adapter._session_store = store
        cache_doc = Mock(return_value="/tmp/huge.pdf")
        monkeypatch.setattr("gateway.platforms.base.cache_document_from_bytes", cache_doc)
        document = SimpleNamespace(
            file_name="huge.pdf", mime_type="application/pdf",
            file_size=101, get_file=AsyncMock(),
        )
        update = SimpleNamespace(
            update_id=3005, message=_group_document_message(document=document), effective_message=None,
        )

        await adapter._handle_media_message(update, SimpleNamespace())

        cache_doc.assert_not_called()
        document.get_file.assert_not_called()
        _, message, _ = store.messages[0]
        assert "too large" in message["content"]
        assert "/tmp/huge.pdf" not in message["content"]

    asyncio.run(_run())


def test_unmentioned_unsupported_document_observed_and_cached(monkeypatch):
    async def _run():
        adapter = _make_adapter(
            require_mention=True, allowed_chats=["-100"],
            group_allowed_chats=["-100"], observe_unmentioned_group_messages=True,
        )
        store = _FakeSessionStore()
        adapter._session_store = store
        cache_doc = Mock(return_value="/tmp/program.exe")
        monkeypatch.setattr("gateway.platforms.base.cache_document_from_bytes", cache_doc)
        file_obj = SimpleNamespace(
            file_path="documents/program.exe",
            download_as_bytearray=AsyncMock(return_value=bytearray(b"MZ")),
        )
        document = SimpleNamespace(
            file_name="program.exe", mime_type="application/x-msdownload",
            file_size=2, get_file=AsyncMock(return_value=file_obj),
        )
        update = SimpleNamespace(
            update_id=3006, message=_group_document_message(document=document), effective_message=None,
        )

        await adapter._handle_media_message(update, SimpleNamespace())

        # Any file type is now cached — authorization is the gate, not the
        # extension. The observed message records a path-pointing note.
        cache_doc.assert_called_once()
        _, message, _ = store.messages[0]
        assert "program.exe" in message["content"]

    asyncio.run(_run())
