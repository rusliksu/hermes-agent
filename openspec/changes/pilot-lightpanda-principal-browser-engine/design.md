## Контекст

`tools/browser_tool.py` уже содержит нативную поддержку Lightpanda через `browser.engine`, резервное значение `AGENT_BROWSER_ENGINE` и передачу `--engine lightpanda/chrome` в существующий подпроцесс `agent-browser`. В change-зависимости `introduce-gurra-principal-role-isolation` уже задана typed модель полномочий: `ResolvedAccessContext` с ровно шестью полями полномочий, политикой role/scope/backend, маршрутизацией с закрытым отказом и запретом резервного перехода к `owner`/default.

Этот исходный план не реализует код. Он фиксирует минимальный путь для будущего среза реализации: использовать существующий hook движка браузера, но заменить любой user/model-controlled выбор движка на серверное решение из typed policy профиля и роли. Начальный пилот ограничен синтетическими публичными неаутентифицированными нагрузками и не запускает browser execution на этом этапе планирования.

## Цели / Вне целей

**Цели:**

- Подготовить отдельный исходный план OpenSpec для пилота Lightpanda после merge `introduce-gurra-principal-role-isolation`.
- Сохранить ровно шесть полей полномочий `ResolvedAccessContext` и поведение с закрытым отказом без резервного перехода к `owner`.
- Использовать существующий запуск `agent-browser` и hook `browser.engine` без нового MCP, поверхности инструментов или зависимости.
- Сделать выбор движка серверным: доверенная typed policy для полномочий/профиля/роли выбирает Lightpanda, Chrome или отказ.
- Разрешить резервный переход к Chrome только в том же контексте и только когда backend/role/scope policy разрешает Chrome.
- Сначала доказать безопасность в синтетическом публичном неаутентифицированном режиме и собрать измеренный отчет: `success`, `latency`, `peak RSS`, `fallback count`, `crash count`.
- Зафиксировать supply-chain evidence: binary pin/checksum/SBOM/license note, no floating nightly in live.
- Описать live gates и rollback, не выполняя live/config/service/browser actions.

**Вне целей:**

- Не добавлять новый MCP, новый видимый модели инструмент, общий долгоживущий CDP/MCP server или специфичный для браузера model API.
- Не расширять права `family_standard`, `family_sandbox`, `shared_room` или membership комнаты.
- Не включать canary для владельца, семейных или общих контекстов в этом change без отдельного explicit gate.
- Не поддерживать cookies, учетные данные владельца, logged-in browser, uploads, extensions, clipboard, private addresses, host mounts или пользовательский traffic в начальном пилоте.
- Не обещать vendor multiplier; acceptance опирается только на локальные измерения.

## Решения

### 1. Минимальный путь: существующий hook движка браузера + явное включение политикой

Будущая реализация должна проходить через текущие `agent/runtime_browser.py` и `tools/browser_tool.py`: `BrowserRequestAuthority.browser_engine()`, `_get_browser_engine()`, `_should_inject_engine()` и `_run_browser_command()`. Данные политики могут находиться в typed config профиля/role policy, но model/tool args не должны становиться источником полномочий.

Рассмотренная альтернатива: отдельный MCP/CDP server для Lightpanda. Отклонено, потому что это добавляет поверхность model/runtime, общие полномочия сессии и еще одну границу изоляции. Существующий путь подпроцесса `agent-browser` уже поддерживает `--engine`.

### 2. Сначала полномочия, потом движок

Выбор движка браузера должен происходить после успешного разрешения typed context и до запуска подпроцесса. Missing/malformed context, unknown role, отсутствующая browser capability или небезопасная backend policy приводят к отказу до Lightpanda или Chrome.

Рассмотренная альтернатива: глобальный config `browser.engine: lightpanda` для пилота. Отклонено для multi-principal traffic, потому что process-global config/env может обойти изоляцию role/profile и ограничения резервного перехода к `owner`.

### 3. Резервный переход к Chrome только в том же контексте

