## Why

Разрешённые Telegram shared scopes сейчас принудительно получают
`enabled_toolsets = ["memory"]`, поэтому запрос с публичной URL доходит до
модели без `web_search`/`web_extract` и завершается неподтверждённым
model-only ответом.

## What Changes

- Строить разрешённые shared agents с существующими toolsets `memory + web`.
- Принимать только exact безопасные runtime profiles `{memory}` и
  `{memory, web_search, web_extract}`; отклонять частичный web profile и лишние
  tools.
- Требовать от shared agent использовать public web tools для URL-задач и
  сообщать о недоступном backend вместо угадывания содержимого страницы.
- Добавить focused behavioral tests без нового tool, dependency или config key.

## Capabilities

### New Capabilities

- `shared-public-web`: scoped public web access для authoritative разрешённых
  Telegram shared scopes.

### Modified Capabilities

Нет.

## Impact

- `gateway/run.py`, `gateway/session.py` и focused single-principal tests.
- Runtime/config/service не меняются этим repo change.

## Approval

- 2026-07-24: implementation-local projection одобрённого Gurra OpenSpec change
  `restore-gurra-shared-link-opening`; scope и live gates не изменены.
