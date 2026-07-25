## ADDED Requirements

### Requirement: Server-side bindings как authority
Система SHALL define server-side `RolePolicy`, `PrincipalBinding` and `SharedScopeBinding` as the only sources of role/profile/scope authority.

#### Scenario: PrincipalBinding accepted русифицирован
- **WHEN** trusted transport identity exactly matches active `PrincipalBinding` в resolver
- **THEN** система SHALL assign only configured `profile_id`, `role_id`, `conversation_scope`, `capabilities` and `delivery_target` from server policy

#### Scenario: SharedScopeBinding accepted русифицирован
- **WHEN** room/topic identity exactly matches active `SharedScopeBinding` в resolver
- **THEN** система SHALL assign only configured shared room `profile_id`, `conversation_scope`, `capabilities` and `delivery_target`

#### Scenario: User-provided role ignored русифицирован
- **WHEN** message text, callback payload или command args contain role/profile claim
- **THEN** система MUST ignore that claim for authorization

### Requirement: Positive intersection permissions для policy
Система MUST compute effective permission as positive intersection of role policy, scope policy and backend/tool capability.

#### Scenario: Allowed intersection русифицирован
- **WHEN** role, scope и backend all allow same capability
- **THEN** система SHALL allow that capability subject to request validation

#### Scenario: Scope denies capability русифицирован
- **WHEN** role allows capability but scope does not allow it в policy
- **THEN** система MUST deny the capability

#### Scenario: Backend denies capability русифицирован
- **WHEN** role and scope allow capability but backend/tool declares it unavailable or unsafe в backend
- **THEN** система MUST deny the capability

#### Scenario: Unknown tool denied русифицирован
- **WHEN** model, command или plugin asks for unknown capability or tool name
- **THEN** система MUST deny before execution and log redacted audit

### Requirement: Маршрутизация MCP использует текущий разрешенный контекст
Система MUST сохранять видимые для backend/model имена MCP tool как имена server/tool, но MUST разрешать маршрутизацию только через привязанный к профилю MCP-пул текущего разрешенного контекста и политику role/scope/backend.

#### Scenario: Видимое для модели имя MCP не является глобальным полномочием
- **WHEN** модель вызывает MCP tool по видимому имени `server/tool`
- **THEN** обработчик MUST разрешить текущий task-local или восстановленный `ResolvedAccessContext`, выбрать только привязанный к профилю пул этого контекста и MUST NOT использовать глобальный handle сервера, захваченный при регистрации

#### Scenario: Два профиля с одинаковым именем MCP server не дают перекрестный вызов
- **WHEN** profile A и profile B предоставляют одинаковое видимое для модели имя MCP `server/tool` с разными refs учетных данных конкретного инструмента
- **THEN** вызов из profile A MUST выполняться только в пуле profile A и MUST NOT достигать конфигурации, процесса, соединения, учетных данных или результатов инструмента из profile B

#### Scenario: Отсутствующие MCP-пул, конфигурация или секрет запрещены
- **WHEN** в текущем контексте отсутствует настроенный MCP-пул, снимок конфигурации, точный allowlist инструментов или обязательный profile-local credential ref
- **THEN** система MUST deny до spawn, соединения или маршрутизации инструмента

#### Scenario: Фоновый MCP не может открыть чужой профиль заново
- **WHEN** фоновая задача, cron, callback или восстановленная задача вызывает MCP после перезапуска
- **THEN** система MUST восстановить исходный контекст из шести полей и MUST NOT открывать, переиспользовать или создавать MCP-пул другого профиля

### Requirement: Owner capability boundary для Руслана
Система SHALL allow owner access only inside current full owner profile and explicit admin surfaces.

#### Scenario: Owner private access русифицирован
- **WHEN** owner uses private DM в текущем профиле
- **THEN** система MAY allow owner private sessions, owner memory, owner prompt, owner workspace, scoped model credentials, authenticated browser, terminal и dashboard admin according to owner policy

