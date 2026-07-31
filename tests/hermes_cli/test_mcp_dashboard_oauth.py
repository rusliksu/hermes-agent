"""Dashboard HTTP contract for hosted MCP OAuth."""

from unittest.mock import patch

import pytest


def _client():
    import asyncio
    import httpx

    from hermes_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

    class ASGIClient:
        def __init__(self):
            self.headers = {_SESSION_HEADER_NAME: _SESSION_TOKEN}

        def request(self, method: str, url: str, **kwargs):
            async def _request():
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://testserver",
                    headers=self.headers,
                ) as client:
                    return await client.request(method, url, **kwargs)

            return asyncio.run(_request())

        def get(self, url: str, **kwargs):
            return self.request("GET", url, **kwargs)

        def post(self, url: str, **kwargs):
            return self.request("POST", url, **kwargs)

    client = ASGIClient()
    return client


@pytest.fixture(autouse=True)
def _clear_flows(tmp_path, monkeypatch):
    from hermes_cli import web_server
    from agent.secret_scope import set_multiplex_active
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    home_token = set_hermes_home_override(hermes_home)
    set_multiplex_active(False)
    web_server._mcp_oauth_flows.clear()
    web_server.app.state.auth_required = False
    yield
    web_server._mcp_oauth_flows.clear()
    web_server.app.state.auth_required = False
    set_multiplex_active(False)
    reset_hermes_home_override(home_token)


def _registry_for_profile(profile_id="family-profile", *, active=True, extra_bindings=()):
    from gateway.access_registry import (
        AccessRegistry,
        DeliveryTarget,
        PrincipalBinding,
        RolePolicy,
        TransportIdentity,
    )

    return AccessRegistry(
        roles={"family_standard": RolePolicy("family_standard", frozenset({"mcp"}))},
        profiles=frozenset({profile_id}),
        principal_bindings=(
            PrincipalBinding(
                principal_id="principal-family",
                role_id="family_standard",
                profile_id=profile_id,
                transport_identity=TransportIdentity(
                    platform="telegram",
                    account="bot-main",
                    peer_kind="dm",
                    user_id="user-family",
                    chat_id="user-family",
                ),
                conversation_scope="dm:family",
                delivery_target=DeliveryTarget(
                    platform="telegram",
                    account="bot-main",
                    peer_kind="dm",
                    chat_id="user-family",
                ),
                active=active,
            ),
        ) + tuple(extra_bindings),
        shared_scope_bindings=(),
        scope_capabilities={"dm:family": frozenset({"mcp"})},
        backend_capabilities=frozenset({"mcp"}),
    )


def test_hosted_auth_start_returns_public_authorization_url(monkeypatch):
    from hermes_cli import web_server

    client = _client()
    client.post(
        "/api/mcp/servers",
        json={"name": "reports", "url": "https://mcp.example/mcp", "auth": "oauth"},
    )

    def fake_worker(flow, cfg):
        import asyncio

        asyncio.run(flow.publish_authorization_url("https://idp.example/authorize?state=s1"))

    monkeypatch.setattr(web_server, "_run_dashboard_mcp_oauth", fake_worker)
    with patch(
        "hermes_cli.dashboard_auth.prefix.resolve_public_url",
        return_value="https://agent.example",
    ):
        response = client.post("/api/mcp/servers/reports/auth")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "authorization_required"
    assert body["authorization_url"] == "https://idp.example/authorize?state=s1"
    flow = web_server._mcp_oauth_flows[body["flow_id"]]
    assert flow.redirect_uri == "https://agent.example/api/mcp/oauth/callback/reports"


def test_hosted_callback_is_public_and_delivers_code():
    import asyncio

    from hermes_cli import web_server
    from hermes_cli.dashboard_auth.public_paths import PUBLIC_API_PATHS
    from tools.mcp_dashboard_oauth import DashboardOAuthFlow

    flow = DashboardOAuthFlow(
        flow_id="flow-public",
        server_name="reports",
        profile=None,
        hermes_home="/tmp/hermes-test",
        redirect_uri="https://agent.example/api/mcp/oauth/callback/reports",
    )
    asyncio.run(
        flow.publish_authorization_url(
            "https://idp.example/authorize?state=expected"
        )
    )
    web_server._mcp_oauth_flows[flow.flow_id] = flow

    assert "/api/mcp/oauth/callback" not in PUBLIC_API_PATHS
    response = _client().get(
        "/api/mcp/oauth/callback/reports?code=abc&state=expected"
    )
    assert response.status_code == 200
    assert flow._callback == ("abc", "expected")


