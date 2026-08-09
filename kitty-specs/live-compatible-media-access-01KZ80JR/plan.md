# Implementation Plan: Live-compatible Gurra access and media isolation

**Branch**: `codex/live-compatible-media-cutover` | **Date**: 2026-08-05 | **Spec**: `kitty-specs/live-compatible-media-access-01KZ80JR/spec.md`
**Input**: Feature specification from `kitty-specs/live-compatible-media-access-01KZ80JR/spec.md`

## Summary

Текущий live уже содержит profile multiplexing, session-context и независимые media hooks, но его `profile_routes` и `_resolve_profile_home_for_source` допускают default/owner fallback, а six-field access contract отсутствует. Экспериментальная ветка с готовым registry/media router расходится с live на 16 273 коммита, поэтому её нельзя переносить целиком.

Лучший безопасный вариант --- live-derived compatibility slice: добавить типизированный resolver доступа и scoped media policy поверх существующих hooks, затем включать поведение через явный config gate. До live cutover кандидат собирается на staging из текущего live HEAD, проходит dry-run и synthetic privacy/media canaries, а live restart остаётся отдельным gate.

## Technical Context

**Language/Version**: Python 3.11+ (текущий Hermes runtime; поддержать Python 3.11 и 3.12)
**Primary Dependencies**: stdlib `dataclasses`/`contextvars`/`typing`, существующие `gateway`, `hermes_cli`, `tools`, plugin registries; без OPA/OpenFGA и без новой обязательной cloud-зависимости
**Storage**: существующие per-profile `HERMES_HOME`, SQLite session stores, memory/profile directories; immutable access context не хранится в пользовательских данных
**Testing**: pytest unit/contract/integration suites, synthetic policy dry-run, pairwise privacy canaries, staging gateway/dashboard smoke; credential-safe fixtures only
**Target Platform**: Linux VPS, systemd `hermes-gateway` и `hermes-dashboard`, один Telegram bot, localhost/SSH dashboard access
**Project Type**: single Python service with CLI, gateway adapters, tools and dashboard
**Performance Goals**: access resolution без network/DB round-trip на ingress; один bounded resolver и не более одного retry-перехода между media providers
**Constraints**: unknown/malformed identity reject-before-model; ровно шесть serialized context fields; no owner/default fallback; no credential reads in tests/evidence; live restart only after explicit gate
**Scale/Scope**: 10 principals (Руслан + 9 family) and 2 registered shared rooms/topics; one compatibility release, not a wholesale branch transplant

## Charter Check

*GATE: planning artifacts are limited to a bounded live-derived compatibility slice; no code or live mutation is authorized by this plan alone.*

- Preserve current live base `a4096896ed92d1edb3dd02e62876dc0fc1ce140a`.
- Keep one Telegram bot and existing profile/session/media abstractions.
- No secrets, auth files, provider token values or global memory contents are read by implementation or validation.
- Implementation begins only after explicit user approval of this baseline; live config/restart/canary require a second explicit gate.

## Architecture and Boundaries

1. **Ingress resolver** --- `gateway/access_registry.py` owns immutable `ResolvedAccessContext`, principal/role/scope bindings, capability intersection and redacted deny/audit reasons. It is pure server-side logic and has no model/tool imports.
2. **Route adapter** --- `gateway/profile_routing.py`, `gateway/authz_mixin.py`, `gateway/run.py` adapt `SessionSource` to the resolver. Exact DM key is `platform + account + peer_kind + user_id`; Telegram DM additionally requires normalized positive `user_id == chat_id`. Room/topic routes use explicit `SharedScopeBinding` and never promote the personal role.
3. **Runtime propagation** --- `gateway/session_context.py`, session key/profile-home helpers, memory/session search and background paths receive the resolved context once. Model-supplied profile/session namespaces are ignored or denied; no exception path calls active/default `HERMES_HOME` for a rejected request.
4. **Media facade** --- `tools/media_provider_routing.py` provides typed ordered image/STT/TTS policies, retry classification, one-attempt-per-provider execution and opaque secret references. Existing `tools/image_generation_tool.py`, `tools/transcription_tools.py`, `tools/tts_tool.py` remain provider implementations and are called through the facade only when capability/backend policy allows.
5. **Config/CLI surface** --- `hermes_cli/config.py` and `hermes_cli/subcommands/config.py` add parse/validate/dry-run support without exposing secrets. Existing legacy config remains accepted in compatibility mode; new policy is opt-in until canary evidence is complete.
6. **Validation and operations** --- focused tests/fixtures and a redacted canary packet exercise owner, family, room, unknown and malformed cases. Backup, staging artifact hash, rollback command and live gate are documented; no live service mutation belongs in implementation WPs.

