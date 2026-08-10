#!/usr/bin/env python3
"""Canonical append-only finalization-manifest grammar and controller writer.

The publisher and the finalization controller share this module.  A manifest
has one logical writer identity even when the publisher is synchronously
invoked as one of that controller's tools.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

if __package__:
    from .install_inventory import PublicationError, parse_inventory
    from .validate_panel_inputs import validate_panel_input
else:
    from install_inventory import PublicationError, parse_inventory
    from validate_panel_inputs import validate_panel_input


LEGACY_SCHEMA_VERSION = 1
LEGACY_MANIFEST_SCHEMA = "doodlerun-overnight-finalization-v1"
SCHEMA_VERSION = 2
MANIFEST_SCHEMA = "overnight-finalization-v2"
PREFIX_RECEIPT_TYPE = "manifest_prefix_receipt"
PHASE_ORDER = ("dispatch", "judgment", "acceptance")
JUDGE_RECEIPT_TYPE = "finalization_judge_verdict"
PREPUBLICATION_SOURCE_REVIEW_BOUNDARY = "prepublication-source-and-staged-snapshot"
POSTPUBLICATION_REVIEW_BOUNDARY = "postpublication-installed-snapshot"
JUDGE_RECEIPT_FIELDS = {
    "schema_version",
    "record_type",
    "review_id",
    "raw_input_inventory_sha256",
    "dispatch_manifest_prefix_sha256",
    "adjudicated_records_sha256",
    "adjudicated_record_count",
    "adjudicated_last_sequence",
    "judge_role",
    "verdict",
    "recorded_at",
    "findings_unresolved",
}
HEX_64_RE = re.compile(r"[0-9a-f]{64}\Z")
GIT_OID_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
REPOSITORY_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/+\-]{0,255}\Z")
RFC3339_UTC_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z"
)
SAFE_COMPONENT_FORBIDDEN = {"", ".", ".."}

PREPARE_RECEIPT_FIELDS = {
    "schema_version", "operation_id", "generation_id", "source",
    "immutable_source", "expected_live_source", "predecessor_source",
    "candidate_inventory", "preflight_live_inventory", "evidence_snapshot",
    "staged_validation", "named_mutation_outcomes", "mutation_outcome",
    "prepared_at",
}
READER_RECEIPT_FIELDS = {
    "schema_version", "record_type", "operation_id", "authorized_by",
    "maintenance_window", "known_reader_inventory",
    "publisher_validation_scope", "controller",
}
TERMINAL_RECEIPT_FIELDS = {
    "schema_version", "operation_id", "generation_id", "terminal_state",
    "source", "expected_live_source", "evidence_snapshot",
    "candidate_inventory", "live_inventory_at_dispatch",
    "live_inventory_immediately_before_swap",
    "live_inventory_at_terminal_validation", "reservation",
    "exchange_primitive", "previous_generation", "rollback",
    "mutation_outcome", "recovery_takeover_authorization",
    "reader_attestation_validation_scope", "reader_attestation_renewals",
    "atomic_exchange_reader_attestations", "reservation_state",
    "finalization_manifest", "validation", "named_mutation_outcomes",
    "finalized_at",
}
LIVE_INVENTORY_RECEIPT_FIELDS = {
    "schema_version", "record_type", "phase", "chain_position",
    "required_phase_order", "operation_id", "generation_id",
    "terminal_state", "observed_at", "installed_root", "source",
    "expected_live_source", "live_inventory", "terminal_receipt",
    "reservation", "predecessor_receipt",
    "prior_finalization_manifest_prefix",
    "current_finalization_manifest_prefix", "finalization_manifest_prefix",
    "manifest_prefix_registration", "review_id",
    "raw_input_inventory_sha256", "mutation_outcome",
}
LIVE_INVENTORY_IDENTITY_FIELDS = {
    "record_type", "phase", "chain_position", "review_id",
    "raw_input_inventory_sha256", "path", "sha256", "inventory_path",
    "inventory_sha256", "prior_manifest_prefix_sha256",
    "current_manifest_prefix_sha256",
}
INVENTORY_IDENTITY_FIELDS = {
    "format", "sha256", "file_count", "total_bytes", "path",
    "identity_kind", "installed_paths",
}

ENVELOPE_FIELDS = {
    "schema_version",
    "manifest_schema",
    "sequence",
    "recorded_at",
    "record_type",
    "finalization_id",
    "writer_controller_id",
}

PRE_DISPATCH_VALIDATION_FIELDS = {
    "artifact_kind",
    "path",
    "sha256",
    "status_field",
    "required_status",
}


@dataclass(frozen=True)
class RecordSchema:
    required: frozenset[str]
    optional: frozenset[str] = frozenset()
    allow_additional: bool = False
    identity_fields: Tuple[str, ...] = ()


def _fields(value: str) -> frozenset[str]:
    return frozenset(value.split())


# Controller-owned schemas are exact.  New source-review input uses generic
# repository roles; the separate legacy table below preserves the archived
# project-specific input shape.  Smaller artifact schemas serve later
# postpublication panels.  Publisher rows have large, independently validated
# nested receipts, so this grammar fixes their identity fields and joins.
RECORD_SCHEMAS: Dict[str, RecordSchema] = {
    "source_review_input_registered": RecordSchema(
        _fields(
            "review_id panel_id review_boundary repository_heads "
            "prepared_generation_id prepare_receipt_sha256 panel_input_path "
            "panel_input_sha256 raw_input_inventory_path raw_input_inventory_sha256 "
            "raw_input_seal_path raw_input_seal_sha256 raw_input_max_files "
            "raw_input_max_total_bytes raw_input_actual_files "
            "raw_input_actual_total_bytes expected_reports received_reports "
            "expected_challenge_responses received_challenge_responses "
            "expected_judges received_judges live_installation_status "
            "source_guidance_status state next_action"
        ),
        optional=_fields("required_pre_dispatch_validation"),
        identity_fields=("review_id",),
    ),
    "source_review_independent_reports_received": RecordSchema(
        _fields(
            "review_id panel_id raw_input_inventory_sha256 expected_reports "
            "received_reports expected_challenge_responses "
            "received_challenge_responses findings_received p0_received p1_received "
            "p2_received p3_received reports source_guidance_status state next_action"
        ),
        identity_fields=("review_id",),
    ),
    "source_review_challenges_received": RecordSchema(
        _fields(
            "review_id panel_id raw_input_inventory_sha256 "
            "expected_challenge_responses received_challenge_responses "
            "findings_received findings_answered deduplicated_findings_reported_min "
            "deduplicated_findings_reported_max challenges source_guidance_status "
            "state next_action"
        ),
        identity_fields=("review_id",),
    ),
    "source_review_judge_verdict_received": RecordSchema(
        _fields(
            "review_id panel_id raw_input_inventory_sha256 judge accepted_findings "
            "merged_findings live_installation_status source_guidance_status state "
            "next_action"
        ),
        identity_fields=("review_id",),
    ),
    "prepare_receipt_registered": RecordSchema(
        _fields(
            "operation_id generation_id receipt_path receipt_sha256 "
            "mutation_outcome state next_action"
        ),
        identity_fields=("operation_id", "generation_id"),
    ),
    "raw_input_registered": RecordSchema(
        _fields(
            "review_id review_boundary inventory_path raw_input_inventory_sha256 "
            "raw_input_max_files raw_input_max_total_bytes raw_input_actual_files "
            "raw_input_actual_total_bytes state next_action"
        ),
        identity_fields=("review_id",),
    ),
    "repository_identity": RecordSchema(
        _fields(
            "review_id repository target_ref target_ref_sha_at_dispatch target_sha "
            "merge_base_sha head_sha head_tree_oid diff_path diff_digest_algorithm "
            "diff_digest diff_argv"
        ),
        identity_fields=("review_id", "repository"),
    ),
    "artifact_registered": RecordSchema(
        _fields("review_id artifact_kind path sha256 state next_action"),
        identity_fields=("review_id", "artifact_kind", "path"),
    ),
    "artifact_invalidation": RecordSchema(
        _fields("review_id artifact_kind path prior_sha256 reason state next_action"),
        identity_fields=("review_id", "artifact_kind", "path"),
    ),
    "review_report_registered": RecordSchema(
        _fields("review_id reviewer_role path sha256 state next_action"),
        identity_fields=("review_id", "reviewer_role"),
    ),
    "challenge_response_registered": RecordSchema(
        _fields("review_id participant_role path sha256 state next_action"),
        identity_fields=("review_id", "participant_role"),
    ),
    "judge_verdict_registered": RecordSchema(
        _fields("review_id judge_role path sha256 verdict state next_action"),
        identity_fields=("review_id", "judge_role"),
    ),
    "review_summary": RecordSchema(
        _fields(
            "review_id raw_input_inventory_sha256 independent_reports_expected "
            "independent_reports_received challenge_participants_expected "
            "challenge_participants_received findings_received findings_answered "
            "findings_unresolved judge_reports_expected judge_reports_received "
            "state next_action"
        ),
        identity_fields=("review_id",),
    ),
    "phase_ready": RecordSchema(
        _fields("review_id phase raw_input_inventory_sha256 state next_action"),
        identity_fields=("review_id", "phase"),
    ),
    "manifest_prefix_registered": RecordSchema(
        _fields(
            "review_id phase manifest_prefix_path manifest_prefix_sha256 "
            "manifest_prefix_byte_count manifest_prefix_record_count "
            "manifest_prefix_last_sequence receipt_path receipt_sha256 "
            "raw_input_inventory_sha256"
        ),
        identity_fields=("review_id", "phase"),
    ),
    "external_dispatch_note": RecordSchema(
        _fields("note"),
        optional=_fields("operation_id generation_id"),
        identity_fields=("note",),
    ),
    "external_panel_note": RecordSchema(
        _fields("note"),
        optional=_fields("operation_id generation_id"),
        identity_fields=("note",),
    ),
    "installed_publication_reservation_intent": RecordSchema(
        _fields(
            "operation_id generation_id installer installed_root lock_path "
            "prepare_receipt_path prepare_receipt_sha256 prepare_receipt "
            "reader_quiescence_record_path reader_quiescence_record_sha256 "
            "reader_quiescence_record preflight_inventory candidate_inventory "
            "expected_live_source_commit reservation_state atomic_operation "
            "mandatory_recovery_condition"
        ),
        identity_fields=("operation_id", "generation_id"),
    ),
    "installed_publication_terminal": RecordSchema(
        _fields(
            "operation_id generation_id installed_root lock_path reservation_state "
            "terminal_state publication_receipt_path publication_receipt_sha256"
            " publication_receipt"
        ),
        identity_fields=("operation_id", "generation_id"),
    ),
    "installed_publication_accepted": RecordSchema(
        _fields(
            "operation_id generation_id installed_root lock_path reservation_state "
            "terminal_state accepted_by acceptance_reason accepted_at "
            "terminal_receipt acceptance_inventory_receipt "
            "live_inventory_receipt_chain acceptance_inventory "
            "acceptance_manifest_predecessor_prefix"
        ),
        identity_fields=("operation_id", "generation_id"),
    ),
}

# The project-specific v1 grammar remains readable so archived evidence can be
# reproduced.  New manifests and every mutation path use only the generic v2
# schema above.
LEGACY_RECORD_SCHEMAS: Dict[str, RecordSchema] = dict(RECORD_SCHEMAS)
LEGACY_RECORD_SCHEMAS["source_review_input_registered"] = RecordSchema(
    _fields(
        "review_id panel_id review_boundary global_head_sha doodlerun_head_sha "
        "prepared_generation_id prepare_receipt_sha256 panel_input_path "
        "panel_input_sha256 raw_input_inventory_path raw_input_inventory_sha256 "
        "raw_input_seal_path raw_input_seal_sha256 raw_input_max_files "
        "raw_input_max_total_bytes raw_input_actual_files "
        "raw_input_actual_total_bytes expected_reports received_reports "
        "expected_challenge_responses received_challenge_responses "
        "expected_judges received_judges live_installation_status "
        "source_guidance_status state next_action"
    ),
    identity_fields=("review_id",),
)

CONTROLLER_RECORD_TYPES = frozenset(
    set(RECORD_SCHEMAS)
    - {
        "installed_publication_reservation_intent",
        "installed_publication_terminal",
        "installed_publication_accepted",
        "manifest_prefix_registered",
    }
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not RFC3339_UTC_RE.fullmatch(value):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0


def _require_identity(value: Any, label: str) -> str:
    if not isinstance(value, str) or not IDENTITY_RE.fullmatch(value):
        raise PublicationError(f"finalization {label} must be a normalized identity")
    return value


def _require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not HEX_64_RE.fullmatch(value):
        raise PublicationError(f"finalization {label} must be lowercase SHA-256")
    return value


def _require_positive_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise PublicationError(f"finalization {label} must be a positive integer")
    return value


def _require_nonnegative_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise PublicationError(f"finalization {label} must be a nonnegative integer")
    return value


def _safe_absolute(path: Path, *, label: str, must_exist: bool = False) -> Path:
    path = Path(path)
    if any(ord(character) < 32 or ord(character) == 127 for character in str(path)):
        raise PublicationError(f"{label} contains a control character: {path!r}")
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
            observed = os.lstat(current)
        except FileNotFoundError:
            missing_seen = True
            continue
        if missing_seen:
            raise PublicationError(
                f"{label} has an existing child below a missing parent: {current}"
            )
        if stat.S_ISLNK(observed.st_mode):
            raise PublicationError(f"{label} has a symlink component: {current}")
        if current != path and not stat.S_ISDIR(observed.st_mode):
            raise PublicationError(f"{label} parent is not a directory: {current}")
    if must_exist and not path.exists():
        raise PublicationError(f"{label} does not exist: {path}")
    return path


def _read_regular_bytes(path: Path, *, label: str) -> bytes:
    path = _safe_absolute(path, label=label, must_exist=True)
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise PublicationError(f"{label} must be a regular single-link file")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (
            (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
        ):
            raise PublicationError(f"{label} changed before it was opened")
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
            raise PublicationError(f"{label} changed while it was read")
        return data
    finally:
        os.close(descriptor)


def _validate_artifact_identity(value: Any, label: str) -> None:
    if not isinstance(value, dict):
        raise PublicationError(f"finalization {label} must be an object")
    required = {"path", "sha256", "bytes", "lines"}
    if not required.issubset(value):
        raise PublicationError(f"finalization {label} lacks immutable artifact identity")
    path = value.get("path")
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise PublicationError(f"finalization {label}.path must be absolute")
    _safe_absolute(Path(path), label=f"finalization {label}.path")
    _require_sha256(value.get("sha256"), f"{label}.sha256")
    for field in ("bytes", "lines"):
        if not isinstance(value.get(field), int) or isinstance(value.get(field), bool) or value[field] < 0:
            raise PublicationError(f"finalization {label}.{field} must be nonnegative")


def _validate_repository_heads(value: Any, label: str) -> None:
    if not isinstance(value, dict) or not value:
        raise PublicationError(f"{label} must be a nonempty role-to-repository map")
    repository_keys: List[str] = []
    for role, identity in value.items():
        if not isinstance(role, str) or not REPOSITORY_IDENTITY_RE.fullmatch(role):
            raise PublicationError(f"{label} role must be a normalized identity")
        if not isinstance(identity, dict) or set(identity) != {
            "repository_key",
            "head_sha",
        }:
            raise PublicationError(
                f"{label}.{role} must have exact repository_key and head_sha fields"
            )
        repository_key = identity.get("repository_key")
        if (
            not isinstance(repository_key, str)
            or not REPOSITORY_IDENTITY_RE.fullmatch(repository_key)
        ):
            raise PublicationError(f"{label}.{role} key must be a normalized identity")
        repository_keys.append(repository_key)
        if not isinstance(identity.get("head_sha"), str) or not GIT_OID_RE.fullmatch(
            identity["head_sha"]
        ):
            raise PublicationError(f"{label}.{role} head_sha must be a full Git object ID")
    if len(repository_keys) != len(set(repository_keys)):
        raise PublicationError(f"{label} repository keys must be unique")
    if "installed_source" not in value:
        raise PublicationError(f"{label} must declare the installed_source role")


def _validate_pre_dispatch_validation_requirement(value: Any, label: str) -> None:
    if not isinstance(value, dict) or set(value) != PRE_DISPATCH_VALIDATION_FIELDS:
        raise PublicationError(
            f"{label} must have exact artifact kind, path, digest, and status fields"
        )
    _require_identity(value.get("artifact_kind"), f"{label} artifact_kind")
    path = value.get("path")
    if not isinstance(path, str):
        raise PublicationError(f"{label} path must be absolute")
    _safe_absolute(Path(path), label=f"{label} path")
    _require_sha256(value.get("sha256"), f"{label} sha256")
    _require_identity(value.get("status_field"), f"{label} status_field")
    if value.get("required_status") != "PASS":
        raise PublicationError(f"{label} required_status must equal PASS")


def _validate_record_payload(
    record: Mapping[str, Any],
    row: int,
    *,
    manifest_schema: str = MANIFEST_SCHEMA,
) -> None:
    record_type = record["record_type"]
    schemas = (
        LEGACY_RECORD_SCHEMAS
        if manifest_schema == LEGACY_MANIFEST_SCHEMA
        else RECORD_SCHEMAS
    )
    schema = schemas.get(record_type)
    if schema is None:
        raise PublicationError(
            f"finalization manifest row {row} has unknown record_type: {record_type!r}"
        )
    payload_fields = set(record) - ENVELOPE_FIELDS
    missing = set(schema.required) - payload_fields
    if missing:
        raise PublicationError(
            f"finalization manifest row {row} lacks {record_type} fields: {sorted(missing)}"
        )
    if not schema.allow_additional:
        unknown = payload_fields - set(schema.required) - set(schema.optional)
        if unknown:
            raise PublicationError(
                f"finalization manifest row {row} has undeclared {record_type} fields: "
                f"{sorted(unknown)}"
            )
    for field in (
        "review_id",
        "operation_id",
        "generation_id",
        "reviewer_role",
        "participant_role",
        "judge_role",
        "artifact_kind",
    ):
        if field in record:
            _require_identity(record.get(field), f"row {row} {field}")
    for field in ("raw_input_inventory_sha256", "sha256", "prior_sha256"):
        if field in record:
            _require_sha256(record[field], f"row {row} {field}")
    for field in (
        "receipt_sha256",
        "publication_receipt_sha256",
        "prepare_receipt_sha256",
        "reader_quiescence_record_sha256",
    ):
        if field in record:
            _require_sha256(record[field], f"row {row} {field}")
    for field in (
        "path",
        "receipt_path",
        "manifest_prefix_path",
        "installed_root",
        "lock_path",
        "prepare_receipt_path",
        "reader_quiescence_record_path",
        "publication_receipt_path",
    ):
        if field in record:
            if not isinstance(record[field], str):
                raise PublicationError(f"finalization row {row} {field} must be absolute")
            _safe_absolute(Path(record[field]), label=f"finalization row {row} {field}")
    if "phase" in record and record["phase"] not in PHASE_ORDER:
        raise PublicationError(f"finalization row {row} has unsupported phase")
    if record_type == "manifest_prefix_registered":
        _require_sha256(record["manifest_prefix_sha256"], "manifest prefix SHA-256")
        for field in (
            "manifest_prefix_byte_count",
            "manifest_prefix_record_count",
            "manifest_prefix_last_sequence",
        ):
            _require_positive_int(record[field], field)
    if record_type == "source_review_input_registered":
        if manifest_schema == MANIFEST_SCHEMA:
            _validate_repository_heads(
                record.get("repository_heads"),
                f"finalization row {row} repository_heads",
            )
            if "required_pre_dispatch_validation" in record:
                _validate_pre_dispatch_validation_requirement(
                    record["required_pre_dispatch_validation"],
                    f"finalization row {row} required_pre_dispatch_validation",
                )
        else:
            for field in ("global_head_sha", "doodlerun_head_sha"):
                if (
                    not isinstance(record.get(field), str)
                    or not GIT_OID_RE.fullmatch(record[field])
                ):
                    raise PublicationError(
                        f"finalization legacy row {row} {field} must be a full Git object ID"
                    )
        for field in (
            "panel_input_sha256",
            "prepare_receipt_sha256",
            "raw_input_seal_sha256",
        ):
            _require_sha256(record[field], field)
        for field in (
            "raw_input_max_files",
            "raw_input_max_total_bytes",
            "raw_input_actual_files",
            "raw_input_actual_total_bytes",
        ):
            _require_positive_int(record[field], field)
        for field in (
            "expected_reports",
            "received_reports",
            "expected_challenge_responses",
            "received_challenge_responses",
            "expected_judges",
            "received_judges",
        ):
            _require_nonnegative_int(record[field], field)
        if record["raw_input_actual_files"] > record["raw_input_max_files"]:
            raise PublicationError("source review raw-input file cap was exceeded")
        if record["raw_input_actual_total_bytes"] > record["raw_input_max_total_bytes"]:
            raise PublicationError("source review raw-input byte cap was exceeded")
    if record_type == "source_review_independent_reports_received":
        for field in (
            "expected_reports",
            "received_reports",
            "expected_challenge_responses",
            "received_challenge_responses",
            "findings_received",
            "p0_received",
            "p1_received",
            "p2_received",
            "p3_received",
        ):
            _require_nonnegative_int(record[field], field)
        reports = record.get("reports")
        if not isinstance(reports, list) or not reports:
            raise PublicationError("source review reports must be a nonempty array")
        for index, report in enumerate(reports):
            _validate_artifact_identity(report, f"reports[{index}]")
    if record_type == "source_review_challenges_received":
        for field in (
            "expected_challenge_responses",
            "received_challenge_responses",
            "findings_received",
            "findings_answered",
            "deduplicated_findings_reported_min",
            "deduplicated_findings_reported_max",
        ):
            _require_nonnegative_int(record[field], field)
        challenges = record.get("challenges")
        if not isinstance(challenges, list) or not challenges:
            raise PublicationError("source review challenges must be a nonempty array")
        for index, challenge in enumerate(challenges):
            _validate_artifact_identity(challenge, f"challenges[{index}]")
    if record_type == "source_review_judge_verdict_received":
        accepted = record.get("accepted_findings")
        if not isinstance(accepted, list) or any(
            not isinstance(value, str) or not value for value in accepted
        ):
            raise PublicationError("source review accepted_findings must be an array of IDs")
        merged = record.get("merged_findings")
        if not isinstance(merged, dict) or any(
            not isinstance(key, str)
            or not key
            or not isinstance(value, str)
            or not value
            for key, value in merged.items()
        ):
            raise PublicationError("source review merged_findings must map finding IDs")
        _validate_artifact_identity(record.get("judge"), "judge")
    if record_type == "review_summary":
        for field in (
            "independent_reports_expected",
            "independent_reports_received",
            "challenge_participants_expected",
            "challenge_participants_received",
            "findings_received",
            "findings_answered",
            "findings_unresolved",
            "judge_reports_expected",
            "judge_reports_received",
        ):
            _require_nonnegative_int(record[field], field)
    if record_type == "prepare_receipt_registered":
        if record.get("mutation_outcome") != "NO_LIVE_MUTATION_PREPARED":
            raise PublicationError("prepare registration mutation outcome is invalid")
        if record.get("state") != "PREPARED":
            raise PublicationError("prepare registration state is not PREPARED")
    if record_type == "raw_input_registered":
        if (
            manifest_schema == MANIFEST_SCHEMA
            and record.get("review_boundary") != POSTPUBLICATION_REVIEW_BOUNDARY
        ):
            raise PublicationError(
                "generic-v2 raw_input_registered is postpublication-only; "
                "prepublication source review requires source_review_input_registered"
            )
        for field in (
            "raw_input_max_files",
            "raw_input_max_total_bytes",
            "raw_input_actual_files",
            "raw_input_actual_total_bytes",
        ):
            _require_positive_int(record[field], field)
        if record["raw_input_actual_files"] > record["raw_input_max_files"]:
            raise PublicationError("raw-input file cap was exceeded")
        if record["raw_input_actual_total_bytes"] > record["raw_input_max_total_bytes"]:
            raise PublicationError("raw-input byte cap was exceeded")
    if record_type == "installed_publication_reservation_intent":
        if record.get("reservation_state") != "INTENT_RECORDED":
            raise PublicationError("publication reservation intent state is invalid")
        if record.get("atomic_operation") != "darwin-rename-swap":
            raise PublicationError("publication reservation atomic operation is invalid")
        for field in (
            "installer",
            "mandatory_recovery_condition",
            "expected_live_source_commit",
        ):
            if not isinstance(record.get(field), str) or not record[field].strip():
                raise PublicationError(f"publication reservation {field} must be nonempty")
        prepare_receipt = _verify_registered_json(
            record.get("prepare_receipt_path"),
            record.get("prepare_receipt_sha256"),
            record.get("prepare_receipt"),
            fields=PREPARE_RECEIPT_FIELDS,
            label="publication reservation prepare receipt",
        )
        if (
            prepare_receipt.get("schema_version") != 3
            or prepare_receipt.get("operation_id") != record.get("operation_id")
            or prepare_receipt.get("generation_id") != record.get("generation_id")
            or prepare_receipt.get("mutation_outcome")
            != "NO_LIVE_MUTATION_PREPARED"
            or not _valid_timestamp(prepare_receipt.get("prepared_at"))
        ):
            raise PublicationError(
                "publication reservation prepare receipt changes lifecycle identity"
            )
        reader_receipt = _verify_registered_json(
            record.get("reader_quiescence_record_path"),
            record.get("reader_quiescence_record_sha256"),
            record.get("reader_quiescence_record"),
            fields=READER_RECEIPT_FIELDS,
            label="publication reservation reader-quiescence receipt",
        )
        if (
            reader_receipt.get("schema_version") != 2
            or reader_receipt.get("record_type")
            != "external_reader_quiescence_attestation"
            or reader_receipt.get("operation_id") != record.get("operation_id")
            or not isinstance(reader_receipt.get("maintenance_window"), dict)
            or set(reader_receipt["maintenance_window"])
            != {"id", "starts_at", "ends_at"}
            or not isinstance(reader_receipt.get("known_reader_inventory"), dict)
            or set(reader_receipt["known_reader_inventory"])
            != {
                "scope", "method", "evidence_reference", "inventory_complete",
                "known_reader_count", "known_active_reader_count",
                "unknown_reader_policy", "unknown_reader_status", "checked_at",
                "expires_at",
            }
            or not isinstance(reader_receipt.get("controller"), dict)
            or set(reader_receipt["controller"]) != {"id", "state", "owner"}
            or not isinstance(reader_receipt["controller"].get("owner"), dict)
            or set(reader_receipt["controller"]["owner"])
            != {"host", "pid", "process_start_identity"}
        ):
            raise PublicationError(
                "publication reservation reader-quiescence receipt schema drifted"
            )
        for field in ("preflight_inventory", "candidate_inventory"):
            _validate_inventory_identity(
                record.get(field), f"publication reservation {field}"
            )
    if record_type == "installed_publication_terminal":
        if record.get("reservation_state") != "RETAINED_PENDING_PANEL_ACCEPTANCE":
            raise PublicationError("publication terminal reservation state is invalid")
        if not isinstance(record.get("terminal_state"), str) or not record["terminal_state"].strip():
            raise PublicationError("publication terminal state must be nonempty")
        receipt = _verify_registered_json(
            record.get("publication_receipt_path"),
            record.get("publication_receipt_sha256"),
            record.get("publication_receipt"),
            fields=TERMINAL_RECEIPT_FIELDS,
            label="publication terminal receipt",
        )
        if (
            receipt.get("schema_version") != 3
            or receipt.get("operation_id") != record.get("operation_id")
            or receipt.get("generation_id") != record.get("generation_id")
            or receipt.get("terminal_state") != record.get("terminal_state")
            or receipt.get("reservation_state") != record.get("reservation_state")
            or not _valid_timestamp(receipt.get("finalized_at"))
            or not isinstance(receipt.get("named_mutation_outcomes"), dict)
            or not receipt["named_mutation_outcomes"]
        ):
            raise PublicationError("publication terminal receipt changes lifecycle identity")
    if record_type == "installed_publication_accepted":
        if record.get("reservation_state") != "PANEL_ACCEPTANCE_RECORDED":
            raise PublicationError("publication acceptance reservation state is invalid")
        if not _valid_timestamp(record.get("accepted_at")):
            raise PublicationError("publication acceptance time is invalid")
        for field in ("accepted_by", "acceptance_reason", "terminal_state"):
            if not isinstance(record.get(field), str) or not record[field].strip():
                raise PublicationError(f"publication acceptance {field} must be nonempty")
        if not isinstance(record.get("terminal_receipt"), dict) or set(
            record["terminal_receipt"]
        ) != {"path", "sha256"}:
            raise PublicationError("publication acceptance terminal receipt is malformed")
        chain = record.get("live_inventory_receipt_chain")
        if not isinstance(chain, list) or len(chain) != len(PHASE_ORDER):
            raise PublicationError("publication acceptance receipt chain must be an array")
        receipts = [
            _verify_live_inventory_receipt_identity(
                identity,
                operation_id=record.get("operation_id"),
                generation_id=record.get("generation_id"),
                terminal_state=record.get("terminal_state"),
                installed_root=record.get("installed_root"),
                expected_phase=phase,
                expected_position=index,
            )
            for index, (phase, identity) in enumerate(zip(PHASE_ORDER, chain), 1)
        ]
        acceptance_identity = record.get("acceptance_inventory_receipt")
        if (
            not isinstance(acceptance_identity, dict)
            or set(acceptance_identity) != {"path", "sha256"}
            or acceptance_identity.get("path") != chain[-1].get("path")
            or acceptance_identity.get("sha256") != chain[-1].get("sha256")
        ):
            raise PublicationError(
                "publication acceptance identity is not the final receipt in its chain"
            )
        if record.get("acceptance_inventory") != receipts[-1]:
            raise PublicationError(
                "publication acceptance inventory differs from its durable receipt"
            )
        if not isinstance(record.get("acceptance_manifest_predecessor_prefix"), dict):
            raise PublicationError(
                "publication acceptance manifest predecessor prefix must be an object"
            )


def _matching_record(
    records: Sequence[Mapping[str, Any]], record_type: str, **fields: Any
) -> Optional[Mapping[str, Any]]:
    for record in records:
        if record.get("record_type") != record_type:
            continue
        if all(record.get(field) == expected for field, expected in fields.items()):
            return record
    return None


def _verify_registered_file(path: Any, digest: Any, label: str) -> bytes:
    if not isinstance(path, str):
        raise PublicationError(f"{label} path is malformed")
    _require_sha256(digest, f"{label} SHA-256")
    data = _read_regular_bytes(Path(path), label=label)
    if hashlib.sha256(data).hexdigest() != digest:
        raise PublicationError(f"{label} digest drifted")
    return data


def _verify_registered_json(
    path: Any,
    digest: Any,
    recorded: Any,
    *,
    fields: set[str],
    label: str,
) -> Mapping[str, Any]:
    """Bind a nested publisher receipt to one exact durable JSON object."""
    data = _verify_registered_file(path, digest, label)
    value = _decode_json_without_duplicate_keys(data, label)
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or (
            recorded is not None
            and (not isinstance(recorded, dict) or value != recorded)
        )
    ):
        raise PublicationError(
            f"{label} must have its exact schema and equal the recorded receipt"
        )
    return value


def _validate_inventory_identity(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != INVENTORY_IDENTITY_FIELDS:
        raise PublicationError(f"{label} must have the exact inventory identity schema")
    _require_sha256(value.get("sha256"), f"{label} SHA-256")
    _require_positive_int(value.get("file_count"), f"{label} file_count")
    _require_nonnegative_int(value.get("total_bytes"), f"{label} total_bytes")
    if not isinstance(value.get("format"), str) or not value["format"]:
        raise PublicationError(f"{label} format must be nonempty")
    if value.get("identity_kind") != "exact-installed-paths-and-file-bytes":
        raise PublicationError(f"{label} identity_kind is invalid")
    installed_paths = value.get("installed_paths")
    if (
        not isinstance(installed_paths, list)
        or not installed_paths
        or any(not isinstance(path, str) or not path for path in installed_paths)
        or len(installed_paths) != len(set(installed_paths))
    ):
        raise PublicationError(f"{label} installed_paths are invalid")
    if not isinstance(value.get("path"), str):
        raise PublicationError(f"{label} path must be an absolute path")
    _safe_absolute(Path(value["path"]), label=f"{label} path")
    return value


def _verify_live_inventory_receipt_identity(
    identity: Any,
    *,
    operation_id: Any,
    generation_id: Any,
    terminal_state: Any,
    installed_root: Any,
    expected_phase: str,
    expected_position: int,
) -> Mapping[str, Any]:
    if not isinstance(identity, dict) or set(identity) != LIVE_INVENTORY_IDENTITY_FIELDS:
        raise PublicationError("publication inventory receipt identity schema drifted")
    if (
        identity.get("record_type") != "live_inventory_observation"
        or identity.get("phase") != expected_phase
        or identity.get("chain_position") != expected_position
    ):
        raise PublicationError("publication inventory receipt chain is out of order")
    receipt = _verify_registered_json(
        identity.get("path"),
        identity.get("sha256"),
        None,
        fields=LIVE_INVENTORY_RECEIPT_FIELDS,
        label=f"{expected_phase} live inventory receipt",
    )
    # _verify_registered_json normally compares a nested copy.  The chain
    # stores only identities, so reproduce the identity directly here.
    expected_identity = {
        "record_type": receipt.get("record_type"),
        "phase": receipt.get("phase"),
        "chain_position": receipt.get("chain_position"),
        "review_id": receipt.get("review_id"),
        "raw_input_inventory_sha256": receipt.get("raw_input_inventory_sha256"),
        "path": identity.get("path"),
        "sha256": identity.get("sha256"),
        "inventory_path": (
            receipt.get("live_inventory", {}).get("path")
            if isinstance(receipt.get("live_inventory"), dict)
            else None
        ),
        "inventory_sha256": (
            receipt.get("live_inventory", {}).get("sha256")
            if isinstance(receipt.get("live_inventory"), dict)
            else None
        ),
        "prior_manifest_prefix_sha256": (
            receipt.get("prior_finalization_manifest_prefix", {}).get("prefix_sha256")
            if isinstance(receipt.get("prior_finalization_manifest_prefix"), dict)
            else None
        ),
        "current_manifest_prefix_sha256": (
            receipt.get("current_finalization_manifest_prefix", {}).get("prefix_sha256")
            if isinstance(receipt.get("current_finalization_manifest_prefix"), dict)
            else None
        ),
    }
    if dict(identity) != expected_identity:
        raise PublicationError("publication inventory receipt identity differs from its bytes")
    if (
        receipt.get("schema_version") != 2
        or receipt.get("required_phase_order") != list(PHASE_ORDER)
        or receipt.get("operation_id") != operation_id
        or receipt.get("generation_id") != generation_id
        or receipt.get("terminal_state") != terminal_state
        or receipt.get("installed_root") != installed_root
        or not _valid_timestamp(receipt.get("observed_at"))
    ):
        raise PublicationError("publication inventory receipt changes lifecycle identity")
    return receipt


def _verify_artifact_record(record: Mapping[str, Any], label: str) -> bytes:
    data = _verify_registered_file(record.get("path"), record.get("sha256"), label)
    if not data:
        raise PublicationError(f"{label} must be nonempty")
    if "bytes" in record and record.get("bytes") != len(data):
        raise PublicationError(f"{label} recorded byte count drifted")
    if "lines" in record and record.get("lines") != len(data.splitlines()):
        raise PublicationError(f"{label} recorded line count drifted")
    return data


def _decode_json_without_duplicate_keys(data: bytes, label: str) -> Any:
    def reject_duplicate_keys(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
        value: Dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise PublicationError(f"{label} contains duplicate JSON key {key!r}")
            value[key] = item
        return value

    try:
        return json.loads(
            data.decode("utf-8"), object_pairs_hook=reject_duplicate_keys
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PublicationError(f"{label} is invalid JSON") from exc


def _verify_required_pre_dispatch_validation(
    records: Sequence[Mapping[str, Any]],
    source_input: Mapping[str, Any],
) -> None:
    requirement = source_input.get("required_pre_dispatch_validation")
    if requirement is None:
        return
    if not isinstance(requirement, dict):
        raise PublicationError("required pre-dispatch validation is malformed")
    review_id = source_input.get("review_id")
    artifact_kind = requirement.get("artifact_kind")
    path = requirement.get("path")
    digest = requirement.get("sha256")
    registration = _matching_record(
        records,
        "artifact_registered",
        review_id=review_id,
        artifact_kind=artifact_kind,
        path=path,
        sha256=digest,
        state="PASS",
    )
    if registration is None:
        raise PublicationError(
            "manifest-prefix dispatch lacks its required PASS artifact registration"
        )
    invalidation = _matching_record(
        records,
        "artifact_invalidation",
        review_id=review_id,
        artifact_kind=artifact_kind,
        path=path,
        prior_sha256=digest,
    )
    if invalidation is not None:
        raise PublicationError(
            "manifest-prefix dispatch required validation artifact was invalidated"
        )
    data = _verify_artifact_record(
        registration, "required pre-dispatch validation artifact"
    )
    receipt = _decode_json_without_duplicate_keys(
        data, "required pre-dispatch validation artifact"
    )
    status_field = requirement.get("status_field")
    required_status = requirement.get("required_status")
    if (
        not isinstance(receipt, dict)
        or receipt.get("review_id") != review_id
        or receipt.get(status_field) != required_status
    ):
        raise PublicationError(
            "required pre-dispatch validation artifact is not PASS for this review"
        )


def judgment_input_identity(
    records: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Return the canonical semantic identity a judge must attest before append."""
    if not records:
        raise PublicationError("judge input cannot be empty")
    encoded = b"".join(
        (
            json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        for record in records
    )
    last_sequence = records[-1].get("sequence")
    if type(last_sequence) is not int:
        raise PublicationError("judge input has malformed last sequence")
    return {
        "adjudicated_records_sha256": hashlib.sha256(encoded).hexdigest(),
        "adjudicated_record_count": len(records),
        "adjudicated_last_sequence": last_sequence,
    }


def _raw_input_tree_closure(
    root: Path, *, label: str
) -> Tuple[
    Dict[str, Path],
    set[str],
    Dict[str, Tuple[int, int, int, int, int]],
    Dict[str, Tuple[int, int, int, int, int, int, int]],
]:
    """Enumerate one exact no-follow regular-file/directory closure."""
    root = _safe_absolute(root, label=f"{label} root", must_exist=True)
    root_stat = os.lstat(root)
    if not stat.S_ISDIR(root_stat.st_mode):
        raise PublicationError(f"{label} root must be a directory")
    files: Dict[str, Path] = {}
    observed_files: Dict[Path, Tuple[int, int, int, int, int, int, int]] = {}
    directories = {"."}
    observed_directories: Dict[Path, Tuple[int, int, int, int, int]] = {
        root: (
            root_stat.st_dev,
            root_stat.st_ino,
            stat.S_IFMT(root_stat.st_mode),
            root_stat.st_mtime_ns,
            root_stat.st_ctime_ns,
        )
    }
    pending: List[Tuple[Path, PurePosixPath]] = [(root, PurePosixPath("."))]
    while pending:
        directory, relative_directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise PublicationError(f"{label} tree cannot be enumerated: {exc}") from exc
        for entry in entries:
            if any(ord(character) < 32 or ord(character) == 127 for character in entry.name):
                raise PublicationError(f"{label} tree contains a control-character name")
            relative = (
                PurePosixPath(entry.name)
                if relative_directory == PurePosixPath(".")
                else relative_directory / entry.name
            )
            name = relative.as_posix()
            try:
                observed = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise PublicationError(f"{label} tree entry cannot be inspected: {name}") from exc
            entry_path = directory / entry.name
            if stat.S_ISLNK(observed.st_mode):
                raise PublicationError(f"{label} tree contains a symlink: {name}")
            if stat.S_ISDIR(observed.st_mode):
                directories.add(name)
                observed_directories[entry_path] = (
                    observed.st_dev,
                    observed.st_ino,
                    stat.S_IFMT(observed.st_mode),
                    observed.st_mtime_ns,
                    observed.st_ctime_ns,
                )
                pending.append((entry_path, relative))
            elif stat.S_ISREG(observed.st_mode):
                if observed.st_nlink != 1:
                    raise PublicationError(
                        f"{label} tree file must be single-link: {name}"
                    )
                files[name] = entry_path
                observed_files[entry_path] = (
                    observed.st_dev,
                    observed.st_ino,
                    stat.S_IFMT(observed.st_mode),
                    observed.st_nlink,
                    observed.st_size,
                    observed.st_mtime_ns,
                    observed.st_ctime_ns,
                )
            else:
                raise PublicationError(
                    f"{label} tree contains a non-regular entry: {name}"
                )
    for directory, identity in observed_directories.items():
        after = os.lstat(directory)
        if (
            after.st_dev,
            after.st_ino,
            stat.S_IFMT(after.st_mode),
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) != identity:
            raise PublicationError(f"{label} tree changed while it was enumerated")
    for path, identity in observed_files.items():
        after = os.lstat(path)
        if (
            after.st_dev,
            after.st_ino,
            stat.S_IFMT(after.st_mode),
            after.st_nlink,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) != identity:
            raise PublicationError(f"{label} tree changed while it was enumerated")
    directory_identities = {
        (
            "."
            if directory == root
            else directory.relative_to(root).as_posix()
        ): identity
        for directory, identity in observed_directories.items()
    }
    file_identities = {
        path.relative_to(root).as_posix(): identity
        for path, identity in observed_files.items()
    }
    return files, directories, directory_identities, file_identities


def _verify_raw_input_closure(
    root: Path,
    entries: Sequence[Any],
    *,
    label: str,
) -> int:
    expected_files = {str(entry.path) for entry in entries}
    expected_directories = {"."}
    for name in expected_files:
        relative = PurePosixPath(name)
        for depth in range(1, len(relative.parts)):
            expected_directories.add(PurePosixPath(*relative.parts[:depth]).as_posix())
    files, directories, directory_identities, file_identities = _raw_input_tree_closure(
        root, label=label
    )
    if set(files) != expected_files or directories != expected_directories:
        raise PublicationError(
            f"{label} tree differs from the exact inventory closure"
        )
    total_bytes = 0
    for index, entry in enumerate(entries, 1):
        data = _read_regular_bytes(files[str(entry.path)], label=f"{label} row {index}")
        if len(data) != entry.size or hashlib.sha256(data).hexdigest() != entry.sha256:
            raise PublicationError(f"{label} row {index} bytes drifted")
        total_bytes += entry.size
    (
        after_files,
        after_directories,
        after_directory_identities,
        after_file_identities,
    ) = _raw_input_tree_closure(root, label=label)
    if (
        set(after_files) != expected_files
        or after_directories != expected_directories
        or after_directory_identities != directory_identities
        or after_file_identities != file_identities
    ):
        raise PublicationError(f"{label} tree changed while its members were read")
    return total_bytes


def _verify_generic_raw_input(record: Mapping[str, Any]) -> None:
    inventory_path = Path(str(record["inventory_path"]))
    inventory_data = _read_regular_bytes(
        inventory_path, label="postpublication raw-input inventory"
    )
    if hashlib.sha256(inventory_data).hexdigest() != record["raw_input_inventory_sha256"]:
        raise PublicationError("postpublication raw-input inventory digest drifted")
    try:
        entries = parse_inventory(inventory_data)
    except PublicationError as exc:
        raise PublicationError(
            f"postpublication raw-input inventory is malformed: {exc}"
        ) from exc
    raw_root = inventory_path.parent / "raw-inputs"
    total_bytes = _verify_raw_input_closure(
        raw_root, entries, label="postpublication raw-input"
    )
    if (
        len(entries) != record["raw_input_actual_files"]
        or total_bytes != record["raw_input_actual_total_bytes"]
        or len(entries) > record["raw_input_max_files"]
        or total_bytes > record["raw_input_max_total_bytes"]
    ):
        raise PublicationError("postpublication raw-input inventory counts drifted")


def _verify_judge_receipt(
    record: Mapping[str, Any],
    *,
    records: Sequence[Mapping[str, Any]],
    review_id: str,
    raw_input_inventory_sha256: str,
    expected_role: Optional[str],
    expected_verdict: str,
    verdict_record: Mapping[str, Any],
    label: str,
) -> Mapping[str, Any]:
    data = _verify_artifact_record(record, label)
    receipt = _decode_json_without_duplicate_keys(data, label)
    dispatch = _matching_record(
        records,
        "manifest_prefix_registered",
        review_id=review_id,
        phase="dispatch",
        raw_input_inventory_sha256=raw_input_inventory_sha256,
    )
    try:
        verdict_index = next(
            index for index, item in enumerate(records) if item is verdict_record
        )
    except StopIteration as exc:
        raise PublicationError(f"{label} row is not in the manifest") from exc
    adjudicated = judgment_input_identity(records[:verdict_index])
    if (
        not isinstance(receipt, dict)
        or set(receipt) != JUDGE_RECEIPT_FIELDS
        or type(receipt.get("schema_version")) is not int
        or receipt["schema_version"] != 1
        or receipt.get("record_type") != JUDGE_RECEIPT_TYPE
        or receipt.get("review_id") != review_id
        or receipt.get("raw_input_inventory_sha256")
        != raw_input_inventory_sha256
        or dispatch is None
        or receipt.get("dispatch_manifest_prefix_sha256")
        != dispatch.get("manifest_prefix_sha256")
        or any(receipt.get(field) != expected for field, expected in adjudicated.items())
        or not isinstance(receipt.get("judge_role"), str)
        or not IDENTITY_RE.fullmatch(receipt["judge_role"])
        or (expected_role is not None and receipt.get("judge_role") != expected_role)
        or receipt.get("verdict") != expected_verdict
        or not _valid_timestamp(receipt.get("recorded_at"))
        or type(receipt.get("findings_unresolved")) is not int
        or receipt["findings_unresolved"] != 0
    ):
        raise PublicationError(f"{label} does not bind an accepted judge decision")
    return receipt


def _verify_source_raw_input(record: Mapping[str, Any]) -> None:
    inventory_path = Path(str(record["raw_input_inventory_path"]))
    inventory_data = _read_regular_bytes(
        inventory_path, label="source-review raw-input inventory"
    )
    if hashlib.sha256(inventory_data).hexdigest() != record["raw_input_inventory_sha256"]:
        raise PublicationError("source-review raw-input inventory digest drifted")
    root = inventory_path.parent / "raw-inputs"
    try:
        entries = parse_inventory(inventory_data)
    except PublicationError as exc:
        raise PublicationError(
            f"source-review raw-input inventory is malformed: {exc}"
        ) from exc
    total_bytes = _verify_raw_input_closure(
        root, entries, label="source-review raw-input"
    )
    if (
        len(entries) != record["raw_input_actual_files"]
        or total_bytes != record["raw_input_actual_total_bytes"]
        or len(entries) > record["raw_input_max_files"]
        or total_bytes > record["raw_input_max_total_bytes"]
    ):
        raise PublicationError("source-review raw-input inventory counts drifted")

    panel_data = _verify_registered_file(
        record.get("panel_input_path"),
        record.get("panel_input_sha256"),
        "source-review panel input",
    )
    reviewer_panel_path = root / "panel-input.jsonl"
    if (
        "panel-input.jsonl" not in {entry.path for entry in entries}
        or _read_regular_bytes(
            reviewer_panel_path, label="source-review reviewer panel input"
        )
        != panel_data
    ):
        raise PublicationError(
            "source-review raw inputs must contain the exact reviewer panel-input copy"
        )
    if (
        not panel_data
        or not panel_data.endswith(b"\n")
        or panel_data.endswith(b"\n\n")
        or b"\n" in panel_data[:-1]
        or b"\r" in panel_data
    ):
        raise PublicationError("source-review panel input must be exactly one JSONL row")
    panel_record = _decode_json_without_duplicate_keys(
        panel_data[:-1], "source-review panel input"
    )
    # The archived mutation receipt is provenance, not an authority token.
    # Older receipt schemas do not bind the panel bytes, validator bytes, or
    # launcher, so reproduce the complete canonical validation here.
    panel_errors = validate_panel_input(panel_record, verify=True)
    if panel_errors:
        raise PublicationError(
            "source-review panel input fails its canonical contract: "
            + " | ".join(panel_errors)
        )
    if "repository_heads" in record:
        repositories = panel_record.get("repositories")
        roles = panel_record.get("repository_roles")
        if (
            panel_record.get("schema_version") != 3
            or not isinstance(repositories, dict)
            or not isinstance(roles, dict)
        ):
            raise PublicationError(
                "generic source review requires a schema-3 panel repository role map"
            )
        reproduced_heads = {
            role: {
                "repository_key": repository_key,
                "head_sha": repositories.get(repository_key, {}).get("head_sha")
                if isinstance(repositories.get(repository_key), dict)
                else None,
            }
            for role, repository_key in roles.items()
        }
        if record.get("repository_heads") != reproduced_heads:
            raise PublicationError(
                "source-review repository_heads differ from the validated panel role map"
            )
    seal_path = Path(str(record["raw_input_seal_path"]))
    seal_data = _read_regular_bytes(seal_path, label="source-review raw-input seal")
    if hashlib.sha256(seal_data).hexdigest() != record["raw_input_seal_sha256"]:
        raise PublicationError("source-review raw-input seal digest drifted")
    seal = _decode_json_without_duplicate_keys(
        seal_data, "source-review raw-input seal"
    )
    validation_path = seal_path.parent / "panel-input-validation-canonical.json"
    validation_data = _read_regular_bytes(
        validation_path, label="source-review panel validation receipt"
    )
    validation = _decode_json_without_duplicate_keys(
        validation_data, "source-review panel validation receipt"
    )
    validation_outcomes = (
        validation.get("named_mutation_outcomes")
        if isinstance(validation, dict)
        else None
    )
    if (
        not isinstance(validation, dict)
        or set(validation)
        != {"schema_version", "status", "named_mutation_outcomes"}
        or type(validation.get("schema_version")) is not int
        or validation["schema_version"] != 1
        or validation.get("status") != "PASS"
        or not isinstance(validation_outcomes, dict)
        or not validation_outcomes
        or any(
            not isinstance(name, str) or result != "PASS"
            for name, result in validation_outcomes.items()
        )
    ):
        raise PublicationError(
            "source-review panel validation receipt must be exact nonempty all-PASS evidence"
        )
    seal_fields = {
        "schema_version",
        "record_type",
        "review_id",
        "sealed_at",
        "inventory_format",
        "inventory_path",
        "inventory_sha256",
        "raw_input_max_files",
        "raw_input_max_total_bytes",
        "raw_input_actual_files",
        "raw_input_actual_total_bytes",
        "panel_input_path",
        "panel_input_sha256",
        "panel_input_validation_sha256",
        "source_guidance_status_before_review",
        "live_installation_status",
        "live_publication_status",
    }
    if (
        not isinstance(seal, dict)
        or set(seal) != seal_fields
        or type(seal.get("schema_version")) is not int
        or seal["schema_version"] != 1
        or seal.get("record_type") != "raw_input_seal"
        or seal.get("review_id") != record.get("review_id")
        or seal.get("inventory_format") != "sha256-size-path-v1"
        or seal.get("inventory_path") != str(inventory_path)
        or seal.get("inventory_sha256") != record.get("raw_input_inventory_sha256")
        or seal.get("panel_input_path") != record.get("panel_input_path")
        or seal.get("panel_input_sha256") != record.get("panel_input_sha256")
        or any(
            seal.get(field) != record.get(field)
            for field in (
                "raw_input_max_files",
                "raw_input_max_total_bytes",
                "raw_input_actual_files",
                "raw_input_actual_total_bytes",
            )
        )
        or not _valid_timestamp(seal.get("sealed_at"))
        or seal.get("panel_input_validation_sha256")
        != hashlib.sha256(validation_data).hexdigest()
        or seal.get("source_guidance_status_before_review") != "NOT_REVIEWED"
        or seal.get("live_installation_status") != "UNCHANGED_PREDECESSOR"
        or seal.get("live_publication_status") != "NOT_RUN"
    ):
        raise PublicationError("source-review raw-input seal does not bind its inputs")


def _require_review_phase_prerequisites(
    records: Sequence[Mapping[str, Any]],
    *,
    review_id: str,
    raw_input_inventory_sha256: str,
    phase: str,
) -> None:
    historical_input = _matching_record(
        records,
        "source_review_input_registered",
        review_id=review_id,
        raw_input_inventory_sha256=raw_input_inventory_sha256,
    )
    generic_input = _matching_record(
        records,
        "raw_input_registered",
        review_id=review_id,
        raw_input_inventory_sha256=raw_input_inventory_sha256,
    )
    if historical_input is None and generic_input is None:
        raise PublicationError(
            "manifest-prefix dispatch requires the registered raw-input digest"
        )
    if historical_input is not None:
        _verify_source_raw_input(historical_input)
        _verify_required_pre_dispatch_validation(records, historical_input)
    if generic_input is not None:
        if (
            records[0].get("manifest_schema") == MANIFEST_SCHEMA
            and generic_input.get("review_boundary")
            != POSTPUBLICATION_REVIEW_BOUNDARY
        ):
            raise PublicationError(
                "manifest-prefix dispatch cannot use raw_input_registered for "
                "prepublication source review"
            )
        _verify_generic_raw_input(generic_input)
    if phase == "dispatch":
        return

    historical_complete = False
    if historical_input is not None:
        panel_id = historical_input.get("panel_id")
        report = _matching_record(
            records,
            "source_review_independent_reports_received",
            review_id=review_id,
            panel_id=panel_id,
            raw_input_inventory_sha256=raw_input_inventory_sha256,
        )
        challenge = _matching_record(
            records,
            "source_review_challenges_received",
            review_id=review_id,
            panel_id=panel_id,
            raw_input_inventory_sha256=raw_input_inventory_sha256,
        )
        verdict = _matching_record(
            records,
            "source_review_judge_verdict_received",
            review_id=review_id,
            panel_id=panel_id,
            raw_input_inventory_sha256=raw_input_inventory_sha256,
        )
        judge_artifact = verdict.get("judge") if verdict is not None else None
        historical_complete = (
            report is not None
            and challenge is not None
            and verdict is not None
            and historical_input.get("expected_reports") == report.get("expected_reports")
            and report.get("expected_reports") == report.get("received_reports")
            and report.get("received_reports") == len(report.get("reports", []))
            and historical_input.get("expected_challenge_responses")
            == report.get("expected_challenge_responses")
            and report.get("expected_challenge_responses")
            == challenge.get("expected_challenge_responses")
            and challenge.get("expected_challenge_responses")
            == challenge.get("received_challenge_responses")
            and challenge.get("received_challenge_responses")
            == len(challenge.get("challenges", []))
            and historical_input.get("expected_judges") == 1
            and report.get("findings_received")
            == sum(report.get(field, -1) for field in (
                "p0_received", "p1_received", "p2_received", "p3_received"
            ))
            and report.get("findings_received") == challenge.get("findings_received")
            and challenge.get("findings_received") == challenge.get("findings_answered")
            and report.get("state") in {"CHALLENGE_PENDING", "REPORTS_RECEIVED"}
            and challenge.get("state") in {"JUDGE_PENDING", "CHALLENGES_RECEIVED"}
            and verdict.get("state") in {"SOURCE_ACCEPTED", "JUDGED"}
            and verdict.get("source_guidance_status")
            in {"SOURCE_GUIDANCE_ACCEPTED", "REVIEWED"}
            and isinstance(judge_artifact, dict)
            and judge_artifact.get("verdict") == "ACCEPT"
        )
        if historical_complete:
            report_identities = {
                (artifact.get("path"), artifact.get("sha256"))
                for artifact in report["reports"]
            }
            challenge_identities = {
                (artifact.get("path"), artifact.get("sha256"))
                for artifact in challenge["challenges"]
            }
            report_roles = [artifact.get("role") for artifact in report["reports"]]
            challenge_roles = [
                artifact.get("role") for artifact in challenge["challenges"]
            ]
            if (
                report_identities & challenge_identities
                or len(report_identities) != len(report["reports"])
                or len(challenge_identities) != len(challenge["challenges"])
                or len(set(report_roles)) != len(report_roles)
                or len(set(challenge_roles)) != len(challenge_roles)
            ):
                raise PublicationError(
                    "source-review reports and challenges must be distinct independent artifacts"
                )
            for index, artifact in enumerate(report["reports"]):
                _verify_artifact_record(artifact, f"source-review report {index}")
            for index, artifact in enumerate(challenge["challenges"]):
                _verify_artifact_record(artifact, f"source-review challenge {index}")
            judge_receipt = _verify_judge_receipt(
                judge_artifact,
                records=records,
                review_id=review_id,
                raw_input_inventory_sha256=raw_input_inventory_sha256,
                expected_role=judge_artifact.get("role"),
                expected_verdict="ACCEPT",
                verdict_record=verdict,
                label="source-review judge verdict",
            )
            participants = [*report["reports"], *challenge["challenges"]]
            if any(
                not isinstance(artifact.get("role"), str)
                or not IDENTITY_RE.fullmatch(artifact["role"])
                for artifact in participants
            ):
                raise PublicationError("source-review participant roles are malformed")
            participant_roles = {artifact["role"] for artifact in participants}
            if judge_receipt["judge_role"] in participant_roles:
                raise PublicationError("source-review judge must be independent")

    generic_reports = [
        record
        for record in records
        if record.get("record_type") == "review_report_registered"
        and record.get("review_id") == review_id
    ]
    generic_challenges = [
        record
        for record in records
        if record.get("record_type") == "challenge_response_registered"
        and record.get("review_id") == review_id
    ]
    generic_verdicts = [
        record
        for record in records
        if record.get("record_type") == "judge_verdict_registered"
        and record.get("review_id") == review_id
    ]
    generic_complete = (
        generic_input is not None
        and bool(generic_reports)
        and bool(generic_challenges)
        and bool(generic_verdicts)
        and all(record.get("state") == "RECEIVED" for record in generic_reports)
        and all(record.get("state") == "RECEIVED" for record in generic_challenges)
        and all(
            record.get("state") == "RECEIVED" and record.get("verdict") == "ACCEPT"
            for record in generic_verdicts
        )
    )
    if generic_complete:
        report_identities = {
            (artifact.get("path"), artifact.get("sha256"))
            for artifact in generic_reports
        }
        challenge_identities = {
            (artifact.get("path"), artifact.get("sha256"))
            for artifact in generic_challenges
        }
        if (
            report_identities & challenge_identities
            or len(report_identities) != len(generic_reports)
            or len(challenge_identities) != len(generic_challenges)
        ):
            raise PublicationError(
                "postpublication reports and challenges must be distinct artifacts"
            )
        for index, artifact in enumerate(generic_reports):
            _verify_artifact_record(artifact, f"review report {index}")
        for index, artifact in enumerate(generic_challenges):
            _verify_artifact_record(artifact, f"challenge response {index}")
        participant_roles = {
            *(
                record["reviewer_role"]
                for record in generic_reports
            ),
            *(
                record["participant_role"]
                for record in generic_challenges
            ),
        }
        for index, artifact in enumerate(generic_verdicts):
            _verify_judge_receipt(
                artifact,
                records=records,
                review_id=review_id,
                raw_input_inventory_sha256=raw_input_inventory_sha256,
                expected_role=artifact["judge_role"],
                expected_verdict=artifact["verdict"],
                verdict_record=artifact,
                label=f"judge verdict {index}",
            )
            if artifact["judge_role"] in participant_roles:
                raise PublicationError("postpublication judge must be independent")
    if not historical_complete and not generic_complete:
        raise PublicationError(
            "manifest-prefix judgment requires report, challenge, and judge rows"
        )
    if phase != "acceptance":
        return
    if (
        not generic_complete
        or generic_input.get("review_boundary") != POSTPUBLICATION_REVIEW_BOUNDARY
    ):
        raise PublicationError(
            "manifest-prefix acceptance requires a complete postpublication review"
        )
    summary = _matching_record(
        records,
        "review_summary",
        review_id=review_id,
        raw_input_inventory_sha256=raw_input_inventory_sha256,
        state="REVIEW_COMPLETE",
    )
    if (
        summary is None
        or summary.get("independent_reports_expected")
        != summary.get("independent_reports_received")
        or summary.get("challenge_participants_expected")
        != summary.get("challenge_participants_received")
        or summary.get("judge_reports_expected") != summary.get("judge_reports_received")
        or summary.get("findings_received") != summary.get("findings_answered")
        or summary.get("findings_unresolved") != 0
        or summary.get("independent_reports_received") != len(generic_reports)
        or summary.get("challenge_participants_received") != len(generic_challenges)
        or summary.get("judge_reports_received") != len(generic_verdicts)
    ):
        raise PublicationError(
            "manifest-prefix acceptance requires a complete clean review summary"
        )


def _validate_record_lifecycle(
    records: Sequence[Mapping[str, Any]], record: Mapping[str, Any]
) -> None:
    record_type = record.get("record_type")
    review_id = record.get("review_id")
    if record_type == "source_review_independent_reports_received":
        if _matching_record(
            records, "source_review_input_registered", review_id=review_id
        ) is None:
            raise PublicationError("source review reports require registered input")
        if any(
            item.get("review_id") == review_id
            and item.get("record_type")
            in {"source_review_challenges_received", "source_review_judge_verdict_received"}
            for item in records
        ):
            raise PublicationError("source review reports are out of lifecycle order")
    elif record_type == "source_review_challenges_received":
        if _matching_record(
            records, "source_review_independent_reports_received", review_id=review_id
        ) is None:
            raise PublicationError("source review challenge requires reports first")
        if _matching_record(
            records, "source_review_judge_verdict_received", review_id=review_id
        ) is not None:
            raise PublicationError("source review challenge is out of lifecycle order")
    elif record_type == "source_review_judge_verdict_received":
        if _matching_record(
            records, "source_review_challenges_received", review_id=review_id
        ) is None:
            raise PublicationError("source review judge requires challenges first")
    elif record_type == "review_report_registered":
        if _matching_record(records, "raw_input_registered", review_id=review_id) is None:
            raise PublicationError("review report requires registered raw input")
        if any(
            item.get("review_id") == review_id
            and item.get("record_type")
            in {"challenge_response_registered", "judge_verdict_registered", "review_summary"}
            for item in records
        ):
            raise PublicationError("review report is out of lifecycle order")
    elif record_type == "challenge_response_registered":
        if _matching_record(records, "review_report_registered", review_id=review_id) is None:
            raise PublicationError("challenge response requires reports first")
        if any(
            item.get("review_id") == review_id
            and item.get("record_type") in {"judge_verdict_registered", "review_summary"}
            for item in records
        ):
            raise PublicationError("challenge response is out of lifecycle order")
    elif record_type == "judge_verdict_registered":
        if _matching_record(
            records, "challenge_response_registered", review_id=review_id
        ) is None:
            raise PublicationError("judge verdict requires challenges first")
        if _matching_record(records, "review_summary", review_id=review_id) is not None:
            raise PublicationError("judge verdict is out of lifecycle order")
    elif record_type == "review_summary":
        if _matching_record(records, "judge_verdict_registered", review_id=review_id) is None:
            raise PublicationError("review summary requires judge verdicts first")
    elif record_type == "installed_publication_reservation_intent":
        prepare = _matching_record(
            records,
            "prepare_receipt_registered",
            operation_id=record.get("operation_id"),
            generation_id=record.get("generation_id"),
            receipt_path=record.get("prepare_receipt_path"),
            receipt_sha256=record.get("prepare_receipt_sha256"),
            mutation_outcome="NO_LIVE_MUTATION_PREPARED",
            state="PREPARED",
        )
        if prepare is None:
            raise PublicationError(
                "publication reservation requires a matching registered prepare receipt"
            )
    elif record_type == "installed_publication_terminal":
        reservation = _matching_record(
            records,
            "installed_publication_reservation_intent",
            operation_id=record.get("operation_id"),
            generation_id=record.get("generation_id"),
        )
        if reservation is None:
            raise PublicationError(
                "publication terminal requires its prior reservation intent"
            )
        for field in ("installed_root", "lock_path"):
            if record.get(field) != reservation.get(field):
                raise PublicationError(
                    f"publication terminal changes reservation {field}"
                )
        receipt = record.get("publication_receipt")
        if not isinstance(receipt, dict) or any(
            field in receipt and receipt.get(field) != expected
            for field, expected in (
                ("operation_id", record.get("operation_id")),
                ("generation_id", record.get("generation_id")),
                ("terminal_state", record.get("terminal_state")),
                ("reservation_state", record.get("reservation_state")),
            )
        ):
            raise PublicationError("publication terminal receipt changes lifecycle identity")
    elif record_type == "installed_publication_accepted":
        terminal = _matching_record(
            records,
            "installed_publication_terminal",
            operation_id=record.get("operation_id"),
            generation_id=record.get("generation_id"),
        )
        if terminal is None:
            raise PublicationError(
                "publication acceptance requires its prior terminal record"
            )
        for field in ("installed_root", "lock_path", "terminal_state"):
            if record.get(field) != terminal.get(field):
                raise PublicationError(
                    f"publication acceptance changes terminal {field}"
                )
        expected_terminal_receipt = {
            "path": terminal.get("publication_receipt_path"),
            "sha256": terminal.get("publication_receipt_sha256"),
        }
        if record.get("terminal_receipt") != expected_terminal_receipt:
            raise PublicationError(
                "publication acceptance changes terminal receipt identity"
            )
        inventory = record.get("acceptance_inventory")
        if not isinstance(inventory, dict):
            raise PublicationError("publication acceptance lacks inventory evidence")
        for field, expected in (
            ("operation_id", record.get("operation_id")),
            ("generation_id", record.get("generation_id")),
            ("terminal_state", record.get("terminal_state")),
            ("installed_root", record.get("installed_root")),
            ("terminal_receipt", record.get("terminal_receipt")),
        ):
            if field in inventory and inventory.get(field) != expected:
                raise PublicationError(
                    f"publication acceptance inventory changes {field}"
                )
        review_id = inventory.get("review_id")
        raw_digest = inventory.get("raw_input_inventory_sha256")
        if not isinstance(review_id, str) or not isinstance(raw_digest, str):
            raise PublicationError("publication acceptance inventory lacks review identity")
        _require_review_phase_prerequisites(
            records,
            review_id=review_id,
            raw_input_inventory_sha256=raw_digest,
            phase="acceptance",
        )
        phases = [
            item.get("phase")
            for item in records
            if item.get("record_type") == "manifest_prefix_registered"
            and item.get("review_id") == review_id
        ]
        if phases != list(PHASE_ORDER):
            raise PublicationError(
                "publication acceptance requires the complete registered phase chain"
            )


def parse_finalization_jsonl(data: bytes, path: Path) -> List[Dict[str, Any]]:
    """Parse the complete grammar without trusting a filename or caller label."""
    if not data or not data.endswith(b"\n") or data.endswith(b"\n\n"):
        raise PublicationError(
            f"finalization manifest must be non-empty JSONL with one final LF: {path}"
        )
    records: List[Dict[str, Any]] = []
    finalization_id: Optional[str] = None
    writer_controller_id: Optional[str] = None
    manifest_schema: Optional[str] = None
    envelope_schema_version: Optional[int] = None
    raw_digest_by_review: Dict[str, str] = {}
    seen_identities: Dict[Tuple[Any, ...], Mapping[str, Any]] = {}
    for row, raw_line in enumerate(data[:-1].split(b"\n"), 1):
        if not raw_line:
            raise PublicationError(f"finalization manifest has an empty row at {row}")
        value = _decode_json_without_duplicate_keys(
            raw_line, f"finalization manifest row {row}"
        )
        if not isinstance(value, dict):
            raise PublicationError(f"finalization manifest row {row} is not an object")
        for field in ENVELOPE_FIELDS:
            if field not in value:
                raise PublicationError(f"finalization manifest row {row} lacks {field}")
        if row == 1:
            if type(value.get("schema_version")) is not int:
                raise PublicationError(
                    "finalization manifest header has unsupported schema identity"
                )
            observed_contract = (value.get("schema_version"), value.get("manifest_schema"))
            if observed_contract == (SCHEMA_VERSION, MANIFEST_SCHEMA):
                envelope_schema_version, manifest_schema = observed_contract
            elif observed_contract == (LEGACY_SCHEMA_VERSION, LEGACY_MANIFEST_SCHEMA):
                envelope_schema_version, manifest_schema = observed_contract
            else:
                raise PublicationError(
                    "finalization manifest header has unsupported schema identity"
                )
        elif (
            type(value.get("schema_version")) is not int
            or value.get("schema_version") != envelope_schema_version
        ):
            raise PublicationError(f"finalization manifest row {row} has schema drift")
        if value.get("manifest_schema") != manifest_schema:
            raise PublicationError(f"finalization manifest row {row} has manifest-schema drift")
        if type(value.get("sequence")) is not int or value["sequence"] != row:
            raise PublicationError(
                f"finalization manifest sequence must be contiguous from 1 at row {row}"
            )
        if not _valid_timestamp(value.get("recorded_at")):
            raise PublicationError(f"finalization manifest row {row} has invalid UTC time")
        record_type = value.get("record_type")
        if not isinstance(record_type, str):
            raise PublicationError(f"finalization manifest row {row} lacks record type")
        current_finalization_id = _require_identity(
            value.get("finalization_id"), f"row {row} finalization_id"
        )
        current_writer = _require_identity(
            value.get("writer_controller_id"), f"row {row} writer_controller_id"
        )
        if row == 1:
            if record_type != "manifest_header":
                raise PublicationError("finalization manifest row 1 must be manifest_header")
            allowed_header = ENVELOPE_FIELDS | {"state"}
            if set(value) - allowed_header:
                raise PublicationError("finalization manifest header has undeclared fields")
            if "state" in value and (
                not isinstance(value["state"], str) or not value["state"].strip()
            ):
                raise PublicationError("finalization manifest header state is invalid")
            finalization_id = current_finalization_id
            writer_controller_id = current_writer
        else:
            if record_type == "manifest_header":
                raise PublicationError("finalization manifest has more than one header")
            if current_finalization_id != finalization_id:
                raise PublicationError("finalization manifest mixes finalization IDs")
            if current_writer != writer_controller_id:
                raise PublicationError("finalization manifest mixes writer controller IDs")
            assert manifest_schema is not None
            _validate_record_payload(value, row, manifest_schema=manifest_schema)
            _validate_record_lifecycle(records, value)
            schemas = (
                LEGACY_RECORD_SCHEMAS
                if manifest_schema == LEGACY_MANIFEST_SCHEMA
                else RECORD_SCHEMAS
            )
            schema = schemas[record_type]
            identity = (record_type,) + tuple(value.get(field) for field in schema.identity_fields)
            previous = seen_identities.get(identity)
            if previous is not None and dict(previous) != value:
                raise PublicationError(
                    f"finalization manifest has conflicting duplicate {record_type} identity"
                )
            if previous is not None:
                raise PublicationError(
                    f"finalization manifest repeats {record_type} identity"
                )
            seen_identities[identity] = value
            review_id = value.get("review_id")
            raw_digest = value.get("raw_input_inventory_sha256")
            if isinstance(review_id, str) and isinstance(raw_digest, str):
                prior_digest = raw_digest_by_review.setdefault(review_id, raw_digest)
                if prior_digest != raw_digest:
                    raise PublicationError(
                        f"finalization manifest changes raw-input digest for {review_id}"
                    )
        records.append(value)
    return records


def _read_descriptor(descriptor: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: List[bytes] = []
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def _prefix_identity(
    path: Path, data: bytes, records: Sequence[Mapping[str, Any]]
) -> Dict[str, Any]:
    if not records:
        raise PublicationError("finalization prefix has no records")
    raw_digest = None
    review_id = None
    for record in reversed(records):
        if isinstance(record.get("raw_input_inventory_sha256"), str):
            raw_digest = record["raw_input_inventory_sha256"]
            review_id = record.get("review_id")
            break
    return {
        "path": str(path),
        "prefix_bytes": len(data),
        "prefix_sha256": hashlib.sha256(data).hexdigest(),
        "record_count": len(records),
        "last_sequence": records[-1]["sequence"],
        "last_record_type": records[-1]["record_type"],
        "finalization_id": records[0]["finalization_id"],
        "writer_controller_id": records[0]["writer_controller_id"],
        "manifest_schema": records[0]["manifest_schema"],
        "review_id": review_id,
        "raw_input_inventory_sha256": raw_digest,
    }


def _receipt_value(record: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": record["schema_version"],
        "record_type": PREFIX_RECEIPT_TYPE,
        "phase": record["phase"],
        "manifest_prefix_path": record["manifest_prefix_path"],
        "manifest_prefix_sha256": record["manifest_prefix_sha256"],
        "manifest_prefix_byte_count": record["manifest_prefix_byte_count"],
        "manifest_prefix_record_count": record["manifest_prefix_record_count"],
        "manifest_prefix_last_sequence": record["manifest_prefix_last_sequence"],
        "finalization_id": record["finalization_id"],
        "writer_controller_id": record["writer_controller_id"],
        "review_id": record["review_id"],
        "raw_input_inventory_sha256": record["raw_input_inventory_sha256"],
    }


def _validate_registrations(
    manifest_path: Path, data: bytes, records: Sequence[Mapping[str, Any]]
) -> None:
    lines = data.splitlines(keepends=True)
    registrations: Dict[str, List[str]] = {}
    for index, record in enumerate(records):
        if record.get("record_type") != "manifest_prefix_registered":
            continue
        review_id = str(record["review_id"])
        phase = str(record["phase"])
        observed_order = registrations.setdefault(review_id, [])
        if phase in observed_order or phase != PHASE_ORDER[len(observed_order)]:
            raise PublicationError("manifest-prefix phases are duplicated or out of order")
        observed_order.append(phase)
        prefix_data = b"".join(lines[:index])
        prefix_records = records[:index]
        _require_review_phase_prerequisites(
            prefix_records,
            review_id=review_id,
            raw_input_inventory_sha256=str(record["raw_input_inventory_sha256"]),
            phase=phase,
        )
        if not prefix_records:
            raise PublicationError("manifest-prefix registration cannot precede the header")
        if (
            record["manifest_prefix_byte_count"] != len(prefix_data)
            or record["manifest_prefix_record_count"] != len(prefix_records)
            or record["manifest_prefix_last_sequence"] != prefix_records[-1]["sequence"]
            or record["manifest_prefix_sha256"]
            != hashlib.sha256(prefix_data).hexdigest()
        ):
            raise PublicationError("manifest-prefix registration does not bind its prefix")
        prefix_path = Path(str(record["manifest_prefix_path"]))
        if prefix_path == manifest_path:
            raise PublicationError("manifest-prefix snapshot must differ from live manifest")
        if _read_regular_bytes(prefix_path, label="manifest-prefix snapshot") != prefix_data:
            raise PublicationError("manifest-prefix snapshot bytes drifted")
        receipt_path = Path(str(record["receipt_path"]))
        receipt_data = _read_regular_bytes(receipt_path, label="manifest-prefix receipt")
        if hashlib.sha256(receipt_data).hexdigest() != record["receipt_sha256"]:
            raise PublicationError("manifest-prefix receipt digest drifted")
        receipt = _decode_json_without_duplicate_keys(
            receipt_data, "manifest-prefix receipt"
        )
        if receipt != _receipt_value(record):
            raise PublicationError("manifest-prefix receipt does not match registration")


def _open_manifest(path: Path, flags: int) -> Tuple[int, os.stat_result]:
    path = _safe_absolute(path, label="finalization manifest", must_exist=True)
    observed = os.lstat(path)
    if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
        raise PublicationError("finalization manifest must be a regular single-link file")
    descriptor = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0))
    opened = os.fstat(descriptor)
    if (
        (observed.st_dev, observed.st_ino) != (opened.st_dev, opened.st_ino)
        or not stat.S_ISREG(opened.st_mode)
        or opened.st_nlink != 1
    ):
        os.close(descriptor)
        raise PublicationError("finalization manifest changed before it was opened")
    return descriptor, opened