def test_hosted_callback_bypasses_gated_cookie_auth(monkeypatch):
    import asyncio

    from hermes_cli import web_server
    from tools.mcp_dashboard_oauth import DashboardOAuthFlow

    flow = DashboardOAuthFlow(
        flow_id="flow-gated",
        server_name="reports",
        profile=None,
        hermes_home="/tmp/hermes-test",
        redirect_uri="https://agent.example/api/mcp/oauth/callback/reports",
    )
    asyncio.run(
        flow.publish_authorization_url(
            "https://idp.example/authorize?state=expected"
        )
    )
    web_server._mcp_oauth_flows[flow.flow_id] = flow
    monkeypatch.setattr(web_server.app.state, "auth_required", True, raising=False)

    response = _client().get(
        "/api/mcp/oauth/callback/reports?code=abc&state=expected"
    )

    assert response.status_code == 200
    assert flow._callback == ("abc", "expected")


def test_hosted_callback_rejects_wrong_state_before_waking_sdk():
    import asyncio

    from hermes_cli import web_server
    from tools.mcp_dashboard_oauth import DashboardOAuthFlow

    flow = DashboardOAuthFlow(
        flow_id="flow-state-route",
        server_name="reports",
        profile=None,
        hermes_home="/tmp/hermes-test",
        redirect_uri="https://agent.example/api/mcp/oauth/callback/reports",
    )
    asyncio.run(
        flow.publish_authorization_url(
            "https://idp.example/authorize?state=expected-state"
        )
    )
    web_server._mcp_oauth_flows[flow.flow_id] = flow

    response = _client().get(
        "/api/mcp/oauth/callback/reports?code=attacker&state=wrong"
    )
    assert response.status_code == 404
    assert flow._callback is None


def test_hosted_auth_start_bounds_pending_flow_registry():
    from hermes_cli import web_server
    from tools.mcp_dashboard_oauth import DashboardOAuthFlow

    client = _client()
    client.post(
        "/api/mcp/servers",
        json={"name": "reports", "url": "https://mcp.example/mcp", "auth": "oauth"},
    )
    for index in range(web_server._MAX_PENDING_MCP_OAUTH_FLOWS):
        flow = DashboardOAuthFlow(
            flow_id=f"existing-{index}",
            server_name="reports",
            profile=None,
            hermes_home="/tmp/hermes-test",
            redirect_uri=f"https://agent.example/callback/{index}",
        )
        web_server._mcp_oauth_flows[flow.flow_id] = flow

    response = client.post("/api/mcp/servers/reports/auth")
    assert response.status_code == 429


def test_hosted_auth_rejects_overlapping_flow_for_same_server():
    from hermes_cli import web_server
    from tools.mcp_dashboard_oauth import DashboardOAuthFlow

    client = _client()
    client.post(
        "/api/mcp/servers",
        json={"name": "reports", "url": "https://mcp.example/mcp", "auth": "oauth"},
    )
    from hermes_constants import get_hermes_home

    existing = DashboardOAuthFlow(
        flow_id="existing-reports",
        server_name="reports",
        profile="other-profile",
        hermes_home=str(get_hermes_home().expanduser().resolve(strict=False)),
        redirect_uri="https://agent.example/callback/existing",
    )
    web_server._mcp_oauth_flows[existing.flow_id] = existing

    response = client.post("/api/mcp/servers/reports/auth")

    assert response.status_code == 409
    assert "already in progress" in response.text


def test_hosted_auth_allows_same_server_name_in_different_profiles(tmp_path, monkeypatch):
    from hermes_cli import web_server
    from tools.mcp_dashboard_oauth import DashboardOAuthFlow

    profile_home = tmp_path / "profiles" / "work"
    profile_home.mkdir(parents=True)
    monkeypatch.setattr(web_server, "_resolve_profile_dir", lambda _name: profile_home)

    existing = DashboardOAuthFlow(
        flow_id="existing-default",
        server_name="reports",
        profile=None,
        hermes_home=str(tmp_path / "default"),
        redirect_uri="https://agent.example/callback/existing",
    )
    web_server._mcp_oauth_flows[existing.flow_id] = existing

    def fake_worker(flow, cfg):
        import asyncio

        asyncio.run(flow.publish_authorization_url("https://idp.example/authorize?state=work"))

    with patch("hermes_cli.mcp_config._get_mcp_servers", return_value={"reports": {"url": "https://mcp.example"}}), \
         patch.object(web_server, "_run_dashboard_mcp_oauth", fake_worker):
        response = _client().post("/api/mcp/servers/reports/auth?profile=work")

    assert response.status_code != 409


