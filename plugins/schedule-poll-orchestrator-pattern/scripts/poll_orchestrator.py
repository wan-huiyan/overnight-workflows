#!/usr/bin/env python3
"""Durable, idempotent schedule-poll state machine.

The state machine durably claims local consolidation and authorized PR creation
with stable idempotency keys.  Loaded callers can pass an action callback to
:func:`run_guarded_action`; that boundary invokes it only after revalidating the
exact decision and authority receipt.  The module has no built-in commit, push,
network, paid-call, schedule, or pull-request implementation.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import unicodedata
from typing import Any, Callable, Dict, Mapping, Optional, Sequence


SCHEMA_VERSION = 2
EXTERNAL_SCHEMA_VERSION = 1
STATE_TYPE = "schedule_poll_state"
AUTHORITY_TYPE = "schedule_external_action_authority"
DECISION_TYPE = "schedule_external_action_decision"
PR_RECEIPT_TYPE = "schedule_pull_request_receipt"
TRACK_PHASES = {"running", "complete", "tapped_out"}
TERMINAL_TRACK_PHASES = {"complete", "tapped_out"}
GRANT_FIELDS = {
    "commit",
    "push",
    "pull_request",
    "network",
    "paid_call",
    "external_write",
}
ACTION_REQUIREMENTS = {
    "schedule-trigger": {"network", "external_write"},
    "commit": {"commit"},
    "push": {"push", "network", "external_write"},
    "pull-request": {"pull_request", "network", "external_write"},
    "paid-call": {"paid_call", "network"},
    "external-write": {"external_write", "network"},
}
PR_REQUIRED_GRANTS = ACTION_REQUIREMENTS["pull-request"]
PR_RECEIPT_FIELDS = {
    "schema_version",
    "record_type",
    "run_id",
    "operation_id",
    "idempotency_key",
    "provider",
    "repository",
    "pull_request_id",
    "url",
    "state",
    "recorded_at",
}
REPOSITORY_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
HTTPS_URL_RE = re.compile(r"https://[^\s\x00-\x1f\x7f]+\Z")
JOURNAL_RECORD_FIELDS = {
    "poll_initialized": {
        "schema_version",
        "record_type",
        "event_id",
        "run_id",
        "status_path",
        "journal_path",
        "configuration",
        "configuration_sha256",
        "recorded_at",
    },
    "track_status_recorded": {
        "schema_version",
        "record_type",
        "event_id",
        "run_id",
        "track",
        "update_id",
        "phase",
        "reason",
        "evidence",
        "recorded_at",
    },
    "poll_outcome": {
        "schema_version",
        "record_type",
        "event_id",
        "action",
        "trigger_id",
        "run_id",
        "recorded_at",
        "phases",
    },
    "consolidation_complete": {
        "schema_version",
        "record_type",
        "event_id",
        "run_id",
        "operation_id",
        "evidence",
        "recorded_at",
    },
    "pull_request_claimed": {
        "schema_version",
        "record_type",
        "event_id",
        "run_id",
        "operation_id",
        "authority",
        "recorded_at",
    },
    "pull_request_complete": {
        "schema_version",
        "record_type",
        "event_id",
        "run_id",
        "operation_id",
        "receipt",
        "recorded_at",
    },
}
IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
HEX_64_RE = re.compile(r"[0-9a-f]{64}\Z")
RFC3339_UTC_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z"
)
SAFE_COMPONENT_FORBIDDEN = {"", ".", ".."}


class NonConformingJSON(ValueError):
    """These bytes do not have one meaning, so no gate may act on them.

    Four ways ``json.loads`` will hand back a confident answer where another
    conforming parser would hand back a different one, or none:

    * a repeated key -- RFC 8259 leaves the outcome undefined, Python keeps the
      last, and an implementation that keeps the first reads the same bytes as
      a different document;
    * ``NaN`` / ``Infinity`` / ``-Infinity`` -- a Python extension, not JSON at
      all, which a conforming parser rejects outright;
    * a number too large for a float, such as ``1e400`` -- ordinary JSON number
      syntax, which Python resolves to the same infinity as the line above and
      then re-encodes as the literal ``Infinity`` this decoder refuses;
    * an unpaired surrogate escape such as ``\\ud800`` -- Python keeps the lone
      code point and Go's encoding/json substitutes U+FFFD, so one document
      decodes to two different strings and a digest over them disagrees.

    Every decode routed through ``_strict_json_loads`` feeds a gate, so all four
    are refused rather than resolved.
    """


def _strict_json_loads(text):
    """Decode one JSON document, refusing anything with two readings."""

    def reject_duplicate_keys(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise NonConformingJSON(f"duplicate JSON key {key!r}")
            value[key] = item
        return value

    def reject_non_json_constant(constant: str):
        raise NonConformingJSON(f"non-JSON constant {constant!r}")

    def reject_non_finite_number(number: str):
        # parse_float sees every number carrying a "." or an exponent, and
        # parse_constant is NEVER called for ordinary number syntax -- which is
        # why refusing the spelled-out Infinity alone still accepted 1e400 and
        # then re-encoded it to the very literal it refuses. Integer tokens
        # cannot overflow, Python's ints being arbitrary precision, so this is
        # the whole of the surface.
        value = float(number)
        if value == float("inf") or value == float("-inf"):
            raise NonConformingJSON(f"out-of-range JSON number {number!r}")
        return value

    value = json.loads(
        text,
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=reject_non_json_constant,
        parse_float=reject_non_finite_number,
    )
    # The complete test for an unpaired surrogate anywhere in the document --
    # in a key, in a value, or inside an array -- without walking it. Encoding
    # refuses a lone surrogate and accepts the character a surrogate PAIR
    # decodes to, so ordinary non-BMP text written as an escape pair still
    # passes. Raw surrogate bytes cannot arrive at all: UTF-8 decoding rejects
    # them before this function is reached.
    try:
        json.dumps(value, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError as exc:
        raise NonConformingJSON("unpaired surrogate in a JSON string") from exc
    return value


class PollError(RuntimeError):
    """Fail-closed state requiring inspection rather than another side effect."""


Failpoint = Optional[Callable[[str], None]]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _timestamp_from_epoch(value: int) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not RFC3339_UTC_RE.fullmatch(value):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.utcoffset() is not None and parsed.utcoffset().total_seconds() == 0


def _identity(value: Any, label: str) -> str:
    if not isinstance(value, str) or not IDENTITY_RE.fullmatch(value):
        raise PollError(f"{label} must be a normalized identity")
    return value


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not HEX_64_RE.fullmatch(value):
        raise PollError(f"{label} must be lowercase SHA-256")
    return value


def _positive_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise PollError(f"{label} must be a positive integer")
    return value


def _safe_absolute(path: Path, *, label: str, must_exist: bool = False) -> Path:
    path = Path(path)
    if any(ord(character) < 32 or ord(character) == 127 for character in str(path)):
        raise PollError(f"{label} contains a control character: {path!r}")
    if not path.is_absolute() or ".." in path.parts:
        raise PollError(f"{label} must be a normalized absolute path: {path}")
    current = Path(path.anchor)
    missing_seen = False
    for component in path.parts[1:]:
        if component in SAFE_COMPONENT_FORBIDDEN:
            raise PollError(f"unsafe component in {label}: {path}")
        current = current / component
        try:
            observed = os.lstat(current)
        except FileNotFoundError:
            missing_seen = True
            continue
        if missing_seen:
            raise PollError(f"{label} has an existing child below a missing parent")
        if stat.S_ISLNK(observed.st_mode):
            raise PollError(f"{label} has a symlink component")
        if current != path and not stat.S_ISDIR(observed.st_mode):
            raise PollError(f"{label} parent is not a directory")
    if must_exist and not path.exists():
        raise PollError(f"{label} does not exist: {path}")
    return path


def _read_regular_bytes(path: Path, *, label: str) -> bytes:
    path = _safe_absolute(path, label=label, must_exist=True)
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise PollError(f"{label} must be a regular single-link file")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (
            (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or opened.st_nlink != 1
        ):
            raise PollError(f"{label} changed before open")
        chunks: list[bytes] = []
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
            or not stat.S_ISREG(after.st_mode)
            or len(data) != after.st_size
            or after.st_nlink != 1
        ):
            raise PollError(f"{label} changed while read")
        return data
    finally:
        os.close(descriptor)


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


CONFIGURATION_FIELDS = {
    "run_id",
    "status_path",
    "journal_path",
    "dispatch_epoch",
    "hard_ceiling_seconds",
    "poll_interval_seconds",
    "expected_tracks",
}


def _configuration_digest(configuration: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(configuration)).hexdigest()


def _validate_configuration(value: Any, digest: Any) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) != CONFIGURATION_FIELDS:
        raise PollError("poll configuration has schema drift")
    _identity(value.get("run_id"), "poll configuration run_id")
    for field in ("status_path", "journal_path"):
        if not isinstance(value.get(field), str):
            raise PollError(f"poll configuration {field} is malformed")
        _safe_absolute(Path(value[field]), label=f"poll configuration {field}")
    for field in ("dispatch_epoch", "hard_ceiling_seconds", "poll_interval_seconds"):
        _positive_int(value.get(field), f"poll configuration {field}")
    tracks = value.get("expected_tracks")
    if (
        not isinstance(tracks, list)
        or len(tracks) < 2
        or any(not isinstance(track, str) for track in tracks)
        or len(set(tracks)) != len(tracks)
    ):
        raise PollError("poll configuration expected_tracks is malformed")
    for track in tracks:
        _identity(track, "poll configuration track")
    _sha256(digest, "poll configuration SHA-256")
    if digest != _configuration_digest(value):
        raise PollError("poll configuration digest does not match its object")
    return value


def _lock_path(journal_path: Path) -> Path:
    return journal_path.with_name(journal_path.name + ".lock")


def _same_inode(left: Path, right: Path) -> bool:
    try:
        left_stat = os.lstat(left)
        right_stat = os.lstat(right)
    except FileNotFoundError:
        return False
    return (left_stat.st_dev, left_stat.st_ino) == (right_stat.st_dev, right_stat.st_ino)


def _filesystem_alias_key(path: Path) -> str:
    """Conservatively identify names that alias on common insensitive filesystems."""

    return unicodedata.normalize("NFC", os.fspath(path)).casefold()


def _control_paths(status_path: Path, journal_path: Path) -> tuple[Path, Path, Path]:
    status_path = _safe_absolute(status_path, label="poll status")
    journal_path = _safe_absolute(journal_path, label="poll journal")
    lock_path = _safe_absolute(_lock_path(journal_path), label="poll run lock")
    paths = (status_path, journal_path, lock_path)
    for index, left in enumerate(paths):
        for right in paths[index + 1 :]:
            if (
                _filesystem_alias_key(left) == _filesystem_alias_key(right)
                or _same_inode(left, right)
            ):
                raise PollError("poll status, journal, and run lock cannot alias")
    return paths


class RunLock:
    def __init__(self, status_path: Path, journal_path: Path) -> None:
        self.status_path, self.journal_path, self.path = _control_paths(
            status_path, journal_path
        )
        self.descriptor: Optional[int] = None

    def __enter__(self) -> "RunLock":
        _control_paths(self.status_path, self.journal_path)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        self.descriptor = os.open(self.path, flags, 0o600)
        locked = False
        try:
            observed = os.fstat(self.descriptor)
            named = os.lstat(self.path)
            if (
                not stat.S_ISREG(observed.st_mode)
                or not stat.S_ISREG(named.st_mode)
                or observed.st_nlink != 1
                or named.st_nlink != 1
                or (observed.st_dev, observed.st_ino) != (named.st_dev, named.st_ino)
            ):
                raise PollError("poll run lock must be a regular single-link file")
            fcntl.flock(self.descriptor, fcntl.LOCK_EX)
            locked = True
            observed = os.fstat(self.descriptor)
            named = os.lstat(self.path)
            if (
                not stat.S_ISREG(observed.st_mode)
                or not stat.S_ISREG(named.st_mode)
                or observed.st_nlink != 1
                or named.st_nlink != 1
                or (observed.st_dev, observed.st_ino) != (named.st_dev, named.st_ino)
            ):
                raise PollError("poll run lock changed while acquiring it")
            _control_paths(self.status_path, self.journal_path)
        except BaseException:
            if locked:
                fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            os.close(self.descriptor)
            self.descriptor = None
            raise
        return self

    def __exit__(self, *_: object) -> None:
        assert self.descriptor is not None
        try:
            fcntl.flock(self.descriptor, fcntl.LOCK_UN)
        finally:
            os.close(self.descriptor)
            self.descriptor = None


def _write_state_atomic(path: Path, state: Mapping[str, Any]) -> None:
    path = _safe_absolute(path, label="poll status output")
    data = _canonical_json(state)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise PollError("poll status write made no progress")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary_path, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_path.exists():
            temporary_path.unlink()


def _validate_journal_record(value: Any, row: int) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise PollError(f"poll journal row {row} must be an object")
    record_type = value.get("record_type")
    expected = JOURNAL_RECORD_FIELDS.get(record_type)
    if record_type == "poll_outcome":
        expected = set(expected or ())
        if value.get("action") == "RESCHEDULE":
            expected.update({"next_trigger_id", "next_trigger_at"})
        else:
            expected.add("operation_id")
    if expected is None or set(value) != expected:
        raise PollError(f"poll journal row {row} has schema drift")
    if (
        type(value.get("schema_version")) is not int
        or value["schema_version"] != SCHEMA_VERSION
        or not _valid_timestamp(value.get("recorded_at"))
    ):
        raise PollError(f"poll journal row {row} has malformed common fields")
    event_id = _identity(value.get("event_id"), f"poll journal row {row} event_id")
    run_id = _identity(value.get("run_id"), f"poll journal row {row} run_id")
    if record_type == "poll_initialized":
        if event_id != f"init-{run_id}":
            raise PollError(f"poll journal row {row} has the wrong init identity")
        for field in ("status_path", "journal_path"):
            if not isinstance(value.get(field), str):
                raise PollError(f"poll journal row {row} has malformed control paths")
            _safe_absolute(
                Path(value[field]), label=f"poll journal row {row} {field}"
            )
        configuration = _validate_configuration(
            value.get("configuration"), value.get("configuration_sha256")
        )
        if (
            configuration["run_id"] != run_id
            or configuration["status_path"] != value["status_path"]
            or configuration["journal_path"] != value["journal_path"]
        ):
            raise PollError(f"poll journal row {row} initialization is not bound")
    elif record_type == "track_status_recorded":
        track = _identity(value.get("track"), f"poll journal row {row} track")
        update_id = _identity(value.get("update_id"), f"poll journal row {row} update_id")
        phase = value.get("phase")
        if event_id != f"track-{run_id}-{track}-{update_id}" or phase not in TRACK_PHASES:
            raise PollError(f"poll journal row {row} has malformed track identity")
        if phase in TERMINAL_TRACK_PHASES:
            if not isinstance(value.get("reason"), str) or not value["reason"].strip():
                raise PollError(f"poll journal row {row} terminal track lacks a reason")
            if value.get("evidence") is not None:
                _validate_evidence(value["evidence"], f"poll journal row {row} evidence")
        elif value.get("reason") is not None or value.get("evidence") is not None:
            raise PollError(f"poll journal row {row} running track has terminal fields")
    elif record_type == "poll_outcome":
        trigger_id = _identity(
            value.get("trigger_id"), f"poll journal row {row} trigger_id"
        )
        if event_id != f"trigger-{trigger_id}":
            raise PollError(f"poll journal row {row} has the wrong trigger identity")
        action = value.get("action")
        if action not in {
            "RESCHEDULE",
            "CONSOLIDATION_CLAIMED",
            "RESUME_CONSOLIDATION_CLAIM",
            "RESUME_PULL_REQUEST_CLAIM",
            "LOCAL_COMPLETE_EXTERNAL_ACTIONS_NOT_AUTHORIZED",
            "COMPLETE",
        }:
            raise PollError(f"poll journal row {row} has an unsupported action")
        phases = value.get("phases")
        if (
            not isinstance(phases, dict)
            or len(phases) < 2
            or any(
                not isinstance(name, str) or phase not in TRACK_PHASES
                for name, phase in phases.items()
            )
        ):
            raise PollError(f"poll journal row {row} has malformed phases")
        if action == "RESCHEDULE":
            _identity(value.get("next_trigger_id"), f"poll journal row {row} next trigger")
            if not _valid_timestamp(value.get("next_trigger_at")):
                raise PollError(f"poll journal row {row} has malformed next-trigger time")
        else:
            _identity(value.get("operation_id"), f"poll journal row {row} operation_id")
            if any(phase not in TERMINAL_TRACK_PHASES for phase in phases.values()):
                raise PollError(f"poll journal row {row} external phase is not terminal")
    elif record_type == "consolidation_complete":
        operation_id = _identity(
            value.get("operation_id"), f"poll journal row {row} operation_id"
        )
        if event_id != f"complete-{operation_id}":
            raise PollError(f"poll journal row {row} has the wrong completion identity")
        _validate_evidence(value.get("evidence"), f"poll journal row {row} evidence")
    elif record_type == "pull_request_claimed":
        operation_id = _identity(
            value.get("operation_id"), f"poll journal row {row} operation_id"
        )
        if event_id != f"claim-{operation_id}":
            raise PollError(f"poll journal row {row} has the wrong PR-claim identity")
        _validate_authority_identity(value.get("authority"))
    elif record_type == "pull_request_complete":
        operation_id = _identity(
            value.get("operation_id"), f"poll journal row {row} operation_id"
        )
        if event_id != f"complete-{operation_id}":
            raise PollError(f"poll journal row {row} has the wrong PR-completion identity")
        _validate_evidence(value.get("receipt"), f"poll journal row {row} receipt")
    return value


def _journal_records(
    path: Path, *, expected_run_id: Optional[str] = None
) -> list[dict[str, Any]]:
    if not path.exists() and not path.is_symlink():
        return []
    raw = _read_regular_bytes(path, label="poll journal")
    if not raw:
        return []
    if not raw.endswith(b"\n"):
        raise PollError("poll journal has a partial final row")
    records: list[dict[str, Any]] = []
    for row, line in enumerate(raw[:-1].split(b"\n"), 1):
        try:
            value = _strict_json_loads(line.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError, NonConformingJSON) as exc:
            raise PollError(f"poll journal row {row} is invalid") from exc
        records.append(_validate_journal_record(value, row))
    event_ids = [record["event_id"] for record in records]
    if len(event_ids) != len(set(event_ids)):
        raise PollError("poll journal repeats an event_id")
    run_ids = {record["run_id"] for record in records}
    if len(run_ids) > 1 or (expected_run_id is not None and run_ids not in ({expected_run_id}, set())):
        raise PollError("poll journal contains the wrong run")
    if records:
        if records[0]["record_type"] != "poll_initialized":
            raise PollError("poll journal must start with initialization")
        if sum(record["record_type"] == "poll_initialized" for record in records) != 1:
            raise PollError("poll journal must contain one initialization")
    claimed_consolidations: set[str] = set()
    completed_consolidations: set[str] = set()
    claimed_pull_requests: set[str] = set()
    completed_pull_requests: set[str] = set()
    for row, record in enumerate(records, 1):
        record_type = record["record_type"]
        operation_id = record.get("operation_id")
        if record_type == "poll_outcome" and record["action"] == "CONSOLIDATION_CLAIMED":
            if claimed_consolidations:
                raise PollError(
                    f"poll journal row {row} attempts a second consolidation claim"
                )
            claimed_consolidations.add(operation_id)
        elif (
            record_type == "poll_outcome"
            and record["action"] == "RESUME_CONSOLIDATION_CLAIM"
            and operation_id not in claimed_consolidations
        ):
            raise PollError(f"poll journal row {row} resumes an unclaimed consolidation")
        elif (
            record_type == "poll_outcome"
            and record["action"] == "LOCAL_COMPLETE_EXTERNAL_ACTIONS_NOT_AUTHORIZED"
            and operation_id not in completed_consolidations
        ):
            raise PollError(f"poll journal row {row} reports an incomplete consolidation")
        elif (
            record_type == "poll_outcome"
            and record["action"] == "RESUME_PULL_REQUEST_CLAIM"
            and operation_id not in claimed_pull_requests
        ):
            raise PollError(f"poll journal row {row} resumes an unclaimed pull request")
        elif (
            record_type == "poll_outcome"
            and record["action"] == "COMPLETE"
            and (
                operation_id not in completed_consolidations
                or not completed_pull_requests
            )
        ):
            raise PollError(f"poll journal row {row} reports an incomplete run")
        elif record_type == "consolidation_complete":
            if operation_id not in claimed_consolidations:
                raise PollError(
                    f"poll journal row {row} completes an unclaimed consolidation"
                )
            completed_consolidations.add(operation_id)
        elif record_type == "pull_request_claimed":
            if not completed_consolidations:
                raise PollError(
                    f"poll journal row {row} claims a PR before consolidation completion"
                )
            if claimed_pull_requests:
                raise PollError(f"poll journal row {row} attempts a second PR claim")
            claimed_pull_requests.add(operation_id)
        elif record_type == "pull_request_complete":
            if operation_id not in claimed_pull_requests:
                raise PollError(f"poll journal row {row} completes an unclaimed pull request")
            completed_pull_requests.add(operation_id)
    return records


def _preflight_journal_event(path: Path, event: Mapping[str, Any]) -> bool:
    existing = _journal_records(path, expected_run_id=str(event.get("run_id")))
    matching = [row for row in existing if row["event_id"] == event.get("event_id")]
    if matching:
        if len(matching) != 1 or matching[0] != dict(event):
            raise PollError("poll journal event identity conflicts with retry")
        return False
    _validate_journal_record(dict(event), len(existing) + 1)
    return True


def _ensure_journal_event(path: Path, event: Mapping[str, Any]) -> None:
    path = _safe_absolute(path, label="poll journal")
    if not _preflight_journal_event(path, event):
        return
    before = None
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        pass
    # Path.open("a") is intentional: append semantics are part of the tested contract.
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        observed = os.fstat(handle.fileno())
        named = os.lstat(path)
        if (
            not stat.S_ISREG(named.st_mode)
            or not stat.S_ISREG(observed.st_mode)
            or named.st_nlink != 1
            or observed.st_nlink != 1
            or (named.st_dev, named.st_ino) != (observed.st_dev, observed.st_ino)
            or (
                before is not None
                and (before.st_dev, before.st_ino) != (observed.st_dev, observed.st_ino)
            )
        ):
            raise PollError("poll journal must be a regular single-link file")
        handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _operation_key(run_id: str, label: str) -> str:
    digest = hashlib.sha256(f"{run_id}\0{label}".encode()).hexdigest()[:24]
    return f"{run_id}-{label}-{digest}"


def _validate_evidence(value: Any, label: str) -> None:
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        raise PollError(f"{label} identity is malformed")
    _safe_absolute(Path(str(value.get("path"))), label=f"{label} path")
    _sha256(value.get("sha256"), f"{label} SHA-256")


def _verify_stored_evidence(value: Any, label: str) -> Dict[str, str]:
    _validate_evidence(value, label)
    assert isinstance(value, dict)
    path = Path(value["path"])
    raw = _read_regular_bytes(path, label=label)
    if hashlib.sha256(raw).hexdigest() != value["sha256"]:
        raise PollError(f"{label} digest drifted")
    return {"path": str(path), "sha256": value["sha256"]}


def _validate_authority_identity(value: Any) -> None:
    _validate_authority_identity_without_required_grants(value)
    assert isinstance(value, dict)
    grants = value.get("grants")
    if not isinstance(grants, dict) or any(not grants[grant] for grant in PR_REQUIRED_GRANTS):
        raise PollError("pull-request authority grants are malformed")


def _validate_state(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise PollError("poll status must be an object")
    required = {
        "schema_version",
        "record_type",
        "run_id",
        "status_path",
        "journal_path",
        "configuration",
        "configuration_sha256",
        "dispatch_epoch",
        "hard_ceiling_seconds",
        "poll_interval_seconds",
        "expected_tracks",
        "tracks",
        "trigger_outcomes",
        "consolidation",
        "pull_request",
        "initialized_at",
        "updated_at",
    }
    if set(value) != required:
        raise PollError("poll status has missing or undeclared fields")
    if (
        type(value.get("schema_version")) is not int
        or value["schema_version"] != SCHEMA_VERSION
        or value.get("record_type") != STATE_TYPE
    ):
        raise PollError("poll status schema is unsupported")
    _identity(value.get("run_id"), "run_id")
    for field in ("status_path", "journal_path"):
        if not isinstance(value.get(field), str):
            raise PollError(f"poll status {field} is malformed")
        _safe_absolute(Path(value[field]), label=f"poll {field} identity")
    for field in ("dispatch_epoch", "hard_ceiling_seconds", "poll_interval_seconds"):
        _positive_int(value.get(field), field)
    for field in ("initialized_at", "updated_at"):
        if not _valid_timestamp(value.get(field)):
            raise PollError(f"poll status {field} is invalid")
    configuration = _validate_configuration(
        value.get("configuration"), value.get("configuration_sha256")
    )
    expected_configuration = {
        "run_id": value["run_id"],
        "status_path": value["status_path"],
        "journal_path": value["journal_path"],
        "dispatch_epoch": value["dispatch_epoch"],
        "hard_ceiling_seconds": value["hard_ceiling_seconds"],
        "poll_interval_seconds": value["poll_interval_seconds"],
        "expected_tracks": value["expected_tracks"],
    }
    if configuration != expected_configuration:
        raise PollError("poll status fields conflict with its bound configuration")
    tracks = value.get("tracks")
    expected_tracks = value.get("expected_tracks")
    if (
        not isinstance(expected_tracks, list)
        or len(expected_tracks) < 2
        or any(not isinstance(track, str) for track in expected_tracks)
        or len(set(expected_tracks)) != len(expected_tracks)
    ):
        raise PollError("poll status expected_tracks is malformed")
    if not isinstance(tracks, dict) or len(tracks) < 2:
        raise PollError("poll status must contain at least two tracks")
    if list(tracks) != expected_tracks:
        raise PollError("poll status is missing or reorders an expected track")
    for name, track in tracks.items():
        _identity(name, "track name")
        if not isinstance(track, dict) or set(track) != {
            "phase",
            "update_id",
            "updated_at",
            "reason",
            "evidence",
        }:
            raise PollError(f"track {name} status is malformed")
        _identity(track.get("update_id"), f"track {name} update_id")
        if track.get("phase") not in TRACK_PHASES or not _valid_timestamp(track.get("updated_at")):
            raise PollError(f"track {name} phase or timestamp is malformed")
        if track["phase"] in TERMINAL_TRACK_PHASES:
            if not isinstance(track.get("reason"), str) or not track["reason"].strip():
                raise PollError(f"terminal track {name} lacks reason")
            if track.get("evidence") is not None:
                _validate_evidence(track["evidence"], f"track {name} evidence")
        elif track.get("reason") is not None or track.get("evidence") is not None:
            raise PollError(f"running track {name} cannot claim terminal evidence")
    outcomes = value.get("trigger_outcomes")
    if not isinstance(outcomes, dict):
        raise PollError("trigger outcomes must be an object")
    for trigger_id, outcome in outcomes.items():
        _identity(trigger_id, "trigger identity")
        if not isinstance(outcome, dict) or outcome.get("trigger_id") != trigger_id:
            raise PollError("trigger outcome is malformed")
        action = outcome.get("action")
        common = {"action", "trigger_id", "run_id", "recorded_at", "phases"}
        expected = (
            common | {"next_trigger_id", "next_trigger_at"}
            if action == "RESCHEDULE"
            else common | {"operation_id"}
        )
        if (
            action
            not in {
                "RESCHEDULE",
                "CONSOLIDATION_CLAIMED",
                "RESUME_CONSOLIDATION_CLAIM",
                "RESUME_PULL_REQUEST_CLAIM",
                "LOCAL_COMPLETE_EXTERNAL_ACTIONS_NOT_AUTHORIZED",
                "COMPLETE",
            }
            or set(outcome) != expected
            or outcome.get("run_id") != value["run_id"]
            or not _valid_timestamp(outcome.get("recorded_at"))
            or not isinstance(outcome.get("phases"), dict)
            or list(outcome["phases"]) != expected_tracks
            or any(phase not in TRACK_PHASES for phase in outcome["phases"].values())
        ):
            raise PollError("trigger outcome is malformed")
        if action == "RESCHEDULE":
            _identity(outcome.get("next_trigger_id"), "next trigger identity")
            if not _valid_timestamp(outcome.get("next_trigger_at")):
                raise PollError("next trigger timestamp is malformed")
        else:
            _identity(outcome.get("operation_id"), "trigger operation identity")

    consolidation = value.get("consolidation")
    if not isinstance(consolidation, dict) or consolidation.get("state") not in {
        "NOT_CLAIMED",
        "CLAIMED",
        "COMPLETE",
    }:
        raise PollError("consolidation claim is malformed")
    if consolidation["state"] == "NOT_CLAIMED":
        if set(consolidation) != {"state"}:
            raise PollError("unclaimed consolidation has undeclared fields")
    else:
        expected = {"state", "operation_id", "claimed_at", "claimed_by_trigger", "evidence"}
        if consolidation["state"] == "COMPLETE":
            expected.add("completed_at")
        if set(consolidation) != expected:
            raise PollError("consolidation claim fields are malformed")
        _identity(consolidation.get("operation_id"), "consolidation operation_id")
        _identity(consolidation.get("claimed_by_trigger"), "consolidation trigger")
        if not _valid_timestamp(consolidation.get("claimed_at")):
            raise PollError("consolidation claim timestamp is malformed")
        if consolidation["state"] == "CLAIMED":
            if consolidation.get("evidence") is not None:
                raise PollError("claimed consolidation cannot carry completion evidence")
        else:
            if not _valid_timestamp(consolidation.get("completed_at")):
                raise PollError("consolidation completion timestamp is malformed")
            _validate_evidence(consolidation.get("evidence"), "consolidation evidence")

    pull_request = value.get("pull_request")
    if not isinstance(pull_request, dict) or pull_request.get("state") not in {
        "NOT_CLAIMED",
        "CLAIMED",
        "COMPLETE",
    }:
        raise PollError("pull_request claim is malformed")
    if pull_request["state"] == "NOT_CLAIMED":
        if set(pull_request) != {"state"}:
            raise PollError("unclaimed pull request has undeclared fields")
    else:
        expected = {"state", "operation_id", "claimed_at", "authority", "receipt"}
        if pull_request["state"] == "COMPLETE":
            expected.add("completed_at")
        if set(pull_request) != expected:
            raise PollError("pull-request claim fields are malformed")
        _identity(pull_request.get("operation_id"), "pull-request operation_id")
        if not _valid_timestamp(pull_request.get("claimed_at")):
            raise PollError("pull-request claim timestamp is malformed")
        _validate_authority_identity(pull_request.get("authority"))
        if pull_request["state"] == "CLAIMED":
            if pull_request.get("receipt") is not None:
                raise PollError("claimed pull request cannot carry completion receipt")
        else:
            if not _valid_timestamp(pull_request.get("completed_at")):
                raise PollError("pull-request completion timestamp is malformed")
            _validate_evidence(pull_request.get("receipt"), "pull-request receipt")
    if pull_request["state"] in {"CLAIMED", "COMPLETE"} and consolidation["state"] != "COMPLETE":
        raise PollError("pull-request claim requires completed consolidation")
    expected_consolidation_id = _operation_key(value["run_id"], "consolidation")
    if consolidation["state"] != "NOT_CLAIMED":
        if consolidation["operation_id"] != expected_consolidation_id:
            raise PollError("consolidation claim has a nondeterministic operation identity")
        if any(
            track["phase"] not in TERMINAL_TRACK_PHASES for track in tracks.values()
        ):
            raise PollError("consolidation claim requires every track to be terminal")
        claimed_outcome = outcomes.get(consolidation["claimed_by_trigger"])
        if (
            claimed_outcome is None
            or claimed_outcome.get("action") != "CONSOLIDATION_CLAIMED"
            or claimed_outcome.get("operation_id") != expected_consolidation_id
        ):
            raise PollError("consolidation claim lacks its originating trigger outcome")
    expected_pull_request_id = _operation_key(value["run_id"], "pull-request")
    if (
        pull_request["state"] != "NOT_CLAIMED"
        and pull_request["operation_id"] != expected_pull_request_id
    ):
        raise PollError("pull-request claim has a nondeterministic operation identity")
    for outcome in outcomes.values():
        action = outcome["action"]
        if action in {
            "CONSOLIDATION_CLAIMED",
            "RESUME_CONSOLIDATION_CLAIM",
            "LOCAL_COMPLETE_EXTERNAL_ACTIONS_NOT_AUTHORIZED",
            "COMPLETE",
        }:
            if (
                consolidation["state"] == "NOT_CLAIMED"
                or outcome["operation_id"] != consolidation.get("operation_id")
            ):
                raise PollError("trigger outcome does not join its consolidation claim")
        elif action == "RESUME_PULL_REQUEST_CLAIM":
            if (
                pull_request["state"] == "NOT_CLAIMED"
                or outcome["operation_id"] != pull_request.get("operation_id")
            ):
                raise PollError("trigger outcome does not join its pull-request claim")
        if action != "RESCHEDULE" and any(
            phase not in TERMINAL_TRACK_PHASES for phase in outcome["phases"].values()
        ):
            raise PollError("terminal trigger outcome contains a running phase")
        if action == "CONSOLIDATION_CLAIMED" and (
            consolidation["state"] == "NOT_CLAIMED"
            or consolidation["claimed_by_trigger"] != outcome["trigger_id"]
        ):
            raise PollError("claimed trigger is not the consolidation owner")
        if action == "LOCAL_COMPLETE_EXTERNAL_ACTIONS_NOT_AUTHORIZED" and (
            consolidation["state"] != "COMPLETE"
            or pull_request["state"] != "NOT_CLAIMED"
        ):
            raise PollError("local-complete outcome conflicts with durable claims")
        if action == "RESUME_PULL_REQUEST_CLAIM" and pull_request["state"] not in {
            "CLAIMED",
            "COMPLETE",
        }:
            raise PollError("PR-resume outcome conflicts with durable claim")
        if action == "COMPLETE" and (
            consolidation["state"] != "COMPLETE"
            or pull_request["state"] != "COMPLETE"
        ):
            raise PollError("complete outcome conflicts with durable claims")
    return value


def _bound_journal(
    state: Mapping[str, Any],
    status_path: Path,
    journal_path: Path,
    *,
    run_id: str,
    allow_missing_event_ids: Sequence[str] = (),
) -> list[dict[str, Any]]:
    status_path = _safe_absolute(status_path, label="poll status")
    journal_path = _safe_absolute(journal_path, label="poll journal")
    if state.get("status_path") != str(status_path):
        raise PollError("status path does not match the initialized run")
    if state.get("journal_path") != str(journal_path):
        raise PollError("journal path does not match the initialized run")
    records = _journal_records(journal_path, expected_run_id=run_id)
    if not records or records[0].get("event_id") != f"init-{run_id}":
        raise PollError("poll journal lacks this run's initialization")
    expected_init = {
        "schema_version": SCHEMA_VERSION,
        "record_type": "poll_initialized",
        "event_id": f"init-{run_id}",
        "run_id": run_id,
        "status_path": state["status_path"],
        "journal_path": state["journal_path"],
        "configuration": state["configuration"],
        "configuration_sha256": state["configuration_sha256"],
        "recorded_at": state["initialized_at"],
    }
    if records[0] != expected_init:
        raise PollError("poll journal initialization conflicts with state")
    _validate_state_journal_join(
        state, records, allow_missing_event_ids=set(allow_missing_event_ids)
    )
    return records


def _validate_state_journal_join(
    state: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    *,
    allow_missing_event_ids: set[str],
) -> None:
    by_id = {record["event_id"]: record for record in records}

    def require(expected: Mapping[str, Any]) -> None:
        event_id = str(expected["event_id"])
        observed = by_id.get(event_id)
        if observed is None:
            if event_id not in allow_missing_event_ids:
                raise PollError(f"poll journal is missing state event {event_id}")
        elif observed != dict(expected):
            raise PollError(f"poll journal event {event_id} conflicts with state")

    state_outcome_ids: set[str] = set()
    for trigger_id, outcome in state["trigger_outcomes"].items():
        expected = {
            "schema_version": SCHEMA_VERSION,
            "record_type": "poll_outcome",
            "event_id": f"trigger-{trigger_id}",
            **outcome,
        }
        state_outcome_ids.add(expected["event_id"])
        require(expected)
    journal_outcome_ids = {
        record["event_id"]
        for record in records
        if record["record_type"] == "poll_outcome"
    }
    if not journal_outcome_ids.issubset(state_outcome_ids):
        raise PollError("poll journal contains an outcome absent from state")

    for track_name, track in state["tracks"].items():
        update_id = track["update_id"]
        if update_id == "init" or update_id.startswith("ceiling-"):
            continue
        require(
            {
                "schema_version": SCHEMA_VERSION,
                "record_type": "track_status_recorded",
                "event_id": f"track-{state['run_id']}-{track_name}-{update_id}",
                "run_id": state["run_id"],
                "track": track_name,
                "update_id": update_id,
                "phase": track["phase"],
                "reason": track["reason"],
                "evidence": track["evidence"],
                "recorded_at": track["updated_at"],
            }
        )
    for record in records:
        if record["record_type"] == "track_status_recorded" and record["track"] not in state[
            "tracks"
        ]:
            raise PollError("poll journal contains an unknown track")

    consolidation = state["consolidation"]
    consolidation_complete_ids = {
        record["event_id"]
        for record in records
        if record["record_type"] == "consolidation_complete"
    }
    expected_consolidation_complete: set[str] = set()
    if consolidation["state"] == "COMPLETE":
        expected = {
            "schema_version": SCHEMA_VERSION,
            "record_type": "consolidation_complete",
            "event_id": f"complete-{consolidation['operation_id']}",
            "run_id": state["run_id"],
            "operation_id": consolidation["operation_id"],
            "evidence": consolidation["evidence"],
            "recorded_at": consolidation["completed_at"],
        }
        expected_consolidation_complete.add(expected["event_id"])
        require(expected)
    if not consolidation_complete_ids.issubset(expected_consolidation_complete):
        raise PollError("poll journal consolidation completion conflicts with state")

    pull_request = state["pull_request"]
    claim_ids = {
        record["event_id"]
        for record in records
        if record["record_type"] == "pull_request_claimed"
    }
    complete_ids = {
        record["event_id"]
        for record in records
        if record["record_type"] == "pull_request_complete"
    }
    expected_claim_ids: set[str] = set()
    expected_complete_ids: set[str] = set()
    if pull_request["state"] != "NOT_CLAIMED":
        claim = {
            "schema_version": SCHEMA_VERSION,
            "record_type": "pull_request_claimed",
            "event_id": f"claim-{pull_request['operation_id']}",
            "run_id": state["run_id"],
            "operation_id": pull_request["operation_id"],
            "authority": pull_request["authority"],
            "recorded_at": pull_request["claimed_at"],
        }
        expected_claim_ids.add(claim["event_id"])
        require(claim)
        if pull_request["state"] == "COMPLETE":
            complete = {
                "schema_version": SCHEMA_VERSION,
                "record_type": "pull_request_complete",
                "event_id": f"complete-{pull_request['operation_id']}",
                "run_id": state["run_id"],
                "operation_id": pull_request["operation_id"],
                "receipt": pull_request["receipt"],
                "recorded_at": pull_request["completed_at"],
            }
            expected_complete_ids.add(complete["event_id"])
            require(complete)
    if not claim_ids.issubset(expected_claim_ids) or not complete_ids.issubset(
        expected_complete_ids
    ):
        raise PollError("poll journal pull-request lifecycle conflicts with state")


def _reject_control_file_alias(
    candidate: Path, status_path: Path, journal_path: Path, *, label: str
) -> None:
    candidate = _safe_absolute(candidate, label=label, must_exist=True)
    status_path, journal_path, lock_path = _control_paths(status_path, journal_path)
    for control in (status_path, journal_path, lock_path):
        if candidate == control or _same_inode(candidate, control):
            raise PollError(f"{label} cannot alias poll control state")


def _verify_terminal_track_evidence(state: Mapping[str, Any]) -> None:
    for name, track in state["tracks"].items():
        if track["phase"] in TERMINAL_TRACK_PHASES and track.get("evidence") is not None:
            _verify_stored_evidence(track["evidence"], f"track {name} evidence")


def _load_state(path: Path) -> Dict[str, Any]:
    raw = _read_regular_bytes(path, label="poll status")
    try:
        value = _strict_json_loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, NonConformingJSON) as exc:
        raise PollError("poll status is invalid JSON") from exc
    return _validate_state(value)


def _is_pristine_initial_state(state: Mapping[str, Any]) -> bool:
    initialized_at = state["initialized_at"]
    return (
        state["updated_at"] == initialized_at
        and not state["trigger_outcomes"]
        and state["consolidation"] == {"state": "NOT_CLAIMED"}
        and state["pull_request"] == {"state": "NOT_CLAIMED"}
        and all(
            track
            == {
                "phase": "running",
                "update_id": "init",
                "updated_at": initialized_at,
                "reason": None,
                "evidence": None,
            }
            for track in state["tracks"].values()
        )
    )


def initialize(
    *,
    status_path: Path,
    journal_path: Path,
    run_id: str,
    dispatch_epoch: int,
    hard_ceiling_seconds: int,
    poll_interval_seconds: int,
    tracks: Sequence[str],
    now: Optional[int] = None,
    failpoint: Failpoint = None,
) -> Dict[str, Any]:
    status_path, journal_path, _ = _control_paths(status_path, journal_path)
    _identity(run_id, "run_id")
    _positive_int(dispatch_epoch, "dispatch_epoch")
    _positive_int(hard_ceiling_seconds, "hard_ceiling_seconds")
    _positive_int(poll_interval_seconds, "poll_interval_seconds")
    if (
        len(tracks) < 2
        or any(not isinstance(track, str) for track in tracks)
        or len(set(tracks)) != len(tracks)
    ):
        raise PollError("expected track list must contain two or more unique tracks")
    for track in tracks:
        _identity(track, "track name")
    observed_now = int(datetime.now(timezone.utc).timestamp()) if now is None else now
    _positive_int(observed_now, "now")
    stamp = _timestamp_from_epoch(observed_now)
    configuration: Dict[str, Any] = {
        "run_id": run_id,
        "status_path": str(status_path),
        "journal_path": str(journal_path),
        "dispatch_epoch": dispatch_epoch,
        "hard_ceiling_seconds": hard_ceiling_seconds,
        "poll_interval_seconds": poll_interval_seconds,
        "expected_tracks": list(tracks),
    }
    configuration_sha256 = _configuration_digest(configuration)
    _validate_configuration(configuration, configuration_sha256)
    init_event = {
        "schema_version": SCHEMA_VERSION,
        "record_type": "poll_initialized",
        "event_id": f"init-{run_id}",
        "run_id": run_id,
        "status_path": str(status_path),
        "journal_path": str(journal_path),
        "configuration": configuration,
        "configuration_sha256": configuration_sha256,
        "recorded_at": stamp,
    }
    with RunLock(status_path, journal_path):
        if status_path.exists() or status_path.is_symlink():
            existing = _load_state(status_path)
            if existing["configuration"] == configuration and existing[
                "configuration_sha256"
            ] == configuration_sha256:
                recovered_event = dict(init_event)
                recovered_event["recorded_at"] = existing["initialized_at"]
                records = _journal_records(journal_path, expected_run_id=run_id)
                if not records and not _is_pristine_initial_state(existing):
                    raise PollError(
                        "cannot reconstruct a missing initialization journal for advanced state"
                    )
                _ensure_journal_event(journal_path, recovered_event)
                return {"action": "ALREADY_INITIALIZED", "state": existing}
            raise PollError("existing poll status conflicts with initialization")
        state: Dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "record_type": STATE_TYPE,
            "run_id": run_id,
            "status_path": str(status_path),
            "journal_path": str(journal_path),
            "configuration": configuration,
            "configuration_sha256": configuration_sha256,
            "dispatch_epoch": dispatch_epoch,
            "hard_ceiling_seconds": hard_ceiling_seconds,
            "poll_interval_seconds": poll_interval_seconds,
            "expected_tracks": list(tracks),
            "tracks": {
                track: {
                    "phase": "running",
                    "update_id": "init",
                    "updated_at": stamp,
                    "reason": None,
                    "evidence": None,
                }
                for track in tracks
            },
            "trigger_outcomes": {},
            "consolidation": {"state": "NOT_CLAIMED"},
            "pull_request": {"state": "NOT_CLAIMED"},
            "initialized_at": stamp,
            "updated_at": stamp,
        }
        _validate_state(state)
        _preflight_journal_event(journal_path, init_event)
        _write_state_atomic(status_path, state)
        if failpoint:
            failpoint("after_state_replace")
        _ensure_journal_event(journal_path, init_event)
        return {"action": "INITIALIZED", "state": state}


def mark_track(
    *,
    status_path: Path,
    journal_path: Path,
    run_id: str,
    track: str,
    phase: str,
    reason: Optional[str],
    evidence_path: Optional[Path],
    evidence_sha256: Optional[str],
    update_id: Optional[str] = None,
    now: Optional[int] = None,
    failpoint: Failpoint = None,
) -> Dict[str, Any]:
    if phase not in TRACK_PHASES:
        raise PollError("track phase is unsupported")
    observed_now = int(datetime.now(timezone.utc).timestamp()) if now is None else now
    stamp = _timestamp_from_epoch(_positive_int(observed_now, "now"))
    with RunLock(status_path, journal_path):
        state = _load_state(status_path)
        if state["run_id"] != run_id or track not in state["tracks"]:
            raise PollError("track update does not match this run")
        if phase == "running":
            if update_id is None:
                raise PollError("running track update requires a stable update_id")
            update_id = _identity(update_id, "track update_id")
            if update_id == "init":
                raise PollError("running track update_id is reserved")
        else:
            expected_update_id = f"terminal-{phase}"
            if update_id not in {None, expected_update_id}:
                raise PollError("terminal track update_id conflicts with its phase")
            update_id = expected_update_id
        event_id = f"track-{run_id}-{track}-{update_id}"
        current = state["tracks"][track]
        recoverable = (
            current["phase"] == phase and current["update_id"] == update_id
        )
        journal_records = _bound_journal(
            state,
            status_path,
            journal_path,
            run_id=run_id,
            allow_missing_event_ids=[event_id] if recoverable else [],
        )
        evidence = None
        if phase in TERMINAL_TRACK_PHASES:
            if not isinstance(reason, str) or not reason.strip():
                raise PollError("terminal track update requires a reason")
            if evidence_path is not None or evidence_sha256 is not None:
                if evidence_path is None or evidence_sha256 is None:
                    raise PollError("track evidence path and digest must be supplied together")
                _reject_control_file_alias(
                    evidence_path, status_path, journal_path, label="track evidence"
                )
                raw = _read_regular_bytes(evidence_path, label="track evidence")
                _sha256(evidence_sha256, "track evidence SHA-256")
                if hashlib.sha256(raw).hexdigest() != evidence_sha256:
                    raise PollError("track evidence digest drifted")
                evidence = {"path": str(evidence_path), "sha256": evidence_sha256}
        elif reason is not None or evidence_path is not None or evidence_sha256 is not None:
            raise PollError("running track cannot carry terminal reason or evidence")
        existing = current
        if (
            existing["phase"] == phase
            and existing["update_id"] == update_id
            and existing["reason"] == reason
            and existing["evidence"] == evidence
        ):
            recovered_event = {
                "schema_version": SCHEMA_VERSION,
                "record_type": "track_status_recorded",
                "event_id": event_id,
                "run_id": run_id,
                "track": track,
                "update_id": update_id,
                "phase": phase,
                "reason": reason,
                "evidence": evidence,
                "recorded_at": existing["updated_at"],
            }
            _ensure_journal_event(journal_path, recovered_event)
            return {
                "action": "TRACK_UPDATE_ALREADY_RECORDED",
                "track": track,
                "phase": phase,
                "update_id": update_id,
            }
        if existing["phase"] in TERMINAL_TRACK_PHASES:
            if (
                phase == existing["phase"]
                and reason == existing["reason"]
                and evidence == existing["evidence"]
            ):
                _ensure_journal_event(
                    journal_path,
                    {
                        "schema_version": SCHEMA_VERSION,
                        "record_type": "track_status_recorded",
                        "event_id": event_id,
                        "run_id": run_id,
                        "track": track,
                        "update_id": update_id,
                        "phase": phase,
                        "reason": reason,
                        "evidence": evidence,
                        "recorded_at": existing["updated_at"],
                    },
                )
                return {"action": "TRACK_ALREADY_TERMINAL", "track": track, "state": existing}
            raise PollError("terminal track update conflicts with durable state")
        recorded = next(
            (row for row in journal_records if row["event_id"] == event_id), None
        )
        if recorded is not None:
            invariant = {
                "schema_version": SCHEMA_VERSION,
                "record_type": "track_status_recorded",
                "event_id": event_id,
                "run_id": run_id,
                "track": track,
                "update_id": update_id,
                "phase": phase,
                "reason": reason,
                "evidence": evidence,
            }
            if any(recorded.get(field) != value for field, value in invariant.items()):
                raise PollError("track update_id conflicts with durable journal")
            return {
                "action": "TRACK_UPDATE_ALREADY_RECORDED",
                "track": track,
                "phase": phase,
                "update_id": update_id,
            }
        event = {
            "schema_version": SCHEMA_VERSION,
            "record_type": "track_status_recorded",
            "event_id": event_id,
            "run_id": run_id,
            "track": track,
            "update_id": update_id,
            "phase": phase,
            "reason": reason,
            "evidence": evidence,
            "recorded_at": stamp,
        }
        _preflight_journal_event(journal_path, event)
        state["tracks"][track] = {
            "phase": phase,
            "update_id": update_id,
            "updated_at": stamp,
            "reason": reason,
            "evidence": evidence,
        }
        state["updated_at"] = stamp
        _validate_state(state)
        _write_state_atomic(status_path, state)
        if failpoint:
            failpoint("after_state_replace")
        _ensure_journal_event(journal_path, event)
        return {
            "action": "TRACK_RECORDED",
            "track": track,
            "phase": phase,
            "update_id": update_id,
        }


def poll(
    *,
    status_path: Path,
    journal_path: Path,
    run_id: str,
    trigger_id: str,
    now: Optional[int] = None,
    failpoint: Failpoint = None,
) -> Dict[str, Any]:
    _identity(trigger_id, "trigger_id")
    observed_now = int(datetime.now(timezone.utc).timestamp()) if now is None else now
    _positive_int(observed_now, "now")
    stamp = _timestamp_from_epoch(observed_now)
    with RunLock(status_path, journal_path):
        state = _load_state(status_path)
        if state["run_id"] != run_id:
            raise PollError("poll trigger belongs to another run")
        journal_records = _bound_journal(
            state,
            status_path,
            journal_path,
            run_id=run_id,
            allow_missing_event_ids=[f"trigger-{trigger_id}"],
        )
        consolidation = state["consolidation"]
        if consolidation["state"] != "NOT_CLAIMED":
            claim_event_id = f"trigger-{consolidation['claimed_by_trigger']}"
            if not any(row["event_id"] == claim_event_id for row in journal_records):
                if trigger_id != consolidation["claimed_by_trigger"]:
                    raise PollError("consolidation claim journal event requires recovery")
        _verify_terminal_track_evidence(state)
        if state["consolidation"]["state"] == "COMPLETE":
            _verify_stored_evidence(
                state["consolidation"]["evidence"], "consolidation evidence"
            )
        if state["pull_request"]["state"] in {"CLAIMED", "COMPLETE"}:
            _verify_stored_authority(state["pull_request"]["authority"], run_id=run_id)
        if state["pull_request"]["state"] == "COMPLETE":
            _verify_stored_pull_request_receipt(
                state["pull_request"]["receipt"],
                run_id=run_id,
                operation_id=state["pull_request"]["operation_id"],
            )
        existing = state["trigger_outcomes"].get(trigger_id)
        if existing is not None:
            event = {
                "schema_version": SCHEMA_VERSION,
                "record_type": "poll_outcome",
                "event_id": f"trigger-{trigger_id}",
                **existing,
            }
            _ensure_journal_event(journal_path, event)
            replay = dict(existing)
            if replay.get("action") == "CONSOLIDATION_CLAIMED":
                consolidation = state["consolidation"]
                pull_request = state["pull_request"]
                if consolidation["state"] == "CLAIMED":
                    replay["action"] = "RESUME_CONSOLIDATION_CLAIM"
                elif pull_request["state"] == "CLAIMED":
                    replay["action"] = "RESUME_PULL_REQUEST_CLAIM"
                    replay["operation_id"] = pull_request["operation_id"]
                elif pull_request["state"] == "COMPLETE":
                    replay["action"] = "COMPLETE"
                else:
                    replay["action"] = "LOCAL_COMPLETE_EXTERNAL_ACTIONS_NOT_AUTHORIZED"
            return replay
        tracks = state["tracks"]
        deadline = state["dispatch_epoch"] + state["hard_ceiling_seconds"]
        terminal = all(track["phase"] in TERMINAL_TRACK_PHASES for track in tracks.values())
        if not terminal and observed_now < deadline:
            next_epoch = min(
                observed_now + state["poll_interval_seconds"], deadline
            )
            outcome: Dict[str, Any] = {
                "action": "RESCHEDULE",
                "trigger_id": trigger_id,
                "run_id": run_id,
                "recorded_at": stamp,
                "next_trigger_id": _operation_key(run_id, f"poll-{next_epoch}"),
                "next_trigger_at": _timestamp_from_epoch(next_epoch),
                "phases": {name: track["phase"] for name, track in tracks.items()},
            }
        else:
            if not terminal:
                for track in tracks.values():
                    if track["phase"] not in TERMINAL_TRACK_PHASES:
                        track.update(
                            {
                                "phase": "tapped_out",
                                "update_id": f"ceiling-{trigger_id}",
                                "updated_at": stamp,
                                "reason": "hard wallclock ceiling reached",
                                "evidence": None,
                            }
                        )
            claim = state["consolidation"]
            operation_id = _operation_key(run_id, "consolidation")
            if claim["state"] == "NOT_CLAIMED":
                state["consolidation"] = {
                    "state": "CLAIMED",
                    "operation_id": operation_id,
                    "claimed_at": stamp,
                    "claimed_by_trigger": trigger_id,
                    "evidence": None,
                }
                action = "CONSOLIDATION_CLAIMED"
            elif claim["state"] == "CLAIMED":
                operation_id = claim["operation_id"]
                action = "RESUME_CONSOLIDATION_CLAIM"
            elif state["pull_request"]["state"] == "COMPLETE":
                _verify_stored_evidence(claim["evidence"], "consolidation evidence")
                _verify_stored_pull_request_receipt(
                    state["pull_request"]["receipt"],
                    run_id=run_id,
                    operation_id=state["pull_request"]["operation_id"],
                )
                operation_id = claim["operation_id"]
                action = "COMPLETE"
            elif state["pull_request"]["state"] == "CLAIMED":
                _verify_stored_evidence(claim["evidence"], "consolidation evidence")
                operation_id = state["pull_request"]["operation_id"]
                action = "RESUME_PULL_REQUEST_CLAIM"
            else:
                _verify_stored_evidence(claim["evidence"], "consolidation evidence")
                operation_id = claim["operation_id"]
                action = "LOCAL_COMPLETE_EXTERNAL_ACTIONS_NOT_AUTHORIZED"
            outcome = {
                "action": action,
                "trigger_id": trigger_id,
                "run_id": run_id,
                "recorded_at": stamp,
                "operation_id": operation_id,
                "phases": {name: track["phase"] for name, track in tracks.items()},
            }
        state["trigger_outcomes"][trigger_id] = outcome
        state["updated_at"] = stamp
        _validate_state(state)
        event = {
            "schema_version": SCHEMA_VERSION,
            "record_type": "poll_outcome",
            "event_id": f"trigger-{trigger_id}",
            **outcome,
        }
        _preflight_journal_event(journal_path, event)
        _write_state_atomic(status_path, state)
        if failpoint:
            failpoint("after_state_replace")
        _ensure_journal_event(journal_path, event)
        if failpoint:
            failpoint("after_journal_append")
        return outcome


def _evidence_identity(path: Path, sha256: str, label: str) -> Dict[str, str]:
    raw = _read_regular_bytes(path, label=label)
    if not raw:
        raise PollError(f"{label} must be nonempty")
    _sha256(sha256, f"{label} SHA-256")
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise PollError(f"{label} digest drifted")
    return {"path": str(path), "sha256": sha256}


def _pull_request_receipt_identity(
    path: Path, sha256: str, *, run_id: str, operation_id: str
) -> Dict[str, str]:
    identity = _evidence_identity(path, sha256, "pull-request receipt")
    raw = _read_regular_bytes(path, label="pull-request receipt")
    try:
        receipt = _strict_json_loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, NonConformingJSON) as exc:
        raise PollError("pull-request receipt must be structured JSON") from exc
    if (
        not isinstance(receipt, dict)
        or set(receipt) != PR_RECEIPT_FIELDS
        or type(receipt.get("schema_version")) is not int
        or receipt["schema_version"] != EXTERNAL_SCHEMA_VERSION
        or receipt.get("record_type") != PR_RECEIPT_TYPE
        or receipt.get("run_id") != run_id
        or receipt.get("operation_id") != operation_id
        or receipt.get("idempotency_key") != operation_id
        or not isinstance(receipt.get("repository"), str)
        or not REPOSITORY_RE.fullmatch(receipt["repository"])
        or not isinstance(receipt.get("url"), str)
        or not HTTPS_URL_RE.fullmatch(receipt["url"])
        or receipt.get("state") not in {"OPEN", "EXISTING_OPEN"}
        or not _valid_timestamp(receipt.get("recorded_at"))
    ):
        raise PollError("pull-request receipt does not bind this operation")
    _identity(receipt.get("provider"), "pull-request receipt provider")
    _identity(receipt.get("pull_request_id"), "pull-request receipt identity")
    return identity


def _verify_stored_pull_request_receipt(
    value: Any, *, run_id: str, operation_id: str
) -> None:
    _validate_evidence(value, "pull-request receipt")
    assert isinstance(value, dict)
    reproduced = _pull_request_receipt_identity(
        Path(value["path"]),
        value["sha256"],
        run_id=run_id,
        operation_id=operation_id,
    )
    if reproduced != value:
        raise PollError("pull-request receipt identity drifted")


def complete_consolidation(
    *,
    status_path: Path,
    journal_path: Path,
    run_id: str,
    operation_id: str,
    evidence_path: Path,
    evidence_sha256: str,
    now: Optional[int] = None,
    failpoint: Failpoint = None,
) -> Dict[str, Any]:
    observed_now = int(datetime.now(timezone.utc).timestamp()) if now is None else now
    stamp = _timestamp_from_epoch(_positive_int(observed_now, "now"))
    with RunLock(status_path, journal_path):
        state = _load_state(status_path)
        recovery_event_id = f"complete-{operation_id}"
        journal_records = _bound_journal(
            state,
            status_path,
            journal_path,
            run_id=run_id,
            allow_missing_event_ids=[recovery_event_id]
            if state["consolidation"].get("operation_id") == operation_id
            and state["consolidation"]["state"] == "COMPLETE"
            else [],
        )
        _reject_control_file_alias(
            evidence_path,
            status_path,
            journal_path,
            label="consolidation evidence",
        )
        evidence = _evidence_identity(
            evidence_path, evidence_sha256, "consolidation evidence"
        )
        claim = state["consolidation"]
        if state["run_id"] != run_id or claim.get("operation_id") != operation_id:
            raise PollError("consolidation completion does not match durable claim")
        claim_event_id = f"trigger-{claim.get('claimed_by_trigger')}"
        if not any(row["event_id"] == claim_event_id for row in journal_records):
            raise PollError("consolidation claim journal event requires recovery")
        if claim["state"] == "COMPLETE":
            if claim.get("evidence") != evidence:
                raise PollError("consolidation completion conflicts with retry")
            _verify_stored_evidence(claim["evidence"], "consolidation evidence")
            _ensure_journal_event(
                journal_path,
                {
                    "schema_version": SCHEMA_VERSION,
                    "record_type": "consolidation_complete",
                    "event_id": f"complete-{operation_id}",
                    "run_id": run_id,
                    "operation_id": operation_id,
                    "evidence": evidence,
                    "recorded_at": claim["completed_at"],
                },
            )
            return {"action": "CONSOLIDATION_ALREADY_COMPLETE", "operation_id": operation_id}
        if claim["state"] != "CLAIMED":
            raise PollError("consolidation must be claimed before completion")
        event = {
            "schema_version": SCHEMA_VERSION,
            "record_type": "consolidation_complete",
            "event_id": f"complete-{operation_id}",
            "run_id": run_id,
            "operation_id": operation_id,
            "evidence": evidence,
            "recorded_at": stamp,
        }
        _preflight_journal_event(journal_path, event)
        claim.update({"state": "COMPLETE", "completed_at": stamp, "evidence": evidence})
        state["updated_at"] = stamp
        _validate_state(state)
        _write_state_atomic(status_path, state)
        if failpoint:
            failpoint("after_state_replace")
        _ensure_journal_event(journal_path, event)
        return {
            "action": "LOCAL_COMPLETE_EXTERNAL_ACTIONS_NOT_AUTHORIZED",
            "operation_id": operation_id,
            "missing_authority": sorted(PR_REQUIRED_GRANTS),
        }


def _authority_receipt(path: Path, *, run_id: str) -> Dict[str, Any]:
    raw = _read_regular_bytes(path, label="external-action authority receipt")
    try:
        value = _strict_json_loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, NonConformingJSON) as exc:
        raise PollError("external-action authority receipt is invalid JSON") from exc
    required = {
        "schema_version",
        "record_type",
        "run_id",
        "authorized_by",
        "recorded_at",
        "grants",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise PollError("external-action authority receipt has wrong fields")
    if (
        value["schema_version"] != EXTERNAL_SCHEMA_VERSION
        or value["record_type"] != AUTHORITY_TYPE
        or value["run_id"] != run_id
        or not _valid_timestamp(value["recorded_at"])
        or not isinstance(value["authorized_by"], str)
        or not value["authorized_by"].strip()
        or not isinstance(value["grants"], dict)
        or set(value["grants"]) != GRANT_FIELDS
        or any(not isinstance(grant, bool) for grant in value["grants"].values())
    ):
        raise PollError("external-action authority receipt is malformed")
    return {
        "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "authorized_by": value["authorized_by"],
        "grants": value["grants"],
    }


def _validate_action_decision(value: Any) -> Dict[str, Any]:
    fields = {
        "schema_version",
        "record_type",
        "run_id",
        "action",
        "authority",
        "result",
        "callable",
        "missing_grants",
        "recorded_at",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise PollError("external-action decision has schema drift")
    action = value.get("action")
    if (
        type(value.get("schema_version")) is not int
        or value["schema_version"] != EXTERNAL_SCHEMA_VERSION
        or value.get("record_type") != DECISION_TYPE
        or action not in ACTION_REQUIREMENTS
        or value.get("result") not in {"AUTHORIZED", "MISSING_AUTHORITY"}
        or not isinstance(value.get("callable"), bool)
        or not _valid_timestamp(value.get("recorded_at"))
    ):
        raise PollError("external-action decision is malformed")
    _identity(value.get("run_id"), "external-action decision run_id")
    authority = value.get("authority")
    if authority is not None:
        if not isinstance(authority, dict):
            raise PollError("external-action decision authority is malformed")
        _validate_authority_identity_without_required_grants(authority)
    missing = value.get("missing_grants")
    if (
        not isinstance(missing, list)
        or missing != sorted(set(missing))
        or any(grant not in GRANT_FIELDS for grant in missing)
        or value["callable"] != (value["result"] == "AUTHORIZED")
        or value["callable"] != (not missing)
    ):
        raise PollError("external-action decision result is inconsistent")
    return value


def _validate_authority_identity_without_required_grants(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {
        "path",
        "sha256",
        "authorized_by",
        "grants",
    }:
        raise PollError("external-action authority identity is malformed")
    _safe_absolute(Path(str(value.get("path"))), label="external-action authority path")
    _sha256(value.get("sha256"), "external-action authority SHA-256")
    if not isinstance(value.get("authorized_by"), str) or not value["authorized_by"].strip():
        raise PollError("external-action authority owner is malformed")
    grants = value.get("grants")
    if (
        not isinstance(grants, dict)
        or set(grants) != GRANT_FIELDS
        or any(not isinstance(grant, bool) for grant in grants.values())
    ):
        raise PollError("external-action authority grants are malformed")


def _write_action_decision_once(path: Path, value: Mapping[str, Any]) -> Dict[str, Any]:
    path = _safe_absolute(path, label="external-action decision")
    encoded = _canonical_json(value)
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except FileExistsError:
        try:
            existing = _validate_action_decision(
                _strict_json_loads(_read_regular_bytes(path, label="external-action decision"))
            )
        except (UnicodeError, json.JSONDecodeError, NonConformingJSON) as exc:
            raise PollError("external-action decision is invalid JSON") from exc
        invariant = {key: item for key, item in value.items() if key != "recorded_at"}
        existing_invariant = {
            key: item for key, item in existing.items() if key != "recorded_at"
        }
        if existing_invariant != invariant:
            raise PollError("existing external-action decision conflicts with retry")
        return existing
    try:
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise PollError("external-action decision write made no progress")
            view = view[written:]
        os.fsync(descriptor)
        if os.fstat(descriptor).st_nlink != 1:
            raise PollError("external-action decision gained a hard link")
    finally:
        os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return dict(value)


def decide_external_action(
    *,
    run_id: str,
    action: str,
    decision_output: Path,
    authority_receipt_path: Optional[Path] = None,
) -> Dict[str, Any]:
    run_id = _identity(run_id, "run_id")
    if action not in ACTION_REQUIREMENTS:
        raise PollError("external action is unsupported")
    authority = (
        _authority_receipt(authority_receipt_path, run_id=run_id)
        if authority_receipt_path is not None
        else None
    )
    grants = authority["grants"] if authority is not None else {}
    missing = sorted(
        grant for grant in ACTION_REQUIREMENTS[action] if grants.get(grant) is not True
    )
    decision = {
        "schema_version": EXTERNAL_SCHEMA_VERSION,
        "record_type": DECISION_TYPE,
        "run_id": run_id,
        "action": action,
        "authority": authority,
        "result": "MISSING_AUTHORITY" if missing else "AUTHORIZED",
        "callable": not missing,
        "missing_grants": missing,
        "recorded_at": utc_now(),
    }
    _validate_action_decision(decision)
    durable = _write_action_decision_once(decision_output, decision)
    return {"action": action, "result": durable["result"], "decision": durable}


def run_guarded_action(
    *,
    decision: Mapping[str, Any],
    action: str,
    callback: Callable[[str], Any],
) -> Dict[str, Any]:
    """Invoke ``callback`` once only when ``decision`` authorizes ``action``.

    The grant calculation and authority bytes are rechecked at the action point
    so callers cannot turn a stale, partial, forged, or different-action
    decision into an external call merely by branching on ``callable``.
    """

    if action not in ACTION_REQUIREMENTS:
        raise PollError("guarded external action is unsupported")
    if not callable(callback):
        raise PollError("guarded external action callback must be callable")
    durable = _validate_action_decision(dict(decision))
    if durable["action"] != action:
        raise PollError("guarded external action does not match its durable decision")

    authority = durable["authority"]
    if authority is None:
        grants: Mapping[str, bool] = {}
    else:
        reproduced = _authority_receipt(
            Path(authority["path"]), run_id=durable["run_id"]
        )
        if reproduced != authority:
            raise PollError("guarded external-action authority receipt drifted")
        grants = reproduced["grants"]
    missing = sorted(
        grant for grant in ACTION_REQUIREMENTS[action] if grants.get(grant) is not True
    )
    if durable["missing_grants"] != missing:
        raise PollError("guarded external-action decision does not match required grants")
    if missing:
        if durable["result"] != "MISSING_AUTHORITY" or durable["callable"]:
            raise PollError("guarded external-action denial is inconsistent")
        return {"action": action, "result": "MISSING_AUTHORITY", "called": False}
    if durable["result"] != "AUTHORIZED" or not durable["callable"]:
        raise PollError("guarded external-action authorization is inconsistent")

    callback(action)
    return {"action": action, "result": "AUTHORIZED", "called": True}


def _verify_stored_authority(value: Any, *, run_id: str) -> None:
    _validate_authority_identity(value)
    assert isinstance(value, dict)
    reproduced = _authority_receipt(Path(value["path"]), run_id=run_id)
    if reproduced != value:
        raise PollError("pull-request authority receipt drifted")


def claim_pull_request(
    *,
    status_path: Path,
    journal_path: Path,
    run_id: str,
    decision_output: Path,
    authority_receipt_path: Optional[Path] = None,
    now: Optional[int] = None,
) -> Dict[str, Any]:
    observed_now = int(datetime.now(timezone.utc).timestamp()) if now is None else now
    stamp = _timestamp_from_epoch(_positive_int(observed_now, "now"))
    with RunLock(status_path, journal_path):
        state = _load_state(status_path)
        if state["run_id"] != run_id or state["consolidation"]["state"] != "COMPLETE":
            raise PollError("pull request requires completed local consolidation")
        recovery_operation_id = _operation_key(run_id, "pull-request")
        journal_records = _bound_journal(
            state,
            status_path,
            journal_path,
            run_id=run_id,
            allow_missing_event_ids=[f"claim-{recovery_operation_id}"]
            if state["pull_request"].get("operation_id") == recovery_operation_id
            else [],
        )
        consolidation_event = f"complete-{state['consolidation']['operation_id']}"
        if not any(row["event_id"] == consolidation_event for row in journal_records):
            raise PollError("consolidation completion journal event requires recovery")
        _verify_stored_evidence(
            state["consolidation"]["evidence"], "consolidation evidence"
        )
        action_decision = decide_external_action(
            run_id=run_id,
            action="pull-request",
            decision_output=decision_output,
            authority_receipt_path=authority_receipt_path,
        )
        if action_decision["result"] != "AUTHORIZED":
            return {
                "action": "MISSING_AUTHORITY",
                "operation_id": _operation_key(run_id, "pull-request"),
                "decision": action_decision["decision"],
            }
        authority = action_decision["decision"]["authority"]
        assert isinstance(authority, dict)
        claim = state["pull_request"]
        operation_id = _operation_key(run_id, "pull-request")
        if claim["state"] != "NOT_CLAIMED":
            if claim.get("operation_id") != operation_id or claim.get("authority") != authority:
                raise PollError("pull-request claim conflicts with retry")
            _verify_stored_authority(claim["authority"], run_id=run_id)
            if claim["state"] == "COMPLETE":
                _verify_stored_pull_request_receipt(
                    claim["receipt"], run_id=run_id, operation_id=operation_id
                )
            _ensure_journal_event(
                journal_path,
                {
                    "schema_version": SCHEMA_VERSION,
                    "record_type": "pull_request_claimed",
                    "event_id": f"claim-{operation_id}",
                    "run_id": run_id,
                    "operation_id": operation_id,
                    "authority": authority,
                    "recorded_at": claim["claimed_at"],
                },
            )
            return {
                "action": "RESUME_PULL_REQUEST_CLAIM"
                if claim["state"] == "CLAIMED"
                else "PULL_REQUEST_ALREADY_COMPLETE",
                "operation_id": operation_id,
                "idempotency_key": operation_id,
            }
        state["pull_request"] = {
            "state": "CLAIMED",
            "operation_id": operation_id,
            "claimed_at": stamp,
            "authority": authority,
            "receipt": None,
        }
        state["updated_at"] = stamp
        _validate_state(state)
        event = {
            "schema_version": SCHEMA_VERSION,
            "record_type": "pull_request_claimed",
            "event_id": f"claim-{operation_id}",
            "run_id": run_id,
            "operation_id": operation_id,
            "authority": authority,
            "recorded_at": stamp,
        }
        _preflight_journal_event(journal_path, event)
        _write_state_atomic(status_path, state)
        _ensure_journal_event(journal_path, event)
        return {
            "action": "CHECK_OR_CREATE_PULL_REQUEST",
            "operation_id": operation_id,
            "idempotency_key": operation_id,
        }


def complete_pull_request(
    *,
    status_path: Path,
    journal_path: Path,
    run_id: str,
    operation_id: str,
    receipt_path: Path,
    receipt_sha256: str,
    now: Optional[int] = None,
) -> Dict[str, Any]:
    observed_now = int(datetime.now(timezone.utc).timestamp()) if now is None else now
    stamp = _timestamp_from_epoch(_positive_int(observed_now, "now"))
    with RunLock(status_path, journal_path):
        state = _load_state(status_path)
        journal_records = _bound_journal(
            state,
            status_path,
            journal_path,
            run_id=run_id,
            allow_missing_event_ids=[f"complete-{operation_id}"]
            if state["pull_request"].get("operation_id") == operation_id
            and state["pull_request"]["state"] == "COMPLETE"
            else [],
        )
        _reject_control_file_alias(
            receipt_path,
            status_path,
            journal_path,
            label="pull-request receipt",
        )
        receipt = _pull_request_receipt_identity(
            receipt_path,
            receipt_sha256,
            run_id=run_id,
            operation_id=operation_id,
        )
        claim = state["pull_request"]
        if state["run_id"] != run_id or claim.get("operation_id") != operation_id:
            raise PollError("pull-request completion does not match durable claim")
        claim_event_id = f"claim-{operation_id}"
        if not any(row["event_id"] == claim_event_id for row in journal_records):
            raise PollError("pull-request claim journal event requires recovery")
        _verify_stored_evidence(
            state["consolidation"]["evidence"], "consolidation evidence"
        )
        _verify_stored_authority(claim.get("authority"), run_id=run_id)
        if claim["state"] == "COMPLETE":
            if claim.get("receipt") != receipt:
                raise PollError("pull-request completion conflicts with retry")
            _verify_stored_pull_request_receipt(
                claim["receipt"], run_id=run_id, operation_id=operation_id
            )
            _ensure_journal_event(
                journal_path,
                {
                    "schema_version": SCHEMA_VERSION,
                    "record_type": "pull_request_complete",
                    "event_id": f"complete-{operation_id}",
                    "run_id": run_id,
                    "operation_id": operation_id,
                    "receipt": receipt,
                    "recorded_at": claim["completed_at"],
                },
            )
            return {"action": "PULL_REQUEST_ALREADY_COMPLETE", "operation_id": operation_id}
        if claim["state"] != "CLAIMED":
            raise PollError("pull request must be claimed before completion")
        event = {
            "schema_version": SCHEMA_VERSION,
            "record_type": "pull_request_complete",
            "event_id": f"complete-{operation_id}",
            "run_id": run_id,
            "operation_id": operation_id,
            "receipt": receipt,
            "recorded_at": stamp,
        }
        _preflight_journal_event(journal_path, event)
        claim.update({"state": "COMPLETE", "completed_at": stamp, "receipt": receipt})
        state["updated_at"] = stamp
        _validate_state(state)
        _write_state_atomic(status_path, state)
        _ensure_journal_event(journal_path, event)
        return {"action": "COMPLETE", "operation_id": operation_id}


def inspect(*, status_path: Path, run_id: str) -> Dict[str, Any]:
    initial = _load_state(status_path)
    journal_path = Path(initial["journal_path"])
    with RunLock(status_path, journal_path):
        state = _load_state(status_path)
        if state["run_id"] != run_id:
            raise PollError("inspect belongs to another run")
        _bound_journal(state, status_path, journal_path, run_id=run_id)
        return {"action": "INSPECT", "state": state}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--status", required=True, type=Path)
    init.add_argument("--journal", required=True, type=Path)
    init.add_argument("--run-id", required=True)
    init.add_argument("--dispatch-epoch", required=True, type=int)
    init.add_argument("--hard-ceiling-seconds", required=True, type=int)
    init.add_argument("--poll-interval-seconds", required=True, type=int)
    init.add_argument("--track", action="append", required=True)
    mark = commands.add_parser("mark-track")
    mark.add_argument("--status", required=True, type=Path)
    mark.add_argument("--journal", required=True, type=Path)
    mark.add_argument("--run-id", required=True)
    mark.add_argument("--track", required=True)
    mark.add_argument("--phase", choices=sorted(TRACK_PHASES), required=True)
    mark.add_argument(
        "--update-id",
        help="stable idempotency key required for a running heartbeat",
    )
    mark.add_argument("--reason")
    mark.add_argument("--evidence-path", type=Path)
    mark.add_argument("--evidence-sha256")
    poll_parser = commands.add_parser("poll")
    poll_parser.add_argument("--status", required=True, type=Path)
    poll_parser.add_argument("--journal", required=True, type=Path)
    poll_parser.add_argument("--run-id", required=True)
    poll_parser.add_argument("--trigger-id", required=True)
    consolidate = commands.add_parser("complete-consolidation")
    consolidate.add_argument("--status", required=True, type=Path)
    consolidate.add_argument("--journal", required=True, type=Path)
    consolidate.add_argument("--run-id", required=True)
    consolidate.add_argument("--operation-id", required=True)
    consolidate.add_argument("--evidence-path", required=True, type=Path)
    consolidate.add_argument("--evidence-sha256", required=True)
    claim_pr = commands.add_parser("claim-pr")
    claim_pr.add_argument("--status", required=True, type=Path)
    claim_pr.add_argument("--journal", required=True, type=Path)
    claim_pr.add_argument("--run-id", required=True)
    claim_pr.add_argument("--decision-output", required=True, type=Path)
    claim_pr.add_argument("--authority-receipt", type=Path)
    decide_action = commands.add_parser("decide-action")
    decide_action.add_argument("--run-id", required=True)
    decide_action.add_argument("--action", choices=sorted(ACTION_REQUIREMENTS), required=True)
    decide_action.add_argument("--decision-output", required=True, type=Path)
    decide_action.add_argument("--authority-receipt", type=Path)
    complete_pr = commands.add_parser("complete-pr")
    complete_pr.add_argument("--status", required=True, type=Path)
    complete_pr.add_argument("--journal", required=True, type=Path)
    complete_pr.add_argument("--run-id", required=True)
    complete_pr.add_argument("--operation-id", required=True)
    complete_pr.add_argument("--receipt-path", required=True, type=Path)
    complete_pr.add_argument("--receipt-sha256", required=True)
    inspect_parser = commands.add_parser("inspect")
    inspect_parser.add_argument("--status", required=True, type=Path)
    inspect_parser.add_argument("--run-id", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "init":
            result = initialize(
                status_path=args.status,
                journal_path=args.journal,
                run_id=args.run_id,
                dispatch_epoch=args.dispatch_epoch,
                hard_ceiling_seconds=args.hard_ceiling_seconds,
                poll_interval_seconds=args.poll_interval_seconds,
                tracks=args.track,
            )
        elif args.command == "mark-track":
            result = mark_track(
                status_path=args.status,
                journal_path=args.journal,
                run_id=args.run_id,
                track=args.track,
                phase=args.phase,
                reason=args.reason,
                evidence_path=args.evidence_path,
                evidence_sha256=args.evidence_sha256,
                update_id=args.update_id,
            )
        elif args.command == "poll":
            result = poll(
                status_path=args.status,
                journal_path=args.journal,
                run_id=args.run_id,
                trigger_id=args.trigger_id,
            )
        elif args.command == "complete-consolidation":
            result = complete_consolidation(
                status_path=args.status,
                journal_path=args.journal,
                run_id=args.run_id,
                operation_id=args.operation_id,
                evidence_path=args.evidence_path,
                evidence_sha256=args.evidence_sha256,
            )
        elif args.command == "claim-pr":
            result = claim_pull_request(
                status_path=args.status,
                journal_path=args.journal,
                run_id=args.run_id,
                decision_output=args.decision_output,
                authority_receipt_path=args.authority_receipt,
            )
        elif args.command == "decide-action":
            result = decide_external_action(
                run_id=args.run_id,
                action=args.action,
                decision_output=args.decision_output,
                authority_receipt_path=args.authority_receipt,
            )
        elif args.command == "complete-pr":
            result = complete_pull_request(
                status_path=args.status,
                journal_path=args.journal,
                run_id=args.run_id,
                operation_id=args.operation_id,
                receipt_path=args.receipt_path,
                receipt_sha256=args.receipt_sha256,
            )
        elif args.command == "inspect":
            result = inspect(status_path=args.status, run_id=args.run_id)
        else:
            raise PollError(f"unknown command: {args.command}")
    except (PollError, OSError) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
