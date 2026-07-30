"""Immutable sealed-content capture for the Kanban MCP preflight boundary."""

from __future__ import annotations

import hashlib
import fcntl
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

try:
    from scripts import hermes_kanban_mcp_resources as resources
    from scripts import hermes_kanban_mcp_invocation as invocation
    from scripts.hermes_kanban_mcp_elf import ElfFormatError, ElfInfo, parse_elf
    from scripts.hermes_kanban_mcp_inventory import (
        InventoryBuilder,
        InventoryError,
        InventoryPlan,
        MAX_DIRECTORY_DEPTH,
    )
    from scripts.hermes_kanban_mcp_resources import ResourceBudgetError
except ImportError:  # Direct execution from scripts/.
    import hermes_kanban_mcp_resources as resources
    import hermes_kanban_mcp_invocation as invocation
    from hermes_kanban_mcp_elf import ElfFormatError, ElfInfo, parse_elf
    from hermes_kanban_mcp_inventory import (
        InventoryBuilder,
        InventoryError,
        InventoryPlan,
        MAX_DIRECTORY_DEPTH,
    )
    from hermes_kanban_mcp_resources import ResourceBudgetError


SandboxError = resources.SandboxError


@dataclass(frozen=True)
class SealedEntry:
    source: Path | None
    destination: Path
    kind: str
    mode: int
    fd: int | None = None
    size: int = 0
    digest: str | None = None
    target: str | None = None


_FDResourceOwner = resources.FDResourceOwner


@dataclass
class SealedContentBundle:
    owner: _FDResourceOwner
    entries: tuple[SealedEntry, ...]
    manifest_sha256: str
    source_digest: str
    venv_digest: str
    bwrap_sha256: str
    bwrap_fd: int
    loader_fd: int
    bwrap_library_fds: tuple[int, ...]
    runtime: Path
    venv_dirname: str
    invocation: invocation.CanonicalInvocationSpec

    @property
    def descriptors(self) -> tuple[int, ...]:
        return self.owner.fds

    def add_data(self, name: str, data: bytes) -> int:
        return _data_fd(self.owner, name, data)

    def file_entry(self, destination: Path) -> SealedEntry:
        mapping = {entry.destination: entry for entry in self.entries}
        entry = _resolve_manifest_entry(destination, mapping)
        if entry is None or entry.kind != "file":
            raise SandboxError(
                f"sealed manifest does not resolve to a regular file: {destination}"
            )
        return entry

    def read_file(self, destination: Path) -> bytes:
        entry = self.file_entry(destination)
        if entry.fd is None:
            raise SandboxError("sealed regular file has no descriptor")
        return _read_all(entry.fd)

    def verify(self) -> None:
        if self.owner.closed:
            raise SandboxError("sealed content bundle closed before verification")
        for entry in self.entries:
            if entry.kind == "file":
                if entry.fd is None or entry.digest is None:
                    raise SandboxError("sealed manifest contains incomplete regular file")
                _verify_sealed_fd(entry.fd, entry.digest, entry.size)

    def close(
        self,
        *,
        primary: BaseException | None = None,
        replacement_applied: bool = False,
    ) -> None:
        self.owner.close_all(
            primary=primary, replacement_applied=replacement_applied
        )


