"""Exact wrapper grammar and isolated import-origin checks for Kanban MCP."""

from __future__ import annotations

import json
import os
import re
import shlex
import stat
import sys
import sysconfig
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

try:
    from scripts import hermes_kanban_mcp_os_sandbox as os_sandbox
    from scripts import hermes_kanban_mcp_sealed_bundle as sealed_bundle
    from scripts import hermes_kanban_mcp_invocation as invocation
    from scripts.hermes_kanban_mcp_rollout_common import (
        RolloutError,
        canonical_path,
        sha256,
        strictly_within,
        validate_venv_startup,
    )
except ModuleNotFoundError:
    import hermes_kanban_mcp_os_sandbox as os_sandbox
    import hermes_kanban_mcp_sealed_bundle as sealed_bundle
    import hermes_kanban_mcp_invocation as invocation
    from hermes_kanban_mcp_rollout_common import (
        RolloutError,
        canonical_path,
        sha256,
        strictly_within,
        validate_venv_startup,
    )


WRAPPER_CONTRACT = "source-cwd-v1"
PREFLIGHT_CONTRACT = "bwrap-import-origin-v1"
REQUIRED_WRITE_TOOL = "kanban_sync_external_task"
_SHEBANG = "#!/bin/bash"
_SET_LINE = "set -euo pipefail"
_EXPORT_ORDER = (
    "HERMES_HOME",
    "HERMES_QUIET",
    "HERMES_REDACT_SECRETS",
    "PYTHONDONTWRITEBYTECODE",
)
_EXACT_EXPORT_VALUES = {
    "HERMES_QUIET": "1",
    "HERMES_REDACT_SECRETS": "true",
    "PYTHONDONTWRITEBYTECODE": "1",
}
_REQUIRED_EXPORTS = set(_EXPORT_ORDER)
_SAFE_LITERAL = re.compile(r"^[A-Za-z0-9._/-]+$")


@dataclass(frozen=True)
class WrapperGrammar:
    header: tuple[str, ...]
    runtime: Path
    venv_dirname: str
    canonical: bool


@dataclass(frozen=True)
class ImportEvidence:
    source_cwd: Path
    target_interpreter: Path
    resolved_interpreter: Path
    interpreter_symlink_chain: tuple[dict[str, Any], ...]
    pyvenv_cfg: Path
    pyvenv_cfg_sha256: str
    pyvenv_cfg_home: Path
    site_packages: Path
    trusted_interpreter: Path
    stdlib_roots: tuple[Path, ...]
    candidate_digest: str
    source_digest: str
    venv_digest: str
    bwrap_sha256: str
    hermes_cli_main_origin: Path
    kanban_server_origin: Path
    write_tools: tuple[str, ...]

    def manifest_fields(self) -> dict[str, Any]:
        return {
            "source_cwd": str(self.source_cwd),
            "target_interpreter": str(self.target_interpreter),
            "resolved_interpreter": str(self.resolved_interpreter),
            "interpreter_symlink_chain": list(self.interpreter_symlink_chain),
            "pyvenv_cfg": str(self.pyvenv_cfg),
            "pyvenv_cfg_sha256": self.pyvenv_cfg_sha256,
            "pyvenv_cfg_home": str(self.pyvenv_cfg_home),
            "site_packages": str(self.site_packages),
            "trusted_interpreter": str(self.trusted_interpreter),
            "stdlib_roots": [str(path) for path in self.stdlib_roots],
            "candidate_digest": self.candidate_digest,
            "source_digest": self.source_digest,
            "venv_digest": self.venv_digest,
            "bwrap_sha256": self.bwrap_sha256,
            "hermes_cli_main_origin": str(self.hermes_cli_main_origin),
            "kanban_server_origin": str(self.kanban_server_origin),
            "write_tools": list(self.write_tools),
        }

