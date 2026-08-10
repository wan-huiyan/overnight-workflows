#!/usr/bin/env python3
"""Validate large-queue state, recovery, routing, and installed package closure."""

import argparse
from collections import Counter
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import select
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
import types
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
ROUTING_CASES = ROOT / "scripts/eval/overnight-workflow-routing-cases.json"
RESOURCE_DIRS = ("references", "assets", "scripts")
EXPECTED_INSTALL_COUNT = 44
INSTALL_IDENTITY_FRAMING = "overnight-workflows-install-input-v1"
INSTALL_INVENTORY_FORMAT = "sha256-size-path-v1"
EXPECTED_CODEX_VERSION = "0.147.0"
MULTI_ISSUE_PLUGIN = "overnight-multi-issue-implementation"
INSTALLED_PANEL_VALIDATOR_SELF_TEST_TIMEOUT_SECONDS = 120
_HELD_PYTHON_WITH_DEPENDENCIES_FD_LAUNCHER = r"""
import hashlib
import os
import stat
import sys
import types

def held_source(descriptor, expected_sha256):
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode):
        raise SystemExit("held source is not a regular file")
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
        raise SystemExit("held source changed while it was read")
    if hashlib.sha256(source).hexdigest() != expected_sha256:
        raise SystemExit("held source digest differs from authenticated bytes")
    return source

main_descriptor = int(sys.argv[1])
main_sha256 = sys.argv[2]
main_path = sys.argv[3]
module_name = sys.argv[4]
module_descriptor = int(sys.argv[5])
module_sha256 = sys.argv[6]
module_path = sys.argv[7]
resource_name = sys.argv[8]
resource_descriptor = int(sys.argv[9])
resource_sha256 = sys.argv[10]
resource_path = sys.argv[11]
script_arguments = sys.argv[12:]
module_source = held_source(module_descriptor, module_sha256)
resource_source = held_source(resource_descriptor, resource_sha256)
module = types.ModuleType(module_name)
module.__file__ = module_path
module.__package__ = None
module.__cached__ = None
module.__loader__ = None
module.__spec__ = None
sys.modules[module_name] = module
exec(compile(module_source, module_path, "exec"), module.__dict__, module.__dict__)
main_source = held_source(main_descriptor, main_sha256)
sys.argv = [main_path, *script_arguments]
namespace = {
    "__name__": "__main__",
    "__file__": main_path,
    "__package__": None,
    "__cached__": None,
    "__loader__": None,
    "__spec__": None,
    "__builtins__": __builtins__,
    "__authenticated_source_bytes__": main_source,
    "__authenticated_module_sources__": {
        module_name: {
            "path": module_path,
            "sha256": module_sha256,
            "source": module_source,
        }
    },
    "__authenticated_resource_sources__": {
        resource_name: {
            "path": resource_path,
            "sha256": resource_sha256,
            "source": resource_source,
        }
    },
}
exec(compile(main_source, main_path, "exec"), namespace, namespace)
"""
PANEL_VALIDATOR_RESOURCES = (
    "install_inventory.py",
    "panel_input_fixtures.json",
    "validate_panel_inputs.py",
)
PANEL_VALIDATOR_INSTALLED_DIRECTORY = (
    "references/workflows/overnight-multi-issue-implementation/scripts"
)
AUTHENTICATED_CHECKER_CONTROL_SOURCE_PATHS = frozenset(
    {
        "scripts/large_queue_state_contract.json",
        "scripts/large_queue_state_fixtures.json",
        "scripts/eval/overnight-workflow-routing-cases.json",
    }
)
AUTHENTICATED_NESTED_VALIDATION_SOURCE_PATHS = frozenset(
    {
        "scripts/install_inventory.py",
        "scripts/panel_input_fixtures.json",
        "scripts/validate_panel_inputs.py",
        "plugins/overnight-review-client-delivery/scripts/action_authority.py",
        "plugins/schedule-poll-orchestrator-pattern/scripts/poll_orchestrator.py",
    }
)
AUTHENTICATED_VALIDATION_SOURCE_PATHS = (
    AUTHENTICATED_CHECKER_CONTROL_SOURCE_PATHS
    | AUTHENTICATED_NESTED_VALIDATION_SOURCE_PATHS
)
PANEL_SCHEMA3_ROUTE_MARKERS = (
    "New panel inputs must use generic schema 3",
    "Set `schema_version` to `3`",
    "exact bijection—that is, a one-to-one mapping with no omissions—from semantic "
    "roles to the complete set of keys in `repositories`",
    "The role map must include `installed_source`",
    "unique reviewed repository whose repository path, `head_sha`, and `head_tree_oid` "
    "equal the installed snapshot's `source_repository`, `source_commit`, and `source_tree`",
    "Derive finalization `repository_heads` mechanically from the validated panel input",
    '"repository_key": repository_key',
    '"head_sha": repositories[repository_key]["head_sha"]',
    "Do not create new schema-2 panel rows",
)
PANEL_SCHEMA3_README_MARKERS = (
    "Panel dispatches use generic schema-3 `panel_input` records",
    "an exact bijection—a one-to-one mapping covering every `repositories` key—from "
    "semantic roles to repository keys",
    "derive finalization `repository_heads` mechanically from that validated map",
)
PRE_DISPATCH_VALIDATION_ROUTE_MARKERS = (
    "the same source-review row must declare its exact generic gate",
    '"required_pre_dispatch_validation"',
    'literal `state: "PASS"`',
    "rejects a missing registration, changed bytes, a non-PASS state or receipt, "
    "another review ID, or an invalidation row",
    "An archived generic-v2 source row without `required_pre_dispatch_validation` "
    "keeps its original ungated meaning",
)
PRE_DISPATCH_VALIDATION_README_MARKERS = (
    "A source review may also declare one exact "
    "`required_pre_dispatch_validation`",
    "an `artifact_registered` row must bind the same artifact and review with "
    "state `PASS`",
    "rejects absence, byte drift, invalidation, non-PASS status, or a different "
    "review ID",
    "retain their explicit ungated compatibility meaning",
)
SOURCE_REVIEW_BOUNDARY_ROUTE_MARKERS = (
    "The generic-v2 `raw_input_registered` row is only for the "
    "`postpublication-installed-snapshot` boundary",
    "It cannot dispatch a `prepublication-source-and-staged-snapshot` review",
    "Every new prepublication source/staged review must use "
    "`source_review_input_registered`",
    "Duplicate JSON keys fail, including duplicate review IDs or configured "
    "status fields",
)
SOURCE_REVIEW_BOUNDARY_README_MARKERS = (
    "Generic-v2 `raw_input_registered` rows are restricted to the "
    "`postpublication-installed-snapshot` boundary",
    "A `prepublication-source-and-staged-snapshot` dispatch must use "
    "`source_review_input_registered`",
    "decoded with duplicate-key rejection",
)
UTC_TIMESTAMP = re.compile(
    r"^(?:[0-9]{4})-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]{1,6})?Z$"
)
IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$")
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
REASON_CODES = {
    "OWNER",
    "AUTHORITY",
    "EXISTING_PR",
    "COLLISION",
    "REVIEW",
    "AGENT_FAILURE",
    "TIME",
    "NONE",
}
EXPECTED_SEMANTIC_CONSTRAINTS = {
    "timestamp_format": "iso8601-utc-z",
    "sequence": "positive-globally-unique-append-order",
    "identity": "nonempty-normalized",
    "repository_id": "github-owner-repository-or-local-absolute-root",
    "pid": "positive-integer-or-null",
    "join_arrays": "unique-nonempty-string-identities",
    "reservation_paths": "unique-normalized-repository-relative-posix",
    "transfer": "valid-active-replacement-precedes-terminal-transfer",
}
REQUIRED_FIXTURE_NAMES = {
    "record_mutations": {
        "rename_log_path",
        "rename_review_artifacts",
        "missing_controller_expiry",
        "empty_controller_takeover_condition",
        "missing_authorized_successors",
        "lease_without_controller_join",
        "null_reservation_paths",
        "empty_controller_identity",
        "empty_run_identity",
        "malformed_repository_identity",
        "parent_repository_identity",
        "dot_repository_identity",
        "malformed_controller_timestamp",
        "timezone_naive_controller_timestamp",
        "controller_timestamp_order",
        "negative_controller_pid",
        "empty_authorized_successors",
        "malformed_authorized_successor",
        "unknown_reservation_owner",
        "dot_reservation_path",
        "parent_reservation_path",
        "tab_reservation_path",
        "newline_reservation_path",
        "relative_lease_worktree",
        "duplicate_task_reservation_join",
        "nonstring_task_reservation_join",
        "unknown_task_reason_code",
        "empty_task_next_action",
        "forbidden_task_snapshot",
    },
    "sequence_cases": {"increasing", "duplicate", "out_of_order", "nonpositive"},
    "operational_separation_cases": {
        "controller_stop_retains_active_pr_reservation",
        "controller_stop_cannot_release_open_pr_reservation",
    },
    "takeover_cases": {
        "complete_inspected_takeover",
        "stale_controller_without_inspection",
        "unavailable_pid_is_unchecked",
        "active_pid_blocks_takeover",
        "active_tool_session_blocks_takeover",
        "unknown_tool_session_blocks_takeover",
        "active_lease_blocks_takeover",
        "expiry_alone_does_not_stop",
        "unauthorized_successor",
        "heartbeat_not_expired",
        "lease_joined_to_different_controller",
    },
    "transfer_cases": {
        "valid_prior_replacement",
        "future_replacement",
        "cross_run_replacement",
        "same_id_replacement",
        "released_replacement",
    },
    "journal_cases": {
        "valid_joined_journal",
        "missing_controller_join",
        "duplicate_global_sequence",
        "two_live_controllers",
        "released_open_pr_after_stop",
    },
}
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
    ),
    (
        "plugins/overnight-multi-issue-implementation/references/large-live-queue-orchestration.md",
        "scripts/run_task.py",
    ),
}

REQUIRED_ROUTING_NEGATIVES = {
    "summarize_without_execution",
    "review_without_execution",
    "authority_is_not_inferred",
}
REQUIRED_ROUTING_NEGATIVE_CLAUSES = {
    "summarize_without_execution": (
        "A request to summarize, explain, audit, or review a plan without executing it "
        "does not enter an execution route."
    ),
    "review_without_execution": (
        "A request to summarize, explain, audit, or review a plan without executing it "
        "does not enter an execution route."
    ),
    "authority_is_not_inferred": (
        "A route choice never grants commit, push, "
        "pull-request, merge, deploy, network, paid-call, or external-write authority."
    ),
}
REQUIRED_ROUTING_CASES = {
    "parallel_branch_collision_audit": {
        "route": "references/workflows/large-redesign-parallel-branch-collision-audit/WORKFLOW.md",
        "clause": "large redesign with unmerged parallel branches",
        "prompt_contract": ["large redesign", "unmerged feature branch"],
    },
    "insight_discovery": {
        "route": "references/workflows/overnight-insight-discovery/WORKFLOW.md",
        "clause": "new client-facing insight discovery from data",
        "prompt_contract": ["overnight B-vs-C", "client insights from data"],
    },
    "multi_issue_or_live_queue": {
        "route": "references/workflows/overnight-multi-issue-implementation/WORKFLOW.md",
        "clause": "related issue cluster, stacked pull requests, or large mixed live queue",
        "prompt_contract": ["large mixed live queue", "authorized slices overnight"],
    },
    "client_delivery": {
        "route": "references/workflows/overnight-review-client-delivery/WORKFLOW.md",
        "clause": "polish an existing client deliverable",
        "prompt_contract": ["existing client report overnight", "reviewed delivery package"],
    },
    "blocked_reviewer": {
        "route": "references/workflows/overnight-review-panel-blocked-reviewer-reads-as-clean/WORKFLOW.md",
        "clause": "harden a panel whose reviewer could not read its input",
        "prompt_contract": ["unattended panel", "reviewer could not read"],
    },
    "scheduled_poll": {
        "route": "references/workflows/schedule-poll-orchestrator-pattern/WORKFLOW.md",
        "clause": "scheduled fire-ASAP multi-track orchestration",
        "prompt_contract": ["scheduled triggers", "fire-ASAP multi-track overnight run"],
    },
    "review_tier_calibration": {
        "route": "references/workflows/subagent-review-tier-calibration-for-overnight-pr-chains/WORKFLOW.md",
        "clause": "calibrate review tiers across a long independent pull-request chain",
        "prompt_contract": ["review intensity", "independent pull requests overnight"],
    },
}
REQUIRED_ROUTE_SEMANTICS = {
    "client_delivery": {
        "authority_markers": [
            "Routing never grants execution authority.",
            "With only local-work authority",
            "Commit only with a commit grant.",
            "Push only with push, network, and external-write",
            "MISSING_AUTHORITY",
        ],
        "ordered_markers": [
            [
                "After Phase A and every advisory-driven fix are complete",
                "Dispatch an independent reviewer who did not author or fix the deliverables.",
            ],
            [
                "Dispatch an independent reviewer who did not author or fix the deliverables.",
                "Enter Phase C only while the final reviewer report still matches the frozen",
            ],
        ],
        "required_safety_markers": [
            "ADVISORY_NON_GATING",
            "FINAL_REVIEW_PENDING",
            "Any later byte change invalidates the final review.",
        ],
        "forbidden_markers": [
            "The branch should be pushed to origin with a PR opened.",
            "Push immediately after every commit",
        ],
    },
    "scheduled_poll": {
        "authority_markers": [
            "Routing here grants no execution authority.",
            "Commit, push, pull-request creation,",
            "Local consolidation and pull-request creation are",
            "MISSING_AUTHORITY",
        ],
        "ordered_markers": [
            [
                "Routing here grants no execution authority.",
                "### Use the installed state-machine helper",
            ]
        ],
        "required_safety_markers": [
            "scripts/poll_orchestrator.py",
            "CONSOLIDATION_CLAIMED",
            "RESUME_CONSOLIDATION_CLAIM",
            "schedule_external_action_authority",
            "the helper itself never consolidates, commits, pushes, schedules, spends, or",
        ],
        "forbidden_markers": [
            'Path("state/orchestrator_poll_log.jsonl").write_text',
            "run_consolidation_and_pr()",
            'json.dump(status, open("state/status.json", "w"))',
        ],
    },
}
UNCONDITIONAL_ACTION_PATTERNS = {
    "direct git push": re.compile(r"(?im)^\s*(?:[-*]\s*)?git\s+push(?:\s|$)"),
    "direct pull-request call": re.compile(r"(?im)\bcreate_pull_request\s*\("),
}


