"""Regression tests for multiplex profile-aware own-policy authorization."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.access_registry import DeliveryTarget, ResolvedAccessContext
from gateway.config import GatewayConfig, Platform, PlatformConfig, StreamingConfig
from gateway.session import SessionSource
from gateway.single_principal import SinglePrincipalPolicy


def _clear_auth_env(monkeypatch) -> None:
    for key in (
        "TELEGRAM_ALLOWED_USERS",
        "TELEGRAM_GROUP_ALLOWED_USERS",
        "TELEGRAM_GROUP_ALLOWED_CHATS",
        "TELEGRAM_ALLOW_BOTS",
        "TELEGRAM_ALLOW_ALL_USERS",
        "WECOM_ALLOWED_USERS",
        "GATEWAY_ALLOWED_USERS",
        "GATEWAY_ALLOW_ALL_USERS",
        "WECOM_ALLOW_ALL_USERS",
    ):
        monkeypatch.delenv(key, raising=False)


def _make_auth_runner(monkeypatch):
    from gateway.run import GatewayRunner

    _clear_auth_env(monkeypatch)

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(multiplex_profiles=True)
    runner.adapters = {}
    runner._profile_adapters = {}
    runner.pairing_store = MagicMock()
    runner.pairing_store.is_approved.return_value = False
    return runner


def _make_proxy_runner(monkeypatch):
    from gateway.run import GatewayRunner

    _clear_auth_env(monkeypatch)
    for key in ("GATEWAY_PROXY_URL", "GATEWAY_PROXY_KEY"):
        monkeypatch.delenv(key, raising=False)

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(multiplex_profiles=True, streaming=StreamingConfig(enabled=False))
    runner.adapters = {}
    runner._profile_adapters = {}
    runner._running_agents = {}
    runner._session_run_generation = {}
    runner._session_model_overrides = {}
    runner._agent_cache = {}
    runner._agent_cache_lock = None
    return runner


class _ProxyResponse:
    status = 200

    def __init__(self, payload: bytes = b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'):
        self.content = self
        self._payload = payload

    async def text(self):
        return ""

    async def iter_any(self):
        yield self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class _ProxySession:
    def __init__(self):
        self.captured_url = None
        self.captured_headers = None
        self.captured_json = None

    def post(self, url, json=None, headers=None, **_kwargs):
        self.captured_url = url
        self.captured_headers = headers
        self.captured_json = json
        return _ProxyResponse()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


def _make_multiplex_runner(monkeypatch):
    """Runner with default allowlist WeCom and secondary open-policy WeCom."""
    from gateway.run import GatewayRunner

    _clear_auth_env(monkeypatch)

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(multiplex_profiles=True)

    default_adapter = SimpleNamespace(
        send=AsyncMock(),
        enforces_own_access_policy=True,
        _dm_policy="allowlist",
        _group_policy="pairing",
    )
    secondary_adapter = SimpleNamespace(
        send=AsyncMock(),
        enforces_own_access_policy=True,
        _dm_policy="open",
        _group_policy="open",
    )

    runner.adapters = {Platform.WECOM: default_adapter}
    runner._profile_adapters = {
        "coder": {Platform.WECOM: secondary_adapter},
    }
    runner.pairing_store = MagicMock()
    runner.pairing_store.is_approved.return_value = False
    return runner, default_adapter, secondary_adapter


def _coder_wecom_dm_context(profile_id: str = "coder") -> ResolvedAccessContext:
    return ResolvedAccessContext(
        principal_id="principal-family",
        role_id="family_standard",
        profile_id=profile_id,
        conversation_scope="private",
        capabilities=frozenset({"chat"}),
        delivery_target=DeliveryTarget(
            platform=Platform.WECOM.value,
            account="corp-coder",
            peer_kind="dm",
            chat_id="wecom-dm-chat",
        ),
    )


def _typed_telegram_context() -> ResolvedAccessContext:
    return ResolvedAccessContext(
        principal_id="principal-synthetic",
        role_id="family_standard",
        profile_id="family-synthetic",
        conversation_scope="private",
        capabilities=frozenset({"chat"}),
        delivery_target=DeliveryTarget(
            platform=Platform.TELEGRAM.value,
            account="bot-synthetic",
            peer_kind="dm",
            chat_id="synthetic-user",
        ),
    )


def _telegram_source(
    *,
    user_id: str | None = "synthetic-user",
    chat_id: str = "synthetic-user",
    chat_type: str = "dm",
    resolved_access_context: ResolvedAccessContext | None = None,
    is_bot: bool = False,
) -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        user_id=user_id,
        chat_id=chat_id,
        user_name="synthetic",
        chat_type=chat_type,
        is_bot=is_bot,
        resolved_access_context=resolved_access_context,
    )


def _single_principal_policy() -> SinglePrincipalPolicy:
    return SinglePrincipalPolicy.from_dict(
        {
            "enabled": True,
            "telegram_owner_id": "owner-user",
            "allow_owner_bound_relay": True,
        }
    )


@pytest.mark.parametrize(
    "env",
    [
        {"GATEWAY_ALLOW_ALL_USERS": "true"},
        {"TELEGRAM_ALLOW_ALL_USERS": "true"},
        {"TELEGRAM_ALLOWED_USERS": "synthetic-user"},
    ],
)
def test_typed_context_ignores_process_global_dm_auth_env(monkeypatch, env):
    runner = _make_auth_runner(monkeypatch)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    assert runner._is_user_authorized(
        _telegram_source(resolved_access_context=_typed_telegram_context())
    ) is False


def test_typed_context_ignores_process_group_allowlists(monkeypatch):
    runner = _make_auth_runner(monkeypatch)
    monkeypatch.setenv("TELEGRAM_GROUP_ALLOWED_CHATS", "synthetic-group")
    monkeypatch.setenv("TELEGRAM_GROUP_ALLOWED_USERS", "synthetic-user")

    assert runner._is_user_authorized(
        _telegram_source(
            chat_id="synthetic-group",
            chat_type="group",
            resolved_access_context=_typed_telegram_context(),
        )
    ) is False


def test_typed_context_ignores_process_allow_bots(monkeypatch):
    runner = _make_auth_runner(monkeypatch)
    monkeypatch.setenv("TELEGRAM_ALLOW_BOTS", "all")

    assert runner._is_user_authorized(
        _telegram_source(
            user_id=None,
            is_bot=True,
            resolved_access_context=_typed_telegram_context(),
        )
    ) is False


def test_typed_context_uses_scoped_auth_provider(monkeypatch):
    from agent.secret_scope import reset_secret_scope, set_secret_scope

    runner = _make_auth_runner(monkeypatch)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "other-process-user")
    token = set_secret_scope({"TELEGRAM_ALLOWED_USERS": "synthetic-user"})
    try:
        assert runner._is_user_authorized(
            _telegram_source(resolved_access_context=_typed_telegram_context())
        ) is True
    finally:
        reset_secret_scope(token)


def test_legacy_no_context_still_uses_process_auth_env(monkeypatch):
    runner = _make_auth_runner(monkeypatch)

    monkeypatch.setenv("GATEWAY_ALLOW_ALL_USERS", "true")
    assert runner._is_user_authorized(_telegram_source()) is True

    monkeypatch.delenv("GATEWAY_ALLOW_ALL_USERS", raising=False)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "synthetic-user")
    assert runner._is_user_authorized(_telegram_source()) is True

    monkeypatch.delenv("TELEGRAM_ALLOWED_USERS", raising=False)
    monkeypatch.setenv("TELEGRAM_GROUP_ALLOWED_CHATS", "synthetic-group")
    assert runner._is_user_authorized(
        _telegram_source(user_id=None, chat_id="synthetic-group", chat_type="group")
    ) is True

    monkeypatch.delenv("TELEGRAM_GROUP_ALLOWED_CHATS", raising=False)
    monkeypatch.setenv("TELEGRAM_ALLOW_BOTS", "all")
    assert runner._is_user_authorized(_telegram_source(user_id=None, is_bot=True)) is True


@pytest.mark.asyncio
async def test_typed_context_ignores_poisoned_process_proxy_url_and_key(monkeypatch):
    runner = _make_proxy_runner(monkeypatch)
    source = _telegram_source(resolved_access_context=_typed_telegram_context())
    monkeypatch.setenv("GATEWAY_PROXY_URL", "http://owner-default.invalid:8642")
    monkeypatch.setenv("GATEWAY_PROXY_KEY", "owner-default-key")
    runner._run_agent_via_proxy = AsyncMock()

    with patch("gateway.run._load_gateway_config", return_value={}):
        try:
            await runner._run_agent_inner("hi", "", [], source, "session")
        except Exception:
            pass

    runner._run_agent_via_proxy.assert_not_called()


@pytest.mark.asyncio
async def test_typed_context_malformed_server_bound_proxy_config_does_not_call_remote(
    monkeypatch,
):
    runner = _make_proxy_runner(monkeypatch)
    source = _telegram_source(resolved_access_context=_typed_telegram_context())
    runner._run_agent_via_proxy = AsyncMock()

    with patch(
        "gateway.run._load_gateway_config",
        return_value={"gateway": {"proxy_url": "not a url"}},
    ):
        try:
            await runner._run_agent_inner("hi", "", [], source, "session")
        except Exception:
            pass

    runner._run_agent_via_proxy.assert_not_called()


@pytest.mark.asyncio
async def test_typed_context_profile_proxy_uses_only_scoped_config_and_key(monkeypatch):
    from agent.secret_scope import reset_secret_scope, set_secret_scope

    runner = _make_proxy_runner(monkeypatch)
    source = _telegram_source(resolved_access_context=_typed_telegram_context())
    monkeypatch.setenv("GATEWAY_PROXY_URL", "http://owner-default.invalid:8642")
    monkeypatch.setenv("GATEWAY_PROXY_KEY", "owner-default-key")
    session = _ProxySession()
    secret_token = set_secret_scope({"GATEWAY_PROXY_KEY": "scoped-profile-key"})
    try:
        with patch(
            "gateway.run._load_gateway_config",
            return_value={"gateway": {"proxy_url": "http://profile-proxy:8642/"}},
        ), patch("aiohttp.ClientSession", return_value=session), patch("aiohttp.ClientTimeout"):
            result = await runner._run_agent_via_proxy(
                message="hi",
                context_prompt="",
                history=[],
                source=source,
                session_id="typed-session",
            )
    finally:
        reset_secret_scope(secret_token)

    assert result["final_response"] == "ok"
    assert session.captured_url == "http://profile-proxy:8642/v1/chat/completions"
    assert session.captured_headers["Authorization"] == "Bearer scoped-profile-key"
    assert session.captured_headers["X-Hermes-Session-Id"] == "typed-session"


@pytest.mark.asyncio
async def test_typed_context_proxy_secret_provider_error_fail_closed(monkeypatch):
    runner = _make_proxy_runner(monkeypatch)
    source = _telegram_source(resolved_access_context=_typed_telegram_context())
    runner._run_agent_via_proxy = AsyncMock()

    def _boom(_name, _default=None):
        raise RuntimeError("scoped provider unavailable")

    with patch(
        "gateway.run._load_gateway_config",
        return_value={"gateway": {"proxy_url": "http://profile-proxy:8642"}},
    ), patch("agent.secret_scope.get_secret", side_effect=_boom):
        try:
            await runner._run_agent_inner("hi", "", [], source, "session")
        except Exception:
            pass

    runner._run_agent_via_proxy.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_no_context_proxy_env_parity_preserved(monkeypatch):
    runner = _make_proxy_runner(monkeypatch)
    runner.config.multiplex_profiles = False
    source = _telegram_source()
    monkeypatch.setenv("GATEWAY_PROXY_URL", "http://legacy-env-proxy:8642")
    monkeypatch.setenv("GATEWAY_PROXY_KEY", "legacy-env-key")
    session = _ProxySession()

    with patch(
        "gateway.run._load_gateway_config",
        return_value={"gateway": {"proxy_url": "http://profile-proxy:8642"}},
    ), patch("aiohttp.ClientSession", return_value=session), patch("aiohttp.ClientTimeout"):
        await runner._run_agent_via_proxy(
            message="hi",
            context_prompt="",
            history=[],
            source=source,
            session_id="legacy-session",
        )

    assert session.captured_url == "http://legacy-env-proxy:8642/v1/chat/completions"
    assert session.captured_headers["Authorization"] == "Bearer legacy-env-key"


def test_secondary_open_policy_not_authorized_by_default_allowlist(monkeypatch):
    """Secondary-profile open intake must not inherit default allowlist trust."""
    runner, _default_adapter, _secondary_adapter = _make_multiplex_runner(monkeypatch)

    source = SessionSource(
        platform=Platform.WECOM,
        user_id="attacker",
        chat_id="dm-chat",
        user_name="attacker",
        chat_type="dm",
        profile="coder",
    )

    assert runner._adapter_dm_policy(Platform.WECOM, profile="coder") == "open"
    assert runner._adapter_dm_policy(Platform.WECOM) == "allowlist"
    assert runner._is_user_authorized(source) is False


def test_default_profile_still_trusts_own_allowlist(monkeypatch):
    """Default-profile allowlist trust is unchanged when profile is unstamped."""
    runner, _default_adapter, _secondary_adapter = _make_multiplex_runner(monkeypatch)

    source = SessionSource(
        platform=Platform.WECOM,
        user_id="allowed-user",
        chat_id="dm-chat",
        user_name="allowed-user",
        chat_type="dm",
        profile=None,
    )

    assert runner._is_user_authorized(source) is True


def test_secondary_allowlist_still_authorized(monkeypatch):
    """Secondary profile with allowlist policy is trusted on its own adapter."""
    runner, _default_adapter, secondary_adapter = _make_multiplex_runner(monkeypatch)
    secondary_adapter._dm_policy = "allowlist"

    source = SessionSource(
        platform=Platform.WECOM,
        user_id="allowed-user",
        chat_id="dm-chat",
        user_name="allowed-user",
        chat_type="dm",
        profile="coder",
    )

    assert runner._is_user_authorized(source) is True


def test_adapter_for_source_resolves_secondary_profile_adapter(monkeypatch):
    """Ingress adapter lookup must use the stamped profile's adapter map."""
    runner, default_adapter, secondary_adapter = _make_multiplex_runner(monkeypatch)

    source = SessionSource(
        platform=Platform.WECOM,
        user_id="attacker",
        chat_id="dm-chat",
        user_name="attacker",
        chat_type="dm",
        profile="coder",
    )

    assert runner._adapter_for_source(source) is secondary_adapter
    assert runner._adapter_for_source(
        SessionSource(
            platform=Platform.WECOM,
            user_id="allowed-user",
            chat_id="dm-chat",
            user_name="allowed-user",
            chat_type="dm",
            profile=None,
        )
    ) is default_adapter


