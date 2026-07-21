## ADDED Requirements

### Requirement: Разрешение Telegram DM principal
Система SHALL разрешать Telegram DM только по exact trusted identity `platform + account + peer_kind + user_id`, и для DM MUST требовать `user_id == chat_id`.

#### Scenario: Owner DM разрешен
- **WHEN** Telegram DM приходит от configured owner binding с matching `account`, `peer_kind=dm`, `user_id` и `chat_id`
- **THEN** система SHALL создать immutable `ResolvedAccessContext` для owner profile до доступа к model, session или tools

#### Scenario: Family DM разрешен
- **WHEN** Telegram DM приходит от любого из 9 configured family bindings с matching `account`, `peer_kind=dm`, `user_id` и `chat_id`
- **THEN** система SHALL создать `ResolvedAccessContext` для соответствующего family profile и role

#### Scenario: DM user_id и chat_id не совпадают
- **WHEN** Telegram DM содержит разные `user_id` и `chat_id`
- **THEN** система MUST отказать до session lookup, model init, memory hydration и tool schema staging

### Requirement: Разрешение shared room principal
Система SHALL разрешать 2 configured shared rooms через `SharedScopeBinding`, не выводя роль principal из room membership.

#### Scenario: Shared room разрешена
- **WHEN** сообщение приходит из configured shared room или topic с active `SharedScopeBinding`
- **THEN** система SHALL использовать shared room profile и room `conversation_scope`, сохранив sender identity только как redacted audit metadata

#### Scenario: Room membership не выдает role
- **WHEN** family principal пишет в shared room
- **THEN** система MUST не повышать role этого principal и MUST вычислить permissions только через пересечение role/scope/backend

### Requirement: Проверенный семейный roster до provisioning и cutover
Система MUST требовать private manually confirmed roster `transport identity -> opaque principal_id -> role_id -> profile_id` для всех девяти family transport identities до provisioning, migration preflight или live cutover.

#### Scenario: Owner baseline сохраняется
- **WHEN** baseline registry preflight выполняется
- **THEN** система MUST считать Руслана configured `owner` и MUST сохранить его текущий полный owner profile без понижения роли или смены profile

#### Scenario: Уникальность live registry является предусловием
- **WHEN** credential-safe live audit сообщает 1 owner, 9 unique family transport identities и 2 unique shared rooms без дублей
- **THEN** система MAY использовать этот факт только как uniqueness baseline и MUST NOT считать family identities безопасно human-labeled

#### Scenario: Private roster связывает все family identities
- **WHEN** provisioning или migration preflight запускается для family profiles
- **THEN** система MUST require private manually confirmed roster entry для каждой из 9 family transport identities with opaque `principal_id`, `role_id` и `profile_id`

#### Scenario: Юлина binding назначается вручную
- **WHEN** оператор подтверждает roster
- **THEN** оператор MUST manually mark exactly one binding as Юлина и система MUST assign `family_sandbox` only to that binding, while assigning `family_standard` to the other eight family bindings

#### Scenario: Display labels не являются authority
- **WHEN** display name или username доступны из transport metadata, dashboard или audit
- **THEN** система MUST treat them only as redacted admin labels and MUST NOT use them as authority, roster proof или auto-link evidence across multiple accounts

#### Scenario: Неоднозначный roster блокирует cutover
- **WHEN** roster has missing, duplicate или ambiguous mapping for any family transport identity, role или profile
- **THEN** система MUST block provisioning, migration preflight and live cutover and MUST NOT guess the intended person, role или profile

### Requirement: Unknown и malformed ingress fail-closed
Система MUST отказывать unknown, missing или malformed transport identity без owner/default fallback.

#### Scenario: Unknown DM запрещен
- **WHEN** Telegram DM приходит от user_id без active `PrincipalBinding`
- **THEN** система MUST вернуть deny response или silent deny согласно transport policy без создания session, memory read/write или model call

