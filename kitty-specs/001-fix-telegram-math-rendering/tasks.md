# Work packages

## WP01 — Telegram display math and tutor formatting

Status: in progress
Beads: HERMES-kq1

Scope:

- Patch `plugins/platforms/telegram/adapter.py`.
- Add behavioral tests in `tests/gateway/test_telegram_rich_messages.py`.
- Update the active tutor prompt `/home/openclaw/.hermes/SOUL.md` after source
  and test validation.
- Activate and verify HOSTKEY staging.

Verification:

- Focused and relevant test suites pass.
- Compile and diff checks pass.
- `hermes-gateway.service` is active and health endpoint responds after the
  restart.
