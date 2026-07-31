#!/usr/bin/env python3
"""Tests for execute_code's strict / project execution modes.

The mode switch controls two things:
  - working directory: staging tmpdir (strict) vs session CWD (project)
  - interpreter:       sys.executable (strict) vs active venv's python (project)

Security-critical invariants — env scrubbing, tool whitelist, resource caps —
must apply identically in both modes. These tests guard all three layers.

Mode is sourced exclusively from ``code_execution.mode`` in config.yaml —
there is no env-var override. Tests patch ``_load_config`` directly.
"""

import json
import os
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ["TERMINAL_ENV"] = "local"


@pytest.fixture(autouse=True)
def _force_local_terminal(monkeypatch):
    """Mirror test_code_execution.py — guarantee local backend under xdist."""
    monkeypatch.setenv("TERMINAL_ENV", "local")


from tools.code_execution_tool import (
    SANDBOX_ALLOWED_TOOLS,
    DEFAULT_EXECUTION_MODE,
    EXECUTION_MODES,
    _get_execution_mode,
    _is_usable_python,
    _resolve_child_cwd,
    _resolve_child_python,
    build_execute_code_schema,
    execute_code,
)


def _typed_context(profile_id="profile-a"):
    from gateway.access_registry import DeliveryTarget, ResolvedAccessContext

    return ResolvedAccessContext(
        principal_id="principal-a",
        role_id="family_standard",
        profile_id=profile_id,
        conversation_scope="dm:principal-a",
        capabilities=frozenset({"code"}),
        delivery_target=DeliveryTarget(
            platform="telegram",
            account="bot-a",
            peer_kind="dm",
            chat_id="1001",
        ),
    )


def _make_profile(root: Path, profile_id="profile-a") -> tuple[Path, Path]:
    profile_root = root / "profiles" / profile_id
    profile_home = profile_root / "home"
    profile_home.mkdir(parents=True)
    return profile_root, profile_home


def _capture_local_child_env(monkeypatch, *, context, env):
    import tools.code_execution_tool as cet
    import tools.terminal_tool as terminal_tool
    from gateway.session_context import bind_resolved_access_context

    captured = {}

    class FakeServerSocket:
        def bind(self, _address):
            return None

        def listen(self, _backlog):
            return None

        def settimeout(self, _timeout):
            return None

        def accept(self):
            raise cet.socket.timeout()

        def close(self):
            return None

    def fake_popen(_args, **kwargs):
        captured["env"] = dict(kwargs["env"])
        raise RuntimeError("captured child env before spawn")

    monkeypatch.setattr(terminal_tool, "_get_env_config", lambda: {"env_type": "local"})
    monkeypatch.setattr(terminal_tool, "_docker_has_host_access", lambda _cfg: False)
    monkeypatch.setattr("tools.approval.check_execute_code_guard", lambda *_a, **_k: {"approved": True})
    monkeypatch.setattr(cet, "_load_config", lambda: {"timeout": 5, "max_tool_calls": 5, "mode": "strict"})
    monkeypatch.setattr(cet, "_resolve_child_python", lambda _mode: sys.executable)
    monkeypatch.setattr(cet, "_resolve_child_cwd", lambda _mode, tmpdir, task_id="": tmpdir)
    monkeypatch.setattr(cet.socket, "socket", lambda *_args, **_kwargs: FakeServerSocket())
    monkeypatch.setattr(cet.os, "chmod", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cet.subprocess, "Popen", fake_popen)

    with patch.dict(os.environ, env, clear=True), bind_resolved_access_context(context):
        raw = execute_code("print('env probe')", task_id="typed-env-probe", enabled_tools=["terminal"])

    return captured, json.loads(raw)


@contextmanager
def _mock_mode(mode):
    """Context manager that pins code_execution.mode to the given value."""
    with patch("tools.code_execution_tool._load_config",
               return_value={"mode": mode}):
        yield


def _mock_handle_function_call(function_name, function_args, task_id=None, user_task=None):
    """Minimal mock dispatcher reused across tests."""
    if function_name == "terminal":
        return json.dumps({"output": "mock", "exit_code": 0})
    if function_name == "read_file":
        return json.dumps({"content": "line1\n", "total_lines": 1})
    return json.dumps({"error": f"Unknown tool: {function_name}"})


