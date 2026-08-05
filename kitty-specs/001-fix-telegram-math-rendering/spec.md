# Fix Telegram display-math rendering

Status: approved for implementation
Approval: user confirmed `Ок, делай` on 2026-08-05
Beads: HERMES-kq1

## Problem

The active Hermes Telegram adapter detects display math but still requires the
global `rich_messages` opt-in. With that flag false, explicit `$$...$$`
formulas fall back to legacy MarkdownV2 and are not reliably rendered.
The active tutor prompt also does not distinguish simple one-line arithmetic
from complex or multiline formulas.

## Scope

- Route safe paired display math through Telegram rich delivery per message,
  without enabling rich delivery globally.
- Keep fenced/source LaTeX, malformed or unclosed delimiters, and protected
  regions on the legacy copyable path.
- Tell the tutor to use rendered `$$...$$` blocks for complex/multiline
  mathematical solutions while keeping simple one-line answers plain.
- Add focused behavioral regression tests and activate the change on HOSTKEY
  staging only.

## Non-goals

- No Telegram family-message smoke send.
- No change to global `rich_messages` or unrelated rich constructs.
- No Timeweb/live, credentials, database, DNS, or service migration changes.

## Acceptance scenarios

1. With `rich_messages: false`, a safe paired display-math block uses the rich
   API and preserves reply/thread metadata.
2. Plain Markdown, fenced LaTeX, and unclosed/protected math use legacy send.
3. The tutor prompt requests rendered blocks for complex/multiline formulas
   and keeps source requests fenced.
4. Focused tests, compile checks, and staging service health pass.
