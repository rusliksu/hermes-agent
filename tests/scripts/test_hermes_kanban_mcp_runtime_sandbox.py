from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import resource
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from tests.scripts.hermes_kanban_mcp_resource_test_support import (
    assert_deep_acquisition_plan_rejected,
    assert_deep_mutation_rejected_before_content_memfd,
    assert_post_preflight_depth_cap,
)

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts" / "hermes_kanban_mcp_rollout.py"
SPEC = importlib.util.spec_from_file_location("runtime_sandbox_rollout", HELPER)
assert SPEC is not None and SPEC.loader is not None
rollout = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rollout
SPEC.loader.exec_module(rollout)
coherence = rollout.coherence
os_sandbox = coherence.os_sandbox
sealed = os_sandbox.sealed
invocation = os_sandbox.invocation
elf = sys.modules[sealed.parse_elf.__module__]
inventory = sys.modules[sealed.InventoryBuilder.__module__]


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _oracle(root: Path) -> tuple[tuple[object, ...], ...]:
    result = []
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in sorted(dirnames + filenames):
            path = base / name
            info = path.lstat()
            relative = str(path.relative_to(root))
            if path.is_symlink():
                result.append((relative, "link", os.readlink(path)))
            elif path.is_file():
                result.append((relative, "file", _hash(path.read_bytes())))
            else:
                result.append((relative, "dir", stat.S_IMODE(info.st_mode)))
    return tuple(result)


def _fd_oracle() -> dict[int, str]:
    result = {}
    for raw in os.listdir("/proc/self/fd"):
        try:
            result[int(raw)] = os.readlink(f"/proc/self/fd/{raw}")
        except FileNotFoundError:
            continue
    return result


def _literal_elf64_dynamic(
    *,
    needed: tuple[str, ...] = ("libchild.so",),
    rpath: str | None = None,
    runpath: str | None = None,
    terminate: bool = True,
) -> bytes:
    """Independent handcrafted ELF oracle; production parser is not used."""
    strings = bytearray(b"\0")

    def add(value: str) -> int:
        offset = len(strings)
        strings.extend(value.encode("ascii") + b"\0")
        return offset

    dynamic = [(1, add(name)) for name in needed]
    if rpath is not None:
        dynamic.append((15, add(rpath)))
    if runpath is not None:
        dynamic.append((29, add(runpath)))
    string_offset = 0x300
    string_vaddr = 0x400300
    dynamic.extend(((5, string_vaddr), (10, len(strings))))
    if terminate:
        dynamic.append((0, 0))
    dynamic_bytes = b"".join(struct.pack("<qQ", *entry) for entry in dynamic)
    dynamic_offset = 0x200
    header = bytearray(64)
    header[:16] = b"\x7fELF\x02\x01\x01" + b"\0" * 9
    struct.pack_into("<HHIQQQIHHHHHH", header, 16, 3, 62, 1, 0, 64, 0, 0, 64, 56, 2, 0, 0, 0)
    load = struct.pack("<IIQQQQQQ", 1, 4, 0, 0x400000, 0, 0x400, 0x400, 0x1000)
    dyn = struct.pack(
        "<IIQQQQQQ", 2, 4, dynamic_offset, 0x400200, 0, len(dynamic_bytes), len(dynamic_bytes), 8
    )
    result = header + load + dyn
    result.extend(b"\0" * (dynamic_offset - len(result)))
    result.extend(dynamic_bytes)
    result.extend(b"\0" * (string_offset - len(result)))
    result.extend(strings)
    result.extend(b"\0" * (0x400 - len(result)))
    return bytes(result)


def test_elf_parser_keeps_rpath_runpath_distinct_and_requires_dt_null() -> None:
    parsed = sealed.parse_elf(
        _literal_elf64_dynamic(
            rpath="/legacy/$ORIGIN", runpath="/direct/$ORIGIN"
        )
    )
    assert parsed is not None
    assert parsed.rpath == ("/legacy/$ORIGIN",)
    assert parsed.runpath == ("/direct/$ORIGIN",)
    with pytest.raises(elf.ElfFormatError, match="DT_NULL"):
        sealed.parse_elf(_literal_elf64_dynamic(terminate=False))


