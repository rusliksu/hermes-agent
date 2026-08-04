# Media provider fallback: backup, rollback и synthetic canary

Этот runbook описывает только обратимый подготовительный пакет. Fixture и canary
синтетические: они не являются live-конфигурацией Hermes, не содержат значений
секретов и не отправляют сообщения в Telegram.

## Backup

Перед любым live-изменением оператор сохраняет redacted JSON из read-only
проверки `hermes config media-policy --dry-run --json` в отдельный операторский
артефакт `media-policy-pre-change.json`. В backup попадает только порядок
провайдеров, статусы opaque secret references и diagnostics. `.env`, credential
файлы и значения секретов не копируются.

Для dry-run fixture используется команда из
[`media-provider-fallback-canary.json`](fixtures/media-provider-fallback-canary.json).
Сначала сверяется SHA-256 fixture, затем проверяется `valid: true` и ожидаемый
порядок провайдеров для image generation, STT и TTS.

## Rollback

Rollback выполняется только после отдельного подтверждения live configuration.
Оператор удаляет ключи `image_gen.fallbacks`, `stt.fallbacks` и `tts.fallbacks`
в утверждённой live-процедуре, не меняя profile bindings и не восстанавливая
секреты из backup. После этого повторяется read-only dry-run: отчёт должен
показывать legacy provider choices. Перезапуск Hermes и Telegram canary —
отдельные gates и в этот пакет не входят.

## Synthetic canary

Матрица содержит `owner`, `family_standard`, `family_sandbox`, `shared_room` и
`unknown`. Первые четыре проверяют только зарегистрированный scope; unknown
должен быть отклонён. Команды packet помечены `mutates: false`, не читают
credential-файлы, не используют owner fallback и не запускают restart/systemd
или Telegram delivery.
