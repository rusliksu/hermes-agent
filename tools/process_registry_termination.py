"""Process-tree termination and post-kill survivor verification for ProcessRegistry."""

import logging
import os
import signal
import subprocess
import threading
import time
from contextlib import suppress
from typing import TYPE_CHECKING, List, Optional

from hermes_cli._subprocess_compat import windows_hide_flags

if TYPE_CHECKING:
    from tools.process_registry import ProcessSession

logger = logging.getLogger("tools.process_registry")


class ProcessTerminationMixin:
    """The subclass supplies ``_host_pid_is_ours``, ``_is_host_pid_alive``,
    ``_detached_host_fate`` and ``_daemon_term_grace_seconds``. See ProcessRegistry."""

    @staticmethod
    def _proc_alive(proc) -> bool:
        """True if a psutil.Process is running and not a zombie (already dead, just unreaped)."""
        try:
            import psutil
            return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
        except Exception:
            return False

    @classmethod
    def _terminate_host_pid(cls, pid: int, expected_start: Optional[int] = None) -> None:
        """Terminate a host-visible PID and its descendants.

        expected_start (kernel start time at spawn) is re-validated first: a mismatch
        or dead PID means the number was recycled onto a stranger and we refuse to touch
        it - a leaked orphan beats tree-killing someone else's browser.

        The platform strategy is a separate classmethod so both arms stay testable on
        every host: Windows uses taskkill /T /F (psutil's stale PPID links miss orphans
        there), POSIX snapshots descendants and lets the parent shut down first.
        """
        from tools.process_registry import _IS_WINDOWS

        if expected_start is not None and not cls._host_pid_is_ours(pid, expected_start):
            logger.warning(
                "Refusing to terminate host pid %d: start-time mismatch — "
                "PID was recycled onto an unrelated process.", pid)
            return
        if _IS_WINDOWS:
            cls._terminate_windows(pid)
        else:
            cls._terminate_posix(pid)

    @staticmethod
    def _sigterm_quietly(pid: int) -> None:
        with suppress(OSError, ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGTERM)

    @classmethod
    def _terminate_windows(cls, pid: int) -> None:
        """taskkill /T /F; os.kill is the fallback when taskkill is unavailable."""
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True,
                encoding='utf-8', errors='replace', timeout=10, creationflags=windows_hide_flags(),
                stdin=subprocess.DEVNULL, check=False)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            cls._sigterm_quietly(pid)

    @classmethod
    def _terminate_posix(cls, pid: int) -> None:
        """POSIX arm: SIGTERM the parent alone, then clean up surviving snapshot descendants."""
        import psutil

        gone = (psutil.NoSuchProcess, psutil.AccessDenied, OSError)
        try:
            parent = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return
        except (OSError, PermissionError):
            cls._sigterm_quietly(pid)
            return
        # Snapshot before signalling: once the parent exits, psutil can no longer
        # reliably find children that it failed to reap.
        try:
            descendants = parent.children(recursive=True)
        except gone:
            descendants = []

        # Let self-managing parents (notably Chromium/Electron) shut down their
        # tree before touching children. Killing their zygotes first can turn a
        # graceful browser shutdown into a crash dump.
        with suppress(gone):
            parent.terminate()

        grace = cls._daemon_term_grace_seconds()

        def _wait_for_exit(targets) -> None:
            if grace <= 0:
                return
            deadline = time.monotonic() + grace
            while time.monotonic() < deadline and any(cls._proc_alive(p) for p in targets):
                time.sleep(0.05)

        # Preserve descendants during the parent's configured shutdown window.
        _wait_for_exit([parent])

        # The snapshot is an anti-orphan guarantee: only descendants still alive
        # after the parent had its chance are asked to terminate themselves.
        remaining = descendants if grace <= 0 else [
            proc for proc in descendants if cls._proc_alive(proc)
        ]
        for proc in remaining:
            with suppress(gone):
                proc.terminate()

        # Preserve the existing SIGKILL escalation semantics for every owned
        # process that remains after its SIGTERM grace window. The parent is
        # included in case it ignored the first signal.
        targets = [parent, *remaining]
        # Escalate to SIGKILL for anything that ignored SIGTERM within the grace window.
        # psutil.wait_procs' gone/alive partition is deliberately NOT trusted: it
        # reaps via Process.wait() and mis-partitions across zombie transitions in a
        # parent/child tree, leaving survivors un-killed. Re-probing every target is
        # deterministic.
        if grace <= 0:
            return
        _wait_for_exit(targets)
        # A parent that ignored SIGTERM (the interactive bash -lic wrapper does) keeps
        # running its script through both grace windows and can spawn children the first
        # snapshot never saw. Re-snapshot while it is still alive: once it is SIGKILLed
        # they reparent to init and nothing can find them again.
        with suppress(gone):
            if cls._proc_alive(parent):
                known = {proc.pid for proc in targets}
                targets.extend(p for p in parent.children(recursive=True) if p.pid not in known)
        for proc in targets:
            with suppress(gone):
                if cls._proc_alive(proc):
                    proc.kill()  # SIGKILL on POSIX
                    logger.info("Escalated to SIGKILL for pid %d (ignored SIGTERM within %.1fs grace)", proc.pid, grace)

    @staticmethod
    def _live_descendants(pid: int) -> list[int]:
        """PIDs of living non-zombie descendants of host PID ``pid`` (best-effort)."""
        try:
            import psutil
            children = psutil.Process(pid).children(recursive=True)
        except Exception:
            logger.debug("Could not list descendants of pid %s", pid, exc_info=True)
            return []
        return [c.pid for c in children if ProcessTerminationMixin._proc_alive(c)]

    # SIGKILL / taskkill are asynchronous: the kernel needs a scheduling tick to
    # tear the process down and the parent must reap it before poll()/isalive()
    # stop saying "alive". Verifying survivors in that window flagged every
    # escalated kill as incomplete.
    _KILL_SETTLE_SECONDS = 1.0

    def _post_kill_survivors(self, session: "ProcessSession") -> list[int]:
        """Host PIDs still alive once the kill signals have had time to land (#115490).

        Fail-closed: anything unverifiable counts as a survivor, so a kill
        that leaves a live tree can never write a killed receipt. Sandbox
        (env) sessions have no host-visible tree and are unverifiable by
        design — they return no survivors, preserving existing behavior."""
        deadline = time.monotonic() + self._KILL_SETTLE_SECONDS
        while True:
            survivors = self._probe_survivors(session)
            if not survivors or time.monotonic() >= deadline:
                return survivors
            time.sleep(0.05)

    def _probe_survivors(self, session: "ProcessSession") -> list[int]:
        survivors: list[int] = []
        proc = getattr(session, "process", None)
        if proc is not None:
            try:
                root_alive = proc.poll() is None
            except Exception:
                root_alive = True
            if root_alive:
                survivors.append(getattr(proc, "pid", None) or session.pid)
        pty = getattr(session, "_pty", None)
        if pty is not None:
            try:
                pty_alive = bool(pty.isalive())
            except Exception:
                pty_alive = self._is_host_pid_alive(session.pid)
            if pty_alive:
                survivors.append(session.pid)
        if session.pid_scope == "host" and session.pid:
            if self._detached_host_fate(session.pid, session.host_start_time) == "running":
                if session.pid not in survivors:
                    survivors.append(session.pid)
                survivors.extend(
                    pid for pid in self._live_descendants(session.pid)
                    if pid not in survivors)
            # A dead/recycled root has no PID-scope descendants left to find:
            # reparented orphans are outside PID scope (systemd scope stop,
            # issued before this check, covers the cgroup case).
        return [pid for pid in survivors if pid]

    @staticmethod
    def _close_stream(stream) -> None:
        """Close a Popen stream; test doubles may expose no ``close`` at all."""
        close = getattr(stream, "close", None)
        if callable(close):
            with suppress(OSError, ValueError):  # a stdin flush can hit EPIPE
                close()

    def _release_finished_handles(self, session):
        """Close a finished session's OS handles (Popen pipes / PTY master).

        Best-effort and idempotent: the session may have no local Popen (env
        backends, detached recovery), or the handles may already be closed by
        the reader loop / kill path. Closing a Popen's stream objects does not
        kill anything — the child has already exited — it only releases the
        parent's pipe FDs, which is exactly the retained-resource leak.
        """
        proc = session.process
        if proc is not None:
            # A pipe reader blocked in ``read`` holds the buffer lock; closing its stream from
            # another thread would wait on that lock while the child is already gone. The reader
            # closes its own pipes in ``_finish_reader``; only stdin is safe to close here.
            reader = getattr(session, "_reader_thread", None)
            reader_busy = (reader is not None and reader.is_alive()
                           and reader is not threading.current_thread())
            for stream in ((getattr(proc, "stdout", None), getattr(proc, "stderr", None))
                           if not reader_busy else ()):
                self._close_stream(stream)
            self._close_stream(getattr(proc, "stdin", None))
        if session._pty is not None:
            # A live ptyprocess reader sits in a blocking read holding the PTY file
            # object's buffer lock, and that read only ends once every holder of
            # the slave side is gone. A descendant that setsid()s past the kill
            # keeps it open, so close() here would block forever (under _lock on
            # the prune path). The reader closes the PTY itself via
            # _finish_reader once its read ends. pywinpty reads don't block, so
            # Windows closes here as before.
            from tools.process_registry import _IS_WINDOWS

            reader = session._reader_thread
            if (not _IS_WINDOWS and reader is not None and reader.is_alive()
                    and reader is not threading.current_thread()):
                return
            # ptyprocess/pywinpty close() is idempotent (``closed`` flag) and
            # closes the master fd exactly once; it raises only if the child
            # ignores SIGKILL, which we don't want to surface on the finish path.
            with suppress(Exception):
                session._pty.close()
