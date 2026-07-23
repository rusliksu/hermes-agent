## 1. Контракты, resolver, registry и compare mode

- [x] 1.1 Зафиксировать typed contract `ResolvedAccessContext` с ровно шестью authority-полями: `principal_id`, `role_id`, `profile_id`, `conversation_scope`, `capabilities`, `delivery_target`.
- [x] 1.2 Описать typed contracts для `RolePolicy`, `PrincipalBinding`, `SharedScopeBinding`, redacted audit metadata и server-bound delivery target.
- [x] 1.3 Реализовать resolver, который принимает transport/account/peer identity только как вход и не добавляет эти значения в authority-поля downstream context.
- [x] 1.4 Добавить registry roles и bindings для owner, 9 family principals и 2 shared rooms с fail-closed validation.
- [x] 1.5 Ввести compare mode, который показывает различия legacy routing и нового resolver без изменения active routing.
- [ ] 1.6 Запретить model args, command args и callback payload выбирать foreign profile, namespace, role, session или delivery target.
  - 2026-07-22: локальный slice закрыл slash-confirm callbacks для `/new`, `/undo` и `/reload-mcp`: callback payload/session_key/choice больше не могут заменить authority, handler запускается только после revalidate captured server-bound context; broader command args/model args/cron/delegation остаются.
  - 2026-07-22: локальный slice запретил `session_search` принимать profile/role/delivery/session authority из model args: tool читает только task-local `ResolvedAccessContext`, registry-extra args игнорируются, public schema/signature не расширены; остальные model/command surfaces остаются.

## 2. Exact multiplex routing и request-path hardening

- [x] 2.1 Подключить exact Telegram DM routing по `platform + account + peer_kind + user_id` с обязательным `user_id == chat_id`.
  - 2026-07-23: локальный slice расширил shared-room resolver для Telegram topics: exact topic binding имеет приоритет, root binding с `thread_id=None` действует только как parent по exact `platform+account+peer_kind+chat_id`, без owner/default fallback.
  - 2026-07-23: review-fix вынес единый shared-room selector для ingress и persisted-context validation: active exact выигрывает у stale disabled exact, disabled exact/root parent остаются categorical deny до fallback.
- [ ] 2.2 Реализовать deny-before-model/session/tools для unknown, missing, malformed, disabled binding и mismatched DM identity.
  - 2026-07-22: добавлен optional runtime `AccessRegistry` gate в `GatewayRunner._handle_message` и focused ingress tests; checkbox остаётся открытым до config/schema loader и cutover без legacy fallback.
  - 2026-07-22: добавлен strict `access_registry` parser в `GatewayConfig.from_dict`, process-only `GatewayConfig.access_registry` и fallback wiring в `GatewayRunner.__init__`; checkbox остаётся открытым до downstream/cutover завершения.
- [ ] 2.3 Удалить owner/default/last-used fallback из private ingress, shared ingress, slash commands, callbacks и resume path.
  - 2026-07-22: локальный slice убрал owner/default/global fallback после server-resolved `ResolvedAccessContext` в profile home resolution, SessionStore key namespace и `/resume` same-origin guards; legacy path без context оставлен прежним, checkbox остаётся открытым до callbacks/background и оставшихся ingress путей.
  - 2026-07-22: локальный slice убрал configured-registry bypass для `internal=True` gateway events: in-process events теперь проходят validation существующего typed `ResolvedAccessContext` и exact delivery target match; persistence/restart/callback propagation ещё не добавлены, поэтому потерявшие context события fail-closed и 2.3/3.6 остаются открытыми.
  - 2026-07-22: локальный slice запретил registry CLI handoff без server-owned persisted `ResolvedAccessContext` до выбора adapter/home/thread/session/cache/model/delivery; row переводится в failed с categorical reason и redacted audit, checkbox остаётся открытым до полноценного server-issued handoff context propagation.
- [ ] 2.4 Укрепить request path так, чтобы `HERMES_HOME` и profile home брались из server-bound context, а не из module/import-time cache.
- [ ] 2.5 Заменить request-path `os.getenv` authority/auth fallbacks на server-bound config/policy providers.
- [ ] 2.6 Проверить outbound delivery routing только через resolved `delivery_target` без owner default delivery.
  - 2026-07-23: локальный slice делает shared-room topic `delivery_target` server-derived из trusted incoming identity с точным `thread_id`; disabled exact topic binding deny до parent fallback, unknown room/non-member остаются deny.

