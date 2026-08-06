"""Audit log for dashboard-auth events.

Profile-aware location: ``$HERMES_HOME/logs/dashboard-auth.log``.
Format: one JSON object per line. Token-like fields are stripped before
serialisation to avoid leaking refresh tokens or JWTs to disk.

This module deliberately keeps a minimal dependency surface — no imports
from ``hermes_constants`` or other hermes_cli modules — so it can be
imported safely from middleware code that loads early in the startup
sequence.
"""
from __future__ import annotations

import datetime as _dt
import enum
import hashlib
import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Optional

_log = logging.getLogger(__name__)
_write_lock = threading.Lock()

# Field names that must never appear in the log raw. Any kwarg matching
# these is silently dropped.
_REDACTED_FIELDS: frozenset = frozenset({
    "access_token", "refresh_token", "code", "code_verifier",
    "state", "ticket", "cookie", "Authorization", "authorization",
})


class AuditEvent(enum.Enum):
    """Event types written to dashboard-auth.log.

    Values are the literal ``event`` field on the JSON line.
    """

    LOGIN_START = "login_start"
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILURE = "login_failure"
    LOGOUT = "logout"
    REFRESH_SUCCESS = "refresh_success"
    REFRESH_FAILURE = "refresh_failure"
    REVOKE = "revoke"
    SESSION_VERIFY_FAILURE = "session_verify_failure"
    WS_TICKET_MINTED = "ws_ticket_minted"
    WS_TICKET_REJECTED = "ws_ticket_rejected"
    TOKEN_AUTH_SUCCESS = "token_auth_success"
    TOKEN_AUTH_FAILURE = "token_auth_failure"
    BREAK_GLASS_CREATED = "break_glass_created"
    BREAK_GLASS_READ = "break_glass_read"
    BREAK_GLASS_EXPIRED = "break_glass_expired"
    BREAK_GLASS_REVOKED = "break_glass_revoked"
    BREAK_GLASS_DENIED = "break_glass_denied"


BREAK_GLASS_MAX_AGE = _dt.timedelta(minutes=15)
_BREAK_GLASS_READ_ACTIONS = frozenset({"history_read", "single_record_read"})
_BREAK_GLASS_FORBIDDEN_ACTIONS = frozenset({
    "bulk_search",
    "export",
    "model_delivery",
    "telegram_delivery",
    "write",
    "migration",
    "role_change",
    "tool_execution",
    "session_mutation",
    "memory_mutation",
    "private_memory_export",
})
_BREAK_GLASS_AUDIT_ACTIONS = (
    _BREAK_GLASS_READ_ACTIONS | _BREAK_GLASS_FORBIDDEN_ACTIONS
)


class BreakGlassError(PermissionError):
    """A break-glass lease cannot authorize the requested operation."""


