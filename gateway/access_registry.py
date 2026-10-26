"""Fail-closed principal/role access contracts for gateway ingress."""

from __future__ import annotations

import hashlib
import json
import shlex
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Optional


_LEGACY_ROLE_ALIASES = {
    "family_standard": "family",
    "family_sandbox": "family",
}


def canonical_role_id(role_id: Any) -> Any:
    if not isinstance(role_id, str):
        return role_id
    return _LEGACY_ROLE_ALIASES.get(role_id, role_id)


def _immutable_capabilities(values: Any) -> frozenset[str]:
    if values is None:
        return frozenset()
    return frozenset(str(value) for value in values if isinstance(value, str) and value)


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _redact(value: Any) -> Optional[str]:
    return "present" if _is_nonempty_str(value) else None


_AUDIT_PLATFORMS = frozenset({"signal", "telegram"})
_AUDIT_PEER_KINDS = frozenset({"dm", "group", "supergroup", "channel"})
_RESOLVED_ACCESS_CONTEXT_KEYS = frozenset({
    "principal_id",
    "role_id",
    "profile_id",
    "conversation_scope",
    "capabilities",
    "delivery_target",
})
_DELIVERY_TARGET_KEYS = frozenset({
    "platform",
    "account",
    "peer_kind",
    "chat_id",
    "thread_id",
})
_AUTHORITY_SELECTOR_KEYS = frozenset({
    "capabilities",
    "capability",
    "conversation_scope",
    "delivery",
    "delivery_target",
    "memory_namespace",
    "namespace",
    "principal",
    "principal_id",
    "profile",
    "profile_id",
    "role",
    "role_id",
    "scope",
    "session",
    "session_id",
    "target_delivery",
    "target_namespace",
    "target_profile",
    "target_profile_id",
    "target_role",
    "target_session",
})


def _audit_label(value: Any, allowed: frozenset[str]) -> Optional[str]:
    if not isinstance(value, str):
        return None
    label = value.strip().lower()
    return label if label in allowed else "unknown"


