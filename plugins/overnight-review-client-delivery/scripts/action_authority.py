#!/usr/bin/env python3
"""Fail-closed point-of-action authority decisions for client delivery.

The command-line interface records whether one requested action is callable.
Loaded callers can pass an action callback to :func:`run_guarded_action`; that
boundary invokes the callback only after revalidating the exact decision and
its authority receipt.  The module has no built-in commit, push, pull-request,
merge, deploy, paid-call, network, or external-write implementation.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Callable, Dict, Mapping, Optional, Sequence


SCHEMA_VERSION = 1
AUTHORITY_TYPE = "client_delivery_action_authority"
DECISION_TYPE = "client_delivery_action_decision"
GRANT_FIELDS = {
    "commit",
    "push",
    "pull_request",
    "merge",
    "deploy",
    "network",
    "paid_call",
    "external_write",
}
ACTION_REQUIREMENTS = {
    "commit": {"commit"},
    "push": {"push", "network", "external_write"},
    "pull-request": {"pull_request", "network", "external_write"},
    "merge": {"merge", "network", "external_write"},
    "deploy": {"deploy", "network", "external_write"},
    "paid-call": {"paid_call", "network"},
    "external-write": {"external_write", "network"},
}
AUTHORITY_FIELDS = {
    "schema_version",
    "record_type",
    "review_id",
    "authorized_by",
    "recorded_at",
    "grants",
}
DECISION_FIELDS = {
    "schema_version",
    "record_type",
    "review_id",
    "action",
    "authority",
    "result",
    "callable",
    "missing_grants",
    "recorded_at",
}
AUTHORITY_IDENTITY_FIELDS = {"path", "sha256", "authorized_by", "recorded_at", "grants"}
IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/+\-]{0,255}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
UTC_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z"
)


class NonConformingJSON(ValueError):
    """These bytes do not have one meaning, so no gate may act on them.

    Two ways ``json.loads`` will hand back a confident answer where another
    conforming parser would hand back a different one, or none:

    * a repeated key -- RFC 8259 leaves the outcome undefined, Python keeps the
      last, and an implementation that keeps the first reads the same bytes as
      a different document;
    * ``NaN`` / ``Infinity`` / ``-Infinity`` -- a Python extension, not JSON at
      all, which a conforming parser rejects outright.

    Every decode routed through ``_strict_json_loads`` feeds a gate, so both are
    refused rather than resolved.
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

    return json.loads(
        text,
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=reject_non_json_constant,
    )


