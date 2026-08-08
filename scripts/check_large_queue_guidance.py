#!/usr/bin/env python3
"""Fail when the large-queue route loses its core safety contracts."""

import argparse
from collections import Counter
import json
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
SKILL = ROOT / "plugins/overnight-multi-issue-implementation/SKILL.md"
REFERENCE = (
    ROOT
    / "plugins/overnight-multi-issue-implementation/references/large-live-queue-orchestration.md"
)
CODEX = ROOT / "codex/overnight-workflows/SKILL.md"
INSTALL_MANIFEST = ROOT / "codex/overnight-workflows/install-manifest.json"


def require(text: str, phrase: str, where: Path, errors: list[str]) -> None:
    if phrase not in text:
        errors.append(f"{where.relative_to(ROOT)}: missing {phrase!r}")


def expected_install_mappings() -> list[dict[str, str]]:
    """Derive the umbrella inventory from every top-level workflow plugin."""
    mappings = [
        {
            "canonical_source": "codex/overnight-workflows/SKILL.md",
            "installed_path": "SKILL.md",
        },
        {
            "canonical_source": "codex/overnight-workflows/agents/openai.yaml",
            "installed_path": "agents/openai.yaml",
        },
    ]
    for source in sorted(ROOT.glob("plugins/*/SKILL.md")):
        plugin_name = source.parent.name
        mappings.append(
            {
                "canonical_source": source.relative_to(ROOT).as_posix(),
                "installed_path": f"references/workflows/{plugin_name}.md",
                "navigation_stub": (
                    "codex/overnight-workflows/references/workflows/"
                    f"{plugin_name}.md"
                ),
            }
        )
    mappings.append(
        {
            "canonical_source": (
                "plugins/overnight-multi-issue-implementation/references/"
                "large-live-queue-orchestration.md"
            ),
            "installed_path": (
                "references/workflows/references/"
                "large-live-queue-orchestration.md"
            ),
            "navigation_stub": (
                "codex/overnight-workflows/references/workflows/references/"
                "large-live-queue-orchestration.md"
            ),
        }
    )
    return mappings


def json_examples(markdown: str, errors: list[str]) -> dict[str, dict]:
    records: dict[str, dict] = {}
    for block in re.findall(r"```json\n(.*?)\n```", markdown, flags=re.DOTALL):
        try:
            value = json.loads(block)
        except json.JSONDecodeError as exc:
            errors.append(f"large-queue reference has invalid JSON example: {exc}")
            continue
        if isinstance(value, dict) and "record_type" in value:
            records[value["record_type"]] = value
    return records


