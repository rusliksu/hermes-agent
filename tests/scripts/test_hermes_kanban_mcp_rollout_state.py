from __future__ import annotations

import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import hermes_kanban_mcp_rollout as rollout
from scripts import hermes_kanban_mcp_runtime_coherence as coherence
from scripts.hermes_kanban_mcp_rollout_common import RolloutError
from tests.scripts.hermes_kanban_mcp_test_support import (
    RolloutLayout,
    build_rollout_layout,
    filesystem_oracle,
    hash_bytes,
    install_trusted_bwrap_result,
)

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_V2_WRAPPER = (
    ROOT
    / "tests/scripts/fixtures/hermes_kanban_mcp_schema_v2_rollout/wrapper.before"
)
SCHEMA_V2_RUNTIME = (
    b"/__schema_v2_golden__/state/"
    b"hermes-kanban-mcp-6f8738dc308f909bf1735883344f2fcc12f3cbcd"
)
SCHEMA_V2_HOME = b"/__schema_v2_golden__/home"


def _header(root: Path) -> tuple[str, ...]:
    return (
        "#!/bin/bash",
        "set -euo pipefail",
        f"export HERMES_HOME={root}/hermes-home",
        "export HERMES_QUIET=1",
        "export HERMES_REDACT_SECRETS=true",
        "export PYTHONDONTWRITEBYTECODE=1",
    )


def _wrapper(tmp_path: Path) -> tuple[Path, Path, bytes]:
    runtime = tmp_path / "runtime"
    interpreter = runtime / "venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text(
        "#!/bin/bash\n"
        'printf "%s %s\\n" "$(ulimit -Sn)" "$(ulimit -Hn)" > "${!#}"\n',
        encoding="utf-8",
    )
    interpreter.chmod(0o755)
    data = coherence.canonical_rollout_wrapper(
        runtime, "venv", header=_header(tmp_path)
    )
    wrapper = tmp_path / "run.sh"
    wrapper.write_bytes(data)
    wrapper.chmod(0o755)
    return runtime, wrapper, data


def _run_wrapper_with_limits(
    wrapper: Path, marker: Path, *, soft: int, hard: int
) -> subprocess.CompletedProcess[str]:
    trampoline = (
        "import os, resource, sys\n"
        "resource.setrlimit(resource.RLIMIT_NOFILE, (int(sys.argv[1]), int(sys.argv[2])))\n"
        "os.execv(sys.argv[3], sys.argv[3:])\n"
    )
    return subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            trampoline,
            str(soft),
            str(hard),
            str(wrapper),
            str(marker),
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _install_schema_v2_wrapper(layout: RolloutLayout) -> tuple[bytes, bytes]:
    home = layout.root / "legacy-home"
    literal = SCHEMA_V2_WRAPPER.read_bytes()
    before = literal.replace(
        SCHEMA_V2_RUNTIME, str(layout.current_runtime).encode()
    ).replace(SCHEMA_V2_HOME, str(home).encode())
    assert before != literal
    (layout.current_runtime / ".venv").rename(layout.current_runtime / "venv")
    layout.stable_wrapper.write_bytes(before)
    layout.stable_wrapper.chmod(layout.wrapper_mode)
    layout.wrapper_before = before
    layout.wrapper_before_hash = hash_bytes(before)
    expected = coherence.canonical_rollout_wrapper(
        layout.candidate_path,
        "venv",
        header=(
            "#!/bin/bash",
            "set -euo pipefail",
            f"export HERMES_HOME={home}",
            "export HERMES_QUIET=1",
            "export HERMES_REDACT_SECRETS=true",
            "export PYTHONDONTWRITEBYTECODE=1",
        ),
    )
    return before, expected


def _schema_v2_prepare_args(
    layout: RolloutLayout, wrapper_after_sha256: str
) -> list[str]:
    arguments = layout.prepare_args(
        apply=True,
        wrapper_after_sha256=wrapper_after_sha256,
    )
    arguments[arguments.index("--venv-dirname") + 1] = "venv"
    return arguments


