"""Real terminal and file tools must not leave the bound profile workspace."""
import json
from contextlib import contextmanager

import pytest

from gateway.access_context import ResolvedAccessContext, bind_resolved_access_context
from hermes_constants import reset_hermes_home_override, set_hermes_home_override


@pytest.fixture
def homes(tmp_path):
    root = tmp_path / "state"
    made = {}
    for name in ("alpha", "beta"):
        home = root / "profiles" / name
        home.mkdir(parents=True)
        (home / "config.yaml").write_text(
            f"terminal:\n  backend: local\n  cwd: {home}\n", encoding="utf-8")
        made[name] = home
    return made


@contextmanager
def scoped(home):
    token = set_hermes_home_override(str(home))
    try:
        yield
    finally:
        reset_hermes_home_override(token)


def authority(profile="alpha"):
    return ResolvedAccessContext(
        profile_id=profile,
        role_id="member",
        capabilities=frozenset({"terminal", "private_memory"}),
    )


@pytest.mark.platforms("posix")
def test_foreign_workdir_does_not_execute(homes):
    from tools import terminal_tool as terminal

    with scoped(homes["alpha"]), bind_resolved_access_context(authority()):
        result = json.loads(terminal.terminal_tool(
            "printf foreign > escaped-marker",
            workdir=str(homes["beta"]), task_id="fixture-alpha", force=True,
        ))
    assert result["exit_code"] != 0
    assert not (homes["beta"] / "escaped-marker").exists()


@pytest.mark.platforms("posix")
def test_marker_survives_rebind_and_foreign_read_is_denied(homes):
    from tools import terminal_tool as terminal
    from tools.file_tools import read_file_tool

    with scoped(homes["alpha"]), bind_resolved_access_context(authority()):
        written = json.loads(terminal.terminal_tool(
            "printf ALPHA_MARKER > marker", task_id="fixture-alpha", force=True,
        ))
        assert written["exit_code"] == 0
    with scoped(homes["beta"]), bind_resolved_access_context(authority("beta")):
        refused = json.loads(read_file_tool(str(homes["alpha"] / "marker"), task_id="fixture-beta"))
        assert refused.get("error")
        assert "ALPHA_MARKER" not in json.dumps(refused)