def require(text: str, phrase: str, where: Path, errors: list[str]) -> None:
    if phrase not in text:
        errors.append(f"{where.relative_to(ROOT)}: missing {phrase!r}")


def check_panel_schema3_guidance(
    reference: str,
    readme: str,
    errors: list[str],
) -> None:
    """Require the public route to produce the generic panel/finalization join."""
    normalized_reference = " ".join(reference.split())
    normalized_readme = " ".join(readme.split())
    for marker in PANEL_SCHEMA3_ROUTE_MARKERS:
        if marker not in normalized_reference:
            errors.append(f"panel schema-3 route guidance is missing {marker!r}")
    for marker in PANEL_SCHEMA3_README_MARKERS:
        if marker not in normalized_readme:
            errors.append(f"panel schema-3 README guidance is missing {marker!r}")
    for marker in PRE_DISPATCH_VALIDATION_ROUTE_MARKERS:
        if marker not in normalized_reference:
            errors.append(
                f"pre-dispatch validation route guidance is missing {marker!r}"
            )
    for marker in PRE_DISPATCH_VALIDATION_README_MARKERS:
        if marker not in normalized_readme:
            errors.append(
                f"pre-dispatch validation README guidance is missing {marker!r}"
            )
    for marker in SOURCE_REVIEW_BOUNDARY_ROUTE_MARKERS:
        if marker not in normalized_reference:
            errors.append(f"source-review boundary guidance is missing {marker!r}")
    for marker in SOURCE_REVIEW_BOUNDARY_README_MARKERS:
        if marker not in normalized_readme:
            errors.append(f"source-review boundary README is missing {marker!r}")
    for stale in (
        "Panel dispatches use exact schema-2 `panel_input` records",
        "Run that schema-2 validation before dispatch",
    ):
        if stale in normalized_reference or stale in normalized_readme:
            errors.append(f"panel route still instructs new schema-2 production: {stale!r}")


def run_panel_schema3_guidance_negative_control(
    reference: str,
    readme: str,
    errors: list[str],
) -> None:
    """Prove a route regression from schema 3 to schema 2 is observable."""
    marker = PANEL_SCHEMA3_ROUTE_MARKERS[1]
    if marker not in reference:
        errors.append("panel schema-3 regression control setup marker is absent")
        return
    mutated = reference.replace(
        marker,
        "Set `schema_version` to `2`",
        1,
    )
    observed: list[str] = []
    check_panel_schema3_guidance(mutated, readme, observed)
    if not any("panel schema-3 route guidance is missing" in item for item in observed):
        errors.append("panel schema-3 regression negative control did not fail")


def run_pre_dispatch_validation_guidance_negative_control(
    reference: str,
    readme: str,
    errors: list[str],
) -> None:
    """Prove removal of the mechanical dispatch gate is observable."""
    marker = PRE_DISPATCH_VALIDATION_ROUTE_MARKERS[0]
    normalized_reference = " ".join(reference.split())
    if marker not in normalized_reference:
        errors.append("pre-dispatch validation control setup marker is absent")
        return
    mutated = normalized_reference.replace(marker, "a prose note may mention a gate", 1)
    observed: list[str] = []
    check_panel_schema3_guidance(mutated, readme, observed)
    if not any(
        "pre-dispatch validation route guidance is missing" in item
        for item in observed
    ):
        errors.append("pre-dispatch validation negative control did not fail")


def run_source_review_boundary_guidance_negative_control(
    reference: str,
    readme: str,
    errors: list[str],
) -> None:
    """Prove a generic raw-row prepublication bypass is observable."""
    marker = SOURCE_REVIEW_BOUNDARY_ROUTE_MARKERS[0]
    normalized_reference = " ".join(reference.split())
    if marker not in normalized_reference:
        errors.append("source-review boundary control setup marker is absent")
        return
    mutated = normalized_reference.replace(
        marker,
        "The generic-v2 `raw_input_registered` row may use either boundary",
        1,
    )
    observed: list[str] = []
    check_panel_schema3_guidance(mutated, readme, observed)
    if not any(
        "source-review boundary guidance is missing" in item for item in observed
    ):
        errors.append("source-review boundary negative control did not fail")


def read_json(
    path: Path,
    errors: list[str],
    *,
    authenticated_bytes: Optional[bytes] = None,
) -> dict[str, Any]:
    try:
        text = (
            path.read_text(encoding="utf-8")
            if authenticated_bytes is None
            else authenticated_bytes.decode("utf-8")
        )
        value = json.loads(text)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
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
                "installed_path": f"references/workflows/{plugin}/WORKFLOW.md",
                "navigation_stub": (
                    "codex/overnight-workflows/references/workflows/"
                    f"{plugin}/WORKFLOW.md"
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
        if plugin == MULTI_ISSUE_PLUGIN:
            for resource_name in PANEL_VALIDATOR_RESOURCES:
                mappings.append(
                    {
                        "canonical_source": f"scripts/{resource_name}",
                        "installed_path": (
                            f"{PANEL_VALIDATOR_INSTALLED_DIRECTORY}/{resource_name}"
                        ),
                    }
                )
    for mapping in mappings:
        source = ROOT / mapping["canonical_source"]
        if source.is_file() and not source.is_symlink():
            mapping["source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    return mappings


def bind_authenticated_validation_sources(
    mappings: list[dict[str, str]], errors: list[str]
) -> Optional[dict[str, bytes]]:
    """Bind held checker controls and nested inputs to caller-supplied identities."""
    held_source_present = "__authenticated_source_bytes__" in globals()
    authenticated_context_present = "__authenticated_source_sha256__" in globals()
    authenticated = globals().get("__authenticated_source_sha256__")
    if not held_source_present and not authenticated_context_present:
        return {}
    if (
        not isinstance(globals().get("__authenticated_source_bytes__"), bytes)
        or authenticated is None
    ):
        errors.append(
            "held checker execution requires its authenticated nested validation "
            "digest context"
        )
        return None
    if not isinstance(authenticated, dict) or set(authenticated) != set(
        AUTHENTICATED_VALIDATION_SOURCE_PATHS
    ) or any(
        not isinstance(path, str)
        or not isinstance(digest, str)
        or not SHA256.fullmatch(digest)
        for path, digest in authenticated.items()
    ):
        errors.append(
            "authenticated validation digest context must contain the exact eight "
            "validation source paths with lowercase SHA-256 values"
        )
        return None
    runtime = {
        mapping["canonical_source"]: mapping.get("source_sha256")
        for mapping in mappings
        if mapping["canonical_source"]
        in AUTHENTICATED_NESTED_VALIDATION_SOURCE_PATHS
    }
    expected_nested = {
        path: authenticated[path]
        for path in AUTHENTICATED_NESTED_VALIDATION_SOURCE_PATHS
    }
    if runtime != expected_nested:
        errors.append(
            "runtime nested validation bytes differ from the authenticated "
            "immutable-source inventory"
        )
        return None
    bound_controls: dict[str, bytes] = {}
    for relative in sorted(AUTHENTICATED_CHECKER_CONTROL_SOURCE_PATHS):
        path = ROOT / relative
        try:
            descriptor, source, identity = _open_held_source(
                path,
                f"checker control {relative}",
            )
            try:
                if hashlib.sha256(source).hexdigest() != authenticated[relative]:
                    errors.append(
                        "runtime checker control bytes differ from the authenticated "
                        f"immutable-source inventory: {relative}"
                    )
                    return None
                _verify_held_source(
                    path,
                    descriptor,
                    identity,
                    f"checker control {relative}",
                )
            finally:
                os.close(descriptor)
        except (OSError, RuntimeError) as exc:
            errors.append(f"authenticated checker control could not be held: {exc}")
            return None
        bound_controls[relative] = source
    return bound_controls


def _frame(value: bytes) -> bytes:
    return struct.pack(">Q", len(value)) + value


def install_input_identity(
    mappings: list[dict[str, str]], installed_root: Optional[Path] = None
) -> str:
    """Bind mapping order, source/destination names, and mapped file bytes."""
    stream = bytearray(INSTALL_IDENTITY_FRAMING.encode("utf-8") + b"\0")
    for mapping in mappings:
        source_path = mapping["canonical_source"].encode("utf-8")
        installed_path = mapping["installed_path"].encode("utf-8")
        content_path = (
            installed_root / mapping["installed_path"]
            if installed_root is not None
            else ROOT / mapping["canonical_source"]
        )
        content = content_path.read_bytes()
        stream.extend(_frame(source_path))
        stream.extend(_frame(installed_path))
        stream.extend(_frame(content))
    return hashlib.sha256(stream).hexdigest()


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
    installed_to_source = {
        mapping["installed_path"]: (ROOT / mapping["canonical_source"]).resolve()
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
            logical_target = (
                target
                if target.startswith("references/workflows/")
                else f"{installed_package_root}/{target}"
            )
            canonical_package_target = (canonical_package_root / target).resolve()
            if canonical_package_target.is_file():
                expected_target = source_to_installed.get(canonical_package_target)
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
                continue
            canonical_target = installed_to_source.get(logical_target)
            if canonical_target is None:
                errors.append(
                    f"canonical textual dependency is not in install manifest: "
                    f"{relative_source} -> {target}"
                )
            elif not canonical_target.is_file():
                errors.append(
                    f"canonical textual dependency is missing: "
                    f"{relative_source} -> {target}"
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
            if relative_parts[3:] == ("WORKFLOW.md",):
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
                installed_target = (
                    installed_root / target
                    if target.startswith("references/workflows/")
                    else plugin_root / target
                )
                if not installed_target.is_file():
                    errors.append(
                        f"installed textual dependency is missing: "
                        f"{relative} -> {target}"
                    )


def check_installed_panel_validator(
    installed_root: Path,
    expected_resource_sha256: dict[str, str],
    errors: list[str],
) -> None:
    """Run the bundled read-only validator without writing bytecode into the skill."""
    scripts_root = installed_root.joinpath(
        *PurePosixPath(PANEL_VALIDATOR_INSTALLED_DIRECTORY).parts
    )
    publisher = scripts_root / "publish_codex_install.py"
    if publisher.exists() or publisher.is_symlink():
        errors.append("installed umbrella must not contain the mutating publisher")
    missing = [
        resource
        for resource in PANEL_VALIDATOR_RESOURCES
        if not (scripts_root / resource).is_file()
        or (scripts_root / resource).is_symlink()
    ]
    if missing:
        errors.append(
            "installed panel validator is missing required resources: "
            + ",".join(missing)
        )
        return
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        completed = _run_authenticated_installed_validator(
            scripts_root / "validate_panel_inputs.py",
            scripts_root / "install_inventory.py",
            scripts_root / "panel_input_fixtures.json",
            expected_validator_sha256=expected_resource_sha256.get(
                "validate_panel_inputs.py", ""
            ),
            expected_inventory_sha256=expected_resource_sha256.get(
                "install_inventory.py", ""
            ),
            expected_fixture_sha256=expected_resource_sha256.get(
                "panel_input_fixtures.json", ""
            ),
            cwd=str(scripts_root),
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        errors.append(
            "installed panel validator self-test timed out after "
            f"{INSTALLED_PANEL_VALIDATOR_SELF_TEST_TIMEOUT_SECONDS} seconds: {exc}"
        )
        return
    except (OSError, RuntimeError) as exc:
        errors.append(f"installed panel validator self-test could not run: {exc}")
        return
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        errors.append(
            "installed panel validator self-test failed"
            + (f": {detail[:1000]}" if detail else "")
        )
    if list(installed_root.rglob("__pycache__")):
        errors.append("installed panel validator self-test wrote bytecode into the package")


def _open_held_source(
    path: Path, label: str
) -> tuple[int, bytes, tuple[int, int, int, int, int]]:
    observed = os.lstat(path)
    if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
        raise RuntimeError(f"{label} is not a regular single-link file: {path}")
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (observed.st_dev, observed.st_ino, observed.st_size)
            != (opened.st_dev, opened.st_ino, opened.st_size)
        ):
            raise RuntimeError(f"{label} changed before it was opened: {path}")
        chunks: list[bytes] = []
        offset = 0
        while True:
            chunk = os.pread(descriptor, 1024 * 1024, offset)
            if not chunk:
                break
            chunks.append(chunk)
            offset += len(chunk)
        source = b"".join(chunks)
        after = os.fstat(descriptor)
        identity = (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        )
        if identity != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) or len(source) != after.st_size:
            raise RuntimeError(f"{label} changed while it was read: {path}")
        return descriptor, source, identity
    except BaseException:
        os.close(descriptor)
        raise


def _verify_held_source(
    path: Path,
    descriptor: int,
    identity: tuple[int, int, int, int, int],
    label: str,
) -> None:
    try:
        current = os.lstat(path)
    except FileNotFoundError as exc:
        raise RuntimeError(f"authenticated {label} path changed during execution") from exc
    if not stat.S_ISREG(current.st_mode) or (
        current.st_nlink != 1
        or (current.st_dev, current.st_ino) != identity[:2]
    ):
        raise RuntimeError(f"authenticated {label} path changed during execution")
    held = os.fstat(descriptor)
    if (
        held.st_dev,
        held.st_ino,
        held.st_size,
        held.st_mtime_ns,
        held.st_ctime_ns,
    ) != identity:
        raise RuntimeError(f"authenticated {label} bytes changed during execution")


def _run_authenticated_installed_validator(
    validator: Path,
    inventory_module: Path,
    fixtures: Path,
    *,
    expected_validator_sha256: str,
    expected_inventory_sha256: str,
    expected_fixture_sha256: str,
    cwd: str,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    validator_descriptor, validator_source, validator_identity = (
        _open_held_source(validator, "installed panel validator")
    )
    try:
        validator_sha256 = hashlib.sha256(validator_source).hexdigest()
        if (
            not SHA256.fullmatch(expected_validator_sha256)
            or validator_sha256 != expected_validator_sha256
        ):
            raise RuntimeError(
                "installed panel validator differs from authenticated package mapping"
            )
        inventory_descriptor, inventory_source, inventory_identity = (
            _open_held_source(
                inventory_module, "installed panel validator inventory dependency"
            )
        )
        try:
            inventory_sha256 = hashlib.sha256(inventory_source).hexdigest()
            if (
                not SHA256.fullmatch(expected_inventory_sha256)
                or inventory_sha256 != expected_inventory_sha256
            ):
                raise RuntimeError(
                    "installed panel validator inventory dependency differs from "
                    "authenticated package mapping"
                )
            fixture_descriptor, fixture_source, fixture_identity = _open_held_source(
                fixtures, "installed panel validator fixture dependency"
            )
            try:
                fixture_sha256 = hashlib.sha256(fixture_source).hexdigest()
                if (
                    not SHA256.fullmatch(expected_fixture_sha256)
                    or fixture_sha256 != expected_fixture_sha256
                ):
                    raise RuntimeError(
                        "installed panel validator fixture dependency differs from "
                        "authenticated package mapping"
                    )
                validator_display_path = str(
                    validator.parent.resolve() / validator.name
                )
                inventory_display_path = str(
                    inventory_module.parent.resolve() / inventory_module.name
                )
                fixture_display_path = str(
                    fixtures.parent.resolve() / fixtures.name
                )
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        "-B",
                        "-c",
                        _HELD_PYTHON_WITH_DEPENDENCIES_FD_LAUNCHER,
                        str(validator_descriptor),
                        validator_sha256,
                        validator_display_path,
                        "install_inventory",
                        str(inventory_descriptor),
                        inventory_sha256,
                        inventory_display_path,
                        "panel_input_fixtures.json",
                        str(fixture_descriptor),
                        fixture_sha256,
                        fixture_display_path,
                        "--self-test",
                    ],
                    cwd=cwd,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=INSTALLED_PANEL_VALIDATOR_SELF_TEST_TIMEOUT_SECONDS,
                    check=False,
                    pass_fds=(
                        validator_descriptor,
                        inventory_descriptor,
                        fixture_descriptor,
                    ),
                )
                _verify_held_source(
                    validator,
                    validator_descriptor,
                    validator_identity,
                    "installed panel validator",
                )
                _verify_held_source(
                    inventory_module,
                    inventory_descriptor,
                    inventory_identity,
                    "installed panel validator inventory dependency",
                )
                _verify_held_source(
                    fixtures,
                    fixture_descriptor,
                    fixture_identity,
                    "installed panel validator fixture dependency",
                )
                return completed
            finally:
                os.close(fixture_descriptor)
        finally:
            os.close(inventory_descriptor)
    finally:
        os.close(validator_descriptor)


def check_validation_time_budget_contract(errors: list[str]) -> None:
    """Keep the nested validator budget large enough for its real self-test."""
    if INSTALLED_PANEL_VALIDATOR_SELF_TEST_TIMEOUT_SECONDS != 120:
        errors.append(
            "installed panel validator self-test timeout budget must be 120 seconds"
        )


def check_installed_inventory(
    installed_root: Path, mappings: list[dict[str, str]], errors: list[str]
) -> None:
    starting_error_count = len(errors)
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
    reserved = sorted(path for path in actual_paths if PurePosixPath(path).name == "SKILL.md")
    if reserved != ["SKILL.md"]:
        errors.append(
            "installed umbrella must contain exactly one reserved SKILL.md at its root; "
            f"found {reserved}"
        )
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
    if len(errors) == starting_error_count:
        installed_identity = install_input_identity(mappings, installed_root)
        canonical_identity = install_input_identity(mappings)
        if installed_identity != canonical_identity:
            errors.append("installed umbrella aggregate identity differs from canonical")
    check_disk_markdown_dependencies(installed_root, expected_paths, errors)
    if len(errors) == starting_error_count:
        expected_resource_sha256 = {
            PurePosixPath(mapping["installed_path"]).name: mapping["source_sha256"]
            for mapping in mappings
            if mapping["installed_path"].startswith(
                PANEL_VALIDATOR_INSTALLED_DIRECTORY + "/"
            )
            and PurePosixPath(mapping["installed_path"]).name
            in {
                "validate_panel_inputs.py",
                "install_inventory.py",
                "panel_input_fixtures.json",
            }
        }
        check_installed_panel_validator(
            installed_root,
            expected_resource_sha256,
            errors,
        )


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


def is_identity(value: Any) -> bool:
    return bool(isinstance(value, str) and value.strip() == value and IDENTITY.fullmatch(value))


def contains_control_character(value: str) -> bool:
    """Reject C0 controls and DEL in every persisted identity or path."""
    return any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)


def is_repository_component(value: str) -> bool:
    return bool(
        value not in {".", ".."}
        and not contains_control_character(value)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value)
    )


