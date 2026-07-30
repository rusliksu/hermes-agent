"""Shared Git/layout/oracle owner for Kanban MCP rollout tests."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def git(repo: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(repo), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip()


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def wrapper_bytes(runtime: Path, *, canonical: bool) -> bytes:
    canonical_lines = (
        f"ulimit -S -n 4096\ncd -- {runtime}\n" if canonical else ""
    )
    return (
        f"#!/bin/bash\nset -euo pipefail\n"
        f"export HERMES_HOME={runtime.parent}/hermes-home\n"
        "export HERMES_QUIET=1\n"
        "export HERMES_REDACT_SECRETS=true\n"
        f"export PYTHONDONTWRITEBYTECODE=1\n{canonical_lines}"
        f"exec {runtime}/.venv/bin/python"
        ' -m hermes_cli.main mcp serve-kanban --allow-write "$@"\n'
    ).encode()


def filesystem_oracle(root: Path) -> tuple[tuple[object, ...], ...]:
    records: list[tuple[object, ...]] = []
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in sorted(dirnames + filenames):
            path = base / name
            relative = str(path.relative_to(root))
            info = path.lstat()
            mode = stat.S_IMODE(info.st_mode)
            if path.is_symlink():
                records.append(
                    (relative, "symlink", mode, os.readlink(path), info.st_mtime_ns)
                )
            elif path.is_file():
                records.append(
                    (
                        relative,
                        "file",
                        mode,
                        hash_bytes(path.read_bytes()),
                        info.st_mtime_ns,
                    )
                )
            else:
                records.append((relative, "directory", mode, info.st_mtime_ns))
    return tuple(records)


def content_oracle(root: Path) -> dict[str, tuple[object, ...]]:
    return {
        str(record[0]): record[1:-1]
        for record in filesystem_oracle(root)
    }


def rollout_temporary_paths(layout: "RolloutLayout") -> tuple[Path, ...]:
    return tuple(
        sorted(
            layout.stable_wrapper.parent.glob(
                f".{layout.stable_wrapper.name}.rollout-*"
            )
        )
    )


def _is_at_or_below(path: str, root: str) -> bool:
    return path == root or path.startswith(f"{root}/")


def assert_exact_rollout_origins(
    before: dict[str, tuple[object, ...]],
    after: dict[str, tuple[object, ...]],
    layout: "RolloutLayout",
    *,
    snapshot_expected: bool,
) -> None:
    candidate = str(layout.candidate_path.relative_to(layout.root))
    snapshot = str(layout.snapshot_path.relative_to(layout.root))
    snapshots_root = str(layout.snapshot_path.parent.relative_to(layout.root))
    git_admin = str(
        (
            layout.source / ".git" / "worktrees" / layout.candidate_path.name
        ).relative_to(layout.root)
    )
    added = set(after) - set(before)
    assert set(before) - set(after) == set()
    assert {
        path
        for path in set(before) & set(after)
        if before[path] != after[path]
    } == set()
    allowed = {
        path
        for path in added
        if _is_at_or_below(path, candidate)
        or _is_at_or_below(path, git_admin)
        or (
            snapshot_expected
            and (path == snapshots_root or _is_at_or_below(path, snapshot))
        )
    }
    assert added == allowed
    assert candidate in added
    assert git_admin in added
    if snapshot_expected:
        assert snapshot in added


@dataclass
class RolloutLayout:
    root: Path
    source: Path
    runtime_root: Path
    state_root: Path
    current_runtime: Path
    stable_wrapper: Path
    current_sha: str
    candidate_sha: str
    wrapper_before: bytes
    wrapper_before_hash: str
    wrapper_mode: int

    @property
    def candidate_path(self) -> Path:
        return self.runtime_root / f"hermes-kanban-mcp-{self.candidate_sha}"

    @property
    def snapshot_id(self) -> str:
        return f"{self.current_sha}-to-{self.candidate_sha}"

    @property
    def snapshot_path(self) -> Path:
        return self.state_root / "snapshots" / self.snapshot_id

    def prepare_args(
        self,
        *,
        apply: bool = False,
        wrapper_after_sha256: str | None | object = ...,
    ) -> list[str]:
        result = [
            "prepare",
            "--source-repo", str(self.source),
            "--runtime-root", str(self.runtime_root),
            "--state-root", str(self.state_root),
            "--current-runtime", str(self.current_runtime),
            "--expected-current-runtime-sha", self.current_sha,
            "--candidate-sha", self.candidate_sha,
            "--venv-dirname", ".venv",
            "--stable-wrapper", str(self.stable_wrapper),
            "--expected-current-wrapper-sha256", self.wrapper_before_hash,
        ]
        if wrapper_after_sha256 is ... and apply:
            wrapper_after_sha256 = hash_bytes(
                wrapper_bytes(self.candidate_path, canonical=True)
            )
        if isinstance(wrapper_after_sha256, str):
            result.extend(
                ["--expected-wrapper-after-sha256", wrapper_after_sha256]
            )
        return [*result, "--apply"] if apply else result

    def transition_args(
        self,
        command: str,
        expected_hash: str,
        *,
        apply: bool = False,
        wrapper_after_sha256: str | None | object = ...,
    ) -> list[str]:
        result = [
            command,
            "--runtime-root", str(self.runtime_root),
            "--state-root", str(self.state_root),
            "--snapshot-id", self.snapshot_id,
            "--stable-wrapper", str(self.stable_wrapper),
            "--expected-current-wrapper-sha256", expected_hash,
        ]
        if command == "switch" and wrapper_after_sha256 is ... and apply:
            wrapper_after_sha256 = hash_bytes(
                (self.snapshot_path / "wrapper.after").read_bytes()
            )
        if command == "switch" and isinstance(wrapper_after_sha256, str):
            result.extend(
                ["--expected-wrapper-after-sha256", wrapper_after_sha256]
            )
        return [*result, "--apply"] if apply else result


def build_rollout_layout(tmp_path: Path) -> RolloutLayout:
    source = tmp_path / "source"
    runtime_root = tmp_path / "runtimes"
    state_root = tmp_path / "rollout-state"
    stable_wrapper = tmp_path / "bin" / "hermes-kanban-mcp"
    source.mkdir()
    runtime_root.mkdir()
    state_root.mkdir()
    stable_wrapper.parent.mkdir()
    git(source, "init")
    git(source, "config", "user.email", "tests@example.invalid")
    git(source, "config", "user.name", "Hermes rollout tests")
    payload = source / "payload.txt"
    payload.write_text("current\n", encoding="utf-8")
    for package in ("hermes_cli", "agent", "agent/transports"):
        (source / package).mkdir(exist_ok=True)
        (source / package / "__init__.py").write_text("", encoding="utf-8")
    (source / "hermes_cli" / "main.py").write_text("", encoding="utf-8")
    (source / "agent/transports/hermes_kanban_mcp_server.py").write_text(
        'WRITE_TOOLS = ("kanban_enqueue", "kanban_sync_external_task")\n',
        encoding="utf-8",
    )
    git(source, "add", ".")
    git(source, "commit", "-m", "current")
    current_sha = git(source, "rev-parse", "HEAD")
    current_runtime = runtime_root / f"hermes-kanban-mcp-{current_sha}"
    payload.write_text("candidate\n", encoding="utf-8")
    git(source, "commit", "-am", "candidate")
    candidate_sha = git(source, "rev-parse", "HEAD")
    git(source, "worktree", "add", "--detach", str(current_runtime), current_sha)
    interpreter = current_runtime / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(Path(sys.executable).read_bytes())
    interpreter.chmod(stat.S_IMODE(Path(sys.executable).stat().st_mode))
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    (current_runtime / ".venv" / "pyvenv.cfg").write_text(
        f"home = {Path(getattr(sys, '_base_executable', sys.executable)).resolve().parent}\n"
        f"include-system-site-packages = false\nversion = {version}\n",
        encoding="utf-8",
    )
    (
        current_runtime / ".venv/lib" / f"python{version}/site-packages"
    ).mkdir(parents=True)
    (current_runtime / "do-not-copy.txt").write_text(
        "untracked runtime data\n", encoding="utf-8"
    )
    before = wrapper_bytes(current_runtime, canonical=False)
    stable_wrapper.write_bytes(before)
    stable_wrapper.chmod(0o750)
    return RolloutLayout(
        tmp_path,
        source,
        runtime_root,
        state_root,
        current_runtime,
        stable_wrapper,
        current_sha,
        candidate_sha,
        before,
        hash_bytes(before),
        0o750,
    )


def install_trusted_bwrap_result(
    monkeypatch: Any, rollout: Any, tmp_path: Path
) -> None:
    """Keep orchestration suites independent of the host's nested namespace policy."""
    coherence = rollout.coherence

    trusted_stdlib = tmp_path / "trusted-stdlib"
    trusted_stdlib.mkdir()
    (trusted_stdlib / "os.py").write_text("# trusted test stdlib\n", encoding="utf-8")
    trusted_interpreter = Path(
        getattr(sys, "_base_executable", sys.executable)
    ).resolve()
    monkeypatch.setattr(
        coherence,
        "_trusted_python",
        lambda: (trusted_interpreter, (trusted_stdlib,)),
    )

    def run(**kwargs: object) -> bytes:
        dirname = kwargs["venv_dirname"]
        bundle = kwargs["bundle"]
        anchors = json.loads(bytes(kwargs["anchors_bytes"]).decode())
        assert isinstance(dirname, str)
        runtime = bundle.runtime
        sandbox = coherence.invocation.SANDBOX_RUNTIME
        value = {
            "bundle_manifest_sha256": bundle.manifest_sha256,
            "candidate_digest": hashlib.sha256(
                f"{bundle.source_digest}:{bundle.venv_digest}".encode()
            ).hexdigest(),
            "hermes_cli_main_origin": str(sandbox / "hermes_cli/main.py"),
            "kanban_server_origin": str(
                sandbox / "agent/transports/hermes_kanban_mcp_server.py"
            ),
            "pyvenv_cfg_home": anchors["pyvenv_cfg_home"],
            "pyvenv_cfg_sha256": anchors["pyvenv_cfg_sha256"],
            "resolved_interpreter": str(sandbox / dirname / "bin/python"),
            "source_digest": bundle.source_digest,
            "stdlib_roots": anchors["stdlib_roots"],
            "venv_digest": bundle.venv_digest,
            "write_tools": ["kanban_enqueue", "kanban_sync_external_task"],
        }
        return (json.dumps(value, sort_keys=True) + "\n").encode()

    monkeypatch.setattr(coherence.os_sandbox, "run", run)
