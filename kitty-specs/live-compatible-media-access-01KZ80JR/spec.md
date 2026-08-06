# Mission Specification: Live-compatible Gurra access and media isolation

**Mission Branch**: `codex/live-compatible-media-cutover`
**Created**: 2026-08-05
**Status**: Draft for approval
**Input**: User request to choose the safest best option for Codex OAuth-compatible image generation, voice recognition/transcription and voice synthesis, while preserving fail-closed per-user isolation.

## User Scenarios & Testing

### User Story 1 - Private principal routing (Priority: P1)

Руслан, Юля, мама и остальные подтверждённые пользователи получают ответы в своих изолированных профилях одного Telegram-бота. Руслан остаётся `owner`; семейные principals могут сохранять метки `family_standard`/`family_sandbox` для совместимости, но получают одинаковый безопасный пользовательский набор инструментов. Отдельные зарегистрированные темы работают как `shared_room`.

**Why this priority**: Ошибка маршрутизации может раскрыть историю, память, файлы или credentials другого человека. Изоляция важнее удобства и должна быть включена до расширения media tools.

**Independent Test**: На синтетической матрице owner + 9 family + 2 rooms + unknown отправить DM и topic-сообщение и проверить выбранный profile/scope либо отказ до model/session/tools.

**Acceptance Scenarios**:

1. **Given** подтверждённый Telegram DM с согласованными `user_id` и `chat_id`, **When** ingress разрешает запрос, **Then** он получает ровно один `ResolvedAccessContext` с шестью полями и используется только его `profile_id`.
2. **Given** неизвестный, malformed или отсутствующий route, **When** приходит сообщение, **Then** запрос отклоняется до запуска модели и не получает owner/default fallback.
3. **Given** участник shared room, **When** он пишет в зарегистрированный topic, **Then** применяется только scope комнаты; membership не повышает его личную роль и не открывает private memory.

### User Story 2 - Scoped media generation and transcription (Priority: P1)

Подтверждённый пользователь может попросить доступное изображение, распознать голосовое или получить озвучку через серверную цепочку providers. Система предпочитает Codex-scoped provider, затем использует настроенные fallbacks, не раскрывая ключи tool-процессам.

**Why this priority**: Это непосредственный пользовательский сценарий последнего запроса, но он должен работать только после правильного access context.

**Independent Test**: В dry-run прогнать image/STT/TTS policy для каждой роли с synthetic provider outcomes и убедиться в порядке providers, retry-классах и redacted audit.

**Acceptance Scenarios**:

1. **Given** `family_standard` с разрешённой image capability, **When** Codex provider временно недоступен, **Then** выполняется только следующий разрешённый provider, а результат остаётся в его profile namespace.
2. **Given** STT input с повреждённым/неподдерживаемым форматом, **When** первый provider отвечает permanent error, **Then** цепочка не делает бессмысленный retry и возвращает безопасную ошибку без передачи секретов.
3. **Given** shared_room без явного server-configured media provider, **When** запрошена генерация, **Then** доступ запрещается (fail-closed), а не наследуется от личной роли участника.

### User Story 3 - Persistent per-profile state (Priority: P1)

Каждый principal получает отдельные Hermes profile/home, memory, SQLite session namespace, prompt/context, skills и workspace; room profile отдельно владеет только общей комнатной памятью.

**Why this priority**: RBAC без изоляции состояния не предотвращает утечки через session search, compaction, callbacks или фоновые задачи.

**Independent Test**: Выполнить pairwise canary для session, memory, guessed IDs, attachments, filesystem, browser state, compaction/reset/restart и concurrent turns.

**Acceptance Scenarios**:

1. **Given** два разных principals с одинаковым guessed session ID, **When** они выполняют `session_search` или attachment lookup, **Then** каждый видит только свой namespace, а чужой ID даёт отказ/пустой результат без probe.
2. **Given** callback, cron delivery или delegation, **When** работа продолжается после ingress, **Then** она переносит исходный six-field context и не может обратиться к другому profile.