# ---------------------------------------------------------------------------
# Mode resolution
# ---------------------------------------------------------------------------

class TestGetExecutionMode(unittest.TestCase):
    """_get_execution_mode reads config.yaml only (no env var surface)."""

    def test_default_is_project(self):
        self.assertEqual(DEFAULT_EXECUTION_MODE, "project")

    def test_config_project(self):
        with patch("tools.code_execution_tool._load_config",
                   return_value={"mode": "project"}):
            self.assertEqual(_get_execution_mode(), "project")

    def test_config_strict(self):
        with patch("tools.code_execution_tool._load_config",
                   return_value={"mode": "strict"}):
            self.assertEqual(_get_execution_mode(), "strict")

    def test_config_case_insensitive(self):
        with patch("tools.code_execution_tool._load_config",
                   return_value={"mode": "STRICT"}):
            self.assertEqual(_get_execution_mode(), "strict")

    def test_config_strips_whitespace(self):
        with patch("tools.code_execution_tool._load_config",
                   return_value={"mode": "  project  "}):
            self.assertEqual(_get_execution_mode(), "project")

    def test_empty_config_falls_back_to_default(self):
        with patch("tools.code_execution_tool._load_config", return_value={}):
            self.assertEqual(_get_execution_mode(), DEFAULT_EXECUTION_MODE)

    def test_bogus_config_falls_back_to_default(self):
        with patch("tools.code_execution_tool._load_config",
                   return_value={"mode": "banana"}):
            self.assertEqual(_get_execution_mode(), DEFAULT_EXECUTION_MODE)

    def test_none_config_falls_back_to_default(self):
        with patch("tools.code_execution_tool._load_config",
                   return_value={"mode": None}):
            # str(None).lower() = "none" → not in EXECUTION_MODES → default
            self.assertEqual(_get_execution_mode(), DEFAULT_EXECUTION_MODE)

    def test_execution_modes_tuple(self):
        """Canonical set of modes — tests + config layer rely on this shape."""
        self.assertEqual(set(EXECUTION_MODES), {"project", "strict"})


# ---------------------------------------------------------------------------
# Interpreter resolver
# ---------------------------------------------------------------------------

