"""Deterministic pre-acquisition resource budgets for sealed bwrap execution."""

from __future__ import annotations

import os
import resource
import sys
from dataclasses import dataclass
from typing import Mapping, Sequence


FD_SUBPROCESS_RESERVE = 16
FD_BWRAP_RESERVE = 16
FD_FIXED_DATA_OBJECTS = 5  # manifest, harness, anchors, probe args, prod args
FD_EXECUTABLE_HANDOFF = 1
ARG_MAX_SAFETY_MARGIN = 32 * 1024
BWRAP_ARGS_MAX_BYTES = 4 * 1024 * 1024
TOPOLOGY_MAX_BYTES = 16 * 1024 * 1024


class ResourceBudgetError(ValueError):
    """The complete planned invocation does not fit a finite platform limit."""


class SandboxError(RuntimeError):
    """Structured primary/cleanup/replacement failure at the sandbox boundary."""

    def __init__(
        self,
        message: str,
        *,
        primary: BaseException | None = None,
        secondary_failures: Sequence[str] = (),
        cleanup_failures: Sequence[str] = (),
        replacement_applied: bool = False,
    ) -> None:
        super().__init__(message)
        self.primary = primary
        self.secondary_failures = tuple(secondary_failures)
        self.cleanup_failures = tuple(cleanup_failures)
        self.replacement_applied = replacement_applied


class FDResourceOwner:
    """Own every acquired descriptor from acquisition through invocation."""

    def __init__(self) -> None:
        self._fds: list[int] = []
        self.closed = False

    @property
    def fds(self) -> tuple[int, ...]:
        return tuple(self._fds)

    def register(self, fd: int) -> int:
        if self.closed:
            try:
                os.close(fd)
            except OSError as cleanup:
                raise SandboxError(
                    "cannot register or close a descriptor on a closed sealed bundle",
                    cleanup_failures=(f"fd={fd}: {cleanup}",),
                ) from cleanup
            raise SandboxError("cannot register a descriptor on a closed sealed bundle")
        self._fds.append(fd)
        return fd

    def close_one(self, fd: int, *, primary: BaseException | None = None) -> None:
        if fd not in self._fds:
            raise SandboxError("sealed bundle descriptor ownership was lost")
        try:
            os.close(fd)
        except OSError as exc:
            raise SandboxError(
                "cannot deterministically close sealed bundle descriptor",
                primary=primary,
                cleanup_failures=(f"fd={fd}: {exc}",),
            ) from exc
        self._fds.remove(fd)

    def close_all(
        self,
        *,
        primary: BaseException | None = None,
        replacement_applied: bool = False,
    ) -> None:
        if self.closed:
            raise SandboxError(
                "sealed content bundle was closed more than once",
                primary=primary,
                replacement_applied=replacement_applied,
            )
        failures = []
        remaining = []
        for fd in reversed(self._fds):
            try:
                os.close(fd)
            except OSError as exc:
                failures.append(f"fd={fd}: {exc}")
                try:
                    os.fstat(fd)
                except OSError:
                    continue
                try:
                    os.close(fd)
                except OSError as retry:
                    failures.append(f"fd={fd} retry: {retry}")
                    remaining.append(fd)
        self._fds[:] = reversed(remaining)
        self.closed = not remaining
        if failures:
            raise SandboxError(
                "cannot deterministically close sealed content bundle",
                primary=primary,
                cleanup_failures=failures,
                replacement_applied=replacement_applied,
            )


@dataclass(frozen=True)
class ResourcePlan:
    current_open_fds: int
    content_fds: int
    acquisition_temporary_fds: int
    fixed_fds: int
    handoff_fds: int
    reserve_fds: int
    required_fds: int
    nofile_soft: int
    exec_bytes: int
    arg_max: int
    arg_margin: int
    probe_args_bytes: int
    production_args_bytes: int
    bwrap_args_max: int
    topology_bytes: int


def open_fd_count() -> int:
    try:
        return len(os.listdir("/proc/self/fd"))
    except OSError as exc:
        raise ResourceBudgetError("cannot count current open descriptors") from exc


def check_actual_fds(
    *,
    additional_fds: int,
    pass_fd_count: int,
    nofile_soft: int,
) -> None:
    if min(additional_fds, pass_fd_count) < 0:
        raise ResourceBudgetError("resource counts cannot be negative")
    current = open_fd_count()
    required = current + additional_fds + FD_SUBPROCESS_RESERVE + FD_BWRAP_RESERVE
    if required > nofile_soft:
        raise ResourceBudgetError(
            f"sealed invocation requires {required} FDs but RLIMIT_NOFILE is {nofile_soft}"
        )
    if pass_fd_count > current + additional_fds:
        raise ResourceBudgetError("pass_fds exceeds current descriptor capacity")