def _shell_path(path: Path) -> str:
    text = str(path)
    if "\n" in text or "\r" in text:
        raise RolloutError("wrapper path cannot contain a line break")
    return shlex.quote(text)


def _exec_line(runtime: Path, venv_dirname: str) -> str:
    interpreter = runtime / venv_dirname / "bin" / "python"
    return (
        f"exec {_shell_path(interpreter)}"
        ' -m hermes_cli.main mcp serve-kanban --allow-write "$@"'
    )


def _cd_line(runtime: Path) -> str:
    return f"cd -- {_shell_path(runtime)}"


def _validate_export(name: str, value: str) -> None:
    if name == "HERMES_HOME":
        if (
            not value.startswith("/")
            or not _SAFE_LITERAL.fullmatch(value)
            or os.path.normpath(value) != value
        ):
            raise RolloutError("HERMES_HOME must be a literal safe absolute path")
    elif _EXACT_EXPORT_VALUES.get(name) != value:
        raise RolloutError(f"{name} has an unsupported value")


def _validate_header(lines: list[str]) -> tuple[str, ...]:
    if len(lines) < 2 or lines[0] != _SHEBANG:
        raise RolloutError("wrapper must use the exact bash shebang")
    if lines[1] != _SET_LINE:
        raise RolloutError("wrapper must use exact set -euo pipefail")
    seen_exports: set[str] = set()
    last_order = -1
    for line in lines[2:]:
        match = re.fullmatch(r"export ([A-Z][A-Z0-9_]*)=([^\s]+)", line)
        if match is None:
            raise RolloutError("wrapper export is not an exact assignment")
        name, value = match.groups()
        if name not in _EXPORT_ORDER or name in seen_exports:
            raise RolloutError("wrapper export is not allow-listed or is duplicated")
        order = _EXPORT_ORDER.index(name)
        if order <= last_order:
            raise RolloutError("wrapper exports are not in the exact allow-listed order")
        if any(char in value for char in ("$", "`", "'", '"', "\\", "{", "}")):
            raise RolloutError("wrapper export contains shell expansion or quoting")
        _validate_export(name, value)
        seen_exports.add(name)
        last_order = order
    if seen_exports != _REQUIRED_EXPORTS:
        raise RolloutError("wrapper exports do not match the exact required template")
    return tuple(lines)


def parse_rollout_wrapper(
    data: bytes, runtime: Path, venv_dirname: str
) -> WrapperGrammar:
    if venv_dirname not in {".venv", "venv"}:
        raise RolloutError("venv dirname must be exactly .venv or venv")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RolloutError("wrapper is not UTF-8") from exc
    if "\r" in text or not text.endswith("\n") or "\x00" in text:
        raise RolloutError("wrapper must use terminated UTF-8 LF lines")
    lines = text[:-1].split("\n")
    if not lines or any(not line for line in lines):
        raise RolloutError("wrapper contains a blank line")
    exec_line = _exec_line(runtime, venv_dirname)
    if lines[-1] != exec_line:
        raise RolloutError("wrapper must end with one exact exec command")
    canonical = len(lines) >= 2 and lines[-2] == _cd_line(runtime)
    header_lines = lines[:-2] if canonical else lines[:-1]
    header = _validate_header(header_lines)
    if text.count(str(runtime)) != (2 if canonical else 1):
        raise RolloutError("wrapper has ambiguous runtime path evidence")
    return WrapperGrammar(header, runtime, venv_dirname, canonical)


