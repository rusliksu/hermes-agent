"""Typed Gurra path checks layered on native profile/terminal scope."""
from pathlib import Path, PurePosixPath
import posixpath


def bound_profile_home():
    from gateway.access_context import get_resolved_access_context, profile_home
    from gateway.access_context import serialize_resolved_access_context
    from hermes_constants import get_hermes_home

    context = get_resolved_access_context()
    if context is None:
        return None
    serialize_resolved_access_context(context)
    if context.profile_id == "default" and context.role_id != "owner":
        raise ValueError("invalid bound profile")
    home = profile_home(context)
    if home.is_symlink() or home.resolve() != get_hermes_home().resolve():
        raise ValueError("bound profile home mismatch")
    return home.resolve()


def restricted_profile_home():
    home = bound_profile_home()
    if home is not None:
        from gateway.access_context import get_resolved_access_context
        context = get_resolved_access_context()
        if context.role_id == "owner" and context.profile_id == "default":
            return None
    return home


def profile_local_cwd():
    home = restricted_profile_home()
    if home is None:
        return None
    from tools.terminal_scope import get_terminal_scope, terminal_env
    scope = get_terminal_scope()
    raw = "" if scope is not None and scope.get("_cwd_placeholder") == "1" else terminal_env("TERMINAL_CWD", "")
    return guard_terminal_cwd(raw or str(home), str(home))


def bound_artifact_roots(task_id="default"):
    """Profile plus declared workspace, without process-cwd or HOME fallback."""
    home = bound_profile_home()
    if home is None:
        raise ValueError("bound artifact home unavailable")
    from agent.runtime_cwd import scope_terminal_cwd
    try:
        configured = profile_local_cwd()
    except ValueError:
        from gateway.platforms.base import _docker_env_active, _translate_docker_container_media_path
        if not _docker_env_active():
            raise
        configured = _translate_docker_container_media_path(
            Path(scope_terminal_cwd()), task_id, must_exist=False,
        )
        if configured is None or not configured.is_relative_to(home):
            raise ValueError("bound Docker workspace unavailable")
    workspace = Path(configured or scope_terminal_cwd() or home).expanduser()
    workspace = (workspace if workspace.is_absolute() else home / workspace).resolve(strict=True)
    if not workspace.is_dir():
        raise ValueError("bound artifact workspace unavailable")
    roots = [home, workspace]  # Keep position 1 as the base for relative artifact paths.
    from tools.terminal_tool import get_session_cwd
    candidate = get_session_cwd(task_id)
    if isinstance(candidate, str) and candidate:
        path = Path(candidate).expanduser().resolve()
        if path.is_dir() and path.is_relative_to(workspace) and path not in roots:
            roots.append(path)
    return tuple(roots)


def guard_terminal_cwd(candidate, base, *, remote=False):
    """Normalize within the configured workspace; never dereference remote paths on host."""
    home = restricted_profile_home()
    if home is None:
        return str(candidate)
    if remote:
        root = PurePosixPath(posixpath.normpath(str(base)))
        path = PurePosixPath(str(candidate))
        path = PurePosixPath(posixpath.normpath(str(path if path.is_absolute() else root / path)))
        if not root.is_absolute() or not path.is_relative_to(root):
            raise ValueError("cwd outside profile workspace")
    else:
        root = Path(base).resolve()
        path = Path(candidate).expanduser()
        path = (path if path.is_absolute() else root / path).resolve()
        if not root.is_relative_to(home) or not path.is_relative_to(root) or not path.is_dir():
            raise ValueError("cwd outside profile workspace")
    return str(path)


def profile_read_block_error(path):
    from agent.secret_scope import is_multiplex_active
    from gateway.access_context import get_resolved_access_context

    context = get_resolved_access_context()
    if context is None and not is_multiplex_active():
        return None  # Native registry-free callers retain their existing read policy.
    try:
        from hermes_constants import get_default_hermes_root

        root = get_default_hermes_root().resolve()
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_relative_to(root):
            return None
        if context is None:
            # Multiplex without a typed registry context: the active scope's own home is the
            # authority. Another profile's subtree (and the default root for a secondary) stays
            # unaddressable.
            from hermes_constants import get_hermes_home

            current = get_hermes_home().resolve(strict=False)
            if current == root:
                parts = resolved.relative_to(root).parts
                if parts and parts[0] == "profiles":
                    return "Access denied: path belongs to another Hermes profile."
                return None
            return None if resolved.is_relative_to(current) else (
                "Access denied: path belongs to another Hermes profile.")
        if bound_profile_home() is None:
            return "Access denied: bound profile context unavailable."
        parts = resolved.relative_to(root).parts
        if not parts:
            return None
        if parts[0] == "profiles":
            if len(parts) < 2:
                return "Access denied: Hermes profile namespace is not addressable."
            target_profile = parts[1]
        else:
            target_profile = "default"
        if target_profile != context.profile_id:
            return "Access denied: path belongs to another Hermes profile."
    except (OSError, RuntimeError, ValueError):
        return "Access denied: bound profile context unavailable."
    return None