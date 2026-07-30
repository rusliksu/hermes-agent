"""Bounded descriptor-relative inventory and exact ELF dependency planning."""

from __future__ import annotations

import hashlib
import os
import stat
import sysconfig
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

try:
    from scripts.hermes_kanban_mcp_elf import (
        ElfFormatError,
        LoaderPlatform,
        dependency_search,
        parse_elf,
    )
except ImportError:
    from hermes_kanban_mcp_elf import (
        ElfFormatError,
        LoaderPlatform,
        dependency_search,
        parse_elf,
    )


MAX_TOPOLOGY_ENTRIES = 250_000
MAX_DIRECTORY_DEPTH = 64
ACQUISITION_TEMPORARY_FDS = MAX_DIRECTORY_DEPTH + 3
READ_CHUNK = 1024 * 1024
_ROOTS = (Path("/usr"), Path("/lib"), Path("/lib64"))


class InventoryError(ValueError):
    """Inventory is incomplete, mutable, ambiguous, or unsupported."""


@dataclass(frozen=True)
class InventoryEntry:
    source: Path
    destination: Path
    kind: str
    mode: int
    identity: tuple[int, ...]
    size: int = 0
    digest: str | None = None
    target: str | None = None


@dataclass(frozen=True)
class InventoryPlan:
    entries: tuple[InventoryEntry, ...]
    elf_dependencies: tuple[tuple[Path, tuple[Path, ...]], ...]
    acquisition_temporary_fds: int = ACQUISITION_TEMPORARY_FDS

    @property
    def regular_count(self) -> int:
        return sum(entry.kind == "file" for entry in self.entries)


def identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _flags(*, directory: bool = False) -> int:
    result = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    if directory:
        result |= getattr(os, "O_DIRECTORY", 0)
    return result


def _read_digest(fd: int) -> tuple[bytes, str]:
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    chunks = []
    while True:
        chunk = os.read(fd, READ_CHUNK)
        if not chunk:
            break
        chunks.append(chunk)
        digest.update(chunk)
    os.lseek(fd, 0, os.SEEK_SET)
    return b"".join(chunks), digest.hexdigest()


