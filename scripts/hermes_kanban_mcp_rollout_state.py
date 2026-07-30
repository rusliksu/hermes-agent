"""Snapshot validation and atomic transitions for Kanban MCP rollout."""

from __future__ import annotations

import json
import os
import re
import stat
import sys
import tempfile
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts import hermes_kanban_mcp_runtime_coherence as coherence
    from scripts.hermes_kanban_mcp_rollout_common import (
        FULL_GIT_SHA,
        FULL_SHA256,
        SHELL_PATH_CHARS,
        RolloutError,
        Wrapper,
        canonical_path,
        lexists,
        read_wrapper,
        require_full_sha,
        sha256,
        strictly_within,
        validate_clean_worktree,
        validate_commit,
        validate_repo_root,
        validate_roots,
        validate_venv,
        validate_wrapper_contract,
    )
except ModuleNotFoundError:
    import hermes_kanban_mcp_runtime_coherence as coherence
    from hermes_kanban_mcp_rollout_common import (
        FULL_GIT_SHA,
        FULL_SHA256,
        SHELL_PATH_CHARS,
        RolloutError,
        Wrapper,
        canonical_path,
        lexists,
        read_wrapper,
        require_full_sha,
        sha256,
        strictly_within,
        validate_clean_worktree,
        validate_commit,
        validate_repo_root,
        validate_roots,
        validate_venv,
        validate_wrapper_contract,
    )


ROLLOUT_SNAPSHOT_ID = re.compile(r"^([0-9a-f]{40})-to-([0-9a-f]{40})$")
BOOTSTRAP_SNAPSHOT_ID = re.compile(r"^bootstrap-([0-9a-f]{40})$")
MANIFEST_FILES = {"manifest.json", "wrapper.before", "wrapper.after"}
MANIFEST_KEYS_V2 = set(
    """schema_version snapshot_kind created_at source_repo runtime_root state_root
    snapshot_id stable_wrapper before_runtime_kind before_runtime_path
    before_runtime_sha before_manifest_path before_manifest_sha256
    after_runtime_kind after_runtime_path after_runtime_sha venv_dirname
    venv_interpreter_sha256 venv_interpreter_mode wrapper_before_sha256
    wrapper_after_sha256 wrapper_mode runtime_path_replacements""".split()
)
MANIFEST_KEYS_V3 = MANIFEST_KEYS_V2 | set(
    """wrapper_contract preflight_contract source_cwd target_interpreter
    resolved_interpreter interpreter_symlink_chain pyvenv_cfg
    pyvenv_cfg_sha256 pyvenv_cfg_home site_packages trusted_interpreter
    stdlib_roots candidate_digest source_digest venv_digest bwrap_sha256
    hermes_cli_main_origin kanban_server_origin write_tools""".split()
)
WRAPPER_CONTRACT = coherence.WRAPPER_CONTRACT
LEGACY_WRAPPER_CONTRACT = coherence.LEGACY_WRAPPER_CONTRACT
NOFILE_SOFT_LIMIT = coherence.NOFILE_SOFT_LIMIT
PREFLIGHT_CONTRACT = coherence.PREFLIGHT_CONTRACT
REQUIRED_WRITE_TOOL = coherence.REQUIRED_WRITE_TOOL


def parsed_soft_nofile(data: bytes, runtime: Path, venv_dirname: str) -> int:
    grammar = coherence.parse_rollout_wrapper(
        data,
        runtime,
        venv_dirname,
        expected_contract=WRAPPER_CONTRACT,
    )
    if grammar.soft_nofile != NOFILE_SOFT_LIMIT:
        raise RolloutError("wrapper has an invalid planned soft NOFILE limit")
    return grammar.soft_nofile


