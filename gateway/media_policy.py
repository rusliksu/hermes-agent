"""Shared config→env bridge for media-delivery policy.

``validate_media_delivery_path`` reads ``HERMES_MEDIA_DELIVERY_STRICT`` (gateway.strict),
``HERMES_MEDIA_ALLOW_DIRS`` (gateway.media_delivery_allow_dirs) and
``HERMES_MEDIA_TRUST_RECENT_FILES`` (gateway.trust_recent_files).  Every delivery
entrypoint (gateway startup, ``hermes cron run``, ``hermes send``) calls
:func:`apply_media_policy_env` first so standalone paths filter under the gateway's
policy instead of silently dropping attachments in strict/allowlisted deployments.
An explicitly-set env var WINS over config.yaml, so shell overrides survive. Under a HERMES_HOME
override (a served profile's turn or cron fire) nothing is bridged: readers load that profile's config.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_FLAG_ENVS = (("strict", "HERMES_MEDIA_DELIVERY_STRICT"), ("trust_recent_files", "HERMES_MEDIA_TRUST_RECENT_FILES"))
_ALLOW_DIRS_ENV = "HERMES_MEDIA_ALLOW_DIRS"
_TRUST_RECENT_SECONDS_ENV = "HERMES_MEDIA_TRUST_RECENT_SECONDS"
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_REGISTRY_FREE_MEDIA_SCOPE = ContextVar("HERMES_REGISTRY_FREE_MEDIA_SCOPE", default=False)


@contextmanager
def registry_free_media_scope(enabled):
    """Native runner verified profile scope, never a fallback for a registry-bound source."""
    token = _REGISTRY_FREE_MEDIA_SCOPE.set(bool(enabled))
    try:
        yield
    finally:
        _REGISTRY_FREE_MEDIA_SCOPE.reset(token)


def media_requires_typed_scope():
    from agent.secret_scope import is_multiplex_active
    from gateway.access_context import get_resolved_access_context
    return get_resolved_access_context() is not None or (
        is_multiplex_active() and not _REGISTRY_FREE_MEDIA_SCOPE.get())


def _routed_gateway_cfg() -> Optional[dict[str, Any]]:
    """``gateway`` section of the ROUTED profile's config when a HERMES_HOME override is active
    (multiplexed turn), else None. The env bridge is one process-wide copy of the launch profile's
    policy, so a secondary's deliveries must read their own config instead of ``os.environ``."""
    from hermes_constants import get_hermes_home_override
    if not get_hermes_home_override():
        return None
    try:
        from hermes_cli.config import load_config_readonly
        gateway_cfg = load_config_readonly().get("gateway")
    except Exception:
        return {}
    return gateway_cfg if isinstance(gateway_cfg, dict) else {}


def media_delivery_strict() -> bool:
    cfg = _routed_gateway_cfg()
    if cfg is not None:
        return bool(cfg.get("strict", False))
    return os.environ.get(_FLAG_ENVS[0][1], "0").strip().lower() in _TRUTHY


def media_delivery_allow_dirs() -> str:
    """Operator allowlist as the ``os.pathsep``-joined string the validator splits."""
    cfg = _routed_gateway_cfg()
    if cfg is not None:
        return _allow_dirs_str(cfg.get("media_delivery_allow_dirs"))
    return os.environ.get(_ALLOW_DIRS_ENV, "")


def media_delivery_trust_recent() -> bool:
    cfg = _routed_gateway_cfg()
    if cfg is not None:
        return bool(cfg.get("trust_recent_files", True))
    return os.environ.get(_FLAG_ENVS[1][1], "1").strip().lower() not in ("0", "false", "no", "off", "")


def media_delivery_trust_recent_seconds() -> str:
    """Raw recency window (``""`` = validator default); the caller parses/floors it."""
    cfg = _routed_gateway_cfg()
    if cfg is not None:
        raw = cfg.get("trust_recent_files_seconds")
        return "" if raw is None else str(raw)
    return os.environ.get(_TRUST_RECENT_SECONDS_ENV, "")



