"""Tests for gateway/profile_routing.py — profile-based routing."""

import pytest
from gateway.profile_routing import (
    ProfileRoute,
    ProfileRoutingError,
    parse_profile_routes,
    match_profile_route,
)


class TestProfileRoute:
    def test_specificity_thread(self):
        r = ProfileRoute(name="t", platform="discord", profile="p",
                         guild_id="g", chat_id="c", thread_id="t")
        assert r.specificity == 14  # 2 + 4 + 8

    def test_specificity_channel(self):
        r = ProfileRoute(name="c", platform="discord", profile="p",
                         guild_id="g", chat_id="c")
        assert r.specificity == 6  # 2 + 4

    def test_specificity_guild(self):
        r = ProfileRoute(name="g", platform="discord", profile="p",
                         guild_id="g")
        assert r.specificity == 2

    def test_specificity_minimal(self):
        r = ProfileRoute(name="m", platform="telegram", profile="p")
        assert r.specificity == 0

    def test_frozen(self):
        r = ProfileRoute(name="x", platform="discord", profile="p")
        with pytest.raises(AttributeError):
            r.name = "y"


class TestProfileRouteMatching:
    def test_exact_thread_match(self):
        r = ProfileRoute(name="t", platform="discord", profile="trader",
                         guild_id="111", chat_id="222", thread_id="333")
        assert r.matches("discord", guild_id="111", chat_id="222", thread_id="333")
        assert not r.matches("discord", guild_id="111", chat_id="222", thread_id="444")

    def test_channel_match(self):
        r = ProfileRoute(name="c", platform="discord", profile="helper",
                         chat_id="222")
        assert r.matches("discord", chat_id="222")
        assert not r.matches("discord", chat_id="333")
        assert not r.matches("telegram", chat_id="222")

    def test_guild_match(self):
        r = ProfileRoute(name="g", platform="discord", profile="server",
                         guild_id="111")
        assert r.matches("discord", guild_id="111")
        assert not r.matches("discord", guild_id="222")

    def test_disabled_route_no_match(self):
        r = ProfileRoute(name="d", platform="discord", profile="off",
                         guild_id="111", enabled=False)
        assert not r.matches("discord", guild_id="111")

    def test_guild_route_matches_any_channel_in_guild(self):
        r = ProfileRoute(name="g", platform="discord", profile="server",
                         guild_id="111")
        assert r.matches("discord", guild_id="111", chat_id="222")
        assert r.matches("discord", guild_id="111", chat_id="222", thread_id="333")

    def test_extra_fields_ignored(self):
        r = ProfileRoute(name="g", platform="discord", profile="server",
                         guild_id="111")
        assert r.matches("discord", guild_id="111", chat_id="any")

    def test_trusted_transport_fields_are_conjunctive(self):
        r = ProfileRoute(
            name="dm",
            platform="telegram",
            profile="family",
            account="bot-a",
            peer_kind="dm",
            user_id="u1",
        )
        assert r.matches("telegram", account="bot-a", peer_kind="dm", user_id="u1")
        assert not r.matches("telegram", account="bot-b", peer_kind="dm", user_id="u1")
        assert not r.matches("telegram", account="bot-a", peer_kind="group", user_id="u1")
        assert not r.matches("telegram", account="bot-a", peer_kind="dm", user_id="u2")

    def test_guild_and_chat_are_conjunctive(self):
        # A route declaring BOTH guild_id and chat_id requires both to match.
        # Regression guard: previously chat_id was checked first and returned
        # True before guild_id was ever consulted.
        r = ProfileRoute(name="gc", platform="discord", profile="scoped",
                         guild_id="111", chat_id="222")
        # Both match (direct channel) -> match
        assert r.matches("discord", guild_id="111", chat_id="222")
        # Both match via parent (thread inside the channel) -> match
        assert r.matches("discord", guild_id="111", chat_id="333", parent_chat_id="222")
        # chat matches but guild differs -> NO match (the bug this guards)
        assert not r.matches("discord", guild_id="999", chat_id="222")
        # guild matches but chat differs -> NO match
        assert not r.matches("discord", guild_id="111", chat_id="333")


