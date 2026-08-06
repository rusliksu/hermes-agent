## ADDED Requirements

### Requirement: Единая карточка настроек Telegram
Система SHALL предоставлять команду `/settings`, которая показывает эффективные модель, уровень размышлений и состояние Fast API для текущего Telegram topic lane и позволяет изменять каждую настройку независимо.

#### Scenario: Открытие карточки
- **WHEN** авторизованный пользователь вызывает `/settings` в Telegram-топике
- **THEN** бот показывает текущую комбинацию и кнопки Luna, Terra, Sol, полного списка моделей, low, medium, high, advanced и Fast API

#### Scenario: Независимое изменение
- **WHEN** пользователь выбирает новый уровень размышлений в карточке
- **THEN** система изменяет только reasoning preference и сохраняет выбранную модель и Fast API без изменений

### Requirement: Долговечная topic preference
Система MUST сохранять model/provider и reasoning preference по каноническому lane key, включающему platform, chat, thread и user, и MUST восстанавливать её после restart gateway и `/new`.

#### Scenario: Новый контекст того же топика
- **WHEN** пользователь выбрал Luna/high в топике и затем вызывает `/new`
- **THEN** новый активный контекст того же топика использует Luna/high

#### Scenario: Изоляция соседнего топика
- **WHEN** один пользователь выбирает разные настройки в двух топиках одного чата
- **THEN** настройки каждого топика применяются независимо

### Requirement: Приоритет эффективных настроек
Система MUST вычислять эффективную model/reasoning комбинацию в порядке topic preference, затем legacy session override, затем global default.

#### Scenario: Topic preference перекрывает legacy override
- **WHEN** для текущего lane существуют topic preference и отличающийся legacy session override
- **THEN** runtime использует topic preference

#### Scenario: Fallback на global default
- **WHEN** для текущего lane нет topic preference и session override
- **THEN** runtime использует global default

### Requirement: Явный scope команд
Команды `/model` и `/reasoning` SHALL принимать взаимоисключающие флаги `--session`, `--topic` и `--global`; `/fast` SHALL принимать `--session` и `--global` и MUST отклонять `--topic` как несовместимый с session-scoped режимом. В Telegram отсутствие флага SHALL означать `--topic` для model/reasoning и `--session` для Fast API.

#### Scenario: Telegram model без флага
- **WHEN** пользователь вызывает `/model gpt-5.6-luna` в Telegram-топике без scope-флага
- **THEN** система сохраняет модель как topic preference текущего lane

#### Scenario: Явное глобальное изменение
- **WHEN** уполномоченный пользователь вызывает команду с `--global`
- **THEN** система изменяет global default и явно сообщает глобальный scope

#### Scenario: Конфликт scope-флагов
- **WHEN** команда содержит более одного scope-флага
- **THEN** система отклоняет команду без изменения настроек

#### Scenario: Topic scope для Fast API
- **WHEN** пользователь вызывает `/fast --topic`
- **THEN** система отклоняет команду без изменения Fast API

### Requirement: Fast API остаётся session-scoped
Fast API MUST включаться только вручную, MUST относиться к активной сессии по умолчанию и MUST сбрасываться при `/new`; model/reasoning topic preferences при этом MUST сохраняться.

#### Scenario: Сброс Fast API
- **WHEN** пользователь включил Fast API и затем вызывает `/new`
- **THEN** новая сессия имеет выключенный Fast API и прежние topic model/reasoning preferences