class TestResolveChildPython(unittest.TestCase):
    """_resolve_child_python — picks the right interpreter per mode."""

    def test_strict_always_sys_executable(self):
        """Strict mode never leaves sys.executable, even if venv is set."""
        with patch.dict(os.environ, {"VIRTUAL_ENV": "/some/venv"}):
            self.assertEqual(_resolve_child_python("strict"), sys.executable)

    def test_project_with_no_venv_falls_back(self):
        """Project mode without VIRTUAL_ENV or CONDA_PREFIX → sys.executable."""
        env = {k: v for k, v in os.environ.items()
               if k not in {"VIRTUAL_ENV", "CONDA_PREFIX"}}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(_resolve_child_python("project"), sys.executable)

    def test_project_with_virtualenv_picks_venv_python(self):
        """Project mode + VIRTUAL_ENV pointing at a real venv → that python."""
        if sys.platform == "win32":
            pytest.skip(
                "Creates symlinks and assumes POSIX venv layout (bin/python). "
                "Windows venvs use Scripts/python.exe and symlink creation "
                "requires elevated privileges (WinError 1314)."
            )
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as td:
            fake_venv = pathlib.Path(td)
            (fake_venv / "bin").mkdir()
            # Symlink to real python so the version check actually passes
            (fake_venv / "bin" / "python").symlink_to(sys.executable)
            with patch.dict(os.environ, {"VIRTUAL_ENV": str(fake_venv)}):
                # Clear cache — _is_usable_python memoizes on path
                _is_usable_python.cache_clear()
                result = _resolve_child_python("project")
                self.assertEqual(result, str(fake_venv / "bin" / "python"))

    def test_project_with_broken_venv_falls_back(self):
        """VIRTUAL_ENV set but bin/python missing → sys.executable."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            # No bin/python inside — broken venv
            with patch.dict(os.environ, {"VIRTUAL_ENV": td}):
                _is_usable_python.cache_clear()
                self.assertEqual(_resolve_child_python("project"), sys.executable)

    def test_project_prefers_virtualenv_over_conda(self):
        """If both VIRTUAL_ENV and CONDA_PREFIX are set, VIRTUAL_ENV wins."""
        if sys.platform == "win32":
            pytest.skip(
                "Creates symlinks and assumes POSIX venv layout (bin/python). "
                "Windows venvs use Scripts/python.exe and symlink creation "
                "requires elevated privileges (WinError 1314)."
            )
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as ve_td, tempfile.TemporaryDirectory() as conda_td:
            ve = pathlib.Path(ve_td)
            (ve / "bin").mkdir()
            (ve / "bin" / "python").symlink_to(sys.executable)

            conda = pathlib.Path(conda_td)
            (conda / "bin").mkdir()
            (conda / "bin" / "python").symlink_to(sys.executable)

            with patch.dict(os.environ, {"VIRTUAL_ENV": str(ve), "CONDA_PREFIX": str(conda)}):
                _is_usable_python.cache_clear()
                result = _resolve_child_python("project")
                self.assertEqual(result, str(ve / "bin" / "python"))

    def test_is_usable_python_rejects_nonexistent(self):
        _is_usable_python.cache_clear()
        self.assertFalse(_is_usable_python("/does/not/exist/python"))

    def test_is_usable_python_accepts_real_python(self):
        _is_usable_python.cache_clear()
        self.assertTrue(_is_usable_python(sys.executable))


# ---------------------------------------------------------------------------
# CWD resolver
# ---------------------------------------------------------------------------

class TestResolveChildCwd(unittest.TestCase):

    def test_strict_uses_staging_dir(self):
        self.assertEqual(_resolve_child_cwd("strict", "/tmp/staging"), "/tmp/staging")

    def test_project_without_terminal_cwd_uses_getcwd(self):
        env = {k: v for k, v in os.environ.items() if k != "TERMINAL_CWD"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(_resolve_child_cwd("project", "/tmp/staging"), os.getcwd())

    def test_project_uses_terminal_cwd_when_set(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"TERMINAL_CWD": td}):
                self.assertEqual(_resolve_child_cwd("project", "/tmp/staging"), td)

    def test_project_bogus_terminal_cwd_falls_back_to_getcwd(self):
        with patch.dict(os.environ, {"TERMINAL_CWD": "/does/not/exist/anywhere"}):
            self.assertEqual(_resolve_child_cwd("project", "/tmp/staging"), os.getcwd())

    def test_project_expands_tilde(self):
        import pathlib
        home = str(pathlib.Path.home())
        with patch.dict(os.environ, {"TERMINAL_CWD": "~"}):
            self.assertEqual(_resolve_child_cwd("project", "/tmp/staging"), home)

    def test_project_prefers_registered_task_cwd_override(self):
        import tempfile
        import tools.terminal_tool as terminal_tool

        with tempfile.TemporaryDirectory() as td:
            task_id = "session-cwd-test"
            with patch.dict(os.environ, {"TERMINAL_CWD": "/does/not/exist"}):
                with patch.object(terminal_tool, "_task_env_overrides", {}, create=False):
                    terminal_tool.register_task_env_overrides(task_id, {"cwd": td})
                    self.assertEqual(_resolve_child_cwd("project", "/tmp/staging", task_id=task_id), td)

    def test_project_prefers_session_cwd_record_over_override(self):
        """The session's cwd RECORD (its live `cd` state) outranks the
        registration-time workspace override — same ladder as file tools
        and the terminal, so a `cd` before execute_code is honored."""
        import tempfile
        import tools.terminal_tool as terminal_tool

        with tempfile.TemporaryDirectory() as reg, tempfile.TemporaryDirectory() as cded:
            task_id = "session-record-test"
            with patch.dict(os.environ, {"TERMINAL_CWD": "/does/not/exist"}):
                with patch.object(terminal_tool, "_task_env_overrides", {}, create=False), \
                     patch.object(terminal_tool, "_session_cwd", {}, create=False):
                    terminal_tool.register_task_env_overrides(task_id, {"cwd": reg})
                    # Simulate a later `cd`: post-command tracking rewrites the record.
                    terminal_tool.record_session_cwd(task_id, cded)
                    self.assertEqual(
                        _resolve_child_cwd("project", "/tmp/staging", task_id=task_id), cded
                    )

    def test_project_uses_session_cwd_record_without_any_override(self):
        """A session that only `cd`'d (no session.cwd.set registration) still
        resolves to its recorded directory."""
        import tempfile
        import tools.terminal_tool as terminal_tool

        with tempfile.TemporaryDirectory() as cded:
            task_id = "record-only-test"
            with patch.dict(os.environ, {"TERMINAL_CWD": "/does/not/exist"}):
                with patch.object(terminal_tool, "_task_env_overrides", {}, create=False), \
                     patch.object(terminal_tool, "_session_cwd", {}, create=False):
                    terminal_tool.record_session_cwd(task_id, cded)
                    self.assertEqual(
                        _resolve_child_cwd("project", "/tmp/staging", task_id=task_id), cded
                    )

    def test_project_stale_record_falls_through_to_override(self):
        """A recorded directory that no longer exists is skipped; the
        registered override is the next rung."""
        import tempfile
        import tools.terminal_tool as terminal_tool

        with tempfile.TemporaryDirectory() as reg:
            task_id = "stale-record-test"
            with patch.dict(os.environ, {"TERMINAL_CWD": "/does/not/exist"}):
                with patch.object(terminal_tool, "_task_env_overrides", {}, create=False), \
                     patch.object(terminal_tool, "_session_cwd", {}, create=False):
                    terminal_tool.register_task_env_overrides(task_id, {"cwd": reg})
                    terminal_tool.record_session_cwd(task_id, "/deleted/dir/gone")
                    self.assertEqual(
                        _resolve_child_cwd("project", "/tmp/staging", task_id=task_id), reg
                    )


# ---------------------------------------------------------------------------
# Schema description
# ---------------------------------------------------------------------------

class TestModeAwareSchema(unittest.TestCase):

    def test_strict_description_mentions_temp_dir(self):
        desc = build_execute_code_schema(mode="strict")["description"]
        self.assertIn("temp dir", desc)

    def test_project_description_mentions_session_and_venv(self):
        desc = build_execute_code_schema(mode="project")["description"]
        self.assertIn("session", desc)
        self.assertIn("venv", desc)

    def test_neither_description_uses_sandbox_language(self):
        """REGRESSION GUARD for commit 39b83f34.

        Agents on local backends falsely believed they were sandboxed and
        refused networking tasks. Do not reintroduce any 'sandbox' /
        'isolated' / 'cloud' language in the tool description.
        """
        for mode in EXECUTION_MODES:
            desc = build_execute_code_schema(mode=mode)["description"].lower()
            for forbidden in ("sandbox", "isolated", "cloud"):
                self.assertNotIn(forbidden, desc,
                                 f"mode={mode}: '{forbidden}' leaked into description")

    def test_descriptions_are_similar_length(self):
        """Both modes should have roughly the same-size description."""
        strict = len(build_execute_code_schema(mode="strict")["description"])
        project = len(build_execute_code_schema(mode="project")["description"])
        self.assertLess(abs(strict - project), 200)

    def test_default_mode_reads_config(self):
        """build_execute_code_schema() with mode=None reads config.yaml."""
        with _mock_mode("strict"):
            desc = build_execute_code_schema()["description"]
            self.assertIn("temp dir", desc)
        with _mock_mode("project"):
            desc = build_execute_code_schema()["description"]
            self.assertIn("session", desc)


# ---------------------------------------------------------------------------
# Integration: what actually happens when execute_code runs per mode
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "Assumes POSIX venv layout (bin/python) and symlink creation "
        "privileges.  execute_code itself works on Windows — these "
        "integration tests just haven't been ported to the Scripts/"
        "python.exe layout yet."
    ),
)
class TestExecuteCodeModeIntegration(unittest.TestCase):
    """End-to-end: verify the subprocess actually runs where we expect."""

    def _run(self, code, mode, enabled_tools=None, extra_env=None):
        env_overrides = extra_env or {}
        with _mock_mode(mode):
            with patch.dict(os.environ, env_overrides):
                with patch("model_tools.handle_function_call",
                           side_effect=_mock_handle_function_call):
                    raw = execute_code(
                        code=code,
                        task_id=f"test-{mode}",
                        enabled_tools=enabled_tools or list(SANDBOX_ALLOWED_TOOLS),
                    )
        return json.loads(raw)

    def test_strict_mode_runs_in_tmpdir(self):
        """Strict mode: script's os.getcwd() is the staging tmpdir."""
        result = self._run("import os; print(os.getcwd())", mode="strict")
        self.assertEqual(result["status"], "success")
        self.assertIn("hermes_sandbox_", result["output"])

    def test_project_mode_runs_in_session_cwd(self):
        """Project mode: script's os.getcwd() is the session's working dir."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            result = self._run(
                "import os; print(os.getcwd())",
                mode="project",
                extra_env={"TERMINAL_CWD": td},
            )
            self.assertEqual(result["status"], "success")
            # Resolve symlinks (macOS /tmp → /private/tmp) on both sides
            self.assertEqual(
                os.path.realpath(result["output"].strip()),
                os.path.realpath(td),
            )

    def test_project_mode_uses_registered_session_cwd_override(self):
        """Project mode must honor session.cwd.set-style overrides even when
        TERMINAL_CWD is absent or points elsewhere."""
        import tempfile
        import tools.terminal_tool as terminal_tool

        with tempfile.TemporaryDirectory() as td:
            task_id = "session-cwd-test"
            with patch.dict(os.environ, {"TERMINAL_CWD": "/does/not/exist"}):
                with patch.object(terminal_tool, "_task_env_overrides", {}, create=False):
                    terminal_tool.register_task_env_overrides(task_id, {"cwd": td})
                    with _mock_mode("project"):
                        with patch("model_tools.handle_function_call", side_effect=_mock_handle_function_call):
                            raw = execute_code(
                                code="import os; print(os.getcwd())",
                                task_id=task_id,
                                enabled_tools=list(SANDBOX_ALLOWED_TOOLS),
                            )
            result = json.loads(raw)
            self.assertEqual(result["status"], "success")
            self.assertEqual(os.path.realpath(result["output"].strip()), os.path.realpath(td))

    def test_project_mode_interpreter_is_venv_python(self):
        """Project mode: sys.executable inside the child is the venv's python
        when VIRTUAL_ENV is set to a real venv."""
        # The hermes-agent venv is always active during tests, so this also
        # happens to equal sys.executable of the parent. What we're asserting
        # is: resolver picked a venv-bin/python path, not that it differs
        # from sys.executable.
        result = self._run("import sys; print(sys.executable)", mode="project")
        self.assertEqual(result["status"], "success")
        # Either VIRTUAL_ENV-bin/python or sys.executable fallback, both OK.
        output = result["output"].strip()
        ve = os.environ.get("VIRTUAL_ENV", "").strip()
        if ve:
            self.assertTrue(
                output.startswith(ve) or output == sys.executable,
                f"project-mode python should be under VIRTUAL_ENV={ve} or sys.executable={sys.executable}, got {output}",
            )

    def test_project_mode_can_still_import_hermes_tools(self):
        """Regression: hermes_tools still importable from non-tmpdir CWD.

        This is the PYTHONPATH fix — without it, switching to session CWD
        breaks `from hermes_tools import terminal`.
        """
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            code = (
                "from hermes_tools import terminal\n"
                "r = terminal('echo x')\n"
                "print(r.get('output', 'MISSING'))\n"
            )
            result = self._run(code, mode="project", extra_env={"TERMINAL_CWD": td})
            self.assertEqual(result["status"], "success")
            self.assertIn("mock", result["output"])

    def test_strict_mode_can_still_import_hermes_tools(self):
        """Regression: strict mode's tmpdir CWD still works for imports."""
        code = (
            "from hermes_tools import terminal\n"
            "r = terminal('echo x')\n"
            "print(r.get('output', 'MISSING'))\n"
        )
        result = self._run(code, mode="strict")
        self.assertEqual(result["status"], "success")
        self.assertIn("mock", result["output"])


