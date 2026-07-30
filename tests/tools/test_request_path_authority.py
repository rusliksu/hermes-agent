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
    workspace = home / "workspace"
    home.mkdir(parents=True)
    workspace.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(root))
    cfg = {"terminal": terminal_cfg} if terminal_cfg is not None else {}
    (home / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return home, workspace


def test_typed_terminal_config_ignores_poisoned_process_env(monkeypatch, tmp_path):
    _home, workspace = _profile(
        monkeypatch,
        tmp_path,
        {
            "backend": "local",
            "timeout": 17,
            "docker_network": False,
            "ssh_host": "profile-ssh-host",
        },
    )
    (_home / "config.yaml").write_text(
        yaml.safe_dump({
            "terminal": {
                "backend": "local",
                "cwd": str(workspace),
                "timeout": 17,
                "docker_network": False,
                "ssh_host": "profile-ssh-host",
            },
        }),
        encoding="utf-8",
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


def test_typed_terminal_foreground_uses_profile_cwd_not_env(
    monkeypatch,
    tmp_path,
):
    _home, workspace = _profile(
        monkeypatch,
        tmp_path,
        {"backend": "local"},
    )
    (_home / "config.yaml").write_text(
        yaml.safe_dump({"terminal": {"backend": "local", "cwd": str(workspace)}}),
        encoding="utf-8",
    )
    owner = tmp_path / "owner"
    owner.mkdir()
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
            )
        )

    assert result["exit_code"] == 0
    assert calls == [("pwd", {"timeout": 180, "cwd": str(workspace), "bounded_capture": True})]


def test_typed_foreign_workdir_denies_before_environment_creation(
    monkeypatch,
    tmp_path,
):
    _home, workspace = _profile(
        monkeypatch,
        tmp_path,
        {"backend": "local"},
    )
    (_home / "config.yaml").write_text(
        yaml.safe_dump({"terminal": {"backend": "local", "cwd": str(workspace)}}),
        encoding="utf-8",
    )
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    monkeypatch.setattr(terminal_tool, "_task_env_overrides", {})
    monkeypatch.setattr(terminal_tool, "_session_cwd", {})
    created = False

    def _create(**_kwargs):
        nonlocal created
        created = True
        raise AssertionError("environment must not be created")

    monkeypatch.setattr(terminal_tool, "_create_environment", _create)
    with bind_resolved_access_context(_ctx()):
        result = json.loads(
            terminal_tool.terminal_tool(
                command="pwd",
                task_id="typed-terminal-deny",
                workdir=str(foreign),
            )
        )

    assert result["status"] == "error"
    assert "typed candidate cwd" in result["error"]
    assert created is False


@pytest.mark.parametrize("workdir_value", ["relative/workspace", "__SYMLINK_ESCAPE__"])
def test_typed_malformed_candidate_workdir_denies_before_environment_creation(
    monkeypatch,
    tmp_path,
    workdir_value,
):
    _home, workspace = _profile(
        monkeypatch,
        tmp_path,
        {"backend": "local"},
    )
    (_home / "config.yaml").write_text(
        yaml.safe_dump({"terminal": {"backend": "local", "cwd": str(workspace)}}),
        encoding="utf-8",
    )
    if workdir_value == "__SYMLINK_ESCAPE__":
        outside = tmp_path / "outside"
        outside.mkdir()
        link = workspace / "escape-cwd"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("filesystem does not support directory symlinks")
        workdir = str(link)
    else:
        workdir = workdir_value
    monkeypatch.setattr(terminal_tool, "_task_env_overrides", {})
    monkeypatch.setattr(terminal_tool, "_session_cwd", {})
    created = False

    def _create(**_kwargs):
        nonlocal created
        created = True
        raise AssertionError("environment must not be created")

    monkeypatch.setattr(terminal_tool, "_create_environment", _create)
    with bind_resolved_access_context(_ctx()):
        result = json.loads(
            terminal_tool.terminal_tool(
                command="pwd",
                task_id="typed-candidate-deny",
                workdir=workdir,
            )
        )

    assert result["status"] == "error"
    assert "typed candidate cwd" in result["error"]
    assert created is False


