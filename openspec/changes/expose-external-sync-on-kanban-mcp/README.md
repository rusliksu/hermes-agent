# expose-external-sync-on-kanban-mcp

PR #15 доставил dedicated Kanban MCP surface, PR #16 — guarded rollout helper.
Текущий material delta планирует отдельный bootstrap-helper PR: non-Git export
runtime преобразуется в exact immutable Git baseline до обычного `prepare`.

Bootstrap-helper PR ограничен capability, schema v2 и temp-only tests. Его
merge не разрешает live state root/baseline/snapshot, wrapper switch,
prepare, процессы, DB, services, network или smoke; для live действий нужен
отдельно одобренный exact dry-run plan.

Входной экспортный `manifest.txt` — обычный файл без символьных ссылок строго
внутри экспортированной среды, а не `JSON`: он использует непустые строки
`key=value` в `UTF-8`, уникальные непустые ключи и обязательный единственный
`source_commit`, равный явно переданному полному `Git SHA`. Неизвестные ключи
разрешены без вывода или копирования их значений. Точный `SHA-256` сырых байтов
остаётся защитой доверия: пробный запуск печатает наблюдаемый хэш, а `--apply`
требует ожидаемый.
