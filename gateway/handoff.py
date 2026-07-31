"""Shared helpers for CLI/TUI to gateway session handoff."""

from __future__ import annotations

from typing import Any

from gateway.access_registry import (
    AccessDeniedError,
    DeliveryTarget,
    RedactedAuditMetadata,
    ResolvedAccessContext,
    TransportIdentity,
)
from gateway.config import Platform


def categorize_handoff_failure(
    exc: BaseException,
    *,
    registry_configured: bool,
) -> str | None:
    """Return a safe persisted/loggable failure category for handoff errors.

    Legacy no-registry handoff keeps its historical diagnostics, so ``None``
    means the caller may use its old error handling.
    """
    if isinstance(exc, AccessDeniedError):
        return exc.reason
    if not registry_configured:
        return None
    if isinstance(exc, TimeoutError):
        return "handoff_timeout"
    if isinstance(exc, (ConnectionError, OSError)):
        return "handoff_delivery_error"
    return "handoff_processing_failed"


def _handoff_denied(reason: str, target: DeliveryTarget | None = None) -> AccessDeniedError:
    return AccessDeniedError(
        reason,
        RedactedAuditMetadata.from_delivery_target(reason, target),
    )


def resolve_handoff_access_context(
    config: Any,
    platform: Platform,
    *,
    access_registry: Any = None,
) -> ResolvedAccessContext | None:
    """Return the server-owned handoff context for a configured registry.

    No registry keeps legacy behavior. Registry mode is v1 Telegram DM-home
    only: exact configured account, chat_id == user_id, and no thread/topic.
    """
    registry = (
        access_registry
        if access_registry is not None
        else getattr(config, "access_registry", None)
    )
    if registry is None:
        return None
    if platform != Platform.TELEGRAM:
        raise _handoff_denied("handoff_registry_unsupported_platform")

    platform_cfg = getattr(config, "platforms", {}).get(platform)
    extra = getattr(platform_cfg, "extra", {}) if platform_cfg is not None else {}
    account = extra.get("account") if isinstance(extra, dict) else None
    if not isinstance(account, str) or not account.strip():
        raise _handoff_denied("handoff_missing_route_account")
    account = account.strip()

    home = config.get_home_channel(platform)
    chat_id = str(home.chat_id).strip() if home and home.chat_id is not None else ""
    if not chat_id:
        raise _handoff_denied("handoff_missing_home_channel")
    if getattr(home, "thread_id", None) is not None:
        raise _handoff_denied("handoff_registry_thread_unsupported")

    identity = TransportIdentity(
        platform=platform.value,
        account=account,
        peer_kind="dm",
        user_id=chat_id,
        chat_id=chat_id,
        thread_id=None,
    )
    context = registry.validate_resolved_context(registry.resolve(identity))
    target = context.delivery_target
    if (
        target.platform != platform.value
        or target.account != account
        or target.peer_kind != "dm"
        or target.chat_id != chat_id
        or target.thread_id is not None
    ):
        raise _handoff_denied("handoff_destination_mismatch", target)
    return context
