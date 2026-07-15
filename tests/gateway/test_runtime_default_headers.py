"""Gateway runtime kwargs propagation tests."""

import gateway.run as gateway_run


def test_gateway_runtime_kwargs_preserve_default_headers(monkeypatch):
    monkeypatch.setattr(
        gateway_run,
        "_try_resolve_fallback_provider",
        lambda: None,
    )
    monkeypatch.setattr(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        lambda **_: {
            "api_key": "codex-token",
            "base_url": "https://chatgpt.com/backend-api/codex",
            "provider": "openai-codex",
            "api_mode": "codex_responses",
            "default_headers": {
                "originator": "codex_cli_rs",
                "ChatGPT-Account-Id": "acct_gateway_123",
            },
        },
    )
    monkeypatch.setattr(
        "hermes_cli.runtime_provider._get_model_config",
        lambda: {},
    )

    runtime = gateway_run._resolve_runtime_agent_kwargs()

    assert runtime["default_headers"] == {
        "originator": "codex_cli_rs",
        "ChatGPT-Account-Id": "acct_gateway_123",
    }