def _load_gateway_cfg(config: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    if config is None:
        try:
            from hermes_cli.config import load_config

            config = load_config() or {}
        except Exception:
            return {}
    gateway_cfg = config.get("gateway", {})
    return gateway_cfg if isinstance(gateway_cfg, dict) else {}


def _set_env_default(env: str, value: str) -> None:
    """Set ``env`` only when unset/empty and ``value`` is non-empty (env wins)."""
    if value and not os.environ.get(env):
        os.environ[env] = value


def _allow_dirs_str(allow_dirs: Any) -> str:
    if isinstance(allow_dirs, (list, tuple)):
        return os.pathsep.join(str(p) for p in allow_dirs if p)
    return allow_dirs if isinstance(allow_dirs, str) else ""


def apply_media_policy_env(config: Optional[dict[str, Any]] = None) -> None:
    """Bridge gateway media-policy settings from config.yaml into the env.  Idempotent,
    env-wins, never raises — a bridge failure must not break delivery (validator defaults apply).

    No-op under a HERMES_HOME override, the same gate the readers use: there they take the
    profile's own config (``_routed_gateway_cfg``), and ``os.environ`` is shared — a routed cron
    fire would hand its allowlist and strictness to the launch profile and its children."""
    try:
        from hermes_constants import get_hermes_home_override
        if get_hermes_home_override():
            return
        gateway_cfg = _load_gateway_cfg(config)
        if not gateway_cfg:
            return
        for key, env in _FLAG_ENVS:
            flag = gateway_cfg.get(key)
            if flag is not None:
                _set_env_default(env, "1" if flag else "0")
        allow_dirs = gateway_cfg.get("media_delivery_allow_dirs")
        if allow_dirs:
            _set_env_default(_ALLOW_DIRS_ENV, _allow_dirs_str(allow_dirs))
    except Exception:
        logger.debug("apply_media_policy_env failed", exc_info=True)


def typed_artifact_roots(session_key: Optional[str] = None) -> list:
    """Roots a documents-capable access context may deliver from; empty when unbound."""
    from agent.access_paths import bound_artifact_roots

    try:
        roots = bound_artifact_roots() if session_key is None else bound_artifact_roots(session_key)
    except (OSError, RuntimeError, ValueError):
        logger.exception("bound artifact roots unavailable")
        return []
    return list(roots)


def media_path_in_scope(resolved, *, translated: bool = False) -> bool:
    """Typed-scope delivery: bound artifact roots plus the foreign-profile/denylist guard.

    `translated` marks a Docker container-absolute MEDIA tag mapped to its host mount by the ACTIVE
    profile scope. Multiplex-without-registry has no bound access context, so that scope's own home
    is the authority (#109024); an untyped host path still requires a bound context.
    """
    from agent.access_paths import bound_artifact_roots, bound_profile_home, profile_read_block_error
    from gateway.platforms.base import _path_under_denied_prefix

    try:
        if _path_under_denied_prefix(resolved):
            return False
        if translated and bound_profile_home() is None:
            from hermes_constants import get_hermes_home

            return resolved.is_relative_to(get_hermes_home())
        if profile_read_block_error(resolved):
            return False
        return any(resolved.is_relative_to(root) for root in bound_artifact_roots())
    except (OSError, RuntimeError, ValueError):
        logger.debug("typed media scope check failed", exc_info=True)
        return False


def remote_media_may_fetch(raw, scoped, exists) -> bool:
    """Whether the remote-sandbox fallback may copy *raw* into an allowed cache."""
    if not scoped:
        return True
    from pathlib import Path

    from agent.access_paths import bound_profile_home, profile_read_block_error
    from gateway.platforms.base import _path_under_denied_prefix

    try:
        return (bound_profile_home() is not None and not exists(raw)
                and not profile_read_block_error(raw)
                and not _path_under_denied_prefix(Path(raw).expanduser().resolve()))
    except (OSError, RuntimeError, ValueError):
        logger.debug("remote media fetch guard failed", exc_info=True)
        return False