def _authority_key(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower().replace("-", "_").lstrip("_")


def payload_has_authority_selector(
    payload: Any,
    *,
    allowed_top_level_keys: frozenset[str] = frozenset(),
) -> bool:
    """Detect user/model supplied authority selectors in structured payloads."""
    allowed = frozenset(_authority_key(key) for key in allowed_top_level_keys)

    def walk(value: Any, *, top_level: bool = False) -> bool:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = _authority_key(key)
                if normalized in _AUTHORITY_SELECTOR_KEYS and not (
                    top_level and normalized in allowed
                ):
                    return True
                if walk(item):
                    return True
        elif isinstance(value, (list, tuple)):
            return any(walk(item) for item in value)
        return False

    return walk(payload, top_level=True)


def command_args_have_authority_selector(raw_args: Any) -> bool:
    """Detect structured authority selectors in slash/command args."""
    if not isinstance(raw_args, str) or not raw_args.strip():
        return False
    try:
        parsed = json.loads(raw_args)
    except Exception:
        parsed = None
    if isinstance(parsed, dict) and payload_has_authority_selector(parsed):
        return True
    try:
        tokens = shlex.split(raw_args)
    except ValueError:
        tokens = raw_args.split()
    for token in tokens:
        key = ""
        if token.startswith("--"):
            key = token[2:].split("=", 1)[0]
        elif "=" in token:
            key = token.split("=", 1)[0].lstrip("-")
        if _authority_key(key) in _AUTHORITY_SELECTOR_KEYS:
            return True
    return False


@dataclass(frozen=True)
class DeliveryTarget:
    """Server-owned destination for replies after access resolution."""

    platform: str
    account: str
    peer_kind: str
    chat_id: str
    thread_id: Optional[str] = None

    def __post_init__(self) -> None:
        for value in (self.platform, self.account, self.peer_kind, self.chat_id):
            if not _is_nonempty_str(value):
                raise ValueError("delivery target requires nonempty string fields")
        if self.thread_id is not None and not _is_nonempty_str(self.thread_id):
            raise ValueError("delivery target thread_id must be nonempty when set")


@dataclass(frozen=True)
class ResolvedAccessContext:
    principal_id: str
    role_id: str
    profile_id: str
    conversation_scope: str
    capabilities: frozenset[str]
    delivery_target: DeliveryTarget

    def __post_init__(self) -> None:
        object.__setattr__(self, "role_id", canonical_role_id(self.role_id))
        object.__setattr__(self, "capabilities", _immutable_capabilities(self.capabilities))


def _require_exact_keys(data: Any, keys: frozenset[str], reason: str) -> Mapping[str, Any]:
    if not isinstance(data, dict) or frozenset(data.keys()) != keys:
        raise ValueError(reason)
    return data


def _require_serialized_string(value: Any, reason: str) -> str:
    if not _is_nonempty_str(value):
        raise ValueError(reason)
    return value


def _require_serialized_thread_id(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not _is_nonempty_str(value):
        raise ValueError("invalid_thread_id")
    return value


def _serialize_delivery_target(target: Any) -> dict[str, Any]:
    if not isinstance(target, DeliveryTarget):
        raise ValueError("malformed_delivery_target")
    return {
        "platform": _require_serialized_string(target.platform, "malformed_delivery_target"),
        "account": _require_serialized_string(target.account, "malformed_delivery_target"),
        "peer_kind": _require_serialized_string(target.peer_kind, "malformed_delivery_target"),
        "chat_id": _require_serialized_string(target.chat_id, "malformed_delivery_target"),
        "thread_id": _require_serialized_thread_id(target.thread_id),
    }


def _deserialize_delivery_target(data: Any) -> DeliveryTarget:
    target = _require_exact_keys(data, _DELIVERY_TARGET_KEYS, "malformed_delivery_target")
    return DeliveryTarget(
        platform=_require_serialized_string(target["platform"], "malformed_delivery_target"),
        account=_require_serialized_string(target["account"], "malformed_delivery_target"),
        peer_kind=_require_serialized_string(target["peer_kind"], "malformed_delivery_target"),
        chat_id=_require_serialized_string(target["chat_id"], "malformed_delivery_target"),
        thread_id=_require_serialized_thread_id(target["thread_id"]),
    )


def _serialize_capabilities(values: Any) -> list[str]:
    if not isinstance(values, frozenset):
        raise ValueError("malformed_capabilities")
    if any(not _is_nonempty_str(value) for value in values):
        raise ValueError("malformed_capabilities")
    return sorted(values)


def _deserialize_capabilities(values: Any) -> frozenset[str]:
    if not isinstance(values, list):
        raise ValueError("malformed_capabilities")
    seen: set[str] = set()
    for value in values:
        if not _is_nonempty_str(value):
            raise ValueError("malformed_capabilities")
        if value in seen:
            raise ValueError("duplicate_capabilities")
        seen.add(value)
    return frozenset(values)


def serialize_resolved_access_context(context: Any) -> dict[str, Any]:
    """Serialize a trusted access context for authoritative routing storage."""
    if not isinstance(context, ResolvedAccessContext):
        raise ValueError("malformed_resolved_access_context")
    return {
        "principal_id": _require_serialized_string(
            context.principal_id,
            "malformed_resolved_access_context",
        ),
        "role_id": _require_serialized_string(
            context.role_id,
            "malformed_resolved_access_context",
        ),
        "profile_id": _require_serialized_string(
            context.profile_id,
            "malformed_resolved_access_context",
        ),
        "conversation_scope": _require_serialized_string(
            context.conversation_scope,
            "malformed_resolved_access_context",
        ),
        "capabilities": _serialize_capabilities(context.capabilities),
        "delivery_target": _serialize_delivery_target(context.delivery_target),
    }


def deserialize_resolved_access_context(data: Any) -> ResolvedAccessContext:
    """Strictly restore a routing-store access context."""
    context = _require_exact_keys(
        data,
        _RESOLVED_ACCESS_CONTEXT_KEYS,
        "malformed_resolved_access_context",
    )
    return ResolvedAccessContext(
        principal_id=_require_serialized_string(
            context["principal_id"],
            "malformed_resolved_access_context",
        ),
        role_id=_require_serialized_string(
            context["role_id"],
            "malformed_resolved_access_context",
        ),
        profile_id=_require_serialized_string(
            context["profile_id"],
            "malformed_resolved_access_context",
        ),
        conversation_scope=_require_serialized_string(
            context["conversation_scope"],
            "malformed_resolved_access_context",
        ),
        capabilities=_deserialize_capabilities(context["capabilities"]),
        delivery_target=_deserialize_delivery_target(context["delivery_target"]),
    )


def canonical_access_context_fingerprint(context: Any) -> str:
    canonical = json.dumps(
        serialize_resolved_access_context(
            deserialize_resolved_access_context(
                serialize_resolved_access_context(context)
            )
        ),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def shared_memory_namespace_for_access_context(context: Any) -> str:
    resolved = deserialize_resolved_access_context(
        serialize_resolved_access_context(context)
    )
    if resolved.role_id != "shared_room":
        raise ValueError("wrong_role")
    target = resolved.delivery_target
    canonical = "\0".join(
        (
            resolved.profile_id,
            resolved.conversation_scope,
            target.platform,
            target.account,
            target.peer_kind,
            target.chat_id,
            target.thread_id or "root",
        )
    ).encode("utf-8")
    return f"access/{hashlib.sha256(canonical).hexdigest()}"


def memory_scope_from_resolved_access_context(context: Any) -> str:
    if isinstance(context, dict):
        resolved = deserialize_resolved_access_context(context)
    else:
        resolved = deserialize_resolved_access_context(
            serialize_resolved_access_context(context)
        )
    if resolved.role_id == "shared_room":
        return f"memory/shared/{shared_memory_namespace_for_access_context(resolved)}"
    canonical = "\0".join((resolved.profile_id, resolved.conversation_scope)).encode("utf-8")
    return f"memory/personal/{hashlib.sha256(canonical).hexdigest()}"


def session_scope_from_resolved_access_context(context: Any) -> dict[str, Any]:
    resolved = deserialize_resolved_access_context(
        serialize_resolved_access_context(context)
    )
    target = resolved.delivery_target
    is_dm = target.peer_kind == "dm"
    return {
        "profile_name": resolved.profile_id,
        "source": target.platform,
        "account": target.account,
        "chat_type": target.peer_kind,
        "chat_id": target.chat_id,
        "thread_id": target.thread_id or "",
        "user_id": target.chat_id if is_dm else "",
        "is_dm": is_dm,
    }


@dataclass(frozen=True)
class RolePolicy:
    role_id: str
    capabilities: frozenset[str]
    active: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "role_id", canonical_role_id(self.role_id))
        object.__setattr__(self, "capabilities", _immutable_capabilities(self.capabilities))


@dataclass(frozen=True)
class TransportIdentity:
    """Trusted adapter identity input; never copied into ResolvedAccessContext."""

    platform: Any
    account: Any
    peer_kind: Any
    user_id: Any
    chat_id: Any
    thread_id: Any = None

    @classmethod
    def from_session_source(cls, source: Any, *, account: str) -> "TransportIdentity":
        platform = getattr(getattr(source, "platform", None), "value", None)
        return cls(
            platform=platform,
            account=account,
            peer_kind=getattr(source, "chat_type", None),
            user_id=getattr(source, "user_id", None),
            chat_id=getattr(source, "chat_id", None),
            thread_id=getattr(source, "thread_id", None),
        )


@dataclass(frozen=True)
class ParticipantIdentity:
    platform: str
    account: str
    user_id: str

    def key(self) -> tuple[str, str, str]:
        return (self.platform, self.account, self.user_id)


@dataclass(frozen=True)
class PrincipalBinding:
    principal_id: str
    role_id: str
    profile_id: str
    transport_identity: TransportIdentity
    conversation_scope: str
    delivery_target: DeliveryTarget
    active: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "role_id", canonical_role_id(self.role_id))


@dataclass(frozen=True)
class SharedScopeBinding:
    principal_id: str
    role_id: str
    profile_id: str
    room_identity: TransportIdentity
    conversation_scope: str
    delivery_target: DeliveryTarget
    participant_identities: tuple[ParticipantIdentity, ...] = field(default_factory=tuple)
    active: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "role_id", canonical_role_id(self.role_id))
        object.__setattr__(self, "participant_identities", tuple(self.participant_identities))


