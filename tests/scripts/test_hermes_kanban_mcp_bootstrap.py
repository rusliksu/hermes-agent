from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest

from tests.scripts.hermes_kanban_mcp_test_support import (
    install_trusted_bwrap_result,
)

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts" / "hermes_kanban_mcp_rollout.py"
SPEC = importlib.util.spec_from_file_location("hermes_kanban_mcp_rollout", HELPER)
assert SPEC is not None and SPEC.loader is not None
rollout = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rollout
SPEC.loader.exec_module(rollout)


@pytest.fixture(autouse=True)
def _trusted_bwrap_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_trusted_bwrap_result(monkeypatch, rollout, tmp_path)
UNKNOWN_VALUE = "future-private-value=must-not-leak"


def _git(repo: Path, *arguments: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(repo), *arguments],
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip()


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _wrapper_bytes(runtime: Path) -> bytes:
    return (
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        f"export HERMES_HOME={runtime.parent}/hermes-home\n"
        "export HERMES_QUIET=1\n"
        "export HERMES_REDACT_SECRETS=true\n"
        "export PYTHONDONTWRITEBYTECODE=1\n"
        f"exec {runtime}/venv/bin/python"
        ' -m hermes_cli.main mcp serve-kanban --allow-write "$@"\n'
    ).encode()


def _oracle(root: Path) -> tuple[tuple[object, ...], ...]:
    records: list[tuple[object, ...]] = []
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in sorted(dirnames + filenames):
            path = base / name
            info = path.lstat()
            relative = str(path.relative_to(root))
            mode = stat.S_IMODE(info.st_mode)
            if path.is_symlink():
                records.append((relative, "symlink", mode, os.readlink(path), info.st_mtime_ns))
            elif path.is_file():
                records.append(
                    (relative, "file", mode, _hash(path.read_bytes()), info.st_mtime_ns)
                )
            else:
                records.append((relative, "directory", mode, info.st_mtime_ns))
    return tuple(records)


def _replace_option(arguments: list[str], option: str, value: str) -> list[str]:
    changed = list(arguments)
    changed[changed.index(option) + 1] = value
    return changed


def _manifest_bytes(source_commit: str, *, include_unknown: bool = True) -> bytes:
    lines = [
        f"source_commit={source_commit}",
        "deployed_utc=2026-07-28T12:00:00Z",
        "python_version=3.12.3",
        "mcp_version=1",
        "command=mcp serve-kanban --allow-write",
    ]
    if include_unknown:
        lines.append(f"future_key={UNKNOWN_VALUE}")
    return ("\n".join(lines) + "\n").encode()


@dataclass
class BootstrapLayout:
    root: Path
    source: Path
    export_runtime: Path
    export_manifest: Path
    state_root: Path
    stable_wrapper: Path
    source_commit: str
    target_commit: str
    interpreter_sha256: str
    wrapper_before: bytes
    wrapper_before_sha256: str
    wrapper_mode: int

    @property
    def baseline_path(self) -> Path:
        return self.state_root / f"hermes-kanban-mcp-{self.source_commit}"

    @property
    def target_path(self) -> Path:
        return self.state_root / f"hermes-kanban-mcp-{self.target_commit}"

    @property
    def snapshot_id(self) -> str:
        return f"bootstrap-{self.source_commit}"

    @property
    def snapshot_path(self) -> Path:
        return self.state_root / "snapshots" / self.snapshot_id

    def bootstrap_args(
        self,
        *,
        apply: bool = False,
        manifest_sha256: str | None | object = ...,
    ) -> list[str]:
        arguments = [
            "bootstrap-prepare",
            "--source-repo",
            str(self.source),
            "--state-root",
            str(self.state_root),
            "--export-runtime",
            str(self.export_runtime),
            "--export-manifest",
            str(self.export_manifest),
            "--expected-source-commit",
            self.source_commit,
            "--venv-dirname",
            "venv",
            "--expected-venv-interpreter-sha256",
            self.interpreter_sha256,
            "--stable-wrapper",
            str(self.stable_wrapper),
            "--expected-current-wrapper-sha256",
            _hash(self.stable_wrapper.read_bytes()),
        ]
        if manifest_sha256 is ... and apply:
            manifest_sha256 = _hash(self.export_manifest.read_bytes())
        if isinstance(manifest_sha256, str):
            arguments.extend(
                ["--expected-export-manifest-sha256", manifest_sha256]
            )
        if apply:
            arguments.append("--apply")
        return arguments

    def transition_args(
        self, command: str, expected_hash: str, *, apply: bool = False
    ) -> list[str]:
        arguments = [
            command,
            "--runtime-root",
            str(self.state_root),
            "--state-root",
            str(self.state_root),
            "--snapshot-id",
            self.snapshot_id,
            "--stable-wrapper",
            str(self.stable_wrapper),
            "--expected-current-wrapper-sha256",
            expected_hash,
        ]
        if apply:
            arguments.append("--apply")
        return arguments

    def prepare_target_args(self, wrapper_hash: str) -> list[str]:
        return [
            "prepare",
            "--source-repo",
            str(self.source),
            "--runtime-root",
            str(self.state_root),
            "--state-root",
            str(self.state_root),
            "--current-runtime",
            str(self.baseline_path),
            "--expected-current-runtime-sha",
            self.source_commit,
            "--candidate-sha",
            self.target_commit,
            "--venv-dirname",
            "venv",
            "--stable-wrapper",
            str(self.stable_wrapper),
            "--expected-current-wrapper-sha256",
            wrapper_hash,
            "--apply",
        ]


