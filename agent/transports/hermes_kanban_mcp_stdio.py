"""Newline-delimited stdio transport for the dedicated Kanban MCP server."""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from contextlib import asynccontextmanager
from typing import Any


class ByteLineFramer:
    """Buffer byte chunks and emit only complete newline-delimited lines."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[bytes]:
        self._buffer.extend(chunk)
        lines: list[bytes] = []
        while (newline_index := self._buffer.find(b"\n")) >= 0:
            line = bytes(self._buffer[:newline_index])
            del self._buffer[: newline_index + 1]
            if line.strip():
                lines.append(line)
        return lines

    def finish(self) -> bytes | None:
        if not self._buffer:
            return None
        line = bytes(self._buffer)
        self._buffer.clear()
        return line if line.strip() else None


def _parse_frame(frame: bytes) -> Any:
    """Convert one nonblank frame to an SDK message or validation error."""
    from mcp import types
    from mcp.shared.message import SessionMessage

    try:
        return SessionMessage(types.JSONRPCMessage.model_validate_json(frame))
    except Exception as exc:
        return exc


@asynccontextmanager
async def _stdio_transport():
    """Use byte-buffered stdin framing around the installed MCP SDK."""
    try:
        import anyio
    except ImportError as exc:  # pragma: no cover - checked before run
        raise ImportError(
            f"hermes-kanban MCP server requires the 'mcp' package: {exc}"
        ) from exc

    read_writer, read_stream = anyio.create_memory_object_stream(0)
    write_stream, write_reader = anyio.create_memory_object_stream(0)

    async def stdin_reader() -> None:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes | Exception | None] = asyncio.Queue()
        stdin_fd = sys.stdin.fileno()
        was_blocking = os.get_blocking(stdin_fd)
        os.set_blocking(stdin_fd, False)
        reader_removed = False

        def remove_stdin_reader() -> None:
            nonlocal reader_removed
            if not reader_removed:
                reader_removed = True
                with contextlib.suppress(Exception):
                    loop.remove_reader(stdin_fd)

        def on_stdin_ready() -> None:
            try:
                chunk = os.read(stdin_fd, 65536)
            except BlockingIOError:
                return
            except OSError as exc:
                remove_stdin_reader()
                queue.put_nowait(exc)
                return
            if not chunk:
                remove_stdin_reader()
                queue.put_nowait(None)
                return
            queue.put_nowait(chunk)

        async def send_line(line: bytes) -> None:
            await read_writer.send(_parse_frame(line))

        try:
            loop.add_reader(stdin_fd, on_stdin_ready)
            async with read_writer:
                framer = ByteLineFramer()
                while True:
                    chunk = await queue.get()
                    if chunk is None:
                        tail = framer.finish()
                        if tail is not None:
                            await send_line(tail)
                        break
                    if isinstance(chunk, Exception):
                        await read_writer.send(chunk)
                        break
                    for line in framer.feed(chunk):
                        await send_line(line)
        except anyio.ClosedResourceError:  # pragma: no cover
            await anyio.lowlevel.checkpoint()
        finally:
            remove_stdin_reader()
            with contextlib.suppress(OSError):
                os.set_blocking(stdin_fd, was_blocking)

    async def stdout_writer() -> None:
        try:
            async with write_reader:
                async for session_message in write_reader:
                    data = session_message.message.model_dump_json(
                        by_alias=True,
                        exclude_none=True,
                    )
                    sys.stdout.write(data + "\n")
                    sys.stdout.flush()
        except anyio.ClosedResourceError:  # pragma: no cover
            await anyio.lowlevel.checkpoint()

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(stdin_reader)
        task_group.start_soon(stdout_writer)
        try:
            yield read_stream, write_stream
        except BaseException:
            await write_stream.aclose()
            task_group.cancel_scope.cancel()
            raise
        else:
            await write_stream.aclose()


async def _run_stdio_async(server: Any) -> None:
    async with _stdio_transport() as (read_stream, write_stream):
        await server._mcp_server.run(
            read_stream,
            write_stream,
            server._mcp_server.create_initialization_options(),
        )


def run_stdio(server: Any) -> None:
    """Run the MCP server until stdin reaches EOF."""
    import anyio

    anyio.run(_run_stdio_async, server)