def test_typed_execute_code_child_env_uses_bound_profile_not_poisoned_owner_env(monkeypatch, tmp_path):
    """Defect: typed execute_code inherited owner/default HOME and Hermes paths."""
    owner_root = tmp_path / "owner-default-hermes"
    profile_root, profile_home = _make_profile(owner_root)
    owner_home = tmp_path / "owner-home"
    owner_home.mkdir()
    owner_tmp = tmp_path / "owner-tmp"
    owner_tmp.mkdir()
    owner_xdg = tmp_path / "owner-xdg"
    owner_xdg.mkdir()

    env = {
        "PATH": os.environ.get("PATH", ""),
        "TERMINAL_ENV": "local",
        "HERMES_HOME": str(owner_root),
        "HERMES_PROFILE": "default",
        "HERMES_REAL_HOME": str(owner_home),
        "HERMES_CONFIG": str(owner_root / "config.yaml"),
        "HERMES_ENV": str(owner_root / ".env"),
        "HOME": str(owner_home),
        "USERPROFILE": str(owner_home),
        "HOMEDRIVE": "Z:",
        "HOMEPATH": "\\Users\\owner",
        "XDG_CONFIG_HOME": str(owner_xdg),
        "XDG_RUNTIME_DIR": str(owner_xdg / "runtime"),
        "TMPDIR": str(owner_tmp),
        "TMP": str(owner_tmp),
        "TEMP": str(owner_tmp),
        "PYTHONPATH": str(owner_root / "owner-pythonpath"),
        "VIRTUAL_ENV": str(owner_root / "venv"),
        "CONDA_PREFIX": str(owner_root / "conda"),
    }

    captured, result = _capture_local_child_env(
        monkeypatch,
        context=_typed_context(),
        env=env,
    )

    assert result["status"] == "error"
    child_env = captured["env"]
    assert child_env["HERMES_HOME"] == str(profile_root)
    assert child_env["HERMES_PROFILE"] == "profile-a"
    assert child_env["HOME"] == str(profile_home)
    assert child_env["USERPROFILE"] == str(profile_home)
    assert str(owner_root) not in child_env["PYTHONPATH"]
    assert child_env["PYTHONPATH"].split(os.pathsep)[:2] == [
        child_env["TMPDIR"],
        str(Path(__file__).resolve().parents[2]),
    ]
    assert "HERMES_REAL_HOME" not in child_env
    assert "HERMES_CONFIG" not in child_env
    assert "HERMES_ENV" not in child_env
    assert not any(key.startswith("XDG_") for key in child_env)
    assert "VIRTUAL_ENV" not in child_env
    assert "CONDA_PREFIX" not in child_env
    assert child_env["TMPDIR"] == child_env["TMP"] == child_env["TEMP"]
    assert child_env["TMPDIR"].startswith(os.path.realpath("/tmp"))


