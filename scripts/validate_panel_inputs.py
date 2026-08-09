#!/usr/bin/env python3
"""Validate exact repository diffs and installed-package snapshots for a panel."""

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Optional

from publish_codex_install import INVENTORY_FORMAT, PublicationError, build_inventory

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "scripts/panel_input_fixtures.json"
SHA1 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+\-]*$")
UTC = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$")
DIFF_FLAGS = ["--binary", "--full-index", "--no-color", "--no-ext-diff", "--no-textconv", "--no-renames", "--diff-algorithm=myers", "--unified=3"]
REPO_FIELDS = {"repository", "target_ref", "target_ref_sha_at_dispatch", "target_sha", "merge_base_sha", "head_sha", "head_tree_oid", "diff_argv", "diff_path", "diff_digest_algorithm", "diff_digest"}
INSTALL_FIELDS = {"root", "inventory_path", "inventory_format", "inventory_sha256", "file_count", "total_bytes", "generation_id", "source_repository", "source_commit", "source_tree", "install_manifest_path", "install_manifest_repository_path", "install_manifest_sha256"}
OMISSION_NAMES = {"missing_target_ref", "missing_target_ref_sha", "missing_diff_argv", "missing_diff_digest", "missing_inventory_path", "missing_inventory_digest", "missing_inventory_count", "missing_inventory_bytes", "missing_generation", "missing_source_commit", "missing_source_tree", "missing_manifest_digest"}
DRIFT_NAMES = {"target_ref_drift", "diff_argv_drift", "diff_digest_drift", "inventory_digest_drift", "inventory_count_drift", "inventory_bytes_drift", "generation_drift", "source_commit_drift", "source_tree_drift", "snapshot_byte_drift"}


def absolute(value: Any) -> Optional[Path]:
    if not isinstance(value, str) or not value or "\x00" in value:
        return None
    path = Path(value)
    return path if path.is_absolute() and os.path.normpath(value) == value else None


def timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not UTC.fullmatch(value):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo == timezone.utc


def git(repository: Path, *arguments: str) -> bytes:
    return subprocess.run(["git", "-C", str(repository), *arguments], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20).stdout


def required(value: Any, fields: set[str], label: str, errors: list[str]) -> bool:
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return False
    missing = sorted(fields - value.keys())
    if missing:
        errors.append(f"{label} missing required fields: {missing}")
    return not missing


def regular_bytes(path: Path, label: str, errors: list[str]) -> Optional[bytes]:
    try:
        if path.is_symlink() or not path.is_file():
            errors.append(f"{label} must be a regular non-symlink file")
            return None
        return path.read_bytes()
    except OSError as exc:
        errors.append(f"cannot read {label}: {exc}")
        return None


