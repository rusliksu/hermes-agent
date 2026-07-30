from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.scripts.hermes_kanban_mcp_test_support import (
    install_trusted_bwrap_result,
)
from tests.scripts.hermes_kanban_mcp_resource_test_support import (
    assert_external_symlink_case,
    assert_late_probe_pressure_rejected,
)

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts" / "hermes_kanban_mcp_rollout.py"
SCHEMA_V2_GOLDEN = (
    ROOT / "tests" / "scripts" / "fixtures"
    / "hermes_kanban_mcp_schema_v2_rollout"
)
SPEC = importlib.util.spec_from_file_location("runtime_coherence_rollout", HELPER)
assert SPEC is not None and SPEC.loader is not None
rollout = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rollout
SPEC.loader.exec_module(rollout)
_sealed = rollout.coherence.os_sandbox.sealed
_elf = sys.modules[_sealed.parse_elf.__module__]
_inventory = sys.modules[_sealed.InventoryBuilder.__module__]
_os_sandbox = rollout.coherence.os_sandbox


@pytest.fixture(autouse=True)
def _trusted_bwrap_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_trusted_bwrap_result(monkeypatch, rollout, tmp_path)


def _git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(repo), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


def test_gnu_loader_rpath_inheritance_and_direct_runpath_are_distinct() -> None:
    platform = _elf.LoaderPlatform("lib64", "x86_64")
    roots = (Path("/opt/runtime"), Path("/lib64"))
    defaults = (Path("/lib64"),)
    legacy = _elf.ElfInfo(None, ("libchild.so",), ("/opt/runtime/legacy",), ())
    direct, inherited = _elf.dependency_search(
        legacy,
        origin=Path("/opt/runtime/bin"),
        inherited_rpath=(),
        platform=platform,
        allowed_roots=roots,
        defaults=defaults,
    )
    assert direct == (Path("/opt/runtime/legacy"), Path("/lib64"))
    assert inherited == (Path("/opt/runtime/legacy"),)

    runpath = _elf.ElfInfo(
        None, ("libgrandchild.so",), ("/opt/runtime/ignored",),
        ("$ORIGIN/direct/$LIB/$PLATFORM",),
    )
    child_direct, child_inherited = _elf.dependency_search(
        runpath,
        origin=Path("/opt/runtime/child"),
        inherited_rpath=inherited,
        platform=platform,
        allowed_roots=roots,
        defaults=defaults,
    )
    assert child_direct == (
        Path("/opt/runtime/legacy"),
        Path("/opt/runtime/child/direct/lib64/x86_64"),
        Path("/lib64"),
    )
    assert child_inherited == inherited


def test_unresolved_loader_token_rejects_before_default_search() -> None:
    info = _elf.ElfInfo(None, ("libchild.so",), (), ("$UNSUPPORTED/lib",))
    with pytest.raises(_elf.ElfFormatError, match="unsupported"):
        _elf.dependency_search(
            info,
            origin=Path("/opt/runtime"),
            inherited_rpath=(),
            platform=_elf.LoaderPlatform("lib64", "x86_64"),
            allowed_roots=(Path("/opt/runtime"), Path("/lib64")),
            defaults=(Path("/lib64"),),
        )


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _oracle(root: Path) -> tuple[tuple[object, ...], ...]:
    records = []
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in sorted(dirnames + filenames):
            path = base / name
            relative = str(path.relative_to(root))
            mode = stat.S_IMODE(path.lstat().st_mode)
            if path.is_symlink():
                records.append((relative, "link", mode, os.readlink(path)))
            elif path.is_file():
                records.append((relative, "file", mode, _hash(path.read_bytes())))
            else:
                records.append((relative, "dir", mode))
    return tuple(records)


def _write_surface(root: Path, *, external_sync: bool) -> None:
    for package in ("hermes_cli", "agent", "agent/transports"):
        (root / package).mkdir(parents=True, exist_ok=True)
        (root / package / "__init__.py").write_text("", encoding="utf-8")
    (root / "hermes_cli" / "main.py").write_text(
        'raise AssertionError("hermes_cli.main must not be imported by preflight")\n',
        encoding="utf-8",
    )
    tools = ("kanban_enqueue", "kanban_sync_external_task") if external_sync else ("old",)
    (root / "agent" / "transports" / "hermes_kanban_mcp_server.py").write_text(
        f"WRITE_TOOLS = {tools!r}\n", encoding="utf-8"
    )


