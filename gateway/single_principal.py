"""Fail-closed owner-only policy for personal messaging gateways."""

from __future__ import annotations

import argparse
import json
import os
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
class SinglePrincipalPolicy:
    """Compiled policy for one logical owner and explicit ingress mappings."""

    enabled: bool = False
    telegram_owner_id: Optional[str] = None
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
        return cls(
            enabled=enabled,
            telegram_owner_id=owner or None,
            allow_owner_bound_relay=relay,
            config_error_count=(
                shape_errors + sum((not enabled_ok, not relay_ok, not owner_ok))
            ),
        )

    def authorize(self, source: Any, *, upstream_authenticated: bool = False) -> Optional[bool]:
        """Return an authoritative decision, or ``None`` when mode is disabled."""
        if not self.enabled:
            return None
        if getattr(source, "chat_type", None) != "dm":
            return False
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
    if not policy.telegram_owner_id and not policy.allow_owner_bound_relay:
        add("missing_owner_mapping")
    if policy.telegram_owner_id == "*":
        add("wildcard_owner")

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
        require_enabled=args.require_enabled,
    )
    output = json.dumps(report.as_dict(), sort_keys=True)
    print(output)
    return 0 if report.valid else 2


if __name__ == "__main__":
    raise SystemExit(main())