## Project Structure

### Documentation (this mission)

```
kitty-specs/live-compatible-media-access-01KZ80JR/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
└── tasks.md
```

### Source Code (repository root)

```
gateway/
├── access_registry.py              # new six-field resolver and bindings
├── profile_routing.py              # exact route matching adapter
├── authz_mixin.py                  # ingress authorization bridge
├── run.py                          # profile scope/context propagation
└── session_context.py              # task-local context extension
hermes_cli/
├── config.py                       # policy schema/load/check
└── subcommands/config.py           # dry-run/check command surface
tools/
├── media_provider_routing.py       # new scoped image/STT/TTS facade
├── image_generation_tool.py        # facade integration only
├── transcription_tools.py          # facade integration only
└── tts_tool.py                     # facade integration only
tests/
├── gateway/test_access_registry.py
├── gateway/test_profile_routing_fail_closed.py
├── test_media_provider_routing.py
├── test_media_privacy_pairwise.py
└── fixtures/access_policy_matrix.json
docs/ops/
└── media-access-canary-rollback.md
```

**Structure Decision**: preserve the current live single-project layout and add pure policy modules beside existing gateway/tool boundaries. Do not import dashboard, provider SDKs or secrets into `gateway/access_registry.py`; do not replace existing provider implementations.

## Data and Contract Decisions

### `ResolvedAccessContext` (exactly six fields)

```text
principal_id
role_id
profile_id
conversation_scope
capabilities
delivery_target
```

The object is immutable, has strict field-count validation on serialization/deserialization, and carries no raw Telegram username, prompt text, session contents or credentials. `conversation_scope` is either an explicit private namespace or a registered room/topic namespace. `delivery_target` is server-resolved and cannot be overridden by model arguments.

### Role policies

- `owner`: Руслан's current full profile, subject to existing backend safety controls.
- `family`: one private user-tool policy for all nine family principals --- private memory/session search, documents, attachments, vision, public web, image/voice generation, self-only reminders, Wolfram, same-profile delegation, personal Docker workspace and isolated public browser. The profile/backend policy forbids host mounts/credentials, keeps terminal network disabled and enforces 2 vCPU/2 GiB/256 PID/5 GiB limits.
- `family_standard` and `family_sandbox` are accepted only as migration-boundary aliases, normalize to `family`, and are never emitted by the resolver or dashboard. The old sandbox label is not a trust or capability tier.
- `shared_room`: room profile only; shared session/memory, documents/vision/public web; no private memory, cron, private delivery, shell or cross-user search. Wolfram is not inherited and is enabled only by an explicit room policy entry.

Effective capabilities are the intersection of role policy, scope policy and backend policy. Unknown capabilities/tools are denied.

### Media policy

Default candidate order is image `openai-codex → fal → openrouter`, STT `local → mistral → openai → elevenlabs`, TTS `edge → openai → elevenlabs`. The order is configuration, not a credential discovery mechanism. A provider gets one attempt; only an allowlisted transient class advances the chain. Permanent/unsupported/invalid-input errors stop immediately. Secret references resolve server-side for the model client and never enter tool environment, prompt, fixture or redacted evidence.

## Implementation Concern Map

### IC-01 --- Exact identity and fail-closed resolution

- **Purpose**: turn trusted ingress metadata into one six-field context or a deny result with no fallback.
- **Relevant requirements**: FR-001, FR-002, FR-004, NFR-002, NFR-003.
- **Affected surfaces**: `gateway/access_registry.py`, `gateway/profile_routing.py`, `gateway/authz_mixin.py`, identity fixtures.
- **Sequencing/depends-on**: none.
- **Risks**: legacy adapters omit account/user metadata; reject rather than infer from display name.

### IC-02 --- Profile/session/memory isolation and propagation

- **Purpose**: bind every foreground/background operation to the context's profile and scope.
- **Relevant requirements**: FR-003, FR-005, FR-007, NFR-001.
- **Affected surfaces**: `gateway/run.py`, `gateway/session_context.py`, session/memory search hooks, callbacks/cron/delegation tests.
- **Sequencing/depends-on**: IC-01.
- **Risks**: current defensive profile resolver has a global-home fallback; replace with explicit deny/legacy compatibility branch guarded by policy.

