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


def _tool_result(name: str, call_id: str, payload: dict) -> dict:
    return {
        "role": "tool",
        "name": name,
        "tool_name": name,
        "tool_call_id": call_id,
        "content": json.dumps(payload),
    }


def _successful_write_result(path: str) -> dict:
    return {
        "bytes_written": 16,
        "resolved_path": path,
        "files_modified": [path],
    }


def _successful_delivery_result(path: str) -> dict:
    return {
        "success": True,
        "status": "ready_for_delivery",
        "file_name": Path(path).name,
        "media_tag": f"MEDIA:{path}",
    }


async def _run_full_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    correction_succeeds: bool,
    document_succeeds: bool = True,
    no_space_v4a: bool = False,
    boundary_scenario: str | None = None,
    transcript_rewrite: str | None = None,
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
    preexisting = workspace / "preexisting-report.xls"
    ordinary = workspace / "ordinary-note.txt"

    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(gateway_run, "_hermes_home", home)
    monkeypatch.setattr(gateway_run, "_env_path", home / ".env")
    monkeypatch.setattr(run_agent, "_hermes_home", profile_home)
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
    monkeypatch.setattr(
        "agent.title_generator.maybe_auto_title", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr("agent.lsp.get_service", lambda: None)

    unsafe_final = _model_response(text=f"UNSAFE_SUCCESS\nMEDIA:{outside}")
    if boundary_scenario == "failed_then_unrelated":
        preexisting.write_text("preexisting", encoding="utf-8")
        responses = [
            _model_response(
                tool="write_file",
                args={"path": str(outside), "content": "rejected"},
                call_id="failed-write",
            ),
            _model_response(
                tool="deliver_artifact",
                args={"path": str(preexisting)},
                call_id="unrelated-delivery",
            ),
            unsafe_final,
            _model_response(
                tool="write_file",
                args={"path": str(outside), "content": "rejected-again"},
                call_id="failed-correction",
            ),
            unsafe_final,
        ]
    elif boundary_scenario == "successful_exact":
        responses = [
            _model_response(
                tool="write_file",
                args={"path": str(safe), "content": "synthetic-safe-xls"},
                call_id="exact-write",
            ),
            _model_response(
                tool="deliver_artifact",
                args={"path": str(safe)},
                call_id="exact-delivery",
            ),
            _model_response(text="DOCUMENT_SENT_AFTER_COMPRESSION"),
        ]
    elif boundary_scenario == "ordinary":
        ordinary.write_text("ordinary", encoding="utf-8")
        responses = [
            _model_response(
                tool="read_file",
                args={"path": str(ordinary)},
                call_id="ordinary-read",
            ),
            _model_response(text="PLAIN_AFTER_COMPRESSION"),
        ]
    elif boundary_scenario == "failed_after_repair":
        responses = [
            _model_response(
                tool="write_file",
                args={"path": str(outside), "content": "rejected"},
                call_id="repair-failed-write",
            ),
            unsafe_final,
            _model_response(
                tool="write_file",
                args={"path": str(outside), "content": "rejected-again"},
                call_id="repair-failed-correction",
            ),
            unsafe_final,
        ]
    elif correction_succeeds:
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
    if boundary_scenario is None:
        responses = [unsafe_mutation, unsafe_final, *followup]
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

    compression_calls: list[int] = []
    if transcript_rewrite:
        from agent import conversation_loop

        original_build_turn_context = conversation_loop.build_turn_context

        def _build_turn_context_with_long_prior_transcript(*args, **kwargs):
            context = original_build_turn_context(*args, **kwargs)
            if transcript_rewrite == "repair":
                prior = [
                    {"role": "assistant", "content": f"prior-{index}"}
                    for index in range(48)
                ]
            else:
                prior = [
                    {
                        "role": "user" if index % 2 == 0 else "assistant",
                        "content": f"prior-{index}",
                    }
                    for index in range(48)
                ]
            context.messages[:0] = prior
            context.current_turn_user_idx += len(prior)
            return context

        monkeypatch.setattr(
            conversation_loop,
            "build_turn_context",
            _build_turn_context_with_long_prior_transcript,
        )

        if transcript_rewrite == "compression":
            from agent.context_compressor import ContextCompressor

            should_compress_calls: dict[int, int] = {}

            def _compress_once_after_first_tool(self, _prompt_tokens=None):
                key = id(self)
                call = should_compress_calls.get(key, 0) + 1
                should_compress_calls[key] = call
                return call == 2

            def _compact_current_turn(
                agent,
                messages,
                _system_message,
                **_kwargs,
            ):
                compression_calls.append(len(messages))
                compacted = [dict(message) for message in messages[-3:]]
                agent._session_messages = compacted
                agent.context_compressor.last_prompt_tokens = -1
                return compacted, agent._cached_system_prompt

            monkeypatch.setattr(
                ContextCompressor,
                "should_compress",
                _compress_once_after_first_tool,
            )
            monkeypatch.setattr(
                run_agent.AIAgent,
                "_compress_context",
                _compact_current_turn,
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
    try:
        runner = gateway_run.GatewayRunner(config)
        adapter = TelegramAdapter(config.platforms[Platform.TELEGRAM])
        delivery_order: list[str] = []

        async def _send(*args, **kwargs):
            delivery_order.append(
                f"text:{kwargs.get('content', args[1] if len(args) > 1 else '')}"
            )
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
            try:
                await adapter.cancel_background_tasks()
            finally:
                try:
                    with runner._agent_cache_lock:
                        agents = [
                            entry[0] if isinstance(entry, tuple) else entry
                            for entry in runner._agent_cache.values()
                        ]
                        runner._agent_cache.clear()
                    for agent in agents:
                        if isinstance(agent, run_agent.AIAgent):
                            await runner._cleanup_agent_resources_off_loop(agent)
                finally:
                    try:
                        runner._shutdown_executor()
                    finally:
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
            preexisting=preexisting,
            ordinary=ordinary,
            compression_calls=compression_calls,
            event=event,
            runner=runner,
        )
    finally:
        set_multiplex_active(previous_multiplex_state)


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
        _require_native=True,
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
        _require_native=True,
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


@pytest.mark.asyncio
async def test_compression_keeps_failed_mutation_from_binding_unrelated_preexisting_delivery(
    monkeypatch,
    tmp_path,
):
    case = await _run_full_boundary(
        monkeypatch,
        tmp_path,
        correction_succeeds=False,
        boundary_scenario="failed_then_unrelated",
        transcript_rewrite="compression",
    )

    assert case.compression_calls
    assert case.provider.chat.completions.create.call_count == 5
    case.adapter.send_document.assert_not_awaited()
    assert "UNSAFE_SUCCESS" not in "\n".join(_visible_texts(case.adapter))
    assert case.preexisting.read_text(encoding="utf-8") == "preexisting"


@pytest.mark.asyncio
async def test_compression_keeps_exact_mutation_delivery_confirmation_exactly_once(
    monkeypatch,
    tmp_path,
):
    case = await _run_full_boundary(
        monkeypatch,
        tmp_path,
        correction_succeeds=True,
        boundary_scenario="successful_exact",
        transcript_rewrite="compression",
    )

    assert case.compression_calls
    assert case.provider.chat.completions.create.call_count == 3
    case.adapter.send_document.assert_awaited_once_with(
        chat_id=CHAT_ID,
        file_path=str(case.safe.resolve()),
        metadata={"thread_id": THREAD_ID, "notify": True},
        _require_native=True,
    )
    assert "DOCUMENT_SENT_AFTER_COMPRESSION" in "\n".join(
        _visible_texts(case.adapter)
    )


@pytest.mark.asyncio
async def test_non_document_final_text_is_unchanged_after_current_turn_compression(
    monkeypatch,
    tmp_path,
):
    case = await _run_full_boundary(
        monkeypatch,
        tmp_path,
        correction_succeeds=True,
        boundary_scenario="ordinary",
        transcript_rewrite="compression",
    )

    assert case.compression_calls
    assert case.provider.chat.completions.create.call_count == 2
    case.adapter.send_document.assert_not_awaited()
    assert _visible_texts(case.adapter) == ["PLAIN_AFTER_COMPRESSION"]


@pytest.mark.asyncio
async def test_repaired_stale_numeric_boundary_fails_closed_for_artifact_activity(
    monkeypatch,
    tmp_path,
):
    case = await _run_full_boundary(
        monkeypatch,
        tmp_path,
        correction_succeeds=False,
        boundary_scenario="failed_after_repair",
        transcript_rewrite="repair",
    )

    assert case.provider.chat.completions.create.call_count == 4
    case.adapter.send_document.assert_not_awaited()
    assert "UNSAFE_SUCCESS" not in "\n".join(_visible_texts(case.adapter))


@pytest.mark.asyncio
async def test_durable_begin_failure_prevents_document_tool_execution(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        hermes_state.SessionDB,
        "begin_artifact_delivery",
        lambda *_args, **_kwargs: False,
    )

    case = await _run_full_boundary(
        monkeypatch,
        tmp_path,
        correction_succeeds=True,
    )

    assert case.provider.chat.completions.create.call_count == 1
    case.adapter.send_document.assert_not_awaited()
    assert not case.outside.exists()
    assert not case.safe.exists()


@pytest.mark.asyncio
async def test_stop_scanner_exception_fails_closed_in_active_artifact_turn(
    monkeypatch,
    tmp_path,
):
    from agent import artifact_delivery_stop

    monkeypatch.setattr(
        artifact_delivery_stop,
        "bound_artifact_stop_action",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("scanner crash")),
    )

    case = await _run_full_boundary(
        monkeypatch,
        tmp_path,
        correction_succeeds=True,
    )

    assert case.provider.chat.completions.create.call_count == 2
    case.adapter.send_document.assert_not_awaited()
    assert "UNSAFE_SUCCESS" not in "\n".join(_visible_texts(case.adapter))


