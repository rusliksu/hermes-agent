## Зачем

В Hermes уже есть нативный выбор `browser.engine` в существующем пути `agent-browser`, но пилот Lightpanda должен быть привязан к новой модели изоляции `principal`/роли. Иначе ускоренный браузер может стать обходом профиля, cookies, поверхности инструментов или полномочий доставки. Нужен отдельный исходный план, который стартует после слияния `introduce-gurra-principal-role-isolation` и сначала доказывает безопасность на синтетических публичных неаутентифицированных нагрузках.

## Что меняется

- Ввести пилотный путь для Lightpanda как движка браузера, управляемого серверной политикой через существующий hook `agent-browser`/`browser.engine`.
- Не добавлять новый MCP, новую видимую модели поверхность инструментов, новый общий долгоживущий MCP/CDP server или новую runtime-зависимость.
- Зафиксировать gate зависимости: `introduce-gurra-principal-role-isolation` должен быть слит первым; пилот обязан сохранить ровно 6 полей полномочий `ResolvedAccessContext` и маршрутизацию с закрытым отказом без резервного перехода к `owner`/default.
- Выбирать движок только на сервере из доверенной typed policy для полномочий, профиля и роли; model args, tool args, command args и callback payload не могут выбирать движок, профиль, сессию или состояние браузера.
- Разрешить резервный переход Lightpanda -> Chrome только внутри того же `ResolvedAccessContext` и только если backend, роль и область явно разрешают Chrome; иначе резервный переход завершается отказом.
- Ограничить начальный пилот только синтетическим публичным неаутентифицированным режимом: без cookies, учетных данных владельца, logged-in browser, uploads, extensions, clipboard, private addresses, host mounts или пользовательского traffic.
- Использовать существующий запуск `agent-browser` для каждой task/session; если технически нужен порт, он должен быть только loopback-only, без общих MCP session ids.
- Требовать scoped env подпроцесса: отключать Lightpanda telemetry/core dumps только для child process, сохранять typed fingerprint профиля/env/home/session и SSRF/private-page guards при резервном переходе.
- Требовать binary pin/checksum/SBOM/license note; не использовать floating nightly в live.
- Сохранить Chrome для screenshot/PDF/unsupported operations.
- Зафиксировать критерии приемки по тестам auth/privacy, two-principal concurrency, guessed ids, тестам резервного перехода/отказа и доказательствам в синтетическом отчете: `success`, `latency`, `peak RSS`, `fallback count`, `crash count`. Vendor multiplier не обещается.
- Live install/config/restart, canary владельца и rollout для семейных/общих контекстов остаются отдельными explicit gates с rollback. Никакие права `family` или `shared` этот исходный план не расширяет.

## Возможности

### Новые возможности

- `principal-browser-engine-policy`: серверный выбор Lightpanda/Chrome через typed `principal` authority, закрытый отказ при резервном переходе и сохранение privacy/profile isolation.
- `lightpanda-synthetic-browser-canary`: dry-run закрепленного артефакта, синтетический неаутентифицированный harness, доказательства производительности и отдельные разрешения на live-применение/rollback.

### Измененные возможности

- Нет. Этот исходный план не меняет текущие archived specs; он зависит от active change `introduce-gurra-principal-role-isolation` и не архивирует его дельты.

## Влияние

- Затронутые planning surfaces: `openspec/changes/pilot-lightpanda-principal-browser-engine/*`.
- Ожидаемые поверхности реализации после одобрения: существующие полномочия браузера и код запуска вокруг `agent/runtime_browser.py`, `tools/browser_tool.py`, политика ролей/toolset в gateway, тесты для gateway/полномочий браузера и инструменты для синтетического benchmark/отчета.
- На этом этапе исходного плана не выполняется реализация, commit, push, live config, service restart, browser execution, install или canary.