@pytest.fixture
def layout(tmp_path: Path) -> BootstrapLayout:
    source = tmp_path / "source"
    export_runtime = tmp_path / "export-runtime"
    state_root = tmp_path / "dedicated-state"
    stable_wrapper = tmp_path / "bin" / "hermes-kanban-mcp"
    source.mkdir()
    export_runtime.mkdir()
    stable_wrapper.parent.mkdir()

    _git(source, "init")
    _git(source, "config", "user.email", "tests@example.invalid")
    _git(source, "config", "user.name", "Hermes bootstrap tests")
    payload = source / "payload.txt"
    payload.write_text("base\n", encoding="utf-8")
    for package in ("hermes_cli", "agent", "agent/transports"):
        (source / package).mkdir(exist_ok=True)
        (source / package / "__init__.py").write_text("", encoding="utf-8")
    (source / "hermes_cli" / "main.py").write_text("", encoding="utf-8")
    (source / "agent" / "transports" / "hermes_kanban_mcp_server.py").write_text(
        'WRITE_TOOLS = ("kanban_sync_external_task",)\n', encoding="utf-8"
    )
    _git(source, "add", ".")
    _git(source, "commit", "-m", "base")
    base_commit = _git(source, "rev-parse", "HEAD")
    payload.write_text("source\n", encoding="utf-8")
    _git(source, "commit", "-am", "source")
    source_commit = _git(source, "rev-parse", "HEAD")
    _git(source, "checkout", "-b", "target", base_commit)
    payload.write_text("target\n", encoding="utf-8")
    _git(source, "commit", "-am", "target")
    target_commit = _git(source, "rev-parse", "HEAD")

    interpreter = export_runtime / "venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(Path(sys.executable).read_bytes())
    interpreter.chmod(stat.S_IMODE(Path(sys.executable).stat().st_mode))
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    (export_runtime / "venv" / "pyvenv.cfg").write_text(
        f"home = {Path(getattr(sys, '_base_executable', sys.executable)).resolve().parent}\n"
        "include-system-site-packages = false\n"
        f"version = {version}\n",
        encoding="utf-8",
    )
    (
        export_runtime / "venv" / "lib" / f"python{version}" / "site-packages"
    ).mkdir(parents=True)
    (export_runtime / "do-not-copy.txt").write_text(
        "export-only runtime state\n", encoding="utf-8"
    )
    export_manifest = export_runtime / "manifest.txt"
    export_manifest.write_bytes(_manifest_bytes(source_commit))
    wrapper_before = _wrapper_bytes(export_runtime)
    wrapper_mode = 0o750
    stable_wrapper.write_bytes(wrapper_before)
    stable_wrapper.chmod(wrapper_mode)
    return BootstrapLayout(
        root=tmp_path,
        source=source,
        export_runtime=export_runtime,
        export_manifest=export_manifest,
        state_root=state_root,
        stable_wrapper=stable_wrapper,
        source_commit=source_commit,
        target_commit=target_commit,
        interpreter_sha256=_hash(interpreter.read_bytes()),
        wrapper_before=wrapper_before,
        wrapper_before_sha256=_hash(wrapper_before),
        wrapper_mode=wrapper_mode,
    )


