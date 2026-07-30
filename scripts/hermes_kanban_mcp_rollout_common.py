"""Shared path, Git, wrapper-file and venv primitives for Kanban MCP rollout."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


FULL_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
FULL_SHA256 = re.compile(r"^[0-9a-f]{64}$")
SHELL_PATH_CHARS = frozenset("~$*?[]{}")


class RolloutError(RuntimeError):
    """A rollout precondition or guarded operation failed."""

    def __init__(
        self,
        message: str,
        *,
        primary_failure: BaseException | None = None,
        cleanup_failures: Sequence[str] = (),
        replacement_applied: bool = False,
    ) -> None:
        super().__init__(message)
        self.primary_failure = primary_failure
        self.cleanup_failures = tuple(cleanup_failures)
        self.replacement_applied = replacement_applied


@dataclass(frozen=True)
class Wrapper:
    path: Path
    data: bytes
    mode: int
    sha256: str


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _reject_symlink_components(path: Path, label: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if lexists(current) and current.is_symlink():
            raise RolloutError(f"{label} contains a symlink component: {current}")


def canonical_path(raw: str, label: str, *, must_exist: bool) -> Path:
    if "\x00" in raw or any(char in raw for char in SHELL_PATH_CHARS):
        raise RolloutError(f"{label} contains unsafe path characters")
    path = Path(raw)
    if not path.is_absolute() or ".." in path.parts or os.path.normpath(raw) != raw:
        raise RolloutError(f"{label} must be an absolute lexically canonical path")
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
        runtime_root.is_relative_to(state_root) or state_root.is_relative_to(runtime_root)
    ):
        raise RolloutError("different runtime and state roots must not be nested")


def run_git(repo: Path, arguments: Sequence[str]) -> str:
    try:
        completed = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(repo), *arguments],
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
    top = canonical_path(
        run_git(repo, ["rev-parse", "--show-toplevel"]), label, must_exist=True
    )
    if top != repo:
        raise RolloutError(f"{label} must be the Git worktree root")


def require_full_sha(value: str, pattern: re.Pattern[str], label: str) -> str:
    if not pattern.fullmatch(value):
        raise RolloutError(f"{label} must be a lowercase full-length hash")
    return value


def git_head(repo: Path, label: str) -> str:
    return require_full_sha(
        run_git(repo, ["rev-parse", "--verify", "HEAD"]),
        FULL_GIT_SHA,
        f"{label} HEAD",
    )


def validate_clean_worktree(repo: Path, expected_sha: str, label: str) -> None:
    validate_repo_root(repo, label)
    if git_head(repo, label) != expected_sha:
        raise RolloutError(f"{label} HEAD does not match expected full SHA")
    if run_git(repo, ["status", "--porcelain=v1", "--untracked-files=no"]):
        raise RolloutError(f"{label} has dirty tracked files")


def validate_commit(repo: Path, sha: str, label: str = "commit SHA") -> None:
    if run_git(repo, ["rev-parse", "--verify", f"{sha}^{{commit}}"]) != sha:
        raise RolloutError(f"{label} does not name that exact commit object")


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
    return Wrapper(path, data, mode, sha256(data))


def validate_wrapper_contract(data: bytes) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RolloutError("wrapper snapshot is not UTF-8") from exc
    if "mcp serve-kanban" not in text or "--allow-write" not in text:
        raise RolloutError("wrapper lacks the standalone write-mode Kanban MCP contract")
    return text


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
    if venv.is_symlink() or not venv.is_dir():
        raise RolloutError(f"expected top-level venv directory is missing: {venv}")
    interpreter = venv / "bin" / "python"
    if not interpreter.is_file() or not os.access(interpreter, os.X_OK):
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


def validate_venv_startup(
    runtime: Path,
    dirname: str,
    *,
    expected_pyvenv_cfg_sha256: str | None = None,
) -> tuple[Path, str, Path]:
    if dirname not in {".venv", "venv"}:
        raise RolloutError("venv dirname must be exactly .venv or venv")
    venv = runtime / dirname
    pyvenv_cfg = venv / "pyvenv.cfg"
    if pyvenv_cfg.is_symlink() or not pyvenv_cfg.is_file():
        raise RolloutError("pyvenv.cfg must be a regular non-symlink file")
    canonical_path(str(pyvenv_cfg), "pyvenv.cfg", must_exist=True)
    try:
        raw = pyvenv_cfg.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RolloutError("cannot validate UTF-8 pyvenv.cfg") from exc
    digest = sha256(raw)
    if expected_pyvenv_cfg_sha256 is not None and digest != expected_pyvenv_cfg_sha256:
        raise RolloutError("pyvenv.cfg SHA-256 does not match trusted evidence")
    versions = []
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip().lower() in {"version", "version_info"}:
            versions.append(value.strip())
    if len(versions) != 1:
        raise RolloutError("pyvenv.cfg must contain one exact Python version")
    match = re.fullmatch(r"([0-9]+)\.([0-9]+)(?:\.[0-9]+)?", versions[0])
    if match is None:
        raise RolloutError("pyvenv.cfg Python version is invalid")
    site_packages = (
        venv / "lib" / f"python{match.group(1)}.{match.group(2)}" / "site-packages"
    )
    if site_packages.is_symlink() or not site_packages.is_dir():
        raise RolloutError("site-packages must be one regular non-symlink directory")
    canonical_path(str(site_packages), "site-packages", must_exist=True)
    strictly_within(site_packages, venv, "site-packages")
    return pyvenv_cfg, digest, site_packages