def validate_repository(label: str, value: Any, verify: bool, errors: list[str]) -> None:
    if not required(value, REPO_FIELDS, label, errors):
        return
    repository, diff_path = absolute(value.get("repository")), absolute(value.get("diff_path"))
    if repository is None:
        errors.append(f"{label}.repository must be a normalized absolute path")
    if diff_path is None:
        errors.append(f"{label}.diff_path must be a normalized absolute path")
    target_ref = value.get("target_ref")
    if not isinstance(target_ref, str) or not target_ref.strip() or "\x00" in target_ref:
        errors.append(f"{label}.target_ref must be a nonempty literal ref")
    for field in ("target_ref_sha_at_dispatch", "target_sha", "merge_base_sha", "head_sha", "head_tree_oid"):
        if not isinstance(value.get(field), str) or not SHA1.fullmatch(value[field]):
            errors.append(f"{label}.{field} must be a lowercase full Git SHA")
    if value.get("target_sha") != value.get("target_ref_sha_at_dispatch"):
        errors.append(f"{label}.target_sha must equal target_ref_sha_at_dispatch")
    if value.get("diff_digest_algorithm") != "SHA-256":
        errors.append(f"{label}.diff_digest_algorithm must be SHA-256")
    if not isinstance(value.get("diff_digest"), str) or not SHA256.fullmatch(value["diff_digest"]):
        errors.append(f"{label}.diff_digest must be lowercase SHA-256")
    expected_argv = ["git", "diff", *DIFF_FLAGS, value.get("target_sha"), value.get("head_sha"), "--"]
    if value.get("diff_argv") != expected_argv:
        errors.append(f"{label}.diff_argv must equal the deterministic literal argv array")
    if not verify or repository is None or diff_path is None:
        return
    try:
        if git(repository, "rev-parse", "--verify", f"{target_ref}^{{commit}}").decode().strip() != value.get("target_ref_sha_at_dispatch"):
            errors.append(f"{label}.target_ref no longer resolves to its dispatch SHA")
        if git(repository, "rev-parse", f"{value.get('head_sha')}^{{tree}}").decode().strip() != value.get("head_tree_oid"):
            errors.append(f"{label}.head_tree_oid does not match head_sha")
        if git(repository, "merge-base", value.get("target_sha"), value.get("head_sha")).decode().strip() != value.get("merge_base_sha"):
            errors.append(f"{label}.merge_base_sha does not reproduce")
        if value.get("diff_argv") == expected_argv:
            reproduced = subprocess.run(expected_argv, cwd=repository, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30).stdout
            saved = regular_bytes(diff_path, f"{label} diff", errors)
            if saved is not None:
                if saved != reproduced:
                    errors.append(f"{label} saved diff bytes do not reproduce from diff_argv")
                if hashlib.sha256(saved).hexdigest() != value.get("diff_digest"):
                    errors.append(f"{label} saved diff digest differs from diff_digest")
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, UnicodeError) as exc:
        errors.append(f"{label} Git verification failed: {exc}")


