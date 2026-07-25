## Контекст

Hermes уже имеет несколько точек, где границы principal могут разойтись: gateway ingress в `gateway/run.py`, Telegram adapter в plugin platform, session/state слой `gateway/session.py` и `hermes_state.py`, profile-aware пути через `hermes_constants.get_hermes_home()`, memory managers в `agent/memory_manager.py`, tool dispatch в `model_tools.py` и `tools/*`, cron в `cron/*`, dashboard backend в `hermes_cli/web_server.py`, dashboard auth в `hermes_cli/dashboard_auth/*`, а также web UI в `web/src/*`. Существующие тесты вокруг Telegram auth, resume/search, cron profile isolation, browser hardening и dashboard auth показывают, что часть границ уже есть, но они не образуют единый immutable access contract.

Новый baseline должен обслуживать одного owner, 9 family principals и 2 shared rooms через один gateway multiplex. Главная security-граница проходит не через prompt и не через room membership, а через серверное разрешение trusted transport identity в immutable `ResolvedAccessContext` на ingress. Если контекст не разрешен точно, запрос обязан завершиться deny до model/session/tools.

Minor baseline clarification по credential-safe live audit: Руслан явно является `owner` и сохраняет текущий полный owner profile. Current live registry доказанно содержит 1 owner, 9 unique family transport identities и 2 unique shared rooms без дублей, но family entries не имеют безопасных human labels. До provisioning или cutover нужен private manually confirmed roster `transport identity -> opaque principal_id -> role_id -> profile_id` для всех девяти family identities.

Оператор вручную подтверждает одну sandbox binding: только эта binding получает `family_sandbox`, остальные восемь family identities получают `family_standard`. Display name/username разрешены только как redacted admin labels; они никогда не являются authority, никогда не используются для auto-link нескольких аккаунтов и не заменяют private roster confirmation. Missing, duplicate или ambiguous roster mapping блокирует provisioning и live cutover; система и операторские tooling не должны угадывать.

`ResolvedAccessContext` имеет ровно шесть authority-полей: `principal_id`, `role_id`, `profile_id`, `conversation_scope`, `capabilities`, `delivery_target`. Transport/account/peer identity являются входом resolver/`PrincipalBinding` и redacted audit metadata отдельно, но не дополнительными authority-полями контекста.

Этот planning пакет описывает полный change, включая live config, migration, restart и Telegram canary как явный delivery gate. В текущем turn live effects, runtime/config/service изменения, credentials/env/session/memory чтение и commits не выполняются.

Подтвержденная дельта 2026-07-24: typed `shared_room` получает полный серверно настроенный Telegram tool profile, включая configured MCP и cron, вместо прежней формулировки web/vision/room-memory-only. Это расширяет только формулировку shared-room policy: доступность каждого tool/backend/background/cron execution по-прежнему вычисляется сервером из validated room `ResolvedAccessContext`, configured tool profile и backend policy. Model, command и callback payload не могут придумывать tool names, capabilities, profile, scope или delivery target; unknown или unconfigured tool/MCP остается deny. Все execution paths остаются привязаны к тому же room `profile_id`, `conversation_scope` и room/topic `delivery_target`; private DM memory/session/attachments, foreign profile/room access, owner/default fallback и private/cross-room/owner delivery остаются deny.

Подтвержденная дельта 2026-07-25: идентификаторы ролей остаются ровно `owner`, `family_standard`, `family_sandbox`, `shared_room`. Wolfram больше не назначается индивидуальными roster-записями: все personal family profiles получают Wolfram через role policy. Восемь `family_standard` profiles получают точный настроенный Wolfram MCP allowlist по умолчанию, а единственный `family_sandbox` сохраняет свой Wolfram MCP allowlist. Это не создает пятую роль, отдельную роль под человека или special-case principal. Username/display-name не являются authority, proof или источником назначения. Wolfram role policy действует только для точного настроенного allowlist Wolfram MCP в собственном family profile/context и не дает terminal, host filesystem, browser, delegation, arbitrary MCP, cross-profile search/data, private/cross-profile delivery или foreign delivery. `shared_room` не получает Wolfram автоматически и не получает Wolfram MCP, пока отдельная room policy не будет явно одобрена отдельной дельтой; shared-room configured MCP/cron policy остается отдельной.