class ReplacementAppliedError(RolloutError):
    """The wrapper was replaced, but a post-replacement check failed."""

    def __init__(
        self,
        expected_sha256: str,
        detail: str,
        *,
        primary_failure: BaseException | None = None,
        cleanup_failures: tuple[str, ...] = (),
    ) -> None:
        super().__init__(
            detail,
            primary_failure=primary_failure,
            cleanup_failures=cleanup_failures,
            replacement_applied=True,
        )
        self.expected_sha256 = expected_sha256


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
    schema_version: int = 2,
    import_evidence: coherence.ImportEvidence | None = None,
) -> dict[str, Any]:
    manifest = {
        "schema_version": schema_version,
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
    if schema_version == 3:
        if snapshot_kind not in {"bootstrap", "rollout"} or import_evidence is None:
            raise RolloutError("schema v3 requires import evidence")
        manifest.update(import_evidence.manifest_fields())
        manifest.update(
            wrapper_contract=WRAPPER_CONTRACT,
            preflight_contract=PREFLIGHT_CONTRACT,
        )
    elif schema_version != 2:
        raise RolloutError("unsupported manifest schema version")
    return manifest


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
    if not isinstance(value, dict):
        raise RolloutError("manifest has an unexpected schema")
    schema_version = value.get("schema_version")
    expected_keys = (
        MANIFEST_KEYS_V2
        if schema_version == 2
        else MANIFEST_KEYS_V3
        if schema_version == 3
        else set()
    )
    if type(schema_version) is not int or set(value) != expected_keys:
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
    string_fields = (
        MANIFEST_KEYS_V2 - integer_fields - nullable - {"schema_version"}
    )
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
    if schema_version == 3:
        if (
            value["wrapper_contract"]
            not in {LEGACY_WRAPPER_CONTRACT, WRAPPER_CONTRACT}
            or value["preflight_contract"] != PREFLIGHT_CONTRACT
        ):
            raise RolloutError("manifest schema v3 contract is invalid")
        for field in (
            "source_cwd",
            "target_interpreter",
            "resolved_interpreter",
            "pyvenv_cfg",
            "pyvenv_cfg_home",
            "site_packages",
            "trusted_interpreter",
            "hermes_cli_main_origin",
            "kanban_server_origin",
        ):
            if not isinstance(value[field], str) or not value[field]:
                raise RolloutError("manifest schema v3 path evidence is invalid")
        require_full_sha(
            value["pyvenv_cfg_sha256"],
            FULL_SHA256,
            "manifest pyvenv.cfg SHA-256",
        )
        for field in (
            "candidate_digest",
            "source_digest",
            "venv_digest",
            "bwrap_sha256",
        ):
            require_full_sha(value[field], FULL_SHA256, f"manifest {field}")
        chain = value["interpreter_symlink_chain"]
        if (
            not isinstance(chain, list)
            or not chain
            or any(
                not isinstance(item, dict)
                or set(item) != {"path", "mode", "device", "inode", "size", "target"}
                or not isinstance(item["path"], str)
                or not item["path"]
                or type(item["mode"]) is not int
                or type(item["device"]) is not int
                or type(item["inode"]) is not int
                or type(item["size"]) is not int
                or (item["target"] is not None and not isinstance(item["target"], str))
                for item in chain
            )
        ):
            raise RolloutError("manifest interpreter symlink-chain evidence is invalid")
        stdlib_roots = value["stdlib_roots"]
        if (
            not isinstance(stdlib_roots, list)
            or not stdlib_roots
            or any(not isinstance(path, str) or not path for path in stdlib_roots)
            or len(set(stdlib_roots)) != len(stdlib_roots)
        ):
            raise RolloutError("manifest schema v3 stdlib evidence is invalid")
        tools = value["write_tools"]
        if (
            not isinstance(tools, list)
            or any(not isinstance(tool, str) or not tool for tool in tools)
            or len(set(tools)) != len(tools)
            or REQUIRED_WRITE_TOOL not in tools
        ):
            raise RolloutError("manifest schema v3 WRITE_TOOLS evidence is invalid")
    return value


def _snapshot_identity(snapshot_id: str) -> tuple[str, str, str]:
    rollout = ROLLOUT_SNAPSHOT_ID.fullmatch(snapshot_id)
    if rollout is not None:
        return "rollout", rollout.group(1), rollout.group(2)
    bootstrap = BOOTSTRAP_SNAPSHOT_ID.fullmatch(snapshot_id)
    if bootstrap is not None:
        return "bootstrap", bootstrap.group(1), bootstrap.group(1)
    raise RolloutError("snapshot ID must be bootstrap-SHA or SHA-to-SHA")


def _manifest_path(raw: Any, label: str) -> Path:
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise RolloutError(f"{label} must be an absolute canonical path")
    path = Path(raw)
    if (
        not path.is_absolute()
        or ".." in path.parts
        or os.path.normpath(raw) != raw
        or any(char in raw for char in SHELL_PATH_CHARS)
    ):
        raise RolloutError(f"{label} must be an absolute canonical path")
    return path


def load_rollback_snapshot_context(
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

    source_repo = _manifest_path(manifest["source_repo"], "manifest source repo")
    before_runtime = _manifest_path(
        manifest["before_runtime_path"], "manifest before runtime"
    )
    after_runtime = _manifest_path(
        manifest["after_runtime_path"], "manifest after runtime"
    )
    if source_repo in {runtime_root, state_root}:
        raise RolloutError("managed roots must not be the source repo root")
    strictly_within(after_runtime, runtime_root, "manifest after runtime")
    if after_runtime != runtime_root / f"hermes-kanban-mcp-{after_sha}":
        raise RolloutError("manifest after runtime is not the exact derived path")
    if requested_kind == "rollout":
        strictly_within(before_runtime, runtime_root, "manifest before runtime")
        if before_runtime != runtime_root / f"hermes-kanban-mcp-{before_sha}":
            raise RolloutError("manifest before runtime is not the exact derived path")
    if manifest["schema_version"] == 3:
        site_packages = _manifest_path(
            manifest["site_packages"], "manifest site-packages"
        )
        strictly_within(
            site_packages,
            after_runtime / manifest["venv_dirname"],
            "manifest site-packages",
        )
        site_relative = site_packages.relative_to(
            after_runtime / manifest["venv_dirname"]
        )
        if (
            len(site_relative.parts) != 3
            or site_relative.parts[0] != "lib"
            or re.fullmatch(r"python[0-9]+\.[0-9]+", site_relative.parts[1]) is None
            or site_relative.parts[2] != "site-packages"
        ):
            raise RolloutError("manifest site-packages is not exact target evidence")
        for raw in manifest["stdlib_roots"]:
            _manifest_path(raw, "manifest trusted interpreter stdlib root")
        for field in (
            "resolved_interpreter",
            "pyvenv_cfg_home",
            "trusted_interpreter",
        ):
            _manifest_path(manifest[field], f"manifest {field}")
        chain = manifest["interpreter_symlink_chain"]
        for item in chain:
            _manifest_path(item["path"], "manifest interpreter symlink-chain path")
        if (
            chain[0]["path"] != manifest["target_interpreter"]
            or chain[-1]["path"] != manifest["resolved_interpreter"]
            or chain[-1]["target"] is not None
        ):
            raise RolloutError("manifest interpreter symlink chain is inconsistent")
        expected_evidence = {
            "source_cwd": str(after_runtime),
            "target_interpreter": str(after_runtime / manifest["venv_dirname"]
                                      / "bin" / "python"),
            "pyvenv_cfg": str(after_runtime / manifest["venv_dirname"]
                              / "pyvenv.cfg"),
            "site_packages": str(site_packages),
            "hermes_cli_main_origin": str(after_runtime / "hermes_cli" / "main.py"),
            "kanban_server_origin": str(after_runtime / "agent" / "transports"
                                        / "hermes_kanban_mcp_server.py"),
        }
        for key, expected_value in expected_evidence.items():
            if manifest[key] != expected_value:
                raise RolloutError(f"manifest {key} is not exact target evidence")

    wrapper_before = _read_snapshot_file(snapshot_path, "wrapper.before")
    wrapper_after = _read_snapshot_file(snapshot_path, "wrapper.after")
    if (
        sha256(wrapper_before) != manifest["wrapper_before_sha256"]
        or sha256(wrapper_after) != manifest["wrapper_after_sha256"]
    ):
        raise RolloutError("snapshot wrapper hash does not match manifest")
    if manifest["schema_version"] == 3:
        coherence.validate_rollout_wrapper_transition(
            wrapper_before,
            wrapper_after,
            before_runtime,
            after_runtime,
            manifest["venv_dirname"],
            manifest["wrapper_contract"],
        )
    else:
        before_text = validate_wrapper_contract(wrapper_before)
        replacements = before_text.count(str(before_runtime))
        if replacements != manifest["runtime_path_replacements"] or replacements < 1:
            raise RolloutError("manifest runtime replacement count is invalid")
        expected_after = wrapper_before.replace(
            str(before_runtime).encode(), str(after_runtime).encode()
        )
        coherence.validate_schema_v2_rollout_wrapper(
            wrapper_before, before_runtime, manifest["venv_dirname"]
        )
        coherence.validate_schema_v2_rollout_wrapper(
            wrapper_after, after_runtime, manifest["venv_dirname"]
        )
    if manifest["schema_version"] != 3 and wrapper_after != expected_after:
        raise RolloutError("wrapper.after is not the exact guarded transformation")

    return SnapshotContext(
        manifest, snapshot_path, source_repo, runtime_root, state_root,
        before_runtime, after_runtime, read_wrapper(stable_path),
        wrapper_before, wrapper_after)


def load_snapshot_context(
    *,
    runtime_root_raw: str,
    state_root_raw: str,
    snapshot_id: str,
    stable_wrapper_raw: str,
) -> SnapshotContext:
    context = load_rollback_snapshot_context(
        runtime_root_raw=runtime_root_raw,
        state_root_raw=state_root_raw,
        snapshot_id=snapshot_id,
        stable_wrapper_raw=stable_wrapper_raw,
    )
    manifest = context.manifest
    if manifest["schema_version"] != 3:
        raise RolloutError("switch requires a schema v3 snapshot")
    if manifest["wrapper_contract"] != WRAPPER_CONTRACT:
        raise RolloutError("source-cwd-v1 snapshots are rollback-only")
    parsed_soft_nofile(
        context.wrapper_after,
        context.after_runtime,
        manifest["venv_dirname"],
    )
    source_repo = canonical_path(
        str(context.source_repo), "manifest source repo", must_exist=True
    )
    before_runtime = canonical_path(
        str(context.before_runtime), "manifest before runtime", must_exist=True
    )
    after_runtime = canonical_path(
        str(context.after_runtime), "manifest after runtime", must_exist=True
    )
    validate_repo_root(source_repo, "manifest source repo")
    validate_commit(source_repo, manifest["after_runtime_sha"], "manifest after SHA")
    validate_clean_worktree(
        after_runtime, manifest["after_runtime_sha"], "after runtime"
    )
    interpreter_hash = manifest["venv_interpreter_sha256"]
    if manifest["snapshot_kind"] == "bootstrap":
        export_manifest_path = canonical_path(
            manifest["before_manifest_path"],
            "manifest export manifest",
            must_exist=True,
        )
        read_export_manifest(
            export_manifest_path,
            before_runtime,
            manifest["before_runtime_sha"],
            manifest["before_manifest_sha256"],
        )
    else:
        validate_commit(
            source_repo, manifest["before_runtime_sha"], "manifest before SHA"
        )
        validate_clean_worktree(
            before_runtime, manifest["before_runtime_sha"], "before runtime"
        )
    for runtime in (before_runtime, after_runtime):
        validate_venv(
            runtime,
            manifest["venv_dirname"],
            expected_sha256=interpreter_hash,
            expected_mode=manifest["venv_interpreter_mode"],
        )
    return SnapshotContext(
        manifest,
        context.snapshot_path,
        source_repo,
        context.runtime_root,
        context.state_root,
        before_runtime,
        after_runtime,
        context.stable_wrapper,
        context.wrapper_before,
        context.wrapper_after,
    )


def _transition_plan(command: str, context: SnapshotContext, apply: bool) -> dict[str, Any]:
    manifest = context.manifest
    source_name = "wrapper.after" if command == "switch" else "wrapper.before"
    validation = (
        f"revalidate schema v{manifest['schema_version']}, snapshot hashes, "
        "runtime evidence, venv and import origins"
        if command == "switch"
        else (
            f"revalidate schema v{manifest['schema_version']}, exact snapshot "
            "bytes, modes, hashes and current wrapper guard"
        )
    )
    plan = {
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
            validation,
            f"write same-directory temporary file from {source_name}",
            "fsync temporary file and preserve executable mode",
            "replace stable wrapper with one os.replace",
            "fsync stable wrapper directory",
            "leave runtimes, snapshot, processes and DB unchanged",
        ],
    }
    if command == "switch":
        plan["wrapper_contract"] = manifest["wrapper_contract"]
        plan["planned_soft_nofile"] = parsed_soft_nofile(
            context.wrapper_after,
            context.after_runtime,
            manifest["venv_dirname"],
        )
        plan["import_origin"] = {
            key: manifest[key]
            for key in (
                "source_cwd",
                "target_interpreter",
                "hermes_cli_main_origin",
                "kanban_server_origin",
                "write_tools",
            )
        }
    return plan


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
    expected_wrapper_after_sha256: str | None = None,
) -> dict[str, Any]:
    if command not in {"switch", "rollback"}:
        raise RolloutError("unsupported transition command")
    approved_after_hash: str | None = None
    if command == "switch" and apply:
        if expected_wrapper_after_sha256 is None:
            raise RolloutError("switch apply requires expected wrapper.after SHA-256")
        approved_after_hash = require_full_sha(
            expected_wrapper_after_sha256,
            FULL_SHA256,
            "expected wrapper.after SHA-256",
        )
    loader = (
        load_snapshot_context
        if command == "switch"
        else load_rollback_snapshot_context
    )
    context = loader(
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
    if approved_after_hash is not None and (
        approved_after_hash != manifest["wrapper_after_sha256"]
        or approved_after_hash != sha256(context.wrapper_after)
    ):
        raise RolloutError("expected wrapper.after SHA-256 does not match snapshot")
    if command == "switch":
        session = coherence.import_preflight_session(
            context.after_runtime,
            manifest["venv_dirname"],
            expected_pyvenv_cfg_sha256=manifest["pyvenv_cfg_sha256"],
            expected_site_packages=manifest["site_packages"],
            expected_stdlib_roots=manifest["stdlib_roots"],
        )
        required_hash = manifest["wrapper_before_sha256"]
        replacement = context.wrapper_after
    elif command == "rollback":
        session = nullcontext(None)
        required_hash = manifest["wrapper_after_sha256"]
        replacement = context.wrapper_before
    expected_installed = (
        manifest["wrapper_after_sha256"]
        if command == "switch"
        else manifest["wrapper_before_sha256"]
    )
    replacement_applied = False
    try:
        with session as evidence:
            if evidence is not None:
                observed = evidence.manifest_fields()
                if any(manifest[key] != value for key, value in observed.items()):
                    raise RolloutError("target import evidence changed since prepare")
            if (
                explicit_hash != required_hash
                or context.stable_wrapper.sha256 != required_hash
            ):
                raise RolloutError(
                    "stable wrapper SHA-256 does not match command and manifest guards"
                )
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
            replacement_applied = True
            try:
                installed = read_wrapper(context.stable_wrapper.path)
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
    except ReplacementAppliedError:
        raise
    except (RolloutError, OSError) as exc:
        if not replacement_applied:
            raise
        raise ReplacementAppliedError(
            expected_installed,
            f"post-replacement trust or wrapper verification failed: {exc}",
            primary_failure=getattr(exc, "primary_failure", exc),
            cleanup_failures=getattr(exc, "cleanup_failures", ()),
        ) from exc
    plan["result"] = "switched" if command == "switch" else "rolled-back"
    return plan
