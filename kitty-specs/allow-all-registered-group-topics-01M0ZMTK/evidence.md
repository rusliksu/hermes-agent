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

### Runtime correction after real canary

Первый post-fix message снова показал `missing_shared_scope_binding`. Redacted
transport comparison доказал, что `principal-room-drafts` содержал stale
`chat_id`: он не совпадал ни с одним observed batch нужного чата. Group-level
thread inheritance было корректным, но применялось к другому transport key.

- `room_identity.chat_id` и `delivery_target.chat_id` заменены на transport key
  фактического чата: ещё две строки active runtime config.
- Owner и Юля уже входили в server-owned membership; роли, capabilities,
  profile/scope и participant list не менялись.
- SHA256 config:
  `2a17aa68ee06183690612046ed352df77b7638341314b758c563db88a9efaa9e` →
  `1edf175a8f1edccfaf8e3e8932f850c52fe71ebf46174a8e1dba7746f2b305a0`.
- Rollback metadata:
  `/home/openclaw/.hermes/backups/allow-all-registered-group-topics-20260826/chat-rollback.json`.
- Post-correction resolver на фактических chat/topic keys: `allowed`, profile
  `room-drafts`, topic delivery preserved, registry `pass`.

## Real Telegram canary

После correction и restart в ранее отклонявшемся топике получено новое
сообщение. Runtime подтвердил один ingress batch, один completed model response
и одну Telegram delivery в тот же target. После restart отсутствуют
`missing_shared_scope_binding`, `resolved_access_context_mismatch` и agent
errors. Screenshot пользователя показывает ответ Gurra в топике.

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
