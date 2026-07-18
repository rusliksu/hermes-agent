## 1. Frontend API

- [x] 0.1 Зафиксировано approval пользователя на реализацию change `hermes-sessions-history-filters`; minor living-spec clarification: Overview/recent sessions polling остается all-source, а History reload всегда использует выбранный source.
- [x] 0.2 Зафиксирован scoped review fixup: prune action/payload не получает History `sourceFilter`, backend/storage не меняются; latest-request guard применяется только к History list state.
- [x] 1.1 Расширить `web/src/lib/api.ts` для `api.getSessions`, чтобы он мог передавать `source` и не передавал параметр для варианта `Все`.
- [x] 1.2 Добавить/обновить tests в `web/src/lib/api.test.ts` на URL/query params: default без source, `source=telegram`, сохранение `limit`/`offset`/`order`/`profile`.

## 2. Sessions UI

- [x] 2.1 В `web/src/pages/SessionsPage.tsx` сделать default `view` равным History/list, сохранив Overview tab.
- [x] 2.2 Добавить одиночный source filter в History toolbar с default `telegram`, вариантами `Telegram`, `Cron`, остальные source из `stats.by_source`, и `Все`.
- [x] 2.3 Подключить выбранный source к History `loadSessions`, post-delete reload и post-prune History reload paths так, чтобы `total` и pagination приходили от server-side source filter; Overview/recent polling остается all-source.
- [x] 2.4 При смене source сбрасывать page на 0, selection/range anchor и expanded row.
- [x] 2.5 Сохранить текущую search semantics: не менять backend `/api/sessions/search`, а отображать только пересечение search results с текущим source/list.
- [x] 2.6 Убедиться, что delete/export/resume/rename/prune действия не меняют session data contract; prune payload не получает source и удаляет тот же набор, что до History filter, после чего History reload использует текущий source.

## 3. Typography And Responsive UI

- [x] 3.1 Локально привести source badge на Sessions к существующему readable body/UI `font-sans`, `normal-case`, `tracking-normal`, без `font-compressed`; не менять глобальный DS `Badge`.
- [x] 3.2 Проверить, что row title использует существующий readable body/UI `font-sans`, не использует `font-mondwest` и не ломается при rename/preview fallback.
- [x] 3.3 Проверить desktop/mobile layout: toolbar с filter+search+pagination не перекрывается, rows/actions не выходят за контейнер.

## 4. Tests And Validation

- [x] 4.1 Добавить targeted pure helper tests на default History+Telegram constants, реально используемые `SessionsPage`.
- [x] 4.2 Добавить targeted pure helper/API tests на смену source semantics: reset helper для page/selection/expanded state, `source` query для History list и отсутствие `source` в prune payload.
- [x] 4.3 Добавить targeted pure helper tests на scoped typography constants для row title/source badge; выполнить manual wiring review, что `SessionsPage` использует эти constants.
- [x] 4.4 Из каталога `web` запустить targeted Vitest для API/filter/latest-request guard helpers через поддерживаемый test runner/package script.
- [x] 4.5 Из каталога `web` запустить full frontend tests: `npm run test`.
- [x] 4.6 Из каталога `web` запустить `npm run typecheck`.
- [x] 4.7 Из каталога `web` запустить `npm run lint` (выполнено; команда падает на существующих unscoped lint debt вне change scope).
- [x] 4.8 Из каталога `web` запустить `npm run build`.

## 5. Delivery Gate

- [ ] 5.1 После реализации и локальных проверок показать diff/status и запросить отдельное явное разрешение на live build/install/restart `hermes-dashboard.service`.
- [ ] 5.2 До получения отдельного разрешения не выполнять live build, install, restart, deploy, push или merge.