### IC-03 --- Scoped media provider facade

- **Purpose**: expose one ordered, auditable and capability-checked image/STT/TTS path over existing providers.
- **Relevant requirements**: FR-006, FR-007, NFR-004, NFR-005.
- **Affected surfaces**: `tools/media_provider_routing.py`, image/STT/TTS tools, config validators.
- **Sequencing/depends-on**: IC-01.
- **Risks**: provider plugins have different error/result shapes; normalize to a small internal result and redact diagnostics.

### IC-04 --- Configuration and policy dry-run

- **Purpose**: validate role/binding/provider shapes and emit a redacted effective policy before activation.
- **Relevant requirements**: FR-004, FR-006, FR-008, NFR-003, NFR-007.
- **Affected surfaces**: `hermes_cli/config.py`, `hermes_cli/subcommands/config.py`, policy fixtures and CLI tests.
- **Sequencing/depends-on**: IC-01, IC-03.
- **Risks**: legacy config keys must remain readable without silently broadening access.

### IC-05 --- Migration, canary and rollback evidence

- **Purpose**: prove the compatibility slice on a live-derived staging artifact before any restart.
- **Relevant requirements**: FR-009, FR-010, NFR-008, C-001, C-005, C-006.
- **Affected surfaces**: `docs/ops/media-access-canary-rollback.md`, redacted fixtures/scripts, staging-only runner.
- **Sequencing/depends-on**: IC-01 through IC-04.
- **Risks**: live branch may have hidden local deployment assumptions; keep candidate isolated and compare service state before/after.

### IC-06 --- Явная доставка outbound-артефакта

- **Purpose**: добавить минимальный структурированный contract для доставки существующего non-image файла в текущий разговор через уже выбранный adapter.
- **Relevant requirements**: FR-001, FR-003, FR-004, FR-007, FR-011, NFR-001, NFR-009, C-007.
- **Affected surfaces**: реестр инструментов `tools/`, существующий `file` toolset, boundary regression и access/document/media tests.
- **Design**: schema принимает только путь; handler повторно валидирует bound six-field context, current MessageEvent target и `documents` capability для family/shared, разрешает regular file после `resolve` только внутри typed profile/workspace roots и возвращает structured success с trusted `MEDIA:<absolute-path>`. Существующие gateway auto-append и `_deliver_media_from_response` выполняют единственный `send_document` с current event/thread metadata; tool сам не отправляет.
- **Non-goals**: generic path scraping, terminal stdout scanning, новый target API, dependency, retry/fallback, изменение MEDIA/TTS/image/voice paths, live mutation.
- **Sequencing/depends-on**: IC-01 и IC-02; реализация строго RED → GREEN от candidate `907dbea2960907d21e38a9b5f55ac7a10a62864c`.
- **Risks**: модель может попытаться подменить target или передать symlink; неизвестные аргументы, foreign roots, missing/malformed/mismatched context, missing `documents` и account/thread mismatch отклоняются fail-closed без `MEDIA:` tag.

### IC-07 --- Bound создание и подтверждение доставки generated document

- **Purpose**: закрыть exact shared-room/topic sequence, где `write_file` создаёт документ вне bound roots, legacy `MEDIA:` отклоняется validator-ом, но success text всё равно уходит пользователю.
- **Relevant requirements**: FR-003, FR-005, FR-007, FR-011, FR-012, NFR-001, NFR-009, NFR-010, C-008.
- **Affected surfaces**: существующие agent continuation/finalization и gateway document delivery boundaries; full-boundary synthetic test с real registry/tool dispatch и captured Telegram adapter.
- **Design**: переиспользовать `ResolvedAccessContext`, typed bound-root validation, текущую MEDIA extraction и agent continuation machinery. Для family/shared generated document unsafe/out-of-root finalization получает ровно одну synthetic corrective continuation с сохранением role alternation; повторная неудача завершается fail-closed. Success claim удерживается до успешного `send_document` в current event/topic target.
- **Non-goals**: новый service/parser/dependency, arbitrary outside-path copy, ослабление validator, прямой target от модели, изменения owner/photo/voice/inbound-document/plain-text behavior.
- **Sequencing/depends-on**: IC-01, IC-02 и IC-06; строго full-boundary RED до production code, затем минимальный ponytail/full root-cause fix.
- **Risks**: общий MEDIA pipeline обслуживает photo/voice/video; gate должен быть узким по typed family/shared context и generated-document evidence, а continuation иметь жёсткий предел один.

