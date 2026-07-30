"""Canonical probe/production invocation model with symbolic FD budgeting."""

from __future__ import annotations

import os
import resource
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

try:
    from scripts import hermes_kanban_mcp_resources as resources
except ImportError:
    import hermes_kanban_mcp_resources as resources


BWRAP = Path("/usr/bin/bwrap")
SANDBOX_RUNTIME = Path("/candidate")
SANDBOX_ENV = {
    "HOME": "/sandbox/home",
    "HERMES_HOME": "/sandbox/hermes-home",
    "TMPDIR": "/sandbox/tmp",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "TZ": "UTC",
    "PYTHONDONTWRITEBYTECODE": "1",
}


@dataclass(frozen=True)
class FDArg:
    role: str
    prefix: str = ""


@dataclass(frozen=True)
class FDListArg:
    roles: tuple[str, ...]
    prefix: str = ""


Argument = str | FDArg | FDListArg


def _base_args() -> tuple[str, ...]:
    result = [
        str(BWRAP),
        "--unshare-user", "--disable-userns", "--unshare-pid", "--unshare-ipc",
        "--unshare-uts", "--unshare-cgroup", "--unshare-net",
        "--new-session", "--die-with-parent", "--clearenv",
    ]
    for name, value in SANDBOX_ENV.items():
        result.extend(("--setenv", name, value))
    for root in (Path("/usr"), Path("/lib"), Path("/lib64")):
        if root.exists():
            result.extend(("--ro-bind", str(root), str(root)))
    result.extend(
        (
            "--proc", "/proc", "--dev", "/dev", "--dir", "/sandbox",
            "--dir", "/sandbox/home", "--dir", "/sandbox/hermes-home",
            "--dir", "/sandbox/tmp", "--tmpfs", "/sandbox/home",
            "--tmpfs", "/sandbox/hermes-home", "--tmpfs", "/sandbox/tmp",
        )
    )
    return tuple(result)


def _production_base() -> tuple[str, ...]:
    result = []
    args = _base_args()
    index = 0
    while index < len(args):
        if args[index] == "--ro-bind":
            index += 3
        else:
            result.append(args[index])
            index += 1
    return tuple(result)


def file_role(destination: Path) -> str:
    return f"file:{destination}"


def _render(arguments: Sequence[Argument], roles: Mapping[str, int | str]) -> tuple[str, ...]:
    result = []
    for argument in arguments:
        if isinstance(argument, FDArg):
            result.append(argument.prefix + str(roles[argument.role]))
        elif isinstance(argument, FDListArg):
            result.append(
                ":".join(argument.prefix + str(roles[role]) for role in argument.roles)
            )
        else:
            result.append(argument)
    return tuple(result)


def _role_order(arguments: Sequence[Argument]) -> tuple[str, ...]:
    result: list[str] = []
    for argument in arguments:
        found = (
            (argument.role,)
            if isinstance(argument, FDArg)
            else argument.roles
            if isinstance(argument, FDListArg)
            else ()
        )
        for role in found:
            if role not in result:
                result.append(role)
    return tuple(result)


def _launcher(library_roles: tuple[str, ...], args_role: str) -> tuple[Argument, ...]:
    result: list[Argument] = [
        FDArg("loader", "/proc/self/fd/"),
        "--inhibit-cache",
    ]
    if library_roles:
        result.extend(
            ("--preload", FDListArg(library_roles, "/proc/self/fd/"))
        )
    result.extend(
        (
            FDArg("bwrap", "/proc/self/fd/"),
            "--args",
            FDArg(args_role),
        )
    )
    return tuple(result)


