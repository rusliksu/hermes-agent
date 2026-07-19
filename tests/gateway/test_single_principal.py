import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource, build_session_key
from gateway.single_principal import (
    SinglePrincipalPolicy,
    SinglePrincipalPolicyError,
    require_valid_single_principal_policy,
    validate_single_principal_policy,
)


OWNER = "10001"
OUTSIDER = "20002"


def _policy(**overrides):
    raw = {
        "enabled": True,
        "telegram_owner_id": OWNER,
        "allow_owner_bound_relay": False,
    }
    raw.update(overrides)
    return SinglePrincipalPolicy.from_dict(raw)


def _source(
    user_id=OWNER,
    *,
    platform=Platform.TELEGRAM,
    chat_type="dm",
    thread_id=None,
    relay=False,
):
    return SessionSource(
        platform=platform,
        chat_id="10001",
        chat_type=chat_type,
        user_id=user_id,
        thread_id=thread_id,
        delivered_via_upstream_relay=relay,
    )


def _runner(policy=None):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner._single_principal_policy = policy or _policy()
    runner.adapters = {}
    runner.pairing_store = MagicMock()
    runner.pairing_store.is_approved.return_value = True
    return runner


def test_policy_parser_and_redacted_validation():
    policy = _policy()
    assert policy.enabled is True
    assert policy.authorize(_source()) is True
    assert policy.authorize(_source(thread_id="77")) is True
    assert policy.authorize(_source(OUTSIDER)) is False
    assert policy.authorize(_source(chat_type="group")) is False

    report = validate_single_principal_policy(
        policy,
        environ={
            "TELEGRAM_ALLOWED_USERS": f"{OWNER},{OUTSIDER},*",
            "TELEGRAM_GROUP_ALLOWED_CHATS": "-123",
            "GATEWAY_ALLOW_ALL_USERS": "true",
        },
    )
    rendered = json.dumps(report.as_dict())
    assert report.verdict == "fail"
    assert {category for category, _ in report.conflicts} == {
        "allow_all",
        "group_grant",
        "non_owner_allowlist",
        "wildcard_grant",
    }
    assert OWNER not in rendered
    assert OUTSIDER not in rendered
    assert "-123" not in rendered


@pytest.mark.parametrize(
    "raw,category",
    [
        ({"enabled": True}, "missing_owner_mapping"),
        ({"enabled": True, "telegram_owner_id": "*"}, "wildcard_owner"),
        ({"enabled": "invalid", "telegram_owner_id": OWNER}, "malformed_policy"),
        ({"enabled_typo": True, "telegram_owner_id": OWNER}, "malformed_policy"),
    ],
)
def test_invalid_policy_fails_without_identity(raw, category):
    policy = SinglePrincipalPolicy.from_dict(raw)
    with pytest.raises(SinglePrincipalPolicyError) as exc:
        require_valid_single_principal_policy(policy, require_enabled=True, environ={})
    assert category in str(exc.value)
    assert OWNER not in str(exc.value)


def test_runtime_guard_precedes_legacy_pairing_and_role_grants(monkeypatch):
    monkeypatch.setenv("GATEWAY_ALLOW_ALL_USERS", "true")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "*")
    runner = _runner()
    outsider = _source(OUTSIDER)
    outsider.role_authorized = True

    assert runner._is_user_authorized(_source()) is True
    assert runner._is_user_authorized(_source(thread_id="77")) is True
    assert runner._is_user_authorized(outsider) is False
    assert runner._is_user_authorized(_source(chat_type="forum")) is False
    runner.pairing_store.is_approved.assert_not_called()


def test_relay_requires_explicit_owner_bound_flag_and_dm_shape():
    relay_dm = _source(
        OUTSIDER,
        platform=Platform.DISCORD,
        relay=True,
    )
    disabled = _runner(_policy(allow_owner_bound_relay=False))
    enabled = _runner(_policy(allow_owner_bound_relay=True))

    assert disabled._is_user_authorized(relay_dm) is False
    assert enabled._is_user_authorized(relay_dm) is True
    assert enabled._is_user_authorized(
        _source(
            OUTSIDER,
            platform=Platform.DISCORD,
            chat_type="group",
            relay=True,
        )
    ) is False


def test_owner_session_keys_remain_byte_compatible():
    assert build_session_key(_source()) == "agent:main:telegram:dm:10001"
    assert build_session_key(_source(thread_id="77")) == (
        "agent:main:telegram:dm:10001:77"
    )