class _BundleBuilder:
    def __init__(
        self,
        runtime: Path,
        venv_dirname: str,
        trusted_interpreter: Path,
        stdlib_roots: Sequence[Path],
        bwrap_path: Path,
        inventory: InventoryPlan,
        invocation_spec: invocation.CanonicalInvocationSpec,
    ) -> None:
        self.owner = _FDResourceOwner()
        self.runtime = runtime
        self.venv_dirname = venv_dirname
        self.trusted_interpreter = trusted_interpreter
        self.stdlib_roots = tuple(stdlib_roots)
        self.bwrap_path = bwrap_path
        self.inventory = inventory
        self.invocation_spec = invocation_spec
        self.entries: dict[Path, SealedEntry] = {}
        self.sources: dict[Path, Path] = {}

    def _add(self, entry: SealedEntry) -> SealedEntry:
        existing = self.entries.get(entry.destination)
        if existing is not None:
            if (
                existing.kind != entry.kind
                or existing.mode != entry.mode
                or existing.digest != entry.digest
                or existing.target != entry.target
            ):
                raise SandboxError(
                    f"sealed manifest destination is ambiguous: {entry.destination}"
                )
            if entry.fd is not None:
                self.owner.close_one(entry.fd)
            return existing
        self.entries[entry.destination] = entry
        if entry.source is not None:
            self.sources[entry.destination] = entry.source
        return entry

    def _directory(self, source: Path | None, destination: Path, mode: int) -> None:
        self._add(
            SealedEntry(
                source, destination, "directory", stat.S_IMODE(mode)
            )
        )

    def _symlink(
        self, source: Path, destination: Path, mode: int, target: str
    ) -> None:
        if not target or "\x00" in target:
            raise SandboxError("sealed manifest contains an invalid symlink target")
        self._add(
            SealedEntry(
                source,
                destination,
                "symlink",
                stat.S_IMODE(mode),
                target=target,
            )
        )

    def _regular_from_fd(
        self,
        source: Path,
        destination: Path,
        source_fd: int,
        before: os.stat_result,
    ) -> SealedEntry:
        try:
            data = _read_all(source_fd)
            after = os.fstat(source_fd)
            if _capture_identity(before) != _capture_identity(after):
                raise SandboxError(f"regular file changed during capture: {source}")
            fd, digest = _sealed_data_fd(
                self.owner, f"kanban-{source.name}", data
            )
            return self._add(
                SealedEntry(
                    source,
                    destination,
                    "file",
                    stat.S_IMODE(before.st_mode),
                    fd,
                    len(data),
                    digest,
                )
            )
        finally:
            self.owner.close_one(source_fd, primary=sys.exception())

    def _walk_tree(
        self,
        source: Path,
        destination: Path,
        *,
        excluded: Sequence[str] = (),
    ) -> None:
        if source == destination:
            self._capture_parent_topology(source)
        root_fd = _open_owned(self.owner, source, directory=True)
        try:
            root_info = os.fstat(root_fd)
            self._directory(source, destination, root_info.st_mode)
            self._walk_directory(
                root_fd, source, destination, set(excluded), 0
            )
        finally:
            self.owner.close_one(root_fd, primary=sys.exception())

    def _capture_parent_topology(self, source: Path) -> None:
        """Create only the existing ancestor directories needed for an absolute tree."""
        for parent in reversed(source.parents[:-1]):
            if parent in self.entries:
                continue
            descriptor = _open_owned(self.owner, parent, directory=True)
            try:
                info = os.fstat(descriptor)
                self._directory(parent, parent, info.st_mode)
            finally:
                self.owner.close_one(descriptor, primary=sys.exception())

    def _walk_directory(
        self,
        directory_fd: int,
        source: Path,
        destination: Path,
        excluded: set[str],
        depth: int,
    ) -> None:
        if depth > MAX_DIRECTORY_DEPTH:
            raise SandboxError("acquisition directory depth exceeds named maximum")
        before = os.fstat(directory_fd)
        try:
            names = sorted(os.listdir(directory_fd))
        except OSError as exc:
            raise SandboxError(f"cannot list trusted tree: {source}") from exc
        if excluded:
            names = [name for name in names if name not in excluded]
        for name in names:
            child_source = source / name
            child_destination = destination / name
            try:
                info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as exc:
                raise SandboxError(
                    f"trusted tree changed during manifest capture: {child_source}"
                ) from exc
            if stat.S_ISDIR(info.st_mode):
                if depth >= MAX_DIRECTORY_DEPTH:
                    raise SandboxError(
                        "acquisition directory depth exceeds named maximum"
                    )
                child_fd = _openat_owned(
                    self.owner, directory_fd, name, directory=True
                )
                try:
                    opened = os.fstat(child_fd)
                    if _capture_identity(info) != _capture_identity(opened):
                        raise SandboxError(
                            f"directory changed during capture: {child_source}"
                        )
                    self._directory(
                        child_source, child_destination, opened.st_mode
                    )
                    self._walk_directory(
                        child_fd, child_source, child_destination, set(), depth + 1
                    )
                finally:
                    self.owner.close_one(child_fd, primary=sys.exception())
            elif stat.S_ISREG(info.st_mode):
                child_fd = _openat_owned(self.owner, directory_fd, name)
                handed_off = False
                try:
                    opened = os.fstat(child_fd)
                    if _capture_identity(info) != _capture_identity(opened):
                        raise SandboxError(
                            f"regular file changed during capture: {child_source}"
                        )
                    self._regular_from_fd(
                        child_source, child_destination, child_fd, opened
                    )
                    handed_off = True
                finally:
                    if not handed_off and child_fd in self.owner.fds:
                        self.owner.close_one(child_fd, primary=sys.exception())
            elif stat.S_ISLNK(info.st_mode):
                try:
                    target = os.readlink(name, dir_fd=directory_fd)
                    after = os.stat(
                        name, dir_fd=directory_fd, follow_symlinks=False
                    )
                except OSError as exc:
                    raise SandboxError(
                        f"symlink changed during capture: {child_source}"
                    ) from exc
                if _capture_identity(info) != _capture_identity(after):
                    raise SandboxError(
                        f"symlink changed during capture: {child_source}"
                    )
                self._symlink(
                    child_source, child_destination, info.st_mode, target
                )
            else:
                raise SandboxError(
                    f"trusted tree contains unsupported file type: {child_source}"
                )
        try:
            if names != sorted(
                name for name in os.listdir(directory_fd) if name not in excluded
            ) or _capture_identity(before) != _capture_identity(os.fstat(directory_fd)):
                raise SandboxError(f"directory changed during capture: {source}")
        except OSError as exc:
            raise SandboxError(f"directory changed during capture: {source}") from exc

    def _capture_external(self, raw: Path) -> SealedEntry:
        path = _lexical_absolute(raw)
        if not _trusted_system_path(path):
            raise SandboxError(f"runtime closure escaped trusted system roots: {path}")
        pending = list(path.parts[1:])
        current = Path("/")
        directory_fd = _open_owned(self.owner, Path("/"), directory=True)
        followed: set[Path] = set()
        try:
            while pending:
                name = pending.pop(0)
                source = current / name
                try:
                    info = os.stat(
                        name, dir_fd=directory_fd, follow_symlinks=False
                    )
                except OSError as exc:
                    raise SandboxError(
                        f"required runtime closure file is unavailable: {source}"
                    ) from exc
                if stat.S_ISLNK(info.st_mode):
                    if source in followed:
                        raise SandboxError("runtime closure contains a symlink cycle")
                    followed.add(source)
                    target = os.readlink(name, dir_fd=directory_fd)
                    self._symlink(source, source, info.st_mode, target)
                    resolved = (
                        Path(target)
                        if Path(target).is_absolute()
                        else current / target
                    )
                    resolved = _lexical_absolute(
                        Path(resolved, *pending)
                    )
                    if not _trusted_system_path(resolved):
                        raise SandboxError(
                            "runtime closure symlink escaped trusted system roots"
                        )
                    self.owner.close_one(directory_fd)
                    directory_fd = _open_owned(
                        self.owner, Path("/"), directory=True
                    )
                    current = Path("/")
                    pending = list(resolved.parts[1:])
                    continue
                if pending:
                    if not stat.S_ISDIR(info.st_mode):
                        raise SandboxError(
                            f"runtime closure component is not a directory: {source}"
                        )
                    next_fd = _openat_owned(
                        self.owner, directory_fd, name, directory=True
                    )
                    self._directory(source, source, info.st_mode)
                    self.owner.close_one(directory_fd)
                    directory_fd = next_fd
                    current = source
                    continue
                if not stat.S_ISREG(info.st_mode):
                    raise SandboxError(
                        f"runtime closure endpoint is not a regular file: {source}"
                    )
                file_fd = _openat_owned(self.owner, directory_fd, name)
                handed_off = False
                try:
                    opened = os.fstat(file_fd)
                    if _capture_identity(info) != _capture_identity(opened):
                        raise SandboxError(
                            f"runtime closure file changed during capture: {source}"
                        )
                    result = self._regular_from_fd(source, source, file_fd, opened)
                    handed_off = True
                    return result
                finally:
                    if not handed_off and file_fd in self.owner.fds:
                        self.owner.close_one(file_fd, primary=sys.exception())
        finally:
            self.owner.close_one(directory_fd, primary=sys.exception())
        raise SandboxError(f"runtime closure path is incomplete: {path}")

    def _capture_missing_system_symlink_targets(self) -> None:
        while True:
            missing: list[Path] = []
            for destination, entry in tuple(self.entries.items()):
                if entry.kind != "symlink":
                    continue
                resolved = _symlink_target(destination, entry.target or "")
                if resolved not in self.entries:
                    missing.append(resolved)
            if not missing:
                return
            progress = False
            for path in missing:
                if _trusted_system_path(path):
                    self._capture_external(path)
                    progress = True
                else:
                    raise SandboxError(
                        f"sealed manifest contains dangling or escaping symlink: {path}"
                    )
            if not progress:
                raise SandboxError("sealed manifest symlink closure is incomplete")

    def build(self) -> SealedContentBundle:
        try:
            try:
                observed_inventory = _topology_inventory(
                    self.runtime,
                    self.venv_dirname,
                    self.stdlib_roots,
                )
            except (InventoryError, OSError) as exc:
                raise SandboxError(str(exc), primary=exc) from exc
            _verify_preflight_inventory(
                self.inventory,
                observed_inventory,
                self.runtime,
                self.venv_dirname,
                self.stdlib_roots,
            )
            self._walk_tree(
                self.runtime,
                invocation.SANDBOX_RUNTIME,
                excluded=(".git", self.venv_dirname),
            )
            self._walk_tree(
                self.runtime / self.venv_dirname,
                invocation.SANDBOX_RUNTIME / self.venv_dirname,
            )
            for root in self.stdlib_roots:
                # -S makes sitecustomize unreachable; Debian's link escapes to /etc.
                self._walk_tree(root, root, excluded=("sitecustomize.py",))
            for planned in self.inventory.entries:
                if (
                    planned.destination == planned.source
                    and _trusted_system_path(planned.source)
                    and planned.destination not in self.entries
                    and planned.kind == "directory"
                ):
                    self._directory(planned.source, planned.destination, planned.mode)
                elif (
                    planned.destination == planned.source
                    and _trusted_system_path(planned.source)
                    and planned.destination not in self.entries
                    and planned.kind == "symlink"
                ):
                    self._symlink(
                        planned.source,
                        planned.destination,
                        planned.mode,
                        planned.target or "",
                    )
            for planned in self.inventory.entries:
                if (
                    planned.destination == planned.source
                    and _trusted_system_path(planned.source)
                    and planned.kind == "file"
                    and planned.destination not in self.entries
                ):
                    self._capture_external(planned.source)
            bwrap = self.entries[self.bwrap_path]
            closure = {
                source: tuple(self.entries[path] for path in dependencies)
                for source, dependencies in self.inventory.elf_dependencies
            }
            self._capture_missing_system_symlink_targets()
            loader = next(
                (
                    item
                    for item in closure.get(bwrap.destination, ())
                    if item.destination.name.startswith("ld-")
                    or "ld-linux" in item.destination.name
                ),
                None,
            )
            if loader is None:
                parsed = _parse_elf(self._entry_bytes(bwrap))
                if parsed is None or parsed.interpreter is None:
                    raise SandboxError("sealed bwrap has no supported ELF interpreter")
                loader = self._capture_external(Path(parsed.interpreter))
            bwrap_libraries = tuple(
                item
                for item in _dependency_closure(bwrap, closure)
                if item.destination != loader.destination
            )
            entries = tuple(
                sorted(self.entries.values(), key=lambda item: str(item.destination))
            )
            _verify_inventory(self.inventory, entries)
            _validate_manifest(entries)
            manifest = _manifest_bytes(entries)
            source_digest = _tree_digest(
                entries,
                invocation.SANDBOX_RUNTIME,
                excluded=(self.venv_dirname,),
            )
            venv_digest = _tree_digest(
                entries, invocation.SANDBOX_RUNTIME / self.venv_dirname
            )
            bundle = SealedContentBundle(
                self.owner,
                entries,
                hashlib.sha256(manifest).hexdigest(),
                source_digest,
                venv_digest,
                bwrap.digest or "",
                bwrap.fd or -1,
                loader.fd or -1,
                tuple(item.fd or -1 for item in bwrap_libraries),
                self.runtime,
                self.venv_dirname,
                self.invocation_spec,
            )
            bundle.verify()
            return bundle
        except BaseException as exc:
            try:
                self.owner.close_all(primary=exc)
            except SandboxError as cleanup:
                raise cleanup from exc
            if isinstance(exc, SandboxError):
                raise
            raise SandboxError("cannot build sealed content bundle", primary=exc) from exc


