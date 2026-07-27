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
- **THEN** система SHALL использовать shared room profile, room `conversation_scope`, room `delivery_target` и полный серверно настроенный Telegram tool profile, сохранив sender identity только как redacted audit metadata

#### Scenario: Room membership не выдает role
- **WHEN** family principal пишет в shared room
- **THEN** система MUST не повышать role этого principal и MUST вычислить permissions только через пересечение role/scope/backend

#### Scenario: Shared room tool profile не выбирается моделью
- **WHEN** model args, command args или callback payload include tool name, MCP server, capability, profile, scope или delivery target для shared room turn
- **THEN** система MUST authorize только exact configured room tools из validated room `ResolvedAccessContext`, configured Telegram tool profile и backend policy

### Requirement: Telegram free-response trigger для configured shared room
Система SHALL использовать `telegram.free_response_chats` только как серверную транспортную политику запуска для уже настроенных Telegram shared rooms, а не как authorization, membership, role, capability, profile, scope или delivery authority.

#### Scenario: Free-response запускает agent во всех topics configured группы
- **WHEN** exact `chat_id` Telegram group является active server-configured shared room, тот же exact `chat_id` включен в `telegram.free_response_chats`, отправитель является allowed participant, а сообщение в любом topic этой группы не является mention или reply
- **THEN** система SHALL разрешить этому сообщению запустить agent с тем же shared-room `ResolvedAccessContext` и topic-specific `delivery_target`, которые использовал бы обычный mention/reply ingress

#### Scenario: Free-response не меняет topic isolation
- **WHEN** два неупомянутых сообщения приходят в два разных topic одной free-response configured shared room
- **THEN** система SHALL сохранить отдельные topic `delivery_target`, session scope и memory namespace для каждого topic и MUST NOT склеивать topic context

#### Scenario: `Free-response` gates выполняются после более ранних Telegram gates
- **WHEN** event в free-response configured shared room является own message, ignored thread или topic вне `allowed_topics`
- **THEN** система MUST отказать или проигнорировать event до применения free-response trigger policy и MUST NOT создавать session, memory read/write или model call

#### Scenario: Другие shared rooms все еще требуют mention или reply
- **WHEN** сообщение приходит в другую active shared room, exact `chat_id` которой не включен в `telegram.free_response_chats`, и сообщение не является mention или reply
- **THEN** система MUST NOT запускать agent через free-response policy и MUST сохранить существующее требование mention/reply

#### Scenario: Неизвестный chat остается fail-closed
- **WHEN** Telegram group `chat_id` включен в `telegram.free_response_chats`, но не является active server-configured shared room с matching `SharedScopeBinding`
- **THEN** система MUST отказать до session lookup, model init, memory hydration или tool schema staging и MUST NOT создавать room membership или fallback в owner/default

### Requirement: Проверенный семейный roster до provisioning и cutover
Система MUST требовать private manually confirmed roster `transport identity -> opaque principal_id -> role_id -> profile_id` для всех девяти family transport identities до provisioning, migration preflight или live cutover; тот же private roster MUST содержать ровно одну `family_sandbox` binding и восемь `family_standard` bindings. Wolfram MUST назначаться через role policy для всех personal family profiles, а не через roster.

#### Scenario: Owner baseline сохраняется
- **WHEN** baseline registry preflight выполняется
- **THEN** система MUST считать Руслана configured `owner` и MUST сохранить его текущий полный owner profile без понижения роли или смены profile

#### Scenario: Уникальность live registry является предусловием
- **WHEN** credential-safe live audit сообщает 1 owner, 9 unique family transport identities и 2 unique shared rooms без дублей
- **THEN** система MAY использовать этот факт только как uniqueness baseline и MUST NOT считать family identities безопасно human-labeled

#### Scenario: Private roster связывает все family identities
- **WHEN** provisioning или migration preflight запускается для family profiles
- **THEN** система MUST require private manually confirmed roster entry для каждой из 9 family transport identities with opaque `principal_id`, `role_id` и `profile_id`

#### Scenario: Sandbox binding назначается вручную
- **WHEN** оператор подтверждает roster
- **THEN** оператор MUST manually mark exactly one binding as sandbox binding и система MUST assign `family_sandbox` only to that binding, while assigning `family_standard` to the other eight family bindings

#### Scenario: Wolfram назначается role policy
- **WHEN** оператор подтверждает private roster
- **THEN** система MUST NOT требовать или принимать Wolfram assignments в roster; восемь `family_standard` profiles SHALL получать exact configured Wolfram MCP allowlist по role policy по умолчанию, один `family_sandbox` SHALL сохранять свой Wolfram MCP allowlist, и система MUST NOT создавать пятую роль, роль под человека, special-case principal или выводить Wolfram availability из username, display-name, message text, model args или room membership

#### Scenario: Display labels не являются authority
- **WHEN** username/display-name доступны из transport metadata, dashboard или audit
- **THEN** система MUST treat username/display-name only as non-authoritative redacted admin labels, MUST NOT use them as roster proof, exact identity source, role/profile assignment source, Wolfram source или auto-link evidence across multiple accounts

#### Scenario: Неоднозначный roster блокирует cutover
- **WHEN** roster has missing, duplicate или ambiguous mapping for any family transport identity, role или profile
- **THEN** система MUST block provisioning, migration preflight and live cutover and MUST NOT guess the intended exact identity, role или profile

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
- **THEN** job MUST использовать persisted server-bound `ResolvedAccessContext` с шестью authority-полями, а не current process env, guessed transport IDs или model-selected tool/delivery scope

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

#### Scenario: Shared room cron delivery русифицирован
- **WHEN** shared room cron или background delivery fires after original turn
- **THEN** delivery MUST target only тот же configured shared room/topic `delivery_target` и MUST deny private, cross-room и owner default delivery
