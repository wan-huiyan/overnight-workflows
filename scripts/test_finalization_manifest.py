#!/usr/bin/env python3
"""Mutation controls for the canonical finalization-manifest seam."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import finalization_manifest as manifest  # noqa: E402
import validate_panel_inputs as panel_inputs  # noqa: E402


class FinalizationManifestTests(unittest.TestCase):
    def make_manifest(self, root: Path, name: str = "manifest.jsonl") -> Path:
        path = root / name
        manifest.initialize_manifest(
            path,
            finalization_id="finalization-test",
            writer_controller_id="controller-test",
            state="PREPARING",
            recorded_at="2026-08-09T00:00:00Z",
        )
        return path

    def append_artifact(self, path: Path, *, suffix: str = "one") -> dict[str, object]:
        return manifest.append_finalization_record(
            path,
            record_type="artifact_registered",
            writer_controller_id="controller-test",
            recorded_at="2026-08-09T00:00:01Z",
            payload={
                "review_id": "review-test",
                "artifact_kind": f"artifact-{suffix}",
                "path": str(path.parent / f"artifact-{suffix}.txt"),
                "sha256": hashlib.sha256(suffix.encode()).hexdigest(),
                "state": "REGISTERED",
                "next_action": "continue",
            },
        )

    def append_raw_input(self, path: Path, digest: str | None = None) -> str:
        inventory_path = path.parent / "raw-input.inventory"
        raw_root = path.parent / "raw-inputs"
        raw_root.mkdir(exist_ok=True)
        raw_file = raw_root / "generic-input.txt"
        raw_file.write_bytes(b"raw input fixture\n")
        inventory_path.write_text(
            f"{hashlib.sha256(raw_file.read_bytes()).hexdigest()}\t"
            f"{len(raw_file.read_bytes())}\tgeneric-input.txt\n",
            encoding="utf-8",
        )
        if digest is None:
            digest = hashlib.sha256(inventory_path.read_bytes()).hexdigest()
        manifest.append_finalization_record(
            path,
            record_type="raw_input_registered",
            writer_controller_id="controller-test",
            recorded_at="2026-08-09T00:00:02Z",
            payload={
                "review_id": "review-test",
                "review_boundary": "postpublication-installed-snapshot",
                "inventory_path": str(inventory_path),
                "raw_input_inventory_sha256": digest,
                "raw_input_max_files": 100,
                "raw_input_max_total_bytes": 1000000,
                "raw_input_actual_files": 1,
                "raw_input_actual_total_bytes": len(raw_file.read_bytes()),
                "state": "REGISTERED",
                "next_action": "dispatch",
            },
        )
        return digest

    def append_generic_review(self, path: Path) -> None:
        records = self.records(path)
        raw_input = next(
            record
            for record in records
            if record["record_type"] == "raw_input_registered"
            and record["review_id"] == "review-test"
        )
        dispatch = next(
            record
            for record in records
            if record["record_type"] == "manifest_prefix_registered"
            and record["review_id"] == "review-test"
            and record["phase"] == "dispatch"
        )
        for record_type, role_field, role, suffix in (
            ("review_report_registered", "reviewer_role", "reviewer-1", "report"),
            (
                "challenge_response_registered",
                "participant_role",
                "reviewer-1",
                "challenge",
            ),
            ("judge_verdict_registered", "judge_role", "judge-1", "judge"),
        ):
            artifact_path = path.parent / f"{suffix}.md"
            if record_type == "judge_verdict_registered":
                adjudicated = manifest.judgment_input_identity(self.records(path))
                artifact_path = path.parent / "judge.json"
                artifact_path.write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "record_type": manifest.JUDGE_RECEIPT_TYPE,
                            "review_id": "review-test",
                            "raw_input_inventory_sha256": raw_input[
                                "raw_input_inventory_sha256"
                            ],
                            "dispatch_manifest_prefix_sha256": dispatch[
                                "manifest_prefix_sha256"
                            ],
                            **adjudicated,
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
                artifact_path.write_bytes(suffix.encode())
            payload: dict[str, object] = {
                "review_id": "review-test",
                role_field: role,
                "path": str(artifact_path),
                "sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
                "state": "RECEIVED",
                "next_action": "continue",
            }
            if record_type == "judge_verdict_registered":
                payload["verdict"] = "ACCEPT"
            manifest.append_finalization_record(
                path,
                record_type=record_type,
                writer_controller_id="controller-test",
                payload=payload,
            )

    def append_generic_summary(self, path: Path, digest: str = "2" * 64) -> None:
        manifest.append_finalization_record(
            path,
            record_type="review_summary",
            writer_controller_id="controller-test",
            payload={
                "review_id": "review-test",
                "raw_input_inventory_sha256": digest,
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
                "next_action": "accept",
            },
        )

    def seal(
        self, path: Path, phase: str, digest: str | None = None
    ) -> dict[str, object]:
        if digest is None:
            digest = next(
                (
                    str(record["raw_input_inventory_sha256"])
                    for record in reversed(self.records(path))
                    if record.get("review_id") == "review-test"
                    and isinstance(record.get("raw_input_inventory_sha256"), str)
                ),
                "2" * 64,
            )
        return manifest.seal_manifest_prefix(
            path,
            writer_controller_id="controller-test",
            review_id="review-test",
            phase=phase,
            raw_input_inventory_sha256=digest,
            prefix_output=path.parent / f"manifest-prefix.{phase}.jsonl",
            receipt_output=path.parent / f"manifest-prefix.{phase}.json",
        )

    def records(self, path: Path) -> list[dict[str, object]]:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def write_records(self, path: Path, records: list[dict[str, object]]) -> None:
        path.write_text(
            "".join(
                json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
                for record in records
            ),
            encoding="utf-8",
        )

    def make_real_schema3_panel(self, root: Path) -> dict[str, object]:
        repository = root / "panel-repository"
        repository.mkdir()
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        subprocess.run(
            ["git", "-C", str(repository), "config", "user.name", "Panel Test"],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "config",
                "user.email",
                "panel@example.invalid",
            ],
            check=True,
        )
        source = repository / "source/SKILL.md"
        source.parent.mkdir()
        source.write_text(
            "---\nname: test\ndescription: test\n---\n", encoding="utf-8"
        )
        checker = repository / "scripts/check_large_queue_guidance.py"
        checker.parent.mkdir()
        checker.write_text(
            "#!/usr/bin/env python3\n"
            "import argparse, json\n"
            "from pathlib import Path\n"
            "p=argparse.ArgumentParser(); p.add_argument('--installed-root'); "
            "p.add_argument('--self-test', action='store_true'); "
            "p.add_argument('--json', action='store_true'); a=p.parse_args()\n"
            "ok=(Path(a.installed_root)/'SKILL.md').read_bytes()=="
            "b'---\\nname: test\\ndescription: test\\n---\\n'\n"
            "v='PASS' if ok else 'FAIL'; print(json.dumps({'schema_version':1,"
            "'status':v,'named_mutation_outcomes':{'contract.one':v,'contract.two':v}},"
            "sort_keys=True,separators=(',',':')))\n",
            encoding="utf-8",
        )
        manifest_repository_path = "codex/overnight-workflows/install-manifest.json"
        install_manifest = repository / manifest_repository_path
        install_manifest.parent.mkdir(parents=True)
        install_manifest.write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "mappings": [
                        {
                            "canonical_source": "source/SKILL.md",
                            "installed_path": "SKILL.md",
                        }
                    ],
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "add",
                "source/SKILL.md",
                "scripts/check_large_queue_guidance.py",
                manifest_repository_path,
            ],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repository), "commit", "-qm", "target"], check=True
        )
        target = panel_inputs.git(repository, "rev-parse", "HEAD").decode().strip()
        subprocess.run(
            ["git", "-C", str(repository), "branch", "review-target", target],
            check=True,
        )
        (repository / "review.txt").write_text("review input\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(repository), "add", "review.txt"], check=True
        )
        subprocess.run(
            ["git", "-C", str(repository), "commit", "-qm", "head"], check=True
        )
        head = panel_inputs.git(repository, "rev-parse", "HEAD").decode().strip()
        tree = panel_inputs.git(repository, "rev-parse", "HEAD^{tree}").decode().strip()
        target_tree = (
            panel_inputs.git(repository, "rev-parse", f"{target}^{{tree}}")
            .decode()
            .strip()
        )
        snapshot = root / "panel-snapshot"
        snapshot.mkdir()
        shutil.copy2(source, snapshot / "SKILL.md")
        inventory = panel_inputs.build_inventory(snapshot, ["SKILL.md"])
        inventory_path = root / "panel-snapshot.inventory"
        inventory_path.write_bytes(inventory.data)
        manifest_copy = root / "panel-install-manifest.json"
        shutil.copy2(install_manifest, manifest_copy)
        argv = [
            "git",
            "-C",
            str(repository),
            "diff",
            *panel_inputs.DIFF_FLAGS,
            target,
            head,
            "--",
        ]
        diff = subprocess.run(argv, check=True, stdout=subprocess.PIPE).stdout
        diff_path = root / "panel-source.diff"
        diff_path.write_bytes(diff)
        consumer_argv = [
            "git",
            "-C",
            str(repository),
            "diff",
            *panel_inputs.DIFF_FLAGS,
            target,
            target,
            "--",
        ]
        consumer_diff = subprocess.run(
            consumer_argv, check=True, stdout=subprocess.PIPE
        ).stdout
        consumer_diff_path = root / "panel-consumer.diff"
        consumer_diff_path.write_bytes(consumer_diff)
        immutable_root = root / "panel-operation/immutable-source"
        immutable_checker = immutable_root / "scripts/check_large_queue_guidance.py"
        immutable_checker.parent.mkdir(parents=True)
        shutil.copy2(checker, immutable_checker)
        immutable_inventory = panel_inputs.build_inventory(
            immutable_root, ["scripts/check_large_queue_guidance.py"]
        )
        immutable_inventory_path = root / "panel-immutable.inventory"
        immutable_inventory_path.write_bytes(immutable_inventory.data)
        exchange_slot = immutable_root.parent / "exchange-slot"
        exchange_slot.mkdir()
        shutil.copy2(source, exchange_slot / "SKILL.md")
        outcomes = {"contract.one": "PASS", "contract.two": "PASS"}
        result = {
            "schema_version": 1,
            "status": "PASS",
            "named_mutation_outcomes": outcomes,
        }
        staged = {
            "argv": [
                str(Path(sys.executable).resolve()),
                str(immutable_checker),
                "--installed-root",
                str(exchange_slot),
                "--self-test",
                "--json",
            ],
            "checker_sha256": hashlib.sha256(immutable_checker.read_bytes()).hexdigest(),
            "exit_status": 0,
            "named_mutation_outcomes": outcomes,
            "result": result,
            "stderr": "",
            "stdout": json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n",
        }
        operation_id = "panel-prepare-test"
        receipt_path = root / "panel-prepare.json"
        state_path = root / "panel-state.json"
        manifest_digest = hashlib.sha256(manifest_copy.read_bytes()).hexdigest()
        receipt = {
            "schema_version": 3,
            "operation_id": operation_id,
            "source": {
                "repository": str(repository),
                "commit": head,
                "tree": tree,
                "manifest_path": manifest_repository_path,
                "manifest_sha256": manifest_digest,
            },
            "immutable_source": {
                "root": str(immutable_root),
                "path": str(immutable_inventory_path),
                "format": panel_inputs.INVENTORY_FORMAT,
                "sha256": immutable_inventory.digest,
                "file_count": immutable_inventory.file_count,
                "total_bytes": immutable_inventory.total_bytes,
                "commit": head,
                "tree": tree,
            },
            "expected_live_source": {
                "commit": head,
                "tree": tree,
                "manifest_path": manifest_repository_path,
                "manifest_sha256": manifest_digest,
            },
            "predecessor_source": {
                "root": str(immutable_root),
                "path": str(immutable_inventory_path),
                "format": panel_inputs.INVENTORY_FORMAT,
                "sha256": immutable_inventory.digest,
                "file_count": immutable_inventory.file_count,
                "total_bytes": immutable_inventory.total_bytes,
                "commit": head,
                "tree": tree,
            },
            "candidate_inventory": {
                "format": panel_inputs.INVENTORY_FORMAT,
                "sha256": inventory.digest,
                "file_count": inventory.file_count,
                "total_bytes": inventory.total_bytes,
            },
            "preflight_live_inventory": {
                "format": panel_inputs.INVENTORY_FORMAT,
                "sha256": inventory.digest,
                "file_count": inventory.file_count,
                "total_bytes": inventory.total_bytes,
            },
            "evidence_snapshot": {
                "format": panel_inputs.INVENTORY_FORMAT,
                "sha256": inventory.digest,
                "file_count": inventory.file_count,
                "total_bytes": inventory.total_bytes,
            },
            "generation_id": inventory.digest,
            "mutation_outcome": "NO_LIVE_MUTATION_PREPARED",
            "named_mutation_outcomes": {"staged": outcomes},
            "staged_validation": staged,
            "prepared_at": "2026-08-09T06:00:00Z",
        }
        receipt_data = (
            json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        receipt_path.write_bytes(receipt_data)
        receipt_digest = hashlib.sha256(receipt_data).hexdigest()
        state = {
            "schema_version": 2,
            "operation_id": operation_id,
            "status": "PREPARED",
            "state_root": str(root / "panel-operation-state"),
            "install_root": str(exchange_slot),
            "evidence_root": str(root / "panel-evidence"),
            "source_repository": str(repository),
            "source_commit": head,
            "source_tree": tree,
            "expected_live_source_commit": head,
            "expected_live_source_tree": tree,
            "manifest_path": manifest_repository_path,
            "manifest_sha256": manifest_digest,
            "manifest_schema_version": 3,
            "expected_live_manifest_sha256": manifest_digest,
            "expected_live_manifest_schema_version": 3,
            "generation_id": inventory.digest,
            "candidate_expected_paths": ["SKILL.md"],
            "preflight_expected_paths": ["SKILL.md"],
            "immutable_source": copy.deepcopy(receipt["immutable_source"]),
            "predecessor_source": copy.deepcopy(receipt["predecessor_source"]),
            "candidate_inventory": copy.deepcopy(receipt["candidate_inventory"]),
            "preflight_inventory": copy.deepcopy(receipt["preflight_live_inventory"]),
            "evidence_snapshot": copy.deepcopy(receipt["evidence_snapshot"]),
            "prepare_receipt": {"path": str(receipt_path), "sha256": receipt_digest},
            "validation": {"staged": copy.deepcopy(staged)},
            "created_at": "2026-08-09T05:59:00Z",
            "updated_at": "2026-08-09T06:00:00Z",
            "events": [
                {"at": "2026-08-09T05:59:00Z", "event": "prepare_started"},
                {"at": "2026-08-09T06:00:00Z", "event": "prepare_completed"},
            ],
        }
        state_data = (
            json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        state_path.write_bytes(state_data)
        return {
            "schema_version": 3,
            "record_type": "panel_input",
            "panel_id": "panel-test",
            "finalization_id": "finalization-test",
            "recorded_at": "2026-08-09T06:00:00Z",
            "review_boundary": panel_inputs.PREPUBLICATION_BOUNDARY,
            "repository_roles": {
                "installed_source": "package_source",
                "consumer": "consumer_fixture",
            },
            "repositories": {
                "package_source": {
                    "repository": str(repository),
                    "target_ref": "refs/heads/review-target",
                    "target_ref_sha_at_dispatch": target,
                    "target_sha": target,
                    "merge_base_sha": target,
                    "head_sha": head,
                    "head_tree_oid": tree,
                    "diff_argv": argv,
                    "diff_path": str(diff_path),
                    "diff_digest_algorithm": "SHA-256",
                    "diff_digest": hashlib.sha256(diff).hexdigest(),
                },
                "consumer_fixture": {
                    "repository": str(repository),
                    "target_ref": "refs/heads/review-target",
                    "target_ref_sha_at_dispatch": target,
                    "target_sha": target,
                    "merge_base_sha": target,
                    "head_sha": target,
                    "head_tree_oid": target_tree,
                    "diff_argv": consumer_argv,
                    "diff_path": str(consumer_diff_path),
                    "diff_digest_algorithm": "SHA-256",
                    "diff_digest": hashlib.sha256(consumer_diff).hexdigest(),
                },
            },
            "installed": {
                "root": str(snapshot),
                "inventory_path": str(inventory_path),
                "inventory_format": panel_inputs.INVENTORY_FORMAT,
                "inventory_sha256": inventory.digest,
                "file_count": inventory.file_count,
                "total_bytes": inventory.total_bytes,
                "generation_id": inventory.digest,
                "source_repository": str(repository),
                "source_commit": head,
                "source_tree": tree,
                "install_manifest_path": str(manifest_copy),
                "install_manifest_repository_path": manifest_repository_path,
                "install_manifest_sha256": manifest_digest,
            },
            "prepare": {
                "operation_id": operation_id,
                "receipt_path": str(receipt_path),
                "receipt_sha256": receipt_digest,
                "state_path": str(state_path),
                "state_sha256": hashlib.sha256(state_data).hexdigest(),
                "mutation_outcome": "NO_LIVE_MUTATION_PREPARED",
            },
            "scope": copy.deepcopy(panel_inputs.PREPUBLICATION_SCOPE),
        }

    def source_review_evidence(
        self,
        root: Path,
        panel_record: dict[str, object],
        repository_heads: dict[str, object],
    ) -> tuple[dict[str, object], str]:
        root.mkdir(exist_ok=True)
        panel_path = root / "panel-input.jsonl"
        panel_data = (
            json.dumps(panel_record, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        panel_path.write_bytes(panel_data)
        raw_root = root / "raw-inputs"
        raw_root.mkdir()
        (raw_root / "panel-input.jsonl").write_bytes(panel_data)
        (raw_root / "source.txt").write_bytes(b"source review input\n")
        inventory = panel_inputs.build_inventory(raw_root)
        inventory_path = root / "raw-input.inventory"
        inventory_path.write_bytes(inventory.data)
        validation_path = root / "panel-input-validation-canonical.json"
        validation_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "PASS",
                    "named_mutation_outcomes": {"schema3.integration": "PASS"},
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        panel_digest = hashlib.sha256(panel_data).hexdigest()
        seal_path = root / "raw-input-seal.json"
        seal = {
            "schema_version": 1,
            "record_type": "raw_input_seal",
            "review_id": "review-test",
            "sealed_at": "2026-08-09T06:01:00Z",
            "inventory_format": panel_inputs.INVENTORY_FORMAT,
            "inventory_path": str(inventory_path),
            "inventory_sha256": inventory.digest,
            "raw_input_max_files": 100,
            "raw_input_max_total_bytes": 1000000,
            "raw_input_actual_files": inventory.file_count,
            "raw_input_actual_total_bytes": inventory.total_bytes,
            "panel_input_path": str(panel_path),
            "panel_input_sha256": panel_digest,
            "panel_input_validation_sha256": hashlib.sha256(
                validation_path.read_bytes()
            ).hexdigest(),
            "source_guidance_status_before_review": "NOT_REVIEWED",
            "live_installation_status": "UNCHANGED_PREDECESSOR",
            "live_publication_status": "NOT_RUN",
        }
        seal_path.write_text(
            json.dumps(seal, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        installed = panel_record["installed"]
        prepare = panel_record["prepare"]
        assert isinstance(installed, dict) and isinstance(prepare, dict)
        payload = {
            "review_id": "review-test",
            "panel_id": panel_record["panel_id"],
            "review_boundary": panel_record["review_boundary"],
            "repository_heads": repository_heads,
            "prepared_generation_id": installed["generation_id"],
            "prepare_receipt_sha256": prepare["receipt_sha256"],
            "panel_input_path": str(panel_path),
            "panel_input_sha256": panel_digest,
            "raw_input_inventory_path": str(inventory_path),
            "raw_input_inventory_sha256": inventory.digest,
            "raw_input_seal_path": str(seal_path),
            "raw_input_seal_sha256": hashlib.sha256(seal_path.read_bytes()).hexdigest(),
            "raw_input_max_files": seal["raw_input_max_files"],
            "raw_input_max_total_bytes": seal["raw_input_max_total_bytes"],
            "raw_input_actual_files": inventory.file_count,
            "raw_input_actual_total_bytes": inventory.total_bytes,
            "expected_reports": 1,
            "received_reports": 0,
            "expected_challenge_responses": 1,
            "received_challenge_responses": 0,
            "expected_judges": 1,
            "received_judges": 0,
            "live_installation_status": "UNCHANGED_PREDECESSOR",
            "source_guidance_status": "NOT_REVIEWED",
            "state": "SOURCE_REVIEW_REGISTERED",
            "next_action": "dispatch reviewers",
        }
        return payload, inventory.digest

    def required_validation(
        self,
        root: Path,
        *,
        review_id: str = "review-test",
        status: str = "PASS",
    ) -> tuple[dict[str, object], Path]:
        path = root / "claim-map-validation.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "result_schema": "readiness-claim-map-validation-v1",
                    "review_id": review_id,
                    "identity_coverage_status": status,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        return (
            {
                "artifact_kind": "readiness-claim-map-validation",
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "status_field": "identity_coverage_status",
                "required_status": "PASS",
            },
            path,
        )

    def append_required_validation_artifact(
        self,
        path: Path,
        requirement: dict[str, object],
        *,
        review_id: str = "review-test",
        state: str = "PASS",
    ) -> None:
        manifest.append_finalization_record(
            path,
            record_type="artifact_registered",
            writer_controller_id="controller-test",
            payload={
                "review_id": review_id,
                "artifact_kind": requirement["artifact_kind"],
                "path": requirement["path"],
                "sha256": requirement["sha256"],
                "state": state,
                "next_action": "dispatch source review",
            },
        )

    def make_frozen_legacy_sealed_manifest(
        self,
        root: Path,
        *,
        dispatch_only: bool = False,
        review_boundary: str = manifest.POSTPUBLICATION_REVIEW_BOUNDARY,
    ) -> Path:
        """Reframe a valid v2 lifecycle as archived v1 bytes."""

        path = self.make_manifest(root, "legacy-sealed.jsonl")
        raw_digest = self.append_raw_input(path)
        self.seal(path, "dispatch", raw_digest)
        if not dispatch_only:
            self.append_generic_review(path)
            self.seal(path, "judgment", raw_digest)
            self.append_generic_summary(path, raw_digest)
            self.seal(path, "acceptance", raw_digest)
        records = self.records(path)
        for record in records:
            record["schema_version"] = manifest.LEGACY_SCHEMA_VERSION
            record["manifest_schema"] = manifest.LEGACY_MANIFEST_SCHEMA
            if record["record_type"] == "raw_input_registered":
                record["review_boundary"] = review_boundary
        for index, record in enumerate(records):
            if record["record_type"] == "judge_verdict_registered":
                dispatch = next(
                    item
                    for item in records[:index]
                    if item["record_type"] == "manifest_prefix_registered"
                    and item["phase"] == "dispatch"
                )
                judge_path = root / "legacy-judge.json"
                judge_path.write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "record_type": manifest.JUDGE_RECEIPT_TYPE,
                            "review_id": record["review_id"],
                            "raw_input_inventory_sha256": raw_digest,
                            "dispatch_manifest_prefix_sha256": dispatch[
                                "manifest_prefix_sha256"
                            ],
                            **manifest.judgment_input_identity(records[:index]),
                            "judge_role": record["judge_role"],
                            "verdict": record["verdict"],
                            "recorded_at": "2026-08-09T00:00:03Z",
                            "findings_unresolved": 0,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n",
                    encoding="utf-8",
                )
                record["path"] = str(judge_path)
                record["sha256"] = hashlib.sha256(judge_path.read_bytes()).hexdigest()
            if record["record_type"] != "manifest_prefix_registered":
                continue
            phase = str(record["phase"])
            prefix_data = b"".join(
                (json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n").encode()
                for item in records[:index]
            )
            prefix_path = root / f"legacy-prefix.{phase}.jsonl"
            prefix_path.write_bytes(prefix_data)
            record.update(
                {
                    "manifest_prefix_path": str(prefix_path),
                    "manifest_prefix_sha256": hashlib.sha256(prefix_data).hexdigest(),
                    "manifest_prefix_byte_count": len(prefix_data),
                    "manifest_prefix_record_count": index,
                    "manifest_prefix_last_sequence": records[index - 1]["sequence"],
                }
            )
            receipt = {
                "schema_version": manifest.LEGACY_SCHEMA_VERSION,
                "record_type": manifest.PREFIX_RECEIPT_TYPE,
                "phase": phase,
                "manifest_prefix_path": record["manifest_prefix_path"],
                "manifest_prefix_sha256": record["manifest_prefix_sha256"],
                "manifest_prefix_byte_count": record["manifest_prefix_byte_count"],
                "manifest_prefix_record_count": record["manifest_prefix_record_count"],
                "manifest_prefix_last_sequence": record["manifest_prefix_last_sequence"],
                "finalization_id": record["finalization_id"],
                "writer_controller_id": record["writer_controller_id"],
                "review_id": record["review_id"],
                "raw_input_inventory_sha256": record[
                    "raw_input_inventory_sha256"
                ],
            }
            receipt_path = root / f"legacy-prefix.{phase}.json"
            receipt_path.write_text(
                json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            record["receipt_path"] = str(receipt_path)
            record["receipt_sha256"] = hashlib.sha256(
                receipt_path.read_bytes()
            ).hexdigest()
        self.write_records(path, records)
        return path

    def test_valid_controller_append_round_trips_and_exact_retry_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="finalization-manifest-") as raw:
            path = self.make_manifest(Path(raw).resolve())
            first = self.append_artifact(path)
            retry = self.append_artifact(path)
            self.assertTrue(first["appended"])
            self.assertFalse(retry["appended"])
            identity = manifest.validate_manifest(path)
            self.assertEqual(2, identity["record_count"])
            self.assertEqual("controller-test", identity["writer_controller_id"])

    def test_new_manifest_is_generic_and_rejects_project_specific_head_fields(self) -> None:
        with tempfile.TemporaryDirectory(prefix="finalization-generic-") as raw:
            root = Path(raw).resolve()
            path = self.make_manifest(root)
            self.assertEqual(manifest.MANIFEST_SCHEMA, self.records(path)[0]["manifest_schema"])
            payload = {
                "review_id": "review-test",
                "panel_id": "panel-test",
                "review_boundary": "prepublication-source-and-staged-snapshot",
                "repository_heads": {
                    "installed_source": {
                        "repository_key": "acme/package-source",
                        "head_sha": "1" * 40,
                    },
                    "consumer": {
                        "repository_key": "example/sample-consumer",
                        "head_sha": "2" * 40,
                    },
                },
                "prepared_generation_id": "3" * 64,
                "prepare_receipt_sha256": "4" * 64,
                "panel_input_path": str(root / "panel-input.jsonl"),
                "panel_input_sha256": "5" * 64,
                "raw_input_inventory_path": str(root / "raw-input.inventory"),
                "raw_input_inventory_sha256": "6" * 64,
                "raw_input_seal_path": str(root / "raw-input-seal.json"),
                "raw_input_seal_sha256": "7" * 64,
                "raw_input_max_files": 20,
                "raw_input_max_total_bytes": 1000,
                "raw_input_actual_files": 2,
                "raw_input_actual_total_bytes": 20,
                "expected_reports": 1,
                "received_reports": 0,
                "expected_challenge_responses": 1,
                "received_challenge_responses": 0,
                "expected_judges": 1,
                "received_judges": 0,
                "live_installation_status": "UNCHANGED_PREDECESSOR",
                "source_guidance_status": "NOT_REVIEWED",
                "state": "SOURCE_REVIEW_REGISTERED",
                "next_action": "dispatch reviewers",
            }
            manifest.append_finalization_record(
                path,
                record_type="source_review_input_registered",
                writer_controller_id="controller-test",
                payload=payload,
            )
            self.assertEqual(2, manifest.validate_manifest(path)["record_count"])

            invalid_requirement = copy.deepcopy(payload)
            invalid_requirement["required_pre_dispatch_validation"] = {
                "artifact_kind": "validation",
                "path": str(root / "validation.json"),
                "sha256": "8" * 64,
                "status_field": "status",
                "required_status": "FAIL",
            }
            invalid_path = self.make_manifest(root, "invalid-requirement.jsonl")
            with self.assertRaisesRegex(
                manifest.PublicationError, "required_status must equal PASS"
            ):
                manifest.append_finalization_record(
                    invalid_path,
                    record_type="source_review_input_registered",
                    writer_controller_id="controller-test",
                    payload=invalid_requirement,
                )

            project_specific = copy.deepcopy(self.records(path))
            source = project_specific[1]
            source.pop("repository_heads")
            source["global_head_sha"] = "1" * 40
            source["doodlerun_head_sha"] = "2" * 40
            self.write_records(path, project_specific)
            with self.assertRaisesRegex(manifest.PublicationError, "source_review_input_registered"):
                manifest.validate_manifest(path)

    def test_real_schema3_panel_drives_generic_v2_dispatch_without_mocks(self) -> None:
        with tempfile.TemporaryDirectory(prefix="finalization-schema3-integration-") as raw:
            root = Path(raw).resolve()
            panel_record = self.make_real_schema3_panel(root)
            self.assertEqual([], panel_inputs.validate_panel_input(panel_record, verify=True))
            roles = panel_record["repository_roles"]
            repositories = panel_record["repositories"]
            assert isinstance(roles, dict) and isinstance(repositories, dict)
            repository_heads = {
                role: {
                    "repository_key": repository_key,
                    "head_sha": repositories[repository_key]["head_sha"],
                }
                for role, repository_key in roles.items()
            }
            evidence_root = root / "schema3-evidence"
            payload, raw_digest = self.source_review_evidence(
                evidence_root, panel_record, repository_heads
            )
            requirement, validation_path = self.required_validation(evidence_root)
            payload["required_pre_dispatch_validation"] = requirement
            path = self.make_manifest(evidence_root, "schema3-finalization.jsonl")
            manifest.append_finalization_record(
                path,
                record_type="source_review_input_registered",
                writer_controller_id="controller-test",
                payload=payload,
            )
            before_registration = path.read_bytes()
            with self.assertRaisesRegex(
                manifest.PublicationError, "required PASS artifact registration"
            ):
                self.seal(path, "dispatch", raw_digest)
            self.assertEqual(before_registration, path.read_bytes())
            self.append_required_validation_artifact(path, requirement)
            registered = path.read_bytes()
            validation_bytes = validation_path.read_bytes()
            validation_path.write_bytes(validation_bytes + b"drift")
            with self.assertRaisesRegex(manifest.PublicationError, "digest drifted"):
                self.seal(path, "dispatch", raw_digest)
            self.assertEqual(registered, path.read_bytes())
            validation_path.write_bytes(validation_bytes)
            sealed = self.seal(path, "dispatch", raw_digest)
            self.assertEqual("dispatch", sealed["record"]["phase"])
            self.assertEqual(manifest.MANIFEST_SCHEMA, manifest.validate_manifest(path)["manifest_schema"])

            archived_panel = copy.deepcopy(panel_record)
            archived_panel["schema_version"] = 2
            archived_panel.pop("repository_roles")
            self.assertEqual(
                [], panel_inputs.validate_panel_input(archived_panel, verify=True)
            )
            archived_evidence = root / "schema2-evidence"
            archived_payload, archived_digest = self.source_review_evidence(
                archived_evidence, archived_panel, repository_heads
            )
            archived_attempt = self.make_manifest(root, "schema2-attempt.jsonl")
            manifest.append_finalization_record(
                archived_attempt,
                record_type="source_review_input_registered",
                writer_controller_id="controller-test",
                payload=archived_payload,
            )
            before = archived_attempt.read_bytes()
            with self.assertRaisesRegex(
                manifest.PublicationError, "schema-3 panel repository role map"
            ):
                self.seal(archived_attempt, "dispatch", archived_digest)
            self.assertEqual(before, archived_attempt.read_bytes())

    def test_declared_pre_dispatch_validation_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="finalization-required-gate-") as raw:
            root = Path(raw).resolve()
            panel_record = self.make_real_schema3_panel(root)
            roles = panel_record["repository_roles"]
            repositories = panel_record["repositories"]
            assert isinstance(roles, dict) and isinstance(repositories, dict)
            repository_heads = {
                role: {
                    "repository_key": repository_key,
                    "head_sha": repositories[repository_key]["head_sha"],
                }
                for role, repository_key in roles.items()
            }

            def make_case(
                name: str,
                *,
                receipt_review_id: str = "review-test",
                status: str = "PASS",
                registration_review_id: str = "review-test",
                registration_state: str = "PASS",
                receipt_bytes: bytes | None = None,
            ) -> tuple[Path, str, dict[str, object]]:
                case = root / name
                payload, digest = self.source_review_evidence(
                    case, panel_record, repository_heads
                )
                requirement, validation_path = self.required_validation(
                    case, review_id=receipt_review_id, status=status
                )
                if receipt_bytes is not None:
                    validation_path.write_bytes(receipt_bytes)
                    requirement["sha256"] = hashlib.sha256(receipt_bytes).hexdigest()
                payload["required_pre_dispatch_validation"] = requirement
                path = self.make_manifest(case)
                manifest.append_finalization_record(
                    path,
                    record_type="source_review_input_registered",
                    writer_controller_id="controller-test",
                    payload=payload,
                )
                self.append_required_validation_artifact(
                    path,
                    requirement,
                    review_id=registration_review_id,
                    state=registration_state,
                )
                return path, digest, requirement

            wrong_registration, digest, _ = make_case(
                "wrong-registration", registration_review_id="other-review"
            )
            before = wrong_registration.read_bytes()
            with self.assertRaisesRegex(
                manifest.PublicationError, "required PASS artifact registration"
            ):
                self.seal(wrong_registration, "dispatch", digest)
            self.assertEqual(before, wrong_registration.read_bytes())

            nonpass_registration, digest, _ = make_case(
                "nonpass-registration", registration_state="FAIL"
            )
            before = nonpass_registration.read_bytes()
            with self.assertRaisesRegex(
                manifest.PublicationError, "required PASS artifact registration"
            ):
                self.seal(nonpass_registration, "dispatch", digest)
            self.assertEqual(before, nonpass_registration.read_bytes())

            failed_receipt, digest, _ = make_case("failed-receipt", status="FAIL")
            before = failed_receipt.read_bytes()
            with self.assertRaisesRegex(
                manifest.PublicationError, "not PASS for this review"
            ):
                self.seal(failed_receipt, "dispatch", digest)
            self.assertEqual(before, failed_receipt.read_bytes())

            wrong_receipt, digest, _ = make_case(
                "wrong-receipt", receipt_review_id="other-review"
            )
            before = wrong_receipt.read_bytes()
            with self.assertRaisesRegex(
                manifest.PublicationError, "not PASS for this review"
            ):
                self.seal(wrong_receipt, "dispatch", digest)
            self.assertEqual(before, wrong_receipt.read_bytes())

            duplicate_review, digest, _ = make_case(
                "duplicate-review-key",
                receipt_bytes=(
                    b'{"review_id":"review-test","review_id":"other-review",'
                    b'"identity_coverage_status":"PASS"}\n'
                ),
            )
            before = duplicate_review.read_bytes()
            with self.assertRaisesRegex(
                manifest.PublicationError, "duplicate JSON key 'review_id'"
            ):
                self.seal(duplicate_review, "dispatch", digest)
            self.assertEqual(before, duplicate_review.read_bytes())
            self.assertFalse(
                (duplicate_review.parent / "manifest-prefix.dispatch.jsonl").exists()
            )
            self.assertFalse(
                (duplicate_review.parent / "manifest-prefix.dispatch.json").exists()
            )

            duplicate_status, digest, _ = make_case(
                "duplicate-status-key",
                receipt_bytes=(
                    b'{"review_id":"review-test",'
                    b'"identity_coverage_status":"PASS",'
                    b'"identity_coverage_status":"FAIL"}\n'
                ),
            )
            before = duplicate_status.read_bytes()
            with self.assertRaisesRegex(
                manifest.PublicationError,
                "duplicate JSON key 'identity_coverage_status'",
            ):
                self.seal(duplicate_status, "dispatch", digest)
            self.assertEqual(before, duplicate_status.read_bytes())
            self.assertFalse(
                (duplicate_status.parent / "manifest-prefix.dispatch.jsonl").exists()
            )
            self.assertFalse(
                (duplicate_status.parent / "manifest-prefix.dispatch.json").exists()
            )

            invalidated, digest, requirement = make_case("invalidated")
            manifest.append_finalization_record(
                invalidated,
                record_type="artifact_invalidation",
                writer_controller_id="controller-test",
                payload={
                    "review_id": "review-test",
                    "artifact_kind": requirement["artifact_kind"],
                    "path": requirement["path"],
                    "prior_sha256": requirement["sha256"],
                    "reason": "source evidence changed",
                    "state": "INVALIDATED",
                    "next_action": "rebuild validation",
                },
            )
            before = invalidated.read_bytes()
            with self.assertRaisesRegex(manifest.PublicationError, "was invalidated"):
                self.seal(invalidated, "dispatch", digest)
            self.assertEqual(before, invalidated.read_bytes())

    def test_legacy_project_manifest_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="finalization-legacy-") as raw:
            root = Path(raw).resolve()
            path = root / "legacy.jsonl"
            envelope = {
                "schema_version": manifest.LEGACY_SCHEMA_VERSION,
                "manifest_schema": manifest.LEGACY_MANIFEST_SCHEMA,
                "recorded_at": "2026-08-09T00:00:00Z",
                "finalization_id": "legacy-finalization",
                "writer_controller_id": "legacy-controller",
            }
            source = {
                **envelope,
                "sequence": 2,
                "record_type": "source_review_input_registered",
                "review_id": "legacy-review",
                "panel_id": "legacy-panel",
                "review_boundary": "prepublication-source-and-staged-snapshot",
                "global_head_sha": "1" * 40,
                "doodlerun_head_sha": "2" * 40,
                "prepared_generation_id": "3" * 64,
                "prepare_receipt_sha256": "4" * 64,
                "panel_input_path": str(root / "panel.jsonl"),
                "panel_input_sha256": "5" * 64,
                "raw_input_inventory_path": str(root / "raw.inventory"),
                "raw_input_inventory_sha256": "6" * 64,
                "raw_input_seal_path": str(root / "seal.json"),
                "raw_input_seal_sha256": "7" * 64,
                "raw_input_max_files": 10,
                "raw_input_max_total_bytes": 1000,
                "raw_input_actual_files": 1,
                "raw_input_actual_total_bytes": 1,
                "expected_reports": 1,
                "received_reports": 0,
                "expected_challenge_responses": 1,
                "received_challenge_responses": 0,
                "expected_judges": 1,
                "received_judges": 0,
                "live_installation_status": "UNCHANGED_PREDECESSOR",
                "source_guidance_status": "NOT_REVIEWED",
                "state": "SOURCE_REVIEW_REGISTERED",
                "next_action": "dispatch",
            }
            records = [
                {
                    **envelope,
                    "sequence": 1,
                    "record_type": "manifest_header",
                    "state": "PREPARING",
                },
                source,
                *[
                    {
                        **envelope,
                        "sequence": sequence,
                        "record_type": "external_panel_note",
                        "note": f"legacy-note-{sequence}",
                    }
                    for sequence in range(3, 6)
                ],
            ]
            self.write_records(path, records)
            self.assertEqual(5, manifest.validate_manifest(path)["record_count"])
            before = path.read_bytes()
            with self.assertRaisesRegex(manifest.PublicationError, "read-only"):
                manifest.append_finalization_record(
                    path,
                    record_type="external_panel_note",
                    payload={"note": "must-not-append"},
                )
            self.assertEqual(before, path.read_bytes())

    def test_frozen_legacy_phase_receipts_remain_readable_but_not_writable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="finalization-legacy-sealed-") as raw:
            root = Path(raw).resolve()
            path = self.make_frozen_legacy_sealed_manifest(root)
            baseline_manifest = path.read_bytes()
            identity = manifest.validate_manifest(path)
            self.assertEqual(9, identity["record_count"])
            registrations = [
                record
                for record in self.records(path)
                if record["record_type"] == "manifest_prefix_registered"
            ]
            self.assertEqual(
                ["dispatch", "judgment", "acceptance"],
                [record["phase"] for record in registrations],
            )
            self.assertTrue(
                all(
                    json.loads(Path(str(record["receipt_path"])).read_text())["schema_version"]
                    == manifest.LEGACY_SCHEMA_VERSION
                    for record in registrations
                )
            )
            with self.assertRaisesRegex(manifest.PublicationError, "read-only"):
                manifest.seal_manifest_prefix(
                    path,
                    writer_controller_id="legacy-controller",
                    review_id="review-test",
                    phase="acceptance",
                    raw_input_inventory_sha256=str(
                        registrations[-1]["raw_input_inventory_sha256"]
                    ),
                    prefix_output=root / "forbidden-prefix.jsonl",
                    receipt_output=root / "forbidden-receipt.json",
                )
            self.assertEqual(baseline_manifest, path.read_bytes())

            wrong_schema = Path(str(registrations[0]["receipt_path"]))
            baseline_receipt = wrong_schema.read_bytes()
            receipt = json.loads(baseline_receipt)
            receipt["schema_version"] = manifest.SCHEMA_VERSION
            wrong_schema.write_text(
                json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            mutated_records = self.records(path)
            mutated_records[2]["receipt_sha256"] = hashlib.sha256(
                wrong_schema.read_bytes()
            ).hexdigest()
            self.write_records(path, mutated_records)
            with self.assertRaisesRegex(manifest.PublicationError, "does not match"):
                manifest.validate_manifest(path)
            wrong_schema.write_bytes(baseline_receipt)
            path.write_bytes(baseline_manifest)

            acceptance_receipt = Path(str(registrations[-1]["receipt_path"]))
            baseline_acceptance_receipt = acceptance_receipt.read_bytes()
            acceptance_receipt.write_bytes(baseline_acceptance_receipt + b"drift")
            with self.assertRaisesRegex(manifest.PublicationError, "receipt digest drifted"):
                manifest.validate_manifest(path)
            acceptance_receipt.write_bytes(baseline_acceptance_receipt)

            acceptance_prefix = Path(str(registrations[-1]["manifest_prefix_path"]))
            acceptance_prefix.write_bytes(acceptance_prefix.read_bytes() + b"drift")
            with self.assertRaisesRegex(manifest.PublicationError, "snapshot bytes drifted"):
                manifest.validate_manifest(path)

    def test_frozen_legacy_prepublication_raw_dispatch_remains_readable_but_not_writable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="finalization-legacy-raw-dispatch-") as raw:
            root = Path(raw).resolve()
            path = self.make_frozen_legacy_sealed_manifest(
                root,
                dispatch_only=True,
                review_boundary=manifest.PREPUBLICATION_SOURCE_REVIEW_BOUNDARY,
            )
            baseline_manifest = path.read_bytes()
            records = self.records(path)
            registration = records[-1]
            prefix_path = Path(str(registration["manifest_prefix_path"]))
            receipt_path = Path(str(registration["receipt_path"]))
            baseline_prefix = prefix_path.read_bytes()
            baseline_receipt = receipt_path.read_bytes()

            identity = manifest.validate_manifest(path)
            self.assertEqual(3, identity["record_count"])
            self.assertEqual(manifest.LEGACY_MANIFEST_SCHEMA, identity["manifest_schema"])
            self.assertEqual(
                manifest.PREPUBLICATION_SOURCE_REVIEW_BOUNDARY,
                records[1]["review_boundary"],
            )
            self.assertEqual("dispatch", registration["phase"])

            with self.assertRaisesRegex(manifest.PublicationError, "read-only"):
                manifest.append_finalization_record(
                    path,
                    record_type="external_panel_note",
                    payload={"note": "must-not-append"},
                )
            with self.assertRaisesRegex(manifest.PublicationError, "read-only"):
                manifest.seal_manifest_prefix(
                    path,
                    writer_controller_id="legacy-controller",
                    review_id="review-test",
                    phase="judgment",
                    raw_input_inventory_sha256=str(
                        registration["raw_input_inventory_sha256"]
                    ),
                    prefix_output=root / "forbidden-prefix.jsonl",
                    receipt_output=root / "forbidden-receipt.json",
                )

            self.assertEqual(baseline_manifest, path.read_bytes())
            self.assertEqual(baseline_prefix, prefix_path.read_bytes())
            self.assertEqual(baseline_receipt, receipt_path.read_bytes())
            self.assertFalse((root / "forbidden-prefix.jsonl").exists())
            self.assertFalse((root / "forbidden-receipt.json").exists())

    def test_controller_cli_initializes_and_appends_known_rows_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="finalization-cli-") as raw:
            root = Path(raw).resolve()
            path = root / "manifest.jsonl"
            payload = root / "payload.json"
            payload.write_text(
                json.dumps(
                    {
                        "review_id": "review-test",
                        "artifact_kind": "report",
                        "path": str(root / "report.md"),
                        "sha256": "1" * 64,
                        "state": "REGISTERED",
                        "next_action": "review",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertEqual(
                0,
                manifest.main(
                    [
                        "init",
                        "--manifest",
                        str(path),
                        "--finalization-id",
                        "finalization-test",
                        "--writer-controller-id",
                        "controller-test",
                    ]
                ),
            )
            self.assertEqual(
                0,
                manifest.main(
                    [
                        "append",
                        "--manifest",
                        str(path),
                        "--writer-controller-id",
                        "controller-test",
                        "--record-type",
                        "artifact_registered",
                        "--payload-file",
                        str(payload),
                    ]
                ),
            )
            self.assertEqual(2, manifest.validate_manifest(path)["record_count"])
            self.assertNotIn("invented", manifest.CONTROLLER_RECORD_TYPES)

    def test_seal_prefix_is_create_once_registered_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="finalization-seal-") as raw:
            root = Path(raw).resolve()
            path = self.make_manifest(root)
            self.append_artifact(path)
            raw_digest = self.append_raw_input(path)
            prefix = root / "manifest-prefix.dispatch.jsonl"
            receipt = root / "manifest-prefix.dispatch.json"
            arguments = {
                "writer_controller_id": "controller-test",
                "review_id": "review-test",
                "phase": "dispatch",
                "raw_input_inventory_sha256": raw_digest,
                "prefix_output": prefix,
                "receipt_output": receipt,
            }
            first = manifest.seal_manifest_prefix(path, **arguments)
            retry = manifest.seal_manifest_prefix(path, **arguments)
            self.assertTrue(first["appended"])
            self.assertFalse(retry["appended"])
            self.assertEqual(first["receipt"], retry["receipt"])
            registration = manifest.latest_phase_registration(path, phase="dispatch")
            self.assertEqual(raw_digest, registration["raw_input_inventory_sha256"])
            self.assertEqual(prefix.read_bytes(), path.read_bytes()[: prefix.stat().st_size])

    def test_manifest_grammar_mutations_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="finalization-mutations-") as raw:
            root = Path(raw).resolve()
            base = self.make_manifest(root, "base.jsonl")
            self.append_artifact(base)
            baseline = self.records(base)
            mutations: dict[str, list[dict[str, object]]] = {}

            missing_header = copy.deepcopy(baseline[1:])
            missing_header[0]["sequence"] = 1
            mutations["missing header"] = missing_header
            wrong_header = copy.deepcopy(baseline)
            wrong_header[0]["record_type"] = "artifact_registered"
            mutations["wrong header"] = wrong_header
            start_sequence = copy.deepcopy(baseline)
            start_sequence[0]["sequence"] = 2
            mutations["start sequence"] = start_sequence
            gap = copy.deepcopy(baseline)
            gap[1]["sequence"] = 3
            mutations["gap"] = gap
            duplicate = copy.deepcopy(baseline)
            duplicate[1]["sequence"] = 1
            mutations["duplicate"] = duplicate
            schema = copy.deepcopy(baseline)
            schema[1]["schema_version"] = 999
            mutations["schema drift"] = schema
            boolean_schema = copy.deepcopy(baseline)
            boolean_schema[0]["schema_version"] = True
            mutations["boolean schema"] = boolean_schema
            boolean_sequence = copy.deepcopy(baseline)
            boolean_sequence[0]["sequence"] = True
            mutations["boolean sequence"] = boolean_sequence
            manifest_schema = copy.deepcopy(baseline)
            manifest_schema[1]["manifest_schema"] = "invented"
            mutations["manifest schema drift"] = manifest_schema
            finalization = copy.deepcopy(baseline)
            finalization[1]["finalization_id"] = "other-finalization"
            mutations["finalization drift"] = finalization
            writer = copy.deepcopy(baseline)
            writer[1]["writer_controller_id"] = "intruder"
            mutations["writer drift"] = writer
            invalid_time = copy.deepcopy(baseline)
            invalid_time[1]["recorded_at"] = "not-a-time"
            mutations["invalid time"] = invalid_time
            unknown_type = copy.deepcopy(baseline)
            unknown_type[1]["record_type"] = "invented_record"
            mutations["unknown type"] = unknown_type
            unknown_field = copy.deepcopy(baseline)
            unknown_field[1]["undeclared"] = True
            mutations["undeclared field"] = unknown_field
            reproduced = copy.deepcopy(baseline)
            reproduced[1].update(
                {
                    "sequence": 3,
                    "schema_version": 999,
                    "writer_controller_id": "intruder",
                    "recorded_at": "not-a-time",
                    "record_type": "invented_record",
                }
            )
            mutations["judge two-row mutation"] = reproduced

            for index, (name, records) in enumerate(mutations.items()):
                candidate = root / f"mutation-{index}.jsonl"
                self.write_records(candidate, records)
                with self.subTest(name=name):
                    with self.assertRaises(manifest.PublicationError):
                        manifest.validate_manifest(candidate)

            partial = root / "partial.jsonl"
            partial.write_bytes(base.read_bytes().removesuffix(b"\n"))
            with self.assertRaises(manifest.PublicationError):
                manifest.validate_manifest(partial)

    def test_raw_input_digest_drift_and_malformed_registration_fail(self) -> None:
        with tempfile.TemporaryDirectory(prefix="finalization-registration-") as raw:
            root = Path(raw).resolve()
            path = self.make_manifest(root)
            for state, digest in (("FIRST", "3" * 64), ("SECOND", "4" * 64)):
                records = self.records(path)
                record = {
                    "schema_version": manifest.SCHEMA_VERSION,
                    "manifest_schema": manifest.MANIFEST_SCHEMA,
                    "sequence": len(records) + 1,
                    "recorded_at": "2026-08-09T00:00:01Z",
                    "record_type": "phase_ready",
                    "finalization_id": "finalization-test",
                    "writer_controller_id": "controller-test",
                    "review_id": "review-test",
                    "phase": "dispatch" if state == "FIRST" else "judgment",
                    "raw_input_inventory_sha256": digest,
                    "state": state,
                    "next_action": "continue",
                }
                records.append(record)
                self.write_records(path, records)
                if state == "FIRST":
                    manifest.validate_manifest(path)
            with self.assertRaisesRegex(manifest.PublicationError, "changes raw-input digest"):
                manifest.validate_manifest(path)

            append_path = self.make_manifest(root, "append-must-not-mutate.jsonl")
            manifest.append_finalization_record(
                append_path,
                record_type="phase_ready",
                writer_controller_id="controller-test",
                payload={
                    "review_id": "review-test",
                    "phase": "dispatch",
                    "raw_input_inventory_sha256": "3" * 64,
                    "state": "FIRST",
                    "next_action": "continue",
                },
            )
            before_rejected_append = append_path.read_bytes()
            with self.assertRaisesRegex(
                manifest.PublicationError, "changes raw-input digest"
            ):
                manifest.append_finalization_record(
                    append_path,
                    record_type="phase_ready",
                    writer_controller_id="controller-test",
                    payload={
                        "review_id": "review-test",
                        "phase": "judgment",
                        "raw_input_inventory_sha256": "4" * 64,
                        "state": "SECOND",
                        "next_action": "continue",
                    },
                )
            self.assertEqual(before_rejected_append, append_path.read_bytes())

            sealed = self.make_manifest(root, "sealed.jsonl")
            self.append_artifact(sealed)
            sealed_digest = self.append_raw_input(sealed)
            manifest.seal_manifest_prefix(
                sealed,
                writer_controller_id="controller-test",
                review_id="review-test",
                phase="dispatch",
                raw_input_inventory_sha256=sealed_digest,
                prefix_output=root / "prefix.jsonl",
                receipt_output=root / "receipt.json",
            )
            sealed_records = self.records(sealed)
            sealed_records[-1]["manifest_prefix_last_sequence"] = 999
            self.write_records(sealed, sealed_records)
            with self.assertRaises(manifest.PublicationError):
                manifest.validate_manifest(sealed)

    def test_raw_input_inventory_is_the_exact_recursive_tree_closure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="finalization-raw-closure-") as raw:
            root = Path(raw).resolve()
            path = self.make_manifest(root)
            raw_digest = self.append_raw_input(path)
            raw_root = root / "raw-inputs"
            before = path.read_bytes()

            extra = raw_root / "undeclared.txt"
            extra.write_bytes(b"not inventoried\n")
            with self.assertRaisesRegex(manifest.PublicationError, "closure"):
                self.seal(path, "dispatch", raw_digest)
            self.assertEqual(before, path.read_bytes())
            extra.unlink()

            empty = raw_root / "undeclared-empty-directory"
            empty.mkdir()
            with self.assertRaisesRegex(manifest.PublicationError, "closure"):
                self.seal(path, "dispatch", raw_digest)
            self.assertEqual(before, path.read_bytes())
            empty.rmdir()

            target = raw_root / "generic-input.txt"
            saved = target.read_bytes()
            target.unlink()
            target.mkdir()
            with self.assertRaises(manifest.PublicationError):
                self.seal(path, "dispatch", raw_digest)
            self.assertEqual(before, path.read_bytes())
            target.rmdir()
            target.write_bytes(saved)

            hardlink = raw_root / "undeclared-hardlink"
            os.link(target, hardlink)
            with self.assertRaisesRegex(manifest.PublicationError, "single-link"):
                self.seal(path, "dispatch", raw_digest)
            self.assertEqual(before, path.read_bytes())
            hardlink.unlink()

            linked = raw_root / "undeclared-link"
            linked.symlink_to(target)
            with self.assertRaisesRegex(manifest.PublicationError, "symlink"):
                self.seal(path, "dispatch", raw_digest)
            self.assertEqual(before, path.read_bytes())
            linked.unlink()

            fifo = raw_root / "undeclared-fifo"
            os.mkfifo(fifo)
            with self.assertRaisesRegex(manifest.PublicationError, "non-regular"):
                self.seal(path, "dispatch", raw_digest)
            self.assertEqual(before, path.read_bytes())
            fifo.unlink()

            target = raw_root / "generic-input.txt"
            stable_bytes = target.read_bytes()
            original_reader = manifest._read_regular_bytes

            def mutate_after_read(candidate: Path, *, label: str) -> bytes:
                data = original_reader(candidate, label=label)
                if candidate == target and "raw-input row" in label:
                    target.write_bytes(data + b"changed after read")
                return data

            with mock.patch.object(
                manifest, "_read_regular_bytes", side_effect=mutate_after_read
            ):
                with self.assertRaisesRegex(
                    manifest.PublicationError, "changed while its members were read"
                ):
                    self.seal(path, "dispatch", raw_digest)
            self.assertEqual(before, path.read_bytes())
            target.write_bytes(stable_bytes)

    def test_prepublication_source_dispatch_rejects_generic_raw_input_bypass(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="finalization-raw-bypass-") as raw:
            root = Path(raw).resolve()
            path = self.make_manifest(root)
            raw_digest = self.append_raw_input(path)
            records = self.records(path)
            raw_input = next(
                record
                for record in records
                if record["record_type"] == "raw_input_registered"
            )
            raw_input["review_boundary"] = (
                manifest.PREPUBLICATION_SOURCE_REVIEW_BOUNDARY
            )
            self.write_records(path, records)
            mutated = path.read_bytes()

            with self.assertRaisesRegex(
                manifest.PublicationError,
                "raw_input_registered is postpublication-only",
            ):
                self.seal(path, "dispatch", raw_digest)

            self.assertEqual(mutated, path.read_bytes())
            self.assertFalse((root / "manifest-prefix.dispatch.jsonl").exists())
            self.assertFalse((root / "manifest-prefix.dispatch.json").exists())

    def test_phase_seals_require_raw_input_and_complete_review_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory(prefix="finalization-lifecycle-negative-") as raw:
            root = Path(raw).resolve()
            path = self.make_manifest(root)
            before = path.read_bytes()
            with self.assertRaisesRegex(manifest.PublicationError, "raw-input digest"):
                self.seal(path, "dispatch")
            self.assertEqual(before, path.read_bytes())
            self.assertFalse((root / "manifest-prefix.dispatch.jsonl").exists())
            self.append_raw_input(path)
            self.seal(path, "dispatch")
            before_judgment = path.read_bytes()
            with self.assertRaisesRegex(
                manifest.PublicationError, "report, challenge, and judge"
            ):
                self.seal(path, "judgment")
            self.assertEqual(before_judgment, path.read_bytes())
            self.assertFalse((root / "manifest-prefix.judgment.jsonl").exists())
            before_out_of_order = path.read_bytes()
            with self.assertRaisesRegex(manifest.PublicationError, "challenges first"):
                manifest.append_finalization_record(
                    path,
                    record_type="judge_verdict_registered",
                    writer_controller_id="controller-test",
                    payload={
                        "review_id": "review-test",
                        "judge_role": "judge-early",
                        "path": str(root / "judge-early.md"),
                        "sha256": "8" * 64,
                        "verdict": "ACCEPT",
                        "state": "RECEIVED",
                        "next_action": "continue",
                    },
                )
            self.assertEqual(before_out_of_order, path.read_bytes())
            self.append_generic_review(path)
            report_path = root / "report.md"
            original_report = report_path.read_bytes()
            report_path.write_bytes(original_report + b"drift")
            drift_before = path.read_bytes()
            with self.assertRaisesRegex(manifest.PublicationError, "digest drifted"):
                self.seal(path, "judgment")
            self.assertEqual(drift_before, path.read_bytes())
            report_path.write_bytes(original_report)
            valid_review_bytes = path.read_bytes()
            records = self.records(path)
            report = next(
                record
                for record in records
                if record["record_type"] == "review_report_registered"
            )
            report_path.write_bytes(b"")
            report["sha256"] = hashlib.sha256(b"").hexdigest()
            self.write_records(path, records)
            empty_before = path.read_bytes()
            with self.assertRaisesRegex(manifest.PublicationError, "must be nonempty"):
                self.seal(path, "judgment")
            self.assertEqual(empty_before, path.read_bytes())
            report_path.write_bytes(original_report)
            path.write_bytes(valid_review_bytes)

            records = self.records(path)
            report = next(
                record
                for record in records
                if record["record_type"] == "review_report_registered"
            )
            report_path.write_bytes(b"changed but self-consistent report")
            report["sha256"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
            self.write_records(path, records)
            rewritten_before = path.read_bytes()
            with self.assertRaisesRegex(
                manifest.PublicationError, "accepted judge decision"
            ):
                self.seal(path, "judgment")
            self.assertEqual(rewritten_before, path.read_bytes())
            report_path.write_bytes(original_report)
            path.write_bytes(valid_review_bytes)

            records = self.records(path)
            judge = next(
                record
                for record in records
                if record["record_type"] == "judge_verdict_registered"
            )
            judge_path = Path(judge["path"])
            accepted_judge_bytes = judge_path.read_bytes()
            forged_receipt = json.loads(accepted_judge_bytes)
            forged_receipt["verdict"] = "REQUEST_CHANGES"
            judge_path.write_text(
                json.dumps(forged_receipt, sort_keys=True, separators=(",", ":"))
                + "\n",
                encoding="utf-8",
            )
            judge["sha256"] = hashlib.sha256(judge_path.read_bytes()).hexdigest()
            self.write_records(path, records)
            forged_before = path.read_bytes()
            with self.assertRaisesRegex(
                manifest.PublicationError, "accepted judge decision"
            ):
                self.seal(path, "judgment")
            self.assertEqual(forged_before, path.read_bytes())
            judge_path.write_bytes(accepted_judge_bytes)
            path.write_bytes(valid_review_bytes)

            records = self.records(path)
            report = next(
                record
                for record in records
                if record["record_type"] == "review_report_registered"
            )
            challenge = next(
                record
                for record in records
                if record["record_type"] == "challenge_response_registered"
            )
            challenge["path"] = report["path"]
            challenge["sha256"] = report["sha256"]
            self.write_records(path, records)
            reused_before = path.read_bytes()
            with self.assertRaisesRegex(manifest.PublicationError, "distinct artifacts"):
                self.seal(path, "judgment")
            self.assertEqual(reused_before, path.read_bytes())
            path.write_bytes(valid_review_bytes)

            records = self.records(path)
            judge = next(
                record
                for record in records
                if record["record_type"] == "judge_verdict_registered"
            )
            judge["verdict"] = "REQUEST_CHANGES"
            self.write_records(path, records)
            rejected_before = path.read_bytes()
            with self.assertRaisesRegex(
                manifest.PublicationError, "report, challenge, and judge"
            ):
                self.seal(path, "judgment")
            self.assertEqual(rejected_before, path.read_bytes())

            incomplete = self.make_manifest(root, "incomplete-review.jsonl")
            self.append_raw_input(incomplete)
            self.seal(incomplete, "dispatch")
            for record_type, role_field, suffix in (
                ("review_report_registered", "reviewer_role", "report"),
                ("challenge_response_registered", "participant_role", "challenge"),
            ):
                manifest.append_finalization_record(
                    incomplete,
                    record_type=record_type,
                    writer_controller_id="controller-test",
                    payload={
                        "review_id": "review-test",
                        role_field: "reviewer-1",
                        "path": str(root / f"{suffix}.md"),
                        "sha256": hashlib.sha256(suffix.encode()).hexdigest(),
                        "state": "MISSING",
                        "next_action": "skip",
                    },
                )
            manifest.append_finalization_record(
                incomplete,
                record_type="judge_verdict_registered",
                writer_controller_id="controller-test",
                payload={
                    "review_id": "review-test",
                    "judge_role": "judge-1",
                    "path": str(root / "judge-incomplete.md"),
                    "sha256": "9" * 64,
                    "verdict": "ACCEPT",
                    "state": "RECEIVED",
                    "next_action": "continue",
                },
            )
            incomplete_before = incomplete.read_bytes()
            with self.assertRaisesRegex(
                manifest.PublicationError, "report, challenge, and judge"
            ):
                self.seal(incomplete, "judgment")
            self.assertEqual(incomplete_before, incomplete.read_bytes())

            complete = self.make_manifest(root, "complete-review.jsonl")
            complete_digest = self.append_raw_input(complete)
            self.seal(complete, "dispatch")
            self.append_generic_review(complete)
            self.seal(complete, "judgment")
            before_summary = complete.read_bytes()
            with self.assertRaisesRegex(manifest.PublicationError, "review summary"):
                self.seal(complete, "acceptance")
            self.assertEqual(before_summary, complete.read_bytes())
            self.append_generic_summary(complete, complete_digest)
            self.seal(complete, "acceptance")
            self.assertEqual(
                ["dispatch", "judgment", "acceptance"],
                [
                    record["phase"]
                    for record in self.records(complete)
                    if record["record_type"] == "manifest_prefix_registered"
                ],
            )

    @mock.patch.object(manifest, "validate_panel_input", return_value=[])
    def test_full_historical_controller_and_publication_lifecycle_round_trips(
        self, panel_validator: mock.Mock
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="finalization-full-prefix-") as raw:
            root = Path(raw).resolve()
            path = self.make_manifest(root)
            raw_inventory = root / "historical-raw-input.inventory"
            raw_root = root / "raw-inputs"
            raw_root.mkdir()
            raw_file = raw_root / "input.txt"
            raw_file.write_bytes(b"historical raw input\n")
            panel_input = root / "panel-input.jsonl"
            panel_input.write_text(
                json.dumps(
                    {
                        "schema_version": 3,
                        "repository_roles": {
                            "installed_source": "package-source",
                            "consumer": "sample-consumer",
                        },
                        "repositories": {
                            "package-source": {"head_sha": "1" * 40},
                            "sample-consumer": {"head_sha": "2" * 40},
                        },
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            reviewer_panel_input = raw_root / "panel-input.jsonl"
            reviewer_panel_input.write_bytes(panel_input.read_bytes())
            raw_inventory.write_text(
                f"{hashlib.sha256(raw_file.read_bytes()).hexdigest()}\t"
                f"{len(raw_file.read_bytes())}\tinput.txt\n"
                f"{hashlib.sha256(reviewer_panel_input.read_bytes()).hexdigest()}\t"
                f"{len(reviewer_panel_input.read_bytes())}\tpanel-input.jsonl\n",
                encoding="utf-8",
            )
            raw_digest = hashlib.sha256(raw_inventory.read_bytes()).hexdigest()
            panel_digest = hashlib.sha256(panel_input.read_bytes()).hexdigest()
            validation = root / "panel-input-validation-canonical.json"
            validation.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "status": "PASS",
                        "named_mutation_outcomes": {"panel.contract": "PASS"},
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            raw_seal = root / "raw-input-seal.json"
            seal_value = {
                "schema_version": 1,
                "record_type": "raw_input_seal",
                "review_id": "review-test",
                "sealed_at": "2026-08-09T00:00:00Z",
                "inventory_format": "sha256-size-path-v1",
                "inventory_path": str(raw_inventory),
                "inventory_sha256": raw_digest,
                "raw_input_max_files": 200,
                "raw_input_max_total_bytes": 10000000,
                "raw_input_actual_files": 2,
                "raw_input_actual_total_bytes": len(raw_file.read_bytes())
                + len(reviewer_panel_input.read_bytes()),
                "panel_input_path": str(panel_input),
                "panel_input_sha256": panel_digest,
                "panel_input_validation_sha256": hashlib.sha256(
                    validation.read_bytes()
                ).hexdigest(),
                "source_guidance_status_before_review": "NOT_REVIEWED",
                "live_installation_status": "UNCHANGED_PREDECESSOR",
                "live_publication_status": "NOT_RUN",
            }
            raw_seal.write_text(
                json.dumps(seal_value, sort_keys=True) + "\n", encoding="utf-8"
            )
            seal_digest = hashlib.sha256(raw_seal.read_bytes()).hexdigest()
            artifact_path = root / "artifact.md"
            artifact_path.write_bytes(b"review artifact\n")
            artifact = {
                "path": str(artifact_path),
                "sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
                "bytes": len(artifact_path.read_bytes()),
                "lines": 1,
                "role": "reviewer-1",
            }
            challenge_path = root / "challenge.md"
            challenge_path.write_bytes(b"challenge artifact\n")
            challenge_artifact = {
                "path": str(challenge_path),
                "sha256": hashlib.sha256(challenge_path.read_bytes()).hexdigest(),
                "bytes": len(challenge_path.read_bytes()),
                "lines": 1,
                "role": "challenger-1",
            }
            source_rows: list[tuple[str, dict[str, object]]] = [
                (
                    "source_review_input_registered",
                    {
                        "review_id": "review-test",
                        "panel_id": "panel-test",
                        "review_boundary": "prepublication-source-and-staged-snapshot",
                        "repository_heads": {
                            "installed_source": {
                                "repository_key": "package-source",
                                "head_sha": "1" * 40,
                            },
                            "consumer": {
                                "repository_key": "sample-consumer",
                                "head_sha": "2" * 40,
                            },
                        },
                        "prepared_generation_id": "3" * 64,
                        "prepare_receipt_sha256": "4" * 64,
                        "panel_input_path": str(panel_input),
                        "panel_input_sha256": panel_digest,
                        "raw_input_inventory_path": str(raw_inventory),
                        "raw_input_inventory_sha256": raw_digest,
                        "raw_input_seal_path": str(raw_seal),
                        "raw_input_seal_sha256": seal_digest,
                        "raw_input_max_files": 200,
                        "raw_input_max_total_bytes": 10000000,
                        "raw_input_actual_files": 2,
                        "raw_input_actual_total_bytes": len(raw_file.read_bytes())
                        + len(reviewer_panel_input.read_bytes()),
                        "expected_reports": 1,
                        "received_reports": 0,
                        "expected_challenge_responses": 1,
                        "received_challenge_responses": 0,
                        "expected_judges": 1,
                        "received_judges": 0,
                        "live_installation_status": "UNCHANGED_PREDECESSOR",
                        "source_guidance_status": "NOT_REVIEWED",
                        "state": "SOURCE_REVIEW_REGISTERED",
                        "next_action": "dispatch reviewers",
                    },
                ),
                (
                    "source_review_independent_reports_received",
                    {
                        "review_id": "review-test",
                        "panel_id": "panel-test",
                        "raw_input_inventory_sha256": raw_digest,
                        "expected_reports": 1,
                        "received_reports": 1,
                        "expected_challenge_responses": 1,
                        "received_challenge_responses": 0,
                        "findings_received": 4,
                        "p0_received": 0,
                        "p1_received": 2,
                        "p2_received": 2,
                        "p3_received": 0,
                        "reports": [artifact],
                        "source_guidance_status": "REVIEW_PENDING_CHALLENGE",
                        "state": "CHALLENGE_PENDING",
                        "next_action": "challenge",
                    },
                ),
                (
                    "source_review_challenges_received",
                    {
                        "review_id": "review-test",
                        "panel_id": "panel-test",
                        "raw_input_inventory_sha256": raw_digest,
                        "expected_challenge_responses": 1,
                        "received_challenge_responses": 1,
                        "findings_received": 4,
                        "findings_answered": 4,
                        "deduplicated_findings_reported_min": 3,
                        "deduplicated_findings_reported_max": 4,
                        "challenges": [challenge_artifact],
                        "source_guidance_status": "REVIEW_PENDING_JUDGE",
                        "state": "JUDGE_PENDING",
                        "next_action": "judge",
                    },
                ),
                (
                    "source_review_judge_verdict_received",
                    {
                        "review_id": "review-test",
                        "panel_id": "panel-test",
                        "raw_input_inventory_sha256": raw_digest,
                        "judge": {**artifact, "verdict": "ACCEPT"},
                        "accepted_findings": ["G3-001", "G3-002", "G3-003"],
                        "merged_findings": {"G3-004": "G3-003"},
                        "live_installation_status": "UNCHANGED_PREDECESSOR",
                        "source_guidance_status": "SOURCE_GUIDANCE_ACCEPTED",
                        "state": "SOURCE_ACCEPTED",
                        "next_action": "prepare postpublication review",
                    },
                ),
            ]
            manifest.append_finalization_record(
                path,
                record_type=source_rows[0][0],
                writer_controller_id="controller-test",
                payload=source_rows[0][1],
            )
            original_manifest = path.read_bytes()
            panel_validator.return_value = ["full panel validation failed"]
            with self.assertRaisesRegex(
                manifest.PublicationError, "full panel validation failed"
            ):
                self.seal(path, "dispatch", raw_digest)
            self.assertEqual(original_manifest, path.read_bytes())
            panel_validator.return_value = []
            head_mutation = self.records(path)
            head_mutation[1]["repository_heads"]["consumer"]["head_sha"] = "9" * 40
            self.write_records(path, head_mutation)
            mutated_head_bytes = path.read_bytes()
            with self.assertRaisesRegex(manifest.PublicationError, "panel role map"):
                self.seal(path, "dispatch", raw_digest)
            self.assertEqual(mutated_head_bytes, path.read_bytes())
            path.write_bytes(original_manifest)
            for label, target in (
                ("raw entry", raw_file),
                ("panel input", panel_input),
                ("reviewer panel input", reviewer_panel_input),
                ("validation receipt", validation),
                ("raw seal", raw_seal),
            ):
                original_bytes = target.read_bytes()
                target.write_bytes(original_bytes + b"drift")
                with self.subTest(drift=label):
                    with self.assertRaises(manifest.PublicationError):
                        self.seal(path, "dispatch", raw_digest)
                    self.assertEqual(original_manifest, path.read_bytes())
                target.write_bytes(original_bytes)
            undeclared = raw_root / "undeclared.txt"
            undeclared.write_bytes(b"not inventoried\n")
            with self.assertRaisesRegex(manifest.PublicationError, "closure"):
                self.seal(path, "dispatch", raw_digest)
            self.assertEqual(original_manifest, path.read_bytes())
            undeclared.unlink()
            undeclared_empty = raw_root / "undeclared-empty"
            undeclared_empty.mkdir()
            with self.assertRaisesRegex(manifest.PublicationError, "closure"):
                self.seal(path, "dispatch", raw_digest)
            self.assertEqual(original_manifest, path.read_bytes())
            undeclared_empty.rmdir()
            original_raw = raw_file.read_bytes()
            raw_file.unlink()
            with self.assertRaises(manifest.PublicationError):
                self.seal(path, "dispatch", raw_digest)
            self.assertEqual(original_manifest, path.read_bytes())
            raw_file.write_bytes(original_raw)
            linked_target = raw_root / "linked-target.txt"
            raw_file.rename(linked_target)
            raw_file.symlink_to(linked_target)
            with self.assertRaisesRegex(manifest.PublicationError, "symlink"):
                self.seal(path, "dispatch", raw_digest)
            self.assertEqual(original_manifest, path.read_bytes())
            raw_file.unlink()
            linked_target.rename(raw_file)
            dispatch = self.seal(path, "dispatch", raw_digest)
            before_out_of_order = path.read_bytes()
            with self.assertRaisesRegex(manifest.PublicationError, "reports first"):
                manifest.append_finalization_record(
                    path,
                    record_type=source_rows[2][0],
                    writer_controller_id="controller-test",
                    payload=source_rows[2][1],
                )
            self.assertEqual(before_out_of_order, path.read_bytes())
            for record_type, payload in source_rows[1:3]:
                manifest.append_finalization_record(
                    path,
                    record_type=record_type,
                    writer_controller_id="controller-test",
                    payload=payload,
                )
            adjudicated = manifest.judgment_input_identity(self.records(path))
            judge_path = root / "source-judge.json"
            judge_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "record_type": manifest.JUDGE_RECEIPT_TYPE,
                        "review_id": "review-test",
                        "raw_input_inventory_sha256": raw_digest,
                        "dispatch_manifest_prefix_sha256": dispatch["record"][
                            "manifest_prefix_sha256"
                        ],
                        **adjudicated,
                        "judge_role": "judge-1",
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
            source_rows[3][1]["judge"] = {
                "path": str(judge_path),
                "sha256": hashlib.sha256(judge_path.read_bytes()).hexdigest(),
                "bytes": len(judge_path.read_bytes()),
                "lines": 1,
                "role": "judge-1",
                "verdict": "ACCEPT",
            }
            manifest.append_finalization_record(
                path,
                record_type=source_rows[3][0],
                writer_controller_id="controller-test",
                payload=source_rows[3][1],
            )
            valid_before_count_mutation = path.read_bytes()
            artifact_count_mutation = self.records(path)
            historical_report = next(
                record
                for record in artifact_count_mutation
                if record["record_type"] == "source_review_independent_reports_received"
            )
            historical_report["reports"][0]["bytes"] += 1
            self.write_records(path, artifact_count_mutation)
            count_drift_before = path.read_bytes()
            with self.assertRaisesRegex(
                manifest.PublicationError, "recorded byte count drifted"
            ):
                self.seal(path, "judgment", raw_digest)
            self.assertEqual(count_drift_before, path.read_bytes())
            path.write_bytes(valid_before_count_mutation)

            judge_count_mutation = self.records(path)
            historical_verdict = next(
                record
                for record in judge_count_mutation
                if record["record_type"] == "source_review_judge_verdict_received"
            )
            historical_verdict["judge"]["bytes"] += 1
            self.write_records(path, judge_count_mutation)
            judge_count_drift_before = path.read_bytes()
            with self.assertRaisesRegex(
                manifest.PublicationError, "recorded byte count drifted"
            ):
                self.seal(path, "judgment", raw_digest)
            self.assertEqual(judge_count_drift_before, path.read_bytes())
            path.write_bytes(valid_before_count_mutation)

            mutated = self.records(path)
            report_row = next(
                record
                for record in mutated
                if record["record_type"] == "source_review_independent_reports_received"
            )
            report_row["expected_reports"] = 2
            report_row["received_reports"] = 2
            self.write_records(path, mutated)
            rejected_count_bytes = path.read_bytes()
            with self.assertRaisesRegex(
                manifest.PublicationError, "report, challenge, and judge"
            ):
                self.seal(path, "judgment", raw_digest)
            self.assertEqual(rejected_count_bytes, path.read_bytes())
            path.write_bytes(valid_before_count_mutation)
            self.seal(path, "judgment", raw_digest)
            before_acceptance = path.read_bytes()
            with self.assertRaisesRegex(
                manifest.PublicationError, "postpublication review"
            ):
                self.seal(path, "acceptance", raw_digest)
            self.assertEqual(before_acceptance, path.read_bytes())
            identity = manifest.validate_manifest(path)
            self.assertEqual("manifest_prefix_registered", identity["last_record_type"])
            self.assertEqual(7, identity["record_count"])
            self.assertTrue(panel_validator.called)
            self.assertTrue(
                all(
                    call.kwargs.get("verify") is True
                    for call in panel_validator.call_args_list
                )
            )

    def test_publisher_rows_have_exact_schemas_types_and_prepare_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory(prefix="finalization-publisher-schema-") as raw:
            root = Path(raw).resolve()
            path = self.make_manifest(root)
            operation = "operation-test"
            generation = "generation-test"
            prepare_path = root / "prepare.json"
            preflight_inventory = {
                "format": "sha256-size-path-v1",
                "sha256": "1" * 64,
                "file_count": 1,
                "total_bytes": 1,
                "path": str(root / "preflight.inventory"),
                "identity_kind": "exact-installed-paths-and-file-bytes",
                "installed_paths": ["SKILL.md"],
            }
            candidate_inventory = {
                "format": "sha256-size-path-v1",
                "sha256": "2" * 64,
                "file_count": 1,
                "total_bytes": 1,
                "path": str(root / "candidate.inventory"),
                "identity_kind": "exact-installed-paths-and-file-bytes",
                "installed_paths": ["SKILL.md"],
            }
            prepare_receipt = {
                "schema_version": 3,
                "operation_id": operation,
                "generation_id": generation,
                "source": {},
                "immutable_source": {},
                "expected_live_source": {},
                "predecessor_source": {},
                "candidate_inventory": candidate_inventory,
                "preflight_live_inventory": preflight_inventory,
                "evidence_snapshot": {},
                "staged_validation": {},
                "named_mutation_outcomes": {"staged": {"control": "PASS"}},
                "mutation_outcome": "NO_LIVE_MUTATION_PREPARED",
                "prepared_at": "2026-08-09T00:00:00Z",
            }
            prepare_path.write_text(
                json.dumps(prepare_receipt, sort_keys=True) + "\n", encoding="utf-8"
            )
            prepare_digest = hashlib.sha256(prepare_path.read_bytes()).hexdigest()
            reader_path = root / "reader.json"
            reader_receipt = {
                "schema_version": 2,
                "record_type": "external_reader_quiescence_attestation",
                "operation_id": operation,
                "authorized_by": "operator-test",
                "maintenance_window": {
                    "id": "window-test",
                    "starts_at": "2026-08-09T00:00:00Z",
                    "ends_at": "2026-08-09T00:10:00Z",
                },
                "known_reader_inventory": {
                    "scope": "test",
                    "method": "test",
                    "evidence_reference": "test",
                    "inventory_complete": True,
                    "known_reader_count": 0,
                    "known_active_reader_count": 0,
                    "unknown_reader_policy": "STOP_IF_UNKNOWN",
                    "unknown_reader_status": "CLEAR",
                    "checked_at": "2026-08-09T00:00:00Z",
                    "expires_at": "2026-08-09T00:10:00Z",
                },
                "publisher_validation_scope": "recorded-claim-only",
                "controller": {
                    "id": "controller-test",
                    "state": "ACTIVE",
                    "owner": {
                        "host": "host-test",
                        "pid": 1,
                        "process_start_identity": "start-test",
                    },
                },
            }
            reader_path.write_text(
                json.dumps(reader_receipt, sort_keys=True) + "\n", encoding="utf-8"
            )
            reader_digest = hashlib.sha256(reader_path.read_bytes()).hexdigest()
            reservation_payload: dict[str, object] = {
                "operation_id": operation,
                "generation_id": generation,
                "installer": "controller-test",
                "installed_root": str(root / "installed"),
                "lock_path": str(root / "package.lock"),
                "prepare_receipt_path": str(prepare_path),
                "prepare_receipt_sha256": prepare_digest,
                "prepare_receipt": prepare_receipt,
                "reader_quiescence_record_path": str(reader_path),
                "reader_quiescence_record_sha256": reader_digest,
                "reader_quiescence_record": reader_receipt,
                "preflight_inventory": preflight_inventory,
                "candidate_inventory": candidate_inventory,
                "expected_live_source_commit": "3" * 40,
                "reservation_state": "INTENT_RECORDED",
                "atomic_operation": "darwin-rename-swap",
                "mandatory_recovery_condition": "inspect before recovery",
            }
            before = path.read_bytes()
            with self.assertRaisesRegex(manifest.PublicationError, "registered prepare"):
                manifest.append_finalization_record(
                    path,
                    record_type="installed_publication_reservation_intent",
                    payload=reservation_payload,
                )
            self.assertEqual(before, path.read_bytes())
            manifest.append_finalization_record(
                path,
                record_type="prepare_receipt_registered",
                writer_controller_id="controller-test",
                payload={
                    "operation_id": operation,
                    "generation_id": generation,
                    "receipt_path": str(prepare_path),
                    "receipt_sha256": prepare_digest,
                    "mutation_outcome": "NO_LIVE_MUTATION_PREPARED",
                    "state": "PREPARED",
                    "next_action": "reserve",
                },
            )
            invalid_reservation = copy.deepcopy(reservation_payload)
            invalid_reservation["prepare_receipt"] = {}
            before_invalid_reservation = path.read_bytes()
            with self.assertRaisesRegex(
                manifest.PublicationError, "exact schema"
            ):
                manifest.append_finalization_record(
                    path,
                    record_type="installed_publication_reservation_intent",
                    payload=invalid_reservation,
                )
            self.assertEqual(before_invalid_reservation, path.read_bytes())
            manifest.append_finalization_record(
                path,
                record_type="installed_publication_reservation_intent",
                payload=reservation_payload,
            )
            terminal_path = root / "terminal.json"
            terminal_receipt = {
                "schema_version": 3,
                "operation_id": operation,
                "generation_id": generation,
                "terminal_state": "PUBLISHED",
                "source": {},
                "expected_live_source": {},
                "evidence_snapshot": {},
                "candidate_inventory": candidate_inventory,
                "live_inventory_at_dispatch": preflight_inventory,
                "live_inventory_immediately_before_swap": None,
                "live_inventory_at_terminal_validation": candidate_inventory,
                "reservation": {},
                "exchange_primitive": None,
                "previous_generation": None,
                "rollback": None,
                "mutation_outcome": "LIVE_PUBLISHED",
                "recovery_takeover_authorization": None,
                "reader_attestation_validation_scope": "recorded-claim-only",
                "reader_attestation_renewals": [],
                "atomic_exchange_reader_attestations": [],
                "reservation_state": "RETAINED_PENDING_PANEL_ACCEPTANCE",
                "finalization_manifest": str(path),
                "validation": {},
                "named_mutation_outcomes": {"terminal": {"control": "PASS"}},
                "finalized_at": "2026-08-09T00:00:00Z",
            }
            terminal_path.write_text(
                json.dumps(terminal_receipt, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            terminal_payload = {
                "operation_id": operation,
                "generation_id": generation,
                "installed_root": str(root / "installed"),
                "lock_path": str(root / "package.lock"),
                "reservation_state": "RETAINED_PENDING_PANEL_ACCEPTANCE",
                "terminal_state": "PUBLISHED",
                "publication_receipt_path": str(terminal_path),
                "publication_receipt_sha256": hashlib.sha256(
                    terminal_path.read_bytes()
                ).hexdigest(),
                "publication_receipt": terminal_receipt,
            }
            invalid_terminal = copy.deepcopy(terminal_payload)
            invalid_terminal["publication_receipt"] = {}
            before_invalid_terminal = path.read_bytes()
            with self.assertRaisesRegex(
                manifest.PublicationError, "exact schema"
            ):
                manifest.append_finalization_record(
                    path,
                    record_type="installed_publication_terminal",
                    payload=invalid_terminal,
                )
            self.assertEqual(before_invalid_terminal, path.read_bytes())
            manifest.append_finalization_record(
                path,
                record_type="installed_publication_terminal",
                payload=terminal_payload,
            )
            original_terminal = terminal_path.read_bytes()
            terminal_path.write_bytes(original_terminal + b"drift")
            with self.assertRaisesRegex(manifest.PublicationError, "digest drifted"):
                manifest.validate_manifest(path)
            terminal_path.write_bytes(original_terminal)
            baseline = self.records(path)
            publisher_indexes = [
                index
                for index, record in enumerate(baseline)
                if str(record["record_type"]).startswith("installed_publication_")
            ]
            for mutation_name, mutate in (
                ("undeclared", lambda row: row.__setitem__("invented", True)),
                ("missing", lambda row: row.pop("installed_root")),
                ("identity type", lambda row: row.__setitem__("operation_id", 7)),
            ):
                for sequence, index in enumerate(publisher_indexes):
                    candidate_records = copy.deepcopy(baseline)
                    mutate(candidate_records[index])
                    candidate = root / f"publisher-{mutation_name}-{sequence}.jsonl"
                    self.write_records(candidate, candidate_records)
                    with self.subTest(mutation=mutation_name, row=index):
                        with self.assertRaises(manifest.PublicationError):
                            manifest.validate_manifest(candidate)

    def test_symlink_and_hardlink_manifests_fail(self) -> None:
        with tempfile.TemporaryDirectory(prefix="finalization-links-") as raw:
            root = Path(raw).resolve()
            path = self.make_manifest(root)
            symlink = root / "symlink.jsonl"
            symlink.symlink_to(path)
            with self.assertRaises(manifest.PublicationError):
                manifest.validate_manifest(symlink)
            hardlink = root / "hardlink.jsonl"
            os.link(path, hardlink)
            with self.assertRaises(manifest.PublicationError):
                manifest.validate_manifest(path)
            hardlink.unlink()
            manifest.validate_manifest(path)

    def test_concurrent_cli_appends_are_serialized_without_loss(self) -> None:
        with tempfile.TemporaryDirectory(prefix="finalization-concurrent-") as raw:
            root = Path(raw).resolve()
            path = self.make_manifest(root)
            processes: list[subprocess.Popen[str]] = []
            for index in range(6):
                payload = root / f"payload-{index}.json"
                payload.write_text(
                    json.dumps(
                        {
                            "review_id": "review-test",
                            "artifact_kind": f"artifact-{index}",
                            "path": str(root / f"artifact-{index}.txt"),
                            "sha256": hashlib.sha256(str(index).encode()).hexdigest(),
                            "state": "REGISTERED",
                            "next_action": "continue",
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                processes.append(
                    subprocess.Popen(
                        [
                            sys.executable,
                            str(SCRIPT_DIR / "finalization_manifest.py"),
                            "append",
                            "--manifest",
                            str(path),
                            "--writer-controller-id",
                            "controller-test",
                            "--record-type",
                            "artifact_registered",
                            "--payload-file",
                            str(payload),
                        ],
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                )
            for process in processes:
                stdout, stderr = process.communicate(timeout=20)
                self.assertEqual(0, process.returncode, (stdout, stderr))
            identity = manifest.validate_manifest(path)
            self.assertEqual(7, identity["record_count"])
            self.assertEqual(list(range(1, 8)), [row["sequence"] for row in self.records(path)])


if __name__ == "__main__":
    unittest.main()
