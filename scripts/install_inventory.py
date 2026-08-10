#!/usr/bin/env python3
"""Canonical installed-package inventory codec and safe tree reader."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


INVENTORY_FORMAT = "sha256-size-path-v1"
HEX_64_RE = re.compile(r"[0-9a-f]{64}\Z")
SAFE_COMPONENT_FORBIDDEN = {"", ".", ".."}
_HELD_PYTHON_FD_LAUNCHER = r"""
import hashlib
import json
import os
import stat
import sys

descriptor = int(sys.argv[1])
expected_sha256 = sys.argv[2]
display_path = sys.argv[3]
authenticated_source_sha256 = json.loads(sys.argv[4])
script_arguments = sys.argv[5:]
before = os.fstat(descriptor)
if not stat.S_ISREG(before.st_mode):
    raise SystemExit("held Python source is not a regular file")
chunks = []
offset = 0
while True:
    chunk = os.pread(descriptor, 1024 * 1024, offset)
    if not chunk:
        break
    chunks.append(chunk)
    offset += len(chunk)
source = b"".join(chunks)
after = os.fstat(descriptor)
identity = lambda value: (
    value.st_dev,
    value.st_ino,
    value.st_size,
    value.st_mtime_ns,
    value.st_ctime_ns,
)
if identity(before) != identity(after) or len(source) != after.st_size:
    raise SystemExit("held Python source changed while it was read")
if hashlib.sha256(source).hexdigest() != expected_sha256:
    raise SystemExit("held Python source digest differs from authenticated bytes")