def _wrapper_bytes(runtime: Path, *, canonical: bool) -> bytes:
    lines = [
        "#!/bin/bash",
        "set -euo pipefail",
        f"export HERMES_HOME={runtime.parent}/hermes-home",
        "export HERMES_QUIET=1",
        "export HERMES_REDACT_SECRETS=true",
        "export PYTHONDONTWRITEBYTECODE=1",
    ]
    if canonical:
        lines.append("ulimit -S -n 4096")
        lines.append(f"cd -- {runtime}")
    lines.append(
        f"exec {runtime}/.venv/bin/python"
        ' -m hermes_cli.main mcp serve-kanban --allow-write "$@"'
    )
    return ("\n".join(lines) + "\n").encode()


def _make_venv(
    runtime: Path, *, old_site_packages: bool, pth_marker: Path | None = None
) -> None:
    venv = runtime / ".venv"
    interpreter = venv / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(Path(sys.executable).read_bytes())
    interpreter.chmod(stat.S_IMODE(Path(sys.executable).stat().st_mode))
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    (venv / "pyvenv.cfg").write_text(
        f"home = {Path(getattr(sys, '_base_executable', sys.executable)).resolve().parent}\n"
        "include-system-site-packages = false\n"
        f"version = {version}\n",
        encoding="utf-8",
    )
    site_packages = venv / "lib" / f"python{version}" / "site-packages"
    site_packages.mkdir(parents=True)
    if old_site_packages:
        _write_surface(site_packages, external_sync=False)
        if pth_marker is not None:
            (site_packages / "must-not-run.pth").write_text(
                "import pathlib; "
                f"pathlib.Path({str(pth_marker)!r}).write_text('executed')\n",
                encoding="utf-8",
            )


@dataclass
class Layout:
    root: Path
    source: Path
    runtime_root: Path
    state_root: Path
    wrapper: Path
    current_runtime: Path
    current_sha: str
    candidate_sha: str

    def runtime(self, sha: str) -> Path:
        return self.runtime_root / f"hermes-kanban-mcp-{sha}"

    def snapshot(self, before: str, after: str) -> Path:
        return self.state_root / "snapshots" / f"{before}-to-{after}"

    def prepare(
        self,
        current_runtime: Path,
        current_sha: str,
        candidate_sha: str,
        wrapper_hash: str,
        *,
        apply: bool = False,
    ) -> list[str]:
        args = [
            "prepare", "--source-repo", str(self.source),
            "--runtime-root", str(self.runtime_root),
            "--state-root", str(self.state_root),
            "--current-runtime", str(current_runtime),
            "--expected-current-runtime-sha", current_sha,
            "--candidate-sha", candidate_sha,
            "--venv-dirname", ".venv",
            "--stable-wrapper", str(self.wrapper),
            "--expected-current-wrapper-sha256", wrapper_hash,
        ]
        if apply:
            generated = _wrapper_bytes(self.runtime(candidate_sha), canonical=True)
            args += ["--expected-wrapper-after-sha256", _hash(generated)]
        return [*args, "--apply"] if apply else args

    def transition(
        self, command: str, before: str, after: str, wrapper_hash: str, *, apply: bool
    ) -> list[str]:
        args = [
            command, "--runtime-root", str(self.runtime_root),
            "--state-root", str(self.state_root),
            "--snapshot-id", f"{before}-to-{after}",
            "--stable-wrapper", str(self.wrapper),
            "--expected-current-wrapper-sha256", wrapper_hash,
        ]
        if apply and command == "switch":
            generated = self.snapshot(before, after) / "wrapper.after"
            args += ["--expected-wrapper-after-sha256", _hash(generated.read_bytes())]
        return [*args, "--apply"] if apply else args


@pytest.fixture
def layout(tmp_path: Path) -> Layout:
    source = tmp_path / "source"
    runtime_root = tmp_path / "runtimes"
    state_root = tmp_path / "state"
    wrapper = tmp_path / "bin" / "run-kanban"
    for path in (source, runtime_root, state_root, wrapper.parent):
        path.mkdir()
    _git(source, "init")
    _git(source, "config", "user.email", "tests@example.invalid")
    _git(source, "config", "user.name", "Runtime coherence tests")
    _write_surface(source, external_sync=True)
    payload = source / "payload.txt"
    payload.write_text("current\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-m", "current")
    current_sha = _git(source, "rev-parse", "HEAD")
    payload.write_text("candidate\n", encoding="utf-8")
    _git(source, "commit", "-am", "candidate")
    candidate_sha = _git(source, "rev-parse", "HEAD")
    current_runtime = runtime_root / f"hermes-kanban-mcp-{current_sha}"
    _git(source, "worktree", "add", "--detach", str(current_runtime), current_sha)
    _make_venv(
        current_runtime,
        old_site_packages=True,
        pth_marker=tmp_path / "pth-executed",
    )
    wrapper.write_bytes(_wrapper_bytes(current_runtime, canonical=False))
    wrapper.chmod(0o750)
    return Layout(
        tmp_path, source, runtime_root, state_root, wrapper,
        current_runtime, current_sha, candidate_sha,
    )


