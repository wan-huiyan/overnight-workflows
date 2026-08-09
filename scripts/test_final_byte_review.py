#!/usr/bin/env python3
"""Behavioral controls for the client-delivery frozen-final-byte gate."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = (
    ROOT
    / "plugins/overnight-review-client-delivery/scripts/final_byte_review.py"
)
SPEC = importlib.util.spec_from_file_location("final_byte_review", HELPER)
assert SPEC is not None and SPEC.loader is not None
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


class FinalByteReviewTests(unittest.TestCase):
    def fixture(
        self,
    ) -> tuple[tempfile.TemporaryDirectory[str], Path, Path, Path]:
        temporary = tempfile.TemporaryDirectory(prefix="final-byte-review-")
        root = Path(temporary.name).resolve()
        state = root / "gate.json"
        package = root / "package"
        package.mkdir()
        first = package / "deliverable.md"
        second = package / "appendix.csv"
        first.write_text("version one\n", encoding="utf-8")
        second.write_text("a,b\n1,2\n", encoding="utf-8")
        return temporary, state, first, second

    def state(self, path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding="utf-8"))

    def freeze(self, state: Path, *artifacts: Path) -> dict[str, object]:
        return gate.freeze(
            state,
            review_id="client-review-1",
            contributors=["author-1", "fixer-1"],
            artifacts=list(artifacts),
            artifact_root=artifacts[0].parent,
        )["state"]

    def report(
        self,
        root: Path,
        frozen: dict[str, object],
        *,
        reviewer: str = "reviewer-1",
        name: str = "final-report.json",
    ) -> Path:
        path = root / name
        inventory = frozen["frozen_inventory"]
        assert isinstance(inventory, dict)
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "record_type": gate.REPORT_TYPE,
                    "review_id": frozen["review_id"],
                    "cycle": frozen["cycle"],
                    "freeze_id": frozen["freeze_id"],
                    "reviewer_id": reviewer,
                    "frozen_inventory_sha256": inventory["sha256"],
                    "verdict": "PASS",
                    "reviewed_at": "2026-08-09T00:00:00Z",
                    "summary": "Reviewed every frozen final byte.",
                    "findings": [],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def test_byte_change_before_and_after_report_blocks_phase_c(self) -> None:
        temporary, state, first, second = self.fixture()
        self.addCleanup(temporary.cleanup)
        frozen = self.freeze(state, first, second)
        report = self.report(Path(temporary.name).resolve(), frozen)
        first.write_text("changed before approval\n", encoding="utf-8")
        with self.assertRaisesRegex(gate.GateError, "changed before final approval"):
            gate.approve(state, report_path=report)
        self.assertEqual(gate.INVALIDATED, self.state(state)["status"])

        frozen = self.freeze(state, first, second)
        report = self.report(Path(temporary.name).resolve(), frozen, name="report-2.json")
        self.assertEqual(gate.APPROVED, gate.approve(state, report_path=report)["action"])
        self.assertEqual("READY_FOR_PHASE_C", gate.check(state)["action"])
        second.write_text("changed after approval\n", encoding="utf-8")
        with self.assertRaisesRegex(gate.GateError, "deliverable drift"):
            gate.check(state)
        self.assertEqual(gate.INVALIDATED, self.state(state)["status"])

    def test_restore_same_bytes_still_requires_a_fresh_cycle_report(self) -> None:
        temporary, state, first, second = self.fixture()
        self.addCleanup(temporary.cleanup)
        original = first.read_bytes()
        cycle_one = self.freeze(state, first, second)
        old_report = self.report(Path(temporary.name).resolve(), cycle_one)
        gate.approve(state, report_path=old_report)
        first.write_bytes(b"temporary drift\n")
        with self.assertRaises(gate.GateError):
            gate.check(state)
        first.write_bytes(original)
        cycle_two = self.freeze(state, first, second)
        self.assertNotEqual(cycle_one["cycle"], cycle_two["cycle"])
        self.assertNotEqual(cycle_one["freeze_id"], cycle_two["freeze_id"])
        with self.assertRaisesRegex(gate.GateError, "another freeze cycle"):
            gate.approve(state, report_path=old_report)
        self.assertEqual(gate.PENDING, self.state(state)["status"])
        fresh = self.report(
            Path(temporary.name).resolve(), cycle_two, name="fresh-report.json"
        )
        gate.approve(state, report_path=fresh)
        self.assertEqual("READY_FOR_PHASE_C", gate.check(state)["action"])

    def test_snapshot_is_immutable_and_live_change_restore_invalidates(self) -> None:
        temporary, state, first, second = self.fixture()
        self.addCleanup(temporary.cleanup)
        original = first.read_bytes()
        frozen = self.freeze(state, first, second)
        inventory = frozen["frozen_inventory"]
        assert isinstance(inventory, dict)
        entry = next(
            item for item in inventory["entries"] if item["path"] == str(first)  # type: ignore[index,union-attr]
        )
        snapshot = Path(entry["snapshot_path"])
        self.assertEqual(original, snapshot.read_bytes())
        self.assertEqual(0o400, stat.S_IMODE(snapshot.stat().st_mode))

        # The reviewer is directed to the snapshot, so an author changing and
        # restoring the live path cannot change the bytes under review.
        first.write_bytes(b"temporary live edit during review\n")
        self.assertEqual(original, snapshot.read_bytes())
        first.write_bytes(original)
        report = self.report(Path(temporary.name).resolve(), frozen)
        with self.assertRaisesRegex(gate.GateError, "changed before final approval"):
            gate.approve(state, report_path=report)
        self.assertEqual(gate.INVALIDATED, self.state(state)["status"])

    def test_snapshot_preserves_renderable_relative_package_layout(self) -> None:
        with tempfile.TemporaryDirectory(prefix="final-byte-package-") as raw:
            root = Path(raw).resolve()
            package = root / "site"
            styles = package / "styles"
            styles.mkdir(parents=True)
            page = package / "index.html"
            stylesheet = styles / "main.css"
            page.write_text(
                '<link rel="stylesheet" href="styles/main.css">\n', encoding="utf-8"
            )
            stylesheet.write_text("body { color: black; }\n", encoding="utf-8")
            frozen = gate.freeze(
                root / "gate.json",
                review_id="client-review-1",
                contributors=["author-1"],
                artifacts=[page, stylesheet],
                artifact_root=package,
            )["state"]
            inventory = frozen["frozen_inventory"]
            snapshot_root = Path(inventory["snapshot_root"])
            self.assertEqual(page.read_bytes(), (snapshot_root / "index.html").read_bytes())
            self.assertEqual(
                stylesheet.read_bytes(),
                (snapshot_root / "styles/main.css").read_bytes(),
            )
            self.assertEqual(
                ["index.html", "styles/main.css"],
                sorted(entry["relative_path"] for entry in inventory["entries"]),
            )
            self.assertEqual(0o500, stat.S_IMODE((snapshot_root / "styles").stat().st_mode))

    def test_artifact_list_must_equal_the_complete_package_closure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="final-byte-package-omission-") as raw:
            root = Path(raw).resolve()
            package = root / "site"
            styles = package / "styles"
            styles.mkdir(parents=True)
            page = package / "index.html"
            stylesheet = styles / "main.css"
            page.write_text(
                '<link rel="stylesheet" href="styles/main.css">\n', encoding="utf-8"
            )
            stylesheet.write_text("body { color: black; }\n", encoding="utf-8")
            with self.assertRaisesRegex(gate.GateError, "complete package closure"):
                gate.freeze(
                    root / "gate.json",
                    review_id="client-review-1",
                    contributors=["author-1"],
                    artifacts=[page],
                    artifact_root=package,
                )
            self.assertFalse((root / "gate.json").exists())

    def test_change_and_restore_after_approval_is_still_detected(self) -> None:
        temporary, state, first, second = self.fixture()
        self.addCleanup(temporary.cleanup)
        original = first.read_bytes()
        frozen = self.freeze(state, first, second)
        report = self.report(Path(temporary.name).resolve(), frozen)
        gate.approve(state, report_path=report)
        first.write_bytes(b"temporary post-approval change\n")
        first.write_bytes(original)
        with self.assertRaisesRegex(gate.GateError, "deliverable drift"):
            gate.check(state)
        self.assertEqual(gate.INVALIDATED, self.state(state)["status"])

    def test_snapshot_byte_drift_invalidates_approval(self) -> None:
        temporary, state, first, second = self.fixture()
        self.addCleanup(temporary.cleanup)
        frozen = self.freeze(state, first, second)
        inventory = frozen["frozen_inventory"]
        assert isinstance(inventory, dict)
        snapshot = Path(inventory["entries"][0]["snapshot_path"])  # type: ignore[index,union-attr]
        os.chmod(snapshot, 0o600)
        snapshot.write_bytes(b"snapshot drift\n")
        report = self.report(Path(temporary.name).resolve(), frozen)
        with self.assertRaisesRegex(gate.GateError, "changed before final approval"):
            gate.approve(state, report_path=report)
        self.assertEqual(gate.INVALIDATED, self.state(state)["status"])

    def test_snapshot_change_and_restore_invalidates_approval(self) -> None:
        temporary, state, first, second = self.fixture()
        self.addCleanup(temporary.cleanup)
        frozen = self.freeze(state, first, second)
        inventory = frozen["frozen_inventory"]
        assert isinstance(inventory, dict)
        snapshot = Path(inventory["entries"][0]["snapshot_path"])  # type: ignore[index,union-attr]
        original = snapshot.read_bytes()
        os.chmod(snapshot, 0o600)
        snapshot.write_bytes(b"temporary snapshot edit\n")
        snapshot.write_bytes(original)
        os.chmod(snapshot, 0o400)
        self.assertEqual(original, snapshot.read_bytes())
        self.assertEqual(0o400, stat.S_IMODE(snapshot.stat().st_mode))
        report = self.report(Path(temporary.name).resolve(), frozen)
        with self.assertRaisesRegex(gate.GateError, "changed before final approval"):
            gate.approve(state, report_path=report)
        self.assertEqual(gate.INVALIDATED, self.state(state)["status"])

    def test_snapshot_mode_only_drift_invalidates_approval(self) -> None:
        temporary, state, first, second = self.fixture()
        self.addCleanup(temporary.cleanup)
        frozen = self.freeze(state, first, second)
        inventory = frozen["frozen_inventory"]
        assert isinstance(inventory, dict)
        snapshot = Path(inventory["entries"][0]["snapshot_path"])  # type: ignore[index,union-attr]
        original = snapshot.read_bytes()
        os.chmod(snapshot, 0o600)
        self.assertEqual(original, snapshot.read_bytes())
        report = self.report(Path(temporary.name).resolve(), frozen)
        with self.assertRaisesRegex(gate.GateError, "changed before final approval"):
            gate.approve(state, report_path=report)
        self.assertEqual(gate.INVALIDATED, self.state(state)["status"])

    def test_author_or_fixer_cannot_approve(self) -> None:
        temporary, state, first, second = self.fixture()
        self.addCleanup(temporary.cleanup)
        frozen = self.freeze(state, first, second)
        report = self.report(
            Path(temporary.name).resolve(), frozen, reviewer="fixer-1"
        )
        with self.assertRaisesRegex(gate.GateError, "must not be an author or fixer"):
            gate.approve(state, report_path=report)
        self.assertEqual(gate.PENDING, self.state(state)["status"])

    def test_report_byte_drift_invalidates_approval(self) -> None:
        temporary, state, first, second = self.fixture()
        self.addCleanup(temporary.cleanup)
        frozen = self.freeze(state, first, second)
        report = self.report(Path(temporary.name).resolve(), frozen)
        gate.approve(state, report_path=report)
        report.write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(gate.GateError, "report drift"):
            gate.check(state)
        self.assertEqual(gate.INVALIDATED, self.state(state)["status"])

    def test_linked_and_control_character_paths_fail_closed(self) -> None:
        temporary, state, first, second = self.fixture()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        hardlink = root / "hardlink.md"
        os.link(first, hardlink)
        with self.assertRaisesRegex(gate.GateError, "single-link"):
            self.freeze(state, first, second)
        hardlink.unlink()
        symlink = root / "symlink.md"
        symlink.symlink_to(first)
        with self.assertRaisesRegex(gate.GateError, "symlink"):
            self.freeze(state, symlink, second)
        unsafe = root / "bad\nname.md"
        unsafe.write_text("unsafe\n", encoding="utf-8")
        with self.assertRaisesRegex(gate.GateError, "normalized absolute"):
            self.freeze(state, unsafe, second)

    def test_cli_round_trip_reports_ready_only_after_approval(self) -> None:
        temporary, state, first, second = self.fixture()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.assertEqual(
            0,
            gate.main(
                [
                    "freeze",
                    "--state",
                    str(state),
                    "--review-id",
                    "client-review-1",
                    "--contributor",
                    "author-1",
                    "--artifact-root",
                    str(first.parent),
                    "--artifact",
                    str(first),
                    "--artifact",
                    str(second),
                ]
            ),
        )
        self.assertEqual(2, gate.main(["check", "--state", str(state)]))
        report = self.report(root, self.state(state))
        self.assertEqual(
            0,
            gate.main(["approve", "--state", str(state), "--report", str(report)]),
        )
        self.assertEqual(0, gate.main(["check", "--state", str(state)]))


if __name__ == "__main__":
    unittest.main()