def test_typed_execute_code_child_env_ignores_passthrough_provider_api_mcp_secrets(
    monkeypatch, tmp_path
):
    """Defect: process-global passthrough could put provider/API/MCP secrets in typed child env."""
    owner_root = tmp_path / "owner-default-hermes"
    _make_profile(owner_root)
    provider_secret = "tenor-provider-secret"
    env = {
        "PATH": os.environ.get("PATH", ""),
        "TERMINAL_ENV": "local",
        "HERMES_HOME": str(owner_root),
        "HOME": str(tmp_path / "owner-home"),
        "TENOR_API_KEY": provider_secret,
        "OPENAI_API_KEY": "openai-secret",
        "ANTHROPIC_AUTH_TOKEN": "anthropic-secret",
        "MCP_SERVER_TOKEN": "mcp-secret",
    }

    monkeypatch.setattr("tools.env_passthrough.is_env_passthrough", lambda key: key == "TENOR_API_KEY")
    captured, _result = _capture_local_child_env(
        monkeypatch,
        context=_typed_context(),
        env=env,
    )

    child_env = captured["env"]
    for key in ("TENOR_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_AUTH_TOKEN", "MCP_SERVER_TOKEN"):
        assert key not in child_env
    assert provider_secret not in json.dumps(child_env, sort_keys=True)


