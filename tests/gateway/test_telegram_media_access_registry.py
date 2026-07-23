import asyncio
import json
import sys
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.access_registry import (
    AccessRegistry,
    DeliveryTarget,
    PrincipalBinding,
    ResolvedAccessContext,
    RolePolicy,
    TransportIdentity,
)
from gateway.config import PlatformConfig
from gateway.run import GatewayRunner


def _ensure_telegram_mock():
    if "telegram" in sys.modules and hasattr(sys.modules["telegram"], "__file__"):
        return
    telegram_mod = MagicMock()
    telegram_mod.ext.ContextTypes.DEFAULT_TYPE = type(None)
    telegram_mod.constants.ParseMode.MARKDOWN_V2 = "MarkdownV2"
    telegram_mod.constants.ChatType.GROUP = "group"
    telegram_mod.constants.ChatType.SUPERGROUP = "supergroup"
    telegram_mod.constants.ChatType.CHANNEL = "channel"
    telegram_mod.constants.ChatType.PRIVATE = "private"
    for name in ("telegram", "telegram.ext", "telegram.constants", "telegram.request"):
        sys.modules.setdefault(name, telegram_mod)


_ensure_telegram_mock()

from plugins.platforms.telegram.adapter import TelegramAdapter  # noqa: E402


ACCOUNT = "bot-a"
CAPS = frozenset({"attachments", "vision"})
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 16


def _identity(user_id: str, *, account: str = ACCOUNT) -> TransportIdentity:
    return TransportIdentity(
        platform="telegram",
        account=account,
        peer_kind="dm",
        user_id=user_id,
        chat_id=user_id,
    )


def _target(identity: TransportIdentity) -> DeliveryTarget:
    return DeliveryTarget(
        platform=identity.platform,
        account=identity.account,
        peer_kind=identity.peer_kind,
        chat_id=identity.chat_id,
        thread_id=identity.thread_id,
    )


def _context(user_id: str, profile_id: str) -> ResolvedAccessContext:
    identity = _identity(user_id)
    return ResolvedAccessContext(
        principal_id=f"principal-{profile_id}",
        role_id="family",
        profile_id=profile_id,
        conversation_scope="private",
        capabilities=CAPS,
        delivery_target=_target(identity),
    )


def _registry(profile_by_user: dict[str, str]) -> AccessRegistry:
    return AccessRegistry(
        roles={"family": RolePolicy("family", CAPS)},
        profiles=frozenset(profile_by_user.values()),
        principal_bindings=tuple(
            PrincipalBinding(
                principal_id=f"principal-{profile_id}",
                role_id="family",
                profile_id=profile_id,
                transport_identity=_identity(user_id),
                conversation_scope="private",
                delivery_target=_target(_identity(user_id)),
            )
            for user_id, profile_id in profile_by_user.items()
        ),
        scope_capabilities={"private": CAPS},
        backend_capabilities=CAPS,
    )


class _Runner:
    def __init__(self, registry: AccessRegistry | None, homes: dict[str, Path]):
        self.access_registry = registry
        self.config = SimpleNamespace(multiplex_profiles=True)
        self._homes = homes
        self._allow_access_registry_ingress = MethodType(
            GatewayRunner._allow_access_registry_ingress,
            self,
        )

    def _profile_name_for_source(self, _source):
        return None

    def _resolve_profile_home_for_source(self, source):
        return self._homes[source.resolved_access_context.profile_id]


def _adapter(runner: _Runner | None = None) -> TelegramAdapter:
    adapter = TelegramAdapter(
        PlatformConfig(enabled=True, token="token", extra={"account": ACCOUNT})
    )
    adapter.gateway_runner = runner
    adapter.handle_message = AsyncMock()
    adapter._is_callback_user_authorized = lambda _user_id, **_kw: True
    return adapter


def _file(data: bytes, *, path: str = "file.bin"):
    file_obj = MagicMock()
    file_obj.file_path = path
    file_obj.download_as_bytearray = AsyncMock(return_value=bytearray(data))
    return file_obj


