"""Bubblewrap execution boundary over an immutable sealed-content bundle."""

from __future__ import annotations

import hashlib
import hmac
import os
import selectors
import signal
import stat
import subprocess
import time
from dataclasses import dataclass
from typing import Sequence

try:
    from scripts import hermes_kanban_mcp_invocation as invocation
    from scripts import hermes_kanban_mcp_resources as resources
    from scripts import hermes_kanban_mcp_sealed_bundle as sealed
except ImportError:  # Direct execution from scripts/.
    import hermes_kanban_mcp_invocation as invocation
    import hermes_kanban_mcp_resources as resources
    import hermes_kanban_mcp_sealed_bundle as sealed


SandboxError = sealed.SandboxError
SealedContentBundle = sealed.SealedContentBundle
_read_all = sealed._read_all

BWRAP_STDOUT_CAPTURE_LIMIT = 65536
BWRAP_STDERR_CAPTURE_LIMIT = 65536
_CAPTURE_CHUNK_BYTES = 8192
_CHILD_STOP_GRACE_SECONDS = 1.0
_PIPE_CLOSE_GRACE_SECONDS = 0.25


@dataclass(frozen=True)
class _BwrapAnchor:
    sha256: str
    device: int
    inode: int
    uid: int
    gid: int
    mode: int
    size: int


@dataclass
class _BwrapHandoff:
    descriptor: int
    anchor: _BwrapAnchor


def _safe_failure(code: str) -> RuntimeError:
    """Keep child and syscall details out of rollout-visible error evidence."""
    return RuntimeError(code)


def _preserve_failure_with_cleanup(
    failure: BaseException,
    *,
    message: str,
    primary_code: str,
    cleanup_failures: Sequence[str],
) -> SandboxError:
    if isinstance(failure, SandboxError):
        return SandboxError(
            str(failure),
            primary=failure.primary or failure,
            secondary_failures=failure.secondary_failures,
            cleanup_failures=(*failure.cleanup_failures, *cleanup_failures),
        )
    return SandboxError(
        message,
        primary=_safe_failure(primary_code),
        cleanup_failures=cleanup_failures,
    )


def _anchor_from_descriptor(descriptor: int) -> _BwrapAnchor:
    info = os.fstat(descriptor)
    mode = stat.S_IMODE(info.st_mode)
    if (
        not stat.S_ISREG(info.st_mode)
        or not mode & 0o111
        or info.st_uid != 0
        or info.st_gid != 0
        or mode & 0o022
    ):
        raise SandboxError("exact /usr/bin/bwrap is not a regular executable")
    return _BwrapAnchor(
        hashlib.sha256(_read_all(descriptor)).hexdigest(),
        info.st_dev,
        info.st_ino,
        info.st_uid,
        info.st_gid,
        mode,
        info.st_size,
    )


def _open_canonical_bwrap() -> int:
    directory = -1
    descriptor = -1
    open_error: OSError | None = None
    cleanup_failures: list[str] = []
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(nofollow, int) or nofollow <= 0:
        raise SandboxError(
            "exact /usr/bin/bwrap requires O_NOFOLLOW verification",
            primary=_safe_failure("bwrap_nofollow_unavailable"),
        )
    try:
        directory = os.open(
            invocation.BWRAP.parent,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0),
        )
        descriptor = os.open(
            invocation.BWRAP.name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | nofollow,
            dir_fd=directory,
        )
    except OSError as exc:
        open_error = exc
    if directory >= 0:
        try:
            os.close(directory)
        except OSError:
            cleanup_failures.append("bwrap_directory_cleanup_failed")
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    cleanup_failures.append("bwrap_descriptor_cleanup_failed")
                descriptor = -1
    if open_error is not None:
        failure = SandboxError(
            "exact /usr/bin/bwrap is unavailable",
            primary=_safe_failure("bwrap_open_failed"),
        )
        if cleanup_failures:
            failure = _preserve_failure_with_cleanup(
                failure,
                message=str(failure),
                primary_code="bwrap_open_failed",
                cleanup_failures=cleanup_failures,
            )
        raise failure from open_error
    if cleanup_failures:
        raise SandboxError(
            "cannot close exact bwrap directory descriptor",
            primary=_safe_failure(cleanup_failures[0]),
            cleanup_failures=cleanup_failures,
        )
    return descriptor


