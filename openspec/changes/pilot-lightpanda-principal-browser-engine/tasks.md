## 0. Утверждение исходного плана

- [ ] 0.1 Зафиксировать approval исходного OpenSpec-плана для `pilot-lightpanda-principal-browser-engine` до начала реализации.

## 1. Зависимость и предварительная проверка

- [ ] 1.1 Подтвердить, что `introduce-gurra-principal-role-isolation` merged до начала работ по реализации.
- [ ] 1.2 Подтвердить, что ветка реализации основана на HEAD зависимости `bd9d688297fce5a07b317a61e6147b6e00b109f2` или его merged equivalent.
- [ ] 1.3 Перед реализацией перечитать применимые `AGENTS.md`, текущий код полномочий браузера и specs зависимости.
- [ ] 1.4 Подтвердить, что не добавляется новый MCP, новый видимый модели browser tool, новая runtime-зависимость или общий long-running MCP/CDP server.
- [ ] 1.5 Определить typed policy contract для выбора `lightpanda`, `chrome` или отказа из серверных полномочий role/profile/backend.

## 2. Тесты и полномочия

- [ ] 2.1 Добавить tests, что `ResolvedAccessContext` сохраняет ровно шесть полей полномочий во всех полномочиях браузера и путях резервного перехода.
- [ ] 2.2 Добавить tests, что model/tool/command/callback args не могут выбирать engine, profile, session, scope, role, capability или delivery target.
- [ ] 2.3 Добавить тесты two-principal concurrency для изоляции key browser-сессии, profile HOME/env, cache, snapshot, резервного перехода и cleanup.
- [ ] 2.4 Добавить guessed-id tests для ids browser-сессий, profile ids, delivery targets, URL authority, capability names и engine names.
- [ ] 2.5 Добавить fail-closed tests для missing, malformed, unknown и mismatched typed contexts без резервного перехода к `owner`/default.
- [ ] 2.6 Добавить tests, что room membership никогда не повышает приватные права браузера и не дает cross-room/browser state.
- [ ] 2.7 Добавить tests, что cookies, owner credentials, logged-in browser state, uploads, extensions, clipboard, private addresses и host mounts запрещены в initial pilot.

## 3. Закрепленный artifact и dry-run установки

- [ ] 3.1 Выбрать exact pin артефакта Lightpanda с version или commit, checksum, source URL, SBOM note и license note.
- [ ] 3.2 Добавить validation, что floating nightly или missing checksum отклоняются до live install/config gates.
- [ ] 3.3 Добавить install dry-run, который сообщает planned path binary, checksum verification и license/SBOM evidence без live mutation.
- [ ] 3.4 Добавить scoped subprocess env hardening для Lightpanda telemetry/core dumps без изменения process-global env.
- [ ] 3.5 Проверить, что любой требуемый port bind выполняется только на loopback и общие MCP session ids не создаются.

## 4. Синтетический harness и отчет

- [ ] 4.1 Определить список синтетических публичных неаутентифицированных workloads без cookies, private addresses, uploads, clipboard, extensions или user traffic.
- [ ] 4.2 Реализовать same-workload comparison для Lightpanda и Chrome под синтетическими identities.
- [ ] 4.3 Собирать `success count`, `latency`, `peak RSS`, `fallback count` и `crash count` по каждому engine.
- [ ] 4.4 Включить cases резервного перехода/отказа для unsupported screenshot/PDF operations и Lightpanda unusable output.
- [ ] 4.5 Убедиться, что отчет редактирует credentials, raw transport IDs, private message bodies, private profile content и model secrets.
- [ ] 4.6 Указывать только measured performance evidence; не кодировать vendor multiplier как acceptance requirement.

## 5. Review, full suites и OpenSpec

- [ ] 5.1 Запустить focused auth/privacy/полномочия браузера tests через `scripts/run_tests.sh`.
- [ ] 5.2 Запустить полные relevant suites для gateway, browser tools, URL safety, terminal/file authority, cron/background/резервного перехода и profile isolation.
- [ ] 5.3 Провести privacy/security review для prompt caching, role alternation, tool footprint, subprocess env, SSRF и резервного перехода с закрытым отказом.
- [ ] 5.4 Запустить `git diff --check`.
- [ ] 5.5 Запустить `/home/openclaw/.local/bin/openspec validate pilot-lightpanda-principal-browser-engine --strict --no-interactive`.

## 6. Отдельные разрешения на live-применение и rollback

- [ ] 6.1 Подготовить отдельный explicit gate для live install/config/restart только после прохождения реализации, dry-run, отчета и review.
- [ ] 6.2 Подготовить отдельный explicit gate для canary только для `owner` после успеха синтетического canary.
- [ ] 6.3 Держать rollout для семейных/общих контекстов вне этого change, пока позднейшая material delta не approved.
- [ ] 6.4 Документировать rollback: disable Lightpanda opt-in, restore Chrome/auto policy, stop affected дочерние browser processes и preserve redacted diagnostics.
- [ ] 6.5 Подтвердить, что live config, service restart, browser execution, canary, commit или push не входят в planning-only исходный план.
