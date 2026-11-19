"""Registry-aware identity seams for adapter ingress: one implementation, many call sites."""
from __future__ import annotations

import logging
from typing import Any, Optional

from gateway.access_runtime import has_access_registry

logger = logging.getLogger(__name__)


def registry_owns_identity(adapter: Any) -> bool:
    """A configured registry owns every ingress, so pre-identity routing must not run."""
    return has_access_registry(getattr(adapter, "gateway_runner", None))


def canonicalize(adapter: Any, source: Optional[Any]):
    """Pin the source identity, failing closed only when a registry is watching."""
    from gateway.session_identity import canonical_identity, identity_of

    if source is None:
        return None
    identity = identity_of(source)
    registered = registry_owns_identity(adapter)
    if identity is not None and not registered:
        return identity
    runner = getattr(adapter, "gateway_runner", None)
    if runner is None or not callable(getattr(runner, "_transport_owner", None)):
        return None
    try:
        return canonical_identity(
            source, runner=runner, adapter=adapter, transport_profile=adapter._owner_transport_profile())
    except Exception:
        if registered:
            source.profile_route_rejected = True
        logger.debug("%s identity resolution failed; using legacy key readers",
                     getattr(adapter, "name", adapter), exc_info=True)
        return None


def prepare_source_access(adapter: Any, owner_profile):
    """Registry-bound sources keep ``profile`` empty until the identity resolves."""
    registered = registry_owns_identity(adapter)
    return (None if registered else owner_profile), registered