## Цели / Не-цели

**Цели:**

- Ввести immutable `ResolvedAccessContext` на ingress и передавать шесть authority-полей через request path, callbacks, background, cron, delegation, compaction, reset, restart и concurrent turns.
- Обеспечить exact Telegram DM identity: `platform + account + peer_kind + user_id`, где для DM `user_id == chat_id`; mismatch, missing, malformed и unknown дают deny без fallback.
- Закрепить правило: один `principal` или shared room соответствует ровно одному Hermes profile, а один Telegram bot/gateway multiplex обслуживает эти профили без смешивания данных.
- Развести понятия role и room membership: участие в комнате не выдает роль, а роль не добавляет участника в комнату.
- Реализовать server-side `RolePolicy`, `PrincipalBinding`, `SharedScopeBinding` и positive intersection role/scope/backend; unknown capability/tool всегда deny.
- Задать prompt layering: security layer -> read-only role layer -> scope layer -> private USER/memory layer; prompt может сужать поведение, но не выдает права.
- Устранить на request path module/import-time `HERMES_HOME` и `os.getenv` auth fallbacks; `session_search` и memory namespace становятся server-bound.
- Зафиксировать roster/preflight baseline: provisioning 9 family profiles, одна manually confirmed `family_sandbox` binding и восемь `family_standard` bindings возможны только после private manually confirmed roster для всех девяти family transport identities.
- Зафиксировать capability границы для `owner`, `family_standard`, `family_sandbox`, `shared_room`, включая Docker sandbox для `family_sandbox` без host mounts/owner credentials, Wolfram role policy для всех personal family profiles и полный серверно настроенный Telegram tool profile для typed shared rooms без автоматического Wolfram, private/cross-room/owner fallback.
- Добавить Dashboard Access/Users только для localhost/SSH tunnel, role preview/confirm/audit и break-glass 15m read-only с reason, reconfirm и manual revoke.
- Описать deterministic DM migration с сохранением IDs/timestamps/counts/hashes, ambiguous legacy archive, room/topic mapping, backup/dry-run/rollback и explicit live gate.

**Не-цели:**

- Отдельные Telegram bots для каждого family principal или room.
- Authenticated persistent browser для non-owner в v1.
- OPA/OpenFGA или другой внешний policy engine.
- Bulk search/export, model exposure или tool-content exposure в dashboard.
- Изменение Telegram/model permissions через break-glass.
- Импорт global `MEMORY.md`/`USER.md` в family или shared профили.
- Live rollout без отдельного explicit delivery approval.

## Решения

### 1. `ResolvedAccessContext` является единственным request-scoped authority

Решение: gateway на ingress строит immutable `ResolvedAccessContext` из trusted adapter metadata и binding tables. Контекст содержит ровно `principal_id`, `role_id`, `profile_id`, `conversation_scope`, `capabilities`, `delivery_target`. Любой downstream код получает эти шесть authority-полей явно или через server-owned context wrapper; prompt text, user input, slash args, callback payload и guessed IDs не могут заменить этот authority.

Transport/account/peer identity не являются authority-полями downstream контекста. Они используются как вход resolver/`PrincipalBinding` и сохраняются только как redacted audit metadata, достаточная для диагностики без raw IDs, content или secrets.

Для Telegram DM identity точное правило: `platform == telegram`, известный `account`, `peer_kind == dm`, `user_id` присутствует, `chat_id` присутствует, `user_id == chat_id`, binding active. Любое расхождение дает deny до session lookup, model init, tool schema staging и memory hydration. Fallback в owner/default/profile-last-used отсутствует.

В callbacks, background jobs, delegation workers, cron, compaction, reset и restart передаются те же шесть authority-полей без расширения. Model args не могут выбрать foreign namespace, profile, session store, memory namespace, role или delivery target.

