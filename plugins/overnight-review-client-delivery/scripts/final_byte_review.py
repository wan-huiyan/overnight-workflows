#!/usr/bin/env python3
"""Executable frozen-final-byte gate for client-delivery workflows.

The helper is deliberately local-only.  It inventories deliverable files,
records an independent final report against that exact inventory, and refuses
Phase C after any later artifact or report byte changes.  It never commits,
pushes, opens a pull request, calls a network service, or performs another
external action.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Dict, Iterator, Mapping, Optional, Sequence
import uuid


SCHEMA_VERSION = 2
STATE_TYPE = "client_delivery_final_byte_gate"
REPORT_TYPE = "frozen_final_byte_review"
INVENTORY_FORMAT = "sha256-size-independent-file-and-directory-stat-v6"
PENDING = "FINAL_REVIEW_PENDING"
APPROVED = "FINAL_REVIEW_APPROVED"
INVALIDATED = "FINAL_REVIEW_INVALIDATED"
IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/+\-]{0,255}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
UTC = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z"
)
STATE_FIELDS = {
    "schema_version",
    "record_type",
    "review_id",
    "cycle",
    "freeze_id",
    "status",
    "contributors",
    "frozen_inventory",
    "final_report",
    "invalidation",
    "prior_invalidations",
}
INVENTORY_FIELDS = {
    "format",
    "sha256",
    "file_count",
    "total_bytes",
    "artifact_root",
    "snapshot_root",
    "entries",
    "directories",
}
ENTRY_FIELDS = {
    "path",
    "relative_path",
    "snapshot_path",
    "sha256",
    "bytes",
    "device",
    "inode",
    "mode",
    "mtime_ns",
    "ctime_ns",
    "snapshot_device",
    "snapshot_inode",
    "snapshot_mode",
    "snapshot_mtime_ns",
    "snapshot_ctime_ns",
}
DIRECTORY_FIELDS = {
    "path",
    "relative_path",
    "snapshot_path",
    "device",
    "inode",
    "mode",
    "mtime_ns",
    "ctime_ns",
    "snapshot_device",
    "snapshot_inode",
    "snapshot_mode",
    "snapshot_mtime_ns",
    "snapshot_ctime_ns",
}
REPORT_FIELDS = {
    "schema_version",
    "record_type",
    "review_id",
    "cycle",
    "freeze_id",
    "reviewer_id",
    "frozen_inventory_sha256",
    "verdict",
    "reviewed_at",
    "summary",
    "findings",
}
REPORT_IDENTITY_FIELDS = {"path", "sha256", "bytes", "report"}
INVALIDATION_FIELDS = {
    "cycle",
    "freeze_id",
    "invalidated_at",
    "reason",
    "frozen_inventory_sha256",
}


class GateError(RuntimeError):
    """The final-byte gate is incomplete, unsafe, or invalidated."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _valid_utc(value: Any) -> bool:
    if not isinstance(value, str) or not UTC.fullmatch(value):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0


def _identity(value: Any, label: str) -> str:
    if not isinstance(value, str) or not IDENTITY.fullmatch(value):
        raise GateError(f"{label} must be a normalized identity")
    return value


def _absolute(path: Path, *, label: str, must_exist: bool = False) -> Path:
    path = Path(path)
    rendered = str(path)
    if (
        not path.is_absolute()
        or os.path.normpath(rendered) != rendered
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in rendered)
    ):
        raise GateError(f"{label} must be a normalized absolute path: {path}")
    current = Path(path.anchor)
    missing = False
    for component in path.parts[1:]:
        if component in {"", ".", ".."}:
            raise GateError(f"{label} has an unsafe component: {path}")
        current = current / component
        try:
            observed = os.lstat(current)
        except FileNotFoundError:
            missing = True
            continue
        if missing:
            raise GateError(f"{label} has an existing child below a missing parent: {path}")
        if stat.S_ISLNK(observed.st_mode):
            raise GateError(f"{label} has a symlink component: {current}")
        if current != path and not stat.S_ISDIR(observed.st_mode):
            raise GateError(f"{label} parent is not a directory: {current}")
    if must_exist and not path.exists():
        raise GateError(f"{label} does not exist: {path}")
    return path


def _regular_bytes_and_stat(path: Path, *, label: str) -> tuple[bytes, os.stat_result]:
    path = _absolute(path, label=label, must_exist=True)
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise GateError(f"{label} must be a regular non-symlink single-link file")
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (before.st_dev, before.st_ino, before.st_size)
            != (opened.st_dev, opened.st_ino, opened.st_size)
        ):
            raise GateError(f"{label} changed before it was opened")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        data = b"".join(chunks)
        if (
            after.st_nlink != 1
            or (opened.st_dev, opened.st_ino, opened.st_size)
            != (after.st_dev, after.st_ino, after.st_size)
            or len(data) != after.st_size
        ):
            raise GateError(f"{label} changed while it was read")
        return data, after
    finally:
        os.close(descriptor)


def _regular_bytes(path: Path, *, label: str) -> bytes:
    return _regular_bytes_and_stat(path, label=label)[0]