def test_low_fd_budget_fails_before_first_content_memfd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(
        tmp_path, 'WRITE_TOOLS = ("kanban_sync_external_task",)\n'
    )
    trusted_interpreter, roots = coherence._trusted_python()
    memfd_calls = 0
    real_memfd = sealed.os.memfd_create

    def observed_memfd(*args: object, **kwargs: object) -> int:
        nonlocal memfd_calls
        memfd_calls += 1
        return real_memfd(*args, **kwargs)

    monkeypatch.setattr(sealed.os, "memfd_create", observed_memfd)
    monkeypatch.setattr(
        sealed.resources.resource, "getrlimit", lambda _kind: (64, 64)
    )
    with pytest.raises(sealed.SandboxError):
        sealed.capture_bundle(runtime, ".venv", trusted_interpreter, roots)
    assert memfd_calls == 0


def test_deep_acquisition_peak_fails_before_content_memfd_without_leak(
    tmp_path: Path,
) -> None:
    assert_deep_acquisition_plan_rejected(ROOT, tmp_path / "deep")


def test_deep_topology_mutation_fails_before_first_content_memfd_without_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(
        tmp_path, 'WRITE_TOOLS = ("kanban_sync_external_task",)\n'
    )
    assert (runtime / "agent/__init__.py").is_file()
    assert_deep_mutation_rejected_before_content_memfd(
        sealed, inventory, coherence, monkeypatch, runtime, _fd_oracle
    )


def test_acquisition_depth_cap_survives_post_preflight_toctou_without_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(
        tmp_path, 'WRITE_TOOLS = ("kanban_sync_external_task",)\n'
    )
    assert_post_preflight_depth_cap(
        sealed, inventory, coherence, monkeypatch, runtime, _fd_oracle
    )


def test_resource_planner_counts_open_fds_and_named_size_caps() -> None:
    planner = sealed.resources
    base = dict(
        content_fds=3,
        acquisition_temporary_fds=67,
        topology_bytes=100,
        probe_arguments=("--probe",),
        production_arguments=("--production",),
        exec_argv=("/proc/self/fd/1",),
        exec_env={},
        current_open_fds=20,
        nofile_soft=200,
        arg_max=256 * 1024,
    )
    planned = planner.plan_resources(**base)
    assert planned.required_fds == (
        20
        + 3
        + 67
        + planner.FD_FIXED_DATA_OBJECTS
        + planner.FD_SUBPROCESS_RESERVE
        + planner.FD_BWRAP_RESERVE
    )
    with pytest.raises(planner.ResourceBudgetError, match="RLIMIT_NOFILE"):
        planner.plan_resources(**{**base, "nofile_soft": planned.required_fds - 1})
    with pytest.raises(planner.ResourceBudgetError, match="topology"):
        planner.plan_resources(
            **{**base, "topology_bytes": planner.TOPOLOGY_MAX_BYTES + 1}
        )
    with pytest.raises(planner.ResourceBudgetError, match="SC_ARG_MAX"):
        planner.plan_resources(
            **{
                **base,
                "exec_argv": ("x" * (256 * 1024),),
                "arg_max": 256 * 1024,
            }
        )
    with pytest.raises(planner.ResourceBudgetError, match="--args"):
        planner.plan_resources(
            **{
                **base,
                "production_arguments": ("x" * 100,),
                "args_max": 64,
            }
        )


def test_second_pass_mutation_fails_closed_and_closes_partial_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(
        tmp_path, 'WRITE_TOOLS = ("kanban_sync_external_task",)\n'
    )
    target = runtime / "agent/transports/hermes_kanban_mcp_server.py"
    original = target.read_bytes()
    trusted_interpreter, roots = coherence._trusted_python()
    baseline = _fd_oracle()
    real_build = sealed._BundleBuilder.build

    def mutate_before_acquisition(builder: object) -> object:
        target.write_bytes(b"changed between inventory and acquisition\n")
        return real_build(builder)

    monkeypatch.setattr(sealed._BundleBuilder, "build", mutate_before_acquisition)
    try:
        with pytest.raises(
            sealed.SandboxError, match="between inventory and sealed acquisition"
        ):
            sealed.capture_bundle(
                runtime, ".venv", trusted_interpreter, roots
            )
    finally:
        target.write_bytes(original)
    assert _fd_oracle() == baseline


def _write_surface(root: Path, server_source: str) -> None:
    for package in ("hermes_cli", "agent", "agent/transports"):
        (root / package).mkdir(parents=True, exist_ok=True)
        (root / package / "__init__.py").write_text("", encoding="utf-8")
    (root / "hermes_cli" / "main.py").write_text(
        'raise AssertionError("find_spec must not import hermes_cli.main")\n',
        encoding="utf-8",
    )
    (root / "agent/transports/hermes_kanban_mcp_server.py").write_text(
        server_source, encoding="utf-8"
    )