class TestParseProfileRoutes:
    def test_empty(self):
        assert parse_profile_routes(None) == []
        assert parse_profile_routes([]) == []

    def test_valid_routes_sorted_by_specificity(self):
        raw = [
            {"name": "guild", "platform": "discord", "profile": "p", "guild_id": "1"},
            {"name": "thread", "platform": "discord", "profile": "p",
             "guild_id": "1", "chat_id": "2", "thread_id": "3"},
            {"name": "channel", "platform": "discord", "profile": "p", "chat_id": "2"},
        ]
        routes = parse_profile_routes(raw)
        names = [r.name for r in routes]
        assert names == ["thread", "channel", "guild"]

    def test_skips_invalid(self):
        raw = [
            {"platform": "discord"},
            {"profile": "p"},
            "not a dict",
            {"name": "ok", "platform": "telegram", "profile": "p"},
        ]
        routes = parse_profile_routes(raw)
        assert len(routes) == 1
        assert routes[0].name == "ok"

    def test_enabled_flag(self):
        raw = [
            {"name": "off", "platform": "discord", "profile": "p",
             "guild_id": "1", "enabled": False},
            {"name": "on", "platform": "discord", "profile": "p", "guild_id": "1"},
        ]
        routes = parse_profile_routes(raw)
        assert not routes[0].enabled
        assert routes[1].enabled

    def test_parses_exact_telegram_dm_fields(self):
        routes = parse_profile_routes([
            {
                "name": "family",
                "platform": "telegram",
                "profile": "family-profile",
                "account": "bot-a",
                "peer_kind": "dm",
                "user_id": "u1",
            }
        ])
        assert routes == [
            ProfileRoute(
                name="family",
                platform="telegram",
                profile="family-profile",
                account="bot-a",
                peer_kind="dm",
                user_id="u1",
            )
        ]


class TestMatchProfileRoute:
    def test_no_routes(self):
        assert match_profile_route([], "discord") is None

    def test_returns_first_match(self):
        routes = [
            ProfileRoute(name="thread", platform="discord", profile="trader",
                         guild_id="1", chat_id="2", thread_id="3"),
            ProfileRoute(name="channel", platform="discord", profile="helper",
                         chat_id="2"),
        ]
        m = match_profile_route(routes, "discord", guild_id="1", chat_id="2", thread_id="3")
        assert m is not None
        assert m.profile == "trader"

    def test_falls_through_to_channel(self):
        routes = [
            ProfileRoute(name="thread", platform="discord", profile="trader",
                         guild_id="1", chat_id="2", thread_id="3"),
            ProfileRoute(name="channel", platform="discord", profile="helper",
                         chat_id="2"),
        ]
        m = match_profile_route(routes, "discord", guild_id="1", chat_id="2")
        assert m is not None
        assert m.profile == "helper"

    def test_no_match_returns_none(self):
        routes = [
            ProfileRoute(name="r", platform="telegram", profile="p"),
        ]
        assert match_profile_route(routes, "discord") is None