def _directory_identity(path: Path, relative_path: str) -> Dict[str, Any]:
    observed = os.lstat(path)
    if stat.S_ISLNK(observed.st_mode) or not stat.S_ISDIR(observed.st_mode):
        raise GateError(f"deliverable package contains an unsafe directory: {path}")
    return {
        "path": str(path),
        "relative_path": relative_path,
        "device": observed.st_dev,
        "inode": observed.st_ino,
        "mode": stat.S_IMODE(observed.st_mode),
        "mtime_ns": observed.st_mtime_ns,
        "ctime_ns": observed.st_ctime_ns,
    }


def _discover_package_closure(root: Path) -> tuple[list[Path], list[Dict[str, Any]]]:
    root = _absolute(root, label="deliverable package root", must_exist=True)
    root_observed = os.lstat(root)
    if stat.S_ISLNK(root_observed.st_mode) or not stat.S_ISDIR(root_observed.st_mode):
        raise GateError("deliverable package root must be a directory")
    discovered: list[Path] = []
    directory_identities: list[Dict[str, Any]] = []
    for directory, directories, files in os.walk(root, topdown=True, followlinks=False):
        current = Path(directory)
        relative_current = current.relative_to(root).as_posix()
        if relative_current == ".":
            relative_current = "."
        directory_identities.append(_directory_identity(current, relative_current))
        safe_directories: list[str] = []
        for name in sorted(directories, key=lambda item: item.encode("utf-8")):
            child = current / name
            observed = os.lstat(child)
            if stat.S_ISLNK(observed.st_mode) or not stat.S_ISDIR(observed.st_mode):
                raise GateError(f"deliverable package contains an unsafe directory: {child}")
            safe_directories.append(name)
        directories[:] = safe_directories
        for name in sorted(files, key=lambda item: item.encode("utf-8")):
            child = _absolute(
                current / name, label="deliverable package member", must_exist=True
            )
            observed = os.lstat(child)
            if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
                raise GateError(
                    f"deliverable package member must be a regular single-link file: {child}"
                )
            discovered.append(child)
    if not discovered:
        raise GateError("deliverable package must contain at least one file")
    directory_identities.sort(
        key=lambda item: str(item["relative_path"]).encode("utf-8")
    )
    for identity in directory_identities:
        reproduced = _directory_identity(
            Path(str(identity["path"])), str(identity["relative_path"])
        )
        if reproduced != identity:
            raise GateError("deliverable package directories changed during enumeration")
    return (
        sorted(discovered, key=lambda item: str(item).encode("utf-8")),
        directory_identities,
    )


def _discover_package_files(root: Path) -> list[Path]:
    return _discover_package_closure(root)[0]


