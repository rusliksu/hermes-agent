import importlib
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient


def _clear_webapi_modules() -> None:
    for name in list(sys.modules):
        if (
            name == "hermes_state"
            or name == "gateway.run"
            or name == "webapi"
            or name.startswith("webapi.")
        ):
            sys.modules.pop(name, None)


def _clear_config_caches() -> None:
    config = importlib.import_module("hermes_cli.config")
    for attr in (
        "_LOAD_CONFIG_CACHE",
        "_RAW_CONFIG_CACHE",
        "_LAST_EXPANDED_CONFIG_BY_PATH",
        "_CONFIG_PARSE_WARNED",
    ):
        value = getattr(config, attr, None)
        if hasattr(value, "clear"):
            value.clear()


def _app(monkeypatch, tmp_path: Path, config: dict[str, Any]):
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    _clear_config_caches()
    _clear_webapi_modules()

    from webapi.app import create_app

    return create_app(), hermes_home


class FakeSessionDB:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.messages: list[dict[str, Any]] = []

    def sanitize_title(self, title: str | None) -> str | None:
        return title.strip() if isinstance(title, str) and title.strip() else None

    def get_next_title_in_lineage(self, title: str) -> str:
        return title

    def create_session(self, session_id: str, source: str, **kwargs: Any) -> str:
        self.sessions[session_id] = {
            "id": session_id,
            "source": source,
            "user_id": kwargs.get("user_id"),
            "model": kwargs.get("model"),
            "model_config": kwargs.get("model_config"),
            "system_prompt": kwargs.get("system_prompt"),
            "parent_session_id": kwargs.get("parent_session_id"),
            "started_at": 1.0,
            "ended_at": None,
            "end_reason": None,
            "message_count": 0,
            "tool_call_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "title": None,
            "preview": None,
            "last_active": 1.0,
        }
        return session_id

    def set_session_title(self, session_id: str, title: str) -> None:
        self.sessions[session_id]["title"] = title

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        return self.sessions.get(session_id)

    def list_sessions_rich(
        self,
        source: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        sessions = list(self.sessions.values())
        if source:
            sessions = [session for session in sessions if session["source"] == source]
        return sessions[offset : offset + limit]

    def session_count(self, source: str | None = None) -> int:
        return len(self.list_sessions_rich(source=source, limit=10_000, offset=0))

    def append_message(self, session_id: str, role: str, content: str | None = None, **kwargs: Any) -> None:
        self.messages.append({"session_id": session_id, "role": role, "content": content, **kwargs})
        if session_id in self.sessions:
            self.sessions[session_id]["message_count"] += 1

    def get_messages_as_conversation(self, session_id: str) -> list[dict[str, Any]]:
        return [
            {"role": message["role"], "content": message.get("content")}
            for message in self.messages
            if message["session_id"] == session_id
        ]


def _decode_sse_events(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for frame in body.strip().split("\n\n"):
        event_name = ""
        data = ""
        for line in frame.splitlines():
            if line.startswith("event: "):
                event_name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = line.removeprefix("data: ")
        if event_name and data:
            events.append((event_name, json.loads(data)))
    return events


@pytest.mark.asyncio
async def test_health_and_config_nested_resolution(monkeypatch, tmp_path):
    app, _home = _app(
        monkeypatch,
        tmp_path,
        {
            "model": {
                "default": "custom-model",
                "provider": "custom-provider",
                "base_url": "http://127.0.0.1:9999/v1",
                "api_mode": "responses",
            }
        },
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        assert (await client.get("/health")).json() == {
            "status": "ok",
            "platform": "hermes-agent",
            "service": "webapi",
        }
        config = (await client.get("/api/config")).json()
    assert config["model"] == "custom-model"
    assert config["provider"] == "custom-provider"
    assert config["base_url"] == "http://127.0.0.1:9999/v1"
    assert config["api_mode"] == "responses"


@pytest.mark.asyncio
async def test_config_patch_writes_nested_model_config(monkeypatch, tmp_path):
    app, home = _app(monkeypatch, tmp_path, {"model": "legacy-model"})

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.patch(
            "/api/config",
            json={
                "model": "patched-model",
                "provider": "patched-provider",
                "base_url": "http://127.0.0.1:8765/v1",
                "api_mode": "chat",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "patched-model"
    assert body["provider"] == "patched-provider"
    saved = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert saved["model"]["default"] == "patched-model"
    assert saved["model"]["provider"] == "patched-provider"
    assert saved["model"]["base_url"] == "http://127.0.0.1:8765/v1"
    assert saved["model"]["api_mode"] == "chat"
    assert "provider" not in saved


@pytest.mark.asyncio
async def test_sessions_route_construction_and_chat_runs_agent(monkeypatch, tmp_path):
    app, _home = _app(
        monkeypatch,
        tmp_path,
        {"model": {"default": "dummy-model", "provider": "dummy-provider"}},
    )
    from webapi.deps import get_session_db_dependency
    import webapi.routes.chat as chat_route

    fake_db = FakeSessionDB()
    create_calls: list[dict[str, Any]] = []
    run_calls: list[dict[str, Any]] = []
    threadpool_calls: list[dict[str, Any]] = []

    async def fake_session_db_dependency() -> FakeSessionDB:
        return fake_db

    class FakeAgent:
        def run_conversation(self, user_message, **kwargs):
            run_calls.append({"user_message": user_message, **kwargs})
            return {
                "final_response": "real mocked answer",
                "completed": True,
                "partial": False,
                "interrupted": False,
                "api_calls": 2,
                "messages": [
                    {"role": "user", "content": kwargs["persist_user_message"]},
                    {"role": "assistant", "content": "real mocked answer"},
                ],
            }

    def fake_create_agent(**kwargs):
        create_calls.append(kwargs)
        return FakeAgent()

    async def fake_run_in_threadpool(func, *args, **kwargs):
        threadpool_calls.append({"func": func, "args": args, "kwargs": kwargs})
        return func(*args, **kwargs)

    app.dependency_overrides[get_session_db_dependency] = fake_session_db_dependency
    monkeypatch.setattr(chat_route, "create_agent", fake_create_agent)
    monkeypatch.setattr(chat_route, "run_in_threadpool", fake_run_in_threadpool)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        created = await client.post(
            "/api/sessions",
            json={"id": "sess_test", "title": "Web UI", "model": "dummy-model"},
        )
        assert created.status_code == 201
        assert created.json()["session"]["id"] == "sess_test"

        listed = (await client.get("/api/sessions")).json()
        assert listed["total"] == 1
        assert listed["items"][0]["id"] == "sess_test"

        fake_db.append_message("sess_test", role="user", content="old question")
        fake_db.append_message("sess_test", role="assistant", content="old answer")
        chat = await client.post(
            "/api/sessions/sess_test/chat",
            json={
                "message": "hello",
                "persist_user_message": "clean hello",
                "model": "request-model",
                "enabled_toolsets": ["memory"],
                "skip_memory": True,
            },
        )
    assert chat.status_code == 200
    payload = chat.json()
    assert payload["session_id"] == "sess_test"
    assert payload["api_calls"] == 2
    assert payload["completed"] is True
    assert payload["interrupted"] is False
    assert payload["final_response"] == "real mocked answer"
    assert len(create_calls) == 1
    assert create_calls[0]["session_id"] == "sess_test"
    assert create_calls[0]["session_db"] is fake_db
    assert create_calls[0]["model"] == "request-model"
    assert create_calls[0]["enabled_toolsets"] == ["memory"]
    assert create_calls[0]["skip_memory"] is True
    assert len(threadpool_calls) == 1
    assert threadpool_calls[0]["func"] is chat_route._run_chat
    assert threadpool_calls[0]["kwargs"]["session_id"] == "sess_test"
    assert len(run_calls) == 1
    assert run_calls[0]["user_message"] == "hello"
    assert run_calls[0]["conversation_history"] == [
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
    ]
    assert run_calls[0]["persist_user_message"] == "clean hello"


@pytest.mark.asyncio
async def test_chat_stream_runs_agent_and_emits_callbacks_result_done(monkeypatch, tmp_path):
    app, _home = _app(
        monkeypatch,
        tmp_path,
        {"model": {"default": "dummy-model", "provider": "dummy-provider"}},
    )
    from webapi.deps import get_session_db_dependency
    import webapi.routes.chat as chat_route

    fake_db = FakeSessionDB()
    fake_db.create_session("sess_stream", source="web", model="dummy-model")
    fake_db.append_message("sess_stream", role="user", content="old question")
    fake_db.append_message("sess_stream", role="assistant", content="old answer")
    create_calls: list[dict[str, Any]] = []
    run_calls: list[dict[str, Any]] = []

    async def fake_session_db_dependency() -> FakeSessionDB:
        return fake_db

    class FakeAgent:
        def __init__(self, callbacks: dict[str, Any]) -> None:
            self.callbacks = callbacks

        def run_conversation(self, user_message, **kwargs):
            run_calls.append({"user_message": user_message, **kwargs})
            self.callbacks["stream_callback"]("partial ")
            self.callbacks["tool_progress_callback"](
                "tool.started",
                "terminal",
                "terminal: echo hi",
                {"command": "echo hi"},
            )
            self.callbacks["tool_progress_callback"](
                "tool.completed",
                "terminal",
                None,
                None,
                duration=0.25,
                is_error=False,
                result="{\"path\":\"/tmp/out.txt\"}",
            )
            self.callbacks["thinking_callback"]("thinking")
            self.callbacks["reasoning_callback"]("reasoning")
            return {
                "final_response": "streamed answer",
                "completed": True,
                "partial": False,
                "interrupted": False,
                "api_calls": 3,
                "messages": [
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {"name": "terminal", "arguments": "{\"command\":\"echo hi\"}"},
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": "call_1", "content": "{\"path\":\"/tmp/out.txt\"}"},
                    {"role": "assistant", "content": "streamed answer"},
                ],
            }

    def fake_create_agent(**kwargs):
        create_calls.append(kwargs)
        return FakeAgent(kwargs)

    app.dependency_overrides[get_session_db_dependency] = fake_session_db_dependency
    monkeypatch.setattr(chat_route, "create_agent", fake_create_agent)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/sessions/sess_stream/chat/stream",
            json={"message": "stream hello", "persist_user_message": "clean stream hello"},
        )

    assert response.status_code == 200
    events = _decode_sse_events(response.text)
    event_names = [name for name, _data in events]
    assert event_names[:3] == ["session.created", "run.started", "message.started"]
    assert "assistant.delta" in event_names
    assert "tool.started" in event_names
    assert "thinking.delta" in event_names
    assert "reasoning.delta" in event_names
    assert "artifact.created" in event_names
    assert "assistant.completed" in event_names
    assert "run.completed" in event_names
    assert event_names[-1] == "done"
    assert event_names.count("tool.completed") == 1
    assert event_names.index("tool.started") < event_names.index("tool.completed")
    assert event_names.index("tool.completed") < event_names.index("artifact.created")
    tool_completed = [data for name, data in events if name == "tool.completed"][0]
    assistant_completed = [data for name, data in events if name == "assistant.completed"][-1]
    run_completed = [data for name, data in events if name == "run.completed"][-1]
    assert tool_completed["tool_name"] == "terminal"
    assert tool_completed["result_preview"] == "{\"path\":\"/tmp/out.txt\"}"
    assert any(data["path"] == "/tmp/out.txt" for name, data in events if name == "artifact.created")
    assert assistant_completed["content"] == "streamed answer"
    assert run_completed["api_calls"] == 3
    assert len(create_calls) == 1
    assert callable(create_calls[0]["stream_callback"])
    assert callable(create_calls[0]["tool_progress_callback"])
    assert callable(create_calls[0]["thinking_callback"])
    assert callable(create_calls[0]["reasoning_callback"])
    assert callable(create_calls[0]["step_callback"])
    assert len(run_calls) == 1
    assert run_calls[0]["user_message"] == "stream hello"
    assert run_calls[0]["conversation_history"] == [
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
    ]
    assert run_calls[0]["persist_user_message"] == "clean stream hello"
    assert "stream_callback" not in run_calls[0]


def test_legacy_webui_routes_are_registered(monkeypatch, tmp_path):
    app, _home = _app(
        monkeypatch,
        tmp_path,
        {"model": {"default": "dummy-model", "provider": "dummy-provider"}},
    )

    client = TestClient(app)
    route_paths = {getattr(route, "path", "") for route in client.app.routes}
    assert "/health" in route_paths
    assert "/api/config" in route_paths
    assert "/api/sessions" in route_paths
    assert "/api/sessions/{session_id}/chat" in route_paths
    assert "/api/sessions/{session_id}/chat/stream" in route_paths
    assert "/api/skills" in route_paths
    assert "/api/memory" in route_paths
    assert "/api/models" in route_paths
