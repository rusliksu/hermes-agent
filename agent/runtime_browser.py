"""Typed request-path authority for browser and URL safety helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.runtime_cwd import bound_profile_home

_VALID_ENGINES = frozenset({"auto", "chrome", "lightpanda"})


@dataclass(frozen=True)
class BrowserRequestAuthority:
    """Server-bound browser policy for one typed request.

    ``None`` from :func:`browser_request_authority` means legacy/no typed
    context.  A concrete instance means request-path code must not fall back to
    process-global browser env/config authority.
    """

    profile_home: Path
    role_id: str
    browser_config: dict[str, Any]
    scope_fingerprint: str

    @property
    def allow_private_urls(self) -> bool:
        return False

    @property
    def auto_local_for_private_urls(self) -> bool:
        return False

    @property
    def cdp_url(self) -> str:
        return ""

    @property
    def cloud_provider(self) -> None:
        return None

    @property
    def camofox_url(self) -> str:
        return ""

    @property
    def camofox_config(self) -> dict[str, Any]:
        value = self.browser_config.get("camofox", {})
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("typed browser config malformed")
        return dict(value)

    def browser_engine(self) -> str:
        value = str(self.browser_config.get("engine") or "auto").strip().lower()
        return value if value in _VALID_ENGINES else "auto"


def browser_request_authority() -> BrowserRequestAuthority | None:
    """Return typed browser authority, or ``None`` for legacy no-context paths.

    Missing profile browser config means public-only ephemeral local browser.
    Malformed typed profile config fails closed.  Positive persistent/CDP/cloud
    authority remains a material follow-up until it has an explicit
    server-bound credential/profile contract.
    """
    context, home = _bound_browser_context_and_home()
    if context is None or home is None:
        return None
    cfg = _read_profile_config(home)
    browser_cfg = cfg.get("browser", {})
    if browser_cfg is None:
        browser_cfg = {}
    if not isinstance(browser_cfg, dict):
        raise ValueError("typed browser config malformed")

    return BrowserRequestAuthority(
        profile_home=home,
        role_id=context.role_id,
        browser_config=dict(browser_cfg),
        scope_fingerprint=_browser_scope_fingerprint(context, home),
    )


def sanitize_browser_env_for_typed(
    env: dict[str, str],
    authority: BrowserRequestAuthority,
) -> dict[str, str]:
    """Strip process-global browser authority from a typed subprocess env."""
    sanitized = dict(env)
    for key in _TYPED_BROWSER_ENV_BLOCKLIST:
        sanitized.pop(key, None)

    profile_home = authority.profile_home
    cache_home = _contained_profile_subdir(profile_home, ".cache")
    config_home = _contained_profile_subdir(profile_home, ".config")
    data_home = _contained_profile_subdir(profile_home, ".local", "share")
    tmp_home = _contained_profile_subdir(profile_home, "tmp")

    sanitized["HERMES_HOME"] = str(profile_home)
    sanitized["HOME"] = str(profile_home)
    sanitized["XDG_CACHE_HOME"] = str(cache_home)
    sanitized["XDG_CONFIG_HOME"] = str(config_home)
    sanitized["XDG_DATA_HOME"] = str(data_home)
    sanitized["TMPDIR"] = str(tmp_home)
    return sanitized


def typed_browser_home(default: Path | None = None) -> Path | None:
    """Return the typed profile home for browser support paths, if any."""
    authority = browser_request_authority()
    if authority is None:
        return default
    return authority.profile_home


def browser_scope_fingerprint() -> str | None:
    """Return the typed browser scope fingerprint, or None for legacy paths."""
    authority = browser_request_authority()
    if authority is None:
        return None
    return authority.scope_fingerprint


def browser_scoped_task_key(task_id: str | None = None) -> str:
    """Return a browser session key partitioned by typed request authority.

    Legacy/no-context keys are unchanged.  Typed keys carry only an opaque
    digest, never raw profile or conversation identifiers.
    """
    logical_task = (task_id or "default").strip() or "default"
    if "::access:" in logical_task:
        return logical_task
    fingerprint = browser_scope_fingerprint()
    if fingerprint is None:
        return logical_task
    return f"{logical_task}::access:{fingerprint[:24]}"


def _bound_browser_context_and_home() -> tuple[Any | None, Path | None]:
    home = bound_profile_home()
    if home is None:
        return None, None
    try:
        from gateway.access_registry import ResolvedAccessContext
        from gateway.session_context import get_resolved_access_context

        context = get_resolved_access_context(None)
    except Exception as exc:
        raise ValueError("typed browser authority unavailable") from exc
    if not isinstance(context, ResolvedAccessContext):
        raise ValueError("malformed resolved access context")
    return context, home


def _browser_scope_fingerprint(context: Any, profile_home: Path) -> str:
    canonical = json.dumps(
        {
            "profile_id": context.profile_id,
            "conversation_scope": context.conversation_scope,
            "profile_home": str(profile_home),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _contained_profile_subdir(profile_home: Path, *parts: str) -> Path:
    path = profile_home.joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    try:
        resolved = path.resolve()
    except Exception as exc:
        raise ValueError("typed browser profile path unavailable") from exc
    if not _relative_to(resolved, profile_home):
        raise ValueError("typed browser profile path outside profile")
    return resolved


def _read_profile_config(home: Path) -> dict[str, Any]:
    config_path = home / "config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
    except Exception as exc:
        raise ValueError("typed browser config unavailable") from exc
    if cfg is None:
        return {}
    if not isinstance(cfg, dict):
        raise ValueError("typed browser config malformed")
    return dict(cfg)


def _relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


_TYPED_BROWSER_ENV_BLOCKLIST = frozenset({
    "AGENT_BROWSER_ARGS",
    "AGENT_BROWSER_CHROME_FLAGS",
    "AGENT_BROWSER_ENGINE",
    "AGENT_BROWSER_EXECUTABLE_PATH",
    "AGENT_BROWSER_PROFILE",
    "AGENT_BROWSER_USER_DATA_DIR",
    "BROWSERBASE_ADVANCED_STEALTH",
    "BROWSERBASE_API_KEY",
    "BROWSERBASE_KEEP_ALIVE",
    "BROWSERBASE_PROJECT_ID",
    "BROWSERBASE_PROXIES",
    "BROWSERBASE_SESSION_TIMEOUT",
    "BROWSER_CDP_URL",
    "BROWSER_USE_API_KEY",
    "CAMOFOX_ADOPT_EXISTING_TAB",
    "CAMOFOX_API_KEY",
    "CAMOFOX_LOOPBACK_HOST_ALIAS",
    "CAMOFOX_REWRITE_LOOPBACK_URLS",
    "CAMOFOX_SESSION_KEY",
    "CAMOFOX_URL",
    "CAMOFOX_USER_ID",
    "FIRECRAWL_API_KEY",
    "FIRECRAWL_API_URL",
    "FIRECRAWL_BROWSER_TTL",
    "HOME",
    "HERMES_REAL_HOME",
    "HERMES_ALLOW_PRIVATE_URLS",
    "PLAYWRIGHT_BROWSERS_PATH",
    "TERMINAL_ENV",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "TMPDIR",
})
