# План реализации: `fix-family-profile-terminal-config-hjr01`

**Spec**: `kitty-specs/fix-family-profile-terminal-config-hjr01/spec.md`
**Branch**: `codex/fix-family-profile-terminal-config`
**Bead**: `HERMES-jir`

## Минимальное решение

`bound_profile_terminal_config()` уже вызывается после `_bound_profile_home()`, который валидирует typed access context и границы profile home. Поэтому root-cause fix остаётся в одной точке:

- если `home / "config.yaml"` отсутствует — вернуть `{}`;
- если файл существует — сохранить текущие `open()`/YAML/type checks и `ValueError` для ошибок;
- не менять `_bound_profile_home()`, `resolve_bound_profile_cwd()` и публичные resolver-ы.

Это решение выбрано по `ponytail` ladder: существующая цепочка после `{}` уже выбирает profile home, поэтому дополнительные fallback-и, migration или новые abstractions не нужны.

## Проверка

Добавить один тест в `tests/agent/test_runtime_cwd.py`, который:

1. создаёт валидный typed profile home без `config.yaml`;
2. задаёт конфликтующий внешний cwd;
3. проверяет пустую terminal config и результат `profile home` через публичный resolver;
4. оставляет существующие malformed/unreadable и cross-profile tests без изменений.

Основная команда проверки:

```bash
python -m pytest -q tests/agent/test_runtime_cwd.py
```

## Gates

1. Initial approval на implementation.
2. Targeted tests и diff review.
3. Проверенный task-owned commit и ready PR `#24` поверх доказанного remote integration base.
4. Отдельный live deploy/restart gate с preflight и post-restart canary.
