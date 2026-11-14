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
    from gateway.access_context import get_resolved_access_context
    if get_resolved_access_context() is None:
        return None  # Native registry-free callers retain their existing read policy.
    try:
        from gateway.access_context import home_root

        home = bound_profile_home()
        root = home_root().resolve()
        resolved = Path(path).resolve()
        if resolved.is_relative_to(root) and not resolved.is_relative_to(home):
            return "Access denied: path belongs to another Hermes profile."
    except Exception:
        return "Access denied: bound profile context unavailable."
    return None
