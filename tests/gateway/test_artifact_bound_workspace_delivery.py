"""Full boundary for bound creation and confirmed document delivery."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import gateway.run as gateway_run
import hermes_state
import run_agent
from gateway.access_registry import (
    AccessRegistry,
    DeliveryTarget,
    ParticipantIdentity,
    RolePolicy,
    SharedScopeBinding,
    TransportIdentity,
)
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, SendResult
from gateway.session import SessionSource
from gateway.session_context import bind_resolved_access_context
from gateway.single_principal import SinglePrincipalPolicy
from model_tools import handle_function_call
from plugins.platforms.telegram.adapter import TelegramAdapter
from tools.file_tools import patch_tool, write_file_tool


ACCOUNT = "artifact-boundary-bot"
CHAT_ID = "-10042008"
THREAD_ID = "808"
USER_ID = "synthetic-member"
PROFILE_ID = "synthetic-room-profile"


def _registry() -> AccessRegistry:
    capabilities = frozenset({"documents"})
    room_identity = TransportIdentity(
        platform="telegram",
        account=ACCOUNT,
        peer_kind="group",
        user_id="ignored-member",
        chat_id=CHAT_ID,
        thread_id=THREAD_ID,
    )
    target = DeliveryTarget(
        platform="telegram",
        account=ACCOUNT,
        peer_kind="group",
        chat_id=CHAT_ID,
        thread_id=THREAD_ID,
    )
    return AccessRegistry(
        roles={"shared_room": RolePolicy("shared_room", capabilities)},
        profiles=frozenset({PROFILE_ID}),
        shared_scope_bindings=(
            SharedScopeBinding(
                principal_id="synthetic-room-principal",
                role_id="shared_room",
                profile_id=PROFILE_ID,
                room_identity=room_identity,
                conversation_scope="shared-documents",
                delivery_target=target,
                participant_identities=(
                    ParticipantIdentity("telegram", ACCOUNT, USER_ID),
                ),
            ),
        ),
        scope_capabilities={"shared-documents": capabilities},
        backend_capabilities=capabilities,
    )


def _model_response(*, text: str | None = None, tool: str | None = None, args=None, call_id="call"):
    tool_calls = None
    finish_reason = "stop"
    if tool is not None:
        tool_calls = [
            SimpleNamespace(
                id=call_id,
                function=SimpleNamespace(
                    name=tool,
                    arguments=json.dumps(args or {}),
                ),
            )
        ]
        finish_reason = "tool_calls"
    message = SimpleNamespace(
        content=text,
        tool_calls=tool_calls,
        reasoning=None,
        reasoning_content=None,
        reasoning_details=None,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        model="synthetic-model",
        usage=None,
    )


async def _wait_for_turn(adapter: TelegramAdapter) -> None:
    tasks = tuple(adapter._session_tasks.values())
    assert tasks
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=20)


def _visible_texts(adapter: TelegramAdapter) -> list[str]:
    return [
        str(call.kwargs.get("content") if "content" in call.kwargs else call.args[1])
        for call in adapter.send.await_args_list
    ]


async def _run_full_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    correction_succeeds: bool,
    document_succeeds: bool = True,
    no_space_v4a: bool = False,
):
    from agent.secret_scope import is_multiplex_active, set_multiplex_active

    previous_multiplex_state = is_multiplex_active()
    home = tmp_path / "hermes-home"
    profile_home = home / "profiles" / PROFILE_ID
    workspace = profile_home / "workspace"
    for relative in ("sessions", "memories", "logs", "workspace", "home"):
        (profile_home / relative).mkdir(parents=True, exist_ok=True)
    (profile_home / "config.yaml").write_text(
        json.dumps({"terminal": {"cwd": str(workspace)}}),
        encoding="utf-8",
    )
    outside = tmp_path / "outside-report.xls"
    safe = workspace / "safe-report.xls"

    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(gateway_run, "_hermes_home", home)
    monkeypatch.setattr(gateway_run, "_env_path", home / ".env")
    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", home / "state.db")
    monkeypatch.setattr(gateway_run, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        gateway_run,
        "_load_gateway_config",
        lambda: {
            "platform_toolsets": {"telegram": ["file"]},
            "display": {
                "tool_progress": "off",
                "thinking_progress": False,
                "streaming": False,
            },
            "memory": {},
            "agent": {"max_iterations": 8},
        },
    )
    monkeypatch.setattr(gateway_run, "_load_gateway_runtime_config", lambda: {})
    monkeypatch.setattr(gateway_run, "_resolve_gateway_model", lambda config=None: "synthetic-model")
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs",
        lambda: {
            "provider": "openrouter",
            "api_mode": "chat_completions",
            "base_url": "https://example.invalid/v1",
            "api_key": "synthetic-key-1234567890",
        },
    )
    monkeypatch.setattr(
        "agent.model_metadata.get_model_context_length",
        lambda *_args, **_kwargs: 100_000,
    )

    unsafe_final = _model_response(
        text=f"UNSAFE_SUCCESS\nMEDIA:{outside}",
    )
    if correction_succeeds:
        followup = [
            _model_response(
                tool="write_file",
                args={"path": str(safe), "content": "synthetic-safe-xls"},
                call_id="safe-write",
            ),
            _model_response(
                tool="deliver_artifact",
                args={"path": str(safe)},
                call_id="safe-deliver",
            ),
            _model_response(text=f"DOCUMENT_SENT_OK {safe.resolve()}"),
        ]
    else:
        followup = [
            _model_response(
                tool="write_file",
                args={"path": str(outside), "content": "second-unsafe-xls"},
                call_id="second-unsafe-write",
            ),
            unsafe_final,
        ]
    unsafe_mutation = (
        _model_response(
            tool="patch",
            args={
                "mode": "patch",
                "patch": (
                    "*** Begin Patch\n"
                    f"***Add File: {outside}\n"
                    "+first-unsafe-xls\n"
                    "*** End Patch"
                ),
            },
            call_id="first-unsafe-write",
        )
        if no_space_v4a
        else _model_response(
            tool="write_file",
            args={"path": str(outside), "content": "first-unsafe-xls"},
            call_id="first-unsafe-write",
        )
    )
    responses = [
        unsafe_mutation,
        unsafe_final,
        *followup,
    ]
    provider_client = MagicMock()
    provider_client.chat.completions.create.side_effect = responses
    monkeypatch.setattr(run_agent, "OpenAI", lambda *args, **kwargs: provider_client)
    monkeypatch.setattr(
        run_agent.AIAgent,
        "_create_request_openai_client",
        lambda self, **kwargs: provider_client,
    )
    monkeypatch.setattr(
        run_agent.AIAgent,
        "_close_request_openai_client",
        lambda self, client, **kwargs: None,
    )

    policy = SinglePrincipalPolicy.from_dict(
        {
            "enabled": True,
            "telegram_owner_id": "90001",
            "telegram_shared_chat_ids": [CHAT_ID],
            "allow_owner_bound_relay": False,
        }
    )
    config = GatewayConfig(
        sessions_dir=home / "sessions",
        multiplex_profiles=True,
        single_principal=policy,
        access_registry=_registry(),
        write_sessions_json=False,
        platforms={
            Platform.TELEGRAM: PlatformConfig(enabled=True, token="synthetic-token")
        },
    )
    runner = gateway_run.GatewayRunner(config)
    adapter = TelegramAdapter(config.platforms[Platform.TELEGRAM])
    delivery_order: list[str] = []

    async def _send(*args, **kwargs):
        delivery_order.append(f"text:{kwargs.get('content', args[1] if len(args) > 1 else '')}")
        return SendResult(success=True, message_id="synthetic-text")

    async def _send_document(*args, **kwargs):
        delivery_order.append(f"document:{kwargs.get('file_path', '')}")
        return SendResult(
            success=document_succeeds,
            message_id="synthetic-document" if document_succeeds else None,
            error=None if document_succeeds else "synthetic document failure",
        )

    adapter.send = AsyncMock(side_effect=_send)
    adapter.send_document = AsyncMock(side_effect=_send_document)
    if correction_succeeds:
        monkeypatch.setattr(
            adapter,
            "filter_local_delivery_paths",
            lambda paths: list(paths),
        )
    adapter.send_typing = AsyncMock()
    adapter.stop_typing = AsyncMock()
    adapter.set_message_handler(runner._handle_message)
    runner.adapters[Platform.TELEGRAM] = adapter
    event = MessageEvent(
        text="Создай и отправь общую таблицу",
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id=CHAT_ID,
            chat_type="group",
            user_id=USER_ID,
            thread_id=THREAD_ID,
            route_account=ACCOUNT,
        ),
        message_id="synthetic-inbound",
    )

    try:
        await adapter.handle_message(event)
        await _wait_for_turn(adapter)
    finally:
        set_multiplex_active(previous_multiplex_state)

    if runner._session_db is not None:
        runner._session_db._db.close()
    if runner.session_store._db is not None:
        runner.session_store._db.close()
    return SimpleNamespace(
        adapter=adapter,
        provider=provider_client,
        delivery_order=delivery_order,
        outside=outside,
        safe=safe,
        event=event,
        runner=runner,
    )


@pytest.mark.asyncio
async def test_outside_generated_xls_gets_one_safe_correction_and_confirmed_topic_delivery(
    monkeypatch,
    tmp_path,
):
    case = await _run_full_boundary(
        monkeypatch,
        tmp_path,
        correction_succeeds=True,
    )

    assert case.provider.chat.completions.create.call_count == 5
    assert not case.outside.exists()
    assert case.safe.read_text(encoding="utf-8") == "synthetic-safe-xls"
    case.adapter.send_document.assert_awaited_once_with(
        chat_id=CHAT_ID,
        file_path=str(case.safe.resolve()),
        metadata={"thread_id": THREAD_ID, "notify": True},
    )
    texts = _visible_texts(case.adapter)
    assert "UNSAFE_SUCCESS" not in "\n".join(texts)
    assert any("DOCUMENT_SENT_OK" in text for text in texts)
    document_index = case.delivery_order.index(f"document:{case.safe.resolve()}")
    success_index = next(
        index
        for index, delivery in enumerate(case.delivery_order)
        if delivery.startswith("text:DOCUMENT_SENT_OK")
    )
    assert document_index < success_index


@pytest.mark.asyncio
async def test_no_space_v4a_outside_add_gets_one_safe_correction(
    monkeypatch,
    tmp_path,
):
    case = await _run_full_boundary(
        monkeypatch,
        tmp_path,
        correction_succeeds=True,
        no_space_v4a=True,
    )

    assert case.provider.chat.completions.create.call_count == 5
    assert not case.outside.exists()
    assert case.safe.read_text(encoding="utf-8") == "synthetic-safe-xls"
    texts = _visible_texts(case.adapter)
    assert "UNSAFE_SUCCESS" not in "\n".join(texts)
    case.adapter.send_document.assert_awaited_once_with(
        chat_id=CHAT_ID,
        file_path=str(case.safe.resolve()),
        metadata={"thread_id": THREAD_ID, "notify": True},
    )


@pytest.mark.asyncio
async def test_second_unsafe_generation_fails_without_loop_or_success_claim(
    monkeypatch,
    tmp_path,
):
    case = await _run_full_boundary(
        monkeypatch,
        tmp_path,
        correction_succeeds=False,
    )

    assert case.provider.chat.completions.create.call_count == 4
    case.adapter.send_document.assert_not_awaited()
    assert "UNSAFE_SUCCESS" not in "\n".join(_visible_texts(case.adapter))
    assert not case.outside.exists()
    assert not case.safe.exists()


@pytest.mark.asyncio
async def test_success_claim_is_suppressed_when_current_topic_document_send_fails(
    monkeypatch,
    tmp_path,
):
    case = await _run_full_boundary(
        monkeypatch,
        tmp_path,
        correction_succeeds=True,
        document_succeeds=False,
    )

    case.adapter.send_document.assert_awaited_once()
    assert "DOCUMENT_SENT_OK" not in "\n".join(_visible_texts(case.adapter))


def test_exact_deliver_artifact_result_wins_over_earlier_image_and_tts_tags(
    monkeypatch,
):
    from agent import artifact_delivery_stop

    document_path = "/trusted/workspace/report.xlsx"
    monkeypatch.setattr(
        artifact_delivery_stop, "bound_document_context_active", lambda: True
    )
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "image-call",
                    "function": {"name": "image_generate", "arguments": "{}"},
                },
                {
                    "id": "tts-call",
                    "function": {"name": "text_to_speech", "arguments": "{}"},
                },
                {
                    "id": "patch-call",
                    "function": {
                        "name": "patch",
                        "arguments": json.dumps(
                            {
                                "mode": "patch",
                                "patch": (
                                    "*** Begin Patch\n"
                                    "*** Add File: report.xlsx\n"
                                    "+synthetic report\n"
                                    "*** End Patch"
                                ),
                            }
                        ),
                    },
                },
                {
                    "id": "document-call",
                    "function": {
                        "name": "deliver_artifact",
                        "arguments": json.dumps({"path": document_path}),
                    },
                },
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "image-call",
            "content": json.dumps(
                {"success": True, "image": "/trusted/workspace/earlier.png"}
            ),
        },
        {
            "role": "tool",
            "tool_call_id": "tts-call",
            "content": "MEDIA:/trusted/workspace/earlier.mp3 [[audio_as_voice]]",
        },
        {
            "role": "tool",
            "tool_call_id": "document-call",
            "content": json.dumps(
                {
                    "success": True,
                    "status": "ready_for_delivery",
                    "media_tag": f"MEDIA:{document_path}",
                }
            ),
        },
    ]

    action, nudge, confirmation = artifact_delivery_stop.bound_artifact_stop_action(
        messages,
        attempts=0,
    )

    assert action == "confirmed"
    assert nudge is None
    assert confirmation == {
        "tool_call_id": "document-call",
        "path": document_path,
        "media_tag": f"MEDIA:{document_path}",
    }


def test_streaming_document_tool_call_buffers_claim_and_plain_text_flushes():
    emitted: list[str | None] = []
    stream = gateway_run._BoundArtifactStreamBuffer(enabled=True)
    stream.on_delta("PREMATURE_SUCCESS", emitted.append)
    stream.on_delta(None, emitted.append)
    stream.on_tool_start(
        "patch",
        {
            "mode": "patch",
            "patch": (
                "*** Begin Patch\n"
                "*** Add File: report.xlsx\n"
                "+synthetic report\n"
                "*** End Patch"
            ),
        },
    )
    stream.resolve(
        {
            "tool_call_id": "document-call",
            "path": "/trusted/workspace/report.xlsx",
            "media_tag": "MEDIA:/trusted/workspace/report.xlsx",
        },
        emitted.append,
    )

    assert emitted == []

    plain_emitted: list[str | None] = []
    plain_stream = gateway_run._BoundArtifactStreamBuffer(enabled=True)
    plain_stream.on_delta("PLAIN_TEXT_ONLY", plain_emitted.append)
    plain_stream.on_delta(None, plain_emitted.append)
    plain_stream.resolve(None, plain_emitted.append)

    assert plain_emitted == ["PLAIN_TEXT_ONLY", None]


def test_bound_patch_denies_outside_add_and_allows_safe_relative_document(
    monkeypatch,
    tmp_path,
):
    registry = _registry()
    context = registry.resolve(
        TransportIdentity(
            platform="telegram",
            account=ACCOUNT,
            peer_kind="group",
            user_id=USER_ID,
            chat_id=CHAT_ID,
            thread_id=THREAD_ID,
        )
    )
    profile_home = tmp_path / "hermes" / "profiles" / PROFILE_ID
    workspace = profile_home / "workspace"
    workspace.mkdir(parents=True)
    (profile_home / "config.yaml").write_text(
        json.dumps({"terminal": {"cwd": str(workspace)}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(profile_home))
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id=CHAT_ID,
        chat_type="group",
        user_id=USER_ID,
        thread_id=THREAD_ID,
        route_account=ACCOUNT,
        resolved_access_context=context,
    )
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.adapters = {}
    session = SimpleNamespace(source=source, session_key="synthetic-patch-session")
    outside = tmp_path / "outside-patch.xlsx"
    file_ops = MagicMock()
    patch_result = MagicMock()
    patch_result.to_dict.return_value = {"status": "ok", "operations": 1}
    file_ops.patch_v4a.return_value = patch_result
    monkeypatch.setattr("tools.file_tools._get_file_ops", lambda task_id: file_ops)

    with bind_resolved_access_context(context):
        tokens = runner._set_session_env(session)
        try:
            outside_result = json.loads(
                patch_tool(
                    mode="patch",
                    patch=(
                        "*** Begin Patch\n"
                        f"*** Add File: {outside}\n"
                        "+outside\n"
                        "*** End Patch"
                    ),
                    task_id="synthetic-patch-session",
                )
            )
            safe_result = json.loads(
                patch_tool(
                    mode="patch",
                    patch=(
                        "*** Begin Patch\n"
                        "***Add File: safe-relative.xlsx\n"
                        "+inside\n"
                        "*** End Patch"
                    ),
                    task_id="synthetic-patch-session",
                )
            )
        finally:
            runner._clear_session_env(tokens)

    assert outside_result == {
        "error": "bound_artifact_output_rejected: path_outside_bound_roots"
    }
    assert not outside.exists()
    assert safe_result.get("error") is None
    file_ops.patch_v4a.assert_called_once_with(
        "*** Begin Patch\n"
        "***Add File: safe-relative.xlsx\n"
        "+inside\n"
        "*** End Patch"
    )


def test_foreign_context_and_symlink_escape_remain_fail_closed(monkeypatch, tmp_path):
    registry = _registry()
    identity = TransportIdentity(
        platform="telegram",
        account=ACCOUNT,
        peer_kind="group",
        user_id=USER_ID,
        chat_id=CHAT_ID,
        thread_id=THREAD_ID,
    )
    context = registry.resolve(identity)
    profile_home = tmp_path / "hermes" / "profiles" / PROFILE_ID
    workspace = profile_home / "workspace"
    workspace.mkdir(parents=True)
    (profile_home / "config.yaml").write_text(
        json.dumps({"terminal": {"cwd": str(workspace)}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(profile_home))
    outside = tmp_path / "foreign.xls"
    outside.write_text("foreign", encoding="utf-8")
    owner_outside = tmp_path / "owner.xls"
    unrelated_outside = tmp_path / "shared-helper.py"
    escaped = workspace / "escaped.xls"
    escaped.symlink_to(outside)
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id=CHAT_ID,
        chat_type="group",
        user_id=USER_ID,
        thread_id=THREAD_ID,
        route_account=ACCOUNT,
        resolved_access_context=context,
    )
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.adapters = {}
    session = SimpleNamespace(source=source, session_key="synthetic-session")

    with bind_resolved_access_context(context):
        tokens = runner._set_session_env(session)
        try:
            symlink_result = json.loads(
                handle_function_call(
                    "deliver_artifact",
                    {"path": str(escaped)},
                    task_id="synthetic-session",
                    enabled_toolsets=["file"],
                )
            )
            symlink_write = json.loads(
                write_file_tool(
                    str(escaped),
                    "must-not-overwrite",
                    task_id="synthetic-session",
                )
            )
            unrelated_write = json.loads(
                write_file_tool(
                    str(unrelated_outside),
                    "synthetic-helper",
                    task_id="synthetic-session",
                )
            )
            foreign_context = replace(
                context,
                delivery_target=replace(context.delivery_target, thread_id="foreign-topic"),
            )
            with bind_resolved_access_context(foreign_context):
                foreign_result = json.loads(
                    handle_function_call(
                        "deliver_artifact",
                        {"path": str(outside)},
                        task_id="synthetic-session",
                        enabled_toolsets=["file"],
                    )
                )
                foreign_write = json.loads(
                    write_file_tool(
                        str(tmp_path / "foreign-target.xls"),
                        "must-not-write",
                        task_id="synthetic-session",
                    )
                )
            owner_context = replace(
                context,
                principal_id="synthetic-owner-principal",
                role_id="owner",
            )
            with bind_resolved_access_context(owner_context):
                owner_write = json.loads(
                    write_file_tool(
                        str(owner_outside),
                        "owner-capability-preserved",
                        task_id="synthetic-session",
                    )
                )
        finally:
            runner._clear_session_env(tokens)

    assert symlink_result == {
        "success": False,
        "status": "failed",
        "error": "path_outside_bound_roots",
    }
    assert foreign_result == {
        "success": False,
        "status": "failed",
        "error": "context_target_mismatch",
    }
    assert symlink_write == {
        "error": "bound_artifact_output_rejected: path_outside_bound_roots"
    }
    assert outside.read_text(encoding="utf-8") == "foreign"
    assert unrelated_write.get("error") is None
    assert unrelated_outside.read_text(encoding="utf-8") == "synthetic-helper"
    assert foreign_write == {
        "error": "bound_artifact_output_rejected: context_target_mismatch"
    }
    assert owner_write.get("error") is None
    assert owner_outside.read_text(encoding="utf-8") == "owner-capability-preserved"
    assert "MEDIA:" not in json.dumps([symlink_result, foreign_result])