def is_repository_id(value: Any) -> bool:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value.strip() != value
        or contains_control_character(value)
    ):
        return False
    if value.startswith("github.com/"):
        components = value.split("/")
        return bool(
            len(components) == 3
            and components[0] == "github.com"
            and all(is_repository_component(component) for component in components[1:])
        )
    if value.startswith("local:"):
        root = value.removeprefix("local:")
        return is_normalized_absolute_path(root)
    return False


def is_normalized_relative_path(value: Any) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or contains_control_character(value)
    ):
        return False
    parsed = PurePosixPath(value)
    return bool(
        not parsed.is_absolute()
        and value not in {".", ".."}
        and not value.endswith("/")
        and all(part not in {"", ".", ".."} for part in parsed.parts)
        and parsed.as_posix() == value
        and posixpath.normpath(value) == value
    )


def is_normalized_absolute_path(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and value
        and "\\" not in value
        and not contains_control_character(value)
        and PurePosixPath(value).is_absolute()
        and not value.endswith("/")
        and all(part not in {"", ".", ".."} for part in PurePosixPath(value).parts)
        and PurePosixPath(value).as_posix() == value
        and posixpath.normpath(value) == value
    )


def validate_owner(owner: Any) -> list[str]:
    if not isinstance(owner, dict):
        return ["owner must be an object"]
    if set(owner) != {"kind", "id", "branch"}:
        return ["owner must contain exactly kind, id, and branch"]
    if owner.get("kind") not in {
        "agent",
        "branch",
        "controller",
        "integrator",
        "pull_request",
        "worker",
    }:
        return ["owner kind is not recognized"]
    errors: list[str] = []
    if not is_identity(owner.get("id")):
        errors.append("owner id must be a nonempty identity")
    if not isinstance(owner.get("branch"), str) or not owner["branch"].strip():
        errors.append("owner branch must be a nonempty string")
    return errors


def validate_timestamp_order(
    record: dict[str, Any], earlier: str, later: str, label: str
) -> list[str]:
    first = parse_timestamp(record.get(earlier))
    second = parse_timestamp(record.get(later))
    if first is None or second is None:
        return []
    if first > second:
        return [f"{label}: timestamp {earlier} must not follow {later}"]
    return []


