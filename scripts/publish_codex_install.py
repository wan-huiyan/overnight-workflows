#!/usr/bin/env python3
"""Publish the Codex umbrella as one verified, recoverable directory generation.

The real publisher deliberately supports only Darwin ``renameatx_np`` with
``RENAME_SWAP``.  Tests inject ``FakeAtomicExchanger``; production never falls
back to a sequence of renames or to per-file writes in the live tree.
Terminal validation retains the durable package reservation. Locked inventory
receipts bind dispatch, judgment, and acceptance to an exact live generation
and bounded finalization-manifest prefix; only ``accept`` releases the package.

The inventory codec stays in this safety-critical module so every filesystem
read, exchange, rollback, recovery, and receipt uses one implementation. Panel
validation imports it directly; a regression test also locks its wire-format
name to the independent install checker and committed manifest contract.
"""

from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


INVENTORY_FORMAT = "sha256-size-path-v1"
STATE_SCHEMA_VERSION = 2
RECEIPT_SCHEMA_VERSION = 3
RENAME_SWAP = 0x00000002
OPERATION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
HEX_40_RE = re.compile(r"[0-9a-f]{40}\Z")
HEX_64_RE = re.compile(r"[0-9a-f]{64}\Z")
SAFE_COMPONENT_FORBIDDEN = {"", ".", ".."}
MUTATION_NO_LIVE_CHANGE = "NO_LIVE_MUTATION_PREPARED"
MUTATION_PUBLISHED = "PUBLISHED_CANDIDATE_GENERATION"
MUTATION_ROLLED_BACK = "ROLLED_BACK_TO_PREFLIGHT_GENERATION"
LIVE_INVENTORY_PHASES = {"dispatch", "judgment", "acceptance"}


class PublicationError(RuntimeError):
    """A fail-closed publication result which requires human inspection."""


class ValidationFailure(PublicationError):
    """The complete new live generation failed its post-swap checker."""


class InjectedFailure(RuntimeError):
    """A deterministic test-only crash point."""


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


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _require_disjoint(left: Path, right: Path, *, labels: Tuple[str, str]) -> None:
    if _is_within(left, right) or _is_within(right, left):
        raise PublicationError(f"{labels[0]} and {labels[1]} must be disjoint: {left}, {right}")


def _require_same_filesystem(left: Path, right: Path) -> None:
    if os.stat(left, follow_symlinks=False).st_dev != os.stat(
        right, follow_symlinks=False
    ).st_dev:
        raise PublicationError("stage and live parent are on different filesystems")


def _mkdir_secure(path: Path, mode: int = 0o700, *, exist_ok: bool = False) -> None:
    path = _safe_absolute(path, label="directory to create")
    if path.exists():
        info = os.lstat(path)
        if not exist_ok:
            raise PublicationError(f"directory already exists: {path}")
        if not stat.S_ISDIR(info.st_mode):
            raise PublicationError(f"expected directory: {path}")
        return
    parent = path.parent
    if not parent.exists():
        _mkdir_secure(parent, mode=mode, exist_ok=True)
    os.mkdir(path, mode)
    _fsync_directory(parent)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    _safe_absolute(path.parent, label="atomic-write parent", must_exist=True)
    try:
        existing = os.lstat(path)
    except FileNotFoundError:
        existing = None
    if existing is not None and not stat.S_ISREG(existing.st_mode):
        raise PublicationError(f"refusing non-regular or symlink write target: {path}")
    temporary = path.parent / f".{path.name}.tmp-{os.getpid()}-{os.urandom(6).hex()}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, mode)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    data = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8")
    _atomic_write_bytes(path, data)


def _read_json_file(path: Path, *, label: str) -> Dict[str, Any]:
    raw = _read_regular_bytes(path, label=label)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PublicationError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise PublicationError(f"{label} must contain one JSON object")
    return value


