"""Document publication and write admission share real typed roots and capabilities."""
import json
import os
from contextlib import contextmanager
from dataclasses import replace

import pytest

from gateway.access_context import DeliveryTarget, ResolvedAccessContext, bind_resolved_access_context
from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from tools.artifact_delivery_tool import _deliver_artifact, validate_bound_artifact_output
from tools.file_tools import patch_tool, write_file_tool


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


def context(profile="alpha"):
    return ResolvedAccessContext(
        principal_id="person:1001",
        role_id="family",
        profile_id=profile,
        conversation_scope="telegram:1001",
        capabilities=frozenset({"terminal", "private_memory", "documents"}),
        delivery_target=DeliveryTarget("telegram", "account", "dm", "1001"),
    )


@pytest.fixture
def access():
    return context()


def _bindings(home, ctx, monkeypatch):
    monkeypatch.setenv("TERMINAL_CWD", str(home))
    return scoped(home), bind_resolved_access_context(ctx)


def test_prepared_status_and_relative_workspace_path(homes, access, monkeypatch):
    artifact = homes["alpha"] / "report.csv"
    artifact.write_text("fixture,value\n")
    ctx_scope, ctx_bind = _bindings(homes["alpha"], access, monkeypatch)
    with ctx_scope, ctx_bind:
        result = json.loads(_deliver_artifact({"path": "report.csv"}))
        assert result == {
            "success": True,
            "status": "ready_for_delivery",
            "file_name": "report.csv",
            "media_tag": "MEDIA:" + str(artifact),
        }
        assert validate_bound_artifact_output(str(homes["alpha"] / "new.csv"), "fixture") is None


@pytest.mark.parametrize("invalid", ["foreign", "symlink", "capability", "destination", "home", "media"])
def test_invalid_artifact_never_becomes_a_delivery_candidate(homes, access, invalid, monkeypatch):
    own = homes["alpha"] / "report.csv"
    own.write_text("fixture,value\n")
    foreign = homes["beta"] / "report.csv"
    foreign.write_text("foreign,value\n")
    args = {"path": str(own)}
    home = homes["alpha"]
    if invalid == "foreign":
        args["path"] = str(foreign)
    elif invalid == "symlink":
        link = home / "link.csv"
        try:
            link.symlink_to(foreign)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unavailable on this host")
        args["path"] = str(link)
    elif invalid == "capability":
        access = replace(access, capabilities=frozenset())
    elif invalid == "destination":
        args["chat_id"] = "2002"
    elif invalid == "home":
        home = homes["beta"]
    elif invalid == "media":
        image = home / "image.png"
        image.write_bytes(b"fixture")
        args["path"] = str(image)
    ctx_scope, ctx_bind = _bindings(home, access, monkeypatch)
    with ctx_scope, ctx_bind:
        result = json.loads(_deliver_artifact(args))
    assert result["success"] is False
    assert "media_tag" not in result


def test_foreign_document_write_and_v4a_bypass_refused_before_execution(homes, access, monkeypatch):
    path = homes["beta"] / "new.csv"
    patch = f"*** Begin Patch\n*** Add File: {path}\n+foreign,value\n*** End Patch"
    ctx_scope, ctx_bind = _bindings(homes["alpha"], access, monkeypatch)
    with ctx_scope, ctx_bind:
        write = json.loads(write_file_tool(str(path), "foreign,value\n", cross_profile=True))
        replaced = json.loads(patch_tool(mode="patch", patch=patch, cross_profile=True))
    assert "bound_artifact_output_rejected:" in write["error"]
    assert "bound_artifact_output_rejected:" in replaced["error"]
    assert not path.exists()
