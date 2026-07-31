## ADDED Requirements

### Requirement: Детерминированная DM migration
Система SHALL переносить все однозначные owned DM sessions детерминированно в principal profiles с сохранением IDs, timestamps, counts и hashes.

#### Scenario: Сохранение IDs timestamps counts hashes
- **WHEN** dry-run или live migration обрабатывает owned DM session
- **THEN** migrated output MUST сохранить session IDs, message timestamps, message counts и deterministic content hashes

#### Scenario: Повторный dry-run стабилен
- **WHEN** тот же input archive обрабатывается дважды в dry-run mode
- **THEN** система MUST выдать identical mapping, counts и hashes

#### Scenario: Global memory не импортируется
- **WHEN** migration создает family или shared profiles
- **THEN** система MUST NOT импортировать global `MEMORY.md`, global `USER.md` или personal context в эти profiles

### Requirement: Ambiguous legacy archive для спорных сессий
Система MUST перемещать ambiguous legacy sessions в closed read-only archive вместо active profiles.

#### Scenario: Ambiguous identity для legacy
- **WHEN** legacy session не имеет exact owner/family DM identity или содержит conflicting chat/user fields
- **THEN** migration MUST поместить ее в closed read-only legacy archive с preserved hashes/counts

#### Scenario: Archive не является active memory
- **WHEN** archived legacy session существует
- **THEN** active profiles MUST NOT включать ее в session search, memory hydration, prompt context или tool-visible files

### Requirement: Room and topic mapping для комнат
Система SHALL отображать shared rooms и topics в configured shared profiles без слияния private DM history.

#### Scenario: Room mapping для shared room
- **WHEN** migration видит rows для одной из 2 configured shared rooms или topics
- **THEN** система MUST mapped rows поместить в matching shared room profile и scope

#### Scenario: Topic namespace separation внутри комнаты
- **WHEN** migration видит несколько topics inside same shared room profile
- **THEN** система MUST держать каждый topic в separate namespace внутри room profile

#### Scenario: DM room separation между DM и комнатой
- **WHEN** один human имеет private DM history и room participation
- **THEN** migration MUST держать DM history в private profile, а room history в shared room profile

### Requirement: Backup dry-run compare rollback перед live
Система SHALL предоставлять backup, dry-run, compare mode и rollback до любой live migration.

#### Scenario: Backup обязателен
- **WHEN** live migration запрошена
- **THEN** система MUST требовать verified backup affected config, profile mappings, sessions, memory и attachments до mutation

#### Scenario: Dry-run обязателен
- **WHEN** operator запрашивает live migration без successful dry-run report
- **THEN** система MUST отказать в live migration

#### Scenario: Compare mode без live изменений
- **WHEN** additive rollout запускается в compare mode
- **THEN** система MUST report differences между legacy и new routing без изменения live active routing

#### Scenario: Rollback восстанавливает state
- **WHEN** rollback invoked после gated live migration
- **THEN** система MUST restore saved profile mappings and migrated data from backup или отказать с explicit error при failed backup verification

### Requirement: Explicit live delivery gate с отдельным approval
Система MUST считать live config, migration, restart и Telegram canary отдельным explicit delivery gate после implementation и validation.

#### Scenario: Нет implicit live effects
- **WHEN** planning, implementation или local validation runs до explicit live approval
- **THEN** система MUST NOT менять live config, run live migration, restart services или send Telegram canaries

#### Scenario: Gate approval обязателен
- **WHEN** operator пытается выполнить live config/migration/restart/canary
- **THEN** система MUST require separate explicit approval после implementation checks, dry-run и preflight

### Requirement: Live preflight and canary safety для rollout
Система SHALL выполнять live preflight, canaries, service health checks, privacy warnings и rollback checks во время delivery gate.

#### Scenario: Preflight блокирует rollout
- **WHEN** live preflight detects missing bindings, duplicate profiles, failed backup, failed dry-run, credential risk, service mismatch или privacy warning not acknowledged
- **THEN** система MUST block live rollout до config или service changes

#### Scenario: Service active check для сервисов
- **WHEN** gateway или dashboard restarted during approved delivery
- **THEN** система MUST verify active/running state для `hermes-gateway` и `hermes-dashboard` and MUST verify no restart loop before canaries accepted

#### Scenario: Telegram canaries by cohort отдельно
- **WHEN** approved delivery доходит до canary phase
- **THEN** система MUST test owner, Юля, мама, remaining seven family principals, both shared rooms, unknown ingress и malformed ingress separately without logging private message bodies

#### Scenario: Privacy warnings в отчетах
- **WHEN** canary или migration report показан
- **THEN** report MUST include privacy warnings и MUST redact raw IDs, message text, credentials and model secrets

#### Scenario: Rollback on failed canary при fail
- **WHEN** any required canary, active/running check, no-restart-loop check или privacy warning check fails
- **THEN** система MUST stop rollout, preserve diagnostics in redacted form and execute or present verified rollback path