def _same_anchor(actual: _BwrapAnchor, expected: _BwrapAnchor) -> bool:
    return (
        actual.device == expected.device
        and actual.inode == expected.inode
        and actual.uid == expected.uid
        and actual.gid == expected.gid
        and actual.mode == expected.mode
        and actual.size == expected.size
        and hmac.compare_digest(actual.sha256, expected.sha256)
    )


def _verify_canonical_path(anchor: _BwrapAnchor) -> None:
    descriptor = _open_canonical_bwrap()
    primary: BaseException | None = None
    cleanup_failures: list[str] = []
    try:
        if not _same_anchor(_anchor_from_descriptor(descriptor), anchor):
            raise SandboxError(
                "exact /usr/bin/bwrap changed during sealed handoff",
                primary=_safe_failure("bwrap_path_identity_mismatch"),
            )
    except BaseException as exc:
        primary = exc
    finally:
        try:
            os.close(descriptor)
        except OSError:
            cleanup_failures.append("bwrap_verification_cleanup_failed")
    if primary is not None:
        raise _preserve_failure_with_cleanup(
            primary,
            message="exact /usr/bin/bwrap verification failed",
            primary_code="bwrap_path_verification_failed",
            cleanup_failures=cleanup_failures,
        ) from primary
    if cleanup_failures:
        raise SandboxError(
            "cannot close exact bwrap verification descriptor",
            primary=_safe_failure(cleanup_failures[0]),
            cleanup_failures=cleanup_failures,
        )


def identity() -> tuple[str, tuple[int, int, int, int]]:
    """Compatibility read-only identity query used by existing runtime evidence."""
    descriptor = _open_canonical_bwrap()
    primary: BaseException | None = None
    result: tuple[str, tuple[int, int, int, int]] | None = None
    cleanup_failures: list[str] = []
    try:
        anchor = _anchor_from_descriptor(descriptor)
        result = anchor.sha256, (anchor.device, anchor.inode, anchor.mode, anchor.size)
    except BaseException as exc:
        primary = exc
    try:
        os.close(descriptor)
    except OSError:
        cleanup_failures.append("bwrap_identity_cleanup_failed")
    if primary is not None:
        raise _preserve_failure_with_cleanup(
            primary,
            message="exact /usr/bin/bwrap identity verification failed",
            primary_code="bwrap_identity_verification_failed",
            cleanup_failures=cleanup_failures,
        ) from primary
    if cleanup_failures:
        raise SandboxError(
            "cannot close exact bwrap identity descriptor",
            primary=_safe_failure(cleanup_failures[0]),
            cleanup_failures=cleanup_failures,
        )
    if result is None:
        raise SandboxError("exact /usr/bin/bwrap identity verification failed")
    return result


def _args_data(arguments: Sequence[str]) -> bytes:
    try:
        data = resources.serialized_args(arguments)
    except resources.ResourceBudgetError as exc:
        raise SandboxError(str(exc), primary=exc) from exc
    if len(data) > resources.BWRAP_ARGS_MAX_BYTES:
        raise SandboxError("serialized bwrap --args exceeds named maximum")
    return data


def _final_handoff(
    bundle: SealedContentBundle, pass_fds: tuple[int, ...]
) -> tuple[int, ...]:
    try:
        resources.check_final_handoff(
            pass_fds,
            expected_fds=pass_fds,
            nofile_soft=bundle.invocation.nofile_soft,
        )
    except resources.ResourceBudgetError as exc:
        raise SandboxError(str(exc), primary=exc) from exc
    return pass_fds


def _open_bwrap_handoff(bundle: SealedContentBundle) -> _BwrapHandoff:
    descriptor = _open_canonical_bwrap()
    try:
        anchor = _anchor_from_descriptor(descriptor)
        if not hmac.compare_digest(anchor.sha256, bundle.bwrap_sha256):
            raise SandboxError("exact /usr/bin/bwrap does not match sealed anchor")
        _verify_canonical_path(anchor)
        return _BwrapHandoff(descriptor, anchor)
    except BaseException as exc:
        try:
            os.close(descriptor)
        except OSError:
            raise _preserve_failure_with_cleanup(
                exc,
                message="exact bwrap handoff verification failed",
                primary_code="bwrap_handoff_verification_failed",
                cleanup_failures=("bwrap_descriptor_cleanup_failed",),
            ) from exc
        raise


