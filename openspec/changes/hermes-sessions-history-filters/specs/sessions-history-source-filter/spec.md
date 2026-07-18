## ADDED Requirements

### Requirement: Sessions opens History by default
Страница `/sessions` SHALL открывать History/list view по умолчанию, при этом Overview SHALL оставаться доступным через существующий переключатель.

#### Scenario: Default route lands on History
- **WHEN** пользователь открывает `/sessions`
- **THEN** отображается History/list view
- **AND** Overview остается доступен как отдельный tab/option, если для него есть данные

### Requirement: History source filter defaults to Telegram
History view SHALL иметь один source-фильтр с default значением `telegram`.

#### Scenario: Initial source filter is Telegram
- **WHEN** пользователь впервые открывает History на `/sessions`
- **THEN** выбран фильтр `Telegram`
- **AND** запрос списка сессий использует server-side query param `source=telegram`

### Requirement: Source filter options reflect available sources
Source filter SHALL показывать варианты `Telegram`, `Cron`, остальные реально доступные source из `stats.by_source`, и `Все`.

#### Scenario: Stats include additional sources
- **WHEN** `stats.by_source` содержит `telegram`, `cron`, `desktop` и `cli`
- **THEN** фильтр показывает `Telegram`, `Cron`, `Desktop`, `Cli` и `Все`
- **AND** варианты не дублируются

#### Scenario: All source option disables source query
- **WHEN** пользователь выбирает `Все`
- **THEN** запрос списка сессий не передает `source`
- **AND** `total` и pagination соответствуют unfiltered server response

### Requirement: Source changes reset transient list state
При смене source-фильтра UI MUST сбрасывать pagination page, row selection/range anchor и expanded row.

#### Scenario: Changing source resets page and selection
- **WHEN** пользователь находится на странице 2, выбрал строки и раскрыл одну строку
- **AND** пользователь меняет source-фильтр
- **THEN** page становится 0
- **AND** selection очищается
- **AND** expanded row очищается

### Requirement: Pagination total is server scoped by source
History list SHALL использовать server-side `source` filter для конкретного source, чтобы `total` и pagination считались backend-ом.

#### Scenario: Telegram pagination uses server total
- **WHEN** выбран `Telegram`
- **AND** backend возвращает `total=37` для `/api/sessions?source=telegram`
- **THEN** UI показывает pagination относительно `37`
- **AND** не пересчитывает total клиентским фильтром

### Requirement: Search intersects with selected source
Search behavior MUST сохранять текущую backend semantics и отображать только пересечение результатов поиска с выбранным source/list state.

#### Scenario: Search does not expand backend API
- **WHEN** пользователь вводит search query при выбранном `Telegram`
- **THEN** UI может вызвать существующий `/api/sessions/search`
- **AND** UI не требует нового backend source-scoped search parameter
- **AND** видимыми остаются только строки из текущего Telegram list, которые есть в search results

### Requirement: Sessions typography is scoped
На странице Sessions row titles и source badges SHALL использовать существующий readable body/UI `font-sans`; row title MUST NOT использовать `font-mondwest`; source badge MUST быть `normal-case` с `tracking-normal` и MUST NOT использовать `font-compressed`. Глобальная typography, branding и design system MUST NOT изменяться.

#### Scenario: Source badge uses readable local typography
- **WHEN** строка History отображает source badge
- **THEN** badge text использует local existing readable body/UI `font-sans`
- **AND** badge text is normal-case
- **AND** badge text использует `tracking-normal`
- **AND** badge не использует `font-compressed`
- **AND** глобальный компонент `Badge` не меняется

#### Scenario: Row title uses readable local typography
- **WHEN** строка History отображает title
- **THEN** title использует existing readable body/UI `font-sans`
- **AND** title не использует `font-mondwest`

### Requirement: Session actions and data remain unchanged
Фильтрация History MUST NOT менять session data, delete/export/resume/rename/prune actions или backend storage.

#### Scenario: Existing actions remain available
- **WHEN** пользователь видит строку в отфильтрованном History list
- **THEN** delete, export, resume, rename и row expand actions остаются доступны на тех же условиях
- **AND** payloads этих действий не получают новых обязательных session data fields

### Requirement: Delivery requires explicit live gate
После реализации и локальной проверки live build/install/restart `hermes-dashboard.service` MUST выполняться только после отдельного явного разрешения пользователя.

#### Scenario: Implementation complete before deployment
- **WHEN** реализация и локальные проверки завершены
- **THEN** agent показывает diff/status и validation result
- **AND** не выполняет live build, install, restart или deploy без отдельного explicit gate
