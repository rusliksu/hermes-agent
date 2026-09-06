# Hermes main divergence inventory - 2026-09-06

Scope: Mission `hermes-telegram-polling-observability-01M1NJGX`; Bead `hermes-tg-polling-obs-gy5` context only. No lifecycle update, fetch, checkout, reset, clean, tests, or broad audits.

## Safety ref

- Saved local `main`: `c23ea20b8d66632fc6816164fbe8d4d70cf80d86`
- Created/verified immutable safety ref: `refs/heads/codex/safety-hermes-main-20260906T1325` -> `c23ea20b8d66632fc6816164fbe8d4d70cf80d86`
- Verification: safety ref equals saved `main`; `refs/heads/main` remained `c23ea20b8d66632fc6816164fbe8d4d70cf80d86` after creation.

## Checkout and refs

- Current checkout branch: `codex/hermes-telegram-polling-planning-root`
- Current checkout HEAD: `a66343704b64bbd47237b4379439a54fce15f110`
- Status before/after inventory: branch `codex/hermes-telegram-polling-planning-root`; pre-existing modified file `kitty-specs/hermes-telegram-polling-observability-01M1NJGX/tasks/WP01-runtime-status-persistence-contract.md`
- Local `main`: `c23ea20b8d66632fc6816164fbe8d4d70cf80d86`
- Local tracking `origin/main`: `d9fb7fb49051815158791b2e7b50789fa51398fa`
- `git ls-remote --heads origin main`: `d9fb7fb49051815158791b2e7b50789fa51398fa refs/heads/main`
- Sanitized origin URL: `https://github.com/rusliksu/hermes-agent.git`

Worktrees:
- root: `codex/hermes-telegram-polling-planning-root` at `a66343704b64bbd47237b4379439a54fce15f110`
- coord: `kitty/mission-hermes-telegram-polling-observability-01M1NJGX` at `6c9205ff62783ecb922a84589d769c5f5bd5baa8`
- lane-a: `kitty/mission-hermes-telegram-polling-observability-01M1NJGX-lane-a` at `d25cc9c2fe085768b45bd91caf10788f86ba8532`
- lane-c: `kitty/mission-hermes-telegram-polling-observability-01M1NJGX-lane-c` at `d356c2e9cf674218328f6291ee9548c0ef33bb20`
- WP01/WP02/WP03 worktrees present at `9805549682a5a0dd8bd1d10203c4352b9f70c134`, `9cf577fd61eb64fe9960ab633293f875972c3c9b`, `f080ba79cd7ce6a0d043e0c8cce4f33d5e5ed9c3`

## Divergence

- `git rev-list --left-right --count origin/main...main`: `0 23`
- Merge-base: `d9fb7fb49051815158791b2e7b50789fa51398fa`
- Ancestry: `origin/main` is an ancestor of local `main`; local `main` is not an ancestor of `origin/main`.
- Local-only `main` tip: `c23ea20b8d66632fc6816164fbe8d4d70cf80d86 Add analysis report for mission hermes-telegram-polling-observability-01M1NJGX`
- Local-only range includes mission scaffold/spec/plan/tasks/status commits and WP03 status/review commits; `git diff --stat origin/main..main` reports 57 files changed, 3455 insertions; all are additions under `.kittify/` and `kitty-specs/hermes-telegram-polling-observability-01M1NJGX/`.

## Retention refs

- Refs containing local `main` tip `c23ea20b8...`: `refs/heads/main`, `refs/heads/codex/safety-hermes-main-20260906T1325`, coord branch, lane-a branch.
- Refs containing first local-only commit `32c3c99f...`: safety ref, WP01/WP02/WP03 branches, coord, lane-a, lane-c, and `main`.
- Specified branch heads: planning-root `a6634370...`; coord `6c9205f...`; lane-a `d25cc9c...`; lane-c `d356c2e...`.

## Main reflog

- `2026-09-06T14:35:02+02:00` -> `c23ea20b8...`; action/message blank, so actor/source not inferred.
- `2026-09-04T08:46:21+02:00` -> `d9fb7fb4...`; `reset: moving to d9fb7fb49051815158791b2e7b50789fa51398fa`
- `2026-09-04T08:45:37+02:00` -> `b7543010...`; `commit: bd: clear sync.remote`
- `2026-09-04T08:45:37+02:00` -> `d9fb7fb4...`; reset message as above.
- `2026-09-04T08:45:00+02:00` -> `ef414630...`; `commit: bd init: initialize beads issue tracking`
- `2026-09-04T08:40:18+02:00` -> `d9fb7fb4...`; `clone: from https://github.com/rusliksu/hermes-agent.git`

## Future recovery proposal only

With separate destructive/ref-mutation approval, `refs/heads/main` alone could be CAS-restored without checkout from old `c23ea20b8d66632fc6816164fbe8d4d70cf80d86` to new `d9fb7fb49051815158791b2e7b50789fa51398fa`, while `refs/heads/codex/safety-hermes-main-20260906T1325` preserves the current local-only commits. Proposed command only: `git update-ref refs/heads/main d9fb7fb49051815158791b2e7b50789fa51398fa c23ea20b8d66632fc6816164fbe8d4d70cf80d86`.