def validate_inspection_evidence(evidence: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(evidence, dict):
        return ["inspection_evidence must be an object"]
    missing = INSPECTION_FIELDS - evidence.keys()
    if missing:
        errors.append("inspection_evidence missing: " + ", ".join(sorted(missing)))
    extra = evidence.keys() - INSPECTION_FIELDS
    if extra:
        errors.append("inspection_evidence undeclared: " + ", ".join(sorted(extra)))
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

    sequence = record.get("sequence")
    if (
        not isinstance(sequence, int)
        or isinstance(sequence, bool)
        or sequence <= 0
    ):
        errors.append(f"{label}: sequence must be a positive integer")
    for field in ("run_id",):
        if not is_identity(record.get(field)):
            errors.append(f"{label}: {field} must be a nonempty identity")
    if not is_repository_id(record.get("repository_id")):
        errors.append(f"{label}: repository_id must be a canonical repository identity")
    id_field = schema.get("id_field")
    if not isinstance(id_field, str) or not is_identity(record.get(id_field)):
        errors.append(f"{label}: {id_field or 'record id'} must be a nonempty identity")
    for field, value in record.items():
        if field == "time" or field.endswith("_at"):
            if value is not None and parse_timestamp(value) is None:
                errors.append(f"{label}: {field} must be an ISO-8601 UTC timestamp ending Z")
    pid = record.get("pid")
    if pid is not None and (
        not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0
    ):
        errors.append(f"{label}: pid must be a positive integer or null")
    for field in (
        "takeover_condition",
        "stop_reason",
        "end_reason",
        "release_reason",
        "next_action",
    ):
        value = record.get(field)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            errors.append(f"{label}: {field} must be a nonempty string or null")

    if record_type == "controller_liveness":
        for field in ("controller_id", "host"):
            if not is_identity(record.get(field)):
                errors.append(f"{label}: {field} must be a nonempty identity")
        authorized = record.get("authorized_successor_ids")
        if not isinstance(authorized, list) or not authorized:
            errors.append(f"{label}: authorized_successor_ids must be a nonempty array")
        elif any(not is_identity(item) for item in authorized):
            errors.append(
                f"{label}: authorized_successor_ids entries must be normalized identities"
            )
        elif len(set(authorized)) != len(authorized):
            errors.append(f"{label}: authorized_successor_ids must be unique")
        tool_session_id = record.get("tool_session_id")
        if tool_session_id is not None and not is_identity(tool_session_id):
            errors.append(f"{label}: tool_session_id must be a nonempty identity or null")
        errors.extend(validate_timestamp_order(record, "heartbeat_at", "time", label))
        errors.extend(
            validate_timestamp_order(record, "heartbeat_at", "heartbeat_expires_at", label)
        )
        if record.get("state") == "STOPPED":
            errors.extend(validate_timestamp_order(record, "heartbeat_at", "stopped_at", label))
            errors.extend(validate_timestamp_order(record, "stopped_at", "time", label))
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
    if record_type == "execution_lease":
        for field in ("controller_id", "lease_id", "attempt_id", "lease_owner"):
            if not is_identity(record.get(field)):
                errors.append(f"{label}: {field} must be a nonempty identity")
        for field in ("worktree",):
            if not is_normalized_absolute_path(record.get(field)):
                errors.append(f"{label}: {field} must be a normalized absolute path")
        if not isinstance(record.get("branch"), str) or not record["branch"].strip():
            errors.append(f"{label}: branch must be a nonempty string")
        tool_session_id = record.get("tool_session_id")
        if tool_session_id is not None and not is_identity(tool_session_id):
            errors.append(f"{label}: tool_session_id must be a nonempty identity or null")
        errors.extend(validate_timestamp_order(record, "started_at", "heartbeat_at", label))
        errors.extend(validate_timestamp_order(record, "heartbeat_at", "time", label))
        errors.extend(
            validate_timestamp_order(record, "heartbeat_at", "lease_expires_at", label)
        )
        if record.get("state") == "ENDED":
            errors.extend(validate_timestamp_order(record, "heartbeat_at", "ended_at", label))
            errors.extend(validate_timestamp_order(record, "ended_at", "time", label))
    if record_type == "path_reservation":
        exact_paths = record.get("exact_paths")
        if isinstance(exact_paths, list):
            if not exact_paths:
                errors.append(f"{label}: exact_paths must not be empty")
            for exact_path in exact_paths:
                if not isinstance(exact_path, str):
                    errors.append(f"{label}: exact_paths entries must be strings")
                    continue
                if not is_normalized_relative_path(exact_path):
                    errors.append(f"{label}: path is not normalized repository-relative: {exact_path}")
            string_paths = [item for item in exact_paths if isinstance(item, str)]
            if len(set(string_paths)) != len(string_paths):
                errors.append(f"{label}: exact_paths must be unique")
        owner = record.get("owner")
        errors.extend(f"{label}: {error}" for error in validate_owner(owner))
        errors.extend(validate_timestamp_order(record, "created_at", "time", label))
        expires_at = record.get("expires_at")
        if expires_at is not None:
            errors.extend(validate_timestamp_order(record, "created_at", "expires_at", label))
        if record.get("state") in {"RELEASED", "TRANSFERRED"}:
            errors.extend(validate_timestamp_order(record, "created_at", "released_at", label))
            errors.extend(validate_timestamp_order(record, "released_at", "time", label))
        replacement_id = record.get("replacement_reservation_id")
        if replacement_id is not None and not is_identity(replacement_id):
            errors.append(
                f"{label}: replacement_reservation_id must be a nonempty identity or null"
            )
        reason_code = record.get("release_reason_code")
        if record.get("state") == "RELEASED" and reason_code not in {
            "MERGED_VERIFIED",
            "ABANDONED",
            "SUPERSEDED",
        }:
            errors.append(f"{label}: RELEASED reservation has invalid release_reason_code")
        if record.get("state") == "TRANSFERRED" and reason_code != "TRANSFER":
            errors.append(f"{label}: TRANSFERRED reservation requires TRANSFER reason code")
    if record_type == "task_transition":
        for field in ("source_occurrence_id", "task_id", "attempt_id"):
            if not is_identity(record.get(field)):
                errors.append(f"{label}: {field} must be a nonempty identity")
        for field in ("controller_id", "lease_id"):
            value = record.get(field)
            if value is not None and not is_identity(value):
                errors.append(f"{label}: {field} must be a nonempty identity or null")
        reservation_ids = record.get("reservation_ids")
        if not isinstance(reservation_ids, list):
            errors.append(f"{label}: reservation_ids must be an array")
        else:
            if any(not is_identity(item) for item in reservation_ids):
                errors.append(f"{label}: reservation_ids entries must be nonempty identities")
            if len(set(item for item in reservation_ids if isinstance(item, str))) != len(
                reservation_ids
            ):
                errors.append(f"{label}: reservation_ids must contain unique strings")
        if record.get("reason_code") not in REASON_CODES:
            errors.append(f"{label}: invalid reason_code {record.get('reason_code')!r}")
        for field in ("source_sha", "execution_base_sha", "head_sha"):
            if not isinstance(record.get(field), str) or not GIT_SHA.fullmatch(record[field]):
                errors.append(f"{label}: {field} must be a lowercase full Git SHA")
        if not is_normalized_absolute_path(record.get("worktree")):
            errors.append(f"{label}: worktree must be a normalized absolute path")
        if not isinstance(record.get("branch"), str) or not record["branch"].strip():
            errors.append(f"{label}: branch must be a nonempty string")
        retry_count = record.get("retry_count")
        if (
            not isinstance(retry_count, int)
            or isinstance(retry_count, bool)
            or retry_count < 0
        ):
            errors.append(f"{label}: retry_count must be a nonnegative integer")
        log_path = record.get("log_path")
        if log_path is not None and not is_normalized_absolute_path(log_path):
            errors.append(f"{label}: log_path must be a normalized absolute path or null")
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


def check_fixture_inventory(fixtures: dict[str, Any], errors: list[str]) -> None:
    for section, required_names in REQUIRED_FIXTURE_NAMES.items():
        cases = fixtures.get(section)
        if not isinstance(cases, list) or not cases:
            errors.append(f"state fixtures section {section} must be a nonempty array")
            continue
        names = [case.get("name") for case in cases if isinstance(case, dict)]
        if len(names) != len(cases) or any(not isinstance(name, str) or not name for name in names):
            errors.append(f"state fixtures section {section} has an unnamed/non-object case")
            continue
        duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
        if duplicates:
            errors.append(f"state fixtures section {section} repeats names: {duplicates}")
        actual = set(names)
        missing = sorted(required_names - actual)
        unknown = sorted(actual - required_names)
        if missing:
            errors.append(f"state fixtures section {section} is missing cases: {missing}")
        if unknown:
            errors.append(f"state fixtures section {section} has unknown cases: {unknown}")


def run_fixture_inventory_negative_controls(
    fixtures: dict[str, Any], errors: list[str]
) -> None:
    for section in REQUIRED_FIXTURE_NAMES:
        removed = copy.deepcopy(fixtures)
        removed.pop(section, None)
        found: list[str] = []
        check_fixture_inventory(removed, found)
        if not any(f"section {section} must be a nonempty array" in item for item in found):
            errors.append(f"removed fixture-class control did not fail: {section}")
    section = "record_mutations"
    missing_case = copy.deepcopy(fixtures)
    if isinstance(missing_case.get(section), list) and missing_case[section]:
        missing_case[section] = missing_case[section][1:]
    found = []
    check_fixture_inventory(missing_case, found)
    if not any(f"section {section} is missing cases" in item for item in found):
        errors.append("removed fixture-name control did not fail")


def invalid_type_value(allowed_types: Any) -> Any:
    candidates: list[Any] = [None, True, 7, "bad", [], {}]
    allowed = set(allowed_types) if isinstance(allowed_types, list) else set()
    for candidate in candidates:
        if value_type(candidate) not in allowed:
            return candidate
    raise AssertionError(f"no invalid value for {allowed_types!r}")


def run_generated_schema_controls(
    base_records: dict[str, dict[str, Any]],
    contract: dict[str, Any],
    errors: list[str],
) -> None:
    schemas = contract.get("records", {})
    if not isinstance(schemas, dict):
        errors.append("state contract records must be an object")
        return
    for record_type, schema in schemas.items():
        base = base_records.get(record_type)
        if base is None or not isinstance(schema, dict):
            continue
        for field in schema.get("required", []):
            mutated = copy.deepcopy(base)
            mutated.pop(field, None)
            if not validate_record(mutated, contract, f"generated missing {record_type}.{field}"):
                errors.append(f"generated required-field control passed: {record_type}.{field}")
        types = schema.get("types", {})
        if isinstance(types, dict):
            for field, allowed_types in types.items():
                if field not in base:
                    continue
                mutated = copy.deepcopy(base)
                mutated[field] = invalid_type_value(allowed_types)
                if not validate_record(mutated, contract, f"generated type {record_type}.{field}"):
                    errors.append(f"generated type control passed: {record_type}.{field}")
        enums = schema.get("enums", {})
        if isinstance(enums, dict):
            for field in enums:
                mutated = copy.deepcopy(base)
                mutated[field] = "__INVALID_ENUM__"
                if not validate_record(mutated, contract, f"generated enum {record_type}.{field}"):
                    errors.append(f"generated enum control passed: {record_type}.{field}")
        states = schema.get("states")
        if isinstance(states, list):
            mutated = copy.deepcopy(base)
            mutated["state"] = "__INVALID_STATE__"
            if not validate_record(mutated, contract, f"generated state {record_type}"):
                errors.append(f"generated state control passed: {record_type}")
        state_rules = schema.get("state_rules", {})
        active_rules = state_rules.get(base.get("state"), {}) if isinstance(state_rules, dict) else {}
        for field in active_rules.get("non_null", []):
            mutated = copy.deepcopy(base)
            mutated[field] = None
            if not validate_record(mutated, contract, f"generated nonnull {record_type}.{field}"):
                errors.append(f"generated non-null control passed: {record_type}.{field}")
        for field in active_rules.get("null", []):
            mutated = copy.deepcopy(base)
            mutated[field] = "unexpected"
            if not validate_record(mutated, contract, f"generated null {record_type}.{field}"):
                errors.append(f"generated null control passed: {record_type}.{field}")
        mutated = copy.deepcopy(base)
        mutated["undeclared_probe"] = True
        if not validate_record(mutated, contract, f"generated undeclared {record_type}"):
            errors.append(f"generated undeclared-field control passed: {record_type}")


def sequence_is_valid(sequences: Any) -> bool:
    return bool(
        isinstance(sequences, list)
        and all(isinstance(value, int) and not isinstance(value, bool) for value in sequences)
        and all(value > 0 for value in sequences)
        and all(later > earlier for earlier, later in zip(sequences, sequences[1:]))
    )


def journal_record_key(
    record: dict[str, Any], contract: dict[str, Any]
) -> Optional[tuple[str, str, str, str]]:
    schema = contract.get("records", {}).get(record.get("record_type"), {})
    id_field = schema.get("id_field") if isinstance(schema, dict) else None
    if not isinstance(id_field, str) or not isinstance(record.get(id_field), str):
        return None
    return (
        record.get("run_id"),
        record.get("repository_id"),
        record.get("record_type"),
        record[id_field],
    )


def validate_journal(records: Any, contract: dict[str, Any]) -> list[str]:
    """Validate one append-ordered operational/task JSONL journal."""
    errors: list[str] = []
    if not isinstance(records, list) or not records:
        return ["journal must be a nonempty array of records"]
    sequences = [record.get("sequence") if isinstance(record, dict) else None for record in records]
    if not sequence_is_valid(sequences):
        errors.append("journal sequences must be globally positive, unique, and increasing")

    latest: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for index, record in enumerate(records):
        label = f"journal[{index}]"
        record_errors = validate_record(record, contract, label)
        errors.extend(record_errors)
        if not isinstance(record, dict) or record_errors:
            continue
        key = journal_record_key(record, contract)
        if key is None:
            errors.append(f"{label}: cannot derive journal record key")
            continue
        previous = latest.get(key)
        if previous is not None and record["sequence"] <= previous["sequence"]:
            errors.append(f"{label}: per-key sequence did not increase")

        identity_prefix = (record["run_id"], record["repository_id"])
        if record["record_type"] == "execution_lease":
            controller_key = (*identity_prefix, "controller_liveness", record["controller_id"])
            controller = latest.get(controller_key)
            if controller is None:
                errors.append(f"{label}: execution lease controller join is missing")
        elif record["record_type"] == "path_reservation" and previous is not None:
            for field in ("exact_paths", "owner", "created_at"):
                if record.get(field) != previous.get(field):
                    errors.append(f"{label}: reservation transition changed immutable {field}")
            if previous.get("state") in {"RELEASED", "TRANSFERRED"}:
                errors.append(f"{label}: reservation transition follows a terminal state")
        elif record["record_type"] == "task_transition":
            controller_id = record.get("controller_id")
            if controller_id is not None:
                controller = latest.get((*identity_prefix, "controller_liveness", controller_id))
                if controller is None:
                    errors.append(f"{label}: task controller join is missing")
            lease_id = record.get("lease_id")
            if lease_id is not None:
                lease = latest.get((*identity_prefix, "execution_lease", lease_id))
                if lease is None:
                    errors.append(f"{label}: task lease join is missing")
                elif controller_id is not None and lease.get("controller_id") != controller_id:
                    errors.append(f"{label}: task lease joins a different controller")
            for reservation_id in record.get("reservation_ids", []):
                reservation = latest.get(
                    (*identity_prefix, "path_reservation", reservation_id)
                )
                if reservation is None:
                    errors.append(f"{label}: task reservation join {reservation_id!r} is missing")

        if record.get("record_type") == "path_reservation":
            if record.get("state") == "TRANSFERRED":
                replacement_id = record.get("replacement_reservation_id")
                replacement = latest.get(
                    (*identity_prefix, "path_reservation", replacement_id)
                )
                if not valid_reservation_transfer(record, replacement):
                    errors.append(f"{label}: reservation transfer has no valid prior replacement")
            if (
                record.get("state") == "RELEASED"
                and record.get("release_reason_code") not in {
                    "MERGED_VERIFIED",
                    "ABANDONED",
                    "SUPERSEDED",
                }
            ):
                errors.append(f"{label}: released reservation has invalid reason code")
        latest[key] = record

    controller_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for key, record in latest.items():
        if key[2] == "controller_liveness":
            controller_groups.setdefault(key[:2], []).append(record)
    if not controller_groups:
        errors.append("journal must contain at least one controller_liveness record")
    for identity, controllers in controller_groups.items():
        running = [record for record in controllers if record.get("state") == "RUNNING"]
        if len(running) != 1:
            errors.append(
                f"journal {identity} must reduce to exactly one RUNNING controller; "
                f"found {len(running)}"
            )
    return errors


def valid_reservation_transfer(old: dict[str, Any], replacement: Optional[dict[str, Any]]) -> bool:
    replacement_id = old.get("replacement_reservation_id")
    old_sequence = old.get("sequence")
    replacement_sequence = replacement.get("sequence") if replacement else None
    old_time = parse_timestamp(old.get("time"))
    replacement_time = parse_timestamp(replacement.get("time")) if replacement else None
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
        and replacement.get("owner") != old.get("owner")
        and isinstance(old_sequence, int)
        and isinstance(replacement_sequence, int)
        and replacement_sequence < old_sequence
        and old_time
        and replacement_time
        and replacement_time <= old_time
    )


def parse_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not UTC_TIMESTAMP.fullmatch(value):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    return parsed if parsed.tzinfo == timezone.utc else None


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
        "time": case.get("observed_at"),
        "sequence": working_controller.get("sequence", 0) + 1,
        "state": "STOPPED",
        "stopped_at": case.get("observed_at"),
        "stop_reason": "inspected crashed-controller takeover",
        "inspection_evidence": evidence,
        "stopped_by": case.get("stopped_by"),
    }
    successor = {
        **working_controller,
        "time": "2026-08-09T01:00:01Z",
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
        "time": case.get("observed_at"),
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
        "time": "2026-08-09T01:00:00Z",
        "sequence": controller["sequence"] + 1,
        "state": fixture.get("controller_state"),
        "stopped_at": "2026-08-09T01:00:00Z",
        "stop_reason": "clean controller shutdown",
        "inspection_evidence": evidence,
        "stopped_by": "controller-1",
    }
    ended = {
        **lease,
        "time": "2026-08-09T01:00:00Z",
        "sequence": lease["sequence"] + 1,
        "state": fixture.get("lease_state"),
        "ended_at": "2026-08-09T01:00:00Z",
        "end_reason": "process inspection confirmed exit",
    }
    retained = {
        **reservation,
        "time": "2026-08-09T01:00:00Z",
        "state": fixture.get("reservation_state"),
    }
    if retained["state"] == "RELEASED":
        retained["released_at"] = "2026-08-09T01:00:00Z"
        retained["release_reason"] = fixture.get(
            "release_reason", "controller stopped while pull request remains open"
        )
        retained["release_reason_code"] = fixture.get(
            "release_reason_code", "MERGED_VERIFIED"
        )
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
            f"operational separation control {fixture.get('name')} produced {valid}"
        )


