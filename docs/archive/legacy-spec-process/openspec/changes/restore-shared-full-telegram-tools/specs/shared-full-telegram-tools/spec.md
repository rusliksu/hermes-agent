## ADDED Requirements

### Requirement: Shared turn получает штатный Telegram tool profile

Для exact allowlisted shared scope gateway MUST использовать тот же configured
Telegram tool profile и те же runtime availability checks, что для обычного
Telegram turn, без shared-only memory whitelist.

#### Scenario: Полный профиль доступен участнику разрешённой темы

- **WHEN** участник отправляет сообщение в exact allowlisted shared scope
- **THEN** effective tool schemas совпадают со стандартным Telegram turn
- **AND** включают доступные Browser Automation, terminal, file и code tools
- **AND** не требуют Nous subscription или нового external credential

### Requirement: Private context остаётся изолированным

Расширение tool profile MUST NOT добавлять owner private context files, private
DM history, private MemoryStore или owner identity injection в shared turn.

#### Scenario: Shared agent создаётся отдельно от private agent

- **WHEN** после private turn создаётся shared turn
- **THEN** shared turn использует отдельную cache signature и scoped state
- **AND** private prompt/history/memory не переиспользуются

### Requirement: Policy и approval остаются закрытыми

Unknown shared scopes MUST отклоняться до model/tools, а approve/deny, admin,
pairing и elevated authority MUST оставаться owner-only.

#### Scenario: Обычный shared participant не подтверждает elevated действие

- **WHEN** не-owner участник вызывает approval/admin pathway
- **THEN** gateway отклоняет действие до privileged side effect

#### Scenario: Неизвестная комната не получает tools

- **WHEN** Telegram room/topic не совпадает с exact allowlist
- **THEN** gateway завершает обработку до agent/model/tool construction

### Requirement: Shared memory остаётся общей и scoped

Memory tool MUST быть привязан к shared MemoryStore, одновременно допуская
остальные schemas штатного Telegram-профиля.

#### Scenario: Memory и остальные tools сосуществуют

- **WHEN** shared agent создаётся с `memory`, browser и terminal schemas
- **THEN** только memory handler получает shared scoped store
- **AND** остальные handlers остаются доступными без private memory binding
