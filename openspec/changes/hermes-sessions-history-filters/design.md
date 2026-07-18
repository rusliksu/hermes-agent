## Context

Проверенный baseline:

- live source checkout: `/home/openclaw/staging/hermes-deploy-cdfe5227-20260715`, clean detached HEAD `1b5d161039820b8494d5c15bebfcea5d3ce202a4`;
- `127.0.0.1:9119` обслуживается `hermes-dashboard.service` через symlink `/home/openclaw/.hermes/hermes-agent`;
- `SessionsPage` сейчас хранит `view` как `"overview"` по умолчанию, а History соответствует `"list"`;
- `api.getSessions` пока строит `/api/sessions?limit=...&offset=...&order=...` и не передает `source`/`exclude_sources`;
- backend `GET /api/sessions` уже принимает `source` и применяет его и к `list_sessions_rich`, и к `session_count`, что дает корректные `total`/pagination;
- `GET /api/sessions/stats` возвращает `by_source`, пригодный для построения вариантов фильтра;
- `SessionRow` должен использовать существующий readable body/UI `font-sans` для title/preview; source badge сейчас выводит raw source через глобальный `Badge`, который наследует compressed typography.

## Goals / Non-Goals

**Goals:**

- Сделать History default view для `/sessions`, сохранив Overview.
- Добавить одиночный source-фильтр в History с default `telegram`.
- Передавать выбранный конкретный source в `api.getSessions` как server-side query param.
- Строить варианты фильтра из `stats.by_source`: всегда `Telegram`, `Cron`, `Все`, плюс остальные реально присутствующие source.
- Сбрасывать `page`, `selectedIds`/range anchor и `expandedId` при смене source.
- Сохранить текущую search semantics: backend search не расширяется, UI отображает пересечение найденных session ids с текущим выбранным source/list.
- Исправить локальную типографику только для Sessions row titles и source badges: оба используют существующий readable body/UI `font-sans`.
- Сохранить desktop/mobile адаптивность текущей toolbar/list layout.

**Non-Goals:**

- Не менять session schema, storage, delete/export/resume/rename/prune поведение.
- Не добавлять новый backend search filter, новый endpoint или миграции.
- Не менять глобальный `Badge`, global design system, branding, compressed font defaults.
- Не выполнять build/install/restart/deploy live `hermes-dashboard.service` без отдельного явного разрешения.

## Decisions

1. **Source filter state lives in `SessionsPage`.**
   - Решение: добавить локальное состояние вида `"telegram" | "cron" | <known source> | "all"` и передавать его в `loadSessions`.
   - Почему: фильтр относится только к странице Sessions и не требует shared store.
   - Альтернатива: URL query param. Отклонено для baseline, потому что пользователь запросил минимальный scope и не требовал deep-link/state persistence.

2. **`api.getSessions` получает options object вместо дальнейшего роста positional args.**
   - Решение: расширить frontend API так, чтобы он мог кодировать `source` и при необходимости сохранить совместимость с текущими вызовами.
   - Почему: текущие positional args уже включают `limit`, `offset`, `profile`, `order`; добавление source как пятого позиционного параметра хуже читается и проще ломается.
   - Альтернатива: отдельный `getSessionsBySource`. Отклонено, потому что backend endpoint один, а поведение отличается только query params.

3. **Pagination total остается server-owned.**
   - Решение: при конкретном source `loadSessions` вызывает `/api/sessions?...&source=<source>`; при `Все` не передает `source`.
   - Почему: backend уже применяет source к `session_count`, значит UI не должен клиентски пересчитывать `total`.
   - Альтернатива: загрузить все source и фильтровать на клиенте. Отклонено из-за неверной пагинации и лишней нагрузки.

4. **Search остается текущим backend search плюс client-side intersection.**
   - Решение: не менять `/api/sessions/search`; `filtered` продолжает строиться из текущего `sessions` и `snippetMap`, поэтому видимыми остаются только строки, присутствующие и в выбранном source/page, и в search results.
   - Почему: это сохраняет текущую семантику поиска и прямо избегает backend search expansion.
   - Trade-off: search по-прежнему не становится полноценным server-side source-scoped search across all pages. Это осознанно out of scope.

5. **Фильтр строится из stats, но не мутирует stats UI.**
   - Решение: брать `stats.by_source`, нормализовать labels через локальный helper (`telegram` -> `Telegram`, `cron` -> `Cron`, прочие - readable capitalization/fallback raw), дедуплицировать и сортировать так, чтобы Telegram/Cron/Все были стабильными.
   - Почему: варианты должны отражать реально доступные source, не требуя нового endpoint.
   - Альтернатива: hardcoded только Telegram/Cron/All. Отклонено, потому что требуются остальные реально доступные source.

6. **Typography fix scoped to Sessions markup.**
   - Решение: добавить/сохранить существующий readable body/UI `font-sans` только на row title и source badge content; для source badge локальным `className` задать `normal-case` и `tracking-normal`, без `font-compressed`.
   - Почему: глобальный DS `Badge` менять нельзя, а проблема локальна для Sessions readability.
   - Альтернатива: изменить DS `Badge`. Отклонено по explicit constraint.

## Risks / Trade-offs

- [Risk] `stats.by_source` считает archived тоже, а History list по умолчанию archived исключает. -> Mitigation: фильтр может показывать source, у которого на активном list нет строк; empty state остается корректным, а серверный `total` покажет 0 для текущего list scope.
- [Risk] Existing search UX может выглядеть как "мало результатов", потому что это пересечение с текущей выбранной source/page. -> Mitigation: явно покрыть и описать это в spec/tasks, не обещать full backend search expansion.
- [Risk] Toolbar может переполниться на mobile после добавления фильтра. -> Mitigation: использовать компактный segmented/select-like control с wrap/flex behavior рядом с search и проверить mobile viewport.
- [Risk] `api.getSessions` signature change может сломать существующие вызовы. -> Mitigation: сохранить backward-compatible defaults и добавить URL/params tests.

## Migration Plan

- Implementation stays in task worktree and affects only source files/tests after this planning change.
- Local validation runs before any delivery gate.
- Live build/install/restart of `hermes-dashboard.service` is a separate manual gate after implementation and local checks.
- Rollback for implementation is reverting the frontend commit/worktree; no database or config migration is involved.

## Open Questions

Нет blocking вопросов для baseline scope. Единственный deliberate trade-off: search остается текущим frontend intersection, а не новым source-scoped backend search.
