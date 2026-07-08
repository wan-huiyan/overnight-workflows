---
name: blind-rederive-pass-when-orchestrator-already-read-the-answer
description: |
  When you orchestrate verification subagents AFTER reading the claims/numbers yourself, do NOT
  hand the agents the published number to "check" — that anchors them into replicate-and-confirm,
  not independent verification, and their agreement proves nothing because you already knew the
  answer. Instead BLIND the re-derivation pass: give each agent the QUESTION, not the number
  ("what % of X are Y?", never "verify it's 43%"). Put the published number only in front of a
  SECOND, adversary pass whose job is to break it via a DIFFERENT join/source. Use when: (1) you've
  read a finding doc / analysis / PR claim in full and are about to dispatch agents to verify it;
  (2) you're writing a re-derivation/triple-check/fact-check subagent prompt and tempted to include
  the target number "so they know what to confirm"; (3) a handoff asks you to "independently
  re-derive" something whose answer is already written in the doc you were handed; (4) you want
  subagent convergence to mean real agreement, not anchoring on a number you leaked into the prompt.
  Pairs with review-panel-pre-dispatch-claim-recheck and finding-verification-live-bq-triple-probe.
author: Claude Code
version: 1.1.0
date: 2026-06-04
---

# Blind the re-derivation pass when YOU already read the answer

## Problem

A handoff says "independently triple-check these findings." To know *what* to check, you read the
proof docs in full — so now you know every published number (~43%, ~51%, ~156,000 rows…). Then you
dispatch verifier subagents. The natural prompt is *"verify the doc's claim that 43% of visitors
show has_lead=1 before their lead was created."*

That prompt has already lost. You handed the agent the answer. A capable agent will write SQL that
reproduces 43% and report "confirmed" — **replicate-and-confirm, not independent verification**.
When it agrees, the agreement is worthless: it agreed with a number you injected. This is the
shared-artifact-consensus trap one level deeper — not "all reviewers read the same doc," but "the
orchestrator leaked the doc's number into every reviewer's prompt." You cannot un-know the answer,
but you *can* keep it out of the verifier's context.

## Context / Trigger conditions

- You (the orchestrator) have read a finding doc / ADR / analysis / PR description that contains
  specific numeric headlines, and you're about to spawn agents to verify them.
- You're writing a subagent prompt for a task labeled "re-derive", "independently verify",
  "triple-check", "fact-check", "stress-test", "is this number right?".
- You catch yourself adding the target figure to the prompt "so the agent knows what it's checking".
- Especially when the verification is the whole point (client-facing finding, retraction, board
  packet) — where a rubber-stamp is worse than no check.

## Solution — two passes, asymmetric knowledge

**Pass A — blinded re-derivation (one agent per question).** Give the agent the *question*, the table
/ source pointers, and the mechanism to read (pipeline code is fine — that's the source of truth, not
a "claim") — but **NOT the published number**. Phrase every headline as an open question:

- ✅ "Of visitors whose lead was created in-window, what % show has_lead=1 on a target_date strictly
  before the lead's creation date? Report count and %."
- ❌ "Verify the doc's claim that this is 43% (about 3,100 of 7,300)."

Tell it: report raw numbers, not a verdict; triple-probe each headline via ≥2 independent SQL paths;
if you cannot query, return UNVERIFIED — never infer a number. Convergence now *means something*:
the agent reached the number without being told it.

**Pass B — adversary that knows the number.** A second agent gets the published number AND Pass A's
result, and is told to **break it**: re-derive the decisive figure via a *different* join/source than
both the doc and Pass A; enumerate artifact hypotheses (timezone/truncation, coverage/floor bias,
merge/dedup grain, casing, NULL-safety) and check each; resolve any "meaning" question with the
burden of proof on the *alarming* interpretation. Asymmetric knowledge by design: Pass A can't anchor
(doesn't have the number); Pass B's whole job is to attack the number, so knowing it is correct.

**You synthesize.** Cross-check Pass A vs Pass B for consistency; the orchestrator assigns the
verdict, never a subagent (a subagent that "declares the verdict" skipped the cross-check).

## Verification

The technique worked when:

- Pass A's blinded number lands on (or near, within live-data churn) the published figure **without
  the number appearing anywhere in its prompt or transcript** — grep the dispatched prompt to confirm
  you didn't leak it.
- Pass B reached the decisive number via a genuinely different path (different table, different grain)
  and still agrees — or *disagrees*, in which case the disagreement is the finding.
- If Pass A had been handed the number, you could not distinguish "the finding is real" from "the
  agent reproduced what I told it." Blinding is what buys that distinction.

## Example (verified — a triple-check of three client-facing findings)

Three client-facing findings to triple-check; the orchestrator had read all three proof docs in full,
so it knew every headline number — the leak rate, the label-coverage share, the per-table column
counts. Built a workflow: **Pass A agents got only the questions** ("what % …", "which codes does the
label map, and what share of enrolled accounts fall outside it?", "sweep these 3 tables — is there any
source/sub_type field?"). **Pass B adversaries got the published numbers and were told to break them.**

- F1: blinded Pass A independently re-derived ~43% / ~156,000 rows / 0 transitions (matched the doc
  without being told it); Pass B then refuted 5 artifact hypotheses via a *different* creation source
  (the snapshot's created_at vs the change-data-capture history table) — leak confirmed on an
  independent path.
- F2: blinded Pass A re-derived ~51% via three code-agnostic SQL paths; Pass B, knowing the doc's
  program-category label, attacked it and found **zero codes for that category exist** — a framing
  correction the doc had wrong, which a confirm-the-number prompt would never have surfaced.
- F3: blinded Pass A confirmed the per-table column counts; Pass B's break-it mandate did a
  dataset-wide sweep and found an application-grain source field the first sweep missed.

Had Pass A been handed "confirm ~43% / ~51% / the column counts", all three would have reported
"confirmed" and the two framing corrections (F2 misnomer, F3 non-exhaustive sweep) would have been lost.

## Notes

- **You can read the docs — just don't forward the numbers.** Reading the claims is how you know what
  to verify and how you write the adversary's mandate. The discipline is purely about what lands in
  the *verifier's* prompt.
- **Pipeline/source code is fair game for Pass A**; finding docs / ADRs / prior probe files are not.
  A `WHERE` clause in the actual builder is the mechanism (ground truth); a markdown "we found 43%"
  is a claim (to be re-derived blind).
- **Don't over-blind.** Pass A still needs table names, column hints, and the mechanism to read, or it
  flails. Blind the *answer*, not the *orientation*.
- **Live-data churn:** a blinded re-derivation landing at, say, 87,020 vs the doc's 87,015 is a *pass*
  (table grew), and the small drift is extra evidence the agent computed it fresh rather than parroting.
- See also: `review-panel-pre-dispatch-claim-recheck` (orchestrator re-derives the load-bearing number
  *itself* before dispatch — complementary: do that AND blind the panel); `factcheck-subagent-needs-
  complete-sources` (the inverse leak — under-feeding sources causes false-NEGATIVE "unsupported"
  verdicts); `finding-verification-live-bq-triple-probe` (the triple-probe + synthesis methodology this
  blinding technique slots into); `verify-locked-decided-claim-scope` (split a "CONFIRMED" verdict so a
  narrow confirmation doesn't broadcast as the broad claim); `verifier-rederive-from-raw-not-the-checked-artifact`
  (the CODE-verifier sibling — a deterministic conformance judge / linter / golden-render check must
  likewise re-derive expectations from the raw source, never read them from the intermediate artifact the
  thing-under-test was built from; same family, different mechanism: a code check vs an agent prompt).