def _forbid_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("dry-run called a write primitive")

    monkeypatch.setattr(rollout, "_create_state_root", forbidden)
    monkeypatch.setattr(rollout, "_create_candidate", forbidden)
    monkeypatch.setattr(rollout, "_copy_venv", forbidden)
    monkeypatch.setattr(rollout, "_write_snapshot", forbidden)
    monkeypatch.setattr(rollout.state, "_atomic_replace", forbidden)
    monkeypatch.setattr(rollout.shutil, "copytree", forbidden)
    monkeypatch.setattr(rollout.state.tempfile, "mkstemp", forbidden)
    monkeypatch.setattr(rollout.os, "replace", forbidden)
    original_git = rollout._run_git

    def guarded_git(repo: Path, arguments: list[str]) -> str:
        assert arguments[:2] != ["worktree", "add"]
        return original_git(repo, arguments)

    monkeypatch.setattr(rollout, "_run_git", guarded_git)


def _apply_bootstrap(
    layout: BootstrapLayout, capsys: pytest.CaptureFixture[str]
) -> dict[str, object]:
    assert rollout.main(layout.bootstrap_args(apply=True)) == 0
    return json.loads(capsys.readouterr().out)


def test_bootstrap_dry_run_has_full_no_write_oracle_and_redacts_unknown_values(
    layout: BootstrapLayout,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    before = _oracle(layout.root)
    with monkeypatch.context() as dry_run:
        _forbid_writes(dry_run)
        assert rollout.main(layout.bootstrap_args()) == 0
    raw_output = capsys.readouterr().out
    plan = json.loads(raw_output)
    assert plan["command"] == "bootstrap-prepare"
    assert plan["mode"] == "dry-run"
    assert plan["baseline_path"] == str(layout.baseline_path)
    assert plan["snapshot_id"] == layout.snapshot_id
    assert plan["observed_export_manifest_sha256"] == _hash(
        layout.export_manifest.read_bytes()
    )
    assert UNKNOWN_VALUE not in raw_output
    assert not layout.state_root.exists()
    assert _oracle(layout.root) == before


def test_bootstrap_apply_requires_pinned_manifest_hash_before_state_creation(
    layout: BootstrapLayout, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _oracle(layout.root)
    assert rollout.main(
        layout.bootstrap_args(apply=True, manifest_sha256=None)
    ) == 2
    capsys.readouterr()
    assert _oracle(layout.root) == before
    assert not layout.state_root.exists()


def test_bootstrap_apply_creates_exact_baseline_and_schema_v2_snapshot(
    layout: BootstrapLayout, capsys: pytest.CaptureFixture[str]
) -> None:
    result = _apply_bootstrap(layout, capsys)
    assert result["result"] == "prepared"
    assert stat.S_IMODE(layout.state_root.stat().st_mode) == 0o700
    assert _git(layout.baseline_path, "rev-parse", "HEAD") == layout.source_commit
    assert _git(layout.baseline_path, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"
    assert _git(
        layout.baseline_path, "status", "--porcelain", "--untracked-files=no"
    ) == ""
    assert (layout.baseline_path / "venv" / "bin" / "python").exists()
    assert not (layout.baseline_path / "manifest.txt").exists()
    assert not (layout.baseline_path / "do-not-copy.txt").exists()
    assert layout.stable_wrapper.read_bytes() == layout.wrapper_before
    assert stat.S_IMODE(layout.stable_wrapper.stat().st_mode) == layout.wrapper_mode

    manifest = json.loads((layout.snapshot_path / "manifest.json").read_text())
    assert manifest["schema_version"] == 2
    assert manifest["snapshot_kind"] == "bootstrap"
    assert manifest["runtime_root"] == manifest["state_root"] == str(layout.state_root)
    assert manifest["before_runtime_kind"] == "export"
    assert manifest["before_runtime_sha"] == manifest["after_runtime_sha"]
    assert manifest["before_manifest_path"] == str(layout.export_manifest)
    assert manifest["before_manifest_sha256"] == _hash(
        layout.export_manifest.read_bytes()
    )
    assert manifest["after_runtime_kind"] == "git"
    assert manifest["after_runtime_path"] == str(layout.baseline_path)
    assert manifest["runtime_path_replacements"] == 1
    assert stat.S_IMODE(layout.snapshot_path.stat().st_mode) == 0o700
    assert {
        path.name: stat.S_IMODE(path.stat().st_mode)
        for path in layout.snapshot_path.iterdir()
    } == {
        "manifest.json": 0o600,
        "wrapper.before": 0o600,
        "wrapper.after": 0o600,
    }
    snapshot_bytes = b"".join(
        path.read_bytes() for path in sorted(layout.snapshot_path.iterdir())
    )
    assert UNKNOWN_VALUE.encode() not in snapshot_bytes
    assert (layout.snapshot_path / "wrapper.before").read_bytes() == layout.wrapper_before
    expected_after = layout.wrapper_before.replace(
        str(layout.export_runtime).encode(), str(layout.baseline_path).encode()
    )
    assert (layout.snapshot_path / "wrapper.after").read_bytes() == expected_after


@pytest.mark.parametrize(
    "case",
    [
        "duplicate-key",
        "malformed-line",
        "blank-line",
        "empty-key",
        "nul",
        "invalid-utf8",
        "missing-source",
        "mismatched-source",
    ],
)
def test_export_manifest_format_failures_close_before_writes(
    layout: BootstrapLayout,
    capsys: pytest.CaptureFixture[str],
    case: str,
) -> None:
    valid = _manifest_bytes(layout.source_commit, include_unknown=False)
    mutations = {
        "duplicate-key": valid + f"source_commit={layout.source_commit}\n".encode(),
        "malformed-line": valid + b"broken\n",
        "blank-line": valid + b"\ninvalid=after-blank\n",
        "empty-key": valid + b"=empty\n",
        "nul": valid + b"bad=contains\x00nul\n",
        "invalid-utf8": valid + b"bad=\xff\n",
        "missing-source": b"deployed_utc=2026-07-28T12:00:00Z\n",
        "mismatched-source": _manifest_bytes("0" * 40, include_unknown=False),
    }
    layout.export_manifest.write_bytes(mutations[case])
    before = _oracle(layout.root)
    assert rollout.main(layout.bootstrap_args(apply=True)) == 2
    capsys.readouterr()
    assert _oracle(layout.root) == before
    assert not layout.state_root.exists()
    assert layout.stable_wrapper.read_bytes() == layout.wrapper_before


def test_manifest_raw_hash_change_after_dry_run_is_rejected(
    layout: BootstrapLayout, capsys: pytest.CaptureFixture[str]
) -> None:
    assert rollout.main(layout.bootstrap_args()) == 0
    approved_hash = json.loads(capsys.readouterr().out)[
        "observed_export_manifest_sha256"
    ]
    layout.export_manifest.write_bytes(
        layout.export_manifest.read_bytes().replace(
            b"2026-07-28T12:00:00Z", b"2026-07-28T12:00:01Z"
        )
    )
    before = _oracle(layout.root)
    assert rollout.main(
        layout.bootstrap_args(apply=True, manifest_sha256=approved_hash)
    ) == 2
    capsys.readouterr()
    assert _oracle(layout.root) == before
    assert not layout.state_root.exists()


def test_symlink_manifest_is_rejected_before_writes(
    layout: BootstrapLayout, capsys: pytest.CaptureFixture[str]
) -> None:
    real_manifest = layout.export_runtime / "manifest.real"
    layout.export_manifest.rename(real_manifest)
    layout.export_manifest.symlink_to(real_manifest)
    before = _oracle(layout.root)
    assert rollout.main(layout.bootstrap_args(apply=True)) == 2
    capsys.readouterr()
    assert _oracle(layout.root) == before
    assert not layout.state_root.exists()


@pytest.mark.parametrize(
    "mutation",
    ["existing-root", "relative-root", "nested-export", "manifest-outside", "venv-hash", "two-wrapper-paths"],
)
def test_bootstrap_path_venv_and_wrapper_guards_fail_before_managed_writes(
    layout: BootstrapLayout,
    capsys: pytest.CaptureFixture[str],
    mutation: str,
) -> None:
    arguments = layout.bootstrap_args(apply=True)
    if mutation == "existing-root":
        layout.state_root.mkdir()
    elif mutation == "relative-root":
        arguments = _replace_option(arguments, "--state-root", "relative-state")
    elif mutation == "nested-export":
        arguments = _replace_option(
            arguments, "--state-root", str(layout.export_runtime / "state")
        )
    elif mutation == "manifest-outside":
        outside = layout.root / "manifest.txt"
        outside.write_bytes(layout.export_manifest.read_bytes())
        arguments = _replace_option(arguments, "--export-manifest", str(outside))
    elif mutation == "venv-hash":
        arguments = _replace_option(
            arguments, "--expected-venv-interpreter-sha256", "0" * 64
        )
    else:
        layout.stable_wrapper.write_bytes(
            layout.wrapper_before + b"# " + str(layout.export_runtime).encode() + b"\n"
        )
        arguments = _replace_option(
            arguments,
            "--expected-current-wrapper-sha256",
            _hash(layout.stable_wrapper.read_bytes()),
        )
    before = _oracle(layout.root)
    assert rollout.main(arguments) == 2
    capsys.readouterr()
    assert _oracle(layout.root) == before
    if mutation != "existing-root":
        assert not layout.state_root.exists()


@pytest.mark.parametrize("case", ["symlinked-parent", "broad-target"])
def test_absent_state_root_guards_fail_before_write_primitives(
    layout: BootstrapLayout,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    case: str,
) -> None:
    arguments = layout.bootstrap_args(apply=True)
    if case == "symlinked-parent":
        real_parent = layout.root / "real-state-parent"
        linked_parent = layout.root / "linked-state-parent"
        real_parent.mkdir()
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        arguments = _replace_option(
            arguments, "--state-root", str(linked_parent / "absent-state")
        )
    else:
        broad_target = next(parent for parent in layout.root.parents if len(parent.parts) == 2)
        arguments = _replace_option(
            arguments, "--state-root", str(broad_target)
        )
    before = _oracle(layout.root)
    with monkeypatch.context() as writes:
        _forbid_writes(writes)
        assert rollout.main(arguments) == 2
    capsys.readouterr()
    assert _oracle(layout.root) == before
    assert layout.stable_wrapper.read_bytes() == layout.wrapper_before


@pytest.mark.parametrize("stage", ["state-root", "baseline", "venv", "snapshot"])
def test_partial_bootstrap_failures_preserve_exact_evidence_without_cleanup(
    layout: BootstrapLayout,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stage: str,
) -> None:
    original_write = rollout.state._write_exclusive_file

    def fail_after_file(path: Path, data: bytes) -> None:
        original_write(path, data)
        if path.name == "wrapper.before":
            raise rollout.RolloutError("injected failure after snapshot file")

    with monkeypatch.context() as failure:
        if stage == "state-root":
            failure.setattr(
                rollout,
                "_create_candidate",
                lambda *_args: (_ for _ in ()).throw(
                    rollout.RolloutError("injected worktree failure")
                ),
            )
        elif stage == "baseline":
            failure.setattr(
                rollout,
                "_copy_venv",
                lambda *_args: (_ for _ in ()).throw(
                    rollout.RolloutError("injected venv failure")
                ),
            )
        elif stage == "venv":
            failure.setattr(
                rollout,
                "_write_snapshot",
                lambda *_args: (_ for _ in ()).throw(
                    rollout.RolloutError("injected snapshot failure")
                ),
            )
        else:
            failure.setattr(rollout.state, "_write_exclusive_file", fail_after_file)
        assert rollout.main(layout.bootstrap_args(apply=True)) == 2
    capsys.readouterr()
    assert layout.state_root.exists()
    assert layout.baseline_path.exists() == (stage != "state-root")
    assert (layout.baseline_path / "venv").exists() == (
        stage in {"venv", "snapshot"}
    )
    assert layout.snapshot_path.exists() == (stage == "snapshot")
    if stage == "snapshot":
        assert {path.name for path in layout.snapshot_path.iterdir()} == {
            "wrapper.before"
        }
    assert layout.stable_wrapper.read_bytes() == layout.wrapper_before
    before_retry = _oracle(layout.root)
    assert rollout.main(layout.bootstrap_args(apply=True)) == 2
    capsys.readouterr()
    assert _oracle(layout.root) == before_retry


def test_bootstrap_switch_rollback_and_followup_prepare_share_one_consumer(
    layout: BootstrapLayout,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _apply_bootstrap(layout, capsys)
    wrapper_after = (layout.snapshot_path / "wrapper.after").read_bytes()
    wrapper_after_hash = _hash(wrapper_after)
    replace_calls: list[tuple[Path, Path]] = []
    original_replace = rollout.state.os.replace

    def recording_replace(source: str, destination: Path) -> None:
        replace_calls.append((Path(source), Path(destination)))
        original_replace(source, destination)

    with monkeypatch.context() as transition:
        transition.setattr(rollout.state.os, "replace", recording_replace)
        assert rollout.main(
            layout.transition_args(
                "switch", layout.wrapper_before_sha256, apply=True
            )
        ) == 0
    capsys.readouterr()
    assert len(replace_calls) == 1
    assert layout.stable_wrapper.read_bytes() == wrapper_after
    assert rollout.main(
        layout.transition_args("rollback", wrapper_after_hash, apply=True)
    ) == 0
    capsys.readouterr()
    assert layout.stable_wrapper.read_bytes() == layout.wrapper_before
    assert rollout.main(
        layout.transition_args(
            "switch", layout.wrapper_before_sha256, apply=True
        )
    ) == 0
    capsys.readouterr()
    assert rollout.main(layout.prepare_target_args(wrapper_after_hash)) == 0
    capsys.readouterr()
    target_snapshot = (
        layout.state_root
        / "snapshots"
        / f"{layout.source_commit}-to-{layout.target_commit}"
        / "manifest.json"
    )
    manifest = json.loads(target_snapshot.read_text())
    assert manifest["schema_version"] == 3
    assert manifest["snapshot_kind"] == "rollout"
    assert manifest["wrapper_contract"] == "source-cwd-v1"
    assert manifest["runtime_root"] == manifest["state_root"] == str(layout.state_root)
    assert _git(layout.target_path, "rev-parse", "HEAD") == layout.target_commit


@pytest.mark.parametrize(
    "tamper",
    ["export-manifest", "export-interpreter", "baseline-head", "baseline-dirty", "baseline-interpreter"],
)
def test_bootstrap_transition_revalidates_export_and_baseline_evidence(
    layout: BootstrapLayout,
    capsys: pytest.CaptureFixture[str],
    tamper: str,
) -> None:
    _apply_bootstrap(layout, capsys)
    if tamper == "export-manifest":
        layout.export_manifest.write_bytes(
            layout.export_manifest.read_bytes() + b"new_key=value\n"
        )
    elif tamper == "export-interpreter":
        (layout.export_runtime / "venv" / "bin" / "python").write_bytes(b"changed\n")
    elif tamper == "baseline-head":
        _git(layout.baseline_path, "checkout", "--detach", layout.target_commit)
    elif tamper == "baseline-dirty":
        (layout.baseline_path / "payload.txt").write_text(
            "dirty\n", encoding="utf-8"
        )
    else:
        (layout.baseline_path / "venv" / "bin" / "python").write_bytes(b"changed\n")
    before = layout.stable_wrapper.read_bytes()
    assert rollout.main(
        layout.transition_args(
            "switch", layout.wrapper_before_sha256, apply=True
        )
    ) == 2
    capsys.readouterr()
    assert layout.stable_wrapper.read_bytes() == before
