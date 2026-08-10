#!/usr/bin/env python3
"""Validate exact repository diffs and installed-package snapshots for a panel."""

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, Optional

if __package__:
    from . import install_inventory as _install_inventory_module
    from .install_inventory import (
        INVENTORY_FORMAT,
        PublicationError,
        build_inventory,
        parse_inventory,
        run_authenticated_python as _run_authenticated_python,
    )
else:
    import install_inventory as _install_inventory_module
    from install_inventory import (
        INVENTORY_FORMAT,
        PublicationError,
        build_inventory,
        parse_inventory,
        run_authenticated_python as _run_authenticated_python,
    )

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "scripts/panel_input_fixtures.json"
PANEL_FIXTURE_RESOURCE_NAME = "panel_input_fixtures.json"
SHA1 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+\-]{0,255}$")
UTC = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$")
DIFF_FLAGS = ["--binary", "--full-index", "--no-color", "--no-ext-diff", "--no-textconv", "--no-renames", "--diff-algorithm=myers", "--unified=3"]
PANEL_FIELDS = {"schema_version", "record_type", "panel_id", "finalization_id", "recorded_at", "review_boundary", "repository_roles", "repositories", "installed", "prepare", "scope"}
LEGACY_PANEL_FIELDS = PANEL_FIELDS - {"repository_roles"}
REPO_FIELDS = {"repository", "target_ref", "target_ref_sha_at_dispatch", "target_sha", "merge_base_sha", "head_sha", "head_tree_oid", "diff_argv", "diff_path", "diff_digest_algorithm", "diff_digest"}
INSTALL_FIELDS = {"root", "inventory_path", "inventory_format", "inventory_sha256", "file_count", "total_bytes", "generation_id", "source_repository", "source_commit", "source_tree", "install_manifest_path", "install_manifest_repository_path", "install_manifest_sha256"}
PREPARE_FIELDS = {"operation_id", "receipt_path", "receipt_sha256", "state_path", "state_sha256", "mutation_outcome"}
PREPARE_RECEIPT_FIELDS = {
    "schema_version", "operation_id", "generation_id", "source",
    "immutable_source", "expected_live_source", "predecessor_source",
    "candidate_inventory", "preflight_live_inventory", "evidence_snapshot",
    "staged_validation", "named_mutation_outcomes", "mutation_outcome",
    "prepared_at",
}
PREPARE_STATE_FIELDS = {
    "schema_version", "operation_id", "generation_id", "status", "state_root",
    "install_root", "evidence_root", "source_repository", "source_commit",
    "source_tree", "expected_live_source_commit", "expected_live_source_tree",
    "manifest_path", "manifest_sha256", "manifest_schema_version",
    "expected_live_manifest_sha256", "expected_live_manifest_schema_version",
    "candidate_expected_paths", "preflight_expected_paths", "immutable_source",
    "predecessor_source", "candidate_inventory", "preflight_inventory",
    "evidence_snapshot", "validation", "prepare_receipt", "created_at",
    "updated_at", "events",
}
PREPUBLICATION_BOUNDARY = "prepublication-source-and-staged-snapshot"
PREPUBLICATION_SCOPE = {
    "source_guidance_status_before_review": "NOT_REVIEWED",
    "live_installation": "UNCHANGED_PREDECESSOR",
    "reader_quiescence": "UNCHECKED",
    "reserve": "NOT_RUN",
    "publish": "NOT_RUN",
    "final_fact_review": "NOT_RUN",
    "postpublication_panel": "NOT_RUN",
    "accept": "NOT_RUN",
    "implicit_model_selection": "UNCHECKED",
}
REVIEW_BOUNDARIES = {PREPUBLICATION_BOUNDARY: PREPUBLICATION_SCOPE}
STAGED_VALIDATION_FIELDS = {
    "argv",
    "authenticated_launch",
    "checker_sha256",
    "exit_status",
    "named_mutation_outcomes",
    "result",
    "stderr",
    "stdout",
}
AUTHENTICATED_LAUNCH_FIELDS = {
    "protocol",
    "python_executable",
    "isolation_flags",
    "source_transport",
    "source_path",
    "source_sha256",
    "logical_argv",
    "authenticated_source_sha256",
}
AUTHENTICATED_LAUNCH_PROTOCOL = "held-python-fd-v1"
STAGED_CHECKER_REPLAY_TIMEOUT_SECONDS = 300
STAGED_CHECKER_NESTED_VALIDATOR_TIMEOUT_SECONDS = 120
STAGED_CHECKER_NON_VALIDATOR_OVERHEAD_SECONDS = 60
STAGED_CHECKER_REQUIRED_HEADROOM_SECONDS = 120
PRODUCTION_EVENT_VALIDATION_MUTANT_TIMEOUT_SECONDS = 60
PANEL_VALIDATOR_REQUIRED_PARENT_MARGIN_SECONDS = 60
AUTHENTICATED_VALIDATION_SOURCE_PATHS = frozenset(
    {
        "scripts/install_inventory.py",
        "scripts/large_queue_state_contract.json",
        "scripts/large_queue_state_fixtures.json",
        "scripts/panel_input_fixtures.json",
        "scripts/eval/overnight-workflow-routing-cases.json",
        "scripts/validate_panel_inputs.py",
        "plugins/overnight-review-client-delivery/scripts/action_authority.py",
        "plugins/schedule-poll-orchestrator-pattern/scripts/poll_orchestrator.py",
    }
)
IMMUTABLE_SOURCE_FIELDS = {
    "root",
    "path",
    "format",
    "sha256",
    "file_count",
    "total_bytes",
    "commit",
    "tree",
}
OMISSION_NAMES = {
    "missing_target_ref", "missing_target_ref_sha", "missing_diff_argv", "missing_diff_digest",
    "missing_inventory_path", "missing_inventory_digest", "missing_inventory_count",
    "missing_inventory_bytes", "missing_generation", "missing_source_commit",
    "missing_source_tree", "missing_manifest_digest", "missing_review_boundary",
    "missing_prepare", "missing_scope", "missing_prepare_operation_id",
    "missing_prepare_receipt_path", "missing_prepare_receipt_sha256",
    "missing_prepare_state_path", "missing_prepare_state_sha256",
    "missing_prepare_mutation_outcome", "missing_scope_source_guidance_status",
    "missing_scope_live_installation", "missing_scope_reader_quiescence",
    "missing_scope_reserve", "missing_scope_publish", "missing_scope_final_fact_review",
    "missing_scope_postpublication_panel", "missing_scope_accept",
    "missing_scope_implicit_model_selection",
    "missing_repository_roles", "missing_package_source_repository",
    "missing_consumer_fixture_repository",
}
DRIFT_NAMES = {
    "target_ref_drift",
    "raw_sha_target_ref",
    "dot_target_ref",
    "merge_base_not_target",
    "diff_argv_drift",
    "diff_repository_argv_drift",
    "diff_flag_order_drift",
    "diff_digest_drift",
    "inventory_digest_drift",
    "inventory_count_drift",
    "inventory_bytes_drift",
    "generation_drift",
    "source_commit_drift",
    "source_tree_drift",
    "snapshot_byte_drift",
    "installed_repository_join_drift", "repository_role_key_drift",
    "repository_role_duplicate", "installed_source_role_drift",
    "hardcoded_repository_names_without_role_map", "repository_identity_too_long",
}
BOUNDARY_NAMES = {"postpublication_boundary"}
PREPARE_NAMES = {
    "fabricated_live_mutated_missing_files", "prepare_operation_drift",
    "prepare_receipt_path_drift", "prepare_receipt_digest_drift",
    "prepare_state_path_drift", "prepare_state_digest_drift",
    "prepare_mutation_outcome_drift",
    "receipt_source_repository_drift", "receipt_source_commit_drift",
    "receipt_source_tree_drift", "receipt_manifest_path_drift",
    "receipt_manifest_digest_drift", "receipt_generation_drift",
    "receipt_mutation_outcome_drift", "state_operation_drift", "state_status_drift",
    "state_source_repository_drift", "state_source_commit_drift",
    "state_source_tree_drift", "state_manifest_path_drift",
    "state_manifest_digest_drift", "state_generation_drift",
    "staged_checker_argv_drift", "staged_checker_digest_drift",
    "staged_checker_exit_failure", "staged_checker_stderr_nonempty",
    "staged_checker_result_failure", "staged_checker_outcome_failure",
    "staged_checker_source_drift", "staged_checker_dropped_outcome",
    "staged_checker_fake_launcher", "staged_checker_exchange_slot_drift",
    "state_prepare_receipt_path_drift", "state_prepare_receipt_sha_drift",
    "missing_prepare_started_event", "missing_prepare_completed_event",
    "reordered_prepare_events", "extra_reserve_event", "extra_publication_event",
    "malformed_prepare_event_timestamp", "undeclared_prepare_event_field",
    "removed_production_event_validation",
    "undeclared_prepare_receipt_status", "undeclared_prepare_state_status",
}
SCOPE_NAMES = {
    "fabricated_publication_and_acceptance_complete", "source_guidance_reviewed",
    "live_installation_published", "reader_quiescence_complete", "reserve_complete",
    "publish_complete", "final_fact_review_complete", "postpublication_panel_complete",
    "accept_complete", "implicit_model_selection_complete",
}
UNDECLARED_NAMES = {
    "undeclared_top_level", "undeclared_repository_field", "undeclared_installed_field",
    "undeclared_prepare_field", "undeclared_scope_field",
}
FILE_SAFETY_NAMES = {
    "receipt_symlink", "receipt_hardlink", "state_symlink", "state_hardlink",
    "receipt_size_race", "manifest_symlink", "manifest_hardlink",
    "manifest_size_race", "manifest_missing_final_lf", "manifest_blank_row",
    "manifest_extra_row",
}
COMPATIBILITY_NAMES = {
    "schema2_same_path_baseline",
    "schema2_distinct_identical_original",
    "schema2_missing_original",
    "schema2_nested_digest_drift",
    "schema2_original_byte_drift",
    "schema2_malformed_original_path",
    "schema3_distinct_identical_rejected",
}
DUPLICATE_KEY_NAMES = {
    "manifest_duplicate_schema_version",
    "manifest_duplicate_record_type",
    "manifest_duplicate_nested_scope_publish",
    "manifest_duplicate_nested_repository_role",
    "receipt_duplicate_mutation_outcome",
    "receipt_duplicate_nested_source_commit",
    "state_duplicate_status",
    "state_duplicate_nested_prepare_receipt_sha256",
    "install_manifest_duplicate_mappings",
    "staged_stdout_duplicate_status",
}
FIXTURE_CLASSES = {
    "omission_cases": OMISSION_NAMES,
    "drift_cases": DRIFT_NAMES,
    "boundary_cases": BOUNDARY_NAMES,
    "prepare_cases": PREPARE_NAMES,
    "scope_cases": SCOPE_NAMES,
    "undeclared_cases": UNDECLARED_NAMES,
    "file_safety_cases": FILE_SAFETY_NAMES,
    "compatibility_cases": COMPATIBILITY_NAMES,
    "duplicate_key_cases": DUPLICATE_KEY_NAMES,
}


def check_validation_time_budget_contract(errors: list[str]) -> None:
    """Require enough outer time for the nested validator and checker work."""
    if STAGED_CHECKER_REPLAY_TIMEOUT_SECONDS != 300:
        errors.append("staged checker replay timeout budget must be 300 seconds")
    if STAGED_CHECKER_NESTED_VALIDATOR_TIMEOUT_SECONDS != 120:
        errors.append(
            "staged checker nested validator timeout budget must be 120 seconds"
        )
    if STAGED_CHECKER_NON_VALIDATOR_OVERHEAD_SECONDS != 60:
        errors.append("staged checker non-validator overhead budget must be 60 seconds")
    if STAGED_CHECKER_REQUIRED_HEADROOM_SECONDS != 120:
        errors.append("staged checker required headroom must be 120 seconds")
    if PRODUCTION_EVENT_VALIDATION_MUTANT_TIMEOUT_SECONDS != 60:
        errors.append(
            "production event validation mutant timeout budget must be 60 seconds"
        )
    if PANEL_VALIDATOR_REQUIRED_PARENT_MARGIN_SECONDS != 60:
        errors.append("panel validator required parent margin must be 60 seconds")
    if (
        PRODUCTION_EVENT_VALIDATION_MUTANT_TIMEOUT_SECONDS
        + PANEL_VALIDATOR_REQUIRED_PARENT_MARGIN_SECONDS
        > STAGED_CHECKER_NESTED_VALIDATOR_TIMEOUT_SECONDS
    ):
        errors.append(
            "production event validation mutant budget must retain a 60-second "
            "panel-validator parent margin"
        )
    remaining = (
        STAGED_CHECKER_REPLAY_TIMEOUT_SECONDS
        - STAGED_CHECKER_NESTED_VALIDATOR_TIMEOUT_SECONDS
        - STAGED_CHECKER_NON_VALIDATOR_OVERHEAD_SECONDS
    )
    if remaining < STAGED_CHECKER_REQUIRED_HEADROOM_SECONDS:
        errors.append(
            "staged checker replay budget must retain at least 120 seconds of "
            "headroom after the nested validator and checker overhead"
        )


