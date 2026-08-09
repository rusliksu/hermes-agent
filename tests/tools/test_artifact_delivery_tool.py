"""Boundary contract for explicit delivery of a bound outbound artifact."""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.access_registry import (
    AccessRegistry,
    DeliveryTarget,
    ParticipantIdentity,
    PrincipalBinding,
    ResolvedAccessContext,
    RolePolicy,
    SharedScopeBinding,
    TransportIdentity,
)
from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)
from gateway.run import GatewayRunner, _collect_auto_append_media_tags
from gateway.session import SessionSource
from gateway.session_context import bind_resolved_access_context
from model_tools import get_tool_definitions, handle_function_call
from plugins.platforms.telegram.adapter import TelegramAdapter


ACCOUNT = "synthetic-artifact-bot"
CHAT_ID = "42001"
PROFILE_ID = "artifact-family"
TOOL_NAME = "deliver_artifact"


def _boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    role_id: str = "family",
    capabilities: frozenset[str] = frozenset({"documents"}),
    thread_id: str | None = None,
):
    hermes_root = tmp_path / "hermes-root"
    profile_home = hermes_root / "profiles" / PROFILE_ID
    workspace = profile_home / "workspace"
    workspace.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(hermes_root))

    identity = TransportIdentity(
        platform="telegram",
        account=ACCOUNT,
        peer_kind="dm",
        user_id=CHAT_ID,
        chat_id=CHAT_ID,
        thread_id=thread_id,
    )
    registry = AccessRegistry(
        roles={role_id: RolePolicy(role_id, capabilities)},
        profiles=frozenset({PROFILE_ID}),
        principal_bindings=(
            PrincipalBinding(
                principal_id="synthetic-family-principal",
                role_id=role_id,
                profile_id=PROFILE_ID,
                transport_identity=identity,
                conversation_scope="private",
                delivery_target=DeliveryTarget(
                    platform="telegram",
                    account=ACCOUNT,
                    peer_kind="dm",
                    chat_id=CHAT_ID,
                    thread_id=thread_id,
                ),
            ),
        ),
        scope_capabilities={"private": capabilities},
        backend_capabilities=capabilities,
    )
    context = registry.resolve(identity)
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id=CHAT_ID,
        chat_type="dm",
        user_id=CHAT_ID,
        thread_id=thread_id,
        route_account=ACCOUNT,
        resolved_access_context=context,
    )
    event = MessageEvent(
        text="create the report",
        message_type=MessageType.TEXT,
        source=source,
        message_id="synthetic-inbound-message",
    )
    adapter = TelegramAdapter(
        PlatformConfig(
            enabled=True,
            token="synthetic-token",
            extra={"account": ACCOUNT},
        )
    )
    adapter.send_document = AsyncMock(
        return_value=SendResult(success=True, message_id="synthetic-outbound-message")
    )
    runner = object.__new__(GatewayRunner)
    runner.adapters = {Platform.TELEGRAM: adapter}
    return context, event, adapter, runner, workspace


def _dispatch_bound(runner, event, context, args: dict) -> tuple[str, dict]:
    session = SimpleNamespace(source=event.source, session_key="artifact-session")
    with bind_resolved_access_context(context):
        tokens = runner._set_session_env(session)
        try:
            raw = handle_function_call(
                TOOL_NAME,
                args,
                task_id="artifact-session",
                enabled_toolsets=["file"],
            )
        finally:
            runner._clear_session_env(tokens)
    return raw, json.loads(raw)


def _tool_messages(raw_result: str) -> list[dict]:
    return [
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "artifact-call", "function": {"name": TOOL_NAME}}
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "artifact-call",
            "content": raw_result,
        },
    ]


