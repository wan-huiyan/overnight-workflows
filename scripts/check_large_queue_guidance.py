#!/usr/bin/env python3
"""Validate large-queue state, recovery, routing, and installed package closure."""

import argparse
from collections import Counter
import copy
from datetime import datetime
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import sys
import tempfile
from typing import Any, Optional
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
SKILL = ROOT / "plugins/overnight-multi-issue-implementation/SKILL.md"
REFERENCE = (
    ROOT
    / "plugins/overnight-multi-issue-implementation/references/large-live-queue-orchestration.md"
)
TIEBREAKER = (
    ROOT
    / "plugins/overnight-insight-discovery/assets/tiebreaker_prompt_template.md"
)
CODEX = ROOT / "codex/overnight-workflows/SKILL.md"
INSTALL_MANIFEST = ROOT / "codex/overnight-workflows/install-manifest.json"
STATE_CONTRACT = ROOT / "scripts/large_queue_state_contract.json"
STATE_FIXTURES = ROOT / "scripts/large_queue_state_fixtures.json"
RESOURCE_DIRS = ("references", "assets", "scripts")
EXPECTED_INSTALL_COUNT = 38
INSPECTION_FIELDS = {
    "host",
    "pid",
    "tool_session",
    "journal",
    "leases",
    "logs",
    "worktree",
    "diff",
    "commits",
    "conclusion",
}
PROJECT_LOCAL_REFERENCES = {
    (
        "plugins/overnight-insight-discovery/SKILL.md",
        "scripts/build_drive_bundle.py",
    )
}


def require(text: str, phrase: str, where: Path, errors: list[str]) -> None:
    if phrase not in text:
        errors.append(f"{where.relative_to(ROOT)}: missing {phrase!r}")


def read_json(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path.relative_to(ROOT)}: cannot read JSON: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{path.relative_to(ROOT)}: root must be an object")
        return {}
    return value


def expected_install_mappings() -> list[dict[str, str]]:
    """Derive every install mapping from the complete local plugin packages."""
    mappings: list[dict[str, str]] = [
        {
            "canonical_source": "codex/overnight-workflows/SKILL.md",
            "installed_path": "SKILL.md",
        },
        {
            "canonical_source": "codex/overnight-workflows/agents/openai.yaml",
            "installed_path": "agents/openai.yaml",
        },
    ]
    for skill in sorted(ROOT.glob("plugins/*/SKILL.md")):
        plugin = skill.parent.name
        mappings.append(
            {
                "canonical_source": skill.relative_to(ROOT).as_posix(),
                "installed_path": f"references/workflows/{plugin}/SKILL.md",
                "navigation_stub": (
                    "codex/overnight-workflows/references/workflows/"
                    f"{plugin}/SKILL.md"
                ),
            }
        )
        for resource_dir in RESOURCE_DIRS:
            package_dir = skill.parent / resource_dir
            if not package_dir.is_dir():
                continue
            resources = sorted(
                path
                for path in package_dir.rglob("*")
                if path.is_file() or path.is_symlink()
            )
            for source in resources:
                relative = source.relative_to(skill.parent).as_posix()
                mappings.append(
                    {
                        "canonical_source": source.relative_to(ROOT).as_posix(),
                        "installed_path": (
                            f"references/workflows/{plugin}/{relative}"
                        ),
                    }
                )
    return mappings


