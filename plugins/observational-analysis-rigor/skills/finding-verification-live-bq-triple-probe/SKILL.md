---
name: finding-verification-live-bq-triple-probe
description: >-
  Independently verify a published data finding, dashboard action card, or a
  PRIOR retraction/downgrade of one — by re-deriving every headline number
  from the live database with parallel triple-probing subagents, zero trust in
  finding docs / ADRs / prior probe files. Use this whenever the user says
  "verify the rest of the cards", "re-check finding X", "was that retraction
  correct?", "did we actually verify these cohorts?", "the revalidation
  deferred half the findings", "sweep the action page", or hands you a
  verification handoff prompt. Also trigger when a revalidation effort recorded
  deferred/can't-probe cohorts as "no retract" (false-confidence smell), when a
  finding sits on a snapshot-vs-history temporal-gate data layer, or when a
  retraction's stated *rationale* needs an independent check (the number may be
  real but direction-reversed). Sister to
  `published-claim-not-in-any-source-finding-retraction` (that EXECUTES a
  retraction; this DECIDES whether one is warranted) and
  `whole-dashboard-factcheck-via-parallel-cluster-agents` (that audits a whole
  product for copy+numbers; this deep-verifies individual findings).
version: 1.0.0
author: Claude Code
date: 2026-05-14
---

# Finding verification — live-BQ triple-probe with parallel subagents

## Problem

A dashboard surfaces a finding ("Cohort X enrolls at 22% vs 5% peer — 4.6×").
Someone needs to know if it's *true* — before a client sees it, before a
retraction ships, after a revalidation that "passed" but didn't actually probe
half the cohorts. The finding docs, ADRs, and prior probe files are NOT
trustworthy ground truth: they were often written against an older data layer,
or the "probe" was a no-op stub, or the revalidation **deferred** the cohort
and recorded the deferral as a pass.

Two specific failure modes this skill exists to catch:

1. **"Deferred ≠ verified."** A revalidation re-probes 10 cohorts as line-items
   but only 5 run live queries; the other 5 are deferred ("structural / can't
   probe") and the verdict tally says "🔴 retract candidate: 0" — technically
   true, materially false. The deferral *reason* is often the same root cause
   as the eventual bug.
2. **The retraction rationale itself is wrong.** A finding gets retracted for
   reason R ("the number traces to nothing"), but R is false — the number is
   real, it was just **direction-reversed** on the card, or the cohort is
   degenerate for a *different* reason. A sloppy "retraction correct" verdict
   re-litigates the moment someone finds the real source.

## When to use

- The user asks to verify / re-check / sweep one or more published findings or
  action cards.
- A handoff prompt asks you to independently re-derive a finding or a retraction.
- A revalidation/ADR recorded deferred cohorts in a way that reads as a pass.
- A finding sits on a **snapshot-vs-history temporal-gate** data layer (see below).

NOT for: executing a retraction once you've decided one is needed (use
`published-claim-not-in-any-source-finding-retraction`); whole-product copy +
number audits (use `whole-dashboard-factcheck-via-parallel-cluster-agents`);
"the rate moved, is it the backfill?" (use
`published-finding-rate-doesnt-reproduce-partition-backfill`).

## The snapshot-vs-history caveat (hold it the whole time)

Many pipelines read a source-system status from a **daily snapshot** — today's
state, not the history of how it changed. When a probe looks back to an earlier
`target_date`, the snapshot cannot prove what a record's status *was* then.
Pipelines handle this with a temporal gate that conservatively returns NULL for
records whose decision postdates the anchor.

**Consequence:** `status IS NULL` in a training-features table does NOT reliably
mean "absent." It can mean "present, but the snapshot can't prove it yet." A
cohort defined on `status IS NULL` is therefore contaminated by gated-but-real
records. (Concretely: the training-features model carries a temporal gate that
returns NULL for any record whose decision postdates the anchor; the rescue tool
is the point-in-time **history table** — e.g.
`analytics.<dataset>.application_status_history` — which records each status
change with its effective date, so you can reconstruct the true as-of status.
See your project's cohort-composition / temporal-gating analysis and its
history-table availability reference.)

Every subagent prompt MUST carry this caveat verbatim — they have no other way
to know it.

## Method

### Step 1 — Frame the verification as discrete questions

One finding usually decomposes into 2-4 independent questions, e.g.:
- Does the claimed effect exist *at all*, at the claimed magnitude and direction?
- Is the cohort definition even *constructible* with verified members?
- Where did the literal headline number *come from*, and is it salvageable?

For a multi-finding sweep, each finding/card is its own question (or cluster).

### Step 2 — One parallel subagent per question/finding

Dispatch via the Agent tool, `general-purpose` type, **self-contained** prompts
run in parallel (one message, multiple Agent blocks). Each prompt must include:
- The snapshot-vs-history caveat **verbatim**.
- The exact instruction: *"do not trust any markdown finding doc, ADR, or prior
  probe file — re-derive every number from the live database."*
- An order to **report raw numbers** (yield, N, CI), not interpretations.
- Orientation pointers (table names, the cohort SQL location, how to recover a
  removed fetcher via `git show <commit>^:<path>`) — but tell them to verify
  every name themselves.

### Step 3 — Triple-probe every headline number

This is the load-bearing rule. For each headline figure, the subagent probes it
**3 independent ways** and reports all three:
- (a) the training-features / serving table directly,
- (b) the history-table point-in-time reconstruction,
- (c) a source-system-direct join (bypassing the derived layer).

