# Задачи: `fix-family-profile-terminal-config-hjr01`

Bead: `HERMES-jir`

## WP01 — Минимальный hotfix

- [x] T001 Получить initial approval на implementation.
- [x] T002 В `agent/runtime_cwd.py` трактовать только отсутствующий per-profile `config.yaml` как пустую terminal config.
- [x] T003 Добавить ровно один regression test в `tests/agent/test_runtime_cwd.py`.
- [x] T004 Запустить `python -m pytest -q tests/agent/test_runtime_cwd.py`: `17 passed`; adjacent suite: `65 passed`.
- [x] T005 Проверить diff: existing malformed/unreadable, legacy/default и cross-profile guards не ослаблены; `candidate-guard-check=ok`.
- [x] T006 Создать проверенный task-owned local commit.
- [x] T007 После появления доказанного remote integration base выполнить push, открыть узкий PR `#24` и перевести его в ready.
- [x] T008 Получить отдельное разрешение на live deploy/restart и выполнить post-restart canary.

## Зависимости

`T001 → T002 → T003 → T004 → T005 → T006 → T007 → T008`

## Независимый результат WP01

На task-owned branch есть минимальный patch и один regression test; targeted suite зелёный; live runtime не изменён.

Публикация: ready PR `#24`, `codex/fix-family-profile-terminal-config` → `codex/live-compatible-media-cutover-refresh`.

## Live rollout evidence — 2026-08-07

- Пользователь отдельно разрешил deploy и restart `hermes-gateway`.
- Фактический deployed base: `e8e5ec6742173b90279bc3ef3bf087d5a10590b8`; owner/default hotfix сохранён.
- Integration commit: `fe360df841f4b9d399f74843ee80fbc63be8e478`.
- Artifact: `/home/openclaw/staging/hermes-deploy-family-config-fix-20260807T103026Z`.
- Проверки: `20` targeted, `108` adjacent gateway/access и `1039` post-update tests passed; независимый review — PASS.
- `hermes-gateway` перезапущен в `2026-08-07 12:34:13 CEST`; новый PID `1205615`, `NRestarts=0`, `Result=success`.
- Synthetic `family-02` resolver canary прошёл; Telegram TCP connections установлены; WebAPI/WebUI/dashboard PID не изменились.
- Rollback drop-in: `/home/openclaw/backups/gurra-family-config-fix/20260807T103026Z/40-live-compatible-media-cutover.conf.pre`.
