"""Behavioral security contract for the sealed Bubblewrap launcher boundary."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from scripts import hermes_kanban_mcp_invocation as invocation
from scripts import hermes_kanban_mcp_os_sandbox as sandbox
from scripts import hermes_kanban_mcp_runtime_coherence as coherence


ROOT = Path(__file__).resolve().parents[2]
_BWRAP_DIGEST = hashlib.sha256(b"sealed bytes").hexdigest()


@dataclass(frozen=True)
class _Entry:
    destination: Path
    kind: str = "file"
    mode: int = 0o755
    target: str | None = None


def _spec() -> invocation.CanonicalInvocationSpec:
    return invocation.build(
        (_Entry(invocation.BWRAP),),
        (),
        bwrap_path=invocation.BWRAP,
        venv_dirname=".venv",
        acquisition_temporary_fds=0,
        current_open_fds=3,
        nofile_soft=128,
    )[0]


def _stat_result(
    *,
    device: int = 1,
    inode: int = 2,
    uid: int = 0,
    gid: int = 0,
    mode: int = stat.S_IFREG | 0o755,
    size: int = 3,
) -> SimpleNamespace:
    return SimpleNamespace(
        st_dev=device, st_ino=inode, st_uid=uid, st_gid=gid, st_mode=mode, st_size=size
    )


def _anchor() -> sandbox._BwrapAnchor:
    return sandbox._BwrapAnchor(_BWRAP_DIGEST, 1, 2, 0, 0, 0o755, 3)


def _bundle(spec: invocation.CanonicalInvocationSpec) -> SimpleNamespace:
    descriptors: list[int] = []

    def add_data(_name: str, _data: bytes) -> int:
        descriptor = os.open("/dev/null", os.O_RDONLY)
        descriptors.append(descriptor)
        return descriptor

    return SimpleNamespace(
        add_data=add_data,
        bwrap_sha256=_BWRAP_DIGEST,
        descriptors=descriptors,
        entries=(),
        invocation=spec,
    )


def _close(bundle: SimpleNamespace) -> None:
    for descriptor in bundle.descriptors:
        try:
            os.close(descriptor)
        except OSError:
            pass


def test_probe_and_production_keep_literal_normal_bwrap_and_executable_role() -> None:
    spec = _spec()
    for phase in ("probe", "production"):
        roles = {role: index + 10 for index, role in enumerate(spec.required_roles(phase))}
        render = spec.render_probe if phase == "probe" else spec.render_production
        command = render(roles)[1]
        assert command[:2] == ("/usr/bin/bwrap", "--args")
        assert "executable" in spec.required_roles(phase)
        assert "ld-linux" not in " ".join(command)
        assert "--inhibit-cache" not in command and "--preload" not in command


def test_open_uses_nofollow_and_descriptor_hash_and_path_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, int, int | None]] = []
    descriptors = iter((41, 42, 43, 44))

    def fake_open(path: object, flags: int, *, dir_fd: int | None = None) -> int:
        calls.append((path, flags, dir_fd))
        return next(descriptors)

    monkeypatch.setattr(sandbox.os, "open", fake_open)
    monkeypatch.setattr(sandbox.os, "close", lambda _fd: None)
    monkeypatch.setattr(sandbox.os, "fstat", lambda _fd: _stat_result())
    monkeypatch.setattr(sandbox, "_read_all", lambda _fd: b"sealed bytes")
    handoff = sandbox._open_bwrap_handoff(_bundle(_spec()))
    assert handoff.descriptor == 42
    assert handoff.anchor.sha256 != ""
    final_component_calls = [call for call in calls if call[0] == "bwrap"]
    assert len(final_component_calls) == 2
    assert all(call[1] & os.O_NOFOLLOW for call in final_component_calls)
    assert all(call[2] is not None for call in final_component_calls)


def test_missing_nofollow_fails_closed_before_any_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(sandbox.os, "O_NOFOLLOW")
    monkeypatch.setattr(
        sandbox.os, "open", lambda *_args, **_kwargs: pytest.fail("open must not run")
    )
    with pytest.raises(sandbox.SandboxError, match="requires O_NOFOLLOW"):
        sandbox._open_canonical_bwrap()


@pytest.mark.parametrize(
    "mutated",
    (
        {"uid": 1},
        {"gid": 1},
        {"mode": stat.S_IFREG | 0o775},
        {"mode": stat.S_IFLNK | 0o755},
    ),
)
def test_descriptor_verifier_rejects_owner_and_mode_mutations(
    monkeypatch: pytest.MonkeyPatch, mutated: dict[str, int]
) -> None:
    monkeypatch.setattr(sandbox.os, "fstat", lambda _fd: _stat_result(**mutated))
    monkeypatch.setattr(sandbox, "_read_all", lambda _fd: b"sealed bytes")
    with pytest.raises(sandbox.SandboxError, match="regular executable"):
        sandbox._anchor_from_descriptor(17)


def test_digest_mutation_is_rejected_with_stable_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    hashes = iter((b"sealed bytes", b"changed bytes"))
    descriptors = iter((51, 52, 53, 54))
    monkeypatch.setattr(sandbox.os, "open", lambda *_args, **_kwargs: next(descriptors))
    monkeypatch.setattr(sandbox.os, "close", lambda _fd: None)
    monkeypatch.setattr(sandbox.os, "fstat", lambda _fd: _stat_result())
    monkeypatch.setattr(sandbox, "_read_all", lambda _fd: next(hashes))
    with pytest.raises(sandbox.SandboxError, match="changed during sealed handoff"):
        sandbox._open_bwrap_handoff(_bundle(_spec()))


def test_identity_mutation_is_rejected_with_stable_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    descriptors = iter((61, 62, 63, 64))
    stats = iter((_stat_result(), _stat_result(inode=9)))
    monkeypatch.setattr(sandbox.os, "open", lambda *_args, **_kwargs: next(descriptors))
    monkeypatch.setattr(sandbox.os, "close", lambda _fd: None)
    monkeypatch.setattr(sandbox.os, "fstat", lambda _fd: next(stats))
    monkeypatch.setattr(sandbox, "_read_all", lambda _fd: b"sealed bytes")
    with pytest.raises(sandbox.SandboxError, match="changed during sealed handoff"):
        sandbox._open_bwrap_handoff(_bundle(_spec()))


@pytest.mark.parametrize("mutation", ({"device": 9}, {"inode": 9}, {"size": 9}))
def test_each_path_identity_component_is_checked_independently(
    monkeypatch: pytest.MonkeyPatch, mutation: dict[str, int]
) -> None:
    descriptors = iter((71, 72, 73, 74))
    stats = iter((_stat_result(), _stat_result(**mutation)))
    monkeypatch.setattr(sandbox.os, "open", lambda *_args, **_kwargs: next(descriptors))
    monkeypatch.setattr(sandbox.os, "close", lambda _fd: None)
    monkeypatch.setattr(sandbox.os, "fstat", lambda _fd: next(stats))
    monkeypatch.setattr(sandbox, "_read_all", lambda _fd: b"sealed bytes")
    with pytest.raises(sandbox.SandboxError, match="changed during sealed handoff"):
        sandbox._open_bwrap_handoff(_bundle(_spec()))


def test_launch_passes_verified_descriptor_and_uses_literal_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _bundle(_spec())
    descriptor = os.open("/dev/null", os.O_RDONLY)
    handoff = sandbox._BwrapHandoff(descriptor, _anchor())
    observed: dict[str, object] = {}

    monkeypatch.setattr(sandbox, "_open_bwrap_handoff", lambda _bundle: handoff)
    monkeypatch.setattr(sandbox, "_final_handoff", lambda _bundle, fds: fds)

    def fake_run(command: tuple[str, ...], pass_fds: tuple[int, ...], *_args: object) -> subprocess.CompletedProcess[bytes]:
        observed["command"] = command
        observed["pass_fds"] = pass_fds
        assert handoff.descriptor == descriptor
        os.close(descriptor)
        handoff.descriptor = -1
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(sandbox, "_run_bwrap", fake_run)
    try:
        args_fd = bundle.add_data("args", b"")
        sandbox._launch_bwrap(bundle, {"probe_args": args_fd}, "probe", 1)
    finally:
        _close(bundle)
    assert observed["command"][:2] == ("/usr/bin/bwrap", "--args")
    assert observed["pass_fds"][0] == descriptor
    assert observed["pass_fds"][1] == args_fd


def test_probe_direct_argv_budget_is_checked_before_args_memfd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def add_data(_name: str, _data: bytes) -> int:
        nonlocal calls
        calls += 1
        return 41

    oversized = "x" * 80
    spec = invocation.CanonicalInvocationSpec(
        (),
        (),
        (
            invocation.FDHandoff("executable"),
            str(invocation.BWRAP),
            "--args",
            invocation.FDArg("probe_args"),
            oversized,
        ),
        (),
        (),
        5,
        1,
        1,
        100_000,
        1,
        100_000,
    )
    bundle = SimpleNamespace(
        add_data=add_data, descriptors=(), invocation=spec, entries=()
    )
    monkeypatch.setattr(
        sandbox.os,
        "sysconf",
        lambda _name: sandbox.resources.ARG_MAX_SAFETY_MARGIN + 40,
    )
    monkeypatch.setattr(
        sandbox,
        "_launch_bwrap",
        lambda *_args, **_kwargs: pytest.fail(
            "direct bwrap handoff reached after exec budget failure"
        ),
    )
    with pytest.raises(sandbox.SandboxError, match="SC_ARG_MAX"):
        sandbox._probe(bundle)
    assert calls == 0


def test_resource_plan_reserves_executable_handoff_before_acquisition() -> None:
    planner = sandbox.resources
    base = dict(
        content_fds=3,
        acquisition_temporary_fds=2,
        topology_bytes=1,
        probe_arguments=("probe",),
        production_arguments=("production",),
        exec_argv=("/usr/bin/bwrap",),
        exec_env={},
        current_open_fds=4,
        arg_max=256 * 1024,
    )
    old_peak = (
        4
        + 3
        + 2
        + planner.FD_FIXED_DATA_OBJECTS
        + planner.FD_SUBPROCESS_RESERVE
        + planner.FD_BWRAP_RESERVE
    )
    with pytest.raises(planner.ResourceBudgetError, match="RLIMIT_NOFILE"):
        planner.plan_resources(**base, nofile_soft=old_peak)
    planned = planner.plan_resources(**base, nofile_soft=old_peak + 1)
    assert planned.required_fds == old_peak + planner.FD_EXECUTABLE_HANDOFF


@pytest.mark.parametrize("stream", ("stdout", "stderr"))
def test_real_ephemeral_producer_overflow_is_bounded_for_each_pipe(stream: str) -> None:
    producer = (
        "import sys; "
        f"getattr(sys, '{stream}').buffer.write(b'x' * {sandbox.BWRAP_STDOUT_CAPTURE_LIMIT + 4096}); "
        "getattr(sys, 'stdout').flush(); getattr(sys, 'stderr').flush()"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", producer],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    completed, primary, cleanup = sandbox._capture_child(child, 5, child.pid)
    assert primary == "bwrap_output_limit_exceeded"
    assert len(completed.stdout) <= sandbox.BWRAP_STDOUT_CAPTURE_LIMIT
    assert len(completed.stderr) <= sandbox.BWRAP_STDERR_CAPTURE_LIMIT
    assert child.poll() is not None
    assert not cleanup


def _started_popen(script: str):
    real_popen = subprocess.Popen

    def spawn(command: tuple[str, ...], **kwargs: object) -> subprocess.Popen[bytes]:
        assert command[:2] == ("/usr/bin/bwrap", "--args")
        assert kwargs["executable"] == "/usr/bin/bwrap"
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["env"] == {}
        assert kwargs["start_new_session"] is True
        return real_popen(
            [sys.executable, "-c", script],
            stdin=kwargs["stdin"],
            stdout=kwargs["stdout"],
            stderr=kwargs["stderr"],
            pass_fds=kwargs["pass_fds"],
            start_new_session=kwargs["start_new_session"],
        )

    return spawn


def _handoff() -> sandbox._BwrapHandoff:
    return sandbox._BwrapHandoff(os.open("/dev/null", os.O_RDONLY), _anchor())


def _composite_rollout_failure() -> coherence.RolloutError:
    return coherence.RolloutError(
        "TOKEN=must-not-cross-composite-boundary",
        primary_failure=RuntimeError("inner_primary"),
        secondary_failures=("inner_secondary",),
        cleanup_failures=("inner_cleanup",),
    )


def test_normal_exit_runs_post_handoff_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    handoff = _handoff()
    checks: list[int] = []
    monkeypatch.setattr(sandbox.subprocess, "Popen", _started_popen("pass"))
    monkeypatch.setattr(sandbox, "_verify_bwrap", lambda _handoff: checks.append(1))
    completed = sandbox._run_bwrap(("/usr/bin/bwrap", "--args", "1"), (handoff.descriptor,), 1, handoff)
    assert completed.returncode == 0 and checks == [1] and handoff.descriptor == -1


def test_launcher_does_not_inherit_ambient_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    handoff = _handoff()
    saved_stdin = os.dup(0)
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"ambient")
    try:
        os.dup2(read_fd, 0)
        monkeypatch.setattr(
            sandbox.subprocess,
            "Popen",
            _started_popen(
                "import sys; raise SystemExit(1 if sys.stdin.buffer.read(1) else 0)"
            ),
        )
        monkeypatch.setattr(sandbox, "_verify_bwrap", lambda _handoff: None)
        completed = sandbox._run_bwrap(
            ("/usr/bin/bwrap", "--args", "1"),
            (handoff.descriptor,),
            1,
            handoff,
        )
    finally:
        os.dup2(saved_stdin, 0)
        os.close(saved_stdin)
        os.close(read_fd)
        os.close(write_fd)
    assert completed.returncode == 0


def test_timeout_runs_post_handoff_verification_and_keeps_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handoff = _handoff()
    checks: list[int] = []
    monkeypatch.setattr(sandbox.subprocess, "Popen", _started_popen("import time; time.sleep(5)"))
    monkeypatch.setattr(sandbox, "_verify_bwrap", lambda _handoff: checks.append(1))
    with pytest.raises(sandbox.SandboxError, match="bwrap_timed_out") as raised:
        sandbox._run_bwrap(("/usr/bin/bwrap", "--args", "1"), (handoff.descriptor,), 0, handoff)
    assert str(raised.value.primary) == "bwrap_timed_out"
    assert checks == [1] and handoff.descriptor == -1


def test_post_start_oserror_runs_post_check_without_started_claim_before_popen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handoff = _handoff()
    checks: list[int] = []
    monkeypatch.setattr(sandbox.subprocess, "Popen", _started_popen("import time; time.sleep(5)"))
    monkeypatch.setattr(sandbox, "_capture_child", lambda *_args: (_ for _ in ()).throw(OSError("hidden")))
    monkeypatch.setattr(sandbox, "_verify_bwrap", lambda _handoff: checks.append(1))
    with pytest.raises(sandbox.SandboxError, match="bwrap_execution_failed"):
        sandbox._run_bwrap(("/usr/bin/bwrap", "--args", "1"), (handoff.descriptor,), 1, handoff)
    assert checks == [1] and handoff.descriptor == -1


def test_capture_exception_preserves_pipe_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    assert child.stdout is not None
    real_stdout = child.stdout

    class FailingClose:
        def fileno(self) -> int:
            return real_stdout.fileno()

        def close(self) -> None:
            real_stdout.close()
            raise OSError("injected close failure")

    child.stdout = FailingClose()  # type: ignore[assignment]
    monkeypatch.setattr(
        sandbox.os,
        "read",
        lambda *_args: (_ for _ in ()).throw(OSError("injected capture failure")),
    )
    with pytest.raises(sandbox.SandboxError, match="bwrap_timed_out") as raised:
        sandbox._capture_child(child, 1, child.pid)
    assert str(raised.value.primary) == "bwrap_timed_out"
    assert raised.value.cleanup_failures == ("bwrap_pipe_cleanup_failed",)
    assert child.poll() is not None


def test_primary_secondary_and_cleanup_evidence_survive_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handoff = _handoff()
    monkeypatch.setattr(sandbox.subprocess, "Popen", _started_popen("pass"))
    monkeypatch.setattr(
        sandbox,
        "_capture_child",
        lambda child, *_args: (
            subprocess.CompletedProcess(child.args, 7, b"", b""),
            None,
            (),
        ),
    )
    monkeypatch.setattr(sandbox, "_verify_bwrap", lambda _handoff: (_ for _ in ()).throw(OSError("hidden")))
    monkeypatch.setattr(sandbox, "_close_handoff", lambda _handoff: "bwrap_descriptor_cleanup_failed")
    try:
        with pytest.raises(sandbox.SandboxError, match="bwrap_exited_nonzero") as raised:
            sandbox._run_bwrap(("/usr/bin/bwrap", "--args", "1"), (handoff.descriptor,), 1, handoff)
    finally:
        os.close(handoff.descriptor)
        handoff.descriptor = -1
    assert str(raised.value.primary) == "bwrap_exited_nonzero"
    assert raised.value.secondary_failures == (
        "bwrap_failed",
        "bwrap_post_handoff_verification_failed",
    )
    assert raised.value.cleanup_failures == ("bwrap_descriptor_cleanup_failed",)


def test_uid_map_nonzero_survives_post_handoff_verifier_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handoff = _handoff()
    raw = "bwrap: setting up uid map: Permission denied"
    monkeypatch.setattr(
        sandbox.subprocess,
        "Popen",
        _started_popen(
            f"import sys; sys.stderr.write({raw!r}); raise SystemExit(7)"
        ),
    )
    monkeypatch.setattr(
        sandbox,
        "_verify_bwrap",
        lambda _handoff: (_ for _ in ()).throw(OSError("hidden")),
    )
    with pytest.raises(sandbox.SandboxError) as raised:
        sandbox._run_bwrap(
            ("/usr/bin/bwrap", "--args", "1"),
            (handoff.descriptor,),
            1,
            handoff,
        )
    evidence = (
        str(raised.value),
        str(raised.value.primary),
        *raised.value.secondary_failures,
        *raised.value.cleanup_failures,
    )
    assert str(raised.value.primary) == "bwrap_exited_nonzero"
    assert raised.value.secondary_failures == (
        "bwrap_uid_map_setup_denied",
        "bwrap_post_handoff_verification_failed",
    )
    assert "bwrap_uid_map_setup_denied" in evidence
    assert all("setting up uid map" not in item for item in evidence)


def test_uid_map_nonzero_survives_handoff_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handoff = _handoff()
    raw = "bwrap: setting up uid map: Permission denied"
    monkeypatch.setattr(
        sandbox.subprocess,
        "Popen",
        _started_popen(
            f"import sys; sys.stderr.write({raw!r}); raise SystemExit(7)"
        ),
    )
    monkeypatch.setattr(sandbox, "_verify_bwrap", lambda _handoff: None)
    monkeypatch.setattr(
        sandbox,
        "_close_handoff",
        lambda _handoff: "bwrap_descriptor_cleanup_failed",
    )
    try:
        with pytest.raises(sandbox.SandboxError) as raised:
            sandbox._run_bwrap(
                ("/usr/bin/bwrap", "--args", "1"),
                (handoff.descriptor,),
                1,
                handoff,
            )
    finally:
        os.close(handoff.descriptor)
        handoff.descriptor = -1
    evidence = (
        str(raised.value),
        str(raised.value.primary),
        *raised.value.secondary_failures,
        *raised.value.cleanup_failures,
    )
    assert str(raised.value.primary) == "bwrap_exited_nonzero"
    assert raised.value.secondary_failures == ("bwrap_uid_map_setup_denied",)
    assert raised.value.cleanup_failures == ("bwrap_descriptor_cleanup_failed",)
    assert "bwrap_uid_map_setup_denied" in evidence
    assert all("setting up uid map" not in item for item in evidence)


def test_post_handoff_composite_survives_run_and_rollout_without_child_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handoff = sandbox._BwrapHandoff(-1, _anchor())
    child = SimpleNamespace(args=("/usr/bin/bwrap",), pid=424242)
    raw_output = b"/private/path TOKEN=must-not-cross-boundary"
    monkeypatch.setattr(sandbox.subprocess, "Popen", lambda *_args, **_kwargs: child)
    monkeypatch.setattr(
        sandbox,
        "_capture_child",
        lambda *_args: (
            subprocess.CompletedProcess(child.args, 9, b"", raw_output),
            None,
            (),
        ),
    )
    monkeypatch.setattr(sandbox, "_stop_child", lambda *_args: ())
    monkeypatch.setattr(
        sandbox,
        "_verify_bwrap",
        lambda _handoff: (_ for _ in ()).throw(
            sandbox.SandboxError(
                raw_output.decode(),
                primary=RuntimeError("post_handoff_primary"),
                secondary_failures=("post_handoff_secondary",),
                cleanup_failures=("post_handoff_cleanup",),
            )
        ),
    )
    monkeypatch.setattr(
        sandbox,
        "_close_handoff",
        lambda _handoff: "bwrap_descriptor_cleanup_failed",
    )
    with pytest.raises(sandbox.SandboxError) as sandbox_raised:
        sandbox._run_bwrap(("/usr/bin/bwrap", "--args", "7"), (), 1, handoff)
    error = coherence._rollout_sandbox_error(sandbox_raised.value)
    assert isinstance(error, coherence.RolloutError)
    assert str(error.primary_failure) == "bwrap_exited_nonzero"
    assert error.secondary_failures == (
        "bwrap_failed",
        "post_handoff_primary",
        "post_handoff_secondary",
    )
    assert error.cleanup_failures == (
        "post_handoff_cleanup",
        "bwrap_descriptor_cleanup_failed",
    )
    evidence = (
        str(error),
        str(error.primary_failure),
        *error.secondary_failures,
        *error.cleanup_failures,
    )
    assert all(raw_output.decode() not in item for item in evidence)


def test_parent_content_close_flattens_composite_with_outer_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Content:
        def file_entry(self, _path: Path) -> SimpleNamespace:
            return SimpleNamespace(mode=0o644)

        def read_file(self, _path: Path) -> bytes:
            return b"home = /trusted\n"

        def close(self, *, primary: BaseException, **_kwargs: object) -> None:
            raise sandbox.SandboxError(
                "content close failed",
                primary=primary,
                cleanup_failures=("outer_content_cleanup",),
            )

    monkeypatch.setattr(coherence, "_interpreter_chain", lambda _path: (Path("/trusted/python"), ()))
    monkeypatch.setattr(coherence, "_trusted_python", lambda: (Path("/trusted/python"), ()))
    monkeypatch.setattr(
        coherence,
        "validate_venv_startup",
        lambda *_args: (Path("/candidate/.venv/pyvenv.cfg"), "0" * 64, Path("/site")),
    )
    monkeypatch.setattr(coherence.sealed_bundle, "capture_bundle", lambda *_args, **_kwargs: Content())
    monkeypatch.setattr(coherence, "_pyvenv_home_bytes", lambda _raw: (_ for _ in ()).throw(_composite_rollout_failure()))
    with pytest.raises(coherence.RolloutError) as raised:
        coherence._parent_trust_bundle(Path("/candidate"), ".venv")
    assert str(raised.value.primary_failure) == "inner_primary"
    assert raised.value.secondary_failures == ("inner_secondary",)
    assert raised.value.cleanup_failures == ("inner_cleanup", "outer_content_cleanup")


def test_parent_bundle_close_flattens_composite_with_outer_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = SimpleNamespace(
        close=lambda **kwargs: (_ for _ in ()).throw(
            sandbox.SandboxError(
                "bundle close failed",
                primary=kwargs["primary"],
                cleanup_failures=("outer_bundle_cleanup",),
            )
        )
    )
    bundle = coherence._ParentTrustBundle(SimpleNamespace(), content, b"", 0)
    monkeypatch.setattr(coherence, "_parent_trust_bundle", lambda *_args: bundle)
    monkeypatch.setattr(coherence, "_run_import_preflight", lambda *_args, **_kwargs: SimpleNamespace())
    with pytest.raises(coherence.RolloutError) as raised:
        with coherence.import_preflight_session(Path("/candidate"), ".venv"):
            raise _composite_rollout_failure()
    assert str(raised.value.primary_failure) == "inner_primary"
    assert raised.value.secondary_failures == ("inner_secondary",)
    assert raised.value.cleanup_failures == ("inner_cleanup", "outer_bundle_cleanup")


def test_identity_mismatch_keeps_descriptor_close_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sandbox, "_open_canonical_bwrap", lambda: 41)
    monkeypatch.setattr(
        sandbox,
        "_anchor_from_descriptor",
        lambda _descriptor: sandbox._BwrapAnchor(
            _BWRAP_DIGEST, 1, 99, 0, 0, 0o755, 3
        ),
    )
    monkeypatch.setattr(
        sandbox.os,
        "close",
        lambda _descriptor: (_ for _ in ()).throw(OSError("injected close failure")),
    )
    with pytest.raises(sandbox.SandboxError, match="changed during sealed handoff") as raised:
        sandbox._verify_canonical_path(_anchor())
    assert str(raised.value.primary) == "bwrap_path_identity_mismatch"
    assert raised.value.cleanup_failures == ("bwrap_verification_cleanup_failed",)


def test_open_handoff_preserves_verification_and_close_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verification = sandbox.SandboxError(
        "primary verification failure",
        secondary_failures=("secondary_verification_failure",),
        cleanup_failures=("prior_verification_cleanup_failure",),
    )
    monkeypatch.setattr(sandbox, "_open_canonical_bwrap", lambda: 41)
    monkeypatch.setattr(sandbox, "_anchor_from_descriptor", lambda _fd: _anchor())
    monkeypatch.setattr(
        sandbox,
        "_verify_canonical_path",
        lambda _anchor: (_ for _ in ()).throw(verification),
    )
    monkeypatch.setattr(
        sandbox.os,
        "close",
        lambda _fd: (_ for _ in ()).throw(OSError("injected close failure")),
    )
    with pytest.raises(sandbox.SandboxError) as raised:
        sandbox._open_bwrap_handoff(_bundle(_spec()))
    assert raised.value.primary is verification
    assert raised.value.secondary_failures == ("secondary_verification_failure",)
    assert raised.value.cleanup_failures == (
        "prior_verification_cleanup_failure",
        "bwrap_descriptor_cleanup_failed",
    )


def test_identity_preserves_verification_and_close_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity_failure = sandbox.SandboxError(
        "primary identity failure",
        secondary_failures=("secondary_identity_failure",),
        cleanup_failures=("prior_identity_cleanup_failure",),
    )
    monkeypatch.setattr(sandbox, "_open_canonical_bwrap", lambda: 41)
    monkeypatch.setattr(
        sandbox,
        "_anchor_from_descriptor",
        lambda _fd: (_ for _ in ()).throw(identity_failure),
    )
    monkeypatch.setattr(
        sandbox.os,
        "close",
        lambda _fd: (_ for _ in ()).throw(OSError("injected close failure")),
    )
    with pytest.raises(sandbox.SandboxError) as raised:
        sandbox.identity()
    assert raised.value.primary is identity_failure
    assert raised.value.secondary_failures == ("secondary_identity_failure",)
    assert raised.value.cleanup_failures == (
        "prior_identity_cleanup_failure",
        "bwrap_identity_cleanup_failed",
    )


def test_pre_start_oserror_does_not_run_post_handoff_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    handoff = _handoff()
    checks: list[int] = []
    monkeypatch.setattr(sandbox.subprocess, "Popen", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("hidden")))
    monkeypatch.setattr(sandbox, "_verify_bwrap", lambda _handoff: checks.append(1))
    with pytest.raises(sandbox.SandboxError, match="bwrap_execution_failed"):
        sandbox._run_bwrap(("/usr/bin/bwrap", "--args", "1"), (handoff.descriptor,), 1, handoff)
    assert checks == [] and handoff.descriptor == -1


@pytest.mark.parametrize(
    ("stderr", "reason"),
    (
        (b"bwrap: setting up uid map: Permission denied\n", "bwrap_uid_map_setup_denied"),
        (b"/private/path TOKEN=do-not-copy\n", "bwrap_failed"),
    ),
)
def test_classifier_is_allow_listed_and_never_reflects_raw_stderr(stderr: bytes, reason: str) -> None:
    assert sandbox._stderr_reason(stderr) == reason
    assert "TOKEN" not in sandbox._stderr_reason(stderr)


def test_descendant_holding_pipes_is_bounded_and_cleaned_up() -> None:
    helper = r'''
import json, os, subprocess, sys, time
from scripts import hermes_kanban_mcp_os_sandbox as sandbox
grandchild = "import time; time.sleep(30)"
child_script = (
    "import subprocess,sys; "
    "child=subprocess.Popen([sys.executable,'-c',%r]); "
    "print(child.pid, flush=True)"
) % grandchild
child = subprocess.Popen(
    [sys.executable, "-c", child_script],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    start_new_session=True,
)
started = time.monotonic()
completed, primary, cleanup = sandbox._capture_child(child, 5, child.pid)
print(json.dumps({
    "elapsed": time.monotonic() - started,
    "grandchild": int(completed.stdout.strip()),
    "primary": primary,
    "cleanup": cleanup,
    "returncode": completed.returncode,
}))
'''
    process = subprocess.Popen(
        [sys.executable, "-c", helper],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=3)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, 9)
        process.wait(timeout=1)
        pytest.fail("capture waited for descendant-held pipe beyond the upper bound")
    assert process.returncode == 0, stderr
    evidence = json.loads(stdout)
    assert evidence["elapsed"] < 2.5
    assert evidence["returncode"] == 0
    assert evidence["primary"] is None
    assert evidence["cleanup"] == []
    state_path = Path(f"/proc/{evidence['grandchild']}/stat")

    def running() -> bool:
        try:
            return state_path.read_text().split()[2] != "Z"
        except FileNotFoundError:
            return False

    for _ in range(50):
        if not running():
            break
        time.sleep(0.02)
    assert not running()
