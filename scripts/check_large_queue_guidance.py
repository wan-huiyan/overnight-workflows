#!/usr/bin/env python3
"""Fail when the large-queue route loses its core safety contracts."""

from collections import Counter
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
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


def require(text: str, phrase: str, where: Path, errors: list[str]) -> None:
    if phrase not in text:
        errors.append(f"{where.relative_to(ROOT)}: missing {phrase!r}")


def main() -> int:
    errors: list[str] = []
    skill = SKILL.read_text(encoding="utf-8")
    reference = REFERENCE.read_text(encoding="utf-8")
    codex = CODEX.read_text(encoding="utf-8")

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
    print("OK: large-queue routing, occurrence coverage, state, review, and Codex routes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
