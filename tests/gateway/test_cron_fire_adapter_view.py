import asyncio
from types import SimpleNamespace

import pytest

from gateway.platforms.api_server import APIServerAdapter


class _SpyProvider:
    def __init__(self):
        self.fired = []
        self.adapters = []

    def fire_due(self, job_id, *, adapters=None, loop=None):
        self.fired.append(job_id)
        self.adapters.append(adapters)
        return True


@pytest.mark.asyncio
async def test_api_cron_fire_forwards_runner_bound_adapter_view(monkeypatch):
    """Production break guarded: /api/cron/fire must not pass adapters=None
    when it is running inside a gateway with live account-scoped adapters.
    """
    from gateway.config import Platform
    from gateway.run import GatewayRunner

    spy = _SpyProvider()
    adapter = object.__new__(APIServerAdapter)
    adapter.gateway_runner = None
    adapter._pending_agent_requests = 0
    adapter._background_tasks = set()
    runner = object.__new__(GatewayRunner)
    runner.adapters = {Platform.TELEGRAM: object()}
    runner._profile_adapters = {}
    monkeypatch.setattr("cron.scheduler_provider.resolve_cron_scheduler", lambda: spy)
    monkeypatch.setattr(
        "plugins.cron_providers.chronos.verify.get_fire_verifier",
        lambda: (lambda **kw: {"purpose": "cron_fire", "aud": "agent:x"}),
    )

    async def inline_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", inline_to_thread)

    request = SimpleNamespace(
        headers={"Authorization": "Bearer good"},
        app={"gateway_runner": runner},
        method="POST",
        path_qs="/api/cron/fire",
        remote="127.0.0.1",
        transport=None,
        json=lambda: asyncio.sleep(0, result={"job_id": "abc123"}),
    )

    resp = await adapter._handle_cron_fire(request)
    assert resp.status == 202

    for _ in range(50):
        if spy.adapters:
            break
        await asyncio.sleep(0.01)
    assert spy.fired == ["abc123"]
    assert spy.adapters[0] is runner._cron_adapter_view()
    for _ in range(50):
        if adapter._pending_agent_requests == 0 and not adapter._background_tasks:
            break
        await asyncio.sleep(0.01)
    assert adapter._pending_agent_requests == 0
    assert adapter._background_tasks == set()
