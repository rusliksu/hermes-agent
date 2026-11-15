"""Typed Gurra paths must refuse foreign profiles while native callers keep working."""
from contextlib import contextmanager

import pytest

from agent.access_paths import bound_profile_home, guard_terminal_cwd, profile_read_block_error
from gateway.access_context import DeliveryTarget, ResolvedAccessContext, bind_resolved_access_context
from hermes_constants import reset_hermes_home_override, set_hermes_home_override


@pytest.fixture
def homes(tmp_path):
    root = tmp_path / "state"
    made = {}
    for name in ("alpha", "beta"):
        home = root / "profiles" / name
        home.mkdir(parents=True)
        made[name] = home
    return made


@contextmanager
def scoped(home):
    token = set_hermes_home_override(str(home))
    try:
        yield
    finally:
        reset_hermes_home_override(token)


def authority(profile="alpha", role="member"):
    return ResolvedAccessContext(
        principal_id="person:1001",
        role_id=role,
        profile_id=profile,
        conversation_scope="telegram:1001",
        capabilities=frozenset({"terminal", "private_memory"}),
        delivery_target=DeliveryTarget("telegram", "account", "dm", "1001"),
    )


def test_registry_free_callers_keep_their_cwd(homes):
    with scoped(homes["alpha"]):
        assert guard_terminal_cwd(str(homes["beta"]), str(homes["alpha"])) == str(homes["beta"])


def test_foreign_directory_is_refused(homes):
    with scoped(homes["alpha"]), bind_resolved_access_context(authority()):
        with pytest.raises(ValueError):
            guard_terminal_cwd(str(homes["beta"]), str(homes["alpha"]))


def test_workspace_inside_profile_is_allowed(homes):
    workspace = homes["alpha"] / "ws"
    workspace.mkdir()
    with scoped(homes["alpha"]), bind_resolved_access_context(authority()):
        assert guard_terminal_cwd("ws", str(homes["alpha"])) == str(workspace.resolve())


def test_relative_escape_is_refused(homes):
    with scoped(homes["alpha"]), bind_resolved_access_context(authority()):
        with pytest.raises(ValueError):
            guard_terminal_cwd("../beta", str(homes["alpha"]))


def test_remote_workspace_root_is_posix(homes):
    with scoped(homes["alpha"]), bind_resolved_access_context(authority()):
        assert guard_terminal_cwd("project", "/workspace", remote=True) == "/workspace/project"
        with pytest.raises(ValueError):
            guard_terminal_cwd("/etc", "/workspace", remote=True)


def test_bound_home_must_match_the_active_scope(homes):
    with scoped(homes["beta"]), bind_resolved_access_context(authority("alpha")):
        with pytest.raises(ValueError):
            bound_profile_home()


def test_foreign_profile_files_are_unreadable(homes):
    secret = homes["beta"] / "auth.json"
    secret.write_text("BETA_SECRET", encoding="utf-8")
    with scoped(homes["alpha"]), bind_resolved_access_context(authority()):
        blocked = profile_read_block_error(secret)
    assert blocked and "another Hermes profile" in blocked


def test_own_profile_files_stay_readable(homes):
    own = homes["alpha"] / "notes.txt"
    own.write_text("mine", encoding="utf-8")
    with scoped(homes["alpha"]), bind_resolved_access_context(authority()):
        assert profile_read_block_error(own) is None


def test_agent_context_cwd_cannot_use_another_profile(homes):
    from agent import runtime_cwd

    workspace = homes["alpha"] / "ws"
    workspace.mkdir()
    with scoped(homes["alpha"]), bind_resolved_access_context(authority()):
        token = runtime_cwd.set_session_cwd(str(homes["beta"]))
        try:
            with pytest.raises(ValueError):
                runtime_cwd.resolve_context_cwd()
        finally:
            runtime_cwd.reset_session_cwd(token)
        token = runtime_cwd.set_session_cwd(str(workspace))
        try:
            assert runtime_cwd.resolve_context_cwd() == workspace.resolve()
        finally:
            runtime_cwd.reset_session_cwd(token)
