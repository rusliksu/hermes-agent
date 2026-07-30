"""Bounded ELF metadata parsing and GNU loader search semantics."""

from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


class ElfFormatError(ValueError):
    """An executable uses malformed or unsupported ELF metadata."""


@dataclass(frozen=True)
class ElfInfo:
    interpreter: str | None
    needed: tuple[str, ...]
    rpath: tuple[str, ...]
    runpath: tuple[str, ...]

    @property
    def search_paths(self) -> tuple[str, ...]:
        """Compatibility view; resolution must use rpath/runpath directly."""
        return self.runpath if self.runpath else self.rpath


@dataclass(frozen=True)
class LoaderPlatform:
    lib: str
    platform: str


_TOKEN = re.compile(r"\$(?:\{([^}]+)\}|([A-Za-z_][A-Za-z0-9_]*))")


def _bounded_text(strings: bytes, offset: int, label: str) -> str:
    if offset < 0 or offset >= len(strings):
        raise ElfFormatError(f"runtime closure ELF {label} offset is invalid")
    end = strings.find(b"\0", offset)
    if end < 0:
        raise ElfFormatError(f"runtime closure ELF {label} is not NUL-terminated")
    try:
        return strings[offset:end].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ElfFormatError(f"runtime closure ELF {label} is not UTF-8") from exc


def _split_paths(value: str, label: str) -> tuple[str, ...]:
    parts = tuple(value.split(":"))
    if not parts or any(not item for item in parts):
        raise ElfFormatError(f"runtime closure ELF {label} contains an empty entry")
    return parts


def parse_elf(data: bytes) -> ElfInfo | None:
    if len(data) < 16 or data[:4] != b"\x7fELF":
        return None
    elf_class, encoding = data[4], data[5]
    if elf_class not in (1, 2) or encoding not in (1, 2):
        raise ElfFormatError("runtime closure contains unsupported ELF format")
    endian = "<" if encoding == 1 else ">"
    header_format = endian + (
        "HHIIIIIHHHHHH" if elf_class == 1 else "HHIQQQIHHHHHH"
    )
    header_size = struct.calcsize(header_format)
    if len(data) < 16 + header_size:
        raise ElfFormatError("runtime closure contains truncated ELF header")
    header = struct.unpack_from(header_format, data, 16)
    phoff, phentsize, phnum = header[4], header[8], header[9]
    if phnum == 0:
        return ElfInfo(None, (), (), ())
    ph_format = endian + ("IIIIIIII" if elf_class == 1 else "IIQQQQQQ")
    expected_ph_size = struct.calcsize(ph_format)
    if (
        phentsize < expected_ph_size
        or phnum > 65535
        or phoff > len(data)
        or phentsize * phnum > len(data) - phoff
    ):
        raise ElfFormatError("runtime closure contains malformed ELF headers")
    loads: list[tuple[int, int, int, int]] = []
    dynamic: tuple[int, int] | None = None
    interpreter: str | None = None
    for index in range(phnum):
        values = struct.unpack_from(ph_format, data, phoff + index * phentsize)
        if elf_class == 1:
            p_type, offset, vaddr, filesz, memsz = (
                values[0], values[1], values[2], values[4], values[5]
            )
        else:
            p_type, offset, vaddr, filesz, memsz = (
                values[0], values[2], values[3], values[5], values[6]
            )
        if offset > len(data) or filesz > len(data) - offset:
            raise ElfFormatError("runtime closure contains truncated ELF segment")
        if p_type == 1:
            loads.append((vaddr, vaddr + filesz, offset, memsz))
        elif p_type == 2:
            if dynamic is not None:
                raise ElfFormatError("runtime closure contains multiple dynamic segments")
            dynamic = (offset, filesz)
        elif p_type == 3:
            raw = data[offset : offset + filesz]
            if not raw or raw[-1:] != b"\0" or b"\0" in raw[:-1]:
                raise ElfFormatError("runtime closure ELF interpreter is malformed")
            interpreter = raw[:-1].decode("utf-8")
            if not interpreter.startswith("/") or "\x00" in interpreter:
                raise ElfFormatError("runtime closure ELF interpreter is unsafe")
    if dynamic is None:
        return ElfInfo(interpreter, (), (), ())
    entry_format = endian + ("iI" if elf_class == 1 else "qQ")
    entry_size = struct.calcsize(entry_format)
    dynamic_offset, dynamic_size = dynamic
    if dynamic_size == 0 or dynamic_size % entry_size:
        raise ElfFormatError("runtime closure ELF dynamic segment has invalid size")
    if dynamic_size // entry_size > 1_000_000:
        raise ElfFormatError("runtime closure ELF dynamic segment is too large")
    needed_offsets: list[int] = []
    rpath_offsets: list[int] = []
    runpath_offsets: list[int] = []
    string_vaddr: int | None = None
    string_size: int | None = None
    terminated = False
    for offset in range(dynamic_offset, dynamic_offset + dynamic_size, entry_size):
        tag, value = struct.unpack_from(entry_format, data, offset)
        if tag == 0:
            terminated = True
            break
        if tag == 1:
            needed_offsets.append(value)
        elif tag == 15:
            rpath_offsets.append(value)
        elif tag == 29:
            runpath_offsets.append(value)
        elif tag == 5:
            string_vaddr = value
        elif tag == 10:
            string_size = value
    if not terminated:
        raise ElfFormatError("runtime closure ELF dynamic segment lacks bounded DT_NULL")
    offsets = needed_offsets + rpath_offsets + runpath_offsets
    if string_vaddr is None or string_size is None:
        if offsets:
            raise ElfFormatError("runtime closure ELF string table is incomplete")
        return ElfInfo(interpreter, (), (), ())
    string_offset = next(
        (
            file_offset + string_vaddr - start
            for start, end, file_offset, _memsz in loads
            if start <= string_vaddr < end
        ),
        None,
    )
    if (
        string_offset is None
        or string_size < 0
        or string_offset > len(data)
        or string_size > len(data) - string_offset
    ):
        raise ElfFormatError("runtime closure ELF string table is invalid")
    strings = data[string_offset : string_offset + string_size]
    needed = tuple(_bounded_text(strings, offset, "DT_NEEDED") for offset in needed_offsets)
    for soname in needed:
        if (
            not soname
            or "/" in soname
            or "\x00" in soname
            or soname in (".", "..")
            or ".." in Path(soname).parts
        ):
            raise ElfFormatError("runtime closure ELF DT_NEEDED is unsafe")
    rpath = tuple(
        item
        for offset in rpath_offsets
        for item in _split_paths(_bounded_text(strings, offset, "DT_RPATH"), "DT_RPATH")
    )
    runpath = tuple(
        item
        for offset in runpath_offsets
        for item in _split_paths(_bounded_text(strings, offset, "DT_RUNPATH"), "DT_RUNPATH")
    )
    return ElfInfo(interpreter, needed, rpath, runpath)