def absolute(value: Any) -> Optional[Path]:
    if (
        not isinstance(value, str)
        or not value
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        return None
    path = Path(value)
    return path if path.is_absolute() and os.path.normpath(value) == value else None


def symbolic_full_ref(value: Any) -> bool:
    """Accept a fully qualified named ref, never a raw object ID or ref expression."""
    if not isinstance(value, str) or not value.startswith("refs/"):
        return False
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        return False
    if any(character in value for character in " ~^:?*[\\") or "@{" in value:
        return False
    if value.endswith(("/", ".", ".lock")) or "//" in value or ".." in value:
        return False
    components = value.split("/")
    return bool(
        len(components) >= 3
        and components[1] in {"heads", "remotes", "tags"}
        and all(component and component not in {".", ".."} and not component.startswith(".") for component in components)
    )


def timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not UTC.fullmatch(value):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo == timezone.utc


def git(repository: Path, *arguments: str) -> bytes:
    return subprocess.run(["git", "-C", str(repository), *arguments], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20).stdout


def exact_fields(value: Any, fields: set[str], label: str, errors: list[str]) -> bool:
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return False
    missing = sorted(fields - value.keys())
    undeclared = sorted(value.keys() - fields)
    if missing:
        errors.append(f"{label} missing required fields: {missing}")
    if undeclared:
        errors.append(f"{label} has undeclared fields: {undeclared}")
    return not missing and not undeclared


def regular_bytes(path: Path, label: str, errors: list[str]) -> Optional[bytes]:
    descriptor: Optional[int] = None
    try:
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            errors.append(f"{label} must be a regular non-symlink single-link file")
            return None
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (before.st_dev, before.st_ino, before.st_size)
            != (opened.st_dev, opened.st_ino, opened.st_size)
        ):
            errors.append(f"{label} changed identity or is not a regular single-link file")
            return None
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
            or not stat.S_ISREG(after.st_mode)
            or (opened.st_dev, opened.st_ino, opened.st_size)
            != (after.st_dev, after.st_ino, after.st_size)
            or len(data) != after.st_size
        ):
            errors.append(f"{label} changed identity or size while being read")
            return None
        return data
    except OSError as exc:
        errors.append(f"cannot read {label}: {exc}")
        return None
    finally:
        if descriptor is not None:
            os.close(descriptor)


class DuplicateJSONKey(ValueError):
    """One JSON object at a trust boundary repeated a key."""


def strict_json_loads(text: str) -> Any:
    """Decode one JSON document, refusing any object that repeats a key."""

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise DuplicateJSONKey(f"duplicate JSON key {key!r}")
            value[key] = item
        return value

    return json.loads(text, object_pairs_hook=reject_duplicate_keys)


def json_object(data: Optional[bytes], label: str, errors: list[str]) -> Optional[dict[str, Any]]:
    if data is None:
        return None
    try:
        value = strict_json_loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, DuplicateJSONKey) as exc:
        errors.append(f"{label} must contain one UTF-8 JSON object: {exc}")
        return None
    if not isinstance(value, dict):
        errors.append(f"{label} must contain one JSON object")
        return None
    return value


def validate_repository(label: str, value: Any, verify: bool, errors: list[str]) -> None:
    if not exact_fields(value, REPO_FIELDS, label, errors):
        return
    repository, diff_path = absolute(value.get("repository")), absolute(value.get("diff_path"))
    if repository is None:
        errors.append(f"{label}.repository must be a normalized absolute path")
    if diff_path is None:
        errors.append(f"{label}.diff_path must be a normalized absolute path")
    target_ref = value.get("target_ref")
    if not symbolic_full_ref(target_ref):
        errors.append(
            f"{label}.target_ref must be a fully qualified symbolic ref under refs/"
        )
    for field in ("target_ref_sha_at_dispatch", "target_sha", "merge_base_sha", "head_sha", "head_tree_oid"):
        if not isinstance(value.get(field), str) or not SHA1.fullmatch(value[field]):
            errors.append(f"{label}.{field} must be a lowercase full Git SHA")
    if value.get("target_sha") != value.get("target_ref_sha_at_dispatch"):
        errors.append(f"{label}.target_sha must equal target_ref_sha_at_dispatch")
    if value.get("merge_base_sha") != value.get("target_sha"):
        errors.append(f"{label}.merge_base_sha must equal target_sha")
    if value.get("diff_digest_algorithm") != "SHA-256":
        errors.append(f"{label}.diff_digest_algorithm must be SHA-256")
    if not isinstance(value.get("diff_digest"), str) or not SHA256.fullmatch(value["diff_digest"]):
        errors.append(f"{label}.diff_digest must be lowercase SHA-256")
    expected_argv = [
        "git",
        "-C",
        value.get("repository"),
        "diff",
        *DIFF_FLAGS,
        value.get("target_sha"),
        value.get("head_sha"),
        "--",
    ]
    if value.get("diff_argv") != expected_argv:
        errors.append(f"{label}.diff_argv must equal the deterministic literal argv array")
    if not verify or repository is None or diff_path is None:
        return
    try:
        if git(repository, "rev-parse", "--verify", f"{target_ref}^{{commit}}").decode().strip() != value.get("target_ref_sha_at_dispatch"):
            errors.append(f"{label}.target_ref no longer resolves to its dispatch SHA")
        if git(repository, "rev-parse", f"{value.get('head_sha')}^{{tree}}").decode().strip() != value.get("head_tree_oid"):
            errors.append(f"{label}.head_tree_oid does not match head_sha")
        if git(repository, "merge-base", value.get("target_sha"), value.get("head_sha")).decode().strip() != value.get("merge_base_sha"):
            errors.append(f"{label}.merge_base_sha does not reproduce")
        if value.get("diff_argv") == expected_argv:
            reproduced = subprocess.run(expected_argv, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30).stdout
            saved = regular_bytes(diff_path, f"{label} diff", errors)
            if saved is not None:
                if saved != reproduced:
                    errors.append(f"{label} saved diff bytes do not reproduce from diff_argv")
                if hashlib.sha256(saved).hexdigest() != value.get("diff_digest"):
                    errors.append(f"{label} saved diff digest differs from diff_digest")
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, UnicodeError) as exc:
        errors.append(f"{label} Git verification failed: {exc}")


def validate_installed(value: Any, verify: bool, errors: list[str]) -> None:
    if not exact_fields(value, INSTALL_FIELDS, "installed", errors):
        return
    root = absolute(value.get("root")); inventory_path = absolute(value.get("inventory_path")); repository = absolute(value.get("source_repository")); manifest_path = absolute(value.get("install_manifest_path"))
    for field, path in (("root", root), ("inventory_path", inventory_path), ("source_repository", repository), ("install_manifest_path", manifest_path)):
        if path is None:
            errors.append(f"installed.{field} must be a normalized absolute path")
    if value.get("inventory_format") != INVENTORY_FORMAT:
        errors.append(f"installed.inventory_format must be {INVENTORY_FORMAT}")
    for field in ("inventory_sha256", "install_manifest_sha256"):
        if not isinstance(value.get(field), str) or not SHA256.fullmatch(value[field]):
            errors.append(f"installed.{field} must be lowercase SHA-256")
    for field in ("file_count", "total_bytes"):
        number = value.get(field)
        if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
            errors.append(f"installed.{field} must be a positive integer")
    if not isinstance(value.get("generation_id"), str) or not IDENTITY.fullmatch(value["generation_id"]):
        errors.append("installed.generation_id must be a nonempty normalized identity")
    elif value.get("generation_id") != value.get("inventory_sha256"):
        errors.append("installed.generation_id must equal the candidate inventory digest")
    for field in ("source_commit", "source_tree"):
        if not isinstance(value.get(field), str) or not SHA1.fullmatch(value[field]):
            errors.append(f"installed.{field} must be a lowercase full Git SHA")
    manifest_repo_path = value.get("install_manifest_repository_path")
    parsed = PurePosixPath(manifest_repo_path) if isinstance(manifest_repo_path, str) else None
    if (
        parsed is None
        or parsed.is_absolute()
        or not manifest_repo_path
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in manifest_repo_path)
        or "\\" in manifest_repo_path
        or manifest_repo_path.endswith("/")
        or any(component in {"", ".", ".."} for component in parsed.parts)
        or parsed.as_posix() != manifest_repo_path
    ):
        errors.append("installed.install_manifest_repository_path must be normalized repository-relative POSIX")
    if not verify or None in (root, inventory_path, repository, manifest_path):
        return
    assert root and inventory_path and repository and manifest_path
    try:
        inventory_path.resolve().relative_to(root.resolve())
    except ValueError:
        pass
    else:
        errors.append("installed.inventory_path must be outside the snapshot root")
    manifest_bytes = regular_bytes(manifest_path, "install manifest", errors)
    inventory_bytes = regular_bytes(inventory_path, "installed inventory", errors)
    if manifest_bytes is None or inventory_bytes is None:
        return
    if hashlib.sha256(manifest_bytes).hexdigest() != value.get("install_manifest_sha256"):
        errors.append("installed install manifest digest differs")
    try:
        manifest = strict_json_loads(manifest_bytes.decode())
        mappings = manifest["mappings"]
        if not isinstance(mappings, list) or not mappings:
            raise ValueError("mappings must be nonempty")
        expected = [item["installed_path"] for item in mappings]
        if len(expected) != len(set(expected)):
            raise ValueError("repeated installed path")
        if sorted(path for path in expected if PurePosixPath(path).name == "SKILL.md") != ["SKILL.md"]:
            raise ValueError("snapshot must expose only root SKILL.md")
        inventory = build_inventory(root, expected)
    except (KeyError, ValueError, UnicodeError, json.JSONDecodeError, DuplicateJSONKey, PublicationError) as exc:
        errors.append(f"installed snapshot inventory could not be validated: {exc}")
        return
    if inventory.data != inventory_bytes:
        errors.append("installed inventory bytes do not match the snapshot")
    if inventory.digest != value.get("inventory_sha256"):
        errors.append("installed inventory digest differs from inventory_sha256")
    if inventory.file_count != value.get("file_count"):
        errors.append("installed inventory file_count differs")
    if inventory.total_bytes != value.get("total_bytes"):
        errors.append("installed inventory total_bytes differs")
    try:
        if git(repository, "rev-parse", f"{value.get('source_commit')}^{{tree}}").decode().strip() != value.get("source_tree"):
            errors.append("installed source_tree does not match source_commit")
        if git(repository, "show", f"{value.get('source_commit')}:{manifest_repo_path}") != manifest_bytes:
            errors.append("installed manifest is not the one at source_commit")
        for mapping in mappings:
            source = git(repository, "show", f"{value.get('source_commit')}:{mapping['canonical_source']}")
            if source != (root / mapping["installed_path"]).read_bytes():
                errors.append(f"installed snapshot byte differs from source_commit: {mapping['installed_path']}")
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        errors.append(f"installed source verification failed: {exc}")


def validate_scope(boundary: Any, value: Any, errors: list[str]) -> None:
    expected = REVIEW_BOUNDARIES.get(boundary)
    fields = set(expected) if expected is not None else set(PREPUBLICATION_SCOPE)
    if not exact_fields(value, fields, "scope", errors):
        return
    if expected is None:
        return
    for field, expected_value in expected.items():
        if value.get(field) != expected_value:
            errors.append(
                f"scope.{field} must be {expected_value} for review_boundary {boundary}"
            )