def test_literal_schema_v2_prepare_creates_canonical_and_exact_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    layout = build_rollout_layout(tmp_path)
    install_trusted_bwrap_result(monkeypatch, rollout, tmp_path)
    before, expected = _install_schema_v2_wrapper(layout)

    assert rollout.main(
        _schema_v2_prepare_args(layout, hash_bytes(expected))
    ) == 0
    capsys.readouterr()
    assert layout.stable_wrapper.read_bytes() == before
    assert stat.S_IMODE(layout.stable_wrapper.stat().st_mode) == layout.wrapper_mode
    assert (layout.snapshot_path / "wrapper.before").read_bytes() == before
    assert stat.S_IMODE(
        (layout.snapshot_path / "wrapper.before").stat().st_mode
    ) == 0o600
    assert (layout.snapshot_path / "wrapper.after").read_bytes() == expected
    assert expected.count(b"ulimit -S -n 4096\n") == 1

    assert rollout.main(
        layout.transition_args(
            "switch",
            hash_bytes(before),
            apply=True,
            wrapper_after_sha256=hash_bytes(expected),
        )
    ) == 0
    capsys.readouterr()
    assert layout.stable_wrapper.read_bytes() == expected
    assert rollout.main(
        layout.transition_args(
            "rollback",
            hash_bytes(expected),
            apply=True,
        )
    ) == 0
    capsys.readouterr()
    assert layout.stable_wrapper.read_bytes() == before
    assert stat.S_IMODE(layout.stable_wrapper.stat().st_mode) == layout.wrapper_mode


@pytest.mark.parametrize(
    "mutation",
    ("env-shebang", "missing-separator", "forward-argv"),
)
def test_literal_schema_v2_neighbor_is_rejected_before_mutation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    mutation: str,
) -> None:
    layout = build_rollout_layout(tmp_path)
    before, expected = _install_schema_v2_wrapper(layout)
    if mutation == "env-shebang":
        malformed = before.replace(b"#!/usr/bin/env bash", b"#!/bin/bash", 1)
    elif mutation == "missing-separator":
        malformed = before.replace(b"set -euo pipefail\n\n", b"set -euo pipefail\n", 1)
    else:
        malformed = before.replace(b"--allow-write\n", b'--allow-write "$@"\n', 1)
    layout.stable_wrapper.write_bytes(malformed)
    layout.stable_wrapper.chmod(layout.wrapper_mode)
    layout.wrapper_before_hash = hash_bytes(malformed)
    oracle = filesystem_oracle(layout.root)

    assert rollout.main(
        _schema_v2_prepare_args(layout, hash_bytes(expected))
    ) == 2
    failure = capsys.readouterr().err
    assert "schema v2 wrapper does not match an accepted exact template" in failure
    assert filesystem_oracle(layout.root) == oracle
    assert not layout.candidate_path.exists()
    assert not layout.snapshot_path.exists()


def test_generated_nofile_wrapper_sets_soft_limit_without_raising_hard(
    tmp_path: Path,
) -> None:
    runtime, wrapper, data = _wrapper(tmp_path)
    lines = data.decode().splitlines()
    assert coherence.WRAPPER_CONTRACT == "source-cwd-nofile-v2"
    assert lines.count("ulimit -S -n 4096") == 1
    assert lines.index("ulimit -S -n 4096") == len(_header(tmp_path))
    assert lines[-2] == f"cd -- {runtime}"
    assert lines[-1].startswith(f"exec {runtime}/venv/bin/python ")
    grammar = coherence.parse_rollout_wrapper(
        data,
        runtime,
        "venv",
        expected_contract=coherence.WRAPPER_CONTRACT,
    )
    assert grammar.contract == coherence.WRAPPER_CONTRACT

    marker = tmp_path / "limits.txt"
    controlled_hard = 8192
    completed = _run_wrapper_with_limits(
        wrapper,
        marker,
        soft=controlled_hard,
        hard=controlled_hard,
    )
    assert completed.returncode == 0
    assert marker.read_text(encoding="utf-8").split() == [
        "4096",
        str(controlled_hard),
    ]


def test_hard_limit_below_4096_stops_before_fake_python(tmp_path: Path) -> None:
    _runtime, wrapper, _data = _wrapper(tmp_path)
    marker = tmp_path / "must-not-exist"
    low_hard = 4095
    completed = _run_wrapper_with_limits(
        wrapper,
        marker,
        soft=low_hard,
        hard=low_hard,
    )
    assert completed.returncode != 0
    assert not marker.exists()


@pytest.mark.parametrize(
    "mutation",
    ("missing", "malformed", "duplicate", "wrong", "unlimited", "misplaced"),
)
def test_nofile_parser_rejects_noncanonical_limit(
    tmp_path: Path, mutation: str
) -> None:
    runtime, _wrapper_path, valid = _wrapper(tmp_path)
    line = b"ulimit -S -n 4096\n"
    if mutation == "missing":
        invalid = valid.replace(line, b"")
    elif mutation == "malformed":
        invalid = valid.replace(line, b"ulimit -n 4096\n")
    elif mutation == "duplicate":
        invalid = valid.replace(line, line * 2)
    elif mutation == "wrong":
        invalid = valid.replace(line, b"ulimit -S -n 4095\n")
    elif mutation == "unlimited":
        invalid = valid.replace(line, b"ulimit -S -n unlimited\n")
    else:
        cd = f"cd -- {runtime}\n".encode()
        invalid = valid.replace(line, b"").replace(cd, cd + line)
    with pytest.raises(RolloutError):
        coherence.parse_rollout_wrapper(
            invalid,
            runtime,
            "venv",
            expected_contract=coherence.WRAPPER_CONTRACT,
        )


