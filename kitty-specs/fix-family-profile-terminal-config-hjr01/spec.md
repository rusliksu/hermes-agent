# Спецификация mission: восстановить семейные профили Gurra без `config.yaml`

**Mission**: `fix-family-profile-terminal-config-hjr01`
**Branch**: `codex/fix-family-profile-terminal-config`
**Bead**: `HERMES-jir`
**Статус**: implementation проверена; PR `#24` ready, merge/live gate не выполнялись

## Проблема

После live deploy typed family profile с валидным nested-registry route и существующим profile home падает до построения system prompt, если в его каталоге нет per-profile `config.yaml`. Фактическая ошибка — `ValueError: typed terminal config unavailable` в `agent/runtime_cwd.py`.

## Требования

### FR-001 — Отсутствующий файл

Для уже валидированного typed family profile отсутствие `config.yaml` означает пустую terminal config. Resolver использует собственный profile home как `cwd` и не наследует process env, launch cwd или конфигурацию другого профиля.

### FR-002 — Fail closed для существующего файла

Если `config.yaml` существует, но malformed или unreadable, runtime по-прежнему завершает разрешение с `ValueError` и не использует fallback.

### FR-003 — Сохранение guards

Legacy/default paths, проверка profile home внутри profiles root, candidate cwd и cross-profile guards не меняются.

## Критерии приёмки

1. Валидный typed family profile с существующим home и без `config.yaml` получает пустую terminal config и `cwd=profile home`.
2. Конфликтующие `TERMINAL_CWD` и launch cwd не влияют на результат typed profile.
3. Malformed или unreadable существующий `config.yaml` остаётся fail closed.
4. Меняются только `agent/runtime_cwd.py` и один regression test в `tests/agent/test_runtime_cwd.py`.
5. Targeted tests проходят; commit, push и live deploy/restart остаются отдельными gates.

## Вне scope

- Создание или копирование per-profile `config.yaml`.
- Изменение nested access registry, profile routing или Telegram policy.
- Ослабление cross-profile validation.
- Live deploy, restart, config/env/credential changes.