def check_installed_inventory(
    installed_root: Path, mappings: list[dict[str, str]], errors: list[str]
) -> None:
    if not installed_root.is_dir():
        errors.append(f"installed skill root is not a directory: {installed_root}")
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--installed-root",
        type=Path,
        help="also require this installed umbrella to match the manifest and bytes",
    )
    args = parser.parse_args()

    errors: list[str] = []
    skill = SKILL.read_text(encoding="utf-8")
    reference = REFERENCE.read_text(encoding="utf-8")
    codex = CODEX.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")

    for phrase in (
        "the large-queue reference is the complete procedure",
        "does **not** continue",
        "## Issue-cluster procedure",
        "## Issue-cluster output (morning checklist)",
    ):
        require(skill, phrase, SKILL, errors)

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
        "Stopping a controller clears",
        "latest appended\noperational record is authoritative",
        "only join to them by ID",
        "Execution leases use no transfer state",
        "`TRANSFERRED` ends the old reservation",
        "`replacement_reservation_id`",
    ):
        require(reference, phrase, REFERENCE, errors)

    if "set(source_ids)" in reference:
        errors.append(f"{REFERENCE.relative_to(ROOT)}: filename/set coverage returned")

    # Negative control: two rows may link one file. Reusing the first parent for
    # the second row must fail even though the filename set and total count match.
    source = Counter([("same-prompt.md", 1), ("same-prompt.md", 2)])
    reused_parent = Counter([("same-prompt.md", 1), ("same-prompt.md", 1)])
    if source == reused_parent:
        errors.append("duplicate-occurrence negative control did not fail")

    require(codex, "name: overnight-workflows", CODEX, errors)
    require(codex, "source occurrence", CODEX, errors)
    require(codex, "merge-base SHA", CODEX, errors)
    require(codex, "controller liveness", CODEX, errors)
    require(codex, "execution leases", CODEX, errors)
    require(codex, "path reservations", CODEX, errors)

    mappings = expected_install_mappings()
    require(
        readme,
        "[serial large live-queue procedure](plugins/overnight-multi-issue-implementation/references/large-live-queue-orchestration.md)",
        README,
        errors,
    )
    require(readme, "## Core patterns\n", README, errors)
    require(readme, f"complete {len(mappings)}-file mapping", README, errors)
    require(readme, f"all {len(mappings)} source/install SHA-256 digests", README, errors)
    stub_count = sum("navigation_stub" in mapping for mapping in mappings)
    require(readme, f"The {stub_count} tracked route stubs", README, errors)
    require(readme, "explicit merge-on-green grant", README, errors)
    require(readme, "pull-request authority is absent", README, errors)

    records = json_examples(reference, errors)
    required_fields = {
        "controller_liveness": {
            "time",
            "sequence",
            "schema_version",
            "record_type",
            "run_id",
            "repository_id",
            "controller_id",
            "state",
            "heartbeat_at",
            "stopped_at",
            "stop_reason",
            "tool_session_id",
            "pid",
            "host",
        },
        "execution_lease": {
            "time",
            "sequence",
            "schema_version",
            "record_type",
            "run_id",
            "repository_id",
            "lease_id",
            "attempt_id",
            "lease_owner",
            "state",
            "started_at",
            "heartbeat_at",
            "lease_expires_at",
            "takeover_condition",
            "ended_at",
            "end_reason",
            "tool_session_id",
            "pid",
            "command",
            "worktree",
            "branch",
        },
        "path_reservation": {
            "time",
            "sequence",
            "schema_version",
            "record_type",
            "run_id",
            "repository_id",
            "reservation_id",
            "exact_paths",
            "owner",
            "state",
            "created_at",
            "expires_at",
            "takeover_condition",
            "released_at",
            "release_reason",
        },
        "task_transition": {
            "time",
            "sequence",
            "schema_version",
            "record_type",
            "run_id",
            "repository_id",
            "task_id",
            "controller_id",
            "lease_id",
            "reservation_ids",
            "task_state",
        },
    }
    for record_type, fields in required_fields.items():
        record = records.get(record_type)
        if record is None:
            errors.append(f"large-queue reference is missing {record_type} JSON")
            continue
        missing_fields = fields - record.keys()
        if missing_fields:
            errors.append(
                f"{record_type} JSON is missing fields: "
                + ", ".join(sorted(missing_fields))
            )

    task = records.get("task_transition", {})
    forbidden_task_snapshots = {
        "tool_session_id",
        "pid",
        "command",
        "heartbeat_at",
        "lease_expires_at",
        "controller_state",
        "lease_state",
        "reservation_state",
        "reservation_owner",
        "exact_paths",
        "expires_at",
        "takeover_condition",
    }
    duplicated = forbidden_task_snapshots & task.keys()
    if duplicated:
        errors.append(
            "task transition duplicates operational state: "
            + ", ".join(sorted(duplicated))
        )

    reservation = records.get("path_reservation", {})
    if "expires_at" not in reservation:
        errors.append("path reservation must carry nullable expires_at")
    if not isinstance(reservation.get("takeover_condition"), str) or not reservation[
        "takeover_condition"
    ].strip():
        errors.append("path reservation must carry a non-empty takeover_condition")
    for exact_path in reservation.get("exact_paths", []):
        parsed = PurePosixPath(exact_path)
        if (
            not exact_path
            or exact_path.startswith("/")
            or ".." in parsed.parts
            or "\\" in exact_path
            or parsed.as_posix() != exact_path
        ):
            errors.append(f"reservation path is not repository-relative: {exact_path}")

    # Contract example: stopping the controller and ending its execution lease
    # must not erase a still-open pull request's exact-path reservation.
    controller = records.get("controller_liveness", {})
    lease = records.get("execution_lease", {})
    reservation = records.get("path_reservation", {})
    stopped_controller = {
        **controller,
        "sequence": 20,
        "state": "STOPPED",
        "stopped_at": "ISO-8601",
        "stop_reason": "controller session ended",
    }
    events = [controller, lease, reservation, stopped_controller]
    id_fields = {
        "controller_liveness": "controller_id",
        "execution_lease": "lease_id",
        "path_reservation": "reservation_id",
    }
    latest: dict[tuple[str, str, str, str], dict] = {}
    for event in events:
        record_type = event.get("record_type")
        id_field = id_fields.get(record_type)
        if not id_field or id_field not in event:
            continue
        key = (
            event["run_id"],
            event["repository_id"],
            record_type,
            event[id_field],
        )
        previous = latest.get(key)
        if previous and event["sequence"] <= previous["sequence"]:
            errors.append(f"non-increasing operational sequence for {key}")
        latest[key] = event

    controller_key = (
        controller.get("run_id"),
        controller.get("repository_id"),
        "controller_liveness",
        controller.get("controller_id"),
    )
    lease_key = (
        lease.get("run_id"),
        lease.get("repository_id"),
        "execution_lease",
        lease.get("lease_id"),
    )
    reservation_key = (
        reservation.get("run_id"),
        reservation.get("repository_id"),
        "path_reservation",
        reservation.get("reservation_id"),
    )
    if latest.get(controller_key, {}).get("state") == "RUNNING":
        errors.append("stopped controller still reads as live")
    if latest.get(lease_key, {}).get("state") != "ACTIVE":
        errors.append("controller stop incorrectly ended the live execution lease")
    retained = latest.get(reservation_key)
    if not retained or retained["state"] != "ACTIVE":
        errors.append("controller stop incorrectly released the PR path reservation")
    elif retained["exact_paths"] != ["docs/site/assets/data.js"]:
        errors.append("retained PR reservation lost its exact path")

    # A later process inspection may end the lease. That transition still must
    # not alter the independently owned pull-request reservation.
    latest[lease_key] = {
        **lease,
        "sequence": 21,
        "state": "ENDED",
        "ended_at": "ISO-8601",
        "end_reason": "process inspection confirmed exit",
    }
    if latest[lease_key]["state"] == "ACTIVE":
        errors.append("ended execution lease still reads as active")
    if latest[reservation_key]["state"] != "ACTIVE":
        errors.append("lease end incorrectly released the PR path reservation")

    # A reservation transfer is terminal for the old ID and valid only when a
    # different ACTIVE replacement preserves the same run, repository and
    # exact path set. Exercise both the valid path and realistic false passes.
    def valid_reservation_transfer(old: dict, replacement: Optional[dict]) -> bool:
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

    replacement_reservation = {
        **reservation,
        "sequence": 22,
        "reservation_id": "reservation-pr-934-data-js-successor",
        "owner": {"kind": "agent", "id": "integrator-2", "branch": "successor"},
    }
    transferred_reservation = {
        **reservation,
        "sequence": 23,
        "state": "TRANSFERRED",
        "replacement_reservation_id": replacement_reservation["reservation_id"],
        "released_at": "ISO-8601",
        "release_reason": "named owner transfer",
    }
    if not valid_reservation_transfer(
        transferred_reservation, replacement_reservation
    ):
        errors.append("valid reservation transfer control did not pass")
    invalid_transfers = [
        ({k: v for k, v in transferred_reservation.items()
          if k != "replacement_reservation_id"}, replacement_reservation),
        (transferred_reservation, None),
        (transferred_reservation, {**replacement_reservation, "state": "RELEASED"}),
        (transferred_reservation, {**replacement_reservation,
                                   "repository_id": "github.com/example/other"}),
        (transferred_reservation, {**replacement_reservation,
                                   "exact_paths": ["different/path"]}),
    ]
    if any(valid_reservation_transfer(old, new) for old, new in invalid_transfers):
        errors.append("invalid reservation transfer negative control passed")

    if task:
        if task.get("controller_id") != controller.get("controller_id"):
            errors.append("task transition controller_id does not join controller record")
        if task.get("lease_id") != lease.get("lease_id"):
            errors.append("task transition lease_id does not join lease record")
        if reservation.get("reservation_id") not in task.get("reservation_ids", []):
            errors.append("task transition reservation_ids do not join reservation record")

    try:
        manifest = json.loads(INSTALL_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{INSTALL_MANIFEST.relative_to(ROOT)}: cannot read manifest: {exc}")
        manifest = {}
    if manifest.get("schema_version") != 1:
        errors.append(f"{INSTALL_MANIFEST.relative_to(ROOT)}: expected schema_version 1")
    if manifest.get("installed_skill") != "overnight-workflows":
        errors.append(
            f"{INSTALL_MANIFEST.relative_to(ROOT)}: wrong installed_skill"
        )
    if manifest.get("mappings") != mappings:
        errors.append(
            f"{INSTALL_MANIFEST.relative_to(ROOT)}: install mapping is incomplete or changed"
        )

    installed_paths = [mapping["installed_path"] for mapping in mappings]
    canonical_sources = [mapping["canonical_source"] for mapping in mappings]
    if len(set(installed_paths)) != len(installed_paths):
        errors.append("install manifest repeats an installed path")
    if len(set(canonical_sources)) != len(canonical_sources):
        errors.append("install manifest repeats a canonical source")

    for mapping in mappings:
        source = ROOT / mapping["canonical_source"]
        if not source.is_file():
            errors.append(f"missing canonical install source: {mapping['canonical_source']}")
        installed_path = PurePosixPath(mapping["installed_path"])
        if mapping["installed_path"].startswith("/") or ".." in installed_path.parts:
            errors.append(f"unsafe installed path: {mapping['installed_path']}")

        route_path = mapping.get("navigation_stub")
        if mapping["installed_path"].startswith("references/workflows/") and not route_path:
            errors.append(
                f"workflow mapping has no navigation stub: {mapping['installed_path']}"
            )
            continue
        if not route_path:
            continue
        route = ROOT / route_path
        target = source
        if not route.is_file():
            errors.append(f"missing Codex route: {route.relative_to(ROOT)}")
            continue
        route_text = route.read_text(encoding="utf-8")
        require(route_text, target.relative_to(ROOT).as_posix(), route, errors)
        match = re.search(r"\]\(([^)]+)\)", route_text)
        if not match or (route.parent / match.group(1)).resolve() != target.resolve():
            errors.append(
                f"{route.relative_to(ROOT)}: Markdown route does not resolve to "
                f"{target.relative_to(ROOT)}"
            )

    if args.installed_root:
        check_installed_inventory(args.installed_root.resolve(), mappings, errors)

    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    checked = " and installed bytes" if args.installed_root else ""
    print(
        "OK: large-queue routing, occurrence coverage, state, reservations, "
        f"review, complete Codex routes, and install contract{checked}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
