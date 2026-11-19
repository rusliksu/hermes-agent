"""Idle LSP cleanup must spare active diagnostics and survive wall-clock jumps."""
import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.lsp import manager
from agent.lsp.manager import LSPService
from agent.lsp.protocol import LSPRequestError
from agent.lsp.servers import SERVERS, ServerDef, SpawnSpec
from agent.lsp.workspace import clear_cache


@pytest.fixture
def service_factory(tmp_path, monkeypatch):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "pyproject.toml").write_text("")
    for name in ("a.py", "b.py"):
        (repo / name).write_text("x = 1\n")
    monkeypatch.chdir(repo)
    clear_cache()
    index = next(i for i, server in enumerate(SERVERS) if server.server_id == "pyright")
    original = SERVERS[index]
    services = []

    def make(script="silent", multi_root=False):
        def spawn(root, context):
            return SpawnSpec(command=[sys.executable, str(Path(__file__).with_name("_mock_lsp_server.py"))],
                             workspace_root=root, cwd=root, env={"MOCK_LSP_SCRIPT": script})
        SERVERS[index] = ServerDef(server_id="pyright", extensions=(".py",),
                                  resolve_root=lambda path, workspace: workspace,
                                  build_spawn=spawn, multi_root=multi_root, description="synthetic LSP")
        service = LSPService(enabled=True, wait_mode="document", wait_timeout=30,
                             install_strategy="manual", idle_timeout=60)
        services.append(service)
        return service, repo

    yield make
    for service in services:
        service.shutdown()
    SERVERS[index] = original
    clear_cache()


@pytest.mark.parametrize("multi_root", [False, True])
def test_reaper_keeps_real_process_until_all_diagnostic_waits_finish(service_factory, multi_root):
    service, repo = service_factory(multi_root=multi_root)

    async def exercise():
        existing_tasks = set(asyncio.all_tasks())
        requests = [asyncio.create_task(service._open_and_wait_async(str(repo / name)))
                    for name in ("a.py", "b.py")]
        try:
            from tests.waiting import async_wait_until

            await async_wait_until(
                lambda: bool(service._clients)
                and len(next(iter(service._clients.values()))._docs) == 2,
                timeout=5,
            )
            client = next(iter(service._clients.values()))
            process = client._proc
            # Real round trip establishes that the subprocess consumed both preceding document opens.
            with pytest.raises(LSPRequestError) as exc:
                await asyncio.wait_for(client._send_request("synthetic/barrier", {}), timeout=5)
            assert exc.value.code == -32601
            assert not any(request.done() for request in requests)
            for request in requests:
                for key in service._last_used:
                    service._last_used[key] = 0
                await service._reap_idle_once()
                assert process.returncode is None, "idle sweep terminated a server with active diagnostics"
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)
            # The final cancellation releases the lease; genuinely idle servers must still be reaped.
            for key in service._last_used:
                service._last_used[key] = 0
            await service._reap_idle_once()
            assert process.returncode is not None
            assert not service._clients
            await asyncio.sleep(0)
            assert not (set(asyncio.all_tasks()) - existing_tasks), "cancelled diagnostics left orphan waiters"
        finally:
            for request in requests:
                request.cancel()
            await asyncio.gather(*requests, return_exceptions=True)

    service._loop.run(exercise(), timeout=20)


def test_recent_client_survives_wall_clock_jump(service_factory, monkeypatch):
    service, repo = service_factory(script="errors")
    assert len(service.get_diagnostics_sync(str(repo / "a.py"))) == 1
    client = next(iter(service._clients.values()))
    process = client._proc
    wall_now = time.time()
    monkeypatch.setattr(manager, "time", SimpleNamespace(time=lambda: wall_now + 3600,
                                                       monotonic=time.monotonic))
    service._loop.run(service._reap_idle_once(), timeout=5)
    assert process.returncode is None, "a wall-clock adjustment is not idle time"
    assert client.is_running