@dataclass(frozen=True)
class RedactedAuditMetadata:
    event: str
    platform: Optional[str] = None
    account_ref: Optional[str] = None
    peer_kind: Optional[str] = None
    user_ref: Optional[str] = None
    chat_ref: Optional[str] = None
    thread_ref: Optional[str] = None

    @classmethod
    def from_transport(cls, event: str, identity: Optional[TransportIdentity]) -> "RedactedAuditMetadata":
        if identity is None:
            return cls(event=event)
        return cls(
            event=event,
            platform=_audit_label(getattr(identity, "platform", None), _AUDIT_PLATFORMS),
            account_ref=_redact(getattr(identity, "account", None)),
            peer_kind=_audit_label(getattr(identity, "peer_kind", None), _AUDIT_PEER_KINDS),
            user_ref=_redact(getattr(identity, "user_id", None)),
            chat_ref=_redact(getattr(identity, "chat_id", None)),
            thread_ref=_redact(getattr(identity, "thread_id", None)),
        )

    @classmethod
    def from_delivery_target(cls, event: str, target: Optional[DeliveryTarget]) -> "RedactedAuditMetadata":
        if not isinstance(target, DeliveryTarget):
            return cls(event=event)
        return cls(
            event=event,
            platform=_audit_label(getattr(target, "platform", None), _AUDIT_PLATFORMS),
            account_ref=_redact(getattr(target, "account", None)),
            peer_kind=_audit_label(getattr(target, "peer_kind", None), _AUDIT_PEER_KINDS),
            chat_ref=_redact(getattr(target, "chat_id", None)),
            thread_ref=_redact(getattr(target, "thread_id", None)),
        )

    def as_dict(self) -> dict[str, Optional[str]]:
        return {
            "event": self.event,
            "platform": self.platform,
            "account_ref": self.account_ref,
            "peer_kind": self.peer_kind,
            "user_ref": self.user_ref,
            "chat_ref": self.chat_ref,
            "thread_ref": self.thread_ref,
        }