def validate_schema_v2_rollout_wrapper(
    data: bytes, runtime: Path, venv_dirname: str
) -> None:
    strict_error: RolloutError | None = None
    try:
        parse_rollout_wrapper(data, runtime, venv_dirname)
        return
    except RolloutError as exc:
        strict_error = exc
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RolloutError("schema v2 wrapper is not UTF-8") from exc
    if "\r" in text or not text.endswith("\n") or "\x00" in text:
        raise RolloutError("schema v2 wrapper must use terminated UTF-8 LF lines")
    lines = text[:-1].split("\n")
    if len(lines) != 7:
        raise RolloutError(
            "schema v2 wrapper does not match an accepted exact template"
        ) from strict_error
    home = lines[3].removeprefix("export HERMES_HOME=")
    _validate_export("HERMES_HOME", home)
    interpreter = runtime / venv_dirname / "bin" / "python"
    expected = [
        "#!/usr/bin/env bash",
        _SET_LINE,
        "",
        f"export HERMES_HOME={home}",
        "export PYTHONDONTWRITEBYTECODE=1",
        "",
        f"exec {_shell_path(interpreter)}"
        " -m hermes_cli.main mcp serve-kanban --allow-write",
    ]
    if lines != expected or text.count(str(runtime)) != 1:
        raise RolloutError(
            "schema v2 wrapper does not match an accepted exact template"
        ) from strict_error


def canonical_rollout_wrapper(
    runtime: Path,
    venv_dirname: str,
    *,
    header: tuple[str, ...],
) -> bytes:
    _validate_header(list(header))
    return (
        "\n".join((*header, _cd_line(runtime), _exec_line(runtime, venv_dirname)))
        + "\n"
    ).encode()


def rewrite_rollout_wrapper(
    data: bytes, before_runtime: Path, after_runtime: Path, venv_dirname: str
) -> bytes:
    grammar = parse_rollout_wrapper(data, before_runtime, venv_dirname)
    return canonical_rollout_wrapper(
        after_runtime, venv_dirname, header=grammar.header
    )


