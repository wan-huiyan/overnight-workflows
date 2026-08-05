---
name: verifier-rederive-from-raw-not-the-checked-artifact
description: |
  Use when DESIGNING or building a conformance judge / verifier / linter / LLM-as-judge / golden-render
  check / "does the output honestly reflect the input" gate — anything that validates a DERIVED or RENDERED
  artifact (HTML, report, serialized payload, dashboard, doc) against the source it was produced from. The
  trap: the verifier reads its "expected" values from the SAME intermediate artifact the producer already
  emitted (the serialized framing dict the template consumed, the metadata the renderer wrote, the cached
  classification) — so the check compares a value to itself and PASSES tautologically on the exact dishonesty
  it exists to catch (e.g. a "green badge on a negative result" passes because the badge token and the
  expected token came from the same already-computed dict). Fix + design rule: the verifier must RE-DERIVE
  its expectations from the RAW inputs by calling the producer logic ITSELF (classify/compute from the source
  bundle), never trust the intermediate artifact under test. Use when: (1) writing a
  verify / conformance / judge function (verify_*) over a rendered or derived artifact; (2) the producer emits an
  intermediate dict/metadata that BOTH the artifact and a naive check could read; (3) a "fixture gate" that
  renders-then-checks always passes and you can't tell if it has teeth.
author: Claude Code
version: 1.0.0
date: 2026-06-07
---

# A verifier must re-derive from raw inputs, never trust the artifact it's checking

## Problem
You build a checker that proves a produced artifact (rendered HTML, report, serialized payload, dashboard)
honestly reflects its source. The producer pipeline is `raw inputs → [derive/classify] → intermediate
(a framing dict / metadata / classification) → [render] → artifact`. The artifact and the intermediate
both carry the "answer" (the badge token, the section flags, the class label). If your check reads the
EXPECTED value from that intermediate (or re-reads it off the artifact), it compares a value to itself:
the check is a **structural tautology** that passes on the very dishonesty it exists to catch. A
"green-badge-on-a-negative-result" or "headline says lifted on a reduction" sails through.

## Context / Trigger conditions
- Writing a `verify_*` / `conformance` / judge / linter function over a DERIVED or RENDERED artifact.
- The producer emits an intermediate dict/metadata that BOTH the artifact AND a naive check can read.
- A render-then-check "fixture gate" that always passes — and you can't articulate what regression would
  make it fail.
- Building an LLM-as-judge: the deterministic facts (numbers, structure, class) must NOT pass through the LLM.

## Solution — the design rule + three corollaries
1. **Truth = the raw inputs. Re-derive, don't read.** The judge calls the producer's derivation logic
   ITSELF on the raw source (`classify_result(raw_bundle)`, `recompute(raw)`) and compares the artifact's
   rendered tokens/figures to THAT. It never reads the expected value from the intermediate dict the
   producer already handed the renderer. This single choice is what makes "badge contradicts class"
   *expressible as a failure* instead of a no-op.

2. **Corollary — the honest render-then-check path is tautological by construction.** If your test does
   `artifact = render(bundle); assert verify(artifact, bundle)`, and `verify` derives expectations the
   same way `render` did, it ALWAYS passes — it only proves the producer→artifact *mapping* wired up, not
   correctness. Say so explicitly; put the real teeth in ADVERSARIAL cases that feed a CORRUPTED artifact
   against an untampered source (string-replace one figure/token/section in the rendered output, then
   assert verify FAILS for the right reason).

3. **Corollary — a hand-copied derivation silently drifts.** If the judge re-implements the producer's
   derivation as a parallel copy, the copy drifts (a guard added on one side, a field read differently) and
   the gate goes blind to the very divergence it promised to catch. Extract ONE shared adapter that BOTH the
   producer and the judge call. One code path = no drift. (Guard byte-identity / golden tests across the
   refactor.)

4. **Corollary — test the NOVEL detector, not a proxy.** An adversarial fixture must corrupt a value covered
   ONLY by the new check. If you corrupt a value that an OLDER/cheaper check also catches, deleting the new
   detector entirely leaves the test green (zero real coverage). Prove discrimination by mutation — see
   `test-fixture-nondiscriminating-verify-by-mutation`.

## Verification
- Delete the body of the check you think is load-bearing → its dedicated adversarial test must go RED.
  If it stays green, the test is hitting a different check (corollary 4).
- Construct the canonical dishonest artifact (the bug the feature exists to kill) and confirm the judge
  fails it — and fails on the RIGHT check, not incidentally.
- Grep the judge for any read of the intermediate artifact's "answer" fields; every such read is a
  tautology candidate — replace with a re-derivation from raw.

## Example (anonymized)
A report-conformance judge verifies a rendered `report.html` reflects its raw result bundle. The template
renders `status-{{ framing.badge_status }}` from a `framing` dict the producer computed. The judge does NOT
read `framing.badge_status`; it calls `classify_result(raw_bundle)` itself, takes the expected badge from
THAT, and asserts the rendered token matches. Adversarial fixtures string-replace the rendered badge / a
figure / a headline verb and assert the judge fails. The producer's `derive_framing(...)` is a single shared
function both the live renderer and the judge call (no drift). The LLM "tone" judge is gated off and can
never clear a deterministic failure. An independent reviewer (the dissenting low-scorer on the panel) caught
that the headline VERB — the primary framing signal — was never checked, only the badge token: a correct
badge can sit above a lying headline.

## Notes
- This is the code-verifier sibling of `blind-rederive-pass-when-orchestrator-already-read-the-answer`
  (which is about blinding verification SUBAGENTS so they don't anchor on a number you already read). Same
  family — "don't trust an already-computed answer; re-derive independently" — different mechanism (a code
  check vs an agent prompt).
- For boundaries enforced in executable code, prefer deterministic adjudication over an LLM verdict:
  `redteam-llm-attacks-adjudicated-by-real-code`.
- Keep the dissenting reviewer on an adversarial panel: here the lowest-scoring reviewer caught the
  load-bearing gap the higher scorers missed. Score spread is signal, not noise.