### User Story 4 - Auditable staged rollout and rollback (Priority: P2)

Оператор получает redacted dry-run report, staging canary, backup и понятный rollback; live services не перезапускаются без отдельного подтверждения.

**Why this priority**: Live сейчас работает на ветке, которая расходится с экспериментальной реализацией, поэтому полный перенос создаёт непредсказуемый privacy и availability риск.

**Independent Test**: На снимке live-ветки собрать compatibility release, запустить synthetic gateway/dashboard canary, проверить отсутствие credential reads, затем симулировать rollback до live HEAD и config backup.

**Acceptance Scenarios**:

1. **Given** staging candidate на live-derived base, **When** policy and privacy suites pass, **Then** публикуется redacted evidence с hashes и явно перечисленными изменёнными файлами.
2. **Given** live canary или preflight failure, **When** оператор запускает rollback, **Then** восстанавливаются предыдущие code/config surfaces без удаления legacy данных.

## Edge Cases

- Telegram DM с `user_id != chat_id`, username-only identity, edited message или missing account metadata: reject before model/session/tools.
- User removed from a room, topic unknown, room membership stale, or room profile unhealthy: reject; never fall back to personal profile.
- Provider timeout, rate limit, malformed response, unsupported media, all providers exhausted, or provider policy absent: classify retryable/permanent and return redacted diagnostic.
- Session reset, compaction, restart, background callback and concurrent turns must preserve the same context and namespace.
- Existing ambiguous/legacy session records stay in a closed read-only archive; global `MEMORY.md`, `USER.md` and personal context are not copied into family profiles.
- A tool asks for a foreign profile/session namespace or an unknown capability: deny regardless of model-supplied arguments.

## Requirements

### Functional Requirements

| ID | Title | User Story | Priority | Status |
|----|-------|------------|----------|--------|
| FR-001 | Six-field access contract | As the gateway, I want one immutable `ResolvedAccessContext` containing exactly `principal_id`, `role_id`, `profile_id`, `conversation_scope`, `capabilities`, and `delivery_target` so that every downstream operation has one trusted identity. | High | Open |
| FR-002 | Fail-closed ingress | As the gateway, I want exact `platform + account + peer_kind + user_id` DM and room routing with Telegram identity consistency checks so that unknown or malformed traffic is rejected before model/session/tools and never falls back to owner. | High | Open |
| FR-003 | Profile and namespace isolation | As a principal, I want separate Hermes home, memory, sessions, prompts, skills and workspace so that another principal cannot read or guess my state. | High | Open |
| FR-004 | Role and scope resolution | As an operator, I want `owner`, equal-capability private family execution classes, and `shared_room` policies intersected with explicit room bindings so that room membership never elevates a personal role and sandbox labels do not create family privilege tiers. | High | Open |
| FR-005 | Context propagation | As the runtime, I want the same access context attached to sessions, memory, prompts, tools, callbacks, cron and delegation so that background work cannot cross profile boundaries. | High | Open |
| FR-006 | Scoped media fallback | As an authorized user, I want image, STT and TTS providers tried in server-configured order with one attempt per provider and scoped opaque secret references so that media works without exposing credentials to tools. | High | Open |
| FR-007 | Model-controlled tool deny-by-default | As the authorization layer, I want unknown tools and model-supplied foreign namespaces denied unless capability and backend policy both allow them. | High | Open |
| FR-008 | Dashboard audit and break-glass boundary | As the owner, I want redacted access health and a reason-bound, read-only 15-minute break-glass lease without model delivery, bulk search or export. | Medium | Open |
| FR-009 | Migration safety | As the operator, I want dry-run counts/hashes, per-principal unambiguous DM migration and a closed read-only legacy archive for ambiguous records so that migration is reversible and data ownership is not guessed. | High | Open |
| FR-010 | Staged rollout and rollback | As the operator, I want live-derived staging, synthetic canaries, backups and an explicit live restart gate so that this compatibility slice can be rolled back to the current live base. | High | Open |