def json_examples(markdown: str, errors: list[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for block in re.findall(r"```json\n(.*?)\n```", markdown, flags=re.DOTALL):
        try:
            value = json.loads(block)
        except json.JSONDecodeError as exc:
            errors.append(f"large-queue reference has invalid JSON example: {exc}")
            continue
        if isinstance(value, dict) and isinstance(value.get("record_type"), str):
            records.append(value)
    return records


def markdown_local_targets(text: str) -> list[str]:
    """Return local Markdown/HTML dependency targets, excluding code fences."""
    without_fences = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    candidates = re.findall(
        r"!?\[[^\]]*\]\(([^)\n]+)\)", without_fences
    ) + re.findall(
        r"(?:href|src)=[\"']([^\"']+)[\"']", without_fences
    ) + re.findall(
        r"^\s*\[[^\]]+\]:\s*(\S+)", without_fences, flags=re.MULTILINE
    ) + re.findall(
        r"<([^<>\s]+\.(?:md|markdown|html?|css|js|json|ya?ml|sql|py|sh|"
        r"png|jpe?g|gif|svg|webp|csv|tsv|txt)(?:[?#][^<>\s]*)?)>",
        without_fences,
        flags=re.IGNORECASE,
    )
    local: list[str] = []
    for candidate in candidates:
        candidate = candidate.strip()
        if candidate.startswith("<") and ">" in candidate:
            candidate = candidate[1:candidate.index(">")]
        else:
            candidate = candidate.split(maxsplit=1)[0]
        if not candidate or candidate.startswith("#"):
            continue
        parsed = urlsplit(candidate)
        if parsed.scheme or parsed.netloc:
            continue
        path = unquote(parsed.path)
        # Templates contain future-repository links such as
        # ``../blob/<branch>/...`` and prose may show an ellipsis placeholder
        # such as ``../charts/...``. They are generated-output examples, not
        # dependencies of this installed package. Concrete relative targets
        # remain subject to the closure check below.
        if "<" in path or ">" in path or "..." in PurePosixPath(path).parts:
            continue
        if path:
            local.append(path)
    return local


def package_textual_targets(text: str) -> list[str]:
    """Find concrete package-root paths named in prose or inline code."""
    return sorted(
        set(
            re.findall(
                r"(?<![A-Za-z0-9_./-])"
                r"((?:references|assets|scripts)/[A-Za-z0-9_./-]+"
                r"\.(?:md|yaml|yml|sql|py|json|sh))",
                text,
            )
        )
    )


def logical_installed_target(installed_path: str, target: str) -> Optional[str]:
    if target.startswith("/") or "\\" in target:
        return None
    joined = posixpath.normpath(
        posixpath.join(posixpath.dirname(installed_path), target)
    )
    if joined == ".." or joined.startswith("../"):
        return None
    return joined


def check_canonical_dependency_closure(
    mappings: list[dict[str, str]], errors: list[str]
) -> None:
    source_to_installed = {
        (ROOT / mapping["canonical_source"]).resolve(): mapping["installed_path"]
        for mapping in mappings
    }
    installed_files = set(source_to_installed.values())
    for mapping in mappings:
        source = ROOT / mapping["canonical_source"]
        if source.suffix.lower() != ".md" or not source.is_file():
            continue
        try:
            text = source.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"cannot read canonical Markdown {source}: {exc}")
            continue
        for target in markdown_local_targets(text):
            canonical_target = (source.parent / target).resolve()
            try:
                canonical_target.relative_to(ROOT.resolve())
            except ValueError:
                errors.append(
                    f"canonical local link escapes repository: "
                    f"{source.relative_to(ROOT)} -> {target}"
                )
                continue
            if not canonical_target.exists():
                errors.append(
                    f"canonical local link is missing: "
                    f"{source.relative_to(ROOT)} -> {target}"
                )
                continue

            logical_target = logical_installed_target(
                mapping["installed_path"], target
            )
            if logical_target is None:
                errors.append(
                    f"installed local link escapes package: "
                    f"{mapping['installed_path']} -> {target}"
                )
                continue
            if canonical_target.is_dir():
                prefix = logical_target.rstrip("/") + "/"
                if not any(path.startswith(prefix) for path in installed_files):
                    errors.append(
                        f"installed directory dependency is absent: "
                        f"{mapping['installed_path']} -> {target}"
                    )
            else:
                expected_target = source_to_installed.get(canonical_target)
                if expected_target is None:
                    errors.append(
                        f"canonical dependency is not in install manifest: "
                        f"{source.relative_to(ROOT)} -> {target}"
                    )
                elif expected_target != logical_target:
                    errors.append(
                        f"installed relative link changes target: "
                        f"{mapping['installed_path']} -> {target}; "
                        f"expected {expected_target}, got {logical_target}"
                    )

        relative_source = source.relative_to(ROOT).as_posix()
        source_parts = PurePosixPath(relative_source).parts
        if len(source_parts) < 3 or source_parts[0] != "plugins":
            continue
        plugin = source_parts[1]
        canonical_package_root = ROOT / "plugins" / plugin
        installed_package_root = f"references/workflows/{plugin}"
        for target in package_textual_targets(text):
            if (relative_source, target) in PROJECT_LOCAL_REFERENCES:
                continue
            canonical_target = (canonical_package_root / target).resolve()
            if not canonical_target.is_file():
                errors.append(
                    f"canonical textual dependency is missing: "
                    f"{relative_source} -> {target}"
                )
                continue
            expected_target = source_to_installed.get(canonical_target)
            logical_target = f"{installed_package_root}/{target}"
            if expected_target is None:
                errors.append(
                    f"canonical textual dependency is not in install manifest: "
                    f"{relative_source} -> {target}"
                )
            elif expected_target != logical_target:
                errors.append(
                    f"installed textual dependency changes target: "
                    f"{mapping['installed_path']} -> {target}; "
                    f"expected {expected_target}, got {logical_target}"
                )


def check_disk_markdown_dependencies(
    installed_root: Path, expected_paths: set[str], errors: list[str]
) -> None:
    installed_root_resolved = installed_root.resolve()
    for relative in sorted(expected_paths):
        path = installed_root / relative
        if path.suffix.lower() != ".md" or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"cannot read installed Markdown {relative}: {exc}")
            continue
        for target in markdown_local_targets(text):
            resolved = (path.parent / target).resolve()
            try:
                resolved.relative_to(installed_root_resolved)
            except ValueError:
                errors.append(f"installed local link escapes root: {relative} -> {target}")
                continue
            if not resolved.exists():
                errors.append(f"installed local link is missing: {relative} -> {target}")
        relative_parts = PurePosixPath(relative).parts
        if (
            len(relative_parts) >= 4
            and relative_parts[:2] == ("references", "workflows")
        ):
            plugin_root = installed_root.joinpath(*relative_parts[:3])
            canonical_relative = None
            if relative_parts[3:] == ("SKILL.md",):
                canonical_relative = f"plugins/{relative_parts[2]}/SKILL.md"
            elif len(relative_parts) >= 5:
                canonical_relative = (
                    f"plugins/{relative_parts[2]}/"
                    + "/".join(relative_parts[3:])
                )
            for target in package_textual_targets(text):
                if canonical_relative and (
                    canonical_relative,
                    target,
                ) in PROJECT_LOCAL_REFERENCES:
                    continue
                if not (plugin_root / target).is_file():
                    errors.append(
                        f"installed textual dependency is missing: "
                        f"{relative} -> {target}"
                    )


