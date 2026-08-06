# Активный workflow Gurra: Spec Kitty + Beads

Для новых изменений единственным активным процессом является связка Spec
Kitty + Beads.

Spec Kitty — источник требований и состояния mission: spec.md, plan.md,
tasks.md, work packages и event log. Beads — источник issue identity,
приоритета и зависимостей. В каждой mission указывается связанный Beads ID;
в текущей mission это HERMES-4t0.

Правила:

1. Сначала зафиксировать baseline и bounded scope в mission и Beads.
2. Реализацию вести только в task-owned worktree и task-ветке.
3. Тесты и review относятся к той же mission; статус обновлять через Spec
   Kitty, а зависимости — через Beads.
4. Исторические артефакты прежнего процесса только читаются из архива; новые
   требования и задачи туда не добавляются.
5. Live config, restart, rollout и Telegram canary требуют отдельного
   явного approval и не следуют автоматически из завершения mission.
