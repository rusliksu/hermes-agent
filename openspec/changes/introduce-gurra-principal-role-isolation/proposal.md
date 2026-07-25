## Почему

Gurra/Hermes сейчас не имеет достаточно жесткой серверной границы между доверенными transport `principal` и семейными/shared контекстами, из-за чего routing, память, сессии, prompt/context, workspace и права могут неявно смешиваться. Нужна явная модель `principal -> profile -> policy`, чтобы multi-principal Telegram gateway работал без fallback в owner/default и без доступа к model/session/tools до успешного разрешения профиля.

## Что меняется

- Ввести правило: один trusted transport `principal` или shared room соответствует ровно одному Hermes profile с отдельными `HERMES_HOME`, memory, SQLite sessions, prompt/context, skills и workspace.
- Использовать один Telegram bot/gateway, но выполнять exact DM routing по `platform + account + peer_kind + user_id`; для Telegram DM `user_id` должен совпадать с `chat_id`.
- Deny до model/session/tools для unknown, malformed или missing profile; запретить fallback в owner/default.
- **BREAKING**: отказ от default owner fallback для неразрешенных Telegram/private ingress и других principal resolution failures.
- Зафиксировать immutable `ResolvedAccessContext` как единственный request-scoped authority с ровно шестью authority-полями: `principal_id`, `role_id`, `profile_id`, `conversation_scope`, `capabilities`, `delivery_target`.
- Считать transport/account/peer identity входом resolver/`PrincipalBinding` и redacted audit metadata, а не дополнительными authority-полями контекста.
- Передавать шесть authority-полей `ResolvedAccessContext` в sessions, memory, prompts, tools, callbacks, background, cron, delegation, compaction, reset, restart и delivery без расширения.
- Запретить model args выбирать foreign namespace, profile, session, memory, delivery target или role.
- Ввести server-side `RolePolicy`, `PrincipalBinding`, `SharedScopeBinding`; effective permissions вычисляются как пересечение role/scope/backend, unknown tools всегда deny.
- Реализовать RBAC и room ACL без OPA/OpenFGA.
- Поддержать ровно четыре идентификатора ролей: `owner`, `family_standard`, `family_sandbox`, `shared_room`; отдельные роли под людей не вводятся.
- Подтвержденная дельта 2026-07-24: typed `shared_room` получает полный серверно настроенный Telegram tool profile для этой комнаты, включая configured MCP и cron; это не означает web/vision/room-memory-only профиль и не дает права придумывать tools/MCP/profile/scope/delivery target из model, command или callback args.
- Для любого tool/backend/background/cron execution доступность остается server-side решением по validated room `ResolvedAccessContext`, configured tool profile и backend policy; unknown или unconfigured tool/MCP всегда deny.
- Каждый shared-room tool/backend/background/cron execution остается связан с тем же room `profile_id`, `conversation_scope` и delivery target: private DM memory/session/attachments, foreign profile/room access, owner/default fallback и private/cross-room delivery запрещены.
- Shared-room cron может доставлять только в тот же server-bound room/topic `delivery_target`; private, cross-room и owner delivery остаются deny.
- Подтвержденная дельта 2026-07-25: Wolfram больше не назначается индивидуальными roster-записями; все personal family profiles получают Wolfram через role policy.
- Восемь `family_standard` profiles получают точный настроенный Wolfram MCP allowlist по умолчанию, а единственный `family_sandbox` сохраняет свой Wolfram MCP allowlist; это не создает пятую роль, отдельную роль под человека или special-case principal.
- `shared_room` не получает Wolfram автоматически и не получает Wolfram MCP, пока отдельная room policy не будет явно одобрена отдельной дельтой; configured room MCP/cron policy остается отдельной от family Wolfram policy.
- Wolfram role policy разрешает только точный настроенный allowlist Wolfram MCP в собственном family profile/context; terminal, host filesystem, browser, delegation, arbitrary MCP, cross-profile search/data, private/cross-profile delivery и foreign delivery остаются deny.
- Подготовить отдельные профили для 9 family `principal` и 2 shared rooms.
- Зафиксировать minor baseline clarification по credential-safe live audit: Руслан является `owner` и сохраняет текущий полный owner profile; current live registry доказанно содержит 1 owner, 9 unique family transport identities и 2 unique shared rooms без дублей, но family entries не имеют безопасных human labels.
- До provisioning/cutover требовать private manually confirmed roster `transport identity -> opaque principal_id -> role_id -> profile_id` для всех девяти family identities; оператор вручную подтверждает одну sandbox binding с `family_sandbox`, остальные восемь получают `family_standard`.
- Считать username/display-name только redacted admin labels: они никогда не являются authority, не используются для auto-link нескольких аккаунтов и не заменяют manual roster confirmation.
- Блокировать provisioning и live cutover при missing, duplicate или ambiguous roster mapping; не угадывать family identity, роль или profile.
- Сделать scoped model secrets так, чтобы они не попадали в tool env.
- Добавить Dashboard Access/Users для redacted status, role preview/confirm/audit и break-glass read-only доступа на 15 минут с reason, reconfirm, manual revoke, без bulk search/export, без model/tools exposure и без content в audit.
- Провести deterministic migration owned DM sessions с сохранением IDs, timestamps, counts и hashes.
- Переносить ambiguous sessions в closed read-only legacy archive; не импортировать global `MEMORY.md`/`USER.md`.
- Держать room topics отдельным namespace внутри room profile.
- Обеспечить backup, dry-run, rollback и тесты.
- Включить live config, migration, restart и Telegram canary в полный change как отдельный explicit delivery gate после реализации, локальных/CI-проверок, dry-run, credential-safe preflight и отдельного одобрения; текущий planning turn не выполняет live effects.