def _utc_now(value: Optional[_dt.datetime] = None) -> _dt.datetime:
    """Return an aware UTC timestamp; reject ambiguous local timestamps."""
    if value is None:
        return _dt.datetime.now(_dt.timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("break-glass timestamps must be timezone-aware")
    return value.astimezone(_dt.timezone.utc)


def _hash_label(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class BreakGlassLease:
    """Ephemeral, scoped, read-only inspection lease.

    The lease is intentionally not persisted. A process restart therefore
    invalidates every lease and requires a fresh reason and reconfirmation.
    ``read_only_scope`` is retained for server-side authorization but only its
    hash is emitted to the audit log.
    """

    lease_id: str
    owner_audit_id: str
    reason_hash: str
    read_only_scope: str
    issued_at: _dt.datetime
    expires_at: _dt.datetime
    revoked: bool = False

    def __post_init__(self) -> None:
        if not self.lease_id or not self.owner_audit_id:
            raise ValueError("break-glass lease identifiers are required")
        if not self.reason_hash or len(self.reason_hash) != 64:
            raise ValueError("break-glass reason hash is invalid")
        if not isinstance(self.read_only_scope, str) or not self.read_only_scope.strip():
            raise ValueError("break-glass read-only scope is required")
        issued_at = _utc_now(self.issued_at)
        expires_at = _utc_now(self.expires_at)
        if expires_at <= issued_at:
            raise ValueError("break-glass lease must expire after issuance")
        if expires_at - issued_at > BREAK_GLASS_MAX_AGE:
            raise ValueError("break-glass lease cannot exceed 15 minutes")
        object.__setattr__(self, "issued_at", issued_at)
        object.__setattr__(self, "expires_at", expires_at)

    def is_active(self, now: Optional[_dt.datetime] = None) -> bool:
        current = _utc_now(now)
        return not self.revoked and current < self.expires_at


def _lease_audit_fields(lease: BreakGlassLease) -> dict[str, str]:
    return {
        "lease_id": lease.lease_id,
        "owner_audit_id": lease.owner_audit_id,
        "reason_hash": lease.reason_hash,
        "scope_hash": _hash_label(lease.read_only_scope),
    }


def _safe_break_glass_action(action: Any) -> str:
    if not isinstance(action, str):
        return "unknown"
    normalized = action.strip().lower()
    return normalized if normalized in _BREAK_GLASS_AUDIT_ACTIONS else "unknown"


def create_break_glass_lease(
    reason: str,
    *,
    read_only_scope: str,
    reconfirmed: bool,
    now: Optional[_dt.datetime] = None,
) -> BreakGlassLease:
    """Create one 15-minute lease after reason and second confirmation.

    No message/session content is accepted by this API, and no lease state is
    written to disk. ``reconfirmed`` is a server-side second confirmation,
    not a model-controlled flag.
    """
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("break-glass reason is required")
    if reconfirmed is not True:
        raise BreakGlassError("break-glass reconfirmation is required")
    if not isinstance(read_only_scope, str) or not read_only_scope.strip():
        raise ValueError("break-glass read-only scope is required")

    issued_at = _utc_now(now)
    lease = BreakGlassLease(
        lease_id=uuid.uuid4().hex,
        owner_audit_id=uuid.uuid4().hex,
        reason_hash=_hash_label(reason.strip()),
        read_only_scope=read_only_scope.strip(),
        issued_at=issued_at,
        expires_at=issued_at + BREAK_GLASS_MAX_AGE,
    )
    audit_log(
        AuditEvent.BREAK_GLASS_CREATED,
        **_lease_audit_fields(lease),
        action="create",
        expires_at=lease.expires_at.isoformat(),
    )
    return lease


def assert_break_glass_read(
    lease: BreakGlassLease,
    *,
    action: str = "history_read",
    now: Optional[_dt.datetime] = None,
) -> None:
    """Authorize one redacted read and deny writes, bulk, model and delivery."""
    if not isinstance(lease, BreakGlassLease):
        raise BreakGlassError("break-glass lease is invalid")
    current = _utc_now(now)
    safe_action = _safe_break_glass_action(action)
    if lease.revoked:
        audit_log(AuditEvent.BREAK_GLASS_DENIED, **_lease_audit_fields(lease), action=safe_action, reason="revoked")
        raise BreakGlassError("break-glass lease revoked")
    if current >= lease.expires_at:
        audit_log(AuditEvent.BREAK_GLASS_EXPIRED, **_lease_audit_fields(lease), action=safe_action)
        raise BreakGlassError("break-glass lease expired")
    if safe_action in _BREAK_GLASS_FORBIDDEN_ACTIONS or safe_action not in _BREAK_GLASS_READ_ACTIONS:
        audit_log(AuditEvent.BREAK_GLASS_DENIED, **_lease_audit_fields(lease), action=safe_action, reason="read_only")
        raise BreakGlassError("break-glass lease is read-only")
    audit_log(AuditEvent.BREAK_GLASS_READ, **_lease_audit_fields(lease), action=safe_action)


def revoke_break_glass_lease(
    lease: BreakGlassLease,
    *,
    now: Optional[_dt.datetime] = None,
) -> BreakGlassLease:
    """Revoke a lease immediately; repeated revoke is idempotent."""
    _utc_now(now)
    if not isinstance(lease, BreakGlassLease):
        raise BreakGlassError("break-glass lease is invalid")
    if lease.revoked:
        return lease
    revoked = replace(lease, revoked=True)
    audit_log(AuditEvent.BREAK_GLASS_REVOKED, **_lease_audit_fields(revoked), action="revoke")
    return revoked


def _resolve_log_path() -> Path:
    """``$HERMES_HOME/logs/dashboard-auth.log`` with the standard fallback.

    Mirrors ``hermes_constants.get_hermes_home`` semantics: env var wins,
    else ``~/.hermes``. A local copy avoids an import cycle with the
    middleware which lives below ``hermes_cli``.
    """
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    return Path(home) / "logs" / "dashboard-auth.log"


def audit_log(event: AuditEvent, **fields: Any) -> None:
    """Append one event to the audit log.

    Token-like fields are dropped. Missing log directory is created.
    Write failures are logged at WARNING but never raise — auth must not
    fail because the audit logger broke.
    """
    safe_fields = {
        k: v for k, v in fields.items()
        if k not in _REDACTED_FIELDS
    }
    entry = {
        "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "event": event.value,
        **safe_fields,
    }
    line = json.dumps(entry, separators=(",", ":")) + "\n"
    path = _resolve_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _write_lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception as e:
        _log.warning("dashboard-auth audit log write failed: %s", e)