def _manifest(layout: Layout, before: str, after: str) -> dict[str, object]:
    return json.loads(
        (layout.snapshot(before, after) / "manifest.json").read_text(encoding="utf-8")
    )


def test_legacy_to_canonical_v3_shadowing_dry_run_switch_and_rollback(
    layout: Layout, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PYTHONPATH", "/forbidden/pythonpath")
    monkeypatch.setenv("PYTHONHOME", "/forbidden/pythonhome")
    before = layout.wrapper.read_bytes()
    before_hash = _hash(before)
    oracle = _oracle(layout.root)
    assert rollout.main(
        layout.prepare(
            layout.current_runtime, layout.current_sha, layout.candidate_sha, before_hash
        )
    ) == 0
    prepare_dry_run = json.loads(capsys.readouterr().out)
    assert "import_origin" not in prepare_dry_run
    assert _oracle(layout.root) == oracle

    assert rollout.main(
        layout.prepare(
            layout.current_runtime, layout.current_sha, layout.candidate_sha,
            before_hash, apply=True,
        )
    ) == 0
    prepare_apply = json.loads(capsys.readouterr().out)
    assert prepare_apply["import_origin"]["kanban_server_origin"].endswith(
        "agent/transports/hermes_kanban_mcp_server.py"
    )
    candidate = layout.runtime(layout.candidate_sha)
    snapshot = layout.snapshot(layout.current_sha, layout.candidate_sha)
    manifest = _manifest(layout, layout.current_sha, layout.candidate_sha)
    wrapper_after = (snapshot / "wrapper.after").read_bytes()
    assert manifest["schema_version"] == 3
    assert manifest["wrapper_contract"] == "source-cwd-nofile-v2"
    assert manifest["write_tools"] == [
        "kanban_enqueue", "kanban_sync_external_task"
    ]
    assert manifest["hermes_cli_main_origin"] == str(candidate / "hermes_cli" / "main.py")
    assert wrapper_after == _wrapper_bytes(candidate, canonical=True)
    lines = wrapper_after.decode().splitlines()
    assert lines[-2].startswith("cd -- ")
    assert lines[-1].startswith(f"exec {candidate}/.venv/bin/python ")
    assert not (layout.root / "pth-executed").exists()

    interpreter = candidate / ".venv" / "bin" / "python"
    old = subprocess.run(
        [str(interpreter), "-c",
         "import agent.transports.hermes_kanban_mcp_server as m; print(m.__file__)"],
        cwd=layout.root,
        env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"},
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    assert "site-packages" in old
    evidence = rollout.coherence.run_import_preflight(candidate, ".venv")
    assert evidence.kanban_server_origin == (
        candidate / "agent" / "transports" / "hermes_kanban_mcp_server.py"
    )

    switch_args = layout.transition(
        "switch", layout.current_sha, layout.candidate_sha, before_hash, apply=False
    )
    oracle = _oracle(layout.root)
    assert rollout.main(switch_args) == 0
    switch_dry_run = json.loads(capsys.readouterr().out)
    assert switch_dry_run["import_origin"]["source_cwd"] == str(candidate)
    assert _oracle(layout.root) == oracle
    assert rollout.main([*switch_args, "--expected-wrapper-after-sha256", _hash(wrapper_after), "--apply"]) == 0
    switch_apply = json.loads(capsys.readouterr().out)
    assert switch_apply["import_origin"] == switch_dry_run["import_origin"]
    assert layout.wrapper.read_bytes() == wrapper_after
    after_hash = _hash(wrapper_after)
    assert rollout.main(
        layout.transition(
            "rollback", layout.current_sha, layout.candidate_sha,
            after_hash, apply=True,
        )
    ) == 0
    capsys.readouterr()
    assert layout.wrapper.read_bytes() == before


def test_switch_keeps_sealed_content_fds_open_through_atomic_replacement_bug(
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    before_hash = _hash(layout.wrapper.read_bytes())
    assert rollout.main(
        layout.prepare(
            layout.current_runtime,
            layout.current_sha,
            layout.candidate_sha,
            before_hash,
            apply=True,
        )
    ) == 0
    capsys.readouterr()
    sandbox_run = rollout.coherence.os_sandbox.run
    observed_fds: list[int] = []

    def record_fds(**kwargs: object) -> bytes:
        bundle = kwargs["bundle"]
        observed_fds[:] = list(bundle.descriptors)
        assert observed_fds
        assert all(
            entry.fd in observed_fds
            for entry in bundle.entries
            if entry.kind == "file"
        )
        return sandbox_run(**kwargs)

    atomic_replace = rollout.state._atomic_replace

    def replace_while_fds_are_live(*args: object, **kwargs: object) -> None:
        assert observed_fds
        for descriptor in observed_fds:
            os.fstat(descriptor)
        atomic_replace(*args, **kwargs)

    monkeypatch.setattr(rollout.coherence.os_sandbox, "run", record_fds)
    monkeypatch.setattr(rollout.state, "_atomic_replace", replace_while_fds_are_live)
    assert rollout.main(
        layout.transition(
            "switch",
            layout.current_sha,
            layout.candidate_sha,
            before_hash,
            apply=True,
        )
    ) == 0
    capsys.readouterr()
    for descriptor in observed_fds:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_canonical_to_canonical_targets_only_next_runtime(
    layout: Layout, capsys: pytest.CaptureFixture[str]
) -> None:
    legacy_hash = _hash(layout.wrapper.read_bytes())
    assert rollout.main(
        layout.prepare(
            layout.current_runtime, layout.current_sha, layout.candidate_sha,
            legacy_hash, apply=True,
        )
    ) == 0
    capsys.readouterr()
    first_after = (
        layout.snapshot(layout.current_sha, layout.candidate_sha) / "wrapper.after"
    ).read_bytes()
    assert rollout.main(
        layout.transition(
            "switch", layout.current_sha, layout.candidate_sha,
            legacy_hash, apply=True,
        )
    ) == 0
    capsys.readouterr()
    (layout.source / "payload.txt").write_text("next\n", encoding="utf-8")
    _git(layout.source, "commit", "-am", "next")
    next_sha = _git(layout.source, "rev-parse", "HEAD")
    first_runtime = layout.runtime(layout.candidate_sha)
    assert rollout.main(
        layout.prepare(
            first_runtime, layout.candidate_sha, next_sha,
            _hash(first_after), apply=True,
        )
    ) == 0
    capsys.readouterr()
    second_snapshot = layout.snapshot(layout.candidate_sha, next_sha)
    assert (second_snapshot / "wrapper.before").read_bytes() == first_after
    second_after = (second_snapshot / "wrapper.after").read_bytes()
    assert second_after == rollout.coherence.rewrite_rollout_wrapper(
        first_after, first_runtime, layout.runtime(next_sha), ".venv"
    )
    assert str(first_runtime).encode() not in second_after


def test_historical_v2_golden_is_literal_snapshot_only_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_files = {
        "manifest.json": (
            1311, "73c1ff3fa33e8888a8cfb9184f6adf7aef654f54e3a1d314cea25333de681cfd"
        ),
        "wrapper.before": (
            277, "95e89250e68e46e80b7a3891ee327b9d3558a949685b295129fabf4c8cef9684"
        ),
        "wrapper.after": (
            277, "f5ed7ba0aa765c964cd7920fd57ebc05e3dd1c751df95b078d3a3df98d1e00ca"
        ),
        "provenance.json": (
            4909, "0bbd089881a577296d0c0eca093118f415c8a738a9f8fcff22cb85223a9a1e38"
        ),
    }
    fixture_bytes = {
        name: (SCHEMA_V2_GOLDEN / name).read_bytes() for name in expected_files
    }
    for name, (size, digest) in expected_files.items():
        assert len(fixture_bytes[name]) == size
        assert _hash(fixture_bytes[name]) == digest
        assert b"/home/openclaw" not in fixture_bytes[name]
    blobs = {
        name: fixture_bytes[name]
        for name in ("manifest.json", "wrapper.before", "wrapper.after")
    }

    provenance = json.loads(fixture_bytes["provenance.json"].decode("utf-8"))
    assert provenance["safe_inventory_run_id"] == (
        "20260729T185628Z-kanban-v2-golden-inventory"
    )
    assert provenance["source_snapshot_id"] == (
        "6f8738dc308f909bf1735883344f2fcc12f3cbcd"
        "-to-30500cf973a40bb0918d33eb0476c1025e08ac0f"
    )
    assert provenance["source_snapshot_path_sha256"] == (
        "52608295633bda125705005be40a660b7b0b1ded3981cf4fa52ad345d9c4a604"
    )
    assert provenance["inventory"] == {
        "runtime_path_replacements": 1,
        "sensitive_term_matches": 0,
    }
    expected_original = {
        "manifest.json": {
            "sha256": "83db7f0c4cd2a3239e5d52402f6b8b88e1a66ca46ba1daa5677249fcac4a196f",
            "size": 1406,
            "mode": "0600",
        },
        "wrapper.before": {
            "sha256": "17052c7d51307f47f9d3d6826a584114d26a1e57c0a272bc48179fed662c1ab9",
            "size": 286,
            "mode": "0600",
        },
        "wrapper.after": {
            "sha256": "5e03752f40af19fca3151e6ccb5da182521c7860d6c9ebded8f796ce327aad53",
            "size": 286,
            "mode": "0600",
        },
    }
    assert {
        name: evidence["original"]
        for name, evidence in provenance["artifacts"].items()
    } == expected_original
    for name, (size, digest) in {
        key: expected_files[key] for key in blobs
    }.items():
        evidence = provenance["artifacts"][name]
        assert evidence["sanitized"] == {
            "sha256": digest,
            "size": size,
            "mode": "0600",
        }

    ledger = provenance["ordered_substitutions"]
    ledger_fields = (
        "order", "file", "field", "source_class", "source_sha256",
        "replacement", "count", "reason",
    )
    assert all(set(entry) == set(ledger_fields) for entry in ledger)
    assert [tuple(entry[field] for field in ledger_fields) for entry in ledger] == [
        (1, "manifest.json", "source_repo", "host_absolute_path",
         "5ecd322a8f2b347cbb16e3ba278e486c689c7ec61d1486ee8c66dd84a04a53ab",
         "/__schema_v2_golden__/source", 1,
         "remove the host-specific source checkout path"),
        (2, "manifest.json",
         "after_runtime_path,before_runtime_path,runtime_root,state_root",
         "host_absolute_path",
         "113c196425278ff823e24aae3c9ca0a5dd81defd7daee04a0ca316419464f68a",
         "/__schema_v2_golden__/state", 4,
         "remove the host-specific rollout state root"),
        (3, "wrapper.before", "exec.interpreter", "host_absolute_path",
         "113c196425278ff823e24aae3c9ca0a5dd81defd7daee04a0ca316419464f68a",
         "/__schema_v2_golden__/state", 1,
         "remove the host-specific baseline runtime root"),
        (4, "wrapper.after", "exec.interpreter", "host_absolute_path",
         "113c196425278ff823e24aae3c9ca0a5dd81defd7daee04a0ca316419464f68a",
         "/__schema_v2_golden__/state", 1,
         "remove the host-specific candidate runtime root"),
        (5, "manifest.json", "stable_wrapper", "host_absolute_path",
         "bd283008daf40cb634c12b225ec311fd816096e5cf4028a826a4c1c3aa69082f",
         "/__schema_v2_golden__/stable/run.sh", 1,
         "remove the host-specific stable wrapper path"),
        (6, "wrapper.before", "export.HERMES_HOME", "host_absolute_path",
         "347b2a331466fad4d76120391a94bcb9c7d09717c971ca8bb0603bbe2483ef41",
         "/__schema_v2_golden__/home", 1,
         "remove the host-specific Hermes home"),
        (7, "wrapper.after", "export.HERMES_HOME", "host_absolute_path",
         "347b2a331466fad4d76120391a94bcb9c7d09717c971ca8bb0603bbe2483ef41",
         "/__schema_v2_golden__/home", 1,
         "remove the host-specific Hermes home"),
        (8, "manifest.json", "wrapper_before_sha256", "derived_payload_sha256",
         "767621b3403baadcf45cea6ce126bb361b341d90941a1868f25182ca9185da9b",
         expected_files["wrapper.before"][1], 1,
         "record the sanitized wrapper.before payload digest"),
        (9, "manifest.json", "wrapper_after_sha256", "derived_payload_sha256",
         "df49908224d45e8f53e9c4d6a015392ac924916619872f3cdc854eeec0a8f982",
         expected_files["wrapper.after"][1], 1,
         "record the sanitized wrapper.after payload digest"),
    ]

    marker = b"/__schema_v2_golden__"
    relocated_prefix = str(tmp_path / "golden").encode()
    before = blobs["wrapper.before"].replace(marker, relocated_prefix)
    after = blobs["wrapper.after"].replace(marker, relocated_prefix)
    manifest = json.loads(blobs["manifest.json"])
    for field in (
        "after_runtime_path",
        "before_runtime_path",
        "runtime_root",
        "source_repo",
        "stable_wrapper",
        "state_root",
    ):
        manifest[field] = manifest[field].replace(
            marker.decode(), relocated_prefix.decode()
        )
    manifest["wrapper_before_sha256"] = _hash(before)
    manifest["wrapper_after_sha256"] = _hash(after)
    state_root = Path(manifest["state_root"])
    snapshot = state_root / "snapshots" / manifest["snapshot_id"]
    snapshot.mkdir(parents=True, mode=0o700)
    snapshot.parent.chmod(0o700)
    snapshot.chmod(0o700)
    relocated_blobs = {
        "manifest.json": (
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        ).encode(),
        "wrapper.before": before,
        "wrapper.after": after,
    }
    for name, data in relocated_blobs.items():
        path = snapshot / name
        path.write_bytes(data)
        path.chmod(0o600)
    stable_wrapper = Path(manifest["stable_wrapper"])
    stable_wrapper.parent.mkdir(parents=True)
    stable_wrapper.write_bytes(after)
    stable_wrapper.chmod(manifest["wrapper_mode"])

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("snapshot-only rollback inspected candidate state")

    for owner, name in (
        (rollout.coherence, "import_preflight_session"),
        (rollout.state, "validate_clean_worktree"),
        (rollout.state, "validate_commit"),
        (rollout.state, "validate_venv"),
        (rollout.coherence, "rewrite_rollout_wrapper"),
    ):
        monkeypatch.setattr(owner, name, forbidden)
    context = rollout.state.load_rollback_snapshot_context(
        runtime_root_raw=manifest["runtime_root"],
        state_root_raw=manifest["state_root"],
        snapshot_id=manifest["snapshot_id"],
        stable_wrapper_raw=manifest["stable_wrapper"],
    )
    assert context.wrapper_before == before
    assert context.wrapper_after == after
    assert context.stable_wrapper.data == after
    assert not context.source_repo.exists()
    assert not context.before_runtime.exists()
    assert not context.after_runtime.exists()
    plan = rollout.state.run_transition(
        command="rollback",
        runtime_root=manifest["runtime_root"],
        state_root=manifest["state_root"],
        snapshot_id=manifest["snapshot_id"],
        stable_wrapper=manifest["stable_wrapper"],
        expected_current_wrapper_sha256=_hash(after),
        apply=False,
    )
    assert plan["command"] == "rollback"
    assert any("wrapper.before" in operation for operation in plan["operations"])
    assert stable_wrapper.read_bytes() == after


@pytest.mark.parametrize("candidate_state", ["missing", "corrupt", "dirty"])
def test_v3_rollback_uses_only_snapshot_when_candidate_breaks_after_switch(
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    candidate_state: str,
) -> None:
    before = layout.wrapper.read_bytes()
    before_hash = _hash(before)
    assert rollout.main(
        layout.prepare(
            layout.current_runtime,
            layout.current_sha,
            layout.candidate_sha,
            before_hash,
            apply=True,
        )
    ) == 0
    capsys.readouterr()
    snapshot = layout.snapshot(layout.current_sha, layout.candidate_sha)
    after = (snapshot / "wrapper.after").read_bytes()
    assert rollout.main(
        layout.transition(
            "switch",
            layout.current_sha,
            layout.candidate_sha,
            before_hash,
            apply=True,
        )
    ) == 0
    capsys.readouterr()
    candidate = layout.runtime(layout.candidate_sha)
    if candidate_state == "missing":
        shutil.rmtree(candidate)
    elif candidate_state == "corrupt":
        (candidate / ".venv" / "bin" / "python").write_bytes(b"corrupt")
    else:
        (candidate / "payload.txt").write_text("dirty\n", encoding="utf-8")
    layout.source.rename(layout.root / "source-unavailable")

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("rollback inspected candidate runtime")

    monkeypatch.setattr(rollout.coherence, "import_preflight_session", forbidden)
    monkeypatch.setattr(rollout.state, "validate_clean_worktree", forbidden)
    monkeypatch.setattr(rollout.state, "validate_venv", forbidden)
    assert rollout.main(
        layout.transition(
            "rollback",
            layout.current_sha,
            layout.candidate_sha,
            _hash(after),
            apply=True,
        )
    ) == 0
    capsys.readouterr()
    assert layout.wrapper.read_bytes() == before


@pytest.mark.parametrize(
    ("case", "succeeds"),
    [
        ("relative-multihop", True),
        ("absolute-escape", False),
        ("relative-escape", False),
        ("dangling", False),
        ("cycle", False),
    ],
)
def test_external_elf_symlink_matrix_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str, succeeds: bool
) -> None:
    assert_external_symlink_case(
        _inventory, monkeypatch, tmp_path, case, succeeds,
        _hash(b"literal loader bytes"),
    )


@pytest.mark.parametrize("phase", ["probe", "production"])
@pytest.mark.parametrize("mutation", ["missing", "extra", "reordered"])
def test_canonical_invocation_rejects_non_exact_role_maps(
    phase: str, mutation: str
) -> None:
    spec = _os_sandbox.invocation.CanonicalInvocationSpec(
        (
            _os_sandbox.invocation.FDArg("a"),
            _os_sandbox.invocation.FDArg("b"),
        ),
        (
            _os_sandbox.invocation.FDArg("a"),
            _os_sandbox.invocation.FDArg("b"),
        ),
        (),
        (),
        ("a", "b"),
        2,
        5,
        5,
        1,
        1,
        100,
    )
    roles = {"a": 4, "b": 5}
    if mutation == "missing":
        roles = {"a": 4}
    elif mutation == "extra":
        roles["extra"] = 6
    else:
        roles = {"b": 5, "a": 4}
    render = spec.render_probe if phase == "probe" else spec.render_production
    with pytest.raises(_os_sandbox.resources.ResourceBudgetError, match="role"):
        render(roles)


def test_probe_actual_loader_argv_is_checked_before_args_memfd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def add_data(_name: str, _data: bytes) -> int:
        nonlocal calls
        calls += 1
        return 41

    oversized = "x" * 80
    spec = _os_sandbox.invocation.CanonicalInvocationSpec(
        (), (), (oversized,), (), (), 5, 1, 1, len(oversized) + 1, 1, 100_000
    )
    bundle = SimpleNamespace(
        add_data=add_data, descriptors=(), invocation=spec, entries=(),
        loader_fd=3, bwrap_fd=4, bwrap_library_fds=(),
    )
    monkeypatch.setattr(
        _os_sandbox.os, "sysconf",
        lambda _name: _os_sandbox.resources.ARG_MAX_SAFETY_MARGIN + 40,
    )
    monkeypatch.setattr(
        _os_sandbox.subprocess, "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0),
    )
    with pytest.raises(_os_sandbox.SandboxError, match="SC_ARG_MAX"):
        _os_sandbox._probe(bundle)
    assert calls == 0


def test_probe_final_handoff_rechecks_late_fd_pressure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert_late_probe_pressure_rejected(_os_sandbox, monkeypatch)


def test_directory_symlink_topology_budget_precedes_content_memfd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entries = []
    for index in range(18):
        directory = Path("/candidate") / ("d" * 24 + str(index))
        entries.extend(
            (
                _inventory.InventoryEntry(
                    directory, directory, "directory", 0o755,
                    (1, index + 1, stat.S_IFDIR | 0o755, 0, 1, 1),
                ),
                _inventory.InventoryEntry(
                    directory / ("l" * 24 + str(index)),
                    directory / ("l" * 24 + str(index)),
                    "symlink", 0o777,
                    (1, index + 101, stat.S_IFLNK | 0o777, 8, 1, 1),
                    target="../target",
                ),
            )
        )
    for index, path in enumerate((Path("/usr/bin/bwrap"), Path("/lib/ld-linux.so"))):
        entries.append(
            _inventory.InventoryEntry(
                path, path, "file", 0o755,
                (1, index + 201, stat.S_IFREG | 0o755, 1, 1, 1),
                1, "0" * 64,
            )
        )
    plan = _inventory.InventoryPlan(
        tuple(entries),
        ((Path("/usr/bin/bwrap"), (Path("/lib/ld-linux.so"),)),),
    )
    canonical = []
    for entry in entries[:-2]:
        canonical.extend(
            ("--perms", "755", "--dir", str(entry.destination))
            if entry.kind == "directory"
            else ("--symlink", "../target", str(entry.destination))
        )
    assert len(b"\0".join(item.encode() for item in canonical) + b"\0") > 512
    memfd_calls = 0

    def observed_memfd(*_args: object, **_kwargs: object) -> int:
        nonlocal memfd_calls
        memfd_calls += 1
        raise AssertionError("content memfd preceded canonical topology budget")

    monkeypatch.setattr(_sealed, "_inventory", lambda *_args, **_kwargs: plan)
    monkeypatch.setattr(_sealed.os, "memfd_create", observed_memfd)
    monkeypatch.setattr(_sealed.resources, "BWRAP_ARGS_MAX_BYTES", 512)
    with pytest.raises(_sealed.SandboxError, match="--args"):
        _sealed.capture_bundle(
            tmp_path / "runtime", ".venv", Path("/usr/bin/python"), ()
        )
    assert memfd_calls == 0


@pytest.mark.parametrize(
    "malformed",
    [
        "missing-contract",
        "ambiguous-runtime",
        "comments-only",
        "missing-exec",
        "extra-before",
        "extra-after",
        "redirect",
        "pipe",
        "and-operator",
        "background",
        "bad-shebang",
        "bad-set",
        "unknown-export",
        "missing-export",
        "reordered-export",
        "duplicate-export",
        "home-export",
        "expanded-hermes-home",
        "dollar-hermes-home",
        "backtick-hermes-home",
        "bad-bytecode-value",
    ],
)
def test_malformed_or_ambiguous_wrapper_fails_before_any_write(
    layout: Layout,
    capsys: pytest.CaptureFixture[str],
    malformed: str,
) -> None:
    original = layout.wrapper.read_bytes()
    if malformed == "missing-contract":
        data = original.replace(b" --allow-write", b"")
    elif malformed == "ambiguous-runtime":
        data = (
            original
            + b"exec "
            + str(layout.runtime(layout.candidate_sha) / ".venv/bin/python").encode()
            + b' -m hermes_cli.main mcp serve-kanban --allow-write "$@"\n'
        )
    elif malformed == "comments-only":
        data = b"#!/bin/sh\n# " + original.split(b"\n")[-2] + b"\n"
    elif malformed == "missing-exec":
        data = original.replace(b"\nexec ", b"\n", 1)
    elif malformed == "extra-before":
        data = original.replace(b"\nexec ", b"\nprintf unsafe\nexec ", 1)
    elif malformed == "extra-after":
        data = original + b"printf unsafe\n"
    elif malformed == "redirect":
        data = original.replace(b' "$@"\n', b' "$@" >out.log\n')
    elif malformed == "pipe":
        data = original.replace(b' "$@"\n', b' "$@" | cat\n')
    elif malformed == "and-operator":
        data = original.replace(b' "$@"\n', b' "$@" && true\n')
    elif malformed == "bad-shebang":
        data = original.replace(b"#!/bin/bash", b"#!/usr/bin/env sh", 1)
    elif malformed == "bad-set":
        data = original.replace(b"set -euo pipefail", b"set +e", 1)
    elif malformed == "unknown-export":
        data = original.replace(
            b"export HERMES_QUIET=1\n",
            b"export SECRET_TOKEN=forbidden\n",
            1,
        )
    elif malformed == "missing-export":
        data = original.replace(b"export PYTHONDONTWRITEBYTECODE=1\n", b"", 1)
    elif malformed == "reordered-export":
        data = original.replace(
            b"export HERMES_QUIET=1\nexport HERMES_REDACT_SECRETS=true\n",
            b"export HERMES_REDACT_SECRETS=true\nexport HERMES_QUIET=1\n",
            1,
        )
    elif malformed == "duplicate-export":
        data = original.replace(
            b"export HERMES_QUIET=1\n",
            b"export HERMES_QUIET=1\nexport HERMES_QUIET=1\n",
            1,
        )
    elif malformed == "home-export":
        data = original.replace(
            b"export HERMES_HOME=",
            b"export HOME=/tmp/forbidden\nexport HERMES_HOME=",
            1,
        )
    elif malformed == "expanded-hermes-home":
        line = next(
            line for line in original.splitlines()
            if line.startswith(b"export HERMES_HOME=")
        )
        data = original.replace(line, b"export HERMES_HOME=${HOME}/.hermes", 1)
    elif malformed == "dollar-hermes-home":
        line = next(
            line for line in original.splitlines()
            if line.startswith(b"export HERMES_HOME=")
        )
        data = original.replace(line, b"export HERMES_HOME=$HOME/.hermes", 1)
    elif malformed == "backtick-hermes-home":
        line = next(
            line for line in original.splitlines()
            if line.startswith(b"export HERMES_HOME=")
        )
        data = original.replace(line, b"export HERMES_HOME=`pwd`/.hermes", 1)
    elif malformed == "bad-bytecode-value":
        data = original.replace(
            b"export PYTHONDONTWRITEBYTECODE=1",
            b"export PYTHONDONTWRITEBYTECODE=true",
            1,
        )
    else:
        data = original.replace(b' "$@"\n', b' "$@" &\n')
    layout.wrapper.write_bytes(data)
    oracle = _oracle(layout.root)
    assert rollout.main(
        layout.prepare(
            layout.current_runtime, layout.current_sha, layout.candidate_sha,
            _hash(data), apply=True,
        )
    ) == 2
    capsys.readouterr()
    assert _oracle(layout.root) == oracle
    assert not layout.runtime(layout.candidate_sha).exists()
