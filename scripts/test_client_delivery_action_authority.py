#!/usr/bin/env python3
"""Behavioral deny/allow controls for routed client-delivery actions."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "plugins/overnight-review-client-delivery/scripts/action_authority.py"
SPEC = importlib.util.spec_from_file_location("client_delivery_action_authority", HELPER)
assert SPEC is not None and SPEC.loader is not None
authority = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(authority)


class ClientDeliveryAuthorityTests(unittest.TestCase):
    def receipt(self, root: Path, grants: set[str], name: str = "authority.json") -> Path:
        path = root / name
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "record_type": authority.AUTHORITY_TYPE,
                    "review_id": "client-review-1",
                    "authorized_by": "delivery-owner",
                    "recorded_at": "2026-08-09T00:00:00Z",
                    "grants": {
                        grant: grant in grants for grant in authority.GRANT_FIELDS
                    },
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def test_denied_actions_record_missing_authority_and_call_nothing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="client-authority-deny-") as raw:
            root = Path(raw).resolve()
            receipt = self.receipt(root, set())
            called: list[str] = []
            for action in authority.ACTION_REQUIREMENTS:
                result = authority.decide(
                    review_id="client-review-1",
                    action=action,
                    authority_receipt=receipt,
                    decision_output=root / f"deny-{action}.json",
                )
                guarded = authority.run_guarded_action(
                    decision=result["decision"],
                    action=action,
                    callback=called.append,
                )
                self.assertEqual("MISSING_AUTHORITY", result["result"])
                self.assertTrue(result["decision"]["missing_grants"])
                self.assertEqual(
                    {"action": action, "result": "MISSING_AUTHORITY", "called": False},
                    guarded,
                )
            self.assertEqual([], called)

    def test_each_action_requires_its_exact_separate_grant_set(self) -> None:
        with tempfile.TemporaryDirectory(prefix="client-authority-grants-") as raw:
            root = Path(raw).resolve()
            called: list[str] = []
            for action, requirements in authority.ACTION_REQUIREMENTS.items():
                granted = self.receipt(root, set(requirements), f"grant-{action}.json")
                allowed = authority.decide(
                    review_id="client-review-1",
                    action=action,
                    authority_receipt=granted,
                    decision_output=root / f"allow-{action}.json",
                )
                self.assertEqual("AUTHORIZED", allowed["result"])
                self.assertTrue(allowed["decision"]["callable"])
                before = list(called)
                guarded = authority.run_guarded_action(
                    decision=allowed["decision"],
                    action=action,
                    callback=called.append,
                )
                self.assertTrue(guarded["called"])
                self.assertEqual(before + [action], called)
                for removed in requirements:
                    partial = self.receipt(
                        root,
                        set(requirements) - {removed},
                        f"partial-{action}-{removed}.json",
                    )
                    denied = authority.decide(
                        review_id="client-review-1",
                        action=action,
                        authority_receipt=partial,
                        decision_output=root / f"partial-{action}-{removed}.decision.json",
                    )
                    self.assertEqual("MISSING_AUTHORITY", denied["result"])
                    self.assertIn(removed, denied["decision"]["missing_grants"])
                    before = list(called)
                    guarded = authority.run_guarded_action(
                        decision=denied["decision"],
                        action=action,
                        callback=called.append,
                    )
                    self.assertFalse(guarded["called"])
                    self.assertEqual(before, called)
            self.assertEqual(list(authority.ACTION_REQUIREMENTS), called)

    def test_guard_rejects_forged_or_different_action_decisions_without_call(self) -> None:
        with tempfile.TemporaryDirectory(prefix="client-authority-forged-") as raw:
            root = Path(raw).resolve()
            called: list[str] = []
            denied = authority.decide(
                review_id="client-review-1",
                action="push",
                decision_output=root / "denied.json",
            )
            forged = json.loads(json.dumps(denied["decision"]))
            forged.update(
                {"result": "AUTHORIZED", "callable": True, "missing_grants": []}
            )
            with self.assertRaisesRegex(
                authority.AuthorityError, "does not match required grants"
            ):
                authority.run_guarded_action(
                    decision=forged, action="push", callback=called.append
                )

            receipt = self.receipt(root, {"commit"}, "commit-authority.json")
            commit = authority.decide(
                review_id="client-review-1",
                action="commit",
                authority_receipt=receipt,
                decision_output=root / "commit.json",
            )
            with self.assertRaisesRegex(authority.AuthorityError, "does not match"):
                authority.run_guarded_action(
                    decision=commit["decision"],
                    action="push",
                    callback=called.append,
                )
            self.assertEqual([], called)

    def test_guard_rechecks_authority_receipt_bytes_before_call(self) -> None:
        with tempfile.TemporaryDirectory(prefix="client-authority-drift-") as raw:
            root = Path(raw).resolve()
            receipt = self.receipt(root, {"commit"})
            decision = authority.decide(
                review_id="client-review-1",
                action="commit",
                authority_receipt=receipt,
                decision_output=root / "commit.json",
            )
            receipt_value = json.loads(receipt.read_text(encoding="utf-8"))
            receipt_value["authorized_by"] = "different-owner"
            receipt.write_text(json.dumps(receipt_value) + "\n", encoding="utf-8")
            called: list[str] = []
            with self.assertRaisesRegex(authority.AuthorityError, "receipt drifted"):
                authority.run_guarded_action(
                    decision=decision["decision"],
                    action="commit",
                    callback=called.append,
                )
            self.assertEqual([], called)

    def test_missing_receipt_is_durable_and_exact_retry_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="client-authority-retry-") as raw:
            root = Path(raw).resolve()
            output = root / "decision.json"
            first = authority.decide(
                review_id="client-review-1",
                action="push",
                decision_output=output,
            )
            before = output.read_bytes()
            retry = authority.decide(
                review_id="client-review-1",
                action="push",
                decision_output=output,
            )
            self.assertEqual("MISSING_AUTHORITY", first["result"])
            self.assertEqual(first["decision"], retry["decision"])
            self.assertEqual(before, output.read_bytes())
            with self.assertRaisesRegex(authority.AuthorityError, "conflicts"):
                authority.decide(
                    review_id="client-review-1",
                    action="commit",
                    decision_output=output,
                )
            self.assertEqual(before, output.read_bytes())

    def test_malformed_and_linked_receipts_fail_before_decision(self) -> None:
        with tempfile.TemporaryDirectory(prefix="client-authority-links-") as raw:
            root = Path(raw).resolve()
            receipt = self.receipt(root, {"commit"})
            malformed = json.loads(receipt.read_text(encoding="utf-8"))
            malformed["grants"]["commit"] = 1
            receipt.write_text(json.dumps(malformed) + "\n", encoding="utf-8")
            output = root / "malformed-decision.json"
            with self.assertRaises(authority.AuthorityError):
                authority.decide(
                    review_id="client-review-1",
                    action="commit",
                    authority_receipt=receipt,
                    decision_output=output,
                )
            self.assertFalse(output.exists())

            receipt = self.receipt(root, {"commit"}, "valid.json")
            symlink = root / "linked.json"
            symlink.symlink_to(receipt)
            with self.assertRaisesRegex(authority.AuthorityError, "symlink"):
                authority.decide(
                    review_id="client-review-1",
                    action="commit",
                    authority_receipt=symlink,
                    decision_output=root / "linked-decision.json",
                )
            hardlink = root / "hardlink.json"
            os.link(receipt, hardlink)
            with self.assertRaisesRegex(authority.AuthorityError, "single-link"):
                authority.decide(
                    review_id="client-review-1",
                    action="commit",
                    authority_receipt=receipt,
                    decision_output=root / "hardlink-decision.json",
                )


if __name__ == "__main__":
    unittest.main()