def validate_immutable_source(
    value: Any,
    installed: dict[str, Any],
    verify: bool,
    errors: list[str],
) -> Optional[dict[str, Any]]:
    if not exact_fields(
        value, IMMUTABLE_SOURCE_FIELDS, "prepare receipt immutable_source", errors
    ):
        return None
    assert isinstance(value, dict)
    root = absolute(value.get("root"))
    inventory_path = absolute(value.get("path"))
    if root is None:
        errors.append("prepare receipt immutable_source.root must be normalized absolute")
    if inventory_path is None:
        errors.append("prepare receipt immutable_source.path must be normalized absolute")
    if value.get("format") != INVENTORY_FORMAT:
        errors.append(
            f"prepare receipt immutable_source.format must be {INVENTORY_FORMAT}"
        )
    if not isinstance(value.get("sha256"), str) or not SHA256.fullmatch(value["sha256"]):
        errors.append("prepare receipt immutable_source.sha256 must be lowercase SHA-256")
    for field in ("file_count", "total_bytes"):
        if (
            not isinstance(value.get(field), int)
            or isinstance(value.get(field), bool)
            or value[field] <= 0
        ):
            errors.append(
                f"prepare receipt immutable_source.{field} must be a positive integer"
            )
    for field, installed_field in (("commit", "source_commit"), ("tree", "source_tree")):
        if value.get(field) != installed.get(installed_field):
            errors.append(
                f"prepare receipt immutable_source.{field} differs from installed.{installed_field}"
            )
    if not verify or root is None or inventory_path is None:
        return None
    inventory_data = regular_bytes(
        inventory_path, "prepare immutable-source inventory", errors
    )
    if inventory_data is None:
        return None
    if hashlib.sha256(inventory_data).hexdigest() != value.get("sha256"):
        errors.append("prepare immutable-source inventory digest differs")
    try:
        entries = parse_inventory(inventory_data)
        reproduced = build_inventory(root, [entry.path for entry in entries])
    except PublicationError as exc:
        errors.append(f"prepare immutable-source inventory could not be reproduced: {exc}")
        return None
    if reproduced.data != inventory_data:
        errors.append("prepare immutable-source inventory bytes do not match its root")
    if reproduced.file_count != value.get("file_count"):
        errors.append("prepare immutable-source inventory file_count differs")
    if reproduced.total_bytes != value.get("total_bytes"):
        errors.append("prepare immutable-source inventory total_bytes differs")
    checker = next(
        (
            entry
            for entry in entries
            if entry.path == "scripts/check_large_queue_guidance.py"
        ),
        None,
    )
    if checker is None:
        errors.append("prepare immutable-source inventory omits the staged checker")
        return None
    authenticated_source_sha256 = {
        entry.path: entry.sha256
        for entry in entries
        if entry.path in AUTHENTICATED_VALIDATION_SOURCE_PATHS
    }
    if authenticated_source_sha256 and set(authenticated_source_sha256) != set(
        AUTHENTICATED_VALIDATION_SOURCE_PATHS
    ):
        errors.append(
            "prepare immutable-source inventory has only part of the authenticated "
            "nested validation closure"
        )
    return {
        "path": checker.path,
        "sha256": checker.sha256,
        "bytes": checker.size,
        "authenticated_source_sha256": authenticated_source_sha256,
    }


def validate_staged_validation(
    receipt: dict[str, Any],
    installed: dict[str, Any],
    checker_inventory_entry: Optional[dict[str, Any]],
    verify: bool,
    errors: list[str],
) -> Optional[dict[str, Any]]:
    staged = receipt.get("staged_validation")
    if not exact_fields(staged, STAGED_VALIDATION_FIELDS, "prepare receipt staged_validation", errors):
        return None
    assert isinstance(staged, dict)
    argv = staged.get("argv")
    immutable_source = receipt.get("immutable_source")
    immutable_root = (
        absolute(immutable_source.get("root"))
        if isinstance(immutable_source, dict)
        else None
    )
    expected_checker = (
        immutable_root / "scripts/check_large_queue_guidance.py"
        if immutable_root is not None
        else None
    )
    expected_exchange = immutable_root.parent / "exchange-slot" if immutable_root else None
    launcher_is_trusted = False
    if (
        not isinstance(argv, list)
        or len(argv) != 6
        or not all(isinstance(argument, str) for argument in argv)
    ):
        errors.append(
            "prepare receipt staged_validation.argv must be the six-string logical "
            "checker command"
        )
    else:
        launcher = absolute(argv[0])
        if launcher is None:
            errors.append("prepare receipt staged_validation.argv Python executable must be absolute")
        else:
            try:
                trusted_launcher = Path(sys.executable).resolve(strict=True)
                recorded_launcher = launcher.resolve(strict=True)
            except OSError as exc:
                errors.append(
                    "prepare receipt staged_validation.argv Python executable "
                    f"cannot be resolved: {exc}"
                )
            else:
                if recorded_launcher != trusted_launcher:
                    errors.append(
                        "prepare receipt staged_validation.argv Python executable "
                        "must resolve to the current trusted interpreter"
                    )
                else:
                    launcher_is_trusted = True
        expected_tail = (
            [str(expected_checker), "--installed-root", str(expected_exchange), "--self-test", "--json"]
            if expected_checker is not None and expected_exchange is not None
            else None
        )
        if expected_tail is None or argv[1:] != expected_tail:
            errors.append(
                "prepare receipt staged_validation.argv must name the immutable-source checker, "
                "private exchange slot, --self-test, and --json in exact order"
            )
    checker_digest = staged.get("checker_sha256")
    if not isinstance(checker_digest, str) or not SHA256.fullmatch(checker_digest):
        errors.append("prepare receipt staged_validation.checker_sha256 must be lowercase SHA-256")
    authenticated_launch = staged.get("authenticated_launch")
    authenticated_launch_is_trusted = False
    if exact_fields(
        authenticated_launch,
        AUTHENTICATED_LAUNCH_FIELDS,
        "prepare receipt staged_validation.authenticated_launch",
        errors,
    ):
        assert isinstance(authenticated_launch, dict)
        expected_authenticated_source_sha256 = (
            checker_inventory_entry.get("authenticated_source_sha256")
            if isinstance(checker_inventory_entry, dict)
            else None
        )
        expected_launch = {
            "protocol": AUTHENTICATED_LAUNCH_PROTOCOL,
            "python_executable": argv[0] if isinstance(argv, list) and argv else None,
            "isolation_flags": ["-I", "-B"],
            "source_transport": "inherited-read-only-file-descriptor",
            "source_path": argv[1]
            if isinstance(argv, list) and len(argv) > 1
            else None,
            "source_sha256": checker_digest,
            "logical_argv": argv,
            "authenticated_source_sha256": expected_authenticated_source_sha256,
        }
        if authenticated_launch != expected_launch:
            errors.append(
                "prepare receipt staged_validation.authenticated_launch differs from "
                "the immutable-source held-file-descriptor invocation"
            )
        else:
            authenticated_launch_is_trusted = True
    checker_bytes: Optional[bytes] = None
    if verify and expected_checker is not None:
        checker_bytes = regular_bytes(expected_checker, "prepare staged checker", errors)
        if (
            checker_bytes is not None
            and hashlib.sha256(checker_bytes).hexdigest() != checker_digest
        ):
            errors.append("prepare staged checker digest differs from checker_sha256")
        if checker_bytes is not None and checker_inventory_entry is not None:
            if (
                checker_inventory_entry.get("sha256")
                != hashlib.sha256(checker_bytes).hexdigest()
                or checker_inventory_entry.get("bytes") != len(checker_bytes)
            ):
                errors.append(
                    "prepare staged checker differs from immutable-source inventory"
                )
        repository = absolute(installed.get("source_repository"))
        source_commit = installed.get("source_commit")
        if repository is not None and isinstance(source_commit, str):
            try:
                source_checker = git(
                    repository,
                    "show",
                    f"{source_commit}:scripts/check_large_queue_guidance.py",
                )
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                errors.append(f"cannot read staged checker from source_commit: {exc}")
            else:
                if checker_bytes is not None and checker_bytes != source_checker:
                    errors.append("prepare staged checker differs from source_commit")
    if type(staged.get("exit_status")) is not int or staged.get("exit_status") != 0:
        errors.append("prepare receipt staged_validation.exit_status must be integer zero")
    if staged.get("stderr") != "":
        errors.append("prepare receipt staged_validation.stderr must be empty")
    outcomes = staged.get("named_mutation_outcomes")
    if (
        not isinstance(outcomes, dict)
        or not outcomes
        or not all(
            isinstance(name, str) and name and outcome == "PASS"
            for name, outcome in outcomes.items()
        )
    ):
        errors.append(
            "prepare receipt staged_validation.named_mutation_outcomes must be a nonempty all-PASS object"
        )
    result = staged.get("result")
    if not isinstance(result, dict) or result.get("schema_version") != 1 or result.get("status") != "PASS":
        errors.append("prepare receipt staged_validation.result must be a schema-1 PASS result")
    elif result.get("named_mutation_outcomes") != outcomes:
        errors.append("prepare receipt staged_validation.result outcomes differ from recorded outcomes")
    stdout = staged.get("stdout")
    if not isinstance(stdout, str):
        errors.append("prepare receipt staged_validation.stdout must be JSON text")
    else:
        try:
            stdout_result = strict_json_loads(stdout)
        except (json.JSONDecodeError, DuplicateJSONKey) as exc:
            errors.append(f"prepare receipt staged_validation.stdout is invalid JSON: {exc}")
        else:
            if stdout_result != result:
                errors.append("prepare receipt staged_validation.stdout differs from result")
    top_outcomes = receipt.get("named_mutation_outcomes")
    if top_outcomes != {"staged": outcomes}:
        errors.append("prepare receipt top-level staged outcomes differ from staged_validation")
    if (
        verify
        and isinstance(argv, list)
        and len(argv) == 6
        and all(isinstance(argument, str) for argument in argv)
        and launcher_is_trusted
        and authenticated_launch_is_trusted
        and expected_checker is not None
        and checker_bytes is not None
        and checker_inventory_entry is not None
        and isinstance(checker_inventory_entry.get("sha256"), str)
        and SHA256.fullmatch(checker_inventory_entry["sha256"])
    ):
        try:
            completed, _ = _run_authenticated_python(
                expected_checker,
                argv[2:],
                expected_sha256=checker_inventory_entry["sha256"],
                cwd=immutable_root,
                timeout=STAGED_CHECKER_REPLAY_TIMEOUT_SECONDS,
                label="staged checker",
                authenticated_source_sha256=checker_inventory_entry.get(
                    "authenticated_source_sha256"
                ),
            )
        except subprocess.TimeoutExpired as exc:
            errors.append(
                "prepare staged checker timed out after "
                f"{STAGED_CHECKER_REPLAY_TIMEOUT_SECONDS} seconds: {exc}"
            )
        except (OSError, RuntimeError) as exc:
            errors.append(f"prepare staged checker could not be re-executed: {exc}")
        else:
            exit_status = completed.returncode
            fresh_stdout = completed.stdout
            fresh_stderr = completed.stderr
            try:
                fresh_stdout_text = fresh_stdout.decode("utf-8")
                fresh_stderr_text = fresh_stderr.decode("utf-8")
                fresh_result = strict_json_loads(fresh_stdout_text)
            except (UnicodeError, json.JSONDecodeError, DuplicateJSONKey) as exc:
                errors.append(f"fresh staged checker result is malformed: {exc}")
            else:
                if isinstance(fresh_result, dict) and fresh_result.get("status") == "FAIL":
                    child_errors = fresh_result.get("errors")
                    if (
                        isinstance(child_errors, list)
                        and child_errors
                        and all(isinstance(item, str) and item for item in child_errors)
                    ):
                        errors.append(
                            "fresh staged checker reported FAIL: "
                            + " | ".join(child_errors)
                        )
                    else:
                        errors.append(
                            "fresh staged checker reported FAIL without a nonempty "
                            "string errors list"
                        )
                if exit_status != staged.get("exit_status"):
                    errors.append("fresh staged checker exit status differs from recorded result")
                if fresh_stderr_text != staged.get("stderr"):
                    errors.append("fresh staged checker stderr differs from recorded result")
                if fresh_stdout_text != staged.get("stdout"):
                    errors.append("fresh staged checker stdout differs from recorded result")
                if fresh_result != staged.get("result"):
                    errors.append("fresh staged checker result differs from recorded result")
                if (
                    not isinstance(fresh_result, dict)
                    or fresh_result.get("named_mutation_outcomes") != outcomes
                ):
                    errors.append(
                        "fresh staged checker named outcomes differ from recorded canonical set"
                    )
    return staged