def _live_entries(
    paths: Sequence[Path], artifact_root: Optional[Path]
) -> tuple[Path, list[Dict[str, Any]], list[Dict[str, Any]]]:
    if not paths:
        raise GateError("freeze requires at least one deliverable")
    normalized = [_absolute(path, label="deliverable", must_exist=True) for path in paths]
    names = [str(path) for path in normalized]
    if len(names) != len(set(names)):
        raise GateError("freeze deliverables must be distinct")
    if artifact_root is None:
        raise GateError("freeze requires an authoritative artifact_root")
    normalized_root = _absolute(
        artifact_root, label="deliverable package root", must_exist=True
    )
    discovered, directories = _discover_package_closure(normalized_root)
    if normalized != discovered and sorted(normalized, key=lambda item: str(item).encode("utf-8")) != discovered:
        supplied = {str(path) for path in normalized}
        complete = {str(path) for path in discovered}
        missing = sorted(complete - supplied)
        extra = sorted(supplied - complete)
        raise GateError(
            "freeze artifact list must equal the complete package closure; "
            f"missing={missing}, extra={extra}"
        )
    entries: list[Dict[str, Any]] = []
    for path in sorted(normalized, key=lambda item: str(item).encode("utf-8")):
        data, observed = _regular_bytes_and_stat(path, label=f"deliverable {path}")
        digest = hashlib.sha256(data).hexdigest()
        try:
            relative = path.relative_to(normalized_root).as_posix()
        except ValueError as exc:
            raise GateError("every deliverable must be below the package root") from exc
        if not relative or relative == "." or any(
            part in {"", ".", ".."} for part in Path(relative).parts
        ):
            raise GateError("deliverable relative path is unsafe")
        entry = {
            "path": str(path),
            "relative_path": relative,
            "sha256": digest,
            "bytes": len(data),
            "device": observed.st_dev,
            "inode": observed.st_ino,
            "mode": stat.S_IMODE(observed.st_mode),
            "mtime_ns": observed.st_mtime_ns,
            "ctime_ns": observed.st_ctime_ns,
        }
        entries.append(entry)
    return normalized_root, entries, directories


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _snapshot_inventory(
    state_path: Path,
    live_entries: Sequence[Mapping[str, Any]],
    live_directories: Sequence[Mapping[str, Any]],
    *,
    artifact_root: Path,
    cycle: int,
    freeze_id: str,
) -> Dict[str, Any]:
    snapshot_root = _absolute(
        state_path.with_name(state_path.name + ".snapshots"),
        label="frozen snapshot root",
    )
    try:
        os.mkdir(snapshot_root, 0o700)
        _fsync_directory(snapshot_root.parent)
    except FileExistsError:
        observed = os.lstat(snapshot_root)
        if not stat.S_ISDIR(observed.st_mode) or stat.S_ISLNK(observed.st_mode):
            raise GateError("frozen snapshot root must be a real directory")
    snapshot_directory = _absolute(
        snapshot_root / f"cycle-{cycle}-{freeze_id}",
        label="frozen snapshot directory",
    )
    try:
        os.mkdir(snapshot_directory, 0o700)
    except FileExistsError as exc:
        raise GateError("frozen snapshot directory already exists") from exc
    _fsync_directory(snapshot_root)
    entries: list[Dict[str, Any]] = []
    framed = bytearray()
    for live_directory in sorted(
        live_directories,
        key=lambda item: (
            len(Path(str(item["relative_path"])).parts),
            str(item["relative_path"]).encode("utf-8"),
        ),
    ):
        relative = str(live_directory["relative_path"])
        if relative == ".":
            continue
        os.mkdir(snapshot_directory.joinpath(*Path(relative).parts), 0o700)
    for live in live_entries:
        source_path = Path(str(live["path"]))
        data, source_stat = _regular_bytes_and_stat(
            source_path, label=f"deliverable {source_path}"
        )
        digest = hashlib.sha256(data).hexdigest()
        stat_identity = {
            "device": source_stat.st_dev,
            "inode": source_stat.st_ino,
            "mode": stat.S_IMODE(source_stat.st_mode),
            "mtime_ns": source_stat.st_mtime_ns,
            "ctime_ns": source_stat.st_ctime_ns,
        }
        if (
            digest != live["sha256"]
            or len(data) != live["bytes"]
            or any(live[field] != value for field, value in stat_identity.items())
        ):
            raise GateError("deliverable changed while its frozen snapshot was created")
        snapshot_path = snapshot_directory.joinpath(
            *Path(str(live["relative_path"])).parts
        )
        descriptor = os.open(
            snapshot_path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise GateError("frozen snapshot write made no progress")
                view = view[written:]
            os.fsync(descriptor)
            os.fchmod(descriptor, 0o400)
            observed = os.fstat(descriptor)
            if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
                raise GateError("frozen snapshot must remain a regular single-link file")
            snapshot_identity = {
                "snapshot_device": observed.st_dev,
                "snapshot_inode": observed.st_ino,
                "snapshot_mode": stat.S_IMODE(observed.st_mode),
                "snapshot_mtime_ns": observed.st_mtime_ns,
                "snapshot_ctime_ns": observed.st_ctime_ns,
            }
        finally:
            os.close(descriptor)
        entry = {
            "path": str(source_path),
            "relative_path": live["relative_path"],
            "snapshot_path": str(snapshot_path),
            "sha256": digest,
            "bytes": len(data),
            **stat_identity,
            **snapshot_identity,
        }
        entries.append(entry)
        framed.extend(
            f"F\t{digest}\t{len(data)}\t{stat_identity['device']}\t{stat_identity['inode']}\t"
            f"{stat_identity['mode']}\t{stat_identity['mtime_ns']}\t{stat_identity['ctime_ns']}\t"
            f"{snapshot_identity['snapshot_device']}\t{snapshot_identity['snapshot_inode']}\t"
            f"{snapshot_identity['snapshot_mode']}\t{snapshot_identity['snapshot_mtime_ns']}\t"
            f"{snapshot_identity['snapshot_ctime_ns']}\t"
            f"{live['relative_path']}\t{source_path}\t{snapshot_path}\n".encode(
                "utf-8"
            )
        )
    for directory, _, _ in os.walk(snapshot_directory, topdown=False):
        current = Path(directory)
        _fsync_directory(current)
        os.chmod(current, 0o500)
    _fsync_directory(snapshot_root)
    directory_entries: list[Dict[str, Any]] = []
    for live in live_directories:
        source_path = Path(str(live["path"]))
        reproduced = _directory_identity(source_path, str(live["relative_path"]))
        if reproduced != dict(live):
            raise GateError(
                "deliverable package directory changed while its snapshot was created"
            )
        relative = str(live["relative_path"])
        snapshot_path = (
            snapshot_directory
            if relative == "."
            else snapshot_directory.joinpath(*Path(relative).parts)
        )
        snapshot_observed = os.lstat(snapshot_path)
        if stat.S_ISLNK(snapshot_observed.st_mode) or not stat.S_ISDIR(
            snapshot_observed.st_mode
        ):
            raise GateError("frozen snapshot directory closure is unsafe")
        snapshot_identity = {
            "snapshot_device": snapshot_observed.st_dev,
            "snapshot_inode": snapshot_observed.st_ino,
            "snapshot_mode": stat.S_IMODE(snapshot_observed.st_mode),
            "snapshot_mtime_ns": snapshot_observed.st_mtime_ns,
            "snapshot_ctime_ns": snapshot_observed.st_ctime_ns,
        }
        directory_entry = {
            **dict(live),
            "snapshot_path": str(snapshot_path),
            **snapshot_identity,
        }
        directory_entries.append(directory_entry)
        framed.extend(
            f"D\t{live['device']}\t{live['inode']}\t{live['mode']}\t"
            f"{live['mtime_ns']}\t{live['ctime_ns']}\t"
            f"{snapshot_identity['snapshot_device']}\t{snapshot_identity['snapshot_inode']}\t"
            f"{snapshot_identity['snapshot_mode']}\t{snapshot_identity['snapshot_mtime_ns']}\t"
            f"{snapshot_identity['snapshot_ctime_ns']}\t{relative}\t{source_path}\t"
            f"{snapshot_path}\n".encode("utf-8")
        )
    return {
        "format": INVENTORY_FORMAT,
        "sha256": hashlib.sha256(framed).hexdigest(),
        "file_count": len(entries),
        "total_bytes": sum(entry["bytes"] for entry in entries),
        "artifact_root": str(artifact_root),
        "snapshot_root": str(snapshot_directory),
        "entries": entries,
        "directories": directory_entries,
    }


def _verify_frozen_inventory(value: Mapping[str, Any]) -> None:
    _validate_inventory(value)
    artifact_root = Path(value["artifact_root"])
    discovered_files, discovered_directories = _discover_package_closure(artifact_root)
    discovered = [str(path) for path in discovered_files]
    recorded = [entry["path"] for entry in value["entries"]]
    if discovered != recorded:
        raise GateError("live deliverable package membership differs from the frozen inventory")
    recorded_directories = [
        {
            key: entry[key]
            for key in (
                "path",
                "relative_path",
                "device",
                "inode",
                "mode",
                "mtime_ns",
                "ctime_ns",
            )
        }
        for entry in value["directories"]
    ]
    if discovered_directories != recorded_directories:
        raise GateError(
            "live deliverable package directory closure differs from the frozen inventory"
        )
    for entry in value["directories"]:
        snapshot_path = Path(str(entry["snapshot_path"]))
        observed = os.lstat(snapshot_path)
        if stat.S_ISLNK(observed.st_mode) or not stat.S_ISDIR(observed.st_mode):
            raise GateError("frozen deliverable snapshot directory is unsafe")
        observed_identity = {
            "snapshot_device": observed.st_dev,
            "snapshot_inode": observed.st_ino,
            "snapshot_mode": stat.S_IMODE(observed.st_mode),
            "snapshot_mtime_ns": observed.st_mtime_ns,
            "snapshot_ctime_ns": observed.st_ctime_ns,
        }
        if observed_identity != {key: entry[key] for key in observed_identity}:
            raise GateError(
                "frozen deliverable snapshot directory changed after freeze"
            )
    for entry in value["entries"]:
        for field, label in (
            ("path", "live deliverable"),
            ("snapshot_path", "frozen deliverable snapshot"),
        ):
            data, observed = _regular_bytes_and_stat(Path(entry[field]), label=label)
            if hashlib.sha256(data).hexdigest() != entry["sha256"] or len(data) != entry["bytes"]:
                raise GateError(f"{label} bytes differ from the frozen inventory")
            identity_prefix = "" if field == "path" else "snapshot_"
            observed_identity = {
                f"{identity_prefix}device": observed.st_dev,
                f"{identity_prefix}inode": observed.st_ino,
                f"{identity_prefix}mode": stat.S_IMODE(observed.st_mode),
                f"{identity_prefix}mtime_ns": observed.st_mtime_ns,
                f"{identity_prefix}ctime_ns": observed.st_ctime_ns,
            }
            if observed_identity != {
                key: entry[key] for key in observed_identity
            }:
                raise GateError(f"{label} changed after the frozen inventory was created")


def _validate_inventory(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != INVENTORY_FIELDS:
        raise GateError("frozen inventory has schema drift")
    if value.get("format") != INVENTORY_FORMAT:
        raise GateError("frozen inventory has format drift")
    entries = value.get("entries")
    if not isinstance(entries, list) or not entries:
        raise GateError("frozen inventory entries must be nonempty")
    paths: list[str] = []
    relative_paths: list[str] = []
    snapshot_paths: list[str] = []
    framed = bytearray()
    total = 0
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != ENTRY_FIELDS:
            raise GateError("frozen inventory entry has schema drift")
        path = entry.get("path")
        relative_path = entry.get("relative_path")
        snapshot_path = entry.get("snapshot_path")
        digest = entry.get("sha256")
        size = entry.get("bytes")
        if not isinstance(path, str) or not Path(path).is_absolute():
            raise GateError("frozen inventory entry path must be absolute")
        if not isinstance(snapshot_path, str) or not Path(snapshot_path).is_absolute():
            raise GateError("frozen inventory snapshot path must be absolute")
        if (
            not isinstance(relative_path, str)
            or Path(relative_path).is_absolute()
            or Path(relative_path).as_posix() != relative_path
            or any(part in {"", ".", ".."} for part in Path(relative_path).parts)
        ):
            raise GateError("frozen inventory relative path is unsafe")
        _absolute(Path(path), label="frozen inventory source path")
        _absolute(Path(snapshot_path), label="frozen inventory snapshot path")
        snapshot_root = value.get("snapshot_root")
        if not isinstance(snapshot_root, str) or not Path(snapshot_root).is_absolute():
            raise GateError("frozen inventory snapshot root must be absolute")
        _absolute(Path(snapshot_root), label="frozen inventory snapshot root")
        if Path(snapshot_path) != Path(snapshot_root).joinpath(*Path(relative_path).parts):
            raise GateError("frozen inventory snapshot path does not preserve package layout")
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise GateError("frozen inventory entry digest is invalid")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise GateError("frozen inventory entry byte count is invalid")
        for field in (
            "device",
            "inode",
            "mode",
            "mtime_ns",
            "ctime_ns",
            "snapshot_device",
            "snapshot_inode",
            "snapshot_mode",
            "snapshot_mtime_ns",
            "snapshot_ctime_ns",
        ):
            if (
                type(entry.get(field)) is not int
                or entry[field] < 0
                or (field in {"mode", "snapshot_mode"} and entry[field] > 0o7777)
            ):
                raise GateError(f"frozen inventory entry {field} is invalid")
        paths.append(path)
        relative_paths.append(relative_path)
        snapshot_paths.append(snapshot_path)
        total += size
        framed.extend(
            f"F\t{digest}\t{size}\t{entry['device']}\t{entry['inode']}\t{entry['mode']}\t"
            f"{entry['mtime_ns']}\t{entry['ctime_ns']}\t{entry['snapshot_device']}\t"
            f"{entry['snapshot_inode']}\t{entry['snapshot_mode']}\t"
            f"{entry['snapshot_mtime_ns']}\t{entry['snapshot_ctime_ns']}\t"
            f"{relative_path}\t{path}\t"
            f"{snapshot_path}\n".encode("utf-8")
        )
    if paths != sorted(paths, key=lambda item: item.encode("utf-8")) or len(paths) != len(set(paths)):
        raise GateError("frozen inventory paths must be sorted and distinct")
    if len(snapshot_paths) != len(set(snapshot_paths)):
        raise GateError("frozen inventory snapshot paths must be distinct")
    if len(relative_paths) != len(set(relative_paths)):
        raise GateError("frozen inventory relative paths must be distinct")
    artifact_root = value.get("artifact_root")
    if not isinstance(artifact_root, str) or not Path(artifact_root).is_absolute():
        raise GateError("frozen inventory artifact root must be absolute")
    _absolute(Path(artifact_root), label="frozen inventory artifact root")
    if any(
        not Path(path).is_relative_to(Path(artifact_root)) for path in paths
    ):
        raise GateError("frozen inventory source path escapes its package root")
    directories = value.get("directories")
    if not isinstance(directories, list) or not directories:
        raise GateError("frozen inventory directories must include the package root")
    directory_relatives: list[str] = []
    directory_paths: list[str] = []
    directory_snapshot_paths: list[str] = []
    for entry in directories:
        if not isinstance(entry, dict) or set(entry) != DIRECTORY_FIELDS:
            raise GateError("frozen inventory directory entry has schema drift")
        relative = entry.get("relative_path")
        path = entry.get("path")
        snapshot_path = entry.get("snapshot_path")
        if (
            not isinstance(relative, str)
            or (relative != "." and (
                Path(relative).is_absolute()
                or Path(relative).as_posix() != relative
                or any(part in {"", ".", ".."} for part in Path(relative).parts)
            ))
        ):
            raise GateError("frozen inventory directory relative path is unsafe")
        if not isinstance(path, str) or not Path(path).is_absolute():
            raise GateError("frozen inventory directory path must be absolute")
        if not isinstance(snapshot_path, str) or not Path(snapshot_path).is_absolute():
            raise GateError("frozen inventory snapshot directory path must be absolute")
        expected_path = (
            Path(artifact_root)
            if relative == "."
            else Path(artifact_root).joinpath(*Path(relative).parts)
        )
        expected_snapshot = (
            Path(str(value["snapshot_root"]))
            if relative == "."
            else Path(str(value["snapshot_root"])).joinpath(*Path(relative).parts)
        )
        if Path(path) != expected_path or Path(snapshot_path) != expected_snapshot:
            raise GateError("frozen inventory directory path does not reproduce")
        _absolute(Path(path), label="frozen inventory directory")
        _absolute(Path(snapshot_path), label="frozen inventory snapshot directory")
        for field in DIRECTORY_FIELDS - {"path", "relative_path", "snapshot_path"}:
            if (
                type(entry.get(field)) is not int
                or entry[field] < 0
                or (field in {"mode", "snapshot_mode"} and entry[field] > 0o7777)
            ):
                raise GateError(f"frozen inventory directory {field} is invalid")
        directory_relatives.append(relative)
        directory_paths.append(path)
        directory_snapshot_paths.append(snapshot_path)
        framed.extend(
            f"D\t{entry['device']}\t{entry['inode']}\t{entry['mode']}\t"
            f"{entry['mtime_ns']}\t{entry['ctime_ns']}\t{entry['snapshot_device']}\t"
            f"{entry['snapshot_inode']}\t{entry['snapshot_mode']}\t"
            f"{entry['snapshot_mtime_ns']}\t{entry['snapshot_ctime_ns']}\t"
            f"{relative}\t{path}\t{snapshot_path}\n".encode("utf-8")
        )
    if (
        directory_relatives
        != sorted(directory_relatives, key=lambda item: item.encode("utf-8"))
        or directory_relatives[0] != "."
        or len(directory_relatives) != len(set(directory_relatives))
        or len(directory_paths) != len(set(directory_paths))
        or len(directory_snapshot_paths) != len(set(directory_snapshot_paths))
    ):
        raise GateError("frozen inventory directories must be sorted and distinct")
    expected_directory_relatives = {"."}
    for relative in relative_paths:
        parts = Path(relative).parts
        for depth in range(1, len(parts)):
            expected_directory_relatives.add(Path(*parts[:depth]).as_posix())
    # Empty directories are valid and are therefore allowed in addition to
    # the parents implied by files; every declared directory is still bound.
    if not expected_directory_relatives.issubset(set(directory_relatives)):
        raise GateError("frozen inventory omits a file parent directory")
    if value.get("file_count") != len(entries) or value.get("total_bytes") != total:
        raise GateError("frozen inventory counts do not reproduce")
    digest = hashlib.sha256(framed).hexdigest()
    if value.get("sha256") != digest:
        raise GateError("frozen inventory aggregate digest does not reproduce")


def _report(
    data: bytes,
    *,
    review_id: str,
    cycle: int,
    freeze_id: str,
    inventory_sha256: str,
) -> Dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GateError("final report must contain one UTF-8 JSON object") from exc
    if not isinstance(value, dict) or set(value) != REPORT_FIELDS:
        raise GateError("final report has schema drift")
    if value.get("schema_version") != SCHEMA_VERSION or value.get("record_type") != REPORT_TYPE:
        raise GateError("final report has contract drift")
    if value.get("review_id") != review_id:
        raise GateError("final report names another review")
    if value.get("cycle") != cycle or value.get("freeze_id") != freeze_id:
        raise GateError("final report names another freeze cycle")
    _identity(value.get("reviewer_id"), "reviewer_id")
    if value.get("frozen_inventory_sha256") != inventory_sha256:
        raise GateError("final report does not name the frozen inventory")
    if value.get("verdict") != "PASS":
        raise GateError("final report verdict is not PASS")
    if not _valid_utc(value.get("reviewed_at")):
        raise GateError("final report reviewed_at must be UTC RFC3339")
    if not isinstance(value.get("summary"), str) or not value["summary"].strip():
        raise GateError("final report summary must be nonempty")
    findings = value.get("findings")
    if not isinstance(findings, list) or not all(
        isinstance(finding, str) and finding.strip() for finding in findings
    ):
        raise GateError("final report findings must be an array of nonempty strings")
    return value


def _validate_invalidation(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != INVALIDATION_FIELDS:
        raise GateError("invalidation evidence has schema drift")
    if not isinstance(value.get("cycle"), int) or isinstance(value.get("cycle"), bool) or value["cycle"] <= 0:
        raise GateError("invalidation cycle is invalid")
    _identity(value.get("freeze_id"), "invalidation freeze_id")
    if not _valid_utc(value.get("invalidated_at")):
        raise GateError("invalidation time is invalid")
    if not isinstance(value.get("reason"), str) or not value["reason"].strip():
        raise GateError("invalidation reason is invalid")
    if not isinstance(value.get("frozen_inventory_sha256"), str) or not SHA256.fullmatch(value["frozen_inventory_sha256"]):
        raise GateError("invalidation inventory digest is invalid")


def _validate_state(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) != STATE_FIELDS:
        raise GateError("final-byte state has schema drift")
    if value.get("schema_version") != SCHEMA_VERSION or value.get("record_type") != STATE_TYPE:
        raise GateError("final-byte state has contract drift")
    _identity(value.get("review_id"), "review_id")
    if not isinstance(value.get("cycle"), int) or isinstance(value.get("cycle"), bool) or value["cycle"] <= 0:
        raise GateError("final-byte state cycle is invalid")
    _identity(value.get("freeze_id"), "freeze_id")
    if value.get("status") not in {PENDING, APPROVED, INVALIDATED}:
        raise GateError("final-byte state status is invalid")
    contributors = value.get("contributors")
    if not isinstance(contributors, list) or not contributors:
        raise GateError("final-byte state contributors must be nonempty")
    for contributor in contributors:
        _identity(contributor, "contributor")
    if contributors != sorted(set(contributors), key=lambda item: item.encode("utf-8")):
        raise GateError("final-byte state contributors must be sorted and distinct")
    _validate_inventory(value.get("frozen_inventory"))
    report_identity = value.get("final_report")
    if report_identity is not None:
        if not isinstance(report_identity, dict) or set(report_identity) != REPORT_IDENTITY_FIELDS:
            raise GateError("final report identity has schema drift")
        if not isinstance(report_identity.get("path"), str) or not Path(report_identity["path"]).is_absolute():
            raise GateError("final report identity path must be absolute")
        if not isinstance(report_identity.get("sha256"), str) or not SHA256.fullmatch(report_identity["sha256"]):
            raise GateError("final report identity digest is invalid")
        if not isinstance(report_identity.get("bytes"), int) or isinstance(report_identity.get("bytes"), bool) or report_identity["bytes"] < 0:
            raise GateError("final report identity byte count is invalid")
        report = report_identity.get("report")
        if not isinstance(report, dict):
            raise GateError("final report identity lacks its parsed report")
    prior = value.get("prior_invalidations")
    if not isinstance(prior, list):
        raise GateError("prior invalidations must be an array")
    for invalidation in prior:
        _validate_invalidation(invalidation)
    invalidation = value.get("invalidation")
    if value["status"] == PENDING and (report_identity is not None or invalidation is not None):
        raise GateError("pending final-byte state cannot contain approval or invalidation")
    if value["status"] == APPROVED and (report_identity is None or invalidation is not None):
        raise GateError("approved final-byte state lacks one current report")
    if value["status"] == INVALIDATED:
        _validate_invalidation(invalidation)
    return value


def _lock_path(state_path: Path) -> Path:
    return state_path.with_name(state_path.name + ".lock")


@contextmanager
def _locked(state_path: Path) -> Iterator[None]:
    state_path = _absolute(state_path, label="final-byte state")
    lock_path = _absolute(_lock_path(state_path), label="final-byte lock")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        observed = os.fstat(descriptor)
        if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
            raise GateError("final-byte lock must be a regular single-link file")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _load_state(path: Path) -> Dict[str, Any]:
    data = _regular_bytes(path, label="final-byte state")
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GateError("final-byte state must contain one UTF-8 JSON object") from exc
    return _validate_state(value)


def _legacy_state_invalidation(path: Path, review_id: str) -> tuple[int, Dict[str, Any]]:
    """Read only enough v1 identity to invalidate it; never inherit approval."""
    data = _regular_bytes(path, label="legacy final-byte state")
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GateError("legacy final-byte state is invalid JSON") from exc
    inventory = value.get("frozen_inventory") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or value.get("record_type") != STATE_TYPE
        or value.get("review_id") != review_id
        or type(value.get("cycle")) is not int
        or value["cycle"] <= 0
        or not isinstance(value.get("freeze_id"), str)
        or not IDENTITY.fullmatch(value["freeze_id"])
        or not isinstance(inventory, dict)
        or not isinstance(inventory.get("sha256"), str)
        or not SHA256.fullmatch(inventory["sha256"])
    ):
        raise GateError("legacy final-byte state cannot be safely invalidated")
    return value["cycle"], {
        "cycle": value["cycle"],
        "freeze_id": value["freeze_id"],
        "invalidated_at": utc_now(),
        "reason": "legacy v1 final-byte state requires a complete v2 refreeze and review",
        "frozen_inventory_sha256": inventory["sha256"],
    }


def _persist(path: Path, state: Mapping[str, Any]) -> None:
    path = _absolute(path, label="final-byte state")
    value = _validate_state(dict(state))
    encoded = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise GateError("final-byte state write made no progress")
            view = view[written:]
        os.fsync(descriptor)
        if os.fstat(descriptor).st_nlink != 1:
            raise GateError("final-byte state temporary file gained a hard link")
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _invalidation(state: Mapping[str, Any], reason: str) -> Dict[str, Any]:
    return {
        "cycle": state["cycle"],
        "freeze_id": state["freeze_id"],
        "invalidated_at": utc_now(),
        "reason": reason,
        "frozen_inventory_sha256": state["frozen_inventory"]["sha256"],
    }


def _invalidate(path: Path, state: Dict[str, Any], reason: str) -> None:
    state["status"] = INVALIDATED
    state["invalidation"] = _invalidation(state, reason)
    _persist(path, state)


def freeze(
    state_path: Path,
    *,
    review_id: str,
    contributors: Sequence[str],
    artifacts: Sequence[Path],
    artifact_root: Optional[Path] = None,
) -> Dict[str, Any]:
    review_id = _identity(review_id, "review_id")
    normalized_contributors = sorted(
        {_identity(value, "contributor") for value in contributors},
        key=lambda item: item.encode("utf-8"),
    )
    if not normalized_contributors:
        raise GateError("freeze requires at least one author or fixer identity")
    state_path = _absolute(state_path, label="final-byte state")
    with _locked(state_path):
        normalized_root, live_entries, live_directories = _live_entries(
            artifacts, artifact_root
        )
        prior_invalidations: list[Dict[str, Any]] = []
        cycle = 1
        if state_path.exists():
            try:
                current = _load_state(state_path)
            except GateError as exc:
                try:
                    legacy_cycle, legacy_invalidation = _legacy_state_invalidation(
                        state_path, review_id
                    )
                except GateError:
                    raise exc
                prior_invalidations = [legacy_invalidation]
                cycle = legacy_cycle + 1
                current = None
            if current is None:
                pass
            elif current["review_id"] != review_id:
                raise GateError("existing final-byte state belongs to another review")
            elif (
                current["contributors"] == normalized_contributors
                and [
                    {
                        "path": entry["path"],
                        "relative_path": entry["relative_path"],
                        "sha256": entry["sha256"],
                        "bytes": entry["bytes"],
                        "device": entry["device"],
                        "inode": entry["inode"],
                        "mode": entry["mode"],
                        "mtime_ns": entry["mtime_ns"],
                        "ctime_ns": entry["ctime_ns"],
                    }
                    for entry in current["frozen_inventory"]["entries"]
                ]
                == live_entries
                and [
                    {
                        key: entry[key]
                        for key in (
                            "path",
                            "relative_path",
                            "device",
                            "inode",
                            "mode",
                            "mtime_ns",
                            "ctime_ns",
                        )
                    }
                    for entry in current["frozen_inventory"]["directories"]
                ]
                == live_directories
            ) and current["status"] in {PENDING, APPROVED}:
                _verify_frozen_inventory(current["frozen_inventory"])
                return {"action": current["status"], "state": current, "changed": False}
            elif current is not None:
                prior_invalidations = list(current["prior_invalidations"])
                prior = current.get("invalidation") or _invalidation(
                    current, "deliverable set, bytes, or contributors changed before Phase C"
                )
                prior_invalidations.append(prior)
                cycle = current["cycle"] + 1
        freeze_id = uuid.uuid4().hex
        inventory = _snapshot_inventory(
            state_path,
            live_entries,
            live_directories,
            artifact_root=normalized_root,
            cycle=cycle,
            freeze_id=freeze_id,
        )
        state: Dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "record_type": STATE_TYPE,
            "review_id": review_id,
            "cycle": cycle,
            "freeze_id": freeze_id,
            "status": PENDING,
            "contributors": normalized_contributors,
            "frozen_inventory": inventory,
            "final_report": None,
            "invalidation": None,
            "prior_invalidations": prior_invalidations,
        }
        _persist(state_path, state)
        return {"action": PENDING, "state": state, "changed": True}


def approve(state_path: Path, *, report_path: Path) -> Dict[str, Any]:
    state_path = _absolute(state_path, label="final-byte state", must_exist=True)
    with _locked(state_path):
        state = _load_state(state_path)
        try:
            _verify_frozen_inventory(state["frozen_inventory"])
        except GateError as exc:
            _invalidate(state_path, state, f"deliverable verification failed before approval: {exc}")
            raise GateError("frozen deliverables changed before final approval") from exc
        report_path = _absolute(report_path, label="final report", must_exist=True)
        artifact_paths = {
            path
            for entry in state["frozen_inventory"]["entries"]
            for path in (entry["path"], entry["snapshot_path"])
        }
        if str(report_path) in artifact_paths:
            raise GateError("final report must be separate from the deliverable set")
        data = _regular_bytes(report_path, label="final report")
        report = _report(
            data,
            review_id=state["review_id"],
            cycle=state["cycle"],
            freeze_id=state["freeze_id"],
            inventory_sha256=state["frozen_inventory"]["sha256"],
        )
        if report["reviewer_id"] in state["contributors"]:
            raise GateError("final reviewer must not be an author or fixer")
        identity = {
            "path": str(report_path),
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "report": report,
        }
        if state["status"] == APPROVED:
            if state["final_report"] != identity:
                raise GateError("approved final report conflicts with retry")
            return {"action": APPROVED, "state": state, "changed": False}
        if state["status"] != PENDING:
            raise GateError("invalidated final-byte state must be frozen and reviewed again")
        state["status"] = APPROVED
        state["final_report"] = identity
        _persist(state_path, state)
        return {"action": APPROVED, "state": state, "changed": True}


def check(state_path: Path) -> Dict[str, Any]:
    state_path = _absolute(state_path, label="final-byte state", must_exist=True)
    with _locked(state_path):
        state = _load_state(state_path)
        if state["status"] != APPROVED:
            raise GateError("Phase C requires FINAL_REVIEW_APPROVED")
        try:
            _verify_frozen_inventory(state["frozen_inventory"])
        except GateError as exc:
            _invalidate(state_path, state, f"deliverable verification failed after approval: {exc}")
            raise GateError("final review invalidated by deliverable drift") from exc
        report_identity = state["final_report"]
        assert isinstance(report_identity, dict)
        try:
            report_data = _regular_bytes(Path(report_identity["path"]), label="final report")
            parsed = _report(
                report_data,
                review_id=state["review_id"],
                cycle=state["cycle"],
                freeze_id=state["freeze_id"],
                inventory_sha256=state["frozen_inventory"]["sha256"],
            )
        except GateError as exc:
            _invalidate(state_path, state, f"final report verification failed: {exc}")
            raise GateError("final review invalidated by report drift") from exc
        if (
            hashlib.sha256(report_data).hexdigest() != report_identity["sha256"]
            or len(report_data) != report_identity["bytes"]
            or parsed != report_identity["report"]
        ):
            _invalidate(state_path, state, "final report bytes changed after approval")
            raise GateError("final review invalidated by report drift")
        return {
            "action": "READY_FOR_PHASE_C",
            "review_id": state["review_id"],
            "cycle": state["cycle"],
            "freeze_id": state["freeze_id"],
            "frozen_inventory_sha256": state["frozen_inventory"]["sha256"],
            "reviewer_id": parsed["reviewer_id"],
        }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    freeze_parser = subparsers.add_parser("freeze")
    freeze_parser.add_argument("--state", required=True, type=Path)
    freeze_parser.add_argument("--review-id", required=True)
    freeze_parser.add_argument("--contributor", action="append", required=True)
    freeze_parser.add_argument("--artifact", action="append", required=True, type=Path)
    freeze_parser.add_argument("--artifact-root", required=True, type=Path)
    approve_parser = subparsers.add_parser("approve")
    approve_parser.add_argument("--state", required=True, type=Path)
    approve_parser.add_argument("--report", required=True, type=Path)
    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--state", required=True, type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "freeze":
            result = freeze(
                args.state,
                review_id=args.review_id,
                contributors=args.contributor,
                artifacts=args.artifact,
                artifact_root=args.artifact_root,
            )
        elif args.command == "approve":
            result = approve(args.state, report_path=args.report)
        elif args.command == "check":
            result = check(args.state)
        else:
            raise GateError(f"unsupported command: {args.command}")
    except (GateError, OSError) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
