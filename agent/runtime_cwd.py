"""Single source of truth for the agent working directory.

`TERMINAL_CWD` is the runtime carrier for the configured working directory
(design #19214/#19242: `terminal.cwd` is bridged once to `TERMINAL_CWD` at
gateway/cron startup). The local-CLI backend deliberately leaves it unset and
relies on the launch dir. Reading it in one place keeps the system prompt, the
tool surfaces, and context-file discovery agreeing on where the agent lives.

Multi-session gateways can pin a logical cwd via the `_SESSION_CWD`
contextvar; CLI/cron fall through to `TERMINAL_CWD`/launch cwd.
"""

import logging
import os
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_UNSET: Any = object()

_SESSION_CWD: ContextVar = ContextVar("HERMES_SESSION_CWD", default=_UNSET)

# The Python package/source root (this file lives at <root>/agent/runtime_cwd.py).
# When a backend is launched from, or self-spawns into, this tree (the desktop
# app default), an os.getcwd() fallback would inject this repo's contributor
# AGENTS.md as authoritative project context. Context discovery must never
# resolve here.
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_CWD_PLACEHOLDERS = frozenset({"", ".", "./", "auto", "cwd"})


def _is_install_tree(p: Path) -> bool:
    # True only when p IS the package root or sits inside it. Ancestors of the
    # package root (a user home that happens to contain the checkout, a --user
    # site-packages parent) are legitimate workspaces and must not be blocked.
    try:
        p = p.resolve()
    except Exception:
        return False
    return p == _PACKAGE_ROOT or _PACKAGE_ROOT in p.parents


def set_session_cwd(cwd: str | None) -> Token:
    """Pin the logical cwd for the current context."""
    return _SESSION_CWD.set((cwd or "").strip())


def clear_session_cwd() -> None:
    _SESSION_CWD.set("")


def _session_cwd_override() -> str:
    value = _SESSION_CWD.get()
    if value is _UNSET:
        return ""
    return str(value).strip()


def _bound_access_context() -> Any:
    try:
        from gateway.session_context import get_resolved_access_context

        return get_resolved_access_context(_UNSET)
    except Exception as exc:
        raise ValueError("resolved access context unavailable") from exc


def _bound_owner_default() -> bool:
    context = _bound_access_context()
    try:
        from gateway.access_registry import ResolvedAccessContext
    except Exception:
        return False
    return (
        isinstance(context, ResolvedAccessContext)
        and context.role_id == "owner"
        and context.profile_id == "default"
    )


def _bound_profile_home() -> Path | None:
    context = _bound_access_context()
    if context is _UNSET or context is None:
        return None
    try:
        from gateway.access_registry import ResolvedAccessContext
        from hermes_cli.profiles import (
            _get_profiles_root,
            get_profile_dir,
            normalize_profile_name,
            profile_exists,
            validate_profile_name,
        )
    except Exception as exc:
        raise ValueError("resolved access profile unavailable") from exc
    if not isinstance(context, ResolvedAccessContext):
        raise ValueError("malformed resolved access context")
    profile_id = context.profile_id
    if not isinstance(profile_id, str) or not profile_id.strip():
        raise ValueError("malformed resolved access profile")
    try:
        canonical = normalize_profile_name(profile_id)
        validate_profile_name(canonical)
    except Exception as exc:
        raise ValueError("malformed resolved access profile") from exc
    owner_default = canonical == "default" and context.role_id == "owner"
    if (
        canonical != profile_id
        or (canonical == "default" and not owner_default)
        or canonical in _CWD_PLACEHOLDERS
        or not profile_exists(canonical)
    ):
        raise ValueError("malformed resolved access profile")
    home = get_profile_dir(canonical)
    try:
        resolved = home.resolve()
        profiles_root = _get_profiles_root().resolve()
    except Exception as exc:
        raise ValueError("resolved access profile home unavailable") from exc
    if not resolved.is_dir() or (
        not owner_default
        and (resolved == profiles_root or not _relative_to(resolved, profiles_root))
    ):
        raise ValueError("resolved access profile home unavailable")
    return resolved


def bound_profile_home() -> Path | None:
    """Return the canonical home for the bound typed profile, if any."""
    return _bound_profile_home()


