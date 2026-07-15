from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Any

from fastapi import HTTPException

from hermes_state import SessionDB
from hermes_cli.config import load_config
from tools.memory_tool import MemoryStore


def _coerce_runtime_model_name(model: Any) -> str:
    if isinstance(model, str):
        return model
    if isinstance(model, dict):
        for key in ("default", "model", "id", "name"):
            value = model.get(key)
            if isinstance(value, str) and value:
                return value
    return str(model or "")


def _model_config(config: dict[str, Any]) -> dict[str, Any]:
    model = config.get("model")
    return model if isinstance(model, dict) else {}


def _resolve_config_value(config: dict[str, Any], key: str) -> Any:
    model_config = _model_config(config)
    value = model_config.get(key)
    if value not in (None, ""):
        return value
    value = config.get(key)
    if value not in (None, ""):
        return value
    return None


def _resolve_gateway_model_compat(config: dict[str, Any] | None = None) -> str:
    """Match gateway.run._resolve_gateway_model without importing gateway.run.

    The gateway module performs substantial startup work at import time.  Webapi
    only needs the documented model-shape resolution used there: a legacy string
    ``model`` value, or nested ``model.default`` / ``model.model``.
    """
    config = config or load_config()
    model_config = config.get("model", {})
    if isinstance(model_config, str):
        return model_config
    if isinstance(model_config, dict):
        return model_config.get("default") or model_config.get("model") or ""
    return ""

WEB_SOURCE = "web"


@lru_cache(maxsize=1)
def get_session_db() -> SessionDB:
    return SessionDB()


@lru_cache(maxsize=1)
def get_memory_store() -> MemoryStore:
    store = MemoryStore()
    store.load_from_disk()
    return store


def reload_memory_store() -> MemoryStore:
    store = get_memory_store()
    store.load_from_disk()
    return store


async def get_session_db_dependency() -> SessionDB:
    return get_session_db()


async def reload_memory_store_dependency() -> MemoryStore:
    return reload_memory_store()


def get_config() -> dict[str, Any]:
    return load_config()


def get_runtime_model() -> str:
    config = get_config()
    resolved = _resolve_gateway_model_compat(config)
    return _coerce_runtime_model_name(resolved)


def get_runtime_agent_kwargs() -> dict[str, Any]:
    config = get_config()
    return {
        "provider": _resolve_config_value(config, "provider"),
        "base_url": _resolve_config_value(config, "base_url"),
        "api_mode": _resolve_config_value(config, "api_mode"),
        "default_headers": _resolve_config_value(config, "default_headers") or {},
    }


def create_agent(
    *,
    session_id: str,
    session_db: SessionDB,
    model: str | None = None,
    ephemeral_system_prompt: str | None = None,
    enabled_toolsets: list[str] | None = None,
    disabled_toolsets: list[str] | None = None,
    skip_context_files: bool = False,
    skip_memory: bool = False,
    stream_callback=None,
    tool_progress_callback=None,
    thinking_callback=None,
    reasoning_callback=None,
    step_callback=None,
) -> AIAgent:
    from run_agent import AIAgent

    runtime_kwargs = get_runtime_agent_kwargs()
    effective_model = model or get_runtime_model()

    return AIAgent(
        model=effective_model,
        **runtime_kwargs,
        max_iterations=90,
        quiet_mode=True,
        verbose_logging=False,
        ephemeral_system_prompt=ephemeral_system_prompt,
        session_id=session_id,
        platform="webapi",
        session_db=session_db,
        enabled_toolsets=enabled_toolsets,
        disabled_toolsets=disabled_toolsets,
        skip_context_files=skip_context_files,
        skip_memory=skip_memory,
        tool_progress_callback=tool_progress_callback,
        thinking_callback=thinking_callback,
        reasoning_callback=reasoning_callback,
        step_callback=step_callback,
        stream_delta_callback=stream_callback,
    )


def get_session_or_404(session_id: str, session_db: SessionDB | None = None) -> dict[str, Any]:
    db = session_db or get_session_db()
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    return session


def ensure_session_title(session_db: SessionDB, title: str | None) -> str | None:
    cleaned = session_db.sanitize_title(title)
    if cleaned:
        return cleaned
    return session_db.get_next_title_in_lineage("New Chat")


def new_session_id() -> str:
    return f"sess_{uuid.uuid4().hex}"
