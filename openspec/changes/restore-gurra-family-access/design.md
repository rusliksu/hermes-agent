## Context

Gurra использует Telegram gateway поверх Hermes single-principal policy.
Ужесточение principal checks должно защищать memory/session isolation и
administrative actions, но ordinary family DM является отдельным, более узким
правом: разрешённый человек может вести свой личный диалог с Gurra без доступа
к owner-only операциям.

Две shared Telegram groups уже являются отдельными group scopes. Этот change не
меняет их allowlist, thread semantics, passive context, session key shape или
capability profile.

## Goals / Non-Goals

**Goals:**

- добавить явный policy contract для `telegram_allowed_user_ids` как ordinary
  DM allowlist;
- сохранить owner-only admin, pairing, elevated actions и approvals;
- сохранить fail-closed behavior для unknown, missing и неполных principals;
- подтвердить per-user memory/session isolation targeted-тестами;
- зафиксировать redacted diagnostics и отдельный live gate.

**Non-Goals:**

- добавлять реальные Telegram ID, tokens, private config values или message
  contents в репозиторий;
- менять две shared groups, group/topic routing или passive group context;
- выдавать семейным пользователям admin/elevated/pairing/approval rights;
- выполнять live patch, symlink switch, service restart, deploy или push;
- читать private data, active checkout или systemd/service state.

## Decisions

### 1. Явный ordinary DM allowlist

Ordinary family DM access задаётся только через explicit
`telegram_allowed_user_ids`. Отсутствующий, пустой или нераспознанный principal
не получает fallback-доступ через owner identity, group grants, display name,
username или legacy heuristics.

Реальные значения не документируются и не коммитятся. OpenSpec описывает только
contract и required behavior.

### 2. Owner-only elevated surface остаётся отдельной

Admin commands, pairing setup, elevated actions и approvals проверяются через
owner-only gate даже если Telegram user есть в ordinary DM allowlist. Это
разделяет право написать Gurra в личный чат и право управлять системой.

### 3. Shared groups не участвуют в family DM rollout

Существующие две shared groups остаются неизменными. Их allowlist, topic
isolation, shared session scope и passive behavior не расширяются
`telegram_allowed_user_ids`, потому что ordinary family DM и shared room access
являются разными principal scopes.

### 4. Fail-closed до stateful side effects

Unknown, missing, anonymous, bot-authored или неполный Telegram principal
отклоняется до session lookup, transcript write, memory access, tools, model
turn, callback и visible response. Ошибка диагностики должна показывать только
категорию отказа.

### 5. Per-user isolation является delivery gate

Каждый ordinary DM user получает собственный isolated user/session/memory scope.
Тесты должны покрывать memory tool isolation, session search isolation,
Hermes state isolation, gateway session behavior и OpenAI client kwargs
isolation. Family access не должен менять эти файлы и контракты без отдельного
material delta.

## Risks / Trade-offs

- [Смешение ordinary и elevated прав] → owner-only checks остаются отдельным
  requirement и проверяются targeted policy tests.
- [Случайный доступ неизвестного Telegram user] → missing/unknown principals
  fail-closed без fallback по имени или group grants.
- [Утечка private identity в logs/status] → diagnostics redacted и не содержат
  raw Telegram ID, message text или private values.
- [Live drift между repo и private config] → live patch/config/restart
  остаются pending до отдельного explicit gate и rollback-ready проверки.

## Migration Plan

1. Зафиксировать OpenSpec contract и локальные delivery gates.
2. Проверить exact privacy isolation suite и group/policy affected suite из
   checklist через venv Python без cache/bytecode, private data и live state.
3. Убедиться, что family changes не модифицируют isolation files.
4. Закоммитить только OpenSpec artifacts отдельным additive commit.
5. После отдельного explicit live gate подготовить private config patch,
   backup/rollback и restart только утверждённого Gurra/Hermes target.

## Open Questions

Нет блокирующих вопросов в repo-local scope. Реальные ID, private config patch,
active checkout update и service restart остаются live-only материалом за
отдельным gate.