def test_adapter_for_source_uses_typed_context_profile_when_source_profile_missing(monkeypatch):
    """Typed access context profile must route replies to the secondary adapter."""
    runner, _default_adapter, secondary_adapter = _make_multiplex_runner(monkeypatch)

    source = SessionSource(
        platform=Platform.WECOM,
        user_id="coder-user",
        chat_id="wecom-dm-chat",
        user_name="coder-user",
        chat_type="dm",
        profile=None,
        resolved_access_context=_coder_wecom_dm_context(),
    )

    assert runner._adapter_for_source(source) is secondary_adapter


def test_typed_authz_no_default_relay(monkeypatch):
    runner, default_adapter, secondary_adapter = _make_multiplex_runner(monkeypatch)
    policy = _single_principal_policy()
    runner.config.single_principal = policy
    runner._single_principal_policy = policy
    default_adapter.authorization_is_upstream = True
    secondary_adapter.authorization_is_upstream = False

    source = SessionSource(
        platform=Platform.WECOM,
        user_id="family-user",
        chat_id="wecom-dm-chat",
        user_name="family-user",
        chat_type="dm",
        profile=None,
        resolved_access_context=_coder_wecom_dm_context(),
    )

    assert runner._is_user_authorized(source) is False


def test_typed_elevated_no_default_relay(monkeypatch):
    runner, default_adapter, secondary_adapter = _make_multiplex_runner(monkeypatch)
    policy = _single_principal_policy()
    runner.config.single_principal = policy
    runner._single_principal_policy = policy
    default_adapter.authorization_is_upstream = True
    secondary_adapter.authorization_is_upstream = False

    source = SessionSource(
        platform=Platform.WECOM,
        user_id="family-user",
        chat_id="wecom-dm-chat",
        user_name="family-user",
        chat_type="dm",
        profile=None,
        resolved_access_context=_coder_wecom_dm_context(),
    )

    assert runner._is_elevated_user_authorized(source) is False


