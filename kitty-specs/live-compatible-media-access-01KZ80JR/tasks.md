---
description: "Список задач рабочих пакетов для изоляции доступа и media в Gurra на основе текущего live-состояния"
---

# Рабочие пакеты: совместимый доступ Gurra и изоляция media

**Входы**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/`
**Предварительные условия**: текущая live-derived ветка `a4096896ed92d1edb3dd02e62876dc0fc1ce140a`; реализацию из расходящейся экспериментальной ветки нельзя переносить целиком.
**Тесты**: каждый пакет поведения содержит contract/negative tests; все canary используют synthetic/redacted fixtures и никогда не читают содержимое credential-файлов.

---

## Рабочий пакет WP01: контракт доступа с шестью полями и fail-closed resolver (приоритет P0)

**Цель**: добавить неизменяемый шестиполевой `ResolvedAccessContext`, типизированные role/principal/room bindings, пересечение capabilities и точный identity resolver без изменения live-поведения до включения feature gate.
**Независимая проверка**: unit/contract matrix должна разрешать Руслана(`owner`), все private family role labels с одинаковым набором capabilities, две комнаты(`shared_room`), а unknown и malformed identities --- выдавать ожидаемый context либо deny до model/session/tools.
**Промпт**: `/tasks/WP01-access-contract.md`
**Ссылки на требования**: FR-001, FR-002, FR-004, FR-007

### Включённые подзадачи

- [x] T001 Добавить `gateway/access_registry.py` с ровно шестью serialized context fields, immutable bindings и redacted deny reasons.
- [x] T002 Расширить `gateway/profile_routing.py` для точной DM identity и явного room/topic matching; отклонять ambiguous routes и missing profiles.
- [x] T003 Связать `gateway/authz_mixin.py` и ingress call sites так, чтобы unknown/malformed sources отклонялись до model/session/tools без owner/default fallback.
- [x] T004 [P] Добавить `tests/gateway/test_access_registry.py` и `tests/gateway/test_profile_routing_fail_closed.py` с полной principal/room/unknown matrix.

### Зависимости

- Нет (стартовый пакет).

### Риски и меры

- Старые adapters могут не передавать доверенные account/user metadata; в таком случае отклонять запрос, а не выводить identity из username/display name.
- Существующая single-principal/group policy должна оставаться читаемой; новый resolver размещается за явным compatibility gate, оба режима покрываются тестами.

## Рабочий пакет WP02: изоляция profile, session и фоновых задач (приоритет P0)

**Цель**: провести один resolved context через profile home, session key, memory/session search, attachments, callbacks, cron, compaction/reset/restart и delegation.
**Независимая проверка**: pairwise canaries с guessed session IDs, filenames и memory keys должны показать нулевые наблюдения между двумя family profiles, owner, sandbox и rooms при одновременных turns.
**Промпт**: `/tasks/WP02-runtime-isolation.md`
**Ссылки на требования**: FR-003, FR-005, FR-007

### Включённые подзадачи

- [x] T005 Привязать context в `gateway/session_context.py` и `gateway/run.py` на ingress и очищать его после завершения/cancellation turn.
- [x] T006 Удалить default/active `HERMES_HOME` fallback для rejected или missing profile; foreign `profile_id`/session namespace arguments должны fail closed.
- [x] T007 [P] Обновить `tools/session_search_tool.py`, `tools/memory_tool.py` и file/attachment guards, чтобы namespace выводился только из trusted runtime context.
- [x] T008 [P] Добавить negative tests для callbacks, background tasks, cron delivery, delegation, compaction/reset/restart и simultaneous profiles.

### Зависимости

- Зависит от WP01.

### Риски и меры

- ContextVars могут теряться на thread/executor bridges; добавить явные propagation tests и проверить, что subprocess environment не использует process-global identity fallback.

## Рабочий пакет WP03: scoped image/STT/TTS provider facade (приоритет P1)

**Цель**: добавить ordered media provider policy с capability checks, retry classification, opaque secret references и redacted audit, сохранив текущие provider implementations.
**Независимая проверка**: synthetic providers должны подтвердить порядок image `openai-codex → fal → openrouter`, STT `local → mistral → openai → elevenlabs`, TTS `edge → openai → elevenlabs`, одну попытку на provider и остановку при permanent error.
**Промпт**: `/tasks/WP03-media-provider-facade.md`
**Ссылки на требования**: FR-006, FR-007

### Включённые подзадачи

- [x] T009 Добавить `tools/media_provider_routing.py` с typed image/STT/TTS policies, capability intersection и retry/error normalization.
- [x] T010 Подключить `tools/image_generation_tool.py`, `tools/transcription_tools.py` и `tools/tts_tool.py` через facade без копирования secrets в tool environment или prompts.
- [x] T011 [P] Добавить `tests/test_media_provider_routing.py` и redaction tests для provider outcomes, secret references и unknown tools.
- [x] T012 Проверить, что текущие legacy provider paths не меняются при отключённой compatibility policy.

### Зависимости

- Зависит от WP01.

### Риски и меры

- Providers возвращают несовместимые result/error shapes; нормализовать их к небольшой internal result и писать в log только provider name, status и class.

## Рабочий пакет WP04: проверка policy, dry-run и dashboard audit surface (приоритет P1)

**Цель**: проверять role/principal/room/media configuration, показывать redacted preview effective policy и обеспечивать break-glass lease contract без передачи просмотренных данных в model.
**Независимая проверка**: CLI/dash dry-run отклоняет malformed policies, показывает effective capabilities Руслана/Юли/мамы/rooms, а lease tests требуют reason, истекают через 15 минут и создают metadata-only audit.
**Промпт**: `/tasks/WP04-policy-dry-run-audit.md`
**Ссылки на требования**: FR-004, FR-006, FR-008

### Включённые подзадачи

- [x] T013 Расширить `hermes_cli/config.py` и `hermes_cli/subcommands/config.py` для policy parse/check/dry-run и redacted output.
- [x] T014 Добавить dashboard `Access / Users` endpoints/UI integration на существующей localhost/SSH-authenticated surface; новый внешний listener не создавать.
- [x] T015 [P] Добавить lease/audit tests, подтверждающие отсутствие bulk search/export и model delivery, а также manual early revoke.

### Зависимости

- Зависит от WP01 и WP03.

### Риски и меры

- Legacy config keys могут случайно расширить доступ; проверять unknown keys и считать malformed policy deny-условием.

## Рабочий пакет WP05: migration и profile/room fixture tooling (приоритет P1)

**Цель**: подготовить dry-run migration report, mapping DM ownership по principal, isolated profile/room fixture setup и read-only archive для ambiguous legacy записей.
**Независимая проверка**: synthetic migration counts/hashes стабильны; global memory и ambiguous record не попадают в family profile; rollback оставляет исходные records нетронутыми.
**Промпт**: `/tasks/WP05-migration-fixtures.md`
**Ссылки на требования**: FR-003, FR-009

### Включённые подзадачи

- [x] T016 Добавить redacted migration planner/report и profile/room fixture setup в `tests/fixtures/` и `docs/ops/`.
- [x] T017 Сохранять session IDs/timestamps только для unambiguous DM ownership; ambiguous rows направлять в закрытый read-only archive.
- [x] T018 [P] Добавить migration hash/count и no-global-memory tests; не читать и не копировать credential/auth files.

### Зависимости

- Зависит от WP01 и WP02.

### Риски и меры

- Ownership нельзя безопасно выводить из имён или содержимого; классифицировать такие записи как ambiguous и оставлять в archive.

## Рабочий пакет WP06: staging canary, rollback и live gate packet (приоритет P1)

**Цель**: подготовить live-derived compatibility release, synthetic gateway/dashboard canary, redacted evidence, rollback rehearsal и явный live-cutover checklist.
**Независимая проверка**: staging services остаются healthy, dashboard доступен только через loopback, все privacy/media canaries проходят, rollback восстанавливает captured live code/config surfaces.
**Промпт**: `/tasks/WP06-staging-canary-rollback.md`
**Ссылки на требования**: FR-010

### Включённые подзадачи

- [x] T019 Собрать candidate от current live HEAD в task-owned branch; записать changed-file manifest и SHA-256 evidence без credentials.
- [x] T020 Запустить owner/Юля/мама/other-family/room/unknown synthetic canaries, media policy dry-run и service health checks.
- [x] T021 [P] Документировать backup, rollback и отдельный live restart/Telegram canary gate в `docs/ops/media-access-canary-rollback.md`.
- [ ] T022 Остановиться перед live mutation; выполнять config apply/restart только после нового явного approval пользователя.

### Зависимости

- Зависит от WP01, WP02, WP03, WP04 и WP05.

### Риски и меры

- В live branch могут быть deployment-specific assumptions; сравнить service state до/после и сохранить точные code/config references для rollback.

## Рабочий пакет WP07: явная доставка outbound-артефакта (приоритет P0)

**Цель**: добавить минимальный структурированный инструмент, который публикует обычный non-image файл только из bound profile/workspace как trusted `MEDIA:` candidate; существующий gateway доставляет его в текущий immutable `delivery_target` через adapter `send_document`.
**Независимая проверка**: реальная регистрация/dispatch с family/private context, а также настоящий `AccessRegistry` shared scope с `role_id=shared_room`, group chat и forum thread, возвращают structured success/tag; затем auto-append и gateway delivery вызывают Telegram `send_document` ровно один раз с текущим chat/topic metadata. Foreign chat/thread target, missing/mismatch context, missing `documents`, outside path и symlink escape дают structured failure без tag и adapter call.
**Промпт**: `/tasks/WP07-outbound-artifact-delivery.md`
**Ссылки на требования**: FR-001, FR-003, FR-004, FR-007, FR-011, NFR-001, NFR-009, C-007
**Beads issue**: `hermes-outbound-artifact-delivery-gry`

### Включённые подзадачи

- [x] T023 Проследить все registration/dispatch/toolset-filtering call sites и первым зафиксировать full-boundary RED с точным выводом.
- [x] T024 Добавить negative RED matrix для model-supplied target, missing/malformed/mismatched context, outside path, symlink escape и missing `documents` capability; structured failure не содержит tag, adapter не вызывается.
- [x] T025 Реализовать минимальный structured artifact-publication contract через existing access context, current MessageEvent target, profile/workspace guards и gateway auto-append; tool сам не отправляет и не добавляет dependency, path scraping, retry или fallback.
- [x] T026 Прогнать focused и затронутые send_message/document/access/shared/profile media suites, primary photo/voice regressions, Ruff, `py_compile`, `git diff --check`; выполнить privacy/fail-closed review и commit без live-операций.

### Зависимости

- Зависит от WP01 и WP02.

### Риски и меры

- Подмена target блокируется отсутствием target-полей в schema и отказом на неизвестные аргументы; адресат берётся только из валидированного `delivery_target`.
- Containment проверяется после `resolve(strict=True)`; outside path и symlink escape отклоняются до adapter.
- Для family/shared требуется существующая capability `documents`; owner сохраняет полный file toolset, shared-room membership никогда не повышает роль.

## Рабочий пакет WP08: bound создание и подтверждённая доставка generated document (приоритет P0)

**Цель**: исправить exact shared-room/topic flow, в котором `write_file` создаёт XLS вне trusted roots, legacy `MEDIA:` отклоняется, но пользователю уходит ложный success text; допустить ровно одну correction для safe in-root creation + `deliver_artifact` и разрешать success claim только после успешного `send_document` в current target.
**Независимая проверка**: full-boundary synthetic Telegram group/topic с real registry, agent/model/tool loop и captured adapter сначала воспроизводит unsafe XLS/legacy MEDIA, затем подтверждает одну correction, один safe artifact внутри bound workspace и ровно один `send_document`; second failure не продолжает цикл, foreign/symlink/mismatched context дают ноль delivery/success claim.
**Промпт**: `/tasks/WP08-artifact-bound-workspace-delivery.md`
**Ссылки на требования**: FR-003, FR-005, FR-007, FR-011, FR-012, NFR-001, NFR-009, NFR-010, C-008
**Beads issue**: `tm-ai-loopx-kimi-86x`

### Включённые подзадачи

- [x] T027 Добавить минимальный full-boundary RED с real shared-room/topic sequence и сохранить точную команду/вывод до production code.
- [x] T028 Добавить negative second-failure/no-loop oracle и foreign/symlink/current-context проверки без privacy/log leakage.
- [x] T029 Проследить всех callers и реализовать минимальный ponytail/full root-cause fix через существующие context/root/MEDIA/continuation механизмы, не ослабляя validator.
- [x] T030 Прогнать focused и affected artifact/shared/family/Telegram document/photo/voice/inbound/plain-text suites, Ruff, `py_compile`, `git diff --check`; перед commit получить независимый review и оставить Bead `in_progress` с verification note.

### Зависимости

- Зависит от WP01, WP02 и WP07.

### Риски и меры

- Correction ограничена одной попыткой на user turn; повторный unsafe/failure результат не инициирует новый цикл.
- Success text удерживается до результата `send_document`; target берётся только из текущего typed context/event.
- Gate применяется только к generated non-image document flow в family/shared, поэтому owner capabilities и unrelated photo/voice/inbound/plain-text paths сохраняются.
- Spec Kitty runtime остаётся ограничен `COORDINATION_BRANCH_DELETED`; `status.events.jsonl` и runtime lanes вручную не материализуются.

## Сводка зависимостей и порядка выполнения

- **Последовательность**: WP01 → WP02 и WP03 (параллельно после contract) → WP04 и WP05 → WP06; bounded WP07 зависит от WP01/WP02, WP08 зависит от WP01/WP02/WP07 и не открывает live gate.
- **MVP scope**: WP01 + WP02 + WP03 + focused tests; live rollout не входит.
- **Параллельность**: WP02 и WP03 после WP01 затрагивают непересекающиеся основные модули. WP04 и WP05 можно выполнять после их зависимостей; WP06 --- строго последним.
- **Live gate**: T022 --- жёсткая остановка до отдельного approval; approval реализации не означает разрешение на restart/deploy.

## Покрытие требований

| ID требования | Рабочие пакеты |
|---------------|----------------|
| FR-001 | WP01 |
| FR-002 | WP01 |
| FR-003 | WP02, WP05 |
| FR-004 | WP01, WP04 |
| FR-005 | WP02 |
| FR-006 | WP03, WP04 |
| FR-007 | WP01, WP02, WP03 |
| FR-008 | WP04 |
| FR-009 | WP05 |
| FR-010 | WP06 |
| FR-011 | WP07, WP08 |
| FR-012 | WP08 |

## Индекс подзадач (справочно)

| ID | Краткое описание | Пакет | Приоритет | Параллельная? |
|----|------------------|-------|-----------|---------------|
| T001 | Контекст с шестью полями | WP01 | P0 | Нет |
| T004 | Identity matrix | WP01 | P0 | Да |
| T005 | Context binding | WP02 | P0 | Нет |
| T009 | Media facade | WP03 | P1 | Нет |
| T013 | Policy dry-run | WP04 | P1 | Нет |
| T016 | Migration planner | WP05 | P1 | Нет |
| T019 | Staging artifact | WP06 | P1 | Нет |
| T022 | Остановка на live gate | WP06 | P1 | Нет |
| T023 | Full-boundary RED | WP07 | P0 | Нет |
| T024 | Negative RED matrix | WP07 | P0 | Да |
| T025 | Structured artifact delivery | WP07 | P0 | Нет |
| T026 | Полные проверки и review | WP07 | P0 | Нет |
| T027 | Full-boundary generated-document RED | WP08 | P0 | Нет |
| T028 | Negative no-loop/privacy matrix | WP08 | P0 | Да |
| T029 | Минимальный root-cause fix | WP08 | P0 | Нет |
| T030 | Affected suites и независимый review | WP08 | P0 | Нет |