@dataclass(frozen=True)
class CanonicalInvocationSpec:
    probe_arguments: tuple[Argument, ...]
    production_arguments: tuple[Argument, ...]
    probe_exec: tuple[Argument, ...]
    production_exec: tuple[Argument, ...]
    content_roles: tuple[str, ...]
    fd_width: int
    probe_args_bound: int
    production_args_bound: int
    probe_exec_bound: int
    production_exec_bound: int
    nofile_soft: int

    def required_roles(self, phase: str) -> tuple[str, ...]:
        if phase == "probe":
            return _role_order((*self.probe_arguments, *self.probe_exec))
        if phase == "production":
            return _role_order((*self.production_arguments, *self.production_exec))
        raise resources.ResourceBudgetError("unknown canonical invocation phase")

    def render_probe(self, roles: Mapping[str, int]) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return self._actual(self.probe_arguments, self.probe_exec, roles, "probe")

    def render_production(
        self, roles: Mapping[str, int]
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return self._actual(
            self.production_arguments, self.production_exec, roles, "production"
        )

    def _actual(
        self,
        arguments: tuple[Argument, ...],
        execution: tuple[Argument, ...],
        roles: Mapping[str, int],
        phase: str,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        if tuple(roles) != self.required_roles(phase):
            raise resources.ResourceBudgetError(
                f"{phase} role map does not match exact required role order"
            )
        if any(fd < 0 or fd >= self.nofile_soft for fd in roles.values()):
            raise resources.ResourceBudgetError(
                f"{phase} FD rendering exceeds finite RLIMIT_NOFILE"
            )
        rendered_args = _render(arguments, roles)
        rendered_exec = _render(execution, roles)
        args_size = len(resources.serialized_args(rendered_args))
        exec_size = resources.exec_size(rendered_exec, {})
        args_bound = (
            self.probe_args_bound if phase == "probe" else self.production_args_bound
        )
        exec_bound = (
            self.probe_exec_bound if phase == "probe" else self.production_exec_bound
        )
        if args_size > args_bound or exec_size > exec_bound:
            raise resources.ResourceBudgetError(
                f"actual {phase} invocation exceeded its prevalidated symbolic bound"
            )
        resources.check_exec_budget(rendered_exec, {})
        if args_size > resources.BWRAP_ARGS_MAX_BYTES:
            raise resources.ResourceBudgetError(
                "serialized bwrap --args exceeds named maximum"
            )
        return rendered_args, rendered_exec


def build(
    entries: Sequence[object],
    elf_dependencies: Sequence[tuple[Path, tuple[Path, ...]]],
    *,
    bwrap_path: Path,
    venv_dirname: str,
    acquisition_temporary_fds: int,
    current_open_fds: int | None = None,
    nofile_soft: int | None = None,
) -> tuple[CanonicalInvocationSpec, resources.ResourcePlan]:
    soft = resource.getrlimit(resource.RLIMIT_NOFILE)[0] if nofile_soft is None else nofile_soft
    if soft == resource.RLIM_INFINITY or not isinstance(soft, int) or soft <= 0:
        raise resources.ResourceBudgetError("RLIMIT_NOFILE must have a finite soft limit")
    fd_width = len(str(soft - 1))
    symbolic = "9" * fd_width
    files = tuple(entry for entry in entries if entry.kind == "file")
    content_roles = tuple(file_role(entry.destination) for entry in files)
    direct = dict(elf_dependencies)
    closure = []
    seen = {bwrap_path}
    queue = list(direct.get(bwrap_path, ()))
    while queue:
        path = queue.pop(0)
        if path in seen:
            continue
        seen.add(path)
        closure.append(path)
        queue.extend(direct.get(path, ()))
    loader = next(
        (path for path in closure if path.name.startswith("ld-") or "ld-linux" in path.name),
        None,
    )
    if loader is None:
        raise resources.ResourceBudgetError("canonical invocation has no ELF loader role")
    libraries = tuple(path for path in closure if path != loader)
    library_roles = tuple(f"library:{index}" for index in range(len(libraries)))
    probe_arguments: tuple[Argument, ...] = _base_args()[1:]
    production: list[Argument] = list(_production_base()[1:])
    directories = sorted(
        (entry for entry in entries if entry.kind == "directory"),
        key=lambda entry: (len(entry.destination.parts), str(entry.destination)),
    )
    for entry in directories:
        production.extend(("--perms", f"{entry.mode:o}", "--dir", str(entry.destination)))
    for entry in entries:
        if entry.kind == "file":
            production.extend(
                (
                    "--perms", f"{entry.mode:o}", "--ro-bind-data",
                    FDArg(file_role(entry.destination)), str(entry.destination),
                )
            )
        elif entry.kind == "symlink":
            production.extend(("--symlink", entry.target or "", str(entry.destination)))
    production.extend(
        (
            "--perms", "444", "--ro-bind-data", FDArg("harness"),
            "/sandbox/preflight.py", "--perms", "444", "--ro-bind-data",
            FDArg("anchors"), "/sandbox/anchors.json", "--chdir", str(SANDBOX_RUNTIME),
        )
    )
    probe_exec = (
        *_launcher(library_roles, "probe_args"),
        "--", "/usr/bin/true",
    )
    production_exec = (
        *_launcher(library_roles, "production_args"),
        "--", str(SANDBOX_RUNTIME / venv_dirname / "bin" / "python"),
        "-I", "-S", "-B", "/sandbox/preflight.py",
        str(SANDBOX_RUNTIME), venv_dirname,
    )
    symbolic_roles = {
        role: symbolic
        for role in (
            *content_roles, "loader", "bwrap", *library_roles,
            "harness", "anchors", "probe_args", "production_args",
        )
    }
    rendered_probe_args = _render(probe_arguments, symbolic_roles)
    rendered_production_args = _render(tuple(production), symbolic_roles)
    rendered_probe_exec = _render(probe_exec, symbolic_roles)
    rendered_production_exec = _render(production_exec, symbolic_roles)
    topology_bytes = sum(
        len(str(entry.destination).encode())
        + len((entry.target or "").encode())
        + len(entry.kind)
        + 32
        for entry in entries
    )
    plan = resources.plan_resources(
        content_fds=len(files),
        acquisition_temporary_fds=acquisition_temporary_fds,
        topology_bytes=topology_bytes,
        probe_arguments=rendered_probe_args,
        production_arguments=rendered_production_args,
        exec_argv=rendered_probe_exec,
        exec_env={},
        pass_fd_count=len(files) + 1,
        current_open_fds=current_open_fds,
        nofile_soft=soft,
        args_max=resources.BWRAP_ARGS_MAX_BYTES,
    )
    resources.check_exec_budget(rendered_production_exec, {})
    spec = CanonicalInvocationSpec(
        probe_arguments,
        tuple(production),
        probe_exec,
        production_exec,
        content_roles,
        fd_width,
        len(resources.serialized_args(rendered_probe_args)),
        len(resources.serialized_args(rendered_production_args)),
        resources.exec_size(rendered_probe_exec, {}),
        resources.exec_size(rendered_production_exec, {}),
        soft,
    )
    return spec, plan