def test_adapter_for_source_rejects_source_profile_mismatch_with_typed_context(monkeypatch):
    """A source stamp that contradicts typed context must not fall back to default."""
    runner, _default_adapter, _secondary_adapter = _make_multiplex_runner(monkeypatch)

    source = SessionSource(
        platform=Platform.WECOM,
        user_id="coder-user",
        chat_id="wecom-dm-chat",
        user_name="coder-user",
        chat_type="dm",
        profile="default",
        resolved_access_context=_coder_wecom_dm_context(),
    )

    assert runner._adapter_for_source(source) is None


def test_adapter_for_source_rejects_default_typed_context_without_source_profile(monkeypatch):
    """A typed default profile must not fall back to the unstamped adapter."""
    runner, _default_adapter, _secondary_adapter = _make_multiplex_runner(monkeypatch)

    source = SessionSource(
        platform=Platform.WECOM,
        user_id="coder-user",
        chat_id="wecom-dm-chat",
        user_name="coder-user",
        chat_type="dm",
        profile=None,
        resolved_access_context=_coder_wecom_dm_context(profile_id="default"),
    )

    adapter = runner._adapter_for_source(source)

    assert adapter is None


def test_secondary_allowlist_dm_behavior_ignores_unauthorized(monkeypatch):
    """Unauthorized-DM behavior must read the secondary adapter's dm_policy."""
    runner, _default_adapter, secondary_adapter = _make_multiplex_runner(monkeypatch)
    secondary_adapter._dm_policy = "allowlist"

    assert runner._get_unauthorized_dm_behavior(
        Platform.WECOM,
        profile="coder",
    ) == "ignore"
    assert runner._get_unauthorized_dm_behavior(Platform.WECOM) == "ignore"


