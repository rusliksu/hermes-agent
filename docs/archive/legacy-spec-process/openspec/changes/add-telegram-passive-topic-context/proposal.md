## Why

В allowlisted Telegram-группах Gurra отвечает только по явному вызову, но сейчас
не видит обычный разговор между вызовами. Из-за этого модель ошибочно трактует
последнюю адресованную реплику без контекста группы, хотя Hermes уже имеет
context-only механизм наблюдения для legacy group mode.

## What Changes

- Разрешить существующий `observe_unmentioned_group_messages` только для
  authoritative shared scope из `SinglePrincipalPolicy`.
- Сохранять обычные сообщения без запуска model, tools, callbacks или visible
  response и подавать их только при следующем явном mention/reply/command.
- Сохранить отдельный transcript для обычной группы, General и каждого forum
  topic; неизвестные группы, channels, bot-authored и anonymous ingress остаются
  denied до записи контекста.
- Ограничить context-only replay последними 50 сообщениями, 20 000 символами и
  шестью часами; старые строки остаются audit history, но не попадают в prompt.
- Не записывать passive context в scoped long-term memory автоматически и не
  расширять shared capability profile.
- Убрать raw Telegram identities из observation logs и не утверждать в prompt,
  что bot имеет доступ к личным чатам или полной истории Telegram.

## Capabilities

### New Capabilities

- `telegram-passive-shared-context`: безопасное bounded-наблюдение обычных
  сообщений в allowlisted Telegram group/topic scope до явного вызова агента.

### Modified Capabilities

Нет.

## Impact

- `plugins/platforms/telegram/adapter.py`: authoritative observation gate и
  attribution для single-principal shared scope.
- `gateway/run.py`: bounded replay context без изменения transcript storage.
- Telegram group/session tests, single-principal regressions и live private
  config `observe_unmentioned_group_messages: true` после delivery gates.
- Новые зависимости, API и миграция БД не требуются.

## Approval

- 2026-07-19: Руслан одобрил material delta и live activation после тестов
  ответом «да, делай по опенспеке и понитейлу» на summary bounded passive
  per-group/topic context без фоновых model/tool/memory side effects.