def test_failed_mutation_cannot_bind_unrelated_preexisting_delivery(monkeypatch):
    from agent import artifact_delivery_stop

    new_report = "/trusted/workspace/new-report.xlsx"
    preexisting = "/trusted/workspace/preexisting.xlsx"
    monkeypatch.setattr(
        artifact_delivery_stop, "bound_document_context_active", lambda: True
    )
    messages = [
        _tool_result("write_file", "failed-write", {"error": "synthetic failure"}),
        _tool_result(
            "deliver_artifact",
            "unrelated-delivery",
            _successful_delivery_result(preexisting),
        ),
    ]

    action, nudge, confirmation = artifact_delivery_stop.bound_artifact_stop_action(
        messages,
        attempts=0,
    )

    assert action == "continue"
    assert nudge is not None
    assert confirmation is None


def test_successful_mutation_of_a_cannot_bind_delivery_of_b(monkeypatch):
    from agent import artifact_delivery_stop

    report_a = "/trusted/workspace/report-a.xlsx"
    report_b = "/trusted/workspace/report-b.xlsx"
    monkeypatch.setattr(
        artifact_delivery_stop, "bound_document_context_active", lambda: True
    )
    messages = [
        _tool_result(
            "write_file", "write-a", _successful_write_result(report_a)
        ),
        _tool_result(
            "deliver_artifact",
            "deliver-b",
            _successful_delivery_result(report_b),
        ),
    ]

    action, nudge, confirmation = artifact_delivery_stop.bound_artifact_stop_action(
        messages,
        attempts=0,
    )

    assert action == "continue"
    assert nudge is not None
    assert confirmation is None