def test_adapter_auth_check_stamps_secondary_profile(monkeypatch):
    """The adapter auth-check callback must stamp its own secondary profile.

    Regression for the gap where ``_make_adapter_auth_check`` built a
    profile-less ``SessionSource``, so a secondary adapter's external-context
    authorization (e.g. Slack/Discord thread-reply lookups) silently
    resolved the *active* profile's allowlist scope instead of its own.
    """
    from gateway.run import GatewayRunner

    _clear_auth_env(monkeypatch)

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(multiplex_profiles=True)

    captured: dict = {}

    def fake_is_user_authorized(source):
        captured["profile"] = source.profile
        return True

    runner._is_user_authorized = fake_is_user_authorized

    check = runner._make_adapter_auth_check(Platform.WECOM, profile_name="coder")
    assert check("some-user", "dm", "dm-chat") is True
    assert captured["profile"] == "coder"


def test_adapter_auth_check_defaults_to_active_profile(monkeypatch):
    """Primary-adapter callbacks (no profile_name) still resolve the active profile."""
    from gateway.run import GatewayRunner

    _clear_auth_env(monkeypatch)

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(multiplex_profiles=True)

    captured: dict = {}

    def fake_is_user_authorized(source):
        captured["profile"] = source.profile
        return True

    runner._is_user_authorized = fake_is_user_authorized

    check = runner._make_adapter_auth_check(Platform.WECOM)
    assert check("some-user", "dm", "dm-chat") is True
    assert captured["profile"] is None


def test_secondary_open_policy_fails_startup_guard(monkeypatch):
    """Secondary profiles must pass the same open-policy startup guard."""
    from gateway.run import _own_policy_open_startup_violation

    _clear_auth_env(monkeypatch)

    secondary_cfg = GatewayConfig(multiplex_profiles=True)
    secondary_cfg.platforms = {
        Platform.WECOM: PlatformConfig(
            enabled=True,
            extra={"dm_policy": "open"},
        ),
    }

    violation = _own_policy_open_startup_violation(secondary_cfg)
    assert violation is not None
    assert "wecom" in violation
    assert "open policy" in violation