@pytest.mark.parametrize(
    "cwd_value",
    ["", ".", "auto", "cwd", "relative/workspace", "/does/not/exist", "bad\x00cwd", "__FILE__"],
)
def test_typed_invalid_profile_cwd_fails_closed(monkeypatch, tmp_path, cwd_value):
    file_path = tmp_path / "regular-file"
    file_path.write_text("not a dir", encoding="utf-8")
    configured = str(file_path) if cwd_value == "__FILE__" else cwd_value
    _home, _workspace = _profile(
        monkeypatch,
        tmp_path,
        {"backend": "local", "cwd": configured},
    )
    owner = tmp_path / "owner"
    owner.mkdir()
    monkeypatch.setenv("TERMINAL_CWD", str(owner))

    with bind_resolved_access_context(_ctx()):
        with pytest.raises(ValueError):
            terminal_tool._get_env_config()
        with pytest.raises(ValueError):
            code_execution_tool._resolve_child_cwd("project", "/tmp/staging")


@pytest.mark.parametrize(
    "profile_id",
    [
        "default",
        "auto",
        "cwd",
        "/absolute",
        "../traversal",
        "family/alpha",
        "family\x00alpha",
        "Family-Alpha",
        "missing-profile",
    ],
)
def test_typed_malformed_profile_id_fails_closed(monkeypatch, tmp_path, profile_id):
    _home, workspace = _profile(
        monkeypatch,
        tmp_path,
        {"backend": "local"},
    )
    (_home / "config.yaml").write_text(
        yaml.safe_dump({"terminal": {"backend": "local", "cwd": str(workspace)}}),
        encoding="utf-8",
    )
    with bind_resolved_access_context(_ctx(profile_id)):
        with pytest.raises(ValueError):
            terminal_tool._get_env_config()


@pytest.mark.parametrize("terminal_text", ["terminal: [", "[]\n", "terminal: not-a-map\n"])
def test_typed_malformed_terminal_config_cannot_select_local(monkeypatch, tmp_path, terminal_text):
    home, _workspace = _profile(monkeypatch, tmp_path, {"backend": "local"})
    (home / "config.yaml").write_text(terminal_text, encoding="utf-8")
    created = False

    def _create(**_kwargs):
        nonlocal created
        created = True
        raise AssertionError("environment must not be created")

    monkeypatch.setattr(terminal_tool, "_create_environment", _create)
    with bind_resolved_access_context(_ctx()):
        with pytest.raises(ValueError):
            terminal_tool._get_env_config()
        result = json.loads(terminal_tool.terminal_tool("pwd", task_id="bad-config"))

    assert result["status"] == "error"
    assert created is False


@pytest.mark.parametrize("backend", [None, "", "bogus"])
def test_typed_missing_or_unknown_backend_cannot_default_local(monkeypatch, tmp_path, backend):
    home, workspace = _profile(
        monkeypatch,
        tmp_path,
        {"backend": "local", "cwd": str(tmp_path / "unused")},
    )
    terminal_cfg = {"cwd": str(workspace)}
    if backend is not None:
        terminal_cfg["backend"] = backend
    (home / "config.yaml").write_text(
        yaml.safe_dump({"terminal": terminal_cfg}),
        encoding="utf-8",
    )
    monkeypatch.setenv("TERMINAL_ENV", "local")
    with bind_resolved_access_context(_ctx()):
        with pytest.raises(ValueError):
            terminal_tool._get_env_config()


def test_execute_code_malformed_typed_config_returns_before_subprocess(monkeypatch, tmp_path):
    _home, _workspace = _profile(monkeypatch, tmp_path, {"backend": "bogus"})
    popen_called = False

    def _popen(*_args, **_kwargs):
        nonlocal popen_called
        popen_called = True
        raise AssertionError("subprocess must not start")

    monkeypatch.setattr(code_execution_tool.subprocess, "Popen", _popen)
    with bind_resolved_access_context(_ctx()):
        result = json.loads(code_execution_tool.execute_code("print('hi')"))

    assert result["status"] == "error"
    assert "typed terminal backend unknown" in result["error"]
    assert popen_called is False


