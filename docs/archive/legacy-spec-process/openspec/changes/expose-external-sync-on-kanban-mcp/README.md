# expose-external-sync-on-kanban-mcp

PR #15 доставил dedicated Kanban MCP surface, PR #16 — guarded rollout
helper, а исторический bootstrap-helper — schema v2 baseline из non-Git
export runtime. Этот старый bootstrap/rollout contract теперь superseded:
fresh `bootstrap` и `rollout` создают только `schema_version=3`,
`snapshot_kind=bootstrap|rollout` и
`wrapper_contract=source-cwd-nofile-v2`; fresh bootstrap `wrapper.after`
строит единственный canonical generator.

Любой `schema_version!=3`, а также исторический schema-v3
`source-cwd-v1`, разрешён только отдельному snapshot-only rollback loader
для восстановления exact bytes/mode. Он запрещён для `switch` до preflight
и любой mutation; in-place migration snapshot не существует.

Новый independent thermo review sealed-content реализации завершился
verdict `BLOCK`. Два author exact four-suite run по
`140 passed, 0 failed` сохранены только как historical evidence.

Последующий independent review дельты 26.x снова завершился `BLOCK`.
Исторические implementation evidence 26.3–26.5 и sibling run
`128 passed` не приняты как текущая acceptance. Remediation baseline 27.x
явно одобрен и реализован repo-local; один exact five-module author run
завершился `180 passed`, но 27.5+ и independent acceptance остаются открыты.

Новый MATERIAL REMEDIATION DELTA требует двухфазный bounded inventory →
sealed acquisition, exact GNU/Linux ELF closure с раздельными
`DT_RPATH`/`DT_RUNPATH`, deterministic resource planning относительно
`RLIMIT_NOFILE`, `SC_ARG_MAX` и отдельного cap `bwrap --args`, а также
independent literal handcrafted ELF oracles и low-limit/mutation/cleanup
tests.

Текущий material planning baseline требует:

- материализовать каждый исполняемый или импортируемый regular file
  candidate source tree, exact interpreter, необходимого trusted
  stdlib/runtime closure и bytes `bwrap` в sealed immutable memfd/data
  binding;
- строить anchors и digests из тех же captured bytes и передавать `bwrap`
  только sealed bundle плюс созданную из manifest directory/symlink
  topology, не bind mutable backing directory;
- строить manifest descriptor-relative с `O_NOFOLLOW`, fail-closed при
  неполном или изменившемся capture и обещать только exact captured verified
  bytes от anchor construction до `exec`/import, без недостижимой гарантии до
  capture;
- сделать FD ownership exception-safe: немедленная регистрация каждого
  `open`/`memfd`, закрытие current FD при ошибке `_data_fd`, полный cleanup
  partial bundle и отдельная structured fail-closed ошибка при cleanup
  failure;
- вынести общий Git/layout/oracle harness в существующий
  `tests/scripts/hermes_kanban_mcp_test_support.py` как содержательного owner:
  rollout test `<=850` строк, support `<400`, каждый source/test `<1000`,
  behavior unchanged;
- добавить adversarial nested in-place mutation tests для candidate import,
  stdlib regular file и, где практично, interpreter/`bwrap` bytes: исполняются
  только sealed original bytes либо операция завершается fail-closed, а host
  side effect отсутствует.

Approval material baseline 21.x и его author runs остаются historical
evidence. Exact approval раздела 22.x получено от Руслана 2026-07-30
формулировкой «одобряю material ELF/resource remediation baseline» только
для implementation 22.3–22.6 и repo-local/temp-only author verification.
Tasks 19.4, 19.6, 19.7, 20.2, 21.2, 21.5–21.8 и delivery/live остаются
открытыми до author/independent gates.

Новый independent review после baseline вернул historical `BLOCK` по трём
несоответствиям существующим requirements: post-symlink trusted-root
containment, probe actual exec budget и placeholder pre-acquisition plan.
Minor remediation 23.x/25 выполняется в рамках approval 2026-07-30:
единственный canonical invocation spec используется budget и execution,
symbolic FD-width даёт консервативный pre-acquisition bound. Новый
independent verdict, delivery и live truth остаются открытыми.

Latest independent review снова вернул historical `BLOCK` по implementation
gaps уже существующих требований: acquisition peak недосчитывал recursive
directory lifecycle, final subprocess handoff не перепроверялся после args
memfd, invocation ownership оставался продублирован, exact role order и
полная symlink matrix не были закреплены. Minor remediation 24.x устраняет
эти gaps без material scope change и без нового approval; author,
independent, delivery и live gates сохраняют прежний truth state.

Последующий independent review обнаружил ещё один P1 implementation gap:
между inventory и acquisition могла появиться topology глубже canonical
`MAX_DIRECTORY_DEPTH`, а второй проход не применял depth policy. Historical
minor remediation 25.x добавляет повторный canonical inventory preflight до
первого content memfd и независимый acquisition depth guard. Claims 22.3,
22.5, 24.2 и load-bearing часть 24.3 были временно переоткрыты на valid red и
закрыты только после targeted green. Independent 22.8, 22.9, 23.5, 24.5,
delivery и live gates остаются открытыми.

Reviewer-only deviation зафиксирован честно: временная source mutation была
побайтово восстановлена, pre/post fingerprints совпали, но этот probe
исключён из mandatory acceptance evidence. Он не является implementation
change.

Следующая independent validation снова использует `workspace-write` только
для temp/cache/evidence при source-read-only policy и два последовательных
запуска exact five-module команды с
`HERMES_TEST_FILE_RETRIES=0`: bootstrap, rollout, runtime coherence,
runtime sandbox и новый rollout state. Оба запуска обязаны завершиться без
retry/`FLAKY` и с идентичными pre/post fingerprints.

Live rollout, wrapper/process replacement, restart, MCP/DB/systemd/deploy и
network actions запрещены без отдельного exact разрешения. Commit/push/PR
остаются закрыты до accepted independent review без `BLOCK`.