def check_state_contract(
    reference: str,
    contract: dict[str, Any],
    fixtures: dict[str, Any],
    errors: list[str],
) -> None:
    if contract.get("schema_version") != 1 or contract.get("record_schema_version") != 1:
        errors.append("state contract must declare schema versions 1")
    if contract.get("semantic_constraints") != EXPECTED_SEMANTIC_CONSTRAINTS:
        errors.append("state contract semantic_constraints are missing or stale")
    if fixtures.get("schema_version") != 1:
        errors.append("state fixtures must declare schema_version 1")
    check_fixture_inventory(fixtures, errors)
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

    run_generated_schema_controls(base_records, contract, errors)

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
        for fixture in fixtures.get("operational_separation_cases", []):
            check_operational_separation(
                fixture,
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
        for fixture in fixtures.get("transfer_cases", []):
            replacement_id = reservation["reservation_id"] + "-successor"
            if fixture.get("same_id"):
                replacement_id = reservation["reservation_id"]
            replacement = {
                **reservation,
                "time": "2026-08-08T23:03:00Z",
                "sequence": fixture.get("replacement_sequence"),
                "reservation_id": replacement_id,
                "run_id": fixture.get("replacement_run_id", reservation["run_id"]),
                "owner": {
                    "kind": "agent",
                    "id": "integrator-2",
                    "branch": "successor",
                },
                "state": fixture.get("replacement_state", "ACTIVE"),
            }
            if replacement["state"] != "ACTIVE":
                replacement["released_at"] = "2026-08-08T23:03:00Z"
                replacement["release_reason"] = "documented abandonment"
                replacement["release_reason_code"] = "ABANDONED"
            transferred = {
                **reservation,
                "time": "2026-08-08T23:04:00Z",
                "sequence": fixture.get("transfer_sequence"),
                "state": "TRANSFERRED",
                "replacement_reservation_id": replacement_id,
                "released_at": "2026-08-08T23:04:00Z",
                "release_reason": "named owner transfer",
                "release_reason_code": "TRANSFER",
            }
            record_errors = validate_record(
                replacement, contract, f"{fixture.get('name')}.replacement"
            ) + validate_record(transferred, contract, f"{fixture.get('name')}.transfer")
            actual = not record_errors and valid_reservation_transfer(
                transferred, replacement
            )
            if actual is not fixture.get("expected_valid"):
                errors.append(
                    f"transfer fixture {fixture.get('name')} produced {actual}: "
                    + " | ".join(record_errors)
                )

    if controller and lease and reservation and task:
        canonical_journal = [controller, lease, reservation, task]
        for fixture in fixtures.get("journal_cases", []):
            journal = copy.deepcopy(canonical_journal)
            mutation = fixture.get("mutation")
            if mutation == "missing_controller_join":
                journal[-1]["controller_id"] = "controller-missing"
            elif mutation == "duplicate_global_sequence":
                journal[-1]["sequence"] = journal[-2]["sequence"]
            elif mutation == "two_live_controllers":
                second = {
                    **controller,
                    "time": "2026-08-08T23:03:00Z",
                    "sequence": 20,
                    "controller_id": "controller-2",
                    "authorized_successor_ids": ["controller-2-successor"],
                }
                journal.insert(3, second)
            elif mutation == "released_open_pr_after_stop":
                # The fixture supplies external open-PR evidence. A controller stop
                # and ended lease cannot override that separately owned claim.
                released = {
                    **reservation,
                    "time": "2026-08-09T01:00:00Z",
                    "sequence": 22,
                    "state": "RELEASED",
                    "released_at": "2026-08-09T01:00:00Z",
                    "release_reason": "controller and lease stopped",
                    "release_reason_code": "ABANDONED",
                }
                journal = [controller, lease, reservation, released]
            journal_errors = validate_journal(journal, contract)
            actual = not journal_errors
            if mutation == "released_open_pr_after_stop" and fixture.get("pr_open"):
                actual = False
            if actual is not fixture.get("expected_valid"):
                errors.append(
                    f"journal fixture {fixture.get('name')} produced {actual}: "
                    + " | ".join(journal_errors)
                )


def check_routing_cases(
    cases: dict[str, Any],
    mappings: list[dict[str, str]],
    codex: str,
    errors: list[str],
    workflow_root: Optional[Path] = None,
) -> None:
    """Gate prompt classes against exact routes and retrieved workflow bytes."""
    if cases.get("schema_version") != 1:
        errors.append("routing cases must declare schema_version 1")
    if cases.get("umbrella_skill") != "overnight-workflows":
        errors.append("routing cases must target the overnight-workflows umbrella")

    expected_routes = {
        mapping["installed_path"]: mapping
        for mapping in mappings
        if "navigation_stub" in mapping
    }
    positives = cases.get("positive")
    if not isinstance(positives, list) or not positives:
        errors.append("routing positive cases must be a nonempty array")
        positives = []
    names: list[str] = []
    actual_routes: list[str] = []
    for index, case in enumerate(positives):
        label = f"routing positive[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{label} must be an object")
            continue
        name = case.get("name")
        prompt = case.get("prompt")
        route = case.get("installed_workflow")
        markers = case.get("required_markers")
        semantic_fields = (
            "authority_markers",
            "ordered_markers",
            "required_safety_markers",
            "forbidden_markers",
        )
        prompt_contract = case.get("prompt_contract")
        umbrella_clause = case.get("umbrella_clause")
        if not isinstance(name, str) or not name:
            errors.append(f"{label} has no name")
            expected_case = None
        else:
            names.append(name)
            expected_case = REQUIRED_ROUTING_CASES.get(name)
            if expected_case is None:
                errors.append(f"{label} has unknown case name {name!r}")
        if not isinstance(prompt, str) or not prompt.strip():
            errors.append(f"{label} has no prompt")
        elif not isinstance(prompt_contract, list) or not prompt_contract or any(
            not isinstance(fragment, str) or not fragment for fragment in prompt_contract
        ):
            errors.append(f"{label} prompt_contract must contain nonempty strings")
        else:
            for fragment in prompt_contract:
                if fragment.casefold() not in prompt.casefold():
                    errors.append(
                        f"{label} prompt does not satisfy declared contract {fragment!r}"
                    )
        if not isinstance(route, str):
            errors.append(f"{label} has no installed_workflow")
            continue
        actual_routes.append(route)
        if expected_case is not None:
            if route != expected_case["route"]:
                errors.append(f"{label} selects the wrong route for prompt class {name}")
            if umbrella_clause != expected_case["clause"]:
                errors.append(f"{label} has the wrong umbrella clause for prompt class {name}")
            if prompt_contract != expected_case["prompt_contract"]:
                errors.append(f"{label} has the wrong prompt contract for prompt class {name}")
            expected_semantics = REQUIRED_ROUTE_SEMANTICS.get(name, {})
            declared_semantics = {
                field: case[field] for field in semantic_fields if field in case
            }
            if declared_semantics != expected_semantics:
                errors.append(
                    f"{label} has the wrong exact authority/review semantics contract"
                )
        mapping = expected_routes.get(route)
        if mapping is None:
            errors.append(f"{label} names an unknown workflow route: {route}")
            continue
        if not isinstance(umbrella_clause, str) or not umbrella_clause:
            errors.append(f"{label} has no umbrella_clause")
        elif f"{umbrella_clause} → `{route}`" not in codex:
            errors.append(
                f"{label} exact clause-to-route contract is absent from umbrella SKILL.md"
            )
        if not isinstance(markers, list) or not markers or any(
            not isinstance(marker, str) or not marker for marker in markers
        ):
            errors.append(f"{label} required_markers must be nonempty strings")
            continue
        workflow_path = (
            workflow_root / route
            if workflow_root is not None
            else ROOT / mapping["canonical_source"]
        )
        try:
            if workflow_path.is_symlink() or not workflow_path.is_file():
                errors.append(f"{label} route is not a regular non-symlink file: {route}")
                continue
            source_text = workflow_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            errors.append(f"{label} route could not be retrieved: {route}: {exc}")
            continue
        for marker in markers:
            if marker not in source_text:
                errors.append(
                    f"{label} marker {marker!r} is absent from its canonical workflow"
                )
        for marker in case.get("authority_markers", []):
            if marker not in source_text:
                errors.append(
                    f"{label} authority marker {marker!r} is absent from its workflow"
                )
        for marker in case.get("required_safety_markers", []):
            if marker not in source_text:
                errors.append(
                    f"{label} safety marker {marker!r} is absent from its workflow"
                )
        for pair in case.get("ordered_markers", []):
            if (
                not isinstance(pair, list)
                or len(pair) != 2
                or any(not isinstance(marker, str) or not marker for marker in pair)
            ):
                errors.append(f"{label} ordered marker contract is malformed")
                continue
            before, after = pair
            before_index = source_text.find(before)
            after_index = source_text.find(after)
            if before_index < 0 or after_index < 0 or before_index >= after_index:
                errors.append(
                    f"{label} ordered workflow markers are absent or reversed: "
                    f"{before!r} -> {after!r}"
                )
        for marker in case.get("forbidden_markers", []):
            if marker in source_text:
                errors.append(
                    f"{label} forbidden unconditional-action marker is present: {marker!r}"
                )
        for instruction, pattern in UNCONDITIONAL_ACTION_PATTERNS.items():
            if pattern.search(source_text):
                errors.append(
                    f"{label} forbidden unconditional action is present: {instruction}"
                )
    if len(names) != len(set(names)):
        errors.append("routing positive case names must be unique")
    if set(names) != set(REQUIRED_ROUTING_CASES):
        errors.append("routing positives must have the exact required prompt-class inventory")
    if Counter(actual_routes) != Counter(expected_routes.keys()):
        errors.append("routing positives must cover every workflow route exactly once")

    negatives = cases.get("negative")
    if not isinstance(negatives, list) or not negatives:
        errors.append("routing negative cases must be a nonempty array")
        negatives = []
    negative_names: list[str] = []
    for index, case in enumerate(negatives):
        label = f"routing negative[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{label} must be an object")
            continue
        name = case.get("name")
        if isinstance(name, str) and name:
            negative_names.append(name)
        else:
            errors.append(f"{label} has no name")
        if not isinstance(case.get("prompt"), str) or not case["prompt"].strip():
            errors.append(f"{label} has no prompt")
        prompt_contract = case.get("prompt_contract")
        if not isinstance(prompt_contract, list) or not prompt_contract or any(
            not isinstance(fragment, str) or not fragment for fragment in prompt_contract
        ):
            errors.append(f"{label} prompt_contract must contain nonempty strings")
        elif isinstance(case.get("prompt"), str):
            for fragment in prompt_contract:
                if fragment.casefold() not in case["prompt"].casefold():
                    errors.append(
                        f"{label} prompt does not satisfy declared contract {fragment!r}"
                    )
        if case.get("execution_allowed") is not False:
            errors.append(f"{label} must explicitly deny execution")
        required_clause = REQUIRED_ROUTING_NEGATIVE_CLAUSES.get(name)
        if required_clause is not None and required_clause not in " ".join(codex.split()):
            errors.append(
                f"{label} denial contract is absent from umbrella SKILL.md"
            )
    if len(negative_names) != len(set(negative_names)):
        errors.append("routing negative case names must be unique")
    actual_negative_names = set(negative_names)
    if actual_negative_names != REQUIRED_ROUTING_NEGATIVES:
        errors.append(
            "routing negative cases must have the exact required inventory; "
            f"found {sorted(actual_negative_names)}"
        )


