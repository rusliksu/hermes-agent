import importlib
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
async def test_sessions_route_construction_and_chat_mock_boundary(monkeypatch, tmp_path):
    app, _home = _app(
        monkeypatch,
        tmp_path,
        {"model": {"default": "dummy-model", "provider": "dummy-provider"}},
    )
    from webapi.deps import get_session_db_dependency

    fake_db = FakeSessionDB()

    async def fake_session_db_dependency() -> FakeSessionDB:
        return fake_db

    app.dependency_overrides[get_session_db_dependency] = fake_session_db_dependency

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

        chat = await client.post("/api/sessions/sess_test/chat", json={"message": "hello"})
    assert chat.status_code == 200
    payload = chat.json()
    assert payload["session_id"] == "sess_test"
    assert payload["api_calls"] == 0
    assert payload["completed"] is False
    assert payload["interrupted"] is True
    assert "compatibility shim" in payload["final_response"]


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