## Новые возможности

- `principal-role-routing`: разрешение trusted transport principal/shared room в профиль, exact Telegram DM routing, deny-before-model/session/tools, фиксированный `ResolvedAccessContext` и отсутствие owner/default fallback.
- `profile-data-isolation`: изоляция `HERMES_HOME`, memory, SQLite sessions, prompt/context, skills, workspace, attachments, scoped model secrets и tool env по каждому principal/profile/shared room.
- `role-capability-policy`: server-side роли, bindings, RBAC, room ACL и effective permissions как пересечение role/scope/backend с deny для unknown/unconfigured tools; personal family roles получают точный configured Wolfram MCP allowlist только в собственном profile/context, а typed `shared_room` использует полный configured Telegram tool profile своей комнаты без автоматического Wolfram и без private/cross-room/owner fallback.
- `access-dashboard`: Dashboard Access/Users, redacted status, role preview/confirm/audit и ограниченный break-glass read-only процесс.
- `principal-migration-rollout`: deterministic migration, legacy archive для ambiguous sessions, backup/dry-run/rollback, тесты и explicit gates для live rollout.

## Измененные возможности

- Измененные возможности появятся только как дельты к указанным capabilities; текущий baseline не меняет live runtime.

## Влияние

- Gateway/Telegram ingress: principal resolution, DM identity checks, malformed/unknown denial, shared room ACL и delivery routing.
- Profile/session/memory/prompt/tool boundaries: profile-aware `HERMES_HOME`, SQLite sessions, memory stores, prompt/context assembly, skills, workspace, attachments, configured room tool profile, tool environment и propagation of `ResolvedAccessContext` через callbacks, background, delegation, cron, compaction, reset и restart.
- Policy layer: новые серверные контракты `RolePolicy`, `PrincipalBinding`, `SharedScopeBinding` и проверка effective permissions перед backend/tool доступом.
- Dashboard: Access/Users UI/API для безопасного управления ролями, аудита и break-glass read-only flow только через localhost/SSH tunnel.
- Migration/rollout: owned DM session migration с preservation guarantees, closed read-only legacy archive, backup/dry-run/rollback, preflight, restart-loop guard, Telegram canaries, privacy warnings и rollback criteria.
- Roster/preflight: private manually confirmed roster для 9 family identities является обязательной входной проверкой provisioning и live cutover; redacted labels не дают authority и не используются для auto-link.
- Delivery gate: live config/migration/restart/Telegram canary являются частью полного change, но запускаются только после реализации, проверок и отдельного явного одобрения; этот planning пакет не выполняет runtime/config/service/live изменений и не создает commits.
