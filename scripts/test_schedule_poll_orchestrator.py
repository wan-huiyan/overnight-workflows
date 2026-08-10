#!/usr/bin/env python3
"""Crash, duplicate-trigger, authority, and corruption controls for polling."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = (
    ROOT
    / "plugins/schedule-poll-orchestrator-pattern/scripts/poll_orchestrator.py"
)
SPEC = importlib.util.spec_from_file_location("poll_orchestrator", HELPER)
assert SPEC is not None and SPEC.loader is not None
poller = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(poller)


class InjectedCrash(RuntimeError):
    pass


class SchedulePollTests(unittest.TestCase):
    def fixture(self) -> tuple[tempfile.TemporaryDirectory[str], Path, Path]:
        temporary = tempfile.TemporaryDirectory(prefix="schedule-poll-")
        root = Path(temporary.name).resolve()
        status = root / "status.json"
        journal = root / "poll.jsonl"
        poller.initialize(
            status_path=status,
            journal_path=journal,
            run_id="run-test",
            dispatch_epoch=100,
            hard_ceiling_seconds=100,
            poll_interval_seconds=30,
            tracks=["track-a", "track-b", "track-c"],
            now=100,
        )
        return temporary, status, journal

    def state(self, path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding="utf-8"))

    def journal(self, path: Path) -> list[dict[str, object]]:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def mark_complete(self, status: Path, journal: Path, track: str, *, now: int) -> None:
        poller.mark_track(
            status_path=status,
            journal_path=journal,
            run_id="run-test",
            track=track,
            phase="complete",
            reason="verified output complete",
            evidence_path=None,
            evidence_sha256=None,
            now=now,
        )

    def authority(self, root: Path, **overrides: bool) -> Path:
        grants = {field: False for field in poller.GRANT_FIELDS}
        grants.update(
            {
                "commit": True,
                "push": True,
                "pull_request": True,
                "network": True,
                "external_write": True,
            }
        )
        grants.update(overrides)
        path = root / "authority.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "record_type": poller.AUTHORITY_TYPE,
                    "run_id": "run-test",
                    "authorized_by": "release-owner",
                    "recorded_at": "2026-08-09T00:00:00Z",
                    "grants": grants,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def exact_authority(self, root: Path, grants: set[str], name: str) -> Path:
        path = root / name
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "record_type": poller.AUTHORITY_TYPE,
                    "run_id": "run-test",
                    "authorized_by": "release-owner",
                    "recorded_at": "2026-08-09T00:00:00Z",
                    "grants": {
                        field: field in grants for field in poller.GRANT_FIELDS
                    },
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def pr_receipt(
        self,
        root: Path,
        operation_id: str,
        *,
        name: str = "pr-receipt.json",
        receipt_operation_id: str | None = None,
    ) -> Path:
        bound_operation = receipt_operation_id or operation_id
        path = root / name
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "record_type": poller.PR_RECEIPT_TYPE,
                    "run_id": "run-test",
                    "operation_id": bound_operation,
                    "idempotency_key": bound_operation,
                    "provider": "github",
                    "repository": "owner/repository",
                    "pull_request_id": "54",
                    "url": "https://github.com/owner/repository/pull/54",
                    "state": "OPEN",
                    "recorded_at": "2026-08-09T00:00:00Z",
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def claim_consolidation(self, status: Path, journal: Path) -> dict[str, object]:
        for index, track in enumerate(("track-a", "track-b", "track-c"), 1):
            self.mark_complete(status, journal, track, now=100 + index)
        return poller.poll(
            status_path=status,
            journal_path=journal,
            run_id="run-test",
            trigger_id="trigger-claim",
            now=110,
        )

    def test_first_poll_reschedules_and_duplicate_trigger_is_exact_replay(self) -> None:
        temporary, status, journal = self.fixture()
        self.addCleanup(temporary.cleanup)
        first = poller.poll(
            status_path=status,
            journal_path=journal,
            run_id="run-test",
            trigger_id="trigger-one",
            now=120,
        )
        replay = poller.poll(
            status_path=status,
            journal_path=journal,
            run_id="run-test",
            trigger_id="trigger-one",
            now=180,
        )
        self.assertEqual("RESCHEDULE", first["action"])
        self.assertEqual(first, replay)
        self.assertEqual(
            1,
            sum(row["event_id"] == "trigger-trigger-one" for row in self.journal(journal)),
        )

    def test_missing_corrupt_or_missing_track_status_fails_without_rewrite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="schedule-poll-missing-") as raw:
            root = Path(raw).resolve()
            with self.assertRaises(poller.PollError):
                poller.poll(
                    status_path=root / "missing.json",
                    journal_path=root / "journal.jsonl",
                    run_id="run-test",
                    trigger_id="trigger-one",
                    now=120,
                )
        temporary, status, journal = self.fixture()
        self.addCleanup(temporary.cleanup)
        status.write_text("{bad", encoding="utf-8")
        before = status.read_bytes()
        with self.assertRaises(poller.PollError):
            poller.poll(
                status_path=status,
                journal_path=journal,
                run_id="run-test",
                trigger_id="trigger-one",
                now=120,
            )
        self.assertEqual(before, status.read_bytes())

        temporary2, status2, journal2 = self.fixture()
        self.addCleanup(temporary2.cleanup)
        value = self.state(status2)
        value["tracks"].pop("track-b")  # type: ignore[index,union-attr]
        status2.write_text(json.dumps(value) + "\n", encoding="utf-8")
        before = status2.read_bytes()
        with self.assertRaisesRegex(poller.PollError, "missing or reorders"):
            poller.poll(
                status_path=status2,
                journal_path=journal2,
                run_id="run-test",
                trigger_id="trigger-two",
                now=120,
            )
        self.assertEqual(before, status2.read_bytes())

    def test_nested_outcome_and_claim_corruption_fails_without_rewrite(self) -> None:
        mutations = (
            (
                "partial outcome",
                lambda value: value["trigger_outcomes"].update(  # type: ignore[union-attr]
                    {"trigger-bad": {"trigger_id": "trigger-bad"}}
                ),
            ),
            (
                "partial consolidation claim",
                lambda value: value.__setitem__(
                    "consolidation", {"state": "CLAIMED", "operation_id": "op-bad"}
                ),
            ),
            (
                "undeclared pull-request claim field",
                lambda value: value.__setitem__(
                    "pull_request", {"state": "NOT_CLAIMED", "invented": True}
                ),
            ),
        )
        for name, mutate in mutations:
            temporary, status, journal = self.fixture()
            self.addCleanup(temporary.cleanup)
            value = self.state(status)
            mutate(value)
            status.write_text(json.dumps(value) + "\n", encoding="utf-8")
            before = status.read_bytes()
            with self.subTest(name=name):
                with self.assertRaises(poller.PollError):
                    poller.poll(
                        status_path=status,
                        journal_path=journal,
                        run_id="run-test",
                        trigger_id="trigger-bad",
                        now=120,
                    )
                self.assertEqual(before, status.read_bytes())

        temporary, status, journal = self.fixture()
        self.addCleanup(temporary.cleanup)
        self.claim_consolidation(status, journal)
        value = self.state(status)
        value["trigger_outcomes"]["trigger-claim"]["operation_id"] = "attacker"  # type: ignore[index,union-attr]
        status.write_text(json.dumps(value) + "\n", encoding="utf-8")
        before = status.read_bytes()
        with self.assertRaisesRegex(poller.PollError, "join|originating"):
            poller.inspect(status_path=status, run_id="run-test")
        self.assertEqual(before, status.read_bytes())

    def test_hard_ceiling_taps_incomplete_tracks_and_claims_once(self) -> None:
        temporary, status, journal = self.fixture()
        self.addCleanup(temporary.cleanup)
        self.mark_complete(status, journal, "track-a", now=150)
        first = poller.poll(
            status_path=status,
            journal_path=journal,
            run_id="run-test",
            trigger_id="trigger-ceiling",
            now=200,
        )
        second = poller.poll(
            status_path=status,
            journal_path=journal,
            run_id="run-test",
            trigger_id="trigger-after-ceiling",
            now=201,
        )
        self.assertEqual("CONSOLIDATION_CLAIMED", first["action"])
        self.assertEqual("RESUME_CONSOLIDATION_CLAIM", second["action"])
        self.assertEqual(first["operation_id"], second["operation_id"])
        phases = {name: track["phase"] for name, track in self.state(status)["tracks"].items()}  # type: ignore[union-attr]
        self.assertEqual(
            {"track-a": "complete", "track-b": "tapped_out", "track-c": "tapped_out"},
            phases,
        )

    def test_reschedule_never_overshoots_the_hard_ceiling(self) -> None:
        temporary, status, journal = self.fixture()
        self.addCleanup(temporary.cleanup)
        result = poller.poll(
            status_path=status,
            journal_path=journal,
            run_id="run-test",
            trigger_id="trigger-near-ceiling",
            now=199,
        )
        self.assertEqual("RESCHEDULE", result["action"])
        self.assertEqual("1970-01-01T00:03:20Z", result["next_trigger_at"])

    def test_crash_after_claim_recovers_same_claim_and_repairs_journal(self) -> None:
        temporary, status, journal = self.fixture()
        self.addCleanup(temporary.cleanup)
        for index, track in enumerate(("track-a", "track-b", "track-c"), 1):
            self.mark_complete(status, journal, track, now=100 + index)

        def crash(point: str) -> None:
            if point == "after_state_replace":
                raise InjectedCrash(point)

        with self.assertRaises(InjectedCrash):
            poller.poll(
                status_path=status,
                journal_path=journal,
                run_id="run-test",
                trigger_id="trigger-crash",
                now=110,
                failpoint=crash,
            )
        claimed = self.state(status)["consolidation"]  # type: ignore[index]
        self.assertEqual("CLAIMED", claimed["state"])
        recovered = poller.poll(
            status_path=status,
            journal_path=journal,
            run_id="run-test",
            trigger_id="trigger-crash",
            now=111,
        )
        self.assertEqual("RESUME_CONSOLIDATION_CLAIM", recovered["action"])
        self.assertEqual(claimed["operation_id"], recovered["operation_id"])
        self.assertEqual(
            1,
            sum(row["event_id"] == "trigger-trigger-crash" for row in self.journal(journal)),
        )

    def test_duplicate_claim_trigger_never_instructs_second_consolidation(self) -> None:
        temporary, status, journal = self.fixture()
        self.addCleanup(temporary.cleanup)
        first = self.claim_consolidation(status, journal)
        replay = poller.poll(
            status_path=status,
            journal_path=journal,
            run_id="run-test",
            trigger_id="trigger-claim",
            now=111,
        )
        self.assertEqual("CONSOLIDATION_CLAIMED", first["action"])
        self.assertEqual("RESUME_CONSOLIDATION_CLAIM", replay["action"])
        self.assertEqual(first["operation_id"], replay["operation_id"])

    def test_consolidation_completion_is_idempotent_and_pr_is_not_implicit(self) -> None:
        temporary, status, journal = self.fixture()
        self.addCleanup(temporary.cleanup)
        claim = self.claim_consolidation(status, journal)
        evidence = Path(temporary.name).resolve() / "consolidated.html"
        evidence.write_text("complete\n", encoding="utf-8")
        digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
        first = poller.complete_consolidation(
            status_path=status,
            journal_path=journal,
            run_id="run-test",
            operation_id=claim["operation_id"],
            evidence_path=evidence,
            evidence_sha256=digest,
            now=120,
        )
        replay = poller.complete_consolidation(
            status_path=status,
            journal_path=journal,
            run_id="run-test",
            operation_id=claim["operation_id"],
            evidence_path=evidence,
            evidence_sha256=digest,
            now=121,
        )
        self.assertEqual("LOCAL_COMPLETE_EXTERNAL_ACTIONS_NOT_AUTHORIZED", first["action"])
        self.assertEqual("CONSOLIDATION_ALREADY_COMPLETE", replay["action"])
        self.assertEqual("NOT_CLAIMED", self.state(status)["pull_request"]["state"])  # type: ignore[index]

    def test_terminal_track_retry_requires_exact_evidence_identity(self) -> None:
        temporary, status, journal = self.fixture()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        first = root / "track-first.txt"
        second = root / "track-second.txt"
        first.write_text("first\n", encoding="utf-8")
        second.write_text("second\n", encoding="utf-8")
        first_digest = hashlib.sha256(first.read_bytes()).hexdigest()
        second_digest = hashlib.sha256(second.read_bytes()).hexdigest()
        poller.mark_track(
            status_path=status,
            journal_path=journal,
            run_id="run-test",
            track="track-a",
            phase="complete",
            reason="verified output complete",
            evidence_path=first,
            evidence_sha256=first_digest,
            now=110,
        )
        before = status.read_bytes()
        with self.assertRaisesRegex(poller.PollError, "conflicts"):
            poller.mark_track(
                status_path=status,
                journal_path=journal,
                run_id="run-test",
                track="track-a",
                phase="complete",
                reason="verified output complete",
                evidence_path=second,
                evidence_sha256=second_digest,
                now=111,
            )
        self.assertEqual(before, status.read_bytes())

    def test_terminal_track_evidence_drift_blocks_consolidation_claim(self) -> None:
        for mutation in ("drift", "delete"):
            temporary, status, journal = self.fixture()
            self.addCleanup(temporary.cleanup)
            root = Path(temporary.name).resolve()
            evidence = root / "track-output.txt"
            evidence.write_text("verified output\n", encoding="utf-8")
            digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
            poller.mark_track(
                status_path=status,
                journal_path=journal,
                run_id="run-test",
                track="track-a",
                phase="complete",
                reason="verified output complete",
                evidence_path=evidence,
                evidence_sha256=digest,
                now=101,
            )
            self.mark_complete(status, journal, "track-b", now=102)
            self.mark_complete(status, journal, "track-c", now=103)
            if mutation == "drift":
                evidence.write_text("changed output\n", encoding="utf-8")
            else:
                evidence.unlink()
            before = status.read_bytes()
            with self.subTest(mutation=mutation):
                with self.assertRaises(poller.PollError):
                    poller.poll(
                        status_path=status,
                        journal_path=journal,
                        run_id="run-test",
                        trigger_id="trigger-evidence-drift",
                        now=110,
                    )
                self.assertEqual(before, status.read_bytes())
                self.assertEqual(
                    "NOT_CLAIMED", self.state(status)["consolidation"]["state"]  # type: ignore[index]
                )

    def test_pr_claim_rechecks_durable_consolidation_evidence(self) -> None:
        for mutation in ("drift", "delete"):
            temporary, status, journal = self.fixture()
            self.addCleanup(temporary.cleanup)
            root = Path(temporary.name).resolve()
            consolidation = self.claim_consolidation(status, journal)
            evidence = root / "consolidated.html"
            evidence.write_text("complete\n", encoding="utf-8")
            digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
            poller.complete_consolidation(
                status_path=status,
                journal_path=journal,
                run_id="run-test",
                operation_id=consolidation["operation_id"],
                evidence_path=evidence,
                evidence_sha256=digest,
                now=120,
            )
            authority = self.authority(root)
            if mutation == "drift":
                evidence.write_text("changed\n", encoding="utf-8")
            else:
                evidence.unlink()
            before = status.read_bytes()
            with self.subTest(mutation=mutation):
                with self.assertRaises(poller.PollError):
                    poller.claim_pull_request(
                        status_path=status,
                        journal_path=journal,
                        run_id="run-test",
                        decision_output=root / "pr-action.json",
                        authority_receipt_path=authority,
                        now=121,
                    )
                self.assertEqual(before, status.read_bytes())
                self.assertEqual(
                    "NOT_CLAIMED", self.state(status)["pull_request"]["state"]  # type: ignore[index]
                )

    def test_crash_after_consolidation_state_recovers_without_second_completion(self) -> None:
        temporary, status, journal = self.fixture()
        self.addCleanup(temporary.cleanup)
        claim = self.claim_consolidation(status, journal)
        evidence = Path(temporary.name).resolve() / "consolidated.html"
        evidence.write_text("complete\n", encoding="utf-8")
        digest = hashlib.sha256(evidence.read_bytes()).hexdigest()

        def crash(point: str) -> None:
            if point == "after_state_replace":
                raise InjectedCrash(point)

        with self.assertRaises(InjectedCrash):
            poller.complete_consolidation(
                status_path=status,
                journal_path=journal,
                run_id="run-test",
                operation_id=claim["operation_id"],
                evidence_path=evidence,
                evidence_sha256=digest,
                now=120,
                failpoint=crash,
            )
        replay = poller.complete_consolidation(
            status_path=status,
            journal_path=journal,
            run_id="run-test",
            operation_id=claim["operation_id"],
            evidence_path=evidence,
            evidence_sha256=digest,
            now=121,
        )
        self.assertEqual("CONSOLIDATION_ALREADY_COMPLETE", replay["action"])
        self.assertEqual(
            1,
            sum(
                row["event_id"] == f"complete-{claim['operation_id']}"
                for row in self.journal(journal)
            ),
        )

    def test_pr_requires_every_separate_grant_and_has_one_durable_claim(self) -> None:
        temporary, status, journal = self.fixture()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        consolidation = self.claim_consolidation(status, journal)
        evidence = root / "consolidated.html"
        evidence.write_text("complete\n", encoding="utf-8")
        digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
        poller.complete_consolidation(
            status_path=status,
            journal_path=journal,
            run_id="run-test",
            operation_id=consolidation["operation_id"],
            evidence_path=evidence,
            evidence_sha256=digest,
            now=120,
        )
        denied = self.authority(root, pull_request=False)
        denied_result = poller.claim_pull_request(
            status_path=status,
            journal_path=journal,
            run_id="run-test",
            decision_output=root / "denied-pr-action.json",
            authority_receipt_path=denied,
            now=121,
        )
        self.assertEqual("MISSING_AUTHORITY", denied_result["action"])
        self.assertFalse(denied_result["decision"]["callable"])
        self.assertIn("pull_request", denied_result["decision"]["missing_grants"])
        self.assertEqual("NOT_CLAIMED", self.state(status)["pull_request"]["state"])  # type: ignore[index]
        denied.unlink()
        granted = self.authority(root)
        first = poller.claim_pull_request(
            status_path=status,
            journal_path=journal,
            run_id="run-test",
            decision_output=root / "granted-pr-action.json",
            authority_receipt_path=granted,
            now=122,
        )
        replay = poller.claim_pull_request(
            status_path=status,
            journal_path=journal,
            run_id="run-test",
            decision_output=root / "granted-pr-action.json",
            authority_receipt_path=granted,
            now=123,
        )
        self.assertEqual("CHECK_OR_CREATE_PULL_REQUEST", first["action"])
        self.assertEqual("RESUME_PULL_REQUEST_CLAIM", replay["action"])
        self.assertEqual(first["idempotency_key"], replay["idempotency_key"])
        self.assertEqual(
            1,
            sum(
                row["event_id"] == f"claim-{first['operation_id']}"
                for row in self.journal(journal)
            ),
        )

    def test_every_external_action_has_a_separate_durable_decision(self) -> None:
        with tempfile.TemporaryDirectory(prefix="schedule-action-decisions-") as raw:
            root = Path(raw).resolve()
            called: list[str] = []
            for action, requirements in poller.ACTION_REQUIREMENTS.items():
                receipt = self.exact_authority(
                    root, set(requirements), f"authority-{action}.json"
                )
                allowed = poller.decide_external_action(
                    run_id="run-test",
                    action=action,
                    decision_output=root / f"allow-{action}.json",
                    authority_receipt_path=receipt,
                )
                self.assertEqual("AUTHORIZED", allowed["result"])
                self.assertTrue(allowed["decision"]["callable"])
                before = list(called)
                guarded = poller.run_guarded_action(
                    decision=allowed["decision"],
                    action=action,
                    callback=called.append,
                )
                self.assertTrue(guarded["called"])
                self.assertEqual(before + [action], called)
                for removed in requirements:
                    partial = self.exact_authority(
                        root,
                        set(requirements) - {removed},
                        f"partial-{action}-{removed}.json",
                    )
                    denied = poller.decide_external_action(
                        run_id="run-test",
                        action=action,
                        decision_output=root / f"deny-{action}-{removed}.json",
                        authority_receipt_path=partial,
                    )
                    self.assertEqual("MISSING_AUTHORITY", denied["result"])
                    self.assertFalse(denied["decision"]["callable"])
                    self.assertIn(removed, denied["decision"]["missing_grants"])
                    before = list(called)
                    guarded = poller.run_guarded_action(
                        decision=denied["decision"],
                        action=action,
                        callback=called.append,
                    )
                    self.assertFalse(guarded["called"])
                    self.assertEqual(before, called)
            missing = poller.decide_external_action(
                run_id="run-test",
                action="pull-request",
                decision_output=root / "missing-receipt.json",
            )
            self.assertEqual("MISSING_AUTHORITY", missing["result"])
            self.assertFalse(missing["decision"]["callable"])
            guarded = poller.run_guarded_action(
                decision=missing["decision"],
                action="pull-request",
                callback=called.append,
            )
            self.assertFalse(guarded["called"])
            self.assertEqual(list(poller.ACTION_REQUIREMENTS), called)

    def test_guard_rejects_forgery_action_swap_and_receipt_drift(self) -> None:
        with tempfile.TemporaryDirectory(prefix="schedule-action-guard-") as raw:
            root = Path(raw).resolve()
            called: list[str] = []
            denied = poller.decide_external_action(
                run_id="run-test",
                action="pull-request",
                decision_output=root / "denied.json",
            )
            forged = json.loads(json.dumps(denied["decision"]))
            forged.update(
                {"result": "AUTHORIZED", "callable": True, "missing_grants": []}
            )
            with self.assertRaisesRegex(
                poller.PollError, "does not match required grants"
            ):
                poller.run_guarded_action(
                    decision=forged,
                    action="pull-request",
                    callback=called.append,
                )

            receipt = self.exact_authority(root, {"commit"}, "commit.json")
            commit = poller.decide_external_action(
                run_id="run-test",
                action="commit",
                decision_output=root / "commit-decision.json",
                authority_receipt_path=receipt,
            )
            with self.assertRaisesRegex(poller.PollError, "does not match"):
                poller.run_guarded_action(
                    decision=commit["decision"],
                    action="push",
                    callback=called.append,
                )

            receipt_value = json.loads(receipt.read_text(encoding="utf-8"))
            receipt_value["authorized_by"] = "different-owner"
            receipt.write_text(json.dumps(receipt_value) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(poller.PollError, "receipt drifted"):
                poller.run_guarded_action(
                    decision=commit["decision"],
                    action="commit",
                    callback=called.append,
                )
            self.assertEqual([], called)

    def test_pr_completion_and_post_creation_crash_are_exact_replays(self) -> None:
        temporary, status, journal = self.fixture()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        consolidation = self.claim_consolidation(status, journal)
        evidence = root / "consolidated.html"
        evidence.write_text("complete\n", encoding="utf-8")
        digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
        poller.complete_consolidation(
            status_path=status,
            journal_path=journal,
            run_id="run-test",
            operation_id=consolidation["operation_id"],
            evidence_path=evidence,
            evidence_sha256=digest,
            now=120,
        )
        authority = self.authority(root)
        claim = poller.claim_pull_request(
            status_path=status,
            journal_path=journal,
            run_id="run-test",
            decision_output=root / "pr-action.json",
            authority_receipt_path=authority,
            now=121,
        )
        # A crash after the remote create leaves CLAIMED.  Retry must inspect by
        # the same idempotency key instead of authorizing a second creation.
        resumed = poller.claim_pull_request(
            status_path=status,
            journal_path=journal,
            run_id="run-test",
            decision_output=root / "pr-action.json",
            authority_receipt_path=authority,
            now=122,
        )
        self.assertEqual("RESUME_PULL_REQUEST_CLAIM", resumed["action"])
        receipt = self.pr_receipt(root, claim["operation_id"])
        receipt_digest = hashlib.sha256(receipt.read_bytes()).hexdigest()
        complete = poller.complete_pull_request(
            status_path=status,
            journal_path=journal,
            run_id="run-test",
            operation_id=claim["operation_id"],
            receipt_path=receipt,
            receipt_sha256=receipt_digest,
            now=123,
        )
        replay = poller.complete_pull_request(
            status_path=status,
            journal_path=journal,
            run_id="run-test",
            operation_id=claim["operation_id"],
            receipt_path=receipt,
            receipt_sha256=receipt_digest,
            now=124,
        )
        self.assertEqual("COMPLETE", complete["action"])
        self.assertEqual("PULL_REQUEST_ALREADY_COMPLETE", replay["action"])

    def test_pr_completion_requires_nonempty_operation_bound_receipt(self) -> None:
        temporary, status, journal = self.fixture()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        consolidation = self.claim_consolidation(status, journal)
        evidence = root / "consolidated.html"
        evidence.write_text("complete\n", encoding="utf-8")
        poller.complete_consolidation(
            status_path=status,
            journal_path=journal,
            run_id="run-test",
            operation_id=consolidation["operation_id"],
            evidence_path=evidence,
            evidence_sha256=hashlib.sha256(evidence.read_bytes()).hexdigest(),
            now=120,
        )
        authority = self.authority(root)
        claim = poller.claim_pull_request(
            status_path=status,
            journal_path=journal,
            run_id="run-test",
            decision_output=root / "pr-action.json",
            authority_receipt_path=authority,
            now=121,
        )
        before = status.read_bytes()
        empty = root / "empty-receipt.json"
        empty.write_bytes(b"")
        with self.assertRaisesRegex(poller.PollError, "nonempty"):
            poller.complete_pull_request(
                status_path=status,
                journal_path=journal,
                run_id="run-test",
                operation_id=claim["operation_id"],
                receipt_path=empty,
                receipt_sha256=hashlib.sha256(b"").hexdigest(),
                now=122,
            )
        self.assertEqual(before, status.read_bytes())
        wrong = self.pr_receipt(
            root,
            claim["operation_id"],
            name="wrong-operation.json",
            receipt_operation_id="another-operation",
        )
        with self.assertRaisesRegex(poller.PollError, "bind this operation"):
            poller.complete_pull_request(
                status_path=status,
                journal_path=journal,
                run_id="run-test",
                operation_id=claim["operation_id"],
                receipt_path=wrong,
                receipt_sha256=hashlib.sha256(wrong.read_bytes()).hexdigest(),
                now=123,
            )
        self.assertEqual(before, status.read_bytes())

    def test_concurrent_terminal_polls_share_one_consolidation_claim(self) -> None:
        temporary, status, journal = self.fixture()
        self.addCleanup(temporary.cleanup)
        self.claim_consolidation(status, journal)
        results: list[dict[str, object]] = []
        errors: list[BaseException] = []

        def run(trigger: str) -> None:
            try:
                results.append(
                    poller.poll(
                        status_path=status,
                        journal_path=journal,
                        run_id="run-test",
                        trigger_id=trigger,
                        now=130,
                    )
                )
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = [threading.Thread(target=run, args=(f"trigger-{index}",)) for index in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(errors)
        self.assertEqual(6, len(results))
        self.assertEqual(1, len({result["operation_id"] for result in results}))
        self.assertTrue(all(result["action"] == "RESUME_CONSOLIDATION_CLAIM" for result in results))

    def test_running_heartbeat_ids_are_idempotent_and_recover_after_crash(self) -> None:
        temporary, status, journal = self.fixture()
        self.addCleanup(temporary.cleanup)
        first = poller.mark_track(
            status_path=status,
            journal_path=journal,
            run_id="run-test",
            track="track-a",
            phase="running",
            reason=None,
            evidence_path=None,
            evidence_sha256=None,
            update_id="heartbeat-1",
            now=110,
        )
        before_state = status.read_bytes()
        before_journal = journal.read_bytes()
        replay = poller.mark_track(
            status_path=status,
            journal_path=journal,
            run_id="run-test",
            track="track-a",
            phase="running",
            reason=None,
            evidence_path=None,
            evidence_sha256=None,
            update_id="heartbeat-1",
            now=120,
        )
        self.assertEqual("TRACK_RECORDED", first["action"])
        self.assertEqual("TRACK_UPDATE_ALREADY_RECORDED", replay["action"])
        self.assertEqual(before_state, status.read_bytes())
        self.assertEqual(before_journal, journal.read_bytes())

        def crash(point: str) -> None:
            if point == "after_state_replace":
                raise InjectedCrash(point)

        with self.assertRaises(InjectedCrash):
            poller.mark_track(
                status_path=status,
                journal_path=journal,
                run_id="run-test",
                track="track-a",
                phase="running",
                reason=None,
                evidence_path=None,
                evidence_sha256=None,
                update_id="heartbeat-2",
                now=130,
                failpoint=crash,
            )
        crashed_state = status.read_bytes()
        repaired = poller.mark_track(
            status_path=status,
            journal_path=journal,
            run_id="run-test",
            track="track-a",
            phase="running",
            reason=None,
            evidence_path=None,
            evidence_sha256=None,
            update_id="heartbeat-2",
            now=140,
        )
        self.assertEqual("TRACK_UPDATE_ALREADY_RECORDED", repaired["action"])
        self.assertEqual(crashed_state, status.read_bytes())
        self.assertEqual(
            1,
            sum(
                row["event_id"] == "track-run-test-track-a-heartbeat-2"
                for row in self.journal(journal)
            ),
        )

    def test_journal_schema_lifecycle_and_path_binding_fail_closed(self) -> None:
        for mutation in ("arbitrary", "duplicate", "wrong-run", "wrong-operation"):
            temporary, status, journal = self.fixture()
            self.addCleanup(temporary.cleanup)
            root = Path(temporary.name).resolve()
            if mutation == "arbitrary":
                with journal.open("a", encoding="utf-8") as handle:
                    handle.write('{"event_id":"forged"}\n')
            elif mutation == "duplicate":
                journal.write_bytes(journal.read_bytes() + journal.read_bytes())
            elif mutation == "wrong-run":
                row = self.journal(journal)[0]
                row["run_id"] = "attacker"
                row["event_id"] = "init-attacker"
                journal.write_text(json.dumps(row) + "\n", encoding="utf-8")
            else:
                evidence = root / "forged.txt"
                evidence.write_text("forged\n", encoding="utf-8")
                forged = {
                    "schema_version": poller.SCHEMA_VERSION,
                    "record_type": "consolidation_complete",
                    "event_id": "complete-forged-operation",
                    "run_id": "run-test",
                    "operation_id": "forged-operation",
                    "evidence": {
                        "path": str(evidence),
                        "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
                    },
                    "recorded_at": "1970-01-01T00:01:50Z",
                }
                with journal.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(forged) + "\n")
            before = status.read_bytes()
            with self.subTest(mutation=mutation):
                with self.assertRaises(poller.PollError):
                    poller.poll(
                        status_path=status,
                        journal_path=journal,
                        run_id="run-test",
                        trigger_id="trigger-after-forgery",
                        now=120,
                    )
                self.assertEqual(before, status.read_bytes())

        temporary, status, journal = self.fixture()
        self.addCleanup(temporary.cleanup)
        replacement = Path(temporary.name).resolve() / "split.jsonl"
        replacement.write_bytes(journal.read_bytes())
        before = status.read_bytes()
        with self.assertRaisesRegex(poller.PollError, "does not match"):
            poller.poll(
                status_path=status,
                journal_path=replacement,
                run_id="run-test",
                trigger_id="trigger-split",
                now=120,
            )
        self.assertEqual(before, status.read_bytes())

    def test_state_rollback_cannot_emit_a_second_consolidation_claim(self) -> None:
        temporary, status, journal = self.fixture()
        self.addCleanup(temporary.cleanup)
        for index, track in enumerate(("track-a", "track-b", "track-c"), 1):
            self.mark_complete(status, journal, track, now=100 + index)
        preclaim = status.read_bytes()
        first = poller.poll(
            status_path=status,
            journal_path=journal,
            run_id="run-test",
            trigger_id="trigger-first-claim",
            now=110,
        )
        self.assertEqual("CONSOLIDATION_CLAIMED", first["action"])
        status.write_bytes(preclaim)
        rolled_back = status.read_bytes()
        with self.assertRaisesRegex(poller.PollError, "absent from state"):
            poller.poll(
                status_path=status,
                journal_path=journal,
                run_id="run-test",
                trigger_id="trigger-second-claim",
                now=111,
            )
        self.assertEqual(rolled_back, status.read_bytes())
        self.assertEqual(
            1,
            sum(
                row.get("action") == "CONSOLIDATION_CLAIMED"
                for row in self.journal(journal)
            ),
        )

    def test_coordinated_state_forgery_and_control_aliases_fail_closed(self) -> None:
        temporary, status, journal = self.fixture()
        self.addCleanup(temporary.cleanup)
        value = self.state(status)
        operation_id = poller._operation_key("run-test", "consolidation")
        value["consolidation"] = {
            "state": "CLAIMED",
            "operation_id": operation_id,
            "claimed_at": "1970-01-01T00:01:50Z",
            "claimed_by_trigger": "forged-trigger",
            "evidence": None,
        }
        value["trigger_outcomes"]["forged-trigger"] = {
            "action": "COMPLETE",
            "trigger_id": "forged-trigger",
            "run_id": "run-test",
            "recorded_at": "1970-01-01T00:01:50Z",
            "operation_id": operation_id,
            "phases": {name: "running" for name in value["expected_tracks"]},
        }
        status.write_text(json.dumps(value) + "\n", encoding="utf-8")
        before = status.read_bytes()
        with self.assertRaises(poller.PollError):
            poller.inspect(status_path=status, run_id="run-test")
        self.assertEqual(before, status.read_bytes())

        temporary2, status2, journal2 = self.fixture()
        self.addCleanup(temporary2.cleanup)
        digest = hashlib.sha256(status2.read_bytes()).hexdigest()
        before = status2.read_bytes()
        with self.assertRaisesRegex(poller.PollError, "cannot alias"):
            poller.mark_track(
                status_path=status2,
                journal_path=journal2,
                run_id="run-test",
                track="track-a",
                phase="complete",
                reason="invalid self-evidence",
                evidence_path=status2,
                evidence_sha256=digest,
                now=110,
            )
        self.assertEqual(before, status2.read_bytes())

    def test_partial_or_linked_journal_fails_closed(self) -> None:
        temporary, status, journal = self.fixture()
        self.addCleanup(temporary.cleanup)
        journal.write_bytes(journal.read_bytes().removesuffix(b"\n"))
        with self.assertRaisesRegex(poller.PollError, "partial final row"):
            poller.poll(
                status_path=status,
                journal_path=journal,
                run_id="run-test",
                trigger_id="trigger-one",
                now=120,
            )
        journal.unlink()
        target = Path(temporary.name).resolve() / "target.jsonl"
        target.write_text("", encoding="utf-8")
        journal.symlink_to(target)
        with self.assertRaises(poller.PollError):
            poller.poll(
                status_path=status,
                journal_path=journal,
                run_id="run-test",
                trigger_id="trigger-two",
                now=120,
            )

    def test_v2_initialization_binds_status_journal_and_full_configuration(self) -> None:
        temporary, status, journal = self.fixture()
        self.addCleanup(temporary.cleanup)
        state = self.state(status)
        expected_configuration = {
            "run_id": "run-test",
            "status_path": str(status),
            "journal_path": str(journal),
            "dispatch_epoch": 100,
            "hard_ceiling_seconds": 100,
            "poll_interval_seconds": 30,
            "expected_tracks": ["track-a", "track-b", "track-c"],
        }
        expected_digest = hashlib.sha256(
            (json.dumps(expected_configuration, sort_keys=True, separators=(",", ":")) + "\n").encode()
        ).hexdigest()
        self.assertEqual(2, state["schema_version"])
        self.assertEqual(str(status), state["status_path"])
        self.assertEqual(str(journal), state["journal_path"])
        self.assertEqual(expected_configuration, state["configuration"])
        self.assertEqual(expected_digest, state["configuration_sha256"])
        initialized = self.journal(journal)[0]
        self.assertEqual(2, initialized["schema_version"])
        self.assertEqual(str(status), initialized["status_path"])
        self.assertEqual(str(journal), initialized["journal_path"])
        self.assertEqual(expected_configuration, initialized["configuration"])
        self.assertEqual(expected_digest, initialized["configuration_sha256"])

        canonical_state = status.read_bytes()
        for field, replacement in (
            ("configuration_sha256", "0" * 64),
            ("configuration", {**expected_configuration, "poll_interval_seconds": 31}),
        ):
            mutated = self.state(status)
            mutated[field] = replacement
            status.write_text(json.dumps(mutated, sort_keys=True) + "\n", encoding="utf-8")
            before = status.read_bytes()
            with self.subTest(field=field):
                with self.assertRaisesRegex(poller.PollError, "configuration"):
                    poller.poll(
                        status_path=status,
                        journal_path=journal,
                        run_id="run-test",
                        trigger_id=f"trigger-{field}",
                        now=120,
                    )
                self.assertEqual(before, status.read_bytes())
            status.write_bytes(canonical_state)

    def test_initialize_crash_after_state_write_recovers_one_init_and_can_poll(self) -> None:
        with tempfile.TemporaryDirectory(prefix="schedule-poll-init-crash-") as raw:
            root = Path(raw).resolve()
            status = root / "status.json"
            journal = root / "poll.jsonl"

            def crash(point: str) -> None:
                if point == "after_state_replace":
                    raise InjectedCrash(point)

            with self.assertRaises(InjectedCrash):
                poller.initialize(
                    status_path=status,
                    journal_path=journal,
                    run_id="run-test",
                    dispatch_epoch=100,
                    hard_ceiling_seconds=100,
                    poll_interval_seconds=30,
                    tracks=["track-a", "track-b", "track-c"],
                    now=100,
                    failpoint=crash,
                )
            self.assertTrue(status.is_file())
            self.assertFalse(journal.exists())
            recovered = poller.initialize(
                status_path=status,
                journal_path=journal,
                run_id="run-test",
                dispatch_epoch=100,
                hard_ceiling_seconds=100,
                poll_interval_seconds=30,
                tracks=["track-a", "track-b", "track-c"],
                now=999,
            )
            self.assertEqual("ALREADY_INITIALIZED", recovered["action"])
            self.assertEqual(1, len(self.journal(journal)))
            outcome = poller.poll(
                status_path=status,
                journal_path=journal,
                run_id="run-test",
                trigger_id="trigger-after-init-recovery",
                now=120,
            )
            self.assertEqual("RESCHEDULE", outcome["action"])

    def test_initialize_does_not_reconstruct_a_missing_journal_for_claimed_state(self) -> None:
        temporary, status, journal = self.fixture()
        self.addCleanup(temporary.cleanup)
        self.claim_consolidation(status, journal)
        before = status.read_bytes()
        journal.unlink()
        with self.assertRaisesRegex(poller.PollError, "cannot reconstruct"):
            poller.initialize(
                status_path=status,
                journal_path=journal,
                run_id="run-test",
                dispatch_epoch=100,
                hard_ceiling_seconds=100,
                poll_interval_seconds=30,
                tracks=["track-a", "track-b", "track-c"],
                now=999,
            )
        self.assertEqual(before, status.read_bytes())
        self.assertFalse(journal.exists())

    def test_initialize_rejects_control_aliases_before_any_write(self) -> None:
        with tempfile.TemporaryDirectory(prefix="schedule-poll-alias-") as raw:
            root = Path(raw).resolve()

            same = root / "same.json"
            with self.assertRaisesRegex(poller.PollError, "alias"):
                poller.initialize(
                    status_path=same,
                    journal_path=same,
                    run_id="run-test",
                    dispatch_epoch=100,
                    hard_ceiling_seconds=100,
                    poll_interval_seconds=30,
                    tracks=["track-a", "track-b"],
                    now=100,
                )
            self.assertFalse(same.exists())
            self.assertFalse((root / "same.json.lock").exists())

            case_status = root / "Status.json"
            case_journal = root / "status.json"
            case_lock = case_journal.with_name(case_journal.name + ".lock")
            with self.assertRaisesRegex(poller.PollError, "alias"):
                poller.initialize(
                    status_path=case_status,
                    journal_path=case_journal,
                    run_id="run-case-alias",
                    dispatch_epoch=100,
                    hard_ceiling_seconds=100,
                    poll_interval_seconds=30,
                    tracks=["track-a", "track-b"],
                    now=100,
                )
            self.assertFalse(case_status.exists())
            self.assertFalse(case_journal.exists())
            self.assertFalse(case_lock.exists())

            journal = root / "journal.jsonl"
            run_lock = journal.with_name(journal.name + ".lock")
            with self.assertRaisesRegex(poller.PollError, "alias"):
                poller.initialize(
                    status_path=run_lock,
                    journal_path=journal,
                    run_id="run-lock-alias",
                    dispatch_epoch=100,
                    hard_ceiling_seconds=100,
                    poll_interval_seconds=30,
                    tracks=["track-a", "track-b"],
                    now=100,
                )
            self.assertFalse(journal.exists())
            self.assertFalse(run_lock.exists())

            hard_status = root / "hard-status.json"
            hard_journal = root / "hard-journal.jsonl"
            hard_status.write_bytes(b"unchanged\n")
            os.link(hard_status, hard_journal)
            before = hard_status.read_bytes()
            hard_lock = hard_journal.with_name(hard_journal.name + ".lock")
            with self.assertRaisesRegex(poller.PollError, "alias"):
                poller.initialize(
                    status_path=hard_status,
                    journal_path=hard_journal,
                    run_id="run-hard-alias",
                    dispatch_epoch=100,
                    hard_ceiling_seconds=100,
                    poll_interval_seconds=30,
                    tracks=["track-a", "track-b"],
                    now=100,
                )
            self.assertEqual(before, hard_status.read_bytes())
            self.assertEqual(before, hard_journal.read_bytes())
            self.assertFalse(hard_lock.exists())

            linked_journal = root / "linked-journal.jsonl"
            linked_lock = linked_journal.with_name(linked_journal.name + ".lock")
            linked_status = root / "linked-status.json"
            linked_journal.write_bytes(b"journal sentinel\n")
            os.link(linked_journal, linked_lock)
            linked_before = linked_journal.read_bytes()
            with self.assertRaisesRegex(poller.PollError, "alias"):
                poller.initialize(
                    status_path=linked_status,
                    journal_path=linked_journal,
                    run_id="run-linked-lock",
                    dispatch_epoch=100,
                    hard_ceiling_seconds=100,
                    poll_interval_seconds=30,
                    tracks=["track-a", "track-b"],
                    now=100,
                )
            self.assertFalse(linked_status.exists())
            self.assertEqual(linked_before, linked_journal.read_bytes())
            self.assertEqual(linked_before, linked_lock.read_bytes())

    def test_shared_journal_allows_only_one_concurrent_status_initialization(self) -> None:
        with tempfile.TemporaryDirectory(prefix="schedule-poll-shared-init-") as raw:
            root = Path(raw).resolve()
            journal = root / "poll.jsonl"
            statuses = [root / "status-a.json", root / "status-b.json"]
            barrier = threading.Barrier(2)
            results: list[dict[str, object]] = []
            errors: list[BaseException] = []

            def initialize_status(path: Path) -> None:
                try:
                    barrier.wait(timeout=10)
                    results.append(
                        poller.initialize(
                            status_path=path,
                            journal_path=journal,
                            run_id="run-test",
                            dispatch_epoch=100,
                            hard_ceiling_seconds=100,
                            poll_interval_seconds=30,
                            tracks=["track-a", "track-b", "track-c"],
                            now=100,
                        )
                    )
                except BaseException as exc:  # pragma: no cover - asserted below
                    errors.append(exc)

            threads = [threading.Thread(target=initialize_status, args=(path,)) for path in statuses]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
            self.assertEqual(1, len(results))
            self.assertEqual(1, len(errors))
            self.assertIsInstance(errors[0], poller.PollError)
            self.assertEqual(1, sum(path.exists() for path in statuses))
            self.assertEqual(1, len(self.journal(journal)))

    def test_copied_status_cannot_claim_through_the_shared_journal(self) -> None:
        temporary, status, journal = self.fixture()
        self.addCleanup(temporary.cleanup)
        for index, track in enumerate(("track-a", "track-b", "track-c"), 1):
            self.mark_complete(status, journal, track, now=100 + index)
        copied = Path(temporary.name).resolve() / "copied-status.json"
        copied.write_bytes(status.read_bytes())
        copied_before = copied.read_bytes()
        barrier = threading.Barrier(2)
        results: list[dict[str, object]] = []
        errors: list[BaseException] = []

        def claim(path: Path, trigger_id: str) -> None:
            try:
                barrier.wait(timeout=10)
                results.append(
                    poller.poll(
                        status_path=path,
                        journal_path=journal,
                        run_id="run-test",
                        trigger_id=trigger_id,
                        now=110,
                    )
                )
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = [
            threading.Thread(target=claim, args=(status, "trigger-primary")),
            threading.Thread(target=claim, args=(copied, "trigger-copied")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(1, len(results))
        self.assertEqual("CONSOLIDATION_CLAIMED", results[0]["action"])
        self.assertEqual(1, len(errors))
        self.assertIsInstance(errors[0], poller.PollError)
        self.assertEqual(copied_before, copied.read_bytes())
        self.assertEqual(
            1,
            sum(row.get("action") == "CONSOLIDATION_CLAIMED" for row in self.journal(journal)),
        )

    def test_legacy_claimed_state_is_rejected_without_migration_or_rewrite(self) -> None:
        temporary, status, journal = self.fixture()
        self.addCleanup(temporary.cleanup)
        self.claim_consolidation(status, journal)
        legacy = self.state(status)
        legacy["schema_version"] = 1
        legacy.pop("status_path", None)
        legacy.pop("configuration", None)
        legacy.pop("configuration_sha256", None)
        legacy.pop("initialized_at", None)
        status.write_text(json.dumps(legacy, sort_keys=True) + "\n", encoding="utf-8")
        before = status.read_bytes()
        with self.assertRaisesRegex(poller.PollError, "schema|missing or undeclared"):
            poller.poll(
                status_path=status,
                journal_path=journal,
                run_id="run-test",
                trigger_id="trigger-legacy",
                now=120,
            )
        self.assertEqual(before, status.read_bytes())

    def test_journal_rejects_a_second_consolidation_claim(self) -> None:
        temporary, status, journal = self.fixture()
        self.addCleanup(temporary.cleanup)
        first = self.claim_consolidation(status, journal)
        state = self.state(status)
        forged = {
            "schema_version": poller.SCHEMA_VERSION,
            "record_type": "poll_outcome",
            "event_id": "trigger-trigger-second-claim",
            "action": "CONSOLIDATION_CLAIMED",
            "trigger_id": "trigger-second-claim",
            "run_id": "run-test",
            "recorded_at": "1970-01-01T00:01:51Z",
            "operation_id": first["operation_id"],
            "phases": {name: "complete" for name in state["expected_tracks"]},
        }
        with journal.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(forged, sort_keys=True, separators=(",", ":")) + "\n")
        before = status.read_bytes()
        with self.assertRaisesRegex(poller.PollError, "second consolidation claim"):
            poller.poll(
                status_path=status,
                journal_path=journal,
                run_id="run-test",
                trigger_id="trigger-after-second-claim",
                now=120,
            )
        self.assertEqual(before, status.read_bytes())


if __name__ == "__main__":
    unittest.main()