def _verify_bwrap(handoff: _BwrapHandoff) -> None:
    if handoff.descriptor < 0:
        raise SandboxError("exact bwrap handoff descriptor was closed early")
    if not _same_anchor(_anchor_from_descriptor(handoff.descriptor), handoff.anchor):
        raise SandboxError("exact /usr/bin/bwrap changed during sealed handoff")
    _verify_canonical_path(handoff.anchor)


def _close_handoff(handoff: _BwrapHandoff) -> str | None:
    if handoff.descriptor < 0:
        return None
    descriptor, handoff.descriptor = handoff.descriptor, -1
    try:
        os.close(descriptor)
    except OSError:
        return "bwrap_descriptor_cleanup_failed"
    return None


def _stderr_reason(stderr: bytes) -> str:
    if stderr.rstrip(b"\r\n").lower() == b"bwrap: setting up uid map: permission denied":
        return "bwrap_uid_map_setup_denied"
    return "bwrap_failed"


def _signal_child(
    child: subprocess.Popen[bytes], process_group: int | None, sig: signal.Signals
) -> str | None:
    try:
        if process_group is None:
            child.send_signal(sig)
        else:
            os.killpg(process_group, sig)
    except ProcessLookupError:
        return None
    except OSError:
        return (
            "bwrap_child_terminate_failed"
            if sig == signal.SIGTERM
            else "bwrap_child_kill_failed"
        )
    return None


