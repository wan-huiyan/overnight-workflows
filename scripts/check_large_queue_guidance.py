#!/usr/bin/env python3
"""Fail when the large-queue route loses its core safety contracts."""

from collections import Counter
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
SKILL = ROOT / "plugins/overnight-multi-issue-implementation/SKILL.md"
REFERENCE = (
    ROOT
    / "plugins/overnight-multi-issue-implementation/references/large-live-queue-orchestration.md"
)
CODEX = ROOT / "codex/overnight-workflows/SKILL.md"
CODEX_WORKFLOW_ROUTE = (
    ROOT
    / "codex/overnight-workflows/references/workflows/overnight-multi-issue-implementation.md"
)
CODEX_QUEUE_ROUTE = (
    ROOT
    / "codex/overnight-workflows/references/workflows/references/large-live-queue-orchestration.md"
)
INSTALL_MANIFEST = ROOT / "codex/overnight-workflows/install-manifest.json"

EXPECTED_INSTALL_MAPPINGS = [
    {
        "canonical_source": "codex/overnight-workflows/SKILL.md",
        "installed_path": "SKILL.md",
    },
    {
        "canonical_source": "codex/overnight-workflows/agents/openai.yaml",
        "installed_path": "agents/openai.yaml",
    },
    {
        "canonical_source": "plugins/overnight-multi-issue-implementation/SKILL.md",
        "installed_path": "references/workflows/overnight-multi-issue-implementation.md",
        "navigation_stub": (
            "codex/overnight-workflows/references/workflows/"
            "overnight-multi-issue-implementation.md"
        ),
    },
    {
        "canonical_source": (
            "plugins/overnight-multi-issue-implementation/references/"
            "large-live-queue-orchestration.md"
        ),
        "installed_path": (
            "references/workflows/references/large-live-queue-orchestration.md"
        ),
        "navigation_stub": (
            "codex/overnight-workflows/references/workflows/references/"
            "large-live-queue-orchestration.md"
        ),
    },
]


def require(text: str, phrase: str, where: Path, errors: list[str]) -> None:
    if phrase not in text:
        errors.append(f"{where.relative_to(ROOT)}: missing {phrase!r}")


def main() -> int:
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

    require(
        readme,
        "[serial large live-queue procedure](plugins/overnight-multi-issue-implementation/references/large-live-queue-orchestration.md)",
        README,
        errors,
    )
    require(readme, "## Core patterns\n", README, errors)
    require(readme, "complete four-file mapping", README, errors)

    # Contract example: stopping the controller and ending its execution lease
    # must not erase a still-open pull request's exact-path reservation.
    events = [
        {
            "record_type": "controller_liveness",
            "controller_id": "controller-1",
            "state": "RUNNING",
        },
        {
            "record_type": "execution_lease",
            "lease_id": "lease-1",
            "state": "ACTIVE",
        },
        {
            "record_type": "path_reservation",
            "reservation_id": "reservation-pr-934-data-js",
            "exact_paths": ["docs/site/assets/data.js"],
            "owner": {"kind": "pull_request", "id": "934"},
            "state": "ACTIVE",
        },
        {
            "record_type": "controller_liveness",
            "controller_id": "controller-1",
            "state": "STOPPED",
        },
    ]
    controllers: dict[str, dict] = {}
    leases: dict[str, dict] = {}
    reservations: dict[str, dict] = {}
    for event in events:
        if event["record_type"] == "controller_liveness":
            controllers[event["controller_id"]] = event
        elif event["record_type"] == "execution_lease":
            leases[event["lease_id"]] = event
        elif event["record_type"] == "path_reservation":
            reservations[event["reservation_id"]] = event

    if any(event["state"] == "RUNNING" for event in controllers.values()):
        errors.append("stopped controller still reads as live")
    if not any(event["state"] == "ACTIVE" for event in leases.values()):
        errors.append("controller stop incorrectly ended the live execution lease")
    retained = reservations.get("reservation-pr-934-data-js")
    if not retained or retained["state"] != "ACTIVE":
        errors.append("controller stop incorrectly released the PR path reservation")
    elif retained["exact_paths"] != ["docs/site/assets/data.js"]:
        errors.append("retained PR reservation lost its exact path")

    # A later process inspection may end the lease. That transition still must
    # not alter the independently owned pull-request reservation.
    leases["lease-1"] = {
        "record_type": "execution_lease",
        "lease_id": "lease-1",
        "state": "ENDED",
    }
    if any(event["state"] == "ACTIVE" for event in leases.values()):
        errors.append("ended execution lease still reads as active")
    if reservations["reservation-pr-934-data-js"]["state"] != "ACTIVE":
        errors.append("lease end incorrectly released the PR path reservation")

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
    if manifest.get("mappings") != EXPECTED_INSTALL_MAPPINGS:
        errors.append(
            f"{INSTALL_MANIFEST.relative_to(ROOT)}: install mapping is incomplete or changed"
        )

    for mapping in EXPECTED_INSTALL_MAPPINGS:
        source = ROOT / mapping["canonical_source"]
        if not source.is_file():
            errors.append(f"missing canonical install source: {mapping['canonical_source']}")
        installed_path = Path(mapping["installed_path"])
        if installed_path.is_absolute() or ".." in installed_path.parts:
            errors.append(f"unsafe installed path: {mapping['installed_path']}")

    for route, target in (
        (CODEX_WORKFLOW_ROUTE, SKILL),
        (CODEX_QUEUE_ROUTE, REFERENCE),
    ):
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

    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(
        "OK: large-queue routing, occurrence coverage, state, reservations, "
        "review, and complete Codex install mapping"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