def _capture_identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _lexical_absolute(path: Path) -> Path:
    if not path.is_absolute() or "\x00" in str(path):
        raise SandboxError(f"sealed manifest path is not absolute: {path}")
    normalized = Path(os.path.normpath(path))
    if not normalized.is_absolute() or ".." in normalized.parts:
        raise SandboxError(f"sealed manifest path escaped: {path}")
    return normalized


def _trusted_system_path(path: Path) -> bool:
    return any(path == root or root in path.parents for root in map(Path, ("/usr", "/lib", "/lib64")))


def _open_owned(
    owner: _FDResourceOwner, path: Path, *, directory: bool = False
) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        return owner.register(os.open(path, flags))
    except OSError as exc:
        raise SandboxError(f"cannot descriptor-capture {path}") from exc


def _openat_owned(
    owner: _FDResourceOwner,
    directory_fd: int,
    name: str,
    *,
    directory: bool = False,
) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        return owner.register(os.open(name, flags, dir_fd=directory_fd))
    except OSError as exc:
        raise SandboxError(f"cannot descriptor-capture component: {name}") from exc


def _read_all(fd: int) -> bytes:
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = []
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
    os.lseek(fd, 0, os.SEEK_SET)
    return b"".join(chunks)


def _sealed_data_fd(
    owner: _FDResourceOwner, name: str, data: bytes
) -> tuple[int, str]:
    descriptor = -1
    try:
        flags = getattr(os, "MFD_CLOEXEC", 0) | getattr(os, "MFD_ALLOW_SEALING", 0)
        descriptor = owner.register(os.memfd_create(name, flags=flags))
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write while capturing sealed data")
            view = view[written:]
        os.lseek(descriptor, 0, os.SEEK_SET)
        digest = hashlib.sha256(_read_all(descriptor)).hexdigest()
        seals = (
            fcntl.F_SEAL_WRITE
            | fcntl.F_SEAL_GROW
            | fcntl.F_SEAL_SHRINK
            | fcntl.F_SEAL_SEAL
        )
        if hasattr(fcntl, "F_SEAL_FUTURE_WRITE"):
            seals |= fcntl.F_SEAL_FUTURE_WRITE
        fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS, seals)
        observed = fcntl.fcntl(descriptor, fcntl.F_GET_SEALS)
        if observed & seals != seals:
            raise OSError("kernel did not apply the required memfd seals")
        _verify_sealed_fd(descriptor, digest, len(data))
        return descriptor, digest
    except BaseException as exc:
        if descriptor >= 0 and descriptor in owner.fds:
            try:
                owner.close_one(descriptor, primary=exc)
            except SandboxError as cleanup:
                raise cleanup from exc
        if isinstance(exc, SandboxError):
            raise
        raise SandboxError("cannot create sealed preflight data", primary=exc) from exc