class TestExactTelegramDmRouting:
    @staticmethod
    def _routes():
        return [
            ProfileRoute(
                name="family",
                platform="telegram",
                profile="family-profile",
                account="bot-a",
                peer_kind="dm",
                user_id="u1",
            )
        ]

    def test_exact_match_returns_profile_route(self):
        matched = match_profile_route(
            self._routes(),
            "telegram",
            account="bot-a",
            peer_kind="dm",
            user_id="u1",
            chat_id="u1",
        )
        assert matched is not None
        assert matched.profile == "family-profile"

    @pytest.mark.parametrize(
        "kwargs,reason",
        [
            ({"account": "bot-b", "user_id": "u1", "chat_id": "u1"}, "unknown_exact_telegram_dm_route"),
            ({"account": None, "user_id": "u1", "chat_id": "u1"}, "missing_exact_telegram_dm_identity"),
            ({"account": "bot-a", "user_id": 1, "chat_id": "1"}, "malformed_exact_telegram_dm_identity"),
            ({"account": "bot-a", "user_id": "unknown", "chat_id": "unknown"}, "unknown_exact_telegram_dm_route"),
            ({"account": "bot-a", "user_id": "u1", "chat_id": "other"}, "mismatched_telegram_dm_identity"),
        ],
    )
    def test_exact_mode_denies_bad_dm_identity(self, kwargs, reason):
        with pytest.raises(ProfileRoutingError) as exc:
            match_profile_route(self._routes(), "telegram", peer_kind="dm", **kwargs)

        assert exc.value.reason == reason
        assert "u1" not in str(exc.value)
        assert "bot-a" not in str(exc.value)

    def test_exact_mode_denies_malformed_peer_kind(self):
        for peer_kind in ("private", 1):
            with pytest.raises(ProfileRoutingError) as exc:
                match_profile_route(
                    self._routes(),
                    "telegram",
                    account="bot-a",
                    peer_kind=peer_kind,
                    user_id="u1",
                    chat_id="u1",
                )

            assert exc.value.reason == "malformed_exact_telegram_dm_identity"

    @pytest.mark.parametrize("peer_kind", [None, ""])
    def test_exact_mode_denies_missing_peer_kind(self, peer_kind):
        with pytest.raises(ProfileRoutingError) as exc:
            match_profile_route(
                self._routes(),
                "telegram",
                account="bot-a",
                peer_kind=peer_kind,
                user_id="u1",
                chat_id="u1",
            )

        assert exc.value.reason == "missing_exact_telegram_dm_identity"

    def test_exact_route_with_mismatched_extra_discriminator_denies_redacted(self):
        routes = [
            ProfileRoute(
                name="family",
                platform="telegram",
                profile="family-profile",
                account="bot-a",
                peer_kind="dm",
                user_id="u1",
                chat_id="other-chat",
            )
        ]
        with pytest.raises(ProfileRoutingError) as exc:
            match_profile_route(
                routes,
                "telegram",
                account="bot-a",
                peer_kind="dm",
                user_id="u1",
                chat_id="u1",
            )

        assert exc.value.reason == "unknown_exact_telegram_dm_route"
        assert "u1" not in str(exc.value)
        assert "bot-a" not in str(exc.value)
        assert "other-chat" not in str(exc.value)

    def test_duplicate_exact_routes_deny(self):
        routes = self._routes() + self._routes()
        with pytest.raises(ProfileRoutingError) as exc:
            match_profile_route(
                routes,
                "telegram",
                account="bot-a",
                peer_kind="dm",
                user_id="u1",
                chat_id="u1",
            )

        assert exc.value.reason == "duplicate_exact_telegram_dm_route"

    def test_no_exact_dm_routes_preserves_legacy_telegram_fallback(self):
        routes = [
            ProfileRoute(name="legacy", platform="telegram", profile="legacy", chat_id="chat-1")
        ]
        assert match_profile_route(
            routes,
            "telegram",
            account=None,
            peer_kind="dm",
            user_id="u1",
            chat_id="chat-1",
        ).profile == "legacy"

    def test_non_dm_routes_unchanged_when_exact_dm_routes_exist(self):
        routes = self._routes() + [
            ProfileRoute(name="group", platform="telegram", profile="group", chat_id="group-1")
        ]
        assert match_profile_route(
            routes,
            "telegram",
            account=None,
            peer_kind="group",
            user_id="u1",
            chat_id="group-1",
        ).profile == "group"


