# Задачи: `fix-family-profile-terminal-config-hjr01`

Bead: `HERMES-jir`

## WP01 — Минимальный hotfix

- [x] T001 Получить initial approval на implementation.
- [x] T002 В `agent/runtime_cwd.py` трактовать только отсутствующий per-profile `config.yaml` как пустую terminal config.
- [x] T003 Добавить ровно один regression test в `tests/agent/test_runtime_cwd.py`.
- [x] T004 Запустить `python -m pytest -q tests/agent/test_runtime_cwd.py`: `17 passed`; adjacent suite: `65 passed`.
- [x] T005 Проверить diff: existing malformed/unreadable, legacy/default и cross-profile guards не ослаблены; `candidate-guard-check=ok`.
- [x] T006 Создать проверенный task-owned local commit.
- [x] T007 После появления доказанного remote integration base выполнить push и открыть узкий draft PR `#24`.
- [ ] T008 Получить отдельное разрешение на live deploy/restart и выполнить post-restart canary.

## Зависимости

`T001 → T002 → T003 → T004 → T005 → T006 → T007 → T008`

## Независимый результат WP01

На task-owned branch есть минимальный patch и один regression test; targeted suite зелёный; live runtime не изменён.

Публикация: draft PR `#24`, `codex/fix-family-profile-terminal-config` → `codex/live-compatible-media-cutover-refresh`; merge и live mutation не выполнялись этой mission.