Альтернатива: разрешать principal позднее в session layer. Отклонено, потому что session/search/memory уже являются чувствительными ресурсами и не должны видеть unknown ingress.

### 2. Одна связка principal/room = один profile, один gateway multiplex

Решение: для owner, каждого из 9 family principals и каждой из 2 shared rooms создается отдельный profile с собственным `HERMES_HOME`, sessions DB, memory namespace, prompt/private files, skills и workspace. Один Telegram bot/gateway multiplex маршрутизирует events в соответствующий profile по `ResolvedAccessContext`.

Owner binding закрепляет Руслана как `owner` и сохраняет текущий полный owner profile. Для family provisioning входным gate является private manually confirmed roster для всех девяти unique family transport identities: каждая запись связывает exact transport identity с opaque `principal_id`, `role_id` и `profile_id`. Current live registry уже доказал отсутствие дублей для 1 owner, 9 family identities и 2 shared rooms, но отсутствие безопасных human labels означает, что роли family нельзя назначать по display name/username.

Оператор вручную подтверждает одну sandbox binding. Только она получает `family_sandbox`; остальные восемь family identities получают `family_standard`. Wolfram не назначается через private roster: все personal family profiles получают его через role policy после валидного role/profile roster. Username/display-name сохраняются максимум как redacted admin labels для операторского UI/audit и не являются authority. Любой missing, duplicate или ambiguous roster mapping для identity, role или profile блокирует profile provisioning, migration preflight и live cutover.

Shared room разрешается через `SharedScopeBinding`, а не через principal роль. Room membership дает только room scope, если binding активен; роль principal остается отдельной. Один человек может иметь DM role и room membership, но effective permission считается пересечением role/scope/backend.

Альтернатива: один общий shared profile для семьи. Отклонено, потому что он смешивает private sessions, memory, attachments, browser state и guessed IDs, а также делает rollback/migration недетерминированными.

### 3. Policy model fail-closed и positive intersection

Решение: `RolePolicy` задает allowlist capabilities, tools, backends, network modes, filesystem modes, browser modes, dashboard modes и model controls для роли. `PrincipalBinding` связывает exact transport identity с profile и role. `SharedScopeBinding` связывает room/topic/chat с shared profile и scope. Effective permission существует только при положительном пересечении role allowlist, scope allowlist и backend/tool capabilities. Unknown capability, unknown tool, disabled backend или отсутствующий scope всегда deny.

Роли:

- `owner`: текущий полный профиль Руслана, включая owner private memory, owner sessions, owner prompt/context, owner workspace, scoped model credentials, authenticated browser, terminal в рамках owner-approved boundaries, dashboard admin, migration/rollback gates и owner policy для delegation/cron.
- `family_standard`: только личные memory/session search, документы, attachments, vision, public web, image/voice generation, reminders самому себе и точный настроенный Wolfram MCP allowlist с server-bound `delivery_target`; без host shell, host filesystem, logged-in browser, arbitrary MCP, delegation, private delivery к другим адресатам и cross-principal/profile search.
- `family_sandbox`: все возможности `family_standard` плюс собственный Docker workspace, isolated public browser, Wolfram MCP allowlist и delegation только внутри собственного `profile_id`; роль назначается только одной manually confirmed sandbox binding.
- `shared_room`: полный серверно настроенный Telegram tool profile для этой комнаты, включая configured MCP и cron, только внутри room profile/scope и через server-side availability intersection. Room sessions/memory, документы, attachments, vision, public web и другие configured room tools разрешаются только если они есть в room `ResolvedAccessContext`, configured tool profile и backend policy. Private DM memory/session search, private attachments, foreign profile/room access, owner/default fallback, придуманные моделью tools/MCP, shell/delegation/logged-in browser без явной room/backend allowlist, private delivery и cross-principal/profile search остаются deny.

Альтернатива: per-table `user_id` only. Отклонено, потому что таблицы не покрывают prompts, imports, tool env, browser state, attachments, cron/background callbacks и module-level path caches.