def _data_fd(owner: _FDResourceOwner, name: str, data: bytes) -> int:
    return _sealed_data_fd(owner, name, data)[0]


def _verify_sealed_fd(fd: int, digest: str, size: int) -> None:
    info = os.fstat(fd)
    required = (
        fcntl.F_SEAL_WRITE
        | fcntl.F_SEAL_GROW
        | fcntl.F_SEAL_SHRINK
        | fcntl.F_SEAL_SEAL
    )
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_size != size
        or fcntl.fcntl(fd, fcntl.F_GET_SEALS) & required != required
        or hashlib.sha256(_read_all(fd)).hexdigest() != digest
    ):
        raise SandboxError("sealed content descriptor verification failed")


def _parse_elf(data: bytes) -> ElfInfo | None:
    try:
        return parse_elf(data)
    except (ElfFormatError, UnicodeDecodeError) as exc:
        raise SandboxError(str(exc)) from exc


def _symlink_target(destination: Path, target: str) -> Path:
    return _lexical_absolute(
        Path(target) if Path(target).is_absolute() else destination.parent / target
    )


def _resolve_manifest_entry(
    destination: Path, entries: dict[Path, SealedEntry]
) -> SealedEntry | None:
    current = destination
    seen = set()
    for _ in range(41):
        entry = entries.get(current)
        if entry is None or entry.kind != "symlink":
            return entry
        if current in seen:
            raise SandboxError("sealed manifest contains a symlink cycle")
        seen.add(current)
        current = _symlink_target(current, entry.target or "")
    raise SandboxError("sealed manifest symlink chain is too deep")