def test_typed_execute_code_malformed_context_fails_before_spawn(monkeypatch, tmp_path):
    """Defect: malformed non-None typed context fell through to legacy child spawn."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "TERMINAL_ENV": "local",
        "HERMES_HOME": str(tmp_path / "owner-default-hermes"),
        "HOME": str(tmp_path / "owner-home"),
    }
    captured, result = _capture_local_child_env(
        monkeypatch,
        context={"profile_id": "profile-a"},
        env=env,
    )

    assert "env" not in captured
    assert result["status"] == "error"
    assert result["tool_calls_made"] == 0
    assert "malformed resolved access context" in result["error"]


def test_typed_execute_code_missing_profile_fails_before_spawn(monkeypatch, tmp_path):
    """Defect: missing typed profile home must fail before local child spawn."""
    owner_root = tmp_path / "owner-default-hermes"
    owner_root.mkdir()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "TERMINAL_ENV": "local",
        "HERMES_HOME": str(owner_root),
        "HOME": str(tmp_path / "owner-home"),
    }
    captured, result = _capture_local_child_env(
        monkeypatch,
        context=_typed_context("missing-profile"),
        env=env,
    )

    assert "env" not in captured
    assert result["status"] == "error"
    assert result["tool_calls_made"] == 0
    assert "resolved access profile" in result["error"]


def test_legacy_execute_code_child_env_matches_scrub_apply_contract(monkeypatch, tmp_path):
    """Legacy no-typed-context path keeps exact scrubber/apply_subprocess_home_env behavior."""
    import tools.code_execution_tool as cet
    from gateway.session_context import bind_resolved_access_context
    from hermes_constants import apply_subprocess_home_env

    owner_root = tmp_path / "owner-default-hermes"
    (owner_root / "home").mkdir(parents=True)
    owner_home = tmp_path / "owner-home"
    owner_home.mkdir()
    tmpdir = str(tmp_path / "run-tmp")
    hermes_root = str(Path(cet.__file__).resolve().parents[1])
    env = {
        "PATH": "/usr/bin:/bin",
        "TERMINAL_ENV": "local",
        "HERMES_HOME": str(owner_root),
        "HERMES_PROFILE": "default",
        "HERMES_REAL_HOME": str(owner_home),
        "HERMES_CONFIG": str(owner_root / "config.yaml"),
        "HERMES_ENV": str(owner_root / ".env"),
        "HERMES_TIMEZONE": "Europe/Amsterdam",
        "HOME": str(owner_home),
        "PYTHONPATH": str(owner_root / "owner-pythonpath"),
        "TERMINAL_HOME_MODE": "profile",
        "TENOR_API_KEY": "legacy-passthrough-secret",
        "OPENAI_API_KEY": "blocked-secret",
    }
    monkeypatch.setattr("tools.env_passthrough.is_env_passthrough", lambda key: key == "TENOR_API_KEY")

    with patch.dict(os.environ, env, clear=True), bind_resolved_access_context(None):
        expected = cet._scrub_child_env(os.environ)
        expected["HERMES_RPC_SOCKET"] = "rpc-endpoint"
        expected["HERMES_RPC_TOKEN"] = "rpc-token"
        expected["PYTHONDONTWRITEBYTECODE"] = "1"
        expected["PYTHONIOENCODING"] = "utf-8"
        expected["PYTHONUTF8"] = "1"
        expected["PYTHONPATH"] = os.pathsep.join([
            tmpdir,
            hermes_root,
            str(owner_root / "owner-pythonpath"),
        ])
        expected["TZ"] = "Europe/Amsterdam"
        expected.pop("HERMES_TIMEZONE", None)
        apply_subprocess_home_env(expected)

        actual = cet._build_local_child_env(tmpdir, "rpc-endpoint", "rpc-token")

    assert actual == expected
    assert actual["HERMES_HOME"] == str(owner_root)
    assert actual["HOME"] == str(owner_root / "home")
    assert actual["HERMES_REAL_HOME"] == str(owner_home)
    assert actual["TENOR_API_KEY"] == "legacy-passthrough-secret"
    assert "OPENAI_API_KEY" not in actual


# ---------------------------------------------------------------------------
# SECURITY-CRITICAL regression guards
#
# These MUST pass in both strict and project mode. The whole tiered-mode
# proposition rests on the claim that switching from strict to project only
# changes CWD + interpreter, not the security posture.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "Assumes POSIX venv layout (bin/python) and symlink creation "
        "privileges.  execute_code itself works on Windows — these "
        "integration tests just haven't been ported to the Scripts/"
        "python.exe layout yet."
    ),
)
class TestSecurityInvariantsAcrossModes(unittest.TestCase):

    def _run(self, code, mode):
        with _mock_mode(mode):
            with patch("model_tools.handle_function_call",
                       side_effect=_mock_handle_function_call):
                raw = execute_code(
                    code=code,
                    task_id=f"test-sec-{mode}",
                    enabled_tools=list(SANDBOX_ALLOWED_TOOLS),
                )
        return json.loads(raw)

    def test_api_keys_scrubbed_in_strict_mode(self):
        code = (
            "import os\n"
            "print('KEY=' + os.environ.get('OPENAI_API_KEY', 'MISSING'))\n"
            "print('TOK=' + os.environ.get('ANTHROPIC_API_KEY', 'MISSING'))\n"
        )
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "sk-should-not-leak",
            "ANTHROPIC_API_KEY": "ant-should-not-leak",
        }):
            result = self._run(code, mode="strict")
        self.assertEqual(result["status"], "success")
        self.assertIn("KEY=MISSING", result["output"])
        self.assertIn("TOK=MISSING", result["output"])
        self.assertNotIn("sk-should-not-leak", result["output"])
        self.assertNotIn("ant-should-not-leak", result["output"])

    def test_api_keys_scrubbed_in_project_mode(self):
        """CRITICAL: the project-mode default does NOT leak user credentials."""
        code = (
            "import os\n"
            "print('KEY=' + os.environ.get('OPENAI_API_KEY', 'MISSING'))\n"
            "print('TOK=' + os.environ.get('ANTHROPIC_API_KEY', 'MISSING'))\n"
            "print('SEC=' + os.environ.get('GITHUB_TOKEN', 'MISSING'))\n"
        )
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "sk-should-not-leak",
            "ANTHROPIC_API_KEY": "ant-should-not-leak",
            "GITHUB_TOKEN": "ghp-should-not-leak",
        }):
            result = self._run(code, mode="project")
        self.assertEqual(result["status"], "success")
        for needle in ("KEY=MISSING", "TOK=MISSING", "SEC=MISSING"):
            self.assertIn(needle, result["output"])
        for leaked in ("sk-should-not-leak", "ant-should-not-leak", "ghp-should-not-leak"):
            self.assertNotIn(leaked, result["output"])

    def test_secret_substrings_scrubbed_in_project_mode(self):
        """SECRET/PASSWORD/CREDENTIAL/PASSWD/AUTH filters still apply."""
        code = (
            "import os\n"
            "for k in ('MY_SECRET', 'DB_PASSWORD', 'VAULT_CREDENTIAL', "
            "'LDAP_PASSWD', 'AUTH_TOKEN'):\n"
            "    print(f'{k}=' + os.environ.get(k, 'MISSING'))\n"
        )
        with patch.dict(os.environ, {
            "MY_SECRET": "secret-should-not-leak",
            "DB_PASSWORD": "password-should-not-leak",
            "VAULT_CREDENTIAL": "cred-should-not-leak",
            "LDAP_PASSWD": "passwd-should-not-leak",
            "AUTH_TOKEN": "auth-should-not-leak",
        }):
            result = self._run(code, mode="project")
        self.assertEqual(result["status"], "success")
        for leaked in ("secret-should-not-leak", "password-should-not-leak",
                       "cred-should-not-leak", "passwd-should-not-leak",
                       "auth-should-not-leak"):
            self.assertNotIn(leaked, result["output"])

    def test_tool_whitelist_enforced_in_strict_mode(self):
        """A script cannot RPC-call tools outside SANDBOX_ALLOWED_TOOLS."""
        # execute_code is NOT in SANDBOX_ALLOWED_TOOLS (no recursion)
        self.assertNotIn("execute_code", SANDBOX_ALLOWED_TOOLS)
        code = (
            "import hermes_tools as ht\n"
            "print('execute_code_available:', hasattr(ht, 'execute_code'))\n"
            "print('delegate_task_available:', hasattr(ht, 'delegate_task'))\n"
        )
        result = self._run(code, mode="strict")
        self.assertEqual(result["status"], "success")
        self.assertIn("execute_code_available: False", result["output"])
        self.assertIn("delegate_task_available: False", result["output"])

    def test_tool_whitelist_enforced_in_project_mode(self):
        """CRITICAL: project mode does NOT widen the tool whitelist."""
        code = (
            "import hermes_tools as ht\n"
            "print('execute_code_available:', hasattr(ht, 'execute_code'))\n"
            "print('delegate_task_available:', hasattr(ht, 'delegate_task'))\n"
        )
        result = self._run(code, mode="project")
        self.assertEqual(result["status"], "success")
        self.assertIn("execute_code_available: False", result["output"])
        self.assertIn("delegate_task_available: False", result["output"])


if __name__ == "__main__":
    unittest.main()
