# Evidence

## Причина

Active policy содержала два shared bindings: `principal-room-drafts` был
привязан к одному точному `thread_id`, а group-level binding существовал только
для другой Telegram-группы. Поэтому новый топик первой группы доходил до
`_select_shared_scope_binding`, но не находил ни exact, ни parent binding.

## Изменение

- В `principal-room-drafts` удалён `thread_id` только из `room_identity` и
  `delivery_target`: две строки active runtime config.
- Source code не менялся: parent fallback уже реализован и покрыт тестами.
- До/после SHA256 runtime config:
  `cfb1e4f5a0a971163b3e1aef4283a6acc6054c20578b7db686864a8372f8e5de` →
  `2a17aa68ee06183690612046ed352df77b7638341314b758c563db88a9efaa9e`.
- Credential-free rollback metadata:
  `/home/openclaw/.hermes/backups/allow-all-registered-group-topics-20260826/rollback.json`.

## Проверка

- In-memory RED/GREEN на active policy: `missing_shared_scope_binding` →
  `allowed`, registry `pass`, `room-drafts`, исходящий topic сохранён.
- Full-boundary regression uses a group-level binding without `thread_id`, an
  ingress event with a novel `thread_id`, and asserts same-topic delivery,
  topic-scoped session key and memory namespace.
- `tests/gateway/test_shared_topic_full_boundary.py` plus
  `tests/gateway/test_access_registry.py`: `88 passed`.
- Два synthetic новых топика разрешены и имеют разные memory namespaces;
  synthetic unknown group остаётся `missing_shared_scope_binding`.
- `check-hermes-single-principal`: verdict `pass`.
- `hermes-gateway.service`: active, PID `1783968`, `NRestarts=0`, result success.
- Product spec: Gurra workspace commit `63ce134`.
- Независимый read-only review: первоначально отклонён из-за отсутствия
  parent-inheritance full-boundary oracle; после добавления теста повторный
  verdict `APPROVE`, blocking findings отсутствуют.