@pytest.mark.parametrize(
    "mutation_content",
    [
        json.dumps({"success": True}),
        json.dumps(
            {
                "success": True,
                "files_modified": ["/trusted/workspace/report.xlsx"],
                "resolved_path": "/trusted/workspace/different.xlsx",
            }
        ),
        "not-json",
    ],
)
def test_missing_malformed_or_ambiguous_mutation_result_fails_closed(
    monkeypatch,
    mutation_content,
):
    from agent import artifact_delivery_stop

    report = "/trusted/workspace/report.xlsx"
    monkeypatch.setattr(
        artifact_delivery_stop, "bound_document_context_active", lambda: True
    )
    mutation_result = _tool_result("patch", "patch-call", {})
    mutation_result["content"] = mutation_content
    messages = [
        mutation_result,
        _tool_result(
            "deliver_artifact",
            "deliver-call",
            _successful_delivery_result(report),
        ),
    ]

    action, nudge, confirmation = artifact_delivery_stop.bound_artifact_stop_action(
        messages,
        attempts=0,
    )

    assert action == "continue"
    assert nudge is not None
    assert confirmation is None


def test_outside_root_result_paths_fail_closed(monkeypatch):
    from agent import artifact_delivery_stop
    from tools import artifact_delivery_tool

    outside = "/outside/report.xlsx"
    monkeypatch.setattr(
        artifact_delivery_stop, "bound_document_context_active", lambda: True
    )
    monkeypatch.setattr(
        artifact_delivery_tool,
        "validate_bound_artifact_output",
        lambda path, _task_id: (
            "path_outside_bound_roots" if path == outside else None
        ),
    )
    messages = [
        _tool_result(
            "write_file", "write-call", _successful_write_result(outside)
        ),
        _tool_result(
            "deliver_artifact",
            "deliver-call",
            _successful_delivery_result(outside),
        ),
    ]

    action, nudge, confirmation = artifact_delivery_stop.bound_artifact_stop_action(
        messages,
        attempts=0,
    )

    assert action == "continue"
    assert nudge is not None
    assert confirmation is None


