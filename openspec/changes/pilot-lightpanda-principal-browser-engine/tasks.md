## 0. Утверждение исходного плана

- [x] 0.1 Зафиксировать approval исходного OpenSpec-плана для `pilot-lightpanda-principal-browser-engine` до начала реализации.
  - 2026-07-30: текущий user `@best-step` явно разрешил planning/preflight docs-only apply packet без реализации кода, зависимостей, binary install, browser execution, fetch/push/live/config/services/secrets/env values/private data; baseline требований не менялся.

## 1. Зависимость и предварительная проверка

- [ ] 1.1 Подтвердить, что `introduce-gurra-principal-role-isolation` merged до начала работ по реализации.
  - 2026-07-30: оставлено open. Evidence: `/home/openclaw/.local/bin/openspec instructions apply --change introduce-gurra-principal-role-isolation --json` показывает progress 12/74 complete, 62 remaining; локальные `main`/`origin/main` refs не содержат commit `bd9d688297fce5a07b317a61e6147b6e00b109f2`; change directory `openspec/changes/introduce-gurra-principal-role-isolation` остается active, а не archived/complete реализацией.
- [x] 1.2 Подтвердить, что ветка реализации основана на HEAD зависимости `bd9d688297fce5a07b317a61e6147b6e00b109f2` или его merged equivalent.
  - 2026-07-30: `git merge-base --is-ancestor bd9d688297fce5a07b317a61e6147b6e00b109f2 HEAD` завершился code 0 на branch `codex/pilot-lightpanda-principal-browser-engine`, HEAD `7663a2630d143b173853f18c0de55525857a6df9`.
- [x] 1.3 Перед реализацией перечитать применимые `AGENTS.md`, текущий код полномочий браузера и specs зависимости.
  - 2026-07-30: прочитан repo `AGENTS.md`; applicable nested AGENTS для touched paths отсутствуют (`rg --files -g 'AGENTS.md'` нашел только root и `apps/desktop/AGENTS.md`, desktop вне scope). Прочитаны `agent/runtime_browser.py`, engine/fallback участки `tools/browser_tool.py`, dependency `openspec/changes/introduce-gurra-principal-role-isolation/{design.md,tasks.md,specs/*.md}` и contextFiles текущего change.
- [x] 1.4 Подтвердить, что не добавляется новый MCP, новый видимый модели browser tool, новая runtime-зависимость или общий long-running MCP/CDP server.
  - 2026-07-30: `git diff --name-status bd9d688297fce5a07b317a61e6147b6e00b109f2...HEAD` показывает только добавление planning files под `openspec/changes/pilot-lightpanda-principal-browser-engine/*`; source/dependency inventory не содержит изменений в `tools/`, `toolsets.py`, `model_tools.py`, `pyproject.toml`, lockfiles, MCP/server code или browser runtime files.
- [ ] 1.5 Определить typed policy contract для выбора `lightpanda`, `chrome` или отказа из серверных полномочий role/profile/backend.
  - 2026-07-30: оставлено open. Доказанный текущий contract: `ResolvedAccessContext` имеет ровно шесть authority-полей `principal_id`, `role_id`, `profile_id`, `conversation_scope`, `capabilities`, `delivery_target`; `AccessRegistry.effective_capabilities()` вычисляет role/scope/backend capability intersection. Текущий browser engine code пока читает typed `browser.engine` из profile-local `config.yaml` через `agent/runtime_browser.py::BrowserRequestAuthority.browser_engine()` и `tools/browser_tool.py::_get_browser_engine()`, а не из явно доказанной typed role/scope/backend engine policy. Точное расположение policy key для engine opt-in остается архитектурным blocker из `design.md`; требования не расширялись и 6-field `ResolvedAccessContext` не менялся.

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
