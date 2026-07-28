#!/usr/bin/env python3
"""Fail-closed, dry-run-first rollout helper for standalone Kanban MCP."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


FULL_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
FULL_SHA256 = re.compile(r"^[0-9a-f]{64}$")
SNAPSHOT_ID = re.compile(r"^([0-9a-f]{40})-to-([0-9a-f]{40})$")
MANIFEST_FILES = {"manifest.json", "wrapper.before", "wrapper.after"}
MANIFEST_KEYS = {
    "schema_version",
    "created_at",
    "source_repo",
    "runtime_root",
    "state_root",
    "current_runtime",
    "current_runtime_sha",
    "candidate_sha",
    "candidate_path",
    "snapshot_id",
    "stable_wrapper",
    "venv_dirname",
    "venv_interpreter_sha256",
    "venv_interpreter_mode",
    "wrapper_before_sha256",
    "wrapper_after_sha256",
    "wrapper_mode",
    "runtime_path_replacements",
}
SHELL_PATH_CHARS = frozenset("~$*?[]{}")


class RolloutError(RuntimeError):
    """A precondition or guarded operation failed."""


class ReplacementAppliedError(RolloutError):
    """The stable wrapper was replaced, but a post-replacement check failed."""

    def __init__(self, expected_sha256: str, detail: str) -> None:
        super().__init__(detail)
        self.expected_sha256 = expected_sha256


@dataclass(frozen=True)
class Wrapper:
    path: Path
    data: bytes
    mode: int
    sha256: str


@dataclass(frozen=True)
class PrepareContext:
    source_repo: Path
    runtime_root: Path
    state_root: Path
    current_runtime: Path
    current_sha: str
    candidate_sha: str
    candidate_path: Path
    snapshot_id: str
    snapshot_path: Path
    venv_dirname: str
    interpreter_sha256: str
    interpreter_mode: int
    wrapper: Wrapper
    wrapper_after: bytes
    wrapper_after_sha256: str
    replacement_count: int


@dataclass(frozen=True)
class SnapshotContext:
    manifest: dict[str, Any]
    snapshot_path: Path
    source_repo: Path
    runtime_root: Path
    state_root: Path
    current_runtime: Path
    candidate_path: Path
    stable_wrapper: Wrapper
    wrapper_before: bytes
    wrapper_after: bytes


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _require_full_sha(value: str, pattern: re.Pattern[str], label: str) -> str:
    if not pattern.fullmatch(value):
        raise RolloutError(f"{label} must be a lowercase full-length hash")
    return value


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _reject_symlink_components(path: Path, label: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if _lexists(current) and current.is_symlink():
            raise RolloutError(f"{label} contains a symlink component: {current}")


def _canonical_path(raw: str, label: str, *, must_exist: bool) -> Path:
    if "\x00" in raw:
        raise RolloutError(f"{label} contains NUL")
    if any(char in raw for char in SHELL_PATH_CHARS):
        raise RolloutError(f"{label} contains shell expansion characters")
    path = Path(raw)
    if not path.is_absolute():
        raise RolloutError(f"{label} must be absolute")
    if ".." in path.parts or os.path.normpath(raw) != raw:
        raise RolloutError(f"{label} must be lexically canonical")
    _reject_symlink_components(path, label)
    if must_exist and not _lexists(path):
        raise RolloutError(f"{label} does not exist: {path}")
    try:
        resolved = path.resolve(strict=must_exist)
    except OSError as exc:
        raise RolloutError(f"cannot resolve {label}: {exc}") from exc
    if resolved != path:
        raise RolloutError(f"{label} must be canonical: {path}")
    return path


def _home_directory() -> Path:
    try:
        import pwd

        return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve(strict=True)
    except (ImportError, KeyError, OSError):
        return Path.home().resolve(strict=True)


def _validate_managed_root(path: Path, label: str) -> None:
    if not path.is_dir():
        raise RolloutError(f"{label} must be an existing directory")
    if path == Path(path.anchor) or path == _home_directory() or len(path.parts) < 3:
        raise RolloutError(f"{label} is too broad: {path}")


def _strictly_within(path: Path, root: Path, label: str) -> None:
    if path == root or not path.is_relative_to(root):
        raise RolloutError(f"{label} must be strictly inside {root}")


def _validate_roots(runtime_root: Path, state_root: Path) -> None:
    _validate_managed_root(runtime_root, "runtime root")
    _validate_managed_root(state_root, "state root")
    if runtime_root == state_root:
        raise RolloutError("runtime root and state root must differ")
    if runtime_root.is_relative_to(state_root) or state_root.is_relative_to(runtime_root):
        raise RolloutError("runtime root and state root must not be nested")


def _run_git(repo: Path, arguments: Sequence[str]) -> str:
    command = ["git", "--no-optional-locks", "-C", str(repo), *arguments]
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        raise RolloutError(f"git execution failed: {exc}") from exc
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RolloutError(f"git command failed ({' '.join(arguments)}): {detail}")
    return completed.stdout.strip()


def _validate_repo_root(repo: Path, label: str) -> None:
    if not repo.is_dir():
        raise RolloutError(f"{label} must be a directory")
    top = _canonical_path(_run_git(repo, ["rev-parse", "--show-toplevel"]), label, must_exist=True)
    if top != repo:
        raise RolloutError(f"{label} must be the Git worktree root")


def _git_head(repo: Path, label: str) -> str:
    head = _run_git(repo, ["rev-parse", "--verify", "HEAD"])
    return _require_full_sha(head, FULL_GIT_SHA, f"{label} HEAD")


def _validate_clean_worktree(repo: Path, expected_sha: str, label: str) -> None:
    _validate_repo_root(repo, label)
    if _git_head(repo, label) != expected_sha:
        raise RolloutError(f"{label} HEAD does not match expected full SHA")
    status_text = _run_git(repo, ["status", "--porcelain=v1", "--untracked-files=no"])
    if status_text:
        raise RolloutError(f"{label} has dirty tracked files")


def _validate_commit(repo: Path, sha: str) -> None:
    resolved = _run_git(repo, ["rev-parse", "--verify", f"{sha}^{{commit}}"])
    if resolved != sha:
        raise RolloutError("candidate SHA does not name that exact commit object")


def _validate_venv(
    runtime: Path,
    dirname: str,
    *,
    expected_sha256: str | None = None,
    expected_mode: int | None = None,
) -> tuple[str, int]:
    if dirname not in {".venv", "venv"}:
        raise RolloutError("venv dirname must be exactly .venv or venv")
    venv = runtime / dirname
    if not _lexists(venv) or venv.is_symlink() or not venv.is_dir():
        raise RolloutError(f"expected top-level venv directory is missing: {venv}")
    interpreter = venv / "bin" / "python"
    if not interpreter.exists() or not interpreter.is_file() or not os.access(interpreter, os.X_OK):
        raise RolloutError(f"venv interpreter is missing or not executable: {interpreter}")
    try:
        interpreter_data = interpreter.read_bytes()
        interpreter_mode = stat.S_IMODE(interpreter.stat().st_mode)
    except OSError as exc:
        raise RolloutError(f"cannot validate venv interpreter: {exc}") from exc
    interpreter_hash = _sha256(interpreter_data)
    if interpreter_mode & ~0o777:
        raise RolloutError("venv interpreter has unsupported special mode bits")
    if expected_sha256 is not None and interpreter_hash != expected_sha256:
        raise RolloutError("venv interpreter SHA-256 does not match manifest")
    if expected_mode is not None and interpreter_mode != expected_mode:
        raise RolloutError("venv interpreter mode does not match manifest")
    return interpreter_hash, interpreter_mode


def _read_wrapper(path: Path) -> Wrapper:
    if path.is_symlink() or not path.is_file():
        raise RolloutError("stable wrapper must be a regular non-symlink file")
    try:
        info = path.stat()
        data = path.read_bytes()
        data.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RolloutError(f"cannot read executable UTF-8 stable wrapper: {exc}") from exc
    mode = stat.S_IMODE(info.st_mode)
    if not mode & 0o111:
        raise RolloutError("stable wrapper must be executable")
    if mode & ~0o777:
        raise RolloutError("stable wrapper has unsupported special mode bits")
    return Wrapper(path=path, data=data, mode=mode, sha256=_sha256(data))


def _validate_wrapper_contract(data: bytes) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RolloutError("wrapper snapshot is not UTF-8") from exc
    if re.search(r"\bmcp\s+serve-kanban\b", text) is None or "--allow-write" not in text:
        raise RolloutError("wrapper does not contain the standalone write-mode Kanban MCP contract")
    return text


def _prepare_context(args: argparse.Namespace) -> PrepareContext:
    source_repo = _canonical_path(args.source_repo, "source repo", must_exist=True)
    runtime_root = _canonical_path(args.runtime_root, "runtime root", must_exist=True)
    state_root = _canonical_path(args.state_root, "state root", must_exist=True)
    current_runtime = _canonical_path(args.current_runtime, "current runtime", must_exist=True)
    stable_path = _canonical_path(args.stable_wrapper, "stable wrapper", must_exist=True)
    current_sha = _require_full_sha(
        args.expected_current_runtime_sha, FULL_GIT_SHA, "expected current runtime SHA"
    )
    candidate_sha = _require_full_sha(args.candidate_sha, FULL_GIT_SHA, "candidate SHA")
    if current_sha == candidate_sha:
        raise RolloutError("candidate SHA must differ from current runtime SHA")
    expected_wrapper_hash = _require_full_sha(
        args.expected_current_wrapper_sha256, FULL_SHA256, "expected current wrapper SHA-256"
    )

    _validate_roots(runtime_root, state_root)
    _validate_repo_root(source_repo, "source repo")
    if source_repo in {runtime_root, state_root}:
        raise RolloutError("managed roots must not be the source repo root")
    _strictly_within(current_runtime, runtime_root, "current runtime")
    _validate_clean_worktree(current_runtime, current_sha, "current runtime")
    _validate_commit(source_repo, candidate_sha)
    interpreter_hash, interpreter_mode = _validate_venv(current_runtime, args.venv_dirname)

    candidate_path = _canonical_path(
        str(runtime_root / f"hermes-kanban-mcp-{candidate_sha}"),
        "candidate path",
        must_exist=False,
    )
    _strictly_within(candidate_path, runtime_root, "candidate path")
    if _lexists(candidate_path):
        raise RolloutError(f"candidate path already exists: {candidate_path}")

    snapshot_id = f"{current_sha}-to-{candidate_sha}"
    snapshots_root = _canonical_path(
        str(state_root / "snapshots"), "snapshots root", must_exist=False
    )
    snapshot_path = _canonical_path(
        str(snapshots_root / snapshot_id), "snapshot path", must_exist=False
    )
    _strictly_within(snapshot_path, state_root, "snapshot path")
    if _lexists(snapshot_path):
        raise RolloutError(f"snapshot path already exists: {snapshot_path}")
    if _lexists(snapshots_root) and not snapshots_root.is_dir():
        raise RolloutError("snapshots root exists but is not a directory")

    wrapper = _read_wrapper(stable_path)
    if wrapper.sha256 != expected_wrapper_hash:
        raise RolloutError("stable wrapper SHA-256 does not match the explicit guard")
    wrapper_text = _validate_wrapper_contract(wrapper.data)
    current_text = str(current_runtime)
    candidate_text = str(candidate_path)
    if candidate_text in wrapper_text:
        raise RolloutError("stable wrapper already contains the candidate path")
    replacement_count = wrapper_text.count(current_text)
    if replacement_count < 1:
        raise RolloutError("stable wrapper does not contain the exact current runtime path")
    wrapper_after = wrapper.data.replace(current_text.encode(), candidate_text.encode())

    return PrepareContext(
        source_repo=source_repo,
        runtime_root=runtime_root,
        state_root=state_root,
        current_runtime=current_runtime,
        current_sha=current_sha,
        candidate_sha=candidate_sha,
        candidate_path=candidate_path,
        snapshot_id=snapshot_id,
        snapshot_path=snapshot_path,
        venv_dirname=args.venv_dirname,
        interpreter_sha256=interpreter_hash,
        interpreter_mode=interpreter_mode,
        wrapper=wrapper,
        wrapper_after=wrapper_after,
        wrapper_after_sha256=_sha256(wrapper_after),
        replacement_count=replacement_count,
    )


def _manifest(context: PrepareContext) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_repo": str(context.source_repo),
        "runtime_root": str(context.runtime_root),
        "state_root": str(context.state_root),
        "current_runtime": str(context.current_runtime),
        "current_runtime_sha": context.current_sha,
        "candidate_sha": context.candidate_sha,
        "candidate_path": str(context.candidate_path),
        "snapshot_id": context.snapshot_id,
        "stable_wrapper": str(context.wrapper.path),
        "venv_dirname": context.venv_dirname,
        "venv_interpreter_sha256": context.interpreter_sha256,
        "venv_interpreter_mode": context.interpreter_mode,
        "wrapper_before_sha256": context.wrapper.sha256,
        "wrapper_after_sha256": context.wrapper_after_sha256,
        "wrapper_mode": context.wrapper.mode,
        "runtime_path_replacements": context.replacement_count,
    }


def _prepare_plan(context: PrepareContext, apply: bool) -> dict[str, Any]:
    return {
        "command": "prepare",
        "mode": "apply" if apply else "dry-run",
        "candidate_path": str(context.candidate_path),
        "snapshot_id": context.snapshot_id,
        "snapshot_path": str(context.snapshot_path),
        "stable_wrapper": str(context.wrapper.path),
        "wrapper_before_sha256": context.wrapper.sha256,
        "wrapper_after_sha256": context.wrapper_after_sha256,
        "operations": [
            "validate exact Git SHA, tracked cleanliness, paths, venv, wrapper contract and hash",
            f"git worktree add --detach {context.candidate_path} {context.candidate_sha}",
            f"copy only {context.current_runtime / context.venv_dirname} to candidate",
            "create exclusive snapshot with manifest.json, wrapper.before and wrapper.after",
            "leave stable wrapper unchanged",
        ],
    }


def _create_candidate(context: PrepareContext) -> None:
    _run_git(
        context.source_repo,
        ["worktree", "add", "--detach", str(context.candidate_path), context.candidate_sha],
    )
    _validate_clean_worktree(context.candidate_path, context.candidate_sha, "candidate runtime")


def _copy_venv(context: PrepareContext) -> None:
    source = context.current_runtime / context.venv_dirname
    destination = context.candidate_path / context.venv_dirname
    if _lexists(destination):
        raise RolloutError("candidate already contains the selected venv path")
    try:
        shutil.copytree(source, destination, symlinks=True)
    except OSError as exc:
        raise RolloutError(f"cannot copy selected venv: {exc}") from exc
    _validate_venv(
        context.candidate_path,
        context.venv_dirname,
        expected_sha256=context.interpreter_sha256,
        expected_mode=context.interpreter_mode,
    )
    _validate_clean_worktree(context.candidate_path, context.candidate_sha, "candidate runtime")


def _write_exclusive_file(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(data)
            handle.flush()
            os.fchmod(handle.fileno(), 0o600)
            os.fsync(handle.fileno())
    except OSError as exc:
        raise RolloutError(f"cannot create snapshot file {path}: {exc}") from exc


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_snapshot(context: PrepareContext, manifest: dict[str, Any]) -> None:
    snapshots_root = context.snapshot_path.parent
    try:
        if not _lexists(snapshots_root):
            snapshots_root.mkdir(mode=0o700)
            os.chmod(snapshots_root, 0o700)
        context.snapshot_path.mkdir(mode=0o700)
        os.chmod(context.snapshot_path, 0o700)
    except OSError as exc:
        raise RolloutError(f"cannot create exclusive snapshot directory: {exc}") from exc
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _write_exclusive_file(context.snapshot_path / "wrapper.before", context.wrapper.data)
    _write_exclusive_file(context.snapshot_path / "wrapper.after", context.wrapper_after)
    _write_exclusive_file(context.snapshot_path / "manifest.json", manifest_bytes)
    _fsync_directory(context.snapshot_path)
    _fsync_directory(snapshots_root)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RolloutError(f"manifest contains duplicate key: {key}")
        result[key] = value
    return result


def _load_manifest(snapshot_path: Path) -> dict[str, Any]:
    try:
        data = _read_snapshot_file(snapshot_path, "manifest.json")
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RolloutError(f"cannot read manifest: {exc}") from exc
    if not isinstance(value, dict) or set(value) != MANIFEST_KEYS:
        raise RolloutError("manifest has an unexpected schema")
    if value["schema_version"] != 1:
        raise RolloutError("unsupported manifest schema version")
    try:
        created_at = datetime.fromisoformat(value["created_at"])
    except (TypeError, ValueError) as exc:
        raise RolloutError("manifest created_at is invalid") from exc
    if created_at.utcoffset() != timezone.utc.utcoffset(None):
        raise RolloutError("manifest created_at must be UTC")
    integer_fields = {
        "venv_interpreter_mode",
        "wrapper_mode",
        "runtime_path_replacements",
    }
    if any(type(value[field]) is not int for field in integer_fields):
        raise RolloutError("manifest mode/count fields must be integers")
    if not 0 <= value["venv_interpreter_mode"] <= 0o777:
        raise RolloutError("manifest interpreter mode is invalid")
    if not 0 <= value["wrapper_mode"] <= 0o777:
        raise RolloutError("manifest wrapper mode is invalid")
    if value["runtime_path_replacements"] < 1:
        raise RolloutError("manifest replacement count is invalid")
    string_fields = MANIFEST_KEYS - integer_fields - {"schema_version"}
    if any(not isinstance(value[field], str) or not value[field] for field in string_fields):
        raise RolloutError("manifest string fields must be non-empty")
    return value


def _read_snapshot_file(snapshot: Path, name: str) -> bytes:
    path = _canonical_path(str(snapshot / name), f"snapshot {name}", must_exist=True)
    if path.is_symlink() or not path.is_file():
        raise RolloutError(f"snapshot {name} must be a regular non-symlink file")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise RolloutError(f"snapshot {name} must have mode 0600")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise RolloutError(f"cannot read snapshot {name}: {exc}") from exc


def _snapshot_context(args: argparse.Namespace) -> SnapshotContext:
    runtime_root = _canonical_path(args.runtime_root, "runtime root", must_exist=True)
    state_root = _canonical_path(args.state_root, "state root", must_exist=True)
    stable_path = _canonical_path(args.stable_wrapper, "stable wrapper", must_exist=True)
    _validate_roots(runtime_root, state_root)
    match = SNAPSHOT_ID.fullmatch(args.snapshot_id)
    if match is None:
        raise RolloutError("snapshot ID must contain two full Git SHA values")
    current_sha, candidate_sha = match.groups()
    snapshot_path = _canonical_path(
        str(state_root / "snapshots" / args.snapshot_id),
        "snapshot path",
        must_exist=True,
    )
    _strictly_within(snapshot_path, state_root, "snapshot path")
    if snapshot_path.is_symlink() or not snapshot_path.is_dir():
        raise RolloutError("snapshot path must be a regular directory")
    if stat.S_IMODE(snapshot_path.stat().st_mode) != 0o700:
        raise RolloutError("snapshot directory must have mode 0700")
    if {entry.name for entry in snapshot_path.iterdir()} != MANIFEST_FILES:
        raise RolloutError("snapshot must contain exactly the three manifest files")

    manifest = _load_manifest(snapshot_path)

    expected_values = {
        "runtime_root": str(runtime_root),
        "state_root": str(state_root),
        "stable_wrapper": str(stable_path),
        "snapshot_id": args.snapshot_id,
        "current_runtime_sha": current_sha,
        "candidate_sha": candidate_sha,
    }
    for key, expected in expected_values.items():
        if manifest[key] != expected:
            raise RolloutError(f"manifest {key} does not match the requested rollout")

    source_repo = _canonical_path(manifest["source_repo"], "manifest source repo", must_exist=True)
    current_runtime = _canonical_path(
        manifest["current_runtime"], "manifest current runtime", must_exist=True
    )
    candidate_path = _canonical_path(
        manifest["candidate_path"], "manifest candidate path", must_exist=True
    )
    _validate_repo_root(source_repo, "manifest source repo")
    if source_repo in {runtime_root, state_root}:
        raise RolloutError("managed roots must not be the source repo root")
    _strictly_within(current_runtime, runtime_root, "manifest current runtime")
    _strictly_within(candidate_path, runtime_root, "manifest candidate path")
    expected_candidate = runtime_root / f"hermes-kanban-mcp-{candidate_sha}"
    if candidate_path != expected_candidate:
        raise RolloutError("manifest candidate path is not the exact derived path")
    _validate_commit(source_repo, candidate_sha)
    _validate_clean_worktree(current_runtime, current_sha, "current runtime")
    _validate_clean_worktree(candidate_path, candidate_sha, "candidate runtime")

    interpreter_hash = _require_full_sha(
        manifest["venv_interpreter_sha256"], FULL_SHA256, "manifest interpreter SHA-256"
    )
    _validate_venv(
        current_runtime,
        manifest["venv_dirname"],
        expected_sha256=interpreter_hash,
        expected_mode=manifest["venv_interpreter_mode"],
    )
    _validate_venv(
        candidate_path,
        manifest["venv_dirname"],
        expected_sha256=interpreter_hash,
        expected_mode=manifest["venv_interpreter_mode"],
    )

    wrapper_before = _read_snapshot_file(snapshot_path, "wrapper.before")
    wrapper_after = _read_snapshot_file(snapshot_path, "wrapper.after")
    before_hash = _require_full_sha(
        manifest["wrapper_before_sha256"], FULL_SHA256, "manifest wrapper before SHA-256"
    )
    after_hash = _require_full_sha(
        manifest["wrapper_after_sha256"], FULL_SHA256, "manifest wrapper after SHA-256"
    )
    if _sha256(wrapper_before) != before_hash or _sha256(wrapper_after) != after_hash:
        raise RolloutError("snapshot wrapper hash does not match manifest")
    before_text = _validate_wrapper_contract(wrapper_before)
    _validate_wrapper_contract(wrapper_after)
    replacement_count = before_text.count(str(current_runtime))
    if replacement_count != manifest["runtime_path_replacements"] or replacement_count < 1:
        raise RolloutError("manifest runtime replacement count is invalid")
    expected_after = wrapper_before.replace(
        str(current_runtime).encode(), str(candidate_path).encode()
    )
    if wrapper_after != expected_after:
        raise RolloutError("wrapper.after is not the exact guarded transformation")
    if not manifest["wrapper_mode"] & 0o111:
        raise RolloutError("manifest wrapper mode is not executable")

    return SnapshotContext(
        manifest=manifest,
        snapshot_path=snapshot_path,
        source_repo=source_repo,
        runtime_root=runtime_root,
        state_root=state_root,
        current_runtime=current_runtime,
        candidate_path=candidate_path,
        stable_wrapper=_read_wrapper(stable_path),
        wrapper_before=wrapper_before,
        wrapper_after=wrapper_after,
    )


def _switch_plan(command: str, context: SnapshotContext, apply: bool) -> dict[str, Any]:
    manifest = context.manifest
    source_name = "wrapper.after" if command == "switch" else "wrapper.before"
    return {
        "command": command,
        "mode": "apply" if apply else "dry-run",
        "candidate_path": str(context.candidate_path),
        "snapshot_id": manifest["snapshot_id"],
        "snapshot_path": str(context.snapshot_path),
        "stable_wrapper": str(context.stable_wrapper.path),
        "wrapper_before_sha256": manifest["wrapper_before_sha256"],
        "wrapper_after_sha256": manifest["wrapper_after_sha256"],
        "operations": [
            "revalidate manifest, snapshot hashes, exact Git SHA, tracked cleanliness and venv",
            f"write same-directory temporary file from {source_name}",
            "fsync temporary file and preserve executable mode",
            "replace stable wrapper with one os.replace",
            "fsync stable wrapper directory",
            "leave candidate, snapshot, processes and DB unchanged",
        ],
    }


def _atomic_replace(
    stable_wrapper: Path,
    data: bytes,
    mode: int,
    expected_current_hash: str,
) -> None:
    current = _read_wrapper(
        _canonical_path(str(stable_wrapper), "stable wrapper", must_exist=True)
    )
    if current.sha256 != expected_current_hash or current.mode != mode:
        raise RolloutError("stable wrapper changed before atomic replacement")
    temporary_name: str | None = None
    replacement_applied = False
    expected_installed_hash = _sha256(data)
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{stable_wrapper.name}.rollout-",
            dir=stable_wrapper.parent,
        )
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(data)
            handle.flush()
            os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
        latest = _read_wrapper(
            _canonical_path(str(stable_wrapper), "stable wrapper", must_exist=True)
        )
        if latest.sha256 != expected_current_hash or latest.mode != mode:
            raise RolloutError("stable wrapper changed while preparing atomic replacement")
        os.replace(temporary_name, stable_wrapper)
        replacement_applied = True
        _fsync_directory(stable_wrapper.parent)
    except OSError as exc:
        if replacement_applied:
            raise ReplacementAppliedError(
                expected_installed_hash,
                f"stable wrapper directory fsync failed after replacement: {exc}",
            ) from exc
        raise RolloutError(f"atomic wrapper replacement failed: {exc}") from exc
    finally:
        if temporary_name is not None and not replacement_applied:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            except OSError:
                try:
                    print(
                        json.dumps(
                            {
                                "replacement_applied": False,
                                "warning": (
                                    "exact rollout temporary file cleanup failed; "
                                    "inspect the stable wrapper directory"
                                ),
                            },
                            sort_keys=True,
                        ),
                        file=sys.stderr,
                    )
                except OSError:
                    pass


def _run_prepare(args: argparse.Namespace) -> dict[str, Any]:
    context = _prepare_context(args)
    plan = _prepare_plan(context, args.apply)
    if args.apply:
        _create_candidate(context)
        _copy_venv(context)
        manifest = _manifest(context)
        _write_snapshot(context, manifest)
        loaded = _snapshot_context(
            argparse.Namespace(
                runtime_root=str(context.runtime_root),
                state_root=str(context.state_root),
                snapshot_id=context.snapshot_id,
                stable_wrapper=str(context.wrapper.path),
                expected_current_wrapper_sha256=context.wrapper.sha256,
            )
        )
        if loaded.stable_wrapper.sha256 != context.wrapper.sha256:
            raise RolloutError("prepare changed the stable wrapper")
        plan["result"] = "prepared"
    return plan


def _run_switch_or_rollback(args: argparse.Namespace) -> dict[str, Any]:
    context = _snapshot_context(args)
    explicit_hash = _require_full_sha(
        args.expected_current_wrapper_sha256,
        FULL_SHA256,
        "expected current wrapper SHA-256",
    )
    manifest = context.manifest
    if args.command == "switch":
        required_hash = manifest["wrapper_before_sha256"]
        replacement = context.wrapper_after
    else:
        required_hash = manifest["wrapper_after_sha256"]
        replacement = context.wrapper_before
    if explicit_hash != required_hash or context.stable_wrapper.sha256 != required_hash:
        raise RolloutError("stable wrapper SHA-256 does not match command and manifest guards")
    if context.stable_wrapper.mode != manifest["wrapper_mode"]:
        raise RolloutError("stable wrapper mode does not match manifest")
    plan = _switch_plan(args.command, context, args.apply)
    if args.apply:
        _atomic_replace(
            context.stable_wrapper.path,
            replacement,
            manifest["wrapper_mode"],
            required_hash,
        )
        expected_installed = (
            manifest["wrapper_after_sha256"]
            if args.command == "switch"
            else manifest["wrapper_before_sha256"]
        )
        try:
            installed = _read_wrapper(context.stable_wrapper.path)
            if (
                installed.sha256 != expected_installed
                or installed.mode != manifest["wrapper_mode"]
            ):
                raise RolloutError("installed wrapper does not match the snapshot")
        except (RolloutError, OSError) as exc:
            raise ReplacementAppliedError(
                expected_installed,
                f"post-install wrapper verification failed: {exc}",
            ) from exc
        plan["result"] = "switched" if args.command == "switch" else "rolled-back"
    return plan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare", help="plan or prepare candidate and snapshot")
    prepare.add_argument("--source-repo", required=True)
    prepare.add_argument("--runtime-root", required=True)
    prepare.add_argument("--state-root", required=True)
    prepare.add_argument("--current-runtime", required=True)
    prepare.add_argument("--expected-current-runtime-sha", required=True)
    prepare.add_argument("--candidate-sha", required=True)
    prepare.add_argument("--venv-dirname", choices=(".venv", "venv"), required=True)
    prepare.add_argument("--stable-wrapper", required=True)
    prepare.add_argument("--expected-current-wrapper-sha256", required=True)
    prepare.add_argument("--apply", action="store_true")

    for name in ("switch", "rollback"):
        command = commands.add_parser(name, help=f"plan or apply {name}")
        command.add_argument("--runtime-root", required=True)
        command.add_argument("--state-root", required=True)
        command.add_argument("--snapshot-id", required=True)
        command.add_argument("--stable-wrapper", required=True)
        command.add_argument("--expected-current-wrapper-sha256", required=True)
        command.add_argument("--apply", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            plan = _run_prepare(args)
        else:
            plan = _run_switch_or_rollback(args)
    except ReplacementAppliedError as exc:
        print(
            json.dumps(
                {
                    "error": str(exc),
                    "expected_installed_sha256": exc.expected_sha256,
                    "replacement_applied": True,
                    "required_action": "inspect/rollback",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    except (RolloutError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