If the three agree → robust. **If they disagree, that disagreement IS the
finding** — report it, don't average it. (Common pattern: (a) and (b) agree but
(c) has a different baseline because it includes records the derived layer
drops — the *lift ratio* still reconciles.)

Same triple-probe for membership signals: e.g. "verified segment member" via a
lookup/bridge table AND via an independent flag column.

### Step 4 — Multi-anchor replication

Re-derive at ≥2 anchors: the published anchor, plus a second where history
coverage allows, plus (if possible) a genuine prior-cycle anchor. A finding that
only holds at one anchor is not verified — it's a single-panel artifact.

### Step 5 — Synthesize centrally; never let a subagent declare the verdict

**You** synthesize. Cross-check the subagents' raw numbers against each other
for consistency (e.g. Question 2's constructible-N must be reconcilable with
Question 1's cohort-N at the same anchor — a subset relationship should hold).
Call the `advisor` tool once before committing to a verdict — especially if a
subagent's finding complicates a clean story (the rationale-wrong case).

### Step 6 — Assign a verdict from the taxonomy

| Verdict | Meaning |
|---|---|
| **reproduces** | Claim holds at claimed magnitude + direction across anchors + probes |
| **reframe-needed** | A real effect exists but the card's framing/direction/metric is wrong |
| **retraction-premature** | A prior retraction was wrong; the finding should be restored |
| **retraction-correct** | A prior retraction was warranted; lock it with a rock-solid evidence file |
| **rationale-wrong** | Outcome of a prior retraction/downgrade is right but its *stated reason* is false — needs a rationale-correction follow-up, not an un-retract |
| **unverified** | Could not be probed. This is a TERMINAL status — it is NOT "no retract". It blocks the finding from being sold as a ranked play until it can be probed. |

The `unverified` row is the whole point of the skill. "Couldn't be probed" must
never be folded into a "0 retract candidates" tally. If you find a revalidation
that did this, **file the process gap as an issue**, separately from any data bug.

## Deliverable

A single analysis doc (`docs/analysis/<date>-<finding>-verification.md`)
containing:
- The raw probe tables from every subagent, with the BQ SQL inline.
- The cross-subagent reconciliation check.
- A clear verdict per the taxonomy, with the specific numbers that decide it.
- If `reframe-needed` / `retraction-premature` / `rationale-wrong`: the
  follow-up plan (corrected card spec, un-retract PR, or rationale-fix PR).
- If `retraction-correct`: an explicit statement of *what evidence would have
  caught this earlier* — so the probe-design gap is closed.
- A process-gap issue filed if a prior revalidation mis-recorded deferrals.

Then ship the fixes per the project's normal flow (tracker entry → PR with
deploy label → code-review agent → squash-merge → verify deploy → spot-check
live). When the fix corrects a *rationale*, grep the whole codebase for the old
rationale text — it usually lurks in template comments, route docstrings, and
test comments, not just the user-facing string.

## Worked example — a segment-concentration card re-verification

A handoff asked: was the retraction of a "segment S over-concentrates enrollment
— 2.8× the peer rate" card correct?

- **3 parallel subagents**, one per question (effect exists? / cohort
  constructible? / where did "2.8×" come from?). Each got the snapshot caveat
  verbatim and triple-probed.
- **Q1** — segment-S yield by sub-bucket: the segment landed at roughly
  0.03–0.05× of the baseline group across 3 probe methods + 2 cycles (incl. a
  genuine prior cycle). The "2.8× higher" direction reproduced *nowhere* — the
  real effect was several-fold *lower*, not higher. Verdict input: the effect is
  real but inverted.
- **Q2** — cohort constructibility: at the latest scoring anchor, nearly all
  members had no resolvable segment attribute and none had a source-system
  record to verify against; the deciding N of verified-segment ∩
  verified-no-record was ~1–2. At a clean backtest anchor the history table
  *shrank* the cohort by revealing that over half the "no-record" members
  verifiably HAD a record. Verdict input: **retraction-correct**.
- **Q3** — number trace: "2.8×" was NOT absent (the retraction's claim) — it
  exists in an earlier model run as "segment S enrolls 2.8× *LOWER*". The card
  had reversed the direction and fabricated its rate display. Verdict input:
  **rationale-wrong** on that finding row.
- **Synthesis**: advisor called before verdict (it flagged the rationale-wrong
  nuance). Cross-check held (Q2's cohort-N was a strict subset of Q1's larger
  cohort-N at the same anchor). Final: retraction-correct on the *action*,
  rationale-wrong on the *stated reason* → a small rationale-fix PR, not an
  un-retract.
- **Process-gap issue** filed: the prior revalidation deferred about half its
  cohorts (~5 of 10) yet recorded "0 retract candidates"; that cohort's probe
  file was a no-op stub.

## Notes

- **A no-op stub probe file is a red flag, not a probe.** "For visibility, no
  live probe runs here" means the cohort was never tested. Treat it as
  `unverified`, never as a pass.
- **The deferral reason is often the bug.** If a cohort was deferred because
  "the field lives in a layer we didn't query," that's frequently *exactly* why
  the cohort is degenerate. Chase deferral reasons, don't file them.
- **A real number can still be a wrong card.** Direction-reversal and fabricated
  rate displays are disqualifying even when the underlying figure is genuine and
  reproducible. Keep the row retracted; fix the rationale.
- **Don't let the subagent conclude.** Subagents report raw numbers; the
  orchestrator synthesizes and assigns the verdict. A subagent that "declares
  the verdict" has skipped the cross-check.