def _read_regular_bytes(path: Path, *, label: str) -> bytes:
    path = _safe_absolute(path, label=label, must_exist=True)
    observed = os.lstat(path)
    if not stat.S_ISREG(observed.st_mode):
        raise PublicationError(f"{label} is not a regular file: {path}")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (observed.st_dev, observed.st_ino) != (opened.st_dev, opened.st_ino):
            raise PublicationError(f"{label} changed before it was opened: {path}")
        chunks: List[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise PublicationError(f"{label} changed while it was read: {path}")
        data = b"".join(chunks)
        if len(data) != after.st_size:
            raise PublicationError(f"{label} size changed while it was read: {path}")
        return data
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


def _run(command: Sequence[str], *, cwd: Optional[Path] = None) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            list(command),
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise PublicationError(f"cannot execute {command[0]}: {exc}") from exc


def _git(repository: Path, arguments: Sequence[str], *, check: bool = True) -> bytes:
    completed = _run(["git", "-C", str(repository), *arguments])
    if check and completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        raise PublicationError(f"git {' '.join(arguments)} failed: {detail}")
    return completed.stdout


def _verify_commit(repository: Path, commit: str) -> Tuple[str, str]:
    repository = _safe_absolute(repository, label="source repository", must_exist=True)
    if not HEX_40_RE.fullmatch(commit):
        raise PublicationError("source commit must be a full lowercase 40-hex object ID")
    resolved = _git(repository, ["rev-parse", "--verify", f"{commit}^{{commit}}"])
    resolved_text = resolved.decode("ascii", "strict").strip()
    if resolved_text != commit:
        raise PublicationError(f"source commit did not resolve to itself: {commit} -> {resolved_text}")
    tree = _git(repository, ["rev-parse", "--verify", f"{commit}^{{tree}}"])
    tree_text = tree.decode("ascii", "strict").strip()
    if not HEX_40_RE.fullmatch(tree_text):
        raise PublicationError(f"source tree is not a SHA-1 object ID: {tree_text}")
    return commit, tree_text


def _commit_entries(repository: Path, commit: str) -> List[Tuple[str, str, int]]:
    output = _git(repository, ["ls-tree", "-rz", "--full-tree", commit])
    entries: List[Tuple[str, str, int]] = []
    seen: Set[str] = set()
    for record in output.split(b"\x00"):
        if not record:
            continue
        try:
            header, raw_path = record.split(b"\t", 1)
            mode_bytes, object_type, oid_bytes = header.split(b" ", 2)
            path = raw_path.decode("utf-8", "strict")
            mode_text = mode_bytes.decode("ascii")
            oid = oid_bytes.decode("ascii")
        except (ValueError, UnicodeError) as exc:
            raise PublicationError("cannot parse immutable Git tree entry") from exc
        _validate_relative_path(path, label="Git tree path")
        if path in seen:
            raise PublicationError(f"duplicate Git tree path: {path}")
        seen.add(path)
        if object_type != b"blob" or mode_text not in {"100644", "100755"}:
            raise PublicationError(
                f"Git tree contains symlink, submodule, or non-regular entry: {path} ({mode_text})"
            )
        if not HEX_40_RE.fullmatch(oid):
            raise PublicationError(f"invalid Git blob object ID for {path}")
        entries.append((path, oid, int(mode_text, 8)))
    if not entries:
        raise PublicationError("source commit tree is empty")
    entries.sort(key=lambda item: _utf8_sort_key(item[0]))
    return entries


def _write_new_file(path: Path, data: bytes, mode: int = 0o600) -> None:
    _mkdir_secure(path.parent, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, mode)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def materialize_commit(repository: Path, commit: str, destination: Path) -> Tuple[str, Dict[str, int]]:
    """Materialize only regular blobs from one exact commit into a fresh tree."""
    _, tree = _verify_commit(repository, commit)
    _mkdir_secure(destination)
    modes: Dict[str, int] = {}
    for path, oid, git_mode in _commit_entries(repository, commit):
        data = _git(repository, ["cat-file", "blob", oid])
        target = destination.joinpath(*path.split("/"))
        permissions = 0o700 if git_mode & 0o111 else 0o600
        _write_new_file(target, data, permissions)
        modes[path] = permissions
    _fsync_directory(destination)
    return tree, modes


def _load_manifest(source_snapshot: Path, manifest_path: str) -> Tuple[Dict[str, Any], bytes, List[Dict[str, str]]]:
    _validate_relative_path(manifest_path, label="manifest path")
    path = source_snapshot.joinpath(*manifest_path.split("/"))
    if not path.exists() or path.is_symlink() or not path.is_file():
        raise PublicationError(f"immutable commit has no regular manifest: {manifest_path}")
    raw = _read_regular_bytes(path, label="immutable install manifest")
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PublicationError(f"invalid install manifest: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("mappings"), list):
        raise PublicationError("install manifest must be an object with a mappings array")
    mappings: List[Dict[str, str]] = []
    source_seen: Set[str] = set()
    installed_seen: Set[str] = set()
    for index, item in enumerate(manifest["mappings"]):
        if not isinstance(item, dict):
            raise PublicationError(f"manifest mapping {index} is not an object")
        source = item.get("canonical_source")
        installed = item.get("installed_path")
        if not isinstance(source, str) or not isinstance(installed, str):
            raise PublicationError(f"manifest mapping {index} lacks source/path strings")
        _validate_relative_path(source, label=f"manifest source {index}")
        _validate_relative_path(installed, label=f"installed path {index}")
        if source in source_seen or installed in installed_seen:
            raise PublicationError(f"manifest mapping {index} repeats a source or installed path")
        source_seen.add(source)
        installed_seen.add(installed)
        source_path = source_snapshot.joinpath(*source.split("/"))
        if source_path.is_symlink() or not source_path.is_file():
            raise PublicationError(f"manifest source is not a regular immutable file: {source}")
        mappings.append({"canonical_source": source, "installed_path": installed})
    if not mappings:
        raise PublicationError("install manifest has no mappings")
    return manifest, raw, mappings


def _copy_regular_file(source: Path, destination: Path) -> None:
    source_info = os.lstat(source)
    if not stat.S_ISREG(source_info.st_mode):
        raise PublicationError(f"copy source is not a regular file: {source}")
    descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        chunks: List[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        source_info.st_dev,
        source_info.st_ino,
        source_info.st_size,
        source_info.st_mtime_ns,
    ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise PublicationError(f"copy source changed while read: {source}")
    permissions = 0o700 if source_info.st_mode & 0o111 else 0o600
    _write_new_file(destination, b"".join(chunks), permissions)


def _materialize_candidate(source_snapshot: Path, destination: Path, mappings: Sequence[Mapping[str, str]]) -> None:
    _mkdir_secure(destination)
    for mapping in mappings:
        source = source_snapshot.joinpath(*mapping["canonical_source"].split("/"))
        installed = destination.joinpath(*mapping["installed_path"].split("/"))
        _copy_regular_file(source, installed)
    _fsync_directory(destination)


def _copy_tree(source: Path, destination: Path, expected_paths: Iterable[str]) -> None:
    inventory = build_inventory(source, expected_paths)
    _mkdir_secure(destination)
    for entry in inventory.entries:
        _copy_regular_file(
            source.joinpath(*entry.path.split("/")),
            destination.joinpath(*entry.path.split("/")),
        )
    copied = build_inventory(destination, expected_paths)
    if copied.digest != inventory.digest or copied.data != inventory.data:
        raise PublicationError("independent evidence snapshot differs after copy")
    source_inodes: Set[Tuple[int, int]] = set()
    copied_inodes: Set[Tuple[int, int]] = set()
    for entry in inventory.entries:
        source_info = os.lstat(source.joinpath(*entry.path.split("/")))
        copied_info = os.lstat(destination.joinpath(*entry.path.split("/")))
        source_inodes.add((source_info.st_dev, source_info.st_ino))
        copied_inodes.add((copied_info.st_dev, copied_info.st_ino))
    if source_inodes & copied_inodes:
        raise PublicationError("evidence snapshot is not an independent file copy")


def _make_tree_read_only(root: Path) -> None:
    """Make an evidence tree immutable to ordinary accidental writes."""
    inventory = build_inventory(root)
    for entry in inventory.entries:
        os.chmod(root.joinpath(*entry.path.split("/")), 0o400, follow_symlinks=False)
    for directory in sorted(inventory.directories, key=lambda value: value.count("/"), reverse=True):
        os.chmod(root.joinpath(*directory.split("/")), 0o500, follow_symlinks=False)
    os.chmod(root, 0o500, follow_symlinks=False)
    _fsync_directory(root.parent)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _operation_paths(state_root: Path, operation: str) -> Dict[str, Path]:
    if not OPERATION_RE.fullmatch(operation):
        raise PublicationError(f"unsafe operation ID: {operation!r}")
    operation_root = state_root / "operations" / operation
    return {
        "operation": operation_root,
        "state": operation_root / "state.json",
        "source": operation_root / "immutable-source",
        "source_inventory": operation_root / "immutable-source.inventory",
        "predecessor_source": operation_root / "immutable-predecessor-source",
        "predecessor_source_inventory": operation_root / "immutable-predecessor-source.inventory",
        "preflight_model": operation_root / "expected-live-predecessor",
        "slot": operation_root / "exchange-slot",
        "previous": operation_root / "previous",
        "failed": operation_root / "failed-new",
        "candidate_inventory": operation_root / "candidate.inventory",
        "preflight_inventory": operation_root / "preflight.inventory",
        "reservation": state_root / "package.lock",
        "writer_lock": state_root / "writer.lock",
        "released": state_root / "released" / f"{operation}.json",
    }


def _inventory_record(inventory: Inventory, path: Path) -> Dict[str, Any]:
    return {**inventory.metadata(), "path": str(path)}


def _path_byte_identity(
    inventory: Inventory, path: Path, expected_paths: Sequence[str]
) -> Dict[str, Any]:
    """Name both parts of the identity: exact installed paths and exact bytes."""
    return {
        **_inventory_record(inventory, path),
        "identity_kind": "exact-installed-paths-and-file-bytes",
        "installed_paths": list(expected_paths),
    }


def _normalize_manifest_path(source_repository: Path, manifest_path: str) -> str:
    candidate = Path(manifest_path)
    if candidate.is_absolute():
        candidate = _safe_absolute(
            candidate, label="install manifest argument", must_exist=True
        )
        try:
            relative = candidate.relative_to(source_repository)
        except ValueError as exc:
            raise PublicationError(
                "absolute install manifest must be inside the source repository"
            ) from exc
        manifest_path = relative.as_posix()
    _validate_relative_path(manifest_path, label="manifest path")
    return manifest_path


def _load_state(state_root: Path, operation: str) -> Tuple[Dict[str, Path], Dict[str, Any]]:
    paths = _operation_paths(state_root, operation)
    state_value = _read_json_file(paths["state"], label="operation state")
    if state_value.get("schema_version") != STATE_SCHEMA_VERSION:
        raise PublicationError("operation state has an unsupported schema")
    if state_value.get("operation_id") != operation:
        raise PublicationError("operation state ID does not match requested operation")
    if state_value.get("state_root") != str(state_root):
        raise PublicationError("operation state root does not match requested state root")
    return paths, state_value


def _validate_package_lock_argument(
    state_root: Path, operation: str, lock_path: Optional[Path]
) -> Path:
    expected = _operation_paths(state_root, operation)["reservation"]
    if lock_path is None:
        return expected
    lock_path = _safe_absolute(lock_path, label="package lock")
    if lock_path != expected:
        raise PublicationError(
            f"package lock must be the fixed operation-independent path {expected}"
        )
    return lock_path


def _verify_immutable_source(state: Mapping[str, Any]) -> Inventory:
    source_record = state.get("immutable_source")
    if not isinstance(source_record, dict):
        raise PublicationError("operation lacks an immutable-source identity")
    root_text = source_record.get("root")
    inventory_path_text = source_record.get("path")
    if not isinstance(root_text, str) or not isinstance(inventory_path_text, str):
        raise PublicationError("immutable-source identity paths are malformed")
    state_root_text = state.get("state_root")
    operation_id = state.get("operation_id")
    if not isinstance(state_root_text, str) or not isinstance(operation_id, str):
        raise PublicationError("immutable-source operation paths are malformed")
    expected_operation_paths = _operation_paths(Path(state_root_text), operation_id)
    if (
        Path(root_text) != expected_operation_paths["source"]
        or Path(inventory_path_text) != expected_operation_paths["source_inventory"]
    ):
        raise PublicationError("immutable-source paths differ from the operation layout")
    source_root = _safe_absolute(
        Path(root_text), label="immutable source root", must_exist=True
    )
    inventory_path = _safe_absolute(
        Path(inventory_path_text),
        label="immutable source inventory",
        must_exist=True,
    )
    persisted_entries = parse_inventory(
        _read_regular_bytes(inventory_path, label="immutable source inventory")
    )
    persisted_data = serialize_inventory(persisted_entries)
    persisted_digest = hashlib.sha256(persisted_data).hexdigest()
    if persisted_digest != source_record.get("sha256"):
        raise PublicationError("immutable source inventory receipt drifted")
    expected_paths = [entry.path for entry in persisted_entries]
    current = build_inventory(source_root, expected_paths)
    if current.digest != persisted_digest or current.data != persisted_data:
        raise PublicationError("immutable source tree drifted from its prepared identity")
    source_repository_text = state.get("source_repository")
    source_commit = state.get("source_commit")
    source_tree = state.get("source_tree")
    if not isinstance(source_repository_text, str) or not isinstance(source_commit, str):
        raise PublicationError("immutable source Git identity is malformed")
    if (
        source_record.get("commit") != source_commit
        or source_record.get("tree") != source_tree
        or source_record.get("file_count") != current.file_count
        or source_record.get("total_bytes") != current.total_bytes
    ):
        raise PublicationError("immutable source metadata differs from prepared state")
    _, current_tree = _verify_commit(Path(source_repository_text), source_commit)
    if current_tree != source_tree:
        raise PublicationError("immutable source commit tree differs from prepared state")
    return current


def _verify_predecessor_source(state: Mapping[str, Any]) -> Inventory:
    """Verify the immutable commit used to model the exact live predecessor."""
    source_record = state.get("predecessor_source")
    if not isinstance(source_record, dict):
        raise PublicationError("operation lacks an immutable predecessor-source identity")
    state_root_text = state.get("state_root")
    operation_id = state.get("operation_id")
    if not isinstance(state_root_text, str) or not isinstance(operation_id, str):
        raise PublicationError("predecessor-source operation paths are malformed")
    paths = _operation_paths(Path(state_root_text), operation_id)
    if (
        source_record.get("root") != str(paths["predecessor_source"])
        or source_record.get("path") != str(paths["predecessor_source_inventory"])
    ):
        raise PublicationError("predecessor-source paths differ from the operation layout")
    root = _safe_absolute(
        paths["predecessor_source"], label="immutable predecessor source", must_exist=True
    )
    inventory_path = _safe_absolute(
        paths["predecessor_source_inventory"],
        label="immutable predecessor source inventory",
        must_exist=True,
    )
    persisted_entries = parse_inventory(
        _read_regular_bytes(inventory_path, label="immutable predecessor source inventory")
    )
    persisted_data = serialize_inventory(persisted_entries)
    current = build_inventory(root, [entry.path for entry in persisted_entries])
    if (
        current.data != persisted_data
        or current.digest != source_record.get("sha256")
        or current.file_count != source_record.get("file_count")
        or current.total_bytes != source_record.get("total_bytes")
    ):
        raise PublicationError("immutable predecessor source drifted from its prepared identity")
    commit = state.get("expected_live_source_commit")
    tree = state.get("expected_live_source_tree")
    if (
        not isinstance(commit, str)
        or source_record.get("commit") != commit
        or source_record.get("tree") != tree
    ):
        raise PublicationError("immutable predecessor Git identity differs from prepared state")
    _, observed_tree = _verify_commit(Path(str(state["source_repository"])), commit)
    if observed_tree != tree:
        raise PublicationError("immutable predecessor commit tree differs from prepared state")
    return current


def _verify_prepare_evidence(state: Mapping[str, Any]) -> None:
    receipt_record = state.get("prepare_receipt")
    if not isinstance(receipt_record, dict):
        raise PublicationError("operation lacks a durable prepare receipt")
    receipt_path_text = receipt_record.get("path")
    receipt_digest = receipt_record.get("sha256")
    if not isinstance(receipt_path_text, str) or not HEX_64_RE.fullmatch(str(receipt_digest)):
        raise PublicationError("prepare receipt record is malformed")
    receipt_path = Path(receipt_path_text)
    raw = _read_regular_bytes(receipt_path, label="prepare receipt")
    if hashlib.sha256(raw).hexdigest() != receipt_digest:
        raise PublicationError("prepare receipt drifted after it became durable")
    try:
        receipt = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PublicationError(f"prepare receipt is invalid JSON: {exc}") from exc
    if not isinstance(receipt, dict):
        raise PublicationError("prepare receipt is not an object")
    if receipt.get("operation_id") != state.get("operation_id"):
        raise PublicationError("prepare receipt operation ID differs from state")
    if receipt.get("mutation_outcome") != MUTATION_NO_LIVE_CHANGE:
        raise PublicationError("prepare receipt lacks the named no-live-mutation outcome")
    named_outcomes = receipt.get("named_mutation_outcomes")
    if not isinstance(named_outcomes, dict) or not named_outcomes:
        raise PublicationError("prepare receipt lacks named checker mutation outcomes")
    candidate = receipt.get("candidate_inventory")
    preflight = receipt.get("preflight_live_inventory")
    snapshot = receipt.get("evidence_snapshot")
    state_candidate = state.get("candidate_inventory")
    state_preflight = state.get("preflight_inventory")
    state_snapshot = state.get("evidence_snapshot")
    if (
        not isinstance(candidate, dict)
        or not isinstance(preflight, dict)
        or not isinstance(snapshot, dict)
        or not isinstance(state_candidate, dict)
        or not isinstance(state_preflight, dict)
        or not isinstance(state_snapshot, dict)
    ):
        raise PublicationError("prepare receipt inventory records are malformed")
    if candidate != state_candidate:
        raise PublicationError("prepare receipt candidate identity differs from state")
    if preflight != state_preflight:
        raise PublicationError("prepare receipt preflight identity differs from state")
    if snapshot != state_snapshot:
        raise PublicationError("prepare receipt snapshot identity differs from state")
    receipt_source = receipt.get("immutable_source")
    state_source = state.get("immutable_source")
    if not isinstance(receipt_source, dict) or not isinstance(state_source, dict):
        raise PublicationError("prepare receipt immutable-source record is malformed")
    if receipt_source != state_source:
        raise PublicationError("prepare receipt immutable-source identity differs from state")
    _verify_immutable_source(state)
    receipt_predecessor = receipt.get("predecessor_source")
    state_predecessor = state.get("predecessor_source")
    if (
        not isinstance(receipt_predecessor, dict)
        or not isinstance(state_predecessor, dict)
        or receipt_predecessor != state_predecessor
    ):
        raise PublicationError("prepare receipt predecessor-source identity differs from state")
    expected_live_source = receipt.get("expected_live_source")
    if (
        not isinstance(expected_live_source, dict)
        or expected_live_source.get("commit") != state.get("expected_live_source_commit")
        or expected_live_source.get("tree") != state.get("expected_live_source_tree")
        or expected_live_source.get("manifest_sha256")
        != state.get("expected_live_manifest_sha256")
    ):
        raise PublicationError("prepare receipt expected-live source identity differs from state")
    _verify_predecessor_source(state)


class WriterLock:
    """A non-blocking package-wide writer lock; the durable reservation is separate."""

    def __init__(self, path: Path):
        self.path = path
        self.descriptor: Optional[int] = None

    def __enter__(self) -> "WriterLock":
        _mkdir_secure(self.path.parent, exist_ok=True)
        try:
            info = os.lstat(self.path)
        except FileNotFoundError:
            info = None
        if info is not None:
            if not stat.S_ISREG(info.st_mode):
                raise PublicationError(f"writer lock is not a regular file: {self.path}")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            self.descriptor = os.open(self.path, flags, 0o600)
        except OSError as exc:
            raise PublicationError(f"cannot safely open package writer lock: {exc}") from exc
        opened = os.fstat(self.descriptor)
        if not stat.S_ISREG(opened.st_mode) or (
            info is not None and (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            os.close(self.descriptor)
            self.descriptor = None
            raise PublicationError("package writer lock changed before it was opened")
        try:
            fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self.descriptor)
            self.descriptor = None
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise PublicationError("another publisher holds the package writer lock") from exc
            raise
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.descriptor is not None:
            fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            os.close(self.descriptor)
            self.descriptor = None


CheckerRunner = Callable[[Path, Path], Dict[str, Any]]
Failpoint = Optional[Callable[[str], None]]


def run_installed_checker(source_snapshot: Path, installed_root: Path) -> Dict[str, Any]:
    checker = source_snapshot / "scripts/check_large_queue_guidance.py"
    if checker.is_symlink() or not checker.is_file():
        raise PublicationError("immutable source has no regular installed-root checker")
    checker_digest = hashlib.sha256(
        _read_regular_bytes(checker, label="immutable installed-root checker")
    ).hexdigest()
    argv = [
        sys.executable,
        str(checker),
        "--installed-root",
        str(installed_root),
        "--self-test",
        "--json",
    ]
    completed = _run(argv, cwd=source_snapshot)
    stdout = completed.stdout.decode("utf-8", "replace")
    parsed: Optional[Dict[str, Any]] = None
    try:
        value = json.loads(stdout)
        if isinstance(value, dict):
            parsed = value
    except json.JSONDecodeError:
        parsed = None
    receipt = {
        "argv": argv,
        "checker_sha256": checker_digest,
        "stdout": stdout,
        "stderr": completed.stderr.decode("utf-8", "replace"),
        "exit_status": completed.returncode,
        "result": parsed,
    }
    if completed.returncode != 0:
        raise ValidationFailure("installed-root checker --self-test failed: " + receipt["stderr"].strip())
    outcomes = parsed.get("named_mutation_outcomes") if parsed else None
    if (
        parsed is None
        or parsed.get("status") != "PASS"
        or not isinstance(outcomes, dict)
        or not outcomes
        or any(
            not isinstance(name, str) or not name or result != "PASS"
            for name, result in outcomes.items()
        )
    ):
        raise ValidationFailure(
            "installed-root checker did not return nonempty named mutation PASS outcomes"
        )
    receipt["named_mutation_outcomes"] = outcomes
    return receipt


def _run_bound_checker(
    state: Mapping[str, Any],
    source_snapshot: Path,
    installed_root: Path,
    checker_runner: CheckerRunner,
) -> Dict[str, Any]:
    source_record = state.get("immutable_source")
    if not isinstance(source_record, dict) or source_record.get("root") != str(source_snapshot):
        raise PublicationError("checker source root differs from the bound immutable source")
    _verify_immutable_source(state)
    receipt = checker_runner(source_snapshot, installed_root)
    _verify_immutable_source(state)
    return receipt


def _record_event(state: Dict[str, Any], name: str, **details: Any) -> None:
    events = state.setdefault("events", [])
    if not isinstance(events, list):
        raise PublicationError("operation event journal is malformed")
    events.append({"at": _utc_now(), "event": name, **details})


def _persist_state(path: Path, state: Dict[str, Any], status: Optional[str] = None) -> None:
    if status is not None:
        state["status"] = status
    state["updated_at"] = _utc_now()
    _atomic_write_json(path, state)


def prepare_operation(
    *,
    source_repository: Path,
    source_commit: str,
    expected_live_source_commit: str,
    manifest_path: str,
    install_root: Path,
    state_root: Path,
    evidence_root: Path,
    operation: str,
    receipt_output: Optional[Path] = None,
    checker_runner: CheckerRunner = run_installed_checker,
    failpoint: Failpoint = None,
) -> Dict[str, Any]:
    """Build and validate a candidate without changing the installed root."""
    source_repository = _safe_absolute(source_repository, label="source repository", must_exist=True)
    manifest_path = _normalize_manifest_path(source_repository, manifest_path)
    install_root = _safe_absolute(install_root, label="install root", must_exist=True)
    state_root = _safe_absolute(state_root, label="state root")
    evidence_root = _safe_absolute(evidence_root, label="evidence root")
    _require_disjoint(state_root, install_root, labels=("state root", "install root"))
    _require_disjoint(evidence_root, install_root, labels=("evidence root", "install root"))
    _require_disjoint(evidence_root, state_root, labels=("evidence root", "state root"))
    _require_disjoint(
        source_repository, install_root, labels=("source repository", "install root")
    )
    _require_disjoint(
        source_repository, state_root, labels=("source repository", "state root")
    )
    _require_disjoint(
        source_repository, evidence_root, labels=("source repository", "evidence root")
    )
    if receipt_output is None:
        receipt_output = evidence_root / "prepare-receipt.json"
    receipt_output = _safe_absolute(receipt_output, label="prepare receipt output")
    if receipt_output == evidence_root or not _is_within(receipt_output, evidence_root):
        raise PublicationError("prepare receipt output must be a file inside the evidence root")
    if (
        _is_within(receipt_output, evidence_root / "snapshot")
        or receipt_output == evidence_root / "snapshot.inventory"
        or receipt_output == evidence_root / "publication-receipt.json"
        or receipt_output == evidence_root / "terminal-validation-live.inventory"
    ):
        raise PublicationError("prepare receipt output collides with reserved evidence paths")
    _mkdir_secure(state_root, exist_ok=True)
    _mkdir_secure(state_root / "operations", exist_ok=True)
    paths = _operation_paths(state_root, operation)
    with WriterLock(paths["writer_lock"]):
        _mkdir_secure(paths["operation"])
        state: Dict[str, Any] = {
            "schema_version": STATE_SCHEMA_VERSION,
            "operation_id": operation,
            "generation_id": operation,
            "status": "PREPARING",
            "state_root": str(state_root),
            "install_root": str(install_root),
            "evidence_root": str(evidence_root),
            "source_repository": str(source_repository),
            "source_commit": source_commit,
            "expected_live_source_commit": expected_live_source_commit,
            "manifest_path": manifest_path,
            "created_at": _utc_now(),
            "events": [],
        }
        _record_event(state, "prepare_started")
        _persist_state(paths["state"], state)
        try:
            tree, _ = materialize_commit(source_repository, source_commit, paths["source"])
            state["source_tree"] = tree
            source_inventory = build_inventory(paths["source"])
            _atomic_write_bytes(paths["source_inventory"], source_inventory.data)
            os.chmod(paths["source_inventory"], 0o400, follow_symlinks=False)
            state["immutable_source"] = {
                **_inventory_record(source_inventory, paths["source_inventory"]),
                "root": str(paths["source"]),
                "commit": source_commit,
                "tree": tree,
            }
            _make_tree_read_only(paths["source"])
            _verify_immutable_source(state)
            predecessor_tree, _ = materialize_commit(
                source_repository,
                expected_live_source_commit,
                paths["predecessor_source"],
            )
            state["expected_live_source_tree"] = predecessor_tree
            predecessor_source_inventory = build_inventory(paths["predecessor_source"])
            _atomic_write_bytes(
                paths["predecessor_source_inventory"],
                predecessor_source_inventory.data,
            )
            os.chmod(paths["predecessor_source_inventory"], 0o400, follow_symlinks=False)
            state["predecessor_source"] = {
                **_inventory_record(
                    predecessor_source_inventory,
                    paths["predecessor_source_inventory"],
                ),
                "root": str(paths["predecessor_source"]),
                "commit": expected_live_source_commit,
                "tree": predecessor_tree,
            }
            _make_tree_read_only(paths["predecessor_source"])
            _verify_predecessor_source(state)
            manifest, manifest_raw, mappings = _load_manifest(paths["source"], manifest_path)
            predecessor_manifest, predecessor_manifest_raw, predecessor_mappings = _load_manifest(
                paths["predecessor_source"], manifest_path
            )
            candidate_paths = [mapping["installed_path"] for mapping in mappings]
            preflight_paths = [mapping["installed_path"] for mapping in predecessor_mappings]
            state["manifest_sha256"] = hashlib.sha256(manifest_raw).hexdigest()
            state["manifest_schema_version"] = manifest.get("schema_version")
            state["expected_live_manifest_sha256"] = hashlib.sha256(
                predecessor_manifest_raw
            ).hexdigest()
            state["expected_live_manifest_schema_version"] = predecessor_manifest.get(
                "schema_version"
            )
            state["candidate_expected_paths"] = candidate_paths
            state["preflight_expected_paths"] = preflight_paths
            if failpoint:
                failpoint("during_staging")
            _materialize_candidate(paths["source"], paths["slot"], mappings)
            candidate = build_inventory(paths["slot"], candidate_paths)
            state["generation_id"] = candidate.digest
            _atomic_write_bytes(paths["candidate_inventory"], candidate.data)
            _materialize_candidate(
                paths["predecessor_source"], paths["preflight_model"], predecessor_mappings
            )
            predecessor_model = build_inventory(paths["preflight_model"], preflight_paths)
            preflight = build_inventory(install_root, preflight_paths)
            if preflight.data != predecessor_model.data:
                raise PublicationError(
                    "live generation does not exactly match --expected-live-source-commit"
                )
            _atomic_write_bytes(paths["preflight_inventory"], preflight.data)
            _require_same_filesystem(paths["slot"], install_root.parent)
            staged_checker = _run_bound_checker(
                state, paths["source"], paths["slot"], checker_runner
            )
            _mkdir_secure(evidence_root)
            snapshot = evidence_root / "snapshot"
            _copy_tree(paths["slot"], snapshot, candidate_paths)
            snapshot_inventory = build_inventory(snapshot, candidate_paths)
            if snapshot_inventory.data != candidate.data:
                raise PublicationError("evidence snapshot identity differs from candidate")
            _make_tree_read_only(snapshot)
            evidence_inventory_path = evidence_root / "snapshot.inventory"
            _atomic_write_bytes(evidence_inventory_path, snapshot_inventory.data)
            state["candidate_inventory"] = _path_byte_identity(
                candidate, paths["candidate_inventory"], candidate_paths
            )
            state["preflight_inventory"] = _path_byte_identity(
                preflight, paths["preflight_inventory"], preflight_paths
            )
            state["evidence_snapshot"] = {
                **_inventory_record(snapshot_inventory, evidence_inventory_path),
                "root": str(snapshot),
            }
            state["validation"] = {"staged": staged_checker}
            prepare_receipt = {
                "schema_version": RECEIPT_SCHEMA_VERSION,
                "operation_id": operation,
                "generation_id": state["generation_id"],
                "source": {
                    "repository": str(source_repository),
                    "commit": source_commit,
                    "tree": tree,
                    "manifest_path": manifest_path,
                    "manifest_sha256": state["manifest_sha256"],
                },
                "immutable_source": state["immutable_source"],
                "expected_live_source": {
                    "commit": expected_live_source_commit,
                    "tree": predecessor_tree,
                    "manifest_path": manifest_path,
                    "manifest_sha256": state["expected_live_manifest_sha256"],
                },
                "predecessor_source": state["predecessor_source"],
                "candidate_inventory": state["candidate_inventory"],
                "preflight_live_inventory": state["preflight_inventory"],
                "evidence_snapshot": state["evidence_snapshot"],
                "staged_validation": staged_checker,
                "named_mutation_outcomes": {
                    "staged": staged_checker["named_mutation_outcomes"]
                },
                "mutation_outcome": MUTATION_NO_LIVE_CHANGE,
                "prepared_at": _utc_now(),
            }
            prepare_receipt_path = receipt_output
            _mkdir_secure(prepare_receipt_path.parent, exist_ok=True)
            _atomic_write_json(prepare_receipt_path, prepare_receipt)
            state["prepare_receipt"] = {
                "path": str(prepare_receipt_path),
                "sha256": hashlib.sha256(
                    _read_regular_bytes(prepare_receipt_path, label="prepare receipt")
                ).hexdigest(),
            }
            _record_event(state, "prepare_completed")
            _persist_state(paths["state"], state, "PREPARED")
            return prepare_receipt
        except Exception as exc:
            _record_event(state, "prepare_failed", error=str(exc))
            _persist_state(paths["state"], state, "UNCHECKED")
            raise


def _validate_maintenance_receipt(path: Path, operation: str) -> Tuple[Dict[str, Any], str]:
    receipt = _read_json_file(path, label="maintenance authorization receipt")
    required_strings = (
        "authorized_by",
        "maintenance_window_id",
        "reader_quiescence_checked_at",
        "controller_id",
        "owner_host",
        "owner_process_start_identity",
    )
    if receipt.get("schema_version") != 1 or receipt.get("operation_id") != operation:
        raise PublicationError("maintenance receipt schema or operation ID is wrong")
    if receipt.get("reader_quiescence_status") != "QUIESCENT":
        raise PublicationError("known Codex readers are not recorded as QUIESCENT")
    if receipt.get("controller_state") != "ACTIVE":
        raise PublicationError("maintenance receipt controller_state is not ACTIVE")
    owner_pid = receipt.get("owner_pid")
    if not isinstance(owner_pid, int) or isinstance(owner_pid, bool) or owner_pid <= 0:
        raise PublicationError("maintenance receipt owner_pid must be a positive integer")
    for key in required_strings:
        if not isinstance(receipt.get(key), str) or not receipt[key].strip():
            raise PublicationError(f"maintenance receipt lacks non-empty {key}")
    raw = _read_regular_bytes(path, label="maintenance authorization receipt")
    return receipt, hashlib.sha256(raw).hexdigest()


def _parse_finalization_jsonl(data: bytes, path: Path) -> List[Dict[str, Any]]:
    if not data or not data.endswith(b"\n"):
        raise PublicationError(f"finalization manifest must be non-empty JSONL ending in LF: {path}")
    records: List[Dict[str, Any]] = []
    previous_sequence: Optional[int] = None
    finalization_id: Optional[str] = None
    for line_number, raw_line in enumerate(data[:-1].split(b"\n"), 1):
        if not raw_line:
            raise PublicationError(f"finalization manifest has an empty row at {line_number}")
        try:
            value = json.loads(raw_line.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise PublicationError(
                f"finalization manifest row {line_number} is invalid JSON: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise PublicationError(f"finalization manifest row {line_number} is not an object")
        sequence = value.get("sequence")
        if (
            not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or (previous_sequence is not None and sequence <= previous_sequence)
        ):
            raise PublicationError(
                f"finalization manifest sequence is not strictly increasing at row {line_number}"
            )
        previous_sequence = sequence
        record_finalization_id = value.get("finalization_id")
        if not isinstance(record_finalization_id, str) or not record_finalization_id.strip():
            raise PublicationError(
                f"finalization manifest row {line_number} lacks finalization_id"
            )
        if finalization_id is None:
            finalization_id = record_finalization_id
        elif record_finalization_id != finalization_id:
            raise PublicationError("finalization manifest mixes finalization IDs")
        for field in ("schema_version", "recorded_at", "record_type"):
            if field not in value:
                raise PublicationError(
                    f"finalization manifest row {line_number} lacks {field}"
                )
        records.append(value)
    return records


def _finalization_manifest_prefix(
    path: Path,
    *,
    through_sequence: Optional[int] = None,
    required_prefix: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Bind a JSONL prefix and optionally prove an earlier prefix is unchanged."""
    path = _safe_absolute(path, label="finalization evidence manifest", must_exist=True)
    observed = os.lstat(path)
    if not stat.S_ISREG(observed.st_mode):
        raise PublicationError("finalization manifest is not a regular file")
    if observed.st_nlink != 1:
        raise PublicationError("finalization manifest must have exactly one hard link")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (
            (observed.st_dev, observed.st_ino) != (opened.st_dev, opened.st_ino)
            or opened.st_nlink != 1
        ):
            raise PublicationError("finalization manifest changed before prefix read")
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        if os.fstat(descriptor).st_nlink != 1:
            raise PublicationError("finalization manifest gained a hard link before prefix read")
        chunks: List[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        data = b"".join(chunks)
        if (
            (opened.st_dev, opened.st_ino, opened.st_size)
            != (after.st_dev, after.st_ino, after.st_size)
            or after.st_nlink != 1
            or len(data) != after.st_size
        ):
            raise PublicationError("finalization manifest changed during prefix read")
        records = _parse_finalization_jsonl(data, path)
        prefix_data = data
        prefix_records = records
        if through_sequence is not None:
            matching_indexes = [
                index
                for index, record in enumerate(records)
                if record.get("sequence") == through_sequence
            ]
            if len(matching_indexes) != 1:
                raise PublicationError(
                    "finalization manifest lacks the requested bounded sequence"
                )
            line_count = matching_indexes[0] + 1
            prefix_data = b"".join(data.splitlines(keepends=True)[:line_count])
            prefix_records = records[:line_count]
        observed_prefix = {
            "path": str(path),
            "prefix_bytes": len(prefix_data),
            "prefix_sha256": hashlib.sha256(prefix_data).hexdigest(),
            "record_count": len(prefix_records),
            "last_sequence": prefix_records[-1]["sequence"],
            "last_record_type": prefix_records[-1]["record_type"],
            "finalization_id": prefix_records[-1]["finalization_id"],
        }
        if required_prefix is not None:
            required = dict(required_prefix)
            required_bytes = required.get("prefix_bytes")
            if (
                set(required) != set(observed_prefix)
                or not isinstance(required_bytes, int)
                or isinstance(required_bytes, bool)
                or required_bytes <= 0
                or required_bytes > len(data)
            ):
                raise PublicationError("required finalization-manifest prefix is malformed")
            required_data = data[:required_bytes]
            required_records = _parse_finalization_jsonl(required_data, path)
            actual_required = {
                "path": str(path),
                "prefix_bytes": len(required_data),
                "prefix_sha256": hashlib.sha256(required_data).hexdigest(),
                "record_count": len(required_records),
                "last_sequence": required_records[-1]["sequence"],
                "last_record_type": required_records[-1]["record_type"],
                "finalization_id": required_records[-1]["finalization_id"],
            }
            if actual_required != required:
                raise PublicationError(
                    "finalization manifest no longer extends the required durable prefix"
                )
        return observed_prefix
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _validate_finalization_manifest_path(
    path: Path, *, state_root: Path, state: Mapping[str, Any]
) -> Path:
    path = _safe_absolute(path, label="finalization evidence manifest", must_exist=True)
    observed = os.lstat(path)
    if not stat.S_ISREG(observed.st_mode):
        raise PublicationError("finalization manifest is not a regular file")
    if observed.st_nlink != 1:
        raise PublicationError("finalization manifest must have exactly one hard link")
    protected_roots = {
        "state root": state_root,
        "installed skill root": Path(str(state["install_root"])),
        "source repository": Path(str(state["source_repository"])),
        "operation evidence root": Path(str(state["evidence_root"])),
    }
    for label, protected in protected_roots.items():
        protected = _safe_absolute(protected, label=label, must_exist=True)
        if _is_within(path, protected):
            raise PublicationError(
                f"finalization manifest must be outside the protected {label}"
            )
    return path


def _append_finalization_record(
    manifest_path: Path,
    *,
    record_type: str,
    payload: Mapping[str, Any],
    expected_prefix: Optional[Mapping[str, Any]] = None,
    required_ancestor_prefix: Optional[Mapping[str, Any]] = None,
    predecessor_payload_field: Optional[str] = None,
) -> Dict[str, Any]:
    if expected_prefix is not None and required_ancestor_prefix is not None:
        raise PublicationError(
            "finalization append cannot require both exact and ancestor prefixes"
        )
    if predecessor_payload_field is not None and required_ancestor_prefix is None:
        raise PublicationError(
            "a predecessor payload binding requires an ancestor prefix"
        )
    if predecessor_payload_field is not None and predecessor_payload_field in payload:
        raise PublicationError("predecessor payload field is reserved by the appender")
    observed = os.lstat(manifest_path)
    if not stat.S_ISREG(observed.st_mode):
        raise PublicationError("finalization manifest is not a regular file")
    if observed.st_nlink != 1:
        raise PublicationError("finalization manifest must have exactly one hard link")
    flags = os.O_RDWR | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(manifest_path, flags)
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (observed.st_dev, observed.st_ino) != (opened.st_dev, opened.st_ino)
            or opened.st_nlink != 1
        ):
            raise PublicationError("finalization manifest changed before it was opened")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if os.fstat(descriptor).st_nlink != 1:
            raise PublicationError("finalization manifest gained a hard link before append")
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: List[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        original = b"".join(chunks)
        if os.fstat(descriptor).st_nlink != 1:
            raise PublicationError("finalization manifest gained a hard link during append")
        records = _parse_finalization_jsonl(original, manifest_path)

        def prefix_identity(
            prefix_data: bytes, prefix_records: Sequence[Mapping[str, Any]]
        ) -> Dict[str, Any]:
            if not prefix_records:
                raise PublicationError("finalization prefix has no records")
            return {
                "path": str(manifest_path),
                "prefix_bytes": len(prefix_data),
                "prefix_sha256": hashlib.sha256(prefix_data).hexdigest(),
                "record_count": len(prefix_records),
                "last_sequence": prefix_records[-1]["sequence"],
                "last_record_type": prefix_records[-1]["record_type"],
                "finalization_id": prefix_records[-1]["finalization_id"],
            }

        def require_ancestor(prefix_data: bytes) -> None:
            if required_ancestor_prefix is None:
                return
            required = dict(required_ancestor_prefix)
            byte_count = required.get("prefix_bytes")
            if (
                not isinstance(byte_count, int)
                or isinstance(byte_count, bool)
                or byte_count <= 0
                or byte_count > len(prefix_data)
            ):
                raise PublicationError("required ancestor prefix is malformed")
            ancestor_data = prefix_data[:byte_count]
            ancestor_records = _parse_finalization_jsonl(ancestor_data, manifest_path)
            if prefix_identity(ancestor_data, ancestor_records) != required:
                raise PublicationError(
                    "finalization manifest no longer extends the observed ancestor prefix"
                )

        finalization_id = records[0]["finalization_id"]
        operation_id = payload.get("operation_id")
        generation_id = payload.get("generation_id")
        manifest_lines = original.splitlines(keepends=True)
        for existing_index, existing in enumerate(records):
            if (
                existing.get("record_type") == record_type
                and existing.get("operation_id") == operation_id
                and existing.get("generation_id") == generation_id
            ):
                preceding_data = b"".join(manifest_lines[:existing_index])
                preceding_records = records[:existing_index]
                actual_preceding = prefix_identity(preceding_data, preceding_records)
                effective_payload = dict(payload)
                if predecessor_payload_field is not None:
                    effective_payload[predecessor_payload_field] = actual_preceding
                for key, value in effective_payload.items():
                    if existing.get(key) != value:
                        raise PublicationError(
                            "existing finalization record conflicts with this operation"
                        )
                if expected_prefix is not None:
                    if actual_preceding != dict(expected_prefix):
                        raise PublicationError(
                            "existing finalization record does not immediately extend "
                            "the required bounded prefix"
                        )
                require_ancestor(preceding_data)
                through_record = b"".join(manifest_lines[: existing_index + 1])
                return {
                    "record": existing,
                    "manifest_sha256": hashlib.sha256(through_record).hexdigest(),
                    "manifest_prefix_bytes": len(through_record),
                    "appended": False,
                }
        observed_prefix = prefix_identity(original, records)
        if expected_prefix is not None:
            if observed_prefix != dict(expected_prefix):
                raise PublicationError(
                    "finalization manifest changed after the bounded prefix was observed"
                )
        require_ancestor(original)
        effective_payload = dict(payload)
        if predecessor_payload_field is not None:
            effective_payload[predecessor_payload_field] = observed_prefix
        reserved = {
            "schema_version",
            "sequence",
            "recorded_at",
            "record_type",
            "finalization_id",
        }
        if reserved & effective_payload.keys():
            raise PublicationError("finalization record payload overrides journal fields")
        record = {
            "schema_version": 1,
            "sequence": records[-1]["sequence"] + 1,
            "recorded_at": _utc_now(),
            "record_type": record_type,
            "finalization_id": finalization_id,
            **effective_payload,
        }
        encoded = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        if os.fstat(descriptor).st_nlink != 1:
            raise PublicationError("finalization manifest gained a hard link before append")
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        if os.fstat(descriptor).st_nlink != 1:
            raise PublicationError("finalization manifest gained a hard link during append")
        combined = original + encoded
        return {
            "record": record,
            "manifest_sha256": hashlib.sha256(combined).hexdigest(),
            "manifest_prefix_bytes": len(combined),
            "appended": True,
        }
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _validate_prepare_receipt_argument(
    state: Mapping[str, Any], prepare_receipt: Path
) -> Dict[str, Any]:
    expected = state.get("prepare_receipt")
    if not isinstance(expected, dict) or not isinstance(expected.get("path"), str):
        raise PublicationError("operation prepare receipt record is malformed")
    prepare_receipt = _safe_absolute(
        prepare_receipt, label="prepare receipt", must_exist=True
    )
    if prepare_receipt != Path(expected["path"]):
        raise PublicationError("--prepare-receipt does not name the prepared operation receipt")
    raw = _read_regular_bytes(prepare_receipt, label="prepare receipt")
    if hashlib.sha256(raw).hexdigest() != expected.get("sha256"):
        raise PublicationError("--prepare-receipt digest differs from prepared state")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PublicationError(f"prepare receipt is invalid: {exc}") from exc
    if not isinstance(value, dict):
        raise PublicationError("prepare receipt is not an object")
    return value


def reserve_operation(
    *,
    state_root: Path,
    operation: str,
    maintenance_receipt: Path,
    lock_path: Optional[Path] = None,
    prepare_receipt: Optional[Path] = None,
    finalization_manifest: Optional[Path] = None,
) -> Dict[str, Any]:
    """Acquire a durable reservation; an existing record is never taken over."""
    state_root = _safe_absolute(state_root, label="state root", must_exist=True)
    paths, state = _load_state(state_root, operation)
    _validate_package_lock_argument(state_root, operation, lock_path)
    maintenance_receipt = _safe_absolute(
        maintenance_receipt, label="maintenance authorization receipt", must_exist=True
    )
    if _is_within(maintenance_receipt, Path(state["install_root"])):
        raise PublicationError("maintenance receipt must be outside the installed skill root")
    receipt, receipt_digest = _validate_maintenance_receipt(maintenance_receipt, operation)
    manifest_path: Optional[Path] = None
    if finalization_manifest is not None:
        manifest_path = _validate_finalization_manifest_path(
            finalization_manifest, state_root=state_root, state=state
        )
        if manifest_path == maintenance_receipt:
            raise PublicationError(
                "reader-quiescence record and finalization manifest must be different files"
            )
        if prepare_receipt is None:
            raise PublicationError(
                "--prepare-receipt is required with --finalization-manifest"
            )
    with WriterLock(paths["writer_lock"]):
        paths, state = _load_state(state_root, operation)
        if state.get("status") != "PREPARED":
            raise PublicationError(f"operation is not PREPARED: {state.get('status')}")
        _verify_prepare_evidence(state)
        if paths["reservation"].exists() or paths["reservation"].is_symlink():
            raise PublicationError(
                "package reservation already exists; expiry or malformed ownership never permits takeover"
            )
        prepare_receipt_value: Optional[Dict[str, Any]] = None
        manifest_intent: Optional[Dict[str, Any]] = None
        if prepare_receipt is not None:
            prepare_receipt_value = _validate_prepare_receipt_argument(
                state, prepare_receipt
            )
        if manifest_path is not None:
            assert prepare_receipt_value is not None
            intent_payload = {
                "operation_id": operation,
                "generation_id": state["generation_id"],
                "installer": receipt["controller_id"],
                "installed_root": state["install_root"],
                "lock_path": str(paths["reservation"]),
                "prepare_receipt_path": str(prepare_receipt),
                "prepare_receipt_sha256": state["prepare_receipt"]["sha256"],
                "prepare_receipt": prepare_receipt_value,
                "reader_quiescence_record_path": str(maintenance_receipt),
                "reader_quiescence_record_sha256": receipt_digest,
                "reader_quiescence_record": receipt,
                "preflight_inventory": state["preflight_inventory"],
                "candidate_inventory": state["candidate_inventory"],
                "expected_live_source_commit": state["expected_live_source_commit"],
                "reservation_state": "INTENT_RECORDED",
                "atomic_operation": "darwin-rename-swap",
                "mandatory_recovery_condition": (
                    "inspect owner and complete-tree inventories; never delete or take over "
                    "an active, unknown, missing, or malformed lock"
                ),
            }
            manifest_intent = _append_finalization_record(
                manifest_path,
                record_type="installed_publication_reservation_intent",
                payload=intent_payload,
            )
        reservation = {
            "schema_version": 1,
            "operation_id": operation,
            "generation_id": state["generation_id"],
            "created_at": _utc_now(),
            "owner": {
                "host": receipt["owner_host"],
                "pid": receipt["owner_pid"],
                "process_start_identity": receipt["owner_process_start_identity"],
                "controller_id": receipt["controller_id"],
                "controller_state": receipt["controller_state"],
            },
            "maintenance": {
                "authorized_by": receipt["authorized_by"],
                "maintenance_window_id": receipt["maintenance_window_id"],
                "reader_quiescence_status": receipt["reader_quiescence_status"],
                "reader_quiescence_checked_at": receipt["reader_quiescence_checked_at"],
                "receipt_path": str(maintenance_receipt),
                "receipt_sha256": receipt_digest,
            },
            "preflight_inventory_sha256": state["preflight_inventory"]["sha256"],
            "candidate_inventory_sha256": state["candidate_inventory"]["sha256"],
            "expected_live_source_commit": state["expected_live_source_commit"],
        }
        if manifest_path is not None and manifest_intent is not None:
            intent_prefix = _finalization_manifest_prefix(
                manifest_path,
                through_sequence=manifest_intent["record"]["sequence"],
            )
            if (
                intent_prefix["prefix_sha256"] != manifest_intent["manifest_sha256"]
                or intent_prefix["prefix_bytes"]
                != manifest_intent["manifest_prefix_bytes"]
            ):
                raise PublicationError(
                    "reservation intent prefix differs from the appended manifest identity"
                )
            reservation["finalization_manifest"] = {
                "path": str(manifest_path),
                "intent_sequence": manifest_intent["record"]["sequence"],
                "sha256_after_intent": manifest_intent["manifest_sha256"],
                "intent_prefix": intent_prefix,
            }
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(paths["reservation"], flags, 0o600)
        try:
            data = (json.dumps(reservation, sort_keys=True, indent=2) + "\n").encode("utf-8")
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _fsync_directory(state_root)
        state["reservation"] = reservation
        _record_event(state, "reservation_acquired")
        _persist_state(paths["state"], state, "RESERVED")
        return reservation


def _load_reservation(
    paths: Mapping[str, Path], operation: str, state: Mapping[str, Any]
) -> Dict[str, Any]:
    reservation = _read_json_file(paths["reservation"], label="package reservation")
    if reservation.get("schema_version") != 1 or reservation.get("operation_id") != operation:
        raise PublicationError("package reservation is malformed or belongs to another operation")
    owner = reservation.get("owner")
    maintenance = reservation.get("maintenance")
    if not isinstance(owner, dict) or not isinstance(maintenance, dict):
        raise PublicationError("package reservation owner or maintenance record is malformed")
    if owner.get("controller_state") != "ACTIVE":
        raise PublicationError("reservation controller state is not ACTIVE")
    if maintenance.get("reader_quiescence_status") != "QUIESCENT":
        raise PublicationError("reservation does not record quiescent readers")
    recorded_reservation = state.get("reservation")
    if not isinstance(recorded_reservation, dict) or reservation != recorded_reservation:
        raise PublicationError(
            "package reservation differs from the exact reservation stored in operation state"
        )
    candidate_inventory = state.get("candidate_inventory")
    preflight_inventory = state.get("preflight_inventory")
    if (
        not isinstance(candidate_inventory, dict)
        or not isinstance(preflight_inventory, dict)
        or reservation.get("generation_id") != state.get("generation_id")
        or reservation.get("candidate_inventory_sha256")
        != candidate_inventory.get("sha256")
        or reservation.get("preflight_inventory_sha256")
        != preflight_inventory.get("sha256")
        or reservation.get("expected_live_source_commit")
        != state.get("expected_live_source_commit")
    ):
        raise PublicationError("package reservation identity differs from prepared state")
    receipt_path = maintenance.get("receipt_path")
    if not isinstance(receipt_path, str):
        raise PublicationError("reservation lacks maintenance receipt path")
    receipt_file = Path(receipt_path)
    _safe_absolute(receipt_file, label="maintenance receipt", must_exist=True)
    if hashlib.sha256(
        _read_regular_bytes(receipt_file, label="maintenance authorization receipt")
    ).hexdigest() != maintenance.get("receipt_sha256"):
        raise PublicationError("maintenance authorization receipt drifted after reservation")
    return reservation


class AtomicExchanger:
    name = "abstract"

    def require_available(self) -> None:
        raise NotImplementedError

    def exchange(self, left: Path, right: Path) -> None:
        raise NotImplementedError


class DarwinAtomicExchanger(AtomicExchanger):
    """Darwin directory exchange using opened parent fds and RENAME_SWAP."""

    name = "darwin-renameatx-np-rename-swap"

    def __init__(self) -> None:
        self._function: Optional[Any] = None
        if sys.platform == "darwin":
            library = ctypes.CDLL(None, use_errno=True)
            function = getattr(library, "renameatx_np", None)
            if function is not None:
                function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
                function.restype = ctypes.c_int
                self._function = function

    def require_available(self) -> None:
        if self._function is None:
            raise PublicationError(
                "Darwin renameatx_np(RENAME_SWAP) is unavailable; publication is UNCHECKED"
            )

    def exchange(self, left: Path, right: Path) -> None:
        self.require_available()
        left = _safe_absolute(left, label="left exchange directory", must_exist=True)
        right = _safe_absolute(right, label="right exchange directory", must_exist=True)
        if not stat.S_ISDIR(os.lstat(left).st_mode) or not stat.S_ISDIR(os.lstat(right).st_mode):
            raise PublicationError("atomic exchange operands must be directories")
        left_parent_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        right_parent_flags = left_parent_flags
        left_fd = os.open(left.parent, left_parent_flags)
        right_fd = os.open(right.parent, right_parent_flags)
        try:
            if os.fstat(left_fd).st_dev != os.fstat(right_fd).st_dev:
                raise PublicationError("atomic exchange operands are on different filesystems")
            assert self._function is not None
            result = self._function(
                left_fd,
                os.fsencode(left.name),
                right_fd,
                os.fsencode(right.name),
                RENAME_SWAP,
            )
            if result != 0:
                error_number = ctypes.get_errno()
                raise PublicationError(
                    f"renameatx_np(RENAME_SWAP) failed: {os.strerror(error_number)}"
                )
            _fsync_directory(left.parent)
            if right.parent != left.parent:
                _fsync_directory(right.parent)
        finally:
            os.close(left_fd)
            os.close(right_fd)


class FakeAtomicExchanger(AtomicExchanger):
    """Deterministic test double. Never selected by the command-line publisher."""

    name = "TEST-ONLY-fake-exchange"

    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.calls: List[Tuple[Path, Path]] = []

    def require_available(self) -> None:
        if not self.available:
            raise PublicationError("test exchange unavailable")

    def exchange(self, left: Path, right: Path) -> None:
        self.require_available()
        self.calls.append((left, right))
        temporary = left.parent / f".fake-exchange-{len(self.calls)}"
        if temporary.exists():
            raise PublicationError(f"fake exchange scratch path already exists: {temporary}")
        os.rename(left, temporary)
        os.rename(right, left)
        os.rename(temporary, right)


def _identity_at(path: Path, expected_paths: Sequence[str]) -> Optional[str]:
    if not path.exists() or path.is_symlink() or not path.is_dir():
        return None
    try:
        return build_inventory(path, expected_paths).digest
    except PublicationError:
        return None


def classify_generation_state(state_root: Path, operation: str) -> Dict[str, Any]:
    """Classify complete-tree identities without deleting or moving anything."""
    paths, state = _load_state(state_root, operation)
    candidate_paths = state.get("candidate_expected_paths")
    preflight_paths = state.get("preflight_expected_paths")
    if (
        not isinstance(candidate_paths, list)
        or not all(isinstance(item, str) for item in candidate_paths)
        or not isinstance(preflight_paths, list)
        or not all(isinstance(item, str) for item in preflight_paths)
    ):
        raise PublicationError("operation candidate/preflight path sets are malformed")
    old_digest = state["preflight_inventory"]["sha256"]
    new_digest = state["candidate_inventory"]["sha256"]
    identities = {}
    for name, path in {
        "live": Path(state["install_root"]),
        "exchange_slot": paths["slot"],
        "previous": paths["previous"],
        "failed_new": paths["failed"],
    }.items():
        identities[name] = {
            "candidate": _identity_at(path, candidate_paths),
            "preflight": _identity_at(path, preflight_paths),
        }
    if (
        identities["live"]["preflight"] == old_digest
        and identities["exchange_slot"]["candidate"] == new_digest
    ):
        classification = "PRE_SWAP"
    elif (
        identities["live"]["candidate"] == new_digest
        and identities["exchange_slot"]["preflight"] == old_digest
    ):
        classification = "POST_SWAP_SLOT"
    elif (
        identities["live"]["candidate"] == new_digest
        and identities["previous"]["preflight"] == old_digest
    ):
        classification = "POST_SWAP_RETAINED"
    elif (
        identities["live"]["preflight"] == old_digest
        and identities["previous"]["candidate"] == new_digest
    ):
        classification = "ROLLED_BACK_SLOT"
    elif (
        identities["live"]["preflight"] == old_digest
        and identities["failed_new"]["candidate"] == new_digest
    ):
        classification = "ROLLED_BACK_RETAINED"
    else:
        classification = "AMBIGUOUS"
    return {"classification": classification, "identities": identities}


def _move_complete_tree(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise PublicationError(f"retained generation path already exists: {destination}")
    os.rename(source, destination)
    _fsync_directory(source.parent)


def _rollback_after_validation_failure(
    *,
    paths: Mapping[str, Path],
    state: Dict[str, Any],
    install_root: Path,
    candidate_paths: Sequence[str],
    preflight_paths: Sequence[str],
    exchanger: AtomicExchanger,
    reason: str,
) -> None:
    _record_event(state, "rollback_started", reason=reason)
    _persist_state(paths["state"], state, "ROLLBACK_PENDING")
    exchanger.exchange(paths["previous"], install_root)
    old_inventory = build_inventory(install_root, preflight_paths)
    failed_inventory = build_inventory(paths["previous"], candidate_paths)
    if old_inventory.digest != state["preflight_inventory"]["sha256"]:
        raise PublicationError("atomic rollback did not restore the recorded old generation")
    if failed_inventory.digest != state["candidate_inventory"]["sha256"]:
        raise PublicationError("atomic rollback did not retain the complete failed generation")
    _move_complete_tree(paths["previous"], paths["failed"])
    state["rollback"] = {
        "reason": reason,
        "restored_live_inventory": _path_byte_identity(
            old_inventory, paths["preflight_inventory"], preflight_paths
        ),
        "failed_generation_root": str(paths["failed"]),
        "failed_generation_sha256": failed_inventory.digest,
        "failed_generation_identity": _path_byte_identity(
            failed_inventory, paths["candidate_inventory"], candidate_paths
        ),
        "exchange_primitive": exchanger.name,
    }
    state["previous_generation"] = None
    state["mutation_outcome"] = MUTATION_ROLLED_BACK
    _record_event(state, "rollback_completed")
    _persist_state(paths["state"], state, "ROLLED_BACK")


def publish_operation(
    *,
    state_root: Path,
    operation: str,
    exchanger: AtomicExchanger,
    lock_path: Optional[Path] = None,
    checker_runner: CheckerRunner = run_installed_checker,
    failpoint: Failpoint = None,
    recovery_takeover_authorization: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Recheck the reservation/digests and atomically publish one generation."""
    state_root = _safe_absolute(state_root, label="state root", must_exist=True)
    paths, state = _load_state(state_root, operation)
    _validate_package_lock_argument(state_root, operation, lock_path)
    with WriterLock(paths["writer_lock"]):
        paths, state = _load_state(state_root, operation)
        try:
            if state.get("status") != "RESERVED":
                raise PublicationError(f"operation is not RESERVED: {state.get('status')}")
            _verify_prepare_evidence(state)
            reservation = _load_reservation(paths, operation, state)
            if recovery_takeover_authorization is not None:
                if state.get("recovery_takeover_authorization") != recovery_takeover_authorization:
                    raise PublicationError("recovery takeover authorization drifted before resume")
                _verify_recorded_takeover_authorization(state, reservation)
            candidate_paths = state.get("candidate_expected_paths")
            preflight_paths = state.get("preflight_expected_paths")
            if (
                not isinstance(candidate_paths, list)
                or not all(isinstance(item, str) for item in candidate_paths)
                or not isinstance(preflight_paths, list)
                or not all(isinstance(item, str) for item in preflight_paths)
            ):
                raise PublicationError("operation candidate/preflight paths are malformed")
            install_root = _safe_absolute(Path(state["install_root"]), label="install root", must_exist=True)
            slot = _safe_absolute(paths["slot"], label="staged exchange slot", must_exist=True)
            evidence_root = _safe_absolute(Path(state["evidence_root"]), label="evidence root", must_exist=True)
            exchanger.require_available()
            candidate = build_inventory(slot, candidate_paths)
            if candidate.digest != state["candidate_inventory"]["sha256"]:
                raise PublicationError("staged candidate drifted after prepare")
            persisted_candidate = parse_inventory(
                _read_regular_bytes(paths["candidate_inventory"], label="candidate inventory receipt")
            )
            if serialize_inventory(persisted_candidate) != candidate.data:
                raise PublicationError("candidate inventory receipt drifted after prepare")
            snapshot = build_inventory(evidence_root / "snapshot", candidate_paths)
            if snapshot.digest != state["evidence_snapshot"]["sha256"] or snapshot.data != candidate.data:
                raise PublicationError("immutable evidence snapshot drifted after prepare")
            snapshot_inventory_path = Path(state["evidence_snapshot"]["path"])
            persisted_snapshot = parse_inventory(
                _read_regular_bytes(snapshot_inventory_path, label="evidence snapshot inventory")
            )
            if serialize_inventory(persisted_snapshot) != snapshot.data:
                raise PublicationError("evidence snapshot inventory receipt drifted after prepare")
            live_before = build_inventory(install_root, preflight_paths)
            if live_before.digest != state["preflight_inventory"]["sha256"]:
                raise PublicationError("live inventory drifted since recorded preflight")
            persisted_preflight = parse_inventory(
                _read_regular_bytes(paths["preflight_inventory"], label="preflight inventory receipt")
            )
            if serialize_inventory(persisted_preflight) != live_before.data:
                raise PublicationError("preflight inventory receipt drifted after prepare")
            if reservation.get("preflight_inventory_sha256") != live_before.digest:
                raise PublicationError("reservation preflight digest does not match live tree")
            if reservation.get("candidate_inventory_sha256") != candidate.digest:
                raise PublicationError("reservation candidate digest does not match staged tree")
            if (
                reservation.get("expected_live_source_commit")
                != state["expected_live_source_commit"]
            ):
                raise PublicationError("reservation predecessor commit differs from state")
            staged_validation = _run_bound_checker(
                state, paths["source"], slot, checker_runner
            )
            state.setdefault("validation", {})["immediate_pre_swap_staged"] = staged_validation
            candidate_after_checker = build_inventory(slot, candidate_paths)
            if (
                candidate_after_checker.digest
                != state["candidate_inventory"]["sha256"]
                or candidate_after_checker.digest != candidate.digest
                or candidate_after_checker.data != candidate.data
            ):
                raise PublicationError(
                    "staged candidate changed during the immediate pre-swap checker"
                )
            state["live_inventory_immediately_before_swap"] = _path_byte_identity(
                live_before, paths["preflight_inventory"], preflight_paths
            )
            _record_event(state, "preflight_recheck_passed")
            _persist_state(paths["state"], state, "SWAP_PENDING")
            if failpoint:
                failpoint("before_exchange")
            exchanger.exchange(slot, install_root)
            state["exchange_primitive"] = exchanger.name
            _record_event(state, "atomic_exchange_completed")
            _persist_state(paths["state"], state, "SWAPPED")
            new_live = build_inventory(install_root, candidate_paths)
            old_slot = build_inventory(slot, preflight_paths)
            if new_live.digest != candidate.digest or old_slot.digest != live_before.digest:
                raise PublicationError("post-exchange trees do not match complete old/new identities")
            _move_complete_tree(slot, paths["previous"])
            state["previous_generation"] = {
                "root": str(paths["previous"]),
                **_path_byte_identity(
                    live_before, paths["preflight_inventory"], preflight_paths
                ),
            }
            _record_event(state, "previous_generation_retained")
            _persist_state(paths["state"], state, "VALIDATING")
            if failpoint:
                failpoint("after_exchange")
            try:
                live_validation = _run_bound_checker(
                    state, paths["source"], install_root, checker_runner
                )
                if failpoint:
                    failpoint("post_validation")
            except ValidationFailure as exc:
                _rollback_after_validation_failure(
                    paths=paths,
                    state=state,
                    install_root=install_root,
                    candidate_paths=candidate_paths,
                    preflight_paths=preflight_paths,
                    exchanger=exchanger,
                    reason=str(exc),
                )
                raise
            accepted_live = build_inventory(install_root, candidate_paths)
            accepted_previous = build_inventory(paths["previous"], preflight_paths)
            if accepted_live.digest != candidate.digest or accepted_previous.digest != live_before.digest:
                raise PublicationError("live or retained previous generation drifted during validation")
            state.setdefault("validation", {})["post_publish_live"] = live_validation
            state["live_inventory_at_publication"] = _path_byte_identity(
                accepted_live, evidence_root / "snapshot.inventory", candidate_paths
            )
            state["mutation_outcome"] = MUTATION_PUBLISHED
            _record_event(state, "publication_validated")
            _persist_state(paths["state"], state, "PUBLISHED")
            return state
        except ValidationFailure:
            raise
        except Exception as exc:
            try:
                inspection = classify_generation_state(state_root, operation)
                state["last_inspection"] = inspection
            except Exception as inspection_exc:
                state["last_inspection"] = {
                    "classification": "AMBIGUOUS",
                    "inspection_error": str(inspection_exc),
                }
            _record_event(state, "publication_stopped_unchecked", error=str(exc))
            _persist_state(paths["state"], state, "UNCHECKED")
            raise


def _validate_takeover_authorization(
    path: Optional[Path],
    *,
    state: Mapping[str, Any],
    reservation: Mapping[str, Any],
) -> Dict[str, Any]:
    """Require durable proof that the reserved owner was inspected and is inactive."""
    if path is None:
        raise PublicationError(
            "mutating recovery requires durable stopped/superseded takeover authorization"
        )
    path = _safe_absolute(path, label="takeover authorization", must_exist=True)
    if _is_within(path, Path(str(state["install_root"]))):
        raise PublicationError("takeover authorization must be outside the installed root")
    evidence_root = Path(str(state["evidence_root"]))
    reserved_evidence_paths = {
        evidence_root / "publication-receipt.json",
        evidence_root / "terminal-validation-live.inventory",
        evidence_root / "snapshot.inventory",
    }
    if path in reserved_evidence_paths or _is_within(path, evidence_root / "snapshot"):
        raise PublicationError("takeover authorization overlaps reserved evidence output")
    authorization = _read_json_file(path, label="takeover authorization")
    prior_owner = reservation.get("owner")
    inspection = authorization.get("inspection")
    disposition = authorization.get("owner_disposition")
    required_strings = ("authorized_by", "authorized_at")
    if (
        authorization.get("schema_version") != 1
        or authorization.get("operation_id") != state.get("operation_id")
        or authorization.get("generation_id") != state.get("generation_id")
        or authorization.get("prior_owner") != prior_owner
    ):
        raise PublicationError("takeover authorization is not bound to this owner and operation")
    if disposition not in {"STOPPED", "SUPERSEDED"}:
        raise PublicationError("takeover owner disposition is not STOPPED or SUPERSEDED")
    if any(
        not isinstance(authorization.get(key), str) or not authorization[key].strip()
        for key in required_strings
    ):
        raise PublicationError("takeover authorization lacks durable author/time identity")
    if not isinstance(inspection, dict):
        raise PublicationError("takeover authorization lacks owner inspection evidence")
    evidence = inspection.get("evidence")
    if (
        inspection.get("owner_process_status") != "INACTIVE"
        or inspection.get("tool_session_status") != "INACTIVE"
        or not isinstance(inspection.get("inspected_at"), str)
        or not inspection["inspected_at"].strip()
        or not isinstance(inspection.get("inspected_by"), str)
        or not inspection["inspected_by"].strip()
        or not isinstance(evidence, list)
        or not evidence
        or any(not isinstance(item, str) or not item.strip() for item in evidence)
    ):
        raise PublicationError(
            "takeover inspection does not prove inactive process and tool session"
        )
    raw = _read_regular_bytes(path, label="takeover authorization")
    return {
        "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "authorization": authorization,
    }


def _verify_recorded_takeover_authorization(
    state: Mapping[str, Any], reservation: Mapping[str, Any]
) -> Optional[Dict[str, Any]]:
    recorded = state.get("recovery_takeover_authorization")
    if recorded is None:
        return None
    if not isinstance(recorded, dict) or not isinstance(recorded.get("path"), str):
        raise PublicationError("recorded recovery takeover authorization is malformed")
    observed = _validate_takeover_authorization(
        Path(recorded["path"]), state=state, reservation=reservation
    )
    if observed != recorded:
        raise PublicationError("recovery takeover authorization drifted after recovery")
    return observed


def recover_operation(
    *,
    state_root: Path,
    operation: str,
    action: str,
    exchanger: AtomicExchanger,
    lock_path: Optional[Path] = None,
    takeover_authorization: Optional[Path] = None,
    checker_runner: CheckerRunner = run_installed_checker,
) -> Dict[str, Any]:
    """Inspect first; complete or roll back only an unambiguous complete-tree state."""
    state_root = _safe_absolute(state_root, label="state root", must_exist=True)
    paths, state = _load_state(state_root, operation)
    _validate_package_lock_argument(state_root, operation, lock_path)
    resume_pre_swap = False
    with WriterLock(paths["writer_lock"]):
        paths, state = _load_state(state_root, operation)
        _verify_prepare_evidence(state)
        reservation = _load_reservation(paths, operation, state)
        _verify_recorded_takeover_authorization(state, reservation)
        inspection = classify_generation_state(state_root, operation)
        if action == "inspect":
            return inspection
        takeover = _validate_takeover_authorization(
            takeover_authorization, state=state, reservation=reservation
        )
        state["recovery_takeover_authorization"] = takeover
        _record_event(
            state,
            "mutating_recovery_takeover_authorized",
            owner_disposition=takeover["authorization"]["owner_disposition"],
            authorization_sha256=takeover["sha256"],
        )
        _persist_state(paths["state"], state)
        if inspection["classification"] == "AMBIGUOUS":
            state["last_inspection"] = inspection
            _record_event(state, "ambiguous_recovery_refused")
            _persist_state(paths["state"], state, "UNCHECKED")
            raise PublicationError("recovery state is ambiguous; reservation and all trees were preserved")
        candidate_paths = state["candidate_expected_paths"]
        preflight_paths = state["preflight_expected_paths"]
        install_root = Path(state["install_root"])
        exchanger.require_available()
        if action == "complete":
            if inspection["classification"] == "PRE_SWAP":
                _record_event(state, "pre_swap_recovery_authorized")
                _persist_state(paths["state"], state, "RESERVED")
                resume_pre_swap = True
            else:
                if inspection["classification"] == "POST_SWAP_SLOT":
                    _move_complete_tree(paths["slot"], paths["previous"])
                elif inspection["classification"] != "POST_SWAP_RETAINED":
                    raise PublicationError(f"cannot complete from {inspection['classification']}")
                retained = build_inventory(paths["previous"], preflight_paths)
                live_after_recovery = build_inventory(install_root, candidate_paths)
                state["previous_generation"] = {
                    "root": str(paths["previous"]),
                    **_path_byte_identity(
                        retained, paths["preflight_inventory"], preflight_paths
                    ),
                }
                state["live_inventory_at_publication"] = {
                    "root": str(install_root),
                    **_path_byte_identity(
                        live_after_recovery,
                        Path(state["evidence_root"]) / "snapshot.inventory",
                        candidate_paths,
                    ),
                }
                state["exchange_primitive"] = exchanger.name
                try:
                    validation = _run_bound_checker(
                        state, paths["source"], install_root, checker_runner
                    )
                except ValidationFailure as exc:
                    _rollback_after_validation_failure(
                        paths=paths,
                        state=state,
                        install_root=install_root,
                        candidate_paths=candidate_paths,
                        preflight_paths=preflight_paths,
                        exchanger=exchanger,
                        reason=str(exc),
                    )
                    raise
                checked_live = build_inventory(install_root, candidate_paths)
                checked_previous = build_inventory(paths["previous"], preflight_paths)
                if (
                    checked_live.digest != state["candidate_inventory"]["sha256"]
                    or checked_previous.digest
                    != state["preflight_inventory"]["sha256"]
                ):
                    raise PublicationError(
                        "recovery checker changed the live or retained generation"
                    )
                state.setdefault("validation", {})["recovery_live"] = validation
                state["mutation_outcome"] = MUTATION_PUBLISHED
                _record_event(state, "publication_completed_by_recovery")
                _persist_state(paths["state"], state, "PUBLISHED")
                return state
        if action == "rollback":
            if inspection["classification"] == "POST_SWAP_SLOT":
                exchanger.exchange(paths["slot"], install_root)
                _move_complete_tree(paths["slot"], paths["failed"])
            elif inspection["classification"] == "POST_SWAP_RETAINED":
                exchanger.exchange(paths["previous"], install_root)
                _move_complete_tree(paths["previous"], paths["failed"])
            else:
                raise PublicationError(f"cannot roll back from {inspection['classification']}")
            restored = build_inventory(install_root, preflight_paths)
            if restored.digest != state["preflight_inventory"]["sha256"]:
                raise PublicationError("recovery rollback did not restore the old generation")
            failed = build_inventory(paths["failed"], candidate_paths)
            state["rollback"] = {
                "reason": "explicit unambiguous recovery rollback",
                "restored_live_inventory": _path_byte_identity(
                    restored, paths["preflight_inventory"], preflight_paths
                ),
                "failed_generation_root": str(paths["failed"]),
                "failed_generation_sha256": failed.digest,
                "failed_generation_identity": _path_byte_identity(
                    failed, paths["candidate_inventory"], candidate_paths
                ),
                "exchange_primitive": exchanger.name,
            }
            state["previous_generation"] = None
            state["mutation_outcome"] = MUTATION_ROLLED_BACK
            _record_event(state, "rollback_completed_by_recovery")
            _persist_state(paths["state"], state, "ROLLED_BACK")
            return state
        if action != "complete":
            raise PublicationError(f"unsupported recovery action: {action}")
    if resume_pre_swap:
        return publish_operation(
            state_root=state_root,
            operation=operation,
            exchanger=exchanger,
            checker_runner=checker_runner,
            recovery_takeover_authorization=takeover,
        )
    raise PublicationError("recovery did not select a complete-tree action")


def _reject_receipt_output_overlap(
    receipt_output: Path,
    *,
    state_root: Path,
    state: Mapping[str, Any],
    reservation: Optional[Mapping[str, Any]] = None,
) -> None:
    protected_roots = {
        "state root": state_root,
        "operation root": _operation_paths(state_root, str(state["operation_id"]))["operation"],
        "evidence root": Path(str(state["evidence_root"])),
        "installed skill root": Path(str(state["install_root"])),
        "source repository": Path(str(state["source_repository"])),
    }
    for label, protected in protected_roots.items():
        protected = _safe_absolute(protected, label=label, must_exist=True)
        if _is_within(receipt_output, protected) or _is_within(protected, receipt_output):
            raise PublicationError(
                f"final receipt output overlaps protected {label}: {receipt_output}"
            )
    if reservation is not None:
        maintenance = reservation.get("maintenance")
        if not isinstance(maintenance, dict):
            raise PublicationError("reservation maintenance record is malformed")
        maintenance_path_text = maintenance.get("receipt_path")
        if not isinstance(maintenance_path_text, str):
            raise PublicationError("reservation maintenance receipt path is malformed")
        maintenance_path = _safe_absolute(
            Path(maintenance_path_text),
            label="maintenance authorization receipt",
            must_exist=True,
        )
        if receipt_output == maintenance_path:
            raise PublicationError(
                "final receipt output collides with the maintenance authorization receipt"
            )
    takeover = state.get("recovery_takeover_authorization")
    if isinstance(takeover, dict) and isinstance(takeover.get("path"), str):
        takeover_path = _safe_absolute(
            Path(takeover["path"]),
            label="takeover authorization",
            must_exist=True,
        )
        if receipt_output == takeover_path:
            raise PublicationError(
                "final receipt output collides with the takeover authorization"
            )


def _write_once_or_verify_bytes(path: Path, data: bytes, *, label: str) -> None:
    if path.exists() or path.is_symlink():
        if _read_regular_bytes(path, label=label) != data:
            raise PublicationError(f"existing {label} conflicts with this operation")
        return
    _write_new_file(path, data)
    _fsync_directory(path.parent)


def _complete_pending_terminal_finalization(
    *,
    paths: Mapping[str, Path],
    state: Dict[str, Any],
    reservation: Mapping[str, Any],
    state_root: Path,
    operation: str,
    receipt_output: Optional[Path],
    manifest_path: Optional[Path],
) -> Dict[str, Any]:
    """Resume the deterministic receipt/manifest boundary after any crash."""
    pending = state.get("pending_terminal_finalization")
    if not isinstance(pending, dict) or not isinstance(pending.get("receipt"), dict):
        raise PublicationError("FINALIZING state lacks deterministic terminal evidence")
    _verify_prepare_evidence(state)
    receipt = pending["receipt"]
    evidence_receipt = Path(str(pending.get("evidence_receipt")))
    durable_receipt = Path(str(pending.get("durable_receipt")))
    pending_manifest = pending.get("finalization_manifest")
    if (
        evidence_receipt != Path(str(state["evidence_root"])) / "publication-receipt.json"
        or not evidence_receipt.is_absolute()
        or not durable_receipt.is_absolute()
        or (receipt_output is not None and durable_receipt != receipt_output)
        or pending_manifest != (str(manifest_path) if manifest_path else None)
    ):
        raise PublicationError("terminal finalization retry arguments differ from pending state")
    terminal_state = state.get("terminal_state")
    candidate_paths = state.get("candidate_expected_paths")
    preflight_paths = state.get("preflight_expected_paths")
    if terminal_state == "PUBLISHED":
        live_paths = candidate_paths
        expected_inventory = state.get("candidate_inventory")
        expected_mutation = MUTATION_PUBLISHED
    elif terminal_state == "ROLLED_BACK":
        live_paths = preflight_paths
        expected_inventory = state.get("preflight_inventory")
        expected_mutation = MUTATION_ROLLED_BACK
    else:
        raise PublicationError("pending terminal state is malformed")
    if (
        not isinstance(live_paths, list)
        or not live_paths
        or not isinstance(candidate_paths, list)
        or not candidate_paths
        or not isinstance(expected_inventory, dict)
    ):
        raise PublicationError("pending terminal inventory identity is malformed")
    snapshot = build_inventory(
        Path(str(state["evidence_root"])) / "snapshot", candidate_paths
    )
    snapshot_sidecar = Path(str(state["evidence_snapshot"]["path"]))
    persisted_snapshot = _read_regular_bytes(
        snapshot_sidecar, label="evidence snapshot inventory"
    )
    if (
        snapshot.digest != state["candidate_inventory"]["sha256"]
        or persisted_snapshot != snapshot.data
        or serialize_inventory(parse_inventory(persisted_snapshot)) != snapshot.data
    ):
        raise PublicationError("evidence snapshot drifted during terminal finalization")
    live = build_inventory(Path(str(state["install_root"])), live_paths)
    terminal_inventory_path = Path(str(state["evidence_root"])) / (
        "terminal-validation-live.inventory"
    )
    expected_live_identity = _path_byte_identity(
        live, terminal_inventory_path, live_paths
    )
    if (
        live.digest != expected_inventory.get("sha256")
        or receipt.get("schema_version") != RECEIPT_SCHEMA_VERSION
        or receipt.get("operation_id") != operation
        or receipt.get("generation_id") != state.get("generation_id")
        or receipt.get("terminal_state") != terminal_state
        or receipt.get("live_inventory_at_terminal_validation")
        != expected_live_identity
        or receipt.get("reservation") != reservation
        or receipt.get("candidate_inventory") != state.get("candidate_inventory")
        or receipt.get("live_inventory_at_dispatch") != state.get("preflight_inventory")
        or receipt.get("mutation_outcome") != expected_mutation
        or receipt.get("reservation_state")
        != "RETAINED_PENDING_PANEL_ACCEPTANCE"
        or receipt.get("finalization_manifest") != pending_manifest
        or receipt.get("source")
        != {
            "commit": state["source_commit"],
            "tree": state["source_tree"],
            "manifest_path": state["manifest_path"],
            "manifest_sha256": state["manifest_sha256"],
        }
        or receipt.get("expected_live_source")
        != {
            "commit": state["expected_live_source_commit"],
            "tree": state["expected_live_source_tree"],
            "manifest_path": state["manifest_path"],
            "manifest_sha256": state["expected_live_manifest_sha256"],
        }
        or receipt.get("evidence_snapshot") != state.get("evidence_snapshot")
    ):
        raise PublicationError("pending terminal receipt differs from current exact state")
    named_outcomes = receipt.get("named_mutation_outcomes")
    if not isinstance(named_outcomes, dict) or not named_outcomes:
        raise PublicationError("pending terminal receipt lacks named mutation outcomes")
    _write_once_or_verify_bytes(
        terminal_inventory_path,
        live.data,
        label="terminal live inventory",
    )
    _write_once_or_verify_json(
        evidence_receipt, receipt, label="canonical terminal publication receipt"
    )
    if durable_receipt != evidence_receipt:
        _mkdir_secure(durable_receipt.parent, exist_ok=True)
        _write_once_or_verify_json(
            durable_receipt, receipt, label="external terminal publication receipt"
        )
    reread = _read_json_file(durable_receipt, label="durable final receipt")
    if reread != receipt:
        raise PublicationError("durable final receipt did not round-trip")
    receipt_digest = hashlib.sha256(
        _read_regular_bytes(durable_receipt, label="final publication receipt")
    ).hexdigest()
    manifest_terminal: Optional[Dict[str, Any]] = None
    terminal_prefix: Optional[Dict[str, Any]] = None
    if manifest_path is not None:
        manifest_record = reservation.get("finalization_manifest")
        intent_prefix = (
            manifest_record.get("intent_prefix")
            if isinstance(manifest_record, dict)
            else None
        )
        if not isinstance(intent_prefix, dict):
            raise PublicationError("reservation lacks its terminal predecessor prefix")
        manifest_terminal = _append_finalization_record(
            manifest_path,
            record_type="installed_publication_terminal",
            expected_prefix=intent_prefix,
            payload={
                "operation_id": operation,
                "generation_id": state["generation_id"],
                "installed_root": state["install_root"],
                "lock_path": str(paths["reservation"]),
                "reservation_state": "RETAINED_PENDING_PANEL_ACCEPTANCE",
                "terminal_state": terminal_state,
                "publication_receipt_path": str(durable_receipt),
                "publication_receipt_sha256": receipt_digest,
                "publication_receipt": receipt,
            },
        )
        terminal_prefix = _finalization_manifest_prefix(
            manifest_path,
            through_sequence=manifest_terminal["record"]["sequence"],
            required_prefix=intent_prefix,
        )
        if (
            terminal_prefix["prefix_sha256"]
            != manifest_terminal["manifest_sha256"]
            or terminal_prefix["prefix_bytes"]
            != manifest_terminal["manifest_prefix_bytes"]
        ):
            raise PublicationError(
                "terminal manifest prefix differs from the appended record identity"
            )
    state["final_receipt"] = {
        "path": str(durable_receipt),
        "sha256": receipt_digest,
    }
    if terminal_prefix is not None:
        state["finalization_manifest_terminal"] = terminal_prefix
    state["terminal_finalization"] = pending
    state.pop("pending_terminal_finalization", None)
    _record_event(state, "terminal_validation_finalized_reservation_retained")
    _persist_state(paths["state"], state, "FINALIZED_RESERVED")
    return receipt


def finalize_operation(
    *,
    state_root: Path,
    operation: str,
    receipt_output: Optional[Path] = None,
    lock_path: Optional[Path] = None,
    finalization_manifest: Optional[Path] = None,
    exchanger: Optional[AtomicExchanger] = None,
    checker_runner: CheckerRunner = run_installed_checker,
) -> Dict[str, Any]:
    """Persist terminal validation evidence while retaining the reservation."""
    state_root = _safe_absolute(state_root, label="state root", must_exist=True)
    paths, state = _load_state(state_root, operation)
    _validate_package_lock_argument(state_root, operation, lock_path)
    if receipt_output is not None:
        receipt_output = _safe_absolute(receipt_output, label="final receipt output")
        _reject_receipt_output_overlap(
            receipt_output,
            state_root=state_root,
            state=state,
        )
    manifest_path: Optional[Path] = None
    if finalization_manifest is not None:
        manifest_path = _validate_finalization_manifest_path(
            finalization_manifest, state_root=state_root, state=state
        )
    with WriterLock(paths["writer_lock"]):
        paths, state = _load_state(state_root, operation)
        reservation = _load_reservation(paths, operation, state)
        _verify_recorded_takeover_authorization(state, reservation)
        if receipt_output is not None:
            _reject_receipt_output_overlap(
                receipt_output,
                state_root=state_root,
                state=state,
                reservation=reservation,
            )
        if manifest_path is not None:
            reserved_manifest = _reserved_finalization_manifest(
                reservation, state_root=state_root, state=state
            )
            if reserved_manifest != manifest_path:
                raise PublicationError(
                    "finalization manifest differs from the reserved publication manifest"
                )
        status = state.get("status")
        if status == "FINALIZING":
            return _complete_pending_terminal_finalization(
                paths=paths,
                state=state,
                reservation=reservation,
                state_root=state_root,
                operation=operation,
                receipt_output=receipt_output,
                manifest_path=manifest_path,
            )
        if status not in {"PUBLISHED", "ROLLED_BACK"}:
            raise PublicationError(f"cannot finalize non-terminal publication state: {status}")
        if receipt_output is not None and (
            receipt_output.exists() or receipt_output.is_symlink()
        ):
            raise PublicationError(
                "external final receipt output already exists; refusing to overwrite it"
            )
        _verify_prepare_evidence(state)
        candidate_paths = state["candidate_expected_paths"]
        preflight_paths = state["preflight_expected_paths"]
        install_root = Path(state["install_root"])
        live_paths = candidate_paths if status == "PUBLISHED" else preflight_paths
        live = build_inventory(install_root, live_paths)
        expected_live_digest = (
            state["candidate_inventory"]["sha256"]
            if status == "PUBLISHED"
            else state["preflight_inventory"]["sha256"]
        )
        if live.digest != expected_live_digest:
            raise PublicationError("live tree drifted before finalization")
        terminal_validation: Optional[Dict[str, Any]] = None
        if status == "PUBLISHED":
            try:
                terminal_validation = _run_bound_checker(
                    state, paths["source"], install_root, checker_runner
                )
                live_after_terminal_checker = build_inventory(install_root, candidate_paths)
                if (
                    live_after_terminal_checker.digest != expected_live_digest
                    or live_after_terminal_checker.data != live.data
                ):
                    raise PublicationError(
                        "terminal checker changed the live generation before finalization"
                    )
                live = live_after_terminal_checker
            except ValidationFailure as exc:
                rollback_exchanger = exchanger or DarwinAtomicExchanger()
                try:
                    rollback_exchanger.require_available()
                    _rollback_after_validation_failure(
                        paths=paths,
                        state=state,
                        install_root=install_root,
                        candidate_paths=candidate_paths,
                        preflight_paths=preflight_paths,
                        exchanger=rollback_exchanger,
                        reason=f"finalization validation failed: {exc}",
                    )
                except Exception as rollback_exc:
                    _record_event(
                        state,
                        "finalization_rollback_stopped_unchecked",
                        validation_error=str(exc),
                        rollback_error=str(rollback_exc),
                    )
                    _persist_state(paths["state"], state, "UNCHECKED")
                    raise PublicationError(
                        "finalization validation failed and rollback could not be proved"
                    ) from rollback_exc
                raise
            except Exception as exc:
                _record_event(
                    state,
                    "finalization_stopped_unchecked",
                    error=str(exc),
                )
                _persist_state(paths["state"], state, "UNCHECKED")
                raise
        evidence_root = Path(state["evidence_root"])
        snapshot = build_inventory(evidence_root / "snapshot", candidate_paths)
        if snapshot.digest != state["candidate_inventory"]["sha256"]:
            raise PublicationError("evidence snapshot drifted before finalization")
        persisted_snapshot = parse_inventory(
            _read_regular_bytes(
                Path(state["evidence_snapshot"]["path"]),
                label="evidence snapshot inventory",
            )
        )
        if serialize_inventory(persisted_snapshot) != snapshot.data:
            raise PublicationError("evidence snapshot inventory receipt drifted before finalization")
        expected_mutation_outcome = (
            MUTATION_PUBLISHED if status == "PUBLISHED" else MUTATION_ROLLED_BACK
        )
        if state.get("mutation_outcome") != expected_mutation_outcome:
            raise PublicationError(
                "terminal state lacks its exact named live mutation outcome"
            )
        terminal_inventory_path = evidence_root / "terminal-validation-live.inventory"
        receipt = {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "operation_id": operation,
            "generation_id": state["generation_id"],
            "terminal_state": status,
            "source": {
                "commit": state["source_commit"],
                "tree": state["source_tree"],
                "manifest_path": state["manifest_path"],
                "manifest_sha256": state["manifest_sha256"],
            },
            "expected_live_source": {
                "commit": state["expected_live_source_commit"],
                "tree": state["expected_live_source_tree"],
                "manifest_path": state["manifest_path"],
                "manifest_sha256": state["expected_live_manifest_sha256"],
            },
            "evidence_snapshot": state["evidence_snapshot"],
            "candidate_inventory": state["candidate_inventory"],
            "live_inventory_at_dispatch": state["preflight_inventory"],
            "live_inventory_immediately_before_swap": state.get(
                "live_inventory_immediately_before_swap"
            ),
            "live_inventory_at_terminal_validation": _path_byte_identity(
                live, terminal_inventory_path, live_paths
            ),
            "reservation": reservation,
            "exchange_primitive": state.get("exchange_primitive"),
            "previous_generation": state.get("previous_generation"),
            "rollback": state.get("rollback"),
            "mutation_outcome": expected_mutation_outcome,
            "recovery_takeover_authorization": state.get(
                "recovery_takeover_authorization"
            ),
            "reservation_state": "RETAINED_PENDING_PANEL_ACCEPTANCE",
            "finalization_manifest": str(manifest_path) if manifest_path else None,
            "validation": {
                **state.get("validation", {}),
                "terminal_live": terminal_validation,
            },
            "named_mutation_outcomes": {
                name: validation.get("named_mutation_outcomes")
                for name, validation in {
                    **state.get("validation", {}),
                    "terminal_live": terminal_validation,
                }.items()
                if isinstance(validation, dict)
                and isinstance(validation.get("named_mutation_outcomes"), dict)
            },
            "finalized_at": _utc_now(),
        }
        evidence_receipt = evidence_root / "publication-receipt.json"
        durable_receipt = evidence_receipt
        if receipt_output is not None:
            durable_receipt = receipt_output
        state["terminal_state"] = status
        state["pending_terminal_finalization"] = {
            "receipt": receipt,
            "evidence_receipt": str(evidence_receipt),
            "durable_receipt": str(durable_receipt),
            "finalization_manifest": str(manifest_path) if manifest_path else None,
        }
        _record_event(state, "terminal_finalization_evidence_staged")
        _persist_state(paths["state"], state, "FINALIZING")
        return _complete_pending_terminal_finalization(
            paths=paths,
            state=state,
            reservation=reservation,
            state_root=state_root,
            operation=operation,
            receipt_output=receipt_output,
            manifest_path=manifest_path,
        )


def _validated_record_text(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise PublicationError(f"{label} must be a non-empty normalized string")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise PublicationError(f"{label} contains a control character")
    return value


def _reserved_finalization_manifest(
    reservation: Mapping[str, Any], *, state_root: Path, state: Mapping[str, Any]
) -> Path:
    record = reservation.get("finalization_manifest")
    if not isinstance(record, dict) or not isinstance(record.get("path"), str):
        raise PublicationError(
            "the active reservation lacks a bound finalization manifest"
        )
    path = _validate_finalization_manifest_path(
        Path(record["path"]), state_root=state_root, state=state
    )
    intent_prefix = record.get("intent_prefix")
    if not isinstance(intent_prefix, dict):
        raise PublicationError(
            "the active reservation lacks its durable intent-manifest prefix"
        )
    _finalization_manifest_prefix(path, required_prefix=intent_prefix)
    return path


def _reservation_identity(
    paths: Mapping[str, Path], reservation: Mapping[str, Any]
) -> Dict[str, Any]:
    raw = _read_regular_bytes(paths["reservation"], label="active package reservation")
    try:
        observed = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PublicationError(f"active package reservation is invalid JSON: {exc}") from exc
    if observed != reservation:
        raise PublicationError("active package reservation changed during identity capture")
    return {
        "path": str(paths["reservation"]),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "operation_id": reservation.get("operation_id"),
        "generation_id": reservation.get("generation_id"),
        "owner": reservation.get("owner"),
        "maintenance": reservation.get("maintenance"),
    }


def _terminal_receipt(
    state: Mapping[str, Any], *, operation: str
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    identity = state.get("final_receipt")
    if (
        not isinstance(identity, dict)
        or not isinstance(identity.get("path"), str)
        or not HEX_64_RE.fullmatch(str(identity.get("sha256")))
    ):
        raise PublicationError("operation lacks a durable terminal receipt identity")
    raw = _read_regular_bytes(Path(identity["path"]), label="terminal publication receipt")
    if hashlib.sha256(raw).hexdigest() != identity["sha256"]:
        raise PublicationError("terminal publication receipt drifted")
    try:
        receipt = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PublicationError(f"terminal publication receipt is invalid: {exc}") from exc
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema_version") != RECEIPT_SCHEMA_VERSION
        or receipt.get("operation_id") != operation
        or receipt.get("generation_id") != state.get("generation_id")
        or receipt.get("terminal_state") != state.get("terminal_state")
        or receipt.get("reservation_state") != "RETAINED_PENDING_PANEL_ACCEPTANCE"
    ):
        raise PublicationError("terminal publication receipt is not bound to this operation")
    return dict(identity), receipt


def _terminal_live_identity(
    state: Mapping[str, Any]
) -> Tuple[str, List[str], Mapping[str, Any]]:
    terminal_state = state.get("terminal_state")
    if terminal_state == "PUBLISHED":
        expected_paths = state.get("candidate_expected_paths")
        expected_inventory = state.get("candidate_inventory")
    elif terminal_state == "ROLLED_BACK":
        expected_paths = state.get("preflight_expected_paths")
        expected_inventory = state.get("preflight_inventory")
    else:
        raise PublicationError("operation lacks a valid terminal live-generation state")
    if (
        not isinstance(expected_paths, list)
        or not expected_paths
        or any(not isinstance(path, str) for path in expected_paths)
        or not isinstance(expected_inventory, dict)
        or not HEX_64_RE.fullmatch(str(expected_inventory.get("sha256")))
    ):
        raise PublicationError("terminal live-generation identity is malformed")
    return terminal_state, expected_paths, expected_inventory


def _live_inventory_report_paths(
    output: Path, *, evidence_root: Path
) -> Tuple[Path, Path]:
    output = _safe_absolute(output, label="live inventory receipt output")
    evidence_root = _safe_absolute(
        evidence_root, label="operation evidence root", must_exist=True
    )
    if output == evidence_root or not _is_within(output, evidence_root):
        raise PublicationError("live inventory receipt must be inside the evidence root")
    inventory_path = output.with_name(output.name + ".inventory")
    for candidate in (output, inventory_path):
        _safe_absolute(candidate, label="live inventory evidence output")
        if candidate.exists() or candidate.is_symlink():
            raise PublicationError(
                f"live inventory evidence output already exists: {candidate}"
            )
        if _is_within(candidate, evidence_root / "snapshot") or candidate in {
            evidence_root / "snapshot.inventory",
            evidence_root / "publication-receipt.json",
            evidence_root / "terminal-validation-live.inventory",
        }:
            raise PublicationError(
                f"live inventory evidence overlaps reserved evidence: {candidate}"
            )
    return output, inventory_path


def report_live_inventory(
    *,
    state_root: Path,
    operation: str,
    phase: str,
    output: Path,
    lock_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Record an exact live inventory and manifest prefix under the reservation."""
    if phase not in LIVE_INVENTORY_PHASES:
        raise PublicationError(f"unsupported live inventory phase: {phase!r}")
    state_root = _safe_absolute(state_root, label="state root", must_exist=True)
    paths, state = _load_state(state_root, operation)
    _validate_package_lock_argument(state_root, operation, lock_path)
    output, inventory_path = _live_inventory_report_paths(
        output, evidence_root=Path(str(state["evidence_root"]))
    )
    with WriterLock(paths["writer_lock"]):
        paths, state = _load_state(state_root, operation)
        if state.get("status") != "FINALIZED_RESERVED":
            raise PublicationError(
                "live inventory reporting requires terminal validation and a retained reservation"
            )
        reservation = _load_reservation(paths, operation, state)
        _verify_recorded_takeover_authorization(state, reservation)
        _verify_prepare_evidence(state)
        manifest_path = _reserved_finalization_manifest(
            reservation, state_root=state_root, state=state
        )
        terminal_prefix = state.get("finalization_manifest_terminal")
        if not isinstance(terminal_prefix, dict):
            raise PublicationError(
                "terminal state lacks its durable finalization-manifest prefix"
            )
        manifest_prefix = _finalization_manifest_prefix(
            manifest_path, required_prefix=terminal_prefix
        )
        terminal_receipt_identity, _ = _terminal_receipt(state, operation=operation)
        terminal_state, live_paths, expected_inventory = _terminal_live_identity(state)
        live = build_inventory(Path(str(state["install_root"])), live_paths)
        if live.digest != expected_inventory["sha256"]:
            raise PublicationError(
                f"live tree drifted before the {phase} inventory observation"
            )
        reservation_identity = _reservation_identity(paths, reservation)
        receipt = {
            "schema_version": 1,
            "record_type": "live_inventory_observation",
            "phase": phase,
            "operation_id": operation,
            "generation_id": state["generation_id"],
            "terminal_state": terminal_state,
            "observed_at": _utc_now(),
            "installed_root": state["install_root"],
            "source": {
                "commit": state["source_commit"],
                "tree": state["source_tree"],
                "manifest_path": state["manifest_path"],
                "manifest_sha256": state["manifest_sha256"],
            },
            "expected_live_source": {
                "commit": state["expected_live_source_commit"],
                "tree": state["expected_live_source_tree"],
                "manifest_sha256": state["expected_live_manifest_sha256"],
            },
            "live_inventory": _path_byte_identity(live, inventory_path, live_paths),
            "terminal_receipt": terminal_receipt_identity,
            "reservation": reservation_identity,
            "finalization_manifest_prefix": manifest_prefix,
            "mutation_outcome": state["mutation_outcome"],
        }
        encoded = (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode("utf-8")
        _write_new_file(inventory_path, live.data)
        persisted_inventory = _read_regular_bytes(
            inventory_path, label=f"{phase} live inventory sidecar"
        )
        if (
            persisted_inventory != live.data
            or serialize_inventory(parse_inventory(persisted_inventory)) != live.data
        ):
            raise PublicationError(
                f"{phase} live inventory sidecar changed before receipt commit"
            )
        _write_new_file(output, encoded)
        _fsync_directory(output.parent)
        observed = _read_regular_bytes(output, label=f"{phase} live inventory receipt")
        if observed != encoded:
            raise PublicationError("live inventory receipt did not round-trip")
        live_after_receipt = build_inventory(
            Path(str(state["install_root"])), live_paths
        )
        sidecar_after_receipt = _read_regular_bytes(
            inventory_path, label=f"{phase} live inventory sidecar"
        )
        if (
            live_after_receipt.data != live.data
            or sidecar_after_receipt != live.data
            or _finalization_manifest_prefix(
                manifest_path, required_prefix=terminal_prefix
            )
            != manifest_prefix
            or _reservation_identity(paths, reservation) != reservation_identity
            or _terminal_receipt(state, operation=operation)[0]
            != terminal_receipt_identity
        ):
            raise PublicationError(
                f"{phase} evidence identity changed before report journal commit"
            )
        report_identity = {
            "phase": phase,
            "path": str(output),
            "sha256": hashlib.sha256(observed).hexdigest(),
            "inventory_sha256": live.digest,
            "manifest_prefix_sha256": manifest_prefix["prefix_sha256"],
        }
        reports = state.setdefault("live_inventory_reports", [])
        if not isinstance(reports, list):
            raise PublicationError("operation live inventory report journal is malformed")
        reports.append(report_identity)
        _record_event(
            state,
            "live_inventory_observed_under_reservation",
            phase=phase,
            receipt_sha256=report_identity["sha256"],
        )
        _persist_state(paths["state"], state, "FINALIZED_RESERVED")
        return receipt


def _load_recorded_live_inventory_report(
    *,
    state: Mapping[str, Any],
    reservation_identity: Mapping[str, Any],
    terminal_receipt_identity: Mapping[str, Any],
    receipt_path: Path,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    receipt_path = _safe_absolute(
        receipt_path, label="acceptance live inventory receipt", must_exist=True
    )
    evidence_root = _safe_absolute(
        Path(str(state["evidence_root"])), label="operation evidence root", must_exist=True
    )
    if not _is_within(receipt_path, evidence_root):
        raise PublicationError("acceptance live inventory receipt is outside the evidence root")
    raw = _read_regular_bytes(receipt_path, label="acceptance live inventory receipt")
    digest = hashlib.sha256(raw).hexdigest()
    reports = state.get("live_inventory_reports")
    if not isinstance(reports, list) or not any(
        isinstance(report, dict)
        and report.get("phase") == "acceptance"
        and report.get("path") == str(receipt_path)
        and report.get("sha256") == digest
        for report in reports
    ):
        raise PublicationError("acceptance inventory receipt is not in the operation journal")
    try:
        receipt = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PublicationError(f"acceptance inventory receipt is invalid: {exc}") from exc
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema_version") != 1
        or receipt.get("record_type") != "live_inventory_observation"
        or receipt.get("phase") != "acceptance"
        or receipt.get("operation_id") != state.get("operation_id")
        or receipt.get("generation_id") != state.get("generation_id")
        or receipt.get("terminal_state") != state.get("terminal_state")
        or receipt.get("reservation") != reservation_identity
        or receipt.get("terminal_receipt") != terminal_receipt_identity
    ):
        raise PublicationError("acceptance inventory receipt is not bound to this publication")
    return receipt, {"path": str(receipt_path), "sha256": digest}


def _verify_acceptance_live_identity(
    *,
    state: Mapping[str, Any],
    inventory_receipt: Mapping[str, Any],
    inventory_receipt_identity: Mapping[str, Any],
    acceptance_inventory_receipt: Path,
) -> Inventory:
    raw_receipt = _read_regular_bytes(
        acceptance_inventory_receipt, label="acceptance live inventory receipt"
    )
    if hashlib.sha256(raw_receipt).hexdigest() != inventory_receipt_identity.get(
        "sha256"
    ):
        raise PublicationError("acceptance live inventory receipt drifted")
    terminal_state, live_paths, expected_inventory = _terminal_live_identity(state)
    if terminal_state != inventory_receipt.get("terminal_state"):
        raise PublicationError("acceptance receipt terminal state drifted")
    live = build_inventory(Path(str(state["install_root"])), live_paths)
    recorded_live = inventory_receipt.get("live_inventory")
    expected_sidecar = acceptance_inventory_receipt.with_name(
        acceptance_inventory_receipt.name + ".inventory"
    )
    if not isinstance(recorded_live, dict) or recorded_live.get("path") != str(
        expected_sidecar
    ):
        raise PublicationError("acceptance inventory sidecar path is malformed")
    persisted_inventory = _read_regular_bytes(
        expected_sidecar, label="acceptance live inventory sidecar"
    )
    if (
        live.digest != expected_inventory["sha256"]
        or recorded_live != _path_byte_identity(live, expected_sidecar, live_paths)
        or persisted_inventory != live.data
        or serialize_inventory(parse_inventory(persisted_inventory)) != live.data
    ):
        raise PublicationError("live generation drifted from the acceptance inventory")
    return live


def _write_once_or_verify_json(path: Path, value: Mapping[str, Any], *, label: str) -> None:
    encoded = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if path.exists() or path.is_symlink():
        if _read_regular_bytes(path, label=label) != encoded:
            raise PublicationError(f"existing {label} conflicts with this acceptance")
        return
    _write_new_file(path, encoded)
    _fsync_directory(path.parent)


def _load_bound_release_record(
    *,
    paths: Mapping[str, Path],
    state: Mapping[str, Any],
    operation: str,
    acceptance_inventory_receipt: Path,
    accepted_by: str,
    acceptance_reason: str,
    finalization_manifest: Path,
) -> Dict[str, Any]:
    raw = _read_regular_bytes(paths["released"], label="package release record")
    try:
        release = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PublicationError(f"package release record is invalid: {exc}") from exc
    if not isinstance(release, dict):
        raise PublicationError("package release record is not an object")
    recorded_identity = state.get("release_record")
    acceptance_identity = release.get("acceptance_inventory_receipt")
    authorization = release.get("acceptance_authorization")
    manifest_prefix = release.get("finalization_manifest")
    receipt_path = _safe_absolute(
        acceptance_inventory_receipt,
        label="acceptance live inventory receipt",
        must_exist=True,
    )
    receipt_digest = hashlib.sha256(
        _read_regular_bytes(receipt_path, label="acceptance live inventory receipt")
    ).hexdigest()
    if (
        release.get("schema_version") != 2
        or release.get("record_type") != "package_reservation_release"
        or release.get("operation_id") != operation
        or release.get("generation_id") != state.get("generation_id")
        or not isinstance(recorded_identity, dict)
        or recorded_identity.get("path") != str(paths["released"])
        or recorded_identity.get("sha256") != hashlib.sha256(raw).hexdigest()
        or acceptance_identity
        != {"path": str(receipt_path), "sha256": receipt_digest}
        or not isinstance(authorization, dict)
        or authorization.get("accepted_by") != accepted_by
        or authorization.get("acceptance_reason") != acceptance_reason
        or authorization.get("acceptance_inventory_receipt") != acceptance_identity
        or not isinstance(manifest_prefix, dict)
        or manifest_prefix.get("path") != str(finalization_manifest)
    ):
        raise PublicationError(
            "package release record is not bound to this acceptance invocation"
        )
    return release


def _release_after_verified_acceptance(
    *,
    paths: Mapping[str, Path],
    state: Dict[str, Any],
    state_root: Path,
    operation: str,
    release_record: Mapping[str, Any],
) -> None:
    """Release the lock only across immediate pre/post exact-identity checks."""
    reservation = _load_reservation(paths, operation, state)
    reservation_raw = _read_regular_bytes(
        paths["reservation"], label="reservation immediately before release"
    )
    reservation_identity = _reservation_identity(paths, reservation)
    if release_record.get("reservation") != reservation_identity:
        raise PublicationError("release record reservation identity drifted")
    inventory_identity = release_record.get("acceptance_inventory_receipt")
    if not isinstance(inventory_identity, dict) or not isinstance(
        inventory_identity.get("path"), str
    ):
        raise PublicationError("release record acceptance inventory identity is malformed")
    inventory_path = Path(inventory_identity["path"])
    inventory_raw = _read_regular_bytes(
        inventory_path, label="acceptance live inventory receipt"
    )
    if hashlib.sha256(inventory_raw).hexdigest() != inventory_identity.get("sha256"):
        raise PublicationError("release acceptance inventory receipt drifted")
    try:
        inventory_receipt = json.loads(inventory_raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PublicationError(f"release acceptance inventory receipt is invalid: {exc}") from exc
    if not isinstance(inventory_receipt, dict):
        raise PublicationError("release acceptance inventory receipt is not an object")
    acceptance_prefix = release_record.get("finalization_manifest")
    if not isinstance(acceptance_prefix, dict) or not isinstance(
        acceptance_prefix.get("path"), str
    ):
        raise PublicationError("release acceptance manifest prefix is malformed")
    manifest_path = Path(acceptance_prefix["path"])
    _finalization_manifest_prefix(
        manifest_path, required_prefix=acceptance_prefix
    )
    _verify_acceptance_live_identity(
        state=state,
        inventory_receipt=inventory_receipt,
        inventory_receipt_identity=inventory_identity,
        acceptance_inventory_receipt=inventory_path,
    )
    try:
        os.unlink(paths["reservation"])
    except Exception as exc:
        if not paths["reservation"].exists() and not paths["reservation"].is_symlink():
            try:
                _write_new_file(paths["reservation"], reservation_raw)
                _fsync_directory(state_root)
                _record_event(
                    state,
                    "ambiguous_release_unlink_reservation_restored",
                    error=str(exc),
                )
                _persist_state(paths["state"], state, "ACCEPTED_RELEASE_PENDING")
            except Exception as restore_exc:
                _record_event(
                    state,
                    "ambiguous_release_unlink_restore_failed",
                    error=str(exc),
                    restore_error=str(restore_exc),
                )
                _persist_state(paths["state"], state, "UNCHECKED")
                raise PublicationError(
                    "reservation unlink was ambiguous and restoration failed"
                ) from restore_exc
            raise PublicationError(
                "reservation unlink was ambiguous; the exact reservation was restored"
            ) from exc
        raise
    _fsync_directory(state_root)
    try:
        _finalization_manifest_prefix(
            manifest_path, required_prefix=acceptance_prefix
        )
        _verify_acceptance_live_identity(
            state=state,
            inventory_receipt=inventory_receipt,
            inventory_receipt_identity=inventory_identity,
            acceptance_inventory_receipt=inventory_path,
        )
    except Exception as exc:
        try:
            _write_new_file(paths["reservation"], reservation_raw)
            _fsync_directory(state_root)
            _record_event(
                state,
                "post_release_identity_drift_reservation_restored",
                error=str(exc),
            )
            _persist_state(paths["state"], state, "ACCEPTED_RELEASE_PENDING")
        except Exception as restore_exc:
            _record_event(
                state,
                "post_release_identity_drift_reservation_restore_failed",
                error=str(exc),
                restore_error=str(restore_exc),
            )
            _persist_state(paths["state"], state, "UNCHECKED")
            raise PublicationError(
                "post-release identity drifted and reservation restoration failed"
            ) from restore_exc
        raise PublicationError(
            "post-release identity drifted; the exact reservation was restored"
        ) from exc


def _verify_completed_release_with_missing_reservation(
    *,
    paths: Mapping[str, Path],
    state: Mapping[str, Any],
    release_record: Mapping[str, Any],
) -> None:
    """Classify the post-unlink crash boundary from already-durable evidence."""
    if paths["reservation"].exists() or paths["reservation"].is_symlink():
        raise PublicationError("missing-reservation recovery found a package lock")
    reservation = state.get("reservation")
    if not isinstance(reservation, dict):
        raise PublicationError("operation state lacks its original reservation")
    reservation_raw = (json.dumps(reservation, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )
    expected_reservation_identity = {
        "path": str(paths["reservation"]),
        "sha256": hashlib.sha256(reservation_raw).hexdigest(),
        "operation_id": reservation.get("operation_id"),
        "generation_id": reservation.get("generation_id"),
        "owner": reservation.get("owner"),
        "maintenance": reservation.get("maintenance"),
    }
    if release_record.get("reservation") != expected_reservation_identity:
        raise PublicationError(
            "durable release record does not bind the original missing reservation"
        )
    inventory_identity = release_record.get("acceptance_inventory_receipt")
    if not isinstance(inventory_identity, dict) or not isinstance(
        inventory_identity.get("path"), str
    ):
        raise PublicationError("durable release inventory identity is malformed")
    inventory_path = Path(inventory_identity["path"])
    inventory_raw = _read_regular_bytes(
        inventory_path, label="acceptance live inventory receipt"
    )
    if hashlib.sha256(inventory_raw).hexdigest() != inventory_identity.get("sha256"):
        raise PublicationError("durable release inventory receipt drifted")
    try:
        inventory_receipt = json.loads(inventory_raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PublicationError(f"durable release inventory receipt is invalid: {exc}") from exc
    if not isinstance(inventory_receipt, dict):
        raise PublicationError("durable release inventory receipt is not an object")
    acceptance_prefix = release_record.get("finalization_manifest")
    if not isinstance(acceptance_prefix, dict) or not isinstance(
        acceptance_prefix.get("path"), str
    ):
        raise PublicationError("durable release manifest prefix is malformed")
    _finalization_manifest_prefix(
        Path(acceptance_prefix["path"]), required_prefix=acceptance_prefix
    )
    _verify_acceptance_live_identity(
        state=state,
        inventory_receipt=inventory_receipt,
        inventory_receipt_identity=inventory_identity,
        acceptance_inventory_receipt=inventory_path,
    )


def accept_operation(
    *,
    state_root: Path,
    operation: str,
    acceptance_inventory_receipt: Path,
    accepted_by: str,
    acceptance_reason: str,
    lock_path: Optional[Path] = None,
    finalization_manifest: Path,
) -> Dict[str, Any]:
    """Record panel acceptance, then release exactly this package reservation."""
    accepted_by = _validated_record_text(accepted_by, label="accepted-by")
    acceptance_reason = _validated_record_text(
        acceptance_reason, label="acceptance-reason"
    )
    state_root = _safe_absolute(state_root, label="state root", must_exist=True)
    paths, state = _load_state(state_root, operation)
    _validate_package_lock_argument(state_root, operation, lock_path)
    finalization_manifest = _validate_finalization_manifest_path(
        finalization_manifest, state_root=state_root, state=state
    )
    with WriterLock(paths["writer_lock"]):
        paths, state = _load_state(state_root, operation)
        status = state.get("status")
        if status in {"ACCEPTED", "ACCEPTED_RELEASE_PENDING"}:
            release_record = _load_bound_release_record(
                paths=paths,
                state=state,
                operation=operation,
                acceptance_inventory_receipt=acceptance_inventory_receipt,
                accepted_by=accepted_by,
                acceptance_reason=acceptance_reason,
                finalization_manifest=finalization_manifest,
            )
            if status == "ACCEPTED":
                return release_record
            if not paths["reservation"].exists() and not paths["reservation"].is_symlink():
                _verify_completed_release_with_missing_reservation(
                    paths=paths,
                    state=state,
                    release_record=release_record,
                )
                _record_event(
                    state,
                    "post_unlink_crash_classified_from_durable_acceptance_evidence",
                )
            else:
                _release_after_verified_acceptance(
                    paths=paths,
                    state=state,
                    state_root=state_root,
                    operation=operation,
                    release_record=release_record,
                )
            _record_event(state, "panel_acceptance_release_completed")
            _persist_state(paths["state"], state, "ACCEPTED")
            return release_record
        if status not in {"FINALIZED_RESERVED", "ACCEPTANCE_PENDING"}:
            raise PublicationError(
                f"cannot accept publication from operation state: {status}"
            )
        reservation = _load_reservation(paths, operation, state)
        _verify_recorded_takeover_authorization(state, reservation)
        _verify_prepare_evidence(state)
        reserved_manifest = _reserved_finalization_manifest(
            reservation, state_root=state_root, state=state
        )
        if reserved_manifest != finalization_manifest:
            raise PublicationError(
                "acceptance finalization manifest differs from the active reservation"
            )
        terminal_receipt_identity, terminal_receipt_value = _terminal_receipt(
            state, operation=operation
        )
        reservation_identity = _reservation_identity(paths, reservation)
        inventory_receipt, inventory_receipt_identity = (
            _load_recorded_live_inventory_report(
                state=state,
                reservation_identity=reservation_identity,
                terminal_receipt_identity=terminal_receipt_identity,
                receipt_path=acceptance_inventory_receipt,
            )
        )
        observed_prefix = inventory_receipt.get("finalization_manifest_prefix")
        if not isinstance(observed_prefix, dict):
            raise PublicationError(
                "acceptance inventory receipt lacks a bounded finalization-manifest prefix"
            )
        terminal_state, _, _ = _terminal_live_identity(state)
        _verify_acceptance_live_identity(
            state=state,
            inventory_receipt=inventory_receipt,
            inventory_receipt_identity=inventory_receipt_identity,
            acceptance_inventory_receipt=acceptance_inventory_receipt,
        )
        terminal_prefix = state.get("finalization_manifest_terminal")
        if not isinstance(terminal_prefix, dict):
            raise PublicationError(
                "acceptance state lacks the durable terminal-manifest prefix"
            )
        observed_last_sequence = observed_prefix.get("last_sequence")
        if (
            not isinstance(observed_last_sequence, int)
            or isinstance(observed_last_sequence, bool)
            or _finalization_manifest_prefix(
                finalization_manifest,
                through_sequence=observed_last_sequence,
                required_prefix=terminal_prefix,
            )
            != observed_prefix
        ):
            raise PublicationError(
                "finalization manifest changed after the bounded prefix was observed"
            )
        if (
            status == "FINALIZED_RESERVED"
            and _finalization_manifest_prefix(
                finalization_manifest, required_prefix=terminal_prefix
            )
            != observed_prefix
        ):
            raise PublicationError(
                "finalization manifest changed after the acceptance observation"
            )
        authorization = state.get("acceptance_authorization")
        if status == "FINALIZED_RESERVED":
            authorization = {
                "accepted_by": accepted_by,
                "acceptance_reason": acceptance_reason,
                "accepted_at": _utc_now(),
                "acceptance_inventory_receipt": inventory_receipt_identity,
            }
            state["acceptance_authorization"] = authorization
            _record_event(state, "panel_acceptance_authorized")
            _persist_state(paths["state"], state, "ACCEPTANCE_PENDING")
        elif (
            not isinstance(authorization, dict)
            or authorization.get("accepted_by") != accepted_by
            or authorization.get("acceptance_reason") != acceptance_reason
            or authorization.get("acceptance_inventory_receipt")
            != inventory_receipt_identity
        ):
            raise PublicationError(
                "acceptance retry differs from the durable pending authorization"
            )
        assert isinstance(authorization, dict)
        acceptance_manifest = _append_finalization_record(
            finalization_manifest,
            record_type="installed_publication_accepted",
            required_ancestor_prefix=observed_prefix,
            predecessor_payload_field="acceptance_manifest_predecessor_prefix",
            payload={
                "operation_id": operation,
                "generation_id": state["generation_id"],
                "installed_root": state["install_root"],
                "lock_path": str(paths["reservation"]),
                "reservation_state": "PANEL_ACCEPTANCE_RECORDED",
                "terminal_state": terminal_state,
                "accepted_by": authorization["accepted_by"],
                "acceptance_reason": authorization["acceptance_reason"],
                "accepted_at": authorization["accepted_at"],
                "terminal_receipt": terminal_receipt_identity,
                "acceptance_inventory_receipt": inventory_receipt_identity,
                "acceptance_inventory": inventory_receipt,
            },
        )
        acceptance_prefix = _finalization_manifest_prefix(
            finalization_manifest,
            through_sequence=acceptance_manifest["record"]["sequence"],
            required_prefix=observed_prefix,
        )
        if (
            acceptance_prefix["prefix_sha256"]
            != acceptance_manifest["manifest_sha256"]
            or acceptance_prefix["prefix_bytes"]
            != acceptance_manifest["manifest_prefix_bytes"]
        ):
            raise PublicationError(
                "acceptance manifest prefix differs from the appended record identity"
            )
        _verify_acceptance_live_identity(
            state=state,
            inventory_receipt=inventory_receipt,
            inventory_receipt_identity=inventory_receipt_identity,
            acceptance_inventory_receipt=acceptance_inventory_receipt,
        )
        if _reservation_identity(paths, reservation) != reservation_identity:
            raise PublicationError("package reservation drifted during acceptance")
        release_record = {
            "schema_version": 2,
            "record_type": "package_reservation_release",
            "operation_id": operation,
            "generation_id": state["generation_id"],
            "released_at": authorization["accepted_at"],
            "terminal_state": terminal_state,
            "reservation_state": "RELEASED_AFTER_PANEL_ACCEPTANCE",
            "terminal_receipt": terminal_receipt_identity,
            "terminal_receipt_value": terminal_receipt_value,
            "acceptance_inventory_receipt": inventory_receipt_identity,
            "acceptance_authorization": authorization,
            "reservation": reservation_identity,
            "finalization_manifest": acceptance_prefix,
        }
        _mkdir_secure(paths["released"].parent, exist_ok=True)
        _write_once_or_verify_json(
            paths["released"], release_record, label="package release record"
        )
        _verify_acceptance_live_identity(
            state=state,
            inventory_receipt=inventory_receipt,
            inventory_receipt_identity=inventory_receipt_identity,
            acceptance_inventory_receipt=acceptance_inventory_receipt,
        )
        _finalization_manifest_prefix(
            finalization_manifest, required_prefix=acceptance_prefix
        )
        if _reservation_identity(paths, reservation) != reservation_identity:
            raise PublicationError(
                "package reservation drifted after release-record write"
            )
        state["release_record"] = {
            "path": str(paths["released"]),
            "sha256": hashlib.sha256(
                _read_regular_bytes(paths["released"], label="package release record")
            ).hexdigest(),
        }
        state["finalization_manifest_acceptance"] = acceptance_prefix
        _record_event(state, "panel_acceptance_recorded_release_pending")
        _persist_state(paths["state"], state, "ACCEPTED_RELEASE_PENDING")
        _release_after_verified_acceptance(
            paths=paths,
            state=state,
            state_root=state_root,
            operation=operation,
            release_record=release_record,
        )
        _record_event(state, "panel_acceptance_release_completed")
        _persist_state(paths["state"], state, "ACCEPTED")
        return release_record


def _fake_checker(_: Path, installed_root: Path) -> Dict[str, Any]:
    inventory = build_inventory(installed_root)
    return {
        "argv": ["TEST-ONLY", "--installed-root", str(installed_root), "--self-test", "--json"],
        "checker_sha256": "0" * 64,
        "stdout": "OK: deterministic fake checker\n",
        "stderr": "",
        "exit_status": 0,
        "observed_inventory_sha256": inventory.digest,
        "result": {
            "status": "PASS",
            "named_mutation_outcomes": {"TEST_ONLY_INVENTORY_OBSERVED": "PASS"},
        },
        "named_mutation_outcomes": {"TEST_ONLY_INVENTORY_OBSERVED": "PASS"},
    }


def _self_test() -> None:
    """Run deterministic fake-exchange controls without touching a live install."""
    with tempfile.TemporaryDirectory(prefix="publish-codex-self-test-") as raw_temp:
        root = Path(raw_temp).resolve()
        left = root / "left"
        right = root / "right"
        _mkdir_secure(left)
        _mkdir_secure(right)
        _write_new_file(left / "a", b"new")
        _write_new_file(right / "a", b"old")
        old_digest = build_inventory(right).digest
        new_digest = build_inventory(left).digest
        exchanger = FakeAtomicExchanger()
        exchanger.exchange(left, right)
        if build_inventory(right).digest != new_digest or build_inventory(left).digest != old_digest:
            raise PublicationError("fake exchange self-test did not exchange complete trees")
        mutated = right / "a"
        mutated.write_bytes(b"new!")
        if build_inventory(right).digest == new_digest:
            raise PublicationError("one-byte drift negative control did not change identity")
        unmanaged = right / "unmanaged"
        unmanaged.write_bytes(b"x")
        try:
            build_inventory(right, {"a"})
        except PublicationError:
            pass
        else:
            raise PublicationError("unmanaged-file negative control passed")
        unmanaged.unlink()
        symlink = right / "link"
        os.symlink("a", symlink)
        try:
            build_inventory(right)
        except PublicationError:
            pass
        else:
            raise PublicationError("symlink negative control passed")
        symlink.unlink()
        unavailable = FakeAtomicExchanger(available=False)
        try:
            unavailable.exchange(left, right)
        except PublicationError:
            pass
        else:
            raise PublicationError("unavailable-exchange negative control passed")


def _add_common_operation(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-root", required=True, type=Path)
    parser.add_argument("--operation", required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare", help="materialize and validate a candidate")
    _add_common_operation(prepare)
    prepare.add_argument("--source-repository", required=True, type=Path)
    prepare.add_argument("--source-commit", required=True)
    prepare.add_argument(
        "--expected-live-source-commit",
        required=True,
        help="exact predecessor commit whose install mapping and bytes must match live",
    )
    prepare.add_argument("--manifest", required=True)
    prepare.add_argument("--install-root", required=True, type=Path)
    prepare.add_argument("--evidence-root", required=True, type=Path)
    prepare.add_argument("--receipt", required=True, type=Path)

    reserve = subparsers.add_parser("reserve", help="acquire the durable package reservation")
    _add_common_operation(reserve)
    reserve.add_argument("--lock", required=True, type=Path)
    reserve.add_argument("--prepare-receipt", required=True, type=Path)
    reserve.add_argument(
        "--reader-quiescence-record",
        "--maintenance-receipt",
        dest="maintenance_receipt",
        required=True,
        type=Path,
    )
    reserve.add_argument("--finalization-manifest", required=True, type=Path)

    publish = subparsers.add_parser("publish", help="perform the required Darwin atomic exchange")
    _add_common_operation(publish)
    publish.add_argument("--lock", required=True, type=Path)
    publish.add_argument(
        "--require-atomic-exchange",
        choices=("darwin-rename-swap",),
        required=True,
    )

    recover = subparsers.add_parser("recover", help="inspect or resolve a complete-tree crash state")
    _add_common_operation(recover)
    recover.add_argument("--lock", type=Path)
    recover.add_argument("--action", choices=("inspect", "complete", "rollback"), required=True)
    recover.add_argument(
        "--require-atomic-exchange", choices=("darwin-rename-swap",)
    )
    recover.add_argument(
        "--takeover-authorization",
        type=Path,
        help=(
            "schema-1 JSON bound to operation/generation/prior_owner; mutation requires "
            "STOPPED or SUPERSEDED plus INACTIVE process and tool-session inspection"
        ),
    )

    finalize = subparsers.add_parser(
        "finalize",
        help="write terminal validation receipts and retain the reservation",
    )
    _add_common_operation(finalize)
    finalize.add_argument("--lock", required=True, type=Path)
    finalize.add_argument("--finalization-manifest", required=True, type=Path)
    finalize.add_argument("--receipt-output", type=Path)

    inventory = subparsers.add_parser(
        "inventory",
        help="report the exact live inventory under the retained reservation",
    )
    _add_common_operation(inventory)
    inventory.add_argument("--lock", required=True, type=Path)
    inventory.add_argument("--phase", choices=sorted(LIVE_INVENTORY_PHASES), required=True)
    inventory.add_argument("--output", required=True, type=Path)

    accept = subparsers.add_parser(
        "accept",
        help="record panel acceptance and release the retained reservation",
    )
    _add_common_operation(accept)
    accept.add_argument("--lock", required=True, type=Path)
    accept.add_argument("--finalization-manifest", required=True, type=Path)
    accept.add_argument(
        "--acceptance-inventory-receipt", required=True, type=Path
    )
    accept.add_argument("--accepted-by", required=True)
    accept.add_argument("--acceptance-reason", required=True)

    subparsers.add_parser("self-test", help="run fake-exchange negative controls only")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_operation(
                source_repository=args.source_repository,
                source_commit=args.source_commit,
                expected_live_source_commit=args.expected_live_source_commit,
                manifest_path=args.manifest,
                install_root=args.install_root,
                state_root=args.state_root,
                evidence_root=args.evidence_root,
                operation=args.operation,
                receipt_output=args.receipt,
            )
        elif args.command == "reserve":
            result = reserve_operation(
                state_root=args.state_root,
                operation=args.operation,
                maintenance_receipt=args.maintenance_receipt,
                lock_path=args.lock,
                prepare_receipt=args.prepare_receipt,
                finalization_manifest=args.finalization_manifest,
            )
        elif args.command == "publish":
            result = publish_operation(
                state_root=args.state_root,
                operation=args.operation,
                exchanger=DarwinAtomicExchanger(),
                lock_path=args.lock,
            )
        elif args.command == "recover":
            if args.action != "inspect" and not args.require_atomic_exchange:
                raise PublicationError(
                    "mutating recovery requires explicit --require-atomic-exchange"
                )
            result = recover_operation(
                state_root=args.state_root,
                operation=args.operation,
                action=args.action,
                exchanger=DarwinAtomicExchanger(),
                lock_path=args.lock,
                takeover_authorization=args.takeover_authorization,
            )
        elif args.command == "finalize":
            result = finalize_operation(
                state_root=args.state_root,
                operation=args.operation,
                receipt_output=args.receipt_output,
                lock_path=args.lock,
                finalization_manifest=args.finalization_manifest,
                exchanger=DarwinAtomicExchanger(),
            )
        elif args.command == "inventory":
            result = report_live_inventory(
                state_root=args.state_root,
                operation=args.operation,
                phase=args.phase,
                output=args.output,
                lock_path=args.lock,
            )
        elif args.command == "accept":
            result = accept_operation(
                state_root=args.state_root,
                operation=args.operation,
                acceptance_inventory_receipt=args.acceptance_inventory_receipt,
                accepted_by=args.accepted_by,
                acceptance_reason=args.acceptance_reason,
                lock_path=args.lock,
                finalization_manifest=args.finalization_manifest,
            )
        elif args.command == "self-test":
            _self_test()
            result = {"status": "OK", "exchange": "TEST-ONLY-fake-exchange"}
        else:
            raise PublicationError(f"unknown command: {args.command}")
    except ValidationFailure as exc:
        print(f"ROLLED_BACK: {exc}", file=sys.stderr)
        return 3
    except (PublicationError, InjectedFailure, OSError) as exc:
        print(f"UNCHECKED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