Резервный переход - это путь восстановления, а не путь авторизации. Он может использовать Chrome только с тем же шестипольным контекстом, `HOME`/env профиля, scoped fingerprint сессии и SSRF/private-page guards. Если Chrome не разрешен для этой role/scope/backend, операция завершается закрытым отказом.

Рассмотренная альтернатива: всегда выполнять резервный переход к Chrome ради полноты UX. Отклонено, потому что screenshot/PDF/unsupported operations могут незаметно открыть состояние Chrome из `owner`/default или более широкий сетевой доступ.

### 4. Синтетический canary до canary только для владельца

Первые доказательства приходят из синтетических публичных неаутентифицированных нагрузок. Canary только для владельца - это более поздний explicit gate после успеха синтетического этапа и privacy review. Rollout для семейных/общих контекстов находится вне scope, пока позднейшая material delta не будет approved.

Рассмотренная альтернатива: сразу включить для профиля `owner`. Отклонено, потому что supply-chain, subprocess env и поведение резервного перехода должны быть подтверждены evidence до real traffic.

### 5. Supply-chain evidence до install

Использование Lightpanda binary должно быть закреплено pin с checksum, SBOM note и license note. Floating nightly неприемлем для live. Install dry-run может проверить metadata без изменения live config.

Рассмотренная альтернатива: положиться на install defaults `agent-browser`. Отклонено для доверия в live/staging, потому что этот пилот security-sensitive, а поведенческая зависимость может измениться.

## Риски / компромиссы

- Lightpanda может не поддерживать screenshot/PDF или может возвращать incomplete snapshots -> Chrome остается необходимым для поддержанного резервного перехода в том же контексте; иначе отказ.
- Сбой Lightpanda или spawn failure может добавить latency -> синтетический отчет должен включать счетчики резервного перехода/crash, а не только success timing.
- Supply-chain pinning может замедлить upgrades -> осознанный компромисс ради предсказуемых live artifacts.
- Loopback ports или daemon session names могут случайно стать общими полномочиями -> нужны task/profile-scoped identifiers и отсутствие общих MCP session ids.
- Усиление scoped env может сломать local browser discovery -> install dry-run и синтетический harness должны сообщать точный missing binary/path без добавления ambient credentials.
- Benchmarks могут выглядеть хуже vendor claims -> acceptance использует measured evidence, а не external multiplier.

## План миграции

1. Только исходный план: создать и проверить OpenSpec artifacts, без реализации.
2. После explicit approval исходного плана и merge зависимости реализовать focused tests для полномочий, резервного перехода и privacy.
3. Добавить pinned artifact/install dry-run logic и evidence capture без live mutation.
4. Добавить синтетический публичный неаутентифицированный harness и генерацию отчета.
5. Запустить focused suites, full relevant suites, `openspec validate --strict` и privacy review.
6. Только после отдельного explicit gate на live-применение рассмотреть install/config/restart и canary только для владельца.
7. Rollback для любого позднейшего разрешения на live-применение: отключить Lightpanda policy opt-in, вернуть engine policy к Chrome/auto, остановить затронутые дочерние browser processes, сохранить redacted diagnostics и не выполнять rollout для семейных/общих контекстов без одобрения material delta.

## Открытые вопросы

- Точный source и pinning format для Lightpanda artifact: release version, commit SHA, checksum source, SBOM и license note.
- Точный список синтетических workloads и output schema измерительного harness.
- Точное расположение policy key для engine opt-in внутри typed profile/role/backend policy. Preflight finding 2026-07-30: текущий код доказывает только чтение profile-local `browser.engine` через `agent/runtime_browser.py::BrowserRequestAuthority.browser_engine()` и `tools/browser_tool.py::_get_browser_engine()` после разрешения шестипольного `ResolvedAccessContext`; он еще не доказывает расположение typed role/scope/backend engine policy, поэтому реализация MUST NOT угадывать этот key или расширять `ResolvedAccessContext`.
- Должен ли резервный переход к Chrome быть разрешен для синтетического canary владельца по умолчанию или требовать отдельный capability flag.