def check_installed_inventory(
    installed_root: Path, mappings: list[dict[str, str]], errors: list[str]
) -> None:
    if installed_root.is_symlink():
        errors.append(f"installed skill root is a symlink: {installed_root}")
        return
    if not installed_root.is_dir():
        errors.append(f"installed skill root is not a directory: {installed_root}")
        return
    symlinks = sorted(
        path.relative_to(installed_root).as_posix()
        for path in installed_root.rglob("*")
        if path.is_symlink()
    )
    for symlink in symlinks:
        errors.append(f"installed umbrella contains a symlink: {symlink}")
    if symlinks:
        return

    expected_paths = {mapping["installed_path"] for mapping in mappings}
    actual_paths = {
        path.relative_to(installed_root).as_posix()
        for path in installed_root.rglob("*")
        if path.is_file()
    }
    for missing in sorted(expected_paths - actual_paths):
        errors.append(f"installed umbrella is missing: {missing}")
    for unexpected in sorted(actual_paths - expected_paths):
        errors.append(f"installed umbrella has undeclared file: {unexpected}")
    for mapping in mappings:
        source = ROOT / mapping["canonical_source"]
        installed = installed_root / mapping["installed_path"]
        if source.is_file() and installed.is_file():
            if source.read_bytes() != installed.read_bytes():
                errors.append(
                    "installed umbrella differs from canonical source: "
                    f"{mapping['installed_path']}"
                )
    check_disk_markdown_dependencies(installed_root, expected_paths, errors)


def value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def is_nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (str, list, dict)):
        return bool(value)
    return True


