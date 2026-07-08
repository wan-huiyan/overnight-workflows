---
name: within-stratum-residual-event-floor-anchor-split
description: |
  Decide whether a ranked cohort/segment metric that is confounded by a covariate has a REAL,
  actionable within-stratum residual — or just re-expresses the covariate. Use when: (1) you have a
  descriptive ranking of members (programs, majors, lead sources, segments, regions) by an outcome
  RATE (enroll %, convert %, retain %) and are asked to make it "actionable"; (2) the ranking is
  confounded by a known covariate (level, funnel stage, product type, cohort vintage); (3) you are
  about to compute "within-<covariate>" rates to de-confound; (4) the dataset is small so the OUTCOME
  EVENTS (numerator), not the population, are the binding constraint; (5) you have ≥2 time
  snapshots/anchors of the same cohort. Prevents the trap of naming a member "actionable" off a
  pooled, population-floored within-stratum comparison. The make-or-break: floor on outcome events
  (not population), split the time-anchors (don't pool) and test directional stability, and expect
  the matched-grain (second-covariate) leg to fall below the event floor FIRST so it is
  directional-only confirmation.
author: Claude Code
version: 1.0.0
date: 2026-06-23
---

# Within-stratum residual: floor on events, split the anchors

## Problem
You have a descriptive ranking of cohort members by an outcome rate (e.g. "share of each program's
browsers who enrolled"), and someone wants the ranking turned into an **actionable** insight. But the
ranking is **confounded** by a covariate — the top members are mostly one level / one stage / one
type. The question that decides whether anything ships: *is there a real residual within the same
stratum (does member A still beat member C among same-level peers), or does the spread just collapse
to "the ranking mirrors the covariate"?*

The seductive wrong answer: compute "within-level" rates, pool all your time snapshots, floor on
**population** (≥N members browsed), see A ≈ 2× C, and name A actionable. That comparison can be
entirely noise + composition.

## Context / Trigger conditions
- A ranked, confounded cohort/segment metric asked to be made "actionable" or "a lever."
- A known confounder you can stratify on (academic level, funnel stage, product tier, vintage).
- Small data: the **numerator** (outcome events) is tiny even when the population looks fine
  (e.g. 60–200 members but only 7–48 outcome events each).
- ≥2 time anchors / snapshots of the same cohort available.

## Solution — the three legs, in order
1. **Floor on OUTCOME EVENTS, not population.** A member can have a big denominator (200 browsers)
   but a tiny numerator (10 enrolled). Apply the confirmation floor (a common choice is **≥15 outcome
   events**) to the *numerator count per member*. Members below it may be **shown for completeness but
   must NOT anchor any claim** — say so explicitly.
2. **SPLIT the time-anchors; do NOT pool.** Re-compute the within-stratum ordering at **each anchor
   separately**. Per-anchor cells are thinner, so the test is **directional ordering** (does A > C hold
   at both anchors?), not per-anchor significance. Rule: *stable-across-anchors = finding; flips with
   overlapping CIs = noise.* Pooling hides the instability that kills false residuals.
3. **Run the matched-grain (second-covariate) leg, but expect it to drop sub-floor FIRST.** Holding a
   second covariate fixed (e.g. funnel stage) splits the already-thin per-member cells **below the
   event floor**. So the within-stratum-AND-within-stage comparison is usually **directional-only**
   confirmation, not a powered claim — and that is the expected outcome, not a failure. The member whose
   lead **survives** this leg directionally is the genuine residual; the member whose lead **collapses**
   to ≈ baseline was largely covariate **composition** (it just had a more-favorable mix).

**Verdict logic.** Name a member actionable only if its residual is (a) above its stratum baseline
pooled, (b) directionally above at **each** anchor, and (c) directionally above even with the second
covariate held fixed (even on a sub-floor cell). Pass (a) but fail (b) → noise. Pass (a),(b) but
collapse on (c) → composition, not a lever. If nothing survives, the honest headline is **"the ranking
mirrors <covariate>"** — and the artifact stays a *descriptive segmentation lens*, never a predictor or
a lever, while cells are thin.

## Verification
- Each named member's numerator ≥ the event floor.
- The within-stratum ordering is printed **per anchor** and holds directionally at every anchor.
- The matched-grain table is shown with its (likely sub-floor) cell counts and the survivor is
  flagged as directional-only.
- Every below-floor member is visibly marked "too few outcome events to judge."

## Example
Ranking segments by browse→convert %, confounded by a two-value covariate `level` (e.g. entry-tier vs advanced-tier).
- Pooled within one `level`: segments **A** and **B** both ~2× segment **C** — looks like two findings.
- Leg 1 (event floor ≥15 converted): A=28, B=23, C=17 clear; D/E/F at 7–10 do **not** → drop D/E/F from claims.
- Leg 2 (split anchors): A>C and B>C at **both** snapshots → directionally stable.
- Leg 3 (hold funnel stage fixed): **A stays ahead** (~1.8×, thin cell); **B collapses to ≈C** because
  B's members were disproportionately further down-funnel.
- **Verdict:** A is the one actionable segment; B's lead was stage composition; C/D/E/F are baseline.
  Headline: "ranking mostly mirrors `level`, with A the real exception."

## Notes
- This is the operational consequence of "confirmation budget = outcome events, not data richness":
  every extra split spends events you don't have, and matched-grain spends the most.
- The cross-stratum power fix (more anchors / a cross-cycle unique-member sample) is a *separate*
  effort — note where power runs out and defer the powered version, don't fake it on thin cells.
- See also: `cohort-milestone-lift-is-funnel-position-not-effect` (the confounder is often just funnel
  position), `wide-signal-sweep-confirm-budget-is-events-not-compute`,
  `single-anchor-point-estimate-needs-range-framing`,
  `observational-version-comparison-confounded-by-time`,
  `trailing-window-population-decline-seasonal-vs-real`,
  `conditional-funnel-by-segment-within-level` (the multi-transition extension — when the ranked metric is a
  whole FUNNEL, decompose into conditional transitions to localize *where* the segment acts + flag
  downstream-conditioned attributes that read 100% at upstream gates).
