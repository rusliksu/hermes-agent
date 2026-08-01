## Material delta: элементарная арифметика без инструментов

Статус: одобрено пользователем 1 августа 2026 года сообщением `Одобряю`.

### Причина

Live fixture подтвердил, что каноническая Gurra web-policy присутствует в
provider request, но более поздняя общая инструкция Hermes требует инструмент
для **любой** арифметики. При доступном Telegram web/browser surface Luna/high
вызвала `browser_navigate` и `browser_console` для `17 + 25`, поэтому rollout
gate корректно остановился до config/service mutation.

Диагностическая замена только конфликтующей строки дала прямой правильный
ответ без инструментов примерно за 3 секунды; reasoning fixture также прошёл
без инструментов примерно за 5 секунд. Обязательный current-web fixture при
отключённом более широком OpenAI guidance по-прежнему использовал официальный
источник, поэтому узкое исключение не закрывает web surface.

### Предлагаемое изменение

- В `OPENAI_MODEL_EXECUTION_GUIDANCE` заменить безусловное требование
  инструмента для любой арифметики на требование инструмента для нетривиальных
  вычислений и явное разрешение отвечать на элементарную арифметику напрямую,
  когда пользователь просит короткий ответ.
- Добавить system-prompt regression test точного контракта.
- Не отключать `tool_use_enforcement`, не менять tool routing и не добавлять
  Gurra-specific hard-code в gateway.
- После реализации повторить credential-safe Sol/high vs Luna/high benchmark;
  прежние диагностические результаты не являются rollout evidence.

### Основание одобрения

Строка является общей для GPT/Codex/Grok в Hermes, поэтому observable behavior
меняется не только в Gurra. Пользователь явно одобрил это материальное
расширение после получения live evidence; архитектура и tool routing не
меняются.

### Гейты

- Не ослаблять evaluator и не отключать общий `tool_use_enforcement`.
- При любом Luna internet call на stable fixtures или ускорении менее 25%
  default/config/service не переключать.
