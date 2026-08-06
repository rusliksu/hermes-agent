## ADDED Requirements

### Requirement: Allowlisted shared scope получает public web tools
Authoritative Telegram shared scope MUST запрашивать существующие toolsets
`memory` и `web`.

#### Scenario: Web backend доступен
- **WHEN** shared agent строится при доступных web tools
- **THEN** exact runtime tool set равен `memory`, `web_search`, `web_extract`

#### Scenario: Web backend недоступен
- **WHEN** availability gate скрывает оба web tools
- **THEN** shared agent остаётся memory-only и сообщает, что web backend недоступен

### Requirement: Private tool boundary остаётся fail-closed
Shared runtime profile MUST отклонять частичный web profile и любой tool вне
разрешённых exact sets.

#### Scenario: Partial web profile
- **WHEN** runtime публикует только один из `web_search` или `web_extract`
- **THEN** shared capability validation отклоняет agent

#### Scenario: Extra private tool
- **WHEN** runtime profile содержит terminal, file, browser или иной extra tool
- **THEN** shared capability validation отклоняет agent до tool execution

### Requirement: URL contents не угадываются
Shared prompt MUST требовать web extraction до содержательного ответа по URL и
MUST требовать actionable fallback при отсутствии web tools.

#### Scenario: Tool отсутствует
- **WHEN** пользователь просит обработать URL, а web tool не опубликован
- **THEN** agent называет недоступность web backend и просит текст или скриншоты
