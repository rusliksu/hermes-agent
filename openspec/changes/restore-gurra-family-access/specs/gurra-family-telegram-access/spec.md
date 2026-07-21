## ADDED Requirements

### Requirement: Обычный семейный DM-доступ использует явный список разрешённых пользователей
Система MUST разрешать обычный Telegram DM-доступ только для пользователей,
которые явно присутствуют в `telegram_allowed_user_ids`. Реальные ID и значения
MUST NOT храниться в репозитории, OpenSpec-артефактах или диагностике.

#### Scenario: Разрешённый семейный пользователь пишет в DM
- **WHEN** Telegram DM приходит от идентифицируемого пользователя из явного
  `telegram_allowed_user_ids`
- **THEN** система разрешает обычный DM turn в изолированной пользовательской области
- **AND** не выдаёт права admin, pairing, elevated или approval

#### Scenario: Пользователь отсутствует в списке разрешённых
- **WHEN** Telegram DM приходит от идентифицируемого пользователя вне явного
  `telegram_allowed_user_ids`
- **THEN** система закрывает доступ до поиска сессии, доступа к памяти, tools и model
  turn

#### Scenario: Список разрешённых отсутствует или пуст
- **WHEN** `telegram_allowed_user_ids` отсутствует, пуст или не может быть
  прочитан как явный список
- **THEN** обычный семейный DM-доступ не включается
- **AND** система не использует fallback по username, display name, owner ID или
  legacy-групповым разрешениям

### Requirement: Elevated-поверхность остаётся только для владельца
Система MUST разрешать admin-команды, настройку pairing, elevated-действия и
approval только owner principal. Наличие пользователя в
`telegram_allowed_user_ids` MUST NOT расширять elevated-поверхность.

#### Scenario: Семейный пользователь вызывает admin-команду
- **WHEN** user из списка обычного DM вызывает admin-only command
- **THEN** система отказывает как non-owner
- **AND** отказ не раскрывает owner ID, raw Telegram ID или значения приватной конфигурации

#### Scenario: Семейный пользователь создаёт approval
- **WHEN** обычный семейный DM turn требует approval или elevated action
- **THEN** маршрут approval остаётся только для владельца
- **AND** семейный пользователь не может approve, deny или bypass elevated gate

#### Scenario: Владелец выполняет elevated action
- **WHEN** owner principal вызывает admin, pairing, elevated action или approval
- **THEN** поведение только для владельца остаётся прежним
- **AND** список обычного семейного доступа не меняет проверки владельца

### Requirement: Общие группы остаются неизменными
Система MUST NOT менять две существующие общие Telegram-группы, их область
group/topic, политику passive context или общий профиль возможностей при
восстановлении обычного семейного DM-доступа.

#### Scenario: Общая группа получает сообщение
- **WHEN** Telegram update приходит из одной из существующих общих групп
- **THEN** routing и authorization используют прежнюю область общей группы
- **AND** `telegram_allowed_user_ids` не добавляет новых участников группы или
  возможностей

#### Scenario: DM-список разрешённых не расширяет доступ к группе
- **WHEN** user есть в списке обычного DM, но не имеет отдельного group
  разрешения области
- **THEN** этот факт сам по себе не разрешает доступ к общей группе

### Requirement: Неизвестные или отсутствующие principal закрывают доступ
Система MUST закрывать доступ для unknown, missing, anonymous, bot-authored или
неполных Telegram principal до stateful side effects.

#### Scenario: Отсутствует Telegram user ID
- **WHEN** DM или group update не содержит идентифицируемого human user principal
- **THEN** система отказывает до записи transcript, поиска памяти, tools, model
  turn, callbacks и visible response

#### Scenario: Обновление от имени бота
- **WHEN** Telegram update создан bot-authored principal
- **THEN** система отказывает до stateful side effects

### Requirement: Диагностика скрывает чувствительные данные
Система MUST выводить диагностику и logs без raw Telegram IDs, содержимого сообщений,
tokens, secrets и значения приватной конфигурации.

#### Scenario: Диагностика отказа в доступе
- **WHEN** обычный DM-доступ отклонён policy gate
- **THEN** diagnostic содержит только скрытую категорию отказа
- **AND** не содержит raw Telegram user ID, chat ID, message text или значения
  приватной конфигурации

#### Scenario: Диагностика разрешённого доступа
- **WHEN** обычный DM-доступ разрешён
- **THEN** diagnostic может показывать нечувствительную категорию результата
- **AND** не содержит raw identities или private values

### Requirement: Состояние обычного DM изолировано по пользователям
Система MUST сохранять отдельные session, transcript, memory и approval/elevated
состояние для каждого обычного DM user.

#### Scenario: Два семейных пользователя пишут в DM
- **WHEN** два разных user из списка обычного DM пишут Gurra в личный чат
- **THEN** каждый turn использует отдельную область user/session/memory
- **AND** один пользователь не видит transcript, memory или approval другого

#### Scenario: Семейный пользователь и owner
- **WHEN** обычный семейный user и owner пишут в DM
- **THEN** область обычного user не смешивается с областью owner
- **AND** elevated-состояние только для владельца не становится доступно обычному user

### Requirement: Live-активация требует отдельного разрешения
После repo-local implementation и tests система MUST NOT выполнять live patch,
обновление приватной конфигурации, переключение symlink, restart сервиса, deploy,
push или merge без отдельного явного live-разрешения.

#### Scenario: Локальные проверки репозитория прошли
- **WHEN** OpenSpec-артефакты, evidence реализации и tests готовы локально
- **THEN** agent сообщает status, commit SHA, files и ожидающие live-задачи
- **AND** active checkout, symlink, private config, systemd/service, push и
  deploy остаются нетронутыми

#### Scenario: Live-разрешение ещё не выдано
- **WHEN** явное live-разрешение отсутствует
- **THEN** live patch/config/restart задачи остаются ожидающими
