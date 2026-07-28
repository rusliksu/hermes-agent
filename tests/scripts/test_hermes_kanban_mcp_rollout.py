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


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts" / "hermes_kanban_mcp_rollout.py"
SPEC = importlib.util.spec_from_file_location("hermes_kanban_mcp_rollout", HELPER)
assert SPEC is not None and SPEC.loader is not None
rollout = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rollout
SPEC.loader.exec_module(rollout)


def _git(repo: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(repo), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip()


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _filesystem_oracle(root: Path) -> tuple[tuple[object, ...], ...]:
    records: list[tuple[object, ...]] = []
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in sorted(dirnames + filenames):
            path = base / name
            relative = str(path.relative_to(root))
            info = path.lstat()
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


def _content_oracle(root: Path) -> dict[str, tuple[object, ...]]:
    return {str(record[0]): record[1:-1] for record in _filesystem_oracle(root)}


def _rollout_temporary_paths(layout: Layout) -> tuple[Path, ...]:
    return tuple(
        sorted(
            layout.stable_wrapper.parent.glob(
                f".{layout.stable_wrapper.name}.rollout-*"
            )
        )
    )


def _is_at_or_below(path: str, root: str) -> bool:
    return path == root or path.startswith(f"{root}/")


def _assert_exact_rollout_origins(
    before: dict[str, tuple[object, ...]],
    after: dict[str, tuple[object, ...]],
    layout: Layout,
    *,
    snapshot_expected: bool,
) -> None:
    candidate = str(layout.candidate_path.relative_to(layout.root))
    snapshot = str(layout.snapshot_path.relative_to(layout.root))
    snapshots_root = str(layout.snapshot_path.parent.relative_to(layout.root))
    git_admin = str(
        (
            layout.source
            / ".git"
            / "worktrees"
            / layout.candidate_path.name
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
            and (
                path == snapshots_root
                or _is_at_or_below(path, snapshot)
            )
        )
    }
    assert added == allowed
    assert candidate in added
    assert git_admin in added
    if snapshot_expected:
        assert snapshot in added


@dataclass
class Layout:
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

    def prepare_args(self, *, apply: bool = False) -> list[str]:
        result = [
            "prepare",
            "--source-repo",
            str(self.source),
            "--runtime-root",
            str(self.runtime_root),
            "--state-root",
            str(self.state_root),
            "--current-runtime",
            str(self.current_runtime),
            "--expected-current-runtime-sha",
            self.current_sha,
            "--candidate-sha",
            self.candidate_sha,
            "--venv-dirname",
            ".venv",
            "--stable-wrapper",
            str(self.stable_wrapper),
            "--expected-current-wrapper-sha256",
            self.wrapper_before_hash,
        ]
        if apply:
            result.append("--apply")
        return result

    def transition_args(
        self, command: str, expected_hash: str, *, apply: bool = False
    ) -> list[str]:
        result = [
            command,
            "--runtime-root",
            str(self.runtime_root),
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
            result.append("--apply")
        return result


@pytest.fixture
def layout(tmp_path: Path) -> Layout:
    source = tmp_path / "source"
    runtime_root = tmp_path / "runtimes"
    state_root = tmp_path / "rollout-state"
    current_runtime = runtime_root / "current-runtime"
    stable_wrapper = tmp_path / "bin" / "hermes-kanban-mcp"
    source.mkdir()
    runtime_root.mkdir()
    state_root.mkdir()
    stable_wrapper.parent.mkdir()

    _git(source, "init")
    _git(source, "config", "user.email", "tests@example.invalid")
    _git(source, "config", "user.name", "Hermes rollout tests")
    payload = source / "payload.txt"
    payload.write_text("current\n", encoding="utf-8")
    _git(source, "add", "payload.txt")
    _git(source, "commit", "-m", "current")
    current_sha = _git(source, "rev-parse", "HEAD")
    payload.write_text("candidate\n", encoding="utf-8")
    _git(source, "commit", "-am", "candidate")
    candidate_sha = _git(source, "rev-parse", "HEAD")
    _git(source, "worktree", "add", "--detach", str(current_runtime), current_sha)

    interpreter = current_runtime / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(b"#!/bin/sh\nexit 0\n")
    interpreter.chmod(0o700)
    (current_runtime / "do-not-copy.txt").write_text("untracked runtime data\n", encoding="utf-8")

    wrapper_before = (
        b"#!/bin/sh\nexec "
        + str(interpreter).encode()
        + b" -m hermes_cli.main mcp serve-kanban --allow-write \"$@\"\n"
    )
    wrapper_mode = 0o750
    stable_wrapper.write_bytes(wrapper_before)
    stable_wrapper.chmod(wrapper_mode)
    return Layout(
        root=tmp_path,
        source=source,
        runtime_root=runtime_root,
        state_root=state_root,
        current_runtime=current_runtime,
        stable_wrapper=stable_wrapper,
        current_sha=current_sha,
        candidate_sha=candidate_sha,
        wrapper_before=wrapper_before,
        wrapper_before_hash=_hash(wrapper_before),
        wrapper_mode=wrapper_mode,
    )


def _forbid_write_primitives(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("dry-run called a write primitive")

    monkeypatch.setattr(rollout, "_create_candidate", forbidden)
    monkeypatch.setattr(rollout, "_copy_venv", forbidden)
    monkeypatch.setattr(rollout, "_write_snapshot", forbidden)
    monkeypatch.setattr(rollout, "_atomic_replace", forbidden)
    monkeypatch.setattr(rollout.shutil, "copytree", forbidden)
    monkeypatch.setattr(rollout.tempfile, "mkstemp", forbidden)
    monkeypatch.setattr(rollout.os, "replace", forbidden)
    original_git = rollout._run_git

    def guarded_git(repo: Path, arguments: list[str]) -> str:
        assert arguments[:2] != ["worktree", "add"]
        return original_git(repo, arguments)

    monkeypatch.setattr(rollout, "_run_git", guarded_git)


def _assert_dry_run(
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
    command: str,
) -> dict[str, object]:
    before = _filesystem_oracle(layout.root)
    with monkeypatch.context() as dry_run_patch:
        _forbid_write_primitives(dry_run_patch)
        assert rollout.main(arguments) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["command"] == command
    assert output["mode"] == "dry-run"
    assert _filesystem_oracle(layout.root) == before
    return output


def test_default_dry_runs_are_full_no_write_oracles_and_happy_path_is_reversible(
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rollout_origin = _content_oracle(layout.root)
    prepare_plan = _assert_dry_run(
        layout, monkeypatch, capsys, layout.prepare_args(), "prepare"
    )
    assert prepare_plan["candidate_path"] == str(layout.candidate_path)
    assert prepare_plan["snapshot_id"] == layout.snapshot_id
    assert prepare_plan["wrapper_before_sha256"] == layout.wrapper_before_hash
    assert not layout.candidate_path.exists()
    assert not layout.snapshot_path.exists()

    git_calls: list[tuple[str, ...]] = []
    original_git = rollout._run_git

    def recording_git(repo: Path, arguments: list[str]) -> str:
        git_calls.append(tuple(arguments))
        return original_git(repo, arguments)

    with monkeypatch.context() as apply_patch:
        apply_patch.setattr(rollout, "_run_git", recording_git)
        assert rollout.main(layout.prepare_args(apply=True)) == 0
    capsys.readouterr()
    assert all(not ({"reset", "clean", "rm"} & set(call)) for call in git_calls)
    assert layout.stable_wrapper.read_bytes() == layout.wrapper_before
    assert stat.S_IMODE(layout.stable_wrapper.stat().st_mode) == layout.wrapper_mode
    assert _git(layout.candidate_path, "rev-parse", "HEAD") == layout.candidate_sha
    assert _git(layout.candidate_path, "status", "--porcelain", "--untracked-files=no") == ""
    assert (layout.candidate_path / ".venv" / "bin" / "python").exists()
    assert not (layout.candidate_path / "do-not-copy.txt").exists()
    assert {path.name for path in layout.snapshot_path.iterdir()} == {
        "manifest.json",
        "wrapper.before",
        "wrapper.after",
    }
    assert stat.S_IMODE(layout.snapshot_path.stat().st_mode) == 0o700
    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o600
        for path in layout.snapshot_path.iterdir()
    )
    manifest = json.loads((layout.snapshot_path / "manifest.json").read_text())
    wrapper_after = (layout.snapshot_path / "wrapper.after").read_bytes()
    wrapper_after_hash = _hash(wrapper_after)
    assert manifest["wrapper_before_sha256"] == layout.wrapper_before_hash
    assert manifest["wrapper_after_sha256"] == wrapper_after_hash
    for key in (
        "source_repo",
        "runtime_root",
        "state_root",
        "current_runtime",
        "candidate_path",
        "stable_wrapper",
    ):
        assert Path(manifest[key]).is_relative_to(layout.root)
    assert (layout.snapshot_path / "wrapper.before").read_bytes() == layout.wrapper_before

    _assert_dry_run(
        layout,
        monkeypatch,
        capsys,
        layout.transition_args("switch", layout.wrapper_before_hash),
        "switch",
    )
    replace_calls: list[tuple[Path, Path]] = []
    fsync_calls: list[int] = []
    original_replace = rollout.os.replace
    original_fsync = rollout.os.fsync

    def recording_replace(source: str, destination: Path) -> None:
        replace_calls.append((Path(source), Path(destination)))
        original_replace(source, destination)

    def recording_fsync(descriptor: int) -> None:
        fsync_calls.append(descriptor)
        original_fsync(descriptor)

    with monkeypatch.context() as atomic_patch:
        atomic_patch.setattr(rollout.os, "replace", recording_replace)
        atomic_patch.setattr(rollout.os, "fsync", recording_fsync)
        assert rollout.main(
            layout.transition_args("switch", layout.wrapper_before_hash, apply=True)
        ) == 0
    capsys.readouterr()
    assert len(replace_calls) == 1
    assert replace_calls[0][0].parent == layout.stable_wrapper.parent
    assert replace_calls[0][1] == layout.stable_wrapper
    assert len(fsync_calls) == 2
    assert layout.stable_wrapper.read_bytes() == wrapper_after
    assert stat.S_IMODE(layout.stable_wrapper.stat().st_mode) == layout.wrapper_mode

    stale_before = _filesystem_oracle(layout.root)
    assert rollout.main(
        layout.transition_args("rollback", layout.wrapper_before_hash, apply=True)
    ) == 2
    capsys.readouterr()
    assert _filesystem_oracle(layout.root) == stale_before

    _assert_dry_run(
        layout,
        monkeypatch,
        capsys,
        layout.transition_args("rollback", wrapper_after_hash),
        "rollback",
    )
    assert rollout.main(
        layout.transition_args("rollback", wrapper_after_hash, apply=True)
    ) == 0
    capsys.readouterr()
    assert layout.stable_wrapper.read_bytes() == layout.wrapper_before
    assert stat.S_IMODE(layout.stable_wrapper.stat().st_mode) == layout.wrapper_mode
    assert layout.candidate_path.exists()
    assert layout.snapshot_path.exists()
    _assert_exact_rollout_origins(
        rollout_origin,
        _content_oracle(layout.root),
        layout,
        snapshot_expected=True,
    )


@pytest.mark.parametrize(
    "mutate_args",
    [
        lambda layout, args: _replace_option(
            args, "--expected-current-wrapper-sha256", "0" * 64
        ),
        lambda layout, args: _replace_option(
            args, "--expected-current-runtime-sha", layout.candidate_sha
        ),
        lambda layout, args: _replace_option(
            args, "--candidate-sha", layout.current_sha
        ),
        lambda layout, args: _replace_option(args, "--runtime-root", "runtimes"),
        lambda layout, args: _replace_option(
            args, "--state-root", str(layout.runtime_root)
        ),
        lambda layout, args: _replace_option(
            args, "--runtime-root", str(layout.source)
        ),
        lambda layout, args: _replace_option(
            args, "--current-runtime", str(layout.source)
        ),
        lambda layout, args: _replace_option(
            args, "--runtime-root", str(layout.root)
        ),
    ],
    ids=[
        "stale-wrapper-hash",
        "stale-current-runtime-sha",
        "candidate-matches-current",
        "relative-path",
        "same-managed-roots",
        "source-repo-as-managed-root",
        "current-runtime-outside-root",
        "broad-nested-runtime-root",
    ],
)
def test_prepare_guards_fail_before_writes(
    layout: Layout,
    capsys: pytest.CaptureFixture[str],
    mutate_args: Callable[[Layout, list[str]], list[str]],
) -> None:
    arguments = mutate_args(layout, layout.prepare_args(apply=True))
    before = _filesystem_oracle(layout.root)
    assert rollout.main(arguments) == 2
    capsys.readouterr()
    assert _filesystem_oracle(layout.root) == before
    assert layout.stable_wrapper.read_bytes() == layout.wrapper_before


def _replace_option(arguments: list[str], option: str, value: str) -> list[str]:
    changed = list(arguments)
    changed[changed.index(option) + 1] = value
    return changed


def test_existing_candidate_and_symlink_wrapper_fail_closed(
    layout: Layout, capsys: pytest.CaptureFixture[str]
) -> None:
    layout.candidate_path.mkdir()
    before = layout.stable_wrapper.read_bytes()
    assert rollout.main(layout.prepare_args(apply=True)) == 2
    capsys.readouterr()
    assert layout.stable_wrapper.read_bytes() == before
    assert not layout.snapshot_path.exists()

    symlink_wrapper = layout.root / "bin" / "symlink-wrapper"
    symlink_wrapper.symlink_to(layout.stable_wrapper)
    arguments = _replace_option(
        layout.prepare_args(apply=True), "--stable-wrapper", str(symlink_wrapper)
    )
    assert rollout.main(arguments) == 2
    capsys.readouterr()
    assert layout.stable_wrapper.read_bytes() == before


def test_existing_snapshot_and_symlink_managed_paths_fail_closed(
    layout: Layout, capsys: pytest.CaptureFixture[str]
) -> None:
    layout.snapshot_path.mkdir(parents=True)
    before = _filesystem_oracle(layout.root)
    assert rollout.main(layout.prepare_args(apply=True)) == 2
    capsys.readouterr()
    assert _filesystem_oracle(layout.root) == before

    runtime_link = layout.root / "runtime-link"
    runtime_link.symlink_to(layout.runtime_root, target_is_directory=True)
    arguments = _replace_option(
        layout.prepare_args(apply=True), "--runtime-root", str(runtime_link)
    )
    before = _filesystem_oracle(layout.root)
    assert rollout.main(arguments) == 2
    capsys.readouterr()
    assert _filesystem_oracle(layout.root) == before


@pytest.mark.parametrize("target", ["candidate-parent", "snapshot-parent"])
def test_future_candidate_and_snapshot_parent_symlinks_fail_before_writes(
    layout: Layout,
    capsys: pytest.CaptureFixture[str],
    target: str,
) -> None:
    if target == "candidate-parent":
        real_runtime_root = layout.root / "real-runtimes"
        layout.runtime_root.rename(real_runtime_root)
        layout.runtime_root.symlink_to(real_runtime_root, target_is_directory=True)
    else:
        outside_snapshots = layout.root / "outside-snapshots"
        outside_snapshots.mkdir()
        (layout.state_root / "snapshots").symlink_to(
            outside_snapshots, target_is_directory=True
        )
    before = _filesystem_oracle(layout.root)
    assert rollout.main(layout.prepare_args(apply=True)) == 2
    capsys.readouterr()
    assert _filesystem_oracle(layout.root) == before
    assert layout.stable_wrapper.read_bytes() == layout.wrapper_before
    assert not os.path.lexists(layout.candidate_path)
    assert _rollout_temporary_paths(layout) == ()


def _replace_manifest_with_symlink(layout: Layout) -> None:
    manifest = layout.snapshot_path / "manifest.json"
    moved = layout.state_root / "manifest-outside-snapshot.json"
    manifest.rename(moved)
    manifest.symlink_to(moved)


def _tamper_candidate_head(layout: Layout) -> None:
    _git(layout.candidate_path, "checkout", "--detach", layout.current_sha)


def _tamper_current_tracked_file(layout: Layout) -> None:
    (layout.current_runtime / "payload.txt").write_text(
        "dirty current runtime\n", encoding="utf-8"
    )


def _tamper_snapshot_file_mode(layout: Layout) -> None:
    (layout.snapshot_path / "wrapper.after").chmod(0o640)


def _tamper_snapshot_directory_mode(layout: Layout) -> None:
    layout.snapshot_path.chmod(0o750)


@pytest.mark.parametrize(
    "tamper",
    [
        lambda layout: (layout.snapshot_path / "manifest.json").write_text(
            "{}", encoding="utf-8"
        ),
        lambda layout: (layout.snapshot_path / "wrapper.after").write_bytes(b"tampered\n"),
        lambda layout: (layout.candidate_path / "payload.txt").write_text(
            "dirty candidate\n", encoding="utf-8"
        ),
        lambda layout: (layout.candidate_path / ".venv" / "bin" / "python").write_bytes(
            b"#!/bin/sh\nexit 9\n"
        ),
        lambda layout: layout.stable_wrapper.write_bytes(
            layout.wrapper_before + b"# external wrapper change\n"
        ),
        _replace_manifest_with_symlink,
        _tamper_snapshot_file_mode,
        _tamper_snapshot_directory_mode,
        _tamper_candidate_head,
        _tamper_current_tracked_file,
    ],
    ids=[
        "manifest",
        "snapshot",
        "candidate",
        "venv-interpreter",
        "stable-wrapper",
        "manifest-symlink",
        "snapshot-file-mode",
        "snapshot-directory-mode",
        "candidate-head",
        "current-runtime-dirty",
    ],
)
def test_switch_rejects_tampered_prepared_state_without_changing_wrapper(
    layout: Layout,
    capsys: pytest.CaptureFixture[str],
    tamper: Callable[[Layout], object],
) -> None:
    assert rollout.main(layout.prepare_args(apply=True)) == 0
    capsys.readouterr()
    tamper(layout)
    before = layout.stable_wrapper.read_bytes()
    before_mode = stat.S_IMODE(layout.stable_wrapper.stat().st_mode)
    assert rollout.main(
        layout.transition_args("switch", layout.wrapper_before_hash, apply=True)
    ) == 2
    capsys.readouterr()
    assert layout.stable_wrapper.read_bytes() == before
    assert stat.S_IMODE(layout.stable_wrapper.stat().st_mode) == before_mode


@pytest.mark.parametrize(
    "field",
    [
        "current_runtime",
        "candidate_path",
        "source_repo",
        "runtime_root",
        "state_root",
        "stable_wrapper",
    ],
)
def test_switch_rejects_schema_valid_manifest_path_substitutions(
    layout: Layout,
    capsys: pytest.CaptureFixture[str],
    field: str,
) -> None:
    assert rollout.main(layout.prepare_args(apply=True)) == 0
    capsys.readouterr()
    other_source = layout.root / "other-source"
    other_source.mkdir()
    _git(other_source, "init")
    _git(other_source, "config", "user.email", "tests@example.invalid")
    _git(other_source, "config", "user.name", "Hermes rollout tests")
    (other_source / "other.txt").write_text("other\n", encoding="utf-8")
    _git(other_source, "add", "other.txt")
    _git(other_source, "commit", "-m", "other")
    alternate_wrapper = layout.stable_wrapper.parent / "alternate-wrapper"
    alternate_wrapper.write_bytes(layout.wrapper_before)
    alternate_wrapper.chmod(layout.wrapper_mode)
    substitutions = {
        "current_runtime": str(layout.candidate_path),
        "candidate_path": str(layout.current_runtime),
        "source_repo": str(other_source),
        "runtime_root": str(layout.state_root),
        "state_root": str(layout.runtime_root),
        "stable_wrapper": str(alternate_wrapper),
    }
    manifest_path = layout.snapshot_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field] = substitutions[field]
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    before = layout.stable_wrapper.read_bytes()
    assert rollout.main(
        layout.transition_args("switch", layout.wrapper_before_hash, apply=True)
    ) == 2
    capsys.readouterr()
    assert layout.stable_wrapper.read_bytes() == before
    assert stat.S_IMODE(layout.stable_wrapper.stat().st_mode) == layout.wrapper_mode
    assert _rollout_temporary_paths(layout) == ()


@pytest.mark.parametrize(
    "stage",
    ["candidate", "venv", "snapshot-file"],
)
def test_partial_prepare_failures_keep_only_exact_fail_closed_artifacts(
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stage: str,
) -> None:
    before = _content_oracle(layout.root)
    original_write = rollout._write_exclusive_file

    def fail_after_snapshot_file(path: Path, data: bytes) -> None:
        original_write(path, data)
        if path.name == "wrapper.before":
            raise rollout.RolloutError("injected failure after snapshot file")

    with monkeypatch.context() as failure:
        if stage == "candidate":
            failure.setattr(
                rollout,
                "_copy_venv",
                lambda *_args: (_ for _ in ()).throw(
                    rollout.RolloutError("injected failure after candidate")
                ),
            )
        elif stage == "venv":
            failure.setattr(
                rollout,
                "_write_snapshot",
                lambda *_args: (_ for _ in ()).throw(
                    rollout.RolloutError("injected failure after venv")
                ),
            )
        else:
            failure.setattr(rollout, "_write_exclusive_file", fail_after_snapshot_file)
        assert rollout.main(layout.prepare_args(apply=True)) == 2
    capsys.readouterr()

    assert layout.stable_wrapper.read_bytes() == layout.wrapper_before
    assert stat.S_IMODE(layout.stable_wrapper.stat().st_mode) == layout.wrapper_mode
    assert layout.candidate_path.exists()
    assert (layout.candidate_path / ".venv").exists() == (stage != "candidate")
    assert layout.snapshot_path.exists() == (stage == "snapshot-file")
    if stage == "snapshot-file":
        assert {
            path.name for path in layout.snapshot_path.iterdir()
        } == {"wrapper.before"}
    _assert_exact_rollout_origins(
        before,
        _content_oracle(layout.root),
        layout,
        snapshot_expected=stage == "snapshot-file",
    )
    assert _rollout_temporary_paths(layout) == ()

    before_retry = _filesystem_oracle(layout.root)
    assert rollout.main(layout.prepare_args(apply=True)) == 2
    capsys.readouterr()
    assert _filesystem_oracle(layout.root) == before_retry
    assert layout.stable_wrapper.read_bytes() == layout.wrapper_before


def test_worktree_creation_failure_never_changes_stable_wrapper(
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    original_git = rollout._run_git

    def fail_worktree(repo: Path, arguments: list[str]) -> str:
        if arguments[:2] == ["worktree", "add"]:
            raise rollout.RolloutError("injected worktree failure")
        return original_git(repo, arguments)

    with monkeypatch.context() as failure:
        failure.setattr(rollout, "_run_git", fail_worktree)
        assert rollout.main(layout.prepare_args(apply=True)) == 2
    capsys.readouterr()
    assert layout.stable_wrapper.read_bytes() == layout.wrapper_before
    assert not layout.candidate_path.exists()
    assert _rollout_temporary_paths(layout) == ()


@pytest.mark.parametrize(
    "failure_point",
    ["fchmod", "file-fsync", "latest-guard", "replace"],
)
def test_atomic_pre_replace_failures_remove_exact_temporary_file(
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure_point: str,
) -> None:
    assert rollout.main(layout.prepare_args(apply=True)) == 0
    capsys.readouterr()
    before = layout.stable_wrapper.read_bytes()
    original_read = rollout._read_wrapper
    stable_reads = 0

    def fail_latest_guard(path: Path) -> rollout.Wrapper:
        nonlocal stable_reads
        result = original_read(path)
        if path == layout.stable_wrapper:
            stable_reads += 1
            if stable_reads == 3:
                raise rollout.RolloutError("injected latest guard failure")
        return result

    with monkeypatch.context() as failure:
        if failure_point == "fchmod":
            failure.setattr(
                rollout.os,
                "fchmod",
                lambda *_args: (_ for _ in ()).throw(
                    OSError("injected fchmod failure")
                ),
            )
        elif failure_point == "file-fsync":
            failure.setattr(
                rollout.os,
                "fsync",
                lambda *_args: (_ for _ in ()).throw(
                    OSError("injected file fsync failure")
                ),
            )
        elif failure_point == "latest-guard":
            failure.setattr(rollout, "_read_wrapper", fail_latest_guard)
        else:
            failure.setattr(
                rollout.os,
                "replace",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    OSError("injected replace failure")
                ),
            )
        assert rollout.main(
            layout.transition_args("switch", layout.wrapper_before_hash, apply=True)
        ) == 2
    capsys.readouterr()
    assert layout.stable_wrapper.read_bytes() == before
    assert stat.S_IMODE(layout.stable_wrapper.stat().st_mode) == layout.wrapper_mode
    assert _rollout_temporary_paths(layout) == ()


def test_atomic_cleanup_failure_preserves_primary_error_and_warns_safely(
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert rollout.main(layout.prepare_args(apply=True)) == 0
    capsys.readouterr()
    created: list[Path] = []
    original_mkstemp = rollout.tempfile.mkstemp

    def recording_mkstemp(*args: object, **kwargs: object) -> tuple[int, str]:
        descriptor, name = original_mkstemp(*args, **kwargs)
        created.append(Path(name))
        return descriptor, name

    with monkeypatch.context() as failure:
        failure.setattr(rollout.tempfile, "mkstemp", recording_mkstemp)
        failure.setattr(
            rollout.os,
            "replace",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("injected primary replace failure")
            ),
        )
        failure.setattr(
            rollout.os,
            "unlink",
            lambda *_args: (_ for _ in ()).throw(
                OSError("injected exact cleanup failure")
            ),
        )
        assert rollout.main(
            layout.transition_args("switch", layout.wrapper_before_hash, apply=True)
        ) == 2
    stderr = capsys.readouterr().err
    assert "injected primary replace failure" in stderr
    warning = json.loads(stderr.splitlines()[0])
    assert warning == {
        "replacement_applied": False,
        "warning": (
            "exact rollout temporary file cleanup failed; "
            "inspect the stable wrapper directory"
        ),
    }
    assert created and created[0].exists()
    created[0].unlink()
    assert _rollout_temporary_paths(layout) == ()
    assert layout.stable_wrapper.read_bytes() == layout.wrapper_before


@pytest.mark.parametrize(
    "failure_point",
    ["directory-fsync", "post-install-verification"],
)
def test_post_replace_failures_report_applied_state_and_expected_hash(
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure_point: str,
) -> None:
    assert rollout.main(layout.prepare_args(apply=True)) == 0
    capsys.readouterr()
    wrapper_after = (layout.snapshot_path / "wrapper.after").read_bytes()
    expected_hash = _hash(wrapper_after)
    original_fsync = rollout.os.fsync
    original_read = rollout._read_wrapper
    fsync_calls = 0

    def fail_directory_fsync(descriptor: int) -> None:
        nonlocal fsync_calls
        fsync_calls += 1
        if fsync_calls == 2:
            raise OSError("injected directory fsync failure")
        original_fsync(descriptor)

    def fail_installed_read(path: Path) -> rollout.Wrapper:
        result = original_read(path)
        if path == layout.stable_wrapper and result.sha256 == expected_hash:
            raise rollout.RolloutError("injected post-install verification failure")
        return result

    with monkeypatch.context() as failure:
        if failure_point == "directory-fsync":
            failure.setattr(rollout.os, "fsync", fail_directory_fsync)
        else:
            failure.setattr(rollout, "_read_wrapper", fail_installed_read)
        assert rollout.main(
            layout.transition_args("switch", layout.wrapper_before_hash, apply=True)
        ) == 2
    stderr = capsys.readouterr().err
    error = json.loads(stderr)
    assert error["replacement_applied"] is True
    assert error["expected_installed_sha256"] == expected_hash
    assert error["required_action"] == "inspect/rollback"
    expected_detail = (
        "directory fsync"
        if failure_point == "directory-fsync"
        else "post-install wrapper verification"
    )
    assert expected_detail in error["error"]
    assert layout.stable_wrapper.read_bytes() == wrapper_after
    assert stat.S_IMODE(layout.stable_wrapper.stat().st_mode) == layout.wrapper_mode
    assert _rollout_temporary_paths(layout) == ()