## 3. Sessions, memory, prompt, attachments и context propagation

- [ ] 3.1 Изолировать SQLite sessions по `profile_id` и `conversation_scope` для search, resume, reset, compaction, transcript access, export и delete.
  - 2026-07-22: локальный slice добавил SQL-level scope для `session_search` browse/FTS/title и ownership-check до read/scroll/bookend I/O по `profile_name + delivery_target` origin; resume/reset/compaction/export/delete и полноценная migration остаются.
- [ ] 3.2 Привязать memory namespaces, memory hydration, memory commit и memory tools к server-bound context.
  - 2026-07-22: локальный slice привязал built-in `MemoryStore` load/snapshot/tool writes к task-local `ResolvedAccessContext`, добавил fail-closed guard до memory mkdir/read/write и redacted `memory_access_denied`; external provider commit/full prompt layering/migration ещё остаются.
  - 2026-07-23: локальный slice добавил delivery thread dimension в opaque shared memory namespace: root и разные topics одного room теперь получают разные `access/<sha256>` namespaces без raw IDs.
- [ ] 3.3 Реализовать prompt layering: security layer -> read-only role layer -> scope layer -> private USER/memory layer.
- [ ] 3.4 Исключить private USER/memory из shared room prompts и не импортировать private context между profiles.
- [ ] 3.5 Привязать attachments, generated files и workspaces к resolved profile boundary и room scope.
- [ ] 3.6 Передавать шесть authority-полей context через callbacks, background, cron, delegation, compaction, reset, restart и concurrent turns без расширения.
  - 2026-07-22: локальный slice добавил strict persistence/restoration `ResolvedAccessContext` в authoritative gateway routing store и fail-closed startup resume/watch synthetic routing через restored context/profile adapter; checkbox остаётся открытым до kanban/handoff/cron/delegation/compaction/reset/restart/concurrency покрытия.
  - 2026-07-22: локальный slice закрыл Kanban notify/wakeup callbacks под configured `AccessRegistry`: delivery/wake идут только через persisted session origin context и canonical source после ingress validation; добавлено focused 8.4-покрытие для positive/missing/mismatch, checkbox остаётся открытым до handoff/cron/delegation/compaction/reset/concurrency.
  - 2026-07-22: локальный slice добавил fail-closed handoff watcher path для configured registry без persisted context: no side effects до propagation, failed state redacted; полноценная передача server-issued/persisted handoff context остаётся.
  - 2026-07-22: локальный slice добавил revalidation captured `ResolvedAccessContext` для slash-confirm registration и execution; callbacks не принимают authority из payload/session_key/choice, checkbox остаётся открытым до broader command args/cron/delegation/compaction/reset/restart/concurrency покрытия.
  - 2026-07-22: локальный slice добавил task-local `ResolvedAccessContext` в `gateway.session_context`, gateway propagation из `source.resolved_access_context`, cron job persistence/restoration и inheritance reset/clear checks; checkbox остаётся открытым до broader provider/callback/delegation/compaction/reset/restart/concurrency покрытия.
  - 2026-07-22: локальный slice добавил fail-closed validation incoming и persisted `ResolvedAccessContext` в `SessionStore.get_or_create_session` до compression-tip/recovery; configured registry без authoritative routing entry больше не восстанавливает legacy transcript по peer metadata, checkbox остаётся открытым до callbacks/background/cron/delegation/compaction/concurrency покрытия.
  - 2026-07-22: локальный slice протащил текущий task-local `ResolvedAccessContext` через batch worker submit и inner `child.run_conversation` submit в `delegate_task` через отдельный `contextvars.copy_context()` на каждый concurrent submit; legacy path без context оставлен byte-compatible, checkbox остаётся открытым до полного callback/provider/completion/restart покрытия.

## 4. Role capability enforcement, self cron и scoped secrets

