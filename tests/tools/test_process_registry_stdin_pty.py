"""Interactive PTY stdin: delivery on every host, EOF where the host supports it."""
import shlex
import sys

import pytest

from tests.waiting import wait_until
from tools.process_registry import ProcessRegistry


@pytest.fixture()
def registry(monkeypatch):
    monkeypatch.setattr("hermes_cli.process_identity._update_supervised_pgids", lambda verb, pgids: None)
    return ProcessRegistry()


def test_pty_stdin_round_trips_to_interactive_child(registry, tmp_path):
    """PTY mode delivers interactive input on every host.

    Background non-PTY mode used to expose subprocess stdin via a pipe,
    but PR #214b95392 detached non-PTY stdin to DEVNULL to fix keyboard
    lockout (#17959). For interactive stdin, PTY mode is the only supported
    path. EOF itself is a host feature (ConPTY never half-closes its input),
    so this arm stops at the delivered line; the POSIX arm below keeps the
    EOF contract on the host that has it.
    """
    command = f'{shlex.quote(sys.executable)} -c "import sys; print(sys.stdin.readline().strip())"'
    session = registry.spawn_local(command, cwd=str(tmp_path), use_pty=True)

    try:
        wait_until(lambda: registry.poll(session.id)["status"] == "running",
                   timeout=5.0, what="PTY session running")
        # uptime_seconds only advances once the child console exists; input sent
        # before that is dropped by ConPTY.
        wait_until(lambda: registry.poll(session.id).get("uptime_seconds", 0) >= 1,
                   timeout=5.0, what="PTY console ready")
        assert registry.submit_stdin(session.id, "hello")["status"] == "ok"
        wait_until(lambda: "hello" in registry.poll(session.id).get("output_preview", ""),
                   timeout=5.0, what="PTY child echo")
    finally:
        registry.kill_process(session.id)


@pytest.mark.platforms("posix")
def test_close_stdin_allows_eof_driven_process_to_finish(registry, tmp_path):
    """POSIX PTY: writing data + sending EOF lets an EOF-driven child finish."""
    command = f'{shlex.quote(sys.executable)} -c "import sys; print(sys.stdin.read().strip())"'
    session = registry.spawn_local(command, cwd=str(tmp_path), use_pty=True)

    try:
        wait_until(lambda: registry.poll(session.id)["status"] == "running",
                   timeout=5.0, what="PTY session running")
        assert registry.submit_stdin(session.id, "hello")["status"] == "ok"
        assert registry.close_stdin(session.id)["status"] == "ok"
        wait_until(lambda: registry.poll(session.id)["status"] == "exited",
                   timeout=5.0, what="EOF-driven exit")
        poll = registry.poll(session.id)
        assert poll["exit_code"] == 0
        assert "hello" in poll["output_preview"]
    finally:
        registry.kill_process(session.id)