def _make_venv(runtime: Path) -> None:
    venv = runtime / ".venv"
    interpreter = venv / "bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(Path(sys.executable).read_bytes())
    interpreter.chmod(stat.S_IMODE(Path(sys.executable).stat().st_mode))
    trusted = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    (venv / "pyvenv.cfg").write_text(
        f"home = {trusted.parent}\n"
        "include-system-site-packages = false\n"
        f"version = {version}\n",
        encoding="utf-8",
    )
    (venv / "lib" / f"python{version}" / "site-packages").mkdir(parents=True)


def _runtime(tmp_path: Path, server_source: str) -> Path:
    runtime = tmp_path / "candidate"
    _write_surface(runtime, server_source)
    _make_venv(runtime)
    return runtime


def _run_raw_sandbox(runtime: Path, harness: bytes) -> bytes:
    bundle = coherence._parent_trust_bundle(runtime, ".venv")
    try:
        return os_sandbox.run(
            bundle=bundle.content,
            venv_dirname=".venv",
            harness_bytes=harness,
            anchors_bytes=b"{}",
        )
    finally:
        bundle.close()


def _anchors(runtime: Path) -> object:
    bundle = coherence._parent_trust_bundle(runtime, ".venv")
    try:
        return bundle.anchors
    finally:
        bundle.close()


@pytest.fixture
def nested_bwrap(monkeypatch: pytest.MonkeyPatch) -> None:
    """The outer Codex seccomp blocks bwrap's loopback setup, not other namespaces."""
    original = os_sandbox.invocation._base_args

    def supported_profile() -> list[str]:
        return [argument for argument in original() if argument != "--unshare-net"]

    monkeypatch.setattr(os_sandbox.invocation, "_base_args", supported_profile)


def test_baseline_namespace_profile_and_clearenv_are_exact() -> None:
    args = invocation._base_args()
    for flag in (
        "--unshare-user", "--disable-userns", "--unshare-pid", "--unshare-ipc",
        "--unshare-uts", "--unshare-cgroup", "--unshare-net",
        "--new-session", "--die-with-parent", "--clearenv", "--proc", "--dev",
    ):
        assert flag in args
    observed = {
        args[index + 1]: args[index + 2]
        for index, value in enumerate(args)
        if value == "--setenv"
    }
    assert observed == invocation.SANDBOX_ENV
    assert "PATH" not in observed
    assert "--bind" not in args and "--dev-bind" not in args


def test_baseline_bwrap_capability_probe_fails_closed(tmp_path: Path) -> None:
    runtime = _runtime(
        tmp_path, 'WRITE_TOOLS = ("kanban_sync_external_task",)\n'
    )
    bundle = coherence._parent_trust_bundle(runtime, ".venv")
    try:
        with pytest.raises(
            os_sandbox.SandboxError,
            match="bubblewrap cannot create the required namespace profile",
        ):
            os_sandbox._probe(bundle.content)
    finally:
        bundle.close()