class AccessDeniedError(PermissionError):
    """Typed fail-closed denial that carries only redacted diagnostics."""

    def __init__(self, reason: str, audit: RedactedAuditMetadata):
        self.reason = reason
        self.audit = audit
        super().__init__(f"access denied: {reason}")


@dataclass(frozen=True)
class RegistryValidationReport:
    verdict: str
    conflicts: tuple[tuple[str, int], ...] = ()

    @property
    def valid(self) -> bool:
        return self.verdict == "pass"

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "conflicts": [
                {"category": category, "count": count}
                for category, count in self.conflicts
            ],
        }


class RegistryValidationError(ValueError):
    def __init__(self, report: RegistryValidationReport):
        self.report = report
        categories = ",".join(category for category, _ in report.conflicts)
        super().__init__(f"access registry validation failed: categories={categories}")


@dataclass(frozen=True)
class AccessRegistry:
    roles: Mapping[str, RolePolicy]
    profiles: frozenset[str]
    principal_bindings: tuple[PrincipalBinding, ...] = field(default_factory=tuple)
    shared_scope_bindings: tuple[SharedScopeBinding, ...] = field(default_factory=tuple)
    scope_capabilities: Mapping[str, frozenset[str]] = field(default_factory=dict)
    backend_capabilities: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        roles = dict(self.roles.items())
        scopes = {
            str(key): _immutable_capabilities(value)
            for key, value in self.scope_capabilities.items()
        }
        object.__setattr__(self, "roles", MappingProxyType(roles))
        object.__setattr__(self, "profiles", frozenset(self.profiles))
        object.__setattr__(self, "principal_bindings", tuple(self.principal_bindings))
        object.__setattr__(self, "shared_scope_bindings", tuple(self.shared_scope_bindings))
        object.__setattr__(self, "scope_capabilities", MappingProxyType(scopes))
        object.__setattr__(self, "backend_capabilities", _immutable_capabilities(self.backend_capabilities))

    def validate(self) -> RegistryValidationReport:
        counts: dict[str, int] = {}

        def add(category: str, count: int = 1) -> None:
            if count:
                counts[category] = counts.get(category, 0) + count

        for key, role in self.roles.items():
            if not _is_nonempty_str(key):
                add("malformed_role_key")
            if not isinstance(role, RolePolicy):
                add("malformed_role")
                continue
            if canonical_role_id(key) != role.role_id:
                add("mismatched_role_key")

        principal_keys: dict[tuple[str, str, str, str], int] = {}
        principal_ids: dict[str, int] = {}
        principal_profiles: dict[str, int] = {}
        for binding in self.principal_bindings:
            if not isinstance(binding, PrincipalBinding):
                add("malformed_principal_binding")
                continue
            if not binding.active:
                continue
            if not _valid_principal_binding_identity(binding.transport_identity):
                add("malformed_principal_binding")
            else:
                key = _principal_key(binding.transport_identity)
                principal_keys[key] = principal_keys.get(key, 0) + 1
            self._validate_binding_authority(
                binding.principal_id,
                binding.role_id,
                binding.profile_id,
                binding.conversation_scope,
                add,
            )
            if not _delivery_matches_identity(binding.delivery_target, binding.transport_identity):
                add("delivery_target_mismatch")
            if _is_nonempty_str(binding.principal_id):
                principal_ids[binding.principal_id] = principal_ids.get(binding.principal_id, 0) + 1
            if _is_nonempty_str(binding.profile_id):
                principal_profiles[binding.profile_id] = principal_profiles.get(binding.profile_id, 0) + 1

        room_keys: dict[tuple[str, str, str, str, Optional[str]], int] = {}
        room_profiles: dict[str, int] = {}
        for binding in self.shared_scope_bindings:
            if not isinstance(binding, SharedScopeBinding):
                add("malformed_shared_scope_binding")
                continue
            if not binding.active:
                continue
            if not _valid_room_identity(binding.room_identity):
                add("malformed_shared_scope_binding")
            else:
                key = _room_key(binding.room_identity)
                room_keys[key] = room_keys.get(key, 0) + 1
            if binding.role_id != "shared_room":
                add("invalid_shared_room_role")
            self._validate_binding_authority(
                binding.principal_id,
                binding.role_id,
                binding.profile_id,
                binding.conversation_scope,
                add,
            )
            if not _delivery_matches_identity(binding.delivery_target, binding.room_identity):
                add("delivery_target_mismatch")
            if not binding.participant_identities:
                add("missing_shared_room_membership")
            participant_keys: dict[tuple[str, str, str], int] = {}
            for participant in binding.participant_identities:
                if not isinstance(participant, ParticipantIdentity):
                    add("malformed_shared_room_membership")
                    continue
                if not all(_is_nonempty_str(value) for value in participant.key()):
                    add("malformed_shared_room_membership")
                    continue
                participant_keys[participant.key()] = participant_keys.get(participant.key(), 0) + 1
            add(
                "duplicate_shared_room_member",
                sum(count - 1 for count in participant_keys.values() if count > 1),
            )
            if _is_nonempty_str(binding.principal_id):
                principal_ids[binding.principal_id] = principal_ids.get(binding.principal_id, 0) + 1
            if _is_nonempty_str(binding.profile_id):
                principal_profiles[binding.profile_id] = principal_profiles.get(binding.profile_id, 0) + 1
                room_profiles[binding.profile_id] = room_profiles.get(binding.profile_id, 0) + 1

        add("duplicate_principal_binding", sum(count - 1 for count in principal_keys.values() if count > 1))
        add("duplicate_principal_id", sum(count - 1 for count in principal_ids.values() if count > 1))
        add("duplicate_principal_profile", sum(count - 1 for count in principal_profiles.values() if count > 1))
        add("duplicate_shared_scope_binding", sum(count - 1 for count in room_keys.values() if count > 1))
        add("duplicate_shared_room_profile", sum(count - 1 for count in room_profiles.values() if count > 1))

        conflicts = tuple(sorted(counts.items()))
        return RegistryValidationReport(
            verdict="fail" if conflicts else "pass",
            conflicts=conflicts,
        )

    def require_valid(self) -> RegistryValidationReport:
        report = self.validate()
        if not report.valid:
            raise RegistryValidationError(report)
        return report

    def resolve(self, identity: TransportIdentity) -> ResolvedAccessContext:
        audit = RedactedAuditMetadata.from_transport("resolve", identity)
        if not self.validate().valid:
            raise AccessDeniedError("registry_validation_failed", audit)
        if not _valid_ingress_identity(identity):
            raise AccessDeniedError("malformed_identity", audit)
        if identity.platform not in {"signal", "telegram"}:
            raise AccessDeniedError("unknown_platform", audit)
        if identity.peer_kind == "dm":
            return self._resolve_dm(identity, audit)
        return self._resolve_shared_room(identity, audit)

    def validate_resolved_context(self, context: Any) -> ResolvedAccessContext:
        audit = RedactedAuditMetadata.from_delivery_target(
            "validate_context",
            getattr(context, "delivery_target", None),
        )
        if not isinstance(context, ResolvedAccessContext):
            raise AccessDeniedError("malformed_resolved_access_context", audit)
        if not self.validate().valid:
            raise AccessDeniedError("registry_validation_failed", audit)
        matches = [candidate for candidate in self._active_resolved_contexts() if candidate == context]
        if len(matches) > 1:
            raise AccessDeniedError("ambiguous_resolved_access_context", audit)
        if matches:
            return matches[0]
        principal_context = self._validate_derived_principal_context(context, audit)
        if principal_context is not None:
            return principal_context
        shared_context = self._validate_derived_shared_context(context, audit)
        if shared_context is not None:
            return shared_context
        raise AccessDeniedError("resolved_access_context_mismatch", audit)

    def validate_resolved_context_for_identity(
        self,
        context: Any,
        identity: TransportIdentity,
    ) -> ResolvedAccessContext:
        audit = RedactedAuditMetadata.from_transport("validate_context_source", identity)
        if not isinstance(identity, TransportIdentity) or not _valid_ingress_identity(identity):
            raise AccessDeniedError("malformed_identity", audit)
        validated = self.validate_resolved_context(context)
        expected = self.resolve(identity)
        if (
            not _delivery_matches_identity(validated.delivery_target, identity)
            or validated != expected
        ):
            raise AccessDeniedError("resolved_access_context_source_mismatch", audit)
        return expected

    def resolve_exact_profile_context(self, profile_id: str) -> ResolvedAccessContext:
        audit = RedactedAuditMetadata(event="resolve_profile_context")
        if not _is_nonempty_str(profile_id):
            raise AccessDeniedError("missing_profile", audit)
        active_contexts = [
            context for context in self._active_resolved_contexts() if context.profile_id == profile_id
        ]
        if len(active_contexts) > 1:
            raise AccessDeniedError("ambiguous_profile_binding", audit)
        if not self.validate().valid:
            raise AccessDeniedError("registry_validation_failed", audit)
        if not active_contexts:
            raise AccessDeniedError("missing_profile_binding", audit)
        return self.validate_resolved_context(active_contexts[0])

    def effective_capabilities(self, role_id: str, conversation_scope: str) -> frozenset[str]:
        role = self._role_for_id(role_id)
        if not isinstance(role, RolePolicy) or not role.active:
            return frozenset()
        scope_caps = self.scope_capabilities.get(conversation_scope, frozenset())
        return role.capabilities & scope_caps & self.backend_capabilities

    def _role_for_id(self, role_id: str) -> Optional[RolePolicy]:
        canonical = canonical_role_id(role_id)
        role = self.roles.get(role_id) or self.roles.get(canonical)
        if isinstance(role, RolePolicy):
            return role
        return next(
            (
                candidate
                for key, candidate in self.roles.items()
                if canonical_role_id(key) == canonical and isinstance(candidate, RolePolicy)
            ),
            None,
        )

    def _resolve_dm(self, identity: TransportIdentity, audit: RedactedAuditMetadata) -> ResolvedAccessContext:
        if identity.user_id != identity.chat_id:
            raise AccessDeniedError("dm_identity_mismatch", audit)
        matches = [
            binding
            for binding in self.principal_bindings
            if (
                binding.active
                and _valid_principal_binding_identity(binding.transport_identity)
                and _principal_key(binding.transport_identity) == _principal_key(identity)
            )
        ]
        disabled = [
            binding
            for binding in self.principal_bindings
            if (
                not binding.active
                and _valid_principal_binding_identity(binding.transport_identity)
                and _principal_key(binding.transport_identity) == _principal_key(identity)
            )
        ]
        if not matches and disabled:
            raise AccessDeniedError("disabled_principal_binding", audit)
        binding = _single_match(matches, "principal_binding", audit)
        self._require_known_authority(binding.role_id, binding.profile_id, binding.conversation_scope, audit)
        return self._context_from_binding(
            binding,
            delivery_target=_delivery_target_from_principal_identity(identity)
            if identity.thread_id is not None
            else None,
        )

    def _resolve_shared_room(self, identity: TransportIdentity, audit: RedactedAuditMetadata) -> ResolvedAccessContext:
        binding, parent_match = self._select_shared_scope_binding(identity, audit)
        member = ParticipantIdentity(identity.platform, identity.account, identity.user_id)
        if member.key() not in {participant.key() for participant in binding.participant_identities}:
            raise AccessDeniedError("participant_not_member", audit)
        self._require_known_authority(binding.role_id, binding.profile_id, binding.conversation_scope, audit)
        return self._context_from_binding(
            binding,
            delivery_target=_delivery_target_from_room_identity(identity) if parent_match else None,
        )

    def _select_shared_scope_binding(
        self,
        identity: TransportIdentity,
        audit: RedactedAuditMetadata,
        *,
        allow_missing: bool = False,
    ) -> Optional[tuple[SharedScopeBinding, bool]]:
        exact = [
            binding
            for binding in self.shared_scope_bindings
            if (
                binding.active
                and _valid_room_identity(binding.room_identity)
                and _room_key(binding.room_identity) == _room_key(identity)
            )
        ]
        if exact:
            return _single_match(exact, "shared_scope_binding", audit), False
        if identity.thread_id is not None:
            parent = [
                binding
                for binding in self.shared_scope_bindings
                if (
                    binding.active
                    and _valid_room_identity(binding.room_identity)
                    and binding.room_identity.thread_id is None
                    and _room_parent_key(binding.room_identity) == _room_parent_key(identity)
                )
            ]
            if parent:
                return _single_match(parent, "shared_scope_binding", audit), True
        if allow_missing:
            return None
        raise AccessDeniedError("missing_shared_scope_binding", audit)

    def _validate_derived_shared_context(
        self,
        context: ResolvedAccessContext,
        audit: RedactedAuditMetadata,
    ) -> Optional[ResolvedAccessContext]:
        target = context.delivery_target
        if target.peer_kind == "dm" or target.thread_id is None:
            return None
        identity = TransportIdentity(
            platform=target.platform,
            account=target.account,
            peer_kind=target.peer_kind,
            user_id="validation-only",
            chat_id=target.chat_id,
            thread_id=target.thread_id,
        )
        selected = self._select_shared_scope_binding(identity, audit, allow_missing=True)
        if selected is None:
            return None
        binding, parent_match = selected
        expected = self._context_from_binding(
            binding,
            delivery_target=_delivery_target_from_room_identity(identity) if parent_match else None,
        )
        return expected if expected == context else None

    def _validate_derived_principal_context(
        self,
        context: ResolvedAccessContext,
        audit: RedactedAuditMetadata,
    ) -> Optional[ResolvedAccessContext]:
        target = context.delivery_target
        if target.peer_kind != "dm" or target.thread_id is None:
            return None
        identity = TransportIdentity(
            platform=target.platform,
            account=target.account,
            peer_kind=target.peer_kind,
            user_id=target.chat_id,
            chat_id=target.chat_id,
            thread_id=target.thread_id,
        )
        matches = [
            binding
            for binding in self.principal_bindings
            if (
                binding.active
                and _valid_principal_binding_identity(binding.transport_identity)
                and _principal_key(binding.transport_identity) == _principal_key(identity)
            )
        ]
        if not matches:
            return None
        binding = _single_match(matches, "principal_binding", audit)
        expected = self._context_from_binding(
            binding,
            delivery_target=_delivery_target_from_principal_identity(identity),
        )
        return expected if expected == context else None

    def _context_from_binding(
        self,
        binding: PrincipalBinding | SharedScopeBinding,
        *,
        delivery_target: Optional[DeliveryTarget] = None,
    ) -> ResolvedAccessContext:
        target = delivery_target or binding.delivery_target
        return ResolvedAccessContext(
            principal_id=binding.principal_id,
            role_id=binding.role_id,
            profile_id=binding.profile_id,
            conversation_scope=binding.conversation_scope,
            capabilities=self.effective_capabilities(
                binding.role_id,
                binding.conversation_scope,
            ),
            delivery_target=target,
        )

    def _active_resolved_contexts(self) -> tuple[ResolvedAccessContext, ...]:
        contexts: list[ResolvedAccessContext] = []
        for binding in self.principal_bindings + self.shared_scope_bindings:
            if isinstance(binding, (PrincipalBinding, SharedScopeBinding)) and binding.active:
                try:
                    contexts.append(self._context_from_binding(binding))
                except AccessDeniedError:
                    continue
        return tuple(contexts)

    def _require_known_authority(
        self,
        role_id: str,
        profile_id: str,
        conversation_scope: str,
        audit: RedactedAuditMetadata,
    ) -> None:
        role = self._role_for_id(role_id)
        if not isinstance(role, RolePolicy) or not role.active:
            raise AccessDeniedError("unknown_role", audit)
        if profile_id not in self.profiles:
            raise AccessDeniedError("unknown_profile", audit)
        if conversation_scope not in self.scope_capabilities:
            raise AccessDeniedError("unknown_conversation_scope", audit)

    def _validate_binding_authority(
        self,
        principal_id: str,
        role_id: str,
        profile_id: str,
        conversation_scope: str,
        add: Any,
    ) -> None:
        if not all(_is_nonempty_str(value) for value in (principal_id, role_id, profile_id, conversation_scope)):
            add("malformed_binding_authority")
        if self._role_for_id(role_id) is None:
            add("unknown_role")
        if profile_id not in self.profiles:
            add("unknown_profile")
        if conversation_scope not in self.scope_capabilities:
            add("unknown_conversation_scope")


