"""Focused RED coverage for the primary Telegram media ingress boundary."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from gateway.access_registry import (
    AccessRegistry,
    DeliveryTarget,
    PrincipalBinding,
    RolePolicy,
    TransportIdentity,
)
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageType
from gateway.run import GatewayRunner
from gateway.session_context import get_resolved_access_context, reset_session_vars
from plugins.platforms.telegram.adapter import TelegramAdapter


ACCOUNT = "synthetic-primary-bot"
CHAT_ID = "synthetic-dm"
PROFILE_ID = "synthetic-photo-profile"


def _registry() -> AccessRegistry:
    capabilities = frozenset({"attachments", "vision"})
    identity = TransportIdentity(
        platform="telegram",
        account=ACCOUNT,
        peer_kind="dm",
        user_id=CHAT_ID,
        chat_id=CHAT_ID,
    )
    return AccessRegistry(
        roles={"family": RolePolicy("family", capabilities)},
        profiles=frozenset({PROFILE_ID}),
        principal_bindings=(
            PrincipalBinding(
                principal_id="synthetic-principal",
                role_id="family",
                profile_id=PROFILE_ID,
                transport_identity=identity,
                conversation_scope="private",
                delivery_target=DeliveryTarget(
                    platform="telegram",
                    account=ACCOUNT,
                    peer_kind="dm",
                    chat_id=CHAT_ID,
                ),
            ),
        ),
        scope_capabilities={"private": capabilities},
        backend_capabilities=capabilities,
    )


class _DownloadedPhotoFile:
    file_path = "photos/synthetic-photo.jpg"

    async def download_as_bytearray(self):
        return bytearray(b"\xff\xd8\xffsynthetic-photo")


class _Photo:
    file_size = len(b"\xff\xd8\xffsynthetic-photo")

    async def get_file(self):
        return _DownloadedPhotoFile()


def _photo_update() -> SimpleNamespace:
    chat = SimpleNamespace(
        id=CHAT_ID,
        type="private",
        title=None,
        full_name=None,
        is_forum=False,
    )
    user = SimpleNamespace(
        id=CHAT_ID,
        username=None,
        full_name="synthetic-user",
        is_bot=False,
    )
    message = SimpleNamespace(
        message_id=1,
        text="",
        caption=None,
        date=None,
        photo=[_Photo()],
        sticker=None,
        video=None,
        audio=None,
        voice=None,
        document=None,
        media_group_id=None,
        chat=chat,
        from_user=user,
        sender_chat=None,
        message_thread_id=None,
        is_topic_message=False,
        reply_to_message=None,
        quote=None,
        api_kwargs={},
        entities=[],
        caption_entities=[],
    )
    return SimpleNamespace(message=message, update_id=1)


def _runner(registry: AccessRegistry) -> GatewayRunner:
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(
                enabled=True,
                token="synthetic-token",
                extra={"account": ACCOUNT, "allow_from": [CHAT_ID]},
            )
        },
        multiplex_profiles=True,
        access_registry=registry,
    )
    runner = object.__new__(GatewayRunner)
    runner.config = config
    runner.access_registry = registry
    return runner


@pytest.mark.asyncio
async def test_primary_photo_resolves_allowed_dm_before_profile_scoped_cache(
    monkeypatch,
    tmp_path,
):
    hermes_root = tmp_path / "hermes-root"
    profile_home = hermes_root / "profiles" / PROFILE_ID
    profile_home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(hermes_root))

    registry = _registry()
    runner = _runner(registry)
    assert (
        runner._resolve_profile_home_for_source.__func__
        is GatewayRunner._resolve_profile_home_for_source
    )

    adapter = TelegramAdapter(runner.config.platforms[Platform.TELEGRAM])
    adapter.gateway_runner = runner
    handled = []
    handled_contexts = []

    async def downstream(event):
        handled.append(event)
        handled_contexts.append(get_resolved_access_context())

    adapter.handle_message = downstream

    reset_session_vars()
    assert get_resolved_access_context() is None
    expected_context = registry.resolve(
        TransportIdentity(
            platform="telegram",
            account=ACCOUNT,
            peer_kind="dm",
            user_id=CHAT_ID,
            chat_id=CHAT_ID,
        )
    )

    await adapter._handle_media_message(_photo_update(), SimpleNamespace())
    pending = tuple(adapter._pending_photo_batch_tasks.values())
    assert pending
    await asyncio.gather(*pending)

    assert len(handled) == 1
    assert handled[0].message_type == MessageType.PHOTO
    assert handled_contexts == [expected_context]
    cached_path = Path(handled[0].media_urls[0])
    assert cached_path.is_file()
    assert cached_path.is_relative_to(profile_home)