- [ ] 4.1 Реализовать owner policy как текущий полный профиль Руслана с owner-approved admin, cron и delegation behavior.
- [ ] 4.2 Реализовать `family_standard`: личные memory/session search, documents, attachments, vision, public web, image/voice generation и reminders самому себе.
  - 2026-07-22: локальный slice сузил gateway agent-construction tool surface для typed `family_standard` до пересечения configured platform toolsets с guarded capabilities `public_web`, `vision`, `image_generation`, `voice_generation`, `session_search`, `self_reminder`; memory/documents/attachments и backend enforcement остаются.
  - 2026-07-22: локальный slice разрешил backend `session_search` только для `family_standard`/`family_sandbox` при literal capability `session_search` и только в собственном DM SQL scope; memory/documents/attachments остаются.
  - 2026-07-22: локальный slice добавил family memory tool surface только при literal `memory_search` capability и configured `memory`, а backend guard разрешает только bound private profile `memories` directory с USER; documents/attachments и полный provider/cutover enforcement остаются.
- [ ] 4.3 Запретить для `family_standard` host shell, host filesystem, logged-in/persistent browser, arbitrary MCP, Wolfram MCP, delegation, owner credentials и cross-profile search.
  - 2026-07-22: локальный slice fail-closed исключает `terminal`, `file`, `browser`, `delegation`, Wolfram/arbitrary MCP-like toolsets и прочие не-mapped toolsets из `family_standard` constructor surface даже при platform config; filesystem/memory/attachments/browser/MCP/backend execution guards ещё не завершены.
  - 2026-07-22: локальный slice запретил cross-profile/cross-DM/cross-thread SQLite session search/read/scroll для `family_standard`/`family_sandbox`; остальные backend guards ещё не завершены.
- [ ] 4.4 Реализовать `shared_room`: room sessions/memory, documents, attachments, vision и public web без private memory, cron, private delivery, shell и cross-principal/profile search.
  - 2026-07-22: локально устранён legacy omission для shared public web: gateway shared turn теперь берёт `public_web` только из runtime `source.resolved_access_context.capabilities` и валидирует точную model tool surface memory/web; checkbox остаётся открытым до полной shared_room роли.
  - 2026-07-22: локальный slice добавил configured intersection для typed `shared_room`: generic helper даёт только `public_web -> web` и `vision -> vision`, а shared memory binder включается только при `room_memory` capability и configured `memory`; private/user memory extras, cron, shell, delivery и cross-profile backend enforcement остаются.
  - 2026-07-22: локальный slice оставил backend `session_search` для `shared_room` fail-closed; room memory/documents/attachments остаются.
  - 2026-07-22: локальный slice привязал shared room built-in memory к task-local context и разрешил только room store ниже текущего profile `memories/shared/access/<sha256>`, с USER disabled; AccessRegistry-only `shared_room` без legacy single-principal recognition теперь строится restricted (без private prompt/prefill/user identity/memory), а binder и `/memory` используют один opaque server-derived namespace; documents/attachments/cron/private delivery остаются.
  - 2026-07-22: final-review slice централизовал opaque fingerprint/namespace helpers, tightened exact shared path до server-derived `memories/shared/access/<sha256>` с symlink-deny ниже profile home и scoped `/memory pending|approve|reject` queue по opaque context fingerprint без raw authority IDs; broader provider/prompt/migration work остаётся.
- [ ] 4.5 Реализовать self reminder cron для family roles только с server-bound `delivery_target`.
  - 2026-07-22: локальный slice ограничил configured family cron self-reminder путём: create/update допускают только omitted/literal origin, persisted origin должен exactly match server-bound `delivery_target`, explicit target/model-selected delivery denied; scheduler требует deliver=origin + exact origin; checkbox остаётся открытым до полного cron/provider/cutover покрытия.
- [ ] 4.6 Запретить shared room cron/private delivery и family delivery к чужим principals, rooms или owner default target.
  - 2026-07-22: локальный slice запретил model cron actions для `shared_room`, скрыл/запретил cross-context family job list/mutation/run, и добавил scheduler fail-closed для malformed/tampered persisted context до script/model/delivery; checkbox остаётся открытым до полного private-delivery/provider/cutover покрытия.
- [ ] 4.7 Убедиться, что scoped model secrets используются только model client construction и не попадают в terminal, Docker, browser или MCP tool env.

## 5. Юлин Docker, browser, Wolfram и delegation