def _telegram_file(data: bytes, *, path: str = "file.bin", size: int | None = None):
    obj = MagicMock()
    obj.file_size = len(data) if size is None else size
    obj.get_file = AsyncMock(return_value=_file(data, path=path))
    return obj


def _document(
    data: bytes,
    *,
    file_name: str = "report.pdf",
    mime_type: str = "application/pdf",
):
    obj = _telegram_file(data, path=file_name)
    obj.file_name = file_name
    obj.mime_type = mime_type
    return obj


def _sticker(
    data: bytes = PNG,
    *,
    file_unique_id: str = "sticker-unique",
    emoji: str = "",
    set_name: str = "",
):
    obj = _telegram_file(data, path="sticker.webp")
    obj.emoji = emoji
    obj.set_name = set_name
    obj.file_unique_id = file_unique_id
    obj.is_animated = False
    obj.is_video = False
    return obj


def _message(
    *,
    user_id: str = "1",
    chat_id: str | None = None,
    chat_type: str = "private",
    text: str = "",
    caption: str | None = None,
    document=None,
    photo=None,
    sticker=None,
    reply_to_message=None,
):
    msg = MagicMock()
    msg.message_id = 42
    msg.text = text
    msg.caption = caption
    msg.date = None
    msg.photo = photo
    msg.video = None
    msg.audio = None
    msg.voice = None
    msg.sticker = sticker
    msg.document = document
    msg.media_group_id = None
    msg.reply_to_message = reply_to_message
    msg.message_thread_id = None
    msg.reply_text = AsyncMock()
    msg.chat = MagicMock()
    msg.chat.id = chat_id if chat_id is not None else user_id
    msg.chat.type = chat_type
    msg.chat.title = None
    msg.chat.full_name = "Test User"
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.from_user.full_name = "Test User"
    msg.from_user.username = None
    msg.from_user.is_bot = False
    return msg


def _update(msg, update_id: int = 100):
    return SimpleNamespace(message=msg, effective_message=msg, update_id=update_id)


@pytest.fixture(autouse=True)
def _temp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "default-home"))
    return tmp_path


@pytest.mark.asyncio
async def test_unknown_registry_document_media_denied_before_get_file_and_logs_redacted(tmp_path, caplog):
    runner = _Runner(_registry({"1": "profile-a"}), {"profile-a": tmp_path / "profile-a"})
    adapter = _adapter(runner)
    document = _document(b"document-bytes")
    msg = _message(user_id="9", document=document)

    with caplog.at_level("WARNING"):
        await adapter._handle_media_message(_update(msg), MagicMock())

    document.get_file.assert_not_called()
    adapter.handle_message.assert_not_called()
    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert "missing_principal_binding" in rendered
    for raw in (ACCOUNT, "9", "profile-a", "principal-profile-a"):
        assert raw not in rendered


@pytest.mark.asyncio
async def test_authorized_document_caches_under_resolved_profile(tmp_path):
    profile_home = tmp_path / "profiles" / "profile-a"
    runner = _Runner(_registry({"1": "profile-a"}), {"profile-a": profile_home})
    adapter = _adapter(runner)
    document = _document(b"document-bytes")

    await adapter._handle_media_message(_update(_message(user_id="1", document=document)), MagicMock())

    event = adapter.handle_message.call_args.args[0]
    cached = Path(event.media_urls[0]).resolve()
    assert cached.is_relative_to((profile_home / "cache" / "documents").resolve())
    assert not cached.is_relative_to((tmp_path / "default-home" / "cache").resolve())
    assert event.source.resolved_access_context == _context("1", "profile-a")
    assert len(event.source.resolved_access_context.__dataclass_fields__) == 6


@pytest.mark.asyncio
async def test_authorized_photo_caches_under_resolved_profile(tmp_path):
    profile_home = tmp_path / "profiles" / "profile-a"
    runner = _Runner(_registry({"1": "profile-a"}), {"profile-a": profile_home})
    adapter = _adapter(runner)
    photo = _telegram_file(PNG, path="photo.png")

    await adapter._handle_media_message(
        _update(_message(user_id="1", photo=[photo])),
        MagicMock(),
    )

    event = next(iter(adapter._pending_photo_batches.values()))
    cached = Path(event.media_urls[0]).resolve()
    assert cached.is_relative_to((profile_home / "cache" / "images").resolve())