def validate_installed(value: Any, verify: bool, errors: list[str]) -> None:
    if not required(value, INSTALL_FIELDS, "installed", errors):
        return
    root = absolute(value.get("root")); inventory_path = absolute(value.get("inventory_path")); repository = absolute(value.get("source_repository")); manifest_path = absolute(value.get("install_manifest_path"))
    for field, path in (("root", root), ("inventory_path", inventory_path), ("source_repository", repository), ("install_manifest_path", manifest_path)):
        if path is None:
            errors.append(f"installed.{field} must be a normalized absolute path")
    if value.get("inventory_format") != INVENTORY_FORMAT:
        errors.append(f"installed.inventory_format must be {INVENTORY_FORMAT}")
    for field in ("inventory_sha256", "install_manifest_sha256"):
        if not isinstance(value.get(field), str) or not SHA256.fullmatch(value[field]):
            errors.append(f"installed.{field} must be lowercase SHA-256")
    for field in ("file_count", "total_bytes"):
        number = value.get(field)
        if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
            errors.append(f"installed.{field} must be a positive integer")
    if not isinstance(value.get("generation_id"), str) or not IDENTITY.fullmatch(value["generation_id"]):
        errors.append("installed.generation_id must be a nonempty normalized identity")
    elif value.get("generation_id") != value.get("inventory_sha256"):
        errors.append("installed.generation_id must equal the candidate inventory digest")
    for field in ("source_commit", "source_tree"):
        if not isinstance(value.get(field), str) or not SHA1.fullmatch(value[field]):
            errors.append(f"installed.{field} must be a lowercase full Git SHA")
    manifest_repo_path = value.get("install_manifest_repository_path")
    parsed = PurePosixPath(manifest_repo_path) if isinstance(manifest_repo_path, str) else None
    if parsed is None or parsed.is_absolute() or not manifest_repo_path or ".." in parsed.parts or parsed.as_posix() != manifest_repo_path:
        errors.append("installed.install_manifest_repository_path must be normalized repository-relative POSIX")
    if not verify or None in (root, inventory_path, repository, manifest_path):
        return
    assert root and inventory_path and repository and manifest_path
    try:
        inventory_path.resolve().relative_to(root.resolve())
    except ValueError:
        pass
    else:
        errors.append("installed.inventory_path must be outside the snapshot root")
    manifest_bytes = regular_bytes(manifest_path, "install manifest", errors)
    inventory_bytes = regular_bytes(inventory_path, "installed inventory", errors)
    if manifest_bytes is None or inventory_bytes is None:
        return
    if hashlib.sha256(manifest_bytes).hexdigest() != value.get("install_manifest_sha256"):
        errors.append("installed install manifest digest differs")
    try:
        manifest = json.loads(manifest_bytes.decode())
        mappings = manifest["mappings"]
        if not isinstance(mappings, list) or not mappings:
            raise ValueError("mappings must be nonempty")
        expected = [item["installed_path"] for item in mappings]
        if len(expected) != len(set(expected)):
            raise ValueError("repeated installed path")
        if sorted(path for path in expected if PurePosixPath(path).name == "SKILL.md") != ["SKILL.md"]:
            raise ValueError("snapshot must expose only root SKILL.md")
        inventory = build_inventory(root, expected)
    except (KeyError, ValueError, UnicodeError, json.JSONDecodeError, PublicationError) as exc:
        errors.append(f"installed snapshot inventory could not be validated: {exc}")
        return
    if inventory.data != inventory_bytes:
        errors.append("installed inventory bytes do not match the snapshot")
    if inventory.digest != value.get("inventory_sha256"):
        errors.append("installed inventory digest differs from inventory_sha256")
    if inventory.file_count != value.get("file_count"):
        errors.append("installed inventory file_count differs")
    if inventory.total_bytes != value.get("total_bytes"):
        errors.append("installed inventory total_bytes differs")
    try:
        if git(repository, "rev-parse", f"{value.get('source_commit')}^{{tree}}").decode().strip() != value.get("source_tree"):
            errors.append("installed source_tree does not match source_commit")
        if git(repository, "show", f"{value.get('source_commit')}:{manifest_repo_path}") != manifest_bytes:
            errors.append("installed manifest is not the one at source_commit")
        for mapping in mappings:
            source = git(repository, "show", f"{value.get('source_commit')}:{mapping['canonical_source']}")
            if source != (root / mapping["installed_path"]).read_bytes():
                errors.append(f"installed snapshot byte differs from source_commit: {mapping['installed_path']}")
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        errors.append(f"installed source verification failed: {exc}")


def validate_panel_input(record: Any, verify: bool = True) -> list[str]:
    errors: list[str] = []
    if not isinstance(record, dict):
        return ["panel input must be an object"]
    if record.get("schema_version") != 2:
        errors.append("panel input must declare schema_version 2")
    if record.get("record_type") != "panel_input":
        errors.append("panel input record_type must be panel_input")
    for field in ("panel_id", "finalization_id"):
        if not isinstance(record.get(field), str) or not IDENTITY.fullmatch(record[field]):
            errors.append(f"panel input {field} must be a normalized identity")
    if not timestamp(record.get("recorded_at")):
        errors.append("panel input recorded_at must be ISO-8601 UTC ending Z")
    repositories = record.get("repositories")
    if not isinstance(repositories, dict) or not repositories:
        errors.append("panel input repositories must be a nonempty object")
    else:
        for name, repository in repositories.items():
            if not isinstance(name, str) or not IDENTITY.fullmatch(name):
                errors.append(f"panel repository key is invalid: {name!r}")
            else:
                validate_repository(f"repositories.{name}", repository, verify, errors)
    validate_installed(record.get("installed"), verify, errors)
    return errors