def test_prepare_dry_run_reports_nofile_kind_limit_hash_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    layout = build_rollout_layout(tmp_path)
    before = filesystem_oracle(layout.root)
    monkeypatch.setattr(rollout.coherence, "NOFILE_SOFT_LIMIT", 17)
    assert rollout.main(layout.prepare_args()) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["wrapper_contract"] == "source-cwd-nofile-v2"
    assert plan["planned_soft_nofile"] == 4096
    assert "plan_digest" not in plan
    assert plan["wrapper_after_sha256"] == rollout._sha256(
        rollout.coherence.rewrite_rollout_wrapper(
            layout.wrapper_before,
            layout.current_runtime,
            layout.candidate_path,
            ".venv",
        )
    )
    assert filesystem_oracle(layout.root) == before


def test_switch_apply_rejects_mismatched_approved_after_hash_before_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    layout = build_rollout_layout(tmp_path)
    install_trusted_bwrap_result(monkeypatch, rollout, tmp_path)
    assert rollout.main(layout.prepare_args(apply=True)) == 0
    capsys.readouterr()

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("mismatched approved hash reached preflight or replace")

    monkeypatch.setattr(rollout.coherence, "import_preflight_session", forbidden)
    monkeypatch.setattr(rollout.state, "_atomic_replace", forbidden)
    with pytest.raises(RolloutError, match="wrapper.after SHA-256"):
        rollout.state.run_transition(
            command="switch",
            runtime_root=str(layout.runtime_root),
            state_root=str(layout.state_root),
            snapshot_id=layout.snapshot_id,
            stable_wrapper=str(layout.stable_wrapper),
            expected_current_wrapper_sha256=layout.wrapper_before_hash,
            expected_wrapper_after_sha256="0" * 64,
            apply=True,
        )


def test_source_cwd_v1_snapshot_is_rollback_only_and_byte_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    layout = build_rollout_layout(tmp_path)
    install_trusted_bwrap_result(monkeypatch, rollout, tmp_path)
    assert rollout.main(layout.prepare_args(apply=True)) == 0
    capsys.readouterr()
    before_switch = filesystem_oracle(layout.root)
    assert rollout.main(
        layout.transition_args("switch", layout.wrapper_before_hash)
    ) == 0
    switch_plan = json.loads(capsys.readouterr().out)
    assert switch_plan["wrapper_contract"] == "source-cwd-nofile-v2"
    assert switch_plan["planned_soft_nofile"] == 4096
    assert switch_plan["wrapper_after_sha256"] == hash_bytes(
        (layout.snapshot_path / "wrapper.after").read_bytes()
    )
    assert filesystem_oracle(layout.root) == before_switch
    manifest_path = layout.snapshot_path / "manifest.json"
    after_path = layout.snapshot_path / "wrapper.after"
    old_after = after_path.read_bytes().replace(b"ulimit -S -n 4096\n", b"")
    after_path.write_bytes(old_after)
    after_path.chmod(0o600)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "planned_soft_nofile" not in manifest
    assert "plan_digest" not in manifest
    manifest["wrapper_contract"] = "source-cwd-v1"
    manifest["wrapper_after_sha256"] = hash_bytes(old_after)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest_path.chmod(0o600)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("rollback-only snapshot reached preflight or replace")

    with monkeypatch.context() as switch_guards:
        switch_guards.setattr(
            rollout.coherence, "import_preflight_session", forbidden
        )
        switch_guards.setattr(rollout.state, "_atomic_replace", forbidden)
        assert rollout.main(
            layout.transition_args("switch", layout.wrapper_before_hash)
        ) == 2
    capsys.readouterr()
    layout.stable_wrapper.write_bytes(old_after)
    layout.stable_wrapper.chmod(layout.wrapper_mode)
    assert rollout.main(
        layout.transition_args("rollback", hash_bytes(old_after), apply=True)
    ) == 0
    capsys.readouterr()
    assert layout.stable_wrapper.read_bytes() == layout.wrapper_before
    assert stat.S_IMODE(layout.stable_wrapper.stat().st_mode) == layout.wrapper_mode