def test_registered_file_tool_schema_has_path_only():
    definitions = get_tool_definitions(enabled_toolsets=["file"], quiet_mode=True)
    schema = next(
        item["function"] for item in definitions if item["function"]["name"] == TOOL_NAME
    )

    assert schema["parameters"]["required"] == ["path"]
    assert set(schema["parameters"]["properties"]) == {"path"}
    assert schema["parameters"]["additionalProperties"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("thread_id", [None, "73"])
async def test_bound_xlsx_auto_appends_and_delivers_once_to_current_telegram_target(
    monkeypatch,
    tmp_path,
    thread_id,
):
    context, event, adapter, runner, workspace = _boundary(
        monkeypatch,
        tmp_path,
        thread_id=thread_id,
    )
    artifact = workspace / "synthetic-report.xlsx"
    artifact.write_bytes(b"synthetic-xlsx")

    raw, result = _dispatch_bound(runner, event, context, {"path": str(artifact)})

    assert result == {
        "success": True,
        "status": "ready_for_delivery",
        "file_name": artifact.name,
        "media_tag": f"MEDIA:{artifact.resolve()}",
    }
    adapter.send_document.assert_not_awaited()

    tags, voice = _collect_auto_append_media_tags(_tool_messages(raw))
    assert tags == [f"MEDIA:{artifact.resolve()}"]
    assert voice is False

    with bind_resolved_access_context(context):
        await runner._deliver_media_from_response(tags[0], event, adapter)

    expected_metadata = None
    if thread_id is not None:
        expected_metadata = {
            "thread_id": thread_id,
            "telegram_dm_topic_reply_fallback": True,
            "direct_messages_topic_id": thread_id,
            "telegram_reply_to_message_id": "synthetic-inbound-message",
        }
    adapter.send_document.assert_awaited_once_with(
        chat_id=CHAT_ID,
        file_path=str(artifact.resolve()),
        metadata=expected_metadata,
    )


@pytest.mark.asyncio
async def test_allowed_shared_room_topic_delivers_only_to_current_telegram_scope(
    monkeypatch,
    tmp_path,
):
    group_chat_id = "-10042001"
    group_thread_id = "731"
    member_id = "shared-member"
    capabilities = frozenset({"documents"})
    hermes_root = tmp_path / "hermes-root"
    profile_home = hermes_root / "profiles" / PROFILE_ID
    workspace = profile_home / "workspace"
    workspace.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(hermes_root))

    room_identity = TransportIdentity(
        platform="telegram",
        account=ACCOUNT,
        peer_kind="group",
        user_id="ignored-member",
        chat_id=group_chat_id,
        thread_id=group_thread_id,
    )
    current_identity = replace(room_identity, user_id=member_id)
    delivery_target = DeliveryTarget(
        platform="telegram",
        account=ACCOUNT,
        peer_kind="group",
        chat_id=group_chat_id,
        thread_id=group_thread_id,
    )
    registry = AccessRegistry(
        roles={"shared_room": RolePolicy("shared_room", capabilities)},
        profiles=frozenset({PROFILE_ID}),
        shared_scope_bindings=(
            SharedScopeBinding(
                principal_id="synthetic-shared-room",
                role_id="shared_room",
                profile_id=PROFILE_ID,
                room_identity=room_identity,
                conversation_scope="shared-artifacts",
                delivery_target=delivery_target,
                participant_identities=(
                    ParticipantIdentity("telegram", ACCOUNT, member_id),
                ),
            ),
        ),
        scope_capabilities={"shared-artifacts": capabilities},
        backend_capabilities=capabilities,
    )
    context = registry.resolve(current_identity)
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id=group_chat_id,
        chat_type="group",
        user_id=member_id,
        thread_id=group_thread_id,
        route_account=ACCOUNT,
        resolved_access_context=context,
    )
    event = MessageEvent(
        text="create the shared report",
        message_type=MessageType.TEXT,
        source=source,
        message_id="synthetic-shared-inbound",
    )
    adapter = TelegramAdapter(
        PlatformConfig(
            enabled=True,
            token="synthetic-token",
            extra={"account": ACCOUNT},
        )
    )
    adapter.send_document = AsyncMock(
        return_value=SendResult(success=True, message_id="synthetic-shared-outbound")
    )
    runner = object.__new__(GatewayRunner)
    runner.adapters = {Platform.TELEGRAM: adapter}
    artifact = workspace / "shared-report.xlsx"
    artifact.write_bytes(b"synthetic-shared-xlsx")

    raw, result = _dispatch_bound(runner, event, context, {"path": str(artifact)})

    assert result["success"] is True
    tags, voice = _collect_auto_append_media_tags(_tool_messages(raw))
    assert tags == [f"MEDIA:{artifact.resolve()}"]
    assert voice is False
    await runner._deliver_media_from_response(tags[0], event, adapter)
    adapter.send_document.assert_awaited_once_with(
        chat_id=group_chat_id,
        file_path=str(artifact.resolve()),
        metadata={"thread_id": group_thread_id},
    )

    adapter.send_document.reset_mock()
    foreign_context = replace(
        context,
        delivery_target=replace(context.delivery_target, thread_id="foreign-topic"),
    )
    foreign_raw, foreign_result = _dispatch_bound(
        runner,
        event,
        foreign_context,
        {"path": str(artifact)},
    )

    assert foreign_result == {
        "success": False,
        "status": "failed",
        "error": "context_target_mismatch",
    }
    assert _collect_auto_append_media_tags(_tool_messages(foreign_raw))[0] == []
    await runner._deliver_media_from_response(foreign_raw, event, adapter)
    adapter.send_document.assert_not_awaited()


