#!/usr/bin/env python3
"""Focused, live-safe tests for the whole-directory Codex publisher."""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
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
import install_inventory as inventory_codec  # noqa: E402
import finalization_manifest as finalization  # noqa: E402
import publish_codex_install as publisher  # noqa: E402
import check_large_queue_guidance as guidance  # noqa: E402
import validate_panel_inputs as panel_validator  # noqa: E402


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

    def maintenance(self, operation: str, *, label: str | None = None) -> Path:
        path = self.root / f"maintenance-{label or operation}.json"
        now = publisher._utc_datetime_now().replace(microsecond=0)
        checked_offset = timedelta(seconds=-5 if label is not None else -10)

        def stamp(offset: timedelta) -> str:
            return (now + offset).isoformat().replace("+00:00", "Z")

        path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "record_type": "external_reader_quiescence_attestation",
                    "operation_id": operation,
                    "authorized_by": "release-owner",
                    "maintenance_window": {
                        "id": f"window-{label or 'original'}",
                        "starts_at": stamp(timedelta(minutes=-1)),
                        "ends_at": stamp(timedelta(minutes=10)),
                    },
                    "known_reader_inventory": {
                        "scope": "all-known-codex-skill-readers",
                        "method": "controller process and tool-session inventory",
                        "evidence_reference": (
                            f"reader-inventory-fixture-{label or 'original'}"
                        ),
                        "inventory_complete": True,
                        "known_reader_count": 0,
                        "known_active_reader_count": 0,
                        "unknown_reader_policy": "STOP_IF_UNKNOWN",
                        "unknown_reader_status": "NONE_OBSERVED",
                        "checked_at": stamp(checked_offset),
                        "expires_at": stamp(timedelta(minutes=5)),
                    },
                    "publisher_validation_scope": (
                        "publisher-validates-recorded-external-claim-not-unknowable-world-truth"
                    ),
                    "controller": {
                        "id": "controller-1",
                        "state": "ACTIVE",
                        "owner": {
                            "host": "test-host",
                            "pid": 4242,
                            "process_start_identity": "test-process-start-1",
                        },
                    },
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def report_inventory_chain(
        self, operation: str, *, through: str = "acceptance"
    ) -> dict[str, Path]:
        paths = publisher._operation_paths(self.state, operation)
        outputs: dict[str, Path] = {}
        for phase in publisher.LIVE_INVENTORY_PHASE_ORDER:
            self.seal_phase(operation, phase)
            output = self.evidence_parent / operation / f"{phase}.json"
            publisher.report_live_inventory(
                state_root=self.state,
                operation=operation,
                phase=phase,
                output=output,
                lock_path=paths["reservation"],
            )
            outputs[phase] = output
            if phase == through:
                break
        return outputs

    def seal_phase(self, operation: str, phase: str) -> dict[str, object]:
        _, state = publisher._load_state(self.state, operation)
        terminal = state.get("finalization_manifest_terminal")
        if not isinstance(terminal, dict) or not isinstance(terminal.get("path"), str):
            raise AssertionError("fixture operation lacks terminal finalization manifest")
        manifest_path = Path(terminal["path"])
        records = [
            json.loads(line)
            for line in manifest_path.read_text(encoding="utf-8").splitlines()
        ]
        writer = records[0]["writer_controller_id"]
        review_id = f"review-{operation}"
        raw_inventory_path = manifest_path.parent / f"{operation}-raw-input.inventory"
        raw_root = manifest_path.parent / "raw-inputs"
        raw_root.mkdir(exist_ok=True)
        raw_member = raw_root / f"{operation}.txt"
        raw_member.write_bytes(f"raw-input-{operation}".encode())
        raw_inventory_path.write_text(
            f"{hashlib.sha256(raw_member.read_bytes()).hexdigest()}\t"
            f"{len(raw_member.read_bytes())}\t{operation}.txt\n",
            encoding="utf-8",
        )
        raw_digest = hashlib.sha256(raw_inventory_path.read_bytes()).hexdigest()
        finalization.append_finalization_record(
            manifest_path,
            record_type="raw_input_registered",
            writer_controller_id=writer,
            payload={
                "review_id": review_id,
                "review_boundary": "postpublication-installed-snapshot",
                "inventory_path": str(raw_inventory_path),
                "raw_input_inventory_sha256": raw_digest,
                "raw_input_max_files": 1000,
                "raw_input_max_total_bytes": 100000000,
                "raw_input_actual_files": 1,
                "raw_input_actual_total_bytes": len(raw_member.read_bytes()),
                "state": "REGISTERED",
                "next_action": "dispatch panel",
            },
        )
        if phase != "dispatch":
            for record_type, role, suffix in (
                ("review_report_registered", "reviewer-1", "report"),
                ("challenge_response_registered", "reviewer-1", "challenge"),
                ("judge_verdict_registered", "judge-1", "judge"),
            ):
                if any(
                    record.get("record_type") == record_type
                    and record.get("review_id") == review_id
                    for record in records
                ):
                    continue
                artifact_path = manifest_path.parent / f"{operation}-{suffix}.md"
                if record_type == "judge_verdict_registered":
                    current_records = [
                        json.loads(line)
                        for line in manifest_path.read_text(encoding="utf-8").splitlines()
                    ]
                    dispatch = next(
                        record
                        for record in current_records
                        if record["record_type"] == "manifest_prefix_registered"
                        and record["review_id"] == review_id
                        and record["phase"] == "dispatch"
                    )
                    artifact_path = manifest_path.parent / f"{operation}-{suffix}.json"
                    artifact_path.write_text(
                        json.dumps(
                            {
                                "schema_version": 1,
                                "record_type": finalization.JUDGE_RECEIPT_TYPE,
                                "review_id": review_id,
                                "raw_input_inventory_sha256": raw_digest,
                                "dispatch_manifest_prefix_sha256": dispatch[
                                    "manifest_prefix_sha256"
                                ],
                                **finalization.judgment_input_identity(current_records),
                                "judge_role": role,
                                "verdict": "ACCEPT",
                                "recorded_at": "2026-08-09T00:00:03Z",
                                "findings_unresolved": 0,
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                else:
                    artifact_path.write_bytes(f"{operation}-{suffix}".encode())
                payload = {
                    "review_id": review_id,
                    "path": str(artifact_path),
                    "sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
                    "state": "RECEIVED",
                    "next_action": "continue panel",
                }
                if record_type == "review_report_registered":
                    payload["reviewer_role"] = role
                elif record_type == "challenge_response_registered":
                    payload["participant_role"] = role
                else:
                    payload["judge_role"] = role
                    payload["verdict"] = "ACCEPT"
                finalization.append_finalization_record(
                    manifest_path,
                    record_type=record_type,
                    writer_controller_id=writer,
                    payload=payload,
                )
        if phase == "acceptance":
            finalization.append_finalization_record(
                manifest_path,
                record_type="review_summary",
                writer_controller_id=writer,
                payload={
                    "review_id": review_id,
                    "raw_input_inventory_sha256": raw_digest,
                    "independent_reports_expected": 1,
                    "independent_reports_received": 1,
                    "challenge_participants_expected": 1,
                    "challenge_participants_received": 1,
                    "findings_received": 1,
                    "findings_answered": 1,
                    "findings_unresolved": 0,
                    "judge_reports_expected": 1,
                    "judge_reports_received": 1,
                    "state": "REVIEW_COMPLETE",
                    "next_action": "accept installation",
                },
            )
        evidence = manifest_path.parent / f"{operation}-panel-prefixes"
        evidence.mkdir(exist_ok=True)
        return finalization.seal_manifest_prefix(
            manifest_path,
            writer_controller_id=writer,
            review_id=review_id,
            phase=phase,
            raw_input_inventory_sha256=raw_digest,
            prefix_output=evidence / f"manifest-prefix.{phase}.jsonl",
            receipt_output=evidence / f"manifest-prefix.{phase}.json",
        )

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
            prepare_path = Path(state["prepare_receipt"]["path"])
            finalization.append_finalization_record(
                finalization_manifest,
                record_type="prepare_receipt_registered",
                writer_controller_id=f"controller-{operation}",
                payload={
                    "operation_id": operation,
                    "generation_id": state["generation_id"],
                    "receipt_path": str(prepare_path),
                    "receipt_sha256": state["prepare_receipt"]["sha256"],
                    "mutation_outcome": "NO_LIVE_MUTATION_PREPARED",
                    "state": "PREPARED",
                    "next_action": "reserve when separately authorized",
                },
            )
            arguments.update(
                {
                    "lock_path": self.state / "package.lock",
                    "prepare_receipt": prepare_path,
                    "finalization_manifest": finalization_manifest,
                }
            )
        return publisher.reserve_operation(  # type: ignore[arg-type]
            **arguments
        )

    def finalization_manifest(self, operation: str) -> Path:
        path = self.root / f"controller/{operation}-finalization.jsonl"
        path.parent.mkdir(exist_ok=True)
        finalization.initialize_manifest(
            path,
            finalization_id=f"finalization-{operation}",
            writer_controller_id=f"controller-{operation}",
            state="PREPARING",
            recorded_at="2026-08-09T00:00:00Z",
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
    def test_publisher_and_panel_validator_share_exact_codec_objects(self) -> None:
        for name in (
            "INVENTORY_FORMAT",
            "PublicationError",
            "InventoryEntry",
            "Inventory",
            "serialize_inventory",
            "parse_inventory",
            "build_inventory",
        ):
            with self.subTest(consumer="publisher", name=name):
                self.assertIs(getattr(publisher, name), getattr(inventory_codec, name))
        for name in ("INVENTORY_FORMAT", "PublicationError", "build_inventory"):
            with self.subTest(consumer="panel validator", name=name):
                self.assertIs(getattr(panel_validator, name), getattr(inventory_codec, name))

        with self.assertRaises(inventory_codec.PublicationError) as raised:
            publisher.parse_inventory(b"not-canonical")
        self.assertIs(type(raised.exception), inventory_codec.PublicationError)

    def test_namespace_import_consumers_share_the_namespace_codec(self) -> None:
        namespace_codec = importlib.import_module("scripts.install_inventory")
        namespace_publisher = importlib.import_module("scripts.publish_codex_install")
        namespace_panel = importlib.import_module("scripts.validate_panel_inputs")
        self.assertIs(
            namespace_publisher.PublicationError,
            namespace_codec.PublicationError,
        )
        self.assertIs(namespace_publisher.build_inventory, namespace_codec.build_inventory)
        self.assertIs(namespace_panel.PublicationError, namespace_codec.PublicationError)
        self.assertIs(namespace_panel.build_inventory, namespace_codec.build_inventory)

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

    def finalize_fixture(
        self, harness: PublisherHarness, operation: str
    ) -> tuple[Path, dict[str, Path]]:
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
        harness.seal_phase(operation, "dispatch")
        return manifest, publisher._operation_paths(harness.state, operation)

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
            tracked = set(
                subprocess.run(
                    ["git", "ls-files", "-z"],
                    cwd=canonical_root,
                    check=True,
                    stdout=subprocess.PIPE,
                ).stdout.split(b"\0")
            )
            # The extraction is testable before its new module is staged.
            tracked.add(b"scripts/install_inventory.py")
            manifest_source = json.loads(
                (
                    canonical_root
                    / "codex/overnight-workflows/install-manifest.json"
                ).read_text(encoding="utf-8")
            )
            tracked.update(
                mapping["canonical_source"].encode("utf-8")
                for mapping in manifest_source["mappings"]
            )
            for encoded_path in sorted(tracked):
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
                            reader_quiescence_record=harness.maintenance(
                                operation, label=f"{operation}-recovery"
                            ),
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
        renewal_path = harness.maintenance(
            "recover-pre-swap", label="recover-pre-swap-renewal"
        )
        recovered = publisher.recover_operation(
            state_root=harness.state,
            operation="recover-pre-swap",
            action="complete",
            exchanger=publisher.FakeAtomicExchanger(),
            checker_runner=publisher._fake_checker,
            takeover_authorization=harness.takeover("recover-pre-swap"),
            reader_quiescence_record=renewal_path,
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
        renewal_bytes = renewal_path.read_bytes()
        with self.assertRaisesRegex(
            publisher.PublicationError,
            "collides with a recovery reader-quiescence record",
        ):
            publisher.finalize_operation(
                state_root=harness.state,
                operation="recover-pre-swap",
                receipt_output=renewal_path,
                checker_runner=publisher._fake_checker,
            )
        self.assertEqual(renewal_bytes, renewal_path.read_bytes())
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
                        reader_quiescence_record=harness.maintenance(
                            operation, label="recovery-owner-refusal-renewal"
                        ),
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
                reader_quiescence_record=harness.maintenance(
                    operation, label="recovery-validation-rollback-renewal"
                ),
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
                reader_quiescence_record=harness.maintenance(
                    "recovery-checker-mutation",
                    label="recovery-checker-mutation-renewal",
                ),
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
        phase_outputs = harness.report_inventory_chain("success")
        acceptance_output = phase_outputs["acceptance"]
        observation = json.loads(acceptance_output.read_text(encoding="utf-8"))
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
                "manifest_header",
                "prepare_receipt_registered",
                "installed_publication_reservation_intent",
                "installed_publication_terminal",
                "raw_input_registered",
                "manifest_prefix_registered",
                "review_report_registered",
                "challenge_response_registered",
                "judge_verdict_registered",
                "manifest_prefix_registered",
                "review_summary",
                "manifest_prefix_registered",
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
        harness.seal_phase(operation, "dispatch")
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
        prefix = receipt["current_finalization_manifest_prefix"]
        manifest_bytes = manifest.read_bytes()
        self.assertEqual(len(manifest_bytes), prefix["prefix_bytes"])
        self.assertEqual(hashlib.sha256(manifest_bytes).hexdigest(), prefix["prefix_sha256"])
        self.assertEqual(
            publisher.build_inventory(harness.install, harness.expected).data,
            Path(receipt["live_inventory"]["path"]).read_bytes(),
        )
        self.assertEqual(
            receipt,
            publisher.report_live_inventory(
                state_root=harness.state,
                operation=operation,
                phase="dispatch",
                output=output,
                lock_path=lock,
            ),
        )
        with self.assertRaisesRegex(publisher.PublicationError, "already durably recorded"):
            publisher.report_live_inventory(
                state_root=harness.state,
                operation=operation,
                phase="dispatch",
                output=harness.evidence_parent / operation / "duplicate-dispatch.json",
                lock_path=lock,
            )
        self.assertTrue(lock.is_file())

    def test_live_inventory_receipts_form_one_exact_ordered_chain(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        operation = "inventory-chain"
        manifest, paths = self.finalize_fixture(harness, operation)
        lock = paths["reservation"]
        with self.assertRaisesRegex(publisher.PublicationError, "out of order"):
            publisher.report_live_inventory(
                state_root=harness.state,
                operation=operation,
                phase="judgment",
                output=harness.evidence_parent / operation / "judgment.json",
                lock_path=lock,
            )
        dispatch_path = harness.report_inventory_chain(
            operation, through="dispatch"
        )["dispatch"]
        dispatch = json.loads(dispatch_path.read_text(encoding="utf-8"))
        publisher._append_finalization_record(
            manifest,
            record_type="external_dispatch_note",
            payload={"note": "bounded manifest suffix between phases"},
        )
        harness.seal_phase(operation, "judgment")
        judgment_path = harness.evidence_parent / operation / "judgment.json"
        judgment = publisher.report_live_inventory(
            state_root=harness.state,
            operation=operation,
            phase="judgment",
            output=judgment_path,
            lock_path=lock,
        )
        harness.seal_phase(operation, "acceptance")
        acceptance_path = harness.evidence_parent / operation / "acceptance.json"
        acceptance = publisher.report_live_inventory(
            state_root=harness.state,
            operation=operation,
            phase="acceptance",
            output=acceptance_path,
            lock_path=lock,
        )
        _, state = publisher._load_state(harness.state, operation)
        identities = state["live_inventory_reports"]
        self.assertEqual(
            ["dispatch", "judgment", "acceptance"],
            [identity["phase"] for identity in identities],
        )
        self.assertEqual(
            dispatch["current_finalization_manifest_prefix"],
            judgment["prior_finalization_manifest_prefix"],
        )
        self.assertNotEqual(
            judgment["prior_finalization_manifest_prefix"],
            judgment["current_finalization_manifest_prefix"],
        )
        self.assertEqual(identities[0], judgment["predecessor_receipt"])
        self.assertEqual(identities[1], acceptance["predecessor_receipt"])
        release = publisher.accept_operation(
            state_root=harness.state,
            operation=operation,
            acceptance_inventory_receipt=acceptance_path,
            accepted_by="panel-v3-judge",
            acceptance_reason="exact chain control",
            lock_path=lock,
            finalization_manifest=manifest,
        )
        self.assertEqual(identities, release["live_inventory_receipt_chain"])
        self.assertEqual(
            identities,
            release["acceptance_authorization"]["live_inventory_receipt_chain"],
        )

    def test_inventory_phase_crash_retries_reuse_exact_artifacts_once(self) -> None:
        for scenario in (
            "after_live_inventory_intent_persisted",
            "after_live_inventory_sidecar_write",
            "after_live_inventory_receipt_write",
            "receipt_only",
            "after_live_inventory_journal_commit",
        ):
            crash_point = (
                "after_live_inventory_receipt_write"
                if scenario == "receipt_only"
                else scenario
            )
            with self.subTest(crash_point=scenario):
                temporary, harness = self.make_harness()
                try:
                    operation = f"inventory-crash-{scenario.split('_')[-1]}"
                    _, paths = self.finalize_fixture(harness, operation)
                    output = harness.evidence_parent / operation / "dispatch.json"

                    def stop(point: str) -> None:
                        if point == crash_point:
                            raise publisher.InjectedFailure(point)

                    with self.assertRaises(publisher.InjectedFailure):
                        publisher.report_live_inventory(
                            state_root=harness.state,
                            operation=operation,
                            phase="dispatch",
                            output=output,
                            lock_path=paths["reservation"],
                            failpoint=stop,
                        )
                    if scenario == "receipt_only":
                        output.with_name(output.name + ".inventory").unlink()
                    receipt_before = output.read_bytes() if output.exists() else None
                    with self.assertRaisesRegex(
                        publisher.PublicationError,
                        "different phase or output|already durably recorded",
                    ):
                        publisher.report_live_inventory(
                            state_root=harness.state,
                            operation=operation,
                            phase="dispatch",
                            output=harness.evidence_parent
                            / operation
                            / "different-dispatch.json",
                            lock_path=paths["reservation"],
                        )
                    recovered = publisher.report_live_inventory(
                        state_root=harness.state,
                        operation=operation,
                        phase="dispatch",
                        output=output,
                        lock_path=paths["reservation"],
                    )
                    if receipt_before is not None:
                        self.assertEqual(receipt_before, output.read_bytes())
                    self.assertEqual(
                        recovered,
                        publisher.report_live_inventory(
                            state_root=harness.state,
                            operation=operation,
                            phase="dispatch",
                            output=output,
                            lock_path=paths["reservation"],
                        ),
                    )
                    _, state = publisher._load_state(harness.state, operation)
                    self.assertEqual(1, len(state["live_inventory_reports"]))
                    self.assertNotIn("pending_live_inventory_report", state)
                finally:
                    temporary.cleanup()

    def test_inventory_phase_retry_keeps_bounded_prefix_and_exact_intent_bytes(
        self,
    ) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        operation = "inventory-intent-prefix"
        manifest, paths = self.finalize_fixture(harness, operation)
        output = harness.evidence_parent / operation / "dispatch.json"

        def stop_after_intent(point: str) -> None:
            if point == "after_live_inventory_intent_persisted":
                raise publisher.InjectedFailure(point)

        with self.assertRaises(publisher.InjectedFailure):
            publisher.report_live_inventory(
                state_root=harness.state,
                operation=operation,
                phase="dispatch",
                output=output,
                lock_path=paths["reservation"],
                failpoint=stop_after_intent,
            )
        _, pending_state = publisher._load_state(harness.state, operation)
        pending_prefix = pending_state["pending_live_inventory_report"]["receipt"][
            "current_finalization_manifest_prefix"
        ]
        publisher._append_finalization_record(
            manifest,
            record_type="external_dispatch_note",
            payload={"note": "valid suffix after durable inventory intent"},
        )
        recovered = publisher.report_live_inventory(
            state_root=harness.state,
            operation=operation,
            phase="dispatch",
            output=output,
            lock_path=paths["reservation"],
        )
        self.assertEqual(
            pending_prefix, recovered["current_finalization_manifest_prefix"]
        )
        self.assertGreater(len(manifest.read_bytes()), pending_prefix["prefix_bytes"])

        second_temporary, second_harness = self.make_harness()
        self.addCleanup(second_temporary.cleanup)
        second_operation = "inventory-intent-byte-drift"
        _, second_paths = self.finalize_fixture(second_harness, second_operation)
        second_output = (
            second_harness.evidence_parent / second_operation / "dispatch.json"
        )

        def stop_after_receipt(point: str) -> None:
            if point == "after_live_inventory_receipt_write":
                raise publisher.InjectedFailure(point)

        with self.assertRaises(publisher.InjectedFailure):
            publisher.report_live_inventory(
                state_root=second_harness.state,
                operation=second_operation,
                phase="dispatch",
                output=second_output,
                lock_path=second_paths["reservation"],
                failpoint=stop_after_receipt,
            )
        semantic_value = json.loads(second_output.read_text(encoding="utf-8"))
        second_output.write_text(
            json.dumps(semantic_value, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            publisher.PublicationError, "durable phase intent"
        ):
            publisher.report_live_inventory(
                state_root=second_harness.state,
                operation=second_operation,
                phase="dispatch",
                output=second_output,
                lock_path=second_paths["reservation"],
            )
        _, blocked = publisher._load_state(
            second_harness.state, second_operation
        )
        self.assertIn("pending_live_inventory_report", blocked)
        self.assertTrue(second_paths["reservation"].is_file())

    def test_acceptance_refuses_skipped_or_tampered_phase_chain(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        operation = "acceptance-exact-chain"
        manifest, paths = self.finalize_fixture(harness, operation)
        dispatch = harness.report_inventory_chain(
            operation, through="dispatch"
        )["dispatch"]
        with self.assertRaisesRegex(publisher.PublicationError, "out of order"):
            publisher.report_live_inventory(
                state_root=harness.state,
                operation=operation,
                phase="acceptance",
                output=harness.evidence_parent / operation / "acceptance.json",
                lock_path=paths["reservation"],
            )
        with self.assertRaisesRegex(publisher.PublicationError, "complete dispatch"):
            publisher.accept_operation(
                state_root=harness.state,
                operation=operation,
                acceptance_inventory_receipt=dispatch,
                accepted_by="panel-v3-judge",
                acceptance_reason="skip negative control",
                lock_path=paths["reservation"],
                finalization_manifest=manifest,
            )
        outputs = harness.report_inventory_chain(operation)
        dispatch_value = json.loads(dispatch.read_text(encoding="utf-8"))
        dispatch_value["predecessor_receipt"]["sha256"] = "0" * 64
        dispatch.write_text(
            json.dumps(dispatch_value, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            publisher.PublicationError, "ordered chain|journal identity"
        ):
            publisher.accept_operation(
                state_root=harness.state,
                operation=operation,
                acceptance_inventory_receipt=outputs["acceptance"],
                accepted_by="panel-v3-judge",
                acceptance_reason="tamper negative control",
                lock_path=paths["reservation"],
                finalization_manifest=manifest,
            )
        self.assertTrue(paths["reservation"].is_file())

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
                    observation = harness.report_inventory_chain(operation)["acceptance"]
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
        observation = harness.report_inventory_chain(operation)["acceptance"]
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
        observation = harness.report_inventory_chain(operation)["acceptance"]
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
        observation = harness.report_inventory_chain(operation)["acceptance"]
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
        observation = harness.report_inventory_chain(operation)["acceptance"]
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
        observation = harness.report_inventory_chain(operation)["acceptance"]
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
        observation = harness.report_inventory_chain(operation)["acceptance"]
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
        harness.seal_phase(operation, "dispatch")
        output = harness.evidence_parent / operation / "dispatch-drift.json"
        original_write = publisher._write_new_file

        def mutate_live(path: Path, data: bytes, mode: int = 0o600) -> None:
            original_write(path, data, mode)
            if path == output:
                (harness.install / "SKILL.md").write_text(
                    "changed during dispatch report\n", encoding="utf-8"
                )

        with mock.patch.object(publisher, "_write_new_file", mutate_live):
            with self.assertRaisesRegex(
                publisher.PublicationError, "live generation drifted"
            ):
                publisher.report_live_inventory(
                    state_root=harness.state,
                    operation=operation,
                    phase="dispatch",
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

    def test_reader_quiescence_schema_is_structured_bounded_and_fail_closed(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        operation = "reader-schema"
        record = harness.maintenance(operation)
        valid = json.loads(record.read_text(encoding="utf-8"))
        receipt, digest = publisher._validate_maintenance_receipt(
            record, operation, require_current=True
        )
        self.assertEqual(valid, receipt)
        self.assertEqual(hashlib.sha256(record.read_bytes()).hexdigest(), digest)

        def changed(path: tuple[str, ...], value: object) -> dict[str, object]:
            candidate = json.loads(json.dumps(valid))
            target = candidate
            for component in path[:-1]:
                target = target[component]
            target[path[-1]] = value
            return candidate

        invalid = {
            "timestamp syntax": changed(
                ("known_reader_inventory", "checked_at"), "2026-08-09 00:00:00Z"
            ),
            "unbounded window": changed(
                ("maintenance_window", "ends_at"), "2099-08-09T00:00:00Z"
            ),
            "incomplete inventory": changed(
                ("known_reader_inventory", "inventory_complete"), False
            ),
            "active reader": changed(
                ("known_reader_inventory", "known_active_reader_count"), 1
            ),
            "unknown reader": changed(
                ("known_reader_inventory", "unknown_reader_status"), "UNKNOWN"
            ),
            "unsafe unknown policy": changed(
                ("known_reader_inventory", "unknown_reader_policy"), "CONTINUE"
            ),
            "incomplete scope": changed(
                ("known_reader_inventory", "scope"), "sampled-readers"
            ),
            "world truth claim": changed(
                ("publisher_validation_scope",), "publisher-proves-world-truth"
            ),
            "conflicting undeclared field": changed(
                ("reader_quiescence_status",), "QUIESCENT"
            ),
            "control character": changed(
                ("known_reader_inventory", "method"), "process scan\tresult"
            ),
        }
        nested_undeclared = json.loads(json.dumps(valid))
        nested_undeclared["known_reader_inventory"][
            "unknown_readers_present"
        ] = True
        invalid["nested undeclared conflicting field"] = nested_undeclared
        invalid["active reader"]["known_reader_inventory"]["known_reader_count"] = 1
        for label, value in invalid.items():
            with self.subTest(label=label):
                record.write_text(
                    json.dumps(value, sort_keys=True) + "\n", encoding="utf-8"
                )
                with self.assertRaises(publisher.PublicationError):
                    publisher._validate_maintenance_receipt(
                        record, operation, require_current=False
                    )
        record.write_text(json.dumps(valid, sort_keys=True) + "\n", encoding="utf-8")
        expires = publisher._parse_utc_timestamp(
            valid["known_reader_inventory"]["expires_at"], label="test expiry"
        )
        with self.assertRaisesRegex(publisher.PublicationError, "stale"):
            publisher._validate_maintenance_receipt(
                record, operation, require_current=True, now=expires
            )

    def test_reader_attestation_changed_or_stale_blocks_initial_exchange(self) -> None:
        for condition in ("changed", "stale", "unknown"):
            with self.subTest(condition=condition):
                temporary, harness = self.make_harness()
                try:
                    operation = f"reader-pre-swap-{condition}"
                    harness.prepare(operation)
                    record = harness.maintenance(operation)
                    publisher.reserve_operation(
                        state_root=harness.state,
                        operation=operation,
                        maintenance_receipt=record,
                    )
                    exchanger = publisher.FakeAtomicExchanger()
                    if condition in {"changed", "unknown"}:

                        def mutate(point: str) -> None:
                            if point == "before_exchange":
                                value = json.loads(record.read_text(encoding="utf-8"))
                                if condition == "changed":
                                    value["known_reader_inventory"][
                                        "evidence_reference"
                                    ] = "changed-after-reservation"
                                else:
                                    value["known_reader_inventory"][
                                        "unknown_reader_status"
                                    ] = "UNKNOWN"
                                record.write_text(
                                    json.dumps(value, sort_keys=True) + "\n",
                                    encoding="utf-8",
                                )

                        context = contextlib.nullcontext()
                        failpoint = mutate
                    else:
                        value = json.loads(record.read_text(encoding="utf-8"))
                        expires = publisher._parse_utc_timestamp(
                            value["known_reader_inventory"]["expires_at"],
                            label="test expiry",
                        )
                        context = mock.patch.object(
                            publisher,
                            "_utc_datetime_now",
                            return_value=expires + timedelta(seconds=1),
                        )
                        failpoint = None
                    with context:
                        with self.assertRaisesRegex(
                            publisher.PublicationError, "changed|stale|unknown"
                        ):
                            publisher.publish_operation(
                                state_root=harness.state,
                                operation=operation,
                                exchanger=exchanger,
                                checker_runner=publisher._fake_checker,
                                failpoint=failpoint,
                            )
                    self.assertEqual([], exchanger.calls)
                    _, state = publisher._load_state(harness.state, operation)
                    self.assertEqual("UNCHECKED", state["status"])
                finally:
                    temporary.cleanup()

    def test_no_state_write_occurs_between_final_attestation_read_and_exchange(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        operation = "reader-final-boundary"
        harness.prepare(operation)
        record = harness.maintenance(operation)
        publisher.reserve_operation(
            state_root=harness.state,
            operation=operation,
            maintenance_receipt=record,
        )
        original_revalidate = publisher._revalidate_reader_attestation_before_exchange
        original_persist = publisher._persist_state
        attestation_read = False
        exchange_started = False
        intervening_state_writes = 0

        class BoundaryExchanger(publisher.FakeAtomicExchanger):
            def exchange(self, left: Path, right: Path) -> None:
                nonlocal exchange_started
                exchange_started = True
                super().exchange(left, right)

        def mark_revalidation(*args: object, **kwargs: object) -> dict[str, object]:
            nonlocal attestation_read
            result = original_revalidate(*args, **kwargs)
            attestation_read = True
            return result

        def detect_intervening_write(
            path: Path, state: dict[str, object], status: str | None = None
        ) -> None:
            nonlocal intervening_state_writes
            if attestation_read and not exchange_started:
                intervening_state_writes += 1
                value = json.loads(record.read_text(encoding="utf-8"))
                value["known_reader_inventory"][
                    "evidence_reference"
                ] = "mutation-in-obsolete-persist-gap"
                record.write_text(
                    json.dumps(value, sort_keys=True) + "\n", encoding="utf-8"
                )
            original_persist(path, state, status)

        exchanger = BoundaryExchanger()
        with mock.patch.object(
            publisher,
            "_revalidate_reader_attestation_before_exchange",
            side_effect=mark_revalidation,
        ), mock.patch.object(
            publisher, "_persist_state", side_effect=detect_intervening_write
        ):
            publisher.publish_operation(
                state_root=harness.state,
                operation=operation,
                exchanger=exchanger,
                checker_runner=publisher._fake_checker,
            )
        self.assertEqual(0, intervening_state_writes)
        self.assertEqual(1, len(exchanger.calls))

    def test_automatic_rollback_rechecks_changed_or_stale_attestation(self) -> None:
        for condition in ("changed", "stale"):
            with self.subTest(condition=condition):
                temporary, harness = self.make_harness()
                patcher: object | None = None
                try:
                    operation = f"reader-auto-rollback-{condition}"
                    harness.prepare(operation)
                    record = harness.maintenance(operation)
                    publisher.reserve_operation(
                        state_root=harness.state,
                        operation=operation,
                        maintenance_receipt=record,
                    )
                    expires = publisher._parse_utc_timestamp(
                        json.loads(record.read_text(encoding="utf-8"))[
                            "known_reader_inventory"
                        ]["expires_at"],
                        label="test expiry",
                    )

                    def fail_live_checker(
                        source: Path, installed: Path
                    ) -> dict[str, object]:
                        nonlocal patcher
                        if installed == harness.install:
                            if condition == "changed":
                                value = json.loads(record.read_text(encoding="utf-8"))
                                value["known_reader_inventory"][
                                    "evidence_reference"
                                ] = "changed-before-automatic-rollback"
                                record.write_text(
                                    json.dumps(value, sort_keys=True) + "\n",
                                    encoding="utf-8",
                                )
                            else:
                                patcher = mock.patch.object(
                                    publisher,
                                    "_utc_datetime_now",
                                    return_value=expires + timedelta(seconds=1),
                                )
                                patcher.start()
                            raise publisher.ValidationFailure(
                                "force automatic rollback attestation gate"
                            )
                        return publisher._fake_checker(source, installed)

                    exchanger = publisher.FakeAtomicExchanger()
                    with self.assertRaisesRegex(
                        publisher.PublicationError, "changed|stale"
                    ):
                        publisher.publish_operation(
                            state_root=harness.state,
                            operation=operation,
                            exchanger=exchanger,
                            checker_runner=fail_live_checker,
                        )
                    self.assertEqual(1, len(exchanger.calls))
                    inspection = publisher.classify_generation_state(
                        harness.state, operation
                    )
                    self.assertEqual(
                        "POST_SWAP_RETAINED", inspection["classification"]
                    )
                    _, state = publisher._load_state(harness.state, operation)
                    self.assertEqual("UNCHECKED", state["status"])
                finally:
                    if patcher is not None:
                        patcher.stop()
                    temporary.cleanup()

    def test_explicit_recovery_rollback_rechecks_current_attestation(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        operation = "reader-recovery-rollback-stale"
        harness.prepare(operation)
        record = harness.maintenance(operation)
        publisher.reserve_operation(
            state_root=harness.state,
            operation=operation,
            maintenance_receipt=record,
        )
        initial_exchanger = publisher.FakeAtomicExchanger()

        def stop_after_exchange(point: str) -> None:
            if point == "after_exchange":
                raise publisher.InjectedFailure(point)

        with self.assertRaises(publisher.InjectedFailure):
            publisher.publish_operation(
                state_root=harness.state,
                operation=operation,
                exchanger=initial_exchanger,
                checker_runner=publisher._fake_checker,
                failpoint=stop_after_exchange,
            )
        self.assertEqual(1, len(initial_exchanger.calls))
        recovery_record = harness.maintenance(
            operation, label="reader-recovery-rollback-renewal"
        )
        expires = publisher._parse_utc_timestamp(
            json.loads(recovery_record.read_text(encoding="utf-8"))[
                "known_reader_inventory"
            ]["expires_at"],
            label="test expiry",
        )
        recovery_exchanger = publisher.FakeAtomicExchanger()
        with mock.patch.object(
            publisher,
            "_utc_datetime_now",
            return_value=expires + timedelta(seconds=1),
        ):
            with self.assertRaisesRegex(publisher.PublicationError, "stale"):
                publisher.recover_operation(
                    state_root=harness.state,
                    operation=operation,
                    action="rollback",
                    exchanger=recovery_exchanger,
                    takeover_authorization=harness.takeover(operation),
                    reader_quiescence_record=recovery_record,
                    checker_runner=publisher._fake_checker,
                )
        self.assertEqual([], recovery_exchanger.calls)
        self.assertEqual(
            "POST_SWAP_RETAINED",
            publisher.classify_generation_state(harness.state, operation)[
                "classification"
            ],
        )

    def test_expired_original_attestation_can_use_fresh_bound_recovery_renewal(
        self,
    ) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        operation = "expired-original-fresh-recovery"
        prepared = harness.prepare(operation)
        original = harness.maintenance(operation)
        publisher.reserve_operation(
            state_root=harness.state,
            operation=operation,
            maintenance_receipt=original,
        )

        def stop_before_exchange(point: str) -> None:
            if point == "before_exchange":
                raise publisher.InjectedFailure(point)

        with self.assertRaises(publisher.InjectedFailure):
            publisher.publish_operation(
                state_root=harness.state,
                operation=operation,
                exchanger=publisher.FakeAtomicExchanger(),
                checker_runner=publisher._fake_checker,
                failpoint=stop_before_exchange,
            )
        original_expires = publisher._parse_utc_timestamp(
            json.loads(original.read_text(encoding="utf-8"))["known_reader_inventory"]
            ["expires_at"],
            label="test original expiry",
        )
        recovery_now = original_expires + timedelta(seconds=1, microseconds=750000)
        with mock.patch.object(
            publisher, "_utc_datetime_now", return_value=recovery_now
        ):
            renewal_record = harness.maintenance(
                operation, label="expired-original-recovery-renewal"
            )
            recovered = publisher.recover_operation(
                state_root=harness.state,
                operation=operation,
                action="complete",
                exchanger=publisher.FakeAtomicExchanger(),
                checker_runner=publisher._fake_checker,
                takeover_authorization=harness.takeover(operation),
                reader_quiescence_record=renewal_record,
            )
            receipt = publisher.finalize_operation(
                state_root=harness.state,
                operation=operation,
                checker_runner=publisher._fake_checker,
            )
        self.assertEqual("PUBLISHED", recovered["status"])
        self.assertEqual(
            prepared["candidate_inventory"]["sha256"], harness.old_inventory().digest
        )
        renewals = receipt["reader_attestation_renewals"]
        exchanges = receipt["atomic_exchange_reader_attestations"]
        self.assertEqual(publisher.EXTERNAL_CLAIM_VALIDATION_SCOPE,
                         receipt["reader_attestation_validation_scope"])
        self.assertEqual(1, len(renewals))
        self.assertEqual(
            recovery_now.isoformat().replace("+00:00", "Z"),
            renewals[0]["bound_at"],
        )
        self.assertEqual(
            "original_reservation_reader_attestation",
            renewals[0]["predecessor_renewal"]["record_type"],
        )
        self.assertEqual(1, len(exchanges))
        self.assertEqual("publish", exchanges[0]["purpose"])
        self.assertEqual(
            "recovery_reader_attestation_renewal",
            exchanges[0]["binding_context"]["kind"],
        )
        self.assertEqual(
            publisher._recovery_reader_attestation_renewal_identity(renewals[0]),
            exchanges[0]["binding_context"]["renewal_identity"],
        )

    def test_recovery_renewal_must_be_a_newer_claim_not_a_copied_path(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        operation = "recovery-renewal-freshness"
        harness.prepare(operation)
        original = harness.maintenance(operation)
        publisher.reserve_operation(
            state_root=harness.state,
            operation=operation,
            maintenance_receipt=original,
        )

        def stop_before_exchange(point: str) -> None:
            if point == "before_exchange":
                raise publisher.InjectedFailure(point)

        with self.assertRaises(publisher.InjectedFailure):
            publisher.publish_operation(
                state_root=harness.state,
                operation=operation,
                exchanger=publisher.FakeAtomicExchanger(),
                checker_runner=publisher._fake_checker,
                failpoint=stop_before_exchange,
            )
        copied = harness.root / "copied-original-reader-attestation.json"
        shutil.copyfile(original, copied)
        takeover = harness.takeover(operation)
        exchanger = publisher.FakeAtomicExchanger()
        with self.assertRaisesRegex(
            publisher.PublicationError, "fresh later"
        ):
            publisher.recover_operation(
                state_root=harness.state,
                operation=operation,
                action="complete",
                exchanger=exchanger,
                checker_runner=publisher._fake_checker,
                takeover_authorization=takeover,
                reader_quiescence_record=copied,
            )
        self.assertEqual([], exchanger.calls)
        fresh = harness.maintenance(operation, label="genuinely-newer-renewal")
        recovered = publisher.recover_operation(
            state_root=harness.state,
            operation=operation,
            action="complete",
            exchanger=exchanger,
            checker_runner=publisher._fake_checker,
            takeover_authorization=takeover,
            reader_quiescence_record=fresh,
        )
        self.assertEqual("PUBLISHED", recovered["status"])
        self.assertEqual(1, len(exchanger.calls))

    def test_terminal_receipt_rejects_impossible_recovery_renewal_time(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        operation = "impossible-recovery-renewal-time"
        harness.prepare(operation)
        harness.reserve(operation)

        def stop_before_exchange(point: str) -> None:
            if point == "before_exchange":
                raise publisher.InjectedFailure(point)

        with self.assertRaises(publisher.InjectedFailure):
            publisher.publish_operation(
                state_root=harness.state,
                operation=operation,
                exchanger=publisher.FakeAtomicExchanger(),
                checker_runner=publisher._fake_checker,
                failpoint=stop_before_exchange,
            )
        publisher.recover_operation(
            state_root=harness.state,
            operation=operation,
            action="complete",
            exchanger=publisher.FakeAtomicExchanger(),
            checker_runner=publisher._fake_checker,
            takeover_authorization=harness.takeover(operation),
            reader_quiescence_record=harness.maintenance(
                operation, label="impossible-time-renewal"
            ),
        )
        paths, state = publisher._load_state(harness.state, operation)
        renewal = state["reader_attestation_renewals"][0]
        renewal["bound_at"] = "2000-01-01T00:00:00Z"
        renewal_identity = publisher._recovery_reader_attestation_renewal_identity(
            renewal
        )
        state["active_recovery_reader_attestation_renewal"] = renewal
        exchange = state["atomic_exchange_reader_attestations"][0]
        exchange["binding_context"]["renewal_identity"] = renewal_identity
        state["atomic_exchange_reader_attestation"] = exchange
        publisher._persist_state(paths["state"], state)

        with self.assertRaisesRegex(
            publisher.PublicationError, "bound_at.*outside"
        ):
            publisher.finalize_operation(
                state_root=harness.state,
                operation=operation,
                checker_runner=publisher._fake_checker,
            )

    def test_terminal_receipt_rejects_renewal_recorded_after_exchange(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        operation = "renewal-recorded-after-exchange"
        harness.prepare(operation)
        harness.reserve(operation)

        def stop_before_exchange(point: str) -> None:
            if point == "before_exchange":
                raise publisher.InjectedFailure(point)

        with self.assertRaises(publisher.InjectedFailure):
            publisher.publish_operation(
                state_root=harness.state,
                operation=operation,
                exchanger=publisher.FakeAtomicExchanger(),
                checker_runner=publisher._fake_checker,
                failpoint=stop_before_exchange,
            )
        publisher.recover_operation(
            state_root=harness.state,
            operation=operation,
            action="complete",
            exchanger=publisher.FakeAtomicExchanger(),
            checker_runner=publisher._fake_checker,
            takeover_authorization=harness.takeover(operation),
            reader_quiescence_record=harness.maintenance(
                operation, label="after-exchange-time-renewal"
            ),
        )
        paths, state = publisher._load_state(harness.state, operation)
        exchange = state["atomic_exchange_reader_attestations"][0]
        validated_at = publisher._parse_utc_timestamp(
            exchange["validated_at"], label="test exchange validated_at"
        )
        renewal = state["reader_attestation_renewals"][0]
        renewal["bound_at"] = publisher._format_precise_utc_timestamp(
            validated_at + timedelta(seconds=30)
        )
        renewal_identity = publisher._recovery_reader_attestation_renewal_identity(
            renewal
        )
        state["active_recovery_reader_attestation_renewal"] = renewal
        exchange["binding_context"]["renewal_identity"] = renewal_identity
        state["atomic_exchange_reader_attestation"] = exchange
        publisher._persist_state(paths["state"], state)

        with self.assertRaisesRegex(
            publisher.PublicationError, "timestamp is outside"
        ):
            publisher.finalize_operation(
                state_root=harness.state,
                operation=operation,
                checker_runner=publisher._fake_checker,
            )

    def test_terminal_receipt_rejects_missing_mistimed_or_misbound_exchange_history(
        self,
    ) -> None:
        for condition in ("missing", "mistimed", "wrong-purpose"):
            with self.subTest(condition=condition):
                temporary, harness = self.make_harness()
                try:
                    operation = f"exchange-history-{condition}"
                    harness.prepare(operation)
                    harness.reserve(operation)
                    publisher.publish_operation(
                        state_root=harness.state,
                        operation=operation,
                        exchanger=publisher.FakeAtomicExchanger(),
                        checker_runner=publisher._fake_checker,
                    )
                    paths, state = publisher._load_state(
                        harness.state, operation
                    )
                    if condition == "missing":
                        state["atomic_exchange_reader_attestations"] = []
                        state["atomic_exchange_reader_attestation"] = None
                    else:
                        exchange = state["atomic_exchange_reader_attestations"][0]
                        if condition == "mistimed":
                            exchange["validated_at"] = "2000-01-01T00:00:00Z"
                        else:
                            exchange["purpose"] = "explicit-recovery-rollback"
                        state["atomic_exchange_reader_attestation"] = exchange
                    publisher._persist_state(paths["state"], state)
                    with self.assertRaisesRegex(
                        publisher.PublicationError,
                        "attestation|history|begin with publish",
                    ):
                        publisher.finalize_operation(
                            state_root=harness.state,
                            operation=operation,
                            checker_runner=publisher._fake_checker,
                        )
                    self.assertFalse(
                        (harness.evidence_parent / operation / "publication-receipt.json").exists()
                    )
                finally:
                    temporary.cleanup()

    def test_exchange_history_preserves_fractional_validation_instant(self) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        operation = "fractional-attestation-validation"
        fixed_now = datetime(
            2030, 1, 1, 12, 0, 0, 750000, tzinfo=timezone.utc
        )
        harness.prepare(operation)
        attestation = harness.maintenance(operation)
        value = json.loads(attestation.read_text(encoding="utf-8"))
        value["maintenance_window"] = {
            "id": "fractional-window",
            "starts_at": "2030-01-01T11:59:00Z",
            "ends_at": "2030-01-01T12:10:00Z",
        }
        value["known_reader_inventory"]["checked_at"] = (
            "2030-01-01T12:00:00.500000Z"
        )
        value["known_reader_inventory"]["expires_at"] = (
            "2030-01-01T12:05:00Z"
        )
        attestation.write_text(json.dumps(value), encoding="utf-8")

        with mock.patch.object(
            publisher, "_utc_datetime_now", return_value=fixed_now
        ):
            publisher.reserve_operation(
                state_root=harness.state,
                operation=operation,
                maintenance_receipt=attestation,
            )
            publisher.publish_operation(
                state_root=harness.state,
                operation=operation,
                exchanger=publisher.FakeAtomicExchanger(),
                checker_runner=publisher._fake_checker,
            )
            receipt = publisher.finalize_operation(
                state_root=harness.state,
                operation=operation,
                checker_runner=publisher._fake_checker,
            )

        exchange = receipt["atomic_exchange_reader_attestations"][0]
        self.assertEqual("2030-01-01T12:00:00.750000Z", exchange["validated_at"])

    def test_post_swap_completion_refuses_an_exchange_without_durable_attestation(
        self,
    ) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        operation = "unrecorded-successful-exchange"
        harness.prepare(operation)
        harness.reserve(operation)

        class SwapThenRaise(publisher.FakeAtomicExchanger):
            def exchange(self, left: Path, right: Path) -> None:
                super().exchange(left, right)
                raise OSError("simulated post-rename fsync failure")

        with self.assertRaises(OSError):
            publisher.publish_operation(
                state_root=harness.state,
                operation=operation,
                exchanger=SwapThenRaise(),
                checker_runner=publisher._fake_checker,
            )
        self.assertEqual(
            "POST_SWAP_SLOT",
            publisher.classify_generation_state(harness.state, operation)[
                "classification"
            ],
        )
        recovery_exchanger = publisher.FakeAtomicExchanger()
        with self.assertRaisesRegex(
            publisher.PublicationError, "lacks durable reader-attestation"
        ):
            publisher.recover_operation(
                state_root=harness.state,
                operation=operation,
                action="complete",
                exchanger=recovery_exchanger,
                checker_runner=publisher._fake_checker,
                takeover_authorization=harness.takeover(operation),
                reader_quiescence_record=harness.maintenance(
                    operation, label="unrecorded-exchange-recovery"
                ),
            )
        self.assertEqual([], recovery_exchanger.calls)
        _, blocked = publisher._load_state(harness.state, operation)
        self.assertEqual("UNCHECKED", blocked["status"])

    def test_recovery_renewal_crash_retry_changed_stale_and_later_refresh(
        self,
    ) -> None:
        for condition in ("exact-retry", "changed", "stale-then-refresh"):
            with self.subTest(condition=condition):
                temporary, harness = self.make_harness()
                try:
                    operation = f"recovery-renewal-{condition}"
                    harness.prepare(operation)
                    harness.reserve(operation)

                    def stop_before_exchange(point: str) -> None:
                        if point == "before_exchange":
                            raise publisher.InjectedFailure(point)

                    with self.assertRaises(publisher.InjectedFailure):
                        publisher.publish_operation(
                            state_root=harness.state,
                            operation=operation,
                            exchanger=publisher.FakeAtomicExchanger(),
                            checker_runner=publisher._fake_checker,
                            failpoint=stop_before_exchange,
                        )
                    takeover = harness.takeover(operation)
                    renewal_record = harness.maintenance(
                        operation, label=f"{condition}-bound-renewal"
                    )

                    def stop_after_binding(point: str) -> None:
                        if point == "after_recovery_attestation_renewal_bound":
                            raise publisher.InjectedFailure(point)

                    first_exchanger = publisher.FakeAtomicExchanger()
                    with self.assertRaises(publisher.InjectedFailure):
                        publisher.recover_operation(
                            state_root=harness.state,
                            operation=operation,
                            action="complete",
                            exchanger=first_exchanger,
                            checker_runner=publisher._fake_checker,
                            takeover_authorization=takeover,
                            reader_quiescence_record=renewal_record,
                            failpoint=stop_after_binding,
                        )
                    self.assertEqual([], first_exchanger.calls)
                    _, bound_state = publisher._load_state(harness.state, operation)
                    self.assertEqual(1, len(bound_state["reader_attestation_renewals"]))

                    retry_exchanger = publisher.FakeAtomicExchanger()
                    if condition == "changed":
                        changed = json.loads(
                            renewal_record.read_text(encoding="utf-8")
                        )
                        changed["known_reader_inventory"][
                            "evidence_reference"
                        ] = "changed-after-renewal-binding"
                        renewal_record.write_text(
                            json.dumps(changed, sort_keys=True) + "\n",
                            encoding="utf-8",
                        )
                        with self.assertRaisesRegex(
                            publisher.PublicationError, "drifted"
                        ):
                            publisher.recover_operation(
                                state_root=harness.state,
                                operation=operation,
                                action="complete",
                                exchanger=retry_exchanger,
                                checker_runner=publisher._fake_checker,
                                takeover_authorization=takeover,
                                reader_quiescence_record=renewal_record,
                            )
                        self.assertEqual([], retry_exchanger.calls)
                        self.assertEqual(
                            "PRE_SWAP",
                            publisher.classify_generation_state(
                                harness.state, operation
                            )["classification"],
                        )
                        continue
                    if condition == "stale-then-refresh":
                        expiry = publisher._parse_utc_timestamp(
                            json.loads(renewal_record.read_text(encoding="utf-8"))[
                                "known_reader_inventory"
                            ]["expires_at"],
                            label="test bound renewal expiry",
                        )
                        with mock.patch.object(
                            publisher, "_utc_datetime_now", return_value=expiry
                        ):
                            with self.assertRaisesRegex(
                                publisher.PublicationError, "stale"
                            ):
                                publisher.recover_operation(
                                    state_root=harness.state,
                                    operation=operation,
                                    action="complete",
                                    exchanger=retry_exchanger,
                                    checker_runner=publisher._fake_checker,
                                    takeover_authorization=takeover,
                                    reader_quiescence_record=renewal_record,
                                )
                            later_record = harness.maintenance(
                                operation, label="later-fresh-recovery-renewal"
                            )
                            recovered = publisher.recover_operation(
                                state_root=harness.state,
                                operation=operation,
                                action="complete",
                                exchanger=retry_exchanger,
                                checker_runner=publisher._fake_checker,
                                takeover_authorization=takeover,
                                reader_quiescence_record=later_record,
                            )
                        expected_renewals = 2
                    else:
                        recovered = publisher.recover_operation(
                            state_root=harness.state,
                            operation=operation,
                            action="complete",
                            exchanger=retry_exchanger,
                            checker_runner=publisher._fake_checker,
                            takeover_authorization=takeover,
                            reader_quiescence_record=renewal_record,
                        )
                        expected_renewals = 1
                    self.assertEqual("PUBLISHED", recovered["status"])
                    self.assertEqual(1, len(retry_exchanger.calls))
                    _, final_state = publisher._load_state(harness.state, operation)
                    renewals = final_state["reader_attestation_renewals"]
                    self.assertEqual(expected_renewals, len(renewals))
                    self.assertEqual(
                        expected_renewals,
                        sum(
                            event["event"]
                            == "recovery_reader_attestation_renewed"
                            for event in final_state["events"]
                        ),
                    )
                    if expected_renewals == 2:
                        self.assertEqual(
                            publisher._recovery_reader_attestation_renewal_identity(
                                renewals[0]
                            ),
                            renewals[1]["predecessor_renewal"],
                        )
                finally:
                    temporary.cleanup()

    def test_recovery_automatic_rollback_rechecks_fresh_renewal(self) -> None:
        for condition in ("changed", "stale"):
            with self.subTest(condition=condition):
                temporary, harness = self.make_harness()
                patcher: object | None = None
                try:
                    operation = f"recovery-auto-rollback-{condition}"
                    harness.prepare(operation)
                    harness.reserve(operation)

                    def stop_after_exchange(point: str) -> None:
                        if point == "after_exchange":
                            raise publisher.InjectedFailure(point)

                    with self.assertRaises(publisher.InjectedFailure):
                        publisher.publish_operation(
                            state_root=harness.state,
                            operation=operation,
                            exchanger=publisher.FakeAtomicExchanger(),
                            checker_runner=publisher._fake_checker,
                            failpoint=stop_after_exchange,
                        )
                    renewal_record = harness.maintenance(
                        operation, label=f"recovery-auto-rollback-{condition}-renewal"
                    )
                    expiry = publisher._parse_utc_timestamp(
                        json.loads(renewal_record.read_text(encoding="utf-8"))[
                            "known_reader_inventory"
                        ]["expires_at"],
                        label="test recovery rollback renewal expiry",
                    )

                    def reject_live(
                        source: Path, installed: Path
                    ) -> dict[str, object]:
                        nonlocal patcher
                        if installed == harness.install:
                            if condition == "changed":
                                changed = json.loads(
                                    renewal_record.read_text(encoding="utf-8")
                                )
                                changed["known_reader_inventory"][
                                    "evidence_reference"
                                ] = "changed-before-recovery-automatic-rollback"
                                renewal_record.write_text(
                                    json.dumps(changed, sort_keys=True) + "\n",
                                    encoding="utf-8",
                                )
                            else:
                                patcher = mock.patch.object(
                                    publisher,
                                    "_utc_datetime_now",
                                    return_value=expiry,
                                )
                                patcher.start()
                            raise publisher.ValidationFailure(
                                "force recovery automatic rollback attestation gate"
                            )
                        return publisher._fake_checker(source, installed)

                    recovery_exchanger = publisher.FakeAtomicExchanger()
                    with self.assertRaisesRegex(
                        publisher.PublicationError,
                        "rollback could not be proved",
                    ):
                        publisher.recover_operation(
                            state_root=harness.state,
                            operation=operation,
                            action="complete",
                            exchanger=recovery_exchanger,
                            checker_runner=reject_live,
                            takeover_authorization=harness.takeover(operation),
                            reader_quiescence_record=renewal_record,
                        )
                    self.assertEqual([], recovery_exchanger.calls)
                    self.assertEqual(
                        "POST_SWAP_RETAINED",
                        publisher.classify_generation_state(
                            harness.state, operation
                        )["classification"],
                    )
                    _, state = publisher._load_state(harness.state, operation)
                    self.assertEqual("UNCHECKED", state["status"])
                finally:
                    if patcher is not None:
                        patcher.stop()
                    temporary.cleanup()

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
                "schema_version": 2,
                "sequence": 1,
                "recorded_at": "2026-08-09T00:00:00Z",
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
            publisher.PublicationError, "single-link|exactly one hard link"
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

    def test_reserve_rejects_manifest_grammar_mutation_before_publisher_state_change(
        self,
    ) -> None:
        temporary, harness = self.make_harness()
        self.addCleanup(temporary.cleanup)
        operation = "manifest-grammar-mutation"
        harness.prepare(operation)
        manifest = harness.finalization_manifest(operation)
        records = [
            json.loads(line)
            for line in manifest.read_text(encoding="utf-8").splitlines()
        ]
        records[0]["schema_version"] = 999
        manifest.write_text(
            "".join(
                json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
                for record in records
            ),
            encoding="utf-8",
        )
        paths, state = publisher._load_state(harness.state, operation)
        state_before = paths["state"].read_bytes()
        prepare_path = Path(state["prepare_receipt"]["path"])
        prepare_before = prepare_path.read_bytes()
        manifest_before = manifest.read_bytes()

        with self.assertRaisesRegex(publisher.PublicationError, "schema identity"):
            harness.reserve(operation, finalization_manifest=manifest)

        self.assertEqual(state_before, paths["state"].read_bytes())
        self.assertEqual(prepare_before, prepare_path.read_bytes())
        self.assertEqual(manifest_before, manifest.read_bytes())
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
                reader_quiescence_record=harness.maintenance(
                    "malformed", label="malformed-recovery-renewal"
                ),
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

    def test_help_documents_bounded_attestation_and_exact_phase_order(self) -> None:
        reserve_help = io.StringIO()
        with contextlib.redirect_stdout(reserve_help):
            with self.assertRaises(SystemExit) as reserve_exit:
                publisher._parser().parse_args(["reserve", "--help"])
        self.assertEqual(0, reserve_exit.exception.code)
        reserve_text = " ".join(reserve_help.getvalue().split())
        for required in (
            "--reader-quiescence-record",
            "no undeclared keys",
            "maintenance_window",
            "known_reader_inventory",
            "checked_at/expires_at",
            "zero active readers",
            "STOP_IF_UNKNOWN",
            "NONE_OBSERVED",
            "expiry is exclusive",
            "recorded claim",
            "re-reads/re-hashes",
            "immediately before every atomic exchange",
        ):
            self.assertIn(required, reserve_text)

        recover_help = io.StringIO()
        with contextlib.redirect_stdout(recover_help):
            with self.assertRaises(SystemExit) as recover_exit:
                publisher._parser().parse_args(["recover", "--help"])
        self.assertEqual(0, recover_exit.exception.code)
        recover_text = " ".join(recover_help.getvalue().split())
        for required in (
            "--reader-quiescence-record",
            "fresh exact schema-2 bounded external attestation",
            "required for complete or rollback",
            "omitted for inspect",
            "byte digest must differ from every prior claim",
            "checked_at must be strictly later",
            "exclusive current-time bounds",
            "precise bound_at",
            "no later than any exchange that cites it",
            "append-linked",
            "exact generation",
            "takeover authorization digest",
            "re-read and re-hashed",
            "immediately before each recovery exchange",
        ):
            self.assertIn(required, recover_text)

        inventory_help = io.StringIO()
        with contextlib.redirect_stdout(inventory_help):
            with self.assertRaises(SystemExit) as inventory_exit:
                publisher._parser().parse_args(["inventory", "--help"])
        self.assertEqual(0, inventory_exit.exception.code)
        inventory_text = " ".join(inventory_help.getvalue().split())
        self.assertIn("{dispatch,judgment,acceptance}", inventory_text)
        self.assertIn(
            "skipped, reordered, and duplicate artifacts fail", inventory_text
        )
        self.assertIn("committed phase returns its existing receipt", inventory_text)
        self.assertIn("exact same path", inventory_text)

    def test_exact_doodle_controller_cli_contract_appends_receipts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="publisher-cli-") as raw:
            harness = PublisherHarness(Path(raw).resolve())
            operation = "controller-cli"
            evidence = harness.evidence_parent / operation
            prepare_receipt = evidence / "prepare.json"
            reader_record = harness.maintenance(operation)
            manifest = harness.root / "controller/finalization-evidence.jsonl"
            manifest.parent.mkdir()
            finalization.initialize_manifest(
                manifest,
                finalization_id="finalization-test-1",
                writer_controller_id="controller-test-1",
                state="PREPARING",
                recorded_at="2026-08-09T00:00:00Z",
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
                _, prepared_state = publisher._load_state(harness.state, operation)
                finalization.append_finalization_record(
                    manifest,
                    record_type="prepare_receipt_registered",
                    writer_controller_id="controller-test-1",
                    payload={
                        "operation_id": operation,
                        "generation_id": prepared_state["generation_id"],
                        "receipt_path": str(prepare_receipt),
                        "receipt_sha256": prepared_state["prepare_receipt"]["sha256"],
                        "mutation_outcome": "NO_LIVE_MUTATION_PREPARED",
                        "state": "PREPARED",
                        "next_action": "reserve when separately authorized",
                    },
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
                    harness.seal_phase(operation, phase)
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
            self.assertEqual(
                list(range(1, 14)), [record["sequence"] for record in records]
            )
            self.assertEqual(
                [
                    "manifest_header",
                    "prepare_receipt_registered",
                    "installed_publication_reservation_intent",
                    "installed_publication_terminal",
                    "raw_input_registered",
                    "manifest_prefix_registered",
                    "review_report_registered",
                    "challenge_response_registered",
                    "judge_verdict_registered",
                    "manifest_prefix_registered",
                    "review_summary",
                    "manifest_prefix_registered",
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
