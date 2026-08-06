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

- owner Руслана, Юлю (`family_sandbox`) и остальные 8 family principals;
- два `shared_room` профиля с membership без повышения личной роли;
- unknown principal, Telegram `user_id != chat_id` и guessed foreign profile;
- fail-closed access registry и ровно configured media-provider order для image,
  STT и TTS;
- dashboard `/api/access/users` через существующую auth boundary с redacted
  payload.

Признаки успеха: rollout shape `1 owner + 8 family_standard + 1
family_sandbox + 2 rooms`, неизвестные/malformed identities отклонены до
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
