from types import SimpleNamespace

from gateway.config import Platform


def _adapter(account: str):
    return SimpleNamespace(
        sent=[],
        _profile_route_account_label=lambda: account,
    )


def test_cron_adapter_view_keeps_legacy_get_on_primary():
    """Production break guarded: legacy cron must keep platform-only primary routing."""
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    primary = _adapter("bot-primary")
    secondary = _adapter("bot-secondary")
    runner.adapters = {Platform.TELEGRAM: primary}
    runner._profile_adapters = {"family": {Platform.TELEGRAM: secondary}}

    view = runner._cron_adapter_view()

    assert view.get(Platform.TELEGRAM) is primary
    assert view.resolve(Platform.TELEGRAM, "bot-secondary") is secondary


def test_cron_adapter_view_resolves_current_replacement_and_addition():
    """Production break guarded: long-lived cron provider must not hold stale adapters."""
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    original = _adapter("bot-old")
    replacement = _adapter("bot-new")
    runner.adapters = {Platform.TELEGRAM: original}
    runner._profile_adapters = {}
    view = runner._cron_adapter_view()

    runner.adapters[Platform.TELEGRAM] = replacement
    assert view.get(Platform.TELEGRAM) is replacement
    assert view.resolve(Platform.TELEGRAM, "bot-new") is replacement
    assert view.resolve(Platform.TELEGRAM, "bot-old") is None

    added = _adapter("bot-added")
    runner._profile_adapters = {"added": {Platform.TELEGRAM: added}}
    assert view.resolve(Platform.TELEGRAM, "bot-added") is added


def test_cron_adapter_view_duplicate_account_fails_closed():
    """Production break guarded: exact account collision must not pick by profile_id/order."""
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    first = _adapter("bot-shared")
    second = _adapter("bot-shared")
    runner.adapters = {Platform.TELEGRAM: first}
    runner._profile_adapters = {"other": {Platform.TELEGRAM: second}}

    assert runner._cron_adapter_view().resolve(Platform.TELEGRAM, "bot-shared") is None


def test_cron_adapter_view_missing_account_fails_closed():
    """Production break guarded: typed cron must not degrade to platform-only adapter choice."""
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.adapters = {Platform.TELEGRAM: _adapter("bot-main")}
    runner._profile_adapters = {}

    assert runner._cron_adapter_view().resolve(Platform.TELEGRAM, "bot-missing") is None


def test_cron_adapter_view_account_match_on_other_platform_is_ignored():
    """Production break guarded: account labels are exact only within the requested platform."""
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.adapters = {Platform.DISCORD: _adapter("same-account")}
    runner._profile_adapters = {}

    assert runner._cron_adapter_view().resolve(Platform.TELEGRAM, "same-account") is None
