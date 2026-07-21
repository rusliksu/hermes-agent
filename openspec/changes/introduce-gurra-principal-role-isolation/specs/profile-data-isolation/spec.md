## ADDED Requirements

### Requirement: Один profile на principal или room
Система SHALL maintain exactly one active Hermes profile per configured owner principal, each of 9 family principals, and each of 2 shared rooms.

#### Scenario: Проверка количества profiles
- **WHEN** bindings loaded for rollout проверки
- **THEN** система MUST validate one owner profile, 9 family profiles и 2 shared room profiles without duplicate active bindings

#### Scenario: Нет shared private profile
- **WHEN** two family principals interact through private DMs в личных чатах
- **THEN** система MUST store their sessions, memory, prompt private files, skills и workspace in distinct profiles

### Requirement: Profile-bound HERMES_HOME для профиля
Система MUST resolve `HERMES_HOME` from `ResolvedAccessContext` on request path and MUST NOT rely on module/import-time path state.

#### Scenario: Module cached path rejected русифицирован
- **WHEN** downstream component has cached module-level home path from another profile в request path
- **THEN** request processing MUST use context-bound profile home или deny operation

#### Scenario: os.getenv auth fallback rejected русифицирован
- **WHEN** request path code would read auth, model или profile authority from `os.getenv`
- **THEN** система MUST use server-bound config/policy attached to context или deny

### Requirement: Session isolation между профилями
Система SHALL isolate SQLite sessions by profile and MUST prevent cross-profile session search, resume, reset, compaction and transcript access.

#### Scenario: Pairwise session isolation русифицирован
- **WHEN** any two of owner, 9 family principals и 2 shared rooms search, resume или reset sessions
- **THEN** each actor MUST see only sessions in own profile или authorized room scope

#### Scenario: Guessed session ID denied русифицирован
- **WHEN** principal guesses valid session ID from another profile вручную
- **THEN** система MUST deny lookup, resume, export, delete, compaction и reset for that ID

#### Scenario: Concurrent session writes русифицирован
- **WHEN** two profiles write messages concurrently в одном процессе
- **THEN** writes MUST go to own profile session stores и MUST preserve independent message counts

### Requirement: Memory isolation между профилями
Система SHALL bind memory namespaces to server-resolved profile/scope and MUST prevent cross-profile memory reads and writes.

#### Scenario: Private memory isolation русифицирован
- **WHEN** family principal asks about owner private memory или another family principal memory
- **THEN** система MUST not retrieve, summarize, search или expose that memory

#### Scenario: Shared room memory isolation русифицирован
- **WHEN** shared room context is assembled для prompt
- **THEN** система MUST include only room-scoped memory explicitly enabled for that room и MUST NOT include private USER/memory files

#### Scenario: Memory tool namespace русифицирован
- **WHEN** memory tools are invoked from any role в tool handler
- **THEN** tool handlers MUST use server-bound namespace from `ResolvedAccessContext` и MUST deny unknown or mismatched namespaces

### Requirement: Prompt and private context isolation по слоям
Система MUST assemble prompts in layers security -> read-only role -> scope -> private USER/memory, and prompt text MUST NOT grant permissions.

#### Scenario: Private prompt layer русифицирован
- **WHEN** owner private DM prompt is assembled для владельца
- **THEN** система MAY include owner private USER/memory layer и MUST exclude family/shared private layers

#### Scenario: Shared prompt layer русифицирован
- **WHEN** shared room prompt is assembled для комнаты
- **THEN** система MUST include shared room scope context only и MUST exclude all private DM USER/memory layers

#### Scenario: Prompt injection cannot grant access русифицирован
- **WHEN** user text asks model to enable owner tools, owner memory или another profile
- **THEN** server-side enforcement MUST keep effective permissions unchanged для этого запроса

### Requirement: Attachments and filesystem isolation по профилю
Система SHALL store and read attachments, generated files and workspaces only inside resolved profile boundary.

#### Scenario: Attachment isolation русифицирован
- **WHEN** family principal uploads file в личном профиле
- **THEN** file storage, references and later retrieval MUST remain inside that family profile и MUST NOT be visible to owner or other family profiles unless explicitly copied through approved shared scope

#### Scenario: Filesystem guessed path denied русифицирован
- **WHEN** tool call references another profile path, guessed host path или symlink escape
- **THEN** система MUST deny operation before filesystem access

#### Scenario: Shared room attachment scope русифицирован
- **WHEN** attachment uploaded in shared room профиле
- **THEN** система MUST bind it to that room profile и MUST NOT make it available to private DMs by default

### Requirement: Tool environment and model secret isolation для секретов
Система MUST keep scoped model secrets out of tool env and MUST prevent owner credentials from reaching family/shared tools.

#### Scenario: Tool env redaction русифицирован
- **WHEN** tool process spawned for family или shared role
- **THEN** environment MUST exclude owner credentials, model secrets и unrelated profile paths

#### Scenario: Model secret use русифицирован
- **WHEN** model request is authorized for role сервером
- **THEN** model credentials MUST be available only to model client construction и MUST NOT be exported to terminal, Docker, browser или MCP tool env

### Requirement: Browser and public web state isolation по ролям
Система SHALL isolate public web, browser state and search state by role/profile.

#### Scenario: Family standard public web only без browser
- **WHEN** family_standard использует public web capability
- **THEN** система MUST provide server-mediated public web access без persistent browser, logged-in browser state, owner cookies, localhost access или private network access

#### Scenario: Family sandbox public browser isolation для Юли
- **WHEN** family_sandbox использует browser capability
- **THEN** система MUST use isolated public browser state без owner authenticated cookies, logged-in browser profile или persistent owner profile

#### Scenario: Search isolation русифицирован
- **WHEN** any non-owner searches sessions, files, browser state или web-derived cached data
- **THEN** система MUST restrict results to resolved profile/scope и MUST deny cross-profile guessed IDs

#### Scenario: Authenticated browser non-goal русифицирован
- **WHEN** non-owner requests authenticated persistent browser in v1 режиме
- **THEN** система MUST deny and preserve unauthenticated public boundary
