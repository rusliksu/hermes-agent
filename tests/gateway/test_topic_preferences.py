"""Durable topic-scoped model and reasoning preferences."""

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from gateway.config import GatewayConfig, Platform
from gateway.single_principal import SinglePrincipalPolicy
from gateway.session import (
    AsyncSessionStore,
    SessionSource,
    SessionStore,
    build_topic_preference_key,
    sanitize_topic_preferences,
)


def _source(**overrides):
    values = {
        "platform": Platform.TELEGRAM,
        "chat_id": "10001",
        "chat_type": "dm",
        "thread_id": "17585",
        "user_id": "10001",
    }
    values.update(overrides)
    return SessionSource(**values)


def _store_factory(tmp_path, monkeypatch, config=None):
    from hermes_state import SessionDB as RealSessionDB
    import hermes_state

    db_path = tmp_path / "state.db"
    opened = []

    def _open():
        db = RealSessionDB(db_path=db_path)
        opened.append(db)
        return db

    monkeypatch.setattr(hermes_state, "SessionDB", _open)

    def _make(scope="sessions"):
        return SessionStore(tmp_path / scope, config or GatewayConfig())

    return _make, opened


def _shared_room_config():
    return GatewayConfig(
        multiplex_profiles=True,
        single_principal=SinglePrincipalPolicy.from_dict(
            {
                "enabled": True,
                "telegram_owner_id": "10001",
                "telegram_allowed_user_ids": ["20002"],
                "telegram_shared_chat_ids": ["-10001"],
            }
        ),
    )


def test_lane_key_is_versioned_and_isolates_every_identity_component():
    base = _source()
    key = build_topic_preference_key(base)

    assert key.startswith("lane:v1:")
    assert len(key) == len("lane:v1:") + 64
    variants = [
        _source(chat_id="other"),
        _source(thread_id="other"),
        _source(user_id="other"),
        _source(chat_type="group"),
        _source(platform=Platform.DISCORD),
    ]
    assert all(build_topic_preference_key(item) != key for item in variants)
    assert build_topic_preference_key(base, profile="coder") != key


def test_topic_preference_sanitizer_drops_credentials_and_invalid_effort():
    assert sanitize_topic_preferences(
        {
            "model_override": {
                "model": "gpt-5.6-luna",
                "provider": "openai-codex",
                "base_url": "https://example.invalid",
                "api_key": "secret",
                "api_mode": "codex_responses",
                "credential_pool": "secret-pool",
            },
            "reasoning_effort": "HIGH",
            "service_tier": "priority",
        }
    ) == {
        "model_override": {
            "model": "gpt-5.6-luna",
            "provider": "openai-codex",
            "base_url": "https://example.invalid",
        },
        "reasoning_effort": "high",
    }
    assert sanitize_topic_preferences({"reasoning_effort": "bogus"}) == {}


def test_topic_preference_never_persists_credential_bearing_base_url():
    cleaned = sanitize_topic_preferences(
        {
            "model_override": {
                "model": "custom-model",
                "provider": "custom",
                "base_url": "https://token@host.invalid/v1?key=secret#fragment",
            }
        }
    )

    assert cleaned == {
        "model_override": {"model": "custom-model", "provider": "custom"}
    }
    assert "secret" not in json.dumps(cleaned)


def test_preferences_survive_store_restart_and_new(tmp_path, monkeypatch):
    make_store, opened = _store_factory(tmp_path, monkeypatch)
    source = _source()
    override = {
        "model": "gpt-5.6-luna",
        "provider": "openai-codex",
        "api_key": "must-not-persist",
    }

    store = make_store()
    entry = store.get_or_create_session(source)
    store.update_topic_preferences(
        source, model_override=override, reasoning_effort="high"
    )
    store.reset_session(entry.session_key)

    restarted = make_store()
    assert restarted.get_topic_preferences(source) == {
        "model_override": {
            "model": "gpt-5.6-luna",
            "provider": "openai-codex",
        },
        "reasoning_effort": "high",
    }
    lane_key = restarted._generate_topic_preference_key(source)
    raw = restarted._db.load_gateway_topic_preferences(
        lane_key, scope=restarted._routing_scope()
    )
    assert raw is not None
    assert "must-not-persist" not in raw
    assert "api_key" not in raw
    assert json.loads(raw)["reasoning_effort"] == "high"

    for db in opened:
        db.close()


