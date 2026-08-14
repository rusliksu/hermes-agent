# План реализации

## Технический контекст

Реальный отказ происходит в `gateway/run.py::_check_slash_access` до
`_handle_model_command`. Существующий picker и callback binding уже умеют
shared lane, но тест PR #28 вызывал handler напрямую и использовал topic.

## Изменения

1. Добавить full-dispatch regression для зарегистрированной корневой
   shared-room через `GatewayRunner._handle_message`.
2. Зафиксировать RED: текущий ingress возвращает shared-chat denial и не
   вызывает picker.
3. Минимально разрешить только `canonical_cmd == "model"` без `--global` для
   авторизованного shared source; topic-only разрешения других команд не менять.
4. Проверить отрицательную матрицу: unauthorized/unknown, `--global`, другие
   slash commands и callback другой lane.
5. Прогнать focused и затронутые access/profile/session suites, статические
   проверки и выполнить точечный review diff.

## Риски и меры

- **Расширение прав:** правило ограничено exact model command и server-side
  authorization; остальные команды остаются topic-only/denied.
- **Cross-room callback:** тест проверяет bound shared source и отказ mismatch.
