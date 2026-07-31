"""Profile-based routing for the gateway with hierarchical matching.

Allows a single Hermes instance to route specific Discord guilds/channels/threads
to different profiles — each with their own model, tools, memory, and persona.

Matching priority (most specific first):
  1. platform + chat_id + thread_id (exact thread)  — specificity 14
  2. platform + chat_id (channel route)             — specificity 6
  3. platform + guild_id (guild/server route)       — specificity 2
  4. No match                                       → default profile

Parent-chain matching:
For Discord threads and forum posts, ``parent_chat_id`` carries the
direct parent (the channel for a thread, the forum channel for a post).
Routes keyed on a channel match both direct messages and messages in
any thread/post whose parent is that channel.

Configuration (config.yaml):

    gateway:
      profile_routes:
        - name: server-default
          platform: discord
          guild_id: "YOUR_GUILD_ID"
          profile: server-profile

        - name: special-channel
          platform: discord
          guild_id: "YOUR_GUILD_ID"
          chat_id: "YOUR_CHANNEL_ID"
          profile: channel-profile

        - name: thread-route
          platform: discord
          chat_id: "YOUR_CHANNEL_ID"
          thread_id: "YOUR_THREAD_ID"
          profile: thread-profile
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import logging

logger = logging.getLogger(__name__)


class ProfileRoutingError(PermissionError):
    """Typed fail-closed routing denial with redacted categorical reason."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"profile routing denied: {reason}")


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _identity_field_error(*values: Any) -> Optional[str]:
    for value in values:
        if value is None or (isinstance(value, str) and not value.strip()):
            return "missing_exact_telegram_dm_identity"
        if not isinstance(value, str):
            return "malformed_exact_telegram_dm_identity"
    return None


