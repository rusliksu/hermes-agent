## Context

Shared scope уже определяется authoritative `SinglePrincipalPolicy` до agent
construction. Эта единственная точка перезаписывает platform toolsets на
`memory`, поэтому общий Telegram config не может вернуть web capability.
Существующие web tools уже имеют SSRF/credential URL guards и availability
gate.

## Goals / Non-Goals

**Goals:**

- переиспользовать существующий `web` toolset только после authoritative shared
  scope resolution;
- сохранить exact private capability boundary;
- корректно деградировать до memory-only profile, если backend недоступен.

**Non-Goals:**

- новый tool/provider/dependency;
- terminal/browser/file/MCP в shared scope;
- config, credential или service mutations.

## Decisions

### 1. Изменить единственный shared override

`enabled_toolsets` становится `["memory", "web"]`. Это корневая и минимальная
точка; platform config и остальные ingress paths не меняются.

### 2. Валидировать два полных safe profile

Backend availability уже фильтрует tool schemas. Поэтому разрешены только
полный web profile и memory-only degradation. Частичный web profile и любой
extra tool fail-closed отклоняются.

### 3. Дать стабильную prompt-инструкцию для outage

Shared boundary остаётся cache-stable и сообщает: использовать public web tools,
если они присутствуют; иначе явно назвать недоступность backend и запросить
текст/скриншоты. URL contents нельзя угадывать.

## Risks / Trade-offs

- [Backend недоступен] → memory-only profile и точная model instruction;
  deployment preflight отдельно требует полный web profile.
- [Tool surface случайно расширен] → exact-set validation и negative tests.
- [Future RBAC меняет capability source] → изменение локализовано в shared
  override и exact validation и может быть заменено capability-aware variant.

## Migration Plan

Repo patch и tests; deployment отсутствует. Live rollout выполняется только
родительским Gurra change после отдельного gate.

## Open Questions

Нет.
