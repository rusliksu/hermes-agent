"""Strict server-owned Gurra access registry schema; current live contract.

Kept separate from gateway config facade while preserving existing YAML input.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from gateway.access_registry import AccessRegistry


class AccessRegistryConfigError(ValueError):
    """Raised for unsafe access_registry config without echoing raw identities."""

    def __init__(self, path: str, expected: str):
        super().__init__(
            f"invalid access_registry schema at {path}: expected {expected}"
        )


def _access_registry_schema_error(
    path: str,
    expected: str,
) -> AccessRegistryConfigError:
    return AccessRegistryConfigError(path, expected)


def _require_mapping(value: Any, path: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise _access_registry_schema_error(path, "mapping")
    return value


def _require_keys(
    data: Mapping,
    path: str,
    required: set[str],
    optional: set[str] = frozenset(),
) -> None:
    keys = set(data.keys())
    if missing := required - keys:
        field = min(missing)
        raise _access_registry_schema_error(f"{path}.{field}", "required field")
    if keys - required - optional:
        raise _access_registry_schema_error(f"{path}.<unknown>", "known field")


def _require_str(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _access_registry_schema_error(path, "nonempty string")
    return value


def _optional_str(value: Any, path: str) -> Optional[str]:
    if value is None:
        return None
    return _require_str(value, path)


def _require_bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise _access_registry_schema_error(path, "boolean")
    return value


def _require_str_list(value: Any, path: str) -> list[str]:
    if not isinstance(value, list):
        raise _access_registry_schema_error(path, "list[str]")
    return [_require_str(item, f"{path}[]") for item in value]


def parse_access_registry(raw: Any) -> AccessRegistry:
    from gateway.access_registry import (
        AccessRegistry,
        DeliveryTarget,
        ParticipantIdentity,
        PrincipalBinding,
        RegistryValidationError,
        RolePolicy,
        SharedScopeBinding,
        TransportIdentity,
    )

    data = _require_mapping(raw, "access_registry")
    _require_keys(
        data,
        "access_registry",
        {
            "roles",
            "profiles",
            "scope_capabilities",
            "backend_capabilities",
            "principal_bindings",
            "shared_scope_bindings",
        },
    )

    roles_data = _require_mapping(data["roles"], "access_registry.roles")
    roles = {}
    for role_id, role_data in roles_data.items():
        role = _require_mapping(role_data, "access_registry.roles.<role>")
        _require_keys(
            role,
            "access_registry.roles.<role>",
            {"capabilities"},
            {"active"},
        )
        role_id_str = _require_str(role_id, "access_registry.roles.<role>")
        roles[role_id_str] = RolePolicy(
            role_id=role_id_str,
            capabilities=frozenset(
                _require_str_list(
                    role["capabilities"],
                    "access_registry.roles.<role>.capabilities",
                )
            ),
            active=_require_bool(
                role.get("active", True),
                "access_registry.roles.<role>.active",
            ),
        )

    def transport_identity(
        value: Any,
        path: str,
        *,
        require_user: bool,
    ) -> TransportIdentity:
        item = _require_mapping(value, path)
        _require_keys(
            item,
            path,
            {"platform", "account", "peer_kind", "chat_id"},
            {"user_id", "thread_id"},
        )
        user_id = item.get("user_id")
        if require_user or user_id is not None:
            user_id = _require_str(user_id, f"{path}.user_id")
        return TransportIdentity(
            platform=_require_str(item["platform"], f"{path}.platform"),
            account=_require_str(item["account"], f"{path}.account"),
            peer_kind=_require_str(item["peer_kind"], f"{path}.peer_kind"),
            user_id=user_id,
            chat_id=_require_str(item["chat_id"], f"{path}.chat_id"),
            thread_id=_optional_str(item.get("thread_id"), f"{path}.thread_id"),
        )

    def delivery_target(value: Any, path: str) -> DeliveryTarget:
        item = _require_mapping(value, path)
        _require_keys(
            item,
            path,
            {"platform", "account", "peer_kind", "chat_id"},
            {"thread_id"},
        )
        return DeliveryTarget(
            platform=_require_str(item["platform"], f"{path}.platform"),
            account=_require_str(item["account"], f"{path}.account"),
            peer_kind=_require_str(item["peer_kind"], f"{path}.peer_kind"),
            chat_id=_require_str(item["chat_id"], f"{path}.chat_id"),
            thread_id=_optional_str(item.get("thread_id"), f"{path}.thread_id"),
        )

    def participant_identity(value: Any, path: str) -> ParticipantIdentity:
        item = _require_mapping(value, path)
        _require_keys(item, path, {"platform", "account", "user_id"})
        return ParticipantIdentity(
            platform=_require_str(item["platform"], f"{path}.platform"),
            account=_require_str(item["account"], f"{path}.account"),
            user_id=_require_str(item["user_id"], f"{path}.user_id"),
        )

    principal_bindings_data = data["principal_bindings"]
    if not isinstance(principal_bindings_data, list):
        raise _access_registry_schema_error(
            "access_registry.principal_bindings",
            "list",
        )
    principal_bindings = []
    for binding_data in principal_bindings_data:
        path = "access_registry.principal_bindings[]"
        binding = _require_mapping(binding_data, path)
        _require_keys(
            binding,
            path,
            {
                "principal_id",
                "role_id",
                "profile_id",
                "transport_identity",
                "conversation_scope",
                "delivery_target",
            },
            {"active"},
        )
        principal_bindings.append(
            PrincipalBinding(
                principal_id=_require_str(
                    binding["principal_id"],
                    f"{path}.principal_id",
                ),
                role_id=_require_str(binding["role_id"], f"{path}.role_id"),
                profile_id=_require_str(binding["profile_id"], f"{path}.profile_id"),
                transport_identity=transport_identity(
                    binding["transport_identity"],
                    f"{path}.transport_identity",
                    require_user=True,
                ),
                conversation_scope=_require_str(
                    binding["conversation_scope"],
                    f"{path}.conversation_scope",
                ),
                delivery_target=delivery_target(
                    binding["delivery_target"],
                    f"{path}.delivery_target",
                ),
                active=_require_bool(binding.get("active", True), f"{path}.active"),
            )
        )

    shared_scope_bindings_data = data["shared_scope_bindings"]
    if not isinstance(shared_scope_bindings_data, list):
        raise _access_registry_schema_error(
            "access_registry.shared_scope_bindings",
            "list",
        )
    shared_scope_bindings = []
    for binding_data in shared_scope_bindings_data:
        path = "access_registry.shared_scope_bindings[]"
        binding = _require_mapping(binding_data, path)
        _require_keys(
            binding,
            path,
            {
                "principal_id",
                "role_id",
                "profile_id",
                "room_identity",
                "conversation_scope",
                "delivery_target",
                "participant_identities",
            },
            {"active"},
        )
        participants_raw = binding["participant_identities"]
        if not isinstance(participants_raw, list):
            raise _access_registry_schema_error(
                f"{path}.participant_identities",
                "list",
            )
        shared_scope_bindings.append(
            SharedScopeBinding(
                principal_id=_require_str(
                    binding["principal_id"],
                    f"{path}.principal_id",
                ),
                role_id=_require_str(binding["role_id"], f"{path}.role_id"),
                profile_id=_require_str(binding["profile_id"], f"{path}.profile_id"),
                room_identity=transport_identity(
                    binding["room_identity"],
                    f"{path}.room_identity",
                    require_user=False,
                ),
                conversation_scope=_require_str(
                    binding["conversation_scope"],
                    f"{path}.conversation_scope",
                ),
                delivery_target=delivery_target(
                    binding["delivery_target"],
                    f"{path}.delivery_target",
                ),
                participant_identities=tuple(
                    participant_identity(
                        participant,
                        f"{path}.participant_identities[]",
                    )
                    for participant in participants_raw
                ),
                active=_require_bool(binding.get("active", True), f"{path}.active"),
            )
        )

    scopes_data = _require_mapping(
        data["scope_capabilities"],
        "access_registry.scope_capabilities",
    )
    registry = AccessRegistry(
        roles=roles,
        profiles=frozenset(
            _require_str_list(data["profiles"], "access_registry.profiles")
        ),
        principal_bindings=tuple(principal_bindings),
        shared_scope_bindings=tuple(shared_scope_bindings),
        scope_capabilities={
            _require_str(scope, "access_registry.scope_capabilities.<scope>"): frozenset(
                _require_str_list(caps, "access_registry.scope_capabilities.<scope>")
            )
            for scope, caps in scopes_data.items()
        },
        backend_capabilities=frozenset(
            _require_str_list(
                data["backend_capabilities"],
                "access_registry.backend_capabilities",
            )
        ),
    )
    try:
        registry.require_valid()
    except RegistryValidationError as exc:
        categories = ",".join(category for category, _ in exc.report.conflicts)
        raise AccessRegistryConfigError(
            "access_registry.<validation>",
            f"valid registry categories={categories}",
        ) from None
    return registry
