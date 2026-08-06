# Research: live-derived compatibility slice

## Evidence captured 2026-08-05

- Current live checkout is `/home/openclaw/staging/hermes-deploy-settings-i18n-20260803`, branch `codex/live-gurra-settings-localization-candidate`, HEAD `a4096896ed92d1edb3dd02e62876dc0fc1ce140a`.
- Live services use the staging checkout for `hermes-gateway` and a separate installed dashboard venv. Both were healthy before planning; this mission has not changed config, restarted services or read credentials.
- Live already contains `gateway/profile_routing.py`, `gateway/single_principal.py`, `gateway/session_context.py`, profile helpers, media caches, image/STT/TTS registries and privacy-oriented tests.
- The existing profile route resolver returns `None` on no match and `_resolve_profile_home_for_source` can fall back to the active/global home. That is the exact owner/default fallback the new gate must eliminate for unknown or malformed identities.
- The experimental implementation branch contains a new access registry and media provider router but is 16,273 commits ahead of live. A full branch transplant would include unrelated behavior and deployment drift, so only the bounded contract/facade concepts should be ported.
- Existing provider implementations already cover local/cloud STT, plugin image generation and multiple TTS backends. The compatibility slice should add policy and scoping rather than another provider SDK.

## Decision record

1. Use a typed in-process resolver instead of OPA/OpenFGA for the current 10-principal/two-room scale.
2. Keep one Telegram bot; use confirmed transport identities, never username/display name.
3. Treat a missing route/profile, identity mismatch or malformed policy as deny-before-model/session/tools.
4. Keep media provider order server-configured and capability-scoped; provider secrets remain opaque references.
5. Build staging from live HEAD and require separate live apply/restart approval.

## Research gaps intentionally deferred

- Exact current family and room bindings must be read from server-side metadata during an approved implementation/canary step, with IDs redacted in evidence.
- Provider availability and Codex OAuth credential plumbing will be verified through existing runtime interfaces, not by inspecting auth files.
- Dashboard route names are confirmed during implementation against the existing localhost/SSH-authenticated dashboard surface.
