# Синтетический dry-run миграции профилей

Этот пакет содержит только redacted fixtures и planner-тест. Он не открывает
live `HERMES_HOME`, не читает `MEMORY.md`/`USER.md`, не копирует данные и не
удаляет legacy-записи.

## Правила

- DM переносится только при точном server-side `principal_id -> profile_id`,
  Telegram `user_id == chat_id` и согласованном synthetic account.
- Username/display name, отсутствующая identity и несколько кандидатов не
  являются доказательством владельца: такая запись попадает в закрытый
  read-only `legacy_archive`.
- Явно зарегистрированные rooms получают только свой `shared_room` profile.
- `MEMORY.md`, `USER.md` и daily memory остаются вне family/shared profiles.
- Отчёт содержит counts и SHA-256 от канонического redacted плана; planner
  не выполняет запись.

Проверка:

```bash
python -m pytest -q tests/test_profile_migration.py
```

Фикстуры: `tests/fixtures/access_policy_matrix.json` и
`tests/fixtures/migration_fixture.json`.