def expand_loader_path(
    raw: str,
    *,
    origin: Path,
    platform: LoaderPlatform,
    allowed_roots: Sequence[Path],
) -> Path:
    def replacement(match: re.Match[str]) -> str:
        token = match.group(1) or match.group(2)
        values = {"ORIGIN": str(origin), "LIB": platform.lib, "PLATFORM": platform.platform}
        if token not in values or not values[token]:
            raise ElfFormatError(f"unsupported or unresolved loader token: ${token}")
        return values[token]

    expanded = _TOKEN.sub(replacement, raw)
    if "$" in expanded:
        raise ElfFormatError("unsupported loader token syntax")
    path = Path(expanded)
    if not path.is_absolute() or "\x00" in expanded:
        raise ElfFormatError("loader search entry is relative or unsafe")
    normalized = Path(os.path.normpath(path))
    if ".." in path.parts or not any(
        normalized == root or root in normalized.parents for root in allowed_roots
    ):
        raise ElfFormatError("loader search entry escapes trusted roots")
    return normalized


def dependency_search(
    info: ElfInfo,
    *,
    origin: Path,
    inherited_rpath: Sequence[Path],
    platform: LoaderPlatform,
    allowed_roots: Sequence[Path],
    defaults: Sequence[Path],
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    """Return direct search order and legacy RPATH inherited by children."""
    if info.runpath:
        direct = tuple(inherited_rpath) + tuple(
            expand_loader_path(
                item, origin=origin, platform=platform, allowed_roots=allowed_roots
            )
            for item in info.runpath
        )
        child_inherited = tuple(inherited_rpath)
    else:
        own_rpath = tuple(
            expand_loader_path(
                item, origin=origin, platform=platform, allowed_roots=allowed_roots
            )
            for item in info.rpath
        )
        direct = own_rpath + tuple(inherited_rpath)
        child_inherited = own_rpath + tuple(inherited_rpath)
    return direct + tuple(defaults), child_inherited