def fixture_errors(fixtures: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(fixtures, dict) or fixtures.get("schema_version") != 1:
        return ["panel fixtures must declare schema_version 1"]
    for section, expected in (("omission_cases", OMISSION_NAMES), ("drift_cases", DRIFT_NAMES)):
        cases = fixtures.get(section)
        if not isinstance(cases, list) or not cases:
            errors.append(f"panel fixture section {section} must be nonempty")
            continue
        names = [case.get("name") for case in cases if isinstance(case, dict)]
        if len(names) != len(cases) or len(names) != len(set(names)) or set(names) != expected:
            errors.append(f"panel fixture section {section} must have exact unique inventory")
    return errors


def remove_path(value: dict[str, Any], path: list[str]) -> None:
    cursor: Any = value
    for component in path[:-1]:
        cursor = cursor[component]
    cursor.pop(path[-1], None)


def self_test(fixtures: dict[str, Any]) -> list[str]:
    errors = fixture_errors(fixtures)
    if errors:
        return errors
    for section in ("omission_cases", "drift_cases"):
        removed = copy.deepcopy(fixtures); removed.pop(section, None)
        if not fixture_errors(removed):
            errors.append(f"removed panel fixture-class control did not fail: {section}")
    with tempfile.TemporaryDirectory(prefix="panel-input-contract-") as temporary:
        base = Path(temporary).resolve(); repository = base / "repository"; repository.mkdir()
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        subprocess.run(["git", "-C", str(repository), "config", "user.name", "Panel Test"], check=True)
        subprocess.run(["git", "-C", str(repository), "config", "user.email", "panel@example.invalid"], check=True)
        source = repository / "source/SKILL.md"; source.parent.mkdir()
        source.write_text("---\nname: test\ndescription: test\n---\n", encoding="utf-8")
        manifest_repo_path = "codex/overnight-workflows/install-manifest.json"
        manifest_source = repository / manifest_repo_path; manifest_source.parent.mkdir(parents=True)
        manifest_source.write_text(json.dumps({"schema_version": 3, "mappings": [{"canonical_source": "source/SKILL.md", "installed_path": "SKILL.md"}]}, indent=2) + "\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repository), "add", "source/SKILL.md", manifest_repo_path], check=True)
        subprocess.run(["git", "-C", str(repository), "commit", "-qm", "target"], check=True)
        target = git(repository, "rev-parse", "HEAD").decode().strip()
        subprocess.run(["git", "-C", str(repository), "branch", "review-target", target], check=True)
        (repository / "review.txt").write_text("review input\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repository), "add", "review.txt"], check=True)
        subprocess.run(["git", "-C", str(repository), "commit", "-qm", "head"], check=True)
        head = git(repository, "rev-parse", "HEAD").decode().strip()
        tree = git(repository, "rev-parse", "HEAD^{tree}").decode().strip()
        snapshot = base / "snapshot"; snapshot.mkdir(); shutil.copy2(source, snapshot / "SKILL.md")
        inventory = build_inventory(snapshot, ["SKILL.md"])
        inventory_path = base / "snapshot.inventory"; inventory_path.write_bytes(inventory.data)
        manifest_path = base / "install-manifest.json"; shutil.copy2(manifest_source, manifest_path)
        argv = ["git", "diff", *DIFF_FLAGS, target, head, "--"]
        diff = subprocess.run(argv, cwd=repository, check=True, stdout=subprocess.PIPE).stdout
        diff_path = base / "input.diff"; diff_path.write_bytes(diff)
        record = {
            "schema_version": 2, "record_type": "panel_input", "panel_id": "panel-test",
            "finalization_id": "finalization-test", "recorded_at": "2026-08-09T06:00:00Z",
            "repositories": {"global": {
                "repository": str(repository), "target_ref": "refs/heads/review-target",
                "target_ref_sha_at_dispatch": target, "target_sha": target,
                "merge_base_sha": target, "head_sha": head, "head_tree_oid": tree,
                "diff_argv": argv, "diff_path": str(diff_path),
                "diff_digest_algorithm": "SHA-256", "diff_digest": hashlib.sha256(diff).hexdigest()}},
            "installed": {
                "root": str(snapshot), "inventory_path": str(inventory_path),
                "inventory_format": INVENTORY_FORMAT, "inventory_sha256": inventory.digest,
                "file_count": inventory.file_count, "total_bytes": inventory.total_bytes,
                "generation_id": inventory.digest, "source_repository": str(repository),
                "source_commit": head, "source_tree": tree, "install_manifest_path": str(manifest_path),
                "install_manifest_repository_path": manifest_repo_path,
                "install_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest()}}
        baseline = validate_panel_input(record)
        if baseline:
            return errors + ["panel self-test baseline failed: " + " | ".join(baseline)]
        for case in fixtures["omission_cases"]:
            mutated = copy.deepcopy(record); remove_path(mutated, case["path"])
            if not validate_panel_input(mutated, verify=False):
                errors.append(f"panel omission control {case['name']} did not fail")
        for case in fixtures["drift_cases"]:
            mutated = copy.deepcopy(record); mutation = case["mutation"]; restore = False
            if mutation == "target_ref":
                mutated["repositories"]["global"]["target_ref_sha_at_dispatch"] = "0" * 40
                mutated["repositories"]["global"]["target_sha"] = "0" * 40
            elif mutation == "diff_argv": mutated["repositories"]["global"]["diff_argv"] = argv[:-3] + argv[-2:]
            elif mutation == "diff_digest": mutated["repositories"]["global"]["diff_digest"] = "0" * 64
            elif mutation == "inventory_digest": mutated["installed"]["inventory_sha256"] = "0" * 64
            elif mutation == "inventory_count": mutated["installed"]["file_count"] += 1
            elif mutation == "inventory_bytes": mutated["installed"]["total_bytes"] += 1
            elif mutation == "generation": mutated["installed"]["generation_id"] = ""
            elif mutation == "source_commit": mutated["installed"]["source_commit"] = target
            elif mutation == "source_tree": mutated["installed"]["source_tree"] = "0" * 40
            elif mutation == "snapshot_byte":
                (snapshot / "SKILL.md").write_bytes(source.read_bytes() + b"x"); restore = True
            found = validate_panel_input(mutated)
            if restore: shutil.copy2(source, snapshot / "SKILL.md")
            if not any(case["expect"] in item for item in found):
                errors.append(f"panel drift control {case['name']} did not fail as expected: {found}")
    return errors


def load_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return [], [f"cannot read panel manifest: {exc}"]
    if not lines:
        return [], ["panel manifest must be nonempty"]
    records: list[dict[str, Any]] = []; errors: list[str] = []
    for number, line in enumerate(lines, 1):
        try: value = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"panel manifest line {number} is invalid JSON: {exc}"); continue
        if not isinstance(value, dict): errors.append(f"panel manifest line {number} must be an object")
        else: records.append(value)
    return records, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, help="panel JSONL manifest to validate")
    parser.add_argument("--self-test", action="store_true", help="run omission and drift controls")
    args = parser.parse_args()
    if not args.manifest and not args.self_test: parser.error("provide --manifest and/or --self-test")
    errors: list[str] = []
    try: fixtures = json.loads(FIXTURES.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fixtures = {}; errors.append(f"cannot read panel fixtures: {exc}")
    errors.extend(fixture_errors(fixtures))
    if args.self_test and not errors: errors.extend(self_test(fixtures))
    if args.manifest:
        records, found = load_jsonl(args.manifest); errors.extend(found)
        inputs = [record for record in records if record.get("record_type") == "panel_input"]
        if len(inputs) != 1: errors.append(f"panel manifest must contain exactly one panel_input; found {len(inputs)}")
        else: errors.extend(validate_panel_input(inputs[0]))
    if errors:
        for error in errors: print(f"ERROR: {error}")
        return 1
    print("OK: panel input manifest/snapshot contract" + (" and negative controls" if args.self_test else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