def validate_prepare_events(value: Any, errors: list[str]) -> None:
    """Require the complete ordered prepare-only event sequence."""
    if (
        not isinstance(value, list)
        or [event.get("event") if isinstance(event, dict) else None for event in value]
        != ["prepare_started", "prepare_completed"]
        or any(
            not isinstance(event, dict)
            or set(event) != {"at", "event"}
            or not timestamp(event.get("at"))
            for event in value
        )
    ):
        errors.append(
            "prepare state events must be exactly prepare_started then prepare_completed"
        )


def validate_state_receipt_provenance(
    value: Any,
    panel_schema_version: Any,
    sealed_receipt_path: Path,
    sealed_receipt_sha256: Any,
    sealed_receipt_bytes: bytes,
    errors: list[str],
) -> None:
    """Validate current and archived prepare-receipt provenance.

    Schema 3 binds the nested path directly to the sealed receipt copy. Archived
    schema-2 panels may retain the producer's original absolute path instead. In
    that case the state-bound digest, outer digest, and loaded sealed-copy digest
    must all agree. A surviving original must also be a safe regular file with
    byte-for-byte identical content; a missing original is tolerated because the
    state-bound path and sealed copy retain the archived provenance.
    """
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        errors.append("prepare state prepare_receipt must have exact path and sha256 fields")
        return
    original_receipt_path = absolute(value.get("path"))
    if original_receipt_path is None:
        errors.append("prepare state prepare_receipt.path must be a normalized absolute path")
        return
    loaded_sealed_digest = hashlib.sha256(sealed_receipt_bytes).hexdigest()
    nested_digest = value.get("sha256")
    if nested_digest != loaded_sealed_digest:
        errors.append(
            "prepare state prepare_receipt.sha256 differs from loaded prepare receipt bytes"
        )
    if not (
        nested_digest == sealed_receipt_sha256 == loaded_sealed_digest
    ):
        errors.append(
            "prepare state prepare_receipt.sha256 must equal outer receipt digest "
            "and loaded sealed receipt bytes"
        )
    if panel_schema_version == 3:
        if original_receipt_path != sealed_receipt_path:
            errors.append(
                "prepare state prepare_receipt.path differs from prepare.receipt_path"
            )
        return
    if panel_schema_version != 2 or original_receipt_path == sealed_receipt_path:
        return
    try:
        os.lstat(original_receipt_path)
    except FileNotFoundError:
        return
    except OSError as exc:
        errors.append(f"cannot inspect archived original prepare receipt: {exc}")
        return
    original_receipt_bytes = regular_bytes(
        original_receipt_path, "archived original prepare receipt", errors
    )
    if (
        original_receipt_bytes is not None
        and original_receipt_bytes != sealed_receipt_bytes
    ):
        errors.append("archived original prepare receipt bytes differ from sealed copy")


def validate_prepare(
    value: Any,
    installed: Any,
    panel_schema_version: Any,
    verify: bool,
    errors: list[str],
) -> None:
    if not exact_fields(value, PREPARE_FIELDS, "prepare", errors):
        return
    assert isinstance(value, dict)
    operation_id = value.get("operation_id")
    if not isinstance(operation_id, str) or not IDENTITY.fullmatch(operation_id):
        errors.append("prepare.operation_id must be a normalized identity")
    receipt_path = absolute(value.get("receipt_path"))
    state_path = absolute(value.get("state_path"))
    if receipt_path is None:
        errors.append("prepare.receipt_path must be a normalized absolute path")
    if state_path is None:
        errors.append("prepare.state_path must be a normalized absolute path")
    for field in ("receipt_sha256", "state_sha256"):
        digest = value.get(field)
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            errors.append(f"prepare.{field} must be lowercase SHA-256")
    if value.get("mutation_outcome") != "NO_LIVE_MUTATION_PREPARED":
        errors.append("prepare.mutation_outcome must be NO_LIVE_MUTATION_PREPARED")
    if not verify or receipt_path is None or state_path is None:
        return

    receipt_bytes = regular_bytes(receipt_path, "prepare receipt", errors)
    state_bytes = regular_bytes(state_path, "prepare state", errors)
    if (
        receipt_bytes is not None
        and hashlib.sha256(receipt_bytes).hexdigest() != value.get("receipt_sha256")
    ):
        errors.append("prepare receipt digest differs from receipt_sha256")
    if (
        state_bytes is not None
        and hashlib.sha256(state_bytes).hexdigest() != value.get("state_sha256")
    ):
        errors.append("prepare state digest differs from state_sha256")
    receipt = json_object(receipt_bytes, "prepare receipt", errors)
    state = json_object(state_bytes, "prepare state", errors)
    if receipt is None or state is None or not isinstance(installed, dict):
        return

    exact_fields(receipt, PREPARE_RECEIPT_FIELDS, "prepare receipt", errors)
    exact_fields(state, PREPARE_STATE_FIELDS, "prepare state", errors)

    if receipt.get("schema_version") != 3:
        errors.append("prepare receipt schema_version must be 3")
    if receipt.get("operation_id") != operation_id:
        errors.append("prepare receipt operation_id differs from prepare.operation_id")
    if receipt.get("generation_id") != installed.get("generation_id"):
        errors.append("prepare receipt generation_id differs from installed.generation_id")
    if receipt.get("mutation_outcome") != "NO_LIVE_MUTATION_PREPARED":
        errors.append("prepare receipt mutation_outcome must be NO_LIVE_MUTATION_PREPARED")
    if receipt.get("mutation_outcome") != value.get("mutation_outcome"):
        errors.append("prepare receipt mutation_outcome differs from prepare.mutation_outcome")
    source = receipt.get("source")
    if not isinstance(source, dict):
        errors.append("prepare receipt source must be an object")
    else:
        source_bindings = {
            "repository": "source_repository",
            "commit": "source_commit",
            "tree": "source_tree",
            "manifest_path": "install_manifest_repository_path",
            "manifest_sha256": "install_manifest_sha256",
        }
        for receipt_field, installed_field in source_bindings.items():
            if source.get(receipt_field) != installed.get(installed_field):
                errors.append(
                    f"prepare receipt source.{receipt_field} differs from installed.{installed_field}"
                )
    checker_inventory_entry = validate_immutable_source(
        receipt.get("immutable_source"), installed, verify, errors
    )
    for label in ("candidate_inventory", "evidence_snapshot"):
        inventory = receipt.get(label)
        if not isinstance(inventory, dict):
            errors.append(f"prepare receipt {label} must be an object")
            continue
        for receipt_field, installed_field in (
            ("format", "inventory_format"), ("sha256", "inventory_sha256"),
            ("file_count", "file_count"), ("total_bytes", "total_bytes"),
        ):
            if inventory.get(receipt_field) != installed.get(installed_field):
                errors.append(
                    f"prepare receipt {label}.{receipt_field} differs from installed.{installed_field}"
                )
    staged = validate_staged_validation(
        receipt, installed, checker_inventory_entry, verify, errors
    )

    if state.get("schema_version") != 2:
        errors.append("prepare state schema_version must be 2")
    state_bindings = {
        "operation_id": operation_id,
        "source_repository": installed.get("source_repository"),
        "source_commit": installed.get("source_commit"),
        "source_tree": installed.get("source_tree"),
        "manifest_path": installed.get("install_manifest_repository_path"),
        "manifest_sha256": installed.get("install_manifest_sha256"),
        "generation_id": installed.get("generation_id"),
    }
    for field, expected in state_bindings.items():
        if state.get(field) != expected:
            errors.append(f"prepare state {field} differs from panel input")
    if state.get("status") != "PREPARED":
        errors.append("prepare state status must be PREPARED")
    if not timestamp(receipt.get("prepared_at")):
        errors.append("prepare receipt prepared_at must be ISO-8601 UTC ending Z")
    for field in ("created_at", "updated_at"):
        if not timestamp(state.get(field)):
            errors.append(f"prepare state {field} must be ISO-8601 UTC ending Z")
    if state.get("immutable_source") != receipt.get("immutable_source"):
        errors.append("prepare state immutable_source differs from prepare receipt")
    if state.get("predecessor_source") != receipt.get("predecessor_source"):
        errors.append("prepare state predecessor_source differs from prepare receipt")
    if state.get("preflight_inventory") != receipt.get("preflight_live_inventory"):
        errors.append("prepare state preflight_inventory differs from prepare receipt")
    expected_live = receipt.get("expected_live_source")
    if not isinstance(expected_live, dict) or any(
        expected_live.get(receipt_field) != state.get(state_field)
        for receipt_field, state_field in (
            ("commit", "expected_live_source_commit"),
            ("tree", "expected_live_source_tree"),
            ("manifest_path", "manifest_path"),
            ("manifest_sha256", "expected_live_manifest_sha256"),
        )
    ):
        errors.append("prepare receipt expected_live_source differs from prepare state")
    validate_state_receipt_provenance(
        state.get("prepare_receipt"),
        panel_schema_version,
        receipt_path,
        value.get("receipt_sha256"),
        receipt_bytes,
        errors,
    )
    state_validation = state.get("validation")
    if not isinstance(state_validation, dict) or set(state_validation) != {"staged"}:
        errors.append("prepare state validation must have exactly one staged field")
    elif staged is not None and state_validation.get("staged") != staged:
        errors.append("prepare state staged validation differs from prepare receipt")
    for label in ("candidate_inventory", "evidence_snapshot"):
        inventory = state.get(label)
        if not isinstance(inventory, dict):
            errors.append(f"prepare state {label} must be an object")
            continue
        for state_field, installed_field in (
            ("format", "inventory_format"), ("sha256", "inventory_sha256"),
            ("file_count", "file_count"), ("total_bytes", "total_bytes"),
        ):
            if inventory.get(state_field) != installed.get(installed_field):
                errors.append(
                    f"prepare state {label}.{state_field} differs from installed.{installed_field}"
                )
        if inventory != receipt.get(label):
            errors.append(f"prepare state {label} differs from prepare receipt")
    validate_prepare_events(state.get("events"), errors)


def validate_repository_roles(
    value: Any,
    repositories: Any,
    errors: list[str],
) -> Optional[dict[str, str]]:
    if not isinstance(value, dict) or not value:
        errors.append("panel input repository_roles must be a nonempty object")
        return None
    valid = True
    repository_keys_are_valid = True
    for role, repository_key in value.items():
        if not isinstance(role, str) or not IDENTITY.fullmatch(role):
            errors.append(f"panel repository role is invalid: {role!r}")
            valid = False
        if not isinstance(repository_key, str) or not IDENTITY.fullmatch(repository_key):
            errors.append(
                f"panel repository role {role!r} has an invalid repository key"
            )
            valid = False
            repository_keys_are_valid = False
    if "installed_source" not in value:
        errors.append("panel input repository_roles must declare installed_source")
        valid = False
    if isinstance(repositories, dict):
        if (
            not repository_keys_are_valid
            or set(value.values()) != set(repositories)
        ):
            errors.append(
                "panel input repository_roles values must be exactly the repository keys"
            )
            valid = False
        if (
            len(value) != len(repositories)
            or (
                repository_keys_are_valid
                and len(set(value.values())) != len(value)
            )
        ):
            errors.append("panel input repository_roles must map roles one-to-one")
            valid = False
    return value if valid else None


