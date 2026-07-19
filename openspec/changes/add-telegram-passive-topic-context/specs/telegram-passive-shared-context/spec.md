## ADDED Requirements

### Requirement: Passive ingress использует authoritative shared policy
Система MUST сохранять context-only текст только когда single-principal mode
включён, Telegram chat входит в exact private shared allowlist и operator явно
включил observation. Legacy group grants MUST NOT расширять этот набор.

#### Scenario: Обычная реплика в allowlisted группе
- **WHEN** идентифицируемый человек пишет текст без вызова Gurra в разрешённой группе
- **THEN** система сохраняет context-only строку в shared transcript текущего scope

#### Scenario: Неизвестная группа
- **WHEN** текст без вызова приходит из Telegram group без exact shared mapping
- **THEN** система отказывает до session lookup и transcript write

#### Scenario: Bot или anonymous sender
- **WHEN** passive event создан bot-authored или anonymous sender
- **THEN** система отказывает до transcript write

#### Scenario: Observation выключен
- **WHEN** shared scope разрешён, но observation не включён явно
- **THEN** обычная реплика не сохраняется и текущее mention-only поведение остаётся прежним

### Requirement: Passive сообщение не запускает agent turn
Система MUST отделять observation от invocation: passive text MUST NOT запускать
model, tools, callbacks, long-term memory update или Telegram response.

#### Scenario: Разговор людей между вызовами
- **WHEN** несколько обычных сообщений поступают в разрешённый scope
- **THEN** система только дописывает context-only transcript и не вызывает message handler

#### Scenario: Следующий явный вызов
- **WHEN** после passive сообщений приходит exact mention, reply Gurra или addressed command
- **THEN** система запускает ровно один turn и подаёт passive строки как context-only block перед текущим запросом

### Requirement: Passive context изолирован по group и topic
Система MUST использовать тот же shared session scope без sender ID, что и
явные turn: обычная группа имеет root scope, General имеет stable thread `1`,
а каждый forum topic имеет отдельный thread scope.

#### Scenario: Разные участники одного topic
- **WHEN** два участника пишут passive text в одном forum topic
- **THEN** оба сообщения доступны следующему вызову только в этом topic

#### Scenario: Соседние topics
- **WHEN** passive text записан в topic A, а Gurra вызван в topic B той же группы
- **THEN** context topic A отсутствует в prompt topic B

#### Scenario: Одинаковый topic ID в разных группах
- **WHEN** две allowlisted группы имеют одинаковый Telegram thread ID
- **THEN** их passive transcripts остаются различными благодаря chat scope

### Requirement: Context-only replay ограничен
Система MUST подавать модели не более 50 последних passive сообщений, не более
20 000 символов суммарно и только timestamped сообщения не старше шести часов.
Ограничения MUST применяться до model request.

#### Scenario: Превышен лимит сообщений
- **WHEN** transcript содержит более 50 свежих passive строк
- **THEN** prompt получает последние 50 с сохранением хронологического порядка

#### Scenario: Превышен лимит символов
- **WHEN** свежие passive строки суммарно превышают 20 000 символов
- **THEN** система отбрасывает самые старые строки до соблюдения лимита

#### Scenario: Устаревшая или недатированная строка
- **WHEN** passive строка старше шести часов или не имеет валидного timestamp
- **THEN** строка не попадает в prompt, но остаётся в audit transcript

### Requirement: Shared prompt и logs не раскрывают raw identities
В single-principal shared scope система MUST передавать модели display label
автора без raw Telegram user ID и MUST NOT писать chat/user IDs или message
contents в observation logs.

#### Scenario: Passive строка с известным display name
- **WHEN** разрешённый участник пишет обычный текст
- **THEN** context содержит display label и текст без raw Telegram user ID

#### Scenario: Observation log
- **WHEN** passive строка успешно сохранена или отклонена
- **THEN** log содержит только категорию результата без chat ID, user ID и message body

### Requirement: Первый rollout наблюдает только текст
Single-principal passive observation MUST принимать только Telegram text
messages; media, files, voice, location и service events MUST NOT скачиваться
или сохраняться пассивно.

#### Scenario: Неадресованное вложение
- **WHEN** участник отправляет media или file без явного вызова Gurra
- **THEN** система не скачивает вложение и не создаёт passive transcript row