@pytest.mark.parametrize("file_name", ["report.docx", "report.pdf", "report.csv", "report.zip"])
def test_sibling_document_and_archive_types_publish_trusted_tags(
    monkeypatch,
    tmp_path,
    file_name,
):
    context, event, adapter, runner, workspace = _boundary(monkeypatch, tmp_path)
    artifact = workspace / file_name
    artifact.write_bytes(b"synthetic-artifact")

    raw, result = _dispatch_bound(runner, event, context, {"path": str(artifact)})

    assert result["success"] is True
    assert result["media_tag"] == f"MEDIA:{artifact.resolve()}"
    assert _collect_auto_append_media_tags(_tool_messages(raw))[0] == [
        f"MEDIA:{artifact.resolve()}"
    ]
    adapter.send_document.assert_not_awaited()


@pytest.mark.parametrize("file_name", ["photo.png", "voice.mp3", "video.mp4"])
def test_existing_image_audio_and_video_classes_are_not_rerouted(
    monkeypatch,
    tmp_path,
    file_name,
):
    context, event, adapter, runner, workspace = _boundary(monkeypatch, tmp_path)
    artifact = workspace / file_name
    artifact.write_bytes(b"synthetic-media")

    raw, result = _dispatch_bound(runner, event, context, {"path": str(artifact)})

    assert result == {
        "success": False,
        "status": "failed",
        "error": "media_type_not_supported",
    }
    assert _collect_auto_append_media_tags(_tool_messages(raw))[0] == []
    adapter.send_document.assert_not_awaited()


@pytest.mark.parametrize(
    ("mutate_context", "artifact_kind", "expected_error"),
    [
        (lambda context: None, "present", "missing_context"),
        (lambda context: object(), "present", "malformed_context"),
        (
            lambda context: replace(
                context,
                delivery_target=replace(context.delivery_target, chat_id="foreign-chat"),
            ),
            "present",
            "context_target_mismatch",
        ),
        (lambda context: context, "missing", "file_not_found"),
        (lambda context: context, "outside", "path_outside_bound_roots"),
        (lambda context: context, "symlink_escape", "path_outside_bound_roots"),
    ],
)
def test_invalid_context_or_path_fails_without_delivery_tag(
    monkeypatch,
    tmp_path,
    mutate_context,
    artifact_kind,
    expected_error,
):
    context, event, adapter, runner, workspace = _boundary(monkeypatch, tmp_path)
    if artifact_kind == "present":
        artifact = workspace / "report.pdf"
        artifact.write_bytes(b"synthetic-pdf")
    elif artifact_kind == "missing":
        artifact = workspace / "missing.pdf"
    elif artifact_kind == "outside":
        artifact = tmp_path / "outside.zip"
        artifact.write_bytes(b"synthetic-zip")
    else:
        outside = tmp_path / "foreign.docx"
        outside.write_bytes(b"synthetic-docx")
        artifact = workspace / "escaped.docx"
        artifact.symlink_to(outside)

    raw, result = _dispatch_bound(
        runner,
        event,
        mutate_context(context),
        {"path": str(artifact)},
    )

    assert result == {
        "success": False,
        "status": "failed",
        "error": expected_error,
    }
    assert _collect_auto_append_media_tags(_tool_messages(raw))[0] == []
    adapter.send_document.assert_not_awaited()