def test_full_sealed_content_invocation_has_no_mutable_directory_bind_bug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(
        tmp_path, 'WRITE_TOOLS = ("kanban_sync_external_task",)\n'
    )
    bundle = coherence._parent_trust_bundle(runtime, ".venv")
    observed_command: list[str] = []
    observed_args: list[str] = []
    bwrap_target: list[str] = []

    def rejected(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
        observed_command[:] = command
        args_fd = int(command[command.index("--args") + 1])
        observed_args[:] = [
            item.decode()
            for item in os_sandbox._read_all(args_fd).split(b"\0")
            if item
        ]
        bwrap_target.append(os.readlink(f"/proc/self/fd/{bundle.content.bwrap_fd}"))
        assert _hash(os_sandbox._read_all(bundle.content.bwrap_fd)) == (
            bundle.content.bwrap_sha256
        )
        return subprocess.CompletedProcess(command, 23, stdout=b"", stderr=b"ignored")

    monkeypatch.setattr(os_sandbox, "_probe", lambda _bundle: None)
    monkeypatch.setattr(os_sandbox.subprocess, "run", rejected)
    try:
        with pytest.raises(
            os_sandbox.SandboxError,
            match="target import preflight failed inside bubblewrap",
        ):
            os_sandbox.run(
                bundle=bundle.content,
                venv_dirname=".venv",
                harness_bytes=b"raise SystemExit(99)\n",
                anchors_bytes=b"{}",
            )
    finally:
        bundle.close()
    assert "--ro-bind" not in observed_args
    assert "--ro-bind-fd" not in observed_args
    assert observed_args.count("--ro-bind-data") >= sum(
        entry.kind == "file" for entry in bundle.content.entries
    )
    assert observed_command[-7:] == [
        str(invocation.SANDBOX_RUNTIME / ".venv/bin/python"),
        "-I", "-S", "-B", "/sandbox/preflight.py",
        str(invocation.SANDBOX_RUNTIME), ".venv",
    ]
    assert bwrap_target == ["/memfd:kanban-bwrap (deleted)"]


def test_full_profile_native_network_is_isolated_or_fails_before_exec(
    tmp_path: Path,
) -> None:
    runtime = _runtime(
        tmp_path, 'WRITE_TOOLS = ("kanban_sync_external_task",)\n'
    )
    try:
        listener = socket.socket(socket.AF_INET)
    except PermissionError:
        with pytest.raises(os_sandbox.SandboxError):
            _run_raw_sandbox(runtime, b"print('{}')\n")
        return
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(0.05)
    port = listener.getsockname()[1]
    harness = f"""
import ctypes, json, socket, struct
libc = ctypes.CDLL(None, use_errno=True)
descriptor = libc.socket(socket.AF_INET, socket.SOCK_STREAM, 0)
address = ctypes.create_string_buffer(
    struct.pack("H", socket.AF_INET)
    + struct.pack("!H", {port})
    + socket.inet_aton("127.0.0.1")
    + b"\\0" * 8
)
connected = libc.connect(descriptor, address, 16)
written = libc.write(descriptor, b"bad", 3) if connected == 0 else -1
print(json.dumps({{"connected": connected, "written": written}}))
""".encode()
    try:
        try:
            output = _run_raw_sandbox(runtime, harness)
        except os_sandbox.SandboxError as exc:
            assert str(exc) == "bubblewrap cannot create the required namespace profile"
        else:
            assert json.loads(output) == {"connected": -1, "written": -1}
        with pytest.raises(TimeoutError):
            listener.accept()
    finally:
        listener.close()


def test_real_bwrap_uses_pinned_mounts_synthetic_homes_and_no_pth(
    tmp_path: Path, nested_bwrap: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(
        tmp_path,
        """
import os
from pathlib import Path
assert Path.home() == Path("/sandbox/home")
assert os.environ == {
    "HOME": "/sandbox/home",
    "HERMES_HOME": "/sandbox/hermes-home",
    "TMPDIR": "/sandbox/tmp",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "TZ": "UTC",
    "PYTHONDONTWRITEBYTECODE": "1",
}
WRITE_TOOLS = ("kanban_enqueue", "kanban_sync_external_task")
""",
    )
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    marker = tmp_path / "pth-host-canary"
    site = runtime / ".venv/lib" / f"python{version}" / "site-packages"
    (site / "must-not-run.pth").write_text(
        f"import pathlib; pathlib.Path({str(marker)!r}).write_text('bad')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", "/forbidden")
    monkeypatch.setenv("PYTHONHOME", "/forbidden")
    before = _oracle(tmp_path)
    evidence = coherence.run_import_preflight(runtime, ".venv")
    assert evidence.kanban_server_origin == (
        runtime / "agent/transports/hermes_kanban_mcp_server.py"
    )
    assert evidence.write_tools[-1] == "kanban_sync_external_task"
    assert evidence.bwrap_sha256 == os_sandbox.identity()[0]
    assert not marker.exists()
    assert _oracle(tmp_path) == before


@pytest.mark.parametrize(
    "effect",
    [
        "file-write", "outside-read", "socket", "subprocess", "os-system",
        "database", "suppressed-denial", "unlink", "rename", "mkdir", "exec",
        "kill", "ctypes",
    ],
)
def test_python_second_layer_remains_sticky_diagnostic_evidence(
    tmp_path: Path, nested_bwrap: None, effect: str
) -> None:
    outside = tmp_path / "host"
    outside.mkdir()
    canary = outside / "canary"
    canary.write_text("unchanged", encoding="utf-8")
    target = outside / "target"
    statements = {
        "file-write": f"Path({str(target)!r}).write_text('bad')",
        "outside-read": f"Path({str(canary)!r}).read_text()",
        "socket": "import socket; socket.socket()",
        "subprocess": "import subprocess; subprocess.run(['/usr/bin/true'])",
        "os-system": "import os; os.system('/usr/bin/true')",
        "database": "import sqlite3; sqlite3.connect(':memory:')",
        "suppressed-denial": (
            f"try:\n    Path({str(target)!r}).write_text('bad')\nexcept Exception:\n    pass"
        ),
        "unlink": f"import os; os.unlink({str(canary)!r})",
        "rename": f"import os; os.rename({str(canary)!r}, {str(target)!r})",
        "mkdir": f"import os; os.mkdir({str(target)!r})",
        "exec": "import os; os.execv('/usr/bin/true', ['/usr/bin/true'])",
        "kill": "import os; os.kill(os.getpid(), 0)",
        "ctypes": "import ctypes; ctypes.CDLL(None)",
    }
    runtime = _runtime(
        tmp_path,
        "from pathlib import Path\n"
        + statements[effect]
        + '\nWRITE_TOOLS = ("kanban_sync_external_task",)\n',
    )
    before = _oracle(tmp_path)
    with pytest.raises(
        rollout.RolloutError, match="denied by the isolated policy"
    ):
        coherence.run_import_preflight(runtime, ".venv")
    assert canary.read_text(encoding="utf-8") == "unchanged"
    assert not target.exists()
    assert _oracle(tmp_path) == before


def test_native_bypasses_have_no_host_visible_effects(
    tmp_path: Path, nested_bwrap: None
) -> None:
    runtime = _runtime(
        tmp_path, 'WRITE_TOOLS = ("kanban_sync_external_task",)\n'
    )
    file_canary = tmp_path / "host-file-canary"
    fork_canary = tmp_path / "host-fork-canary"
    file_canary.write_text("unchanged", encoding="utf-8")
    parent_pid = os.getpid()
    limits_before = resource.getrlimit(resource.RLIMIT_NOFILE)
    signalled: list[int] = []
    previous = signal.signal(signal.SIGWINCH, lambda *_args: signalled.append(1))
    socket_canary, socket_peer = socket.socketpair()
    socket_peer.settimeout(0.05)
    host_socket_fd = socket_canary.fileno()
    harness = f"""
import ctypes, json, os, resource, signal, socket, struct, subprocess
libc = ctypes.CDLL(None, use_errno=True)
signal.signal(signal.SIGWINCH, signal.SIG_IGN)
result = {{
    "native_open": libc.open({str(file_canary)!r}.encode(), os.O_WRONLY | os.O_TRUNC),
    "native_socket_write": libc.write({host_socket_fd}, b"bad", 3),
    "native_kill": libc.kill({parent_pid}, signal.SIGWINCH),
    "prlimit_blocked": False,
    "fork_exec_called": False,
}}
try:
    resource.prlimit({parent_pid}, resource.RLIMIT_NOFILE)
except (OSError, ProcessLookupError):
    result["prlimit_blocked"] = True
errread, errwrite = os.pipe()
try:
    pid = subprocess._fork_exec(
        [b"/usr/bin/touch", {str(fork_canary)!r}.encode()],
        (b"/usr/bin/touch",), True, (errwrite,), None, None,
        -1, -1, -1, -1, -1, -1, errread, errwrite,
        True, False, -1, None, None, None, -1, None, False,
    )
    result["fork_exec_called"] = True
    os.close(errwrite)
    os.read(errread, 50000)
    os.waitpid(pid, 0)
except (OSError, TypeError):
    pass
print(json.dumps(result, sort_keys=True))
""".encode()
    try:
        output = _run_raw_sandbox(runtime, harness)
        with pytest.raises(TimeoutError):
            socket_peer.recv(3)
    finally:
        socket_canary.close()
        socket_peer.close()
        signal.signal(signal.SIGWINCH, previous)
    result = json.loads(output)
    assert result["native_kill"] in (-1, 0)
    del result["native_kill"]
    assert isinstance(result["prlimit_blocked"], bool)
    del result["prlimit_blocked"]
    assert result == {
        "fork_exec_called": True,
        "native_open": -1,
        "native_socket_write": -1,
    }
    assert file_canary.read_text(encoding="utf-8") == "unchanged"
    assert not fork_canary.exists()
    assert not signalled
    assert resource.getrlimit(resource.RLIMIT_NOFILE) == limits_before


def test_sealed_candidate_regular_file_blocks_mutate_effect_with_matching_forged_child_evidence_bug(
    tmp_path: Path, nested_bwrap: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(
        tmp_path,
        'WRITE_TOOLS = ("kanban_enqueue", "kanban_sync_external_task")\n',
    )
    source = runtime / "agent/transports/hermes_kanban_mcp_server.py"
    original = source.read_bytes()
    marker = tmp_path / "forged-candidate-effect"
    real_run = os_sandbox.run

    def mutate_after_capture(**kwargs: object) -> bytes:
        source.write_text(
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('forged')\n"
            'WRITE_TOOLS = ("kanban_sync_external_task",)\n',
            encoding="utf-8",
        )
        try:
            observed = json.loads(real_run(**kwargs))
            return (json.dumps(observed, sort_keys=True) + "\n").encode()
        finally:
            source.write_bytes(original)

    monkeypatch.setattr(os_sandbox, "run", mutate_after_capture)
    evidence = coherence.run_import_preflight(runtime, ".venv")
    assert evidence.write_tools == (
        "kanban_enqueue",
        "kanban_sync_external_task",
    )
    assert source.read_bytes() == original
    assert not marker.exists()


def test_sealed_trusted_stdlib_regular_file_blocks_in_place_import_effect_bug(
    tmp_path: Path, nested_bwrap: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    trusted_interpreter, real_roots = coherence._trusted_python()
    extra_root = tmp_path / "trusted-extra-stdlib"
    extra_root.mkdir()
    trusted_module = extra_root / "sealed_stdlib_probe.py"
    trusted_module.write_text('VALUE = "sealed"\n', encoding="utf-8")
    original = trusted_module.read_bytes()
    marker = tmp_path / "forged-stdlib-effect"
    runtime = _runtime(
        tmp_path,
        "from sealed_stdlib_probe import VALUE\n"
        'assert VALUE == "sealed"\n'
        'WRITE_TOOLS = ("kanban_sync_external_task",)\n',
    )
    monkeypatch.setattr(
        coherence,
        "_trusted_python",
        lambda: (trusted_interpreter, (*real_roots, extra_root)),
    )
    real_run = os_sandbox.run

    def mutate_after_capture(**kwargs: object) -> bytes:
        trusted_module.write_text(
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('forged')\n"
            'VALUE = "forged"\n',
            encoding="utf-8",
        )
        try:
            return real_run(**kwargs)
        finally:
            trusted_module.write_bytes(original)

    monkeypatch.setattr(os_sandbox, "run", mutate_after_capture)
    evidence = coherence.run_import_preflight(runtime, ".venv")
    assert evidence.write_tools == ("kanban_sync_external_task",)
    assert trusted_module.read_bytes() == original
    assert not marker.exists()


def test_sealed_interpreter_bytes_survive_in_place_backing_mutation_bug(
    tmp_path: Path, nested_bwrap: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(
        tmp_path, 'WRITE_TOOLS = ("kanban_sync_external_task",)\n'
    )
    interpreter = runtime / ".venv/bin/python"
    original = interpreter.read_bytes()
    real_run = os_sandbox.run

    def mutate_after_capture(**kwargs: object) -> bytes:
        interpreter.write_bytes(b"\0" * len(original))
        try:
            return real_run(**kwargs)
        finally:
            interpreter.write_bytes(original)

    monkeypatch.setattr(os_sandbox, "run", mutate_after_capture)
    assert coherence.run_import_preflight(
        runtime, ".venv"
    ).write_tools == ("kanban_sync_external_task",)
    assert interpreter.read_bytes() == original


def _forged_result(
    anchors: object,
    sandbox: Path,
    dirname: str,
    manifest_sha256: str,
    *,
    forged: bool = False,
) -> bytes:
    value = {
        "bundle_manifest_sha256": manifest_sha256,
        "candidate_digest": "0" * 64 if forged else anchors.candidate_digest,
        "hermes_cli_main_origin": str(sandbox / "hermes_cli/main.py"),
        "kanban_server_origin": str(
            sandbox / "agent/transports/hermes_kanban_mcp_server.py"
        ),
        "pyvenv_cfg_home": str(anchors.pyvenv_cfg_home),
        "pyvenv_cfg_sha256": anchors.pyvenv_cfg_sha256,
        "resolved_interpreter": str(sandbox / dirname / "bin/python"),
        "source_digest": anchors.source_digest,
        "stdlib_roots": [str(path) for path in anchors.stdlib_roots],
        "venv_digest": anchors.venv_digest,
        "write_tools": ["kanban_sync_external_task"],
    }
    return (json.dumps(value, sort_keys=True) + "\n").encode()


def test_forged_child_evidence_cannot_expand_parent_trust(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(
        tmp_path, 'WRITE_TOOLS = ("kanban_sync_external_task",)\n'
    )

    def forged(**kwargs: object) -> bytes:
        anchors = _anchors(runtime)
        bundle = kwargs["bundle"]
        return _forged_result(
            anchors,
            invocation.SANDBOX_RUNTIME,
            ".venv",
            bundle.manifest_sha256,
            forged=True,
        )

    monkeypatch.setattr(os_sandbox, "run", forged)
    with pytest.raises(rollout.RolloutError, match="parent anchor"):
        coherence.run_import_preflight(runtime, ".venv")


@pytest.mark.parametrize("kind", ["missing", "broken", "symlink"])
def test_missing_broken_or_substituted_bwrap_fails_before_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    runtime = _runtime(
        tmp_path, 'WRITE_TOOLS = ("kanban_sync_external_task",)\n'
    )
    candidate_marker = runtime / "agent-imported"
    (runtime / "agent/transports/hermes_kanban_mcp_server.py").write_text(
        f"Path({str(candidate_marker)!r}).write_text('bad')\n",
        encoding="utf-8",
    )
    target = tmp_path / "bwrap"
    if kind == "broken":
        target.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        target.chmod(0o700)
    elif kind == "symlink":
        target.symlink_to("/usr/bin/bwrap")
    monkeypatch.setattr(invocation, "BWRAP", target)
    with pytest.raises(rollout.RolloutError, match="bwrap|bubblewrap"):
        coherence.run_import_preflight(runtime, ".venv")
    assert not candidate_marker.exists()


def test_symlinked_startup_anchors_are_rejected(tmp_path: Path) -> None:
    runtime = _runtime(
        tmp_path, 'WRITE_TOOLS = ("kanban_sync_external_task",)\n'
    )
    site = next((runtime / ".venv/lib").glob("python*/site-packages"))
    outside = tmp_path / "outside"
    outside.mkdir()
    site.rmdir()
    site.symlink_to(outside, target_is_directory=True)
    with pytest.raises(rollout.RolloutError, match="site-packages"):
        coherence.run_import_preflight(runtime, ".venv")
    shutil.rmtree(runtime)
    runtime = _runtime(
        tmp_path, 'WRITE_TOOLS = ("kanban_sync_external_task",)\n'
    )
    cfg = runtime / ".venv/pyvenv.cfg"
    external = tmp_path / "external-pyvenv.cfg"
    cfg.replace(external)
    cfg.symlink_to(external)
    with pytest.raises(rollout.RolloutError, match="pyvenv.cfg"):
        coherence.run_import_preflight(runtime, ".venv")


def test_candidate_special_files_are_rejected_before_mount(tmp_path: Path) -> None:
    runtime = _runtime(
        tmp_path, 'WRITE_TOOLS = ("kanban_sync_external_task",)\n'
    )
    os.mkfifo(runtime / "host-endpoint")
    with pytest.raises(rollout.RolloutError, match="unsupported file type"):
        coherence.run_import_preflight(runtime, ".venv")


@pytest.mark.parametrize(
    "stage",
    ["memfd", "write", "lseek", "readback", "hash", "seal-add", "seal-get"],
)
def test_sealed_data_acquisition_failure_closes_current_fd_without_leak_bug(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    baseline = _fd_oracle()
    owner = sealed._FDResourceOwner()
    prior = sealed._data_fd(owner, "prior", b"prior")
    original_memfd = sealed.os.memfd_create
    original_write = sealed.os.write
    original_lseek = sealed.os.lseek
    original_read = sealed.os.read
    original_hash = sealed.hashlib.sha256
    original_fcntl = sealed.fcntl.fcntl

    if stage == "memfd":
        monkeypatch.setattr(
            sealed.os,
            "memfd_create",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("memfd")),
        )
    elif stage == "write":
        monkeypatch.setattr(
            sealed.os,
            "write",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("write")),
        )
    elif stage == "lseek":
        monkeypatch.setattr(
            sealed.os,
            "lseek",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("lseek")),
        )
    elif stage == "readback":
        monkeypatch.setattr(
            sealed.os,
            "read",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("read")),
        )
    elif stage == "hash":
        monkeypatch.setattr(
            sealed.hashlib,
            "sha256",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("hash")),
        )
    else:
        command = (
            sealed.fcntl.F_ADD_SEALS
            if stage == "seal-add"
            else sealed.fcntl.F_GET_SEALS
        )

        def fail_fcntl(fd: int, operation: int, *args: object) -> int:
            if operation == command:
                raise OSError(stage)
            return original_fcntl(fd, operation, *args)

        monkeypatch.setattr(sealed.fcntl, "fcntl", fail_fcntl)

    with pytest.raises(sealed.SandboxError, match="cannot create sealed"):
        sealed._sealed_data_fd(owner, "current", b"captured")
    assert owner.fds == (prior,)
    monkeypatch.setattr(sealed.os, "memfd_create", original_memfd)
    monkeypatch.setattr(sealed.os, "write", original_write)
    monkeypatch.setattr(sealed.os, "lseek", original_lseek)
    monkeypatch.setattr(sealed.os, "read", original_read)
    monkeypatch.setattr(sealed.hashlib, "sha256", original_hash)
    monkeypatch.setattr(sealed.fcntl, "fcntl", original_fcntl)
    owner.close_all()
    assert _fd_oracle() == baseline


def test_partial_capture_cleanup_reports_primary_and_close_error_without_fd_leak_bug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(
        tmp_path, 'WRITE_TOOLS = ("kanban_sync_external_task",)\n'
    )
    trusted_interpreter, _roots = coherence._trusted_python()
    trusted_root = tmp_path / "trusted-stdlib"
    trusted_root.mkdir()
    (trusted_root / "trusted.py").write_text("VALUE = 1\n", encoding="utf-8")
    baseline = _fd_oracle()
    original_manifest = sealed._manifest_bytes
    original_close = sealed.os.close
    cleanup_started = False
    failed_close = False

    def fail_manifest(_entries: object) -> bytes:
        nonlocal cleanup_started
        cleanup_started = True
        raise RuntimeError("injected manifest capture failure")

    def fail_one_close(fd: int) -> None:
        nonlocal failed_close
        if cleanup_started and not failed_close:
            failed_close = True
            raise OSError("injected close failure")
        original_close(fd)

    monkeypatch.setattr(sealed, "_manifest_bytes", fail_manifest)
    monkeypatch.setattr(sealed.os, "close", fail_one_close)
    with pytest.raises(sealed.SandboxError) as raised:
        sealed.capture_bundle(
            runtime,
            ".venv",
            trusted_interpreter,
            (trusted_root,),
        )
    assert isinstance(raised.value.primary, RuntimeError)
    assert str(raised.value.primary) == "injected manifest capture failure"
    assert raised.value.cleanup_failures
    assert failed_close
    monkeypatch.setattr(sealed, "_manifest_bytes", original_manifest)
    monkeypatch.setattr(sealed.os, "close", original_close)
    assert _fd_oracle() == baseline


def test_sealed_exec_handoff_failure_closes_bundle_without_fd_leak_bug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(
        tmp_path, 'WRITE_TOOLS = ("kanban_sync_external_task",)\n'
    )
    trusted_interpreter, _roots = coherence._trusted_python()
    trusted_root = tmp_path / "trusted-stdlib"
    trusted_root.mkdir()
    (trusted_root / "trusted.py").write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.setattr(
        coherence,
        "_trusted_python",
        lambda: (trusted_interpreter, (trusted_root,)),
    )
    calls = 0

    def fail_production(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess:
        nonlocal calls
        calls += 1
        if calls == 1:
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
        raise OSError("injected exec handoff failure")

    baseline = _fd_oracle()
    monkeypatch.setattr(os_sandbox.subprocess, "run", fail_production)
    with pytest.raises(rollout.RolloutError) as raised:
        coherence.run_import_preflight(runtime, ".venv")
    assert isinstance(raised.value.primary_failure, OSError)
    assert "exec handoff" in str(raised.value.primary_failure)
    assert calls == 2
    assert _fd_oracle() == baseline


def test_memfd_seals_and_no_mutable_backing_bind_are_load_bearing_bug(
    tmp_path: Path
) -> None:
    runtime = _runtime(
        tmp_path, 'WRITE_TOOLS = ("kanban_sync_external_task",)\n'
    )
    source = runtime / "agent/transports/hermes_kanban_mcp_server.py"
    original = source.read_bytes()
    bundle = coherence._parent_trust_bundle(runtime, ".venv")
    try:
        entry = bundle.content.file_entry(
            invocation.SANDBOX_RUNTIME
            / "agent/transports/hermes_kanban_mcp_server.py"
        )
        source.write_bytes(b"forged mutable backing bytes\n")
        assert bundle.content.read_file(entry.destination) == original
        assert source.read_bytes() != original
        with pytest.raises(OSError):
            os.write(entry.fd, b"mutate sealed bytes")
    finally:
        source.write_bytes(original)
        bundle.close()
