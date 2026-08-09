#!/usr/bin/env python3
"""Focused, live-safe tests for the whole-directory Codex publisher."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish_codex_install as publisher  # noqa: E402
import check_large_queue_guidance as guidance  # noqa: E402


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
            "old router\n", encoding="utf-8"
        )
        (self.repository / "plugins/example/assets/note.md").write_text(
            "old note\n", encoding="utf-8"
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
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "if '--json' in sys.argv:\n"
            "    print(json.dumps({'status': 'PASS', 'named_mutation_outcomes': "
            "{'FIXTURE_CHECKER_RAN': 'PASS'}}, sort_keys=True))\n"
            "raise SystemExit(0)\n",
            encoding="utf-8",
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
                "predecessor fixture",
            ],
            self.repository,
        )
        self.predecessor_commit = run(["git", "rev-parse", "HEAD"], self.repository)
        (self.repository / "codex/overnight-workflows/SKILL.md").write_text(
            "new router\n", encoding="utf-8"
        )
        (self.repository / "plugins/example/assets/note.md").write_text(
            "new note\n", encoding="utf-8"
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
                "candidate fixture",
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
            "expected_live_source_commit": self.predecessor_commit,
            "manifest_path": self.manifest_path,
            "install_root": self.install,
            "state_root": self.state,
            "evidence_root": self.evidence_parent / operation,
            "operation": operation,
            "checker_runner": publisher._fake_checker,
        }
        arguments.update(overrides)
        return publisher.prepare_operation(**arguments)  # type: ignore[arg-type]

    def reserve(
        self, operation: str, *, finalization_manifest: Path | None = None
    ) -> dict[str, object]:
        arguments: dict[str, object] = {
            "state_root": self.state,
            "operation": operation,
            "maintenance_receipt": self.maintenance(operation),
        }
        if finalization_manifest is not None:
            _, state = publisher._load_state(self.state, operation)
            arguments.update(
                {
                    "lock_path": self.state / "package.lock",
                    "prepare_receipt": Path(state["prepare_receipt"]["path"]),
                    "finalization_manifest": finalization_manifest,
                }
            )
        return publisher.reserve_operation(  # type: ignore[arg-type]
            **arguments
        )

    def finalization_manifest(self, operation: str) -> Path:
        path = self.root / f"controller/{operation}-finalization.jsonl"
        path.parent.mkdir(exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "sequence": 1,
                    "recorded_at": "2026-08-09T00:00:00Z",
                    "record_type": "controller_started",
                    "finalization_id": f"finalization-{operation}",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def takeover(
        self,
        operation: str,
        *,
        disposition: str = "STOPPED",
        process_status: str = "INACTIVE",
        session_status: str = "INACTIVE",
    ) -> Path:
        paths = publisher._operation_paths(self.state, operation)
        reservation = json.loads(paths["reservation"].read_text(encoding="utf-8"))
        path = self.root / f"takeover-{operation}.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "operation_id": operation,
                    "generation_id": reservation["generation_id"],
                    "prior_owner": reservation["owner"],
                    "owner_disposition": disposition,
                    "authorized_by": "recovery-owner",
                    "authorized_at": "2026-08-09T01:00:00Z",
                    "inspection": {
                        "inspected_at": "2026-08-09T00:59:00Z",
                        "inspected_by": "recovery-owner",
                        "owner_process_status": process_status,
                        "tool_session_status": session_status,
                        "evidence": ["process identity absent", "tool session ended"],
                    },
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def old_inventory(self) -> publisher.Inventory:
        return publisher.build_inventory(self.install, self.expected)

    def configure_skill_to_workflow_migration(
        self,
    ) -> tuple[str, str, set[str], set[str]]:
        """Commit and install an exact predecessor whose child entrypoint is SKILL.md."""
        child_source = self.repository / "plugins/example/routes/child"
        child_source.mkdir(parents=True)
        (child_source / "SKILL.md").write_text("old child route\n", encoding="utf-8")
        (self.repository / "codex/overnight-workflows/SKILL.md").write_text(
            "old router\n", encoding="utf-8"
        )
        (self.repository / "plugins/example/assets/note.md").write_text(
            "old note\n", encoding="utf-8"
        )
        manifest_path = self.repository / self.manifest_path
        predecessor_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        predecessor_manifest["mappings"].append(
            {
                "canonical_source": "plugins/example/routes/child/SKILL.md",
                "installed_path": "references/child/SKILL.md",
            }
        )
        manifest_path.write_text(
            json.dumps(predecessor_manifest, sort_keys=True) + "\n", encoding="utf-8"
        )
        run(["git", "add", "."], self.repository)
        run(
            [
                "git", "-c", "user.name=Publisher Test", "-c",
                "user.email=publisher@example.invalid", "-c", "commit.gpgsign=false",
                "commit", "-qm", "installed predecessor with child skill",
            ],
            self.repository,
        )
        predecessor = run(["git", "rev-parse", "HEAD"], self.repository)
        live_child = self.install / "references/child"
        live_child.mkdir(parents=True)
        (live_child / "SKILL.md").write_text("old child route\n", encoding="utf-8")

        (self.repository / "codex/overnight-workflows/SKILL.md").write_text(
            "new router\n", encoding="utf-8"
        )
        (self.repository / "plugins/example/assets/note.md").write_text(
            "new note\n", encoding="utf-8"
        )
        os.rename(child_source / "SKILL.md", child_source / "WORKFLOW.md")
        candidate_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        candidate_manifest["mappings"][-1] = {
            "canonical_source": "plugins/example/routes/child/WORKFLOW.md",
            "installed_path": "references/child/WORKFLOW.md",
        }
        manifest_path.write_text(
            json.dumps(candidate_manifest, sort_keys=True) + "\n", encoding="utf-8"
        )
        run(["git", "add", "."], self.repository)
        run(
            [
                "git", "-c", "user.name=Publisher Test", "-c",
                "user.email=publisher@example.invalid", "-c", "commit.gpgsign=false",
                "commit", "-qm", "migrate child entrypoint to workflow document",
            ],
            self.repository,
        )
        candidate = run(["git", "rev-parse", "HEAD"], self.repository)
        return (
            predecessor,
            candidate,
            {*self.expected, "references/child/SKILL.md"},
            {*self.expected, "references/child/WORKFLOW.md"},
        )


class InventoryTests(unittest.TestCase):
    def test_inventory_codec_name_matches_checker_and_committed_manifest(self) -> None:
        manifest = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "codex/overnight-workflows/install-manifest.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(publisher.INVENTORY_FORMAT, guidance.INSTALL_INVENTORY_FORMAT)
        self.assertEqual(publisher.INVENTORY_FORMAT, manifest["evidence_inventory_format"])

    def test_rejected_negative_control_probe_inode_is_never_chmodded(self) -> None:
        real_open = os.open
        real_fstat = os.fstat
        real_fchmod = os.fchmod
        guard_descriptors: set[int] = set()
        injected_descriptors: set[int] = set()
        chmod_descriptors: list[int] = []

        def controlled_open(
            path: os.PathLike[str] | str,
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            if dir_fd is None:
                descriptor = real_open(path, flags, mode)
            else:
                descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
            if (
                str(path).endswith("assets/morning_summary_template.md")
                and flags & os.O_ACCMODE == os.O_RDONLY
            ):
                guard_descriptors.add(descriptor)
            return descriptor

        def controlled_fstat(descriptor: int) -> os.stat_result:
            result = real_fstat(descriptor)
            if descriptor in guard_descriptors and descriptor not in injected_descriptors:
                injected_descriptors.add(descriptor)
                fields = list(result)
                fields[1] += 1
                return os.stat_result(fields)
            return result

        def controlled_fchmod(descriptor: int, mode: int) -> None:
            chmod_descriptors.append(descriptor)
            real_fchmod(descriptor, mode)

        errors: list[str] = []
        with mock.patch.object(guidance.os, "open", side_effect=controlled_open), mock.patch.object(
            guidance.os, "fstat", side_effect=controlled_fstat
        ), mock.patch.object(guidance.os, "fchmod", side_effect=controlled_fchmod):
            guidance.run_install_negative_controls(
                guidance.expected_install_mappings(), errors
            )
        self.assertTrue(injected_descriptors)
        self.assertIn(
            "Markdown-link negative-control probe changed before mutation", errors
        )
        self.assertEqual([], chmod_descriptors)

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


class DarwinExchangeTests(unittest.TestCase):
    @unittest.skipUnless(
        sys.platform == "darwin",
        "Darwin-only real RENAME_SWAP control; Linux is explicitly excluded",
    )
    def test_real_rename_swap_preserves_competing_reader_fd_on_temp_dirs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="publisher-real-swap-control-") as raw:
            root = Path(raw).resolve()
            left = root / "candidate"
            right = root / "installed-control"
            left.mkdir()
            right.mkdir()
            (left / "payload").write_bytes(b"candidate-generation")
            (left / "candidate-only").write_bytes(b"candidate-only")
            (right / "payload").write_bytes(b"preflight-generation")
            (right / "preflight-only").write_bytes(b"preflight-only")
            directory_descriptor = os.open(
                right, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            )
            try:
                exchanger = publisher.DarwinAtomicExchanger()
                exchanger.require_available()
                exchanger.exchange(left, right)
                self.assertEqual(b"candidate-generation", (right / "payload").read_bytes())
                self.assertEqual(b"preflight-generation", (left / "payload").read_bytes())
                self.assertEqual(
                    ["candidate-only", "payload"], sorted(os.listdir(right))
                )
                self.assertEqual(
                    ["payload", "preflight-only"],
                    sorted(os.listdir(directory_descriptor)),
                )
                payload_descriptor = os.open(
                    "payload", os.O_RDONLY, dir_fd=directory_descriptor
                )
                try:
                    self.assertEqual(
                        b"preflight-generation", os.read(payload_descriptor, 1024)
                    )
                finally:
                    os.close(payload_descriptor)
                old_only_descriptor = os.open(
                    "preflight-only", os.O_RDONLY, dir_fd=directory_descriptor
                )
                try:
                    self.assertEqual(
                        b"preflight-only", os.read(old_only_descriptor, 1024)
                    )
                finally:
                    os.close(old_only_descriptor)
                with self.assertRaises(FileNotFoundError):
                    os.stat("candidate-only", dir_fd=directory_descriptor)
            finally:
                os.close(directory_descriptor)


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

    def test_real_prepare_checker_accepts_read_only_immutable_source(self) -> None:
        with tempfile.TemporaryDirectory(prefix="publisher-real-read-only-") as raw:
            root = Path(raw).resolve()
            canonical_root = Path(__file__).resolve().parents[1]
            repository = root / "repository"
            repository.mkdir()
            tracked = subprocess.run(
                ["git", "ls-files", "-z"],
                cwd=canonical_root,
                check=True,
                stdout=subprocess.PIPE,
            ).stdout.split(b"\0")
            for encoded_path in tracked:
                if not encoded_path:
                    continue
                relative = Path(os.fsdecode(encoded_path))
                source = canonical_root / relative
                destination = repository / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                if source.is_symlink():
                    os.symlink(os.readlink(source), destination)
                else:
                    shutil.copy2(source, destination)
            run(["git", "init", "-q"], repository)
            run(["git", "add", "."], repository)
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
                    "real checker read-only source fixture",
                ],
                repository,
            )
            commit = run(["git", "rev-parse", "HEAD"], repository)
            manifest_path = "codex/overnight-workflows/install-manifest.json"
            manifest = json.loads((repository / manifest_path).read_text(encoding="utf-8"))
            install_root = root / "skills/overnight-workflows"
            for mapping in manifest["mappings"]:
                destination = install_root / mapping["installed_path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(repository / mapping["canonical_source"], destination)
            expected_paths = {
                mapping["installed_path"] for mapping in manifest["mappings"]
            }
            live_before = publisher.build_inventory(install_root, expected_paths)

            probe_relative = Path(
                "plugins/overnight-insight-discovery/"
                "assets/morning_summary_template.md"
            )
            canonical_probe = canonical_root / probe_relative
            repository_probe = repository / probe_relative
            canonical_before = (
                canonical_probe.read_bytes(),
                os.stat(canonical_probe).st_mode,
            )
            repository_before = (
                repository_probe.read_bytes(),
                os.stat(repository_probe).st_mode,
            )
            state_root = root / "state"
            receipt = publisher.prepare_operation(
                source_repository=repository,
                source_commit=commit,
                expected_live_source_commit=commit,
                manifest_path=manifest_path,
                install_root=install_root,
                state_root=state_root,
                evidence_root=root / "evidence/read-only-source",
                operation="read-only-source",
            )

            paths = publisher._operation_paths(state_root, "read-only-source")
            immutable_probe = paths["source"] / probe_relative
            immutable_checker = paths["source"] / "scripts/check_large_queue_guidance.py"
            self.assertEqual(0, os.stat(immutable_probe).st_mode & 0o222)
            self.assertEqual(0, os.stat(immutable_checker).st_mode & 0o222)
            self.assertEqual(repository_probe.read_bytes(), immutable_probe.read_bytes())
            self.assertEqual(
                live_before.data,
                publisher.build_inventory(install_root, expected_paths).data,
            )
            self.assertEqual(
                canonical_before,
                (canonical_probe.read_bytes(), os.stat(canonical_probe).st_mode),
            )
            self.assertEqual(
                repository_before,
                (repository_probe.read_bytes(), os.stat(repository_probe).st_mode),
            )
            outcomes = receipt["staged_validation"]["named_mutation_outcomes"]
            self.assertGreaterEqual(len(outcomes), 1)
            self.assertTrue(all(result == "PASS" for result in outcomes.values()))

    def test_prepare_rejects_incorrect_expected_live_source_commit(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        old = harness.old_inventory().digest
        with self.assertRaisesRegex(
            publisher.PublicationError,
            "does not exactly match --expected-live-source-commit",
        ):
            harness.prepare(
                "wrong-predecessor",
                expected_live_source_commit=harness.commit,
            )
        self.assertEqual(old, harness.old_inventory().digest)

    def test_exact_nested_skill_to_workflow_migration_uses_predecessor_commit(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        child_source = harness.repository / "plugins/example/routes/child"
        child_source.mkdir(parents=True)
        (child_source / "SKILL.md").write_text("old child route\n", encoding="utf-8")
        (harness.repository / "codex/overnight-workflows/SKILL.md").write_text(
            "old router\n", encoding="utf-8"
        )
        (harness.repository / "plugins/example/assets/note.md").write_text(
            "old note\n", encoding="utf-8"
        )
        manifest_path = harness.repository / harness.manifest_path
        predecessor_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        predecessor_manifest["mappings"].append(
            {
                "canonical_source": "plugins/example/routes/child/SKILL.md",
                "installed_path": "references/child/SKILL.md",
            }
        )
        manifest_path.write_text(
            json.dumps(predecessor_manifest, sort_keys=True) + "\n", encoding="utf-8"
        )
        run(["git", "add", "."], harness.repository)
        run(
            [
                "git", "-c", "user.name=Publisher Test", "-c",
                "user.email=publisher@example.invalid", "-c", "commit.gpgsign=false",
                "commit", "-qm", "installed predecessor with child skill",
            ],
            harness.repository,
        )
        predecessor = run(["git", "rev-parse", "HEAD"], harness.repository)
        live_child = harness.install / "references/child"
        live_child.mkdir(parents=True)
        (live_child / "SKILL.md").write_text("old child route\n", encoding="utf-8")

        (harness.repository / "codex/overnight-workflows/SKILL.md").write_text(
            "new router\n", encoding="utf-8"
        )
        (harness.repository / "plugins/example/assets/note.md").write_text(
            "new note\n", encoding="utf-8"
        )
        os.rename(child_source / "SKILL.md", child_source / "WORKFLOW.md")
        candidate_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        candidate_manifest["mappings"][-1] = {
            "canonical_source": "plugins/example/routes/child/WORKFLOW.md",
            "installed_path": "references/child/WORKFLOW.md",
        }
        manifest_path.write_text(
            json.dumps(candidate_manifest, sort_keys=True) + "\n", encoding="utf-8"
        )
        run(["git", "add", "."], harness.repository)
        run(
            [
                "git", "-c", "user.name=Publisher Test", "-c",
                "user.email=publisher@example.invalid", "-c", "commit.gpgsign=false",
                "commit", "-qm", "migrate child entrypoint to workflow document",
            ],
            harness.repository,
        )
        candidate = run(["git", "rev-parse", "HEAD"], harness.repository)
        prepared = harness.prepare(
            "path-migration",
            source_commit=candidate,
            expected_live_source_commit=predecessor,
        )
        self.assertIn(
            "references/child/SKILL.md",
            prepared["preflight_live_inventory"]["installed_paths"],
        )
        self.assertIn(
            "references/child/WORKFLOW.md",
            prepared["candidate_inventory"]["installed_paths"],
        )
        harness.reserve("path-migration")
        publisher.publish_operation(
            state_root=harness.state,
            operation="path-migration",
            exchanger=publisher.FakeAtomicExchanger(),
            checker_runner=publisher._fake_checker,
        )
        self.assertFalse((live_child / "SKILL.md").exists())
        self.assertEqual(b"old child route\n", (live_child / "WORKFLOW.md").read_bytes())
        receipt = publisher.finalize_operation(
            state_root=harness.state,
            operation="path-migration",
            checker_runner=publisher._fake_checker,
        )
        self.assertEqual(publisher.MUTATION_PUBLISHED, receipt["mutation_outcome"])
        self.assertTrue(receipt["named_mutation_outcomes"])

    def test_path_migration_rolls_back_after_publish_or_recovery_validation(self) -> None:
        for recovery in (False, True):
            with self.subTest(recovery=recovery), tempfile.TemporaryDirectory(
                prefix="publisher-path-migration-rollback-"
            ) as raw:
                harness = PublisherHarness(Path(raw).resolve())
                predecessor, candidate, preflight_paths, candidate_paths = (
                    harness.configure_skill_to_workflow_migration()
                )
                operation = "migration-recovery" if recovery else "migration-publish"
                old = publisher.build_inventory(harness.install, preflight_paths).digest
                prepared = harness.prepare(
                    operation,
                    source_commit=candidate,
                    expected_live_source_commit=predecessor,
                )
                harness.reserve(operation)

                def reject_live(source: Path, installed: Path) -> dict[str, object]:
                    if installed == harness.install:
                        raise publisher.ValidationFailure(
                            "migration postvalidation negative control"
                        )
                    return publisher._fake_checker(source, installed)

                if recovery:
                    def stop(point: str) -> None:
                        if point == "after_exchange":
                            raise publisher.InjectedFailure(point)

                    with self.assertRaises(publisher.InjectedFailure):
                        publisher.publish_operation(
                            state_root=harness.state,
                            operation=operation,
                            exchanger=publisher.FakeAtomicExchanger(),
                            checker_runner=publisher._fake_checker,
                            failpoint=stop,
                        )
                    with self.assertRaises(publisher.ValidationFailure):
                        publisher.recover_operation(
                            state_root=harness.state,
                            operation=operation,
                            action="complete",
                            exchanger=publisher.FakeAtomicExchanger(),
                            checker_runner=reject_live,
                            takeover_authorization=harness.takeover(operation),
                        )
                else:
                    with self.assertRaises(publisher.ValidationFailure):
                        publisher.publish_operation(
                            state_root=harness.state,
                            operation=operation,
                            exchanger=publisher.FakeAtomicExchanger(),
                            checker_runner=reject_live,
                        )

                paths = publisher._operation_paths(harness.state, operation)
                self.assertEqual(
                    old,
                    publisher.build_inventory(harness.install, preflight_paths).digest,
                )
                self.assertEqual(
                    prepared["candidate_inventory"]["sha256"],
                    publisher.build_inventory(paths["failed"], candidate_paths).digest,
                )
                self.assertTrue((harness.install / "references/child/SKILL.md").is_file())
                self.assertFalse((harness.install / "references/child/WORKFLOW.md").exists())
                _, state = publisher._load_state(harness.state, operation)
                self.assertEqual("ROLLED_BACK", state["status"])
                self.assertEqual(
                    publisher.MUTATION_ROLLED_BACK, state["mutation_outcome"]
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
                    identity_kind = "preflight" if point == "before_exchange" else "candidate"
                    self.assertEqual(
                        old if point == "before_exchange" else receipt["candidate_inventory"]["sha256"],
                        inspection["identities"]["live"][identity_kind],
                    )
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
            takeover_authorization=harness.takeover("recover-pre-swap"),
        )
        self.assertEqual("PUBLISHED", recovered["status"])
        self.assertEqual(
            prepared["candidate_inventory"]["sha256"], harness.old_inventory().digest
        )
        takeover_path = harness.root / "takeover-recover-pre-swap.json"
        takeover_bytes = takeover_path.read_bytes()
        with self.assertRaisesRegex(
            publisher.PublicationError,
            "collides with the takeover authorization",
        ):
            publisher.finalize_operation(
                state_root=harness.state,
                operation="recover-pre-swap",
                receipt_output=takeover_path,
                checker_runner=publisher._fake_checker,
            )
        self.assertEqual(takeover_bytes, takeover_path.read_bytes())
        takeover_path.write_bytes(takeover_bytes + b" ")
        with self.assertRaisesRegex(
            publisher.PublicationError,
            "takeover authorization drifted",
        ):
            publisher.finalize_operation(
                state_root=harness.state,
                operation="recover-pre-swap",
                checker_runner=publisher._fake_checker,
            )
        takeover_path.write_bytes(takeover_bytes)
        receipt = publisher.finalize_operation(
            state_root=harness.state,
            operation="recover-pre-swap",
            checker_runner=publisher._fake_checker,
        )
        self.assertEqual(publisher.MUTATION_PUBLISHED, receipt["mutation_outcome"])
        self.assertEqual(
            "STOPPED",
            receipt["recovery_takeover_authorization"]["authorization"][
                "owner_disposition"
            ],
        )

    def test_mutating_recovery_refuses_active_or_unknown_owner_evidence(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        operation = "recovery-owner-refusal"
        old = harness.old_inventory().digest
        harness.prepare(operation)
        harness.reserve(operation)

        def stop(point: str) -> None:
            if point == "before_exchange":
                raise publisher.InjectedFailure(point)

        with self.assertRaises(publisher.InjectedFailure):
            publisher.publish_operation(
                state_root=harness.state,
                operation=operation,
                exchanger=publisher.FakeAtomicExchanger(),
                checker_runner=publisher._fake_checker,
                failpoint=stop,
            )
        reservation_before = publisher._operation_paths(
            harness.state, operation
        )["reservation"].read_bytes()
        for label, authorization in (
            ("active", None),
            ("unknown", harness.takeover(operation, process_status="UNKNOWN")),
        ):
            with self.subTest(label=label):
                exchanger = publisher.FakeAtomicExchanger()
                with self.assertRaisesRegex(
                    publisher.PublicationError,
                    "takeover authorization|does not prove inactive",
                ):
                    publisher.recover_operation(
                        state_root=harness.state,
                        operation=operation,
                        action="complete",
                        exchanger=exchanger,
                        checker_runner=publisher._fake_checker,
                        takeover_authorization=authorization,
                    )
                self.assertEqual([], exchanger.calls)
                self.assertEqual(old, harness.old_inventory().digest)
                self.assertEqual(
                    reservation_before,
                    publisher._operation_paths(harness.state, operation)[
                        "reservation"
                    ].read_bytes(),
                )

    def test_recovery_postvalidation_failure_rolls_back_complete_generations(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        operation = "recovery-validation-rollback"
        old = harness.old_inventory().digest
        prepared = harness.prepare(operation)
        harness.reserve(operation)

        def stop(point: str) -> None:
            if point == "after_exchange":
                raise publisher.InjectedFailure(point)

        with self.assertRaises(publisher.InjectedFailure):
            publisher.publish_operation(
                state_root=harness.state,
                operation=operation,
                exchanger=publisher.FakeAtomicExchanger(),
                checker_runner=publisher._fake_checker,
                failpoint=stop,
            )

        def reject_live(source: Path, installed: Path) -> dict[str, object]:
            if installed == harness.install:
                raise publisher.ValidationFailure("recovery named negative control")
            return publisher._fake_checker(source, installed)

        with self.assertRaises(publisher.ValidationFailure):
            publisher.recover_operation(
                state_root=harness.state,
                operation=operation,
                action="complete",
                exchanger=publisher.FakeAtomicExchanger(),
                checker_runner=reject_live,
                takeover_authorization=harness.takeover(operation),
            )
        paths = publisher._operation_paths(harness.state, operation)
        self.assertEqual(old, harness.old_inventory().digest)
        self.assertEqual(
            prepared["candidate_inventory"]["sha256"],
            publisher.build_inventory(paths["failed"], harness.expected).digest,
        )
        _, state = publisher._load_state(harness.state, operation)
        self.assertEqual("ROLLED_BACK", state["status"])
        self.assertEqual(publisher.MUTATION_ROLLED_BACK, state["mutation_outcome"])
        self.assertEqual(
            sorted(harness.expected),
            state["rollback"]["restored_live_inventory"]["installed_paths"],
        )
        self.assertEqual(
            sorted(harness.expected),
            state["rollback"]["failed_generation_identity"]["installed_paths"],
        )
        receipt = publisher.finalize_operation(
            state_root=harness.state,
            operation=operation,
            checker_runner=publisher._fake_checker,
        )
        self.assertEqual(publisher.MUTATION_ROLLED_BACK, receipt["mutation_outcome"])
        self.assertEqual(
            "STOPPED",
            receipt["recovery_takeover_authorization"]["authorization"][
                "owner_disposition"
            ],
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
                takeover_authorization=harness.takeover("recovery-checker-mutation"),
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

    def test_finalize_retains_reservation_until_inventory_bound_panel_acceptance(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        old = harness.old_inventory().digest
        prepared = harness.prepare("success")
        manifest = harness.finalization_manifest("success")
        harness.reserve("success", finalization_manifest=manifest)
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
            finalization_manifest=manifest,
            checker_runner=publisher._fake_checker,
        )
        self.assertEqual("PUBLISHED", receipt["terminal_state"])
        self.assertEqual(
            "RETAINED_PENDING_PANEL_ACCEPTANCE", receipt["reservation_state"]
        )
        self.assertTrue(paths["reservation"].is_file())
        self.assertFalse(paths["released"].exists())
        _, finalized = publisher._load_state(harness.state, "success")
        self.assertEqual("FINALIZED_RESERVED", finalized["status"])
        self.assertEqual(receipt, json.loads(output.read_text(encoding="utf-8")))
        acceptance_output = harness.evidence_parent / "success/acceptance.json"
        observation = publisher.report_live_inventory(
            state_root=harness.state,
            operation="success",
            phase="acceptance",
            output=acceptance_output,
            lock_path=paths["reservation"],
        )
        self.assertEqual(
            prepared["candidate_inventory"]["sha256"],
            observation["live_inventory"]["sha256"],
        )
        release = publisher.accept_operation(
            state_root=harness.state,
            operation="success",
            acceptance_inventory_receipt=acceptance_output,
            accepted_by="panel-v3-judge",
            acceptance_reason="all required panel findings accepted",
            lock_path=paths["reservation"],
            finalization_manifest=manifest,
        )
        self.assertEqual("RELEASED_AFTER_PANEL_ACCEPTANCE", release["reservation_state"])
        self.assertFalse(paths["reservation"].exists())
        self.assertTrue(paths["released"].is_file())
        _, accepted = publisher._load_state(harness.state, "success")
        self.assertEqual("ACCEPTED", accepted["status"])
        self.assertEqual(
            release,
            publisher.accept_operation(
                state_root=harness.state,
                operation="success",
                acceptance_inventory_receipt=acceptance_output,
                accepted_by="panel-v3-judge",
                acceptance_reason="all required panel findings accepted",
                lock_path=paths["reservation"],
                finalization_manifest=manifest,
            ),
        )
        with self.assertRaisesRegex(publisher.PublicationError, "not bound"):
            publisher.accept_operation(
                state_root=harness.state,
                operation="success",
                acceptance_inventory_receipt=acceptance_output,
                accepted_by="panel-v3-judge",
                acceptance_reason="different reason",
                lock_path=paths["reservation"],
                finalization_manifest=manifest,
            )
        records = [json.loads(line) for line in manifest.read_text().splitlines()]
        self.assertEqual(
            [
                "controller_started",
                "installed_publication_reservation_intent",
                "installed_publication_terminal",
                "installed_publication_accepted",
            ],
            [record["record_type"] for record in records],
        )

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

    def test_live_inventory_gate_requires_terminal_lock_and_new_evidence_path(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        operation = "inventory-gate"
        manifest = harness.finalization_manifest(operation)
        harness.prepare(operation)
        harness.reserve(operation, finalization_manifest=manifest)
        lock = harness.state / "package.lock"
        output = harness.evidence_parent / operation / "dispatch.json"
        with self.assertRaisesRegex(
            publisher.PublicationError, "requires terminal validation"
        ):
            publisher.report_live_inventory(
                state_root=harness.state,
                operation=operation,
                phase="dispatch",
                output=output,
                lock_path=lock,
            )
        publisher.publish_operation(
            state_root=harness.state,
            operation=operation,
            exchanger=publisher.FakeAtomicExchanger(),
            checker_runner=publisher._fake_checker,
        )
        publisher.finalize_operation(
            state_root=harness.state,
            operation=operation,
            finalization_manifest=manifest,
            checker_runner=publisher._fake_checker,
        )
        with self.assertRaisesRegex(publisher.PublicationError, "fixed operation-independent"):
            publisher.report_live_inventory(
                state_root=harness.state,
                operation=operation,
                phase="dispatch",
                output=output,
                lock_path=harness.root / "wrong.lock",
            )
        receipt = publisher.report_live_inventory(
            state_root=harness.state,
            operation=operation,
            phase="dispatch",
            output=output,
            lock_path=lock,
        )
        prefix = receipt["finalization_manifest_prefix"]
        manifest_bytes = manifest.read_bytes()
        self.assertEqual(len(manifest_bytes), prefix["prefix_bytes"])
        self.assertEqual(hashlib.sha256(manifest_bytes).hexdigest(), prefix["prefix_sha256"])
        self.assertEqual(
            publisher.build_inventory(harness.install, harness.expected).data,
            Path(receipt["live_inventory"]["path"]).read_bytes(),
        )
        with self.assertRaisesRegex(publisher.PublicationError, "already exists"):
            publisher.report_live_inventory(
                state_root=harness.state,
                operation=operation,
                phase="dispatch",
                output=output,
                lock_path=lock,
            )
        self.assertTrue(lock.is_file())

    def test_acceptance_refuses_live_or_manifest_drift_and_retains_reservation(self) -> None:
        for drift in ("live", "manifest"):
            with self.subTest(drift=drift):
                temporary, harness = self.make_harness()
                try:
                    operation = f"acceptance-{drift}"
                    manifest = harness.finalization_manifest(operation)
                    harness.prepare(operation)
                    harness.reserve(operation, finalization_manifest=manifest)
                    publisher.publish_operation(
                        state_root=harness.state,
                        operation=operation,
                        exchanger=publisher.FakeAtomicExchanger(),
                        checker_runner=publisher._fake_checker,
                    )
                    publisher.finalize_operation(
                        state_root=harness.state,
                        operation=operation,
                        finalization_manifest=manifest,
                        checker_runner=publisher._fake_checker,
                    )
                    paths = publisher._operation_paths(harness.state, operation)
                    observation = harness.evidence_parent / operation / "acceptance.json"
                    publisher.report_live_inventory(
                        state_root=harness.state,
                        operation=operation,
                        phase="acceptance",
                        output=observation,
                        lock_path=paths["reservation"],
                    )
                    if drift == "live":
                        (harness.install / "SKILL.md").write_text(
                            "post-observation drift\n", encoding="utf-8"
                        )
                        message = "live generation drifted"
                    else:
                        publisher._append_finalization_record(
                            manifest,
                            record_type="external_panel_note",
                            payload={
                                "operation_id": operation,
                                "generation_id": operation,
                                "note": "manifest-prefix negative control",
                            },
                        )
                        message = "manifest changed"
                    with self.assertRaisesRegex(publisher.PublicationError, message):
                        publisher.accept_operation(
                            state_root=harness.state,
                            operation=operation,
                            acceptance_inventory_receipt=observation,
                            accepted_by="panel-v3-judge",
                            acceptance_reason="negative control must not release",
                            lock_path=paths["reservation"],
                            finalization_manifest=manifest,
                        )
                    self.assertTrue(paths["reservation"].is_file())
                    self.assertFalse(paths["released"].exists())
                    _, state = publisher._load_state(harness.state, operation)
                    self.assertEqual("FINALIZED_RESERVED", state["status"])
                finally:
                    temporary.cleanup()

    def test_acceptance_retry_reuses_exact_manifest_record_before_release(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        operation = "acceptance-retry"
        manifest = harness.finalization_manifest(operation)
        harness.prepare(operation)
        harness.reserve(operation, finalization_manifest=manifest)
        publisher.publish_operation(
            state_root=harness.state,
            operation=operation,
            exchanger=publisher.FakeAtomicExchanger(),
            checker_runner=publisher._fake_checker,
        )
        publisher.finalize_operation(
            state_root=harness.state,
            operation=operation,
            finalization_manifest=manifest,
            checker_runner=publisher._fake_checker,
        )
        paths = publisher._operation_paths(harness.state, operation)
        observation = harness.evidence_parent / operation / "acceptance.json"
        publisher.report_live_inventory(
            state_root=harness.state,
            operation=operation,
            phase="acceptance",
            output=observation,
            lock_path=paths["reservation"],
        )
        with mock.patch.object(
            publisher,
            "_write_once_or_verify_json",
            side_effect=publisher.InjectedFailure("after acceptance journal append"),
        ):
            with self.assertRaises(publisher.InjectedFailure):
                publisher.accept_operation(
                    state_root=harness.state,
                    operation=operation,
                    acceptance_inventory_receipt=observation,
                    accepted_by="panel-v3-judge",
                    acceptance_reason="retry durability control",
                    lock_path=paths["reservation"],
                    finalization_manifest=manifest,
                )
        self.assertTrue(paths["reservation"].is_file())
        _, pending = publisher._load_state(harness.state, operation)
        self.assertEqual("ACCEPTANCE_PENDING", pending["status"])
        release = publisher.accept_operation(
            state_root=harness.state,
            operation=operation,
            acceptance_inventory_receipt=observation,
            accepted_by="panel-v3-judge",
            acceptance_reason="retry durability control",
            lock_path=paths["reservation"],
            finalization_manifest=manifest,
        )
        self.assertEqual("RELEASED_AFTER_PANEL_ACCEPTANCE", release["reservation_state"])
        records = [json.loads(line) for line in manifest.read_text().splitlines()]
        self.assertEqual(
            1,
            sum(
                record["record_type"] == "installed_publication_accepted"
                for record in records
            ),
        )

    def test_acceptance_allows_and_binds_valid_manifest_suffix_interleaving(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        operation = "acceptance-interleaving"
        manifest = harness.finalization_manifest(operation)
        harness.prepare(operation)
        harness.reserve(operation, finalization_manifest=manifest)
        publisher.publish_operation(
            state_root=harness.state,
            operation=operation,
            exchanger=publisher.FakeAtomicExchanger(),
            checker_runner=publisher._fake_checker,
        )
        publisher.finalize_operation(
            state_root=harness.state,
            operation=operation,
            finalization_manifest=manifest,
            checker_runner=publisher._fake_checker,
        )
        paths = publisher._operation_paths(harness.state, operation)
        observation = harness.evidence_parent / operation / "acceptance.json"
        publisher.report_live_inventory(
            state_root=harness.state,
            operation=operation,
            phase="acceptance",
            output=observation,
            lock_path=paths["reservation"],
        )
        original_persist = publisher._persist_state
        interleaved = False

        def append_after_acceptance_pending(
            path: Path, state: dict[str, object], status: str | None = None
        ) -> None:
            nonlocal interleaved
            original_persist(path, state, status)
            if status == "ACCEPTANCE_PENDING" and not interleaved:
                interleaved = True
                publisher._append_finalization_record(
                    manifest,
                    record_type="external_panel_note",
                    payload={
                        "operation_id": "other-operation",
                        "generation_id": "other-generation",
                        "note": "valid suffix interleaving",
                    },
                )

        with mock.patch.object(
            publisher, "_persist_state", append_after_acceptance_pending
        ):
            release = publisher.accept_operation(
                state_root=harness.state,
                operation=operation,
                acceptance_inventory_receipt=observation,
                accepted_by="panel-v3-judge",
                acceptance_reason="bounded suffix interleaving control",
                lock_path=paths["reservation"],
                finalization_manifest=manifest,
            )
        records = [json.loads(line) for line in manifest.read_text().splitlines()]
        self.assertEqual(
            ["external_panel_note", "installed_publication_accepted"],
            [record["record_type"] for record in records[-2:]],
        )
        accepted = records[-1]
        predecessor = accepted["acceptance_manifest_predecessor_prefix"]
        self.assertEqual("external_panel_note", predecessor["last_record_type"])
        self.assertEqual(records[-2]["sequence"], predecessor["last_sequence"])
        self.assertEqual(
            release["finalization_manifest"]["prefix_sha256"],
            hashlib.sha256(manifest.read_bytes()).hexdigest(),
        )
        self.assertFalse(paths["reservation"].exists())

    def test_manifest_truncation_cannot_be_rebound_by_acceptance_observation(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        operation = "manifest-chain"
        manifest = harness.finalization_manifest(operation)
        controller_prefix = manifest.read_bytes()
        harness.prepare(operation)
        harness.reserve(operation, finalization_manifest=manifest)
        publisher.publish_operation(
            state_root=harness.state,
            operation=operation,
            exchanger=publisher.FakeAtomicExchanger(),
            checker_runner=publisher._fake_checker,
        )
        publisher.finalize_operation(
            state_root=harness.state,
            operation=operation,
            finalization_manifest=manifest,
            checker_runner=publisher._fake_checker,
        )
        manifest.write_bytes(controller_prefix)
        paths = publisher._operation_paths(harness.state, operation)
        output = harness.evidence_parent / operation / "acceptance-after-truncation.json"
        with self.assertRaisesRegex(
            publisher.PublicationError, "required .*prefix"
        ):
            publisher.report_live_inventory(
                state_root=harness.state,
                operation=operation,
                phase="acceptance",
                output=output,
                lock_path=paths["reservation"],
            )
        self.assertFalse(output.exists())
        self.assertTrue(paths["reservation"].is_file())
        _, state = publisher._load_state(harness.state, operation)
        self.assertEqual("FINALIZED_RESERVED", state["status"])

    def test_mutated_reservation_cannot_be_rebound_or_released(self) -> None:
        for field in ("generation_id", "candidate_inventory_sha256"):
            with self.subTest(field=field):
                temporary, harness = self.make_harness()
                try:
                    operation = f"reservation-drift-{field.replace('_', '-')}"
                    manifest = harness.finalization_manifest(operation)
                    harness.prepare(operation)
                    harness.reserve(operation, finalization_manifest=manifest)
                    publisher.publish_operation(
                        state_root=harness.state,
                        operation=operation,
                        exchanger=publisher.FakeAtomicExchanger(),
                        checker_runner=publisher._fake_checker,
                    )
                    publisher.finalize_operation(
                        state_root=harness.state,
                        operation=operation,
                        finalization_manifest=manifest,
                        checker_runner=publisher._fake_checker,
                    )
                    paths = publisher._operation_paths(harness.state, operation)
                    reservation = json.loads(
                        paths["reservation"].read_text(encoding="utf-8")
                    )
                    reservation[field] = "0" * 64
                    paths["reservation"].write_text(
                        json.dumps(reservation, sort_keys=True, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(
                        publisher.PublicationError, "differs from the exact reservation"
                    ):
                        publisher.report_live_inventory(
                            state_root=harness.state,
                            operation=operation,
                            phase="acceptance",
                            output=harness.evidence_parent / operation / "acceptance.json",
                            lock_path=paths["reservation"],
                        )
                    self.assertTrue(paths["reservation"].is_file())
                    self.assertFalse(paths["released"].exists())
                finally:
                    temporary.cleanup()

    def test_acceptance_live_race_retains_exact_reservation(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        operation = "acceptance-live-race"
        manifest = harness.finalization_manifest(operation)
        harness.prepare(operation)
        harness.reserve(operation, finalization_manifest=manifest)
        publisher.publish_operation(
            state_root=harness.state,
            operation=operation,
            exchanger=publisher.FakeAtomicExchanger(),
            checker_runner=publisher._fake_checker,
        )
        publisher.finalize_operation(
            state_root=harness.state,
            operation=operation,
            finalization_manifest=manifest,
            checker_runner=publisher._fake_checker,
        )
        paths = publisher._operation_paths(harness.state, operation)
        observation = harness.evidence_parent / operation / "acceptance.json"
        publisher.report_live_inventory(
            state_root=harness.state,
            operation=operation,
            phase="acceptance",
            output=observation,
            lock_path=paths["reservation"],
        )
        original_write = publisher._write_once_or_verify_json

        def mutate_after_release_record(
            path: Path, value: dict[str, object], *, label: str
        ) -> None:
            original_write(path, value, label=label)
            if path == paths["released"]:
                (harness.install / "SKILL.md").write_text(
                    "changed after acceptance validation\n", encoding="utf-8"
                )

        with mock.patch.object(
            publisher, "_write_once_or_verify_json", mutate_after_release_record
        ):
            with self.assertRaisesRegex(
                publisher.PublicationError, "live generation drifted"
            ):
                publisher.accept_operation(
                    state_root=harness.state,
                    operation=operation,
                    acceptance_inventory_receipt=observation,
                    accepted_by="panel-v3-judge",
                    acceptance_reason="race negative control",
                    lock_path=paths["reservation"],
                    finalization_manifest=manifest,
                )
        self.assertTrue(paths["reservation"].is_file())
        _, state = publisher._load_state(harness.state, operation)
        self.assertEqual("ACCEPTANCE_PENDING", state["status"])

    def test_pending_release_retry_refuses_mutated_reservation(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        operation = "pending-release-lock-drift"
        manifest = harness.finalization_manifest(operation)
        harness.prepare(operation)
        harness.reserve(operation, finalization_manifest=manifest)
        publisher.publish_operation(
            state_root=harness.state,
            operation=operation,
            exchanger=publisher.FakeAtomicExchanger(),
            checker_runner=publisher._fake_checker,
        )
        publisher.finalize_operation(
            state_root=harness.state,
            operation=operation,
            finalization_manifest=manifest,
            checker_runner=publisher._fake_checker,
        )
        paths = publisher._operation_paths(harness.state, operation)
        observation = harness.evidence_parent / operation / "acceptance.json"
        publisher.report_live_inventory(
            state_root=harness.state,
            operation=operation,
            phase="acceptance",
            output=observation,
            lock_path=paths["reservation"],
        )
        with mock.patch.object(
            publisher.os, "unlink", side_effect=OSError("crash before release unlink")
        ):
            with self.assertRaises(OSError):
                publisher.accept_operation(
                    state_root=harness.state,
                    operation=operation,
                    acceptance_inventory_receipt=observation,
                    accepted_by="panel-v3-judge",
                    acceptance_reason="pending release control",
                    lock_path=paths["reservation"],
                    finalization_manifest=manifest,
                )
        _, pending = publisher._load_state(harness.state, operation)
        self.assertEqual("ACCEPTED_RELEASE_PENDING", pending["status"])
        reservation = json.loads(paths["reservation"].read_text(encoding="utf-8"))
        reservation["candidate_inventory_sha256"] = "0" * 64
        paths["reservation"].write_text(
            json.dumps(reservation, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            publisher.PublicationError, "differs from the exact reservation"
        ):
            publisher.accept_operation(
                state_root=harness.state,
                operation=operation,
                acceptance_inventory_receipt=observation,
                accepted_by="panel-v3-judge",
                acceptance_reason="pending release control",
                lock_path=paths["reservation"],
                finalization_manifest=manifest,
            )
        self.assertTrue(paths["reservation"].is_file())

    def test_post_unlink_live_drift_restores_exact_reservation(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        operation = "post-unlink-live-drift"
        manifest = harness.finalization_manifest(operation)
        harness.prepare(operation)
        harness.reserve(operation, finalization_manifest=manifest)
        publisher.publish_operation(
            state_root=harness.state,
            operation=operation,
            exchanger=publisher.FakeAtomicExchanger(),
            checker_runner=publisher._fake_checker,
        )
        publisher.finalize_operation(
            state_root=harness.state,
            operation=operation,
            finalization_manifest=manifest,
            checker_runner=publisher._fake_checker,
        )
        paths = publisher._operation_paths(harness.state, operation)
        reservation_before = paths["reservation"].read_bytes()
        observation = harness.evidence_parent / operation / "acceptance.json"
        publisher.report_live_inventory(
            state_root=harness.state,
            operation=operation,
            phase="acceptance",
            output=observation,
            lock_path=paths["reservation"],
        )
        original_unlink = publisher.os.unlink

        def mutate_after_unlink(path: object, *args: object, **kwargs: object) -> None:
            original_unlink(path, *args, **kwargs)
            if Path(path) == paths["reservation"]:
                (harness.install / "SKILL.md").write_text(
                    "changed during release unlink\n", encoding="utf-8"
                )

        with mock.patch.object(publisher.os, "unlink", mutate_after_unlink):
            with self.assertRaisesRegex(
                publisher.PublicationError, "reservation was restored"
            ):
                publisher.accept_operation(
                    state_root=harness.state,
                    operation=operation,
                    acceptance_inventory_receipt=observation,
                    accepted_by="panel-v3-judge",
                    acceptance_reason="post unlink negative control",
                    lock_path=paths["reservation"],
                    finalization_manifest=manifest,
                )
        self.assertEqual(reservation_before, paths["reservation"].read_bytes())
        _, state = publisher._load_state(harness.state, operation)
        self.assertEqual("ACCEPTED_RELEASE_PENDING", state["status"])

    def test_post_unlink_process_crash_completes_from_durable_acceptance(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        operation = "post-unlink-process-crash"
        manifest = harness.finalization_manifest(operation)
        harness.prepare(operation)
        harness.reserve(operation, finalization_manifest=manifest)
        publisher.publish_operation(
            state_root=harness.state,
            operation=operation,
            exchanger=publisher.FakeAtomicExchanger(),
            checker_runner=publisher._fake_checker,
        )
        publisher.finalize_operation(
            state_root=harness.state,
            operation=operation,
            finalization_manifest=manifest,
            checker_runner=publisher._fake_checker,
        )
        paths = publisher._operation_paths(harness.state, operation)
        observation = harness.evidence_parent / operation / "acceptance.json"
        publisher.report_live_inventory(
            state_root=harness.state,
            operation=operation,
            phase="acceptance",
            output=observation,
            lock_path=paths["reservation"],
        )
        arguments = {
            "state_root": harness.state,
            "operation": operation,
            "acceptance_inventory_receipt": observation,
            "accepted_by": "panel-v3-judge",
            "acceptance_reason": "post unlink crash control",
            "lock_path": paths["reservation"],
            "finalization_manifest": manifest,
        }
        with mock.patch.object(
            publisher.os, "unlink", side_effect=OSError("stop before modeled unlink")
        ):
            with self.assertRaises(OSError):
                publisher.accept_operation(**arguments)
        _, pending = publisher._load_state(harness.state, operation)
        self.assertEqual("ACCEPTED_RELEASE_PENDING", pending["status"])
        contradictory_manifest = harness.finalization_manifest(
            "contradictory-retry-manifest"
        )
        contradictory_arguments = {
            **arguments,
            "finalization_manifest": contradictory_manifest,
        }
        with self.assertRaisesRegex(publisher.PublicationError, "not bound"):
            publisher.accept_operation(**contradictory_arguments)
        self.assertTrue(paths["reservation"].is_file())
        paths["reservation"].unlink()
        release = publisher.accept_operation(**arguments)
        self.assertEqual("RELEASED_AFTER_PANEL_ACCEPTANCE", release["reservation_state"])
        _, accepted = publisher._load_state(harness.state, operation)
        self.assertEqual("ACCEPTED", accepted["status"])

    def test_terminal_manifest_append_crash_is_deterministically_retryable(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        operation = "terminal-append-crash"
        manifest = harness.finalization_manifest(operation)
        harness.prepare(operation)
        harness.reserve(operation, finalization_manifest=manifest)
        publisher.publish_operation(
            state_root=harness.state,
            operation=operation,
            exchanger=publisher.FakeAtomicExchanger(),
            checker_runner=publisher._fake_checker,
        )
        original_persist = publisher._persist_state

        def crash_after_terminal_append(
            path: Path, state: dict[str, object], status: str | None = None
        ) -> None:
            if status == "FINALIZED_RESERVED":
                raise publisher.InjectedFailure("after terminal append")
            original_persist(path, state, status)

        with mock.patch.object(
            publisher, "_persist_state", crash_after_terminal_append
        ):
            with self.assertRaises(publisher.InjectedFailure):
                publisher.finalize_operation(
                    state_root=harness.state,
                    operation=operation,
                    finalization_manifest=manifest,
                    checker_runner=publisher._fake_checker,
                )
        _, pending = publisher._load_state(harness.state, operation)
        self.assertEqual("FINALIZING", pending["status"])
        first_receipt = pending["pending_terminal_finalization"]["receipt"]
        recovered = publisher.finalize_operation(
            state_root=harness.state,
            operation=operation,
            finalization_manifest=manifest,
            checker_runner=publisher._fake_checker,
        )
        self.assertEqual(first_receipt, recovered)
        records = [json.loads(line) for line in manifest.read_text().splitlines()]
        self.assertEqual(
            1,
            sum(
                record["record_type"] == "installed_publication_terminal"
                for record in records
            ),
        )
        self.assertTrue(
            publisher._operation_paths(harness.state, operation)["reservation"].is_file()
        )

    def test_inventory_sidecar_postwrite_drift_is_not_journaled(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        operation = "inventory-sidecar-drift"
        manifest = harness.finalization_manifest(operation)
        harness.prepare(operation)
        harness.reserve(operation, finalization_manifest=manifest)
        publisher.publish_operation(
            state_root=harness.state,
            operation=operation,
            exchanger=publisher.FakeAtomicExchanger(),
            checker_runner=publisher._fake_checker,
        )
        publisher.finalize_operation(
            state_root=harness.state,
            operation=operation,
            finalization_manifest=manifest,
            checker_runner=publisher._fake_checker,
        )
        paths = publisher._operation_paths(harness.state, operation)
        output = harness.evidence_parent / operation / "dispatch.json"
        original_write = publisher._write_new_file

        def corrupt_inventory(path: Path, data: bytes, mode: int = 0o600) -> None:
            original_write(path, data, mode)
            if path.name == "dispatch.json.inventory":
                path.write_bytes(data + b"x")

        with mock.patch.object(publisher, "_write_new_file", corrupt_inventory):
            with self.assertRaises(publisher.PublicationError):
                publisher.report_live_inventory(
                    state_root=harness.state,
                    operation=operation,
                    phase="dispatch",
                    output=output,
                    lock_path=paths["reservation"],
                )
        self.assertFalse(output.exists())
        _, state = publisher._load_state(harness.state, operation)
        self.assertFalse(state.get("live_inventory_reports"))
        self.assertTrue(paths["reservation"].is_file())

    def test_inventory_live_postwrite_drift_is_not_journaled(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        operation = "inventory-live-drift"
        manifest = harness.finalization_manifest(operation)
        harness.prepare(operation)
        harness.reserve(operation, finalization_manifest=manifest)
        publisher.publish_operation(
            state_root=harness.state,
            operation=operation,
            exchanger=publisher.FakeAtomicExchanger(),
            checker_runner=publisher._fake_checker,
        )
        publisher.finalize_operation(
            state_root=harness.state,
            operation=operation,
            finalization_manifest=manifest,
            checker_runner=publisher._fake_checker,
        )
        paths = publisher._operation_paths(harness.state, operation)
        output = harness.evidence_parent / operation / "judgment.json"
        original_write = publisher._write_new_file

        def mutate_live(path: Path, data: bytes, mode: int = 0o600) -> None:
            original_write(path, data, mode)
            if path == output:
                (harness.install / "SKILL.md").write_text(
                    "changed during judgment report\n", encoding="utf-8"
                )

        with mock.patch.object(publisher, "_write_new_file", mutate_live):
            with self.assertRaisesRegex(
                publisher.PublicationError, "identity changed"
            ):
                publisher.report_live_inventory(
                    state_root=harness.state,
                    operation=operation,
                    phase="judgment",
                    output=output,
                    lock_path=paths["reservation"],
                )
        _, state = publisher._load_state(harness.state, operation)
        self.assertFalse(state.get("live_inventory_reports"))
        self.assertTrue(paths["reservation"].is_file())

    def test_finalize_refuses_preexisting_unrelated_receipt_output(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        operation = "unrelated-receipt-output"
        harness.prepare(operation)
        harness.reserve(operation)
        publisher.publish_operation(
            state_root=harness.state,
            operation=operation,
            exchanger=publisher.FakeAtomicExchanger(),
            checker_runner=publisher._fake_checker,
        )
        output = harness.root / "outside/important.json"
        output.parent.mkdir()
        output.write_bytes(b"KEEP\n")
        with self.assertRaisesRegex(publisher.PublicationError, "already exists"):
            publisher.finalize_operation(
                state_root=harness.state,
                operation=operation,
                receipt_output=output,
                checker_runner=publisher._fake_checker,
            )
        self.assertEqual(b"KEEP\n", output.read_bytes())
        paths = publisher._operation_paths(harness.state, operation)
        self.assertTrue(paths["reservation"].is_file())
        _, state = publisher._load_state(harness.state, operation)
        self.assertEqual("PUBLISHED", state["status"])

    def test_finalize_checker_live_mutation_cannot_write_terminal_receipt(self) -> None:
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
            publisher.PublicationError, "terminal checker changed"
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

    def test_reserve_rejects_hard_linked_manifest_without_mutating_receipt_or_state(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        operation = "manifest-receipt-hard-link"
        harness.prepare(operation)
        paths, state = publisher._load_state(harness.state, operation)
        maintenance = harness.maintenance(operation)
        maintenance_value = json.loads(maintenance.read_text(encoding="utf-8"))
        maintenance_value.update(
            {
                "sequence": 1,
                "recorded_at": "2026-08-09T00:00:00Z",
                "record_type": "controller_started",
                "finalization_id": "hard-link-finalization",
            }
        )
        maintenance.write_text(
            json.dumps(maintenance_value, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        manifest = harness.root / "controller/hard-linked-finalization.jsonl"
        manifest.parent.mkdir()
        os.link(maintenance, manifest)
        maintenance_before = maintenance.read_bytes()
        state_before = paths["state"].read_bytes()

        with self.assertRaisesRegex(
            publisher.PublicationError, "exactly one hard link"
        ):
            publisher.reserve_operation(
                state_root=harness.state,
                operation=operation,
                maintenance_receipt=maintenance,
                lock_path=paths["reservation"],
                prepare_receipt=Path(state["prepare_receipt"]["path"]),
                finalization_manifest=manifest,
            )

        self.assertEqual(maintenance_before, maintenance.read_bytes())
        self.assertEqual(maintenance_before, manifest.read_bytes())
        self.assertEqual(state_before, paths["state"].read_bytes())
        self.assertFalse(paths["reservation"].exists())

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
                takeover_authorization=harness.takeover("malformed"),
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
                            "--expected-live-source-commit",
                            harness.predecessor_commit,
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
                self.assertTrue(lock.is_file())
                phase_outputs: dict[str, Path] = {}
                for phase in ("dispatch", "judgment", "acceptance"):
                    phase_output = evidence / f"{phase}-live.json"
                    phase_outputs[phase] = phase_output
                    self.assertEqual(
                        0,
                        publisher.main(
                            [
                                "inventory",
                                "--operation",
                                operation,
                                "--state-root",
                                str(harness.state),
                                "--lock",
                                str(lock),
                                "--phase",
                                phase,
                                "--output",
                                str(phase_output),
                            ]
                        ),
                    )
                self.assertEqual(
                    0,
                    publisher.main(
                        [
                            "accept",
                            "--operation",
                            operation,
                            "--state-root",
                            str(harness.state),
                            "--lock",
                            str(lock),
                            "--finalization-manifest",
                            str(manifest),
                            "--acceptance-inventory-receipt",
                            str(phase_outputs["acceptance"]),
                            "--accepted-by",
                            "panel-v3-judge",
                            "--acceptance-reason",
                            "required checks accepted",
                        ]
                    ),
                )
            records = [
                json.loads(line)
                for line in manifest.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([1, 2, 3, 4], [record["sequence"] for record in records])
            self.assertEqual(
                [
                    "controller_started",
                    "installed_publication_reservation_intent",
                    "installed_publication_terminal",
                    "installed_publication_accepted",
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