_IMPORT_PREFLIGHT_CODE = r"""
import builtins
import contextlib
import hashlib
import importlib
import importlib.util
import io
import json
import os
import pathlib
import socket
import sqlite3
import subprocess
import sys

class PolicyDenied(RuntimeError):
    pass

runtime = pathlib.Path(sys.argv[1]).resolve(strict=True)
venv_dirname = sys.argv[2]
anchors = json.loads(pathlib.Path("/sandbox/anchors.json").read_text())
os.environ.pop("PWD", None)
sys.dont_write_bytecode = True
if pathlib.Path.cwd().resolve(strict=True) != runtime:
    raise SystemExit(3)
version = f"python{sys.version_info.major}.{sys.version_info.minor}"
stdlib_roots = [pathlib.Path(value).resolve(strict=True) for value in anchors["stdlib_roots"]]
site_packages = pathlib.Path(anchors["site_packages"]).resolve(strict=True)
expected_site = runtime / venv_dirname / "lib" / version / "site-packages"
if site_packages != expected_site or expected_site.is_symlink():
    raise SystemExit(3)
allowed_roots = [runtime, site_packages, *stdlib_roots]
sys.path[:] = [str(runtime), str(site_packages), *(str(root) for root in stdlib_roots)]
write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
filesystem_mutations = {
    "os.chmod", "os.chown", "os.link", "os.mkdir", "os.remove", "os.rename",
    "os.rmdir", "os.symlink", "os.truncate", "os.utime", "os.mknod",
    "os.mkfifo", "os.removexattr", "os.setxattr",
}
process_effects = {
    "os.exec", "os.fork", "os.forkpty", "os.kill", "os.killpg",
    "os.posix_spawn", "os.spawn", "os.system", "pty.spawn", "signal.pthread_kill",
    "subprocess.Popen",
}
ffi_modules = {"ctypes", "_ctypes", "cffi", "_cffi_backend"}
database_modules = {
    "dbm", "duckdb", "mysql", "psycopg", "psycopg2", "pymongo", "pymysql",
    "redis", "shelve", "sqlalchemy",
}

def make_policy():
    violation = []

    def deny(kind):
        if not violation:
            violation.append(kind)
        raise PolicyDenied(kind)

    def violated():
        return bool(violation)

    def audit(event, args):
        if event == "open":
            path, mode, flags = args
            if flags & write_flags or any(char in str(mode) for char in "wax+"):
                deny("file-write")
            if not allowed_read(path):
                deny("outside-read")
        if event in filesystem_mutations or event.startswith("shutil."):
            deny("filesystem-mutation")
        if (
            event in process_effects
            or event.startswith(("os.exec", "os.spawn", "pty."))
        ):
            deny("process")
        if event.startswith("socket."):
            deny("network")
        if event.startswith(("sqlite3.connect", "ctypes.")):
            deny("database" if event.startswith("sqlite3.") else "native-ffi")
        if event == "import" and args:
            imported_root = str(args[0]).split(".", 1)[0]
            if imported_root in ffi_modules:
                deny("native-ffi")
            if imported_root in database_modules:
                deny("database")

    return deny, violated, audit

def allowed_read(path):
    if isinstance(path, int):
        return True
    candidate = pathlib.Path(os.fsdecode(path)).resolve(strict=False)
    return any(candidate == root or candidate.is_relative_to(root) for root in allowed_roots)

def tree_digest(root, excluded=()):
    digest = hashlib.sha256()
    excluded = set(excluded)
    for base, dirs, files in os.walk(root, topdown=True, followlinks=False):
        base_path = pathlib.Path(base)
        if base_path == root:
            dirs[:] = [name for name in sorted(dirs) if name not in excluded]
        else:
            dirs.sort()
        for name in [*dirs, *sorted(files)]:
            path = base_path / name
            relative = path.relative_to(root).as_posix().encode()
            info = path.lstat()
            digest.update(relative + b"\0" + str(info.st_mode).encode() + b"\0")
            if path.is_symlink():
                digest.update(b"L" + os.readlink(path).encode())
            elif path.is_file():
                digest.update(b"F")
                with real_open(path, "rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
            elif path.is_dir():
                digest.update(b"D")
            else:
                raise SystemExit(3)
    return digest.hexdigest()

deny, policy_violated, audit = make_policy()
sys.addaudithook(audit)
real_open = builtins.open
real_io_open = io.open
real_os_open = os.open
real_import = builtins.__import__

def guarded_open(file, mode="r", *args, **kwargs):
    if any(char in mode for char in "wax+"):
        deny("file-write")
    return real_open(file, mode, *args, **kwargs)

def guarded_io_open(file, mode="r", *args, **kwargs):
    if any(char in mode for char in "wax+"):
        deny("file-write")
    return real_io_open(file, mode, *args, **kwargs)

def guarded_os_open(path, flags, *args, **kwargs):
    if flags & write_flags:
        deny("file-write")
    return real_os_open(path, flags, *args, **kwargs)

def guarded_import(name, *args, **kwargs):
    root = name.split(".", 1)[0]
    if root in ffi_modules:
        deny("native-ffi")
    if root in database_modules:
        deny("database")
    return real_import(name, *args, **kwargs)

builtins.open = guarded_open
builtins.__import__ = guarded_import
io.open = guarded_io_open
os.open = guarded_os_open
os.system = lambda *_args, **_kwargs: deny("process")
sqlite3.connect = lambda *_args, **_kwargs: deny("database")
subprocess.Popen = lambda *_args, **_kwargs: deny("process")
for name in (
    "chmod", "chown", "fchmod", "fchown", "lchown", "link", "makedirs",
    "mkdir", "mkfifo", "mknod", "remove", "removedirs", "removexattr",
    "rename", "renames", "replace", "rmdir", "setxattr", "symlink", "truncate",
    "unlink", "utime",
):
    if hasattr(os, name):
        setattr(os, name, lambda *_args, **_kwargs: deny("filesystem-mutation"))
for name in (
    "execv", "execve", "execvp", "execvpe", "fork", "forkpty", "kill", "killpg",
    "posix_spawn", "posix_spawnp", "spawnl", "spawnle", "spawnlp", "spawnlpe",
    "spawnv", "spawnve", "spawnvp", "spawnvpe",
):
    if hasattr(os, name):
        setattr(os, name, lambda *_args, **_kwargs: deny("process"))

pyvenv_cfg = runtime / venv_dirname / "pyvenv.cfg"
if pyvenv_cfg.is_symlink() or not pyvenv_cfg.is_file():
    raise SystemExit(3)
pyvenv_raw = pyvenv_cfg.read_bytes()
if hashlib.sha256(pyvenv_raw).hexdigest() != anchors["pyvenv_cfg_sha256"]:
    raise SystemExit(3)
homes = [
    line.partition("=")[2].strip()
    for line in pyvenv_raw.decode().splitlines()
    if line.partition("=")[0].strip().lower() == "home" and line.partition("=")[1]
]
if homes != [anchors["pyvenv_cfg_home"]]:
    raise SystemExit(3)
source_digest = tree_digest(runtime, {".git", venv_dirname})
venv_digest = tree_digest(runtime / venv_dirname)
candidate_digest = hashlib.sha256(
    f"{source_digest}:{venv_digest}".encode()
).hexdigest()
sink = io.StringIO()
try:
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        main_spec = importlib.util.find_spec("hermes_cli.main")
        if main_spec is None or main_spec.origin is None:
            raise ImportError("missing-main")
        server = importlib.import_module("agent.transports.hermes_kanban_mcp_server")
        result = {
            "hermes_cli_main_origin": str(pathlib.Path(main_spec.origin).resolve(strict=True)),
            "kanban_server_origin": str(pathlib.Path(server.__file__).resolve(strict=True)),
            "write_tools": list(server.WRITE_TOOLS),
            "stdlib_roots": [str(root) for root in stdlib_roots],
            "resolved_interpreter": str(pathlib.Path(sys.executable).resolve(strict=True)),
            "pyvenv_cfg_sha256": hashlib.sha256(pyvenv_raw).hexdigest(),
            "pyvenv_cfg_home": homes[0],
            "candidate_digest": candidate_digest,
            "source_digest": source_digest,
            "venv_digest": venv_digest,
            "bundle_manifest_sha256": anchors["bundle_manifest_sha256"],
        }
        if policy_violated():
            result = {"error": "side-effect-denied"}
except PolicyDenied:
    result = {"error": "side-effect-denied"}
except BaseException:
    result = {"error": "import-failed"}
sys.__stdout__.write(json.dumps(result, sort_keys=True) + "\n")
"""


