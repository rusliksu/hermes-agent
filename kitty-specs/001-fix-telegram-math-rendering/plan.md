# Implementation plan

## Design

Extend the existing Telegram adapter eligibility gate. Reuse the existing
protected-region and display-math detection helpers; only the delivery gate
changes so a safe display-math payload can bypass the disabled global rich
flag. Do not enable rich drafts or alter legacy formatting.

Update the active tutor prompt at `/home/openclaw/.hermes/SOUL.md` with the
smallest policy clarification needed for complex/multiline formulas. Preserve
plain text for simple arithmetic and fenced blocks for LaTeX source.

## Verification

- Add/adjust adapter tests for opt-out display math, legacy invariants, and
  metadata preservation.
- Run `scripts/run_tests.sh` for the focused Telegram rich-message tests,
  relevant Telegram tests, `py_compile`, and `git diff --check`.
- Deploy a clean checkout from the implementation commit to a new HOSTKEY
  staging path, restart only `hermes-gateway`, and verify status plus health.

## Risks and rollback

The rich API is capability-gated and already has legacy fallback behavior.
Rollback is a service stop/repoint to the pre-change deployment and restoration
of the prompt backup; no tracked base checkout is modified.
