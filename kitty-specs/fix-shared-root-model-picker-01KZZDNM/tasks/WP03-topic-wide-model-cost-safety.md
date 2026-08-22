---
work_package_id: WP03
title: Topic-wide model routing и cost-safe fallback
dependencies: [WP01]
requirement_refs: [FR-001, FR-002, NFR-001, NFR-002]
tracker_refs: [tm-ai-loopx-kimi-n1p]
planning_base_branch: codex/hermes-topic-routing-session-scope
merge_target_branch: codex/hermes-topic-routing-session-scope
subtasks: [T009, T010, T011, T012]
phase: Runtime verification
agent: codex
assignee: codex
authoritative_surface: tests/gateway/
execution_mode: code_change
owned_files:
- gateway/session.py
- gateway/run.py
- tests/gateway/
- kitty-specs/fix-shared-root-model-picker-01KZZDNM/
---

# WP03 — Topic-wide model routing и cost-safe fallback

## Цель

Сделать shared-room model preference общей внутри точной room/topic lane,
сохранить изоляцию между lanes и исключить неявный платный OpenRouter GLM.

## Проверка

1. RED/GREEN на canonical shared lane key и legacy sender-bound migration.
2. Runtime test: разные участники одной lane видят один override.
3. Cross-room/cross-topic отрицательная матрица.
4. Provider-resolution test для Codex default и fallback order.
5. Isolated candidate + HOSTKEY service/log canary.

## Реализация

- Shared allowlisted room/topic preference key больше не содержит sender identity.
- Legacy sender-bound rows всех настроенных участников выбираются по `updated_at`,
  повторно sanitизируются и атомарно переносятся в canonical lane.
- `room-drafts` и `room-research` получили явный Codex/Luna default и цепочку
  Codex → direct DeepSeek → free Kimi; автоматические auxiliary overrides
  OpenRouter заменены на `auto`.

## Evidence

- RED: 2/13 сценариев падали до production fix.
- GREEN: `tests/gateway/test_topic_preferences.py` — 13/13.
- Affected suites: topic preferences + shared picker + single-principal — 86/86.
- Ruff, `py_compile`, `git diff --check` — pass.
- Isolated candidate:
  `/home/openclaw/staging/hermes-topic-model-costsafe-037e84bf1-r7-20260822T083457Z`.
- HOSTKEY service: active/running, new PID `1070579`, `NRestarts=0`, Telegram
  connected and 60 commands registered.
- Candidate runtime resolver confirms both room profiles use
  Codex/Luna → direct DeepSeek → free Kimi; post-start log contains no
  OpenRouter/GLM/402 route.