#### Scenario: Owner shared room access русифицирован
- **WHEN** owner participates in shared room как участник
- **THEN** система MUST apply shared room scope for that room turn and MUST NOT automatically inject owner private USER/memory into shared prompt

### Requirement: Family standard capability boundary для семьи
Система SHALL давать `family_standard` только personal memory/session search, documents, attachments, vision, public web, image/voice generation, self reminders и точный настроенный Wolfram MCP allowlist по умолчанию; arbitrary MCP остается deny. Восемь `family_standard` profiles получают Wolfram через role policy, без индивидуальных roster-записей для Wolfram и без пятой роли.

#### Scenario: Family standard personal capability личная
- **WHEN** family_standard requests personal memory/session search, documents, attachments, vision, public web, image generation, voice generation или reminder to self
- **THEN** система SHALL allow it only inside family profile, configured `conversation_scope` и server-bound `delivery_target`

#### Scenario: Family standard browser and arbitrary MCP denied запрет
- **WHEN** family_standard requests host shell, host filesystem, logged-in browser, persistent browser, arbitrary MCP, delegation, localhost access, owner credentials или service controls
- **THEN** система MUST deny

#### Scenario: Family standard Wolfram role policy ограничен
- **WHEN** `family_standard` запрашивает вычисление через Wolfram MCP
- **THEN** система SHALL разрешать только точный настроенный allowlist Wolfram MCP внутри собственного family profile/context и MUST deny terminal, host filesystem, browser, delegation, arbitrary MCP, cross-profile search/data, private/cross-profile delivery и foreign delivery

#### Scenario: Family standard Wolfram не выводится из labels
- **WHEN** username/display-name, human label, message text, command args, callback payload или user-supplied capability field пытаются указать Wolfram availability для `family_standard`
- **THEN** система MUST игнорировать их как authority и MUST использовать только server-side role policy, configured toolset и backend intersection

#### Scenario: Family standard cross profile denied запрет
- **WHEN** family_standard requests owner sessions, owner memory, another family profile, shared room private data или cross-principal/profile search
- **THEN** система MUST deny

### Requirement: Граница capabilities для family sandbox
Система SHALL allow `family_sandbox` all `family_standard` capabilities plus own Docker workspace, isolated public browser, Wolfram MCP allowlist and same-profile delegation.

#### Scenario: Docker workspace в sandbox
- **WHEN** `family_sandbox` использует terminal capability
- **THEN** система MUST run terminal in Docker without host mounts, without owner credentials, with `HOME` inside profile, terminal network disabled, 2 vCPU, 2 GiB memory, 256 PID and 5 GiB writable disk

#### Scenario: Sandbox public browser изолированный
- **WHEN** family_sandbox uses browser capability в своем профиле
- **THEN** система MUST use isolated public browser state without owner authenticated cookies, logged-in browser profile, localhost access или persistent owner profile

#### Scenario: Sandbox Wolfram allowlist для MCP
- **WHEN** family_sandbox requests Wolfram MCP computation через allowlist
- **THEN** система SHALL allow only configured Wolfram MCP allowlist inside own `family_sandbox` profile/context and MUST deny arbitrary MCP tools; this sandbox policy is retained as part of all personal family role policy

#### Scenario: Sandbox same-profile delegation только same-profile
- **WHEN** family_sandbox delegates work to subagent или worker
- **THEN** система SHALL allow delegation only inside same `profile_id`, `conversation_scope`, `capabilities` и `delivery_target`

#### Scenario: Sandbox escape denied для Docker
- **WHEN** family_sandbox terminal command attempts network access, symlink escape, host path mount, owner credential injection или quota bypass
- **THEN** система MUST deny before container start или run with isolation that prevents escape

### Requirement: Shared room capability boundary для комнат
Система SHALL выдавать typed `shared_room` полный серверно настроенный Telegram tool profile этой комнаты, включая configured MCP и cron, только внутри room profile и room `conversation_scope`.

#### Scenario: Shared room private data denied запрет
- **WHEN** shared_room turn asks for participant private DM sessions, private memory, private attachments, private delivery, foreign profile/room access, owner/default fallback или cross-principal/profile search
- **THEN** система MUST deny, unless отдельный explicit shared artifact существует в том же room scope и backend policy разрешает его