## Phased Delivery and Gates

### Phase 0 --- Read-only baseline (current)

- Capture live HEAD, service ExecStart, branch divergence, current profile/media hooks and Bead `hermes-live-compatible-a61`.
- Complete this mission's spec/plan/tasks in the isolated planning snapshot.
- Gate: user approves baseline before code implementation.

### Phase 1 --- Contract and negative tests

- Implement IC-01 and fixtures first.
- Gate: six-field shape, identity matrix and no-fallback tests pass.

### Phase 2 --- Runtime propagation

- Implement IC-02 with one context binding at ingress and explicit reject paths.
- Gate: pairwise session/memory/filesystem/search and concurrent/background tests pass.

### Phase 3 --- Media policy and dry-run

- Implement IC-03 and IC-04; wire only existing image/STT/TTS implementations.
- Gate: policy order/retry/privacy tests and redacted CLI dry-run pass.

### Phase 4 --- Staging compatibility release

- Build from live-derived branch, package a redacted evidence bundle, run gateway/dashboard synthetic canary and rollback rehearsal.
- Gate: services healthy in staging; no credential reads; all privacy/media suites green.

### Phase 5 --- Separate live gate

- Only after an explicit new approval: backup metadata/config, apply live code/config, restart `hermes-gateway`/`hermes-dashboard`, run owner/Юля/мама/other-family/room/unknown canaries, verify service health.
- Failure action: restore exact prior code/config surfaces; do not delete legacy/archive data.

### Phase 6 --- Bounded outbound artifact delivery

- Создать WP07 и связать его с Bead `hermes-outbound-artifact-delivery-gry` (`spec_id: live-compatible-media-access-01KZ80JR`).
- Сначала зафиксировать full-boundary RED через real registration/dispatch, family/private context и captured Telegram adapter, включая negative matrix.
- Затем внести минимальный tool/toolset patch, прогнать focused и затронутые access/document/shared/profile media suites, Ruff, `py_compile` и `git diff --check`.
- Gate: commit task-owned changes; остановиться до push/merge/deploy/restart/config/symlink/Telegram операций.

### Phase 7 --- Bound workspace artifact correction

- Создать WP08 и связать его с Bead `tm-ai-loopx-kimi-86x` (`spec_id: live-compatible-media-access-01KZ80JR`).
- Первым зафиксировать full-boundary RED реального shared-room/topic sequence: unsafe outside-root XLS + legacy `MEDIA:`/success, затем одна safe correction через `write_file` + `deliver_artifact`; добавить second-failure/no-loop и foreign/symlink/current-context oracles.
- Минимально исправить общий root cause без изменения fail-closed validator и без copy произвольного outside artifact.
- Gate: focused/affected suites, независимый review и task-owned commit; Bead остаётся `in_progress`, live/push/merge/deploy/restart запрещены.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Add one pure access registry and one media facade | Existing live hooks are scattered and default fallback is unsafe | More ad-hoc checks in adapters would duplicate identity logic and miss callbacks/tools |
| Keep a compatibility mode during rollout | Current live config and existing profiles must remain readable until canary | Big-bang migration would couple unknown data ownership with a live restart |

## Open Decisions for Implementation Gate

- Exact list of currently confirmed family Telegram IDs and room/topic IDs must be supplied from server-side config metadata without printing credential/auth contents.
- Whether the first live activation uses a feature flag or a separate config key is an implementation detail; either must fail closed when malformed.
- Existing provider plugin availability is discovered by registered capability, never by assuming an environment variable is safe or present.

## Verification Commands

```bash
python -m pytest -q tests/gateway/test_access_registry.py tests/gateway/test_profile_routing_fail_closed.py tests/test_media_provider_routing.py tests/test_media_privacy_pairwise.py
python -m hermes_cli.main config check
python -m hermes_cli.main config media-policy --dry-run  # if the compatibility CLI adds this command
```

The last command is conditional on the implementation adding the subcommand; until then the equivalent library dry-run test is authoritative. All validation runs on synthetic/redacted inputs and must not read credential file contents.