@pytest.mark.asyncio
async def test_concurrent_two_profile_media_updates_do_not_cross_cache_paths(tmp_path):
    homes = {
        "profile-a": tmp_path / "profiles" / "profile-a",
        "profile-b": tmp_path / "profiles" / "profile-b",
    }
    runner = _Runner(_registry({"1": "profile-a", "2": "profile-b"}), homes)
    adapter = _adapter(runner)

    async def slow_download_a():
        await asyncio.sleep(0)
        return bytearray(b"a")

    async def slow_download_b():
        await asyncio.sleep(0)
        return bytearray(b"b")

    doc_a = _document(b"a", file_name="a.txt", mime_type="text/plain")
    doc_b = _document(b"b", file_name="b.txt", mime_type="text/plain")
    doc_a.get_file.return_value.download_as_bytearray = AsyncMock(side_effect=slow_download_a)
    doc_b.get_file.return_value.download_as_bytearray = AsyncMock(side_effect=slow_download_b)

    await asyncio.gather(
        adapter._handle_media_message(_update(_message(user_id="1", document=doc_a), 1), MagicMock()),
        adapter._handle_media_message(_update(_message(user_id="2", document=doc_b), 2), MagicMock()),
    )

    by_profile = {
        call.args[0].source.resolved_access_context.profile_id: Path(call.args[0].media_urls[0]).resolve()
        for call in adapter.handle_message.call_args_list
    }
    assert by_profile["profile-a"].is_relative_to((homes["profile-a"] / "cache" / "documents").resolve())
    assert by_profile["profile-b"].is_relative_to((homes["profile-b"] / "cache" / "documents").resolve())


@pytest.mark.asyncio
async def test_no_registry_keeps_legacy_default_cache_root(tmp_path):
    adapter = _adapter(None)
    document = _document(b"legacy", file_name="legacy.txt", mime_type="text/plain")

    await adapter._handle_media_message(_update(_message(user_id="1", document=document)), MagicMock())

    cached = Path(adapter.handle_message.call_args.args[0].media_urls[0]).resolve()
    assert cached.is_relative_to((tmp_path / "default-home" / "cache" / "documents").resolve())


@pytest.mark.asyncio
async def test_replied_media_denial_stops_before_get_file_and_text_enqueue(tmp_path):
    runner = _Runner(_registry({"1": "profile-a"}), {"profile-a": tmp_path / "profile-a"})
    adapter = _adapter(runner)
    replied_doc = _document(b"reply", file_name="reply.txt", mime_type="text/plain")
    reply = _message(user_id="1", document=replied_doc)
    msg = _message(user_id="9", text="see attached", reply_to_message=reply)

    await adapter._handle_text_message(_update(msg), MagicMock())

    replied_doc.get_file.assert_not_called()
    adapter.handle_message.assert_not_called()
    assert adapter._pending_text_batches == {}


@pytest.mark.asyncio
async def test_observed_media_denial_stops_before_get_file_and_observe_queue(tmp_path):
    runner = _Runner(_registry({"1": "profile-a"}), {"profile-a": tmp_path / "profile-a"})
    adapter = _adapter(runner)
    adapter._should_process_message = lambda _msg: False
    adapter._should_observe_unmentioned_group_message = lambda _msg: True
    adapter._observe_unmentioned_group_message = MagicMock()
    document = _document(b"group", file_name="group.txt", mime_type="text/plain")
    msg = _message(user_id="9", chat_id="-100", chat_type="group", document=document)

    await adapter._handle_media_message(_update(msg), MagicMock())

    document.get_file.assert_not_called()
    adapter._observe_unmentioned_group_message.assert_not_called()
    adapter.handle_message.assert_not_called()


