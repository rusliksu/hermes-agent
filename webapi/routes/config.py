from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from hermes_cli.config import load_config, save_config
from webapi.deps import get_config, get_runtime_agent_kwargs, get_runtime_model
from webapi.models.config import ConfigResponse


router = APIRouter(prefix="/api/config", tags=["config"])


class ConfigPatch(BaseModel):
    model: str | None = None
    provider: str | None = None
    base_url: str | None = None
    api_mode: str | None = None


def _ensure_model_config(config: dict[str, Any]) -> dict[str, Any]:
    existing = config.get("model")
    if isinstance(existing, dict):
        return existing
    model_config: dict[str, Any] = {}
    if isinstance(existing, str) and existing.strip():
        model_config["default"] = existing.strip()
    config["model"] = model_config
    return model_config


def _set_or_remove(model_config: dict[str, Any], key: str, value: str | None) -> None:
    if value is None:
        return
    cleaned = value.strip()
    if cleaned:
        model_config[key] = cleaned
    else:
        model_config.pop(key, None)


@router.get("", response_model=ConfigResponse)
async def get_web_config() -> ConfigResponse:
    runtime = get_runtime_agent_kwargs()
    return ConfigResponse(
        model=get_runtime_model(),
        provider=runtime.get("provider"),
        api_mode=runtime.get("api_mode"),
        base_url=runtime.get("base_url"),
        config=get_config(),
    )


@router.patch("")
async def patch_web_config(patch: ConfigPatch) -> dict[str, Any]:
    """Patch the active HERMES_HOME config.yaml with webui model settings.

    Hermes 0.18 stores the selected provider/model under the nested
    ``model`` section.  Keep legacy flat model strings readable by migrating
    them to ``model.default`` on write, but do not write root-level provider
    keys from the web API.
    """
    try:
        config = load_config()
        model_config = _ensure_model_config(config)

        _set_or_remove(model_config, "default", patch.model)
        _set_or_remove(model_config, "provider", patch.provider)
        _set_or_remove(model_config, "base_url", patch.base_url)
        _set_or_remove(model_config, "api_mode", patch.api_mode)

        save_config(config)
        runtime = get_runtime_agent_kwargs()
        return {
            "ok": True,
            "model": get_runtime_model(),
            "provider": runtime.get("provider"),
            "base_url": runtime.get("base_url"),
            "api_mode": runtime.get("api_mode"),
            "config": get_config(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
