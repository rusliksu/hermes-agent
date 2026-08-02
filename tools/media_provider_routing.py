"""Fail-closed routing primitives for media-provider fallback chains."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
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
_PROFILE_REF_LENGTH = 12


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