class InventoryBuilder:
    def __init__(self) -> None:
        self.entries: dict[Path, InventoryEntry] = {}
        self.bytes_by_destination: dict[Path, bytes] = {}
        self.dependencies: dict[Path, tuple[Path, ...]] = {}
        self.inherited: dict[Path, tuple[Path, ...]] = {}

    def _add(self, entry: InventoryEntry, data: bytes | None = None) -> None:
        existing = self.entries.get(entry.destination)
        if existing is not None and existing != entry:
            raise InventoryError(f"ambiguous inventory destination: {entry.destination}")
        self.entries[entry.destination] = entry
        if data is not None:
            self.bytes_by_destination[entry.destination] = data
        if len(self.entries) > MAX_TOPOLOGY_ENTRIES:
            raise InventoryError("inventory topology exceeds named maximum")

    def tree(
        self,
        source: Path,
        destination: Path,
        *,
        excluded: Sequence[str] = (),
        capture_content: bool = True,
    ) -> None:
        if source == destination:
            for parent in reversed(source.parents[:-1]):
                descriptor = os.open(parent, _flags(directory=True))
                try:
                    info = os.fstat(descriptor)
                    self._add(
                        InventoryEntry(
                            parent,
                            parent,
                            "directory",
                            stat.S_IMODE(info.st_mode),
                            identity(info),
                        )
                    )
                finally:
                    os.close(descriptor)
        fd = os.open(source, _flags(directory=True))
        try:
            self._directory(
                fd, source, destination, set(excluded), 0, capture_content
            )
        finally:
            os.close(fd)

    def _directory(
        self,
        fd: int,
        source: Path,
        destination: Path,
        excluded: set[str],
        depth: int,
        capture_content: bool,
    ) -> None:
        if depth > MAX_DIRECTORY_DEPTH:
            raise InventoryError("inventory directory depth exceeds named maximum")
        before = os.fstat(fd)
        self._add(
            InventoryEntry(
                source, destination, "directory", stat.S_IMODE(before.st_mode), identity(before)
            )
        )
        names = sorted(name for name in os.listdir(fd) if name not in excluded)
        for name in names:
            raw = os.stat(name, dir_fd=fd, follow_symlinks=False)
            child_source = source / name
            child_destination = destination / name
            if stat.S_ISDIR(raw.st_mode):
                child = os.open(name, _flags(directory=True), dir_fd=fd)
                try:
                    if identity(raw) != identity(os.fstat(child)):
                        raise InventoryError(f"directory changed during inventory: {child_source}")
                    self._directory(
                        child,
                        child_source,
                        child_destination,
                        set(),
                        depth + 1,
                        capture_content,
                    )
                finally:
                    os.close(child)
            elif stat.S_ISREG(raw.st_mode):
                if not capture_content:
                    self._add(
                        InventoryEntry(
                            child_source,
                            child_destination,
                            "file",
                            stat.S_IMODE(raw.st_mode),
                            identity(raw),
                            raw.st_size,
                        )
                    )
                    continue
                child = os.open(name, _flags(), dir_fd=fd)
                try:
                    opened = os.fstat(child)
                    data, digest = _read_digest(child)
                    if identity(raw) != identity(opened) or identity(opened) != identity(os.fstat(child)):
                        raise InventoryError(f"file changed during inventory: {child_source}")
                    self._add(
                        InventoryEntry(
                            child_source,
                            child_destination,
                            "file",
                            stat.S_IMODE(opened.st_mode),
                            identity(opened),
                            len(data),
                            digest,
                        ),
                        data,
                    )
                finally:
                    os.close(child)
            elif stat.S_ISLNK(raw.st_mode):
                target = os.readlink(name, dir_fd=fd)
                after = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if not target or "\x00" in target or identity(raw) != identity(after):
                    raise InventoryError(f"symlink changed during inventory: {child_source}")
                self._add(
                    InventoryEntry(
                        child_source,
                        child_destination,
                        "symlink",
                        stat.S_IMODE(raw.st_mode),
                        identity(raw),
                        target=target,
                    )
                )
            else:
                raise InventoryError(f"inventory contains unsupported file type: {child_source}")
        if names != sorted(name for name in os.listdir(fd) if name not in excluded):
            raise InventoryError(f"directory topology changed during inventory: {source}")
        if identity(before) != identity(os.fstat(fd)):
            raise InventoryError(f"directory changed during inventory: {source}")

    def external(self, path: Path) -> InventoryEntry:
        normalized = Path(os.path.normpath(path))
        if not normalized.is_absolute() or ".." in path.parts or not _trusted(normalized):
            raise InventoryError(f"ELF closure escaped trusted roots: {path}")
        current_fd = os.open("/", _flags(directory=True))
        current = Path("/")
        pending = list(normalized.parts[1:])
        followed: set[Path] = set()
        try:
            while pending:
                name = pending.pop(0)
                source = current / name
                raw = os.stat(name, dir_fd=current_fd, follow_symlinks=False)
                if stat.S_ISLNK(raw.st_mode):
                    if source in followed:
                        raise InventoryError("ELF closure contains a symlink cycle")
                    followed.add(source)
                    target = os.readlink(name, dir_fd=current_fd)
                    self._add(
                        InventoryEntry(
                            source, source, "symlink", stat.S_IMODE(raw.st_mode),
                            identity(raw), target=target,
                        )
                    )
                    resolved = Path(target) if Path(target).is_absolute() else current / target
                    normalized = Path(os.path.normpath(Path(resolved, *pending)))
                    if not normalized.is_absolute() or not _trusted(normalized):
                        raise InventoryError(
                            f"ELF closure symlink escaped trusted roots: {source}"
                        )
                    os.close(current_fd)
                    current_fd = os.open("/", _flags(directory=True))
                    current = Path("/")
                    pending = list(normalized.parts[1:])
                    continue
                if pending:
                    if not stat.S_ISDIR(raw.st_mode):
                        raise InventoryError(f"ELF closure component is not a directory: {source}")
                    child = os.open(name, _flags(directory=True), dir_fd=current_fd)
                    opened = os.fstat(child)
                    if identity(raw) != identity(opened):
                        os.close(child)
                        raise InventoryError(f"ELF closure directory changed: {source}")
                    self._add(
                        InventoryEntry(
                            source, source, "directory", stat.S_IMODE(opened.st_mode),
                            identity(opened),
                        )
                    )
                    os.close(current_fd)
                    current_fd = child
                    current = source
                    continue
                if not stat.S_ISREG(raw.st_mode):
                    raise InventoryError(f"ELF closure endpoint is not regular: {source}")
                child = os.open(name, _flags(), dir_fd=current_fd)
                try:
                    opened = os.fstat(child)
                    data, digest = _read_digest(child)
                    if identity(raw) != identity(opened) or identity(opened) != identity(os.fstat(child)):
                        raise InventoryError(f"ELF closure changed during inventory: {source}")
                    entry = InventoryEntry(
                        source, source, "file", stat.S_IMODE(opened.st_mode),
                        identity(opened), len(data), digest,
                    )
                    self._add(entry, data)
                    return entry
                finally:
                    os.close(child)
        except OSError as exc:
            raise InventoryError(f"cannot inventory required ELF closure: {path}") from exc
        finally:
            os.close(current_fd)
        raise InventoryError(f"ELF closure path is incomplete: {path}")

    def elf_closure(self, initial: Sequence[Path]) -> None:
        machine = os.uname().machine
        platform = LoaderPlatform("lib64" if machine in ("x86_64", "aarch64") else "lib", machine)
        multiarch = sysconfig.get_config_var("MULTIARCH")
        defaults = [Path("/lib"), Path("/usr/lib"), Path("/lib64"), Path("/usr/lib64")]
        if isinstance(multiarch, str) and multiarch:
            defaults[:0] = [Path("/lib") / multiarch, Path("/usr/lib") / multiarch]
        queue = [(path, ()) for path in initial]
        seen: set[tuple[Path, tuple[Path, ...]]] = set()
        while queue:
            destination, inherited = queue.pop(0)
            key = (destination, inherited)
            if key in seen:
                continue
            seen.add(key)
            data = self.bytes_by_destination.get(destination)
            if data is None:
                entry = self.external(destination)
                data = self.bytes_by_destination[entry.destination]
                destination = entry.destination
            try:
                info = parse_elf(data)
            except (ElfFormatError, UnicodeDecodeError) as exc:
                raise InventoryError(str(exc)) from exc
            if info is None:
                self.dependencies[destination] = ()
                continue
            direct: list[Path] = []
            if info.interpreter:
                loader = self.external(Path(info.interpreter))
                direct.append(loader.destination)
                queue.append((loader.destination, ()))
            search, child_inherited = dependency_search(
                info,
                origin=destination.parent,
                inherited_rpath=inherited,
                platform=platform,
                allowed_roots=_ROOTS,
                defaults=defaults,
            )
            for soname in info.needed:
                dependency = next(
                    (
                        candidate
                        for root in search
                        for candidate in (root / soname,)
                        if os.path.lexists(candidate)
                    ),
                    None,
                )
                if dependency is None:
                    raise InventoryError(f"required shared library is unavailable: {soname}")
                entry = self.external(dependency)
                direct.append(entry.destination)
                queue.append((entry.destination, child_inherited))
            previous = self.dependencies.get(destination)
            if previous is not None and previous != tuple(direct):
                raise InventoryError(f"ELF dependency plan is context-ambiguous: {destination}")
            self.dependencies[destination] = tuple(direct)

    def finish(self) -> InventoryPlan:
        return InventoryPlan(
            tuple(sorted(self.entries.values(), key=lambda entry: str(entry.destination))),
            tuple(sorted(self.dependencies.items(), key=lambda item: str(item[0]))),
        )


def _trusted(path: Path) -> bool:
    return any(path == root or root in path.parents for root in _ROOTS)