def validate_inspection_evidence(evidence: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(evidence, dict):
        return ["inspection_evidence must be an object"]
    missing = INSPECTION_FIELDS - evidence.keys()
    if missing:
        errors.append("inspection_evidence missing: " + ", ".join(sorted(missing)))
    for field in INSPECTION_FIELDS & evidence.keys():
        value = evidence[field]
        if not isinstance(value, dict):
            errors.append(f"inspection_evidence {field} must be an object")
            continue
        if set(value) != {"status", "detail"}:
            errors.append(
                f"inspection_evidence {field} must contain only status and detail"
            )
        status = value.get("status")
        detail = value.get("detail")
        if status not in {"CLEAR", "ACTIVE", "UNKNOWN"}:
            errors.append(f"inspection_evidence {field} has invalid status")
        elif status != "CLEAR":
            errors.append(f"inspection_evidence {field} is not CLEAR")
        if not isinstance(detail, str) or not detail.strip():
            errors.append(f"inspection_evidence {field} detail must be nonempty")
    return errors


def validate_record(
    record: Any, contract: dict[str, Any], label: str
) -> list[str]:
    errors: list[str] = []
    if not isinstance(record, dict):
        return [f"{label}: record must be an object"]
    record_type = record.get("record_type")
    schemas = contract.get("records")
    if not isinstance(schemas, dict) or record_type not in schemas:
        return [f"{label}: unknown record_type {record_type!r}"]
    schema = schemas[record_type]
    required = schema.get("required", [])
    optional = schema.get("optional", [])
    if not isinstance(required, list) or not isinstance(optional, list):
        return [f"{label}: malformed field contract"]
    for field in required:
        if field not in record:
            errors.append(f"{label}: missing required field {field}")
    allowed = set(required) | set(optional)
    for field in sorted(set(record) - allowed):
        errors.append(f"{label}: undeclared field {field}")

    types = schema.get("types", {})
    if not isinstance(types, dict):
        errors.append(f"{label}: malformed type contract")
        types = {}
    for field, value in record.items():
        allowed_types = types.get(field)
        if not isinstance(allowed_types, list):
            errors.append(f"{label}: no type contract for field {field}")
            continue
        actual = value_type(value)
        if actual not in allowed_types:
            errors.append(
                f"{label}: field {field} has type {actual}; "
                f"expected {'/'.join(allowed_types)}"
            )

    if record.get("schema_version") != contract.get("record_schema_version"):
        errors.append(f"{label}: wrong schema_version")
    states = schema.get("states")
    if isinstance(states, list) and record.get("state") not in states:
        errors.append(f"{label}: invalid state {record.get('state')!r}")
    enums = schema.get("enums", {})
    if isinstance(enums, dict):
        for field, values in enums.items():
            if field in record and record[field] not in values:
                errors.append(f"{label}: invalid {field} {record[field]!r}")
    forbidden = schema.get("forbidden", [])
    if isinstance(forbidden, list):
        for field in forbidden:
            if field in record:
                errors.append(f"{label}: forbidden field {field}")

    state_rules = schema.get("state_rules", {})
    rules = state_rules.get(record.get("state"), {}) if isinstance(state_rules, dict) else {}
    for field in rules.get("non_null", []):
        if not is_nonempty(record.get(field)):
            errors.append(f"{label}: nonempty field {field} required")
    for field in rules.get("null", []):
        if record.get(field) is not None:
            errors.append(f"{label}: field {field} must be null")

    if record_type == "controller_liveness":
        authorized = record.get("authorized_successor_ids")
        if not isinstance(authorized, list) or not authorized:
            errors.append(f"{label}: authorized_successor_ids must be a nonempty array")
        elif any(not isinstance(item, str) or not item.strip() for item in authorized):
            errors.append(
                f"{label}: authorized_successor_ids entries must be nonempty strings"
            )
        elif len(set(authorized)) != len(authorized):
            errors.append(f"{label}: authorized_successor_ids must be unique")
        if record.get("state") == "STOPPED":
            errors.extend(
                f"{label}: {error}"
                for error in validate_inspection_evidence(
                    record.get("inspection_evidence")
                )
            )
            allowed_stoppers = (
                set(authorized)
                if isinstance(authorized, list)
                and all(isinstance(item, str) for item in authorized)
                else set()
            )
            allowed_stoppers.add(record.get("controller_id"))
            if record.get("stopped_by") not in allowed_stoppers:
                errors.append(f"{label}: stopped_by is not an authorized controller")
    if record_type == "path_reservation":
        exact_paths = record.get("exact_paths")
        if isinstance(exact_paths, list):
            if not exact_paths:
                errors.append(f"{label}: exact_paths must not be empty")
            for exact_path in exact_paths:
                if not isinstance(exact_path, str):
                    errors.append(f"{label}: exact_paths entries must be strings")
                    continue
                parsed = PurePosixPath(exact_path)
                if (
                    not exact_path
                    or exact_path.startswith("/")
                    or ".." in parsed.parts
                    or "\\" in exact_path
                    or parsed.as_posix() != exact_path
                ):
                    errors.append(f"{label}: path is not repository-relative: {exact_path}")
        owner = record.get("owner")
        if isinstance(owner, dict) and not owner:
            errors.append(f"{label}: owner must not be empty")
    return errors


def find_example(
    examples: list[dict[str, Any]], record_type: str, state: Optional[str] = None
) -> Optional[dict[str, Any]]:
    for record in examples:
        if record.get("record_type") == record_type and (
            state is None or record.get("state") == state
        ):
            return record
    return None


def apply_mutation(record: dict[str, Any], fixture: dict[str, Any]) -> dict[str, Any]:
    changed = copy.deepcopy(record)
    for old, new in fixture.get("rename", {}).items():
        if old in changed:
            changed[new] = changed.pop(old)
    for field in fixture.get("remove", []):
        changed.pop(field, None)
    for field, value in fixture.get("set", {}).items():
        changed[field] = value
    return changed


def sequence_is_valid(sequences: Any) -> bool:
    return bool(
        isinstance(sequences, list)
        and all(isinstance(value, int) and not isinstance(value, bool) for value in sequences)
        and all(later > earlier for earlier, later in zip(sequences, sequences[1:]))
    )


def valid_reservation_transfer(old: dict[str, Any], replacement: Optional[dict[str, Any]]) -> bool:
    replacement_id = old.get("replacement_reservation_id")
    return bool(
        old.get("state") == "TRANSFERRED"
        and replacement_id
        and replacement_id != old.get("reservation_id")
        and replacement
        and replacement.get("reservation_id") == replacement_id
        and replacement.get("state") == "ACTIVE"
        and replacement.get("run_id") == old.get("run_id")
        and replacement.get("repository_id") == old.get("repository_id")
        and replacement.get("exact_paths") == old.get("exact_paths")
    )


def parse_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def clear_inspection_evidence(conclusion: str) -> dict[str, dict[str, str]]:
    details = {
        "host": "host inspected; old controller absent",
        "pid": "PID not running",
        "tool_session": "session closed",
        "journal": "latest sequence inspected",
        "leases": "all joined leases ended after process inspection",
        "logs": "last log record inspected",
        "worktree": "worktree inspected",
        "diff": "diff inspected",
        "commits": "commits inspected",
        "conclusion": conclusion,
    }
    return {
        field: {"status": "CLEAR", "detail": detail}
        for field, detail in details.items()
    }


def takeover_case_result(
    case: dict[str, Any],
    controller: dict[str, Any],
    lease: dict[str, Any],
    contract: dict[str, Any],
) -> tuple[bool, str]:
    successor_id = "controller-successor"
    working_controller = {
        **controller,
        "heartbeat_expires_at": case.get("controller_heartbeat_expires_at"),
    }
    evidence = clear_inspection_evidence("no live controller or work")
    if not case.get("inspection_complete"):
        evidence.pop("journal")
    overrides = case.get("inspection_status_overrides")
    if isinstance(overrides, dict):
        evidence.update(copy.deepcopy(overrides))

    stopped = {
        **working_controller,
        "sequence": working_controller.get("sequence", 0) + 1,
        "state": "STOPPED",
        "stopped_at": case.get("observed_at"),
        "stop_reason": "inspected crashed-controller takeover",
        "inspection_evidence": evidence,
        "stopped_by": case.get("stopped_by"),
    }
    successor = {
        **working_controller,
        "sequence": working_controller.get("sequence", 0) + 2,
        "controller_id": successor_id,
        "heartbeat_at": "2026-08-09T01:00:01Z",
        "heartbeat_expires_at": "2026-08-09T01:20:01Z",
        "stopped_at": None,
        "stop_reason": None,
        "inspection_evidence": None,
        "stopped_by": None,
        "authorized_successor_ids": ["controller-third"],
    }
    joined_lease = {
        **lease,
        "controller_id": case.get("joined_lease_controller_id"),
    }
    ended_lease = {
        **joined_lease,
        "sequence": joined_lease.get("sequence", 0) + 1,
        "state": "ENDED",
        "ended_at": case.get("observed_at"),
        "end_reason": "inspection confirmed process exit",
    }
    relevant_leases = [
        joined_lease if case.get("active_lease_remains") else ended_lease
    ]

    stop_errors = validate_record(stopped, contract, f"{case.get('name')}.stop")
    successor_errors = validate_record(
        successor, contract, f"{case.get('name')}.successor"
    )
    lease_errors = [
        error
        for index, item in enumerate(relevant_leases)
        for error in validate_record(
            item, contract, f"{case.get('name')}.lease[{index}]"
        )
    ]
    evidence_errors = validate_inspection_evidence(evidence)
    observed_at = parse_timestamp(case.get("observed_at"))
    heartbeat_expires_at = parse_timestamp(
        working_controller.get("heartbeat_expires_at")
    )
    heartbeat_expired = bool(
        observed_at
        and heartbeat_expires_at
        and heartbeat_expires_at <= observed_at
    )
    leases_join_controller = all(
        item.get("controller_id") == working_controller.get("controller_id")
        for item in relevant_leases
    )
    valid = bool(
        heartbeat_expired
        and case.get("stop_appended")
        and not stop_errors
        and not successor_errors
        and not lease_errors
        and not evidence_errors
        and leases_join_controller
        and all(item.get("state") == "ENDED" for item in relevant_leases)
    )

    controller_records = [working_controller]
    if case.get("stop_appended"):
        controller_records.append(stopped)
    controller_records.append(successor)
    latest: dict[str, dict[str, Any]] = {}
    for record in controller_records:
        current = latest.get(record["controller_id"])
        if current is None or record["sequence"] > current["sequence"]:
            latest[record["controller_id"]] = record
    if sum(item.get("state") == "RUNNING" for item in latest.values()) != 1:
        valid = False
    return valid, "PASS" if valid else "UNCHECKED"


def check_operational_separation(
    fixture: Any,
    controller: dict[str, Any],
    lease: dict[str, Any],
    reservation: dict[str, Any],
    contract: dict[str, Any],
    errors: list[str],
) -> None:
    """Prove stopping supervision does not release an independent PR claim."""
    if not isinstance(fixture, dict):
        errors.append("operational_separation fixture must be an object")
        return
    evidence = clear_inspection_evidence(
        "controller and lease stopped; PR remains open"
    )
    stopped = {
        **controller,
        "sequence": controller["sequence"] + 1,
        "state": fixture.get("controller_state"),
        "stopped_at": "2026-08-09T01:00:00Z",
        "stop_reason": "clean controller shutdown",
        "inspection_evidence": evidence,
        "stopped_by": "controller-1",
    }
    ended = {
        **lease,
        "sequence": lease["sequence"] + 1,
        "state": fixture.get("lease_state"),
        "ended_at": "2026-08-09T01:00:00Z",
        "end_reason": "process inspection confirmed exit",
    }
    retained = {
        **reservation,
        "state": fixture.get("reservation_state"),
    }
    valid = not (
        validate_record(stopped, contract, "operational separation controller")
        + validate_record(ended, contract, "operational separation lease")
        + validate_record(retained, contract, "operational separation reservation")
    )
    valid = bool(
        valid
        and stopped.get("state") == "STOPPED"
        and ended.get("state") == "ENDED"
        and retained.get("state") == "ACTIVE"
        and isinstance(retained.get("owner"), dict)
        and retained["owner"].get("kind") == fixture.get("reservation_owner_kind")
    )
    if valid is not fixture.get("expected_valid"):
        errors.append(
            "operational separation control did not retain the active PR reservation"
        )


def check_state_contract(
    reference: str,
    contract: dict[str, Any],
    fixtures: dict[str, Any],
    errors: list[str],
) -> None:
    if contract.get("schema_version") != 1 or contract.get("record_schema_version") != 1:
        errors.append("state contract must declare schema versions 1")
    if fixtures.get("schema_version") != 1:
        errors.append("state fixtures must declare schema_version 1")
    examples = json_examples(reference, errors)
    for index, record in enumerate(examples):
        errors.extend(validate_record(record, contract, f"reference example {index}"))

    base_records: dict[str, dict[str, Any]] = {}
    for record_type, state in (
        ("controller_liveness", "RUNNING"),
        ("execution_lease", "ACTIVE"),
        ("path_reservation", "ACTIVE"),
        ("task_transition", None),
    ):
        record = find_example(examples, record_type, state)
        if record is None:
            errors.append(f"reference has no canonical {record_type} example")
        else:
            base_records[record_type] = record

    for fixture in fixtures.get("record_mutations", []):
        if not isinstance(fixture, dict):
            errors.append("record mutation fixture must be an object")
            continue
        base = base_records.get(fixture.get("record_type"))
        if base is None:
            continue
        mutated = apply_mutation(base, fixture)
        found = validate_record(mutated, contract, fixture.get("name", "mutation"))
        expected = fixture.get("expect_error")
        if not isinstance(expected, str) or not any(expected in item for item in found):
            errors.append(
                f"schema negative fixture {fixture.get('name')} did not fail as expected"
            )

    for fixture in fixtures.get("sequence_cases", []):
        if not isinstance(fixture, dict):
            errors.append("sequence fixture must be an object")
            continue
        actual = sequence_is_valid(fixture.get("sequences"))
        if actual is not fixture.get("expected_valid"):
            errors.append(f"sequence fixture {fixture.get('name')} produced {actual}")

    controller = base_records.get("controller_liveness")
    lease = base_records.get("execution_lease")
    reservation = base_records.get("path_reservation")
    if controller and lease and reservation:
        check_operational_separation(
            fixtures.get("operational_separation"),
            controller,
            lease,
            reservation,
            contract,
            errors,
        )
    if controller and lease:
        for fixture in fixtures.get("takeover_cases", []):
            if not isinstance(fixture, dict):
                errors.append("takeover fixture must be an object")
                continue
            actual, verification = takeover_case_result(
                fixture, controller, lease, contract
            )
            expected = fixture.get("expected_valid")
            if actual is not expected:
                errors.append(f"takeover fixture {fixture.get('name')} produced {actual}")
            if not actual and verification != "UNCHECKED":
                errors.append(f"invalid takeover {fixture.get('name')} was not UNCHECKED")

    task = base_records.get("task_transition")
    controller = base_records.get("controller_liveness")
    lease = base_records.get("execution_lease")
    reservation = base_records.get("path_reservation")
    if task and controller and task.get("controller_id") != controller.get("controller_id"):
        errors.append("task controller_id does not join controller record")
    if task and lease and task.get("lease_id") != lease.get("lease_id"):
        errors.append("task lease_id does not join execution lease")
    if lease and controller and lease.get("controller_id") != controller.get("controller_id"):
        errors.append("execution lease controller_id does not join controller record")
    if task and reservation:
        reservation_ids = task.get("reservation_ids")
        if not isinstance(reservation_ids, list) or reservation.get("reservation_id") not in reservation_ids:
            errors.append("task reservation_ids do not join reservation record")

    if reservation:
        replacement = {
            **reservation,
            "sequence": reservation["sequence"] + 1,
            "reservation_id": reservation["reservation_id"] + "-successor",
            "owner": {"kind": "agent", "id": "integrator-2", "branch": "successor"},
        }
        transferred = {
            **reservation,
            "sequence": reservation["sequence"] + 2,
            "state": "TRANSFERRED",
            "replacement_reservation_id": replacement["reservation_id"],
            "released_at": "2026-08-09T01:00:00Z",
            "release_reason": "named owner transfer",
        }
        if not valid_reservation_transfer(transferred, replacement):
            errors.append("valid reservation transfer control did not pass")
        invalid = [
            ({k: v for k, v in transferred.items() if k != "replacement_reservation_id"}, replacement),
            (transferred, None),
            (transferred, {**replacement, "state": "RELEASED"}),
            (transferred, {**replacement, "repository_id": "github.com/example/other"}),
            (transferred, {**replacement, "exact_paths": ["different/path"]}),
        ]
        if any(valid_reservation_transfer(old, new) for old, new in invalid):
            errors.append("invalid reservation transfer negative control passed")


def materialize_install(installed_root: Path, mappings: list[dict[str, str]]) -> None:
    for mapping in mappings:
        source = ROOT / mapping["canonical_source"]
        destination = installed_root / mapping["installed_path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def run_install_negative_controls(
    mappings: list[dict[str, str]], errors: list[str]
) -> None:
    with tempfile.TemporaryDirectory(prefix="overnight-install-contract-") as temp:
        installed_root = Path(temp) / "overnight-workflows"
        materialize_install(installed_root, mappings)
        baseline: list[str] = []
        check_installed_inventory(installed_root, mappings, baseline)
        if baseline:
            errors.append("self-test baseline install failed: " + " | ".join(baseline))
            return

        required = installed_root / (
            "references/workflows/overnight-insight-discovery/assets/"
            "tiebreaker_prompt_template.md"
        )
        required.unlink()
        missing_errors: list[str] = []
        check_installed_inventory(installed_root, mappings, missing_errors)
        if not any("installed umbrella is missing" in item for item in missing_errors):
            errors.append("missing-resource negative control did not fail")
        if not any(
            "installed textual dependency is missing" in item
            and "tiebreaker_prompt_template.md" in item
            for item in missing_errors
        ):
            errors.append("transitive-link missing-resource control did not fail")
        source = TIEBREAKER
        shutil.copy2(source, required)

        link_probe = installed_root / (
            "references/workflows/overnight-insight-discovery/"
            "assets/morning_summary_template.md"
        )
        original_probe = link_probe.read_bytes()
        link_probe.write_bytes(
            original_probe
            + b"\n[missing reference][probe]\n[probe]: missing-reference.md\n"
            + b"<missing-autolink.md>\n"
        )
        link_errors: list[str] = []
        check_installed_inventory(installed_root, mappings, link_errors)
        for missing_name in ("missing-reference.md", "missing-autolink.md"):
            if not any(
                "installed local link is missing" in item
                and missing_name in item
                for item in link_errors
            ):
                errors.append(
                    f"{missing_name} Markdown-link negative control did not fail"
                )
        link_probe.write_bytes(original_probe)

        victim = installed_root / (
            "references/workflows/overnight-insight-discovery/SKILL.md"
        )
        victim.unlink()
        os.symlink((ROOT / "plugins/overnight-insight-discovery/SKILL.md"), victim)
        symlink_errors: list[str] = []
        check_installed_inventory(installed_root, mappings, symlink_errors)
        if not any("contains a symlink" in item for item in symlink_errors):
            errors.append("symlink negative control did not fail")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--installed-root",
        type=Path,
        help="also require this installed umbrella to match inventory, bytes, and links",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run missing-resource and symlink negative controls in a temporary install",
    )
    args = parser.parse_args()

    errors: list[str] = []
    try:
        skill = SKILL.read_text(encoding="utf-8")
        reference = REFERENCE.read_text(encoding="utf-8")
        codex = CODEX.read_text(encoding="utf-8")
        readme = README.read_text(encoding="utf-8")
        tiebreaker = TIEBREAKER.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: cannot read required guidance: {exc}")
        return 1

    for phrase in (
        "the large-queue reference is the complete procedure",
        "does **not** continue",
        "## Issue-cluster procedure",
        "## Issue-cluster output (morning checklist)",
        "prepares reviewed commits/PRs by morning",
        "merge only\n  under an explicit recorded merge grant",
    ):
        require(skill, phrase, SKILL, errors)
    if "ships them to merged PRs by morning" in skill:
        errors.append("multi-issue routing still promises unconditional merges")

    for phrase in (
        "`source_occurrence_id`",
        "`classification_state`",
        "`EXECUTABLE`",
        "`task_state`",
        "`run_disposition`",
        "Counter(source_occurrence_ids)",
        "--no-ext-diff --no-textconv",
        "--no-renames --diff-algorithm=myers",
        "`reservation_id`",
        "`exact_paths`",
        "`takeover_condition`",
        "`released_at`",
        "`release_reason`",
        "latest appended\noperational record is authoritative",
        "Execution leases use no transfer state",
        "`TRANSFERRED` ends the old reservation",
        "`replacement_reservation_id`",
        "### Crashed-controller takeover",
        "A passed `heartbeat_expires_at` does not change",
        "`authorized_successor_ids`",
        "`status` of `CLEAR`, `ACTIVE`, or `UNKNOWN`",
        "set the takeover verification to `UNCHECKED`",
        "exactly one latest\n   `RUNNING` controller",
        "`log_path`",
        "`review_artifacts`",
    ):
        require(reference, phrase, REFERENCE, errors)
    for where, text in ((SKILL, skill), (REFERENCE, reference)):
        if "expiry or takeover condition" in text:
            errors.append(
                f"{where.relative_to(ROOT)}: makes mandatory reservation fields alternatives"
            )
    if "set(source_ids)" in reference:
        errors.append(f"{REFERENCE.relative_to(ROOT)}: filename/set coverage returned")

    for phrase in (
        "<claim_yaml>",
        "<supporting_sql>",
        "<gate_report_line>",
        "<persona_verdicts_json>",
        "<known_knowns_row>",
        '"verdict": "approve"',
        "zero to three concise strings",
        "No track brief prose, chart files, or prior-round reasoning chains",
    ):
        require(tiebreaker, phrase, TIEBREAKER, errors)

    source_occurrences = Counter([("same-prompt.md", 1), ("same-prompt.md", 2)])
    reused_parent = Counter([("same-prompt.md", 1), ("same-prompt.md", 1)])
    if source_occurrences == reused_parent:
        errors.append("duplicate-occurrence negative control did not fail")

    for phrase in (
        "name: overnight-workflows",
        "source occurrence",
        "merge-base SHA",
        "controller liveness",
        "execution leases",
        "path reservations",
        "references/workflows/<plugin>/SKILL.md",
    ):
        require(codex, phrase, CODEX, errors)

    mappings = expected_install_mappings()
    if len(mappings) != EXPECTED_INSTALL_COUNT:
        errors.append(
            f"install package has {len(mappings)} mappings; "
            f"expected pinned closure of {EXPECTED_INSTALL_COUNT}"
        )
    require(
        readme,
        f"complete {EXPECTED_INSTALL_COUNT}-file mapping",
        README,
        errors,
    )
    require(
        readme,
        f"all {EXPECTED_INSTALL_COUNT} source/install SHA-256 digests",
        README,
        errors,
    )
    stub_count = sum("navigation_stub" in mapping for mapping in mappings)
    require(readme, f"The {stub_count} tracked entrypoint route stubs", README, errors)
    require(readme, "every local Markdown dependency resolves", README, errors)
    require(readme, "explicit merge-on-green grant", README, errors)
    require(readme, "pull-request authority is absent", README, errors)

    manifest = read_json(INSTALL_MANIFEST, errors)
    if manifest.get("schema_version") != 2:
        errors.append("install manifest must use schema_version 2")
    if manifest.get("installed_skill") != "overnight-workflows":
        errors.append("install manifest has wrong installed_skill")
    if manifest.get("layout") != "per-workflow-package":
        errors.append("install manifest has wrong layout")
    if manifest.get("mappings") != mappings:
        errors.append("install manifest is incomplete, stale, or not mechanically ordered")

    installed_paths = [mapping["installed_path"] for mapping in mappings]
    canonical_sources = [mapping["canonical_source"] for mapping in mappings]
    if len(set(installed_paths)) != len(installed_paths):
        errors.append("install manifest repeats an installed path")
    if len(set(canonical_sources)) != len(canonical_sources):
        errors.append("install manifest repeats a canonical source")
    for mapping in mappings:
        source = ROOT / mapping["canonical_source"]
        if source.is_symlink():
            errors.append(f"canonical install source is a symlink: {mapping['canonical_source']}")
            continue
        if not source.is_file():
            errors.append(f"missing canonical install source: {mapping['canonical_source']}")
        installed_path = PurePosixPath(mapping["installed_path"])
        if mapping["installed_path"].startswith("/") or ".." in installed_path.parts:
            errors.append(f"unsafe installed path: {mapping['installed_path']}")
        route_path = mapping.get("navigation_stub")
        is_entrypoint = mapping["installed_path"].endswith("/SKILL.md")
        if is_entrypoint and not route_path:
            errors.append(f"workflow entrypoint has no navigation stub: {mapping['installed_path']}")
        if route_path:
            route = ROOT / route_path
            if not route.is_file():
                errors.append(f"missing Codex route: {route_path}")
                continue
            route_text = route.read_text(encoding="utf-8")
            match = re.search(r"\]\(([^)]+)\)", route_text)
            if not match or (route.parent / match.group(1)).resolve() != source.resolve():
                errors.append(f"Codex route does not resolve to source: {route_path}")

    check_canonical_dependency_closure(mappings, errors)
    contract = read_json(STATE_CONTRACT, errors)
    fixtures = read_json(STATE_FIXTURES, errors)
    check_state_contract(reference, contract, fixtures, errors)

    if args.self_test:
        run_install_negative_controls(mappings, errors)
    if args.installed_root:
        check_installed_inventory(args.installed_root.expanduser(), mappings, errors)

    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    installed_note = ", installed inventory/bytes/links" if args.installed_root else ""
    self_test_note = ", negative controls" if args.self_test else ""
    print(
        "OK: queue coverage, canonical state/recovery fixtures, "
        f"{len(mappings)}-file package "
        f"closure/routes/links{installed_note}{self_test_note}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