@dataclass(frozen=True)
class _ParentAnchors:
    interpreter: Path
    resolved_interpreter: Path
    interpreter_chain: tuple[dict[str, Any], ...]
    pyvenv_cfg: Path
    pyvenv_cfg_sha256: str
    pyvenv_cfg_home: Path
    site_packages: Path
    trusted_interpreter: Path
    stdlib_roots: tuple[Path, ...]
    candidate_digest: str
    source_digest: str
    venv_digest: str


@dataclass
class _ParentTrustBundle:
    anchors: _ParentAnchors
    content: sealed_bundle.SealedContentBundle
    pyvenv_bytes: bytes
    pyvenv_mode: int
    closed: bool = False

    @property
    def descriptors(self) -> tuple[int, ...]:
        return self.content.descriptors

    def verify(self) -> None:
        if self.closed:
            raise RolloutError("parent trust bundle closed before verification")
        try:
            self.content.verify()
        except sealed_bundle.SandboxError as exc:
            raise _rollout_sandbox_error(exc) from exc

    def close(
        self,
        *,
        primary: BaseException | None = None,
        replacement_applied: bool = False,
    ) -> None:
        if self.closed:
            raise RolloutError("parent trust bundle was closed more than once")
        try:
            self.content.close(
                primary=primary, replacement_applied=replacement_applied
            )
        except sealed_bundle.SandboxError as exc:
            self.closed = True
            raise _rollout_sandbox_error(exc) from exc
        self.closed = True