def test_model_supplied_target_is_rejected_without_delivery_tag(monkeypatch, tmp_path):
    context, event, adapter, runner, workspace = _boundary(monkeypatch, tmp_path)
    artifact = workspace / "report.csv"
    artifact.write_text("a,b\n1,2\n", encoding="utf-8")

    raw, result = _dispatch_bound(
        runner,
        event,
        context,
        {"path": str(artifact), "chat_id": "foreign-chat"},
    )

    assert result == {
        "success": False,
        "status": "failed",
        "error": "invalid_arguments",
    }
    assert _collect_auto_append_media_tags(_tool_messages(raw))[0] == []
    adapter.send_document.assert_not_awaited()


@pytest.mark.parametrize("role_id", ["family", "shared_room"])
def test_family_and_shared_require_documents_capability(
    monkeypatch,
    tmp_path,
    role_id,
):
    context, event, adapter, runner, workspace = _boundary(
        monkeypatch,
        tmp_path,
        role_id=role_id,
        capabilities=frozenset({"attachments"}),
    )
    artifact = workspace / "report.xlsx"
    artifact.write_bytes(b"synthetic-xlsx")

    raw, result = _dispatch_bound(runner, event, context, {"path": str(artifact)})

    assert result == {
        "success": False,
        "status": "failed",
        "error": "missing_documents_capability",
    }
    assert _collect_auto_append_media_tags(_tool_messages(raw))[0] == []
    adapter.send_document.assert_not_awaited()


def test_family_documents_does_not_require_attachments_capability(monkeypatch, tmp_path):
    context, event, adapter, runner, workspace = _boundary(monkeypatch, tmp_path)
    artifact = workspace / "report.docx"
    artifact.write_bytes(b"synthetic-docx")

    _, result = _dispatch_bound(runner, event, context, {"path": str(artifact)})

    assert result["success"] is True
    assert result["media_tag"] == f"MEDIA:{artifact.resolve()}"
    adapter.send_document.assert_not_awaited()


def test_owner_keeps_full_file_tool_access_and_bound_workspace_root(monkeypatch, tmp_path):
    hermes_home = tmp_path / "owner-hermes"
    workspace = tmp_path / "owner-workspace"
    hermes_home.mkdir()
    workspace.mkdir()
    (hermes_home / "config.yaml").write_text(
        json.dumps({"terminal": {"cwd": str(workspace)}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    context = ResolvedAccessContext(
        principal_id="owner-principal",
        role_id="owner",
        profile_id="default",
        conversation_scope="private",
        capabilities=frozenset(),
        delivery_target=DeliveryTarget(
            platform="telegram",
            account=ACCOUNT,
            peer_kind="dm",
            chat_id=CHAT_ID,
        ),
    )
    event = MessageEvent(
        text="create archive",
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id=CHAT_ID,
            chat_type="dm",
            user_id=CHAT_ID,
            route_account=ACCOUNT,
            resolved_access_context=context,
        ),
    )
    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    artifact = workspace / "owner-export.zip"
    artifact.write_bytes(b"synthetic-zip")

    _, result = _dispatch_bound(runner, event, context, {"path": str(artifact)})

    assert result["success"] is True
    assert result["media_tag"] == f"MEDIA:{artifact.resolve()}"
    monkeypatch.setattr("agent.secret_scope.is_multiplex_active", lambda: True)
    with bind_resolved_access_context(context):
        assert BasePlatformAdapter.filter_media_delivery_paths(
            [(str(artifact), False)]
        ) == [(str(artifact.resolve()), False)]
    assert GatewayRunner._toolsets_for_resolved_access_context(["file"], context) == [
        "file"
    ]
