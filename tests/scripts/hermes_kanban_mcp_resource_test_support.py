from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Callable

import pytest


def assert_external_symlink_case(
    inventory: object,
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    case: str,
    succeeds: bool,
    expected_digest: str,
) -> None:
    trusted, escaped = root / "trusted", root / "escaped"
    (trusted / "real").mkdir(parents=True)
    escaped.mkdir()
    (trusted / "real" / "loader.so").write_bytes(b"literal loader bytes")
    (escaped / "loader.so").write_bytes(b"literal loader bytes")
    targets = {
        "absolute-escape": str(escaped),
        "relative-escape": "../../escaped",
        "dangling": "missing",
    }
    if case in targets:
        (trusted / "first").symlink_to(targets[case])
    else:
        (trusted / "first").symlink_to("second")
        (trusted / "second").symlink_to(
            "real" if case == "relative-multihop" else "first"
        )
    monkeypatch.setattr(inventory, "_ROOTS", (trusted,))
    builder = inventory.InventoryBuilder()
    if succeeds:
        assert (
            builder.external(trusted / "first" / "loader.so").digest
            == expected_digest
        )
    else:
        with pytest.raises(inventory.InventoryError):
            builder.external(trusted / "first" / "loader.so")


def assert_deep_acquisition_plan_rejected(repo: Path, root: Path) -> None:
    script = f"""
import resource, sys
from pathlib import Path
sys.path.insert(0, {str(repo)!r})
from scripts import hermes_kanban_mcp_inventory as inventory
from scripts import hermes_kanban_mcp_resources as resources
root = Path(sys.argv[1]); root.mkdir(); current = root
for index in range(inventory.MAX_DIRECTORY_DEPTH):
    current /= str(index); current.mkdir()
(current / "payload").write_bytes(b"x")
builder = inventory.InventoryBuilder()
builder.tree(root, Path("/candidate"))
plan = builder.finish()
baseline = resources.open_fd_count()
soft = baseline + plan.regular_count + 50
resource.setrlimit(resource.RLIMIT_NOFILE, (soft, soft))
try:
    resources.plan_resources(
        content_fds=plan.regular_count,
        acquisition_temporary_fds=plan.acquisition_temporary_fds,
        topology_bytes=1,
        probe_arguments=("probe",),
        production_arguments=("production",),
        exec_argv=("exec",),
        exec_env={{}},
    )
except resources.ResourceBudgetError:
    assert resources.open_fd_count() == baseline
    raise SystemExit(0)
raise SystemExit("resource plan accepted an impossible acquisition peak")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(root)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def assert_deep_mutation_rejected_before_content_memfd(
    sealed: object,
    inventory: object,
    coherence: object,
    monkeypatch: pytest.MonkeyPatch,
    runtime: Path,
    fd_oracle: Callable[[], tuple[int, ...]],
) -> None:
    trusted_interpreter, roots = coherence._trusted_python()
    baseline = fd_oracle()
    real_build = sealed._BundleBuilder.build
    real_memfd = sealed.os.memfd_create
    memfd_calls = 0

    def mutate_before_acquisition(builder: object) -> object:
        current = runtime / "zz-depth"
        current.mkdir()
        for index in range(inventory.MAX_DIRECTORY_DEPTH + 1):
            current /= str(index)
            current.mkdir()
        (current / "payload").write_bytes(b"late topology")
        return real_build(builder)

    def observed_memfd(*args: object, **kwargs: object) -> int:
        nonlocal memfd_calls
        memfd_calls += 1
        return real_memfd(*args, **kwargs)

    monkeypatch.setattr(sealed._BundleBuilder, "build", mutate_before_acquisition)
    monkeypatch.setattr(sealed.os, "memfd_create", observed_memfd)
    with pytest.raises(
        sealed.SandboxError, match="directory depth exceeds named maximum"
    ):
        sealed.capture_bundle(runtime, ".venv", trusted_interpreter, roots)
    assert memfd_calls == 0
    assert fd_oracle() == baseline


def assert_post_preflight_depth_cap(
    sealed: object,
    inventory: object,
    coherence: object,
    monkeypatch: pytest.MonkeyPatch,
    runtime: Path,
    fd_oracle: Callable[[], tuple[int, ...]],
) -> None:
    trusted_interpreter, roots = coherence._trusted_python()
    baseline = fd_oracle()
    real_verify = sealed._verify_preflight_inventory

    def mutate_after_preflight(*args: object) -> None:
        real_verify(*args)
        current = runtime / "zz-after-preflight"
        current.mkdir()
        for index in range(inventory.MAX_DIRECTORY_DEPTH + 1):
            current /= str(index)
            current.mkdir()

    monkeypatch.setattr(
        sealed, "_verify_preflight_inventory", mutate_after_preflight
    )
    with pytest.raises(
        sealed.SandboxError,
        match="acquisition directory depth exceeds named maximum",
    ):
        sealed.capture_bundle(runtime, ".venv", trusted_interpreter, roots)
    assert fd_oracle() == baseline


def assert_late_probe_pressure_rejected(
    sandbox: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = [sandbox.os.open("/dev/null", sandbox.os.O_RDONLY) for _ in range(2)]
    pressure: list[int] = []
    before = sandbox.resources.open_fd_count()
    soft = (
        before
        + 2
        + sandbox.resources.FD_SUBPROCESS_RESERVE
        + sandbox.resources.FD_BWRAP_RESERVE
    )
    spec = sandbox.invocation.CanonicalInvocationSpec(
        (),
        (),
        (
            sandbox.invocation.FDHandoff("executable"),
            str(sandbox.invocation.BWRAP),
            "--args",
            sandbox.invocation.FDArg("probe_args"),
        ),
        (),
        (),
        len(str(soft - 1)),
        1,
        1,
        1_000,
        1,
        soft,
    )

    def late_add_data(_name: str, _data: bytes) -> int:
        descriptor = sandbox.os.memfd_create("late-pressure")
        owned.append(descriptor)
        pressure.extend(
            sandbox.os.open("/dev/null", sandbox.os.O_RDONLY) for _ in range(2)
        )
        return descriptor

    class Bundle:
        invocation = spec
        entries = ()

        def add_data(self, name: str, data: bytes) -> int:
            return late_add_data(name, data)

        @property
        def descriptors(self) -> tuple[int, ...]:
            return tuple(owned)

    monkeypatch.setattr(
        sandbox,
        "_open_bwrap_handoff",
        lambda _bundle: sandbox._BwrapHandoff(
            sandbox.os.open("/dev/null", sandbox.os.O_RDONLY),
            sandbox._BwrapAnchor("", 0, 0, 0, 0, 0, 0),
        ),
    )
    monkeypatch.setattr(
        sandbox,
        "_run_bwrap",
        lambda *_args, **_kwargs: pytest.fail(
            "direct bwrap subprocess reached after late FD pressure"
        ),
    )
    try:
        with pytest.raises(sandbox.SandboxError, match="RLIMIT_NOFILE"):
            sandbox._probe(Bundle())
    finally:
        for descriptor in (*pressure, *owned):
            sandbox.os.close(descriptor)
