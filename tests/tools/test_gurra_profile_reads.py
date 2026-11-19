"""Profile-state reads require a trusted namespace even when the home is root."""
import json
from dataclasses import replace

import pytest

from agent import secret_scope
from agent.file_safety import get_read_block_error
from gateway.run import _profile_runtime_scope
from gateway.access_context import bind_resolved_access_context
from tests.agent.test_gurra_memory_snapshot import context, homes
from tools.file_tools import read_file_tool

_FIXTURES = (homes, context)  # imported for pytest fixture discovery, not called here


@pytest.fixture(autouse=True)
def multiplex():
    previous = secret_scope.is_multiplex_active()
    secret_scope.set_multiplex_active(True)
    try:
        yield
    finally:
        secret_scope.set_multiplex_active(previous)


def test_missing_context_cannot_read_profile_state(homes):
    marker = homes["alpha"] / "note.txt"
    marker.write_text("ALPHA_PRIVATE_FIXTURE")
    with _profile_runtime_scope(homes["alpha"]), bind_resolved_access_context(None):
        result = json.loads(read_file_tool(str(marker), task_id="untrusted-read"))
    assert result.get("error"), result
    assert "ALPHA_PRIVATE_FIXTURE" not in json.dumps(result)


def test_root_owner_reads_own_note_but_not_nested_profile_or_symlink(homes):
    root = homes["alpha"].parent.parent
    (root / "note.txt").write_text("OWNER_FIXTURE")
    foreign = homes["beta"] / "note.txt"
    foreign.write_text("BETA_PRIVATE_FIXTURE")
    link = root / "foreign.txt"
    link.symlink_to(foreign)
    owner = replace(context(), role_id="owner", profile_id="default")
    with _profile_runtime_scope(root), bind_resolved_access_context(owner):
        own = json.loads(read_file_tool(str(root / "note.txt"), task_id="owner-own"))
        assert "OWNER_FIXTURE" in own["content"]
        for path in (foreign, link):
            result = json.loads(read_file_tool(str(path), task_id="owner-foreign"))
            assert result.get("error"), result
            assert "BETA_PRIVATE_FIXTURE" not in json.dumps(result)


def test_profile_can_read_own_state_and_unscoped_project_files(homes, tmp_path):
    own = homes["alpha"] / "note.txt"
    own.write_text("ALPHA_OWN_FIXTURE")
    project = tmp_path / "project.txt"
    project.write_text("PUBLIC_PROJECT_FIXTURE")
    with _profile_runtime_scope(homes["alpha"]), bind_resolved_access_context(context()):
        result = json.loads(read_file_tool(str(own), task_id="alpha-own"))
        assert "ALPHA_OWN_FIXTURE" in result["content"]
    with bind_resolved_access_context(None):
        assert get_read_block_error(str(project)) is None


def test_registry_free_non_multiplex_read_retains_native_behavior(homes):
    marker = homes["beta"] / "note.txt"
    marker.write_text("NATIVE_FIXTURE")
    secret_scope.set_multiplex_active(False)
    with _profile_runtime_scope(homes["alpha"]), bind_resolved_access_context(None):
        result = json.loads(read_file_tool(str(marker), task_id="native-read"))
    assert "NATIVE_FIXTURE" in result["content"]