def validate_panel_input(record: Any, verify: bool = True) -> list[str]:
    errors: list[str] = []
    if not isinstance(record, dict):
        return ["panel input must be an object"]
    schema_version = record.get("schema_version")
    if schema_version == 3:
        exact_fields(record, PANEL_FIELDS, "panel input", errors)
    elif schema_version == 2:
        exact_fields(record, LEGACY_PANEL_FIELDS, "legacy panel input", errors)
    else:
        exact_fields(record, PANEL_FIELDS, "panel input", errors)
        errors.append("panel input must declare schema_version 3 or legacy schema_version 2")
    if record.get("record_type") != "panel_input":
        errors.append("panel input record_type must be panel_input")
    for field in ("panel_id", "finalization_id"):
        if not isinstance(record.get(field), str) or not IDENTITY.fullmatch(record[field]):
            errors.append(f"panel input {field} must be a normalized identity")
    if not timestamp(record.get("recorded_at")):
        errors.append("panel input recorded_at must be ISO-8601 UTC ending Z")
    boundary = record.get("review_boundary")
    if boundary not in REVIEW_BOUNDARIES:
        errors.append(
            f"panel input review_boundary must be one of {sorted(REVIEW_BOUNDARIES)}"
        )
    repositories = record.get("repositories")
    if not isinstance(repositories, dict) or not repositories:
        errors.append("panel input repositories must be a nonempty object")
    else:
        if schema_version == 2 and len(repositories) != 2:
            errors.append(
                "legacy schema-2 panel input repositories must contain exactly two entries"
            )
        for name, repository in repositories.items():
            if not isinstance(name, str) or not IDENTITY.fullmatch(name):
                errors.append(f"panel repository key is invalid: {name!r}")
            else:
                validate_repository(f"repositories.{name}", repository, verify, errors)
    repository_roles = (
        validate_repository_roles(record.get("repository_roles"), repositories, errors)
        if schema_version == 3
        else None
    )
    installed = record.get("installed")
    validate_installed(installed, verify, errors)
    if isinstance(repositories, dict) and isinstance(installed, dict):
        matches = [
            name
            for name, repository in repositories.items()
            if isinstance(repository, dict)
            and repository.get("repository") == installed.get("source_repository")
            and repository.get("head_sha") == installed.get("source_commit")
            and repository.get("head_tree_oid") == installed.get("source_tree")
        ]
        if len(matches) != 1:
            errors.append(
                "installed source repository/commit/tree must match exactly one reviewed repository"
            )
        elif (
            repository_roles is not None
            and repository_roles.get("installed_source") != matches[0]
        ):
            errors.append(
                "panel input installed_source role must identify the reviewed repository "
                "matching installed source repository/commit/tree"
            )
    validate_prepare(
        record.get("prepare"), installed, schema_version, verify, errors
    )
    validate_scope(boundary, record.get("scope"), errors)
    return errors


def fixture_errors(fixtures: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(fixtures, dict) or fixtures.get("schema_version") != 1:
        return ["panel fixtures must declare schema_version 1"]
    expected_top_level = {"schema_version", *FIXTURE_CLASSES}
    if set(fixtures) != expected_top_level:
        errors.append("panel fixtures must contain the exact fixture-class inventory")
    for section, expected in FIXTURE_CLASSES.items():
        cases = fixtures.get(section)
        if not isinstance(cases, list) or not cases:
            errors.append(f"panel fixture section {section} must be nonempty")
            continue
        names = [case.get("name") for case in cases if isinstance(case, dict)]
        if len(names) != len(cases) or len(names) != len(set(names)) or set(names) != expected:
            errors.append(f"panel fixture section {section} must have exact unique inventory")
    return errors


def remove_path(value: dict[str, Any], path: list[str]) -> None:
    cursor: Any = value
    for component in path[:-1]:
        cursor = cursor[component]
    cursor.pop(path[-1], None)


def set_path(value: dict[str, Any], path: list[str], replacement: Any) -> None:
    cursor: Any = value
    for component in path[:-1]:
        cursor = cursor[component]
    cursor[path[-1]] = copy.deepcopy(replacement)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def duplicate_key_text(
    value: Any, path: list[str], key: str, first_value: Any
) -> str:
    """Render ``value`` with ``key`` repeated inside the object at ``path``."""
    target: Any = value
    for component in path:
        target = target[component]
    if not isinstance(target, dict) or key not in target:
        raise RuntimeError(
            f"duplicate-key control target is absent: {path + [key]}"
        )
    duplicated = (
        "{"
        + ",".join(
            [f"{json.dumps(key)}:{_canonical_json(first_value)}"]
            + [
                f"{json.dumps(name)}:{_canonical_json(target[name])}"
                for name in sorted(target)
            ]
        )
        + "}"
    )
    if not path:
        return duplicated
    document = _canonical_json(value)
    canonical_target = _canonical_json(target)
    if document.count(canonical_target) != 1:
        raise RuntimeError(
            f"duplicate-key control target is not uniquely locatable: {path}"
        )
    return document.replace(canonical_target, duplicated, 1)


def _authenticated_panel_fixture_record() -> Optional[dict[str, Any]]:
    """Return held fixture bytes, or None for direct canonical CLI execution."""
    held_source_present = "__authenticated_source_bytes__" in globals()
    held_resources_present = "__authenticated_resource_sources__" in globals()
    if not held_source_present and not held_resources_present:
        return None
    authenticated_source = globals().get("__authenticated_source_bytes__")
    authenticated_resources = globals().get("__authenticated_resource_sources__")
    fixture_record = (
        authenticated_resources.get(PANEL_FIXTURE_RESOURCE_NAME)
        if isinstance(authenticated_resources, dict)
        else None
    )
    if (
        not isinstance(authenticated_source, bytes)
        or not isinstance(authenticated_resources, dict)
        or set(authenticated_resources) != {PANEL_FIXTURE_RESOURCE_NAME}
        or not isinstance(fixture_record, dict)
        or set(fixture_record) != {"path", "sha256", "source"}
        or fixture_record.get("path") != str(FIXTURES)
        or not isinstance(fixture_record.get("sha256"), str)
        or not SHA256.fullmatch(fixture_record["sha256"])
        or not isinstance(fixture_record.get("source"), bytes)
    ):
        raise RuntimeError(
            "held validator requires exact authenticated panel fixture bytes and digest"
        )
    if (
        hashlib.sha256(fixture_record["source"]).hexdigest()
        != fixture_record["sha256"]
    ):
        raise RuntimeError(
            "authenticated panel fixture bytes differ from their digest"
        )
    return fixture_record


def _load_panel_fixtures() -> dict[str, Any]:
    authenticated = _authenticated_panel_fixture_record()
    data = FIXTURES.read_bytes() if authenticated is None else authenticated["source"]
    value = strict_json_loads(data.decode("utf-8"))
    return value if isinstance(value, dict) else {}


def _run_production_event_validation_mutant(
    source_path: Path, environment: dict[str, str]
) -> subprocess.CompletedProcess[bytes]:
    """Run the mutation control from held validator, module, and fixture bytes."""
    held_source_present = "__authenticated_source_bytes__" in globals()
    held_modules_present = "__authenticated_module_sources__" in globals()
    held_resources_present = "__authenticated_resource_sources__" in globals()
    authenticated_source = globals().get("__authenticated_source_bytes__")
    authenticated_modules = globals().get("__authenticated_module_sources__")
    inventory_record = (
        authenticated_modules.get("install_inventory")
        if isinstance(authenticated_modules, dict)
        else None
    )
    inventory_record_is_valid = (
        isinstance(authenticated_modules, dict)
        and set(authenticated_modules) == {"install_inventory"}
        and isinstance(inventory_record, dict)
        and set(inventory_record) == {"path", "sha256", "source"}
        and isinstance(inventory_record.get("path"), str)
        and isinstance(inventory_record.get("sha256"), str)
        and SHA256.fullmatch(inventory_record["sha256"]) is not None
        and isinstance(inventory_record.get("source"), bytes)
    )
    if held_source_present or held_modules_present or held_resources_present:
        fixture_record = _authenticated_panel_fixture_record()
        if (
            not isinstance(authenticated_source, bytes)
            or not inventory_record_is_valid
            or fixture_record is None
        ):
            raise RuntimeError(
                "held validator requires exact authenticated install_inventory and "
                "panel fixture bytes and digests"
            )
        source = authenticated_source
        assert isinstance(inventory_record, dict)
        inventory_path = inventory_record["path"]
        inventory_source = inventory_record["source"]
        inventory_sha256 = inventory_record["sha256"]
        if hashlib.sha256(inventory_source).hexdigest() != inventory_sha256:
            raise RuntimeError(
                "authenticated install_inventory bytes differ from their digest"
            )
        fixture_path = fixture_record["path"]
        fixture_source = fixture_record["source"]
        fixture_sha256 = fixture_record["sha256"]
    else:
        source = source_path.read_bytes()
        inventory_path = str(Path(_install_inventory_module.__file__).resolve())
        inventory_source = Path(inventory_path).read_bytes()
        inventory_sha256 = hashlib.sha256(inventory_source).hexdigest()
        fixture_path = str(FIXTURES)
        fixture_source = FIXTURES.read_bytes()
        fixture_sha256 = hashlib.sha256(fixture_source).hexdigest()
    source_sha256 = hashlib.sha256(source).hexdigest()
    payload = (
        len(inventory_source).to_bytes(8, "big")
        + inventory_source
        + len(fixture_source).to_bytes(8, "big")
        + fixture_source
        + source
    )
    driver = (
        "import hashlib, struct, sys, types\n"
        "payload = sys.stdin.buffer.read()\n"
        "if len(payload) < 8:\n"
        "    raise SystemExit('authenticated mutation payload is truncated')\n"
        "inventory_size = struct.unpack('>Q', payload[:8])[0]\n"
        "inventory_end = 8 + inventory_size\n"
        "if len(payload) < inventory_end + 8:\n"
        "    raise SystemExit('authenticated mutation payload is truncated')\n"
        "inventory_source = payload[8:inventory_end]\n"
        "fixture_size = struct.unpack('>Q', payload[inventory_end:inventory_end + 8])[0]\n"
        "fixture_start = inventory_end + 8\n"
        "fixture_end = fixture_start + fixture_size\n"
        "if fixture_end > len(payload):\n"
        "    raise SystemExit('authenticated mutation payload is truncated')\n"
        "fixture_source = payload[fixture_start:fixture_end]\n"
        "source = payload[fixture_end:]\n"
        "inventory_expected_sha256 = sys.argv[1]\n"
        "if hashlib.sha256(inventory_source).hexdigest() != inventory_expected_sha256:\n"
        "    raise SystemExit('authenticated install_inventory bytes changed in transit')\n"
        "fixture_expected_sha256 = sys.argv[2]\n"
        "if hashlib.sha256(fixture_source).hexdigest() != fixture_expected_sha256:\n"
        "    raise SystemExit('authenticated panel fixture bytes changed in transit')\n"
        "if hashlib.sha256(source).hexdigest() != sys.argv[3]:\n"
        "    raise SystemExit('authenticated validator bytes changed in transit')\n"
        "inventory_path = sys.argv[4]\n"
        "fixture_path = sys.argv[5]\n"
        "path = sys.argv[6]\n"
        "module = types.ModuleType('install_inventory')\n"
        "module.__file__ = inventory_path\n"
        "module.__package__ = None\n"
        "module.__cached__ = None\n"
        "module.__loader__ = None\n"
        "module.__spec__ = None\n"
        "sys.modules['install_inventory'] = module\n"
        "exec(compile(inventory_source, inventory_path, 'exec'), module.__dict__, module.__dict__)\n"
        "needle = b'    validate_prepare_events(state.get(\\\"events\\\"), errors)\\n'\n"
        "if source.count(needle) != 1:\n"
        "    raise SystemExit('event validation call is not uniquely mutable')\n"
        "mutant = source.replace(needle, b'    pass  # removed event validation\\n', 1)\n"
        "sys.argv = [path, *sys.argv[7:]]\n"
        "module_sources = {'install_inventory': {'path': inventory_path, "
        "'sha256': inventory_expected_sha256, 'source': inventory_source}}\n"
        "resource_sources = {'panel_input_fixtures.json': {'path': fixture_path, "
        "'sha256': fixture_expected_sha256, 'source': fixture_source}}\n"
        "scope = {\n"
        "    '__name__': '__main__',\n"
        "    '__file__': path,\n"
        "    '__package__': None,\n"
        "    '__cached__': None,\n"
        "    '__loader__': None,\n"
        "    '__spec__': None,\n"
        "    '__builtins__': __builtins__,\n"
        "    '__authenticated_source_bytes__': mutant,\n"
        "    '__authenticated_module_sources__': module_sources,\n"
        "    '__authenticated_resource_sources__': resource_sources,\n"
        "    '__panel_event_validation_mutant__': True,\n"
        "}\n"
        "exec(compile(mutant, path, 'exec'), scope, scope)\n"
    )
    return subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            driver,
            inventory_sha256,
            fixture_sha256,
            source_sha256,
            inventory_path,
            fixture_path,
            str(source_path),
            "--self-test",
            "--json",
        ],
        cwd=source_path.parent,
        env=environment,
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=PRODUCTION_EVENT_VALIDATION_MUTANT_TIMEOUT_SECONDS,
    )