### 4. Prompt layers не являются security boundary

Решение: prompt assembly строится слоями: security layer -> read-only role layer -> scope layer -> private USER/memory layer. Security layer описывает неизменяемые запреты. Role layer может добавлять read-only guidance. Scope layer описывает room/profile scope. Private USER/memory layer подключается только для matching private profile. Prompt не может выдать capability, расширить filesystem, включить tool или поменять profile; enforcement происходит до prompt и перед каждым tool/backend call.

Альтернатива: выдавать роль через system prompt. Отклонено, потому что prompt инъекция и model obedience не являются authorization mechanism.

### 5. Request path устраняет import-time и env fallback источники authority

Решение: path-sensitive компоненты должны читать profile home через request-scoped context, а не через module/import-time `HERMES_HOME`. Любые `os.getenv` auth fallbacks на request path заменяются server-bound config/policy объектами. `session_search`, memory tools, memory hydration/commit, configured tool/MCP/cron availability и context-engine namespace получают profile/session scope из server-owned `ResolvedAccessContext`.

Это касается cached agents, callbacks, background tasks, cron jobs, delegation workers, compaction, reset/restart recovery и concurrent turns: при resume или delayed callback контекст берется из persisted server-bound metadata, а не угадывается по transport IDs, tool names из model args или текущему process env.

Альтернатива: сохранять env bridge как совместимость. Отклонено для request path, потому что env является process-global и ломает concurrent multi-profile isolation.

### 6. Family sandbox и browser/network границы фиксируются явно

Решение: `family_standard` не получает browser capability. Для этой роли доступен только public web как server-mediated fetch/search без persistent/logged-in browser state, без localhost/private network и без owner cookies. Wolfram MCP для `family_standard` разрешается role policy по умолчанию только как точный настроенный allowlist Wolfram MCP в собственном family profile/context. Terminal, host filesystem, browser, delegation, arbitrary MCP, cross-profile search/data и private/cross-profile delivery остаются deny. `family_sandbox` terminal запускается только в Docker sandbox: без host mounts, без owner credentials, `HOME` внутри profile, terminal network disabled, лимиты 2 vCPU, 2 GiB memory, 256 PID и 5 GiB writable disk.

`family_sandbox` дополнительно получает isolated public browser без owner authenticated cookies или persistent owner profile, а также Wolfram MCP allowlist. Generic network, arbitrary MCP, localhost access и authenticated persistent browser остаются deny, если явно не разрешены role/scope/backend intersection.

Альтернатива: общий terminal с profile cwd. Отклонено, потому что cwd не защищает owner files, credentials, browser state и ambient network.

### 7. Dashboard Access/Users локален и не раскрывает секреты

Решение: dashboard Access/Users доступен только через localhost/SSH tunnel. UI/API показывают redacted status, role preview, explicit confirm и audit. Role/binding change применяется атомарно только после preview, confirm и audit persistence. Break-glass lease scoped на один target profile/session set, длится максимум 15 минут, read-only history only, требует reason, reconfirm, показывает privacy warning, может быть manually revoked и не переживает restart.

Break-glass не разрешает bulk search/export, передачу содержимого модели/tools, credentials exposure, private memory export, изменение Telegram permissions или изменение model permissions. Audit хранит только redacted metadata без content, message bodies, tool outputs, prompts или secrets.

Альтернатива: full admin dashboard для всех roles. Отклонено из-за высокого риска случайного раскрытия model, memory и sessions.

### 8. Migration/rollout детерминированны и поставляются slices

Решение: migration переносит все однозначные owned DM sessions в profile DBs с сохранением IDs, timestamps, message counts и content hashes. Ambiguous legacy rows уходят в closed read-only archive. Global `MEMORY.md`/`USER.md` и personal context не импортируются. Room/topic mapping строит deterministic shared scopes; topics живут отдельным namespace внутри room profile и не склеиваются с DM history.