@dataclass(frozen=True)
class ProfileRoute:
    """A single routing rule that maps a platform scope to a profile."""

    name: str
    platform: str
    profile: str
    guild_id: Optional[str] = None
    chat_id: Optional[str] = None
    thread_id: Optional[str] = None
    account: Optional[str] = None
    peer_kind: Optional[str] = None
    user_id: Optional[str] = None
    enabled: bool = True

    @property
    def specificity(self) -> int:
        """Higher value = more specific match."""
        s = 0
        if self.guild_id:
            s += 2
        if self.chat_id:
            s += 4
        if self.thread_id:
            s += 8
        if self.account:
            s += 16
        if self.peer_kind:
            s += 32
        if self.user_id:
            s += 64
        return s

    @property
    def is_exact_telegram_dm(self) -> bool:
        return (
            self.enabled
            and self.platform == "telegram"
            and _nonempty_str(self.account)
            and self.peer_kind == "dm"
            and _nonempty_str(self.user_id)
        )

    def matches(
        self,
        platform: str,
        guild_id: Optional[str] = None,
        chat_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        parent_chat_id: Optional[str] = None,
        account: Optional[str] = None,
        peer_kind: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> bool:
        """Return True if this route matches the given source fields.

        All configured discriminators are matched conjunctively (AND): every
        discriminator that the route declares must hold. ``chat_id`` supports
        hierarchical matching for Discord forums/threads:
        - Direct channel match: chat_id == route.chat_id
        - Thread in channel: parent_chat_id == route.chat_id
        A route declaring both ``guild_id`` and ``chat_id`` requires both to
        match (a chat match alone does not satisfy a guild constraint).
        """
        if not self.enabled:
            return False
        if self.platform != platform:
            return False
        if self.account and self.account != account:
            return False
        if self.peer_kind and self.peer_kind != peer_kind:
            return False
        if self.user_id and self.user_id != user_id:
            return False
        if self.thread_id and self.thread_id != thread_id:
            return False
        if self.chat_id and self.chat_id != chat_id and self.chat_id != parent_chat_id:
            return False
        if self.guild_id and self.guild_id != guild_id:
            return False
        return True


def parse_profile_routes(raw: Optional[List[Dict[str, Any]]]) -> List[ProfileRoute]:
    """Parse profile_routes from config.yaml into ProfileRoute objects.

    Returns routes sorted by specificity (most specific first).
    """
    if not raw:
        return []
    routes: List[ProfileRoute] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name", "")
        platform = entry.get("platform", "")
        profile = entry.get("profile", "")
        if not platform or not profile:
            logger.warning(
                "Skipping profile route %s: missing platform or profile",
                name,
            )
            continue
        # Validate profile name to prevent path traversal. Lazy import avoids a
        # circular dependency at module load time.
        try:
            from hermes_cli.profiles import (
                normalize_profile_name,
                validate_profile_name,
            )
            profile = normalize_profile_name(profile)
            validate_profile_name(profile)
        except (ValueError, ImportError):
            logger.warning("Skipping profile route %s: invalid profile name %r", name, profile)
            continue
        routes.append(
            ProfileRoute(
                name=name,
                platform=platform,
                profile=profile,
                guild_id=entry.get("guild_id"),
                chat_id=entry.get("chat_id"),
                thread_id=entry.get("thread_id"),
                account=entry.get("account"),
                peer_kind=entry.get("peer_kind"),
                user_id=entry.get("user_id"),
                enabled=entry.get("enabled", True),
            )
        )
    # Sort: most specific first so the first match wins.
    routes.sort(key=lambda r: r.specificity, reverse=True)
    logger.debug("Loaded %d profile routes (most-specific-first)", len(routes))
    return routes


def match_profile_route(
    routes: List[ProfileRoute],
    platform: str,
    guild_id: Optional[str] = None,
    chat_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    parent_chat_id: Optional[str] = None,
    account: Optional[str] = None,
    peer_kind: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Optional[ProfileRoute]:
    """Return the best-matching route, or None for no match."""
    exact_telegram_dm_routes = [route for route in routes if route.is_exact_telegram_dm]
    if exact_telegram_dm_routes and platform == "telegram":
        if peer_kind != "dm":
            if peer_kind is None or (isinstance(peer_kind, str) and not peer_kind.strip()):
                raise ProfileRoutingError("missing_exact_telegram_dm_identity")
            if not isinstance(peer_kind, str):
                raise ProfileRoutingError("malformed_exact_telegram_dm_identity")
            if peer_kind not in {"group", "supergroup", "channel", "thread", "forum"}:
                raise ProfileRoutingError("malformed_exact_telegram_dm_identity")
            return _match_legacy_profile_route(
                routes,
                platform,
                guild_id=guild_id,
                chat_id=chat_id,
                thread_id=thread_id,
                parent_chat_id=parent_chat_id,
                account=account,
                peer_kind=peer_kind,
                user_id=user_id,
            )
        seen = set()
        for route in exact_telegram_dm_routes:
            key = (route.platform, route.account, route.peer_kind, route.user_id)
            if key in seen:
                raise ProfileRoutingError("duplicate_exact_telegram_dm_route")
            seen.add(key)
        identity_error = _identity_field_error(account, user_id, chat_id)
        if identity_error:
            raise ProfileRoutingError(identity_error)
        if user_id != chat_id:
            raise ProfileRoutingError("mismatched_telegram_dm_identity")
        matches = [
            route
            for route in exact_telegram_dm_routes
            if route.matches(
                platform,
                guild_id=guild_id,
                chat_id=chat_id,
                thread_id=thread_id,
                parent_chat_id=parent_chat_id,
                account=account,
                peer_kind=peer_kind,
                user_id=user_id,
            )
        ]
        if len(matches) > 1:
            raise ProfileRoutingError("ambiguous_exact_telegram_dm_route")
        if not matches:
            raise ProfileRoutingError("unknown_exact_telegram_dm_route")
        return matches[0]

    return _match_legacy_profile_route(
        routes,
        platform,
        guild_id=guild_id,
        chat_id=chat_id,
        thread_id=thread_id,
        parent_chat_id=parent_chat_id,
        account=account,
        peer_kind=peer_kind,
        user_id=user_id,
    )


def _match_legacy_profile_route(
    routes: List[ProfileRoute],
    platform: str,
    guild_id: Optional[str] = None,
    chat_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    parent_chat_id: Optional[str] = None,
    account: Optional[str] = None,
    peer_kind: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Optional[ProfileRoute]:
    for route in routes:
        if route.matches(
            platform,
            guild_id=guild_id,
            chat_id=chat_id,
            thread_id=thread_id,
            parent_chat_id=parent_chat_id,
            account=account,
            peer_kind=peer_kind,
            user_id=user_id,
        ):
            return route
    return None