def bound_profile_terminal_config() -> dict[str, Any] | None:
    """Return raw terminal config for the bound typed profile, if any.

    None means legacy/no typed access context. A typed context reads only that
    profile's config.yaml and never process env or the launch profile config.
    """
    home = _bound_profile_home()
    if home is None:
        return None
    try:
        import yaml

        config_path = home / "config.yaml"
        if not config_path.exists():
            return {}
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("typed terminal config unavailable") from exc
    if cfg is None:
        cfg = {}
    if not isinstance(cfg, dict):
        raise ValueError("typed terminal config malformed")
    terminal_cfg = cfg.get("terminal") if isinstance(cfg, dict) else None
    if terminal_cfg is None:
        return {}
    if not isinstance(terminal_cfg, dict):
        raise ValueError("typed terminal config malformed")
    return dict(terminal_cfg)


def _strict_existing_absolute_dir(
    raw: Any,
    *,
    home: Path,
    label: str,
    allow_outside_home: bool = False,
) -> Path | None:
    if not isinstance(raw, str):
        raise ValueError(f"typed {label} cwd malformed")
    value = raw.strip()
    if value.lower() in _CWD_PLACEHOLDERS or "\x00" in value:
        raise ValueError(f"typed {label} cwd malformed")
    try:
        p = Path(value)
        if not p.is_absolute():
            raise ValueError(f"typed {label} cwd malformed")
        resolved = p.resolve()
        if not resolved.is_dir() or (
            not allow_outside_home and not _relative_to(resolved, home)
        ):
            raise ValueError(f"typed {label} cwd outside profile")
        return resolved
    except ValueError:
        raise
    except (OSError, RuntimeError):
        raise ValueError(f"typed {label} cwd malformed")


def _relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_bound_profile_cwd(candidate: str | None = None) -> Path | None:
    """Return the typed profile cwd, or None on legacy/no-context paths.

    The server-bound profile config may name an absolute existing
    ``terminal.cwd`` inside the resolved profile home. The exact owner/default
    context preserves its configured host cwd; every other typed profile stays
    contained in its profile home. Missing or malformed configured/candidate cwd
    values fail closed instead of falling back to process env or local cwd.
    """
    home = _bound_profile_home()
    if home is None:
        return None
    terminal_cfg = bound_profile_terminal_config() or {}
    base = home
    owner_default = _bound_owner_default()
    if "cwd" in terminal_cfg and terminal_cfg.get("cwd") is not None:
        base = (
            _strict_existing_absolute_dir(
                terminal_cfg.get("cwd"),
                home=home,
                label="configured",
                allow_outside_home=owner_default,
            )
            or home
        )

    if candidate is not None and str(candidate).strip():
        resolved = _strict_existing_absolute_dir(
            candidate,
            home=home,
            label="candidate",
            allow_outside_home=owner_default,
        )
        if not _relative_to(resolved, base):
            raise ValueError("typed candidate cwd outside configured cwd")
        return resolved
    return base


def resolve_agent_cwd() -> Path:
    typed = resolve_bound_profile_cwd()
    if typed is not None:
        return typed
    override = _session_cwd_override()
    if override:
        p = Path(override).expanduser()
        if p.is_dir():
            return p
        logger.warning("configured working directory does not exist: %s", override)
    raw = os.environ.get("TERMINAL_CWD", "").strip()
    if raw:
        p = Path(raw).expanduser()
        if p.is_dir():
            return p
        logger.warning("TERMINAL_CWD does not exist: %s", raw)
    return Path(os.getcwd())


def resolve_context_cwd() -> Path | None:
    # None means "no configured cwd": build_context_files_prompt then falls back
    # to the launch dir (os.getcwd()), correct for a local CLI launched inside a
    # real project. A configured path is validated here (previously it was passed
    # through unchecked, diverging from resolve_agent_cwd). An explicitly
    # configured path is otherwise honored verbatim — including the Hermes
    # source tree itself, which is a legitimate workspace when the user is
    # developing Hermes (per-surface policy for fallback-picked directories
    # lives in build_context_files_prompt; see #64590).
    typed = resolve_bound_profile_cwd()
    if typed is not None:
        return typed
    override = _session_cwd_override()
    if override:
        p = Path(override).expanduser()
        if not p.is_dir():
            logger.warning("configured working directory does not exist: %s", override)
        else:
            return p
        return None
    raw = os.environ.get("TERMINAL_CWD", "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_dir():
            logger.warning("TERMINAL_CWD does not exist: %s", raw)
        else:
            return p
    return None