def initialize_manifest(
    path: Path,
    *,
    finalization_id: str,
    writer_controller_id: str,
    state: Optional[str] = None,
    recorded_at: Optional[str] = None,
) -> Dict[str, Any]:
    path = _safe_absolute(path, label="finalization manifest")
    _require_identity(finalization_id, "finalization_id")
    _require_identity(writer_controller_id, "writer_controller_id")
    stamp = recorded_at or utc_now()
    if not _valid_timestamp(stamp):
        raise PublicationError("finalization header recorded_at must be UTC RFC3339")
    record: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "manifest_schema": MANIFEST_SCHEMA,
        "sequence": 1,
        "recorded_at": stamp,
        "record_type": "manifest_header",
        "finalization_id": finalization_id,
        "writer_controller_id": writer_controller_id,
    }
    if state is not None:
        if not isinstance(state, str) or not state.strip():
            raise PublicationError("finalization header state must be nonempty")
        record["state"] = state
    encoded = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise PublicationError("finalization header write made no progress")
            view = view[written:]
        os.fsync(descriptor)
        if os.fstat(descriptor).st_nlink != 1:
            raise PublicationError("finalization manifest gained a hard link during init")
    finally:
        os.close(descriptor)
    _fsync_parent(path)
    return {"record": record, "manifest_sha256": hashlib.sha256(encoded).hexdigest()}