def _dependency_closure(
    root: SealedEntry, direct: dict[Path, tuple[SealedEntry, ...]]
) -> tuple[SealedEntry, ...]:
    result = []
    seen = {root.destination}
    queue = list(direct.get(root.destination, ()))
    while queue:
        item = queue.pop(0)
        if item.destination in seen:
            continue
        seen.add(item.destination)
        result.append(item)
        queue.extend(direct.get(item.destination, ()))
    return tuple(result)


def _manifest_bytes(entries: Sequence[SealedEntry]) -> bytes:
    rows = []
    for entry in entries:
        row = {
            "path": str(entry.destination),
            "kind": entry.kind,
            "mode": entry.mode,
        }
        if entry.kind == "file":
            row.update({"size": entry.size, "sha256": entry.digest})
        elif entry.kind == "symlink":
            row["target"] = entry.target
        rows.append(row)
    import json

    return json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()


def _tree_digest(
    entries: Sequence[SealedEntry],
    root: Path,
    *,
    excluded: Sequence[str] = (),
) -> str:
    by_parent: dict[Path, list[SealedEntry]] = {}
    for entry in entries:
        if entry.destination == root:
            continue
        try:
            relative = entry.destination.relative_to(root)
        except ValueError:
            continue
        if relative.parts and relative.parts[0] in excluded:
            continue
        by_parent.setdefault(entry.destination.parent, []).append(entry)
    digest = hashlib.sha256()
    def visit(parent: Path) -> None:
        children = by_parent.get(parent, [])
        directories = sorted(
            (item for item in children if item.kind == "directory"),
            key=lambda item: item.destination.name,
        )
        others = sorted(
            (item for item in children if item.kind != "directory"),
            key=lambda item: item.destination.name,
        )
        for entry in (*directories, *others):
            relative = entry.destination.relative_to(root).as_posix().encode()
            type_mode = {
                "directory": stat.S_IFDIR,
                "file": stat.S_IFREG,
                "symlink": stat.S_IFLNK,
            }[entry.kind]
            digest.update(
                relative
                + b"\0"
                + str(type_mode | entry.mode).encode()
                + b"\0"
            )
            if entry.kind == "directory":
                digest.update(b"D")
            elif entry.kind == "file":
                digest.update(b"F")
                if entry.fd is None:
                    raise SandboxError("sealed tree digest is missing file bytes")
                for chunk in _iter_fd(entry.fd):
                    digest.update(chunk)
            else:
                digest.update(b"L" + (entry.target or "").encode())
        for entry in directories:
            visit(entry.destination)

    visit(root)
    return digest.hexdigest()


