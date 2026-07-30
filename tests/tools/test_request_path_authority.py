"""Typed request-path cwd/backend authority for terminal, code, and file tools."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from agent import runtime_browser
from gateway.access_registry import DeliveryTarget, ResolvedAccessContext
from gateway.session_context import bind_resolved_access_context, reset_session_vars
from tools import browser_camofox, browser_camofox_state, browser_tool, code_execution_tool, file_tools, terminal_tool
from tools import url_safety


def _ctx(
    profile_id: str = "family-alpha",
    *,
    role_id: str = "owner",
    capabilities: frozenset[str] | None = None,
) -> ResolvedAccessContext:
    return ResolvedAccessContext(
        principal_id=f"principal-{profile_id}",
        role_id=role_id,
        profile_id=profile_id,
        conversation_scope=f"dm:{profile_id}",
        capabilities=capabilities or frozenset({"terminal", "file", "execute_code"}),
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


def _browser_profile(
    monkeypatch,
    tmp_path: Path,
    browser_cfg: dict | None = None,
    *,
    profile_id: str = "family-alpha",
) -> Path:
    root = tmp_path / "hermes"
    home = root / "profiles" / profile_id
    home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(root))
    cfg: dict = {"terminal": {"backend": "local"}}
    if browser_cfg is not None:
        cfg["browser"] = browser_cfg
    (home / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return home


def _socket_answer(ip: str):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]


def test_typed_url_safety_ignores_poisoned_allow_private_env(monkeypatch, tmp_path):
    _browser_profile(monkeypatch, tmp_path, {"allow_private_urls": True})
    monkeypatch.setenv("HERMES_ALLOW_PRIVATE_URLS", "true")
    monkeypatch.setattr(url_safety, "_reset_allow_private_cache", url_safety._reset_allow_private_cache)
    url_safety._reset_allow_private_cache()
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: _socket_answer("93.184.216.34"))

    with bind_resolved_access_context(
        _ctx(role_id="family_sandbox", capabilities=frozenset({"browser"}))
    ):
        assert url_safety.is_safe_url("https://example.com/") is True

    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: _socket_answer("10.10.0.5"))
    with bind_resolved_access_context(
        _ctx(role_id="family_sandbox", capabilities=frozenset({"browser"}))
    ):
        assert url_safety.is_safe_url("https://example.test/") is False


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8080/",
        "http://10.0.0.1/",
        "http://169.254.10.20/",
        "http://169.254.169.254/latest/meta-data/",
    ],
)
def test_typed_browser_denies_private_urls_before_browser(monkeypatch, tmp_path, url):
    _browser_profile(monkeypatch, tmp_path, {})
    monkeypatch.setenv("HERMES_ALLOW_PRIVATE_URLS", "true")
    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setenv("BROWSER_CDP_URL", "http://127.0.0.1:9222")
    monkeypatch.setenv("CAMOFOX_URL", "http://127.0.0.1:9377")
    monkeypatch.setattr(browser_tool, "_run_browser_command", lambda *_args, **_kwargs: pytest.fail("browser must not start"))

    with bind_resolved_access_context(
        _ctx(role_id="family_sandbox", capabilities=frozenset({"browser"}))
    ):
        result = json.loads(browser_tool.browser_navigate(url, task_id="typed-private-deny"))

    assert result["success"] is False
    assert "metadata" in result["error"] or "private or internal" in result["error"]


def test_typed_browser_public_navigation_uses_profile_config_not_env(monkeypatch, tmp_path):
    home = _browser_profile(monkeypatch, tmp_path, {"engine": "chrome"})
    owner = tmp_path / "owner"
    owner_win = r"C:\Users\Owner"
    foreign_args = "--user-data-dir=/tmp/owner-browser"
    owner.mkdir()
    monkeypatch.setenv("HOME", str(owner))
    monkeypatch.setenv("USERPROFILE", owner_win)
    monkeypatch.setenv("LOCALAPPDATA", owner_win + r"\AppData\Local")
    monkeypatch.setenv("APPDATA", owner_win + r"\AppData\Roaming")
    monkeypatch.setenv("HOMEDRIVE", "C:")
    monkeypatch.setenv("HOMEPATH", r"\Users\Owner")
    monkeypatch.setenv("HERMES_REAL_HOME", str(owner))
    monkeypatch.setenv("XDG_CACHE_HOME", str(owner / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(owner / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(owner / "data"))
    monkeypatch.setenv("TMPDIR", str(owner / "tmp"))
    monkeypatch.setenv("PATH", os.pathsep.join([str(owner / "bin"), "/usr/bin"]))
    monkeypatch.setenv("BROWSER_CDP_URL", "http://127.0.0.1:9222")
    monkeypatch.setenv("CAMOFOX_URL", "http://127.0.0.1:9377")
    monkeypatch.setenv("BROWSER_USE_API_KEY", "synthetic-browser-key")
    monkeypatch.setenv("AGENT_BROWSER_ENGINE", "lightpanda")
    monkeypatch.setenv("AGENT_BROWSER_ARGS", foreign_args)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(owner))
    monkeypatch.setenv("AGENT_BROWSER_EXECUTABLE_PATH", str(owner / "chrome"))
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: _socket_answer("93.184.216.34"))
    monkeypatch.setattr(browser_tool, "_find_agent_browser", lambda validate=True: "agent-browser")
    monkeypatch.setattr(browser_tool, "_chromium_installed", lambda: True)
    monkeypatch.setattr(browser_tool, "_maybe_autoinstall_chromium", lambda: False)
    monkeypatch.setattr(browser_tool, "_needs_chromium_sandbox_bypass", lambda: False)
    monkeypatch.setattr(browser_tool, "_socket_safe_tmpdir", lambda: str(tmp_path))
    monkeypatch.setattr(browser_tool, "_write_owner_pid", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(browser_tool, "_start_browser_cleanup_thread", lambda: None)
    monkeypatch.setattr(browser_tool, "BROWSER_SESSION_INACTIVITY_TIMEOUT", 30)
    captured: dict = {}

    class FakeProc:
        returncode = 0

        def __init__(self, cmd, stdout, stderr, env, **_kwargs):
            captured["cmd"] = cmd
            captured["env"] = dict(env)
            os.write(stdout, b'{"success": true, "data": {"url": "https://example.com/", "title": "Example"}}\n')
            os.write(stderr, b"")

        def wait(self, timeout=None):
            return 0

        def kill(self):
            raise AssertionError("process should not be killed")

    monkeypatch.setattr(subprocess, "Popen", FakeProc)

    with bind_resolved_access_context(
        _ctx(role_id="family_sandbox", capabilities=frozenset({"browser"}))
    ):
        result = json.loads(browser_tool.browser_navigate("https://example.com/", task_id="typed-public"))
    browser_tool._active_sessions.clear()
    browser_tool._last_active_session_key.clear()

    assert result["success"] is True
    assert "--session" in captured["cmd"]
    assert "--cdp" not in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--engine") + 1] == "chrome"
    assert captured["env"]["HERMES_HOME"] == str(home)
    assert captured["env"]["HOME"] == str(home)
    assert captured["env"]["USERPROFILE"] == str(home)
    assert captured["env"]["LOCALAPPDATA"] == str(home / "AppData" / "Local")
    assert captured["env"]["APPDATA"] == str(home / "AppData" / "Roaming")
    assert captured["env"]["XDG_CACHE_HOME"] == str(home / ".cache")
    assert captured["env"]["XDG_CONFIG_HOME"] == str(home / ".config")
    assert captured["env"]["XDG_DATA_HOME"] == str(home / ".local" / "share")
    assert captured["env"]["TMPDIR"] == str(home / "tmp")
    assert "HOMEDRIVE" not in captured["env"]
    assert "HOMEPATH" not in captured["env"]
    assert captured["env"].get("AGENT_BROWSER_ARGS") != foreign_args
    for key in (
        "BROWSER_CDP_URL",
        "CAMOFOX_URL",
        "BROWSER_USE_API_KEY",
        "PLAYWRIGHT_BROWSERS_PATH",
        "AGENT_BROWSER_EXECUTABLE_PATH",
        "HERMES_REAL_HOME",
    ):
        assert key not in captured["env"]
    for value in captured["env"].values():
        assert str(owner) not in str(value)
        assert owner_win not in str(value)


def test_typed_browser_env_denies_symlinked_profile_subdir_before_outside_mkdir(
    monkeypatch,
    tmp_path,
):
    home = _browser_profile(monkeypatch, tmp_path, {"engine": "chrome"})
    outside = tmp_path / "outside"
    outside.mkdir()
    link = home / ".local"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("filesystem does not support directory symlinks")

    with bind_resolved_access_context(
        _ctx(role_id="family_sandbox", capabilities=frozenset({"browser"}))
    ):
        authority = runtime_browser.browser_request_authority()
        assert authority is not None
        with pytest.raises(ValueError):
            runtime_browser.sanitize_browser_env_for_typed({}, authority)

    assert not (outside / "share").exists()


def test_typed_windows_chromium_discovery_uses_profile_local_paths(
    monkeypatch,
    tmp_path,
):
    home = _browser_profile(monkeypatch, tmp_path, {"engine": "chrome"})
    owner_win = r"C:\Users\Owner"
    profile_chromium = home / "AppData" / "Local" / "ms-playwright" / "chromium-123"
    profile_chromium.mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", owner_win)
    monkeypatch.setenv("LOCALAPPDATA", owner_win + r"\AppData\Local")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(browser_tool.shutil, "which", lambda *_args, **_kwargs: None)
    browser_tool._cached_chromium_installed = None

    with bind_resolved_access_context(
        _ctx(role_id="family_sandbox", capabilities=frozenset({"browser"}))
    ):
        roots = browser_tool._chromium_search_roots()
        assert browser_tool._chromium_installed() is True

    joined = "\n".join(roots)
    assert owner_win not in joined
    assert str(home / "AppData" / "Local" / "ms-playwright") in roots


def test_typed_browser_discovery_skips_process_path_owner_locations(
    monkeypatch,
    tmp_path,
):
    _browser_profile(monkeypatch, tmp_path, {"engine": "chrome"})
    owner = tmp_path / "owner"
    owner.mkdir()
    owner_browser = str(owner / "agent-browser")
    calls = []

    def fake_which(name, path=None):
        calls.append((name, path))
        if path is None and name in {"agent-browser", "npx"}:
            return owner_browser
        return None

    monkeypatch.setenv("PATH", str(owner))
    monkeypatch.setattr(browser_tool.shutil, "which", fake_which)
    monkeypatch.setattr(browser_tool, "_cached_agent_browser", owner_browser)
    monkeypatch.setattr(browser_tool, "_agent_browser_resolved", True)

    with bind_resolved_access_context(
        _ctx(role_id="family_sandbox", capabilities=frozenset({"browser"}))
    ):
        with pytest.raises(FileNotFoundError):
            browser_tool._find_agent_browser(validate=False)

    assert calls
    assert all(path is not None for _name, path in calls)
    assert all(str(owner) not in str(path) for _name, path in calls)


def test_typed_browser_session_keys_partition_same_task_id(monkeypatch, tmp_path):
    _browser_profile(monkeypatch, tmp_path, {"engine": "chrome"}, profile_id="family-alpha")
    _browser_profile(monkeypatch, tmp_path, {"engine": "chrome"}, profile_id="family-beta")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: _socket_answer("93.184.216.34"))
    browser_tool._active_sessions.clear()
    browser_tool._last_active_session_key.clear()

    with bind_resolved_access_context(
        _ctx("family-alpha", role_id="family_sandbox", capabilities=frozenset({"browser"}))
    ):
        key_a = browser_tool._navigation_session_key("same-task", "https://example.com/")
        browser_tool._active_sessions[key_a] = {
            "session_name": "alpha",
            "session_key": key_a,
            "owner_task_id": key_a,
        }
        browser_tool._last_active_session_key[key_a] = key_a
        assert browser_tool._last_session_key(key_a) == key_a

    with bind_resolved_access_context(
        _ctx("family-beta", role_id="family_sandbox", capabilities=frozenset({"browser"}))
    ):
        key_b = browser_tool._navigation_session_key("same-task", "https://example.com/")
        assert key_b != key_a
        assert browser_tool._last_session_key(key_b) == key_b
        assert browser_tool._active_sessions[key_a]["session_name"] == "alpha"

    browser_tool._active_sessions.clear()
    browser_tool._last_active_session_key.clear()


def test_typed_camofox_sessions_partition_same_task_id(monkeypatch, tmp_path):
    _browser_profile(
        monkeypatch,
        tmp_path,
        {"camofox": {"managed_persistence": True}},
        profile_id="family-alpha",
    )
    _browser_profile(
        monkeypatch,
        tmp_path,
        {"camofox": {"managed_persistence": True}},
        profile_id="family-beta",
    )
    browser_camofox._sessions.clear()

    with bind_resolved_access_context(
        _ctx("family-alpha", role_id="family_sandbox", capabilities=frozenset({"browser"}))
    ):
        session_a = browser_camofox._get_session("same-task")

    with bind_resolved_access_context(
        _ctx("family-beta", role_id="family_sandbox", capabilities=frozenset({"browser"}))
    ):
        session_b = browser_camofox._get_session("same-task")

    assert session_a is not session_b
    assert session_a["user_id"] != session_b["user_id"]
    assert session_a["session_key"] != session_b["session_key"]
    assert len(browser_camofox._sessions) == 2
    browser_camofox._sessions.clear()


def test_typed_browser_malformed_config_fails_closed_before_browser(monkeypatch, tmp_path):
    home = _browser_profile(monkeypatch, tmp_path, {})
    (home / "config.yaml").write_text("browser: [", encoding="utf-8")
    monkeypatch.setattr(browser_tool, "_run_browser_command", lambda *_args, **_kwargs: pytest.fail("browser must not start"))

    with bind_resolved_access_context(
        _ctx(role_id="family_sandbox", capabilities=frozenset({"browser"}))
    ):
        result = json.loads(browser_tool.browser_navigate("https://example.com/", task_id="bad-browser-cfg"))

    assert result["success"] is False
    assert "typed browser authority denied" in result["error"]


def test_typed_camofox_ignores_env_identity_and_uses_profile_state(monkeypatch, tmp_path):
    home = _browser_profile(monkeypatch, tmp_path, {"camofox": {"managed_persistence": True}})
    monkeypatch.setenv("CAMOFOX_URL", "http://127.0.0.1:9377")
    monkeypatch.setenv("CAMOFOX_API_KEY", "synthetic-camofox-key")
    monkeypatch.setenv("CAMOFOX_USER_ID", "owner-user")
    monkeypatch.setenv("CAMOFOX_SESSION_KEY", "owner-session")
    monkeypatch.setenv("CAMOFOX_REWRITE_LOOPBACK_URLS", "true")

    with bind_resolved_access_context(
        _ctx(role_id="family_sandbox", capabilities=frozenset({"browser"}))
    ):
        assert browser_camofox.is_camofox_mode() is False
        assert browser_camofox._auth_headers() == {}
        assert browser_camofox._camofox_identity_override("task", browser_camofox._get_camofox_config()) is None
        state_dir = browser_camofox_state.get_camofox_state_dir()
        identity = browser_camofox_state.get_camofox_identity("task")

    assert state_dir == home / "browser_auth" / "camofox"
    assert str(state_dir).startswith(str(home))
    assert identity["user_id"] != "owner-user"
    assert identity["session_key"] != "owner-session"


def test_camofox_scroll_denies_private_page_before_action(monkeypatch):
    monkeypatch.setattr(
        browser_camofox,
        "_get_session",
        lambda _task_id: {"tab_id": "tab", "user_id": "user"},
    )
    monkeypatch.setattr(browser_tool, "_eval_ssrf_guard_active", lambda _task_id: True)
    monkeypatch.setattr(
        browser_tool,
        "_camofox_current_page_private_url",
        lambda _tab_id, _user_id: "http://169.254.169.254/latest/meta-data/",
    )
    monkeypatch.setattr(browser_camofox, "_post", lambda *_args, **_kwargs: pytest.fail("scroll must not run"))

    result = json.loads(browser_camofox.camofox_scroll("down", task_id="task"))

    assert result["success"] is False
    assert "private or internal" in result["error"]


def test_camofox_back_denies_private_landing_without_url_result(monkeypatch):
    monkeypatch.setattr(
        browser_camofox,
        "_get_session",
        lambda _task_id: {"tab_id": "tab", "user_id": "user"},
    )
    monkeypatch.setattr(browser_tool, "_eval_ssrf_guard_active", lambda _task_id: True)
    monkeypatch.setattr(
        browser_camofox,
        "_post",
        lambda *_args, **_kwargs: {"url": "http://169.254.169.254/latest/meta-data/"},
    )

    result = json.loads(browser_camofox.camofox_back(task_id="task"))

    assert result["success"] is False
    assert "private or internal" in result["error"]
    assert "url" not in result


def test_browser_snapshot_denies_private_page_before_snapshot(monkeypatch):
    monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: False)
    monkeypatch.setattr(browser_tool, "_eval_ssrf_guard_active", lambda _task_id: True)
    monkeypatch.setattr(
        browser_tool,
        "_current_page_private_url",
        lambda _task_id: "http://169.254.169.254/latest/meta-data/",
    )
    monkeypatch.setattr(browser_tool, "_run_browser_command", lambda *_args, **_kwargs: pytest.fail("snapshot must not run"))

    result = json.loads(browser_tool.browser_snapshot(task_id="task"))

    assert result["success"] is False
    assert "private or internal" in result["error"]


def test_browser_get_images_denies_private_page_before_eval(monkeypatch):
    monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: False)
    monkeypatch.setattr(browser_tool, "_eval_ssrf_guard_active", lambda _task_id: True)
    monkeypatch.setattr(
        browser_tool,
        "_current_page_private_url",
        lambda _task_id: "http://169.254.169.254/latest/meta-data/",
    )
    monkeypatch.setattr(browser_tool, "_run_browser_command", lambda *_args, **_kwargs: pytest.fail("image eval must not run"))

    result = json.loads(browser_tool.browser_get_images(task_id="task"))

    assert result["success"] is False
    assert "private or internal" in result["error"]


def test_typed_symlink_profile_home_denies_camofox_state(monkeypatch, tmp_path):
    root = tmp_path / "hermes"
    profiles = root / "profiles"
    profiles.mkdir(parents=True)
    outside = tmp_path / "outside-profile"
    outside.mkdir()
    (outside / "config.yaml").write_text(yaml.safe_dump({"browser": {"camofox": {"managed_persistence": True}}}), encoding="utf-8")
    link = profiles / "family-alpha"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("filesystem does not support directory symlinks")
    monkeypatch.setenv("HERMES_HOME", str(root))

    with bind_resolved_access_context(
        _ctx(role_id="family_sandbox", capabilities=frozenset({"browser"}))
    ):
        with pytest.raises(ValueError):
            browser_camofox_state.get_camofox_state_dir()


def test_legacy_browser_no_context_preserves_env_backend(monkeypatch):
    reset_session_vars()
    browser_tool._cached_browser_engine = None
    browser_tool._browser_engine_resolved = False
    monkeypatch.setenv("BROWSER_CDP_URL", "ws://127.0.0.1:9222/devtools/browser/synthetic")
    monkeypatch.setenv("CAMOFOX_URL", "http://127.0.0.1:9377")
    monkeypatch.setenv("AGENT_BROWSER_ENGINE", "lightpanda")

    assert browser_tool._get_cdp_override().startswith("ws://127.0.0.1:9222/")
    assert browser_tool._get_browser_engine() == "lightpanda"
    assert browser_camofox.is_camofox_mode() is False