def test_delivery_before_later_successful_mutation_of_same_path_is_stale(monkeypatch):
    from agent import artifact_delivery_stop

    report = "/trusted/workspace/report.xlsx"
    monkeypatch.setattr(
        artifact_delivery_stop, "bound_document_context_active", lambda: True
    )
    messages = [
        _tool_result("write_file", "write-v1", _successful_write_result(report)),
        _tool_result(
            "deliver_artifact",
            "deliver-v1",
            _successful_delivery_result(report),
        ),
        _tool_result("write_file", "write-v2", _successful_write_result(report)),
    ]

    action, nudge, confirmation = artifact_delivery_stop.bound_artifact_stop_action(
        messages,
        attempts=0,
    )

    assert action == "continue"
    assert nudge is not None
    assert confirmation is None


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
        _tool_result(
            "patch",
            "patch-call",
            {
                "success": True,
                "files_modified": [document_path],
                "resolved_path": document_path,
            },
        ),
        _tool_result(
            "deliver_artifact",
            "document-call",
            _successful_delivery_result(document_path),
        ),
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


@pytest.mark.parametrize(
    ("mutation_outputs", "delivered_path"),
    [
        (
            ["/trusted/workspace/added.xlsx"],
            "/trusted/workspace/added.xlsx",
        ),
        (
            ["/trusted/workspace/updated.xlsx"],
            "/trusted/workspace/updated.xlsx",
        ),
        (
            [
                "/trusted/workspace/old.xlsx",
                "/trusted/workspace/moved.xlsx",
            ],
            "/trusted/workspace/moved.xlsx",
        ),
    ],
)
def test_patch_result_outputs_bind_only_exact_delivered_path(
    monkeypatch,
    mutation_outputs,
    delivered_path,
):
    from agent import artifact_delivery_stop

    unrelated = "/trusted/workspace/unrelated.xlsx"
    monkeypatch.setattr(
        artifact_delivery_stop, "bound_document_context_active", lambda: True
    )
    mutation_messages = [
        _tool_result(
            "patch",
            "patch-call",
            {"success": True, "files_modified": mutation_outputs},
        ),
    ]

    mismatch_action, mismatch_nudge, mismatch_confirmation = (
        artifact_delivery_stop.bound_artifact_stop_action(
            [
                *mutation_messages,
                _tool_result(
                    "deliver_artifact",
                    "wrong-delivery",
                    _successful_delivery_result(unrelated),
                ),
            ],
            attempts=0,
        )
    )
    assert mismatch_action == "continue"
    assert mismatch_nudge is not None
    assert mismatch_confirmation is None

    action, nudge, confirmation = artifact_delivery_stop.bound_artifact_stop_action(
        [
            *mutation_messages,
            _tool_result(
                "deliver_artifact",
                "exact-delivery",
                _successful_delivery_result(delivered_path),
            ),
        ],
        attempts=0,
    )
    assert action == "confirmed"
    assert nudge is None
    assert confirmation == {
        "tool_call_id": "exact-delivery",
        "path": delivered_path,
        "media_tag": f"MEDIA:{delivered_path}",
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
            outside_results = [
                json.loads(
                    patch_tool(
                        mode="patch",
                        patch=patch_body,
                        task_id="synthetic-patch-session",
                    )
                )
                for patch_body in (
                    (
                        "*** Begin Patch\n"
                        f"*** Add File: {outside}\n"
                        "+outside\n"
                        "*** End Patch"
                    ),
                    (
                        "*** Begin Patch\n"
                        f"*** Update File: {outside}\n"
                        "@@ synthetic @@\n"
                        "-old\n"
                        "+new\n"
                        "*** End Patch"
                    ),
                    (
                        "*** Begin Patch\n"
                        f"*** Move File: safe-relative.xlsx -> {outside}\n"
                        "*** End Patch"
                    ),
                )
            ]
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

    assert outside_results == [
        {"error": "bound_artifact_output_rejected: path_outside_bound_roots"},
        {"error": "bound_artifact_output_rejected: path_outside_bound_roots"},
        {"error": "bound_artifact_output_rejected: path_outside_bound_roots"},
    ]
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
