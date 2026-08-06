# Gurra/Hermes media-access: staging canary и rollback gate

Этот документ описывает только проверяемый staging/read-only пакет. Он не
применяет live-конфигурацию, не перезапускает `hermes-gateway` или dashboard,
не отправляет Telegram-сообщения и не читает credential/auth-файлы.

## Candidate evidence

- Live-derived base: `a4096896ed92d1edb3dd02e62876dc0fc1ce140a`.
- Candidate branch: `codex/live-compatible-media-cutover`.
- Candidate HEAD и полный changed-file manifest берутся из
  `tests/test_media_access_canary.py`: для каждого tracked changed file
  вычисляется SHA-256; секретоподобные пути отклоняются.
- Рабочее дерево перед evidence должно быть чистым. Тест не печатает содержимое
  файлов и не включает `.env`, credentials, auth или private keys.

Проверка:

```bash
python -m pytest -q tests/test_media_access_canary.py -s
```

## Synthetic canary

Canary проверяет:

- owner Руслана и 9 `family` principals (включая Юлю);
- два `shared_room` профиля с membership без повышения личной роли;
- unknown principal, Telegram `user_id != chat_id` и guessed foreign profile;
- fail-closed access registry и ровно configured media-provider order для image,
  STT и TTS;
- dashboard `/api/access/users` через существующую auth boundary с redacted
  payload.

Признаки успеха: rollout shape `1 owner + 9 family + 2 rooms`, неизвестные/malformed identities отклонены до
model/session/tools, secret references не попали в report, dashboard не отдаёт
transport identity или delivery target.

## Service snapshot

Перед любым отдельным live gate сначала повторить read-only status через
`vps-config/bin/codex-hostkey-run.ps1 -Status`. В текущем snapshot сервис
`hermes-gateway` был `active`; этот пакет сервис не изменяет.

## Backup и rollback

До live apply оператор отдельно сохраняет только metadata/redacted snapshots:

1. exact code ref текущего live HEAD;
2. redacted access/media policy dry-run;
3. profile binding and service-unit metadata без secret values.

Rollback разрешён только после отдельного подтверждения live configuration:

1. остановить canary/apply sequence;
2. вернуть exact prior code/config refs и проверить hashes;
3. повторить read-only policy/access checks;
4. только после проверки отдельно решать вопрос о restart и Telegram canary.

Rollback не удаляет legacy sessions, archive или memory и не восстанавливает
секреты из backup.

## Hard stop

T022 остаётся отдельным gate. Никакого `systemctl restart`, live symlink/config
switch, DB mutation, Telegram canary или credential rotation без нового явного
разрешения Руслана.

## Live evidence 2026-08-06

- Live cutover happened after a separate explicit approval.
- The first attempt was automatically rolled back because the source check incorrectly searched `/proc/<pid>/maps` for Python source; the retry fixed the staging editable-path and succeeded.
- No sessions, memory, Telegram messages, or user data were deleted or migrated.
- Retry metadata backup: `/home/openclaw/backups/gurra-live-compatible-media-cutover/20260806T182906Z` (directory mode `0700`, files mode `0600`).
- Live config SHA-256: `cdbe4919dd88ef8b1a83d977f85e062df7757352d481f703ca166d8c34674cbe`; redacted policy validation passed, owner `1`, family `9`, shared rooms `2`.
- Candidate artifact: `/home/openclaw/staging/hermes-deploy-live-compatible-25d5031b-20260806T202000Z`, candidate ref `25d5031bef7862f39303a47dd4f35c71305fd96d`.
- Gateway drop-in SHA-256: `863e767993099a05f90b723896ecd96bf3f7e133929c1cd3ee7a837e87088421d`, mode `664`; metadata records mode `600`.
- The old symlink target was preserved for rollback and the new target is the candidate artifact.
- Focused post-update suite: 18 files, 1039 tests passed; artifact synthetic canary: 4 passed; dashboard loopback HTTP 200.
- Final gateway and dashboard are active/running, `Result=success`, `NRestarts=0`, and the gateway `ExecStart`/import path matches the candidate.
- The Telegram canary was not run in this scope.

T022 закрыт. Следующий live cutover требует отдельного scope-specific approval.
