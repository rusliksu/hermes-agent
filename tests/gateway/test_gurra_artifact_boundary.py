"""Real ingress/agent/tool/store/upload handoff; only model and Telegram are external seams."""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from openai.types.chat import ChatCompletionMessageToolCall

import gateway.run as gateway_run
import hermes_state
import run_agent
from gateway.access_registry import AccessRegistry, RolePolicy, TransportIdentity
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, SendResult
from plugins.platforms.telegram.adapter import TelegramAdapter
from tests.gateway.test_shared_topic_full_boundary import (
    ACCOUNT, CHAT_ID, PROFILE_ID, USER_ID, _provider_response, _registry, _wait_for_delivery,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("upload", ["ack", "missing_receipt", "failure", "native_ack", "native_failure"])
async def test_existing_document_requires_fresh_confirmation_and_native_receipt(tmp_path, monkeypatch, upload):
    from agent.secret_scope import is_multiplex_active, set_multiplex_active
    previous = is_multiplex_active()
    root = tmp_path / "hermes"
    for name in (PROFILE_ID, "exact-profile"):
        home = root / "profiles" / name
        home.mkdir(parents=True)
        (home / "config.yaml").write_text("memory:\n  user_profile_enabled: false\nmodel:\n  context_length: 100000\nauxiliary:\n  title_generation:\n    enabled: false\n")
    artifact = root / "profiles" / PROFILE_ID / "report.csv"
    artifact.write_text("fixture,value\n", encoding="utf-8", newline="\n")
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setattr(gateway_run, "_hermes_home", root)
    monkeypatch.setattr(gateway_run, "_env_path", root / ".env")
    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", hermes_state._IMPORT_DEFAULT_DB_PATH)
    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {
        "platform_toolsets": {"telegram": ["file"]},
        "display": {"tool_progress": "off", "thinking_progress": False, "streaming": True},
        "memory": {"user_profile_enabled": False}, "agent": {"max_iterations": 6},
    })
    monkeypatch.setattr(gateway_run, "_resolve_gateway_model", lambda config=None: "test-model")
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", lambda: {
        "provider": "openrouter", "api_mode": "chat_completions", "base_url": "https://example.invalid/v1",
        "api_key": "test-key-1234567890",
    })
    monkeypatch.setattr("agent.model_metadata.get_model_context_length", lambda *a, **kw: 100000)
    provider = MagicMock()
    monkeypatch.setattr(run_agent.AIAgent, "_create_openai_client", lambda *a, **kw: provider)
    monkeypatch.setattr("agent.process_bootstrap.OpenAI", lambda *a, **kw: provider)
    calls = [ChatCompletionMessageToolCall(id=f"publish-{i}", type="function", function={
        "name": "deliver_artifact", "arguments": json.dumps({"path": str(artifact)}),
    }) for i in (1, 2)]
    provider.chat.completions.create.side_effect = [
        _provider_response(None, [calls[0]]), _provider_response("Готово_АРТЕФАКТ"),
        _provider_response(None, [calls[1]]), _provider_response("Готово_АРТЕФАКТ"),
    ]
    old_registry = _registry()
    caps = old_registry.backend_capabilities | {"documents"}
    registry = AccessRegistry(roles={"shared_room": RolePolicy("shared_room", caps)},
        profiles=old_registry.profiles, shared_scope_bindings=old_registry.shared_scope_bindings,
        scope_capabilities={"room": caps, "exact-room": caps}, backend_capabilities=caps)
    config = GatewayConfig(sessions_dir=root / "sessions", multiplex_profiles=True, access_registry=registry,
        write_sessions_json=False, platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="test-token", extra={"account": ACCOUNT})})
    runner = gateway_run.GatewayRunner(config)
    adapter = TelegramAdapter(config.platforms[Platform.TELEGRAM])
    adapter.send = AsyncMock(return_value=SendResult(success=True, message_id="text-receipt"))
    adapter.send_typing = AsyncMock()
    adapter.stop_typing = AsyncMock()
    adapter.gateway_runner = runner
    adapter.set_message_handler(runner._handle_message)
    runner.adapters[Platform.TELEGRAM] = adapter
    observed = []
    async def send_document(chat_id, file_path, **kwargs):
        key = runner.session_store._generate_session_key(event.source)
        sid = runner.session_store._entries[key].session_id
        db = runner.session_store._db_for_key(key)
        assert json.loads(db.get_session(sid)["artifact_delivery_json"])["state"] == "delivery_started"
        assert not any("Готово_АРТЕФАКТ" in str(c) for c in adapter.send.await_args_list)
        assert chat_id == "-10001"
        assert kwargs["metadata"]["thread_id"] == "31"
        assert kwargs["_require_native"] is True
        assert file_path == str(artifact)
        assert artifact.read_text() == "fixture,value\n"
        observed.append(file_path)
        if upload == "failure":
            return SendResult(success=False, error="fixture upload failure")
        return SendResult(success=True, message_id="file-receipt" if upload == "ack" else None)
    if upload.startswith("native_"):
        async def sdk_send_document(**kwargs):
            key = runner.session_store._generate_session_key(event.source)
            sid = runner.session_store._entries[key].session_id
            db = runner.session_store._db_for_key(key)
            assert json.loads(db.get_session(sid)["artifact_delivery_json"])["state"] == "delivery_started"
            assert not any("Готово_АРТЕФАКТ" in str(c) for c in adapter.send.await_args_list)
            assert kwargs["chat_id"] == -10001
            assert kwargs["message_thread_id"] == 31
            assert kwargs["document"].name == str(artifact)
            assert kwargs["document"].read() == b"fixture,value\n"
            observed.append(str(artifact))
            if upload == "native_failure":
                raise RuntimeError("fixture SDK upload failure")
            return MagicMock(message_id=456)
        adapter._bot = MagicMock(send_document=AsyncMock(side_effect=sdk_send_document))
    else:
        adapter.send_document = AsyncMock(side_effect=send_document)
    event = MessageEvent(text="Send the existing fixture document", message_id="fixture-inbound",
        source=adapter.build_source(chat_id=CHAT_ID, chat_type="group", user_id=USER_ID, thread_id="31"))
    try:
        await adapter.handle_message(event)
        await _wait_for_delivery(adapter)
        assert observed == [str(artifact)]
        key = runner.session_store._generate_session_key(event.source)
        entry = runner.session_store._entries[key]
        db = runner.session_store._db_for_key(key)
        state = json.loads(db.get_session(entry.session_id)["artifact_delivery_json"])
        sent = "\n".join(str(c) for c in adapter.send.await_args_list)
        acknowledged = upload in {"ack", "native_ack"}
        assert state["state"] == ("delivered" if acknowledged else "uncertain")
        if acknowledged:
            assert state["receipt_id"] == ("456" if upload == "native_ack" else "file-receipt")
            assert "Готово_АРТЕФАКТ" in sent
        else:
            assert "Готово_АРТЕФАКТ" not in sent
            assert "не подтверждена" in sent
        assert provider.chat.completions.create.call_count == 4
        from gateway.run import _profile_runtime_scope
        from gateway.access_context import bind_resolved_access_context, get_resolved_access_context
        from hermes_constants import get_hermes_home
        other = registry.resolve(TransportIdentity("telegram", ACCOUNT, "group", USER_ID, CHAT_ID, "33"))
        with _profile_runtime_scope(root / "profiles" / "exact-profile"), bind_resolved_access_context(other):
            with runner._media_delivery_scope_for_source(event.source):
                assert get_resolved_access_context().delivery_target.thread_id == "31"
                assert get_hermes_home() == root / "profiles" / PROFILE_ID
            assert get_resolved_access_context() == other
            assert get_hermes_home() == root / "profiles" / "exact-profile"
    finally:
        for agent in runner._agent_cache.values():
            if hasattr(agent, "close"):
                agent.close()
        runner.session_store.close_all_db_handles()
        set_multiplex_active(previous)


@pytest.mark.asyncio
async def test_generic_document_fallback_is_not_a_native_file_receipt(tmp_path):
    from gateway.platforms.base import BasePlatformAdapter
    path = tmp_path / "fixture.csv"
    path.write_text("fixture,value\n", newline="\n")
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="test-token"))
    adapter.send = AsyncMock(return_value=SendResult(success=True, message_id="text-only"))
    result = await BasePlatformAdapter.send_document(adapter, "1001", str(path), _require_native=True)
    assert result.success is False
    assert adapter.send.call_count == 0