def _principal_key(identity: TransportIdentity) -> tuple[str, str, str, str]:
    return (identity.platform, identity.account, identity.peer_kind, identity.user_id)


def _room_key(identity: TransportIdentity) -> tuple[str, str, str, str, Optional[str]]:
    return (
        identity.platform,
        identity.account,
        identity.peer_kind,
        identity.chat_id,
        identity.thread_id,
    )


def _room_parent_key(identity: TransportIdentity) -> tuple[str, str, str, str]:
    return (identity.platform, identity.account, identity.peer_kind, identity.chat_id)


def _valid_principal_binding_identity(identity: Any) -> bool:
    return isinstance(identity, TransportIdentity) and all(
        _is_nonempty_str(value)
        for value in (identity.platform, identity.account, identity.peer_kind, identity.user_id, identity.chat_id)
    )


def _valid_room_identity(identity: Any) -> bool:
    return isinstance(identity, TransportIdentity) and all(
        _is_nonempty_str(value)
        for value in (identity.platform, identity.account, identity.peer_kind, identity.chat_id)
    )


def _valid_ingress_identity(identity: Any) -> bool:
    if not isinstance(identity, TransportIdentity):
        return False
    if not all(
        _is_nonempty_str(value)
        for value in (identity.platform, identity.account, identity.peer_kind, identity.user_id, identity.chat_id)
    ):
        return False
    if identity.thread_id is not None and not _is_nonempty_str(identity.thread_id):
        return False
    return True


def _delivery_matches_identity(target: DeliveryTarget, identity: TransportIdentity) -> bool:
    return (
        isinstance(target, DeliveryTarget)
        and target.platform == identity.platform
        and target.account == identity.account
        and target.peer_kind == identity.peer_kind
        and target.chat_id == identity.chat_id
        and target.thread_id == identity.thread_id
    )


def _delivery_target_from_principal_identity(identity: TransportIdentity) -> DeliveryTarget:
    return DeliveryTarget(
        platform=identity.platform,
        account=identity.account,
        peer_kind=identity.peer_kind,
        chat_id=identity.chat_id,
        thread_id=identity.thread_id,
    )


def _delivery_target_from_room_identity(identity: TransportIdentity) -> DeliveryTarget:
    return DeliveryTarget(
        platform=identity.platform,
        account=identity.account,
        peer_kind=identity.peer_kind,
        chat_id=identity.chat_id,
        thread_id=identity.thread_id,
    )


def _single_match(matches: list[Any], category: str, audit: RedactedAuditMetadata) -> Any:
    if not matches:
        raise AccessDeniedError(f"missing_{category}", audit)
    if len(matches) > 1:
        raise AccessDeniedError(f"ambiguous_{category}", audit)
    return matches[0]
