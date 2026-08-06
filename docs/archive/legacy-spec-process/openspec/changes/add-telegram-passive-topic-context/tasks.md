## 1. Зафиксировать flow и boundaries

- [x] 1.1 Проследить Telegram auth, invocation, observed transcript, shared session key и replay flow end-to-end
- [x] 1.2 Применить ponytail full и выбрать reuse существующего observed path без нового storage/dependencies
- [x] 1.3 Записать approval, proposal, spec и design material delta на русском

## 2. Реализовать authoritative passive observation

- [x] 2.1 Разрешить text-only observation только для exact single-principal shared scope и explicit flag
- [x] 2.2 Сохранить sender для triggered auth, но использовать sender-less source для passive shared transcript
- [x] 2.3 Убрать raw identities из single-principal attribution и observation success logs
- [x] 2.4 Ограничить observed replay шестью часами, 50 сообщениями и 20 000 символами

## 3. Доказать security и isolation

- [x] 3.1 Проверить no-dispatch/no-model/no-tool/no-response для passive text
- [x] 3.2 Проверить allowlisted/unknown/bot/anonymous/media deny matrix
- [x] 3.3 Проверить group root, General, два topics, две groups и owner DM isolation
- [x] 3.4 Проверить count/char/age limits, chronological order и restart transcript replay

## 4. Выполнить delivery gates

- [x] 4.1 Прогнать Ruff, targeted Telegram/auth/session suites и broad relevant regressions
- [x] 4.2 Провести thermo-nuclear security review и устранить blockers
- [x] 4.3 Выполнить `openspec validate add-telegram-passive-topic-context --strict`
- [ ] 4.4 Commit, push, PR, CI/review/merge и зафиксировать immutable merge ref

## 5. Поддержать live activation

- [ ] 5.1 Передать Gurra rollout packet без identities, message contents или credentials
- [ ] 5.2 После одобренного activation gate проверить active checkout tests и rollback readiness