def test_callback_url_is_stable_for_a_server():
    from hermes_cli import web_server

    # The route helper's stable form must not depend on a one-time flow id.
    first = web_server._mcp_oauth_callback_url_from_base("https://agent.example", "reports")
    second = web_server._mcp_oauth_callback_url_from_base("https://agent.example", "reports")
    assert first == second == "https://agent.example/api/mcp/oauth/callback/reports"


def test_callback_route_supports_server_names_with_slashes():
    import asyncio

    from hermes_cli import web_server
    from tools.mcp_dashboard_oauth import DashboardOAuthFlow

    flow = DashboardOAuthFlow(
        flow_id="flow-slash",
        server_name="github/mcp",
        profile=None,
        hermes_home="/tmp/hermes-test",
        redirect_uri="https://agent.example/api/mcp/oauth/callback/github/mcp",
    )
    asyncio.run(flow.publish_authorization_url("https://idp.example/authorize?state=slash"))
    web_server._mcp_oauth_flows[flow.flow_id] = flow

    response = _client().get(
        "/api/mcp/oauth/callback/github/mcp?code=abc&state=slash"
    )

    assert response.status_code == 200
    assert flow._callback == ("abc", "slash")


def test_flow_status_does_not_expose_authorization_code():
    from hermes_cli import web_server
    from tools.mcp_dashboard_oauth import DashboardOAuthFlow

    flow = DashboardOAuthFlow(
        flow_id="flow-status",
        server_name="reports",
        profile=None,
        hermes_home="/tmp/hermes-test",
        redirect_uri="https://agent.example/api/mcp/oauth/callback/flow-status",
    )
    flow.authorization_url = "https://idp.example/authorize"
    flow.status = "approved"
    flow._callback = ("secret-code", "secret-state")
    web_server._mcp_oauth_flows[flow.flow_id] = flow

    response = _client().get("/api/mcp/oauth/flows/flow-status")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert "secret-code" not in response.text
    assert "secret-state" not in response.text


def test_typed_dcr_403_message_does_not_suggest_client_secret(monkeypatch, tmp_path):
    from hermes_cli import web_server
    from tools.mcp_dashboard_oauth import DashboardOAuthFlow
    from tools.mcp_tool import MCPPoolKey

    class FakeManager:
        def remove(self, *_args, **_kwargs):
            return None

        def restore_entry(self, *_args, **_kwargs):
            return None

    def fail_probe(*_args, **_kwargs):
        raise RuntimeError("403 Forbidden during client registration")

    monkeypatch.setattr("tools.mcp_oauth_manager.get_manager", lambda: FakeManager())
    monkeypatch.setattr("hermes_cli.mcp_config._probe_single_server", fail_probe)

    flow = DashboardOAuthFlow(
        flow_id="flow-typed-dcr",
        server_name="reports",
        profile="family-profile",
        hermes_home=str(tmp_path),
        redirect_uri="https://agent.example/api/mcp/oauth/callback/reports",
        pool_key=MCPPoolKey("family-profile", "dm:family"),
    )

    web_server._run_dashboard_mcp_oauth(flow, {"url": "https://mcp.example/mcp", "auth": "oauth"})

    assert flow.status == "error"
    assert "dynamic client registration" in flow.error
    assert "stdio or API-key MCP server" in flow.error
    assert "client_id" not in flow.error
    assert "client_secret" not in flow.error