def test_preferences_are_isolated_by_topic_and_store_scope(tmp_path, monkeypatch):
    make_store, opened = _store_factory(tmp_path, monkeypatch)
    first = make_store("one")
    second_scope = make_store("two")
    source = _source()
    other_topic = _source(thread_id="222")

    first.update_topic_preferences(source, reasoning_effort="high")

    assert first.get_topic_preferences(other_topic) == {}
    assert second_scope.get_topic_preferences(source) == {}

    for db in opened:
        db.close()


def test_shared_room_preferences_are_topic_wide_across_participants(
    tmp_path, monkeypatch
):
    make_store, opened = _store_factory(
        tmp_path, monkeypatch, config=_shared_room_config()
    )
    store = make_store()
    owner = _source(
        chat_id="-10001",
        chat_type="group",
        thread_id="4",
        user_id="10001",
        profile="room-drafts",
    )
    family = _source(
        chat_id="-10001",
        chat_type="group",
        thread_id="4",
        user_id="20002",
        profile="room-drafts",
    )

    assert store._generate_topic_preference_key(owner) == (
        store._generate_topic_preference_key(family)
    )
    store.update_topic_preferences(
        owner,
        model_override={"model": "gpt-5.6-luna", "provider": "openai-codex"},
    )
    assert store.get_topic_preferences(family)["model_override"] == {
        "model": "gpt-5.6-luna",
        "provider": "openai-codex",
    }

    other_topic = _source(
        chat_id="-10001",
        chat_type="group",
        thread_id="5",
        user_id="20002",
        profile="room-drafts",
    )
    other_room = _source(
        chat_id="-20002",
        chat_type="group",
        thread_id="4",
        user_id="20002",
        profile="room-drafts",
    )
    assert store.get_topic_preferences(other_topic) == {}
    assert store._generate_topic_preference_key(other_room) != (
        store._generate_topic_preference_key(owner)
    )

    for db in opened:
        db.close()


def test_shared_room_migrates_latest_authorized_sender_preference(
    tmp_path, monkeypatch
):
    make_store, opened = _store_factory(
        tmp_path, monkeypatch, config=_shared_room_config()
    )
    store = make_store()
    owner = _source(
        chat_id="-10001",
        chat_type="group",
        thread_id="4",
        user_id="10001",
        profile="room-drafts",
    )
    family = _source(
        chat_id="-10001",
        chat_type="group",
        thread_id="4",
        user_id="20002",
        profile="room-drafts",
    )
    owner_legacy_key = build_topic_preference_key(owner, profile="room-drafts")
    family_legacy_key = build_topic_preference_key(family, profile="room-drafts")
    store._db.save_gateway_topic_preferences(
        owner_legacy_key,
        json.dumps(
            {
                "model_override": {
                    "model": "gpt-5.6-sol",
                    "provider": "openai-codex",
                }
            }
        ),
        scope=store._routing_scope(),
    )
    time.sleep(0.01)
    store._db.save_gateway_topic_preferences(
        family_legacy_key,
        json.dumps(
            {
                "model_override": {
                    "model": "gpt-5.6-luna",
                    "provider": "openai-codex",
                    "api_key": "must-not-migrate",
                }
            }
        ),
        scope=store._routing_scope(),
    )

    assert store.get_topic_preferences(owner) == {
        "model_override": {
            "model": "gpt-5.6-luna",
            "provider": "openai-codex",
        }
    }
    canonical_key = store._generate_topic_preference_key(owner)
    canonical_raw = store._db.load_gateway_topic_preferences(
        canonical_key, scope=store._routing_scope()
    )
    assert canonical_raw is not None
    assert "must-not-migrate" not in canonical_raw
    assert store._db.load_gateway_topic_preferences(
        owner_legacy_key, scope=store._routing_scope()
    ) is None
    assert store._db.load_gateway_topic_preferences(
        family_legacy_key, scope=store._routing_scope()
    ) is None

    for db in opened:
        db.close()


def test_non_shared_preferences_remain_sender_scoped(tmp_path, monkeypatch):
    make_store, opened = _store_factory(
        tmp_path, monkeypatch, config=_shared_room_config()
    )
    store = make_store()
    first = _source(user_id="10001", profile="room-drafts")
    second = _source(user_id="20002", profile="room-drafts")

    assert store._generate_topic_preference_key(first) != (
        store._generate_topic_preference_key(second)
    )

    for db in opened:
        db.close()