#### Scenario: Shared room allowed context комнаты
- **WHEN** shared_room turn asks about room-visible recent context, documents, attachments, vision, public web, room-scoped memory или другой configured room tool
- **THEN** система SHALL использовать только configured room profile, `conversation_scope`, configured Telegram tool profile и backend policy

#### Scenario: Shared room configured MCP allowed комнаты
- **WHEN** shared_room turn requests configured MCP tool из room Telegram tool profile
- **THEN** система SHALL разрешить его только через привязанный к профилю пул этой комнаты после того, как validated room `ResolvedAccessContext`, configured tool profile и backend policy разрешили exact tool; `shared_room` MUST NOT получать Wolfram автоматически и MUST NOT получать Wolfram MCP, пока отдельная room policy не будет явно одобрена отдельной дельтой

#### Scenario: Shared rooms используют только настроенный MCP комнаты
- **WHEN** две shared rooms настраивают разные наборы MCP или у одной комнаты отсутствует настройка MCP
- **THEN** каждая комната MUST предоставлять и маршрутизировать только собственный настроенный MCP для своих room `profile_id` и `conversation_scope`; отсутствующий room MCP MUST deny до spawn и MUST NOT использовать owner, family, default или MCP другой комнаты как запасной источник

#### Scenario: Shared room unknown tool denied запрет
- **WHEN** model, command или callback asks for tool name, MCP server, capability, profile, scope или delivery target, отсутствующий в configured room profile
- **THEN** система MUST отказать до execution и MUST NOT придумывать или выводить tool availability из model text

### Requirement: Self reminder and cron policy для ролей
Система MUST enforce cron and reminders by role, scope and server-bound `delivery_target`.

#### Scenario: Family self reminder allowed только себе
- **WHEN** family_standard или family_sandbox schedules reminder to self
- **THEN** cron job SHALL store and fire with that family `ResolvedAccessContext` and server-bound `delivery_target` для себя

#### Scenario: Family private delivery denied запрет
- **WHEN** family_standard или family_sandbox cron attempts delivery to another principal, shared room или owner default delivery target
- **THEN** система MUST deny before persisting or firing job

#### Scenario: Shared room cron same room allowed комнаты
- **WHEN** shared_room turn schedules configured cron или reminder for the same room/topic
- **THEN** cron job SHALL сохраняться и выполняться с тем же room `ResolvedAccessContext`, `conversation_scope` и server-bound room/topic `delivery_target`

#### Scenario: Shared room cron private delivery denied запрет
- **WHEN** shared_room cron attempts private DM delivery, cross-room delivery или owner default delivery
- **THEN** система MUST deny before persisting or firing job

### Requirement: Policy covers non-chat execution paths для фоновых путей
Система MUST enforce role/scope/backend policy for callbacks, background jobs, cron, delegation, compaction, reset, restart and concurrent turns.

#### Scenario: Background policy русифицирован
- **WHEN** background process emits progress, tool calls или final delivery
- **THEN** each action MUST be checked against original context effective permissions перед выполнением

#### Scenario: Restart policy русифицирован
- **WHEN** gateway restarts with active background или resumable sessions
- **THEN** система MUST reload persisted context and deny any task whose context cannot be validated

#### Scenario: Delegation policy русифицирован
- **WHEN** non-owner role requests delegation в runtime
- **THEN** система MUST allow only `family_sandbox` same-profile delegation and MUST deny delegation for `family_standard` and `shared_room`

### Requirement: Role is not room membership независимо
Система MUST keep role assignment and room membership independent.

#### Scenario: Room participant without DM role участник
- **WHEN** room participant has room membership but no private `PrincipalBinding` для DM
- **THEN** система MUST allow only configured room scope interactions and MUST deny private DM profile creation

#### Scenario: DM role without room membership отдельно
- **WHEN** family principal has private DM role but is not in shared room binding для комнаты
- **THEN** система MUST deny that principal access to shared room scope