- [ ] 5.1 Provision Юлин `family_sandbox` profile с отдельным workspace, home, sessions, memory и capability set.
- [ ] 5.2 Запустить Docker terminal sandbox без host mounts, без owner credentials, с `HOME` внутри profile, disabled terminal network, 2 vCPU, 2 GiB memory, 256 PID и 5 GiB writable disk.
- [ ] 5.3 Проверить sandbox escape denial для symlink, host path mount, owner credential injection, network access и quota bypass.
- [ ] 5.4 Подключить isolated public browser только для `family_sandbox` без owner cookies, logged-in browser profile, localhost access или persistent owner profile.
- [ ] 5.5 Подключить Wolfram MCP allowlist только для `family_sandbox` и запретить arbitrary MCP.
- [ ] 5.6 Разрешить delegation только для `family_sandbox` и только внутри same `profile_id`, `conversation_scope`, `capabilities` и `delivery_target`.
  - 2026-07-22: локальный slice добавил pre-config/pre-credential/pre-child guard в `delegate_task`: при task-local typed `ResolvedAccessContext` delegation разрешён только для `owner`/`family_sandbox` с literal capability `delegation`; `family_standard`, `shared_room`, unknown и malformed получают categorical redacted tool_error, checkbox остаётся открытым до полного same-profile/sandbox/tool/provider enforcement.
  - 2026-07-22: локальный slice сузил gateway constructor surface: `delegation` появляется у `family_sandbox` только при literal capability `delegation` и configured `delegation`; `terminal`, `browser`, Wolfram и arbitrary MCP не добавляются до отдельных sandbox/backend tasks, same-profile runtime enforcement остаётся.

## 6. Dashboard Access/Users, lease и audit

- [ ] 6.1 Ограничить Dashboard Access/Users только localhost/SSH tunnel protected sessions.
- [ ] 6.2 Реализовать redacted status для principals, roles, scopes, bindings, migration state и service state без raw IDs, secrets, memory content или message bodies.
- [ ] 6.3 Реализовать role/binding/scope preview с effective permission delta до сохранения.
- [ ] 6.4 Применять role changes атомарно только после preview, explicit confirm и redacted audit persistence.
- [ ] 6.5 Реализовать break-glass lease на один target profile/session set, read-only history, максимум 15 минут, reason, privacy warning, reconfirm и manual revoke.
- [ ] 6.6 Запретить через break-glass bulk search/export, writes, migration, Telegram permission changes, model permission changes, tool execution, model/tool content exposure и private memory export.
- [ ] 6.7 Хранить dashboard audit без content: без message bodies, prompts, tool args, tool outputs, credentials и raw model secrets.

## 7. Provisioning, migration, backup, dry-run и rollback

- [ ] 7.1 Подготовить private manually confirmed roster/preflight для всех 9 family transport identities: `transport identity -> opaque principal_id -> role_id -> profile_id`, Юлина binding вручную подтверждена как единственная `family_sandbox`, остальные восемь `family_standard`, no missing/duplicate/ambiguous mappings, display name/username только redacted labels и не authority.
- [ ] 7.2 Provision 9 family profiles и 2 shared room profiles только после verified roster/preflight с unique active bindings and no duplicate profile mappings.
- [ ] 7.3 Подготовить backup plan для config, profile mappings, sessions, memory, attachments и migration reports.
- [ ] 7.4 Реализовать dry-run migration для всех однозначных DM sessions с сохранением IDs, timestamps, counts и deterministic content hashes.
- [ ] 7.5 Перенести ambiguous legacy sessions только в closed read-only legacy archive с hashes/counts и без active search/memory/prompt/tool visibility.
- [ ] 7.6 Исключить global `MEMORY.md`, global `USER.md` и personal context из family/shared imports.
- [ ] 7.7 Сохранить room topics как separate namespace inside room profile и не склеивать DM history с room history.
  - 2026-07-23: локальный slice разделил root/topic shared-room contexts через `delivery_target.thread_id` в namespace helper и registry validation; pending fingerprint уже включает полный context и разделяется тем же полем.
- [ ] 7.8 Реализовать rollback, который восстанавливает saved profile mappings и migrated data from verified backup или отказывает с explicit error.

## 8. Focused и full tests

