## ADDED Requirements

### Requirement: Доказательства для закрепленного артефакта Lightpanda
Система SHALL требовать метаданные закрепленного артефакта перед любым пробным запуском установки Lightpanda или разрешением на live-применение.

#### Scenario: Binary pin обязателен
- **WHEN** оператор готовит артефакт Lightpanda для пилота
- **THEN** метаданные артефакта MUST включать exact version или commit, checksum, source URL, SBOM note и license note

#### Scenario: Floating nightly запрещен в live
- **WHEN** запрашивается установка, config или canary gate в live
- **THEN** система MUST отклонить floating nightly или незакрепленные binary references

#### Scenario: Install dry-run без live mutation
- **WHEN** пробный запуск pinned artifact выполняется до approval для разрешения на live-применение
- **THEN** он MUST сообщить planned paths, checksum verification и license/SBOM evidence без изменения live config, перезапуска services или отправки traffic

### Requirement: Синтетический неаутентифицированный same-workload harness
Система SHALL предоставить синтетический harness, который сравнивает Lightpanda и Chrome на одинаковой публичной неаутентифицированной нагрузке без пользовательского traffic.

#### Scenario: Same workload сравнение
- **WHEN** выполняется синтетическое сравнение
- **THEN** Lightpanda и Chrome MUST выполнять одинаковые публичные неаутентифицированные URLs/actions под синтетическими identities и без live traffic владельца, семейных или общих контекстов

#### Scenario: Метрики отчета
- **WHEN** сравнение завершается
- **THEN** отчет MUST включать `success count`, `latency`, `peak RSS`, `fallback count` и `crash count` для каждого engine

#### Scenario: Vendor multiplier не обещается
- **WHEN** отчет суммирует performance
- **THEN** он MUST представлять measured evidence из локального запуска и MUST не обещать или утверждать vendor multiplier как acceptance criterion

### Requirement: Тесты регрессий авторизации и privacy
Система MUST включать focused tests, доказывающие, что Lightpanda не ослабляет полномочия `principal`, privacy профиля или изоляцию состояния браузера.

#### Scenario: Two-principal concurrency изолирована
- **WHEN** два typed principals одновременно запускают browser workloads
- **THEN** их ключи сессий браузера, `HOME`/env профиля, cache paths, snapshots, состояние резервного перехода и cleanup MUST оставаться изолированными

#### Scenario: Guessed ids отклоняются
- **WHEN** model, command или callback payload пытается угадать session id, profile id, browser session id, engine, delivery target, URL authority или capability
- **THEN** система MUST отказать или игнорировать guessed selector до доступа браузера между профилями

#### Scenario: Тесты сбоя и резервного перехода
- **WHEN** Lightpanda crashes, возвращает unusable output, не имеет screenshot/PDF support или не может spawn
- **THEN** тесты MUST проверять резервный переход к Chrome в том же контексте, если он разрешен, и закрытый отказ, если Chrome не разрешен

### Requirement: Отсутствие privacy regressions в acceptance
Система MUST считать privacy regressions блокирующими для approval, readiness реализации и live gates.

#### Scenario: Секреты и приватное содержимое отсутствуют
- **WHEN** тесты или отчеты проверяют env подпроцесса браузера, диагностику и artifacts отчета
- **THEN** они MUST не содержать credentials, raw cookies владельца, приватные тела сообщений, приватное содержимое профиля, raw IDs transport или model secrets

#### Scenario: SSRF/private-page guards сохранены
- **WHEN** workload пытается открыть private address, localhost, metadata IP, file URL или navigation к internal service
- **THEN** SSRF/private-page guard того же контекста MUST отказать до того, как Lightpanda или Chrome достигнут target

#### Scenario: Права семейных и общих контекстов не меняются
- **WHEN** пилот включен для синтетического canary
- **THEN** права `family_standard`, `family_sandbox` или `shared_room` MUST не расширяться за пределы существующей role policy

### Requirement: Разрешения на live-применение и rollback отделены
Система MUST держать live install/config/restart, canary для `owner` и любой canary для семейных/общих контекстов за отдельными explicit gates с documented rollback.

#### Scenario: Live effects отсутствуют во время планирования
- **WHEN** выполняется исходный план OpenSpec или локальная planning validation
- **THEN** система MUST не устанавливать binaries, не менять live config, не перезапускать services, не запускать browser, не отправлять canaries и не мутировать runtime state для production/staging

#### Scenario: Canary только для owner требует позднейшего gate
- **WHEN** синтетический canary пройден и оператор хочет реальный traffic владельца
- **THEN** canary только для `owner` MUST требовать отдельный explicit gate и MUST не включать rollout для семейных/общих контекстов

#### Scenario: Rollout для семейных и общих контекстов требует material delta
- **WHEN** оператор хочет rollout для семейных или общих комнат
- **THEN** это MUST рассматриваться как позднейшая material delta, требующая renewed approval

#### Scenario: План отката задокументирован
- **WHEN** позднейшее разрешение на live-применение approved
- **THEN** план rollback MUST включать отключение Lightpanda opt-in, возврат policy движка браузера к Chrome/auto, остановку затронутых дочерних browser processes и сохранение redacted diagnostics
