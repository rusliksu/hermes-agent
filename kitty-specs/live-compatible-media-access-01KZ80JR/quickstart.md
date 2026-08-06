# Quickstart: compatibility slice (staging only)

1. Use the task-owned live-derived branch `codex/live-compatible-media-cutover` and verify the base SHA matches the plan.
2. Run the contract, privacy and media policy tests with synthetic fixtures only.
3. Run the config policy dry-run; inspect only redacted role/profile/provider names and hashes.
4. Build an isolated staging candidate. Start gateway/dashboard on non-live ports and verify dashboard binds to loopback only.
5. Run canaries for Руслан(owner), every private family role label (the same capability set), each registered room and an unknown/malformed identity.
6. Probe guessed session IDs, memory keys, attachment paths, filesystem roots and provider secret sentinels pairwise; expect zero cross-scope observations.
7. Save redacted evidence, changed-file manifest and hashes. Rehearse rollback to the captured live code/config surfaces.
8. Stop. Applying live config, restarting `hermes-gateway`/`hermes-dashboard` or sending Telegram canaries needs a separate explicit approval.

Credential safety: do not open or print auth/token/secret/key/env files. Provider availability is tested with fake/sentinel resolvers or existing runtime health checks.
