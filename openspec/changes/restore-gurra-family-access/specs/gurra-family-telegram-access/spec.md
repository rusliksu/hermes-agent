## ADDED Requirements

### Requirement: Ordinary family DM использует explicit allowlist
Система MUST разрешать ordinary Telegram DM-доступ только для пользователей,
которые явно присутствуют в `telegram_allowed_user_ids`. Реальные ID и значения
MUST NOT храниться в репозитории, OpenSpec artifacts или diagnostics.

#### Scenario: Разрешённый семейный пользователь пишет в DM
- **WHEN** Telegram DM приходит от идентифицируемого пользователя из explicit
  `telegram_allowed_user_ids`
- **THEN** система разрешает ordinary DM turn в isolated user scope
- **AND** не выдаёт admin, pairing, elevated или approval права

#### Scenario: Пользователь отсутствует в allowlist
- **WHEN** Telegram DM приходит от идентифицируемого пользователя вне explicit
  `telegram_allowed_user_ids`
- **THEN** система fail-closed до session lookup, memory access, tools и model
  turn

#### Scenario: Allowlist отсутствует или пуст
- **WHEN** `telegram_allowed_user_ids` отсутствует, пуст или не может быть
  прочитан как явный список
- **THEN** ordinary family DM-доступ не включается
- **AND** система не использует fallback по username, display name, owner ID или
  legacy group grants

### Requirement: Elevated surface остаётся owner-only
Система MUST разрешать admin commands, pairing setup, elevated actions и
approvals только owner principal. Наличие пользователя в
`telegram_allowed_user_ids` MUST NOT расширять elevated surface.

#### Scenario: Семейный пользователь вызывает admin command
- **WHEN** user из ordinary DM allowlist вызывает admin-only command
- **THEN** система отказывает как non-owner
- **AND** отказ не раскрывает owner ID, raw Telegram ID или private config values

#### Scenario: Семейный пользователь создаёт approval
- **WHEN** ordinary family DM turn требует approval или elevated action
- **THEN** approval route остаётся owner-only
- **AND** семейный пользователь не может approve, deny или bypass elevated gate

#### Scenario: Owner выполняет elevated action
- **WHEN** owner principal вызывает admin, pairing, elevated action или approval
- **THEN** owner-only behavior остаётся прежним
- **AND** ordinary family allowlist не меняет owner checks

### Requirement: Shared groups остаются неизменными
Система MUST NOT менять две существующие shared Telegram groups, их group/topic
scope, passive context policy или shared capability profile при восстановлении
ordinary family DM-доступа.

#### Scenario: Shared group получает сообщение
- **WHEN** Telegram update приходит из одной из существующих shared groups
- **THEN** routing и authorization используют прежний shared group scope
- **AND** `telegram_allowed_user_ids` не добавляет новых group participants или
  capabilities

#### Scenario: DM allowlist не расширяет group access
- **WHEN** user есть в ordinary DM allowlist, но не имеет отдельного group
  scope grant
- **THEN** этот факт сам по себе не разрешает shared group access

### Requirement: Unknown или missing principals fail-closed
Система MUST fail-closed для unknown, missing, anonymous, bot-authored или
неполных Telegram principals до stateful side effects.

#### Scenario: Отсутствует Telegram user ID
- **WHEN** DM или group update не содержит идентифицируемого human user principal
- **THEN** система отказывает до transcript write, memory lookup, tools, model
  turn, callbacks и visible response

#### Scenario: Bot-authored update
- **WHEN** Telegram update создан bot-authored principal
- **THEN** система отказывает до stateful side effects

### Requirement: Diagnostics redacted
Система MUST выводить diagnostics и logs без raw Telegram IDs, message contents,
tokens, secrets и private config values.

#### Scenario: Access denied diagnostic
- **WHEN** ordinary DM access отклонён policy gate
- **THEN** diagnostic содержит только redacted категорию отказа
- **AND** не содержит raw Telegram user ID, chat ID, message text или private
  config values

#### Scenario: Access allowed diagnostic
- **WHEN** ordinary DM access разрешён
- **THEN** diagnostic может показывать non-sensitive категорию результата
- **AND** не содержит raw identities или private values

### Requirement: Ordinary DM state изолирован per-user
Система MUST сохранять отдельные session, transcript, memory и approval/elevated
state для каждого ordinary DM user.

#### Scenario: Два семейных пользователя пишут в DM
- **WHEN** два different users из ordinary DM allowlist пишут Gurra в личный чат
- **THEN** каждый turn использует отдельный user/session/memory scope
- **AND** один пользователь не видит transcript, memory или approvals другого

#### Scenario: Семейный пользователь и owner
- **WHEN** ordinary family user и owner пишут в DM
- **THEN** ordinary user scope не смешивается с owner scope
- **AND** owner-only elevated state не становится доступен ordinary user

### Requirement: Live activation requires separate gate
После repo-local implementation и tests система MUST NOT выполнять live patch,
private config update, symlink switch, service restart, deploy, push или merge
без отдельного explicit live gate.

#### Scenario: Repo-local checks pass
- **WHEN** OpenSpec artifacts, implementation evidence и tests готовы локально
- **THEN** agent reports status, commit SHA, files и pending live tasks
- **AND** active checkout, symlink, private config, systemd/service, push и
  deploy остаются untouched

#### Scenario: Live gate ещё не выдан
- **WHEN** explicit live gate отсутствует
- **THEN** live patch/config/restart tasks остаются pending