def test_clearing_last_preference_deletes_row(tmp_path, monkeypatch):
    make_store, opened = _store_factory(tmp_path, monkeypatch)
    store = make_store()
    source = _source()
    lane_key = store._generate_topic_preference_key(source)

    store.update_topic_preferences(source, reasoning_effort="none")
    store.update_topic_preferences(source, reasoning_effort=None)

    assert store.get_topic_preferences(source) == {}
    assert store._db.load_gateway_topic_preferences(
        lane_key, scope=store._routing_scope()
    ) is None

    for db in opened:
        db.close()


def test_concurrent_model_and_reasoning_updates_do_not_clobber(tmp_path, monkeypatch):
    make_store, opened = _store_factory(tmp_path, monkeypatch)
    store = make_store()
    source = _source()
    store.get_topic_preferences(source)  # Warm the empty cache for both writers.

    original_save = store._db.save_gateway_topic_preferences

    def _slow_save(*args, **kwargs):
        time.sleep(0.03)
        return original_save(*args, **kwargs)

    monkeypatch.setattr(store._db, "save_gateway_topic_preferences", _slow_save)
    ready = threading.Barrier(2)

    def _write_model():
        ready.wait()
        store.update_topic_preferences(
            source,
            model_override={"model": "gpt-5.6-luna", "provider": "openai-codex"},
        )

    def _write_reasoning():
        ready.wait()
        store.update_topic_preferences(source, reasoning_effort="high")

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda fn: fn(), (_write_model, _write_reasoning)))

    assert store.get_topic_preferences(source) == {
        "model_override": {
            "model": "gpt-5.6-luna",
            "provider": "openai-codex",
        },
        "reasoning_effort": "high",
    }

    for db in opened:
        db.close()


def test_cold_reader_cannot_overwrite_newer_writer_cache(tmp_path, monkeypatch):
    make_store, opened = _store_factory(tmp_path, monkeypatch)
    store = make_store()
    source = _source()
    store.update_topic_preferences(source, reasoning_effort="low")
    with store._topic_preferences_lock:
        store._topic_preferences.clear()

    original_load = store._db.load_gateway_topic_preferences
    reader_loaded = threading.Event()
    release_reader = threading.Event()
    call_lock = threading.Lock()
    call_count = 0

    def _load_then_block_first(*args, **kwargs):
        nonlocal call_count
        raw = original_load(*args, **kwargs)
        with call_lock:
            call_count += 1
            is_first = call_count == 1
        if is_first:
            reader_loaded.set()
            if not release_reader.wait(timeout=2):
                raise TimeoutError("writer did not release cold preference reader")
        return raw

    monkeypatch.setattr(
        store._db, "load_gateway_topic_preferences", _load_then_block_first
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        stale_reader = pool.submit(store.get_topic_preferences, source)
        assert reader_loaded.wait(timeout=1)
        store.update_topic_preferences(source, reasoning_effort="high")
        release_reader.set()
        stale_reader.result(timeout=1)

    assert store.get_topic_preferences(source)["reasoning_effort"] == "high"

    for db in opened:
        db.close()


@pytest.mark.asyncio
async def test_cold_async_preference_lookup_does_not_block_event_loop(
    tmp_path, monkeypatch
):
    make_store, opened = _store_factory(tmp_path, monkeypatch)
    store = make_store()
    source = _source()
    facade = AsyncSessionStore(store)
    original_get = store.get_topic_preferences
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    worker_threads = []

    def _slow_get(requested_source):
        worker_threads.append(threading.get_ident())
        loop.call_soon_threadsafe(entered.set)
        if not release.wait(timeout=2):
            raise TimeoutError("event loop did not release cold preference lookup")
        return original_get(requested_source)

    monkeypatch.setattr(store, "get_topic_preferences", _slow_get)
    task = asyncio.create_task(facade.get_topic_preferences(source))
    try:
        await asyncio.wait_for(entered.wait(), timeout=0.5)
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()

    assert await task == {}
    assert len(worker_threads) == 1
    assert worker_threads[0] != threading.get_ident()

    for db in opened:
        db.close()


@pytest.mark.asyncio
async def test_warm_async_preference_lookup_stays_in_memory(tmp_path, monkeypatch):
    make_store, opened = _store_factory(tmp_path, monkeypatch)
    store = make_store()
    source = _source()
    assert store.get_topic_preferences(source) == {}

    async def _unexpected_offload(*_args, **_kwargs):
        raise AssertionError("warm preference lookup should not use a worker thread")

    monkeypatch.setattr(asyncio, "to_thread", _unexpected_offload)

    assert await AsyncSessionStore(store).get_topic_preferences(source) == {}

    for db in opened:
        db.close()