Rollout делится на additive slices с compare mode, backup, dry-run, strict validation, credential-safe live preflight, Telegram canaries, service active/no restart loop checks, privacy warnings и rollback. Post-rollout canary выполняется отдельно для owner, единственной sandbox family binding с Wolfram role policy, восьми `family_standard` bindings с Wolfram role policy, обеих rooms без автоматического Wolfram, unknown и malformed ingress. Любой fail в canary, active/running check для `hermes-gateway` или `hermes-dashboard`, restart-loop guard или privacy warning ведет к rollback.

Альтернатива: отдельные bots. Отклонено для baseline, потому что увеличивает operational surface, Bot API management и delivery complexity, не устраняя server-side isolation требования внутри Hermes.

## Риски / Компромиссы

- [Риск] Legacy rows без достаточной identity нельзя безопасно классифицировать. -> Митигация: переносить их только в closed read-only legacy archive с hashes/counts, без импорта в active profiles.
- [Риск] Cached agents могут удерживать старый profile home или memory manager. -> Митигация: cache key включает `ResolvedAccessContext` profile/session scope, reset/restart очищают cross-profile cached state, tests покрывают concurrent turns.
- [Риск] Существующие tools могут читать `HERMES_HOME` или auth из env на import/request path. -> Митигация: audit request path, заменить на context-bound providers, unknown или non-migrated tool deny для family/shared roles.
- [Риск] Dashboard break-glass может стать обходом private boundary. -> Митигация: localhost/SSH tunnel only, one target profile/session set, 15m read-only history lease, reason/reconfirm/manual revoke, audit без content, no bulk/model/tools exposure.
- [Риск] Telegram IDs могут быть malformed или не соответствовать DM semantics. -> Митигация: exact DM rule `user_id == chat_id`, strict type validation, fail-closed denial before model/session/tools.
- [Риск] Docker sandbox limits могут ломать полезные family tasks. -> Митигация: v1 принимает constrained sandbox, расширение требует material OpenSpec delta и отдельного approval.
- [Риск] Live restart может зациклиться или поднять неверный profile mapping. -> Митигация: preflight, service active check, restart-loop guard, canaries, rollback target и no-live gate до отдельного approval.

## План миграции

1. Реализовать additive server contracts: `ResolvedAccessContext`, bindings, role policies, deny-before-model/session/tools gates и tests.
2. Подключить profile-bound sessions, memory, prompt layers, attachments, tools, callbacks, background, cron, delegation, compaction, reset/restart и concurrent turn propagation в compare mode.
3. Добавить family/shared role capability enforcement, self reminders, shared-room same-room cron delivery, scoped secrets, Docker sandbox для `family_sandbox`, isolated public browser, Wolfram role policy для всех personal family profiles и same-profile delegation.
4. Добавить dashboard Access/Users с role preview/confirm/audit и break-glass lease invariants.
5. Реализовать migration tooling с backup, dry-run, deterministic hashes/counts, ambiguous legacy archive, room topic namespaces и rollback.
6. Выполнить private roster/preflight validation: 9 family transport identities mapped to opaque `principal_id -> role_id -> profile_id`, одна binding manually confirmed as only `family_sandbox`, остальные восемь `family_standard`, no missing/duplicate/ambiguous mappings, username/display-name redacted and non-authoritative.
7. Выполнить local/CI validation, compare reports и privacy review без live effects.
8. Только после отдельного explicit approval выполнить live delivery gate: backup live refs/config, live preflight, apply config/migration, restart gateway/dashboard, verify service active/no restart loop, Telegram DM/shared/unknown/malformed canaries и privacy warnings.
9. Rollback: остановить дальнейшие canaries, восстановить saved refs/config/profile mappings, восстановить DB/files из backup when migration gate touched data, выполнить один restart, проверить service active и denial of unknown ingress.

## Открытые вопросы

Блокирующих вопросов для baseline нет. Принятые допущения: один gateway multiplex вместо separate bots; no OPA/OpenFGA; no authenticated persistent browser for non-owner v1; no global `MEMORY.md`/`USER.md` import; live rollout только через explicit delivery gate после implementation validation.