def check_final_handoff(
    pass_fds: tuple[int, ...],
    *,
    expected_fds: tuple[int, ...],
    nofile_soft: int,
) -> None:
    if pass_fds != expected_fds:
        raise ResourceBudgetError("final pass_fds does not match exact sealed ownership")
    if len(pass_fds) != len(set(pass_fds)):
        raise ResourceBudgetError("final pass_fds contains duplicate descriptors")
    if not isinstance(nofile_soft, int) or nofile_soft <= 0:
        raise ResourceBudgetError("RLIMIT_NOFILE must have a finite soft limit")
    for fd in pass_fds:
        if fd < 0 or fd >= nofile_soft:
            raise ResourceBudgetError("final pass_fds exceeds finite RLIMIT_NOFILE")
        try:
            os.fstat(fd)
        except OSError as exc:
            raise ResourceBudgetError("final pass_fds contains a closed descriptor") from exc
    current = open_fd_count()
    peak = current + FD_SUBPROCESS_RESERVE + FD_BWRAP_RESERVE
    if peak > nofile_soft:
        raise ResourceBudgetError(
            f"sealed invocation requires {peak} FDs but RLIMIT_NOFILE is {nofile_soft}"
        )
    if len(pass_fds) > current:
        raise ResourceBudgetError("final pass_fds exceeds current descriptor capacity")


def serialized_args(arguments: Sequence[str]) -> bytes:
    if any("\x00" in item for item in arguments):
        raise ResourceBudgetError("bubblewrap arguments contain NUL")
    try:
        return b"\0".join(item.encode() for item in arguments) + b"\0"
    except UnicodeEncodeError as exc:
        raise ResourceBudgetError("bubblewrap arguments are not encodable") from exc


def exec_size(argv: Sequence[str], env: Mapping[str, str]) -> int:
    values = [*argv, *(f"{name}={value}" for name, value in env.items())]
    if any("\x00" in item for item in values):
        raise ResourceBudgetError("exec argv/environment contains NUL")
    return sum(len(item.encode()) + 1 for item in values)


def check_exec_budget(
    argv: Sequence[str],
    env: Mapping[str, str],
    *,
    arg_max: int | None = None,
) -> int:
    observed = os.sysconf("SC_ARG_MAX") if arg_max is None else arg_max
    if not isinstance(observed, int) or observed <= ARG_MAX_SAFETY_MARGIN:
        raise ResourceBudgetError("SC_ARG_MAX cannot provide the named safety margin")
    size = exec_size(argv, env)
    if size > observed - ARG_MAX_SAFETY_MARGIN:
        raise ResourceBudgetError("exec argv/environment exceeds SC_ARG_MAX budget")
    return size


def plan_resources(
    *,
    content_fds: int,
    acquisition_temporary_fds: int,
    topology_bytes: int,
    probe_arguments: Sequence[str],
    production_arguments: Sequence[str],
    exec_argv: Sequence[str],
    exec_env: Mapping[str, str],
    pass_fd_count: int | None = None,
    current_open_fds: int | None = None,
    nofile_soft: int | None = None,
    arg_max: int | None = None,
    args_max: int = BWRAP_ARGS_MAX_BYTES,
) -> ResourcePlan:
    if sys.platform != "linux" or not hasattr(os, "memfd_create"):
        raise ResourceBudgetError("platform cannot support sealed bwrap execution")
    if min(content_fds, acquisition_temporary_fds, topology_bytes, args_max) < 0:
        raise ResourceBudgetError("resource counts cannot be negative")
    if topology_bytes > TOPOLOGY_MAX_BYTES:
        raise ResourceBudgetError("sealed manifest topology exceeds named maximum")
    current = open_fd_count() if current_open_fds is None else current_open_fds
    soft = resource.getrlimit(resource.RLIMIT_NOFILE)[0] if nofile_soft is None else nofile_soft
    if soft == resource.RLIM_INFINITY:
        raise ResourceBudgetError("RLIMIT_NOFILE must have a finite soft limit")
    fixed = FD_FIXED_DATA_OBJECTS
    handoff = FD_EXECUTABLE_HANDOFF
    reserve = FD_SUBPROCESS_RESERVE + FD_BWRAP_RESERVE
    required = current + content_fds + acquisition_temporary_fds + fixed + handoff + reserve
    if pass_fd_count is not None and pass_fd_count > content_fds + fixed + handoff:
        raise ResourceBudgetError("pass_fds exceeds planned descriptor ownership")
    if required > soft:
        raise ResourceBudgetError(
            f"sealed invocation requires {required} FDs but RLIMIT_NOFILE is {soft}"
        )
    probe = serialized_args(probe_arguments)
    production = serialized_args(production_arguments)
    if len(probe) > args_max or len(production) > args_max:
        raise ResourceBudgetError("serialized bwrap --args exceeds named maximum")
    observed_arg_max = os.sysconf("SC_ARG_MAX") if arg_max is None else arg_max
    size = check_exec_budget(exec_argv, exec_env, arg_max=observed_arg_max)
    return ResourcePlan(
        current,
        content_fds,
        acquisition_temporary_fds,
        fixed,
        handoff,
        reserve,
        required,
        soft,
        size,
        observed_arg_max,
        ARG_MAX_SAFETY_MARGIN,
        len(probe),
        len(production),
        args_max,
        topology_bytes,
    )