def run_routing_negative_controls(
    cases: dict[str, Any], mappings: list[dict[str, str]], codex: str, errors: list[str]
) -> None:
    unrelated = copy.deepcopy(cases)
    for case in unrelated.get("positive", []):
        case["prompt"] = "Completely unrelated request: tell me tomorrow's weather."
    found: list[str] = []
    check_routing_cases(unrelated, mappings, codex, found)
    if not any("prompt does not satisfy declared contract" in item for item in found):
        errors.append("unrelated-prompt routing negative control did not fail")

    wrong_route = copy.deepcopy(cases)
    positives = wrong_route.get("positive", [])
    if isinstance(positives, list) and len(positives) >= 2:
        positives[0]["installed_workflow"] = positives[1]["installed_workflow"]
    found = []
    check_routing_cases(wrong_route, mappings, codex, found)
    if not found:
        errors.append("wrong-route negative control did not fail")

    granted = copy.deepcopy(cases)
    negatives = granted.get("negative", [])
    if isinstance(negatives, list) and negatives:
        negatives[0]["execution_allowed"] = True
    found = []
    check_routing_cases(granted, mappings, codex, found)
    if not any("explicitly deny execution" in item for item in found):
        errors.append("execution-authority routing negative control did not fail")

    def mutated_route_errors(
        case_name: str, *, remove: Optional[str] = None, append: Optional[str] = None
    ) -> list[str]:
        with tempfile.TemporaryDirectory(prefix="routing-contract-") as raw:
            workflow_root = Path(raw).resolve()
            selected_route = REQUIRED_ROUTING_CASES[case_name]["route"]
            for mapping in mappings:
                if "navigation_stub" not in mapping:
                    continue
                destination = workflow_root / mapping["installed_path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                source = ROOT / mapping["canonical_source"]
                destination.write_bytes(source.read_bytes())
            selected = workflow_root / selected_route
            text = selected.read_text(encoding="utf-8")
            if remove is not None:
                if remove not in text:
                    return [f"negative-control setup marker absent: {remove!r}"]
                text = text.replace(remove, "REMOVED_BY_NEGATIVE_CONTROL")
            if append is not None:
                text += "\n" + append + "\n"
            selected.write_text(text, encoding="utf-8")
            observed: list[str] = []
            check_routing_cases(
                cases,
                mappings,
                codex,
                observed,
                workflow_root=workflow_root,
            )
            return observed

    found = mutated_route_errors(
        "client_delivery",
        remove=REQUIRED_ROUTE_SEMANTICS["client_delivery"]["authority_markers"][0],
    )
    if not any("authority marker" in item for item in found):
        errors.append("routed-child authority negative control did not fail")

    found = mutated_route_errors(
        "client_delivery",
        remove=REQUIRED_ROUTE_SEMANTICS["client_delivery"]["required_safety_markers"][1],
    )
    if not any("safety marker" in item for item in found):
        errors.append("final-byte review negative control did not fail")

    found = mutated_route_errors(
        "scheduled_poll",
        remove=REQUIRED_ROUTE_SEMANTICS["scheduled_poll"]["required_safety_markers"][1],
    )
    if not any("safety marker" in item for item in found):
        errors.append("schedule consolidation-latch negative control did not fail")

    found = mutated_route_errors(
        "scheduled_poll", append="run_consolidation_and_pr()"
    )
    if not any("forbidden unconditional-action marker" in item for item in found):
        errors.append("schedule unconditional-action negative control did not fail")

    for injected in ("git push origin HEAD", "create_pull_request()"):
        found = mutated_route_errors("client_delivery", append=injected)
        if not any("forbidden unconditional action" in item for item in found):
            errors.append(
                f"client unconditional-action negative control did not fail for {injected!r}"
            )


def _load_routed_helper(path: Path, label: str, expected_sha256: str) -> Any:
    module_name = "overnight_route_" + hashlib.sha256(str(path).encode()).hexdigest()[:16]
    descriptor, source, identity = _open_held_source(path, label)
    try:
        if (
            not SHA256.fullmatch(expected_sha256)
            or hashlib.sha256(source).hexdigest() != expected_sha256
        ):
            raise RuntimeError(f"{label} differs from authenticated package mapping")
        module = types.ModuleType(module_name)
        module.__file__ = str(path)
        module.__package__ = None
        module.__cached__ = None
        module.__loader__ = None
        module.__spec__ = None
        exec(compile(source, str(path), "exec"), module.__dict__, module.__dict__)
        _verify_held_source(
            path,
            descriptor,
            identity,
            "routed helper",
        )
        return module
    finally:
        os.close(descriptor)


def run_loaded_authority_controls(
    installed_root: Path,
    mappings: list[dict[str, str]],
    cases: dict[str, Any],
    errors: list[str],
) -> None:
    """Execute both routed authority helpers with an action-call recorder."""
    positives = {
        case.get("name"): case
        for case in cases.get("positive", [])
        if isinstance(case, dict) and isinstance(case.get("name"), str)
    }
    try:
        client_route = positives["client_delivery"]["installed_workflow"]
        schedule_route = positives["scheduled_poll"]["installed_workflow"]
        client_workflow = installed_root.joinpath(*PurePosixPath(client_route).parts)
        schedule_workflow = installed_root.joinpath(*PurePosixPath(schedule_route).parts)
        installed_digests = {
            mapping["installed_path"]: mapping["source_sha256"]
            for mapping in mappings
        }
        client_helper = client_workflow.parent / "scripts/action_authority.py"
        schedule_helper = schedule_workflow.parent / "scripts/poll_orchestrator.py"
        client = _load_routed_helper(
            client_helper,
            "client-delivery authority helper",
            installed_digests[
                client_helper.relative_to(installed_root).as_posix()
            ],
        )
        schedule = _load_routed_helper(
            schedule_helper,
            "schedule-poll authority helper",
            installed_digests[
                schedule_helper.relative_to(installed_root).as_posix()
            ],
        )
    except (KeyError, OSError, RuntimeError) as exc:
        errors.append(f"loaded authority route resolution failed: {exc}")
        return

    def write_receipt(
        path: Path,
        *,
        record_type: str,
        identity_field: str,
        identity: str,
        grant_fields: set[str],
        granted: set[str],
    ) -> None:
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "record_type": record_type,
                    identity_field: identity,
                    "authorized_by": "loader-authority-owner",
                    "recorded_at": "2026-08-09T00:00:00Z",
                    "grants": {
                        grant: grant in granted for grant in grant_fields
                    },
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    def assert_gate_accounting(
        label: str,
        calls: list[str],
        guarded_results: list[dict[str, Any]],
    ) -> None:
        expected: list[str] = []
        for result in guarded_results:
            if set(result) != {"action", "result", "called"}:
                raise RuntimeError(f"{label} guard returned an undeclared result shape")
            if not isinstance(result["action"], str) or not isinstance(
                result["called"], bool
            ):
                raise RuntimeError(f"{label} guard returned malformed call accounting")
            if result["called"]:
                if result["result"] != "AUTHORIZED":
                    raise RuntimeError(f"{label} guard called an unauthorized action")
                expected.append(result["action"])
            elif result["result"] != "MISSING_AUTHORITY":
                raise RuntimeError(f"{label} guard suppressed an authorized action")
        if calls != expected:
            raise RuntimeError(
                f"{label} detected an unconditional action outside the loaded gate: "
                f"expected {expected}, observed {calls}"
            )

    try:
        with tempfile.TemporaryDirectory(prefix="loaded-authority-controls-") as raw:
            root = Path(raw).resolve()

            # The client-delivery route enters its Phase C action points through
            # the loaded helper. A Boolean inspection is not counted as a call.
            client_calls: list[str] = []
            client_guarded: list[dict[str, Any]] = []
            for action in client.ACTION_REQUIREMENTS:
                decision = client.decide(
                    review_id="loaded-review",
                    action=action,
                    decision_output=root / f"client-denied-{action}.json",
                )
                if decision["result"] != "MISSING_AUTHORITY":
                    raise RuntimeError(f"client action {action} did not fail closed")
                client_guarded.append(
                    client.run_guarded_action(
                        decision=decision["decision"],
                        action=action,
                        callback=client_calls.append,
                    )
                )
            assert_gate_accounting("client Phase C denied path", client_calls, client_guarded)
            if client_calls:
                raise RuntimeError(
                    f"denied client Phase C called external actions: {client_calls}"
                )

            for action, requirements in client.ACTION_REQUIREMENTS.items():
                receipt = root / f"client-authority-{action}.json"
                write_receipt(
                    receipt,
                    record_type=client.AUTHORITY_TYPE,
                    identity_field="review_id",
                    identity="loaded-review",
                    grant_fields=set(client.GRANT_FIELDS),
                    granted=set(requirements),
                )
                decision = client.decide(
                    review_id="loaded-review",
                    action=action,
                    authority_receipt=receipt,
                    decision_output=root / f"client-allowed-{action}.json",
                )
                if not decision["decision"]["callable"]:
                    raise RuntimeError(f"client exact grants did not authorize {action}")
                before = list(client_calls)
                guarded = client.run_guarded_action(
                    decision=decision["decision"],
                    action=action,
                    callback=client_calls.append,
                )
                client_guarded.append(guarded)
                if client_calls != before + [action] or not guarded["called"]:
                    raise RuntimeError(
                        f"client Phase C gate did not call only exact action {action}"
                    )
                for removed in requirements:
                    partial = root / f"client-partial-{action}-{removed}.json"
                    write_receipt(
                        partial,
                        record_type=client.AUTHORITY_TYPE,
                        identity_field="review_id",
                        identity="loaded-review",
                        grant_fields=set(client.GRANT_FIELDS),
                        granted=set(requirements) - {removed},
                    )
                    denied = client.decide(
                        review_id="loaded-review",
                        action=action,
                        authority_receipt=partial,
                        decision_output=root
                        / f"client-partial-decision-{action}-{removed}.json",
                    )
                    if denied["decision"]["callable"]:
                        raise RuntimeError(
                            f"client {action} ignored missing grant {removed}"
                        )
                    before = list(client_calls)
                    guarded = client.run_guarded_action(
                        decision=denied["decision"],
                        action=action,
                        callback=client_calls.append,
                    )
                    client_guarded.append(guarded)
                    if client_calls != before or guarded["called"]:
                        raise RuntimeError(
                            f"client Phase C gate called {action} without {removed}"
                        )
            assert_gate_accounting("client Phase C", client_calls, client_guarded)
            if client_calls != list(client.ACTION_REQUIREMENTS):
                raise RuntimeError(
                    "client Phase C recorder did not call each authorized action exactly once"
                )

            # Falsifiable negative: a caller that invokes the callback without
            # the loaded gate creates a counter entry with no guarded result.
            negative_calls: list[str] = []
            negative_guarded: list[dict[str, Any]] = []
            denied = client.decide(
                review_id="loaded-negative",
                action="push",
                decision_output=root / "client-unconditional-negative.json",
            )
            negative_guarded.append(
                client.run_guarded_action(
                    decision=denied["decision"],
                    action="push",
                    callback=negative_calls.append,
                )
            )
            assert_gate_accounting(
                "client unconditional-caller baseline", negative_calls, negative_guarded
            )
            negative_calls.append("push")
            try:
                assert_gate_accounting(
                    "client unconditional-caller negative",
                    negative_calls,
                    negative_guarded,
                )
            except RuntimeError as exc:
                if "outside the loaded gate" not in str(exc):
                    raise
            else:
                raise RuntimeError("unconditional-caller negative did not fail")

            schedule_calls: list[str] = []
            schedule_guarded: list[dict[str, Any]] = []
            for action in schedule.ACTION_REQUIREMENTS:
                decision = schedule.decide_external_action(
                    run_id="loaded-run",
                    action=action,
                    decision_output=root / f"schedule-denied-{action}.json",
                )
                if decision["result"] != "MISSING_AUTHORITY":
                    raise RuntimeError(f"schedule action {action} did not fail closed")
                schedule_guarded.append(
                    schedule.run_guarded_action(
                        decision=decision["decision"],
                        action=action,
                        callback=schedule_calls.append,
                    )
                )
            assert_gate_accounting(
                "schedule denied path", schedule_calls, schedule_guarded
            )
            if schedule_calls:
                raise RuntimeError(
                    f"denied schedule route called external actions: {schedule_calls}"
                )

            for action, requirements in schedule.ACTION_REQUIREMENTS.items():
                receipt = root / f"schedule-authority-{action}.json"
                write_receipt(
                    receipt,
                    record_type=schedule.AUTHORITY_TYPE,
                    identity_field="run_id",
                    identity="loaded-run",
                    grant_fields=set(schedule.GRANT_FIELDS),
                    granted=set(requirements),
                )
                allowed = schedule.decide_external_action(
                    run_id="loaded-run",
                    action=action,
                    authority_receipt_path=receipt,
                    decision_output=root / f"schedule-allowed-{action}.json",
                )
                if not allowed["decision"]["callable"]:
                    raise RuntimeError(f"schedule exact grants did not authorize {action}")
                before = list(schedule_calls)
                guarded = schedule.run_guarded_action(
                    decision=allowed["decision"],
                    action=action,
                    callback=schedule_calls.append,
                )
                schedule_guarded.append(guarded)
                if schedule_calls != before + [action] or not guarded["called"]:
                    raise RuntimeError(
                        f"schedule gate did not call only exact action {action}"
                    )
                for removed in requirements:
                    partial = root / f"schedule-partial-{action}-{removed}.json"
                    write_receipt(
                        partial,
                        record_type=schedule.AUTHORITY_TYPE,
                        identity_field="run_id",
                        identity="loaded-run",
                        grant_fields=set(schedule.GRANT_FIELDS),
                        granted=set(requirements) - {removed},
                    )
                    denied = schedule.decide_external_action(
                        run_id="loaded-run",
                        action=action,
                        authority_receipt_path=partial,
                        decision_output=root
                        / f"schedule-partial-decision-{action}-{removed}.json",
                    )
                    if denied["decision"]["callable"]:
                        raise RuntimeError(
                            f"schedule {action} ignored missing grant {removed}"
                        )
                    before = list(schedule_calls)
                    guarded = schedule.run_guarded_action(
                        decision=denied["decision"],
                        action=action,
                        callback=schedule_calls.append,
                    )
                    schedule_guarded.append(guarded)
                    if schedule_calls != before or guarded["called"]:
                        raise RuntimeError(
                            f"schedule gate called {action} without {removed}"
                        )
            assert_gate_accounting("schedule external actions", schedule_calls, schedule_guarded)
            if schedule_calls != list(schedule.ACTION_REQUIREMENTS):
                raise RuntimeError(
                    "schedule recorder did not call each authorized action exactly once"
                )

            status = root / "all-terminal-status.json"
            journal = root / "all-terminal-journal.jsonl"
            schedule.initialize(
                status_path=status,
                journal_path=journal,
                run_id="terminal-run",
                dispatch_epoch=100,
                hard_ceiling_seconds=100,
                poll_interval_seconds=30,
                tracks=["track-a", "track-b"],
                now=100,
            )
            for index, track in enumerate(("track-a", "track-b"), 1):
                schedule.mark_track(
                    status_path=status,
                    journal_path=journal,
                    run_id="terminal-run",
                    track=track,
                    phase="complete",
                    reason="loaded route control complete",
                    evidence_path=None,
                    evidence_sha256=None,
                    now=100 + index,
                )
            terminal = schedule.poll(
                status_path=status,
                journal_path=journal,
                run_id="terminal-run",
                trigger_id="terminal-trigger",
                now=110,
            )
            if terminal["action"] != "CONSOLIDATION_CLAIMED":
                raise RuntimeError("all-terminal schedule route did not claim local work")
            consolidated = root / "consolidated.txt"
            consolidated.write_text("local consolidation\n", encoding="utf-8")
            schedule.complete_consolidation(
                status_path=status,
                journal_path=journal,
                run_id="terminal-run",
                operation_id=terminal["operation_id"],
                evidence_path=consolidated,
                evidence_sha256=hashlib.sha256(consolidated.read_bytes()).hexdigest(),
                now=111,
            )
            denied_pr = schedule.claim_pull_request(
                status_path=status,
                journal_path=journal,
                run_id="terminal-run",
                decision_output=root / "terminal-pr-decision.json",
                now=112,
            )
            if denied_pr["action"] != "MISSING_AUTHORITY" or denied_pr["decision"][
                "callable"
            ]:
                raise RuntimeError("all-terminal route made a denied PR callable")
            terminal_pr_calls: list[str] = []
            terminal_guarded = [
                schedule.run_guarded_action(
                    decision=denied_pr["decision"],
                    action="pull-request",
                    callback=terminal_pr_calls.append,
                )
            ]
            assert_gate_accounting(
                "all-terminal denied PR", terminal_pr_calls, terminal_guarded
            )
            if terminal_pr_calls:
                raise RuntimeError("all-terminal route invoked a denied PR callback")

            ceiling_status = root / "ceiling-status.json"
            ceiling_journal = root / "ceiling-journal.jsonl"
            schedule.initialize(
                status_path=ceiling_status,
                journal_path=ceiling_journal,
                run_id="ceiling-run",
                dispatch_epoch=100,
                hard_ceiling_seconds=100,
                poll_interval_seconds=30,
                tracks=["track-a", "track-b"],
                now=100,
            )
            ceiling = schedule.poll(
                status_path=ceiling_status,
                journal_path=ceiling_journal,
                run_id="ceiling-run",
                trigger_id="ceiling-trigger",
                now=200,
            )
            if ceiling["action"] != "CONSOLIDATION_CLAIMED" or set(
                ceiling["phases"].values()
            ) != {"tapped_out"}:
                raise RuntimeError("hard-ceiling route did not latch local consolidation")
            ceiling_consolidated = root / "ceiling-consolidated.txt"
            ceiling_consolidated.write_text(
                "hard-ceiling local consolidation\n", encoding="utf-8"
            )
            ceiling_completion = schedule.complete_consolidation(
                status_path=ceiling_status,
                journal_path=ceiling_journal,
                run_id="ceiling-run",
                operation_id=ceiling["operation_id"],
                evidence_path=ceiling_consolidated,
                evidence_sha256=hashlib.sha256(
                    ceiling_consolidated.read_bytes()
                ).hexdigest(),
                now=201,
            )
            if ceiling_completion["action"] != "LOCAL_COMPLETE_EXTERNAL_ACTIONS_NOT_AUTHORIZED":
                raise RuntimeError("hard-ceiling route did not complete local consolidation")
            ceiling_denied_pr = schedule.claim_pull_request(
                status_path=ceiling_status,
                journal_path=ceiling_journal,
                run_id="ceiling-run",
                decision_output=root / "ceiling-pr-decision.json",
                now=202,
            )
            if (
                ceiling_denied_pr["action"] != "MISSING_AUTHORITY"
                or ceiling_denied_pr["decision"]["callable"]
            ):
                raise RuntimeError("hard-ceiling route made a denied PR callable")
            ceiling_pr_calls: list[str] = []
            ceiling_guarded = [
                schedule.run_guarded_action(
                    decision=ceiling_denied_pr["decision"],
                    action="pull-request",
                    callback=ceiling_pr_calls.append,
                )
            ]
            assert_gate_accounting(
                "hard-ceiling denied PR", ceiling_pr_calls, ceiling_guarded
            )
            if ceiling_pr_calls:
                raise RuntimeError("hard-ceiling route invoked a denied PR callback")
            ceiling_state = schedule.inspect(
                status_path=ceiling_status, run_id="ceiling-run"
            )["state"]
            if (
                ceiling_state["consolidation"]["state"] != "COMPLETE"
                or ceiling_state["pull_request"]["state"] != "NOT_CLAIMED"
            ):
                raise RuntimeError(
                    "hard-ceiling route did not preserve completed local work and denied PR"
                )
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, AttributeError) as exc:
        errors.append(f"loaded authority behavioral controls failed: {exc}")