class TestSessionKeyIntegration:
    def test_default_profile_key(self):
        from gateway.session import build_session_key, SessionSource, Platform
        src = SessionSource(platform=Platform.DISCORD, chat_id="123",
                            chat_type="channel", user_id="456")
        key = build_session_key(src)
        assert key.startswith("agent:main:")

    def test_custom_profile_key(self):
        from gateway.session import build_session_key, SessionSource, Platform
        src = SessionSource(platform=Platform.DISCORD, chat_id="123",
                            chat_type="channel", user_id="456")
        key = build_session_key(src, profile="trader")
        assert key.startswith("agent:trader:")
        assert key == "agent:trader:discord:channel:123:456"

    def test_isolated_sessions(self):
        from gateway.session import build_session_key, SessionSource, Platform
        src = SessionSource(platform=Platform.DISCORD, chat_id="123",
                            chat_type="channel", user_id="456")
        key_default = build_session_key(src)
        key_trader = build_session_key(src, profile="trader")
        assert key_default != key_trader

    def test_dm_profile_scoped(self):
        from gateway.session import build_session_key, SessionSource, Platform
        src = SessionSource(platform=Platform.DISCORD, chat_id="999",
                            chat_type="dm", user_id="111")
        key = build_session_key(src, profile="bot2")
        assert key == "agent:bot2:discord:dm:999"



class TestParentChatIdMatching:
    """Thread messages carry thread_id as chat_id; parent_chat_id is the channel."""

    def test_channel_route_matches_via_parent_chat_id(self):
        r = ProfileRoute(name="ch", platform="discord", profile="trader",
                         chat_id="222")
        assert r.matches("discord", chat_id="333", parent_chat_id="222")

    def test_channel_route_no_match_wrong_parent(self):
        r = ProfileRoute(name="ch", platform="discord", profile="trader",
                         chat_id="222")
        assert not r.matches("discord", chat_id="333", parent_chat_id="444")

    def test_match_profile_route_with_parent_chat_id(self):
        routes = [
            ProfileRoute(name="ch", platform="discord", profile="trader",
                         chat_id="222"),
        ]
        m = match_profile_route(routes, "discord", chat_id="333", parent_chat_id="222")
        assert m is not None
        assert m.profile == "trader"

    def test_thread_id_does_not_match_parent_chat_id(self):
        """thread_id only matches the actual thread_id, never parent_chat_id.
        Discord snowflakes are globally unique, so thread_id != channel_id."""
        r = ProfileRoute(name="th", platform="discord", profile="helper",
                         thread_id="555")
        assert r.matches("discord", thread_id="555")
        assert not r.matches("discord", parent_chat_id="555")

    def test_no_parent_chat_id_still_works(self):
        r = ProfileRoute(name="ch", platform="discord", profile="trader",
                         chat_id="222")
        assert r.matches("discord", chat_id="222")

    def test_guild_route_matches_with_parent_chat_id(self):
        """Guild routes should match regardless of chat_id or parent_chat_id."""
        r = ProfileRoute(name="g", platform="discord", profile="server",
                         guild_id="111")
        assert r.matches("discord", guild_id="111", chat_id="333", parent_chat_id="444")


class TestForumPostMatching:
    """Test that forum posts match via parent_chat_id (direct parent)."""

    def test_forum_channel_route_matches_forum_post(self):
        """A route on a forum channel should match comments on posts in that forum.
        
        In Discord, forum posts (threads) have parent_chat_id = forum channel ID.
        No cache is needed — the parent relationship is direct.
        """
        r = ProfileRoute(name="forum", platform="discord", profile="forum_profile",
                         chat_id="forum_channel_123")
        # A comment on a forum post: chat_id=post_thread_id, parent_chat_id=forum_channel_id
        assert r.matches("discord", chat_id="post_thread_456", parent_chat_id="forum_channel_123")

    def test_forum_post_comment_matches_channel_not_thread_id(self):
        """Verify that thread_id matching is distinct from parent_chat_id matching."""
        routes = [
            ProfileRoute(name="forum", platform="discord", profile="forum_profile",
                         chat_id="forum_channel_123"),
            ProfileRoute(name="post", platform="discord", profile="post_profile",
                         thread_id="post_thread_456"),
        ]
        # A comment on the forum post should match the forum channel route, not the thread route
        m = match_profile_route(routes, "discord", chat_id="post_thread_456", 
                                 parent_chat_id="forum_channel_123")
        assert m is not None
        assert m.profile == "forum_profile"