def _rollout_sandbox_error(exc: sealed_bundle.SandboxError) -> RolloutError:
    return RolloutError(
        str(exc),
        primary_failure=exc.primary,
        cleanup_failures=exc.cleanup_failures,
        replacement_applied=exc.replacement_applied,
    )


def _interpreter_chain(path: Path) -> tuple[Path, tuple[dict[str, Any], ...]]:
    current = path
    chain: list[dict[str, Any]] = []
    for _ in range(41):
        try:
            info = current.lstat()
        except OSError as exc:
            raise RolloutError("cannot anchor candidate interpreter") from exc
        target = os.readlink(current) if stat.S_ISLNK(info.st_mode) else None
        chain.append(
            {
                "path": str(current),
                "mode": info.st_mode,
                "device": info.st_dev,
                "inode": info.st_ino,
                "size": info.st_size,
                "target": target,
            }
        )
        if target is None:
            if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o111:
                raise RolloutError("candidate interpreter target is not executable")
            return current, tuple(chain)
        current = Path(target) if Path(target).is_absolute() else current.parent / target
        current = Path(os.path.normpath(current))
    raise RolloutError("candidate interpreter symlink chain is too deep")


def _trusted_python() -> tuple[Path, tuple[Path, ...]]:
    executable = Path(getattr(sys, "_base_executable", sys.executable)).resolve(strict=True)
    roots: list[Path] = []
    variables = {"base": sys.base_prefix, "platbase": sys.base_exec_prefix}
    for name in ("stdlib", "platstdlib"):
        root = Path(sysconfig.get_path(name, vars=variables)).resolve(strict=True)
        if root not in roots:
            roots.append(root)
    if not roots or any(not root.is_dir() for root in roots):
        raise RolloutError("trusted parent interpreter stdlib roots are unavailable")
    return executable, tuple(roots)


def _pyvenv_home_bytes(raw: bytes) -> Path:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RolloutError("cannot anchor UTF-8 pyvenv.cfg") from exc
    homes = [
        value.strip()
        for line in text.splitlines()
        for key, separator, value in [line.partition("=")]
        if separator and key.strip().lower() == "home"
    ]
    if len(homes) != 1 or not homes[0]:
        raise RolloutError("pyvenv.cfg must contain one exact home")
    return canonical_path(homes[0], "pyvenv.cfg home", must_exist=True)


def _parent_trust_bundle(runtime: Path, venv_dirname: str) -> _ParentTrustBundle:
    interpreter = runtime / venv_dirname / "bin" / "python"
    pyvenv_cfg, _digest, site_packages = validate_venv_startup(
        runtime, venv_dirname
    )
    resolved, chain = _interpreter_chain(interpreter)
    trusted_interpreter, stdlib_roots = _trusted_python()
    try:
        content = sealed_bundle.capture_bundle(
            runtime,
            venv_dirname,
            trusted_interpreter,
            stdlib_roots,
            bwrap_path=invocation.BWRAP,
        )
    except sealed_bundle.SandboxError as exc:
        raise _rollout_sandbox_error(exc) from exc
    try:
        pyvenv_destination = (
            invocation.SANDBOX_RUNTIME / venv_dirname / "pyvenv.cfg"
        )
        pyvenv_entry = content.file_entry(pyvenv_destination)
        pyvenv_bytes = content.read_file(pyvenv_destination)
        pyvenv_digest = sha256(pyvenv_bytes)
        pyvenv_home = _pyvenv_home_bytes(pyvenv_bytes)
        if pyvenv_home != trusted_interpreter.parent:
            raise RolloutError(
                "pyvenv.cfg home does not match trusted system interpreter"
            )
        candidate_interpreter = content.file_entry(
            invocation.SANDBOX_RUNTIME / venv_dirname / "bin" / "python"
        )
        trusted_entry = content.file_entry(trusted_interpreter)
        if candidate_interpreter.digest != trusted_entry.digest:
            raise RolloutError("candidate interpreter does not match trusted system Python")
        if _interpreter_chain(interpreter) != (resolved, chain):
            raise RolloutError("candidate interpreter changed during sealed capture")
        source_digest = content.source_digest
        venv_digest = content.venv_digest
        anchors = _ParentAnchors(
            interpreter,
            resolved,
            chain,
            pyvenv_cfg,
            pyvenv_digest,
            pyvenv_home,
            site_packages,
            trusted_interpreter,
            stdlib_roots,
            sha256(f"{source_digest}:{venv_digest}".encode()),
            source_digest,
            venv_digest,
        )
        bundle = _ParentTrustBundle(
            anchors,
            content,
            pyvenv_bytes,
            pyvenv_entry.mode,
        )
        bundle.verify()
        return bundle
    except BaseException as exc:
        try:
            content.close(primary=exc)
        except sealed_bundle.SandboxError as exc:
            raise _rollout_sandbox_error(exc) from exc
        raise