### Non-Functional Requirements

| ID | Title | Requirement | Category | Priority | Status |
|----|-------|-------------|----------|----------|--------|
| NFR-001 | Privacy boundary | In pairwise canaries, 0 cross-principal reads are permitted across sessions, memory, prompts, attachments, filesystem, browser state and search. | Security | High | Open |
| NFR-002 | Early rejection | Unknown or malformed identity must be rejected before model/session/tools in 100% of synthetic cases. | Security | High | Open |
| NFR-003 | Context shape | Serialized access context must contain exactly six contract fields; extra fields fail validation. | Compatibility | High | Open |
| NFR-004 | Provider safety | Provider secrets are never copied into tool environment or redacted evidence; audit contains provider/status/error class only. | Security | High | Open |
| NFR-005 | Availability | A transient provider failure may advance at most once to the next configured provider; permanent errors must not fan out. | Reliability | High | Open |
| NFR-006 | Resource limits | Every private family execution class enforces 2 vCPU, 2 GiB RAM, 256 PIDs, 5 GiB workspace, no host mounts and terminal network disabled. | Security | High | Open |
| NFR-007 | Observability | Every deny, fallback and break-glass event has a redacted audit record with no session contents or credentials. | Auditability | Medium | Open |
| NFR-008 | Rollback | A failed canary can restore previous code/config surfaces without destructive data cleanup. | Reliability | High | Open |

### Constraints

| ID | Title | Constraint | Category | Priority | Status |
|----|-------|------------|----------|----------|--------|
| C-001 | Live-derived base | Implementation must start from current live HEAD `a4096896ed92d1edb3dd02e62876dc0fc1ce140a`; do not transplant the 16k-commit divergent branch wholesale. | Technical | High | Open |
| C-002 | One bot | Keep one Telegram bot and route by confirmed transport identities; username/display name is never an identity key. | Product | High | Open |
| C-003 | No external policy engine | Use a typed server-side resolver; do not add OPA/OpenFGA for the current 10-user/two-room scale. | Technical | Medium | Open |
| C-004 | OAuth and provider scope | Prefer Codex OAuth/scoped model client references; fallbacks are server-configured and opaque to model/tool processes. | Security | High | Open |
| C-005 | Separate live gate | Backup, live config application, service restart and Telegram canary require a separate explicit approval after implementation and staging evidence. | Operations | High | Open |
| C-006 | No global memory copy | Do not migrate global `MEMORY.md`, `USER.md` or personal context into family profiles. | Privacy | High | Open |

### Key Entities

- **ResolvedAccessContext**: immutable six-field ingress result carried through the complete request lifecycle.
- **RolePolicy**: read-only prompt, capabilities, sandbox and delivery restrictions for one role.
- **PrincipalBinding**: opaque principal, profile, role and confirmed transport identities.
- **SharedScopeBinding**: explicit room/topic, room profile and participant set.
- **MediaProviderPolicy**: ordered image/STT/TTS provider chains, retry classes and scoped secret references.
- **LegacyArchiveRecord**: ambiguous legacy session metadata retained read-only with no model access.
- **BreakGlassLease**: owner-audited, reason-bound 15-minute read-only inspection lease.

## Success Criteria

### Measurable Outcomes

- **SC-001**: 100% of owner/family/room/unknown matrix cases resolve to the expected profile/role or are rejected before model/session/tools.
- **SC-002**: Pairwise privacy suite records 0 cross-principal observations in 100% of session, memory, prompt, attachment, filesystem, browser and search probes.
- **SC-003**: Synthetic media policy confirms the configured image, STT and TTS order and never exposes a secret value in logs, evidence or tool environment.
- **SC-004**: Staging gateway and dashboard canary completes with services healthy, loopback-only dashboard, no restart loop and reproducible evidence hashes.
- **SC-005**: Migration dry-run reports stable counts/hashes and leaves ambiguous/global memory records outside family profiles.
- **SC-006**: Rollback rehearsal restores the live-derived code/config baseline without destructive cleanup.