def test_typed_docker_backend_selected_from_bound_config(monkeypatch, tmp_path):
    _home, workspace = _profile(
        monkeypatch,
        tmp_path,
        {"backend": "docker", "docker_network": False},
    )
    (_home / "config.yaml").write_text(
        yaml.safe_dump({
            "terminal": {
                "backend": "docker",
                "cwd": str(workspace),
                "docker_network": False,
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("TERMINAL_ENV", "local")

    with bind_resolved_access_context(_ctx()):
        cfg = terminal_tool._get_env_config()

    assert cfg["env_type"] == "docker"
    assert cfg["docker_network"] is False


def test_typed_file_and_execute_code_cwd_use_profile_path(monkeypatch, tmp_path):
    _home, workspace = _profile(
        monkeypatch,
        tmp_path,
        {"backend": "local"},
    )
    (_home / "config.yaml").write_text(
        yaml.safe_dump({"terminal": {"backend": "local", "cwd": str(workspace)}}),
        encoding="utf-8",
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

    assert resolved_file == (workspace / "notes.txt").resolve()
    assert child_cwd == str(workspace)
    assert str(owner) not in str(resolved_file)
    assert child_cwd != str(owner)


def test_typed_write_allows_profile_path_and_denies_foreign_absolute(monkeypatch, tmp_path):
    _home, workspace = _profile(
        monkeypatch,
        tmp_path,
        {"backend": "local"},
    )
    (_home / "config.yaml").write_text(
        yaml.safe_dump({"terminal": {"backend": "local", "cwd": str(workspace)}}),
        encoding="utf-8",
    )
    foreign = tmp_path / "foreign.txt"

    with bind_resolved_access_context(_ctx()):
        allowed = json.loads(file_tools.write_file_tool("notes.txt", "ok\n", task_id="typed-write"))
        denied = json.loads(file_tools.write_file_tool(str(foreign), "no\n", task_id="typed-write"))

    assert allowed.get("error") in (None, "")
    assert (workspace / "notes.txt").read_text(encoding="utf-8") == "ok\n"
    assert "outside profile boundary" in denied["error"]
    assert not foreign.exists()


def test_typed_write_denies_symlink_escape(monkeypatch, tmp_path):
    _home, workspace = _profile(
        monkeypatch,
        tmp_path,
        {"backend": "local"},
    )
    (_home / "config.yaml").write_text(
        yaml.safe_dump({"terminal": {"backend": "local", "cwd": str(workspace)}}),
        encoding="utf-8",
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    link = workspace / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("filesystem does not support directory symlinks")

    with bind_resolved_access_context(_ctx()):
        denied = json.loads(file_tools.write_file_tool("escape/out.txt", "no\n", task_id="typed-link"))

    assert "outside profile boundary" in denied["error"]
    assert not (outside / "out.txt").exists()


def test_typed_patch_denies_foreign_absolute_before_mutation(monkeypatch, tmp_path):
    _home, workspace = _profile(
        monkeypatch,
        tmp_path,
        {"backend": "local"},
    )
    (_home / "config.yaml").write_text(
        yaml.safe_dump({"terminal": {"backend": "local", "cwd": str(workspace)}}),
        encoding="utf-8",
    )
    target = tmp_path / "foreign.py"
    target.write_text("old\n", encoding="utf-8")

    with bind_resolved_access_context(_ctx()):
        denied = json.loads(
            file_tools.patch_tool(
                mode="replace",
                path=str(target),
                old_string="old",
                new_string="new",
                task_id="typed-patch",
            )
        )

    assert "outside profile boundary" in denied["error"]
    assert target.read_text(encoding="utf-8") == "old\n"


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