def _exact_origin(raw: Any, expected: Path, label: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise RolloutError("target import preflight returned invalid evidence")
    observed = Path(raw)
    if observed != expected:
        raise RolloutError(f"{label} did not import from the exact candidate checkout")
    return observed


def _run_import_preflight(
    bundle: _ParentTrustBundle,
    runtime: Path,
    venv_dirname: str,
    *,
    expected_pyvenv_cfg_sha256: str | None = None,
    expected_site_packages: str | None = None,
    expected_stdlib_roots: Sequence[str] | None = None,
) -> ImportEvidence:
    anchors = bundle.anchors
    if (
        expected_pyvenv_cfg_sha256 is not None
        and anchors.pyvenv_cfg_sha256 != expected_pyvenv_cfg_sha256
    ):
        raise RolloutError("pyvenv.cfg SHA-256 does not match trusted evidence")
    if expected_site_packages is not None and str(anchors.site_packages) != expected_site_packages:
        raise RolloutError("site-packages changed since prepare")
    if expected_stdlib_roots is not None and tuple(map(str, anchors.stdlib_roots)) != tuple(
        expected_stdlib_roots
    ):
        raise RolloutError("trusted interpreter stdlib roots changed since prepare")
    bwrap_sha256 = bundle.content.bwrap_sha256
    sandbox_runtime = invocation.SANDBOX_RUNTIME
    sandbox_site = sandbox_runtime / venv_dirname / "lib" / (
        f"python{sys.version_info.major}.{sys.version_info.minor}"
    ) / "site-packages"
    sandbox_interpreter = sandbox_runtime / venv_dirname / "bin" / "python"
    anchor_data = {
        "site_packages": str(sandbox_site),
        "stdlib_roots": [str(path) for path in anchors.stdlib_roots],
        "pyvenv_cfg_sha256": anchors.pyvenv_cfg_sha256,
        "pyvenv_cfg_home": str(anchors.pyvenv_cfg_home),
        "bundle_manifest_sha256": bundle.content.manifest_sha256,
    }
    bundle.verify()
    try:
        output = os_sandbox.run(
            bundle=bundle.content,
            venv_dirname=venv_dirname,
            harness_bytes=_IMPORT_PREFLIGHT_CODE.encode(),
            anchors_bytes=json.dumps(anchor_data, sort_keys=True).encode(),
        )
    except sealed_bundle.SandboxError as exc:
        raise _rollout_sandbox_error(exc) from exc
    try:
        value = json.loads(output.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RolloutError("target import preflight returned invalid evidence") from exc
    if not isinstance(value, dict) or "error" in value:
        raise RolloutError("target import preflight was denied by the isolated policy")
    expected_keys = {
        "bundle_manifest_sha256", "candidate_digest",
        "hermes_cli_main_origin", "kanban_server_origin",
        "pyvenv_cfg_home", "pyvenv_cfg_sha256", "resolved_interpreter",
        "source_digest", "stdlib_roots", "venv_digest", "write_tools",
    }
    if set(value) != expected_keys:
        raise RolloutError("target import preflight returned unexpected evidence")
    main_origin = _exact_origin(
        value["hermes_cli_main_origin"],
        sandbox_runtime / "hermes_cli" / "main.py",
        "hermes_cli.main",
    )
    server_origin = _exact_origin(
        value["kanban_server_origin"],
        sandbox_runtime / "agent" / "transports" / "hermes_kanban_mcp_server.py",
        "Kanban server",
    )
    expected_child = {
        "resolved_interpreter": str(sandbox_interpreter),
        "pyvenv_cfg_sha256": anchors.pyvenv_cfg_sha256,
        "pyvenv_cfg_home": str(anchors.pyvenv_cfg_home),
        "candidate_digest": anchors.candidate_digest,
        "source_digest": anchors.source_digest,
        "venv_digest": anchors.venv_digest,
        "stdlib_roots": [str(path) for path in anchors.stdlib_roots],
        "bundle_manifest_sha256": bundle.content.manifest_sha256,
    }
    mismatch = next(
        (key for key, expected in expected_child.items() if value[key] != expected),
        None,
    )
    if mismatch is not None:
        raise RolloutError(
            f"target import preflight evidence does not match parent anchor: {mismatch}"
        )
    tools = value["write_tools"]
    if (
        not isinstance(tools, list)
        or any(not isinstance(tool, str) or not tool for tool in tools)
        or len(set(tools)) != len(tools)
        or REQUIRED_WRITE_TOOL not in tools
    ):
        raise RolloutError("target WRITE_TOOLS evidence is invalid or missing external sync")
    bundle.verify()
    return ImportEvidence(
        runtime,
        anchors.interpreter,
        anchors.resolved_interpreter,
        anchors.interpreter_chain,
        anchors.pyvenv_cfg,
        anchors.pyvenv_cfg_sha256,
        anchors.pyvenv_cfg_home,
        anchors.site_packages,
        anchors.trusted_interpreter,
        anchors.stdlib_roots,
        anchors.candidate_digest,
        anchors.source_digest,
        anchors.venv_digest,
        bwrap_sha256,
        runtime / main_origin.relative_to(sandbox_runtime),
        runtime / server_origin.relative_to(sandbox_runtime),
        tuple(tools),
    )


@contextmanager
def import_preflight_session(
    runtime: Path,
    venv_dirname: str,
    *,
    expected_pyvenv_cfg_sha256: str | None = None,
    expected_site_packages: str | None = None,
    expected_stdlib_roots: Sequence[str] | None = None,
) -> Iterator[ImportEvidence]:
    bundle = _parent_trust_bundle(runtime, venv_dirname)
    primary: BaseException | None = None
    try:
        evidence = _run_import_preflight(
            bundle,
            runtime,
            venv_dirname,
            expected_pyvenv_cfg_sha256=expected_pyvenv_cfg_sha256,
            expected_site_packages=expected_site_packages,
            expected_stdlib_roots=expected_stdlib_roots,
        )
        yield evidence
        bundle.verify()
    except BaseException as exc:
        primary = exc
        raise
    finally:
        bundle.close(primary=primary)


def run_import_preflight(
    runtime: Path,
    venv_dirname: str,
    *,
    expected_pyvenv_cfg_sha256: str | None = None,
    expected_site_packages: str | None = None,
    expected_stdlib_roots: Sequence[str] | None = None,
) -> ImportEvidence:
    with import_preflight_session(
        runtime,
        venv_dirname,
        expected_pyvenv_cfg_sha256=expected_pyvenv_cfg_sha256,
        expected_site_packages=expected_site_packages,
        expected_stdlib_roots=expected_stdlib_roots,
    ) as evidence:
        return evidence
