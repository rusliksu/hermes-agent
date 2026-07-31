## ADDED Requirements

### Requirement: Локальность Dashboard Access/Users
Система SHALL показывать Access/Users только через localhost или защищенный SSH tunnel dashboard session.

#### Scenario: Доступ через localhost
- **WHEN** authenticated owner открывает dashboard через localhost или SSH tunnel
- **THEN** Access/Users UI/API MAY показать redacted principal/profile status и policy preview

#### Scenario: Запрет нелокального доступа
- **WHEN** Access/Users endpoint запрошен из non-local или non-tunnel контекста
- **THEN** система MUST отказать до возврата principal, profile, model, memory или session details

### Requirement: Только redacted status
Система MUST показывать только redacted status для principals, roles, scopes, bindings и migrations.

#### Scenario: Редакция статуса
- **WHEN** dashboard показывает owner, 9 family principals и 2 shared rooms
- **THEN** UI/API MUST скрыть raw transport IDs, credentials, model secrets, private memory и message bodies

#### Scenario: Запрет model exposure
- **WHEN** пользователь открывает Access/Users или break-glass screens
- **THEN** система MUST NOT раскрывать model API keys, model credential pool contents, raw provider secrets, model prompts, tool arguments или tool outputs

### Requirement: Role preview confirm audit для ролей
Система SHALL требовать preview, explicit confirm, atomic apply и audit для role или binding changes.

#### Scenario: Preview перед сохранением
- **WHEN** owner редактирует role, binding или shared scope
- **THEN** dashboard MUST показать effective permission delta до применения изменений

#### Scenario: Confirm обязателен
- **WHEN** owner пытается применить role, binding или shared scope change
- **THEN** dashboard MUST требовать explicit confirmation и MUST записать redacted audit до atomic persistence

#### Scenario: Атомарное применение
- **WHEN** confirmed role, binding или shared scope change сохраняется
- **THEN** система MUST применить все affected policy rows атомарно или оставить previous policy unchanged

#### Scenario: Bulk operations запрещены
- **WHEN** пользователь пытается выполнить bulk role/binding search, export или edit operations
- **THEN** dashboard MUST отказать, потому что bulk exposure не разрешен в этом change

### Requirement: Инварианты break-glass lease
Система SHALL предоставлять break-glass как scoped read-only history lease максимум на 15 минут с reason, reconfirm, manual revoke и audit.

#### Scenario: Создание lease
- **WHEN** owner запрашивает break-glass read-only history access
- **THEN** система MUST требовать один target profile/session set, reason, privacy warning и reconfirm до создания lease

#### Scenario: Длительность lease
- **WHEN** break-glass lease создан
- **THEN** lease MUST истечь не позднее чем через 15 минут после создания и MUST оставаться read-only

#### Scenario: Manual revoke lease вручную
- **WHEN** owner вручную отзывает break-glass lease
- **THEN** система MUST немедленно запретить дальнейшие break-glass reads и записать redacted audit

#### Scenario: Lease после restart
- **WHEN** dashboard или gateway перезапускается
- **THEN** система MUST NOT восстанавливать break-glass lease без нового reason и reconfirm flow

#### Scenario: Lease не может писать
- **WHEN** break-glass lease holder пытается write, migration, role change, Telegram permission change, model permission change, tool execution, session mutation или memory mutation
- **THEN** система MUST отказать

#### Scenario: Lease не раскрывает model или tools
- **WHEN** break-glass lease читает history
- **THEN** система MUST NOT передавать private content в model/tools и MUST NOT раскрывать prompts, model payloads, tool arguments или tool outputs

### Requirement: Dashboard не обходит server policy
Система MUST проводить dashboard actions через тот же server-side RolePolicy и binding enforcement, что и gateway ingress.

#### Scenario: Guessed profile в dashboard запрещен
- **WHEN** dashboard request ссылается на guessed profile или session outside authorized context
- **THEN** система MUST отказать до чтения sessions, memory, prompt files, attachments или model config

#### Scenario: Preview не является authority
- **WHEN** role preview UI показывает intended role или scope
- **THEN** preview MUST NOT менять effective permissions до confirm и persistence through server policy

### Requirement: Privacy dashboard audit без контента
Система SHALL аудитить dashboard access и break-glass events без private message bodies, content или secrets.

#### Scenario: Audit содержит reason
- **WHEN** break-glass создается, используется, истекает или отзывается
- **THEN** audit MUST включать timestamp, actor, reason, action type и redacted target labels

#### Scenario: Audit исключает content и secrets
- **WHEN** audit event сохраняется или отображается
- **THEN** audit MUST исключать credentials, raw model secrets, private memory content, message bodies, prompts, tool arguments и tool outputs