def test_multiplex_auth_start_captures_exact_registry_context(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace

    from agent.secret_scope import set_multiplex_active
    from hermes_cli import web_server

    set_multiplex_active(True)
    profile_home = tmp_path / "profiles" / "family-profile"
    profile_home.mkdir(parents=True)
    registry = _registry_for_profile("family-profile")

    monkeypatch.setattr(web_server, "_resolve_profile_dir", lambda name: profile_home)
    monkeypatch.setattr(
        "gateway.config.load_gateway_config",
        lambda: SimpleNamespace(access_registry=registry),
    )
    monkeypatch.setattr(
        "hermes_cli.mcp_config._get_mcp_servers",
        lambda: {"reports": {"url": "https://mcp.example/mcp", "auth": "oauth"}},
    )

    def fake_worker(flow, cfg):
        asyncio.run(flow.publish_authorization_url("https://idp.example/authorize?state=s1"))

    monkeypatch.setattr(web_server, "_run_dashboard_mcp_oauth", fake_worker)

    response = _client().post("/api/mcp/servers/reports/auth?profile=family-profile")

    assert response.status_code == 200
    body = response.json()
    flow = web_server._mcp_oauth_flows[body["flow_id"]]
    assert flow.access_context == registry.resolve_exact_profile_context("family-profile")
    assert flow.pool_key.profile_id == "family-profile"
    assert flow.pool_key.conversation_scope == "dm:family"
    assert flow.hermes_home == str(profile_home.resolve())
    assert "family-profile" not in response.text
    assert "dm:family" not in response.text
    assert "user-family" not in response.text


def test_multiplex_auth_start_denies_missing_profile_before_worker(monkeypatch):
    from agent.secret_scope import set_multiplex_active
    from hermes_cli import web_server

    set_multiplex_active(True)
    called = {"worker": 0, "servers": 0}

    monkeypatch.setattr(
        web_server,
        "_run_dashboard_mcp_oauth",
        lambda *_a, **_k: called.__setitem__("worker", called["worker"] + 1),
    )
    monkeypatch.setattr(
        "hermes_cli.mcp_config._get_mcp_servers",
        lambda: called.__setitem__("servers", called["servers"] + 1),
    )

    response = _client().post("/api/mcp/servers/reports/auth")

    assert response.status_code == 403
    assert called == {"worker": 0, "servers": 0}


@pytest.mark.parametrize("failure_mode", ("import", "check"))
def test_multiplex_auth_start_denies_state_failure_before_profile_config_server_worker(
    monkeypatch,
    failure_mode,
):
    import sys

    from hermes_cli import web_server

    called = {"profile": 0, "gateway": 0, "servers": 0, "worker": 0}

    def fail_multiplex_state():
        raise RuntimeError("raw state/path must not leak")

    if failure_mode == "import":
        monkeypatch.setitem(sys.modules, "agent.secret_scope", None)
    else:
        monkeypatch.setattr("agent.secret_scope.is_multiplex_active", fail_multiplex_state)
    monkeypatch.setattr(
        web_server,
        "_resolve_profile_dir",
        lambda *_a, **_k: called.__setitem__("profile", called["profile"] + 1),
    )
    monkeypatch.setattr(
        "gateway.config.load_gateway_config",
        lambda: called.__setitem__("gateway", called["gateway"] + 1),
    )
    monkeypatch.setattr(
        "hermes_cli.mcp_config._get_mcp_servers",
        lambda: called.__setitem__("servers", called["servers"] + 1),
    )
    monkeypatch.setattr(
        web_server,
        "_run_dashboard_mcp_oauth",
        lambda *_a, **_k: called.__setitem__("worker", called["worker"] + 1),
    )

    response = _client().post("/api/mcp/servers/reports/auth?profile=family-profile")

    assert response.status_code == 403
    assert response.json()["detail"] == "MCP OAuth multiplex state unavailable"
    assert "raw state" not in response.text
    assert called == {"profile": 0, "gateway": 0, "servers": 0, "worker": 0}


def test_multiplex_auth_start_denies_unknown_profile_before_server_config_worker(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from agent.secret_scope import set_multiplex_active
    from hermes_cli import web_server

    set_multiplex_active(True)
    profile_home = tmp_path / "profiles" / "unknown-profile"
    called = {"servers": 0, "worker": 0}

    monkeypatch.setattr(web_server, "_resolve_profile_dir", lambda _name: profile_home)
    monkeypatch.setattr(
        "gateway.config.load_gateway_config",
        lambda: SimpleNamespace(access_registry=_registry_for_profile("family-profile")),
    )
    monkeypatch.setattr(
        "hermes_cli.mcp_config._get_mcp_servers",
        lambda: called.__setitem__("servers", called["servers"] + 1),
    )
    monkeypatch.setattr(
        web_server,
        "_run_dashboard_mcp_oauth",
        lambda *_a, **_k: called.__setitem__("worker", called["worker"] + 1),
    )

    response = _client().post("/api/mcp/servers/reports/auth?profile=unknown-profile")

    assert response.status_code == 403
    assert "missing_profile_binding" in response.text
    assert called == {"servers": 0, "worker": 0}


def test_multiplex_auth_start_denies_ambiguous_profile_before_server_config_worker(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from agent.secret_scope import set_multiplex_active
    from gateway.access_registry import DeliveryTarget, PrincipalBinding, TransportIdentity
    from hermes_cli import web_server

    set_multiplex_active(True)
    profile_home = tmp_path / "profiles" / "family-profile"
    other_binding = PrincipalBinding(
        principal_id="principal-family-other",
        role_id="family_standard",
        profile_id="family-profile",
        transport_identity=TransportIdentity(
            platform="telegram",
            account="bot-main",
            peer_kind="dm",
            user_id="user-family-other",
            chat_id="user-family-other",
        ),
        conversation_scope="dm:family",
        delivery_target=DeliveryTarget(
            platform="telegram",
            account="bot-main",
            peer_kind="dm",
            chat_id="user-family-other",
        ),
        active=True,
    )
    called = {"servers": 0, "worker": 0}

    monkeypatch.setattr(web_server, "_resolve_profile_dir", lambda _name: profile_home)
    monkeypatch.setattr(
        "gateway.config.load_gateway_config",
        lambda: SimpleNamespace(
            access_registry=_registry_for_profile(extra_bindings=(other_binding,))
        ),
    )
    monkeypatch.setattr(
        "hermes_cli.mcp_config._get_mcp_servers",
        lambda: called.__setitem__("servers", called["servers"] + 1),
    )
    monkeypatch.setattr(
        web_server,
        "_run_dashboard_mcp_oauth",
        lambda *_a, **_k: called.__setitem__("worker", called["worker"] + 1),
    )

    response = _client().post("/api/mcp/servers/reports/auth?profile=family-profile")

    assert response.status_code == 403
    assert "ambiguous_profile_binding" in response.text
    assert "user-family-other" not in response.text
    assert called == {"servers": 0, "worker": 0}


def test_multiplex_auth_start_denies_disabled_profile_binding_before_worker(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from agent.secret_scope import set_multiplex_active
    from hermes_cli import web_server

    set_multiplex_active(True)
    profile_home = tmp_path / "profiles" / "family-profile"
    profile_home.mkdir(parents=True)
    called = {"worker": 0, "servers": 0}

    monkeypatch.setattr(web_server, "_resolve_profile_dir", lambda name: profile_home)
    monkeypatch.setattr(
        "gateway.config.load_gateway_config",
        lambda: SimpleNamespace(access_registry=_registry_for_profile(active=False)),
    )
    monkeypatch.setattr(
        web_server,
        "_run_dashboard_mcp_oauth",
        lambda *_a, **_k: called.__setitem__("worker", called["worker"] + 1),
    )
    monkeypatch.setattr(
        "hermes_cli.mcp_config._get_mcp_servers",
        lambda: called.__setitem__("servers", called["servers"] + 1),
    )

    response = _client().post("/api/mcp/servers/reports/auth?profile=family-profile")

    assert response.status_code == 403
    assert "disabled_profile_binding" in response.text
    assert called == {"worker": 0, "servers": 0}


def test_multiplex_callback_cannot_change_captured_scope(monkeypatch):
    import asyncio

    from hermes_cli import web_server
    from tools.mcp_dashboard_oauth import DashboardOAuthFlow
    from tools.mcp_tool import MCPPoolKey

    context = _registry_for_profile("family-profile").resolve_exact_profile_context(
        "family-profile"
    )
    flow = DashboardOAuthFlow(
        flow_id="flow-context",
        server_name="reports",
        profile="family-profile",
        hermes_home="/tmp/hermes-test",
        redirect_uri="https://agent.example/api/mcp/oauth/callback/reports",
        access_context=context,
        pool_key=MCPPoolKey("family-profile", "dm:family"),
    )
    asyncio.run(flow.publish_authorization_url("https://idp.example/authorize?state=expected"))
    web_server._mcp_oauth_flows[flow.flow_id] = flow

    response = _client().get(
        "/api/mcp/oauth/callback/reports"
        "?code=abc&state=expected&profile=owner&conversation_scope=owner"
    )

    assert response.status_code == 200
    assert flow.access_context == context
    assert flow.pool_key == MCPPoolKey("family-profile", "dm:family")


def test_typed_oauth_token_probe_log_redacts_exception_string(monkeypatch, caplog):
    from hermes_cli import mcp_config
    from tools.mcp_tool import MCPPoolKey

    class BrokenStorage:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("/secret/profile/path token-store failed")

    monkeypatch.setattr("tools.mcp_oauth.HermesTokenStorage", BrokenStorage)
    caplog.set_level("DEBUG", logger="hermes_cli.mcp_config")

    assert mcp_config._oauth_tokens_present(
        "reports",
        hermes_home="/secret/profile/path",
        typed_pool_key=MCPPoolKey("family-profile", "dm:family"),
    ) is False

    assert "RuntimeError" in caplog.text
    assert "token storage check failed" in caplog.text
    assert "/secret/profile/path" not in caplog.text
    assert "token-store failed" not in caplog.text
