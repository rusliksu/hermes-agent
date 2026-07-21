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

### Requirement: Owner capability boundary для Руслана
Система SHALL allow owner access only inside current full owner profile and explicit admin surfaces.

#### Scenario: Owner private access русифицирован
- **WHEN** owner uses private DM в текущем профиле
- **THEN** система MAY allow owner private sessions, owner memory, owner prompt, owner workspace, scoped model credentials, authenticated browser, terminal и dashboard admin according to owner policy

#### Scenario: Owner shared room access русифицирован
- **WHEN** owner participates in shared room как участник
- **THEN** система MUST apply shared room scope for that room turn and MUST NOT automatically inject owner private USER/memory into shared prompt

### Requirement: Family standard capability boundary для семьи
Система SHALL give `family_standard` only personal memory/session search, documents, attachments, vision, public web, image/voice generation and self reminders.

#### Scenario: Family standard personal capability личная
- **WHEN** family_standard requests personal memory/session search, documents, attachments, vision, public web, image generation, voice generation или reminder to self
- **THEN** система SHALL allow it only inside family profile, configured `conversation_scope` и server-bound `delivery_target`

#### Scenario: Family standard browser and MCP denied запрет
- **WHEN** family_standard requests host shell, host filesystem, logged-in browser, persistent browser, arbitrary MCP, Wolfram MCP, delegation, localhost access, owner credentials или service controls
- **THEN** система MUST deny

#### Scenario: Family standard cross profile denied запрет
- **WHEN** family_standard requests owner sessions, owner memory, another family profile, shared room private data или cross-principal/profile search
- **THEN** система MUST deny

### Requirement: Family sandbox capability boundary для Юли
Система SHALL allow `family_sandbox` all `family_standard` capabilities plus own Docker workspace, isolated public browser, Wolfram MCP allowlist and same-profile delegation.

#### Scenario: Юлин Docker sandbox
- **WHEN** Юля uses terminal capability under `family_sandbox`
- **THEN** система MUST run terminal in Docker without host mounts, without owner credentials, with `HOME` inside profile, terminal network disabled, 2 vCPU, 2 GiB memory, 256 PID and 5 GiB writable disk

#### Scenario: Sandbox public browser изолированный
- **WHEN** family_sandbox uses browser capability в своем профиле
- **THEN** система MUST use isolated public browser state without owner authenticated cookies, logged-in browser profile, localhost access или persistent owner profile

#### Scenario: Sandbox Wolfram allowlist для MCP
- **WHEN** family_sandbox requests Wolfram MCP computation через allowlist
- **THEN** система SHALL allow only configured Wolfram MCP allowlist and MUST deny arbitrary MCP tools

#### Scenario: Sandbox same-profile delegation только same-profile
- **WHEN** family_sandbox delegates work to subagent или worker
- **THEN** система SHALL allow delegation only inside same `profile_id`, `conversation_scope`, `capabilities` и `delivery_target`

#### Scenario: Sandbox escape denied для Docker
- **WHEN** family_sandbox terminal command attempts network access, symlink escape, host path mount, owner credential injection или quota bypass
- **THEN** система MUST deny before container start или run with isolation that prevents escape

### Requirement: Shared room capability boundary для комнат
Система SHALL restrict `shared_room` to room sessions/memory, documents, attachments, vision and public web inside room scope.

#### Scenario: Shared room private data denied запрет
- **WHEN** shared_room turn asks for participant private DM sessions, private memory, private attachments, cron, private delivery, shell, delegation или cross-principal/profile search
- **THEN** система MUST deny unless separate explicit shared artifact exists in room scope

#### Scenario: Shared room allowed context комнаты
- **WHEN** shared_room turn asks about room-visible recent context, documents, attachments, vision, public web или room-scoped memory
- **THEN** система SHALL use only configured room profile and `conversation_scope`

### Requirement: Self reminder and cron policy для ролей
Система MUST enforce cron and reminders by role, scope and server-bound `delivery_target`.

#### Scenario: Family self reminder allowed только себе
- **WHEN** family_standard или family_sandbox schedules reminder to self
- **THEN** cron job SHALL store and fire with that family `ResolvedAccessContext` and server-bound `delivery_target` для себя

#### Scenario: Family private delivery denied запрет
- **WHEN** family_standard или family_sandbox cron attempts delivery to another principal, shared room или owner default delivery target
- **THEN** система MUST deny before persisting or firing job

#### Scenario: Shared room cron denied запрет
- **WHEN** shared_room turn attempts cron, reminder или private delivery
- **THEN** система MUST deny

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
