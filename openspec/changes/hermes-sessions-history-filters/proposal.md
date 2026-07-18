## Why

Страница `/sessions` сейчас открывается с Overview и смешивает историю разных источников, хотя основной операторский сценарий для Hermes dashboard - быстро просматривать Telegram-сессии. Backend уже умеет корректно считать `total` и пагинацию по `source`, поэтому изменение можно держать в узком frontend/UI scope без расширения session data model.

## What Changes

- `/sessions` по умолчанию открывает History, при этом Overview остается доступен через существующий переключатель.
- В History появляется один фильтр по `source` с дефолтом `telegram`.
- Варианты фильтра: `Telegram`, `Cron`, остальные реально доступные `source` из `stats.by_source`, и `Все`.
- При смене фильтра сбрасываются страница, selection и раскрытая строка; загрузка списка использует server-side `source` query param, чтобы `total` и pagination были корректными.
- Search не получает новую backend-семантику: текущий `/api/sessions/search` остается как есть, UI показывает только пересечение результатов поиска с выбранным source/list state.
- На странице Sessions заголовки строк и source badge используют существующий readable body/UI `font-sans`; source badge получает `normal-case` и `tracking-normal`, без `font-compressed`. Глобальная typography/design system и branding не меняются.
- Delete/export/resume, session data, backend storage и live service lifecycle не меняются.
- После реализации и локальной проверки live build/install/restart `hermes-dashboard.service` требует отдельного явного разрешения.

## Capabilities

### New Capabilities

- `sessions-history-source-filter`: UI-контракт для default History view, source-фильтрации, pagination/search interaction, scoped typography и delivery gates страницы Sessions.

### Modified Capabilities

Нет: в этом worktree нет существующих OpenSpec specs, которые меняют свои требования.

## Impact

- Frontend modules: `web/src/pages/SessionsPage.tsx`, `web/src/lib/api.ts`, возможно `web/src/i18n/*`/`web/src/i18n/types.ts` для подписей фильтра.
- Frontend tests: `web/src/lib/api.test.ts` и targeted component/page tests для Sessions UI, если в проекте уже есть подходящая test harness или ее минимально добавляют рядом с `SessionsPage`.
- Backend implementation: без изменений; `hermes_cli/web_server.py` уже принимает `source` и `exclude_sources` в `GET /api/sessions`, а `GET /api/sessions/stats` уже возвращает `by_source`.
- Validation after implementation: из каталога `web` выполнить `npm run test`, `npm run typecheck`, `npm run lint`, `npm run build`; targeted frontend tests для `api.getSessions` и `SessionsPage` запускать через поддерживаемый test runner/package script без несуществующих команд.
- Delivery: no push, no deploy, no restart in this planning change; future live build/install/restart only after explicit user gate.
