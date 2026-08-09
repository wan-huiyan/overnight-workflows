#!/usr/bin/env python3
"""Focused, live-safe tests for the whole-directory Codex publisher."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish_codex_install as publisher  # noqa: E402


def run(command: list[str], cwd: Path) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"command failed ({completed.returncode}): {command}\n"
            f"stdout={completed.stdout}\nstderr={completed.stderr}"
        )
    return completed.stdout.strip()


class PublisherHarness:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.repository = root / "repository"
        self.install = root / "skills" / "overnight-workflows"
        self.state = root / "install-state" / "overnight-workflows"
        self.evidence_parent = root / "evidence"
        self.manifest_path = "codex/overnight-workflows/install-manifest.json"
        self.expected = {"SKILL.md", "assets/note.md"}
        self._make_repository()
        self._make_live()

    def _make_repository(self) -> None:
        self.repository.mkdir()
        run(["git", "init", "-q"], self.repository)
        (self.repository / "codex/overnight-workflows").mkdir(parents=True)
        (self.repository / "plugins/example/assets").mkdir(parents=True)
        (self.repository / "scripts").mkdir()
        (self.repository / "codex/overnight-workflows/SKILL.md").write_text(
            "new router\n", encoding="utf-8"
        )
        (self.repository / "plugins/example/assets/note.md").write_text(
            "new note\n", encoding="utf-8"
        )
        manifest = {
            "schema_version": 2,
            "installed_skill": "overnight-workflows",
            "mappings": [
                {
                    "canonical_source": "codex/overnight-workflows/SKILL.md",
                    "installed_path": "SKILL.md",
                },
                {
                    "canonical_source": "plugins/example/assets/note.md",
                    "installed_path": "assets/note.md",
                },
            ],
        }
        (self.repository / self.manifest_path).write_text(
            json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
        )
        (self.repository / "scripts/check_large_queue_guidance.py").write_text(
            "#!/usr/bin/env python3\nraise SystemExit(0)\n", encoding="utf-8"
        )
        run(["git", "add", "."], self.repository)
        run(
            [
                "git",
                "-c",
                "user.name=Publisher Test",
                "-c",
                "user.email=publisher@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                "fixture",
            ],
            self.repository,
        )
        self.commit = run(["git", "rev-parse", "HEAD"], self.repository)

    def _make_live(self) -> None:
        (self.install / "assets").mkdir(parents=True)
        (self.install / "SKILL.md").write_text("old router\n", encoding="utf-8")
        (self.install / "assets/note.md").write_text("old note\n", encoding="utf-8")

    def maintenance(self, operation: str) -> Path:
        path = self.root / f"maintenance-{operation}.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "operation_id": operation,
                    "authorized_by": "release-owner",
                    "maintenance_window_id": "window-1",
                    "reader_quiescence_status": "QUIESCENT",
                    "reader_quiescence_checked_at": "2026-08-09T00:00:00Z",
                    "controller_id": "controller-1",
                    "controller_state": "ACTIVE",
                    "owner_host": "test-host",
                    "owner_pid": 4242,
                    "owner_process_start_identity": "test-process-start-1",
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def prepare(self, operation: str, **overrides: object) -> dict[str, object]:
        arguments: dict[str, object] = {
            "source_repository": self.repository,
            "source_commit": self.commit,
            "manifest_path": self.manifest_path,
            "install_root": self.install,
            "state_root": self.state,
            "evidence_root": self.evidence_parent / operation,
            "operation": operation,
            "checker_runner": publisher._fake_checker,
        }
        arguments.update(overrides)
        return publisher.prepare_operation(**arguments)  # type: ignore[arg-type]

    def reserve(self, operation: str) -> dict[str, object]:
        return publisher.reserve_operation(
            state_root=self.state,
            operation=operation,
            maintenance_receipt=self.maintenance(operation),
        )

    def old_inventory(self) -> publisher.Inventory:
        return publisher.build_inventory(self.install, self.expected)


class InventoryTests(unittest.TestCase):
    def test_sha256_size_path_v1_is_exact_and_one_byte_drift_changes_identity(self) -> None:
        with tempfile.TemporaryDirectory(prefix="publisher-inventory-") as raw:
            root = Path(raw).resolve()
            (root / "z").write_bytes(b"z")
            (root / "alpha").write_bytes(b"alpha")
            inventory = publisher.build_inventory(root)
            expected = (
                f"{hashlib.sha256(b'alpha').hexdigest()}\t5\talpha\n"
                f"{hashlib.sha256(b'z').hexdigest()}\t1\tz\n"
            ).encode("ascii")
            self.assertEqual(expected, inventory.data)
            self.assertEqual(publisher.parse_inventory(expected), inventory.entries)
            self.assertEqual(2, inventory.file_count)
            self.assertEqual(6, inventory.total_bytes)
            (root / "z").write_bytes(b"Z")
            self.assertNotEqual(inventory.digest, publisher.build_inventory(root).digest)

    def test_parser_rejects_noncanonical_inventory(self) -> None:
        digest = "0" * 64
        bad_inputs = (
            f"{digest}\t01\ta\n".encode(),
            f"{'A' * 64}\t1\ta\n".encode(),
            f"{digest}\t1\t../a\n".encode(),
            f"{digest}\t1\tb\n{digest}\t1\ta\n".encode(),
            f"{digest}\t1\ta".encode(),
            f"{digest}\t1\ta\n\n".encode(),
        )
        for data in bad_inputs:
            with self.subTest(data=data):
                with self.assertRaises(publisher.PublicationError):
                    publisher.parse_inventory(data)

    def test_inventory_rejects_unmanaged_symlink_nonregular_and_unsafe_names(self) -> None:
        with tempfile.TemporaryDirectory(prefix="publisher-members-") as raw:
            root = Path(raw).resolve()
            (root / "managed").write_bytes(b"ok")
            (root / "extra").write_bytes(b"no")
            with self.assertRaisesRegex(publisher.PublicationError, "unmanaged"):
                publisher.build_inventory(root, {"managed"})
            (root / "extra").unlink()
            os.symlink("managed", root / "link")
            with self.assertRaisesRegex(publisher.PublicationError, "symlink"):
                publisher.build_inventory(root)
            (root / "link").unlink()
            os.mkfifo(root / "pipe")
            with self.assertRaisesRegex(publisher.PublicationError, "non-regular"):
                publisher.build_inventory(root)
            (root / "pipe").unlink()
            (root / "bad\tname").write_bytes(b"x")
            with self.assertRaisesRegex(publisher.PublicationError, "control character"):
                publisher.build_inventory(root)

    def test_symlinked_root_and_parent_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="publisher-symlink-root-") as raw:
            root = Path(raw).resolve()
            real_parent = root / "real-parent"
            real_tree = real_parent / "tree"
            real_tree.mkdir(parents=True)
            (real_tree / "managed").write_bytes(b"ok")
            os.symlink(real_tree, root / "root-link")
            with self.assertRaisesRegex(publisher.PublicationError, "symlink component"):
                publisher.build_inventory(root / "root-link")
            os.symlink(real_parent, root / "parent-link")
            with self.assertRaisesRegex(publisher.PublicationError, "symlink component"):
                publisher.build_inventory(root / "parent-link/tree")

    def test_cross_filesystem_stage_is_rejected(self) -> None:
        first = mock.Mock(st_dev=1)
        second = mock.Mock(st_dev=2)
        with mock.patch.object(publisher.os, "stat", side_effect=(first, second)):
            with self.assertRaisesRegex(publisher.PublicationError, "different filesystems"):
                publisher._require_same_filesystem(Path("/stage"), Path("/live-parent"))


class PublicationTests(unittest.TestCase):
    def make_harness(self) -> tuple[tempfile.TemporaryDirectory[str], PublisherHarness]:
        temporary = tempfile.TemporaryDirectory(prefix="publisher-operation-")
        return temporary, PublisherHarness(Path(temporary.name).resolve())

    def test_prepare_uses_immutable_commit_and_independent_snapshot(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        (harness.repository / "codex/overnight-workflows/SKILL.md").write_text(
            "uncommitted drift\n", encoding="utf-8"
        )
        receipt = harness.prepare("immutable")
        state_paths = publisher._operation_paths(harness.state, "immutable")
        self.assertEqual(
            b"new router\n", (state_paths["slot"] / "SKILL.md").read_bytes()
        )
        snapshot = harness.evidence_parent / "immutable/snapshot/SKILL.md"
        self.assertEqual(b"new router\n", snapshot.read_bytes())
        self.assertNotEqual(
            os.stat(snapshot).st_ino,
            os.stat(state_paths["slot"] / "SKILL.md").st_ino,
        )
        self.assertEqual(
            receipt["candidate_inventory"]["sha256"],
            receipt["evidence_snapshot"]["sha256"],
        )

    def test_staging_failure_and_unmanaged_live_leave_old_root_untouched(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        old = harness.old_inventory().digest

        def fail(point: str) -> None:
            if point == "during_staging":
                raise publisher.InjectedFailure(point)

        with self.assertRaises(publisher.InjectedFailure):
            harness.prepare("stage-failure", failpoint=fail)
        self.assertEqual(old, harness.old_inventory().digest)
        (harness.install / "unmanaged").write_bytes(b"do not delete")
        with self.assertRaisesRegex(publisher.PublicationError, "unmanaged"):
            harness.prepare("unmanaged")
        self.assertEqual(b"do not delete", (harness.install / "unmanaged").read_bytes())

    def test_symlinked_source_and_stage_member_fail_closed(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        os.symlink("SKILL.md", harness.repository / "codex/overnight-workflows/linked")
        run(["git", "add", "codex/overnight-workflows/linked"], harness.repository)
        run(
            [
                "git",
                "-c",
                "user.name=Publisher Test",
                "-c",
                "user.email=publisher@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                "symlink fixture",
            ],
            harness.repository,
        )
        symlink_commit = run(["git", "rev-parse", "HEAD"], harness.repository)
        with self.assertRaisesRegex(publisher.PublicationError, "symlink"):
            harness.prepare("source-link", source_commit=symlink_commit)

        harness.prepare("stage-link")
        harness.reserve("stage-link")
        paths = publisher._operation_paths(harness.state, "stage-link")
        (paths["slot"] / "SKILL.md").unlink()
        os.symlink("assets/note.md", paths["slot"] / "SKILL.md")
        old = harness.old_inventory().digest
        exchange = publisher.FakeAtomicExchanger()
        with self.assertRaisesRegex(publisher.PublicationError, "symlink"):
            publisher.publish_operation(
                state_root=harness.state,
                operation="stage-link",
                exchanger=exchange,
                checker_runner=publisher._fake_checker,
            )
        self.assertEqual([], exchange.calls)
        self.assertEqual(old, harness.old_inventory().digest)

    def test_preflight_one_byte_drift_and_unavailable_exchange_do_not_swap(self) -> None:
        for case in ("drift", "unavailable"):
            with self.subTest(case=case):
                temporary, harness = self.make_harness()
                try:
                    harness.prepare(case)
                    harness.reserve(case)
                    before = harness.old_inventory()
                    if case == "drift":
                        (harness.install / "SKILL.md").write_bytes(b"old router!\n")
                        exchange = publisher.FakeAtomicExchanger()
                        expected_error = "drifted"
                    else:
                        exchange = publisher.FakeAtomicExchanger(available=False)
                        expected_error = "unavailable"
                    with self.assertRaisesRegex(publisher.PublicationError, expected_error):
                        publisher.publish_operation(
                            state_root=harness.state,
                            operation=case,
                            exchanger=exchange,
                            checker_runner=publisher._fake_checker,
                        )
                    self.assertEqual([], exchange.calls)
                    if case == "drift":
                        self.assertEqual(b"old router!\n", (harness.install / "SKILL.md").read_bytes())
                    else:
                        self.assertEqual(before.digest, harness.old_inventory().digest)
                finally:
                    temporary.cleanup()

    def test_one_byte_evidence_snapshot_drift_invalidates_publication(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        old = harness.old_inventory().digest
        prepared = harness.prepare("snapshot-drift")
        harness.reserve("snapshot-drift")
        snapshot_file = harness.evidence_parent / "snapshot-drift/snapshot/SKILL.md"
        os.chmod(snapshot_file, 0o600)
        snapshot_file.write_bytes(b"new router!\n")
        self.assertNotEqual(
            prepared["evidence_snapshot"]["sha256"],
            publisher.build_inventory(
                harness.evidence_parent / "snapshot-drift/snapshot", harness.expected
            ).digest,
        )
        exchanger = publisher.FakeAtomicExchanger()
        with self.assertRaisesRegex(publisher.PublicationError, "snapshot drifted"):
            publisher.publish_operation(
                state_root=harness.state,
                operation="snapshot-drift",
                exchanger=exchanger,
                checker_runner=publisher._fake_checker,
            )
        self.assertEqual([], exchanger.calls)
        self.assertEqual(old, harness.old_inventory().digest)

    def test_successful_staged_checker_cannot_mutate_candidate_before_swap(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        old = harness.old_inventory().digest
        harness.prepare("checker-mutation")
        harness.reserve("checker-mutation")
        exchange = publisher.FakeAtomicExchanger()

        def mutating_checker(source: Path, installed: Path) -> dict[str, object]:
            (installed / "SKILL.md").write_bytes(b"checker-mutated candidate\n")
            return publisher._fake_checker(source, installed)

        with self.assertRaisesRegex(
            publisher.PublicationError, "changed during the immediate pre-swap checker"
        ):
            publisher.publish_operation(
                state_root=harness.state,
                operation="checker-mutation",
                exchanger=exchange,
                checker_runner=mutating_checker,
            )
        self.assertEqual([], exchange.calls)
        self.assertEqual(old, harness.old_inventory().digest)
        paths = publisher._operation_paths(harness.state, "checker-mutation")
        self.assertEqual(
            b"checker-mutated candidate\n", (paths["slot"] / "SKILL.md").read_bytes()
        )
        _, state = publisher._load_state(harness.state, "checker-mutation")
        self.assertEqual("UNCHECKED", state["status"])

    def test_immutable_source_checker_drift_blocks_reserve_and_publish(self) -> None:
        for point in ("before-reserve", "before-publish"):
            with self.subTest(point=point):
                temporary, harness = self.make_harness()
                try:
                    operation = f"source-drift-{point}"
                    old = harness.old_inventory().digest
                    harness.prepare(operation)
                    if point == "before-publish":
                        harness.reserve(operation)
                    paths = publisher._operation_paths(harness.state, operation)
                    checker = paths["source"] / "scripts/check_large_queue_guidance.py"
                    os.chmod(checker, 0o600)
                    checker.write_text(
                        "#!/usr/bin/env python3\n# forged after prepare\nraise SystemExit(0)\n",
                        encoding="utf-8",
                    )
                    if point == "before-reserve":
                        with self.assertRaisesRegex(
                            publisher.PublicationError, "immutable source tree drifted"
                        ):
                            harness.reserve(operation)
                        self.assertFalse(paths["reservation"].exists())
                    else:
                        exchange = publisher.FakeAtomicExchanger()
                        with self.assertRaisesRegex(
                            publisher.PublicationError, "immutable source tree drifted"
                        ):
                            publisher.publish_operation(
                                state_root=harness.state,
                                operation=operation,
                                exchanger=exchange,
                                checker_runner=publisher._fake_checker,
                            )
                        self.assertEqual([], exchange.calls)
                        self.assertTrue(paths["reservation"].is_file())
                    self.assertEqual(old, harness.old_inventory().digest)
                finally:
                    temporary.cleanup()

    def test_before_and_after_exchange_failures_preserve_complete_generations(self) -> None:
        for point, expected_classification in (
            ("before_exchange", "PRE_SWAP"),
            ("after_exchange", "POST_SWAP_RETAINED"),
        ):
            with self.subTest(point=point):
                temporary, harness = self.make_harness()
                try:
                    old = harness.old_inventory().digest
                    receipt = harness.prepare(point)
                    harness.reserve(point)

                    def fail(current: str) -> None:
                        if current == point:
                            raise publisher.InjectedFailure(point)

                    with self.assertRaises(publisher.InjectedFailure):
                        publisher.publish_operation(
                            state_root=harness.state,
                            operation=point,
                            exchanger=publisher.FakeAtomicExchanger(),
                            checker_runner=publisher._fake_checker,
                            failpoint=fail,
                        )
                    inspection = publisher.classify_generation_state(harness.state, point)
                    self.assertEqual(expected_classification, inspection["classification"])
                    self.assertEqual(old if point == "before_exchange" else receipt["candidate_inventory"]["sha256"], inspection["identities"]["live"])
                finally:
                    temporary.cleanup()

    def test_unambiguous_pre_swap_recovery_can_complete_without_lock_reentry(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        prepared = harness.prepare("recover-pre-swap")
        harness.reserve("recover-pre-swap")

        def stop(point: str) -> None:
            if point == "before_exchange":
                raise publisher.InjectedFailure(point)

        with self.assertRaises(publisher.InjectedFailure):
            publisher.publish_operation(
                state_root=harness.state,
                operation="recover-pre-swap",
                exchanger=publisher.FakeAtomicExchanger(),
                checker_runner=publisher._fake_checker,
                failpoint=stop,
            )
        recovered = publisher.recover_operation(
            state_root=harness.state,
            operation="recover-pre-swap",
            action="complete",
            exchanger=publisher.FakeAtomicExchanger(),
            checker_runner=publisher._fake_checker,
        )
        self.assertEqual("PUBLISHED", recovered["status"])
        self.assertEqual(
            prepared["candidate_inventory"]["sha256"], harness.old_inventory().digest
        )

    def test_recovery_checker_live_mutation_cannot_record_published(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        harness.prepare("recovery-checker-mutation")
        harness.reserve("recovery-checker-mutation")

        def stop(point: str) -> None:
            if point == "after_exchange":
                raise publisher.InjectedFailure(point)

        with self.assertRaises(publisher.InjectedFailure):
            publisher.publish_operation(
                state_root=harness.state,
                operation="recovery-checker-mutation",
                exchanger=publisher.FakeAtomicExchanger(),
                checker_runner=publisher._fake_checker,
                failpoint=stop,
            )
        paths = publisher._operation_paths(harness.state, "recovery-checker-mutation")
        previous_before = publisher.build_inventory(paths["previous"], harness.expected).digest

        def mutating_checker(source: Path, installed: Path) -> dict[str, object]:
            (installed / "SKILL.md").write_bytes(b"recovery checker mutation\n")
            return publisher._fake_checker(source, installed)

        with self.assertRaisesRegex(
            publisher.PublicationError, "recovery checker changed"
        ):
            publisher.recover_operation(
                state_root=harness.state,
                operation="recovery-checker-mutation",
                action="complete",
                exchanger=publisher.FakeAtomicExchanger(),
                checker_runner=mutating_checker,
            )
        _, state = publisher._load_state(harness.state, "recovery-checker-mutation")
        self.assertEqual("UNCHECKED", state["status"])
        self.assertTrue(paths["reservation"].is_file())
        self.assertEqual(
            previous_before,
            publisher.build_inventory(paths["previous"], harness.expected).digest,
        )

    def test_post_validation_failure_atomically_rolls_back_and_retains_failed_new(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        old = harness.old_inventory().digest
        prepared = harness.prepare("rollback")
        harness.reserve("rollback")
        calls = 0

        def checker(source: Path, installed: Path) -> dict[str, object]:
            nonlocal calls
            calls += 1
            if installed == harness.install:
                raise publisher.ValidationFailure("named negative control")
            return publisher._fake_checker(source, installed)

        exchanger = publisher.FakeAtomicExchanger()
        with self.assertRaises(publisher.ValidationFailure):
            publisher.publish_operation(
                state_root=harness.state,
                operation="rollback",
                exchanger=exchanger,
                checker_runner=checker,
            )
        self.assertEqual(2, len(exchanger.calls))
        self.assertEqual(old, harness.old_inventory().digest)
        paths = publisher._operation_paths(harness.state, "rollback")
        failed = publisher.build_inventory(paths["failed"], harness.expected)
        self.assertEqual(prepared["candidate_inventory"]["sha256"], failed.digest)
        _, state = publisher._load_state(harness.state, "rollback")
        self.assertEqual("ROLLED_BACK", state["status"])

    def test_success_retains_previous_and_finalize_releases_only_after_receipt(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        old = harness.old_inventory().digest
        prepared = harness.prepare("success")
        harness.reserve("success")
        publisher.publish_operation(
            state_root=harness.state,
            operation="success",
            exchanger=publisher.FakeAtomicExchanger(),
            checker_runner=publisher._fake_checker,
        )
        paths = publisher._operation_paths(harness.state, "success")
        self.assertEqual(
            prepared["candidate_inventory"]["sha256"],
            harness.old_inventory().digest,
        )
        self.assertEqual(old, publisher.build_inventory(paths["previous"], harness.expected).digest)
        output = harness.root / "receipts/final.json"
        receipt = publisher.finalize_operation(
            state_root=harness.state,
            operation="success",
            receipt_output=output,
            checker_runner=publisher._fake_checker,
        )
        self.assertEqual("PUBLISHED", receipt["terminal_state"])
        self.assertFalse(paths["reservation"].exists())
        self.assertTrue(paths["released"].is_file())
        self.assertEqual(receipt, json.loads(output.read_text(encoding="utf-8")))

    def test_finalize_rejects_protected_receipt_outputs_without_overwrite_or_release(self) -> None:
        for collision in ("reservation", "evidence", "maintenance"):
            with self.subTest(collision=collision):
                temporary, harness = self.make_harness()
                try:
                    operation = f"receipt-{collision}"
                    harness.prepare(operation)
                    reservation_record = harness.reserve(operation)
                    publisher.publish_operation(
                        state_root=harness.state,
                        operation=operation,
                        exchanger=publisher.FakeAtomicExchanger(),
                        checker_runner=publisher._fake_checker,
                    )
                    paths = publisher._operation_paths(harness.state, operation)
                    if collision == "reservation":
                        output = paths["reservation"]
                    elif collision == "evidence":
                        output = harness.evidence_parent / operation / "prepare-receipt.json"
                    else:
                        output = Path(reservation_record["maintenance"]["receipt_path"])
                    protected_before = output.read_bytes()
                    reservation_before = paths["reservation"].read_bytes()
                    with self.assertRaisesRegex(
                        publisher.PublicationError, "overlaps protected|collides"
                    ):
                        publisher.finalize_operation(
                            state_root=harness.state,
                            operation=operation,
                            receipt_output=output,
                            checker_runner=publisher._fake_checker,
                        )
                    self.assertEqual(protected_before, output.read_bytes())
                    self.assertEqual(reservation_before, paths["reservation"].read_bytes())
                    self.assertFalse(paths["released"].exists())
                    _, state = publisher._load_state(harness.state, operation)
                    self.assertEqual("PUBLISHED", state["status"])
                finally:
                    temporary.cleanup()

    def test_finalize_checker_live_mutation_cannot_release_stale_receipt(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        operation = "finalize-checker-mutation"
        harness.prepare(operation)
        harness.reserve(operation)
        publisher.publish_operation(
            state_root=harness.state,
            operation=operation,
            exchanger=publisher.FakeAtomicExchanger(),
            checker_runner=publisher._fake_checker,
        )
        paths = publisher._operation_paths(harness.state, operation)
        reservation_before = paths["reservation"].read_bytes()
        output = harness.root / "receipts/finalize-mutation.json"

        def mutating_checker(source: Path, installed: Path) -> dict[str, object]:
            (installed / "SKILL.md").write_bytes(b"acceptance checker mutation\n")
            return publisher._fake_checker(source, installed)

        with self.assertRaisesRegex(
            publisher.PublicationError, "acceptance checker changed"
        ):
            publisher.finalize_operation(
                state_root=harness.state,
                operation=operation,
                receipt_output=output,
                checker_runner=mutating_checker,
            )
        self.assertFalse(output.exists())
        self.assertEqual(reservation_before, paths["reservation"].read_bytes())
        self.assertFalse(paths["released"].exists())
        _, state = publisher._load_state(harness.state, operation)
        self.assertEqual("UNCHECKED", state["status"])

    def test_concurrent_reservation_has_one_winner_and_takeover_is_refused(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        old = harness.old_inventory().digest
        harness.prepare("publisher-a")
        harness.prepare("publisher-b")
        maintenance = {
            operation: harness.maintenance(operation)
            for operation in ("publisher-a", "publisher-b")
        }
        barrier = threading.Barrier(2)
        results: list[tuple[str, str]] = []

        def reserve(operation: str) -> None:
            barrier.wait()
            try:
                publisher.reserve_operation(
                    state_root=harness.state,
                    operation=operation,
                    maintenance_receipt=maintenance[operation],
                )
            except publisher.PublicationError as exc:
                results.append((operation, f"failed:{exc}"))
            else:
                results.append((operation, "won"))

        threads = [threading.Thread(target=reserve, args=(operation,)) for operation in maintenance]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(1, sum(result == "won" for _, result in results), results)
        self.assertEqual(old, harness.old_inventory().digest)
        reservation_path = publisher._operation_paths(
            harness.state, "publisher-a"
        )["reservation"]
        original = reservation_path.read_bytes()
        loser = next(operation for operation, result in results if result != "won")
        with self.assertRaisesRegex(publisher.PublicationError, "takeover"):
            publisher.reserve_operation(
                state_root=harness.state,
                operation=loser,
                maintenance_receipt=maintenance[loser],
            )
        self.assertEqual(original, reservation_path.read_bytes())

    def test_malformed_lock_takeover_and_ambiguous_recovery_are_refused_without_deletion(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        harness.prepare("malformed")
        reservation = publisher._operation_paths(harness.state, "malformed")["reservation"]
        reservation.write_bytes(b"{not-json\n")
        original = reservation.read_bytes()
        with self.assertRaisesRegex(publisher.PublicationError, "takeover"):
            harness.reserve("malformed")
        self.assertEqual(original, reservation.read_bytes())

        reservation.unlink()
        # Return to a valid reservation, stop after exchange, then introduce an
        # extra file so neither complete identity matches. Recovery must inspect
        # and keep every tree instead of guessing or deleting.
        harness.reserve("malformed")

        def stop(point: str) -> None:
            if point == "after_exchange":
                raise publisher.InjectedFailure(point)

        with self.assertRaises(publisher.InjectedFailure):
            publisher.publish_operation(
                state_root=harness.state,
                operation="malformed",
                exchanger=publisher.FakeAtomicExchanger(),
                checker_runner=publisher._fake_checker,
                failpoint=stop,
            )
        (harness.install / "ambiguous").write_bytes(b"preserve")
        paths = publisher._operation_paths(harness.state, "malformed")
        previous_before = publisher.build_inventory(paths["previous"], harness.expected).digest
        with self.assertRaisesRegex(publisher.PublicationError, "ambiguous"):
            publisher.recover_operation(
                state_root=harness.state,
                operation="malformed",
                action="rollback",
                exchanger=publisher.FakeAtomicExchanger(),
                checker_runner=publisher._fake_checker,
            )
        self.assertEqual(b"preserve", (harness.install / "ambiguous").read_bytes())
        self.assertEqual(
            previous_before,
            publisher.build_inventory(paths["previous"], harness.expected).digest,
        )

    def test_unknown_or_inconsistent_reserved_owner_cannot_publish(self) -> None:
        for mutation in ("unknown", "wrong-operation"):
            with self.subTest(mutation=mutation):
                temporary, harness = self.make_harness()
                try:
                    harness.prepare(mutation)
                    harness.reserve(mutation)
                    reservation_path = publisher._operation_paths(
                        harness.state, mutation
                    )["reservation"]
                    reservation = json.loads(reservation_path.read_text(encoding="utf-8"))
                    if mutation == "unknown":
                        reservation["owner"]["controller_state"] = "UNKNOWN"
                    else:
                        reservation["operation_id"] = "different-operation"
                    reservation_path.write_text(
                        json.dumps(reservation, sort_keys=True, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    old = harness.old_inventory().digest
                    exchanger = publisher.FakeAtomicExchanger()
                    with self.assertRaises(publisher.PublicationError):
                        publisher.publish_operation(
                            state_root=harness.state,
                            operation=mutation,
                            exchanger=exchanger,
                            checker_runner=publisher._fake_checker,
                        )
                    self.assertEqual([], exchanger.calls)
                    self.assertEqual(old, harness.old_inventory().digest)
                    self.assertTrue(reservation_path.is_file())
                finally:
                    temporary.cleanup()


class CommandTests(unittest.TestCase):
    def test_self_test_is_live_safe_and_uses_only_fake_exchange(self) -> None:
        self.assertEqual(0, publisher.main(["self-test"]))

    def test_exact_doodle_controller_cli_contract_appends_receipts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="publisher-cli-") as raw:
            harness = PublisherHarness(Path(raw).resolve())
            operation = "controller-cli"
            evidence = harness.evidence_parent / operation
            prepare_receipt = evidence / "prepare.json"
            reader_record = harness.maintenance(operation)
            manifest = harness.root / "controller/finalization-evidence.jsonl"
            manifest.parent.mkdir()
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "sequence": 1,
                        "recorded_at": "2026-08-09T00:00:00Z",
                        "record_type": "controller_started",
                        "finalization_id": "finalization-test-1",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            lock = harness.state / "package.lock"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(
                    0,
                    publisher.main(
                        [
                            "prepare",
                            "--operation",
                            operation,
                            "--source-repository",
                            str(harness.repository),
                            "--source-commit",
                            harness.commit,
                            "--manifest",
                            str(harness.repository / harness.manifest_path),
                            "--install-root",
                            str(harness.install),
                            "--state-root",
                            str(harness.state),
                            "--evidence-root",
                            str(evidence),
                            "--receipt",
                            str(prepare_receipt),
                        ]
                    ),
                )
                self.assertEqual(
                    0,
                    publisher.main(
                        [
                            "reserve",
                            "--operation",
                            operation,
                            "--state-root",
                            str(harness.state),
                            "--lock",
                            str(lock),
                            "--prepare-receipt",
                            str(prepare_receipt),
                            "--reader-quiescence-record",
                            str(reader_record),
                            "--finalization-manifest",
                            str(manifest),
                        ]
                    ),
                )
                with mock.patch.object(
                    publisher,
                    "DarwinAtomicExchanger",
                    return_value=publisher.FakeAtomicExchanger(),
                ):
                    self.assertEqual(
                        0,
                        publisher.main(
                            [
                                "publish",
                                "--operation",
                                operation,
                                "--state-root",
                                str(harness.state),
                                "--lock",
                                str(lock),
                                "--require-atomic-exchange",
                                "darwin-rename-swap",
                            ]
                        ),
                    )
                self.assertEqual(
                    0,
                    publisher.main(
                        [
                            "finalize",
                            "--operation",
                            operation,
                            "--state-root",
                            str(harness.state),
                            "--lock",
                            str(lock),
                            "--finalization-manifest",
                            str(manifest),
                        ]
                    ),
                )
            records = [
                json.loads(line)
                for line in manifest.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([1, 2, 3], [record["sequence"] for record in records])
            self.assertEqual(
                [
                    "controller_started",
                    "installed_publication_reservation_intent",
                    "installed_publication_terminal",
                ],
                [record["record_type"] for record in records],
            )
            prepare_value = json.loads(prepare_receipt.read_text(encoding="utf-8"))
            self.assertEqual(
                prepare_value["candidate_inventory"]["sha256"],
                prepare_value["generation_id"],
            )
            self.assertFalse(lock.exists())
            self.assertTrue((evidence / "publication-receipt.json").is_file())


if __name__ == "__main__":
    unittest.main()
