"""Config-backed per-user gateway overrides.

The gateway owns message lifecycle and session state.  This module keeps the
``gateway.user_overrides`` config contract pure and testable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeBundle:
    """Resolved runtime values needed to construct an AIAgent."""

    provider: str = ""
    api_key: str = ""
    base_url: str = ""
    api_mode: str = ""
    default_headers: dict[str, Any] = field(default_factory=dict)
    command: Any = None
    args: list[Any] = field(default_factory=list)
    credential_pool: Any = None
    max_tokens: Any = None

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None) -> "RuntimeBundle":
        value = value or {}
        return cls(
            provider=str(value.get("provider") or ""),
            api_key=str(value.get("api_key") or ""),
            base_url=str(value.get("base_url") or ""),
            api_mode=str(value.get("api_mode") or ""),
            default_headers=dict(value.get("default_headers") or {}),
            command=value.get("command"),
            args=list(value.get("args") or []),
            credential_pool=value.get("credential_pool"),
            max_tokens=value.get("max_tokens"),
        )

    def to_agent_kwargs(self) -> dict[str, Any]:
        result = {
            "provider": self.provider,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "api_mode": self.api_mode,
            "default_headers": dict(self.default_headers or {}),
            "command": self.command,
            "args": list(self.args or []),
            "credential_pool": self.credential_pool,
        }
        if self.max_tokens is not None:
            result["max_tokens"] = self.max_tokens
        return result


@dataclass(frozen=True)
class UserOverride:
    """Validated per-user gateway override."""

    platform: str
    user_id: str
    scope: str = "dm"
    model: str = ""
    provider: str = ""
    reasoning_config: dict[str, Any] | None = None
    runtime: RuntimeBundle | None = None
    provider_label: str = ""

    @property
    def has_model_override(self) -> bool:
        return bool(self.model)


def _normalize_key(value: Any) -> str:
    return str(value).strip()


def _source_platform_key(source: Any) -> str:
    platform = getattr(source, "platform", None)
    value = getattr(platform, "value", platform)
    return _normalize_key(value).lower()


def _source_user_keys(source: Any) -> list[str]:
    keys: list[str] = []
    for attr in ("user_id", "user_id_alt"):
        value = getattr(source, attr, None)
        if value is None:
            continue
        normalized = _normalize_key(value)
        if normalized and normalized not in keys:
            keys.append(normalized)
    return keys


def _log_ignore(
    log: logging.Logger,
    platform_key: str,
    user_id: str,
    reason: str,
) -> None:
    log.warning(
        "Ignoring malformed gateway.user_overrides.%s.%s: %s",
        platform_key,
        user_id,
        reason,
    )


def _lookup_raw_override(
    config: dict[str, Any],
    source: Any,
) -> tuple[str, str, Any] | None:
    if not isinstance(config, dict) or source is None:
        return None
    if getattr(source, "chat_type", None) != "dm":
        return None

    platform_key = _source_platform_key(source)
    user_keys = _source_user_keys(source)
    if not platform_key or not user_keys:
        return None

    gateway_cfg = config.get("gateway")
    if not isinstance(gateway_cfg, dict):
        return None
    all_overrides = gateway_cfg.get("user_overrides")
    if not isinstance(all_overrides, dict):
        return None

    normalized_platforms = {
        _normalize_key(key).lower(): value
        for key, value in all_overrides.items()
    }
    platform_overrides = normalized_platforms.get(platform_key)
    if not isinstance(platform_overrides, dict):
        return None

    normalized_users = {
        _normalize_key(key): value
        for key, value in platform_overrides.items()
    }
    for user_key in user_keys:
        if user_key in normalized_users:
            return platform_key, user_key, normalized_users[user_key]
    return None


def parse_gateway_user_override(
    config: dict[str, Any] | None,
    source: Any,
    *,
    log: logging.Logger | None = None,
) -> UserOverride | None:
    """Parse and validate a DM-scoped user override from config.

    Supported contract:

    ``gateway.user_overrides.<platform>.<user_id>.scope`` must be ``dm``.
    ``model`` and ``provider`` are optional strings. ``reasoning_effort`` uses
    Hermes' canonical reasoning parser. At least one of ``model`` or
    ``reasoning_effort`` is required.
    """

    log = log or logger
    lookup = _lookup_raw_override(config or {}, source)
    if lookup is None:
        return None
    platform_key, user_id, raw = lookup

    if not isinstance(raw, dict):
        _log_ignore(log, platform_key, user_id, "override must be a mapping")
        return None

    scope = str(raw.get("scope") or "").strip().lower()
    if scope != "dm":
        _log_ignore(log, platform_key, user_id, "scope must be 'dm'")
        return None

    has_model = "model" in raw
    has_reasoning = "reasoning_effort" in raw
    if not has_model and not has_reasoning:
        _log_ignore(log, platform_key, user_id, "expected model and/or reasoning_effort")
        return None

    model = ""
    if has_model:
        raw_model = raw.get("model")
        if not isinstance(raw_model, str) or not raw_model.strip():
            _log_ignore(log, platform_key, user_id, "model must be a non-empty string")
            return None
        model = raw_model.strip()

    provider = ""
    if "provider" in raw:
        raw_provider = raw.get("provider")
        if not isinstance(raw_provider, str) or not raw_provider.strip():
            _log_ignore(log, platform_key, user_id, "provider must be a non-empty string")
            return None
        provider = raw_provider.strip()

    reasoning_config = None
    if has_reasoning:
        effort = raw.get("reasoning_effort")
        if not isinstance(effort, str) or not effort.strip():
            _log_ignore(log, platform_key, user_id, "reasoning_effort must be a non-empty string")
            return None
        from hermes_constants import parse_reasoning_effort

        reasoning_config = parse_reasoning_effort(effort)
        if reasoning_config is None:
            _log_ignore(log, platform_key, user_id, "reasoning_effort is unknown")
            return None

    return UserOverride(
        platform=platform_key,
        user_id=user_id,
        model=model,
        provider=provider,
        reasoning_config=reasoning_config,
    )


def resolve_gateway_user_override(
    config: dict[str, Any] | None,
    source: Any,
    *,
    current_model: str = "",
    current_runtime: dict[str, Any] | RuntimeBundle | None = None,
    switch_model_func: Optional[Callable[..., Any]] = None,
    log: logging.Logger | None = None,
) -> UserOverride | None:
    """Return a parsed override with a full runtime bundle when model is set."""

    log = log or logger
    parsed = parse_gateway_user_override(config, source, log=log)
    if parsed is None or not parsed.model:
        return parsed

    runtime = (
        current_runtime
        if isinstance(current_runtime, RuntimeBundle)
        else RuntimeBundle.from_mapping(current_runtime)
    )
    if switch_model_func is None:
        from hermes_cli.model_switch import switch_model as switch_model_func

    result = switch_model_func(
        raw_input=parsed.model,
        current_provider=runtime.provider or "openrouter",
        current_model=current_model or "",
        current_base_url=runtime.base_url,
        current_api_key=runtime.api_key,
        is_global=False,
        explicit_provider=parsed.provider,
        user_providers=(config or {}).get("providers") if isinstance(config, dict) else None,
        custom_providers=(config or {}).get("custom_providers") if isinstance(config, dict) else None,
    )
    if not getattr(result, "success", False):
        _log_ignore(
            log,
            parsed.platform,
            parsed.user_id,
            f"model runtime resolution failed: {getattr(result, 'error_message', '') or 'unknown error'}",
        )
        if parsed.reasoning_config is not None:
            return UserOverride(
                platform=parsed.platform,
                user_id=parsed.user_id,
                model="",
                provider=parsed.provider,
                reasoning_config=parsed.reasoning_config,
            )
        return None

    runtime_for_provider = runtime
    try:
        from hermes_cli.runtime_provider import resolve_runtime_provider

        runtime_for_provider = RuntimeBundle.from_mapping(
            resolve_runtime_provider(
                requested=getattr(result, "target_provider", "") or parsed.provider
            )
        )
    except Exception:
        runtime_for_provider = runtime

    resolved_runtime = RuntimeBundle(
        provider=getattr(result, "target_provider", "") or runtime.provider,
        api_key=getattr(result, "api_key", "") or runtime_for_provider.api_key,
        base_url=getattr(result, "base_url", "") or runtime_for_provider.base_url,
        api_mode=getattr(result, "api_mode", "") or runtime_for_provider.api_mode,
        default_headers=dict(runtime_for_provider.default_headers or {}),
        command=runtime_for_provider.command,
        args=list(runtime_for_provider.args or []),
        credential_pool=runtime_for_provider.credential_pool,
        max_tokens=runtime_for_provider.max_tokens,
    )
    return UserOverride(
        platform=parsed.platform,
        user_id=parsed.user_id,
        model=getattr(result, "new_model", "") or parsed.model,
        provider=getattr(result, "target_provider", "") or parsed.provider,
        reasoning_config=parsed.reasoning_config,
        runtime=resolved_runtime,
        provider_label=getattr(result, "provider_label", "") or "",
    )
