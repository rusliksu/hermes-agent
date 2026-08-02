#!/usr/bin/env python3
"""Fail-closed, dry-run-first rollout helper for standalone Kanban MCP."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

try:
    from scripts import hermes_kanban_mcp_runtime_coherence as coherence
    from scripts import hermes_kanban_mcp_rollout_state as state
    from scripts.hermes_kanban_mcp_rollout_common import (
        FULL_GIT_SHA,
        FULL_SHA256,
        RolloutError,
        Wrapper,
        canonical_path as _canonical_path,
        lexists as _lexists,
        read_wrapper as _read_wrapper,
        require_full_sha as _require_full_sha,
        run_git as _run_git,
        sha256 as _sha256,
        strictly_within as _strictly_within,
        validate_absent_state_root as _validate_absent_state_root,
        validate_clean_worktree as _validate_clean_worktree,
        validate_commit as _validate_commit,
        validate_managed_root as _validate_managed_root,
        validate_repo_root as _validate_repo_root,
        validate_roots as _validate_roots,
        validate_venv as _validate_venv,
        validate_venv_startup as _validate_venv_startup,
    )
except ModuleNotFoundError:
    import hermes_kanban_mcp_runtime_coherence as coherence
    import hermes_kanban_mcp_rollout_state as state
    from hermes_kanban_mcp_rollout_common import (
        FULL_GIT_SHA,
        FULL_SHA256,
        RolloutError,
        Wrapper,
        canonical_path as _canonical_path,
        lexists as _lexists,
        read_wrapper as _read_wrapper,
        require_full_sha as _require_full_sha,
        run_git as _run_git,
        sha256 as _sha256,
        strictly_within as _strictly_within,
        validate_absent_state_root as _validate_absent_state_root,
        validate_clean_worktree as _validate_clean_worktree,
        validate_commit as _validate_commit,
        validate_managed_root as _validate_managed_root,
        validate_repo_root as _validate_repo_root,
        validate_roots as _validate_roots,
        validate_venv as _validate_venv,
        validate_venv_startup as _validate_venv_startup,
    )


ReplacementAppliedError = state.ReplacementAppliedError
_rewrite_rollout_wrapper = coherence.rewrite_rollout_wrapper
_write_snapshot = state.write_snapshot


@dataclass(frozen=True)
class PrepareContext:
    source_repo: Path
    runtime_root: Path
    state_root: Path
    current_runtime: Path
    current_sha: str
    candidate_sha: str
    candidate_path: Path
    snapshot_id: str
    snapshot_path: Path
    venv_dirname: str
    interpreter_sha256: str
    interpreter_mode: int
    pyvenv_cfg_sha256: str
    wrapper: Wrapper
    wrapper_after: bytes
    wrapper_after_sha256: str
    replacement_count: int


@dataclass(frozen=True)
class BootstrapContext:
    source_repo: Path
    state_root: Path
    export_runtime: Path
    export_manifest: state.ExportManifest
    source_commit: str
    baseline_path: Path
    snapshot_id: str
    snapshot_path: Path
    venv_dirname: str
    interpreter_sha256: str
    interpreter_mode: int
    wrapper: Wrapper
    wrapper_after: bytes
    wrapper_after_sha256: str


def _derived_runtime(root: Path, sha: str) -> Path:
    return root / f"hermes-kanban-mcp-{sha}"


def _derived_snapshot(state_root: Path, snapshot_id: str) -> Path:
    return state_root / "snapshots" / snapshot_id


def _validate_new_runtime_and_snapshot(
    runtime_root: Path,
    state_root: Path,
    runtime_path: Path,
    snapshot_path: Path,
) -> None:
    _strictly_within(runtime_path, runtime_root, "candidate path")
    _strictly_within(snapshot_path, state_root, "snapshot path")
    if _lexists(runtime_path):
        raise RolloutError(f"candidate path already exists: {runtime_path}")
    if _lexists(snapshot_path):
        raise RolloutError(f"snapshot path already exists: {snapshot_path}")
    snapshots_root = snapshot_path.parent
    if _lexists(snapshots_root) and (
        snapshots_root.is_symlink() or not snapshots_root.is_dir()
    ):
        raise RolloutError("snapshots root exists but is not a directory")


def _prepare_context(args: argparse.Namespace) -> PrepareContext:
    source_repo = _canonical_path(args.source_repo, "source repo", must_exist=True)
    runtime_root = _canonical_path(args.runtime_root, "runtime root", must_exist=True)
    state_root = _canonical_path(args.state_root, "state root", must_exist=True)
    current_runtime = _canonical_path(
        args.current_runtime, "current runtime", must_exist=True
    )
    stable_path = _canonical_path(args.stable_wrapper, "stable wrapper", must_exist=True)
    current_sha = _require_full_sha(
        args.expected_current_runtime_sha,
        FULL_GIT_SHA,
        "expected current runtime SHA",
    )
    candidate_sha = _require_full_sha(
        args.candidate_sha, FULL_GIT_SHA, "candidate SHA"
    )
    if current_sha == candidate_sha:
        raise RolloutError("candidate SHA must differ from current runtime SHA")
    expected_wrapper_hash = _require_full_sha(
        args.expected_current_wrapper_sha256,
        FULL_SHA256,
        "expected current wrapper SHA-256",
    )

    _validate_roots(runtime_root, state_root)
    _validate_repo_root(source_repo, "source repo")
    if source_repo in {runtime_root, state_root}:
        raise RolloutError("managed roots must not be the source repo root")
    _strictly_within(current_runtime, runtime_root, "current runtime")
    if current_runtime != _derived_runtime(runtime_root, current_sha):
        raise RolloutError("current runtime is not the exact derived path")
    _validate_clean_worktree(current_runtime, current_sha, "current runtime")
    _validate_commit(source_repo, candidate_sha, "candidate SHA")
    interpreter_hash, interpreter_mode = _validate_venv(
        current_runtime, args.venv_dirname
    )
    _pyvenv_cfg, pyvenv_cfg_sha256, _site_packages = _validate_venv_startup(
        current_runtime, args.venv_dirname
    )

    candidate_path = _canonical_path(
        str(_derived_runtime(runtime_root, candidate_sha)),
        "candidate path",
        must_exist=False,
    )
    snapshot_id = f"{current_sha}-to-{candidate_sha}"
    snapshot_path = _canonical_path(
        str(_derived_snapshot(state_root, snapshot_id)),
        "snapshot path",
        must_exist=False,
    )
    _validate_new_runtime_and_snapshot(
        runtime_root, state_root, candidate_path, snapshot_path
    )
    wrapper = _read_wrapper(stable_path)
    if wrapper.sha256 != expected_wrapper_hash:
        raise RolloutError("stable wrapper SHA-256 does not match the explicit guard")
    wrapper_after = _rewrite_rollout_wrapper(
        wrapper.data, current_runtime, candidate_path, args.venv_dirname
    )
    replacement_count = wrapper.data.decode("utf-8").count(str(current_runtime))
    return PrepareContext(
        source_repo=source_repo,
        runtime_root=runtime_root,
        state_root=state_root,
        current_runtime=current_runtime,
        current_sha=current_sha,
        candidate_sha=candidate_sha,
        candidate_path=candidate_path,
        snapshot_id=snapshot_id,
        snapshot_path=snapshot_path,
        venv_dirname=args.venv_dirname,
        interpreter_sha256=interpreter_hash,
        interpreter_mode=interpreter_mode,
        pyvenv_cfg_sha256=pyvenv_cfg_sha256,
        wrapper=wrapper,
        wrapper_after=wrapper_after,
        wrapper_after_sha256=_sha256(wrapper_after),
        replacement_count=replacement_count,
    )


def _bootstrap_context(args: argparse.Namespace) -> BootstrapContext:
    source_repo = _canonical_path(args.source_repo, "source repo", must_exist=True)
    state_root = _canonical_path(args.state_root, "state root", must_exist=False)
    export_runtime = _canonical_path(
        args.export_runtime, "export runtime", must_exist=True
    )
    export_manifest_path = _canonical_path(
        args.export_manifest, "export manifest", must_exist=True
    )
    stable_path = _canonical_path(args.stable_wrapper, "stable wrapper", must_exist=True)
    source_commit = _require_full_sha(
        args.expected_source_commit,
        FULL_GIT_SHA,
        "expected source commit",
    )
    expected_interpreter_hash = _require_full_sha(
        args.expected_venv_interpreter_sha256,
        FULL_SHA256,
        "expected venv interpreter SHA-256",
    )
    expected_wrapper_hash = _require_full_sha(
        args.expected_current_wrapper_sha256,
        FULL_SHA256,
        "expected current wrapper SHA-256",
    )
    expected_manifest_hash = args.expected_export_manifest_sha256
    if args.apply and expected_manifest_hash is None:
        raise RolloutError(
            "bootstrap apply requires expected export manifest SHA-256"
        )
    if expected_manifest_hash is not None:
        expected_manifest_hash = _require_full_sha(
            expected_manifest_hash,
            FULL_SHA256,
            "expected export manifest SHA-256",
        )

    _validate_absent_state_root(state_root)
    _validate_managed_root(export_runtime, "export runtime")
    _validate_repo_root(source_repo, "source repo")
    if state_root.is_relative_to(export_runtime) or export_runtime.is_relative_to(
        state_root
    ):
        raise RolloutError("state root and export runtime must not be nested")
    if state_root.is_relative_to(source_repo):
        raise RolloutError("state root must not be inside the source repo")
    _validate_commit(source_repo, source_commit, "source commit")
    export_manifest = state.read_export_manifest(
        export_manifest_path,
        export_runtime,
        source_commit,
        expected_manifest_hash,
    )
    interpreter_hash, interpreter_mode = _validate_venv(
        export_runtime,
        args.venv_dirname,
        expected_sha256=expected_interpreter_hash,
    )
    baseline_path = _canonical_path(
        str(_derived_runtime(state_root, source_commit)),
        "baseline path",
        must_exist=False,
    )
    snapshot_id = f"bootstrap-{source_commit}"
    snapshot_path = _canonical_path(
        str(_derived_snapshot(state_root, snapshot_id)),
        "snapshot path",
        must_exist=False,
    )
    _validate_new_runtime_and_snapshot(
        state_root, state_root, baseline_path, snapshot_path
    )
    wrapper = _read_wrapper(stable_path)
    if wrapper.sha256 != expected_wrapper_hash:
        raise RolloutError("stable wrapper SHA-256 does not match the explicit guard")
    wrapper_after = _rewrite_rollout_wrapper(
        wrapper.data,
        export_runtime,
        baseline_path,
        args.venv_dirname,
    )
    return BootstrapContext(
        source_repo=source_repo,
        state_root=state_root,
        export_runtime=export_runtime,
        export_manifest=export_manifest,
        source_commit=source_commit,
        baseline_path=baseline_path,
        snapshot_id=snapshot_id,
        snapshot_path=snapshot_path,
        venv_dirname=args.venv_dirname,
        interpreter_sha256=interpreter_hash,
        interpreter_mode=interpreter_mode,
        wrapper=wrapper,
        wrapper_after=wrapper_after,
        wrapper_after_sha256=_sha256(wrapper_after),
    )


def _prepare_manifest(
    context: PrepareContext, import_evidence: state.ImportEvidence
) -> dict[str, Any]:
    return state.make_manifest(
        snapshot_kind="rollout",
        source_repo=context.source_repo,
        runtime_root=context.runtime_root,
        state_root=context.state_root,
        snapshot_id=context.snapshot_id,
        stable_wrapper=context.wrapper.path,
        before_runtime_kind="git",
        before_runtime_path=context.current_runtime,
        before_runtime_sha=context.current_sha,
        before_manifest_path=None,
        before_manifest_sha256=None,
        after_runtime_path=context.candidate_path,
        after_runtime_sha=context.candidate_sha,
        venv_dirname=context.venv_dirname,
        venv_interpreter_sha256=context.interpreter_sha256,
        venv_interpreter_mode=context.interpreter_mode,
        wrapper_before_sha256=context.wrapper.sha256,
        wrapper_after_sha256=context.wrapper_after_sha256,
        wrapper_mode=context.wrapper.mode,
        runtime_path_replacements=context.replacement_count,
        schema_version=3,
        import_evidence=import_evidence,
    )


def _bootstrap_manifest(
    context: BootstrapContext, import_evidence: state.ImportEvidence
) -> dict[str, Any]:
    return state.make_manifest(
        snapshot_kind="bootstrap",
        source_repo=context.source_repo,
        runtime_root=context.state_root,
        state_root=context.state_root,
        snapshot_id=context.snapshot_id,
        stable_wrapper=context.wrapper.path,
        before_runtime_kind="export",
        before_runtime_path=context.export_runtime,
        before_runtime_sha=context.source_commit,
        before_manifest_path=context.export_manifest.path,
        before_manifest_sha256=context.export_manifest.sha256,
        after_runtime_path=context.baseline_path,
        after_runtime_sha=context.source_commit,
        venv_dirname=context.venv_dirname,
        venv_interpreter_sha256=context.interpreter_sha256,
        venv_interpreter_mode=context.interpreter_mode,
        wrapper_before_sha256=context.wrapper.sha256,
        wrapper_after_sha256=context.wrapper_after_sha256,
        wrapper_mode=context.wrapper.mode,
        runtime_path_replacements=1,
        schema_version=3,
        import_evidence=import_evidence,
    )


def _prepare_plan(context: PrepareContext, apply: bool) -> dict[str, Any]:
    return {
        "command": "prepare",
        "mode": "apply" if apply else "dry-run",
        "candidate_path": str(context.candidate_path),
        "snapshot_id": context.snapshot_id,
        "snapshot_path": str(context.snapshot_path),
        "stable_wrapper": str(context.wrapper.path),
        "wrapper_before_sha256": context.wrapper.sha256,
        "wrapper_after_sha256": context.wrapper_after_sha256,
        "wrapper_contract": coherence.WRAPPER_CONTRACT,
        "planned_soft_nofile": state.parsed_soft_nofile(
            context.wrapper_after,
            context.candidate_path,
            context.venv_dirname,
        ),
        "operations": [
            "validate exact Git SHA, tracked cleanliness, paths, venv and wrapper",
            f"git worktree add --detach {context.candidate_path} {context.candidate_sha}",
            f"copy only {context.current_runtime / context.venv_dirname} to candidate",
            "run sanitized no-DB target import-origin preflight",
            "create exclusive schema v3 rollout snapshot",
            "leave stable wrapper unchanged",
        ],
    }


def _bootstrap_plan(context: BootstrapContext, apply: bool) -> dict[str, Any]:
    return {
        "command": "bootstrap-prepare",
        "mode": "apply" if apply else "dry-run",
        "runtime_root": str(context.state_root),
        "state_root": str(context.state_root),
        "baseline_path": str(context.baseline_path),
        "snapshot_id": context.snapshot_id,
        "snapshot_path": str(context.snapshot_path),
        "export_manifest": str(context.export_manifest.path),
        "observed_export_manifest_sha256": context.export_manifest.sha256,
        "source_commit": context.source_commit,
        "stable_wrapper": str(context.wrapper.path),
        "wrapper_before_sha256": context.wrapper.sha256,
        "wrapper_after_sha256": context.wrapper_after_sha256,
        "wrapper_contract": coherence.WRAPPER_CONTRACT,
        "planned_soft_nofile": state.parsed_soft_nofile(
            context.wrapper_after,
            context.baseline_path,
            context.venv_dirname,
        ),
        "operations": [
            "validate absent state root, export manifest, venv and wrapper evidence",
            f"create exact state root {context.state_root} mode 0700",
            f"git worktree add --detach {context.baseline_path} {context.source_commit}",
            f"copy only {context.export_runtime / context.venv_dirname} to baseline",
            "run sanitized no-DB target import-origin preflight",
            "create exclusive schema v3 bootstrap snapshot",
            "leave export runtime and stable wrapper unchanged",
        ],
    }


def _require_approved_wrapper_after(
    args: argparse.Namespace, observed_sha256: str
) -> None:
    if not args.apply:
        return
    expected = getattr(args, "expected_wrapper_after_sha256", None)
    if expected is None:
        raise RolloutError(f"{args.command} apply requires expected wrapper.after SHA-256")
    approved = _require_full_sha(
        expected,
        FULL_SHA256,
        "expected wrapper.after SHA-256",
    )
    if approved != observed_sha256:
        raise RolloutError("expected wrapper.after SHA-256 does not match generated bytes")


def _create_candidate(
    source_repo: Path, runtime_path: Path, sha: str, label: str
) -> None:
    _run_git(source_repo, ["worktree", "add", "--detach", str(runtime_path), sha])
    _validate_clean_worktree(runtime_path, sha, label)


def _copy_venv(
    source_runtime: Path,
    destination_runtime: Path,
    dirname: str,
    interpreter_sha256: str,
    interpreter_mode: int,
    pyvenv_cfg_sha256: str | None = None,
) -> None:
    source = source_runtime / dirname
    destination = destination_runtime / dirname
    if _lexists(destination):
        raise RolloutError("candidate already contains the selected venv path")
    try:
        shutil.copytree(source, destination, symlinks=True)
    except OSError as exc:
        raise RolloutError(f"cannot copy selected venv: {exc}") from exc
    _validate_venv(
        destination_runtime,
        dirname,
        expected_sha256=interpreter_sha256,
        expected_mode=interpreter_mode,
    )
    if pyvenv_cfg_sha256 is not None:
        _validate_venv_startup(
            destination_runtime,
            dirname,
            expected_pyvenv_cfg_sha256=pyvenv_cfg_sha256,
        )


def _create_state_root(path: Path) -> None:
    _validate_absent_state_root(path)
    try:
        os.mkdir(path, 0o700)
        os.chmod(path, 0o700)
    except OSError as exc:
        raise RolloutError(f"cannot create exclusive state root: {exc}") from exc
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise RolloutError("created state root does not have mode 0700")


def _load_created_snapshot(
    runtime_root: Path, state_root: Path, snapshot_id: str, wrapper: Wrapper
) -> state.SnapshotContext:
    return state.load_snapshot_context(
        runtime_root_raw=str(runtime_root),
        state_root_raw=str(state_root),
        snapshot_id=snapshot_id,
        stable_wrapper_raw=str(wrapper.path),
    )


def _run_prepare(args: argparse.Namespace) -> dict[str, Any]:
    context = _prepare_context(args)
    _require_approved_wrapper_after(args, context.wrapper_after_sha256)
    plan = _prepare_plan(context, args.apply)
    if not args.apply:
        return plan
    _create_candidate(
        context.source_repo,
        context.candidate_path,
        context.candidate_sha,
        "candidate runtime",
    )
    _copy_venv(
        context.current_runtime,
        context.candidate_path,
        context.venv_dirname,
        context.interpreter_sha256,
        context.interpreter_mode,
        context.pyvenv_cfg_sha256,
    )
    _validate_clean_worktree(
        context.candidate_path, context.candidate_sha, "candidate runtime"
    )
    with coherence.import_preflight_session(
        context.candidate_path,
        context.venv_dirname,
        expected_pyvenv_cfg_sha256=context.pyvenv_cfg_sha256,
    ) as import_evidence:
        plan["import_origin"] = import_evidence.manifest_fields()
        _write_snapshot(
            context.snapshot_path,
            _prepare_manifest(context, import_evidence),
            context.wrapper.data,
            context.wrapper_after,
        )
        loaded = _load_created_snapshot(
            context.runtime_root, context.state_root, context.snapshot_id, context.wrapper
        )
        if (
            loaded.stable_wrapper.sha256 != context.wrapper.sha256
            or loaded.stable_wrapper.mode != context.wrapper.mode
        ):
            raise RolloutError("prepare changed the stable wrapper")
    plan["result"] = "prepared"
    return plan


def _run_bootstrap_prepare(args: argparse.Namespace) -> dict[str, Any]:
    context = _bootstrap_context(args)
    _require_approved_wrapper_after(args, context.wrapper_after_sha256)
    plan = _bootstrap_plan(context, args.apply)
    if not args.apply:
        return plan
    _create_state_root(context.state_root)
    _create_candidate(
        context.source_repo,
        context.baseline_path,
        context.source_commit,
        "baseline runtime",
    )
    _copy_venv(
        context.export_runtime,
        context.baseline_path,
        context.venv_dirname,
        context.interpreter_sha256,
        context.interpreter_mode,
    )
    _validate_clean_worktree(
        context.baseline_path, context.source_commit, "baseline runtime"
    )
    with coherence.import_preflight_session(
        context.baseline_path,
        context.venv_dirname,
    ) as import_evidence:
        plan["import_origin"] = import_evidence.manifest_fields()
        _write_snapshot(
            context.snapshot_path,
            _bootstrap_manifest(context, import_evidence),
            context.wrapper.data,
            context.wrapper_after,
        )
        loaded = _load_created_snapshot(
            context.state_root,
            context.state_root,
            context.snapshot_id,
            context.wrapper,
        )
        if (
            loaded.stable_wrapper.sha256 != context.wrapper.sha256
            or loaded.stable_wrapper.mode != context.wrapper.mode
        ):
            raise RolloutError("bootstrap prepare changed the stable wrapper")
    plan["result"] = "prepared"
    return plan


def _run_switch_or_rollback(args: argparse.Namespace) -> dict[str, Any]:
    return state.run_transition(
        command=args.command,
        runtime_root=args.runtime_root,
        state_root=args.state_root,
        snapshot_id=args.snapshot_id,
        stable_wrapper=args.stable_wrapper,
        expected_current_wrapper_sha256=args.expected_current_wrapper_sha256,
        apply=args.apply,
        expected_wrapper_after_sha256=getattr(
            args, "expected_wrapper_after_sha256", None
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    bootstrap = commands.add_parser(
        "bootstrap-prepare", help="plan or prepare export-to-Git baseline"
    )
    bootstrap.add_argument("--source-repo", required=True)
    bootstrap.add_argument("--state-root", required=True)
    bootstrap.add_argument("--export-runtime", required=True)
    bootstrap.add_argument("--export-manifest", required=True)
    bootstrap.add_argument("--expected-export-manifest-sha256")
    bootstrap.add_argument("--expected-source-commit", required=True)
    bootstrap.add_argument("--venv-dirname", choices=(".venv", "venv"), required=True)
    bootstrap.add_argument("--expected-venv-interpreter-sha256", required=True)
    bootstrap.add_argument("--stable-wrapper", required=True)
    bootstrap.add_argument("--expected-current-wrapper-sha256", required=True)
    bootstrap.add_argument("--expected-wrapper-after-sha256")
    bootstrap.add_argument("--apply", action="store_true")

    prepare = commands.add_parser("prepare", help="plan or prepare candidate and snapshot")
    prepare.add_argument("--source-repo", required=True)
    prepare.add_argument("--runtime-root", required=True)
    prepare.add_argument("--state-root", required=True)
    prepare.add_argument("--current-runtime", required=True)
    prepare.add_argument("--expected-current-runtime-sha", required=True)
    prepare.add_argument("--candidate-sha", required=True)
    prepare.add_argument("--venv-dirname", choices=(".venv", "venv"), required=True)
    prepare.add_argument("--stable-wrapper", required=True)
    prepare.add_argument("--expected-current-wrapper-sha256", required=True)
    prepare.add_argument("--expected-wrapper-after-sha256")
    prepare.add_argument("--apply", action="store_true")

    for name in ("switch", "rollback"):
        command = commands.add_parser(name, help=f"plan or apply {name}")
        command.add_argument("--runtime-root", required=True)
        command.add_argument("--state-root", required=True)
        command.add_argument("--snapshot-id", required=True)
        command.add_argument("--stable-wrapper", required=True)
        command.add_argument("--expected-current-wrapper-sha256", required=True)
        if name == "switch":
            command.add_argument("--expected-wrapper-after-sha256")
        command.add_argument("--apply", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "bootstrap-prepare":
            plan = _run_bootstrap_prepare(args)
        elif args.command == "prepare":
            plan = _run_prepare(args)
        else:
            plan = _run_switch_or_rollback(args)
    except ReplacementAppliedError as exc:
        failure = {
            "error": str(exc),
            "expected_installed_sha256": exc.expected_sha256,
            "replacement_applied": True,
            "required_action": "inspect/rollback",
        }
        if exc.primary_failure is not None:
            failure["primary_failure"] = str(exc.primary_failure)
        if exc.secondary_failures:
            failure["secondary_failures"] = list(exc.secondary_failures)
        if exc.cleanup_failures:
            failure["cleanup_failures"] = list(exc.cleanup_failures)
        print(json.dumps(failure, sort_keys=True), file=sys.stderr)
        return 2
    except RolloutError as exc:
        if exc.primary_failure is not None or exc.secondary_failures or exc.cleanup_failures:
            failure = {
                "error": str(exc),
                "replacement_applied": exc.replacement_applied,
            }
            if exc.primary_failure is not None:
                failure["primary_failure"] = str(exc.primary_failure)
            if exc.secondary_failures:
                failure["secondary_failures"] = list(exc.secondary_failures)
            if exc.cleanup_failures:
                failure["cleanup_failures"] = list(exc.cleanup_failures)
            print(
                json.dumps(failure, sort_keys=True),
                file=sys.stderr,
            )
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