def finalization_manifest_prefix(
    path: Path,
    *,
    through_sequence: Optional[int] = None,
    required_prefix: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    path = Path(path)
    descriptor, opened = _open_manifest(path, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        data = _read_descriptor(descriptor)
        after = os.fstat(descriptor)
        if (
            (opened.st_dev, opened.st_ino, opened.st_size)
            != (after.st_dev, after.st_ino, after.st_size)
            or after.st_nlink != 1
            or len(data) != after.st_size
        ):
            raise PublicationError("finalization manifest changed during prefix read")
        records = parse_finalization_jsonl(data, path)
        _validate_registrations(path, data, records)
        prefix_data = data
        prefix_records: Sequence[Mapping[str, Any]] = records
        if through_sequence is not None:
            _require_positive_int(through_sequence, "bounded sequence")
            if through_sequence > len(records):
                raise PublicationError("finalization manifest lacks requested sequence")
            prefix_records = records[:through_sequence]
            prefix_data = b"".join(data.splitlines(keepends=True)[:through_sequence])
        observed_prefix = _prefix_identity(path, prefix_data, prefix_records)
        if required_prefix is not None:
            required = dict(required_prefix)
            byte_count = required.get("prefix_bytes")
            if (
                set(required) != set(observed_prefix)
                or not isinstance(byte_count, int)
                or isinstance(byte_count, bool)
                or byte_count <= 0
                or byte_count > len(data)
            ):
                raise PublicationError("required finalization-manifest prefix is malformed")
            required_data = data[:byte_count]
            required_records = parse_finalization_jsonl(required_data, path)
            if _prefix_identity(path, required_data, required_records) != required:
                raise PublicationError(
                    "finalization manifest no longer extends required durable prefix"
                )
        return observed_prefix
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def validate_manifest(path: Path) -> Dict[str, Any]:
    return finalization_manifest_prefix(path)


def _record_identity(record_type: str, payload: Mapping[str, Any]) -> Tuple[Any, ...]:
    schema = RECORD_SCHEMAS[record_type]
    if schema.identity_fields:
        return (record_type,) + tuple(payload.get(field) for field in schema.identity_fields)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return (record_type, hashlib.sha256(canonical).hexdigest())


def _write_all(descriptor: int, encoded: bytes) -> None:
    view = memoryview(encoded)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise PublicationError("finalization append made no progress")
        view = view[written:]


def _fsync_parent(path: Path) -> None:
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def append_finalization_record(
    manifest_path: Path,
    *,
    record_type: str,
    payload: Mapping[str, Any],
    writer_controller_id: Optional[str] = None,
    expected_prefix: Optional[Mapping[str, Any]] = None,
    required_ancestor_prefix: Optional[Mapping[str, Any]] = None,
    predecessor_payload_field: Optional[str] = None,
    recorded_at: Optional[str] = None,
) -> Dict[str, Any]:
    if record_type not in RECORD_SCHEMAS or record_type == "manifest_prefix_registered":
        raise PublicationError("record type must use a registered append path")
    if expected_prefix is not None and required_ancestor_prefix is not None:
        raise PublicationError("append cannot require exact and ancestor prefixes together")
    if predecessor_payload_field is not None and required_ancestor_prefix is None:
        raise PublicationError("predecessor binding requires an ancestor prefix")
    if not isinstance(payload, Mapping):
        raise PublicationError("finalization payload must be an object")
    descriptor, opened = _open_manifest(
        Path(manifest_path), os.O_RDWR | os.O_APPEND
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        original = _read_descriptor(descriptor)
        if os.fstat(descriptor).st_nlink != 1:
            raise PublicationError("finalization manifest gained a hard link during append")
        records = parse_finalization_jsonl(original, Path(manifest_path))
        if records[0]["manifest_schema"] != MANIFEST_SCHEMA:
            raise PublicationError(
                "legacy finalization manifests are read-only; initialize a generic v2 manifest"
            )
        _validate_registrations(Path(manifest_path), original, records)
        stable_writer = records[0]["writer_controller_id"]
        if writer_controller_id is not None and writer_controller_id != stable_writer:
            raise PublicationError("controller append writer does not match manifest header")
        identity = _record_identity(record_type, payload)
        lines = original.splitlines(keepends=True)

        def prefix_at(count: int) -> Tuple[bytes, Sequence[Mapping[str, Any]]]:
            return b"".join(lines[:count]), records[:count]

        for index, existing in enumerate(records):
            if existing.get("record_type") != record_type:
                continue
            existing_payload = {key: value for key, value in existing.items() if key not in ENVELOPE_FIELDS}
            if _record_identity(record_type, existing_payload) != identity:
                continue
            preceding_data, preceding_records = prefix_at(index)
            effective_payload = dict(payload)
            if predecessor_payload_field is not None:
                effective_payload[predecessor_payload_field] = _prefix_identity(
                    Path(manifest_path), preceding_data, preceding_records
                )
            if existing_payload != effective_payload:
                raise PublicationError("existing finalization record conflicts with retry")
            if expected_prefix is not None and _prefix_identity(
                Path(manifest_path), preceding_data, preceding_records
            ) != dict(expected_prefix):
                raise PublicationError("existing record does not extend required exact prefix")
            if required_ancestor_prefix is not None:
                _require_ancestor(
                    Path(manifest_path), preceding_data, dict(required_ancestor_prefix)
                )
            through = b"".join(lines[: index + 1])
            return {
                "record": existing,
                "manifest_sha256": hashlib.sha256(through).hexdigest(),
                "manifest_prefix_bytes": len(through),
                "appended": False,
            }
        observed_prefix = _prefix_identity(Path(manifest_path), original, records)
        if expected_prefix is not None and observed_prefix != dict(expected_prefix):
            raise PublicationError("finalization manifest changed after bounded prefix")
        if required_ancestor_prefix is not None:
            _require_ancestor(Path(manifest_path), original, dict(required_ancestor_prefix))
        effective_payload = dict(payload)
        if predecessor_payload_field is not None:
            if predecessor_payload_field in effective_payload:
                raise PublicationError("predecessor payload field is reserved")
            effective_payload[predecessor_payload_field] = observed_prefix
        stamp = recorded_at or utc_now()
        record: Dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "manifest_schema": MANIFEST_SCHEMA,
            "sequence": len(records) + 1,
            "recorded_at": stamp,
            "record_type": record_type,
            "finalization_id": records[0]["finalization_id"],
            "writer_controller_id": stable_writer,
            **effective_payload,
        }
        if not _valid_timestamp(stamp):
            raise PublicationError("finalization append recorded_at must be UTC RFC3339")
        _validate_record_payload(record, len(records) + 1)
        _validate_record_lifecycle(records, record)
        encoded = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        combined = original + encoded
        combined_records = parse_finalization_jsonl(combined, Path(manifest_path))
        _validate_registrations(Path(manifest_path), combined, combined_records)
        _write_all(descriptor, encoded)
        os.fsync(descriptor)
        if os.fstat(descriptor).st_nlink != 1:
            raise PublicationError("finalization manifest gained a hard link during append")
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


def _require_ancestor(path: Path, data: bytes, required: Mapping[str, Any]) -> None:
    byte_count = required.get("prefix_bytes")
    if (
        not isinstance(byte_count, int)
        or isinstance(byte_count, bool)
        or byte_count <= 0
        or byte_count > len(data)
    ):
        raise PublicationError("required ancestor prefix is malformed")
    ancestor_data = data[:byte_count]
    ancestor_records = parse_finalization_jsonl(ancestor_data, path)
    if _prefix_identity(path, ancestor_data, ancestor_records) != dict(required):
        raise PublicationError("finalization manifest no longer extends observed ancestor")


def _write_once_or_verify(path: Path, data: bytes, *, label: str) -> None:
    path = _safe_absolute(path, label=label)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        if _read_regular_bytes(path, label=label) != data:
            raise PublicationError(f"existing {label} conflicts with retry")
        return
    try:
        _write_all(descriptor, data)
        os.fsync(descriptor)
        if os.fstat(descriptor).st_nlink != 1:
            raise PublicationError(f"{label} gained a hard link while writing")
    finally:
        os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def seal_manifest_prefix(
    manifest_path: Path,
    *,
    writer_controller_id: str,
    review_id: str,
    phase: str,
    raw_input_inventory_sha256: str,
    prefix_output: Path,
    receipt_output: Path,
) -> Dict[str, Any]:
    _require_identity(writer_controller_id, "writer_controller_id")
    _require_identity(review_id, "review_id")
    if phase not in PHASE_ORDER:
        raise PublicationError("manifest-prefix phase is unsupported")
    _require_sha256(raw_input_inventory_sha256, "raw-input inventory SHA-256")
    manifest_path = Path(manifest_path)
    prefix_output = _safe_absolute(prefix_output, label="manifest-prefix output")
    receipt_output = _safe_absolute(receipt_output, label="manifest-prefix receipt output")
    if len({manifest_path, prefix_output, receipt_output}) != 3:
        raise PublicationError("manifest and prefix evidence paths must be distinct")
    descriptor, opened = _open_manifest(manifest_path, os.O_RDWR | os.O_APPEND)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        original = _read_descriptor(descriptor)
        records = parse_finalization_jsonl(original, manifest_path)
        if records[0]["manifest_schema"] != MANIFEST_SCHEMA:
            raise PublicationError(
                "legacy finalization manifests are read-only; initialize a generic v2 manifest"
            )
        _validate_registrations(manifest_path, original, records)
        if records[0]["writer_controller_id"] != writer_controller_id:
            raise PublicationError("seal writer does not match manifest header")
        prior = [
            record
            for record in records
            if record.get("record_type") == "manifest_prefix_registered"
            and record.get("review_id") == review_id
        ]
        prior_phases = [record["phase"] for record in prior]
        if phase in prior_phases:
            existing = prior[prior_phases.index(phase)]
            if existing is not records[-1]:
                raise PublicationError("completed phase seal cannot be replayed after later rows")
            if (
                existing["raw_input_inventory_sha256"] != raw_input_inventory_sha256
                or existing["manifest_prefix_path"] != str(prefix_output)
                or existing["receipt_path"] != str(receipt_output)
            ):
                raise PublicationError("phase seal retry conflicts with registered evidence")
            _validate_registrations(manifest_path, original, records)
            return {
                "record": existing,
                "prefix": _prefix_identity(
                    manifest_path,
                    original[: existing["manifest_prefix_byte_count"]],
                    records[: existing["manifest_prefix_record_count"]],
                ),
                "receipt": _receipt_value(existing),
                "appended": False,
            }
        if phase != PHASE_ORDER[len(prior_phases)]:
            raise PublicationError("manifest-prefix phase is skipped or out of order")
        _require_review_phase_prerequisites(
            records,
            review_id=review_id,
            raw_input_inventory_sha256=raw_input_inventory_sha256,
            phase=phase,
        )
        current = _prefix_identity(manifest_path, original, records)
        registration_payload: Dict[str, Any] = {
            "review_id": review_id,
            "phase": phase,
            "manifest_prefix_path": str(prefix_output),
            "manifest_prefix_sha256": current["prefix_sha256"],
            "manifest_prefix_byte_count": current["prefix_bytes"],
            "manifest_prefix_record_count": current["record_count"],
            "manifest_prefix_last_sequence": current["last_sequence"],
            "receipt_path": str(receipt_output),
            "receipt_sha256": "0" * 64,
            "raw_input_inventory_sha256": raw_input_inventory_sha256,
        }
        provisional = {
            "schema_version": SCHEMA_VERSION,
            "manifest_schema": MANIFEST_SCHEMA,
            "sequence": len(records) + 1,
            "recorded_at": utc_now(),
            "record_type": "manifest_prefix_registered",
            "finalization_id": records[0]["finalization_id"],
            "writer_controller_id": writer_controller_id,
            **registration_payload,
        }
        receipt = _receipt_value(provisional)
        receipt_bytes = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        provisional["receipt_sha256"] = hashlib.sha256(receipt_bytes).hexdigest()
        # receipt_sha256 is deliberately not part of the receipt it hashes.
        _write_once_or_verify(prefix_output, original, label="manifest-prefix snapshot")
        _write_once_or_verify(receipt_output, receipt_bytes, label="manifest-prefix receipt")
        _validate_record_payload(provisional, len(records) + 1)
        encoded = (json.dumps(provisional, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        combined = original + encoded
        combined_records = parse_finalization_jsonl(combined, manifest_path)
        _validate_registrations(manifest_path, combined, combined_records)
        _write_all(descriptor, encoded)
        os.fsync(descriptor)
        if os.fstat(descriptor).st_nlink != 1:
            raise PublicationError("finalization manifest gained a hard link during seal")
        return {
            "record": provisional,
            "prefix": current,
            "receipt": receipt,
            "appended": True,
        }
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def latest_phase_registration(
    manifest_path: Path, *, phase: str
) -> Dict[str, Any]:
    if phase not in PHASE_ORDER:
        raise PublicationError("manifest-prefix phase is unsupported")
    data = _read_regular_bytes(Path(manifest_path), label="finalization manifest")
    records = parse_finalization_jsonl(data, Path(manifest_path))
    _validate_registrations(Path(manifest_path), data, records)
    if records[-1].get("record_type") != "manifest_prefix_registered":
        raise PublicationError("inventory phase requires the latest registered manifest prefix")
    registration = records[-1]
    if registration.get("phase") != phase:
        raise PublicationError("latest manifest-prefix registration is for another phase")
    return dict(registration)


def _payload_file(path: Path) -> Dict[str, Any]:
    data = _read_regular_bytes(path, label="controller payload file")
    value = _decode_json_without_duplicate_keys(data, "controller payload file")
    if not isinstance(value, dict):
        raise PublicationError("controller payload file must contain one object")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    initialize = subparsers.add_parser("init", help="create the exact manifest header")
    initialize.add_argument("--manifest", required=True, type=Path)
    initialize.add_argument("--finalization-id", required=True)
    initialize.add_argument("--writer-controller-id", required=True)
    initialize.add_argument("--state")
    append = subparsers.add_parser("append", help="append one controller-owned row")
    append.add_argument("--manifest", required=True, type=Path)
    append.add_argument("--writer-controller-id", required=True)
    append.add_argument("--record-type", required=True, choices=sorted(CONTROLLER_RECORD_TYPES))
    append.add_argument("--payload-file", required=True, type=Path)
    seal = subparsers.add_parser("seal-prefix", help="seal and register one panel phase")
    seal.add_argument("--manifest", required=True, type=Path)
    seal.add_argument("--writer-controller-id", required=True)
    seal.add_argument("--review-id", required=True)
    seal.add_argument("--phase", choices=PHASE_ORDER, required=True)
    seal.add_argument("--raw-input-inventory-sha256", required=True)
    seal.add_argument("--prefix-output", required=True, type=Path)
    seal.add_argument("--receipt-output", required=True, type=Path)
    validate = subparsers.add_parser("validate", help="validate the complete manifest")
    validate.add_argument("--manifest", required=True, type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "init":
            result = initialize_manifest(
                args.manifest,
                finalization_id=args.finalization_id,
                writer_controller_id=args.writer_controller_id,
                state=args.state,
            )
        elif args.command == "append":
            result = append_finalization_record(
                args.manifest,
                record_type=args.record_type,
                payload=_payload_file(args.payload_file),
                writer_controller_id=args.writer_controller_id,
            )
        elif args.command == "seal-prefix":
            result = seal_manifest_prefix(
                args.manifest,
                writer_controller_id=args.writer_controller_id,
                review_id=args.review_id,
                phase=args.phase,
                raw_input_inventory_sha256=args.raw_input_inventory_sha256,
                prefix_output=args.prefix_output,
                receipt_output=args.receipt_output,
            )
        elif args.command == "validate":
            result = validate_manifest(args.manifest)
        else:
            raise PublicationError(f"unknown finalization command: {args.command}")
    except (PublicationError, OSError) as exc:
        print(f"UNCHECKED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
