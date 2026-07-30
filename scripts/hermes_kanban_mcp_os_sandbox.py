"""Bubblewrap execution boundary over an immutable sealed-content bundle."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path
from typing import Sequence

try:
    from scripts import hermes_kanban_mcp_sealed_bundle as sealed
    from scripts import hermes_kanban_mcp_resources as resources
    from scripts import hermes_kanban_mcp_invocation as invocation
except ImportError:  # Direct execution from scripts/.
    import hermes_kanban_mcp_sealed_bundle as sealed
    import hermes_kanban_mcp_resources as resources
    import hermes_kanban_mcp_invocation as invocation


SandboxError = sealed.SandboxError
SealedContentBundle = sealed.SealedContentBundle
_read_all = sealed._read_all


def identity() -> tuple[str, tuple[int, int, int, int]]:
    descriptor = -1
    primary: BaseException | None = None
    try:
        descriptor = os.open(
            invocation.BWRAP,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o111:
            raise SandboxError("exact /usr/bin/bwrap is not a regular executable")
        data = _read_all(descriptor)
        return hashlib.sha256(data).hexdigest(), (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_size,
        )
    except BaseException as exc:
        primary = exc
        if isinstance(exc, SandboxError):
            raise
        raise SandboxError("exact /usr/bin/bwrap is unavailable", primary=exc) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError as cleanup:
                raise SandboxError(
                    "cannot close exact bwrap identity descriptor",
                    primary=primary,
                    cleanup_failures=(f"fd={descriptor}: {cleanup}",),
                ) from cleanup


def _args_data(arguments: Sequence[str]) -> bytes:
    try:
        data = resources.serialized_args(arguments)
    except resources.ResourceBudgetError as exc:
        raise SandboxError(str(exc), primary=exc) from exc
    if len(data) > resources.BWRAP_ARGS_MAX_BYTES:
        raise SandboxError("serialized bwrap --args exceeds named maximum")
    return data


def _final_handoff(bundle: SealedContentBundle) -> tuple[int, ...]:
    pass_fds = bundle.descriptors
    try:
        resources.check_final_handoff(
            pass_fds,
            expected_fds=bundle.descriptors,
            nofile_soft=bundle.invocation.nofile_soft,
        )
    except resources.ResourceBudgetError as exc:
        raise SandboxError(str(exc), primary=exc) from exc
    return pass_fds


def _probe(bundle: SealedContentBundle) -> None:
    try:
        resources.check_actual_fds(
            additional_fds=1,
            pass_fd_count=len(bundle.descriptors) + 1,
            nofile_soft=bundle.invocation.nofile_soft,
        )
        available = _roles(bundle)
        roles = _phase_roles(bundle, available, "probe", probe_args=bundle.invocation.nofile_soft - 1)
        probe_arguments, _symbolic_exec = bundle.invocation.render_probe(
            roles
        )
        probe_data = _args_data(probe_arguments)
    except resources.ResourceBudgetError as exc:
        raise SandboxError(str(exc), primary=exc) from exc
    args_fd = bundle.add_data("kanban-bwrap-probe-args", probe_data)
    _arguments, command = bundle.invocation.render_probe(
        _phase_roles(bundle, available, "probe", probe_args=args_fd)
    )
    try:
        pass_fds = _final_handoff(bundle)
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={},
            pass_fds=pass_fds,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxError("bubblewrap capability probe could not run", primary=exc) from exc
    if completed.returncode:
        raise SandboxError("bubblewrap cannot create the required namespace profile")


def _roles(bundle: SealedContentBundle) -> dict[str, int]:
    result = {
        invocation.file_role(entry.destination): entry.fd
        for entry in bundle.entries
        if entry.kind == "file" and entry.fd is not None
    }
    result.update(
        {
            "loader": bundle.loader_fd,
            "bwrap": bundle.bwrap_fd,
            **{
                f"library:{index}": fd
                for index, fd in enumerate(bundle.bwrap_library_fds)
            },
        }
    )
    return result


def _phase_roles(
    bundle: SealedContentBundle,
    available: dict[str, int],
    phase: str,
    **phase_fds: int,
) -> dict[str, int]:
    values = {**available, **phase_fds}
    try:
        return {
            role: values[role]
            for role in bundle.invocation.required_roles(phase)
        }
    except KeyError as exc:
        raise resources.ResourceBudgetError(
            f"{phase} role map is missing a required descriptor"
        ) from exc


def run(
    *,
    bundle: SealedContentBundle,
    venv_dirname: str,
    harness_bytes: bytes,
    anchors_bytes: bytes,
) -> bytes:
    """Run the baseline probe, then the authoritative sealed invocation."""
    bundle.verify()
    _probe(bundle)
    try:
        try:
            resources.check_actual_fds(
                additional_fds=3,
                pass_fd_count=len(bundle.descriptors) + 3,
                nofile_soft=bundle.invocation.nofile_soft,
            )
        except resources.ResourceBudgetError as exc:
            raise SandboxError(str(exc), primary=exc) from exc
        harness_fd = bundle.add_data("kanban-preflight", harness_bytes)
        anchors_fd = bundle.add_data("kanban-anchors", anchors_bytes)
        available = {
            **_roles(bundle),
            "harness": harness_fd,
            "anchors": anchors_fd,
        }
        roles = _phase_roles(
            bundle,
            available,
            "production",
            production_args=bundle.invocation.nofile_soft - 1,
        )
        try:
            arguments, _symbolic_exec = bundle.invocation.render_production(roles)
        except resources.ResourceBudgetError as exc:
            raise SandboxError(str(exc), primary=exc) from exc
        production_data = _args_data(arguments)
        args_fd = bundle.add_data(
            "kanban-bwrap-production-args", production_data
        )
        try:
            _arguments, command = bundle.invocation.render_production(
                _phase_roles(
                    bundle,
                    available,
                    "production",
                    production_args=args_fd,
                )
            )
        except resources.ResourceBudgetError as exc:
            raise SandboxError(str(exc), primary=exc) from exc
        pass_fds = _final_handoff(bundle)
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={},
            pass_fds=pass_fds,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxError("target import preflight could not run", primary=exc) from exc
    if completed.returncode:
        raise SandboxError("target import preflight failed inside bubblewrap")
    bundle.verify()
    return completed.stdout
