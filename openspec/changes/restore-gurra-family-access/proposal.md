## Why

После single-principal hardening Gurra должен оставаться fail-closed для
неизвестных Telegram principals, но ordinary family DM-доступ не должен
зависеть от owner-only прав. Семейные пользователи должны иметь возможность
писать Gurra в личный чат как обычные разрешённые пользователи, при этом
административные, pairing, elevated и approval-действия остаются доступны
только владельцу.

## What Changes

- Ввести явный список `telegram_allowed_user_ids` для ordinary family DM-доступа
  без включения реальных ID или значений в репозиторий.
- Сохранить owner-only gate для admin, pairing, elevated actions и approvals.
- Не менять две существующие shared Telegram group scopes и их group/topic
  semantics.
- Unknown, missing или неполный Telegram principal должен fail-closed до
  session lookup, memory access, tools и model turn.
- Диагностика должна быть redacted: без raw Telegram ID, message text, secrets
  или private config values.
- Per-user DM isolation должна сохраняться: семейные пользователи не видят
  память, transcript, approvals или elevated state друг друга и владельца.
- Live patch, private config update, symlink switch, restart, deploy и push
  требуют отдельного explicit live gate после локальных доказательств.

## Capabilities

### New Capabilities

- `gurra-family-telegram-access`: ordinary Telegram DM-доступ для явно
  разрешённых семейных пользователей с owner-only административными действиями,
  fail-closed policy и per-user isolation.

### Modified Capabilities

Нет: change фиксирует отдельный Gurra access policy rollout и не расширяет
существующие shared group scopes.

## Impact

- Policy/config contract: explicit `telegram_allowed_user_ids` для ordinary DM
  access; реальные значения живут только в private config вне репозитория.
- Gateway/session/memory behavior: DM user principal остаётся частью scope для
  per-user isolation; shared group scopes не меняются.
- Tests: privacy isolation suite и group/policy affected suite из утверждённого
  checklist.
- Diagnostics: redacted status/log output без raw identities и private values.
- Delivery: no push, no deploy, no restart, no active checkout/symlink/private
  config/systemd/service changes in this change; live activation только после
  отдельного разрешения.

## Approval

- 2026-07-21: Руслан задал утверждённый scope для repo-local OpenSpec change,
  локальных тестов и additive commit только OpenSpec artifacts. Live activation,
  private config patch и service restart оставлены за отдельным gate.