def _stop_child(
    child: subprocess.Popen[bytes], process_group: int | None = None
) -> tuple[str, ...]:
    failures: list[str] = []
    failure = _signal_child(child, process_group, signal.SIGTERM)
    if failure:
        failures.append(failure)
    try:
        child.wait(timeout=_CHILD_STOP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    except OSError:
        failures.append("bwrap_child_reap_failed")
    failure = _signal_child(child, process_group, signal.SIGKILL)
    if failure:
        failures.append(failure)
    try:
        child.wait(timeout=_CHILD_STOP_GRACE_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        failures.append("bwrap_child_reap_failed")
    return tuple(failures)


def _capture_child(
    child: subprocess.Popen[bytes],
    timeout: int,
    process_group: int | None = None,
) -> tuple[subprocess.CompletedProcess[bytes], str | None, tuple[str, ...]]:
    streams = (
        (child.stdout, BWRAP_STDOUT_CAPTURE_LIMIT),
        (child.stderr, BWRAP_STDERR_CAPTURE_LIMIT),
    )
    selector = selectors.DefaultSelector()
    captured = [bytearray(), bytearray()]
    primary: str | None = None
    cleanup_failures: list[str] = []
    deadline = time.monotonic() + timeout
    stopped = False
    pipe_close_deadline: float | None = None
    completed: subprocess.CompletedProcess[bytes] | None = None
    capture_error: BaseException | None = None
    try:
        for index, (stream, _limit) in enumerate(streams):
            if stream is None:
                raise OSError("bubblewrap pipe is unavailable")
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, index)
        while selector.get_map():
            now = time.monotonic()
            if not stopped and now >= deadline:
                primary = "bwrap_timed_out"
                cleanup_failures.extend(_stop_child(child, process_group))
                stopped = True
                pipe_close_deadline = time.monotonic() + _PIPE_CLOSE_GRACE_SECONDS
            elif not stopped and child.poll() is not None:
                cleanup_failures.extend(_stop_child(child, process_group))
                stopped = True
                pipe_close_deadline = time.monotonic() + _PIPE_CLOSE_GRACE_SECONDS
            if pipe_close_deadline is not None and time.monotonic() >= pipe_close_deadline:
                if selector.get_map() and primary is None:
                    primary = "bwrap_pipe_drain_incomplete"
                break
            next_deadline = pipe_close_deadline if stopped else deadline
            wait = max(0.0, min(0.05, next_deadline - time.monotonic()))
            for key, _event in selector.select(wait):
                index = key.data
                stream, limit = streams[index]
                try:
                    data = os.read(stream.fileno(), _CAPTURE_CHUNK_BYTES)
                except BlockingIOError:
                    continue
                if not data:
                    selector.unregister(stream)
                    continue
                remaining = limit - len(captured[index])
                if remaining > 0:
                    captured[index].extend(data[:remaining])
                if len(data) > remaining and primary is None:
                    primary = "bwrap_output_limit_exceeded"
                    cleanup_failures.extend(_stop_child(child, process_group))
                    stopped = True
                    pipe_close_deadline = time.monotonic() + _PIPE_CLOSE_GRACE_SECONDS
        try:
            remaining = max(0.0, deadline - time.monotonic())
            returncode = child.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            primary = primary or "bwrap_timed_out"
            cleanup_failures.extend(_stop_child(child, process_group))
            returncode = child.returncode if child.returncode is not None else -1
        except OSError:
            cleanup_failures.extend(_stop_child(child, process_group))
            primary = primary or "bwrap_execution_failed"
            returncode = child.returncode if child.returncode is not None else -1
        completed = subprocess.CompletedProcess(
            child.args, returncode, bytes(captured[0]), bytes(captured[1])
        )
    except BaseException as exc:
        capture_error = exc
        primary = primary or "bwrap_capture_failed"
        cleanup_failures.extend(_stop_child(child, process_group))
    finally:
        try:
            selector.close()
        except OSError:
            cleanup_failures.append("bwrap_selector_cleanup_failed")
        for stream, _limit in streams:
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    cleanup_failures.append("bwrap_pipe_cleanup_failed")
    if completed is None:
        error = SandboxError(
            primary or "bwrap_capture_failed",
            primary=_safe_failure(primary or "bwrap_capture_failed"),
            cleanup_failures=cleanup_failures,
        )
        if capture_error is not None:
            raise error from capture_error
        raise error
    return completed, primary, tuple(cleanup_failures)


def _run_bwrap(
    command: tuple[str, ...],
    pass_fds: tuple[int, ...],
    timeout: int,
    handoff: _BwrapHandoff,
) -> subprocess.CompletedProcess[bytes]:
    child: subprocess.Popen[bytes] | None = None
    primary: str | None = None
    secondary: list[str] = []
    cleanup_failures: list[str] = []
    completed: subprocess.CompletedProcess[bytes] | None = None
    launch_error: BaseException | None = None
    primary_failure: BaseException | None = None
    nonzero_reason: str | None = None
    try:
        child = subprocess.Popen(
            command,
            executable=str(invocation.BWRAP),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={},
            pass_fds=pass_fds,
            start_new_session=True,
        )
        completed, primary, capture_cleanup = _capture_child(
            child, timeout, child.pid
        )
        cleanup_failures.extend(capture_cleanup)
        if completed.returncode and primary is None:
            primary = "bwrap_exited_nonzero"
            nonzero_reason = _stderr_reason(completed.stderr)
    except SandboxError as exc:
        primary = str(exc)
        primary_failure = exc.primary or _safe_failure(primary)
        secondary.extend(exc.secondary_failures)
        cleanup_failures.extend(exc.cleanup_failures)
        launch_error = exc
    except Exception as exc:
        primary = "bwrap_execution_failed"
        primary_failure = _safe_failure(primary)
        launch_error = exc
    finally:
        if child is not None:
            cleanup_failures.extend(_stop_child(child, child.pid))
            try:
                _verify_bwrap(handoff)
            except SandboxError as exc:
                verification_primary = exc.primary
                verification_secondary = list(exc.secondary_failures)
                verification_cleanup = list(exc.cleanup_failures)
                while isinstance(verification_primary, SandboxError):
                    verification_secondary[:0] = verification_primary.secondary_failures
                    verification_cleanup[:0] = verification_primary.cleanup_failures
                    verification_primary = verification_primary.primary
                secondary.append(
                    str(verification_primary)
                    if verification_primary is not None
                    else "bwrap_post_handoff_verification_failed"
                )
                secondary.extend(verification_secondary)
                cleanup_failures.extend(verification_cleanup)
            except BaseException:
                secondary.append("bwrap_post_handoff_verification_failed")
        close_failure = _close_handoff(handoff)
        if close_failure:
            cleanup_failures.append(close_failure)
    if primary is not None:
        if primary == "bwrap_exited_nonzero" and completed is not None and not secondary and not cleanup_failures:
            return completed
        if nonzero_reason is not None:
            secondary.insert(0, nonzero_reason)
        error = SandboxError(
            primary,
            primary=primary_failure or _safe_failure(primary),
            secondary_failures=secondary,
            cleanup_failures=cleanup_failures,
        )
        if launch_error is not None:
            raise error from launch_error
        raise error
    if secondary or cleanup_failures:
        primary = secondary[0] if secondary else cleanup_failures[0]
        raise SandboxError(
            primary,
            primary=_safe_failure(primary),
            secondary_failures=secondary[1:],
            cleanup_failures=cleanup_failures,
        )
    if completed is None:
        raise SandboxError("bwrap_execution_failed", primary=_safe_failure("bwrap_execution_failed"))
    return completed


def _roles(bundle: SealedContentBundle) -> dict[str, int]:
    return {
        invocation.file_role(entry.destination): entry.fd
        for entry in bundle.entries
        if entry.kind == "file" and entry.fd is not None
    }


def _phase_roles(
    bundle: SealedContentBundle,
    available: dict[str, int],
    phase: str,
    **phase_fds: int,
) -> dict[str, int]:
    values = {**available, **phase_fds}
    try:
        return {role: values[role] for role in bundle.invocation.required_roles(phase)}
    except KeyError as exc:
        raise resources.ResourceBudgetError(
            f"{phase} role map is missing a required descriptor"
        ) from exc


def _launch_bwrap(
    bundle: SealedContentBundle,
    available: dict[str, int],
    phase: str,
    timeout: int,
) -> subprocess.CompletedProcess[bytes]:
    handoff = _open_bwrap_handoff(bundle)
    try:
        roles = _phase_roles(
            bundle, {**available, "executable": handoff.descriptor}, phase
        )
        render = (
            bundle.invocation.render_probe
            if phase == "probe"
            else bundle.invocation.render_production
        )
        _arguments, command = render(roles)
        pass_fds = _final_handoff(bundle, tuple(roles.values()))
    except BaseException as exc:
        cleanup_failure = _close_handoff(handoff)
        if cleanup_failure:
            raise _preserve_failure_with_cleanup(
                exc,
                message="bwrap_launch_setup_failed",
                primary_code="bwrap_launch_setup_failed",
                cleanup_failures=(cleanup_failure,),
            ) from exc
        raise
    return _run_bwrap(command, pass_fds, timeout, handoff)


def _probe(bundle: SealedContentBundle) -> None:
    try:
        resources.check_actual_fds(
            additional_fds=2,
            pass_fd_count=2,
            nofile_soft=bundle.invocation.nofile_soft,
        )
        available = _roles(bundle)
        roles = _phase_roles(
            bundle,
            available,
            "probe",
            probe_args=bundle.invocation.nofile_soft - 1,
            executable=bundle.invocation.nofile_soft - 2,
        )
        probe_arguments, _symbolic_exec = bundle.invocation.render_probe(roles)
        probe_data = _args_data(probe_arguments)
    except resources.ResourceBudgetError as exc:
        raise SandboxError(str(exc), primary=exc) from exc
    args_fd = bundle.add_data("kanban-bwrap-probe-args", probe_data)
    completed = _launch_bwrap(bundle, {**available, "probe_args": args_fd}, "probe", 15)
    if completed.returncode:
        raise SandboxError(
            f"bubblewrap cannot create the required namespace profile ({_stderr_reason(completed.stderr)})"
        )


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
        resources.check_actual_fds(
            additional_fds=4,
            pass_fd_count=len(bundle.invocation.required_roles("production")),
            nofile_soft=bundle.invocation.nofile_soft,
        )
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
            executable=bundle.invocation.nofile_soft - 2,
        )
        arguments, _symbolic_exec = bundle.invocation.render_production(roles)
        production_data = _args_data(arguments)
        args_fd = bundle.add_data("kanban-bwrap-production-args", production_data)
        completed = _launch_bwrap(
            bundle, {**available, "production_args": args_fd}, "production", 30
        )
    except resources.ResourceBudgetError as exc:
        raise SandboxError(str(exc), primary=exc) from exc
    if completed.returncode:
        raise SandboxError(
            f"target import preflight failed inside bubblewrap ({_stderr_reason(completed.stderr)})"
        )
    bundle.verify()
    return completed.stdout
