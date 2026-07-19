"""Fail-closed owner-only policy for personal messaging gateways."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional


_ALLOW_ALL_KEYS = (
    "GATEWAY_ALLOW_ALL_USERS",
    "TELEGRAM_ALLOW_ALL_USERS",
)
_DIRECT_ALLOWLIST_KEYS = (
    "GATEWAY_ALLOWED_USERS",
    "TELEGRAM_ALLOWED_USERS",
)
_GROUP_GRANT_KEYS = (
    "TELEGRAM_GROUP_ALLOWED_USERS",
    "TELEGRAM_GROUP_ALLOWED_CHATS",
    "TELEGRAM_ALLOW_BOTS",
)
_TRUE = frozenset({"1", "true", "yes", "on"})
_GRANT_ENV_KEYS = frozenset(
    (*_ALLOW_ALL_KEYS, *_DIRECT_ALLOWLIST_KEYS, *_GROUP_GRANT_KEYS)
)
_GRANT_ENV_AUDIT_ERROR = "_HERMES_GRANT_ENV_AUDIT_ERROR"


def _config_bool(value: Any, *, default: bool = False) -> tuple[bool, bool]:
    if value is None:
        return default, True
    if isinstance(value, bool):
        return value, True
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUE:
            return True, True
        if normalized in {"0", "false", "no", "off"}:
            return False, True
    return default, False


@dataclass(frozen=True)
class SharedTelegramScope:
    """Opaque server-bound identity for one shared Telegram memory scope."""

    memory_namespace: str
    is_topic: bool


@dataclass(frozen=True)
class SinglePrincipalPolicy:
    """Compiled policy for one logical owner and explicit ingress mappings."""

    enabled: bool = False
    telegram_owner_id: Optional[str] = None
    telegram_shared_chat_ids: tuple[str, ...] = field(default_factory=tuple, repr=False)
    allow_owner_bound_relay: bool = False
    config_error_count: int = field(default=0, repr=False)

    @classmethod
    def from_dict(cls, raw: Any) -> "SinglePrincipalPolicy":
        if raw is None:
            return cls()
        if not isinstance(raw, dict):
            return cls(config_error_count=1)

        known_keys = {
            "enabled",
            "telegram_owner_id",
            "telegram_shared_chat_ids",
            "allow_owner_bound_relay",
        }
        shape_errors = len(set(raw) - known_keys)
        if raw and "enabled" not in raw:
            shape_errors += 1

        enabled, enabled_ok = _config_bool(raw.get("enabled"))
        relay, relay_ok = _config_bool(raw.get("allow_owner_bound_relay"))
        owner_raw = raw.get("telegram_owner_id")
        owner_ok = owner_raw is None or (
            not isinstance(owner_raw, (bool, dict, list, tuple, set))
        )
        owner = str(owner_raw).strip() if owner_ok and owner_raw is not None else None

        shared_raw = raw.get("telegram_shared_chat_ids")
        shared_ok = shared_raw is None or isinstance(
            shared_raw, (list, tuple, set, frozenset)
        )
        shared_values: list[str] = []
        if shared_ok and shared_raw is not None:
            for value in shared_raw:
                if isinstance(value, (bool, dict, list, tuple, set, frozenset)):
                    shared_ok = False
                    continue
                normalized = str(value).strip()
                if normalized and normalized not in shared_values:
                    shared_values.append(normalized)
        return cls(
            enabled=enabled,
            telegram_owner_id=owner or None,
            telegram_shared_chat_ids=tuple(shared_values),
            allow_owner_bound_relay=relay,
            config_error_count=(
                shape_errors
                + sum((not enabled_ok, not relay_ok, not owner_ok, not shared_ok))
            ),
        )

    def shared_scope(self, source: Any) -> Optional[SharedTelegramScope]:
        """Resolve an allowlisted shared Telegram group/topic, otherwise deny."""
        if not self.enabled:
            return None
        platform = getattr(getattr(source, "platform", None), "value", None)
        chat_type = str(getattr(source, "chat_type", "") or "").lower()
        chat_id = getattr(source, "chat_id", None)
        user_id = getattr(source, "user_id", None)
        if (
            platform != "telegram"
            or chat_type not in {"group", "forum"}
            or chat_id is None
            or user_id is None
            or bool(getattr(source, "is_bot", False))
            or str(chat_id) not in self.telegram_shared_chat_ids
        ):
            return None

        thread_id = getattr(source, "thread_id", None)
        canonical = "\0".join(
            ("telegram", str(chat_id), str(thread_id) if thread_id else "root")
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return SharedTelegramScope(
            memory_namespace=f"telegram/{digest}",
            is_topic=bool(thread_id),
        )

    def group_sessions_per_user(self, source: Any, *, default: bool) -> bool:
        """Force allowlisted shared scopes onto one session per chat/topic."""
        return False if self.shared_scope(source) is not None else default

    def authorize(self, source: Any, *, upstream_authenticated: bool = False) -> Optional[bool]:
        """Return an authoritative decision, or ``None`` when mode is disabled."""
        if not self.enabled:
            return None
        if getattr(source, "chat_type", None) != "dm":
            return self.shared_scope(source) is not None
        if upstream_authenticated:
            return self.allow_owner_bound_relay
        platform = getattr(getattr(source, "platform", None), "value", None)
        user_id = getattr(source, "user_id", None)
        return bool(
            platform == "telegram"
            and user_id is not None
            and str(user_id) == self.telegram_owner_id
        )

    def pairing_identity_allowed(self, platform: str, user_id: str) -> bool:
        if not self.enabled:
            return True
        return platform == "telegram" and str(user_id) == self.telegram_owner_id

    def to_dict(self) -> dict[str, Any]:
        result = {
            "enabled": self.enabled,
            "allow_owner_bound_relay": self.allow_owner_bound_relay,
        }
        if self.telegram_owner_id:
            result["telegram_owner_id"] = self.telegram_owner_id
        if self.telegram_shared_chat_ids:
            result["telegram_shared_chat_ids"] = list(self.telegram_shared_chat_ids)
        return result


@dataclass(frozen=True)
class PolicyReport:
    mode: str
    verdict: str
    conflicts: tuple[tuple[str, int], ...] = ()

    @property
    def valid(self) -> bool:
        return self.verdict in {"pass", "disabled"}

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "verdict": self.verdict,
            "conflicts": [
                {"category": category, "count": count}
                for category, count in self.conflicts
            ],
        }


class SinglePrincipalPolicyError(ValueError):
    """Raised without identity values when owner-only policy is invalid."""


def _csv_values(raw: str) -> list[str]:
    return [value.strip() for value in raw.split(",") if value.strip()]


def validate_single_principal_policy(
    policy: SinglePrincipalPolicy,
    *,
    gateway_config: Any = None,
    pairing_store: Any = None,
    environ: Optional[Mapping[str, str]] = None,
    require_enabled: bool = False,
) -> PolicyReport:
    """Validate policy and effective legacy grants without exposing identities."""
    if not policy.enabled:
        disabled_counts = {}
        if policy.config_error_count:
            disabled_counts["malformed_policy"] = policy.config_error_count
        if require_enabled:
            disabled_counts["policy_disabled"] = 1
        conflicts = tuple(sorted(disabled_counts.items()))
        return PolicyReport(
            mode="single_principal",
            verdict="fail" if conflicts else "disabled",
            conflicts=conflicts,
        )

    env = environ if environ is not None else os.environ
    counts: dict[str, int] = {}

    def add(category: str, count: int = 1) -> None:
        if count:
            counts[category] = counts.get(category, 0) + count

    add("malformed_policy", policy.config_error_count)
    if env.get(_GRANT_ENV_AUDIT_ERROR):
        add("grant_store_unreadable")
    if not policy.telegram_owner_id and not policy.allow_owner_bound_relay:
        add("missing_owner_mapping")
    if policy.telegram_owner_id == "*":
        add("wildcard_owner")
    add(
        "wildcard_shared_scope",
        sum(value == "*" for value in policy.telegram_shared_chat_ids),
    )
    add(
        "malformed_shared_scope",
        sum(
            value != "*" and re.fullmatch(r"-[1-9][0-9]*", value) is None
            for value in policy.telegram_shared_chat_ids
        ),
    )

    for key in _ALLOW_ALL_KEYS:
        if str(env.get(key, "")).strip().lower() in _TRUE:
            add("allow_all")

    for key in _GROUP_GRANT_KEYS:
        raw = str(env.get(key, "")).strip()
        if raw and raw.lower() not in {"none", "false", "0", "off"}:
            add("group_grant", len(_csv_values(raw)) or 1)

    for key in _DIRECT_ALLOWLIST_KEYS:
        values = _csv_values(str(env.get(key, "")))
        add("wildcard_grant", sum(value == "*" for value in values))
        add(
            "non_owner_allowlist",
            sum(value != "*" and value != policy.telegram_owner_id for value in values),
        )

    if gateway_config is not None:
        if getattr(gateway_config, "multiplex_profiles", False):
            add("multiplex_not_supported")
        for platform, config in getattr(gateway_config, "platforms", {}).items():
            if not getattr(config, "enabled", False):
                continue
            platform_name = getattr(platform, "value", str(platform))
            if platform_name not in {"telegram", "relay"}:
                add("unsupported_external_platform")
            if platform_name == "relay" and not policy.allow_owner_bound_relay:
                add("relay_not_allowed")
            extra = getattr(config, "extra", {}) or {}
            if platform_name == "telegram":
                for key in ("group_allow_from",):
                    values = extra.get(key)
                    if values:
                        add("group_grant", len(values) if isinstance(values, list) else 1)
                values = extra.get("allow_from")
                if values:
                    values = values if isinstance(values, list) else [values]
                    add("wildcard_grant", sum(str(value).strip() == "*" for value in values))
                    add(
                        "non_owner_allowlist",
                        sum(
                            str(value).strip() not in {"*", policy.telegram_owner_id}
                            for value in values
                        ),
                    )

    if pairing_store is not None:
        try:
            pairing_grants = pairing_store.list_approved()
        except (OSError, TypeError, ValueError):
            add("pairing_store_unreadable")
        else:
            for grant in pairing_grants:
                if not policy.pairing_identity_allowed(
                    str(grant.get("platform", "")), str(grant.get("user_id", ""))
                ):
                    add("non_owner_pairing")

    conflicts = tuple(sorted(counts.items()))
    return PolicyReport(
        mode="single_principal",
        verdict="fail" if conflicts else "pass",
        conflicts=conflicts,
    )


def require_valid_single_principal_policy(
    policy: SinglePrincipalPolicy,
    **kwargs: Any,
) -> PolicyReport:
    report = validate_single_principal_policy(policy, **kwargs)
    if not report.valid:
        categories = ",".join(category for category, _ in report.conflicts)
        raise SinglePrincipalPolicyError(
            f"single-principal policy validation failed: categories={categories}"
        )
    return report


def _runtime_grant_environment() -> dict[str, str]:
    """Merge process env with persisted auth grants without exposing credentials."""
    environ = dict(os.environ)
    try:
        from hermes_cli.config import load_env

        persisted = load_env()
    except (OSError, UnicodeError, ValueError):
        environ[_GRANT_ENV_AUDIT_ERROR] = "1"
        return environ
    for key in _GRANT_ENV_KEYS:
        if key in persisted:
            environ[key] = persisted[key]
    return environ


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate single-principal gateway policy")
    parser.add_argument("--require-enabled", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from gateway.config import load_gateway_config
    from gateway.pairing import PairingStore

    config = load_gateway_config()
    store = PairingStore(read_only=True)
    report = validate_single_principal_policy(
        config.single_principal,
        gateway_config=config,
        pairing_store=store,
        environ=_runtime_grant_environment(),
        require_enabled=args.require_enabled,
    )
    output = json.dumps(report.as_dict(), sort_keys=True)
    print(output)
    return 0 if report.valid else 2


if __name__ == "__main__":
    raise SystemExit(main())