#### Scenario: Malformed identity запрещена
- **WHEN** ingress не содержит required platform/account/peer_kind/user_id/chat_id fields или содержит fields неверного типа
- **THEN** система MUST отказать до любых side effects и записать redacted audit event без content

#### Scenario: Missing profile binding запрещен
- **WHEN** identity известна, но profile binding отсутствует или disabled
- **THEN** система MUST отказать без fallback в owner/default или last-used profile

### Requirement: Immutable ResolvedAccessContext с шестью полями
Система SHALL создавать immutable `ResolvedAccessContext` на ingress с ровно шестью authority-полями: `principal_id`, `role_id`, `profile_id`, `conversation_scope`, `capabilities`, `delivery_target`.

#### Scenario: Transport metadata не является authority
- **WHEN** resolver принимает trusted transport/account/peer identity и находит binding
- **THEN** система SHALL использовать transport/account/peer identity только как вход resolver и redacted audit metadata, а не как дополнительные authority-поля `ResolvedAccessContext`

#### Scenario: Context reaches callbacks русифицирован
- **WHEN** gateway turn запускает streaming callback или delivery callback
- **THEN** callback MUST получить те же `principal_id`, `role_id`, `profile_id`, `conversation_scope`, `capabilities`, `delivery_target`, что были разрешены на ingress

#### Scenario: Context reaches background and cron русифицирован
- **WHEN** background command или cron job продолжает работу после исходного turn
- **THEN** job MUST использовать persisted server-bound `ResolvedAccessContext` с шестью authority-полями, а не current process env или guessed transport IDs

#### Scenario: Context reaches delegation русифицирован
- **WHEN** agent delegates work to subagent или worker
- **THEN** delegated worker MUST inherit только caller `principal_id`, `role_id`, `profile_id`, `conversation_scope`, `capabilities`, `delivery_target` and MUST NOT widen profile, role, scope, tools or memory namespace

#### Scenario: Context survives compaction русифицирован
- **WHEN** session compaction создает child session или summary
- **THEN** child session MUST keep same server-bound access context и MUST NOT read sibling profile history

#### Scenario: Context survives reset and restart русифицирован
- **WHEN** reset, auto-reset, process restart или gateway restart resumes active session
- **THEN** resumed work MUST reload context from persisted server-bound metadata и MUST deny if metadata is missing or mismatched

#### Scenario: Concurrent turns isolated русифицирован
- **WHEN** owner и family turns run concurrently в одном gateway process
- **THEN** каждый turn MUST keep own immutable context и MUST NOT observe other profile, tools, memory or session state

### Requirement: Deny before model session tools до доступа
Система MUST enforce principal resolution before model initialization, session selection, memory access, prompt assembly, tool discovery and tool execution.

#### Scenario: Unknown callback запрещен
- **WHEN** callback payload references stale или unknown session without matching persisted access context
- **THEN** система MUST deny before loading session transcript или sending model request

#### Scenario: Unknown slash command запрещен
- **WHEN** unknown principal sends slash command, reset command, resume command или background command
- **THEN** система MUST deny before command handler can inspect private sessions, tools, profiles или model configuration

#### Scenario: Model args cannot select foreign namespace русифицирован
- **WHEN** model tool args, command args или callback payload include foreign profile, memory namespace, session ID, role, scope или delivery target
- **THEN** система MUST ignore those authority claims и MUST authorize only against server-owned `ResolvedAccessContext`

### Requirement: Delivery routing follows resolved context с delivery target
Система SHALL route outbound delivery only to transport target authorized by original `delivery_target` from `ResolvedAccessContext`.

#### Scenario: DM delivery русифицирован
- **WHEN** Telegram DM turn завершается
- **THEN** response delivery MUST target only DM chat from resolved `delivery_target` и MUST NOT use owner default delivery

#### Scenario: Shared room delivery русифицирован
- **WHEN** shared room turn завершается
- **THEN** response delivery MUST target only configured shared room/topic `delivery_target` и MUST NOT leak to principal private DM
