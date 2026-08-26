# План реализации

1. Воспроизвести отказ на full ingress boundary для зарегистрированной группы с неизвестным `thread_id` и зафиксировать RED.
2. Проследить deployed registry shape. Если корневой binding отсутствует, добавить/мигрировать явный group-level default; не выводить authority из sibling topic binding при неоднозначности.
3. Сохранить порядок разрешения: exact topic binding → root group binding → fail closed.
4. Добавить regression matrix для нового топика, exact override, topic namespace isolation, unknown group, non-member и disabled binding.
5. Обновить каноническую product spec текущего shared-room поведения.
6. Выполнить focused/affected tests, независимый review, isolated HOSTKEY candidate, restart и redacted Telegram/runtime canary с rollback на предыдущий candidate.

## Риск и решение

Главный риск — случайно считать зарегистрированной любую группу, где найден один topic binding, и тем самым распространить профиль/права неоднозначно. Поэтому fallback разрешён только через явный server-owned group-level binding; точные topic bindings остаются более приоритетными.
