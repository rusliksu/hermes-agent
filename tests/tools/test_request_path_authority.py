"""Typed request-path cwd/backend authority for terminal, code, and file tools."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from gateway.access_registry import DeliveryTarget, ResolvedAccessContext
from gateway.session_context import bind_resolved_access_context, reset_session_vars
from tools import code_execution_tool, file_tools, terminal_tool


def _ctx(profile_id: str = "family-alpha") -> ResolvedAccessContext:
    return ResolvedAccessContext(
        principal_id=f"principal-{profile_id}",
        role_id="owner",
        profile_id=profile_id,
        conversation_scope=f"dm:{profile_id}",
        capabilities=frozenset({"terminal", "file", "execute_code"}),
        delivery_target=DeliveryTarget(
            platform="telegram",
            account="bot",
            peer_kind="dm",
            chat_id=f"chat-{profile_id}",
        ),
    )


@pytest.fixture(autouse=True)
def _clean_context():
    reset_session_vars()
    try:
        yield
    finally:
        reset_session_vars()


def _profile(monkeypatch, tmp_path: Path, terminal_cfg: dict | None) -> tuple[Path, Path]:
    root = tmp_path / "hermes"
    home = root / "profiles" / "family-alpha"
    workspace = tmp_path / "family-workspace"
    home.mkdir(parents=True)
    workspace.mkdir(exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(root))
    cfg = {"terminal": terminal_cfg} if terminal_cfg is not None else {}
    (home / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return home, workspace


def test_typed_terminal_config_ignores_poisoned_process_env(monkeypatch, tmp_path):
    workspace = tmp_path / "family-workspace"
    workspace.mkdir()
    _home, workspace = _profile(
        monkeypatch,
        tmp_path,
        {
            "backend": "local",
            "cwd": str(workspace),
            "timeout": 17,
            "docker_network": False,
            "ssh_host": "profile-ssh-host",
        },
    )
    owner = tmp_path / "owner"
    owner.mkdir()
    monkeypatch.setenv("TERMINAL_ENV", "ssh")
    monkeypatch.setenv("TERMINAL_CWD", str(owner))
    monkeypatch.setenv("TERMINAL_SSH_HOST", "owner-ssh-host")
    monkeypatch.setenv("TERMINAL_DOCKER_NETWORK", "true")

    with bind_resolved_access_context(_ctx()):
        cfg = terminal_tool._get_env_config()

    assert cfg["env_type"] == "local"
    assert cfg["cwd"] == str(workspace)
    assert cfg["timeout"] == 17
    assert cfg["ssh_host"] == "profile-ssh-host"
    assert cfg["docker_network"] is False


def test_typed_terminal_foreground_uses_profile_cwd_not_env_or_foreign_workdir(
    monkeypatch,
    tmp_path,
):
    workspace = tmp_path / "family-workspace"
    workspace.mkdir()
    _home, workspace = _profile(
        monkeypatch,
        tmp_path,
        {"backend": "local", "cwd": str(workspace)},
    )
    owner = tmp_path / "owner"
    owner.mkdir()
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    monkeypatch.setenv("TERMINAL_CWD", str(owner))
    monkeypatch.setattr(terminal_tool, "_active_environments", {})
    monkeypatch.setattr(terminal_tool, "_last_activity", {})
    monkeypatch.setattr(terminal_tool, "_task_env_overrides", {})
    monkeypatch.setattr(terminal_tool, "_session_cwd", {})
    monkeypatch.setattr(terminal_tool, "_start_cleanup_thread", lambda: None)
    monkeypatch.setattr(
        terminal_tool,
        "_check_all_guards",
        lambda *_args, **_kwargs: {"approved": True},
    )
    calls = []

    class FakeEnv:
        env = {}
        cwd = str(workspace)

        def execute(self, command, **kwargs):
            calls.append((command, kwargs))
            return {"output": "ok", "returncode": 0}

    monkeypatch.setattr(terminal_tool, "_create_environment", lambda **_kwargs: FakeEnv())

    with bind_resolved_access_context(_ctx()):
        result = json.loads(
            terminal_tool.terminal_tool(
                command="pwd",
                task_id="typed-terminal",
                workdir=str(foreign),
            )
        )

    assert result["exit_code"] == 0
    assert calls == [("pwd", {"timeout": 180, "cwd": str(workspace), "bounded_capture": True})]


def test_typed_file_and_execute_code_cwd_fall_closed_to_profile_home(
    monkeypatch,
    tmp_path,
):
    home, _workspace = _profile(
        monkeypatch,
        tmp_path,
        {"backend": "local", "cwd": "relative/not-authority"},
    )
    owner = tmp_path / "owner"
    owner.mkdir()
    monkeypatch.setenv("TERMINAL_CWD", str(owner))
    monkeypatch.setattr(terminal_tool, "_task_env_overrides", {})
    monkeypatch.setattr(terminal_tool, "_session_cwd", {})

    with bind_resolved_access_context(_ctx()):
        resolved_file = file_tools._resolve_path_for_task("notes.txt", task_id="typed-file")
        child_cwd = code_execution_tool._resolve_child_cwd(
            "project",
            str(tmp_path / "staging"),
            task_id="typed-code",
        )

    assert resolved_file == (home / "notes.txt").resolve()
    assert child_cwd == str(home)
    assert str(owner) not in str(resolved_file)
    assert child_cwd != str(owner)


@pytest.mark.parametrize(
    "cwd_value",
    ["", ".", "auto", "cwd", "relative/workspace", "/does/not/exist", "bad\x00cwd", "__FILE__"],
)
def test_typed_invalid_profile_cwd_never_uses_env(monkeypatch, tmp_path, cwd_value):
    file_path = tmp_path / "regular-file"
    file_path.write_text("not a dir", encoding="utf-8")
    configured = str(file_path) if cwd_value == "__FILE__" else cwd_value
    home, _workspace = _profile(
        monkeypatch,
        tmp_path,
        {"backend": "local", "cwd": configured},
    )
    owner = tmp_path / "owner"
    owner.mkdir()
    monkeypatch.setenv("TERMINAL_CWD", str(owner))

    with bind_resolved_access_context(_ctx()):
        cfg = terminal_tool._get_env_config()
        child_cwd = code_execution_tool._resolve_child_cwd("project", "/tmp/staging")

    assert cfg["cwd"] == str(home)
    assert child_cwd == str(home)


def test_legacy_no_context_preserves_env_behavior(monkeypatch, tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setenv("TERMINAL_CWD", str(legacy))

    cfg = terminal_tool._get_env_config()
    resolved_file = file_tools._resolve_path_for_task("notes.txt", task_id="legacy")
    child_cwd = code_execution_tool._resolve_child_cwd("project", "/tmp/staging")

    assert cfg["cwd"] == str(legacy)
    assert resolved_file == (legacy / "notes.txt").resolve()
    assert child_cwd == str(legacy)