def self_test(fixtures: dict[str, Any]) -> list[str]:
    errors = fixture_errors(fixtures)
    check_validation_time_budget_contract(errors)
    if errors:
        return errors
    for section in FIXTURE_CLASSES:
        removed = copy.deepcopy(fixtures); removed.pop(section, None)
        if not fixture_errors(removed):
            errors.append(f"removed panel fixture-class control did not fail: {section}")
    with tempfile.TemporaryDirectory(prefix="panel-input-contract-") as temporary:
        base = Path(temporary).resolve(); repository = base / "repository"; repository.mkdir()
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        subprocess.run(["git", "-C", str(repository), "config", "user.name", "Panel Test"], check=True)
        subprocess.run(["git", "-C", str(repository), "config", "user.email", "panel@example.invalid"], check=True)
        source = repository / "source/SKILL.md"; source.parent.mkdir()
        source.write_text("---\nname: test\ndescription: test\n---\n", encoding="utf-8")
        source_checker = repository / "scripts/check_large_queue_guidance.py"
        source_checker.parent.mkdir()
        source_checker.write_text(
            "#!/usr/bin/env python3\n"
            "import argparse, json\n"
            "from pathlib import Path\n"
            "parser = argparse.ArgumentParser()\n"
            "parser.add_argument('--installed-root', required=True)\n"
            "parser.add_argument('--self-test', action='store_true')\n"
            "parser.add_argument('--json', action='store_true')\n"
            "args = parser.parse_args()\n"
            "passed = (Path(args.installed_root) / 'SKILL.md').read_bytes() == "
            "b'---\\nname: test\\ndescription: test\\n---\\n'\n"
            "outcome = 'PASS' if passed else 'FAIL'\n"
            "result = {'schema_version': 1, 'status': outcome, "
            "'named_mutation_outcomes': {'contract.self_test': outcome, "
            "'contract.second_control': outcome}}\n"
            "print(json.dumps(result, sort_keys=True, separators=(',', ':')))\n",
            encoding="utf-8",
        )
        manifest_repo_path = "codex/overnight-workflows/install-manifest.json"
        manifest_source = repository / manifest_repo_path; manifest_source.parent.mkdir(parents=True)
        manifest_source.write_text(json.dumps({"schema_version": 3, "mappings": [{"canonical_source": "source/SKILL.md", "installed_path": "SKILL.md"}]}, indent=2) + "\n", encoding="utf-8")
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "add",
                "source/SKILL.md",
                "scripts/check_large_queue_guidance.py",
                manifest_repo_path,
            ],
            check=True,
        )
        subprocess.run(["git", "-C", str(repository), "commit", "-qm", "target"], check=True)
        target = git(repository, "rev-parse", "HEAD").decode().strip()
        subprocess.run(["git", "-C", str(repository), "branch", "review-target", target], check=True)
        (repository / "review.txt").write_text("review input\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repository), "add", "review.txt"], check=True)
        subprocess.run(["git", "-C", str(repository), "commit", "-qm", "head"], check=True)
        head = git(repository, "rev-parse", "HEAD").decode().strip()
        tree = git(repository, "rev-parse", "HEAD^{tree}").decode().strip()
        target_tree = git(repository, "rev-parse", f"{target}^{{tree}}").decode().strip()
        (repository / "unreviewed-third.txt").write_text("third commit\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repository), "add", "unreviewed-third.txt"], check=True)
        subprocess.run(["git", "-C", str(repository), "commit", "-qm", "unreviewed third"], check=True)
        third = git(repository, "rev-parse", "HEAD").decode().strip()
        third_tree = git(repository, "rev-parse", "HEAD^{tree}").decode().strip()
        snapshot = base / "snapshot"; snapshot.mkdir(); shutil.copy2(source, snapshot / "SKILL.md")
        inventory = build_inventory(snapshot, ["SKILL.md"])
        inventory_path = base / "snapshot.inventory"; inventory_path.write_bytes(inventory.data)
        manifest_path = base / "install-manifest.json"; shutil.copy2(manifest_source, manifest_path)
        argv = ["git", "-C", str(repository), "diff", *DIFF_FLAGS, target, head, "--"]
        diff = subprocess.run(argv, check=True, stdout=subprocess.PIPE).stdout
        diff_path = base / "input.diff"; diff_path.write_bytes(diff)
        consumer_argv = [
            "git", "-C", str(repository), "diff", *DIFF_FLAGS, target, target, "--"
        ]
        consumer_diff = subprocess.run(
            consumer_argv, check=True, stdout=subprocess.PIPE
        ).stdout
        consumer_diff_path = base / "consumer-input.diff"
        consumer_diff_path.write_bytes(consumer_diff)
        immutable_root = base / "operation/immutable-source"
        checker_path = immutable_root / "scripts/check_large_queue_guidance.py"
        checker_path.parent.mkdir(parents=True)
        shutil.copy2(source_checker, checker_path)
        immutable_inventory = build_inventory(
            immutable_root, ["scripts/check_large_queue_guidance.py"]
        )
        immutable_inventory_path = base / "immutable-source.inventory"
        immutable_inventory_path.write_bytes(immutable_inventory.data)
        exchange_slot = immutable_root.parent / "exchange-slot"
        exchange_slot.mkdir()
        shutil.copy2(source, exchange_slot / "SKILL.md")
        outcome = {
            "contract.self_test": "PASS",
            "contract.second_control": "PASS",
        }
        staged_result = {
            "schema_version": 1,
            "status": "PASS",
            "named_mutation_outcomes": outcome,
        }
        staged = {
            "argv": [
                str(Path(sys.executable).resolve()), str(checker_path),
                "--installed-root", str(exchange_slot), "--self-test", "--json",
            ],
            "authenticated_launch": {
                "protocol": AUTHENTICATED_LAUNCH_PROTOCOL,
                "python_executable": str(Path(sys.executable).resolve()),
                "isolation_flags": ["-I", "-B"],
                "source_transport": "inherited-read-only-file-descriptor",
                "source_path": str(checker_path),
                "source_sha256": hashlib.sha256(checker_path.read_bytes()).hexdigest(),
                "logical_argv": [
                    str(Path(sys.executable).resolve()), str(checker_path),
                    "--installed-root", str(exchange_slot), "--self-test", "--json",
                ],
                "authenticated_source_sha256": {},
            },
            "checker_sha256": hashlib.sha256(checker_path.read_bytes()).hexdigest(),
            "exit_status": 0,
            "named_mutation_outcomes": outcome,
            "result": staged_result,
            "stderr": "",
            "stdout": json.dumps(staged_result, sort_keys=True, separators=(",", ":")) + "\n",
        }
        operation_id = "panel-prepare-test"
        receipt_path = base / "prepare.json"
        state_path = base / "state.json"
        receipt = {
            "schema_version": 3,
            "operation_id": operation_id,
            "source": {
                "repository": str(repository), "commit": head, "tree": tree,
                "manifest_path": manifest_repo_path,
                "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            },
            "immutable_source": {
                "root": str(immutable_root), "path": str(immutable_inventory_path),
                "format": INVENTORY_FORMAT, "sha256": immutable_inventory.digest,
                "file_count": immutable_inventory.file_count,
                "total_bytes": immutable_inventory.total_bytes,
                "commit": head, "tree": tree,
            },
            "expected_live_source": {
                "commit": head, "tree": tree,
                "manifest_path": manifest_repo_path,
                "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            },
            "predecessor_source": {
                "root": str(immutable_root), "path": str(immutable_inventory_path),
                "format": INVENTORY_FORMAT, "sha256": immutable_inventory.digest,
                "file_count": immutable_inventory.file_count,
                "total_bytes": immutable_inventory.total_bytes,
                "commit": head, "tree": tree,
            },
            "candidate_inventory": {
                "format": INVENTORY_FORMAT, "sha256": inventory.digest,
                "file_count": inventory.file_count, "total_bytes": inventory.total_bytes,
            },
            "preflight_live_inventory": {
                "format": INVENTORY_FORMAT, "sha256": inventory.digest,
                "file_count": inventory.file_count, "total_bytes": inventory.total_bytes,
            },
            "evidence_snapshot": {
                "format": INVENTORY_FORMAT, "sha256": inventory.digest,
                "file_count": inventory.file_count, "total_bytes": inventory.total_bytes,
            },
            "generation_id": inventory.digest,
            "mutation_outcome": "NO_LIVE_MUTATION_PREPARED",
            "named_mutation_outcomes": {"staged": outcome},
            "staged_validation": staged,
            "prepared_at": "2026-08-09T06:00:00Z",
        }
        state = {
            "schema_version": 2, "operation_id": operation_id, "status": "PREPARED",
            "state_root": str(base / "operation-state"),
            "install_root": str(exchange_slot),
            "evidence_root": str(base / "evidence"),
            "source_repository": str(repository), "source_commit": head, "source_tree": tree,
            "expected_live_source_commit": head,
            "expected_live_source_tree": tree,
            "manifest_path": manifest_repo_path,
            "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "manifest_schema_version": 3,
            "expected_live_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "expected_live_manifest_schema_version": 3,
            "generation_id": inventory.digest,
            "candidate_expected_paths": ["SKILL.md"],
            "preflight_expected_paths": ["SKILL.md"],
            "immutable_source": copy.deepcopy(receipt["immutable_source"]),
            "predecessor_source": copy.deepcopy(receipt["predecessor_source"]),
            "candidate_inventory": copy.deepcopy(receipt["candidate_inventory"]),
            "preflight_inventory": copy.deepcopy(receipt["preflight_live_inventory"]),
            "evidence_snapshot": copy.deepcopy(receipt["evidence_snapshot"]),
            "prepare_receipt": {}, "validation": {"staged": copy.deepcopy(staged)},
            "created_at": "2026-08-09T05:59:00Z",
            "updated_at": "2026-08-09T06:00:00Z",
            "events": [
                {"at": "2026-08-09T05:59:00Z", "event": "prepare_started"},
                {"at": "2026-08-09T06:00:00Z", "event": "prepare_completed"},
            ],
        }
        record = {
            "schema_version": 3, "record_type": "panel_input", "panel_id": "panel-test",
            "finalization_id": "finalization-test", "recorded_at": "2026-08-09T06:00:00Z",
            "review_boundary": PREPUBLICATION_BOUNDARY,
            "repository_roles": {
                "installed_source": "package_source",
                "consumer": "consumer_fixture",
            },
            "repositories": {
                "package_source": {
                    "repository": str(repository), "target_ref": "refs/heads/review-target",
                    "target_ref_sha_at_dispatch": target, "target_sha": target,
                    "merge_base_sha": target, "head_sha": head, "head_tree_oid": tree,
                    "diff_argv": argv, "diff_path": str(diff_path),
                    "diff_digest_algorithm": "SHA-256",
                    "diff_digest": hashlib.sha256(diff).hexdigest(),
                },
                "consumer_fixture": {
                    "repository": str(repository), "target_ref": "refs/heads/review-target",
                    "target_ref_sha_at_dispatch": target, "target_sha": target,
                    "merge_base_sha": target, "head_sha": target,
                    "head_tree_oid": target_tree,
                    "diff_argv": consumer_argv, "diff_path": str(consumer_diff_path),
                    "diff_digest_algorithm": "SHA-256",
                    "diff_digest": hashlib.sha256(consumer_diff).hexdigest(),
                },
            },
            "installed": {
                "root": str(snapshot), "inventory_path": str(inventory_path),
                "inventory_format": INVENTORY_FORMAT, "inventory_sha256": inventory.digest,
                "file_count": inventory.file_count, "total_bytes": inventory.total_bytes,
                "generation_id": inventory.digest, "source_repository": str(repository),
                "source_commit": head, "source_tree": tree, "install_manifest_path": str(manifest_path),
                "install_manifest_repository_path": manifest_repo_path,
                "install_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest()},
            "prepare": {
                "operation_id": operation_id, "receipt_path": str(receipt_path),
                "receipt_sha256": "", "state_path": str(state_path), "state_sha256": "",
                "mutation_outcome": "NO_LIVE_MUTATION_PREPARED",
            },
            "scope": copy.deepcopy(PREPUBLICATION_SCOPE),
        }

        def write_prepare_pair(
            test_record: dict[str, Any],
            test_receipt: dict[str, Any],
            test_state: dict[str, Any],
        ) -> None:
            test_staged = test_receipt["staged_validation"]
            test_receipt["named_mutation_outcomes"] = {
                "staged": copy.deepcopy(test_staged["named_mutation_outcomes"])
            }
            receipt_data = (
                json.dumps(test_receipt, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode()
            receipt_path.write_bytes(receipt_data)
            receipt_digest = hashlib.sha256(receipt_data).hexdigest()
            test_state["prepare_receipt"] = {
                "path": str(receipt_path), "sha256": receipt_digest,
            }
            test_state["validation"] = {"staged": copy.deepcopy(test_staged)}
            state_data = (
                json.dumps(test_state, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode()
            state_path.write_bytes(state_data)
            test_record["prepare"].update(
                {
                    "receipt_path": str(receipt_path),
                    "receipt_sha256": receipt_digest,
                    "state_path": str(state_path),
                    "state_sha256": hashlib.sha256(state_data).hexdigest(),
                }
            )

        def write_state_only(
            test_record: dict[str, Any], test_state: dict[str, Any]
        ) -> None:
            state_data = (
                json.dumps(test_state, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode()
            state_path.write_bytes(state_data)
            test_record["prepare"]["state_path"] = str(state_path)
            test_record["prepare"]["state_sha256"] = hashlib.sha256(
                state_data
            ).hexdigest()

        write_prepare_pair(record, receipt, state)
        baseline = validate_panel_input(record)
        if baseline:
            return errors + ["panel self-test baseline failed: " + " | ".join(baseline)]
        legacy_record = copy.deepcopy(record)
        legacy_record["schema_version"] = 2
        legacy_record.pop("repository_roles")
        legacy_errors = validate_panel_input(legacy_record)
        if legacy_errors:
            return errors + [
                "legacy schema-2 panel read control failed: "
                + " | ".join(legacy_errors)
            ]
        for case in fixtures["compatibility_cases"]:
            compatibility_record = copy.deepcopy(record)
            compatibility_record["schema_version"] = (
                3 if case["kind"] == "schema3_distinct" else 2
            )
            if compatibility_record["schema_version"] == 2:
                compatibility_record.pop("repository_roles")
            compatibility_state = copy.deepcopy(state)
            original_receipt_path = base / f"{case['name']}.original.json"
            if case["kind"] in {
                "distinct_identical",
                "different_bytes",
                "schema3_distinct",
            }:
                original_receipt_path.write_bytes(receipt_path.read_bytes())
            if case["kind"] == "different_bytes":
                original_receipt_path.write_bytes(receipt_path.read_bytes() + b"drift\n")
            nested_path = (
                str(base / "archive" / ".." / "malformed-original.json")
                if case["kind"] == "malformed_path"
                else str(original_receipt_path)
            )
            if case["kind"] != "same_path":
                compatibility_state["prepare_receipt"]["path"] = nested_path
            if case["kind"] == "different_digest":
                compatibility_state["prepare_receipt"]["sha256"] = "0" * 64
            write_state_only(compatibility_record, compatibility_state)
            found = validate_panel_input(compatibility_record)
            if case["expect"] == "PASS":
                if found:
                    errors.append(
                        f"panel compatibility control {case['name']} failed: {found}"
                    )
            elif not any(case["expect"] in item for item in found):
                errors.append(
                    f"panel compatibility control {case['name']} did not fail "
                    f"as expected: {found}"
                )
            write_prepare_pair(record, receipt, state)
        if globals().get("__panel_event_validation_mutant__") is True:
            mutant_record = copy.deepcopy(record)
            mutant_receipt = copy.deepcopy(receipt)
            mutant_state = copy.deepcopy(state)
            mutant_state["events"].pop(0)
            write_prepare_pair(mutant_record, mutant_receipt, mutant_state)
            if not validate_panel_input(mutant_record):
                errors.append("production event validation mutation probe did not fail")
            return errors
        for case in fixtures["omission_cases"]:
            mutated = copy.deepcopy(record); remove_path(mutated, case["path"])
            if not validate_panel_input(mutated, verify=False):
                errors.append(f"panel omission control {case['name']} did not fail")
        for case in fixtures["drift_cases"]:
            mutated = copy.deepcopy(record); mutation = case["mutation"]; restore = False
            if mutation == "target_ref":
                mutated["repositories"]["package_source"]["target_ref_sha_at_dispatch"] = "0" * 40
                mutated["repositories"]["package_source"]["target_sha"] = "0" * 40
            elif mutation == "raw_sha_target_ref":
                mutated["repositories"]["package_source"]["target_ref"] = target
            elif mutation == "dot_target_ref":
                mutated["repositories"]["package_source"]["target_ref"] = "refs/heads/../review-target"
            elif mutation == "merge_base_not_target":
                mutated["repositories"]["package_source"]["merge_base_sha"] = head
            elif mutation == "diff_argv":
                mutated["repositories"]["package_source"]["diff_argv"] = argv[:-3] + argv[-2:]
            elif mutation == "diff_repository_argv":
                mutated["repositories"]["package_source"]["diff_argv"][2] = str(base)
            elif mutation == "diff_flag_order":
                mutated["repositories"]["package_source"]["diff_argv"][5:7] = reversed(
                    mutated["repositories"]["package_source"]["diff_argv"][5:7]
                )
            elif mutation == "diff_digest": mutated["repositories"]["package_source"]["diff_digest"] = "0" * 64
            elif mutation == "inventory_digest": mutated["installed"]["inventory_sha256"] = "0" * 64
            elif mutation == "inventory_count": mutated["installed"]["file_count"] += 1
            elif mutation == "inventory_bytes": mutated["installed"]["total_bytes"] += 1
            elif mutation == "generation": mutated["installed"]["generation_id"] = ""
            elif mutation == "source_commit": mutated["installed"]["source_commit"] = target
            elif mutation == "source_tree": mutated["installed"]["source_tree"] = "0" * 40
            elif mutation == "snapshot_byte":
                (snapshot / "SKILL.md").write_bytes(source.read_bytes() + b"x"); restore = True
            elif mutation == "installed_repository_join":
                mutated["installed"]["source_commit"] = third
                mutated["installed"]["source_tree"] = third_tree
            elif mutation == "repository_role_key_drift":
                mutated["repository_roles"]["consumer"] = "missing_repository"
            elif mutation == "repository_role_duplicate":
                mutated["repository_roles"]["consumer"] = "package_source"
            elif mutation == "installed_source_role_drift":
                mutated["repository_roles"] = {
                    "installed_source": "consumer_fixture",
                    "consumer": "package_source",
                }
            elif mutation == "hardcoded_repository_names_without_role_map":
                mutated["repositories"] = {
                    case["repository_keys"][0]: mutated["repositories"]["package_source"],
                    case["repository_keys"][1]: mutated["repositories"]["consumer_fixture"],
                }
            elif mutation == "repository_identity_too_long":
                original_key = "consumer_fixture"
                too_long = "r" * 257
                mutated["repositories"][too_long] = mutated["repositories"].pop(original_key)
                mutated["repository_roles"]["consumer"] = too_long
            found = validate_panel_input(mutated)
            if restore: shutil.copy2(source, snapshot / "SKILL.md")
            if not any(case["expect"] in item for item in found):
                errors.append(f"panel drift control {case['name']} did not fail as expected: {found}")

        for section in ("boundary_cases", "scope_cases"):
            for case in fixtures[section]:
                mutated = copy.deepcopy(record)
                if "changes" in case:
                    for change in case["changes"]:
                        set_path(mutated, change["path"], change["value"])
                else:
                    set_path(mutated, case["path"], case["value"])
                found = validate_panel_input(mutated, verify=False)
                if not any(case["expect"] in item for item in found):
                    errors.append(
                        f"panel {section} control {case['name']} did not fail as expected: {found}"
                    )

        for case in fixtures["undeclared_cases"]:
            mutated = copy.deepcopy(record)
            cursor: Any = mutated
            for component in case["path"]:
                cursor = cursor[component]
            cursor[case["field"]] = case["value"]
            found = validate_panel_input(mutated, verify=False)
            if not any(case["expect"] in item for item in found):
                errors.append(
                    f"panel undeclared control {case['name']} did not fail as expected: {found}"
                )

        for case in fixtures["duplicate_key_cases"]:
            duplicate_target = case["target"]
            duplicate_path = case["path"]
            duplicate_key = case["key"]
            duplicate_first = case["first_value"]
            if duplicate_target == "manifest":
                probe = base / f"{case['name']}.jsonl"
                probe.write_bytes(
                    duplicate_key_text(
                        copy.deepcopy(record),
                        duplicate_path,
                        duplicate_key,
                        duplicate_first,
                    ).encode("utf-8")
                    + b"\n"
                )
                _, found = load_jsonl(probe)
            elif duplicate_target == "install_manifest":
                mutated = copy.deepcopy(record)
                probe = base / f"{case['name']}.install-manifest.json"
                probe.write_bytes(
                    duplicate_key_text(
                        strict_json_loads(manifest_path.read_text(encoding="utf-8")),
                        duplicate_path,
                        duplicate_key,
                        duplicate_first,
                    ).encode("utf-8")
                    + b"\n"
                )
                mutated["installed"]["install_manifest_path"] = str(probe)
                found = validate_panel_input(mutated)
            else:
                mutated = copy.deepcopy(record)
                mutated_receipt = copy.deepcopy(receipt)
                mutated_state = copy.deepcopy(state)
                if duplicate_target == "staged_stdout":
                    mutated_receipt["staged_validation"]["stdout"] = (
                        duplicate_key_text(
                            copy.deepcopy(
                                mutated_receipt["staged_validation"]["result"]
                            ),
                            duplicate_path,
                            duplicate_key,
                            duplicate_first,
                        )
                        + "\n"
                    )
                mutated_receipt["named_mutation_outcomes"] = {
                    "staged": copy.deepcopy(
                        mutated_receipt["staged_validation"]["named_mutation_outcomes"]
                    )
                }
                receipt_probe = base / f"{case['name']}.receipt.json"
                receipt_data = (
                    (
                        duplicate_key_text(
                            mutated_receipt,
                            duplicate_path,
                            duplicate_key,
                            duplicate_first,
                        )
                        if duplicate_target == "receipt"
                        else _canonical_json(mutated_receipt)
                    )
                    + "\n"
                ).encode("utf-8")
                receipt_probe.write_bytes(receipt_data)
                receipt_digest = hashlib.sha256(receipt_data).hexdigest()
                mutated_state["prepare_receipt"] = {
                    "path": str(receipt_probe), "sha256": receipt_digest,
                }
                mutated_state["validation"] = {
                    "staged": copy.deepcopy(mutated_receipt["staged_validation"])
                }
                state_probe = base / f"{case['name']}.state.json"
                state_data = (
                    (
                        duplicate_key_text(
                            mutated_state,
                            duplicate_path,
                            duplicate_key,
                            duplicate_first,
                        )
                        if duplicate_target == "state"
                        else _canonical_json(mutated_state)
                    )
                    + "\n"
                ).encode("utf-8")
                state_probe.write_bytes(state_data)
                mutated["prepare"].update(
                    {
                        "receipt_path": str(receipt_probe),
                        "receipt_sha256": receipt_digest,
                        "state_path": str(state_probe),
                        "state_sha256": hashlib.sha256(state_data).hexdigest(),
                    }
                )
                found = validate_panel_input(mutated)
            if not any(case["expect"] in item for item in found):
                errors.append(
                    f"panel duplicate-key control {case['name']} did not fail as expected: {found}"
                )

        for case in fixtures["prepare_cases"]:
            mutated = copy.deepcopy(record)
            mutated_receipt = copy.deepcopy(receipt)
            mutated_state = copy.deepcopy(state)
            mutation = case["mutation"]
            production_found: Optional[list[str]] = None
            if mutation == "fabricated_prepare":
                missing = base / "does-not-exist"
                mutated["prepare"].update(
                    {
                        "receipt_path": str(missing / "prepare.json"),
                        "receipt_sha256": "0" * 64,
                        "state_path": str(missing / "state.json"),
                        "state_sha256": "0" * 64,
                        "mutation_outcome": "LIVE_MUTATED",
                    }
                )
            elif case["target"] == "record":
                set_path(mutated, case["path"], case["value"])
            elif case["target"] == "receipt":
                set_path(mutated_receipt, case["path"], case["value"])
                write_prepare_pair(mutated, mutated_receipt, mutated_state)
            elif case["target"] == "state":
                if mutation in {"state_receipt_path_drift", "state_receipt_sha_drift"}:
                    write_prepare_pair(mutated, mutated_receipt, mutated_state)
                    if mutation == "state_receipt_path_drift":
                        mutated_state["prepare_receipt"]["path"] = str(state_path)
                    else:
                        mutated_state["prepare_receipt"]["sha256"] = "0" * 64
                    state_data = (
                        json.dumps(mutated_state, sort_keys=True, separators=(",", ":"))
                        + "\n"
                    ).encode()
                    state_path.write_bytes(state_data)
                    mutated["prepare"]["state_sha256"] = hashlib.sha256(
                        state_data
                    ).hexdigest()
                elif mutation == "remove_event":
                    mutated_state["events"].pop(case["index"])
                elif mutation == "reverse_events":
                    mutated_state["events"].reverse()
                elif mutation == "append_event":
                    mutated_state["events"].append(copy.deepcopy(case["event"]))
                elif mutation == "add_event_field":
                    mutated_state["events"][case["index"]][case["field"]] = case["value"]
                else:
                    set_path(mutated_state, case["path"], case["value"])
                if mutation not in {"state_receipt_path_drift", "state_receipt_sha_drift"}:
                    write_prepare_pair(mutated, mutated_receipt, mutated_state)
            elif case["target"] == "staged":
                if mutation == "drop_self_test":
                    mutated_receipt["staged_validation"]["argv"].remove("--self-test")
                elif mutation == "fake_launcher":
                    fake_launcher = base / "fake-python"
                    fake_launcher.write_text(
                        "#!/bin/sh\nprintf '%s\\n' "
                        "'{\"schema_version\":1,\"status\":\"PASS\","
                        "\"named_mutation_outcomes\":{}}'\n",
                        encoding="utf-8",
                    )
                    fake_launcher.chmod(0o700)
                    mutated_receipt["staged_validation"]["argv"][0] = str(
                        fake_launcher
                    )
                elif mutation == "checker_source_drift":
                    checker_path.write_bytes(checker_path.read_bytes() + b"# drift\n")
                    mutated_receipt["staged_validation"]["checker_sha256"] = (
                        hashlib.sha256(checker_path.read_bytes()).hexdigest()
                    )
                elif mutation == "drop_outcome":
                    test_staged = mutated_receipt["staged_validation"]
                    test_staged["named_mutation_outcomes"].pop(
                        "contract.second_control"
                    )
                    test_staged["result"]["named_mutation_outcomes"] = copy.deepcopy(
                        test_staged["named_mutation_outcomes"]
                    )
                    test_staged["stdout"] = (
                        json.dumps(
                            test_staged["result"],
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n"
                    )
                elif mutation == "exchange_slot_drift":
                    (exchange_slot / "SKILL.md").write_bytes(
                        (exchange_slot / "SKILL.md").read_bytes() + b"drift\n"
                    )
                else:
                    set_path(
                        mutated_receipt["staged_validation"], case["path"], case["value"]
                    )
                write_prepare_pair(mutated, mutated_receipt, mutated_state)
            elif case["target"] == "production":
                source_path = Path(__file__).resolve()
                mutant_environment = os.environ.copy()
                try:
                    completed = _run_production_event_validation_mutant(
                        source_path,
                        mutant_environment,
                    )
                except subprocess.TimeoutExpired:
                    errors.append(
                        "production event validation mutant timed out after "
                        f"{PRODUCTION_EVENT_VALIDATION_MUTANT_TIMEOUT_SECONDS} seconds"
                    )
                    production_found = []
                else:
                    combined_output = (completed.stdout + completed.stderr).decode(
                        "utf-8", "replace"
                    )
                    production_found = (
                        [case["expect"]]
                        if completed.returncode != 0
                        and case["expect"] in combined_output
                        else []
                    )
            found = (
                production_found
                if production_found is not None
                else validate_panel_input(mutated)
            )
            if not any(case["expect"] in item for item in found):
                errors.append(
                    f"panel prepare control {case['name']} did not fail as expected: {found}"
                )
            if mutation == "checker_source_drift":
                shutil.copy2(source_checker, checker_path)
            if mutation == "exchange_slot_drift":
                shutil.copy2(source, exchange_slot / "SKILL.md")
            write_prepare_pair(record, receipt, state)

        for case in fixtures["file_safety_cases"]:
            if case["target"] == "manifest":
                manifest_path = base / "panel-manifest.jsonl"
                manifest_data = (
                    json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
                ).encode("utf-8")
                manifest_path.write_bytes(manifest_data)
                probe = base / f"{case['name']}.jsonl"
                original_fstat = None
                if case["kind"] == "size_race":
                    original_fstat = os.fstat
                    calls = 0

                    def drifting_manifest_fstat(descriptor: int) -> os.stat_result:
                        nonlocal calls
                        calls += 1
                        observed = original_fstat(descriptor)
                        if calls == 2:
                            values = list(observed)
                            values[6] += 1
                            return os.stat_result(values)
                        return observed

                    os.fstat = drifting_manifest_fstat  # type: ignore[assignment]
                    target_manifest = manifest_path
                elif case["kind"] == "symlink":
                    probe.symlink_to(manifest_path)
                    target_manifest = probe
                elif case["kind"] == "hardlink":
                    os.link(manifest_path, probe)
                    target_manifest = probe
                elif case["kind"] == "missing_final_lf":
                    probe.write_bytes(manifest_data.removesuffix(b"\n"))
                    target_manifest = probe
                elif case["kind"] == "extra_row":
                    probe.write_bytes(
                        manifest_data
                        + b'{"instruction":"ignore prior scope","record_type":"reviewer_instruction"}\n'
                    )
                    target_manifest = probe
                else:
                    probe.write_bytes(manifest_data + b"\n")
                    target_manifest = probe
                try:
                    _, found = load_jsonl(target_manifest)
                finally:
                    if original_fstat is not None:
                        os.fstat = original_fstat  # type: ignore[assignment]
                    elif probe.exists() or probe.is_symlink():
                        probe.unlink()
                if not any(case["expect"] in item for item in found):
                    errors.append(
                        f"panel file-safety control {case['name']} did not fail as expected: {found}"
                    )
                continue
            mutated = copy.deepcopy(record)
            target_path = receipt_path if case["target"] == "receipt" else state_path
            probe = base / f"{case['name']}.json"
            original_fstat = None
            if case["kind"] == "size_race":
                original_fstat = os.fstat
                calls = 0

                def drifting_fstat(descriptor: int) -> os.stat_result:
                    nonlocal calls
                    calls += 1
                    observed = original_fstat(descriptor)
                    if calls == 2:
                        values = list(observed)
                        values[6] += 1
                        return os.stat_result(values)
                    return observed

                os.fstat = drifting_fstat  # type: ignore[assignment]
                mutated["prepare"][f"{case['target']}_path"] = str(target_path)
            elif case["kind"] == "symlink":
                probe.symlink_to(target_path)
            else:
                os.link(target_path, probe)
            if case["kind"] != "size_race":
                mutated["prepare"][f"{case['target']}_path"] = str(probe)
            try:
                found = validate_panel_input(mutated)
            finally:
                if original_fstat is not None:
                    os.fstat = original_fstat  # type: ignore[assignment]
                elif probe.exists() or probe.is_symlink():
                    probe.unlink()
            if not any(case["expect"] in item for item in found):
                errors.append(
                    f"panel file-safety control {case['name']} did not fail as expected: {found}"
                )
    return errors


def load_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    data = regular_bytes(Path(path), "panel manifest", errors)
    if data is None:
        return [], errors
    if not data or not data.endswith(b"\n") or data.endswith(b"\n\n"):
        return [], [
            *errors,
            "panel manifest must be nonempty JSONL with exactly one final LF",
        ]
    lines = data[:-1].split(b"\n")
    if any(not line or b"\r" in line for line in lines):
        return [], [*errors, "panel manifest must not contain blank or CR-framed rows"]
    records: list[dict[str, Any]] = []
    for number, raw_line in enumerate(lines, 1):
        try:
            line = raw_line.decode("utf-8")
            value = strict_json_loads(line)
        except (UnicodeError, json.JSONDecodeError, DuplicateJSONKey) as exc:
            errors.append(f"panel manifest line {number} is invalid JSON: {exc}"); continue
        if not isinstance(value, dict): errors.append(f"panel manifest line {number} must be an object")
        else: records.append(value)
    if len(records) != 1 or records[0].get("record_type") != "panel_input":
        errors.append(
            "panel manifest must contain exactly one panel_input row and no other rows"
        )
    return records, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, help="panel JSONL manifest to validate")
    parser.add_argument("--self-test", action="store_true", help="run omission and drift controls")
    parser.add_argument("--json", action="store_true", help="emit one machine-readable result object")
    args = parser.parse_args()
    if not args.manifest and not args.self_test: parser.error("provide --manifest and/or --self-test")
    errors: list[str] = []
    check_validation_time_budget_contract(errors)
    try: fixtures = _load_panel_fixtures()
    except (OSError, UnicodeError, json.JSONDecodeError, DuplicateJSONKey, RuntimeError) as exc:
        fixtures = {}; errors.append(f"cannot read panel fixtures: {exc}")
    errors.extend(fixture_errors(fixtures))
    if args.self_test and not errors: errors.extend(self_test(fixtures))
    if args.manifest:
        records, found = load_jsonl(args.manifest); errors.extend(found)
        inputs = [record for record in records if record.get("record_type") == "panel_input"]
        if len(inputs) != 1: errors.append(f"panel manifest must contain exactly one panel_input; found {len(inputs)}")
        else: errors.extend(validate_panel_input(inputs[0]))
    if errors:
        for error in errors: print(f"ERROR: {error}", file=sys.stderr if args.json else sys.stdout)
        if args.json:
            print(json.dumps({"schema_version": 1, "status": "FAIL", "named_mutation_outcomes": {}, "errors": errors}, sort_keys=True, separators=(",", ":")))
        return 1
    if args.json:
        outcomes: dict[str, str] = {}
        if args.self_test:
            outcomes["panel.production_event_mutant.timeout_budget_60_seconds"] = "PASS"
            outcomes["panel.production_event_mutant.parent_margin_60_seconds"] = "PASS"
            outcomes["panel.staged_checker.timeout_budget_300_seconds"] = "PASS"
            outcomes["panel.staged_checker.timeout_headroom_120_seconds"] = "PASS"
            outcomes["panel.schema3.non_doodle_repository_roles"] = "PASS"
            outcomes["panel.schema2.legacy_two_repository_read"] = "PASS"
            for section in FIXTURE_CLASSES:
                outcomes[f"panel.fixture_inventory.removed_class.{section}"] = "PASS"
                for case in fixtures[section]:
                    outcomes[f"panel.{section}.{case['name']}"] = "PASS"
        print(json.dumps({"schema_version": 1, "status": "PASS", "named_mutation_outcomes": dict(sorted(outcomes.items()))}, sort_keys=True, separators=(",", ":")))
        return 0
    print("OK: panel input manifest/snapshot contract" + (" and negative controls" if args.self_test else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
