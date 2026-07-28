"""Schema v2, snapshot validation, and atomic transitions for Kanban MCP rollout."""

from __future__ import annotations

import hashlib
import json
import os
import re
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
ROLLOUT_SNAPSHOT_ID = re.compile(r"^([0-9a-f]{40})-to-([0-9a-f]{40})$")
BOOTSTRAP_SNAPSHOT_ID = re.compile(r"^bootstrap-([0-9a-f]{40})$")
MANIFEST_FILES = {"manifest.json", "wrapper.before", "wrapper.after"}
MANIFEST_KEYS = {
    "schema_version",
    "snapshot_kind",
    "created_at",
    "source_repo",
    "runtime_root",
    "state_root",
    "snapshot_id",
    "stable_wrapper",
    "before_runtime_kind",
    "before_runtime_path",
    "before_runtime_sha",
    "before_manifest_path",
    "before_manifest_sha256",
    "after_runtime_kind",
    "after_runtime_path",
    "after_runtime_sha",
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
    """The wrapper was replaced, but a post-replacement check failed."""

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
class ExportManifest:
    path: Path
    sha256: str
    source_commit: str


@dataclass(frozen=True)
class SnapshotContext:
    manifest: dict[str, Any]
    snapshot_path: Path
    source_repo: Path
    runtime_root: Path
    state_root: Path
    before_runtime: Path
    after_runtime: Path
    stable_wrapper: Wrapper
    wrapper_before: bytes
    wrapper_after: bytes


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require_full_sha(value: str, pattern: re.Pattern[str], label: str) -> str:
    if not pattern.fullmatch(value):
        raise RolloutError(f"{label} must be a lowercase full-length hash")
    return value


def lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _reject_symlink_components(path: Path, label: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if lexists(current) and current.is_symlink():
            raise RolloutError(f"{label} contains a symlink component: {current}")


def canonical_path(raw: str, label: str, *, must_exist: bool) -> Path:
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
    if must_exist and not lexists(path):
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


def _validate_managed_target(path: Path, label: str) -> None:
    if path == Path(path.anchor) or path == _home_directory() or len(path.parts) < 3:
        raise RolloutError(f"{label} is too broad: {path}")


def validate_managed_root(path: Path, label: str) -> None:
    if not path.is_dir():
        raise RolloutError(f"{label} must be an existing directory")
    _validate_managed_target(path, label)


def validate_absent_state_root(path: Path) -> None:
    _validate_managed_target(path, "state root")
    if lexists(path):
        raise RolloutError(f"state root must not exist: {path}")
    parent = canonical_path(str(path.parent), "state root parent", must_exist=True)
    if not parent.is_dir():
        raise RolloutError("state root parent must be an existing directory")


def strictly_within(path: Path, root: Path, label: str) -> None:
    if path == root or not path.is_relative_to(root):
        raise RolloutError(f"{label} must be strictly inside {root}")


def validate_roots(runtime_root: Path, state_root: Path) -> None:
    validate_managed_root(runtime_root, "runtime root")
    validate_managed_root(state_root, "state root")
    if runtime_root != state_root and (
        runtime_root.is_relative_to(state_root)
        or state_root.is_relative_to(runtime_root)
    ):
        raise RolloutError(
            "different runtime and state roots must not be nested"
        )


def run_git(repo: Path, arguments: Sequence[str]) -> str:
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


def validate_repo_root(repo: Path, label: str) -> None:
    if not repo.is_dir():
        raise RolloutError(f"{label} must be a directory")
    top = canonical_path(run_git(repo, ["rev-parse", "--show-toplevel"]), label, must_exist=True)
    if top != repo:
        raise RolloutError(f"{label} must be the Git worktree root")


def git_head(repo: Path, label: str) -> str:
    head = run_git(repo, ["rev-parse", "--verify", "HEAD"])
    return require_full_sha(head, FULL_GIT_SHA, f"{label} HEAD")


def validate_clean_worktree(repo: Path, expected_sha: str, label: str) -> None:
    validate_repo_root(repo, label)
    if git_head(repo, label) != expected_sha:
        raise RolloutError(f"{label} HEAD does not match expected full SHA")
    status_text = run_git(repo, ["status", "--porcelain=v1", "--untracked-files=no"])
    if status_text:
        raise RolloutError(f"{label} has dirty tracked files")


def validate_commit(repo: Path, sha: str, label: str = "commit SHA") -> None:
    resolved = run_git(repo, ["rev-parse", "--verify", f"{sha}^{{commit}}"])
    if resolved != sha:
        raise RolloutError(f"{label} does not name that exact commit object")


def validate_venv(
    runtime: Path,
    dirname: str,
    *,
    expected_sha256: str | None = None,
    expected_mode: int | None = None,
) -> tuple[str, int]:
    if dirname not in {".venv", "venv"}:
        raise RolloutError("venv dirname must be exactly .venv or venv")
    venv = runtime / dirname
    if not lexists(venv) or venv.is_symlink() or not venv.is_dir():
        raise RolloutError(f"expected top-level venv directory is missing: {venv}")
    interpreter = venv / "bin" / "python"
    if not interpreter.exists() or not interpreter.is_file() or not os.access(interpreter, os.X_OK):
        raise RolloutError(f"venv interpreter is missing or not executable: {interpreter}")
    try:
        data = interpreter.read_bytes()
        mode = stat.S_IMODE(interpreter.stat().st_mode)
    except OSError as exc:
        raise RolloutError(f"cannot validate venv interpreter: {exc}") from exc
    digest = sha256(data)
    if mode & ~0o777:
        raise RolloutError("venv interpreter has unsupported special mode bits")
    if expected_sha256 is not None and digest != expected_sha256:
        raise RolloutError("venv interpreter SHA-256 does not match expected evidence")
    if expected_mode is not None and mode != expected_mode:
        raise RolloutError("venv interpreter mode does not match expected evidence")
    return digest, mode


def read_wrapper(path: Path) -> Wrapper:
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
    return Wrapper(path=path, data=data, mode=mode, sha256=sha256(data))


def validate_wrapper_contract(data: bytes) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RolloutError("wrapper snapshot is not UTF-8") from exc
    if re.search(r"\bmcp\s+serve-kanban\b", text) is None or "--allow-write" not in text:
        raise RolloutError("wrapper does not contain the standalone write-mode Kanban MCP contract")
    return text


def read_export_manifest(
    path: Path,
    export_runtime: Path,
    expected_source_commit: str,
    expected_sha256: str | None = None,
) -> ExportManifest:
    strictly_within(path, export_runtime, "export manifest")
    if path.is_symlink() or not path.is_file():
        raise RolloutError("export manifest must be a regular non-symlink file")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RolloutError(f"cannot read export manifest: {exc}") from exc
    if b"\x00" in raw:
        raise RolloutError("export manifest contains NUL")
    digest = sha256(raw)
    if expected_sha256 is not None and digest != expected_sha256:
        raise RolloutError("export manifest SHA-256 does not match expected evidence")
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise RolloutError("export manifest is not valid UTF-8") from exc
    values: dict[str, str] = {}
    for line in lines:
        if not line or "=" not in line:
            raise RolloutError("export manifest contains a blank or malformed line")
        key, value = line.split("=", 1)
        if not key:
            raise RolloutError("export manifest contains an empty key")
        if key in values:
            raise RolloutError(f"export manifest contains duplicate key: {key}")
        values[key] = value
    source_commit = values.get("source_commit")
    if source_commit is None:
        raise RolloutError("export manifest is missing source_commit")
    require_full_sha(source_commit, FULL_GIT_SHA, "export manifest source_commit")
    if source_commit != expected_source_commit:
        raise RolloutError("export manifest source_commit does not match expected commit")
    return ExportManifest(path=path, sha256=digest, source_commit=source_commit)


def make_manifest(
    *,
    snapshot_kind: str,
    source_repo: Path,
    runtime_root: Path,
    state_root: Path,
    snapshot_id: str,
    stable_wrapper: Path,
    before_runtime_kind: str,
    before_runtime_path: Path,
    before_runtime_sha: str,
    before_manifest_path: Path | None,
    before_manifest_sha256: str | None,
    after_runtime_path: Path,
    after_runtime_sha: str,
    venv_dirname: str,
    venv_interpreter_sha256: str,
    venv_interpreter_mode: int,
    wrapper_before_sha256: str,
    wrapper_after_sha256: str,
    wrapper_mode: int,
    runtime_path_replacements: int,
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "snapshot_kind": snapshot_kind,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_repo": str(source_repo),
        "runtime_root": str(runtime_root),
        "state_root": str(state_root),
        "snapshot_id": snapshot_id,
        "stable_wrapper": str(stable_wrapper),
        "before_runtime_kind": before_runtime_kind,
        "before_runtime_path": str(before_runtime_path),
        "before_runtime_sha": before_runtime_sha,
        "before_manifest_path": (
            str(before_manifest_path) if before_manifest_path is not None else None
        ),
        "before_manifest_sha256": before_manifest_sha256,
        "after_runtime_kind": "git",
        "after_runtime_path": str(after_runtime_path),
        "after_runtime_sha": after_runtime_sha,
        "venv_dirname": venv_dirname,
        "venv_interpreter_sha256": venv_interpreter_sha256,
        "venv_interpreter_mode": venv_interpreter_mode,
        "wrapper_before_sha256": wrapper_before_sha256,
        "wrapper_after_sha256": wrapper_after_sha256,
        "wrapper_mode": wrapper_mode,
        "runtime_path_replacements": runtime_path_replacements,
    }


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


def write_snapshot(
    snapshot_path: Path,
    manifest: dict[str, Any],
    wrapper_before: bytes,
    wrapper_after: bytes,
) -> None:
    snapshots_root = snapshot_path.parent
    try:
        if not lexists(snapshots_root):
            snapshots_root.mkdir(mode=0o700)
            os.chmod(snapshots_root, 0o700)
        elif (
            snapshots_root.is_symlink()
            or not snapshots_root.is_dir()
            or stat.S_IMODE(snapshots_root.stat().st_mode) != 0o700
        ):
            raise RolloutError("snapshots root must be a mode 0700 directory")
        snapshot_path.mkdir(mode=0o700)
        os.chmod(snapshot_path, 0o700)
    except OSError as exc:
        raise RolloutError(f"cannot create exclusive snapshot directory: {exc}") from exc
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    _write_exclusive_file(snapshot_path / "wrapper.before", wrapper_before)
    _write_exclusive_file(snapshot_path / "wrapper.after", wrapper_after)
    _write_exclusive_file(snapshot_path / "manifest.json", manifest_bytes)
    _fsync_directory(snapshot_path)
    _fsync_directory(snapshots_root)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RolloutError(f"manifest contains duplicate key: {key}")
        result[key] = value
    return result


def _read_snapshot_file(snapshot: Path, name: str) -> bytes:
    path = canonical_path(str(snapshot / name), f"snapshot {name}", must_exist=True)
    if path.is_symlink() or not path.is_file():
        raise RolloutError(f"snapshot {name} must be a regular non-symlink file")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise RolloutError(f"snapshot {name} must have mode 0600")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise RolloutError(f"cannot read snapshot {name}: {exc}") from exc


def _load_manifest(snapshot_path: Path) -> dict[str, Any]:
    try:
        raw = _read_snapshot_file(snapshot_path, "manifest.json")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RolloutError(f"cannot read manifest: {exc}") from exc
    if not isinstance(value, dict) or set(value) != MANIFEST_KEYS:
        raise RolloutError("manifest has an unexpected schema")
    if type(value["schema_version"]) is not int or value["schema_version"] != 2:
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
    if not 0 <= value["wrapper_mode"] <= 0o777 or not value["wrapper_mode"] & 0o111:
        raise RolloutError("manifest wrapper mode is invalid")
    if value["runtime_path_replacements"] < 1:
        raise RolloutError("manifest replacement count is invalid")
    nullable = {"before_manifest_path", "before_manifest_sha256"}
    string_fields = MANIFEST_KEYS - integer_fields - nullable - {"schema_version"}
    if any(not isinstance(value[field], str) or not value[field] for field in string_fields):
        raise RolloutError("manifest string fields must be non-empty")
    if value["snapshot_kind"] == "bootstrap":
        if (
            value["before_runtime_kind"] != "export"
            or value["after_runtime_kind"] != "git"
            or value["before_runtime_sha"] != value["after_runtime_sha"]
            or value["runtime_path_replacements"] != 1
            or not isinstance(value["before_manifest_path"], str)
            or not value["before_manifest_path"]
            or not isinstance(value["before_manifest_sha256"], str)
        ):
            raise RolloutError("manifest bootstrap variant is invalid")
        require_full_sha(
            value["before_manifest_sha256"],
            FULL_SHA256,
            "manifest export manifest SHA-256",
        )
    elif value["snapshot_kind"] == "rollout":
        if (
            value["before_runtime_kind"] != "git"
            or value["after_runtime_kind"] != "git"
            or value["before_runtime_sha"] == value["after_runtime_sha"]
            or value["before_manifest_path"] is not None
            or value["before_manifest_sha256"] is not None
        ):
            raise RolloutError("manifest rollout variant is invalid")
    else:
        raise RolloutError("manifest snapshot kind is invalid")
    require_full_sha(value["before_runtime_sha"], FULL_GIT_SHA, "manifest before SHA")
    require_full_sha(value["after_runtime_sha"], FULL_GIT_SHA, "manifest after SHA")
    require_full_sha(
        value["venv_interpreter_sha256"], FULL_SHA256, "manifest interpreter SHA-256"
    )
    require_full_sha(
        value["wrapper_before_sha256"], FULL_SHA256, "manifest wrapper before SHA-256"
    )
    require_full_sha(
        value["wrapper_after_sha256"], FULL_SHA256, "manifest wrapper after SHA-256"
    )
    return value


def _snapshot_identity(snapshot_id: str) -> tuple[str, str, str]:
    rollout = ROLLOUT_SNAPSHOT_ID.fullmatch(snapshot_id)
    if rollout is not None:
        return "rollout", rollout.group(1), rollout.group(2)
    bootstrap = BOOTSTRAP_SNAPSHOT_ID.fullmatch(snapshot_id)
    if bootstrap is not None:
        return "bootstrap", bootstrap.group(1), bootstrap.group(1)
    raise RolloutError("snapshot ID must be bootstrap-SHA or SHA-to-SHA")


def load_snapshot_context(
    *,
    runtime_root_raw: str,
    state_root_raw: str,
    snapshot_id: str,
    stable_wrapper_raw: str,
) -> SnapshotContext:
    runtime_root = canonical_path(runtime_root_raw, "runtime root", must_exist=True)
    state_root = canonical_path(state_root_raw, "state root", must_exist=True)
    stable_path = canonical_path(stable_wrapper_raw, "stable wrapper", must_exist=True)
    validate_roots(runtime_root, state_root)
    requested_kind, before_sha, after_sha = _snapshot_identity(snapshot_id)
    snapshot_path = canonical_path(
        str(state_root / "snapshots" / snapshot_id), "snapshot path", must_exist=True
    )
    strictly_within(snapshot_path, state_root, "snapshot path")
    if snapshot_path.is_symlink() or not snapshot_path.is_dir():
        raise RolloutError("snapshot path must be a regular directory")
    if stat.S_IMODE(snapshot_path.stat().st_mode) != 0o700:
        raise RolloutError("snapshot directory must have mode 0700")
    if stat.S_IMODE(snapshot_path.parent.stat().st_mode) != 0o700:
        raise RolloutError("snapshots root must have mode 0700")
    if {entry.name for entry in snapshot_path.iterdir()} != MANIFEST_FILES:
        raise RolloutError("snapshot must contain exactly the three manifest files")
    manifest = _load_manifest(snapshot_path)
    if (
        manifest["snapshot_kind"] == "bootstrap"
        and stat.S_IMODE(state_root.stat().st_mode) != 0o700
    ):
        raise RolloutError("bootstrap state root must have mode 0700")
    expected = {
        "snapshot_kind": requested_kind,
        "runtime_root": str(runtime_root),
        "state_root": str(state_root),
        "stable_wrapper": str(stable_path),
        "snapshot_id": snapshot_id,
        "before_runtime_sha": before_sha,
        "after_runtime_sha": after_sha,
    }
    for key, expected_value in expected.items():
        if manifest[key] != expected_value:
            raise RolloutError(f"manifest {key} does not match the requested transition")

    source_repo = canonical_path(
        manifest["source_repo"], "manifest source repo", must_exist=True
    )
    before_runtime = canonical_path(
        manifest["before_runtime_path"], "manifest before runtime", must_exist=True
    )
    after_runtime = canonical_path(
        manifest["after_runtime_path"], "manifest after runtime", must_exist=True
    )
    validate_repo_root(source_repo, "manifest source repo")
    if source_repo in {runtime_root, state_root}:
        raise RolloutError("managed roots must not be the source repo root")
    strictly_within(after_runtime, runtime_root, "manifest after runtime")
    if after_runtime != runtime_root / f"hermes-kanban-mcp-{after_sha}":
        raise RolloutError("manifest after runtime is not the exact derived path")
    validate_commit(source_repo, after_sha, "manifest after SHA")
    validate_clean_worktree(after_runtime, after_sha, "after runtime")

    interpreter_hash = manifest["venv_interpreter_sha256"]
    if requested_kind == "bootstrap":
        export_manifest_path = canonical_path(
            manifest["before_manifest_path"],
            "manifest export manifest",
            must_exist=True,
        )
        read_export_manifest(
            export_manifest_path,
            before_runtime,
            before_sha,
            manifest["before_manifest_sha256"],
        )
    else:
        strictly_within(before_runtime, runtime_root, "manifest before runtime")
        if before_runtime != runtime_root / f"hermes-kanban-mcp-{before_sha}":
            raise RolloutError("manifest before runtime is not the exact derived path")
        validate_commit(source_repo, before_sha, "manifest before SHA")
        validate_clean_worktree(before_runtime, before_sha, "before runtime")
    validate_venv(
        before_runtime,
        manifest["venv_dirname"],
        expected_sha256=interpreter_hash,
        expected_mode=manifest["venv_interpreter_mode"],
    )
    validate_venv(
        after_runtime,
        manifest["venv_dirname"],
        expected_sha256=interpreter_hash,
        expected_mode=manifest["venv_interpreter_mode"],
    )

    wrapper_before = _read_snapshot_file(snapshot_path, "wrapper.before")
    wrapper_after = _read_snapshot_file(snapshot_path, "wrapper.after")
    if (
        sha256(wrapper_before) != manifest["wrapper_before_sha256"]
        or sha256(wrapper_after) != manifest["wrapper_after_sha256"]
    ):
        raise RolloutError("snapshot wrapper hash does not match manifest")
    before_text = validate_wrapper_contract(wrapper_before)
    validate_wrapper_contract(wrapper_after)
    replacements = before_text.count(str(before_runtime))
    if replacements != manifest["runtime_path_replacements"] or replacements < 1:
        raise RolloutError("manifest runtime replacement count is invalid")
    expected_after = wrapper_before.replace(
        str(before_runtime).encode(), str(after_runtime).encode()
    )
    if wrapper_after != expected_after:
        raise RolloutError("wrapper.after is not the exact guarded transformation")

    return SnapshotContext(
        manifest=manifest,
        snapshot_path=snapshot_path,
        source_repo=source_repo,
        runtime_root=runtime_root,
        state_root=state_root,
        before_runtime=before_runtime,
        after_runtime=after_runtime,
        stable_wrapper=read_wrapper(stable_path),
        wrapper_before=wrapper_before,
        wrapper_after=wrapper_after,
    )


def _transition_plan(command: str, context: SnapshotContext, apply: bool) -> dict[str, Any]:
    manifest = context.manifest
    source_name = "wrapper.after" if command == "switch" else "wrapper.before"
    return {
        "command": command,
        "mode": "apply" if apply else "dry-run",
        "snapshot_kind": manifest["snapshot_kind"],
        "candidate_path": str(context.after_runtime),
        "snapshot_id": manifest["snapshot_id"],
        "snapshot_path": str(context.snapshot_path),
        "stable_wrapper": str(context.stable_wrapper.path),
        "wrapper_before_sha256": manifest["wrapper_before_sha256"],
        "wrapper_after_sha256": manifest["wrapper_after_sha256"],
        "operations": [
            "revalidate schema v2, snapshot hashes, runtime evidence and venv",
            f"write same-directory temporary file from {source_name}",
            "fsync temporary file and preserve executable mode",
            "replace stable wrapper with one os.replace",
            "fsync stable wrapper directory",
            "leave runtimes, snapshot, processes and DB unchanged",
        ],
    }


def _atomic_replace(
    stable_wrapper: Path,
    data: bytes,
    mode: int,
    expected_current_hash: str,
) -> None:
    current = read_wrapper(canonical_path(str(stable_wrapper), "stable wrapper", must_exist=True))
    if current.sha256 != expected_current_hash or current.mode != mode:
        raise RolloutError("stable wrapper changed before atomic replacement")
    temporary_name: str | None = None
    replacement_applied = False
    expected_installed_hash = sha256(data)
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{stable_wrapper.name}.rollout-", dir=stable_wrapper.parent
        )
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(data)
            handle.flush()
            os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
        latest = read_wrapper(
            canonical_path(str(stable_wrapper), "stable wrapper", must_exist=True)
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


def run_transition(
    *,
    command: str,
    runtime_root: str,
    state_root: str,
    snapshot_id: str,
    stable_wrapper: str,
    expected_current_wrapper_sha256: str,
    apply: bool,
) -> dict[str, Any]:
    context = load_snapshot_context(
        runtime_root_raw=runtime_root,
        state_root_raw=state_root,
        snapshot_id=snapshot_id,
        stable_wrapper_raw=stable_wrapper,
    )
    explicit_hash = require_full_sha(
        expected_current_wrapper_sha256,
        FULL_SHA256,
        "expected current wrapper SHA-256",
    )
    manifest = context.manifest
    if command == "switch":
        required_hash = manifest["wrapper_before_sha256"]
        replacement = context.wrapper_after
    elif command == "rollback":
        required_hash = manifest["wrapper_after_sha256"]
        replacement = context.wrapper_before
    else:
        raise RolloutError("unsupported transition command")
    if explicit_hash != required_hash or context.stable_wrapper.sha256 != required_hash:
        raise RolloutError("stable wrapper SHA-256 does not match command and manifest guards")
    if context.stable_wrapper.mode != manifest["wrapper_mode"]:
        raise RolloutError("stable wrapper mode does not match manifest")
    plan = _transition_plan(command, context, apply)
    if not apply:
        return plan
    _atomic_replace(
        context.stable_wrapper.path,
        replacement,
        manifest["wrapper_mode"],
        required_hash,
    )
    expected_installed = (
        manifest["wrapper_after_sha256"]
        if command == "switch"
        else manifest["wrapper_before_sha256"]
    )
    try:
        installed = read_wrapper(context.stable_wrapper.path)
        if installed.sha256 != expected_installed or installed.mode != manifest["wrapper_mode"]:
            raise RolloutError("installed wrapper does not match the snapshot")
    except (RolloutError, OSError) as exc:
        raise ReplacementAppliedError(
            expected_installed,
            f"post-install wrapper verification failed: {exc}",
        ) from exc
    plan["result"] = "switched" if command == "switch" else "rolled-back"
    return plan