def _iter_fd(fd: int) -> Iterable[bytes]:
    os.lseek(fd, 0, os.SEEK_SET)
    try:
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                return
            yield chunk
    finally:
        os.lseek(fd, 0, os.SEEK_SET)


def _validate_manifest(entries: Sequence[SealedEntry]) -> None:
    mapping = {entry.destination: entry for entry in entries}
    if len(mapping) != len(entries):
        raise SandboxError("sealed manifest contains duplicate destinations")
    for entry in entries:
        if entry.destination == Path("/") or not entry.destination.is_absolute():
            raise SandboxError("sealed manifest destination is invalid")
        parent = entry.destination.parent
        if parent != Path("/") and (
            parent not in mapping or mapping[parent].kind != "directory"
        ):
            raise SandboxError(
                f"sealed manifest is missing parent topology: {entry.destination}"
            )
        if entry.kind == "symlink":
            target = _symlink_target(entry.destination, entry.target or "")
            resolved = _resolve_manifest_entry(target, mapping)
            if resolved is None:
                raise SandboxError(
                    f"sealed manifest contains dangling symlink: {entry.destination}"
                )
        elif entry.kind == "file":
            if entry.fd is None or entry.digest is None:
                raise SandboxError("sealed manifest regular file is incomplete")


def _verify_inventory(
    inventory: InventoryPlan, entries: Sequence[SealedEntry]
) -> None:
    observed = {entry.destination: entry for entry in entries}
    checked = tuple(
        entry
        for entry in inventory.entries
        if entry.kind == "file" or not _trusted_system_path(entry.destination)
    )
    planned_paths = {entry.destination for entry in checked}
    if not planned_paths.issubset(observed):
        missing = sorted(map(str, planned_paths - set(observed)))[:3]
        raise SandboxError(
            f"topology changed between inventory and sealed acquisition "
            f"(missing={missing})"
        )
    for planned in checked:
        entry = observed[planned.destination]
        if (
            entry.kind != planned.kind
            or entry.mode != planned.mode
            or entry.size != planned.size
            or entry.digest != planned.digest
            or entry.target != planned.target
        ):
            raise SandboxError(
                f"object changed between inventory and sealed acquisition: {planned.source}"
            )
        if planned.kind != "directory":
            try:
                current = os.lstat(planned.source)
            except OSError as exc:
                raise SandboxError(
                    f"object disappeared after sealed acquisition: {planned.source}"
                ) from exc
            if _capture_identity(current) != planned.identity:
                raise SandboxError(
                    f"object identity changed between inventory and acquisition: {planned.source}"
                )