sys.argv = [display_path, *script_arguments]
namespace = {
    "__name__": "__main__",
    "__file__": display_path,
    "__package__": None,
    "__cached__": None,
    "__loader__": None,
    "__spec__": None,
    "__builtins__": __builtins__,
    "__authenticated_source_bytes__": source,
    "__authenticated_source_sha256__": authenticated_source_sha256,
}
exec(compile(source, display_path, "exec"), namespace, namespace)
"""


class PublicationError(RuntimeError):
    """A fail-closed publication result which requires human inspection."""


@dataclass(frozen=True)
class InventoryEntry:
    sha256: str
    size: int
    path: str


@dataclass(frozen=True)
class Inventory:
    entries: Tuple[InventoryEntry, ...]
    data: bytes
    digest: str
    file_count: int
    total_bytes: int
    directories: Tuple[str, ...] = ()

    def metadata(self) -> Dict[str, Any]:
        return {
            "format": INVENTORY_FORMAT,
            "sha256": self.digest,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
        }


def _utf8_sort_key(value: str) -> bytes:
    try:
        return value.encode("utf-8", "strict")
    except UnicodeError as exc:
        raise PublicationError(f"path is not valid UTF-8: {value!r}") from exc


def _validate_relative_path(path: str, *, label: str = "path") -> Tuple[str, ...]:
    if not isinstance(path, str) or not path:
        raise PublicationError(f"{label} must be a non-empty UTF-8 string")
    try:
        encoded = path.encode("utf-8", "strict")
    except UnicodeError as exc:
        raise PublicationError(f"{label} is not valid UTF-8: {exc}") from exc
    if path.startswith("/") or "\\" in path:
        raise PublicationError(f"unsafe absolute or backslash {label}: {path!r}")
    if any(character in path for character in ("\x00", "\t", "\r", "\n")):
        raise PublicationError(f"unsafe control character in {label}: {path!r}")
    components = tuple(path.split("/"))
    if any(component in SAFE_COMPONENT_FORBIDDEN for component in components):
        raise PublicationError(f"unsafe component in {label}: {path!r}")
    if PurePosixPath(path).as_posix() != path:
        raise PublicationError(f"non-canonical {label}: {path!r}")
    if not encoded:
        raise PublicationError(f"empty encoded {label}")
    return components


def serialize_inventory(entries: Iterable[InventoryEntry]) -> bytes:
    """Serialize entries in the canonical sha256-size-path-v1 byte format."""
    checked: List[Tuple[bytes, InventoryEntry]] = []
    seen: Set[str] = set()
    for entry in entries:
        if not HEX_64_RE.fullmatch(entry.sha256):
            raise PublicationError(f"invalid lowercase SHA-256 for {entry.path!r}")
        if not isinstance(entry.size, int) or isinstance(entry.size, bool) or entry.size < 0:
            raise PublicationError(f"invalid byte count for {entry.path!r}")
        _validate_relative_path(entry.path, label="inventory path")
        if entry.path in seen:
            raise PublicationError(f"duplicate inventory path: {entry.path}")
        seen.add(entry.path)
        checked.append((entry.path.encode("utf-8"), entry))
    checked.sort(key=lambda item: item[0])
    if not checked:
        raise PublicationError("inventory must contain at least one regular file")
    return b"".join(
        (
            entry.sha256.encode("ascii")
            + b"\t"
            + str(entry.size).encode("ascii")
            + b"\t"
            + path_bytes
            + b"\n"
        )
        for path_bytes, entry in checked
    )


def parse_inventory(data: bytes) -> Tuple[InventoryEntry, ...]:
    """Parse and require canonical sha256-size-path-v1 serialization."""
    if not isinstance(data, bytes) or not data or not data.endswith(b"\n"):
        raise PublicationError("inventory must be non-empty and end in exactly one LF")
    if data.endswith(b"\n\n"):
        raise PublicationError("inventory has more than one final LF")
    entries: List[InventoryEntry] = []
    for line_number, raw_line in enumerate(data[:-1].split(b"\n"), 1):
        fields = raw_line.split(b"\t")
        if len(fields) != 3:
            raise PublicationError(f"inventory row {line_number} does not have three fields")
        digest_bytes, size_bytes, path_bytes = fields
        try:
            digest = digest_bytes.decode("ascii")
            size_text = size_bytes.decode("ascii")
            path = path_bytes.decode("utf-8", "strict")
        except UnicodeError as exc:
            raise PublicationError(f"inventory row {line_number} has invalid encoding") from exc
        if not size_text or (size_text != "0" and size_text.startswith("0")) or not size_text.isdecimal():
            raise PublicationError(f"inventory row {line_number} has non-canonical size")
        entries.append(InventoryEntry(digest, int(size_text), path))
    canonical = serialize_inventory(entries)
    if canonical != data:
        raise PublicationError("inventory rows are not in canonical raw UTF-8 path order")
    return tuple(entries)


def _safe_absolute(path: Path, *, label: str, must_exist: bool = False) -> Path:
    path = Path(path)
    if not path.is_absolute():
        raise PublicationError(f"{label} must be absolute: {path}")
    if ".." in path.parts:
        raise PublicationError(f"{label} contains '..': {path}")
    current = Path(path.anchor)
    missing_seen = False
    for component in path.parts[1:]:
        if component in SAFE_COMPONENT_FORBIDDEN:
            raise PublicationError(f"unsafe component in {label}: {path}")
        current = current / component
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            missing_seen = True
            continue
        if missing_seen:
            raise PublicationError(f"{label} has an existing child below a missing parent: {current}")
        if stat.S_ISLNK(info.st_mode):
            raise PublicationError(f"{label} has a symlink component: {current}")
        if current != path and not stat.S_ISDIR(info.st_mode):
            raise PublicationError(f"{label} parent is not a directory: {current}")
    if must_exist and not path.exists():
        raise PublicationError(f"{label} does not exist: {path}")
    return path


def run_authenticated_python(
    path: Path,
    arguments: Sequence[str],
    *,
    expected_sha256: str,
    cwd: Optional[Path] = None,
    timeout: Optional[float] = None,
    label: str = "Python source",
    authenticated_source_sha256: Optional[Mapping[str, str]] = None,
) -> Tuple[subprocess.CompletedProcess[bytes], str]:
    """Execute exact regular-file bytes through a held descriptor and isolated Python."""
    path = _safe_absolute(path, label=label, must_exist=True)
    authenticated_context: Optional[Dict[str, str]] = None
    if authenticated_source_sha256 is not None:
        authenticated_context = {}
        for source_path, digest in authenticated_source_sha256.items():
            _validate_relative_path(source_path, label="authenticated source path")
            if not isinstance(digest, str) or not HEX_64_RE.fullmatch(digest):
                raise PublicationError(
                    f"invalid authenticated source SHA-256 for {source_path!r}"
                )
            authenticated_context[source_path] = digest
    authenticated_context_json = json.dumps(
        authenticated_context,
        sort_keys=True,
        separators=(",", ":"),
    )
    observed = os.lstat(path)
    if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
        raise PublicationError(f"{label} is not a regular single-link file: {path}")
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (observed.st_dev, observed.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise PublicationError(f"{label} changed before it was opened: {path}")
        chunks: List[bytes] = []
        offset = 0
        while True:
            chunk = os.pread(descriptor, 1024 * 1024, offset)
            if not chunk:
                break
            chunks.append(chunk)
            offset += len(chunk)
        source = b"".join(chunks)
        after_read = os.fstat(descriptor)
        identity = (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        )
        if identity != (
            after_read.st_dev,
            after_read.st_ino,
            after_read.st_size,
            after_read.st_mtime_ns,
            after_read.st_ctime_ns,
        ) or len(source) != after_read.st_size:
            raise PublicationError(f"{label} changed while it was read: {path}")
        digest = hashlib.sha256(source).hexdigest()
        if digest != expected_sha256:
            raise PublicationError(f"{label} differs from authenticated expected bytes")
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    _HELD_PYTHON_FD_LAUNCHER,
                    str(descriptor),
                    digest,
                    str(path),
                    authenticated_context_json,
                    *arguments,
                ],
                cwd=str(cwd) if cwd else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=timeout,
                pass_fds=(descriptor,),
            )
        except OSError as exc:
            raise PublicationError(f"cannot execute authenticated {label}: {exc}") from exc
        try:
            current = os.lstat(path)
        except FileNotFoundError as exc:
            raise PublicationError(
                f"authenticated {label} path changed during execution"
            ) from exc
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_nlink != 1
            or (current.st_dev, current.st_ino) != identity[:2]
        ):
            raise PublicationError(f"authenticated {label} path changed during execution")
        held = os.fstat(descriptor)
        if (
            held.st_dev,
            held.st_ino,
            held.st_size,
            held.st_mtime_ns,
            held.st_ctime_ns,
        ) != identity:
            raise PublicationError(f"authenticated {label} bytes changed during execution")
        return completed, digest
    finally:
        os.close(descriptor)


def _hash_open_file(directory_fd: int, name: str, observed: os.stat_result) -> Tuple[str, int]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, dir_fd=directory_fd)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise PublicationError(f"inventory member is not a regular file: {name}")
        if (observed.st_dev, observed.st_ino) != (before.st_dev, before.st_ino):
            raise PublicationError(f"inventory member changed before it was opened: {name}")
        digest = hashlib.sha256()
        size = 0
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
            size += len(block)
        after = os.fstat(descriptor)
        stable_fields = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        after_fields = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if stable_fields != after_fields or size != after.st_size:
            raise PublicationError(f"file changed while inventory was read: {name}")
        return digest.hexdigest(), size
    finally:
        os.close(descriptor)


def _walk_tree(directory_fd: int, prefix: str = "") -> Tuple[List[InventoryEntry], Set[str]]:
    entries: List[InventoryEntry] = []
    directories: Set[str] = set()
    with os.scandir(directory_fd) as iterator:
        children = list(iterator)
    children.sort(key=lambda item: _utf8_sort_key(item.name))
    for child in children:
        name = child.name
        _validate_relative_path(name, label="filesystem component")
        relative = name if not prefix else f"{prefix}/{name}"
        info = child.stat(follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            raise PublicationError(f"tree contains a symlink: {relative}")
        if stat.S_ISDIR(info.st_mode):
            directories.add(relative)
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            child_fd = os.open(name, flags, dir_fd=directory_fd)
            try:
                opened_directory = os.fstat(child_fd)
                if (info.st_dev, info.st_ino) != (
                    opened_directory.st_dev,
                    opened_directory.st_ino,
                ):
                    raise PublicationError(
                        f"inventory directory changed before it was opened: {relative}"
                    )
                nested_entries, nested_directories = _walk_tree(child_fd, relative)
            finally:
                os.close(child_fd)
            entries.extend(nested_entries)
            directories.update(nested_directories)
            continue
        if not stat.S_ISREG(info.st_mode):
            raise PublicationError(f"tree contains a non-regular member: {relative}")
        digest, size = _hash_open_file(directory_fd, name, info)
        entries.append(InventoryEntry(digest, size, relative))
    return entries, directories


def _expected_directories(expected_paths: Set[str]) -> Set[str]:
    expected: Set[str] = set()
    for path in expected_paths:
        parts = _validate_relative_path(path, label="managed installed path")
        for index in range(1, len(parts)):
            expected.add("/".join(parts[:index]))
    return expected


def build_inventory(root: Path, expected_paths: Optional[Iterable[str]] = None) -> Inventory:
    """Inventory a no-symlink regular-file tree and optionally enforce ownership."""
    root = _safe_absolute(Path(root), label="inventory root", must_exist=True)
    root_info = os.lstat(root)
    if not stat.S_ISDIR(root_info.st_mode):
        raise PublicationError(f"inventory root is not a directory: {root}")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(root, flags)
    try:
        opened_root = os.fstat(descriptor)
        if (root_info.st_dev, root_info.st_ino) != (opened_root.st_dev, opened_root.st_ino):
            raise PublicationError(f"inventory root changed before it was opened: {root}")
        entries, directories = _walk_tree(descriptor)
    finally:
        os.close(descriptor)
    if expected_paths is not None:
        expected_files = set(expected_paths)
        for path in expected_files:
            _validate_relative_path(path, label="managed installed path")
        actual_files = {entry.path for entry in entries}
        missing = sorted(expected_files - actual_files, key=_utf8_sort_key)
        unmanaged = sorted(actual_files - expected_files, key=_utf8_sort_key)
        unexpected_directories = sorted(
            directories - _expected_directories(expected_files),
            key=_utf8_sort_key,
        )
        if missing or unmanaged or unexpected_directories:
            details: List[str] = []
            if missing:
                details.append("missing=" + ",".join(missing))
            if unmanaged:
                details.append("unmanaged=" + ",".join(unmanaged))
            if unexpected_directories:
                details.append("unmanaged-directories=" + ",".join(unexpected_directories))
            raise PublicationError("managed tree mismatch: " + "; ".join(details))
    data = serialize_inventory(entries)
    ordered = parse_inventory(data)
    return Inventory(
        entries=ordered,
        data=data,
        digest=hashlib.sha256(data).hexdigest(),
        file_count=len(ordered),
        total_bytes=sum(entry.size for entry in ordered),
        directories=tuple(sorted(directories, key=_utf8_sort_key)),
    )
