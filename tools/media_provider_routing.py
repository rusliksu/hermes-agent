"""Fail-closed routing primitives for media-provider fallback chains."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, cast

from gateway.access_registry import (
    ResolvedAccessContext,
    canonical_access_context_fingerprint,
    deserialize_resolved_access_context,
    serialize_resolved_access_context,
)


MediaOperation = Literal["image_generation", "stt", "tts"]


_MEDIA_OPERATIONS = frozenset({"image_generation", "stt", "tts"})
_RETRYABLE_ERROR_CLASSES = frozenset({
    "provider_unavailable",
    "auth_unavailable",
    "capability_unavailable",
    "timeout",
    "rate_limited",
    "upstream_5xx",
})
_SAFE_ERROR_CLASSES = _RETRYABLE_ERROR_CLASSES | frozenset({
    "invalid_context",
    "invalid_operation",
    "invalid_policy",
    "media_provider_unavailable",
    "provider_error",
})
_DEFAULT_MESSAGES = {
    "auth_unavailable": "media provider authentication unavailable",
    "capability_unavailable": "media provider capability unavailable",
    "invalid_context": "invalid media access context",
    "invalid_operation": "invalid media operation",
    "invalid_policy": "invalid media provider policy",
    "media_provider_unavailable": "no media provider available",
    "provider_error": "media provider failed",
    "provider_unavailable": "media provider unavailable",
    "rate_limited": "media provider rate limited",
    "timeout": "media provider timed out",
    "upstream_5xx": "media provider upstream failure",
}
_LEGACY_REQUIRED_CAPABILITIES = MappingProxyType({
    "image_generation": "image_generation",
    "stt": "attachments",
    "tts": "voice_generation",
})
_MEDIA_SECTION_BY_OPERATION = MappingProxyType({
    "image_generation": "image_gen",
    "stt": "stt",
    "tts": "tts",
})
_MEDIA_PROVIDER_IDS = MappingProxyType({
    "image_generation": frozenset({"openai-codex", "fal", "openrouter"}),
    "stt": frozenset({"local", "mistral", "openai", "elevenlabs"}),
    "tts": frozenset({"edge", "openai", "elevenlabs"}),
})
_MEDIA_SECRET_REQUIRED = MappingProxyType({
    "image_generation": frozenset(),
    "stt": frozenset({"mistral", "openai", "elevenlabs"}),
    "tts": frozenset({"openai", "elevenlabs"}),
})
_KNOWN_MEDIA_ROLES = frozenset({
    "owner",
    "family",
    "shared_room",
})
_MISSING = object()
_MEDIA_POLICY_SCHEMA = "media-policy-dry-run/v1"
_PROFILE_REF_LENGTH = 12
_MEDIA_OPERATION_ORDER: tuple[MediaOperation, ...] = (
    "image_generation",
    "stt",
    "tts",
)


def _safe_error_class(value: Any) -> str:
    if isinstance(value, str):
        candidate = value.strip().lower()
        if candidate in _SAFE_ERROR_CLASSES:
            return candidate
    return "provider_error"


def _safe_provider(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    candidate = value.strip()
    return candidate if 0 < len(candidate) <= 128 else ""


def _safe_message(value: Any, error_class: str) -> str:
    return _DEFAULT_MESSAGES[error_class]


def _require_operation(value: Any) -> MediaOperation:
    if not isinstance(value, str) or value not in _MEDIA_OPERATIONS:
        raise ValueError("invalid media operation")
    return cast(MediaOperation, value)


def _require_provider_id(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("provider id must be a nonempty string")
    return value.strip()


def _require_capability(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("required capability must be a nonempty string")
    return value.strip()


class MediaProviderError(RuntimeError):
    """A provider error whose fields are safe to expose to callers and audit."""

    def __init__(
        self,
        error_class: str,
        provider: str = "",
        message: str = "",
    ) -> None:
        self.error_class = _safe_error_class(error_class)
        self.provider = _safe_provider(provider)
        self.message = _safe_message(message, self.error_class)
        super().__init__(self.message)

    @property
    def retryable(self) -> bool:
        return self.error_class in _RETRYABLE_ERROR_CLASSES


@dataclass(frozen=True)
class MediaProviderPolicy:
    provider_order: Mapping[MediaOperation, tuple[str, ...]]
    required_capabilities: Mapping[MediaOperation, str]
    secret_references: Mapping[str, str]
    secret_required: frozenset[str]

    def __post_init__(self) -> None:
        if not isinstance(self.provider_order, Mapping):
            raise ValueError("provider_order must be a mapping")
        if not isinstance(self.required_capabilities, Mapping):
            raise ValueError("required_capabilities must be a mapping")
        if not isinstance(self.secret_references, Mapping):
            raise ValueError("secret_references must be a mapping")
        if isinstance(self.secret_required, (str, bytes)):
            raise ValueError("secret_required must be a set of provider ids")

        provider_order: dict[MediaOperation, tuple[str, ...]] = {}
        for raw_operation, raw_providers in self.provider_order.items():
            operation = _require_operation(raw_operation)
            if not isinstance(raw_providers, (list, tuple)):
                raise ValueError("provider order must be a list or tuple")
            providers: list[str] = []
            for raw_provider in raw_providers:
                provider_id = _require_provider_id(raw_provider)
                if provider_id not in providers:
                    providers.append(provider_id)
            if not providers:
                raise ValueError("provider order must not be empty")
            provider_order[operation] = tuple(providers)

        required_capabilities: dict[MediaOperation, str] = {}
        for raw_operation, raw_capability in self.required_capabilities.items():
            required_capabilities[_require_operation(raw_operation)] = _require_capability(raw_capability)
        for operation in provider_order:
            if operation not in required_capabilities:
                raise ValueError("provider order requires a capability")

        secret_references: dict[str, str] = {}
        for raw_provider, raw_reference in self.secret_references.items():
            secret_references[_require_provider_id(raw_provider)] = _require_capability(raw_reference)

        try:
            secret_required = frozenset(
                _require_provider_id(provider) for provider in self.secret_required
            )
        except TypeError as exc:
            raise ValueError("secret_required must be iterable") from exc

        object.__setattr__(self, "provider_order", MappingProxyType(provider_order))
        object.__setattr__(self, "required_capabilities", MappingProxyType(required_capabilities))
        object.__setattr__(self, "secret_references", MappingProxyType(secret_references))
        object.__setattr__(self, "secret_required", secret_required)

    @classmethod
    def legacy(
        cls,
        provider_by_operation: Mapping[MediaOperation, str],
    ) -> MediaProviderPolicy:
        if not isinstance(provider_by_operation, Mapping):
            raise ValueError("provider_by_operation must be a mapping")
        provider_order: dict[MediaOperation, tuple[str, ...]] = {}
        required_capabilities: dict[MediaOperation, str] = {}
        for raw_operation, raw_provider in provider_by_operation.items():
            operation = _require_operation(raw_operation)
            provider_order[operation] = (_require_provider_id(raw_provider),)
            required_capabilities[operation] = _LEGACY_REQUIRED_CAPABILITIES[operation]
        return cls(
            provider_order=provider_order,
            required_capabilities=required_capabilities,
            secret_references={},
            secret_required=frozenset(),
        )


@dataclass(frozen=True)
class MediaPolicyDiagnostic:
    """Safe, machine-readable finding from the media-policy dry run."""

    code: str
    path: str
    message: str
    severity: Literal["error", "warning"] = "error"

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "path": self.path,
            "message": self.message,
            "severity": self.severity,
        }


@dataclass(frozen=True)
class MediaPolicyValidation:
    """Validated policy plus a credential-safe dry-run report."""

    policy: MediaProviderPolicy | None
    report: Mapping[str, Any]
    diagnostics: tuple[MediaPolicyDiagnostic, ...] = ()

    @property
    def valid(self) -> bool:
        return bool(self.report.get("valid"))

    def as_dict(self) -> dict[str, Any]:
        return dict(self.report)


def _policy_diagnostic(
    diagnostics: list[MediaPolicyDiagnostic],
    code: str,
    path: str,
    message: str,
    *,
    severity: Literal["error", "warning"] = "error",
) -> None:
    diagnostics.append(
        MediaPolicyDiagnostic(
            code=code,
            path=path,
            message=message,
            severity=severity,
        )
    )


def _normalized_policy_provider(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def _section_for_operation(
    raw_config: Mapping[str, Any],
    operation: MediaOperation,
    diagnostics: list[MediaPolicyDiagnostic],
) -> tuple[Mapping[str, Any], str]:
    section_name = _MEDIA_SECTION_BY_OPERATION[operation]
    section = raw_config.get(section_name)
    if section is None:
        return {}, section_name
    if not isinstance(section, Mapping):
        _policy_diagnostic(
            diagnostics,
            "section_not_mapping",
            section_name,
            "media operation section must be a mapping",
        )
        return {}, section_name
    return section, section_name


def _parse_operation_fallbacks(
    section: Mapping[str, Any],
    operation: MediaOperation,
    section_name: str,
    diagnostics: list[MediaPolicyDiagnostic],
) -> tuple[tuple[str, ...] | None, Mapping[str, Any]]:
    """Parse one operation while keeping raw secret values out of diagnostics."""
    if "fallbacks" not in section:
        return None, {}

    fallback_path = f"{section_name}.fallbacks"
    raw = section.get("fallbacks")
    nested_references: Any = _MISSING
    if isinstance(raw, Mapping):
        nested_references = raw.get("secret_references", _MISSING)
        raw_candidates = (
            raw.get(operation, _MISSING),
            raw.get(section_name, _MISSING),
        )
        raw = next((candidate for candidate in raw_candidates if candidate is not _MISSING), _MISSING)
        if raw is _MISSING:
            _policy_diagnostic(
                diagnostics,
                "missing_provider_order",
                f"{fallback_path}.{operation}",
                "fallback mapping must contain an operation provider list",
            )
            return (), nested_references if nested_references is not _MISSING else {}
    if not isinstance(raw, (list, tuple)) or not raw:
        _policy_diagnostic(
            diagnostics,
            "invalid_provider_order",
            f"{fallback_path}.{operation}",
            "fallback provider order must be a non-empty list",
        )
        return (), nested_references if nested_references is not _MISSING else {}

    allowed = _MEDIA_PROVIDER_IDS[operation]
    providers: list[str] = []
    for index, item in enumerate(raw):
        provider_id = _normalized_policy_provider(item)
        item_path = f"{fallback_path}.{operation}[{index}]"
        if not provider_id:
            _policy_diagnostic(
                diagnostics,
                "invalid_provider_id",
                item_path,
                "provider id must be a non-empty string",
            )
            continue
        if provider_id not in allowed:
            _policy_diagnostic(
                diagnostics,
                "unknown_provider",
                item_path,
                f"provider is not allowed for {operation}",
            )
            continue
        if provider_id not in providers:
            providers.append(provider_id)
    if not providers:
        _policy_diagnostic(
            diagnostics,
            "empty_provider_order",
            f"{fallback_path}.{operation}",
            "fallback provider order has no allowed providers",
        )
    return tuple(providers), nested_references if nested_references is not _MISSING else {}


def _parse_secret_references(
    section: Mapping[str, Any],
    nested_references: Mapping[str, Any],
    operation: MediaOperation,
    section_name: str,
    providers: tuple[str, ...],
    diagnostics: list[MediaPolicyDiagnostic],
) -> dict[str, str]:
    references: Any = nested_references
    if references == {} and "secret_references" in section:
        references = section.get("secret_references")
    if references is None:
        references = {}
    if not isinstance(references, Mapping):
        _policy_diagnostic(
            diagnostics,
            "invalid_secret_references",
            f"{section_name}.secret_references",
            "secret references must be a mapping",
        )
        return {}

    required = _MEDIA_SECRET_REQUIRED[operation]
    normalized: dict[str, str] = {}
    for raw_provider, raw_reference in references.items():
        provider_id = _normalized_policy_provider(raw_provider)
        path = f"{section_name}.secret_references"
        if provider_id not in providers or provider_id not in required:
            _policy_diagnostic(
                diagnostics,
                "secret_reference_not_allowed",
                path,
                f"secret reference is not allowed for {operation} provider",
            )
            continue
        if not isinstance(raw_reference, str) or not raw_reference.strip():
            _policy_diagnostic(
                diagnostics,
                "invalid_secret_reference",
                path,
                "secret reference must be a non-empty opaque label",
            )
            continue
        normalized[provider_id] = raw_reference.strip()
    return normalized


def _parse_media_policy(
    raw_config: Any,
    *,
    legacy_provider_by_operation: Mapping[str, str] | None = None,
) -> tuple[MediaProviderPolicy | None, dict[MediaOperation, dict[str, Any]], tuple[MediaPolicyDiagnostic, ...]]:
    diagnostics: list[MediaPolicyDiagnostic] = []
    if not isinstance(raw_config, Mapping):
        _policy_diagnostic(
            diagnostics,
            "config_not_mapping",
            "config",
            "media policy must be a mapping",
        )
        return None, {}, tuple(diagnostics)

    legacy_overrides = legacy_provider_by_operation or {}
    provider_order: dict[MediaOperation, tuple[str, ...]] = {}
    required_capabilities: dict[MediaOperation, str] = {}
    secret_references: dict[str, str] = {}
    secret_required: set[str] = set()
    operation_details: dict[MediaOperation, dict[str, Any]] = {}
    explicit_fallback = False

    for operation in _MEDIA_OPERATION_ORDER:
        section, section_name = _section_for_operation(raw_config, operation, diagnostics)
        required_capability = _LEGACY_REQUIRED_CAPABILITIES[operation]
        required_capabilities[operation] = required_capability
        providers, nested_references = _parse_operation_fallbacks(
            section,
            operation,
            section_name,
            diagnostics,
        )
        if providers is None:
            legacy_provider = legacy_overrides.get(operation, section.get("provider"))
            normalized_legacy = _normalized_policy_provider(legacy_provider)
            if normalized_legacy:
                provider_order[operation] = (normalized_legacy,)
                report_order = [normalized_legacy]
            else:
                report_order = []
            operation_mode = "legacy"
            references = {}
        else:
            explicit_fallback = True
            if not providers:
                report_order = []
                operation_mode = "fallback"
                references = {}
            else:
                provider_order[operation] = providers
                report_order = list(providers)
                operation_mode = "fallback"
                references = _parse_secret_references(
                    section,
                    nested_references,
                    operation,
                    section_name,
                    providers,
                    diagnostics,
                )
                secret_references.update(references)
                secret_required.update(
                    provider_id
                    for provider_id in providers
                    if provider_id in _MEDIA_SECRET_REQUIRED[operation]
                )

        operation_details[operation] = {
            "mode": operation_mode,
            "provider_order": report_order,
            "required_capability": required_capability,
            "secret_reference_status": {
                provider_id: ("configured" if provider_id in references else "missing")
                for provider_id in report_order
                if provider_id in _MEDIA_SECRET_REQUIRED[operation]
            },
        }

    if any(item.severity == "error" for item in diagnostics):
        return None, operation_details, tuple(diagnostics)
    try:
        policy = MediaProviderPolicy(
            provider_order=provider_order,
            required_capabilities=required_capabilities,
            secret_references=secret_references,
            secret_required=frozenset(secret_required),
        )
    except Exception:
        _policy_diagnostic(
            diagnostics,
            "invalid_policy",
            "media_policy",
            "media provider policy failed validation",
        )
        return None, operation_details, tuple(diagnostics)
    for details in operation_details.values():
        details["mode"] = "fallback" if explicit_fallback and details["mode"] == "fallback" else details["mode"]
    return policy, operation_details, tuple(diagnostics)


def parse_media_provider_policy(
    raw_config: Mapping[str, Any],
    *,
    legacy_provider_by_operation: Mapping[str, str] | None = None,
) -> MediaProviderPolicy:
    """Parse server-side YAML/config data into a validated media policy.

    The parser never reads files, environment credentials, or profile state.
    Missing ``fallbacks`` is deliberately represented as a legacy single
    provider, so existing adapter dispatch remains unchanged.
    """
    policy, _details, diagnostics = _parse_media_policy(
        raw_config,
        legacy_provider_by_operation=legacy_provider_by_operation,
    )
    errors = [item for item in diagnostics if item.severity == "error"]
    if policy is None or errors:
        raise ValueError(errors[0].message if errors else "invalid media provider policy")
    return policy


def _context_validation(
    context: Any,
    *,
    known_principal_ids: Iterable[str] | None = None,
    access_registry: Any = None,
) -> tuple[str, str | None, MediaPolicyDiagnostic | None]:
    if context is None:
        return "not_evaluated", None, None
    try:
        validated = _validated_context(context)
    except MediaProviderError:
        return (
            "invalid",
            None,
            MediaPolicyDiagnostic(
                code="invalid_context",
                path="context",
                message="resolved access context is malformed",
            ),
        )
    if validated.role_id not in _KNOWN_MEDIA_ROLES:
        return (
            "invalid",
            None,
            MediaPolicyDiagnostic(
                code="unknown_role",
                path="context.role_id",
                message="resolved access context role is not registered",
            ),
        )
    if known_principal_ids is not None and validated.principal_id not in set(known_principal_ids):
        return (
            "invalid",
            None,
            MediaPolicyDiagnostic(
                code="unknown_identity",
                path="context.principal_id",
                message="resolved access context principal is not registered",
            ),
        )
    if access_registry is not None:
        validator = getattr(access_registry, "validate_resolved_context", None)
        if not callable(validator):
            return (
                "invalid",
                None,
                MediaPolicyDiagnostic(
                    code="invalid_registry",
                    path="context",
                    message="access registry cannot validate the resolved context",
                ),
            )
        try:
            validator(validated)
        except Exception:
            return (
                "invalid",
                None,
                MediaPolicyDiagnostic(
                    code="unknown_identity",
                    path="context",
                    message="resolved access context is not registered",
                ),
            )
    try:
        profile_ref = canonical_access_context_fingerprint(validated)[:_PROFILE_REF_LENGTH]
    except Exception:
        return (
            "invalid",
            None,
            MediaPolicyDiagnostic(
                code="invalid_context",
                path="context",
                message="resolved access context cannot be fingerprinted",
            ),
        )
    return "valid", profile_ref, None


def validate_media_policy_config(
    raw_config: Any,
    *,
    context: ResolvedAccessContext | None = None,
    known_principal_ids: Iterable[str] | None = None,
    access_registry: Any = None,
    legacy_provider_by_operation: Mapping[str, str] | None = None,
) -> MediaPolicyValidation:
    """Return a credential-safe report without changing live configuration."""
    policy, details, parse_diagnostics = _parse_media_policy(
        raw_config,
        legacy_provider_by_operation=legacy_provider_by_operation,
    )
    diagnostics = list(parse_diagnostics)
    context_status, profile_ref, context_diagnostic = _context_validation(
        context,
        known_principal_ids=known_principal_ids,
        access_registry=access_registry,
    )
    if context_diagnostic is not None:
        diagnostics.append(context_diagnostic)

    valid_context = context_status == "valid"
    for operation, operation_details in details.items():
        required = operation_details["required_capability"]
        if context is None:
            capability_status = "not_evaluated"
        elif valid_context and required in context.capabilities:
            capability_status = "available"
        elif valid_context:
            capability_status = "unavailable"
        else:
            capability_status = "invalid_context"
        operation_details["capability_status"] = capability_status
        operation_details["providers"] = [
            {
                "provider_id": provider_id,
                "status": (
                    "legacy"
                    if operation_details["mode"] == "legacy"
                    else (
                        "capability_unavailable"
                        if capability_status == "unavailable"
                        else "invalid_context"
                        if capability_status == "invalid_context"
                        else operation_details["secret_reference_status"].get(
                            provider_id,
                            "ready" if capability_status == "available" else capability_status,
                        )
                    )
                ),
            }
            for provider_id in operation_details["provider_order"]
        ]

    report: dict[str, Any] = {
        "schema": _MEDIA_POLICY_SCHEMA,
        "valid": policy is not None
        and not any(item.severity == "error" for item in diagnostics),
        "mode": "fallback"
        if any(details_item["mode"] == "fallback" for details_item in details.values())
        else "legacy",
        "context": {
            "status": context_status,
            **({"profile_ref": profile_ref} if profile_ref else {}),
        },
        "operations": {
            operation: details[operation]
            for operation in _MEDIA_OPERATION_ORDER
        },
        "diagnostics": [item.as_dict() for item in diagnostics],
    }
    return MediaPolicyValidation(
        policy=policy,
        report=report,
        diagnostics=tuple(diagnostics),
    )


def dry_run_media_policy(
    raw_config: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    """JSON-ready alias used by CLI and synthetic policy checks."""
    return validate_media_policy_config(raw_config, **kwargs).as_dict()


@dataclass(frozen=True)
class MediaResult:
    text: str | None = None
    audio_path: str | None = None
    image_path: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.metadata, Mapping):
            raise ValueError("metadata must be a mapping")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class MediaProviderAuditEvent:
    operation: MediaOperation
    provider_id: str
    error_class: str | None
    elapsed_ms: int
    profile_ref: str
    success: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation", _require_operation(self.operation))
        object.__setattr__(self, "provider_id", _require_provider_id(self.provider_id))
        if not isinstance(self.elapsed_ms, int) or isinstance(self.elapsed_ms, bool):
            raise ValueError("elapsed_ms must be an integer")
        object.__setattr__(self, "elapsed_ms", max(0, self.elapsed_ms))
        if not isinstance(self.profile_ref, str) or not self.profile_ref:
            raise ValueError("profile_ref must be a nonempty string")
        if not isinstance(self.success, bool):
            raise ValueError("success must be a boolean")
        if self.success:
            object.__setattr__(self, "error_class", None)
        else:
            object.__setattr__(self, "error_class", _safe_error_class(self.error_class))


MediaProviderHandler = Callable[[ResolvedAccessContext, Any, Any], MediaResult]
SecretResolver = Callable[[ResolvedAccessContext, str, str], Any]
AuditSink = Callable[[MediaProviderAuditEvent], None]


def _validated_context(context: Any) -> ResolvedAccessContext:
    if not isinstance(context, ResolvedAccessContext):
        raise MediaProviderError("invalid_context")
    try:
        return deserialize_resolved_access_context(serialize_resolved_access_context(context))
    except Exception:
        raise MediaProviderError("invalid_context") from None


def _validated_operation(operation: Any) -> MediaOperation:
    try:
        return _require_operation(operation)
    except ValueError:
        raise MediaProviderError("invalid_operation") from None


def _validated_policy(policy: Any) -> MediaProviderPolicy:
    if not isinstance(policy, MediaProviderPolicy):
        raise MediaProviderError("invalid_policy")
    try:
        return MediaProviderPolicy(
            provider_order=policy.provider_order,
            required_capabilities=policy.required_capabilities,
            secret_references=policy.secret_references,
            secret_required=policy.secret_required,
        )
    except Exception:
        raise MediaProviderError("invalid_policy") from None


class MediaProviderResolver:
    """Resolve only the providers allowed by one validated access context."""

    def resolve(
        self,
        context: ResolvedAccessContext,
        operation: MediaOperation,
        policy: MediaProviderPolicy,
    ) -> tuple[str, ...]:
        return self._resolve_validated(
            _validated_context(context),
            _validated_operation(operation),
            _validated_policy(policy),
        )

    @staticmethod
    def _resolve_validated(
        context: ResolvedAccessContext,
        operation: MediaOperation,
        policy: MediaProviderPolicy,
    ) -> tuple[str, ...]:
        capability = policy.required_capabilities.get(operation)
        if capability is None or capability not in context.capabilities:
            return ()

        providers: list[str] = []
        for provider_id in policy.provider_order.get(operation, ()):
            if provider_id in policy.secret_required:
                reference = policy.secret_references.get(provider_id)
                if not isinstance(reference, str) or not reference.strip():
                    continue
            providers.append(provider_id)
        return tuple(providers)


class MediaProviderExecutor:
    """Execute a validated media-provider fallback chain once per provider."""

    def __init__(
        self,
        handlers: Mapping[str, MediaProviderHandler],
        secret_resolver: SecretResolver | None = None,
        audit_sink: AuditSink | None = None,
    ) -> None:
        if not isinstance(handlers, Mapping):
            raise ValueError("handlers must be a mapping")
        normalized_handlers: dict[str, MediaProviderHandler] = {}
        for raw_provider, handler in handlers.items():
            provider_id = _require_provider_id(raw_provider)
            if not callable(handler):
                raise ValueError("media provider handler must be callable")
            normalized_handlers[provider_id] = handler
        if secret_resolver is not None and not callable(secret_resolver):
            raise ValueError("secret_resolver must be callable")
        if audit_sink is not None and not callable(audit_sink):
            raise ValueError("audit_sink must be callable")
        self._handlers = MappingProxyType(normalized_handlers)
        self._secret_resolver = secret_resolver
        self._audit_sink = audit_sink
        self._resolver = MediaProviderResolver()

    def execute(
        self,
        operation: MediaOperation,
        context: ResolvedAccessContext,
        input_handle: Any,
        policy: MediaProviderPolicy,
    ) -> MediaResult:
        resolved_context = _validated_context(context)
        resolved_operation = _validated_operation(operation)
        resolved_policy = _validated_policy(policy)
        candidates = self._resolver._resolve_validated(
            resolved_context,
            resolved_operation,
            resolved_policy,
        )
        if not candidates:
            raise MediaProviderError("media_provider_unavailable")

        try:
            profile_ref = canonical_access_context_fingerprint(resolved_context)[:_PROFILE_REF_LENGTH]
        except Exception:
            raise MediaProviderError("invalid_context") from None

        for provider_id in candidates:
            started_at = time.monotonic()
            secret_handle: Any = None
            if provider_id in resolved_policy.secret_required:
                reference = resolved_policy.secret_references[provider_id]
                if self._secret_resolver is None:
                    secret_handle = reference
                else:
                    try:
                        secret_handle = self._secret_resolver(
                            resolved_context,
                            provider_id,
                            reference,
                        )
                    except MediaProviderError as error:
                        error = MediaProviderError(
                            error.error_class,
                            provider_id,
                            error.message,
                        )
                    except Exception:
                        error = MediaProviderError("provider_error", provider_id)
                    else:
                        empty_handle = (
                            isinstance(secret_handle, (str, bytes, bytearray))
                            and not secret_handle
                        )
                        error = (
                            MediaProviderError("auth_unavailable", provider_id)
                            if secret_handle is None or empty_handle
                            else None
                        )
                    if error is not None:
                        self._emit_audit(
                            resolved_operation,
                            provider_id,
                            error.error_class,
                            started_at,
                            profile_ref,
                            success=False,
                        )
                        if error.retryable:
                            continue
                        raise error

            handler = self._handlers.get(provider_id)
            if handler is None:
                error = MediaProviderError("provider_unavailable", provider_id)
            else:
                try:
                    result = handler(resolved_context, input_handle, secret_handle)
                    if not isinstance(result, MediaResult):
                        raise MediaProviderError("provider_error", provider_id)
                except MediaProviderError as caught:
                    error = MediaProviderError(
                        caught.error_class,
                        provider_id,
                        caught.message,
                    )
                except Exception:
                    error = MediaProviderError("provider_error", provider_id)
                else:
                    self._emit_audit(
                        resolved_operation,
                        provider_id,
                        None,
                        started_at,
                        profile_ref,
                        success=True,
                    )
                    return result

            self._emit_audit(
                resolved_operation,
                provider_id,
                error.error_class,
                started_at,
                profile_ref,
                success=False,
            )
            if error.retryable:
                continue
            raise error

        raise MediaProviderError("media_provider_unavailable")

    def _emit_audit(
        self,
        operation: MediaOperation,
        provider_id: str,
        error_class: str | None,
        started_at: float,
        profile_ref: str,
        *,
        success: bool,
    ) -> None:
        if self._audit_sink is None:
            return
        event = MediaProviderAuditEvent(
            operation=operation,
            provider_id=provider_id,
            error_class=error_class,
            elapsed_ms=max(0, int((time.monotonic() - started_at) * 1000)),
            profile_ref=profile_ref,
            success=success,
        )
        try:
            self._audit_sink(event)
        except Exception:
            pass
