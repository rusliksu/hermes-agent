## ADDED Requirements

### Requirement: Gate зависимости для изоляции principal
Система MUST считать пилот Lightpanda заблокированным до слияния `introduce-gurra-principal-role-isolation` и MUST сохранять его контракт изоляции `principal` без расширения.

#### Scenario: HEAD зависимости является обязательной базой
- **WHEN** начинается реализация пилота
- **THEN** рабочая ветка MUST быть основана на change-зависимости, содержащем commit `bd9d688297fce5a07b317a61e6147b6e00b109f2`, и MUST не обходить маршрутизацию с закрытым отказом

#### Scenario: Ровно шесть полей полномочий сохранены
- **WHEN** полномочия браузера создаются для типизированного запроса
- **THEN** система MUST использовать только шесть полей полномочий `ResolvedAccessContext`: `principal_id`, `role_id`, `profile_id`, `conversation_scope`, `capabilities`, `delivery_target`

#### Scenario: Резервный переход к owner запрещен
- **WHEN** запрос браузера имеет отсутствующий, неверно сформированный, неизвестный или несовпадающий typed context
- **THEN** система MUST отказать до запуска браузера, поиска сессии, cookies, состояния профиля или резервного перехода и MUST не использовать профиль `owner`/default как запасной источник

### Requirement: Движок выбирается серверной политикой
Система SHALL выбирать `lightpanda`, `chrome` или отказ только на сервере из доверенных typed полномочий, профиля и политики `role`/`scope`/`backend`.

#### Scenario: Аргументы модели не выбирают движок
- **WHEN** аргументы модели, аргументы инструмента, аргументы команды или payload callback содержат `engine`, `browser_engine`, `profile`, `session`, `role`, `scope` или похожий selector полномочий
- **THEN** система MUST игнорировать этот selector для полномочий и MUST использовать только серверную политику текущего `ResolvedAccessContext`

#### Scenario: Политика роли выбирает Lightpanda
- **WHEN** текущий typed context имеет управляемое политикой явное включение Lightpanda для синтетической публичной нагрузки браузера
- **THEN** подпроцесс браузера может получить `--engine lightpanda` через существующий путь `agent-browser` без нового инструмента, видимого модели

#### Scenario: Политика роли запрещает браузер
- **WHEN** текущая политика `role`/`scope`/`backend` не разрешает браузер или публичное выполнение браузера для web
- **THEN** система MUST отказать до запуска `agent-browser` и MUST не пытаться выполнить Chrome или Lightpanda

### Requirement: Новая поверхность инструментов и общий долгоживущий server запрещены
Система MUST использовать существующие инструменты браузера и модель запуска `agent-browser`, не добавляя новый MCP, новую поверхность инструментов или общие полномочия долгоживущей CDP/MCP-сессии.

#### Scenario: Существующая поверхность browser tool сохраняется
- **WHEN** пилот добавляет поведение Lightpanda
- **THEN** имена и схемы `browser tools`, видимые модели, MUST остаться совместимыми с существующим `browser toolset`

#### Scenario: Общие MCP session ids запрещены
- **WHEN** движку браузера требуется runtime-сессия или IPC identity
- **THEN** identity MUST быть scoped к контексту текущих `task`/`session`/`profile` и MUST не становиться общим MCP session id между principals

#### Scenario: Порт только loopback при технической необходимости
- **WHEN** Lightpanda или `agent-browser` технически требует порт
- **THEN** bind MUST быть только loopback и MUST не открывать внешний, общий или cross-profile endpoint

### Requirement: Синтетическая публичная неаутентифицированная граница
Система MUST ограничить начальный пилот Lightpanda синтетическими публичными неаутентифицированными нагрузками и MUST отказывать приватным, аутентифицированным функциям и функциям состояния браузера до отдельного явного разрешения.

#### Scenario: Cookies и учетные данные владельца запрещены
- **WHEN** подпроцесс пилота Lightpanda запускается для non-live синтетической нагрузки
- **THEN** `env`, `HOME`, `user data dir` и состояние браузера MUST исключать cookies владельца, учетные данные владельца, logged-in состояние браузера, provider secrets и состояние чужого профиля

#### Scenario: Приватные и host-функции запрещены
- **WHEN** нагрузка пилота запрашивает upload, extension, clipboard, private address, localhost, metadata IP, host mount или пользовательский трафик
- **THEN** система MUST отказать до операции браузера или сохранить отказ существующего SSRF/private-page guard

#### Scenario: Membership комнаты не повышает права
- **WHEN** участник пишет в общей комнате, где политика комнаты разрешает синтетический browser pilot
- **THEN** membership комнаты MUST не выдавать приватные права браузера для DM, учетные данные владельца, семейные права или состояние браузера между комнатами

### Requirement: Резервный переход к Chrome остается закрытым отказом
Система SHALL разрешать резервный переход Lightpanda -> Chrome только внутри того же `ResolvedAccessContext` и только когда Chrome явно разрешен политикой backend, role и scope.

#### Scenario: Резервный переход разрешен тем же контекстом
- **WHEN** операция Lightpanda завершается сбоем или не поддерживается, а текущая политика разрешает Chrome для тех же `role`/`scope`/`backend`
- **THEN** резервный переход MUST выполняться с тем же шестипольным `ResolvedAccessContext`, `HOME`/env профиля, fingerprint сессии и SSRF/private-page guards

#### Scenario: Резервный переход запрещен политикой
- **WHEN** операция Lightpanda завершается сбоем, но Chrome не разрешен для того же typed context
- **THEN** система MUST вернуть deny/failure без запуска Chrome и MUST не использовать состояние Chrome из `owner`/default

#### Scenario: Операции screenshot/PDF без поддержки остаются за Chrome
- **WHEN** операция требует screenshot, PDF или другой неподдерживаемой Lightpanda capability
- **THEN** система SHALL использовать Chrome только если Chrome разрешен в том же контексте; иначе она MUST отказать без регресса privacy

### Requirement: Усиление защиты scoped subprocess для Lightpanda
Система MUST применять отключение Lightpanda telemetry/core dump и усиление browser env только к окружению scoped дочернего процесса, а не к глобальному состоянию процесса.

#### Scenario: Telemetry и core dumps ограничены scope
- **WHEN** создается env подпроцесса Lightpanda
- **THEN** отключение telemetry и core dump MUST быть задано только в env этого подпроцесса и MUST не менять глобальное окружение процесса оператора

#### Scenario: Typed env сохраняет profile boundary
- **WHEN** typed полномочия браузера строят env подпроцесса
- **THEN** `HERMES_HOME`, `HOME`, пути cache/config/data/tmp и key сессии MUST оставаться внутри fingerprint текущего profile/scope

#### Scenario: Ambient полномочия браузера удаляются
- **WHEN** запускается typed подпроцесс браузера
- **THEN** ambient `AGENT_BROWSER_*`, CDP, cloud credentials браузера, env владельца и настройки чужого профиля MUST быть удалены, если текущая серверная политика явно не добавляет scoped value заново