def run_materialized_authority_controls(
    mappings: list[dict[str, str]], cases: dict[str, Any], errors: list[str]
) -> None:
    with tempfile.TemporaryDirectory(prefix="authority-route-install-") as raw:
        installed_root = Path(raw).resolve() / "overnight-workflows"
        materialize_install(installed_root, mappings)
        run_loaded_authority_controls(installed_root, mappings, cases, errors)


def _loader_skills_for_root(codex_bin: Path, codex_home: Path, root: Path) -> list[dict[str, Any]]:
    """Ask Codex app-server for the real force-reloaded inventory."""
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(codex_home)
    process = subprocess.Popen(
        [str(codex_bin), "app-server", "--listen", "stdio://"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    assert process.stdin is not None and process.stdout is not None

    def send(payload: dict[str, Any]) -> None:
        process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
        process.stdin.flush()

    deadline = time.monotonic() + 15

    def response(response_id: int) -> dict[str, Any]:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([process.stdout], [], [], 0.5)
            if not ready:
                continue
            line = process.stdout.readline()
            if not line:
                break
            parsed = json.loads(line)
            if parsed.get("id") == response_id:
                return parsed
        raise RuntimeError(f"Codex app-server timed out waiting for response {response_id}")

    try:
        send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {
                        "name": "overnight-workflows-loader-probe",
                        "version": "1.0.0",
                    },
                    "capabilities": {},
                },
            }
        )
        initialized = response(1)
        if "error" in initialized:
            raise RuntimeError(f"Codex initialize failed: {initialized['error']}")
        send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
        send(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "skills/list",
                "params": {"cwds": [str(ROOT)], "forceReload": True},
            }
        )
        listed = response(2)
        if "error" in listed:
            raise RuntimeError(f"Codex skills/list failed: {listed['error']}")
        data = listed.get("result", {}).get("data")
        if not isinstance(data, list) or len(data) != 1:
            raise RuntimeError("Codex skills/list returned no unique cwd result")
        skills = data[0].get("skills")
        if not isinstance(skills, list):
            raise RuntimeError("Codex skills/list result has no skills array")
        root_resolved = root.resolve()
        selected: list[dict[str, Any]] = []
        for skill in skills:
            path = skill.get("path") if isinstance(skill, dict) else None
            if not isinstance(path, str):
                continue
            try:
                Path(path).resolve().relative_to(root_resolved)
            except ValueError:
                continue
            selected.append(skill)
        return selected
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def run_real_loader_controls(
    codex_bin: Path,
    mappings: list[dict[str, str]],
    routing_cases: dict[str, Any],
    errors: list[str],
) -> None:
    try:
        version = subprocess.run(
            [str(codex_bin), "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        errors.append(f"real loader probe could not run Codex: {exc}")
        return
    if version != f"codex-cli {EXPECTED_CODEX_VERSION}":
        errors.append(
            f"real loader probe requires codex-cli {EXPECTED_CODEX_VERSION}; found {version!r}"
        )
        return

    with tempfile.TemporaryDirectory(prefix="overnight-loader-probe-") as temp:
        codex_home = Path(temp)
        installed_root = codex_home / "skills/overnight-workflows"
        materialize_install(installed_root, mappings)
        try:
            selected = _loader_skills_for_root(codex_bin, codex_home, installed_root)
        except (OSError, RuntimeError, json.JSONDecodeError) as exc:
            errors.append(f"real loader inventory probe failed: {exc}")
            return
        expected_path = (installed_root / "SKILL.md").resolve()
        selected_paths = sorted(
            str(Path(skill["path"]).resolve()) for skill in selected if "path" in skill
        )
        if selected_paths != [str(expected_path)]:
            errors.append(
                "real loader must expose only the umbrella root SKILL.md; "
                f"found {selected_paths}"
            )
        elif selected[0].get("name") != "overnight-workflows":
            errors.append("real loader did not retrieve the overnight-workflows umbrella")
        else:
            try:
                source_text = expected_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                errors.append(f"loaded umbrella could not be retrieved: {exc}")
                source_text = ""
            description_match = re.search(
                r"^description:\s*(.+?)\s*$", source_text, flags=re.MULTILINE
            )
            expected_description = (
                description_match.group(1) if description_match else None
            )
            if selected[0].get("description") != expected_description:
                errors.append("real loader umbrella description differs from canonical")
            interface = selected[0].get("interface")
            if not isinstance(interface, dict) or {
                "displayName": interface.get("displayName"),
                "shortDescription": interface.get("shortDescription"),
                "defaultPrompt": interface.get("defaultPrompt"),
            } != {
                "displayName": "Overnight Workflows",
                "shortDescription": "Route durable unattended workflows safely",
                "defaultPrompt": (
                    "Use $overnight-workflows to route this unattended request, "
                    "record separate authority grants, and design its recovery gates."
                ),
            }:
                errors.append("real loader umbrella interface differs from routing contract")
            loaded_route_errors: list[str] = []
            check_routing_cases(
                routing_cases,
                mappings,
                source_text,
                loaded_route_errors,
                workflow_root=installed_root,
            )
            errors.extend(
                f"real loader retrieval contract: {error}"
                for error in loaded_route_errors
            )
            if not loaded_route_errors:
                loaded_authority_errors: list[str] = []
                run_loaded_authority_controls(
                    installed_root,
                    mappings,
                    routing_cases,
                    loaded_authority_errors,
                )
                errors.extend(
                    f"real loader authority contract: {error}"
                    for error in loaded_authority_errors
                )

    with tempfile.TemporaryDirectory(prefix="overnight-loader-negative-") as temp:
        codex_home = Path(temp)
        installed_root = codex_home / "skills/overnight-workflows"
        materialize_install(installed_root, mappings)
        nested = installed_root / (
            "references/workflows/overnight-insight-discovery/SKILL.md"
        )
        shutil.copy2(ROOT / "plugins/overnight-insight-discovery/SKILL.md", nested)
        try:
            selected = _loader_skills_for_root(codex_bin, codex_home, installed_root)
        except (OSError, RuntimeError, json.JSONDecodeError) as exc:
            errors.append(f"real loader recursive-child negative control failed: {exc}")
            return
        if len(selected) <= 1:
            errors.append(
                "real loader recursive-child negative control did not expose the nested SKILL.md"
            )


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
        expected_resource_sha256 = {
            PurePosixPath(mapping["installed_path"]).name: mapping["source_sha256"]
            for mapping in mappings
            if mapping["installed_path"].startswith(
                PANEL_VALIDATOR_INSTALLED_DIRECTORY + "/"
            )
            and PurePosixPath(mapping["installed_path"]).name
            in set(PANEL_VALIDATOR_RESOURCES)
        }
        baseline: list[str] = []
        check_installed_inventory(installed_root, mappings, baseline)
        if baseline:
            errors.append("self-test baseline install failed: " + " | ".join(baseline))
            return

        validator_scripts = installed_root.joinpath(
            *PurePosixPath(PANEL_VALIDATOR_INSTALLED_DIRECTORY).parts
        )
        if (validator_scripts / "publish_codex_install.py").exists():
            errors.append("self-test baseline installed the mutating publisher")
            return
        for resource_name in PANEL_VALIDATOR_RESOURCES:
            resource = validator_scripts / resource_name
            canonical = ROOT / "scripts" / resource_name
            canonical_before = canonical.read_bytes()
            original = resource.read_bytes()
            original_mode = stat.S_IMODE(os.lstat(resource).st_mode)

            resource.unlink()
            missing_errors: list[str] = []
            check_installed_inventory(installed_root, mappings, missing_errors)
            if not any(
                "installed umbrella is missing" in item and resource_name in item
                for item in missing_errors
            ):
                errors.append(
                    f"missing installed panel resource control did not fail: {resource_name}"
                )
            runtime_missing_errors: list[str] = []
            check_installed_panel_validator(
                installed_root,
                expected_resource_sha256,
                runtime_missing_errors,
            )
            if not any(
                "installed panel validator is missing required resources" in item
                and resource_name in item
                for item in runtime_missing_errors
            ):
                errors.append(
                    "missing panel validator dependency did not produce its own "
                    f"runtime error: {resource_name}"
                )
            shutil.copy2(canonical, resource)

            drifted = bytes([original[0] ^ 1]) + original[1:]
            os.chmod(resource, original_mode | stat.S_IWUSR, follow_symlinks=False)
            resource.write_bytes(drifted)
            os.chmod(resource, original_mode, follow_symlinks=False)
            drift_errors: list[str] = []
            check_installed_inventory(installed_root, mappings, drift_errors)
            if not any(
                "differs from canonical source" in item and resource_name in item
                for item in drift_errors
            ):
                errors.append(
                    f"one-byte installed panel resource drift did not fail: {resource_name}"
                )
            runtime_drift_errors: list[str] = []
            check_installed_panel_validator(
                installed_root,
                expected_resource_sha256,
                runtime_drift_errors,
            )
            runtime_drift_marker = {
                "validate_panel_inputs.py": (
                    "installed panel validator differs from authenticated package mapping"
                ),
                "install_inventory.py": (
                    "installed panel validator inventory dependency differs from "
                    "authenticated package mapping"
                ),
                "panel_input_fixtures.json": (
                    "installed panel validator fixture dependency differs from "
                    "authenticated package mapping"
                ),
            }[resource_name]
            if not any(
                runtime_drift_marker in item for item in runtime_drift_errors
            ):
                errors.append(
                    "one-byte panel validator dependency drift did not produce its "
                    f"own digest error: {resource_name}"
                )
            os.chmod(resource, original_mode | stat.S_IWUSR, follow_symlinks=False)
            resource.write_bytes(original)
            os.chmod(resource, original_mode, follow_symlinks=False)
            if canonical.read_bytes() != canonical_before:
                errors.append(
                    f"installed panel resource control mutated canonical source: {resource_name}"
                )

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
        canonical_probe = ROOT / (
            "plugins/overnight-insight-discovery/"
            "assets/morning_summary_template.md"
        )
        probe_stat = os.lstat(link_probe)
        canonical_stat = os.lstat(canonical_probe)
        if (
            not stat.S_ISREG(probe_stat.st_mode)
            or not stat.S_ISREG(canonical_stat.st_mode)
            or probe_stat.st_nlink != 1
            or (probe_stat.st_dev, probe_stat.st_ino)
            == (canonical_stat.st_dev, canonical_stat.st_ino)
        ):
            errors.append("Markdown-link negative-control probe is not an isolated file")
            return
        canonical_before = canonical_probe.read_bytes()
        original_mode = stat.S_IMODE(probe_stat.st_mode)
        guard_descriptor = os.open(
            link_probe, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        )
        probe_mode_changed = False
        try:
            opened = os.fstat(guard_descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or (probe_stat.st_dev, probe_stat.st_ino)
                != (opened.st_dev, opened.st_ino)
            ):
                errors.append(
                    "Markdown-link negative-control probe changed before mutation"
                )
                return
            original_chunks: list[bytes] = []
            while True:
                chunk = os.read(guard_descriptor, 1024 * 1024)
                if not chunk:
                    break
                original_chunks.append(chunk)
            original_probe = b"".join(original_chunks)
            os.fchmod(guard_descriptor, original_mode | stat.S_IWUSR)
            probe_mode_changed = True
            write_descriptor = os.open(
                link_probe, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                writable = os.fstat(write_descriptor)
                if (
                    not stat.S_ISREG(writable.st_mode)
                    or writable.st_nlink != 1
                    or (opened.st_dev, opened.st_ino)
                    != (writable.st_dev, writable.st_ino)
                ):
                    errors.append(
                        "Markdown-link negative-control probe changed before write"
                    )
                    return

                def replace_probe(data: bytes) -> None:
                    os.lseek(write_descriptor, 0, os.SEEK_SET)
                    os.ftruncate(write_descriptor, 0)
                    view = memoryview(data)
                    while view:
                        written = os.write(write_descriptor, view)
                        view = view[written:]

                try:
                    replace_probe(
                        original_probe
                        + b"\n[missing reference][probe]\n"
                        + b"[probe]: missing-reference.md\n"
                        + b"<missing-autolink.md>\n"
                    )
                    link_errors: list[str] = []
                    check_installed_inventory(installed_root, mappings, link_errors)
                    for missing_name in (
                        "missing-reference.md",
                        "missing-autolink.md",
                    ):
                        if not any(
                            "installed local link is missing" in item
                            and missing_name in item
                            for item in link_errors
                        ):
                            errors.append(
                                f"{missing_name} Markdown-link negative control did not fail"
                            )
                finally:
                    replace_probe(original_probe)
            finally:
                os.close(write_descriptor)
        finally:
            try:
                if probe_mode_changed:
                    os.fchmod(guard_descriptor, original_mode)
            finally:
                os.close(guard_descriptor)
        canonical_after = os.lstat(canonical_probe)
        if (
            canonical_probe.read_bytes() != canonical_before
            or stat.S_IMODE(canonical_after.st_mode)
            != stat.S_IMODE(canonical_stat.st_mode)
        ):
            errors.append("Markdown-link negative control mutated its canonical source")

        child_entrypoint = installed_root / (
            "references/workflows/overnight-insight-discovery/SKILL.md"
        )
        shutil.copy2(ROOT / "plugins/overnight-insight-discovery/SKILL.md", child_entrypoint)
        child_errors: list[str] = []
        check_installed_inventory(installed_root, mappings, child_errors)
        if not any("exactly one reserved SKILL.md" in item for item in child_errors):
            errors.append("nested SKILL.md exposure negative control did not fail")
        child_entrypoint.unlink()

        victim = installed_root / (
            "references/workflows/overnight-insight-discovery/WORKFLOW.md"
        )
        victim.unlink()
        os.symlink((ROOT / "plugins/overnight-insight-discovery/SKILL.md"), victim)
        symlink_errors: list[str] = []
        check_installed_inventory(installed_root, mappings, symlink_errors)
        if not any("contains a symlink" in item for item in symlink_errors):
            errors.append("symlink negative control did not fail")


def named_self_test_outcomes(
    fixtures: dict[str, Any],
    contract: dict[str, Any],
    routing_cases: dict[str, Any],
    reference: str,
    loader_executed: bool,
) -> dict[str, str]:
    """Name every mutation class exercised before a successful receipt is emitted."""
    names: set[str] = set()
    for section in REQUIRED_FIXTURE_NAMES:
        for case in fixtures.get(section, []):
            if isinstance(case, dict) and isinstance(case.get("name"), str):
                names.add(f"state.fixture.{section}.{case['name']}")
        names.add(f"state.fixture_inventory.removed_class.{section}")
    names.add("state.fixture_inventory.removed_record_mutation")

    outcome_parse_errors: list[str] = []
    examples = json_examples(reference, outcome_parse_errors)
    base_states = {
        "controller_liveness": "RUNNING",
        "execution_lease": "ACTIVE",
        "path_reservation": "ACTIVE",
        "task_transition": None,
    }
    bases = {
        record_type: find_example(examples, record_type, state)
        for record_type, state in base_states.items()
    }
    schemas = contract.get("records", {})
    if isinstance(schemas, dict):
        for record_type, schema in schemas.items():
            if not isinstance(schema, dict):
                continue
            for field in schema.get("required", []):
                names.add(f"state.generated.required.{record_type}.{field}")
            types = schema.get("types", {})
            if isinstance(types, dict):
                for field in types:
                    base = bases.get(record_type)
                    if isinstance(base, dict) and field in base:
                        names.add(f"state.generated.type.{record_type}.{field}")
            enums = schema.get("enums", {})
            if isinstance(enums, dict):
                for field in enums:
                    names.add(f"state.generated.enum.{record_type}.{field}")
            if isinstance(schema.get("states"), list):
                names.add(f"state.generated.state.{record_type}")
            state_rules = schema.get("state_rules", {})
            if isinstance(state_rules, dict):
                base_state = base_states.get(record_type)
                rules = state_rules.get(base_state, {})
                if isinstance(rules, dict):
                    for field in rules.get("non_null", []):
                        names.add(f"state.generated.non_null.{record_type}.{field}")
                    for field in rules.get("null", []):
                        names.add(f"state.generated.null.{record_type}.{field}")
            names.add(f"state.generated.undeclared.{record_type}")

    names.update(
        {
            "install.missing_resource",
            "install.missing_transitive_dependency",
            "install.missing_markdown_reference",
            "install.missing_markdown_autolink",
            "install.nested_skill_exposure",
            "install.panel_validator.self_test",
            "install.panel_validator.timeout_budget_120_seconds",
            "install.panel_validator.mutating_publisher_absent",
            "install.symlink",
            "routing.unrelated_prompt",
            "routing.wrong_route",
            "routing.execution_authority",
            "routing.child_authority",
            "routing.final_byte_review",
            "guidance.panel_schema3_regression",
            "guidance.pre_dispatch_validation_regression",
            "guidance.source_review_boundary_regression",
            "routing.schedule_consolidation_latch",
            "routing.schedule_unconditional_action",
            "routing.authority.loaded_client_deny_and_exact_grants",
            "routing.authority.loaded_unconditional_caller_negative",
            "routing.authority.loaded_schedule_deny_and_exact_grants",
            "routing.authority.loaded_schedule_all_terminal",
            "routing.authority.loaded_schedule_hard_ceiling",
        }
    )
    for resource_name in PANEL_VALIDATOR_RESOURCES:
        names.add(f"install.panel_validator.missing.{resource_name}")
        names.add(f"install.panel_validator.one_byte_drift.{resource_name}")
    for case in routing_cases.get("positive", []):
        if isinstance(case, dict) and isinstance(case.get("name"), str):
            names.add(f"routing.positive.{case['name']}")
            if loader_executed:
                names.add(f"loader.retrieval.positive.{case['name']}")
    for case in routing_cases.get("negative", []):
        if isinstance(case, dict) and isinstance(case.get("name"), str):
            names.add(f"routing.negative.{case['name']}")
            if loader_executed:
                names.add(f"loader.retrieval.negative.{case['name']}")
    if loader_executed:
        names.update(
            {
                "loader.umbrella_only",
                "loader.recursive_child_exposure",
                "loader.authority.client_action_recorder",
                "loader.authority.schedule_terminal_routes",
            }
        )
    return {name: "PASS" for name in sorted(names)}


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
        help="run malformed-install negative controls in a temporary install",
    )
    parser.add_argument(
        "--real-loader",
        type=Path,
        nargs="?",
        const=Path(shutil.which("codex") or "codex"),
        help=(
            "exercise Codex 0.147.0 app-server skills/list(forceReload=true); "
            "optionally provide the codex executable path"
        ),
    )
    parser.add_argument(
        "--release-gate",
        type=Path,
        nargs="?",
        const=Path(shutil.which("codex") or "codex"),
        help=(
            "required local release gate: run the pinned real loader and loaded "
            "umbrella route-retrieval controls; unavailable is a failure"
        ),
    )
    parser.add_argument(
        "--ci-loader-gate",
        action="store_true",
        help=(
            "run the pinned loader when present, otherwise emit an explicit "
            "CI environment classification (never a local release substitute)"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit one machine-readable result object; diagnostics go to stderr",
    )
    args = parser.parse_args()
    if sum(bool(value) for value in (args.real_loader, args.release_gate, args.ci_loader_gate)) > 1:
        parser.error("choose only one of --real-loader, --release-gate, or --ci-loader-gate")

    errors: list[str] = []
    check_validation_time_budget_contract(errors)
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
        "`target_ref_sha_at_dispatch`",
        "`diff_argv`",
        "`sha256-size-path-v1`",
        "ACTIVE_OVERNIGHT_SKILL",
        "BUNDLED_PANEL_VALIDATOR",
        "--self-test --manifest",
        "before dispatch and again before acceptance",
    ):
        require(reference, phrase, REFERENCE, errors)
    for where, text in ((SKILL, skill), (REFERENCE, reference)):
        if "expiry or takeover condition" in text:
            errors.append(
                f"{where.relative_to(ROOT)}: makes mandatory reservation fields alternatives"
            )
    if "set(source_ids)" in reference:
        errors.append(f"{REFERENCE.relative_to(ROOT)}: filename/set coverage returned")
    check_panel_schema3_guidance(reference, readme, errors)

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
        "references/workflows/",
        "WORKFLOW.md",
    ):
        require(codex, phrase, CODEX, errors)

    mappings = expected_install_mappings()
    authenticated_control_sources = bind_authenticated_validation_sources(
        mappings, errors
    )
    nested_execution_is_authenticated = authenticated_control_sources is not None
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
    require(readme, f"The {stub_count} tracked navigation stubs", README, errors)
    require(readme, "every local Markdown dependency resolves", README, errors)
    require(readme, "explicit merge-on-green grant", README, errors)
    require(readme, "pull-request authority is absent", README, errors)

    manifest = read_json(INSTALL_MANIFEST, errors)
    if manifest.get("schema_version") != 3:
        errors.append("install manifest must use schema_version 3")
    if manifest.get("installed_skill") != "overnight-workflows":
        errors.append("install manifest has wrong installed_skill")
    if manifest.get("layout") != "per-workflow-package":
        errors.append("install manifest has wrong layout")
    identity = manifest.get("input_identity")
    expected_identity = install_input_identity(mappings)
    if identity != {
        "format": INSTALL_IDENTITY_FRAMING,
        "digest": expected_identity,
    }:
        errors.append("install manifest has stale or malformed framed input identity")
    if manifest.get("evidence_inventory_format") != INSTALL_INVENTORY_FORMAT:
        errors.append("install manifest has wrong evidence inventory format")
    if manifest.get("loader_contract") != {
        "codex_cli_version": EXPECTED_CODEX_VERSION,
        "reserved_entrypoints": ["SKILL.md"],
    }:
        errors.append("install manifest has wrong loader contract")
    if manifest.get("mappings") != mappings:
        errors.append("install manifest is incomplete, stale, or not mechanically ordered")

    installed_paths = [mapping["installed_path"] for mapping in mappings]
    canonical_sources = [mapping["canonical_source"] for mapping in mappings]
    expected_panel_resources = {
        f"{PANEL_VALIDATOR_INSTALLED_DIRECTORY}/{resource_name}"
        for resource_name in PANEL_VALIDATOR_RESOURCES
    }
    actual_panel_resources = {
        mapping["installed_path"]
        for mapping in mappings
        if mapping["installed_path"].startswith(
            PANEL_VALIDATOR_INSTALLED_DIRECTORY + "/"
        )
    }
    if actual_panel_resources != expected_panel_resources:
        errors.append(
            "installed panel validator must contain exactly its read-only three-file closure"
        )
    if any(
        PurePosixPath(path).name == "publish_codex_install.py"
        for path in installed_paths
    ):
        errors.append("install manifest must exclude the mutating publisher")
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
        if source.is_file() and mapping.get("source_sha256") != hashlib.sha256(source.read_bytes()).hexdigest():
            errors.append(f"stale source digest: {mapping['canonical_source']}")
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
    bound_controls = authenticated_control_sources or {}
    contract = read_json(
        STATE_CONTRACT,
        errors,
        authenticated_bytes=bound_controls.get(
            STATE_CONTRACT.relative_to(ROOT).as_posix()
        ),
    )
    fixtures = read_json(
        STATE_FIXTURES,
        errors,
        authenticated_bytes=bound_controls.get(
            STATE_FIXTURES.relative_to(ROOT).as_posix()
        ),
    )
    routing_cases = read_json(
        ROUTING_CASES,
        errors,
        authenticated_bytes=bound_controls.get(
            ROUTING_CASES.relative_to(ROOT).as_posix()
        ),
    )
    check_state_contract(reference, contract, fixtures, errors)
    check_routing_cases(routing_cases, mappings, codex, errors)

    if args.self_test:
        if nested_execution_is_authenticated:
            run_install_negative_controls(mappings, errors)
        run_routing_negative_controls(routing_cases, mappings, codex, errors)
        run_panel_schema3_guidance_negative_control(reference, readme, errors)
        run_pre_dispatch_validation_guidance_negative_control(
            reference, readme, errors
        )
        run_source_review_boundary_guidance_negative_control(
            reference, readme, errors
        )
        if nested_execution_is_authenticated:
            run_materialized_authority_controls(mappings, routing_cases, errors)
        run_fixture_inventory_negative_controls(fixtures, errors)
    if args.installed_root and nested_execution_is_authenticated:
        check_installed_inventory(args.installed_root.expanduser(), mappings, errors)
    loader_bin = args.release_gate or args.real_loader
    ci_loader_classification: Optional[str] = None
    if args.ci_loader_gate:
        discovered = shutil.which("codex")
        if discovered is None:
            ci_loader_classification = (
                f"UNAVAILABLE: codex executable absent on platform {sys.platform}"
            )
        else:
            try:
                discovered_version = subprocess.run(
                    [discovered, "--version"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=10,
                ).stdout.strip()
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                ci_loader_classification = f"UNAVAILABLE: codex version probe failed: {exc}"
            else:
                if discovered_version == f"codex-cli {EXPECTED_CODEX_VERSION}":
                    loader_bin = Path(discovered)
                else:
                    ci_loader_classification = (
                        "UNAVAILABLE: pinned codex-cli "
                        f"{EXPECTED_CODEX_VERSION} not present; found {discovered_version!r}"
                    )
    if loader_bin:
        run_real_loader_controls(
            loader_bin.expanduser(), mappings, routing_cases, errors
        )

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr if args.json else sys.stdout)
        if args.json:
            print(
                json.dumps(
                    {
                        "schema_version": 1,
                        "status": "FAIL",
                        "named_mutation_outcomes": {},
                        "errors": errors,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        return 1
    installed_note = ", installed inventory/bytes/links" if args.installed_root else ""
    self_test_note = ", negative controls" if args.self_test else ""
    loader_note = (
        ", Codex 0.147.0 real-loader inventory and loaded route retrieval"
        if loader_bin
        else ""
    )
    ci_note = (
        f", CI loader classification {ci_loader_classification}"
        if ci_loader_classification
        else ""
    )
    if args.json:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "PASS",
                    "named_mutation_outcomes": named_self_test_outcomes(
                        fixtures, contract, routing_cases, reference, bool(loader_bin)
                    )
                    if args.self_test
                    else {},
                    "real_loader": {
                        "status": "PASS" if loader_bin else (
                            "SKIPPED" if ci_loader_classification else "NOT_REQUESTED"
                        ),
                        "classification": ci_loader_classification,
                        "implicit_model_selection": "UNCHECKED",
                    },
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    print(
        "OK: queue coverage, canonical state/recovery fixtures, "
        f"{len(mappings)}-file package "
        f"closure/routes/links{installed_note}{self_test_note}{loader_note}{ci_note}; "
        "implicit model route selection remains UNCHECKED without an authorized "
        "pinned-model evaluation"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