def _verify_preflight_inventory(
    expected: InventoryPlan, observed: InventoryPlan, runtime: Path,
    venv_dirname: str, stdlib_roots: Sequence[Path],
) -> None:
    sandbox_runtime = invocation.SANDBOX_RUNTIME
    covered = (
        (runtime, sandbox_runtime),
        (runtime / venv_dirname, sandbox_runtime / venv_dirname),
        *((root, root) for root in stdlib_roots),
    )
    def signature(entry: object) -> tuple[Path, Path, str, int, str | None]:
        return entry.source, entry.destination, entry.kind, entry.mode, entry.target

    planned = {
        signature(entry)
        for entry in expected.entries
        if any(
            entry.source == source or source in entry.source.parents
            for source, _destination in covered
        )
    }
    actual = {
        signature(entry)
        for entry in observed.entries
        if any(
            entry.source == source or source in entry.source.parents
            for source, _destination in covered
        )
    }
    if actual != planned:
        raise SandboxError(
            "topology changed between inventory and sealed acquisition preflight"
        )

def _topology_inventory(
    runtime: Path, venv_dirname: str, stdlib_roots: Sequence[Path]
) -> InventoryPlan:
    builder = InventoryBuilder()
    trees = (
        (runtime, invocation.SANDBOX_RUNTIME, (".git", venv_dirname)),
        (
            runtime / venv_dirname,
            invocation.SANDBOX_RUNTIME / venv_dirname,
            (),
        ),
        *((root, root, ("sitecustomize.py",)) for root in stdlib_roots),
    )
    for source, destination, excluded in trees:
        builder.tree(
            source,
            destination,
            excluded=excluded,
            capture_content=False,
        )
    return builder.finish()


def _inventory(
    runtime: Path,
    venv_dirname: str,
    trusted_interpreter: Path,
    stdlib_roots: Sequence[Path],
    bwrap_path: Path,
) -> InventoryPlan:
    builder = InventoryBuilder()
    builder.tree(
        runtime, invocation.SANDBOX_RUNTIME, excluded=(".git", venv_dirname)
    )
    builder.tree(
        runtime / venv_dirname,
        invocation.SANDBOX_RUNTIME / venv_dirname,
    )
    for root in stdlib_roots:
        builder.tree(root, root, excluded=("sitecustomize.py",))
    trusted = builder.external(trusted_interpreter)
    bwrap = builder.external(bwrap_path)
    initial = [
        entry.destination
        for entry in builder.entries.values()
        if entry.kind == "file"
    ]
    if trusted.destination not in initial:
        initial.append(trusted.destination)
    if bwrap.destination not in initial:
        initial.append(bwrap.destination)
    builder.elf_closure(initial)
    return builder.finish()


def capture_bundle(
    runtime: Path,
    venv_dirname: str,
    trusted_interpreter: Path,
    stdlib_roots: Sequence[Path],
    *,
    bwrap_path: Path = invocation.BWRAP,
) -> SealedContentBundle:
    """Inventory, budget, then acquire and revalidate every sealed byte."""
    try:
        inventory = _inventory(
            runtime, venv_dirname, trusted_interpreter, stdlib_roots, bwrap_path
        )
        invocation_spec, _resource_plan = invocation.build(
            inventory.entries,
            inventory.elf_dependencies,
            bwrap_path=bwrap_path,
            venv_dirname=venv_dirname,
            acquisition_temporary_fds=inventory.acquisition_temporary_fds,
        )
    except (InventoryError, ResourceBudgetError, OSError) as exc:
        raise SandboxError(str(exc), primary=exc) from exc
    return _BundleBuilder(
        runtime,
        venv_dirname,
        trusted_interpreter,
        stdlib_roots,
        bwrap_path,
        inventory,
        invocation_spec,
    ).build()
