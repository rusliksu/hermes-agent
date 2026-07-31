"""Privacy boundaries for typed multiplex runtime footers."""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import yaml

from gateway.access_registry import DeliveryTarget, ResolvedAccessContext
from gateway.config import Platform
from gateway.profile_routing import ProfileRoutingError
from gateway.run import GatewayRunner
from gateway.session import SessionSource


def _typed_source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="family-chat",
        profile="family-alpha",
        resolved_access_context=ResolvedAccessContext(
            principal_id="principal-alpha",
            role_id="family_standard",
            profile_id="family-alpha",
            conversation_scope="dm:family-alpha",
            capabilities=frozenset({"chat"}),
            delivery_target=DeliveryTarget(
                platform="telegram",
                account="family-bot",
                peer_kind="dm",
                chat_id="family-chat",
            ),
        ),
    )


def _legacy_source() -> SessionSource:
    return SessionSource(platform=Platform.TELEGRAM, chat_id="legacy-chat")


def _write_config(home: Path, config: dict) -> None:
    home.mkdir(parents=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump(config),
        encoding="utf-8",
    )


def _footer(runner: GatewayRunner, source: SessionSource) -> str:
    return runner._build_runtime_footer_for_source(
        source,
        model="",
        context_tokens=0,
        context_length=None,
    )


def test_typed_footer_reads_profile_config_and_cwd_not_owner_state(
    tmp_path, monkeypatch
):
    owner_home = tmp_path / "owner"
    family_home = tmp_path / "family-alpha"
    _write_config(
        owner_home,
        {
            "display": {"runtime_footer": {"enabled": True, "fields": ["cwd"]}},
            "terminal": {"cwd": "/srv/owner-default-config"},
        },
    )
    _write_config(
        family_home,
        {
            "display": {"runtime_footer": {"enabled": True, "fields": ["cwd"]}},
            "terminal": {"cwd": "/srv/family-alpha-workspace"},
        },
    )
    monkeypatch.setenv("TERMINAL_CWD", "/srv/owner-ambient")
    runner = object.__new__(GatewayRunner)
    runner._resolve_profile_home_for_source = Mock(return_value=family_home)

    with patch("gateway.run._hermes_home", owner_home):
        result = _footer(runner, _typed_source())

    assert result == "/srv/family-alpha-workspace"
    runner._resolve_profile_home_for_source.assert_called_once()


@pytest.mark.parametrize(
    "configured_cwd",
    [None, "", ".", "auto", "cwd", "relative/workspace", ["malformed"]],
)
def test_typed_footer_omits_invalid_profile_cwd_despite_ambient_env(
    tmp_path, monkeypatch, configured_cwd
):
    family_home = tmp_path / "family-alpha"
    config = {
        "display": {"runtime_footer": {"enabled": True, "fields": ["cwd"]}},
    }
    if configured_cwd is not None:
        config["terminal"] = {"cwd": configured_cwd}
    _write_config(family_home, config)
    monkeypatch.setenv("TERMINAL_CWD", "/srv/owner-ambient")
    runner = object.__new__(GatewayRunner)
    runner._resolve_profile_home_for_source = Mock(return_value=family_home)

    assert _footer(runner, _typed_source()) == ""


def test_typed_footer_profile_resolution_failure_does_not_use_owner_state(
    tmp_path, monkeypatch
):
    owner_home = tmp_path / "owner"
    _write_config(
        owner_home,
        {
            "display": {"runtime_footer": {"enabled": True, "fields": ["cwd"]}},
            "terminal": {"cwd": "/srv/owner-default-config"},
        },
    )
    monkeypatch.setenv("TERMINAL_CWD", "/srv/owner-ambient")
    runner = object.__new__(GatewayRunner)
    runner._resolve_profile_home_for_source = Mock(
        side_effect=ProfileRoutingError("missing_resolved_profile")
    )

    with patch("gateway.run._hermes_home", owner_home):
        with pytest.raises(ProfileRoutingError, match="missing_resolved_profile"):
            _footer(runner, _typed_source())


def test_legacy_footer_preserves_ambient_env_fallback(tmp_path, monkeypatch):
    owner_home = tmp_path / "owner"
    _write_config(
        owner_home,
        {"display": {"runtime_footer": {"enabled": True, "fields": ["cwd"]}}},
    )
    monkeypatch.setenv("TERMINAL_CWD", "/srv/legacy-ambient")
    runner = object.__new__(GatewayRunner)

    with patch("gateway.run._hermes_home", owner_home):
        result = _footer(runner, _legacy_source())

    assert result == "/srv/legacy-ambient"