@pytest.mark.asyncio
async def test_sticker_media_denial_stops_before_get_file_and_downstream(tmp_path):
    runner = _Runner(_registry({"1": "profile-a"}), {"profile-a": tmp_path / "profile-a"})
    adapter = _adapter(runner)
    sticker = _telegram_file(PNG, path="sticker.webp")
    sticker.emoji = ""
    sticker.set_name = ""
    sticker.file_unique_id = "sticker-unique"
    sticker.is_animated = False
    sticker.is_video = False
    msg = _message(user_id="9", sticker=sticker)

    await adapter._handle_media_message(_update(msg), MagicMock())

    sticker.get_file.assert_not_called()
    adapter.handle_message.assert_not_called()


@pytest.mark.asyncio
async def test_static_sticker_cache_and_vision_are_scoped_per_resolved_profile(tmp_path, monkeypatch):
    homes = {
        "profile-a": tmp_path / "profiles" / "profile-a",
        "profile-b": tmp_path / "profiles" / "profile-b",
    }
    runner = _Runner(_registry({"1": "profile-a", "2": "profile-b"}), homes)
    adapter = _adapter(runner)
    seen = []

    async def fake_vision(image_url=None, user_prompt=None, **_kwargs):
        from gateway.session_context import get_resolved_access_context
        from hermes_constants import get_hermes_home

        context = get_resolved_access_context(None)
        home = get_hermes_home()
        path = Path(image_url).resolve()
        seen.append((context, home, path, user_prompt))
        assert context is not None
        assert len(context.__dataclass_fields__) == 6
        assert path.is_relative_to((home / "cache" / "images").resolve())
        return json.dumps({"success": True, "analysis": f"description-{context.profile_id}"})

    monkeypatch.setattr("tools.vision_tools.vision_analyze_tool", fake_vision)

    await adapter._handle_media_message(
        _update(_message(user_id="1", sticker=_sticker(file_unique_id="same-uid")), 1),
        MagicMock(),
    )
    await adapter._handle_media_message(
        _update(_message(user_id="2", sticker=_sticker(file_unique_id="same-uid")), 2),
        MagicMock(),
    )

    first, second = [call.args[0] for call in adapter.handle_message.call_args_list]
    assert "description-profile-a" in first.text
    assert "description-profile-b" in second.text
    assert "description-profile-a" not in second.text
    assert [item[0].profile_id for item in seen] == ["profile-a", "profile-b"]
    assert seen[0][1] == homes["profile-a"]
    assert seen[1][1] == homes["profile-b"]
    assert seen[0][2].is_relative_to((homes["profile-a"] / "cache" / "images").resolve())
    assert seen[1][2].is_relative_to((homes["profile-b"] / "cache" / "images").resolve())


@pytest.mark.asyncio
async def test_static_sticker_vision_exception_resets_profile_and_access_scope(tmp_path, monkeypatch):
    profile_home = tmp_path / "profiles" / "profile-a"
    default_home = tmp_path / "default-home"
    runner = _Runner(_registry({"1": "profile-a"}), {"profile-a": profile_home})
    adapter = _adapter(runner)
    observed = {}

    async def failing_vision(image_url=None, user_prompt=None, **_kwargs):
        from gateway.session_context import get_resolved_access_context
        from hermes_constants import get_hermes_home

        observed["context"] = get_resolved_access_context(None)
        observed["home"] = get_hermes_home()
        observed["path"] = Path(image_url).resolve()
        raise RuntimeError("vision failed")

    monkeypatch.setattr("tools.vision_tools.vision_analyze_tool", failing_vision)

    await adapter._handle_media_message(
        _update(_message(user_id="1", sticker=_sticker(file_unique_id="exception-uid")), 1),
        MagicMock(),
    )

    from gateway.session_context import get_resolved_access_context
    from hermes_constants import get_hermes_home

    assert observed["context"].profile_id == "profile-a"
    assert observed["home"] == profile_home
    assert observed["path"].is_relative_to((profile_home / "cache" / "images").resolve())
    assert get_resolved_access_context(None) is None
    assert get_hermes_home() == default_home
