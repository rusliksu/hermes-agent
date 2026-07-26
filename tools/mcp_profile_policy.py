"""Typed multiplex MCP config and environment policy.

This module is intentionally independent from ``tools.mcp_tool``.  It owns the
pure policy decisions for profile-bound MCP config preparation: validation,
credential substitution, opaque fingerprints, allowed raw operations, and child
environment construction.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")
_CREDENTIAL_REF_PATTERN = re.compile(r"\$\{credential:([^}]+)\}")
_MCP_CREDENTIAL_KEY_PATTERN = re.compile(r"^MCP_[A-Za-z_][A-Za-z0-9_]*$")
_MCP_CREDENTIAL_ALIAS_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_RESERVED_ENV_KEYS = {
    "HOME",
    "HERMES_HOME",
    "PWD",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
}
_TYPED_BASE_ENV_KEYS = {
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOGNAME",
    "PATH",
    "SHELL",
    "TERM",
    "USER",
}
_RESOURCES_OPERATIONS = frozenset({"list_resources", "read_resource"})
_PROMPTS_OPERATIONS = frozenset({"list_prompts", "get_prompt"})


class TypedMCPConfigError(ValueError):
    """Categorical typed-multiplex MCP config denial."""


@dataclass(frozen=True)
class PreparedTypedMCPServerConfig:
    """A validated typed MCP server config ready for runtime integration."""

    prepared_config: dict[str, Any]
    fingerprint: str
    credential_ref_metadata: dict[str, str]
    allowed_operation_names: frozenset[str]


def _error(reason: str) -> TypedMCPConfigError:
    return TypedMCPConfigError(reason)


def _is_reserved_env_key(key: str) -> bool:
    upper = key.upper()
    return upper in _RESERVED_ENV_KEYS or upper.startswith("XDG_")


def _credential_alias(ref: str) -> str:
    alias = ref[len("credential:"):]
    if (
        not alias
        or alias != alias.strip()
        or not _MCP_CREDENTIAL_ALIAS_PATTERN.fullmatch(alias)
    ):
        raise _error("profile-bound-mcp-credential-alias-invalid")
    return alias


def _iter_placeholders(value: Any, path: tuple[Any, ...] = (), *, in_key: bool = False):
    if isinstance(value, str):
        for match in _ENV_VAR_PATTERN.finditer(value):
            yield path, in_key, match.group(1).strip()
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_path = path + (key,)
            yield from _iter_placeholders(key, key_path, in_key=True)
            yield from _iter_placeholders(child, key_path, in_key=False)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_placeholders(child, path + (index,), in_key=False)


def _is_allowed_credential_value_path(path: tuple[Any, ...], in_key: bool) -> bool:
    return (
        not in_key
        and len(path) == 2
        and path[0] in {"env", "headers"}
        and isinstance(path[1], str)
    )


def _validate_ref_locations(cfg: Mapping[str, Any]) -> set[str]:
    aliases: set[str] = set()
    for path, in_key, ref in _iter_placeholders(cfg):
        if not ref.startswith("credential:"):
            raise _error("profile-bound-mcp-legacy-placeholder-denied")
        if not _is_allowed_credential_value_path(path, in_key):
            raise _error("profile-bound-mcp-credential-ref-location-denied")
        aliases.add(_credential_alias(ref))
    return aliases


def _validate_env_section(cfg: Mapping[str, Any]) -> None:
    env = cfg.get("env")
    if env is None:
        return
    if not isinstance(env, Mapping):
        raise _error("profile-bound-mcp-env-invalid")
    for key in env:
        if not isinstance(key, str) or not key:
            raise _error("profile-bound-mcp-env-key-invalid")
        if _is_reserved_env_key(key):
            raise _error("profile-bound-mcp-env-key-denied")


def _validate_headers_section(cfg: Mapping[str, Any]) -> None:
    headers = cfg.get("headers")
    if headers is None:
        return
    if not isinstance(headers, Mapping):
        raise _error("profile-bound-mcp-headers-invalid")
    for key in headers:
        if not isinstance(key, str) or not key:
            raise _error("profile-bound-mcp-header-key-invalid")


def _require_tools_include(cfg: Mapping[str, Any]) -> tuple[frozenset[str], bool, bool]:
    tools_cfg = cfg.get("tools")
    if not isinstance(tools_cfg, Mapping):
        raise _error("profile-bound-mcp-tools-include-missing")

    include = tools_cfg.get("include")
    if not isinstance(include, list) or not include:
        raise _error("profile-bound-mcp-tools-include-missing")

    seen: set[str] = set()
    for item in include:
        if not isinstance(item, str) or not item.strip():
            raise _error("profile-bound-mcp-tools-include-invalid")
        tool_name = item.strip()
        if tool_name != item or "*" in item or "${" in item:
            raise _error("profile-bound-mcp-tools-include-invalid")
        if tool_name in seen:
            raise _error("profile-bound-mcp-tools-include-duplicate")
        seen.add(tool_name)

    for key in ("resources", "prompts"):
        if key in tools_cfg and not isinstance(tools_cfg.get(key), bool):
            raise _error(f"profile-bound-mcp-{key}-flag-invalid")
    return frozenset(seen), tools_cfg.get("resources") is True, tools_cfg.get("prompts") is True


def _credential_ref_mapping(cfg: Mapping[str, Any]) -> dict[str, str]:
    refs = cfg.get("credential_refs")
    if refs is None:
        refs = {}
    if not isinstance(refs, Mapping):
        raise _error("profile-bound-mcp-credential-refs-invalid")

    mapping: dict[str, str] = {}
    for alias, secret_key in refs.items():
        if (
            not isinstance(alias, str)
            or not alias
            or alias != alias.strip()
            or not _MCP_CREDENTIAL_ALIAS_PATTERN.fullmatch(alias)
        ):
            raise _error("profile-bound-mcp-credential-alias-invalid")
        if (
            not isinstance(secret_key, str)
            or not secret_key
            or secret_key != secret_key.strip()
            or not _MCP_CREDENTIAL_KEY_PATTERN.fullmatch(secret_key)
        ):
            raise _error("profile-bound-mcp-credential-key-invalid")
        mapping[alias] = secret_key
    return mapping


def _substitute_allowed_refs(
    cfg: Mapping[str, Any],
    resolved_refs: Mapping[str, str],
    resources_enabled: bool,
    prompts_enabled: bool,
) -> dict[str, Any]:
    prepared = deepcopy(dict(cfg))
    for section in ("env", "headers"):
        values = prepared.get(section)
        if isinstance(values, dict):
            for key, value in list(values.items()):
                if isinstance(value, str):
                    values[key] = _CREDENTIAL_REF_PATTERN.sub(
                        lambda match: resolved_refs[match.group(1)],
                        value,
                    )

    prepared.pop("credential_refs", None)
    tools = dict(prepared.get("tools") or {})
    tools["resources"] = resources_enabled
    tools["prompts"] = prompts_enabled
    prepared["tools"] = tools
    return prepared


def typed_mcp_config_fingerprint(config: Mapping[str, Any]) -> str:
    """Return a deterministic opaque SHA-256 fingerprint for prepared config."""

    def _canonical(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                str(key): _canonical(child)
                for key, child in sorted(value.items(), key=lambda item: str(item[0]))
            }
        if isinstance(value, list):
            return [_canonical(child) for child in value]
        if isinstance(value, tuple):
            return [_canonical(child) for child in value]
        return value

    canonical = json.dumps(
        _canonical(config),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def prepare_typed_mcp_server_config(
    name: str,
    cfg: Mapping[str, Any],
    secret_scope: Mapping[str, str] | None,
) -> PreparedTypedMCPServerConfig:
    """Validate and prepare one profile-bound MCP server config."""

    if not isinstance(cfg, Mapping):
        raise _error("profile-bound-mcp-config-invalid")
    if str(cfg.get("auth") or "").strip().lower() == "oauth":
        raise _error("profile-bound-oauth-not-implemented")

    allowed_tools, resources_enabled, prompts_enabled = _require_tools_include(cfg)
    _validate_env_section(cfg)
    _validate_headers_section(cfg)
    placeholder_aliases = _validate_ref_locations(cfg)
    mapping = _credential_ref_mapping(cfg)
    if placeholder_aliases != set(mapping):
        raise _error("profile-bound-mcp-credential-refs-mismatch")
    if secret_scope is None:
        raise _error("profile-bound-mcp-secret-scope-missing")

    resolved_refs: dict[str, str] = {}
    for alias, secret_key in mapping.items():
        value = secret_scope.get(secret_key)
        if not isinstance(value, str) or not value.strip():
            raise _error("profile-bound-mcp-credential-ref-missing")
        resolved_refs[alias] = value

    prepared = _substitute_allowed_refs(
        cfg,
        resolved_refs,
        resources_enabled,
        prompts_enabled,
    )
    allowed_operations = set(allowed_tools)
    if resources_enabled:
        allowed_operations.update(_RESOURCES_OPERATIONS)
    if prompts_enabled:
        allowed_operations.update(_PROMPTS_OPERATIONS)

    return PreparedTypedMCPServerConfig(
        prepared_config=prepared,
        fingerprint=typed_mcp_config_fingerprint(prepared),
        credential_ref_metadata=dict(mapping),
        allowed_operation_names=frozenset(allowed_operations),
    )


def build_typed_child_env(
    user_env: Mapping[str, Any] | None,
    *,
    profile_home: os.PathLike[str] | str,
    ambient_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build a profile-isolated stdio child environment."""

    source_env = ambient_env if ambient_env is not None else os.environ
    env = {
        key: str(value)
        for key, value in source_env.items()
        if key in _TYPED_BASE_ENV_KEYS
    }
    if user_env:
        for key, value in user_env.items():
            if not isinstance(key, str) or not key:
                raise _error("profile-bound-mcp-env-key-invalid")
            if _is_reserved_env_key(key):
                raise _error("profile-bound-mcp-env-key-denied")
            env[key] = str(value)

    home = Path(profile_home).resolve()
    tmp = home / "tmp"
    env.update({
        "HOME": str(home),
        "HERMES_HOME": str(home),
        "TMPDIR": str(tmp),
        "TEMP": str(tmp),
        "TMP": str(tmp),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
    })
    return env