def test_pairing_rejects_non_owner_without_writing(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from gateway.pairing import PairingStore

    store = PairingStore(profile="test", single_principal_policy=_policy())
    with pytest.raises(SinglePrincipalPolicyError):
        store._approve_user("telegram", OUTSIDER)

    assert store.list_approved("telegram") == []
    store._approve_user("telegram", OWNER)
    assert len(store.list_approved("telegram")) == 1


def test_pairing_approve_rejects_preexisting_non_owner_request(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from gateway.pairing import PairingStore

    store = PairingStore(profile="test")
    code = store.generate_code("telegram", OUTSIDER)
    assert code
    store._single_principal_policy = _policy()

    with pytest.raises(SinglePrincipalPolicyError):
        store.approve_code("telegram", code)

    assert len(store.list_pending("telegram")) == 1
    assert store.list_approved("telegram") == []


@pytest.mark.asyncio
async def test_non_owner_stops_before_pre_dispatch_hook(monkeypatch, caplog):
    runner = _runner()
    runner._startup_restore_in_progress = False
    runner._scale_to_zero_note_real_inbound = MagicMock()
    runner.session_store = MagicMock()
    runner._handle_message_with_agent = AsyncMock()
    hook = MagicMock(return_value=[])
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", hook)

    result = await runner._handle_message(
        MessageEvent(text="private input", source=_source(OUTSIDER))
    )

    assert result is None
    hook.assert_not_called()
    runner._scale_to_zero_note_real_inbound.assert_not_called()
    runner.session_store.assert_not_called()
    runner._handle_message_with_agent.assert_not_called()
    assert OUTSIDER not in caplog.text
    assert "private input" not in caplog.text


def test_gateway_config_parses_single_principal_policy():
    config = GatewayConfig.from_dict(
        {"single_principal": {"enabled": True, "telegram_owner_id": OWNER}}
    )
    assert config.single_principal == _policy()
    assert GatewayConfig.from_dict(config.to_dict()).single_principal == _policy()


def test_validator_rejects_pairing_drift_and_unsupported_ingress():
    pairing_store = MagicMock()
    pairing_store.list_approved.return_value = [
        {"platform": "telegram", "user_id": OWNER},
        {"platform": "telegram", "user_id": OUTSIDER},
    ]
    config = GatewayConfig(
        platforms={Platform.DISCORD: PlatformConfig(enabled=True)},
        single_principal=_policy(),
    )
    report = validate_single_principal_policy(
        config.single_principal,
        gateway_config=config,
        pairing_store=pairing_store,
        environ={},
    )
    assert dict(report.conflicts) == {
        "non_owner_pairing": 1,
        "unsupported_external_platform": 1,
    }


def test_validator_fails_closed_when_pairing_store_is_unreadable():
    pairing_store = MagicMock()
    pairing_store.list_approved.side_effect = PermissionError("private path")
    report = validate_single_principal_policy(
        _policy(), pairing_store=pairing_store, environ={}
    )
    rendered = json.dumps(report.as_dict())
    assert dict(report.conflicts) == {"pairing_store_unreadable": 1}
    assert "private path" not in rendered


def test_cli_preflight_output_is_redacted(monkeypatch, capsys):
    import gateway.config as config_module
    import gateway.pairing as pairing_module
    from gateway.single_principal import main

    for key in (
        "GATEWAY_ALLOW_ALL_USERS",
        "TELEGRAM_ALLOW_ALL_USERS",
        "GATEWAY_ALLOWED_USERS",
        "TELEGRAM_ALLOWED_USERS",
        "TELEGRAM_GROUP_ALLOWED_USERS",
        "TELEGRAM_GROUP_ALLOWED_CHATS",
        "TELEGRAM_ALLOW_BOTS",
    ):
        monkeypatch.delenv(key, raising=False)

    config = GatewayConfig(single_principal=_policy())
    monkeypatch.setattr(config_module, "load_gateway_config", lambda: config)

    class ReadOnlyPairing:
        def __init__(self, *, read_only=False):
            assert read_only is True

        def list_approved(self):
            return [{"platform": "telegram", "user_id": OUTSIDER}]

    monkeypatch.setattr(pairing_module, "PairingStore", ReadOnlyPairing)
    assert main(["--json", "--require-enabled"]) == 2
    output = capsys.readouterr().out
    assert "non_owner_pairing" in output
    assert OWNER not in output
    assert OUTSIDER not in output


def test_gateway_startup_accepts_valid_policy(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import gateway.pairing as pairing_module
    import gateway.run as gateway_run

    monkeypatch.setattr(pairing_module, "PAIRING_DIR", tmp_path / "pairing")
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    runner = gateway_run.GatewayRunner(GatewayConfig(single_principal=_policy()))
    assert runner._single_principal_policy == _policy()


def test_gateway_startup_rejects_conflicting_grant(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("GATEWAY_ALLOW_ALL_USERS", "true")
    import gateway.pairing as pairing_module
    import gateway.run as gateway_run

    monkeypatch.setattr(pairing_module, "PAIRING_DIR", tmp_path / "pairing")
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    with pytest.raises(SinglePrincipalPolicyError) as exc:
        gateway_run.GatewayRunner(GatewayConfig(single_principal=_policy()))
    assert "allow_all" in str(exc.value)
    assert OWNER not in str(exc.value)


def test_telegram_prefilter_and_callback_use_runner_policy():
    from plugins.platforms.telegram.adapter import TelegramAdapter

    class Runner:
        _single_principal_policy = _policy()

        async def handle(self, _event):
            return None

        def _is_user_authorized(self, source):
            return bool(self._single_principal_policy.authorize(source))

    adapter = object.__new__(TelegramAdapter)
    adapter.config = PlatformConfig(enabled=True, token="test", extra={})
    adapter._message_handler = Runner().handle

    def message(user_id):
        return SimpleNamespace(
            from_user=SimpleNamespace(id=user_id, username="", full_name="user"),
            sender_chat=None,
            chat=SimpleNamespace(id=user_id, type="private", is_forum=False),
            message_thread_id=None,
            is_topic_message=False,
        )

    assert adapter._is_user_authorized_from_message(message(OWNER)) is True
    assert adapter._is_user_authorized_from_message(message(OUTSIDER)) is False
    assert adapter._is_callback_user_authorized(OWNER) is True
    assert adapter._is_callback_user_authorized(OUTSIDER) is False