class AuthorityError(RuntimeError):
    """Authority evidence or the durable decision is unsafe or malformed."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _valid_utc(value: Any) -> bool:
    if not isinstance(value, str) or not UTC_RE.fullmatch(value):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0


def _identity(value: Any, label: str) -> str:
    if not isinstance(value, str) or not IDENTITY_RE.fullmatch(value):
        raise AuthorityError(f"{label} must be a normalized identity")
    return value


def _absolute(path: Path, *, label: str, must_exist: bool = False) -> Path:
    path = Path(path)
    rendered = str(path)
    if (
        not path.is_absolute()
        or os.path.normpath(rendered) != rendered
        or any(ord(character) < 32 or ord(character) == 127 for character in rendered)
    ):
        raise AuthorityError(f"{label} must be a normalized absolute path")
    current = Path(path.anchor)
    missing = False
    for component in path.parts[1:]:
        if component in {"", ".", ".."}:
            raise AuthorityError(f"{label} has an unsafe component")
        current = current / component
        try:
            observed = os.lstat(current)
        except FileNotFoundError:
            missing = True
            continue
        if missing:
            raise AuthorityError(f"{label} has an existing child below a missing parent")
        if stat.S_ISLNK(observed.st_mode):
            raise AuthorityError(f"{label} has a symlink component")
        if current != path and not stat.S_ISDIR(observed.st_mode):
            raise AuthorityError(f"{label} parent is not a directory")
    if must_exist and not path.exists():
        raise AuthorityError(f"{label} does not exist")
    return path


def _regular_bytes(path: Path, *, label: str) -> bytes:
    path = _absolute(path, label=label, must_exist=True)
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise AuthorityError(f"{label} must be a regular single-link file")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (
            (before.st_dev, before.st_ino, before.st_size)
            != (opened.st_dev, opened.st_ino, opened.st_size)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
        ):
            raise AuthorityError(f"{label} changed before open")
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
            or after.st_nlink != 1
            or len(data) != after.st_size
        ):
            raise AuthorityError(f"{label} changed while read")
        return data
    finally:
        os.close(descriptor)


def _load_authority(path: Path, *, review_id: str) -> Dict[str, Any]:
    data = _regular_bytes(path, label="client-delivery authority receipt")
    try:
        value = _strict_json_loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, NonConformingJSON) as exc:
        raise AuthorityError("client-delivery authority receipt is invalid JSON") from exc
    if not isinstance(value, dict) or set(value) != AUTHORITY_FIELDS:
        raise AuthorityError("client-delivery authority receipt has schema drift")
    grants = value.get("grants")
    if (
        type(value.get("schema_version")) is not int
        or value["schema_version"] != SCHEMA_VERSION
        or value.get("record_type") != AUTHORITY_TYPE
        or value.get("review_id") != review_id
        or not isinstance(value.get("authorized_by"), str)
        or not value["authorized_by"].strip()
        or not _valid_utc(value.get("recorded_at"))
        or not isinstance(grants, dict)
        or set(grants) != GRANT_FIELDS
        or any(not isinstance(grant, bool) for grant in grants.values())
    ):
        raise AuthorityError("client-delivery authority receipt is malformed")
    return {
        "path": str(path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "authorized_by": value["authorized_by"],
        "recorded_at": value["recorded_at"],
        "grants": grants,
    }


def _validate_decision(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) != DECISION_FIELDS:
        raise AuthorityError("client-delivery action decision has schema drift")
    if (
        type(value.get("schema_version")) is not int
        or value["schema_version"] != SCHEMA_VERSION
        or value.get("record_type") != DECISION_TYPE
        or value.get("action") not in ACTION_REQUIREMENTS
        or value.get("result") not in {"AUTHORIZED", "MISSING_AUTHORITY"}
        or not isinstance(value.get("callable"), bool)
        or not _valid_utc(value.get("recorded_at"))
    ):
        raise AuthorityError("client-delivery action decision is malformed")
    _identity(value.get("review_id"), "decision review_id")
    authority = value.get("authority")
    if authority is not None:
        if not isinstance(authority, dict) or set(authority) != AUTHORITY_IDENTITY_FIELDS:
            raise AuthorityError("client-delivery decision authority identity is malformed")
        if not isinstance(authority.get("path"), str):
            raise AuthorityError("client-delivery decision authority path is malformed")
        _absolute(Path(authority["path"]), label="decision authority receipt")
        if not isinstance(authority.get("sha256"), str) or not SHA256_RE.fullmatch(
            authority["sha256"]
        ):
            raise AuthorityError("client-delivery decision authority digest is invalid")
        if (
            not isinstance(authority.get("authorized_by"), str)
            or not authority["authorized_by"].strip()
            or not _valid_utc(authority.get("recorded_at"))
            or not isinstance(authority.get("grants"), dict)
            or set(authority["grants"]) != GRANT_FIELDS
            or any(not isinstance(grant, bool) for grant in authority["grants"].values())
        ):
            raise AuthorityError("client-delivery decision authority grants are malformed")
    missing = value.get("missing_grants")
    if (
        not isinstance(missing, list)
        or missing != sorted(set(missing))
        or any(grant not in GRANT_FIELDS for grant in missing)
        or value["callable"] != (value["result"] == "AUTHORIZED")
        or value["callable"] != (not missing)
    ):
        raise AuthorityError("client-delivery action decision result is inconsistent")
    return value


def _write_once(path: Path, value: Mapping[str, Any]) -> Dict[str, Any]:
    path = _absolute(path, label="client-delivery action decision")
    encoded = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except FileExistsError:
        existing = _validate_decision(
            _strict_json_loads(_regular_bytes(path, label="client-delivery action decision"))
        )
        invariant = {key: value for key, value in value.items() if key != "recorded_at"}
        existing_invariant = {
            key: observed for key, observed in existing.items() if key != "recorded_at"
        }
        if existing_invariant != invariant:
            raise AuthorityError("existing client-delivery action decision conflicts")
        return existing
    try:
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise AuthorityError("client-delivery decision write made no progress")
            view = view[written:]
        os.fsync(descriptor)
        if os.fstat(descriptor).st_nlink != 1:
            raise AuthorityError("client-delivery action decision gained a hard link")
    finally:
        os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return dict(value)


def decide(
    *,
    review_id: str,
    action: str,
    decision_output: Path,
    authority_receipt: Optional[Path] = None,
) -> Dict[str, Any]:
    review_id = _identity(review_id, "review_id")
    if action not in ACTION_REQUIREMENTS:
        raise AuthorityError("requested action is unsupported")
    authority = (
        _load_authority(authority_receipt, review_id=review_id)
        if authority_receipt is not None
        else None
    )
    grants = authority["grants"] if authority is not None else {}
    missing = sorted(
        grant for grant in ACTION_REQUIREMENTS[action] if grants.get(grant) is not True
    )
    result = "MISSING_AUTHORITY" if missing else "AUTHORIZED"
    decision = {
        "schema_version": SCHEMA_VERSION,
        "record_type": DECISION_TYPE,
        "review_id": review_id,
        "action": action,
        "authority": authority,
        "result": result,
        "callable": not missing,
        "missing_grants": missing,
        "recorded_at": utc_now(),
    }
    _validate_decision(decision)
    durable = _write_once(decision_output, decision)
    return {"action": action, "result": durable["result"], "decision": durable}


def run_guarded_action(
    *,
    decision: Mapping[str, Any],
    action: str,
    callback: Callable[[str], Any],
) -> Dict[str, Any]:
    """Invoke ``callback`` once only when ``decision`` authorizes ``action``.

    Recomputing the required grants here makes this the point-of-action guard,
    rather than trusting a caller's branch on the serialized ``callable`` bit.
    The authority file is reread and its complete identity must still equal the
    one captured by :func:`decide`.
    """

    if action not in ACTION_REQUIREMENTS:
        raise AuthorityError("guarded action is unsupported")
    if not callable(callback):
        raise AuthorityError("guarded action callback must be callable")
    durable = _validate_decision(dict(decision))
    if durable["action"] != action:
        raise AuthorityError("guarded action does not match its durable decision")

    authority = durable["authority"]
    if authority is None:
        grants: Mapping[str, bool] = {}
    else:
        reproduced = _load_authority(
            Path(authority["path"]), review_id=durable["review_id"]
        )
        if reproduced != authority:
            raise AuthorityError("guarded action authority receipt drifted")
        grants = reproduced["grants"]
    missing = sorted(
        grant for grant in ACTION_REQUIREMENTS[action] if grants.get(grant) is not True
    )
    if durable["missing_grants"] != missing:
        raise AuthorityError("guarded action decision does not match required grants")
    if missing:
        if durable["result"] != "MISSING_AUTHORITY" or durable["callable"]:
            raise AuthorityError("guarded action denial is inconsistent")
        return {"action": action, "result": "MISSING_AUTHORITY", "called": False}
    if durable["result"] != "AUTHORIZED" or not durable["callable"]:
        raise AuthorityError("guarded action authorization is inconsistent")

    callback(action)
    return {"action": action, "result": "AUTHORIZED", "called": True}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-id", required=True)
    parser.add_argument("--action", required=True, choices=sorted(ACTION_REQUIREMENTS))
    parser.add_argument("--decision-output", required=True, type=Path)
    parser.add_argument("--authority-receipt", type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = decide(
            review_id=args.review_id,
            action=args.action,
            decision_output=args.decision_output,
            authority_receipt=args.authority_receipt,
        )
    except (AuthorityError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result["result"] == "AUTHORIZED" else 3


if __name__ == "__main__":
    raise SystemExit(main())