- [x] 8.1 Добавить focused tests для typed contracts, resolver, registry validation, compare mode и redacted audit metadata.
- [ ] 8.2 Добавить pairwise isolation tests для owner, Юли, мамы, остальных семи family principals и обеих rooms.
- [ ] 8.3 Добавить tests для guessed IDs: session IDs, profile IDs, memory namespaces, attachment paths, delivery targets и callback payloads.
  - 2026-07-22: добавлены focused tests для typed role/capability/config toolset intersection, malformed/unknown role empty surface, no-tools as `[]`, typed shared memory/web/vision intersection и mocked `_run_agent` assertion, что shared override не открывает memory без `room_memory`; guessed IDs по attachments/filesystem/browser/sandbox/MCP остаются.
  - 2026-07-22: добавлены focused tests для guessed `session_id`, model-supplied profile, foreign profile, same-profile foreign DM/thread и malformed/shared/unknown/missing-db `session_search` denial before transcript I/O; memory namespaces/attachments/delivery broader coverage остаётся.
  - 2026-07-22: добавлены focused tests для guessed/mismatched memory namespace/profile/path: family/shared memory guard denies missing capability, malformed/unknown/mismatched context, stale snapshot, shared USER и `/memory --namespace` args до I/O; attachments/delivery/callback broader coverage остаётся.
  - 2026-07-23: добавлены focused tests для topic-scoped shared-room routing: root topic allow, exact override, disabled exact deny, namespace separation root/two topics, context tamper deny, non-member и unknown room deny.
- [ ] 8.4 Добавить concurrent/background/callback/cron/delegation/compaction/reset/restart tests с persisted context validation.
  - 2026-07-22: добавлены focused session-store tests для restart compression-tip heal с exact persisted context, missing/malformed/mismatch denial before IO, no DB recovery без routing entry и reset/auto-reset context continuity; checkbox остаётся открытым до broader concurrent/background/callback/cron/delegation/compaction покрытия.
  - 2026-07-22: добавлены focused delegate tests для early deny без credential/child side effects, owner/family_sandbox+delegation allow, immutable six-field context visibility after inner executor hop, batch worker propagation и role=`orchestrator` как child-tree role без access-role изменения; checkbox остаётся открытым до broader background/callback/cron/compaction/restart покрытия.
- [ ] 8.5 Добавить sandbox tests для symlink escape, host mount denial, network disabled, quotas и owner credential absence.
- [ ] 8.6 Добавить dashboard lease tests для localhost/SSH tunnel, preview+confirm atomic apply, expiry, manual revoke, no restart resurrection и no content audit.
- [ ] 8.7 Запустить focused tests for changed modules and specs without live effects.
- [ ] 8.8 Запустить full relevant test suite, включая gateway, Telegram routing, sessions, memory, tools, cron, dashboard и migration paths.

## 9. Review, strict validation, preflight и PR readiness

- [ ] 9.1 Выполнить code review по privacy, prompt caching, role alternation, tool footprint и fail-closed behavior.
- [ ] 9.2 Выполнить OpenSpec strict validation для active change.
- [ ] 9.3 Выполнить credential-safe preflight, который не печатает, не читает и не копирует secrets.
- [ ] 9.4 Проверить compare reports, dry-run reports, rollback reports и redacted diagnostics.
- [ ] 9.5 Подготовить PR summary с scope, tests, migration notes, no-live-effects statement и explicit live gate checklist.

## 10. Live gate только после отдельного approval

- [ ] 10.1 После отдельного approval выполнить live backup affected refs/config/profile mappings/sessions/memory/attachments.
- [ ] 10.2 После отдельного approval выполнить live credential-safe preflight и подтвердить privacy warnings.
- [ ] 10.3 После отдельного approval применить live config/profile binding changes.
- [ ] 10.4 После отдельного approval выполнить live migration только после successful dry-run.
- [ ] 10.5 После отдельного approval restart `hermes-gateway` и проверить active/running без restart loop.
- [ ] 10.6 После отдельного approval restart `hermes-dashboard` и проверить active/running без restart loop.
- [ ] 10.7 После отдельного approval выполнить canary owner отдельно.
- [ ] 10.8 После отдельного approval выполнить canary Юли отдельно.
- [ ] 10.9 После отдельного approval выполнить canary мамы отдельно.
- [ ] 10.10 После отдельного approval выполнить canary остальных семи family principals отдельно.
- [ ] 10.11 После отдельного approval выполнить canary обеих shared rooms отдельно.
- [ ] 10.12 После отдельного approval выполнить unknown ingress и malformed ingress denial canaries отдельно.
- [ ] 10.13 После отдельного approval проверить отсутствие privacy warnings, raw IDs, message bodies, credentials и model secrets в reports/logs.
- [ ] 10.14 После отдельного approval выполнить rollback при любом fail в canary, active/running check, restart-loop guard или privacy warning check.
