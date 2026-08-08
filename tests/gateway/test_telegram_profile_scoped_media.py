"""RED regression coverage for Telegram media profile scoping."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import PlatformConfig
from gateway.run import _profile_runtime_scope
from hermes_constants import get_hermes_home
from plugins.platforms.telegram.adapter import TelegramAdapter
from tests.gateway.test_telegram_documents import (
    _make_document,
    _make_file_obj,
    _make_message,
    _make_update,
)


@pytest.fixture()
def adapter():
    instance = TelegramAdapter(PlatformConfig(enabled=True, token="fake-token"))
    instance.handle_message = AsyncMock()
    instance._is_callback_user_authorized = lambda user_id, **_kwargs: True
    return instance


@pytest.mark.asyncio
async def test_docx_media_cache_uses_inbound_profile_home(adapter, tmp_path):
    default_home = get_hermes_home()
    profile_home = tmp_path / "profile-home"
    default_cache = default_home / "cache" / "documents"
    profile_cache = profile_home / "cache" / "documents"
    default_cache.mkdir(parents=True)
    profile_cache.mkdir(parents=True)
    docx_bytes = b"fake-docx-payload"
    document = _make_document(
        file_name="report.docx",
        mime_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        file_size=len(docx_bytes),
        file_obj=_make_file_obj(docx_bytes),
    )

    adapter.set_inbound_profile_scope(
        lambda profile_home=profile_home: _profile_runtime_scope(profile_home)
    )
    await adapter._handle_media_message(
        _make_update(_make_message(document=document)),
        SimpleNamespace(),
    )

    event = adapter.handle_message.call_args.args[0]
    cached_path = Path(event.media_urls[0])
    assert cached_path.parent == profile_cache
    assert cached_path.parent != default_cache


@pytest.mark.asyncio
async def test_registry_primary_telegram_adapter_scopes_docx_cache(adapter, tmp_path):
    default_home = get_hermes_home()
    profile_home = tmp_path / "profile-home"
    default_cache = default_home / "cache" / "documents"
    profile_cache = profile_home / "cache" / "documents"
    default_cache.mkdir(parents=True)
    profile_cache.mkdir(parents=True)
    docx_bytes = b"fake-docx-payload"
    document = _make_document(
        file_name="report.docx",
        mime_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        file_size=len(docx_bytes),
        file_obj=_make_file_obj(docx_bytes),
    )
    event = SimpleNamespace(
        source=SimpleNamespace(profile="secondary"),
        media_urls=[],
        media_types=[],
        text="",
    )
    adapter.gateway_runner = SimpleNamespace(
        config=SimpleNamespace(multiplex_profiles=True),
        access_registry=object(),
        _resolve_profile_home_for_source=lambda _source: profile_home,
    )
    adapter._build_message_event = lambda *_args, **_kwargs: event

    await adapter._handle_media_message(
        _make_update(_make_message(document=document)),
        SimpleNamespace(),
    )

    cached_path = Path(adapter.handle_message.call_args.args[0].media_urls[0])
    assert cached_path.is_relative_to(profile_cache)
    assert not cached_path.is_relative_to(default_cache)
