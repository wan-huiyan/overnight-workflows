---
name: observational-analysis-rigor
description: |
  The validity gate for any finding from OBSERVATIONAL data (no randomization). Use this
  BEFORE trusting or shipping a data finding, and when reviewing/verifying one. Runs an
  8-step protocol — leak-free point-in-time cohort · probe outcome−anchor before an
  event-anchored design · decompose pooled rates by the structural axis (composition /
  Simpson) · de-confound with a multiple-testing-corrected regression · marker-vs-lever
  discipline · coverage-limited-join unbiasedness · triple-probe headlines · honest
  de-stale ledger — that catches the finding which is genuinely surprising but WRONG
  (a composition artifact, a leak, an anchor-timing inversion, or an intent marker sold
  as a lever). Invoke whenever you analyze funnels, cohorts, lift, conversion rates,
  feature signals, retention, or any "do X → they convert / advance" claim from
  observational data; whenever a gradient or effect "looks too good"; and whenever you
  verify a finding before it reaches a stakeholder. The deep-dive skills in this bundle
  each generalize one step — load the matching one when a step gets hard.
author: wan-huiyan + Claude Code
version: 1.0.0
---

# Observational analysis rigor — the validity gate

An observational finding fails in two directions. One is boring ("this 'surprise' is just the
model's top feature restated"). This gate catches the other: **a finding that is genuinely
surprising but wrong** — a pooled rate that is really a mix, a "lift" that is funnel position,
a "do X → they convert" that is reverse-causation. These survive a novelty check (they *look*
new) and a single sleepy read (the number is real; the *interpretation* is the bug), and ship.
Each step below defends against one specific, recurring way that happens.

Run the steps **in order** — a leak invalidates a de-confound; composition invalidates a raw
gradient. Clean the cohort, then decompose, then de-confound, then interpret.

## The protocol

1. **Leak-free cohort from immutable business-event dates — not today's snapshot.** Reconstruct
   each unit's state as of the relevant date from **history/event tables**, not the current
   snapshot; a snapshot value used at a historical anchor is leakage unless the attribute is
   provably static. Read an outcome gate off its real business date with **no snapshot fallback**
   (a COALESCE to a snapshot silently re-introduces the leak).

2. **Before ANY event-anchored design, probe `outcome_date − anchor_date`.** If you will measure
   "activity in a window *after* milestone M → advanced later," first plot the gap distribution.
   **If a large share is ≤ 0, the anchor is structurally wrong** — the outcome largely happens
   *before* the anchor is even stamped, so a forward-ordered design keeps only an unrepresentative
   tail and the lift inverts as an artifact. (A re-writable milestone date also leaks post-outcome
   activity into a "pre-milestone" window — anchor on an immutable-upstream event and verify it
   precedes the outcome.)

3. **Decompose every pooled rate by the dominant structural axis before believing a gradient.** A
   pooled rate is a weighted mix; a "gradient" across some other variable is often that variable
   silently proxying the mix (Simpson). Break the pooled number down by the dominant axis (level /
   segment / program / stage) and read *within* it. Report the sub-population **mix** per segment
   so the composition is visible.

4. **De-confound the survivors with a regression — adjusted OR/coef [CI], multiple-testing-corrected.**
   Fit `outcome ~ segment + controls` (at minimum the structural axes from step 3) and report the
   **adjusted effect with its CI**, BH-corrected. A raw gradient that goes to ≈1 net of controls is
   composition; one that survives is a real association (still not necessarily causal — step 5).
   Handle perfect/quasi-separation (drop/collapse tiny cells, note it) and aliased predictors.

5. **Marker vs lever — a surviving association is still a SELECTION MARKER until an A/B says otherwise.**
   Intent is usually a **common cause** of both the behavior and the outcome, so even a robust,
   de-confounded gradient is a targeting/selection marker, not a proven lever — "make them do X →
   they advance" needs a holdout. Say so explicitly. Flag **downstream-conditioned** features
   separately: if the "predictor" *is* the outcome act, its lift is the act in disguise. Disposition
   is three-way: **lever** (survives an A/B) · **marker** (survives controls, associational) ·
   **artifact** (composition/leak — retire it).

6. **Validate any coverage-limited join is UNBIASED before trusting it.** When an outcome/attribute
   is only observable on a subset (identity bridge, side table, partial instrumentation), compare
   the bridged-subset rate to an **independent full-coverage reference**; match ⇒ report with a
   coverage caveat, diverge ⇒ the estimate is biased. Distinguish "value 0 = none" from "value 0 =
   not observed."

7. **Triple-probe every headline via independent constructions.** Reproduce the number **at least a
   second way** (different source table, join path, or grain) — and for the highest-stakes headline,
   a third — and confirm they agree before it ships. Independent constructions landing on the same
   value is the evidence; a single construction is a hypothesis. Report SQL, denominators, coverage,
   and the **as-of date** per headline.

8. **Write the honest ledger — retire / reframe / reproduces.** For each prior figure in
   circulation, state a verdict (retire = artifact/leak; reframe = right direction, wrong
   denominator/anchor, give the corrected value; reproduces = re-derived clean) with the
   replacement. State every rate's as-of date; keep protected attributes aggregate-only.

## Red flags — a finding about to ship as an artifact

| The brief says… | What it may actually be |
|---|---|
| "Segment X converts N× better" (pooled) | Not decomposed by the structural axis — likely composition (step 3). |
| "Doing X after milestone M predicts advancing" | Did you probe `outcome − anchor`? Advancement may precede M (step 2). |
| "Feature Y is highly predictive of the outcome" | Is Y the outcome act in disguise? Downstream-conditioned (step 5). |
| "The rate is Z%" | As-of when? Cumulative vs mid-cycle snapshot differ (steps 7–8). |
| "N% of the matched rows show…" | Is the unmatched fraction missing-at-random? Validate the bridge (step 6). |
| "We should get them to do X so they advance" | Associational → marker; needs an A/B to be a lever (step 5). |
| A raw effect is negative/null but "should" be positive | Simpson — control the axis, it may flip (steps 3–4). |

## How to apply

- **Doing an analysis:** self-apply steps 1–8 before you write a finding. A signal that cannot clear
  step 2 or collapses under step 3/4 is **not a finding** — log it as a checked-and-retired signal
  (that is itself honest content).
- **Reviewing a finding:** treat a promoted claim that lacks a within-axis decomposition (step 3), an
  adjusted effect (step 4), or a marker/lever label (step 5) as a **blocking** issue — the same
  standing as a wrong CI or an inverted sign.

## Deep-dive skills (one per hard step)

Each step generalizes into a focused skill in this bundle; load the matching one when a step gets hard.

- **Step 1 — leak-free PIT cohort:** `pit-history-reconstruction-needs-canonical-code-source-of-truth`,
  `snapshot-feature-pit-leak-rate-measure-history-change-after-target`,
  `snapshot-transient-state-cohort-censors-outcome-use-ever-entered-from-history`,
  `snapshot-churns-out-one-outcome-class-biases-the-rate`,
  `null-bucket-hides-progressors-in-snapshot-training`,
  `near-absorbing-state-rate-deflated-by-snapshot-residue`,
  `ml-feature-pit-derive-from-anchor-else-ablation-upper-bound`,
  `target-leakage-guard-misses-transforms-of-the-target`,
  `pit-panel-first-appearance-left-truncation-incident-anchor`
- **Step 2 — anchor timing:** `metric-window-spec-needs-event-time-distribution-probe`,
  `funnel-lever-vs-predictor-deleaked-forward-gap`,
  `cohort-milestone-lift-is-funnel-position-not-effect`,
  `within-stratum-residual-event-floor-anchor-split`
- **Step 3 — decomposition / Simpson:** `conditional-funnel-by-segment-within-level`,
  `two-lever-synergy-scale-dependent-and-simpson-ceiling`,
  `sister-cohort-high-overlap-inflates-aggregate-sum`
- **Step 4 — de-confound over time:** `observational-version-comparison-confounded-by-time`,
  `frozen-cohort-rebucket-newer-model-contemporaneity-leak`,
  `cohort-broadening-event-source-scope-leak`
- **Step 5 — marker vs lever:** `differentiator-scoping-by-provenance-not-signal`
- **Step 6 — coverage-limited joins:** `coverage-limited-join-validate-unbiased-before-trusting`
- **Step 7 — triple-probe / re-derive:** `verifier-rederive-from-raw-not-the-checked-artifact`,
  `finding-verification-live-bq-triple-probe`,
  `numeric-rederive-confirms-value-not-label-or-cohort`,
  `blind-rederive-pass-when-orchestrator-already-read-the-answer`,
  `data-provenance-verifier`,
  `amplifying-an-existing-number-is-a-provenance-recheck-trigger`
- **Step 8 — honest ledger / framing:** `single-anchor-point-estimate-needs-range-framing`
