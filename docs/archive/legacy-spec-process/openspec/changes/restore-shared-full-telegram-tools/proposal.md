## Почему

Shared-topic isolation, добавленный 19 июля 2026 года, принудительно заменил
штатный Telegram tool profile на `["memory"]`. Изоляция приватного контекста
сохранилась, но Browser Automation, terminal, files, code execution и остальные
настроенные инструменты исчезли для всех участников разрешённой общей темы.

Руслан одобрил возврат полного штатного Telegram-профиля всем участникам exact
allowlisted shared scope после отдельного раскрытия host/private рисков.

## Что меняется

- Shared turn использует тот же configured Telegram tool profile, что и обычный
  Telegram turn, с теми же runtime availability checks.
- Shared memory остаётся привязана к общей области; owner private context files,
  DM history и owner identity injection не возвращаются.
- Dangerous/elevated approval остаётся owner-only.
- Unknown room/topic по-прежнему отклоняется до model/tools.
- Nous subscription, external web provider и новые credentials не добавляются.

## Возможности

### Новые возможности

- `shared-full-telegram-tools`: parity штатного Telegram tool profile в exact
  allowlisted shared scope при сохранении privacy и approval boundaries.

### Изменённые возможности

Нет.

## Влияние

- `gateway/run.py`: выбор toolsets и binding scoped memory.
- `gateway/session.py`: точное описание shared capability boundary.
- Gateway/security tests, immutable artifact и verified live rollout.

## Одобрение

- 2026-07-24: Руслан выбрал полный профиль «всем участникам».
- 2026-07-24: после предъявления material delta и рисков ответил `Делай`;
  реализация и live rollout с проверяемым откатом одобрены.
